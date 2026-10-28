"""Search in the machine's documentation: the passages useful for a question or a fault code.

1. `build` cuts every document into passages of about `CHUNK_CHARS` characters, with their
   page number (PDF, HTML, text). The result is cached in `data/docs_index/` and rebuilt
   only when a document changes.
2. `DocSearch` ranks the passages with BM25 (the ranking of classic search engines), without
   any dependency: it works well on a technical manual, where exact words (codes, part
   names) matter more than general meaning. A fault code like "C204A3" is also searched in
   its short form "C204".
"""

from __future__ import annotations

import hashlib
import html
import json
import logging
import math
import re
import unicodedata
from collections import Counter
from dataclasses import asdict, dataclass
from pathlib import Path

from pypdf import PdfReader

log = logging.getLogger(__name__)

# Target passage size: long enough to keep the context of a procedure, short enough for
# several passages to fit in one tool result.
CHUNK_CHARS = 1500


@dataclass
class Passage:
    """An extract of a document. `page` is None for documents without pages (HTML, text)."""

    source: str
    page: int | None
    text: str

    def cite(self) -> str:
        """Reference to quote in a report, e.g. "service_manual.pdf, p. 12"."""
        name = Path(self.source).name
        return f"{name}, p. {self.page}" if self.page else name


def _split(text: str) -> list[str]:
    """Cut a text into passages of about `CHUNK_CHARS` characters, at paragraph boundaries."""
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
    """Passages of every document; only the documents changed since the last call are read again.

    A document is identified by its path and modification date: editing or replacing it
    forces a new reading.
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
        log.info("document indexed: %s (%d passages)", path.name, len(found))
        passages += found
    return passages


def tokenize(text: str) -> list[str]:
    """Lower-case words without accents; adds the short form of UR fault codes (c204a3 → c204)."""
    text = unicodedata.normalize("NFKD", text.lower())
    text = "".join(c for c in text if not unicodedata.combining(c))
    words = re.findall(r"[a-z0-9]+", text)
    short_codes = [m.group(1) for w in words if (m := re.fullmatch(r"(c\d+)a\d+", w))]
    return words + short_codes


class DocSearch:
    """BM25 index of a set of passages, computed once at construction.

    Args:
        passages: passages produced by `build`.
        k1: saturation of a word's frequency (usual values 1.2 to 2).
        b: weight of the normalisation by passage length (0 to 1).
    """

    def __init__(self, passages: list[Passage], k1: float = 1.5, b: float = 0.75) -> None:
        """Count the words of each passage and how rare each word is in the whole set."""
        self.passages = passages
        self.k1, self.b = k1, b
        self.docs = [Counter(tokenize(p.text)) for p in passages]
        self.lengths = [sum(d.values()) for d in self.docs]
        self.avg = sum(self.lengths) / len(self.lengths) if self.lengths else 0.0
        df: Counter[str] = Counter()
        for d in self.docs:
            df.update(d.keys())
        n = len(self.docs)
        self.idf = {w: math.log(1 + (n - f + 0.5) / (f + 0.5)) for w, f in df.items()}

    def search(self, query: str, k: int = 5) -> list[tuple[float, Passage]]:
        """The `k` most relevant passages with their score, best first."""
        terms = tokenize(query)
        scores = []
        for i, d in enumerate(self.docs):
            s = 0.0
            for w in terms:
                if w in d:
                    tf = d[w]
                    s += self.idf[w] * tf * (self.k1 + 1) / (tf + self.k1 * (1 - self.b + self.b * self.lengths[i] / self.avg))
            if s > 0:
                scores.append((s, self.passages[i]))
        best = sorted(scores, key=lambda x: -x[0])[:k]
        log.debug("search %r: %s", query, [p.cite() for _, p in best])
        return best
