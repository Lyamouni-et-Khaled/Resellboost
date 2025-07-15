import os
import asyncio
import discord
from discord.ext import commands
import json
import traceback
from aiohttp import web # Librairie pour le serveur web asynchrone

# --- Configuration Globale ---
# Assurez-vous que cette liste correspond bien à tous vos fichiers de cogs
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

# Le token est lu depuis les variables d'environnement, ce qui est sécurisé.
BOT_TOKEN = os.environ.get("DISCORD_TOKEN")


async def health_check(request):
    """Répond aux 'health checks' de Cloud Run."""
    return web.Response(text="Le bot est en ligne et fonctionnel.")


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
        self.synced = False # Pour s'assurer de ne synchroniser qu'une seule fois
        self.web_runner = None

    async def setup_hook(self):
        """
        Hook spécial appelé par discord.py pour la configuration asynchrone.
        Charge toutes les extensions (cogs) et démarre le serveur web.
        """
        print("--- Démarrage du setup_hook ---")
        for cog_name in COGS_TO_LOAD:
            try:
                await self.load_extension(cog_name)
                print(f"✅ Cog '{cog_name}' chargé avec succès.")
            except Exception as e:
                print(f"❌ Erreur lors du chargement du cog '{cog_name}': {e}")
                traceback.print_exc()
        
        # Démarrage du serveur web aiohttp en arrière-plan
        app = web.Application()
        app.router.add_get('/', health_check)
        self.web_runner = web.AppRunner(app)
        await self.web_runner.setup()
        port = int(os.environ.get('PORT', 8080))
        site = web.TCPSite(self.web_runner, '0.0.0.0', port)
        await site.start()
        print(f"Serveur web pour le health check démarré sur le port {port}.")


    async def on_ready(self):
        """
        Événement appelé lorsque le bot est connecté et prêt.
        C'est le meilleur endroit pour forcer la synchronisation des commandes.
        """
        print("-" * 50)
        print(f"Connecté en tant que {self.user} (ID: {self.user.id})")
        print(f"Le bot est prêt et en ligne sur {len(self.guilds)} serveur(s).")
        
        # --- SYNCHRONISATION FORCÉE ---
        if not self.synced:
            print("Tentative de synchronisation des commandes slash...")
            try:
                # Synchronise les commandes pour être sûr qu'elles apparaissent sur Discord
                synced_commands = await self.tree.sync()
                print(f"✅ Synchronisé {len(synced_commands)} commande(s) globalement.")
                self.synced = True
            except Exception as e:
                print(f"❌ Erreur critique lors de la synchronisation globale : {e}")
                traceback.print_exc()
        
        print("-" * 50)

    async def close(self):
        """S'assure que tout est bien arrêté, y compris le serveur web."""
        await super().close()
        if self.web_runner:
            await self.web_runner.cleanup()
            print("Serveur web arrêté proprement.")


async def main():
    """
    Point d'entrée principal qui lance le bot.
    """
    if not BOT_TOKEN:
        print("ERREUR CRITIQUE: Le token du bot (DISCORD_TOKEN) n'est pas défini dans l'environnement.")
        return

    bot = ResellBoostBot()
    await bot.start(BOT_TOKEN)


if __name__ == "__main__":
    print("Lancement du ResellBoost Super-Bot...")
    try:
        # Lance la fonction principale qui gère le bot
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nArrêt du bot.")
    except Exception as e:
        print(f"Une erreur inattendue est survenue: {e}")
        traceback.print_exc()
