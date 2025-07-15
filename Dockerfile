# Étape 1: Utiliser une image de base Python officielle, légère et sécurisée.
# 'slim' est une version minimale qui rend l'image finale plus petite.
FROM python:3.10-slim

# Étape 2: Définir le répertoire de travail à l'intérieur du conteneur.
# Toutes les commandes suivantes seront exécutées depuis ce dossier.
WORKDIR /usr/src/app

# Étape 3: Copier uniquement le fichier des dépendances en premier.
# Docker met cette étape en cache. Si requirements.txt ne change pas,
# Docker n'aura pas besoin de réinstaller toutes les librairies à chaque fois.
COPY requirements.txt ./

# Étape 4: Mettre à jour le gestionnaire de paquets et installer les dépendances.
# - 'apt-get update' met à jour la liste des paquets disponibles.
# - 'apt-get install -y libopenjp2-7' installe une dépendance système nécessaire pour la librairie d'images 'Pillow'.
# - 'pip install --no-cache-dir -r requirements.txt' installe toutes les librairies Python
#   listées dans requirements.txt. '--no-cache-dir' force une installation propre et réduit la taille de l'image.
RUN apt-get update && apt-get install -y libopenjp2-7 && \
    pip install --no-cache-dir -r requirements.txt

# Étape 5: Copier tout le reste de votre projet dans le conteneur.
# Cela inclut vos cogs, assets, fichiers de configuration, etc.
COPY . .

# Étape 6: La commande finale pour lancer votre bot.
# C'est ce que le conteneur exécutera au démarrage.
CMD [ "python", "main.py" ]

