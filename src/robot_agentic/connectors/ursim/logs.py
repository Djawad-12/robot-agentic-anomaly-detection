"""Journal du contrôleur, lu à l'intérieur du conteneur URSim.

URSim écrit son journal dans /ursim/URControl.log (contrôleur) et /ursim/polyscope.log
(interface). On suit les deux avec `docker exec … tail -F`, comme on le ferait en SSH sur un
vrai contrôleur.

Format des lignes du contrôleur :
    2026-10-24_14:00:54:48634 00:00:53:014 INFO    - texte
Format des lignes de PolyScope :
    2026-10-24 14:00:01.875 ERROR -  texte {thread: …, loggerClass: …}
"""

from __future__ import annotations

import logging
import re
import subprocess
from collections.abc import Iterator

from ...core.events import Level, LogLine

log = logging.getLogger(__name__)

FILES = {"/ursim/URControl.log": "controller", "/ursim/polyscope.log": "polyscope"}

_LEVEL = re.compile(r"\b(DEBUG|INFO|WARN(?:ING)?|ERROR|FATAL)\b\s*-\s*(.*)")

# Ouvertures et fermetures de connexions des clients (dont les nôtres) : bruit de fond,
# rétrogradé en DEBUG pour ne jamais déclencher d'alerte.
_SOCKET_NOISE = re.compile(r'^Socket "|TCPServer: Accepted|TCPReceiver closed|object was destroyed|MultiTCPSender send')


def parse(line: str, source: str) -> LogLine | None:
    """Traduit une ligne de journal ; None pour une ligne vide ou un en-tête de `tail`.

    Une ligne sans niveau reconnaissable est gardée au niveau INFO.
    """
    line = line.strip()
    if not line or line.startswith("==>"):
        return None
    m = _LEVEL.search(line)
    if not m:
        return LogLine(source=source, text=line)
    level = {"WARN": "WARNING"}.get(m.group(1), m.group(1))
    text = re.sub(r"\s*\{thread:.*\}$", "", m.group(2)).strip()
    if _SOCKET_NOISE.search(text):
        level = "DEBUG"
    return LogLine(level=Level(level), source=source, text=text)


def stream(container: str) -> Iterator[LogLine]:
    """Nouvelles lignes des deux journaux, à partir de maintenant.

    `tail -q` ne dit pas de quel fichier vient chaque ligne : on le déduit du format de la date.

    Raises:
        ConnectionError: quand `tail` s'arrête (conteneur arrêté ou redémarré).
    """
    cmd = ["docker", "exec", container, "tail", "-n", "0", "-q", "-F", *FILES]
    with subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, bufsize=1) as proc:
        log.info("lecture du journal du conteneur %s", container)
        assert proc.stdout is not None
        for raw in proc.stdout:
            source = "polyscope" if re.match(r"\d{4}-\d{2}-\d{2} \d", raw) else "controller"
            entry = parse(raw, source)
            if entry is not None:
                yield entry
    raise ConnectionError(f"lecture du journal interrompue (conteneur {container} arrêté ?)")
