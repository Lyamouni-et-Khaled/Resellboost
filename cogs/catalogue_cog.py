
import discord
from discord.ext import commands
from discord import app_commands
from typing import List, Dict, Any, Optional
from datetime import datetime, timezone
import uuid
import re

# Importation pour l'autocomplétion et la vérification de type
from .manager_cog import TicketCloseView, TicketCreationView, PurchasePromoView, PaymentVerificationView

# --- Vues et Modals pour l'Interaction avec le Catalogue ---

class ProductActionView(discord.ui.View):
    def __init__(self, product: Dict, manager: 'ManagerCog', user: discord.User, option: Optional[Dict] = None):
        super().__init__(timeout=300)
        self.product = product
        self.manager = manager
        self.option = option
        self.user = user

    @discord.ui.button(label="🛒 Acheter ce produit", style=discord.ButtonStyle.success)
    async def buy_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.start_purchase_flow(interaction)
        
    async def start_purchase_flow(self, interaction: discord.Interaction):
        if self.product.get("options") and not self.option:
            view = OptionSelectView(product=self.product, manager=self.manager)
            await interaction.response.send_message("Ce produit a plusieurs options. Veuillez en choisir une :", view=view, ephemeral=True)
        else:
            await self.create_purchase_ticket(interaction, self.product, self.option)

    async def create_purchase_ticket(self, interaction: discord.Interaction, product: Dict, option: Optional[Dict] = None):
        await interaction.response.defer(ephemeral=True)

        if not self.manager or not self.manager.db: return await interaction.followup.send("Erreur critique.", ephemeral=True)
        
        base_price = option['price'] if option else product.get('price', 0)
        if base_price < 0:
             return await interaction.followup.send("Ce produit a un prix variable et ne peut être acheté directement. Veuillez contacter le staff.", ephemeral=True)

        final_price = base_price
        currency = product.get("currency", "EUR")
        transaction_id = str(uuid.uuid4())
        transaction_code = f"RB-{transaction_id[:4].upper()}"
        
        # --- Embed for the ticket ---
        embed_ticket = discord.Embed(title=f"Nouvelle Commande : {product['name']}", color=discord.Color.gold())
        product_display_name = product['name'] + (f" ({option['name']})" if option else "")
        embed_ticket.description = f"Cette transaction concerne le produit **{product_display_name}**."

        embed_ticket.add_field(name="Utilisateur", value=f"{interaction.user.mention} (`{interaction.user.id}`)", inline=False)
        embed_ticket.add_field(name="**Total à payer**", value=f"**{final_price:.2f} {currency}**", inline=True)
        
        payment_info = self.manager.config.get("PAYMENT_INFO", {})
        paypal_me_link = payment_info.get('PAYPAL_ME_LINK', 'https://paypal.me/example')
        paypal_email = payment_info.get('PAYPAL_EMAIL', 'contact@example.com')

        embed_ticket.add_field(
            name="Instructions de paiement",
            value=f"Veuillez envoyer `{final_price:.2f} {currency}` à notre [PayPal.Me]({paypal_me_link}) ou directement à l'adresse `{paypal_email}`.",
            inline=False
        )
        embed_ticket.add_field(
            name="⚠️ Code de Transaction",
            value=f"Veuillez **IMPÉRATIVEMENT** inclure ce code dans la note de votre paiement PayPal :\n**`{transaction_code}`**",
            inline=False
        )
        embed_ticket.set_footer(text=f"ID de Transaction: {transaction_id}")
        
        # --- Persist transaction data ---
        transaction_data = {
            "user_id": interaction.user.id,
            "product_id": product['id'],
            "option_name": option['name'] if option else None,
            "credit_used": 0,
            "transaction_code": transaction_code,
            "type": "product",
            "created_at": datetime.now(timezone.utc).isoformat()
        }
        await self.manager.db.collection('pending_transactions').document(transaction_id).set(transaction_data)

        # --- Create ticket ---
        ticket_types = self.manager.config.get("TICKET_SYSTEM", {}).get("TICKET_TYPES", [])
        purchase_ticket_type = next((tt for tt in ticket_types if tt.get("label") == "Achat de Produit"), None)

        if not purchase_ticket_type:
             return await interaction.followup.send("Erreur: Le type de ticket 'Achat de Produit' n'est pas configuré.", ephemeral=True)

        ticket_channel = await self.manager.create_ticket(
            user=interaction.user,
            guild=interaction.guild,
            ticket_type=purchase_ticket_type,
            embed=embed_ticket,
            view=PaymentVerificationView(self.manager)
        )
        
        if ticket_channel:
            await interaction.followup.send(f"Votre ticket d'achat a été créé : {ticket_channel.mention}", ephemeral=True)
        else:
            await interaction.followup.send("Impossible de créer le ticket d'achat. Veuillez contacter un administrateur.", ephemeral=True)

class OptionSelect(discord.ui.Select):
    def __init__(self, product: Dict, manager: 'ManagerCog'):
        self.product = product
        self.manager = manager
        currency = product.get("currency", "EUR")
        
        options = [
            discord.SelectOption(
                label=f"{opt['name']} ({opt['price']:.2f} {currency})",
                value=opt['name']
            )
            for opt in product.get('options', [])
        ]
        super().__init__(placeholder="Choisissez une option...", options=options, custom_id=f"option_select:{product['id']}")

    async def callback(self, interaction: discord.Interaction):
        # We need to defer here, as start_purchase_flow will defer again, which is okay
        await interaction.response.defer(ephemeral=True, thinking=False) 
        selected_option_name = self.values[0]
        selected_option = next((opt for opt in self.product['options'] if opt['name'] == selected_option_name), None)

        if not selected_option:
            return await interaction.followup.send("Option invalide.", ephemeral=True)
            
        action_view = ProductActionView(self.product, self.manager, interaction.user, option=selected_option)
        await action_view.start_purchase_flow(interaction)

class OptionSelectView(discord.ui.View):
    def __init__(self, product: Dict, manager: 'ManagerCog'):
        super().__init__(timeout=180)
        self.add_item(OptionSelect(product, manager))

class ProductSelect(discord.ui.Select):
    def __init__(self, cog: 'CatalogueCog', products: List[Dict]):
        self.cog = cog
        self.products = products
        options = [discord.SelectOption(label=p['name'][:100], value=p['id']) for p in products]
        super().__init__(placeholder="Choisissez un produit pour voir les détails...", options=options, custom_id="product_select_menu")

    async def callback(self, interaction: discord.Interaction):
        await interaction.response.defer()
        product_id = self.values[0]
        product = self.cog.manager.get_product(product_id)
        if not product:
            return await interaction.edit_original_response(content="Ce produit n'existe plus.", view=None, embed=None)

        embed = self.cog.create_product_embed(product)
        
        # Create a fresh view to avoid adding items to an existing one
        new_view = CatalogueBrowseView(self.cog, [opt.label for opt in self.view.children[0].options]) # Recreate category select
        new_view.add_item(ProductSelect(self.cog, self.products)) # Re-add product select

        # Add the correct action view
        if product.get("options"):
            action_view = OptionSelectView(product, self.cog.manager)
            for item in action_view.children:
                new_view.add_item(item)
        else:
            action_view = ProductActionView(product, self.cog.manager, interaction.user)
            for item in action_view.children:
                new_view.add_item(item)
            
        await interaction.edit_original_response(embed=embed, view=new_view)


class CatalogueBrowseView(discord.ui.View):
    def __init__(self, cog: 'CatalogueCog', categories: List[str]):
        super().__init__(timeout=300)
        self.cog = cog
        self.manager = cog.manager
        
        self.add_item(discord.ui.Select(
            placeholder="Choisissez une catégorie...",
            options=[discord.SelectOption(label=cat) for cat in categories],
            custom_id="category_select_menu"
        ))
        
    @discord.ui.select(custom_id="category_select_menu")
    async def on_category_select(self, interaction: discord.Interaction, select: discord.ui.Select):
        await interaction.response.defer()
        category = select.values[0]
        products_in_category = [p for p in self.manager.products if p.get('category') == category]

        # Create a new view for the next step
        new_view = CatalogueBrowseView(self.cog, [opt.label for opt in select.options])
        new_view.add_item(ProductSelect(self.cog, products_in_category))
        
        embed = discord.Embed(
            title=f"Catalogue - {category}",
            description="Veuillez sélectionner un produit dans le menu ci-dessous pour afficher ses détails et l'acheter.",
            color=discord.Color.blurple()
        )
        await interaction.edit_original_response(embed=embed, view=new_view)
        
class CatalogueCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.manager: Optional['ManagerCog'] = None

    async def cog_load(self):
        self.manager = self.bot.get_cog('ManagerCog')
        if not self.manager:
            print("ERREUR CRITIQUE: CatalogueCog n'a pas pu trouver le ManagerCog.")
        else:
            self.bot.add_view(PaymentVerificationView(self.manager))
            self.bot.add_view(PurchasePromoView(self.manager))

    def get_display_price(self, product: Dict[str, Any]) -> str:
        currency = product.get("currency", "EUR")
        if "options" in product and product.get("options"):
            try:
                prices = [opt['price'] for opt in product['options']]
                min_price = min(prices)
                return f"À partir de `{min_price:.2f} {currency}`"
            except (ValueError, TypeError):
                 return "`Prix variable`"
        elif "price_text" in product:
            return f"`{product['price_text']}`"
        else:
            price = product.get('price', 0.0)
            if price < 0:
                return "`Prix sur demande`"
            return f"`{price:.2f} {currency}`"

    def create_product_embed(self, product: Dict[str, Any]) -> discord.Embed:
        embed = discord.Embed(
            title=f"🛒 {product.get('name', 'Produit sans nom')}",
            description=product.get("description", "Pas de description."),
            color=discord.Color.blue()
        )
        embed.set_footer(text=f"ID du produit : {product.get('id')}")
        if product.get("image_url"):
            embed.set_thumbnail(url=product.get("image_url"))
        
        embed.add_field(name="Prix", value=self.get_display_price(product), inline=True)
        embed.add_field(name="Catégorie", value=product.get("category", "N/A"), inline=True)
        return embed
    
    @app_commands.command(name="catalogue", description="Affiche les produits disponibles de manière interactive.")
    async def catalogue(self, interaction: discord.Interaction):
        if not self.manager: return await interaction.response.send_message("Erreur interne.", ephemeral=True)
        
        categories = sorted(list(set(p['category'] for p in self.manager.products if p.get('category'))))
        
        view = CatalogueBrowseView(self, categories)
        embed = discord.Embed(
            title="Bienvenue au Catalogue ResellBoost",
            description="Veuillez choisir une catégorie dans le menu déroulant pour commencer.",
            color=discord.Color.purple()
        )
        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)


    @app_commands.command(name="produit", description="Affiche les détails d'un produit par son ID.")
    @app_commands.describe(id="L'ID unique du produit (ex: vbucks)")
    async def produit(self, interaction: discord.Interaction, id: str):
        if not self.manager: return await interaction.response.send_message("Erreur interne du bot.", ephemeral=True)
        
        product = self.manager.get_product(id)
        if not product:
            return await interaction.response.send_message("Ce produit est introuvable.", ephemeral=True)

        embed = self.create_product_embed(product)
        
        if product.get("options"):
            view = OptionSelectView(product, self.manager)
        else:
            view = ProductActionView(product, self.manager, interaction.user)
            
        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)
        
    promo = app_commands.Group(name="promo", description="[Admin] Commandes pour gérer les promotions flash.", default_permissions=discord.Permissions(administrator=True))

    @promo.command(name="creer", description="Créer une promotion flash avec une description améliorée par IA.")
    @app_commands.describe(
        nom="Le nom du produit en promotion.",
        description_courte="Une description brève que l'IA va améliorer.",
        prix="Le prix de vente final en euros.",
        prix_achat="Le coût d'achat pour vous (pour le calcul de la marge d'affiliation)."
    )
    async def promo_creer(self, interaction: discord.Interaction, nom: str, description_courte: str, prix: float, prix_achat: float):
        if not self.manager or not self.manager.model:
            return await interaction.response.send_message("Le module IA n'est pas disponible.", ephemeral=True)

        await interaction.response.defer(ephemeral=True)

        # 1. Generate description with AI
        generated_desc = await self.manager.query_gemini_for_promo(nom, description_courte)

        # 2. Store promo data in Firestore
        promo_id = str(uuid.uuid4())
        promo_data = {
            "name": nom,
            "description": generated_desc,
            "price": prix,
            "purchase_cost": prix_achat,
            "created_at": datetime.now(timezone.utc).isoformat()
        }
        await self.manager.db.collection('active_promos').document(promo_id).set(promo_data)

        # 3. Create Embed
        embed = discord.Embed(
            title=f"⚡ PROMOTION FLASH : {nom} ⚡",
            description=generated_desc,
            color=discord.Color.from_str("#f59e0b") # Gold color
        )
        embed.add_field(name="Prix Exceptionnel", value=f"**{prix:.2f} €**", inline=True)
        embed.add_field(name="Disponibilité", value="Offre à durée limitée !", inline=True)
        embed.set_footer(text=f"Cliquez sur le bouton ci-dessous pour en profiter !\nID de l'Offre: {promo_id}")
        
        # 4. Send to promo channel
        promo_channel_name = self.manager.config["CHANNELS"].get("PROMO_FLASH")
        promo_channel = discord.utils.get(interaction.guild.text_channels, name=promo_channel_name)

        if not promo_channel:
            return await interaction.followup.send(f"Le canal de promotion `{promo_channel_name}` est introuvable.", ephemeral=True)
        
        view = PurchasePromoView(self.manager)
        await promo_channel.send(embed=embed, view=view)
        await interaction.followup.send(f"✅ La promotion a été publiée dans {promo_channel.mention}.", ephemeral=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(CatalogueCog(bot))