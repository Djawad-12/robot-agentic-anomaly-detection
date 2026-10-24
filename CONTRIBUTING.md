# Principes de code

Ces règles s'appliquent à tout le code du dépôt, tests compris. `ruff check` vérifie automatiquement les règles 1 et 2 ; les autres relèvent de la relecture.

## 1. Annotations de type partout

- Chaque fonction et méthode annote tous ses paramètres et sa valeur de retour (`-> None` compris), y compris dans les tests.
- Les attributs de classe et les variables dont le type n'est pas évident à la lecture sont annotés.
- `Any` est réservé aux données réellement dynamiques (arguments JSON envoyés par le LLM, réponses de l'API). Partout ailleurs, on nomme le type.

## 2. Docstrings partout

- Chaque module, classe, fonction et méthode a une docstring, qu'elle soit publique ou privée.
- La première ligne dit **ce que fait** l'élément. Le reste explique ce que la signature ne dit pas : le rôle dans le système, les unités, les formats, les effets de bord, les erreurs levées et pourquoi.
- On utilise les sections `Args:`, `Returns:` et `Raises:` (style Google) dès qu'un paramètre ou une valeur de retour n'est pas évident.

## 3. Des modules profonds (*A Philosophy of Software Design*, J. Ousterhout)

L'objectif : une **interface simple** qui cache une **implémentation riche**. C'est ce que le livre appelle un module « profond ».

- **Pas de petites fonctions appelées une seule fois** pour « simplifier ». Une fonction longue qui fait une chose cohérente se lit de haut en bas et se débogue avec un seul point d'arrêt ; dix fonctions de trois lignes obligent à sauter partout.
- On extrait une fonction **seulement** si l'une de ces conditions est vraie :
  - elle est réutilisée à plusieurs endroits ;
  - elle cache une vraie complexité derrière une interface nettement plus simple ;
  - elle doit être testée isolément.
- **Pas de méthodes ou de couches qui ne font que transmettre l'appel** (*pass-through*) : chaque couche doit apporter quelque chose.
- **Chaque module cache une décision.** Par exemple, le format binaire des paquets UR n'existe que dans `connectors/ursim/realtime.py` : le reste du code ne voit que des `Sample`.
- **Supprimer les erreurs plutôt que les propager** quand c'est possible, en concevant des interfaces qui rendent le cas d'erreur impossible ou normal. Sinon, lever une exception explicite. Jamais de `except` silencieux.

## 4. Journalisation

- Chaque module déclare `log = logging.getLogger(__name__)`. `print` est réservé à l'interface terminal (`hmi/`) et aux scripts en ligne de commande.
- Niveaux :
  - `DEBUG` : le détail utile au débogage (paquets, requêtes, valeurs).
  - `INFO` : les événements du déroulé (connexion, test lancé, alerte, rapport, appel à Claude avec tokens et coût).
  - `WARNING` : une anomalie récupérée (flux coupé puis relancé, test refusé par les garde-fous).
  - `ERROR` : une opération qui a échoué.
- Les messages utilisent les arguments différés : `log.info("test %s : %s", name, outcome)`. On ne met pas de f-string dans les messages de log.
- On ne journalise jamais de secret (clé API, code PIN).
- Les journaux sont écrits dans `data/robot_agentic.log` (niveau `DEBUG`) et affichés dans le terminal à partir du niveau `LOG_LEVEL` (`WARNING` par défaut).

## 5. Commentaires

- On commente **seulement** ce que le code ne peut pas dire : le *pourquoi* d'un choix, une unité, un format, un invariant, une référence à une documentation externe, un piège.
- On ne paraphrase pas le code (`# incrémente i`). Si un commentaire semble nécessaire pour expliquer *ce que* fait un bloc, on le reformule d'abord dans la docstring de la fonction.

## 6. Conventions

- Les identifiants sont en anglais. Les docstrings, les commentaires et les textes destinés à l'utilisateur ou au LLM sont en français.
- Avant chaque commit : `ruff check` et `pytest` doivent passer.
