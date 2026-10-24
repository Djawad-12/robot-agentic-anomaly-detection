"""Découpe la documentation d'une machine en passages, avec leur page.

Formats : PDF (une entrée par page, redécoupée si la page est longue), HTML et texte. Le
résultat est mis en cache dans `data/docs_index/` et reconstruit si un document change.
"""

from __future__ import annotations

import hashlib
import html
import json
import logging
import re
from dataclasses import asdict, dataclass
from pathlib import Path

from pypdf import PdfReader

log = logging.getLogger(__name__)

# Taille visée d'un passage : assez long pour garder le contexte d'une procédure, assez
# court pour que plusieurs passages tiennent dans un résultat d'outil.
CHUNK_CHARS = 1500


@dataclass
class Passage:
    """Un extrait de document. `page` est None pour les documents sans pagination (HTML, texte)."""

    source: str
    page: int | None
    text: str

    def cite(self) -> str:
        """Référence à citer dans un rapport, ex. « service_manual.pdf, p. 12 »."""
        name = Path(self.source).name
        return f"{name}, p. {self.page}" if self.page else name


def _split(text: str) -> list[str]:
    """Découpe un texte en passages d'environ `CHUNK_CHARS` caractères, aux limites de paragraphe."""
    text = re.sub(r"[ \t]+", " ", text).strip()
    if len(text) <= CHUNK_CHARS:
        return [text] if text else []
    parts, current = [], ""
    for para in re.split(r"\n\s*\n", text):
        if len(current) + len(para) > CHUNK_CHARS and current:
            parts.append(current.strip())
            current = ""
        current += para + "\n\n"
    if current.strip():
        parts.append(current.strip())
    return parts


def build(paths: list[str], cache_dir: Path) -> list[Passage]:
    """Passages de tous les documents ; seuls ceux qui ont changé depuis le dernier appel sont relus.

    Un document est identifié par son chemin et sa date de modification : le modifier ou le
    remplacer force sa relecture.
    """
    cache_dir.mkdir(parents=True, exist_ok=True)
    passages: list[Passage] = []
    for path in map(Path, paths):
        digest = hashlib.sha1(f"{path.resolve()}:{path.stat().st_mtime_ns}".encode()).hexdigest()[:16]
        cached = cache_dir / f"{digest}.json"
        if cached.exists():
            passages += [Passage(**x) for x in json.loads(cached.read_text())]
            continue

        if path.suffix.lower() == ".pdf":
            found = [
                Passage(str(path), i, chunk)
                for i, page in enumerate(PdfReader(path).pages, start=1)
                for chunk in _split(page.extract_text() or "")
            ]
        else:
            raw = path.read_text(errors="replace")
            if path.suffix.lower() in {".html", ".htm"}:
                raw = re.sub(r"(?is)<(script|style).*?</\1>", " ", raw)
                raw = html.unescape(re.sub(r"<[^>]+>", " ", raw))
            found = [Passage(str(path), None, chunk) for chunk in _split(raw)]
        cached.write_text(json.dumps([asdict(x) for x in found]))
        log.info("document indexé : %s (%d passages)", path.name, len(found))
        passages += found
    return passages
