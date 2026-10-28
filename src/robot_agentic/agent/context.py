"""Formatting of the recordings for the LLM, within a token budget.

Design choice: the LLM receives the whole recording of a test, in two complementary forms,
rather than a summary:
1. a plot of the channels that move the most: studies on LLMs and time series show they spot
   an anomaly better on an image than in a list of numbers (Zhou & Yu, ICLR 2025,
   arXiv 2410.05440);
2. the values themselves, as a CSV table, at the finest rate the budget allows
   (`RECORDING_TOKEN_BUDGET`). A 5 s test at 500 Hz on 63 channels is about 160,000 numbers:
   the table is averaged over windows until it fits, without losing any channel or the
   start and end of the test. The agent can ask again for one channel at full resolution
   with the `get_recording` tool.
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

# Characters per token, measured roughly on short comma-separated numbers.
CHARS_PER_TOKEN = 2.6


def estimate_tokens(text: str) -> int:
    """Estimate the number of tokens of a text without calling the API (cautious for numbers)."""
    return int(len(text) / CHARS_PER_TOKEN) + 1


def to_csv(rec: Recording, channels: list[str] | None, hz: float) -> str:
    """CSV table of the requested channels, resampled to `hz` (4 significant digits).

    The first column is the time relative to the start of the recording, in seconds. A
    missing value (NaN) leaves the cell empty.
    """
    t, data = rec.resample(hz, channels)
    names = list(data)
    lines = ["t_s," + ",".join(names)]
    for i in range(len(t)):
        lines.append(f"{t[i]:.3f}," + ",".join(f"{data[n][i]:.4g}" if np.isfinite(data[n][i]) else "" for n in names))
    return "\n".join(lines)


def csv_within_budget(rec: Recording, channels: list[str] | None, budget_tokens: int) -> tuple[str, float]:
    """CSV table at the highest rate that fits in `budget_tokens`.

    The rate starts at the recording's own rate and is halved until the table fits (2 Hz at
    least, even if the budget is then exceeded).

    Returns:
        The table and the chosen rate, in Hz.
    """
    hz = max(rec.rate_hz, 1.0)
    while True:
        text = to_csv(rec, channels, hz)
        tokens = estimate_tokens(text)
        if tokens <= budget_tokens or hz <= 2:
            log.debug("table of %s: %.0f Hz, ≈ %d tokens (budget %d)", rec.id, min(hz, rec.rate_hz), tokens, budget_tokens)
            return text, min(hz, rec.rate_hz)
        hz /= 2


def moving_channels(rec: Recording) -> list[str]:
    """Channels that vary during the recording, those that vary the most first.

    The variation is the range of the channel divided by its largest absolute value, so that
    channels in different units (rad, A, °C) can be ranked together. A constant channel
    brings nothing to the table and is left out.
    """
    scores = {}
    for name, x in rec.channels.items():
        x = x[np.isfinite(x)]
        if x.size and float(np.ptp(x)) > 1e-9:
            scores[name] = float(np.ptp(x)) / max(float(np.max(np.abs(x))), 1e-9)
    return sorted(scores, key=lambda k: -scores[k])


def plot(rec: Recording, channels: list[str]) -> str:
    """Plot of the requested channels (8 at most), one per row.

    Returns:
        The PNG image encoded in base64, ready for an image block of the API.
    """
    channels = channels[:8]
    fig, axes = plt.subplots(len(channels), 1, figsize=(9, 1.7 * len(channels) + 0.6), sharex=True, squeeze=False)
    for ax, name in zip(axes[:, 0], channels, strict=True):
        ax.plot(rec.t - rec.t0, rec.channels[name], lw=1.1, color="#4f46e5")
        ax.set_ylabel(name, fontsize=7, rotation=0, ha="right", va="center")
        ax.tick_params(labelsize=7)
        ax.grid(alpha=0.25)
    axes[-1, 0].set_xlabel("time (s)", fontsize=8)
    fig.suptitle(f"{rec.label} ({rec.id})", fontsize=9)
    fig.tight_layout()
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=110)
    plt.close(fig)
    return base64.standard_b64encode(buf.getvalue()).decode()


def image_block(png_b64: str) -> dict[str, Any]:
    """Image block of the Claude API from a base64 PNG."""
    return {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": png_b64}}
