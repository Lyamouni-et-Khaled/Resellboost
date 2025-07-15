
import discord
from discord.ext import commands
from discord import app_commands
from typing import Optional, List, Dict
from datetime import datetime, timezone
import re
import uuid
import asyncio

from .manager_cog import ManagerCog
from google.cloud import firestore
from google.cloud.firestore_v1 import transaction

def is_hex_color(s: str) -> bool:
    if not s: return False
    return re.match(r'^#(?:[0-9a-fA-F]{3}){1,2}$', s) is not None

class GuildInviteView(discord.ui.View):
    def __init__(self, manager: 'ManagerCog', guild_id: str, guild_name: str, inviter: discord.Member):
        super().__init__(timeout=3600) # 1 hour
        self.manager = manager
        self.guild_id = guild_id
        self.guild_name = guild_name
        self.inviter = inviter

    async def _handle_response(self, interaction: discord.Interaction, accepted: bool):
        for item in self.children: item.disabled = True
        
        original_embed = interaction.message.embeds[0]
        
        if accepted:
            user_ref = self.manager.db.collection('users').document(str(interaction.user.id))
            user_data = await self.manager.get_or_create_user_data(user_ref)
            if user_data.get("guild_id"):
                original_embed.description = "Vous êtes déjà dans une guilde."
                original_embed.color = discord.Color.orange()
                await interaction.response.edit_message(embed=original_embed, view=self)
                return
            
            guild_ref = self.manager.db.collection('guilds').document(self.guild_id)
            guild_data = (await guild_ref.get()).to_dict()
            
            if len(guild_data.get('members', [])) >= self.manager.config.get("GUILD_SYSTEM", {}).get("MAX_MEMBERS", 10):
                 original_embed.description = f"La guilde **{self.guild_name}** est pleine."
                 await interaction.response.edit_message(embed=original_embed, view=self)
                 return
            
            role = interaction.guild.get_role(guild_data['role_id'])
            if role: await interaction.user.add_roles(role)
            
            await user_ref.update({"guild_id": self.guild_id})
            await guild_ref.update({"members": firestore.ArrayUnion([str(interaction.user.id)])})
            
            original_embed.description = f"Vous avez rejoint la guilde **{self.guild_name}** !"
            original_embed.color = discord.Color.green()
        else:
            original_embed.description = f"Vous avez refusé l'invitation à rejoindre **{self.guild_name}**."
            original_embed.color = discord.Color.red()
            
        await interaction.response.edit_message(embed=original_embed, view=self)

    @discord.ui.button(label="Accepter", style=discord.ButtonStyle.success)
    async def accept(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._handle_response(interaction, accepted=True)
        
    @discord.ui.button(label="Refuser", style=discord.ButtonStyle.danger)
    async def decline(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._handle_response(interaction, accepted=False)
        
class GuildDissolveView(discord.ui.View):
    def __init__(self, cog: 'GuildCog', guild_id: str):
        super().__init__(timeout=60)
        self.cog = cog
        self.guild_id = guild_id
        
    @discord.ui.button(label="Oui, dissoudre la guilde", style=discord.ButtonStyle.danger)
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer(ephemeral=True)
        await self.cog.execute_dissolve(interaction, self.guild_id)
        for item in self.children: item.disabled = True
        await interaction.edit_original_response(content="La guilde est en cours de dissolution...", view=self)

    @discord.ui.button(label="Non, annuler", style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button):
        for item in self.children: item.disabled = True
        await interaction.response.edit_message(content="Opération annulée.", view=self)
        
class GuildCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.manager: Optional[ManagerCog] = None

    async def cog_load(self):
        await asyncio.sleep(1) # Wait for ManagerCog
        self.manager = self.bot.get_cog('ManagerCog')
        if not self.manager or not self.manager.db:
            return print("❌ ERREUR CRITIQUE: GuildCog n'a pas pu trouver le ManagerCog ou la BDD.")
        print("✅ GuildCog chargé.")
    
    guild_group = app_commands.Group(name="guilde", description="Gère les guildes et leurs membres.")

    @guild_group.command(name="creer", description="Crée une nouvelle guilde (coûte des crédits).")
    @app_commands.describe(nom="Le nom de votre future guilde.", couleur="La couleur de la guilde en hexadécimal (ex: #3b82f6).")
    async def creer(self, interaction: discord.Interaction, nom: str, couleur: Optional[str]):
        guild_config = self.manager.config.get("GUILD_SYSTEM", {})
        if not guild_config.get("ENABLED", False): return await interaction.response.send_message("Système de guildes désactivé.", ephemeral=True)
        
        user_ref = self.manager.db.collection('users').document(str(interaction.user.id))
        user_data = await self.manager.get_or_create_user_data(user_ref)
        
        if user_data.get("guild_id"): return await interaction.response.send_message("❌ Vous êtes déjà dans une guilde.", ephemeral=True)
            
        existing_guild_query = self.manager.db.collection('guilds').where('name_lower', '==', nom.lower()).limit(1).stream()
        if len([doc async for doc in existing_guild_query]) > 0: return await interaction.response.send_message("❌ Une guilde avec ce nom existe déjà.", ephemeral=True)

        cost = guild_config.get("CREATION_COST", 3)
        if user_data.get("store_credit", 0) < cost: return await interaction.response.send_message(f"❌ Il vous faut **{cost} crédits**.", ephemeral=True)
        
        final_color = couleur if couleur and is_hex_color(couleur) else "#99aab5"
        await interaction.response.defer(ephemeral=True)

        guild_id = str(uuid.uuid4())
        guild_ref = self.manager.db.collection('guilds').document(guild_id)
        
        guild_role, text_channel, voice_channel = None, None, None
        try:
            guild_category_name = guild_config.get("GUILD_CATEGORY_NAME", "Guildes")
            category = discord.utils.get(interaction.guild.categories, name=guild_category_name)
            if not category: category = await interaction.guild.create_category(guild_category_name)

            guild_role = await interaction.guild.create_role(name=f"Guilde - {nom}", colour=discord.Color.from_str(final_color), hoist=True)
            overwrites = { interaction.guild.default_role: discord.PermissionOverwrite(read_messages=False), guild_role: discord.PermissionOverwrite(read_messages=True, send_messages=True, connect=True, speak=True) }
            text_channel = await interaction.guild.create_text_channel(f"💬│{nom.lower().replace(' ', '-')}", category=category, overwrites=overwrites)
            voice_channel = await interaction.guild.create_voice_channel(f"🔊│{nom}", category=category, overwrites=overwrites)

            @transaction.async_transactional
            async def create_guild_transaction(trans, u_ref, g_ref):
                await self.manager.add_transaction(trans, u_ref, "store_credit", -cost, f"Création de la guilde '{nom}'")
                guild_db_data = { "name": nom, "name_lower": nom.lower(), "owner_id": str(interaction.user.id), "members": [str(interaction.user.id)], "created_at": datetime.now(timezone.utc).isoformat(), "color": final_color, "weekly_xp": 0, "role_id": guild_role.id, "text_channel_id": text_channel.id, "voice_channel_id": voice_channel.id }
                trans.set(g_ref, guild_db_data)
                trans.update(u_ref, {"guild_id": guild_id})
            
            await self.manager.db.run_transaction(create_guild_transaction, user_ref, guild_ref)
            await interaction.user.add_roles(guild_role)

        except Exception as e:
            if guild_role: await guild_role.delete()
            if text_channel: await text_channel.delete()
            if voice_channel: await voice_channel.delete()
            print(f"Erreur création guilde : {e}")
            return await interaction.followup.send("Une erreur est survenue. L'opération a été annulée.", ephemeral=True)
        
        await interaction.followup.send(f"✅ Félicitations ! Votre guilde **{nom}** a été créée.", ephemeral=True)
    
    # ... other guild commands (info, inviter, etc.)

async def setup(bot: commands.Bot):
    await bot.add_cog(GuildCog(bot))
