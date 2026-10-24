"""Recherche dans la documentation : passages utiles pour une question ou un code d'erreur.

Classement BM25 (le classement des moteurs de recherche classiques), sans dépendance :
efficace sur un manuel technique, où les mots exacts (codes, noms de pièces) comptent plus
que le sens général. Un code d'erreur comme « C204A3 » est aussi cherché sous sa forme
courte « C204 ».
"""

from __future__ import annotations

import logging
import math
import re
import unicodedata
from collections import Counter

from .indexer import Passage

log = logging.getLogger(__name__)


def tokenize(text: str) -> list[str]:
    """Mots en minuscules et sans accents ; ajoute la forme courte des codes d'erreur UR (c204a3 → c204)."""
    text = unicodedata.normalize("NFKD", text.lower())
    text = "".join(c for c in text if not unicodedata.combining(c))
    words = re.findall(r"[a-z0-9]+", text)
    short_codes = [m.group(1) for w in words if (m := re.fullmatch(r"(c\d+)a\d+", w))]
    return words + short_codes


class DocSearch:
    """Index BM25 d'un ensemble de passages, calculé une fois à la construction.

    Args:
        passages: passages produits par `indexer.build`.
        k1: saturation de la fréquence d'un mot (valeur usuelle 1,2 à 2).
        b: poids de la normalisation par la longueur du passage (0 à 1).
    """

    def __init__(self, passages: list[Passage], k1: float = 1.5, b: float = 0.75) -> None:
        """Compte les mots de chaque passage et la rareté de chaque mot dans l'ensemble."""
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
        """Les `k` passages les plus pertinents, avec leur score, du meilleur au moins bon."""
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
        log.debug("recherche %r : %s", query, [p.cite() for _, p in best])
        return best
