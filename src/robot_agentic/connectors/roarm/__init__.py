"""Connecteur du bras Waveshare RoArm-M2-S (phase 2).

À écrire : même contrat que `URSimConnector` (`core.interfaces.Connector`).
- signaux : retour d'état du bras (positions, couples des servomoteurs) en JSON, par liaison
  série USB ou HTTP ;
- journal : journal du programme de contrôle qui pilote le bras ;
- codes d'erreur : erreurs remontées par les servomoteurs et le programme de contrôle ;
- actions : mêmes outils que URSim (joint_test, move_hand, add_load, overall_test), avec des
  bornes adaptées au bras.
"""
