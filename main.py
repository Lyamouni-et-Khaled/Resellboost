import os
import asyncio
import discord
from discord.ext import commands
import json
import traceback
from aiohttp import web # Librairie pour le serveur web asynchrone

# --- Configuration Globale ---
# FIX: Ordre de chargement corrigé pour respecter les dépendances (lottery avant credit_shop)
COGS_TO_LOAD = [
    'cogs.manager_cog',
    'cogs.assistant_cog',
    'cogs.moderator_cog',
    'cogs.giveaway_cog',
    'cogs.guild_cog',
    'cogs.admin_cog',
    'cogs.lottery_cog', # Doit être chargé avant credit_shop
    'cogs.credit_shop_cog',
    'cogs.events_cog',
    'cogs.leaderboard_cog',
    'cogs.catalogue_cog'
]

# Le token est lu depuis les variables d'environnement, ce qui est sécurisé.
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
        self.synced = False # Pour s'assurer de ne synchroniser qu'une seule fois

    async def setup_hook(self):
        """
        Hook spécial appelé par discord.py pour la configuration asynchrone.
        Charge toutes les extensions (cogs) au démarrage.
        """
        print("--- Démarrage du setup_hook ---")
        for cog_name in COGS_TO_LOAD:
            try:
                await self.load_extension(cog_name)
                print(f"✅ Cog '{cog_name}' chargé avec succès.")
            except Exception as e:
                print(f"❌ Erreur lors du chargement du cog '{cog_name}':")
                traceback.print_exc()

    async def on_ready(self):
        """
        Événement appelé lorsque le bot est connecté et prêt.
        C'est le meilleur endroit pour forcer la synchronisation des commandes.
        """
        print("-" * 50)
        print(f"Connecté en tant que {self.user} (ID: {self.user.id})")
        print(f"Le bot est prêt et en ligne sur {len(self.guilds)} serveur(s).")
        
        if not self.synced:
            print("Tentative de synchronisation des commandes slash...")
            try:
                synced_commands = await self.tree.sync()
                print(f"✅ Synchronisé {len(synced_commands)} commande(s) globalement.")
                self.synced = True
            except Exception as e:
                print(f"❌ Erreur critique lors de la synchronisation globale : {e}")
                traceback.print_exc()
        
        print("-" * 50)


async def start_bot(bot):
    """Fonction pour démarrer le bot, à lancer en tâche de fond."""
    await bot.start(BOT_TOKEN)

async def start_web_server(bot):
    """Fonction pour démarrer le serveur web aiohttp."""
    async def health_check(request):
        """Répond aux 'health checks' de Cloud Run."""
        if bot.is_ready():
            return web.Response(text="Bot is ready and serving.", status=200)
        else:
            return web.Response(text="Bot is starting up...", status=503) # Service Unavailable

    app = web.Application()
    app.router.add_get('/', health_check)
    runner = web.AppRunner(app)
    await runner.setup()
    port = int(os.environ.get('PORT', 8080))
    site = web.TCPSite(runner, '0.0.0.0', port)
    
    try:
        await site.start()
        print(f"Serveur web pour le health check démarré sur le port {port}.")
        # Garde le serveur en vie indéfiniment
        await asyncio.Future() 
    finally:
        await runner.cleanup()

async def main():
    """
    Point d'entrée principal qui lance le bot et le serveur web en parallèle.
    """
    if not BOT_TOKEN:
        print("ERREUR CRITIQUE: Le token du bot (DISCORD_TOKEN) n'est pas défini dans l'environnement.")
        return

    bot = ResellBoostBot()

    # Lancement du bot et du serveur web en parallèle
    try:
        await asyncio.gather(
            start_bot(bot),
            start_web_server(bot)
        )
    except Exception as e:
        print(f"Une erreur a interrompu l'exécution principale: {e}")
        traceback.print_exc()
    finally:
        print("Arrêt des services...")
        if not bot.is_closed():
            await bot.close()


if __name__ == "__main__":
    print("Lancement du ResellBoost Super-Bot...")
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nArrêt manuel du bot.")
    except Exception as e:
        print(f"Une erreur inattendue de haut niveau est survenue: {e}")
        traceback.print_exc()
