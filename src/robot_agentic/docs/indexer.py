"""Découpe la documentation d'une machine en passages, avec leur page.

Formats : PDF (une entrée par page, redécoupée si la page est longue), HTML et texte. Le
résultat est mis en cache dans `data/docs_index/` et reconstruit si un document change.
"""

from __future__ import annotations

import hashlib
import html
import json
import re
from dataclasses import asdict, dataclass
from pathlib import Path

from pypdf import PdfReader

CHUNK_CHARS = 1500


@dataclass
class Passage:
    source: str
    page: int | None
    text: str

    def cite(self) -> str:
        name = Path(self.source).name
        return f"{name}, p. {self.page}" if self.page else name


def _split(text: str) -> list[str]:
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


def read(path: Path) -> list[Passage]:
    suffix = path.suffix.lower()
    if suffix == ".pdf":
        out = []
        for i, page in enumerate(PdfReader(path).pages, start=1):
            out += [Passage(str(path), i, chunk) for chunk in _split(page.extract_text() or "")]
        return out
    raw = path.read_text(errors="replace")
    if suffix in {".html", ".htm"}:
        raw = re.sub(r"(?is)<(script|style).*?</\1>", " ", raw)
        raw = html.unescape(re.sub(r"<[^>]+>", " ", raw))
    return [Passage(str(path), None, chunk) for chunk in _split(raw)]


def build(paths: list[str], cache_dir: Path) -> list[Passage]:
    """Index de tous les documents ; relit seulement ceux qui ont changé."""
    cache_dir.mkdir(parents=True, exist_ok=True)
    passages: list[Passage] = []
    for p in map(Path, paths):
        digest = hashlib.sha1(f"{p.resolve()}:{p.stat().st_mtime_ns}".encode()).hexdigest()[:16]
        cached = cache_dir / f"{digest}.json"
        if cached.exists():
            passages += [Passage(**x) for x in json.loads(cached.read_text())]
            continue
        found = read(p)
        cached.write_text(json.dumps([asdict(x) for x in found]))
        passages += found
    return passages
