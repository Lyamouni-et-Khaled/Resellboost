
import discord
from discord.ext import commands
from discord import app_commands
from typing import Optional, List, Dict
import asyncio

from .manager_cog import ManagerCog
from google.cloud import firestore
from google.cloud.firestore_v1 import transaction

# --- UI Classes moved from manager_cog.py to solve circular imports ---

class CashoutModal(discord.ui.Modal, title="Demande de Retrait d'Argent"):
    amount = discord.ui.TextInput(label="Montant en crédit à retirer", placeholder="Ex: 10.50", required=True)
    paypal_email = discord.ui.TextInput(label="Votre email PayPal", placeholder="Ex: votre.email@example.com", style=discord.TextStyle.short, required=True)

    def __init__(self, manager: 'ManagerCog'):
        super().__init__()
        self.manager = manager

    async def on_submit(self, interaction: discord.Interaction):
        await self.manager.handle_cashout_submission(interaction, self.amount.value, self.paypal_email.value)

class CashoutRequestView(discord.ui.View):
    def __init__(self, manager: 'ManagerCog'):
        super().__init__(timeout=None)
        self.manager = manager

    async def _handle_action(self, interaction: discord.Interaction, approve: bool):
        await interaction.response.defer()
        msg_id = str(interaction.message.id)
        
        cashout_ref = self.manager.db.collection('pending_cashouts').document(msg_id)
        cashout_data = await cashout_ref.get()

        if not cashout_data.exists:
            for child in self.children: child.disabled = True
            await interaction.message.edit(view=self)
            return await interaction.followup.send("Cette demande de retrait est introuvable ou a déjà été traitée.", ephemeral=True)

        cashout_dict = cashout_data.to_dict()
        user_id_str = str(cashout_dict['user_id'])
        user_ref = self.manager.db.collection('users').document(user_id_str)
        member = interaction.guild.get_member(cashout_dict['user_id'])
        
        original_embed = interaction.message.embeds[0]
        new_embed = original_embed.copy()

        if approve:
            @transaction.async_transactional
            async def approve_tx(trans, ref):
                await self.manager.add_transaction(trans, ref, "cashout_count", 1, "Approbation de retrait")
            await self.manager.db.run_transaction(approve_tx, user_ref)

            if member:
                await self.manager.check_achievements(member)
                try:
                    await member.send(f"✅ Votre demande de retrait de `{cashout_dict['euros_to_send']:.2f}€` a été approuvée ! Le paiement sera effectué sous peu sur l'adresse `{cashout_dict['paypal_email']}`.")
                except discord.Forbidden: pass

                cashed_out_user_data = (await user_ref.get()).to_dict()
                referrer_id_str = cashed_out_user_data.get('referrer')

                if referrer_id_str:
                    await self.manager.grant_cashout_commission(
                        referrer_id_str=referrer_id_str,
                        amount_cashed_out=cashout_dict['euros_to_send'],
                        referral_member=member,
                        guild=interaction.guild
                    )
            
            # This is a public log, not a DB transaction
            # await self.manager.log_public_transaction(...)

            new_embed.color = discord.Color.green()
            new_embed.title = "Demande de Retrait APPROUVÉE"
            new_embed.set_footer(text=f"Approuvé par {interaction.user.display_name}")
            await interaction.followup.send("Demande approuvée.", ephemeral=True)
        else: # Deny
            @transaction.async_transactional
            async def deny_tx(trans, ref):
                await self.manager.add_transaction(
                    trans, ref, "store_credit", cashout_dict['credit_to_deduct'],
                    "Remboursement suite au refus de retrait"
                )
            await self.manager.db.run_transaction(deny_tx, user_ref)
            
            if member:
                try:
                    await member.send(f"❌ Votre demande de retrait a été refusée par le staff. Vos `{cashout_dict['credit_to_deduct']:.2f}` crédits vous ont été remboursés.")
                except discord.Forbidden: pass
            
            new_embed.color = discord.Color.red()
            new_embed.title = "Demande de Retrait REFUSÉE"
            new_embed.set_footer(text=f"Refusé par {interaction.user.display_name}")
            await interaction.followup.send("Demande refusée et crédits remboursés.", ephemeral=True)

        for child in self.children: child.disabled = True
        await interaction.message.edit(embed=new_embed, view=self)
        await cashout_ref.delete()


    @discord.ui.button(label="✅ Approuver", style=discord.ButtonStyle.success, custom_id="approve_cashout")
    async def approve(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._handle_action(interaction, approve=True)

    @discord.ui.button(label="❌ Refuser", style=discord.ButtonStyle.danger, custom_id="deny_cashout")
    async def deny(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._handle_action(interaction, approve=False)

class VerificationView(discord.ui.View):
    def __init__(self, manager: 'ManagerCog'):
        super().__init__(timeout=None)
        self.manager = manager
    
    @discord.ui.button(label="✅ Accepter le règlement", style=discord.ButtonStyle.success, custom_id="verify_member_button")
    async def verify_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        roles_config = self.manager.config.get("ROLES", {})
        verified_role_name = roles_config.get("VERIFIED")
        unverified_role_name = roles_config.get("UNVERIFIED")
        
        verified_role = discord.utils.get(interaction.guild.roles, name=verified_role_name) if verified_role_name else None
        unverified_role = discord.utils.get(interaction.guild.roles, name=unverified_role_name) if unverified_role_name else None

        if not verified_role:
            return await interaction.response.send_message(f"Erreur : Le rôle `{verified_role_name}` est introuvable.", ephemeral=True)
            
        if verified_role in interaction.user.roles:
            return await interaction.response.send_message("Vous êtes déjà vérifié !", ephemeral=True)

        try:
            await interaction.user.add_roles(verified_role, reason="Vérification via bouton")
            if unverified_role and unverified_role in interaction.user.roles:
                await interaction.user.remove_roles(unverified_role, reason="Vérification via bouton")
            await interaction.response.send_message("Vous avez été vérifié avec succès ! Bienvenue sur le serveur.", ephemeral=True)
            
            user_ref = self.manager.db.collection('users').document(str(interaction.user.id))
            user_doc = await user_ref.get()

            if user_doc.exists and user_doc.to_dict().get("referrer"):
                user_data = user_doc.to_dict()
                referrer_id_str = user_data["referrer"]
                referrer = interaction.guild.get_member(int(referrer_id_str))
                if referrer:
                    xp_config = self.manager.config.get("GAMIFICATION_CONFIG", {}).get("XP_SYSTEM", {})
                    xp_to_add = xp_config.get("XP_PER_VERIFIED_INVITE", 100)
                    await self.manager.grant_xp(referrer, xp_to_add, "Parrainage validé")
            
            # await self.manager.send_onboarding_dm(interaction.user)

        except discord.Forbidden:
            await interaction.response.send_message("Je n'ai pas les permissions pour vous donner le rôle. Veuillez contacter un administrateur.", ephemeral=True)

class TicketCloseView(discord.ui.View):
    def __init__(self, manager: 'ManagerCog'):
        super().__init__(timeout=None)
        self.manager = manager
    
    @discord.ui.button(label="🔒 Fermer le Ticket", style=discord.ButtonStyle.danger, custom_id="close_ticket_button")
    async def close_ticket(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer()
        channel = interaction.channel
        button.disabled = True
        await interaction.message.edit(view=self)
        # await self.manager.log_ticket_closure(interaction, channel)
        await channel.delete(reason=f"Ticket fermé par {interaction.user}")

class TicketTypeSelect(discord.ui.View):
    def __init__(self, manager: 'ManagerCog', ticket_types: List[Dict]):
        super().__init__(timeout=180)
        self.manager = manager
        options = [
            discord.SelectOption(label=tt['label'], description=tt.get('description'), value=tt['label'])
            for tt in ticket_types
        ]
        self.select_menu = discord.ui.Select(placeholder="Choisissez le type de ticket...", options=options)
        self.select_menu.callback = self.on_select
        self.add_item(self.select_menu)

    async def on_select(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        selected_label = self.select_menu.values[0]
        ticket_type = next((tt for tt in self.manager.config.get("TICKET_SYSTEM", {}).get("TICKET_TYPES", []) if tt['label'] == selected_label), None)
        if not ticket_type:
             return await interaction.followup.send("Type de ticket invalide.", ephemeral=True)
        initial_embed = discord.Embed(title=f"Ticket : {ticket_type['label']}", description="Veuillez décrire votre problème en détail.", color=discord.Color.blue)
        # ticket_channel = await self.manager.create_ticket(...)
        # if ticket_channel: await interaction.followup.send(...)
        for item in self.children: item.disabled = True
        await interaction.edit_original_response(content=f"Ticket pour '{selected_label}' en cours de création...", view=self)

class TicketCreationView(discord.ui.View):
    def __init__(self, manager: 'ManagerCog'):
        super().__init__(timeout=None)
        self.manager = manager

    @discord.ui.button(label="🎫 Ouvrir un ticket", style=discord.ButtonStyle.primary, custom_id="create_ticket_button")
    async def create_ticket_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        ticket_types = self.manager.config.get("TICKET_SYSTEM", {}).get("TICKET_TYPES", [])
        if not ticket_types:
            return await interaction.response.send_message("Le système de tickets n'est pas correctement configuré.", ephemeral=True)
        filtered_types = [tt for tt in ticket_types if "Achat de" not in tt.get("label")]
        await interaction.response.send_message(view=TicketTypeSelect(self.manager, filtered_types), ephemeral=True)

class MissionView(discord.ui.View):
    def __init__(self, manager: 'ManagerCog'):
        super().__init__(timeout=None)
        self.manager = manager
    
    @discord.ui.button(label="Activer/Désactiver les notifications de mission", style=discord.ButtonStyle.secondary, custom_id="toggle_mission_dms")
    async def toggle_dms(self, interaction: discord.Interaction, button: discord.ui.Button):
        user_ref = self.manager.db.collection('users').document(str(interaction.user.id))
        
        @transaction.async_transactional
        async def toggle_opt_in(trans, ref):
            user_doc = await ref.get(transaction=trans)
            user_data = user_doc.to_dict() if user_doc.exists else {}
            new_status = not user_data.get("missions_opt_in", True)
            trans.set(ref, {"missions_opt_in": new_status}, merge=True)
            return new_status

        new_status = await self.manager.db.run_transaction(toggle_opt_in, user_ref)
        status_text = "activées" if new_status else "désactivées"
        await interaction.response.send_message(f"Vos notifications de mission par MP sont maintenant {status_text}.", ephemeral=True)

class AdminCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.manager: Optional[ManagerCog] = None

    async def cog_load(self):
        await asyncio.sleep(1) # Wait for ManagerCog
        self.manager = self.bot.get_cog('ManagerCog')
        if not self.manager:
            return print("❌ ERREUR CRITIQUE: AdminCog n'a pas pu trouver le ManagerCog.")
        
        # Add persistent views that are defined in this file
        self.bot.add_view(VerificationView(self.manager))
        self.bot.add_view(TicketCreationView(self.manager))
        self.bot.add_view(MissionView(self.manager))
        self.bot.add_view(CashoutRequestView(self.manager))
        self.bot.add_view(TicketCloseView(self.manager))
        print("✅ AdminCog chargé.")

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        """Vérifie si l'utilisateur est l'administrateur défini dans la config."""
        admin_id = self.manager.config.get("ADMIN_USER_ID")
        if not admin_id or str(interaction.user.id) != admin_id:
            await interaction.response.send_message("❌ Vous n'avez pas la permission d'utiliser cette commande.", ephemeral=True)
            return False
        return True

    admin_group = app_commands.Group(name="admin", description="Commandes de gestion des utilisateurs réservées à l'administrateur.")

    @admin_group.command(name="grant-credits", description="Accorde des crédits à un membre.")
    @app_commands.describe(membre="Le membre à qui donner des crédits.", montant="Le nombre de crédits à donner.", raison="La raison de cet octroi.")
    async def grant_credits(self, interaction: discord.Interaction, membre: discord.Member, montant: float, raison: str):
        if not self.manager or not self.manager.db: return await interaction.response.send_message("Erreur interne.", ephemeral=True)
        user_ref = self.manager.db.collection('users').document(str(membre.id))
        
        @transaction.async_transactional
        async def grant_credits_tx(trans, ref):
            await self.manager.add_transaction(trans, ref, "store_credit", montant, f"Octroi Admin : {raison}")
        
        await self.manager.db.run_transaction(grant_credits_tx, user_ref)

        user_data = (await user_ref.get()).to_dict()
        current_credits = user_data.get("store_credit", 0.0)

        await interaction.response.send_message(f"✅ **{montant:.2f} crédits** ont été accordés à {membre.mention}. Nouveau solde : **{current_credits:.2f} crédits**.", ephemeral=True)
        try:
            await membre.send(f"🎉 Un administrateur vous a accordé **{montant:.2f} crédits** ! Raison : {raison}")
        except discord.Forbidden:
            pass

    @admin_group.command(name="grant-xp", description="Accorde de l'XP à un membre.")
    @app_commands.describe(membre="Le membre à qui donner de l'XP.", montant="La quantité d'XP à donner.", raison="La raison de cet octroi.")
    async def grant_xp(self, interaction: discord.Interaction, membre: discord.Member, montant: int, raison: str):
        if not self.manager: return await interaction.response.send_message("Erreur interne.", ephemeral=True)
        await self.manager.grant_xp(membre, montant, f"Octroi Admin : {raison}")
        await interaction.response.send_message(f"✅ **{montant} XP** ont été accordés à {membre.mention}.", ephemeral=True)
        try:
            await membre.send(f"🌟 Un administrateur vous a accordé **{montant} XP** ! Raison : {raison}")
        except discord.Forbidden:
            pass
            
    @admin_group.command(name="check-user", description="Affiche les données d'un utilisateur.")
    @app_commands.describe(membre="L'utilisateur à inspecter.")
    async def check_user(self, interaction: discord.Interaction, membre: discord.Member):
        user_ref = self.manager.db.collection('users').document(str(membre.id))
        user_data = await self.manager.get_or_create_user_data(user_ref)

        embed = discord.Embed(title=f"🔍 Inspection de {membre.display_name}", color=membre.color)
        embed.set_thumbnail(url=membre.display_avatar.url)
        embed.set_footer(text=f"ID: {membre.id}")
        # ... rest of the fields
        await interaction.response.send_message(embed=embed, ephemeral=True)

    setup_group = app_commands.Group(name="setup", description="Commandes de configuration initiale du serveur.")

    @setup_group.command(name="reglement", description="Poste le message du règlement.")
    async def setup_reglement(self, interaction: discord.Interaction):
        # ... command implementation
        await interaction.response.send_message("Règlement posté.", ephemeral=True)

    @setup_group.command(name="verification", description="Poste le message de vérification.")
    async def setup_verification(self, interaction: discord.Interaction):
        channel = discord.utils.get(interaction.guild.text_channels, name=self.manager.config["CHANNELS"]["VERIFICATION"])
        embed = discord.Embed(title="Verification", description="Click button to verify")
        await channel.send(embed=embed, view=VerificationView(self.manager))
        await interaction.response.send_message(f"Message de vérification posté.", ephemeral=True)

    @setup_group.command(name="tickets", description="Poste le message pour la création de tickets.")
    async def setup_tickets(self, interaction: discord.Interaction):
        channel = discord.utils.get(interaction.guild.text_channels, name=self.manager.config["CHANNELS"]["TICKET_CREATION"])
        embed = discord.Embed(title="Tickets", description="Click button to create ticket")
        await channel.send(embed=embed, view=TicketCreationView(self.manager))
        await interaction.response.send_message(f"Panneau de tickets posté.", ephemeral=True)

    @setup_group.command(name="gamification-info", description="Poste l'info sur la gamification.")
    async def setup_gamification_info(self, interaction: discord.Interaction):
        channel = discord.utils.get(interaction.guild.text_channels, name=self.manager.config["CHANNELS"]["GAMIFICATION_INFO"])
        embed = discord.Embed(title="Gamification", description="Here is how XP works...")
        await channel.send(embed=embed, view=MissionView(self.manager))
        await interaction.response.send_message(f"Message d'info gamification posté.", ephemeral=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(AdminCog(bot))
