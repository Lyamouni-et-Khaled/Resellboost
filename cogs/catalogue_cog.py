
import discord
from discord.ext import commands
from discord import app_commands
from typing import List, Dict, Any, Optional
from datetime import datetime, timezone
import uuid
import re
import asyncio

from .manager_cog import ManagerCog
from .admin_cog import TicketCloseView # Import from where it's defined now

# --- UI Classes for Catalogue Interactions ---

class PurchasePromoView(discord.ui.View):
    def __init__(self, manager: 'ManagerCog'):
        super().__init__(timeout=None)
        self.manager = manager

    @discord.ui.button(label="🛒 Acheter cette offre", style=discord.ButtonStyle.success, custom_id="buy_promo_button")
    async def buy_promo_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer(ephemeral=True)
        footer_text = interaction.message.embeds[0].footer.text
        match = re.search(r"ID de l'Offre: ([a-f0-9-]+)", footer_text)
        if not match: return await interaction.followup.send("ID d'offre introuvable.", ephemeral=True)
        promo_id = match.group(1)
        promo_ref = self.manager.db.collection('active_promos').document(promo_id)
        promo_doc = await promo_ref.get()
        if not promo_doc.exists:
            button.disabled = True
            await interaction.message.edit(view=self)
            return await interaction.followup.send("Cette offre a expiré.", ephemeral=True)
        # ticket_channel = await self.manager.create_promo_purchase_ticket(...)
        await interaction.followup.send(f"Ticket d'achat en cours de création...", ephemeral=True)

class PaymentVerificationView(discord.ui.View):
    def __init__(self, manager: 'ManagerCog'):
        super().__init__(timeout=None)
        self.manager = manager

    async def _handle_action(self, interaction: discord.Interaction, action: str):
        await interaction.response.defer()
        footer_text = interaction.message.embeds[0].footer.text
        match = re.search(r"ID de Transaction: ([a-f0-9-]+)", footer_text)
        if not match: return await interaction.followup.send("ID de transaction introuvable.", ephemeral=True)
        
        transaction_id = match.group(1)
        transaction_ref = self.manager.db.collection('pending_transactions').document(transaction_id)
        transaction_doc = await transaction_ref.get()
        
        if not transaction_doc.exists:
            for item in self.children: item.disabled = True
            await interaction.message.edit(view=self)
            return await interaction.followup.send("Transaction introuvable ou traitée.", ephemeral=True)

        transaction_data = transaction_doc.to_dict()
        original_embed = interaction.message.embeds[0]
        new_embed = original_embed.copy()
        
        if action == "confirm":
            product_to_record, option_to_record, display_name = None, None, ""
            if transaction_data.get('type') == 'promo':
                product_to_record = { 'id': transaction_data.get('promo_id'), 'name': transaction_data.get('promo_name'), 'price': transaction_data.get('price'), 'purchase_cost': transaction_data.get('purchase_cost'), 'currency': 'EUR', 'margin_type': 'net' }
                display_name = product_to_record['name']
            else:
                product_to_record = self.manager.get_product(transaction_data['product_id'])
                if transaction_data.get('option_name') and product_to_record.get('options'):
                    option_to_record = next((opt for opt in product_to_record['options'] if opt['name'] == transaction_data['option_name']), None)
                display_name = product_to_record['name'] + (f" ({option_to_record['name']})" if option_to_record else "")

            if not product_to_record: return await interaction.followup.send("❌ Erreur : produit introuvable.", ephemeral=True)

            purchase_successful, message = await self.manager.record_purchase(
                user_id=transaction_data['user_id'], product=product_to_record, option=option_to_record,
                credit_used=transaction_data.get('credit_used', 0), guild_id=interaction.guild_id,
                transaction_code=transaction_data.get('transaction_code', 'N/A')
            )

            if not purchase_successful: return await interaction.followup.send(f"❌ Erreur: {message}", ephemeral=True)

            new_embed.title = "✅ Commande Validée"
            new_embed.color = discord.Color.green()
            new_embed.description = f"Paiement pour `{display_name}` validé."
            new_embed.set_footer(text=f"Validé par {interaction.user.display_name} | {original_embed.footer.text}")
            
            buyer = interaction.guild.get_member(transaction_data['user_id'])
            if buyer:
                is_sub = product_to_record.get("type") == "subscription"
                embed_delivery = discord.Embed(
                    title=f"✅ {'Abonnement Activé' if is_sub else 'Commande Complétée'}",
                    description=f"Merci pour votre achat de **{display_name}**!", color=discord.Color.green())
                await interaction.channel.send(content=f"{buyer.mention}", embed=embed_delivery, view=TicketCloseView(self.manager))

        elif action == "deny":
            new_embed.title = "❌ Paiement Refusé"; new_embed.color = discord.Color.red()
            new_embed.description = "La commande a été refusée."; new_embed.set_footer(text=f"Refusé par {interaction.user.display_name}")
            await interaction.channel.send(content="Cette commande a été refusée.", view=TicketCloseView(self.manager))

        for item in self.children: item.disabled = True
        await interaction.message.edit(embed=new_embed, view=self)
        await transaction_ref.delete()

    @discord.ui.button(label="✅ Confirmer Paiement", style=discord.ButtonStyle.success, custom_id="confirm_payment_ticket")
    async def confirm_payment_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._handle_action(interaction, "confirm")
    
    @discord.ui.button(label="❌ Refuser", style=discord.ButtonStyle.danger, custom_id="deny_payment_ticket")
    async def deny_payment_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._handle_action(interaction, "deny")

class ProductActionView(discord.ui.View):
    # This class seems to have been replaced by the Select menus logic
    pass

class OptionSelect(discord.ui.Select):
    def __init__(self, product: Dict, manager: 'ManagerCog', cog: 'CatalogueCog'):
        self.product = product
        self.manager = manager
        self.cog = cog
        currency = product.get("currency", "EUR")
        options = [discord.SelectOption(label=f"{opt['name']} ({opt['price']:.2f} {currency})", value=opt['name']) for opt in product.get('options', [])]
        super().__init__(placeholder="Choisissez une option...", options=options, custom_id=f"option_select:{product['id']}")

    async def callback(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True, thinking=False)
        selected_option_name = self.values[0]
        selected_option = next((opt for opt in self.product['options'] if opt['name'] == selected_option_name), None)
        if not selected_option: return await interaction.followup.send("Option invalide.", ephemeral=True)
        await self.cog.create_purchase_ticket(interaction, self.product, selected_option)

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
        if not product: return await interaction.edit_original_response(content="Ce produit n'existe plus.", view=None, embed=None)

        embed = self.cog.create_product_embed(product)
        
        # Recreate parent view to add more items
        original_view = self.view
        new_view = CatalogueBrowseView(self.cog, [opt.label for opt in original_view.children[0].options])
        new_view.add_item(self) # Add self back

        if product.get("options"):
            new_view.add_item(OptionSelect(product, self.cog.manager, self.cog))
        else:
            buy_button = discord.ui.Button(label="🛒 Acheter ce produit", style=discord.ButtonStyle.success)
            async def buy_callback(inter: discord.Interaction):
                await self.cog.create_purchase_ticket(inter, product)
            buy_button.callback = buy_callback
            new_view.add_item(buy_button)
            
        await interaction.edit_original_response(embed=embed, view=new_view)

class CatalogueBrowseView(discord.ui.View):
    def __init__(self, cog: 'CatalogueCog', categories: List[str]):
        super().__init__(timeout=300)
        self.cog = cog
        
        self.add_item(discord.ui.Select(
            placeholder="Choisissez une catégorie...",
            options=[discord.SelectOption(label=cat) for cat in categories],
            custom_id="category_select_menu"
        ))
        
    @discord.ui.select(custom_id="category_select_menu")
    async def on_category_select(self, interaction: discord.Interaction, select: discord.ui.Select):
        await interaction.response.defer()
        category = select.values[0]
        products_in_category = [p for p in self.cog.manager.products if p.get('category') == category]

        new_view = self # Re-use self, just replace items
        # Remove old product select if it exists
        for item in self.children[:]:
            if isinstance(item, ProductSelect) or isinstance(item, OptionSelect) or isinstance(item, discord.ui.Button):
                self.remove_item(item)
        
        new_view.add_item(ProductSelect(self.cog, products_in_category))
        
        embed = discord.Embed(title=f"Catalogue - {category}", description="Sélectionnez un produit ci-dessous.", color=discord.Color.blurple())
        await interaction.edit_original_response(embed=embed, view=new_view)

class CatalogueCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.manager: Optional[ManagerCog] = None

    async def cog_load(self):
        await asyncio.sleep(1) # Wait for ManagerCog
        self.manager = self.bot.get_cog('ManagerCog')
        if not self.manager:
            return print("❌ ERREUR CRITIQUE: CatalogueCog n'a pas pu trouver le ManagerCog.")
        
        self.bot.add_view(PaymentVerificationView(self.manager))
        self.bot.add_view(PurchasePromoView(self.manager))
        print("✅ CatalogueCog chargé.")

    def get_display_price(self, product: Dict[str, Any]) -> str:
        # ... implementation ...
        return "`Prix variable`"

    def create_product_embed(self, product: Dict[str, Any]) -> discord.Embed:
        embed = discord.Embed(title=f"🛒 {product.get('name', 'Produit')}", description=product.get("description", "."), color=discord.Color.blue())
        embed.set_footer(text=f"ID: {product.get('id')}")
        if product.get("image_url"): embed.set_thumbnail(url=product.get("image_url"))
        embed.add_field(name="Prix", value=self.get_display_price(product), inline=True)
        embed.add_field(name="Catégorie", value=product.get("category", "N/A"), inline=True)
        return embed

    async def create_purchase_ticket(self, interaction: discord.Interaction, product: Dict, option: Optional[Dict] = None):
        await interaction.response.defer(ephemeral=True)
        base_price = option['price'] if option else product.get('price', 0)
        if base_price < 0: return await interaction.followup.send("Ce produit a un prix variable.", ephemeral=True)
        
        # ... Logic to create embed and persist transaction ...
        # ticket_channel = await self.manager.create_ticket(...)
        await interaction.followup.send(f"Ticket d'achat pour {product['name']} créé.", ephemeral=True)
    
    @app_commands.command(name="catalogue", description="Affiche les produits disponibles de manière interactive.")
    async def catalogue(self, interaction: discord.Interaction):
        if not self.manager: return await interaction.response.send_message("Erreur interne.", ephemeral=True)
        categories = sorted(list(set(p['category'] for p in self.manager.products if p.get('category'))))
        
        # FIX: Prevent API error for >25 options in select menu
        if len(categories) > 25:
            categories = categories[:25]
            await interaction.followup.send("Attention: Trop de catégories, affichage des 25 premières.", ephemeral=True)

        view = CatalogueBrowseView(self, categories)
        embed = discord.Embed(title="Bienvenue au Catalogue", description="Choisissez une catégorie.", color=discord.Color.purple())
        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)

    @app_commands.command(name="produit", description="Affiche les détails d'un produit par son ID.")
    @app_commands.describe(id="L'ID unique du produit (ex: vbucks)")
    async def produit(self, interaction: discord.Interaction, id: str):
        if not self.manager: return await interaction.response.send_message("Erreur interne.", ephemeral=True)
        product = self.manager.get_product(id)
        if not product: return await interaction.response.send_message("Produit introuvable.", ephemeral=True)
        embed = self.create_product_embed(product)
        # ... view creation logic ...
        view = discord.ui.View() # Placeholder
        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)

async def setup(bot: commands.Bot):
    await bot.add_cog(CatalogueCog(bot))
