
import discord
from discord.ext import commands
from discord import app_commands
from typing import Optional, List, Dict, Any
import json
import asyncio
from datetime import datetime, timedelta, timezone
from google.cloud import firestore
from google.cloud.firestore_v1 import transaction

from .manager_cog import ManagerCog
from .lottery_cog import LotteryCog

CREDIT_SHOP_ITEMS_FILE = 'credit_shop_items.json'

class PurchaseXPModal(discord.ui.Modal, title="Achat d'XP Direct"):
    credits_to_spend = discord.ui.TextInput(label="Crédits à dépenser pour de l'XP", placeholder="Ex: 150.5", required=True)
    
    def __init__(self, manager: 'ManagerCog'):
        super().__init__()
        self.manager = manager
    
    async def on_submit(self, interaction: discord.Interaction):
        try:
            credits = float(self.credits_to_spend.value.replace(',', '.'))
            if credits <= 0: raise ValueError("Le montant doit être positif.")
        except (ValueError, TypeError):
            return await interaction.response.send_message("Montant invalide.", ephemeral=True)
        
        await self.manager.handle_xp_purchase(interaction, credits)

class CreditShopView(discord.ui.View):
    def __init__(self, manager: 'ManagerCog', items: List[Dict[str, Any]], lottery_cog: 'LotteryCog'):
        super().__init__(timeout=300)
        self.manager = manager
        self.lottery_cog = lottery_cog
        self.items = {item['id']: item for item in items}
        
        for item in items:
            button = discord.ui.Button(label=f"{item['name']} ({item['cost']} C)" if item['cost'] > 0 else item['name'], style=discord.ButtonStyle.primary, custom_id=f"credit_shop:{item['id']}")
            button.callback = self.on_button_click
            self.add_item(button)
            
    async def on_button_click(self, interaction: discord.Interaction):
        item_id = interaction.data['custom_id'].split(':')[1]
        item = self.items.get(item_id)
        if not item: return await interaction.response.send_message("Article indisponible.", ephemeral=True)
            
        if item['id'] == 'xp_purchase':
            await interaction.response.send_modal(PurchaseXPModal(self.manager))
        elif item['id'] == 'lottery_ticket':
            await self.lottery_cog.handle_lottery_join(interaction, item['cost'])
        else:
            await self.handle_booster_purchase(interaction, item)

    async def handle_booster_purchase(self, interaction: discord.Interaction, item: Dict[str, Any]):
        user_ref = self.manager.db.collection('users').document(str(interaction.user.id))
        
        @transaction.async_transactional
        async def purchase_booster_tx(trans, ref, item_data):
            user_data = await self.manager.get_or_create_user_data(ref, trans)
            cost = item_data['cost']
            
            if user_data.get("store_credit", 0.0) < cost: return {"success": False, "reason": "Fonds insuffisants."}
            
            await self.manager.add_transaction(trans, ref, "store_credit", -cost, f"Achat boutique: {item_data['name']}")
            
            now = datetime.now(timezone.utc)
            active_boosters = user_data.get('active_boosters', {})
            if item_data['id'] == 'xp_booster_25_24h':
                expires = now + timedelta(hours=24)
                active_boosters['xp_booster_1'] = {'expires_at': expires.isoformat(), 'multiplier': 1.25}
            elif item_data['id'] == 'commission_booster_10_3d':
                expires = now + timedelta(days=3)
                active_boosters['commission_booster_1'] = {'expires_at': expires.isoformat(), 'bonus': 0.10}
            trans.update(ref, {'active_boosters': active_boosters})
            return {"success": True}
        
        result = await self.manager.db.run_transaction(purchase_booster_tx, user_ref, item)
        
        if result['success']:
            await interaction.response.send_message(f"✅ Achat réussi ! Vous avez activé **{item['name']}**.", ephemeral=True)
        else:
            await interaction.response.send_message(f"❌ {result['reason']}", ephemeral=True)
        
class CreditShopCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.manager: Optional[ManagerCog] = None
        self.lottery_cog: Optional[LotteryCog] = None
        self.shop_items: List[Dict[str, Any]] = []

    async def cog_load(self):
        await asyncio.sleep(1) 
        self.manager = self.bot.get_cog('ManagerCog')
        self.lottery_cog = self.bot.get_cog('LotteryCog')
        if not self.manager or not self.lottery_cog:
            return print("❌ ERREUR CRITIQUE: CreditShopCog: Dépendances (Manager, Lottery) introuvables.")
        
        await self._load_items()
        print("✅ CreditShopCog chargé.")

    async def _load_items(self):
        try:
            with open(CREDIT_SHOP_ITEMS_FILE, 'r', encoding='utf-8') as f: self.shop_items = json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            print(f"ATTENTION: {CREDIT_SHOP_ITEMS_FILE} introuvable ou mal formaté.")
            self.shop_items = []

    @app_commands.command(name="boutique_credits", description="Affiche la boutique pour dépenser vos crédits.")
    async def credit_shop(self, interaction: discord.Interaction):
        embed = discord.Embed(title="💎 Boutique à Crédits 💎", description="Dépensez vos crédits pour obtenir des avantages !", color=discord.Color.purple())
        if not self.shop_items:
            embed.description = "La boutique est vide."
            return await interaction.response.send_message(embed=embed, ephemeral=True)
            
        user_data = await self.manager.get_or_create_user_data(self.manager.db.collection('users').document(str(interaction.user.id)))
        embed.set_footer(text=f"Votre solde : {user_data.get('store_credit', 0.0):.2f} crédits")

        view = CreditShopView(self.manager, self.shop_items, self.lottery_cog)
        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)

async def setup(bot: commands.Bot):
    await bot.add_cog(CreditShopCog(bot))
