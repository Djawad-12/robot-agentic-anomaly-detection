"""Connecteur MotorScope : pince ampèremétrique et ESP32-S3 (mode passif, phase 2).

À écrire : même contrat que `URSimConnector` (`core.interfaces.Connector`).
- signaux : courant mesuré par la pince (ADS1115) et spectre calculé par l'ESP32, reçus par USB ;
- journal et codes d'erreur : messages de l'ESP32 (capteur débranché, saturation…) ;
- actions : aucune ; ce mode ne fait qu'écouter, `tools()` renvoie une liste vide.
"""
