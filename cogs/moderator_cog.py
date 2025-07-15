
import discord
from discord.ext import commands
from discord import app_commands
import json
from datetime import datetime, timedelta, timezone
from typing import Dict, Any, Optional
import os
import re
import uuid
import asyncio

from .manager_cog import ManagerCog
from .catalogue_cog import PurchasePromoView # FIX: Import from the correct cog
from google.cloud import firestore
from google.cloud.firestore_v1 import transaction

try:
    import google.generativeai as genai
    from google.generativeai.types import GenerationConfig
    AI_AVAILABLE = True
except ImportError:
    AI_AVAILABLE = False

class ModeratorCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.manager: Optional[ManagerCog] = None
        self.model: Optional[genai.GenerativeModel] = None

    async def cog_load(self):
        await asyncio.sleep(1) # Wait for ManagerCog
        self.manager = self.bot.get_cog('ManagerCog')
        if not self.manager:
            return print("ERREUR CRITIQUE: ModeratorCog n'a pas pu trouver le ManagerCog.")
        
        if AI_AVAILABLE and self.manager.model:
            self.model = self.manager.model
            print("✅ Moderator Cog: Modèle Gemini partagé.")
        else:
            print("⚠️ ATTENTION: ModeratorCog: Modèle AI non disponible.")

    async def query_gemini_moderation(self, message: discord.Message) -> Optional[Dict[str, Any]]:
        # ... implementation ...
        return {"action": "PASS", "reason": f"Erreur d'analyse IA."}

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.author.bot or message.guild is None or not self.manager: return
        # ... auto-moderation logic ...

    async def handle_delete_and_warn(self, message: discord.Message, reason: str):
        try: await message.delete()
        except discord.NotFound: pass
        await self.apply_warning(message.author, reason, message.jump_url, is_dm=True)

    async def handle_warn(self, message: discord.Message, reason: str):
        await self.apply_warning(message.author, reason, message.jump_url, is_dm=True)
        try: await message.add_reaction("⚠️")
        except discord.Forbidden: pass

    async def notify_staff(self, guild: discord.Guild, title: str, description: str):
        # ... implementation ...
        pass

    async def apply_warning(self, member: discord.Member, reason: str, jump_url: str, is_dm: bool = True):
        user_ref = self.manager.db.collection('users').document(str(member.id))
        
        @transaction.async_transactional
        async def increment_warning(trans, ref):
            # This is now a self-contained transaction function
            await self.manager.add_transaction(trans, ref, 'warnings', 1, f"Avertissement: {reason}")
            user_data = await self.manager.get_or_create_user_data(ref, trans)
            return user_data.get('warnings', 0)

        warning_count = await self.manager.db.run_transaction(increment_warning, user_ref)
        
        threshold = self.manager.config.get("MODERATION_CONFIG", {}).get("WARNING_THRESHOLD", 3)
        if is_dm:
            try: await member.send(f"Avertissement sur **{member.guild.name}** : **{reason}**. (Avertissement n°{warning_count})")
            except discord.Forbidden: pass 

        await self.notify_staff(member.guild, f"Avertissement -> {member.mention}", f"Raison: {reason}\nTotal: **{warning_count}/{threshold}**\n[Lien]({jump_url})")
        
        if warning_count >= threshold:
            try:
                await member.timeout(timedelta(days=1), reason=f"Seuil d'avertissement ({threshold}) atteint.")
                await self.notify_staff(member.guild, f"Seuil atteint pour {member.mention}", "Utilisateur mis en silencieux 24h.")
                await user_ref.update({'warnings': 0}) # Reset warnings after timeout
            except discord.Forbidden:
                 await self.notify_staff(member.guild, f"ERREUR Mute {member.mention}", "Permissions manquantes.")

    promo = app_commands.Group(name="promo", description="[Admin] Gère les promotions flash.", default_permissions=discord.Permissions(administrator=True))

    @promo.command(name="creer", description="Crée une promotion flash avec une description améliorée par IA.")
    @app_commands.describe(nom="Nom du produit.", description_courte="Description brève pour l'IA.", prix="Prix de vente.", prix_achat="Coût d'achat (marge).")
    async def promo_creer(self, interaction: discord.Interaction, nom: str, description_courte: str, prix: float, prix_achat: float):
        if not self.manager or not self.manager.model: return await interaction.response.send_message("Module IA non dispo.", ephemeral=True)
        await interaction.response.defer(ephemeral=True)
        generated_desc = await self.manager.query_gemini_for_promo(nom, description_courte)
        promo_id = str(uuid.uuid4())
        promo_data = { "name": nom, "description": generated_desc, "price": prix, "purchase_cost": prix_achat, "created_at": datetime.now(timezone.utc).isoformat() }
        await self.manager.db.collection('active_promos').document(promo_id).set(promo_data)
        embed = discord.Embed(title=f"⚡ PROMO FLASH : {nom} ⚡", description=generated_desc, color=discord.Color.gold())
        embed.add_field(name="Prix Exceptionnel", value=f"**{prix:.2f} €**", inline=True)
        embed.set_footer(text=f"ID de l'Offre: {promo_id}")
        promo_channel_name = self.manager.config["CHANNELS"].get("PROMO_FLASH")
        promo_channel = discord.utils.get(interaction.guild.text_channels, name=promo_channel_name)
        if not promo_channel: return await interaction.followup.send(f"Canal `{promo_channel_name}` introuvable.", ephemeral=True)
        
        view = PurchasePromoView(self.manager)
        await promo_channel.send(embed=embed, view=view)
        await interaction.followup.send(f"✅ Promotion publiée dans {promo_channel.mention}.", ephemeral=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(ModeratorCog(bot))
