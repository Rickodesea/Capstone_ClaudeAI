"""
gen_idef_diagrams.py  --  IDEF-0 style representations of the two optimization models.

IDEF-0 box convention (matches idef_example.png):
  • Center box   : model name
  • Left arrows  : INPUTS        (data consumed)
  • Top arrows   : CONSTRAINTS / CONTROLS (rules the solution must obey)
  • Bottom labels: MECHANISMS    (solver / approach used to produce the output)
  • Right arrows : OUTPUTS       (what the model produces)

Only short descriptive phrases are used — no mathematical symbols.

Run:  python gen_idef_diagrams.py
Output: Cluster_Optimization_Models/Pipeline/timing_data/plots/idef_realtime.png
        Cluster_Optimization_Models/Pipeline/timing_data/plots/idef_planahead.png
"""

from __future__ import annotations

import textwrap
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import FancyArrowPatch, Rectangle
from PIL import Image, ImageChops

ROOT = Path(__file__).resolve().parent
PLOT_DIR = ROOT / "Cluster_Optimization_Models" / "Pipeline" / "timing_data" / "plots"

# ── Geometry (axes are 0..100 in both directions) ───────────────────────────────
BOX_L, BOX_R = 36.0, 64.0          # box left / right
BOX_B, BOX_T = 30.0, 70.0          # box bottom / top
ARROW_KW = dict(arrowstyle="-|>", mutation_scale=16, lw=1.4, color="#222222")


def _spread(lo: float, hi: float, n: int) -> list[float]:
    """n evenly spaced centers inside (lo, hi), with margin."""
    if n == 1:
        return [(lo + hi) / 2]
    pad = (hi - lo) / (n + 1)
    return [lo + pad * (i + 1) for i in range(n)]


def _draw(model_name: str, inputs, constraints, mechanisms, outputs,
          box_color: str, out_path: Path, show_legend: bool = True) -> None:
    fig, ax = plt.subplots(figsize=(13.5, 9.0))
    ax.set_xlim(0, 100)
    ax.set_ylim(0, 100)
    ax.axis("off")

    # ── Center box ──────────────────────────────────────────────────────────────
    ax.add_patch(Rectangle(
        (BOX_L, BOX_B), BOX_R - BOX_L, BOX_T - BOX_B,
        facecolor=box_color, edgecolor="#1b1b1b", linewidth=2.0, zorder=2,
    ))
    ax.text((BOX_L + BOX_R) / 2, (BOX_B + BOX_T) / 2, model_name,
            ha="center", va="center", fontsize=15.5, fontweight="bold",
            color="#10243e", zorder=3)

    # ── Inputs (left → into box) ─────────────────────────────────────────────────
    for y, txt in zip(_spread(BOX_B, BOX_T, len(inputs)), inputs):
        ax.add_patch(FancyArrowPatch((29, y), (BOX_L, y), **ARROW_KW))
        ax.text(28, y, textwrap.fill(txt, 14), ha="right", va="center", fontsize=12,
                fontweight="normal", color="#000000", multialignment="right")

    # ── Constraints (top → down into box) ────────────────────────────────────────
    for x, txt in zip(_spread(BOX_L, BOX_R, len(constraints)), constraints):
        ax.add_patch(FancyArrowPatch((x, 77), (x, BOX_T), **ARROW_KW))
        ax.text(x, 78, textwrap.fill(txt, 12), ha="left", va="bottom", fontsize=11.5,
                rotation=90, fontweight="normal", color="#000000", multialignment="left")

    # ── Mechanisms (bottom → up into box) ────────────────────────────────────────
    for x, txt in zip(_spread(BOX_L, BOX_R, len(mechanisms)), mechanisms):
        ax.add_patch(FancyArrowPatch((x, 23), (x, BOX_B), **ARROW_KW))
        ax.text(x, 22, textwrap.fill(txt, 12), ha="right", va="top", fontsize=11.5,
                rotation=90, fontweight="normal", color="#000000", multialignment="left")

    # ── Outputs (out of box → right) ─────────────────────────────────────────────
    for y, txt in zip(_spread(BOX_B, BOX_T, len(outputs)), outputs):
        ax.add_patch(FancyArrowPatch((BOX_R, y), (71, y), **ARROW_KW))
        ax.text(72, y, textwrap.fill(txt, 14), ha="left", va="center", fontsize=12,
                fontweight="normal", color="#000000", multialignment="left")

    # ── Convention legend (top-left empty corner — no collisions) ────────────────
    if show_legend:
        legend = (
            "IDEF-0 convention\n"
            "Left  = Inputs\n"
            "Top   = Constraints / controls\n"
            "Bottom = Solvers / approach\n"
            "Right = Outputs"
        )
        ax.text(0, 100, legend, ha="left", va="top", fontsize=10.5, color="#333333",
                linespacing=1.5, fontweight="normal",
                bbox=dict(boxstyle="round,pad=0.5", facecolor="#f5f5f5", edgecolor="#999999"))

    PLOT_DIR.mkdir(parents=True, exist_ok=True)
    fig.savefig(out_path, dpi=170, bbox_inches="tight", pad_inches=0.0)
    plt.close(fig)
    _autocrop(out_path, pad=10)
    print(f"  saved {out_path.name}")


def _autocrop(path: Path, pad: int = 10) -> None:
    """Trim white margins down to the ink bounding box plus a small pad."""
    img = Image.open(path).convert("RGB")
    bg = Image.new("RGB", img.size, (255, 255, 255))
    bbox = ImageChops.difference(img, bg).getbbox()
    if bbox is None:
        return
    l, t, r, b = bbox
    l, t = max(l - pad, 0), max(t - pad, 0)
    r, b = min(r + pad, img.width), min(b + pad, img.height)
    img.crop((l, t, r, b)).save(path)


def main() -> None:
    specs = [
        dict(
            stem="idef_realtime",
            model_name="Real-Time\nOptimizer\n(using a MILP solver)",
            inputs=[
                "Pending job queue",
                "Predicted job memory",
                "Predicted job CPU",
                "Node remaining capacity",
                "Tenant wait-time weights",
                "Plan-ahead node filter",
            ],
            constraints=[
                "One-node assignment",
                "Memory capacity",
                "CPU capacity",
                "Binary decisions",
                "Effective capacity",
            ],
            mechanisms=[
                "Integer linear program",
                "Branch and bound",
                "Solver: Gurobi (default)",
            ],
            outputs=[
                "Job-to-node assignments",
                "Unplaced jobs re-queued",
                "Updated node utilization",
                "Wait-time feedback",
            ],
            box_color="#cfe3f7",
        ),
        dict(
            stem="idef_planahead",
            model_name="Plan-Ahead\nOptimizer\n(using MISOCP and\nGurobi solver)",
            inputs=[
                "Forecast tenant demand",
                "Demand variance",
                "Machine pool & capacity",
                "Exclusive / shared tags",
                "SLA violation feedback",
                "Wait-time & queue feedback",
            ],
            constraints=[
                "Min machines per tenant",
                "Cantelli overflow cap",
                "Exclusive isolation",
                "Demand satisfaction",
                "Effective capacity",
            ],
            mechanisms=[
                "Second-order cone program",
                "Cantelli chance constraint",
                "Heavy-light mix bonus",
                "Solver: Gurobi (default)",
            ],
            outputs=[
                "Tenant groups per period",
                "Machine assignments",
                "Activated machine set",
                "Fairness ratio",
                "Filter to real-time model",
            ],
            box_color="#d8f0e3",
        ),
    ]
    # Generate both a legend version and a clean no-legend copy of each diagram.
    for spec in specs:
        stem = spec.pop("stem")
        _draw(**spec, out_path=PLOT_DIR / f"{stem}.png",          show_legend=True)
        _draw(**spec, out_path=PLOT_DIR / f"{stem}_nolegend.png", show_legend=False)


if __name__ == "__main__":
    main()
