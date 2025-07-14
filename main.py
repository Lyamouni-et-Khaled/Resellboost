
import os
import asyncio
import discord
from discord.ext import commands
import json
import traceback
from aiohttp import web

# --- Configuration Globale ---
COGS_TO_LOAD = [
    'cogs.manager_cog',
    'cogs.catalogue_cog',
    'cogs.assistant_cog',
    'cogs.moderator_cog',
    'cogs.giveaway_cog',
    'cogs.guild_cog',
    'cogs.credit_shop_cog',
    'cogs.admin_cog',
    'cogs.lottery_cog',
    'cogs.events_cog',
    'cogs.leaderboard_cog'
]

# Le token est maintenant lu depuis les variables d'environnement,
# ce qui est la méthode sécurisée pour le déploiement sur le cloud.
BOT_TOKEN = os.environ.get("DISCORD_TOKEN")


class ResellBoostBot(commands.Bot):
    """
    Classe personnalisée pour le bot, utilisant setup_hook pour un chargement robuste.
    """
    def __init__(self):
        # Configuration des intents (permissions) du bot
        intents = discord.Intents.default()
        intents.members = True
        intents.message_content = True
        intents.reactions = True
        intents.guilds = True
        intents.invites = True
        super().__init__(command_prefix="!", intents=intents)
        self.web_server_task = None

    async def web_server(self):
        """Lance un serveur web simple pour la compatibilité avec Cloud Run."""
        async def handle(request):
            return web.Response(text="ResellBoost Bot is alive and running!")

        app = web.Application()
        app.add_routes([web.get('/', handle)])
        
        runner = web.AppRunner(app)
        await runner.setup()
        
        # Cloud Run fournit la variable d'environnement PORT.
        port = os.environ.get('PORT', 8080)
        site = web.TCPSite(runner, '0.0.0.0', port)
        
        try:
            await site.start()
            print(f"--- Web server started on port {port} ---")
            # Maintient le serveur en vie jusqu'à ce que le bot soit arrêté
            await self.wait_until_closed()
        finally:
            await runner.cleanup()

    async def setup_hook(self):
        """
        Hook spécial appelé par discord.py pour la configuration asynchrone.
        C'est l'endroit idéal pour charger les extensions et synchroniser les commandes.
        """
        print("--- Démarrage du setup_hook ---")
        
        # 1. Lance le serveur web en tâche de fond
        self.web_server_task = self.loop.create_task(self.web_server())

        # 2. Charger tous les cogs
        for cog_name in COGS_TO_LOAD:
            try:
                await self.load_extension(cog_name)
                print(f"✅ Cog '{cog_name}' chargé avec succès.")
            except Exception as e:
                print(f"❌ Erreur lors du chargement du cog '{cog_name}': {e}")
                traceback.print_exc() # Affiche l'erreur complète pour le débogage

        # 3. Lire la configuration pour l'ID du serveur
        config = {}
        try:
            with open('config.json', 'r', encoding='utf-8') as f:
                config = json.load(f)
        except (FileNotFoundError, json.JSONDecodeError) as e:
            print(f"AVERTISSEMENT: Impossible de lire config.json. La synchronisation est annulée. {e}")
            return

        guild_id_str = config.get("GUILD_ID")
        if not guild_id_str or guild_id_str == "VOTRE_VRAI_ID_DE_SERVEUR_ICI":
            print("ERREUR CRITIQUE: GUILD_ID n'est pas défini dans config.json. Les commandes slash ne seront pas synchronisées.")
            return

        # 4. Synchroniser les commandes pour la guilde spécifique
        try:
            guild_id = int(guild_id_str)
            guild = discord.Object(id=guild_id)
            # La synchronisation des commandes se fait ici
            synced = await self.tree.sync(guild=guild)
            print(f"✅ Synchronisé {len(synced)} commande(s) pour la guilde : {guild_id_str}.")
        except Exception as e:
            print(f"❌ Erreur lors de la synchronisation des commandes pour la guilde {guild_id_str}: {e}")

    async def on_ready(self):
        """Événement appelé lorsque le bot est connecté et prêt."""
        print("-" * 50)
        print(f"Connecté en tant que {self.user} (ID: {self.user.id})")
        print(f"Le bot est prêt et en ligne sur {len(self.guilds)} serveur(s).")
        print("-" * 50)
        
    async def close(self):
        """Arrête proprement le bot et le serveur web."""
        if self.web_server_task:
            self.web_server_task.cancel()
        await super().close()


async def main():
    """Point d'entrée principal pour lancer le bot."""
    if not BOT_TOKEN:
        print("ERREUR CRITIQUE: Le token du bot (DISCORD_TOKEN) n'est pas défini dans l'environnement.")
        return

    # Initialisation et démarrage du bot
    bot = ResellBoostBot()
    try:
        await bot.start(BOT_TOKEN)
    finally:
        await bot.close()


if __name__ == "__main__":
    # Crée les dossiers de base s'ils n'existent pas
    if not os.path.exists('cogs'):
        os.makedirs('cogs')
    
    if not os.path.exists('data'):
        os.makedirs('data')

    if not os.path.exists('assets'):
        os.makedirs('assets')
        print("INFO: Dossier 'assets' créé. N'oubliez pas d'y ajouter les polices Inter-Bold.ttf et Inter-Regular.ttf.")

    print("Lancement du ResellBoost Super-Bot...")
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nArrêt du bot.")
    except Exception as e:
        print(f"Une erreur inattendue est survenue: {e}")
        traceback.print_exc()