"""Mise en forme des données pour le LLM, dans un budget de tokens.

Choix de conception : le LLM reçoit l'enregistrement complet d'un test, sous trois formes
complémentaires, plutôt qu'un simple résumé :
1. les écarts calculés par rapport à la référence (`core.checks.compare`) pour orienter sa
   lecture ;
2. un graphique de chaque voie qui s'écarte, superposé à la référence : les études sur les
   LLM et les séries temporelles montrent qu'ils repèrent mieux une anomalie sur une image
   que dans une suite de nombres (Zhou & Yu, ICLR 2025, arXiv 2410.05440) ;
3. les valeurs elles-mêmes, en tableau CSV, au rythme le plus fin que permet le budget
   (`RECORDING_TOKEN_BUDGET`). Un test de 5 s à 500 Hz sur 63 voies fait environ 160 000
   nombres : le tableau est donc moyenné par fenêtres jusqu'à tenir dans le budget, sans
   perdre de voie ni le début et la fin du test. L'agent peut redemander une voie précise
   à pleine résolution avec l'outil `get_recording`.
"""

from __future__ import annotations

import base64
import io
import logging
from typing import Any

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402

from ..core.recorder import Recording  # noqa: E402

log = logging.getLogger(__name__)

# Caractères par token, mesuré en ordre de grandeur sur des nombres courts séparés par des virgules.
CHARS_PER_TOKEN = 2.6


def estimate_tokens(text: str) -> int:
    """Estimation du nombre de tokens d'un texte, sans appel à l'API (prudente pour des nombres)."""
    return int(len(text) / CHARS_PER_TOKEN) + 1


def to_csv(rec: Recording, channels: list[str] | None, hz: float) -> str:
    """Tableau CSV des voies demandées, rééchantillonnées à `hz` (4 chiffres significatifs).

    La première colonne est le temps relatif au début de l'enregistrement, en secondes. Une
    valeur manquante (NaN) laisse la case vide.
    """
    t, data = rec.resample(hz, channels)
    names = list(data)
    lines = ["t_s," + ",".join(names)]
    for i in range(len(t)):
        lines.append(f"{t[i]:.3f}," + ",".join(f"{data[n][i]:.4g}" if np.isfinite(data[n][i]) else "" for n in names))
    return "\n".join(lines)


def csv_within_budget(rec: Recording, channels: list[str] | None, budget_tokens: int) -> tuple[str, float]:
    """Tableau CSV au rythme le plus élevé qui tient dans `budget_tokens`.

    Le rythme part de celui de l'enregistrement et est divisé par deux jusqu'à tenir dans le
    budget (2 Hz au minimum, même si le budget est alors dépassé).

    Returns:
        Le tableau et le rythme retenu, en Hz.
    """
    hz = max(rec.rate_hz, 1.0)
    while True:
        text = to_csv(rec, channels, hz)
        tokens = estimate_tokens(text)
        if tokens <= budget_tokens or hz <= 2:
            log.debug("tableau de %s : %.0f Hz, ≈ %d tokens (budget %d)", rec.id, min(hz, rec.rate_hz), tokens, budget_tokens)
            return text, min(hz, rec.rate_hz)
        hz /= 2


def informative_channels(rec: Recording) -> list[str]:
    """Voies qui varient pendant l'enregistrement : une voie constante n'apporte rien au tableau."""
    out = []
    for name, x in rec.channels.items():
        x = x[np.isfinite(x)]
        if x.size and float(np.ptp(x)) > 1e-9:
            out.append(name)
    return out


def plot(rec: Recording, channels: list[str], reference: Recording | None = None) -> str:
    """Graphique des voies demandées (8 au plus), une par ligne, avec la référence en pointillés.

    Returns:
        L'image PNG encodée en base64, prête pour un bloc image de l'API.
    """
    channels = channels[:8]
    fig, axes = plt.subplots(len(channels), 1, figsize=(9, 1.7 * len(channels) + 0.6), sharex=True, squeeze=False)
    for ax, name in zip(axes[:, 0], channels, strict=True):
        ax.plot(rec.t - rec.t0, rec.channels[name], lw=1.1, color="#4f46e5", label="test")
        if reference is not None and name in reference.channels:
            ax.plot(reference.t - reference.t0, reference.channels[name], lw=1.0, ls="--", color="#9aa0a6", label="référence")
        ax.set_ylabel(name, fontsize=7, rotation=0, ha="right", va="center")
        ax.tick_params(labelsize=7)
        ax.grid(alpha=0.25)
    axes[0, 0].legend(fontsize=7, loc="upper right")
    axes[-1, 0].set_xlabel("temps (s)", fontsize=8)
    fig.suptitle(f"{rec.label} ({rec.id})", fontsize=9)
    fig.tight_layout()
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=110)
    plt.close(fig)
    return base64.standard_b64encode(buf.getvalue()).decode()


def image_block(png_b64: str) -> dict[str, Any]:
    """Bloc image de l'API Claude à partir d'un PNG en base64."""
    return {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": png_b64}}
