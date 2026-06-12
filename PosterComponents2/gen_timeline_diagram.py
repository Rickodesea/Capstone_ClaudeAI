"""
gen_timeline_diagram.py  --  Square timeline diagram for the capstone poster.

Shows the 24-hour scheduling horizon:
  • Plan-Ahead Optimizer runs on a fixed cadence (every 6 hours).
  • Real-Time Optimizer is invoked on demand, whenever needed, in between.

Theme: blue -> white gradient.

Run:    python gen_timeline_diagram.py
Output: timeline_horizon.png  (square, transparent-friendly white background)
"""

from __future__ import annotations

from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
from matplotlib.colors import LinearSegmentedColormap
from matplotlib.patches import FancyArrowPatch, FancyBboxPatch

OUT = Path(__file__).resolve().parent / "timeline_horizon.png"

# ── Blue → white theme ──────────────────────────────────────────────────────────
NAVY   = "#0d3b66"
BLUE   = "#1f6fb2"
SKY    = "#5aa9e6"
PALE   = "#dcebf7"
GRAD   = LinearSegmentedColormap.from_list("blue_white", ["#ffffff", SKY, BLUE, NAVY])

# ── Horizon geometry (axes are 0..24 hours wide, 0..10 tall) ─────────────────────
H0, H1   = 0.0, 24.0          # horizon start / end (hours)
TRACK_B, TRACK_T = 4.6, 5.4   # timeline track band
PA_HOURS = [0, 6, 12, 18, 24]                 # Plan-Ahead: fixed 6-hour cadence
RT_HOURS = [2.4, 4.1, 8.0, 10.7, 14.2, 16.6, 19.3, 22.1]  # Real-Time: as needed


def main() -> None:
    fig, ax = plt.subplots(figsize=(8.0, 7.0))
    ax.set_xlim(-1.6, 25.6)
    ax.set_ylim(1.25, 10.0)
    ax.axis("off")

    # ── Title ────────────────────────────────────────────────────────────────────
    ax.text(12, 9.55, "Scheduling Horizon",
            ha="center", va="center", fontsize=20, fontweight="bold", color=NAVY)
    ax.text(12, 9.02, "Schedule for an horizon for several periods",
            ha="center", va="center", fontsize=12.5, color=BLUE)
    ax.text(12, 8.62, "(e.g. 24 hour horizon and planning every 6 hours)",
            ha="center", va="center", fontsize=10.5, color=BLUE)

    # ── Gradient timeline track ──────────────────────────────────────────────────
    grad = np.linspace(0, 1, 512).reshape(1, -1)
    im = ax.imshow(grad, extent=(H0, H1, TRACK_B, TRACK_T), aspect="auto",
                   cmap=GRAD, vmin=0, vmax=1, zorder=1)
    # rounded clip so the gradient bar has soft ends
    clip = FancyBboxPatch((H0, TRACK_B), H1 - H0, TRACK_T - TRACK_B,
                          boxstyle="round,pad=0,rounding_size=0.35",
                          transform=ax.transData, facecolor="none",
                          edgecolor=NAVY, linewidth=1.6, zorder=4)
    ax.add_patch(clip)
    im.set_clip_path(clip)

    # hour ticks at the 6-hour boundaries
    for h in PA_HOURS:
        ax.plot([h, h], [TRACK_B - 0.18, TRACK_T + 0.18], color=NAVY, lw=1.4, zorder=5)
        ax.text(h, TRACK_B - 0.55, f"{h:02d}:00", ha="center", va="top",
                fontsize=10.5, color=NAVY, fontweight="bold")

    # ── Plan-Ahead markers (above the track, fixed cadence) ──────────────────────
    for h in PA_HOURS:
        ax.add_patch(FancyArrowPatch((h, 7.05), (h, TRACK_T + 0.12),
                                     arrowstyle="-|>", mutation_scale=15,
                                     lw=2.0, color=NAVY, zorder=6))
    pa_box = FancyBboxPatch((1.5, 7.0), 21.0, 1.15,
                            boxstyle="round,pad=0.18,rounding_size=0.25",
                            facecolor=NAVY, edgecolor="none", zorder=6)
    ax.add_patch(pa_box)
    ax.text(12, 7.78, "Plan-Ahead Optimizer",
            ha="center", va="center", fontsize=14, fontweight="bold",
            color="white", zorder=7)
    ax.text(12, 7.32, "(called per period, e.g. every 6 hours)",
            ha="center", va="center", fontsize=10.5, color=PALE, zorder=7)

    # ── Real-Time markers (below the track, on demand) ───────────────────────────
    for h in RT_HOURS:
        ax.add_patch(FancyArrowPatch((h, 2.95), (h, TRACK_B - 0.12),
                                     arrowstyle="-|>", mutation_scale=12,
                                     lw=1.6, color=SKY, zorder=6))
        ax.scatter([h], [2.95], s=70, marker="D", color=BLUE,
                   edgecolor="white", linewidth=1.0, zorder=7)
    rt_box = FancyBboxPatch((1.5, 1.55), 21.0, 1.15,
                            boxstyle="round,pad=0.18,rounding_size=0.25",
                            facecolor="white", edgecolor=BLUE, linewidth=2.0, zorder=6)
    ax.add_patch(rt_box)
    ax.text(12, 2.33, "Real-Time Optimizer",
            ha="center", va="center", fontsize=14, fontweight="bold",
            color=BLUE, zorder=7)
    ax.text(12, 1.87, "(called whenever jobs enter the queue)",
            ha="center", va="center", fontsize=10.5, color=BLUE, zorder=7)

    fig.savefig(OUT, dpi=200, bbox_inches="tight", facecolor="white")
    plt.close(fig)
    print(f"  saved {OUT.name}")


if __name__ == "__main__":
    main()
