# 🎣 FiveM Fishing Bot

Bot de vision d'écran pour automatiser un mini-jeu de pêche FiveM.

## Fonctionnement

- `F8` : active/désactive le bot
- Capture une petite zone au centre de l'écran
- Détecte l'indication visuelle du mini-jeu
- Attend 5 secondes
- Envoie automatiquement `Z`, `Q`, `S` ou `D`
- Attend la disparition de l'indication avant de chercher la suivante
- Notifications Windows pour les états et actions

## Installation

```bash
py -m pip install -r requirements.txt
```

Puis :

```bash
py bot.py
```

> La détection visuelle devra être calibrée selon l'interface exacte de ton serveur FiveM.
