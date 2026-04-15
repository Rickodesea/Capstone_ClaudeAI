from pptx import Presentation
from pptx.util import Inches, Pt
from pptx.dml.color import RGBColor
from pptx.enum.text import PP_ALIGN
import pptx.oxml.ns as ns
from lxml import etree

# ── Color Palette ──────────────────────────────────────────────────────────────
C_BG        = RGBColor(0x0D, 0x1B, 0x2A)
C_ACCENT    = RGBColor(0x00, 0xB4, 0xD8)
C_GOLD      = RGBColor(0xFF, 0xD1, 0x66)
C_WHITE     = RGBColor(0xFF, 0xFF, 0xFF)
C_LIGHT     = RGBColor(0xCA, 0xE9, 0xFF)
C_DARK_BOX  = RGBColor(0x1A, 0x2E, 0x44)
C_GREEN     = RGBColor(0x4C, 0xAF, 0x50)
C_RED       = RGBColor(0xEF, 0x53, 0x50)
C_YELLOW    = RGBColor(0xFF, 0xEE, 0x58)
C_GRAY      = RGBColor(0x78, 0x90, 0x9C)
C_BRONZE    = RGBColor(0xE0, 0x87, 0x6A)
C_SILVER    = RGBColor(0xA8, 0xDA, 0xDC)
C_TEAL      = RGBColor(0x00, 0x96, 0x88)

prs = Presentation()
prs.slide_width  = Inches(13.33)
prs.slide_height = Inches(7.5)
BLANK = prs.slide_layouts[6]

# ── Helpers ────────────────────────────────────────────────────────────────────
def slide():
    s = prs.slides.add_slide(BLANK)
    s.background.fill.solid()
    s.background.fill.fore_color.rgb = C_BG
    return s

def rect(s, l, t, w, h, fill=None, border=None, bw=Pt(1.5)):
    sh = s.shapes.add_shape(1, Inches(l), Inches(t), Inches(w), Inches(h))
    sh.fill.solid() if fill else sh.fill.background()
    if fill: sh.fill.fore_color.rgb = fill
    if border:
        sh.line.color.rgb = border
        sh.line.width = bw
    else:
        sh.line.fill.background()
    return sh

def tb(s, text, l, t, w, h, sz=Pt(13), bold=False, col=C_WHITE, align=PP_ALIGN.LEFT):
    box = s.shapes.add_textbox(Inches(l), Inches(t), Inches(w), Inches(h))
    box.word_wrap = True
    tf = box.text_frame; tf.word_wrap = True
    p = tf.paragraphs[0]; p.alignment = align
    r = p.add_run(); r.text = text
    r.font.size = sz; r.font.bold = bold; r.font.color.rgb = col
    return box

def bullets(s, items, l, t, w, h, sz=Pt(11.5), col=C_WHITE, prefix="▸  ", spacing=Pt(6)):
    box = s.shapes.add_textbox(Inches(l), Inches(t), Inches(w), Inches(h))
    box.word_wrap = True
    tf = box.text_frame; tf.word_wrap = True
    for i, item in enumerate(items):
        p = tf.paragraphs[0] if i == 0 else tf.add_paragraph()
        p.space_before = spacing
        r = p.add_run(); r.text = prefix + item
        r.font.size = sz; r.font.color.rgb = col
    return box

def header(s, title, sub=None):
    rect(s, 0, 0, 0.08, 7.5, fill=C_ACCENT)
    tb(s, title, 0.2, 0.18, 12.8, 0.65, sz=Pt(28), bold=True)
    if sub:
        tb(s, sub, 0.2, 0.82, 12.8, 0.42, sz=Pt(15), col=C_ACCENT)

def arrow(s, x1, y1, x2, y2, col=C_ACCENT, w=Pt(2)):
    c = s.shapes.add_connector(1, Inches(x1), Inches(y1), Inches(x2), Inches(y2))
    c.line.color.rgb = col; c.line.width = w
    ln = c.line._ln
    etree.SubElement(ln, ns.qn('a:tailEnd')).set('type', 'none')
    h = etree.SubElement(ln, ns.qn('a:headEnd'))
    h.set('type', 'arrow'); h.set('w', 'med'); h.set('len', 'med')

def label_box(s, text, l, t, w, h, fill=C_DARK_BOX, border=C_ACCENT, sz=Pt(12), bold=False, col=C_WHITE):
    rect(s, l, t, w, h, fill=fill, border=border)
    box = s.shapes.add_textbox(Inches(l), Inches(t), Inches(w), Inches(h))
    box.word_wrap = True
    tf = box.text_frame; tf.word_wrap = True
    tf.margin_top = Inches(h * 0.2)
    p = tf.paragraphs[0]; p.alignment = PP_ALIGN.CENTER
    r = p.add_run(); r.text = text
    r.font.size = sz; r.font.bold = bold; r.font.color.rgb = col

# ══════════════════════════════════════════════════════════════════════════════
# SLIDE 1 — TITLE
# ══════════════════════════════════════════════════════════════════════════════
s = slide()
rect(s, 0, 3.1, 13.33, 0.06, fill=C_ACCENT)
tb(s, "Node-Level Predictive Admission Control\nfor Memory Overcommitment",
   0.8, 0.9, 11.7, 1.9, sz=Pt(34), bold=True, align=PP_ALIGN.CENTER)
tb(s, "A Theoretical Framework for SLA-Aware Memory Oversubscription on Multi-Tenant Cloud Nodes",
   0.8, 2.75, 11.7, 0.55, sz=Pt(15), col=C_LIGHT, align=PP_ALIGN.CENTER)
tb(s, "DAMO 699 Capstone  |  Team Concept Overview  |  Spring 2026",
   0.8, 3.4, 11.7, 0.5, sz=Pt(14), col=C_GRAY, align=PP_ALIGN.CENTER)
bullets(s, [
    "Problem: Cloud DRAM utilization stuck at ~40–50% while DRAM costs rise 54–116% YoY",
    "Goal: Push utilization toward ~80% without SLA violations using a node-side predictive model",
    "Method: Simulation validated with real Azure trace data + synthetic workloads",
], 1.5, 4.1, 10.3, 2.8, sz=Pt(14), col=C_LIGHT)

# ══════════════════════════════════════════════════════════════════════════════
# SLIDE 2 — THE CORE IDEA
# ══════════════════════════════════════════════════════════════════════════════
s = slide()
header(s, "The Core Idea", "What is our model actually doing?")
for i, (hdr, body) in enumerate([
    ("WHERE", "On every physical node.\nNot the cluster manager—\nthe node itself."),
    ("WHAT",  "A predictive optimization model\nthat decides:\n'Can I safely accept this VM\nnow and in the future?'"),
    ("HOW",   "Dual predictive models +\nmathematical optimization +\nSLA downtime budget-aware\ntermination as last resort."),
]):
    x = 0.3 + i * 4.3
    rect(s, x, 1.55, 4.0, 0.55, fill=C_ACCENT)
    tb(s, hdr, x, 1.55, 4.0, 0.55, sz=Pt(18), bold=True, col=C_BG, align=PP_ALIGN.CENTER)
    rect(s, x, 2.1, 4.0, 2.3, fill=C_DARK_BOX, border=C_ACCENT)
    tb(s, body, x+0.1, 2.15, 3.8, 2.2, sz=Pt(13), col=C_WHITE, align=PP_ALIGN.CENTER)

rect(s, 0.3, 4.7, 12.7, 0.75, fill=RGBColor(0x00, 0x3A, 0x52), border=C_GOLD)
tb(s, "Key insight: Most research optimizes WHERE to place VMs (bin packing at the cluster manager). "
      "We optimize WHETHER to accept a VM at the node — a node-side advisory that adds temporal intelligence "
      "to any existing bin-packing strategy.",
   0.45, 4.72, 12.4, 0.72, sz=Pt(12.5), col=C_GOLD, align=PP_ALIGN.CENTER)
tb(s, "Theoretical model  ·  Validated via simulation  ·  Real + synthetic workload data",
   0.3, 5.65, 12.7, 0.4, sz=Pt(12), col=C_GRAY, align=PP_ALIGN.CENTER)

# ══════════════════════════════════════════════════════════════════════════════
# SLIDE 3 — SYSTEM ARCHITECTURE
# ══════════════════════════════════════════════════════════════════════════════
s = slide()
header(s, "System Architecture Overview",
       "Cluster manager + node model — decoupled, advisory relationship")

rect(s, 0.25, 1.4, 3.3, 2.2, fill=C_DARK_BOX, border=C_ACCENT, bw=Pt(2))
tb(s, "Cluster Manager", 0.3, 1.42, 3.2, 0.5, sz=Pt(14), bold=True, col=C_ACCENT, align=PP_ALIGN.CENTER)
bullets(s, ["Receives new VM requests","Runs bin-packing algorithm","Selects candidate node","Handles VM re-launch"],
        0.35, 1.9, 3.15, 1.55, sz=Pt(11.5))

arrow(s, 3.55, 2.5, 5.05, 2.5, col=C_GOLD, w=Pt(2.5))
tb(s, "Query:\n'Accept VM?'", 3.55, 2.15, 1.5, 0.65, sz=Pt(10), col=C_GOLD, align=PP_ALIGN.CENTER)
arrow(s, 5.05, 2.9, 3.55, 2.9, col=C_GREEN, w=Pt(2.5))
tb(s, "YES / NO", 3.6, 2.88, 1.4, 0.35, sz=Pt(10), bold=True, col=C_GREEN, align=PP_ALIGN.CENTER)

rect(s, 5.05, 1.1, 7.9, 5.8, fill=C_DARK_BOX, border=C_ACCENT, bw=Pt(2))
tb(s, "Physical Node", 5.1, 1.12, 7.8, 0.5, sz=Pt(14), bold=True, col=C_ACCENT, align=PP_ALIGN.CENTER)
rect(s, 5.3, 1.75, 7.45, 0.85, fill=RGBColor(0x00, 0x3A, 0x52), border=C_GOLD, bw=Pt(1.5))
tb(s, "Optimization & Admission Control Model", 5.35, 1.77, 7.35, 0.42, sz=Pt(12.5), bold=True, col=C_GOLD, align=PP_ALIGN.CENTER)
tb(s, "Accepts/rejects VMs  |  Triggers memory sharing  |  SLA-budget-aware termination",
   5.35, 2.15, 7.35, 0.38, sz=Pt(10.5), col=C_LIGHT, align=PP_ALIGN.CENTER)

rect(s, 5.3, 2.75, 3.55, 1.4, fill=RGBColor(0x0D, 0x2A, 0x1A), border=C_GREEN, bw=Pt(1.2))
tb(s, "Generalized\nPrediction Model", 5.35, 2.77, 3.45, 0.52, sz=Pt(11.5), bold=True, col=C_GREEN, align=PP_ALIGN.CENTER)
tb(s, "Cross-VM trends\nVM size, type, temporal patterns\nUsed for NEW VMs", 5.35, 3.24, 3.45, 0.85, sz=Pt(10))

rect(s, 9.2, 2.75, 3.55, 1.4, fill=RGBColor(0x2A, 0x1A, 0x0D), border=C_BRONZE, bw=Pt(1.2))
tb(s, "Curated\nPrediction Model", 9.25, 2.77, 3.45, 0.52, sz=Pt(11.5), bold=True, col=C_BRONZE, align=PP_ALIGN.CENTER)
tb(s, "Per-VM behavioral history\nLearned usage patterns & peaks\nUsed as history accumulates", 9.25, 3.24, 3.45, 0.85, sz=Pt(10))

arrow(s, 8.85, 3.45, 9.2, 3.45, col=C_GRAY, w=Pt(1.5))
tb(s, "VMs running on node:", 5.3, 4.35, 7.45, 0.35, sz=Pt(11), bold=True, col=C_LIGHT)
for i, (lbl, col) in enumerate(zip(
    ["VM 1","VM 2","VM 3","VM 4","VM 5","VM 6"],
    [C_ACCENT,C_ACCENT,C_SILVER,C_SILVER,C_BRONZE,C_BRONZE])):
    x = 5.35 + i * 1.27
    rect(s, x, 4.72, 1.15, 0.95, fill=RGBColor(0x1A, 0x2E, 0x44), border=col, bw=Pt(1.2))
    tb(s, lbl, x, 4.74, 1.15, 0.9, sz=Pt(9.5), col=col, align=PP_ALIGN.CENTER)

rect(s, 5.3, 5.85, 7.45, 0.3, fill=RGBColor(0x1A, 0x3A, 0x5C), border=C_ACCENT)
tb(s, "Physical RAM  (e.g., 128 GB)", 5.35, 5.86, 7.35, 0.28, sz=Pt(10), col=C_ACCENT, align=PP_ALIGN.CENTER)
tb(s, "✗  Swapping excluded (not used in modern CSPs — Akamai, AWS, GCP)",
   5.3, 6.35, 7.45, 0.35, sz=Pt(10.5), col=C_RED, align=PP_ALIGN.CENTER)

# ══════════════════════════════════════════════════════════════════════════════
# SLIDE 4 — SLA DOWNTIME BUDGET FRAMEWORK  (replaces Gold/Silver/Bronze)
# ══════════════════════════════════════════════════════════════════════════════
s = slide()
header(s, "SLA Downtime Budget Framework",
       "Single configurable parameter α — grounded in real cloud SLA practice")

# Left: real-world context
rect(s, 0.2, 1.38, 5.6, 5.5, fill=C_DARK_BOX, border=C_ACCENT)
tb(s, "Real-World SLA Context", 0.25, 1.4, 5.5, 0.45, sz=Pt(13), bold=True, col=C_ACCENT)
bullets(s, [
    "Akamai (Linode) applies 99.99% monthly uptime to ALL compute VMs — dedicated, shared, GPU, High Memory — no differentiation by VM type",
    "99.99% = 4.32 min allowed downtime/month",
    "99.9%  = 43 min allowed downtime/month",
    "99%    = 7.3 hours allowed downtime/month",
    "Each additional 'nine' cuts allowed downtime by 10× (Siliceum, 2026)",
    "Credits are not automatic — consumer must file a ticket (Akamai, 2026)",
    "Our model adopts platform availability as the SLA metric",
], 0.3, 1.9, 5.45, 4.8, sz=Pt(11))

# Right: the model's SLA formula
rect(s, 6.1, 1.38, 6.95, 5.5, fill=RGBColor(0x00, 0x2A, 0x3A), border=C_GOLD)
tb(s, "Our Model: Single Parameter α", 6.15, 1.4, 6.85, 0.45, sz=Pt(13), bold=True, col=C_GOLD)

formula_lines = [
    ("α  =  configurable SLA uptime %  (e.g., 99.99%)", C_YELLOW),
    ("T_month  =  minutes in billing month  (43,200)", C_LIGHT),
    ("", C_GRAY),
    ("Max allowed downtime per VM:", C_LIGHT),
    ("  D_max = (1 − α) × T_month", C_YELLOW),
    ("", C_GRAY),
    ("Remaining budget for VM i:", C_LIGHT),
    ("  B_i = D_max − D_i", C_YELLOW),
    ("  where D_i = cumulative downtime this month", C_LIGHT),
    ("", C_GRAY),
    ("Termination eligibility:", C_LIGHT),
    ("  B_i > τ  (relaunch threshold time)", C_YELLOW),
    ("", C_GRAY),
    ("Termination order:", C_LIGHT),
    ("  Sort by B_i descending", C_YELLOW),
    ("  (most budget remaining → terminated first)", C_LIGHT),
]
box = s.shapes.add_textbox(Inches(6.2), Inches(1.9), Inches(6.7), Inches(4.8))
box.word_wrap = True; tf = box.text_frame; tf.word_wrap = True
for i, (line, col) in enumerate(formula_lines):
    p = tf.paragraphs[0] if i == 0 else tf.add_paragraph()
    p.space_before = Pt(3)
    r = p.add_run(); r.text = line
    r.font.size = Pt(12); r.font.color.rgb = col

rect(s, 0.2, 6.95, 12.9, 0.38, fill=RGBColor(0x00, 0x3A, 0x52), border=C_GOLD)
tb(s, "Simplification: one α for all VMs → mirrors Akamai's uniform compute SLA. "
      "Per-VM heterogeneous SLAs are future work.",
   0.3, 6.97, 12.7, 0.34, sz=Pt(11), col=C_GOLD, align=PP_ALIGN.CENTER)

# ══════════════════════════════════════════════════════════════════════════════
# SLIDE 5 — PACKING STRATEGY
# ══════════════════════════════════════════════════════════════════════════════
s = slide()
header(s, "Admission & Packing Strategy",
       "Maximize utilization while guaranteeing no VM exceeds its SLA budget")

# Three strategy boxes
strats = [
    ("SAFE PACKING\n(Baseline)", C_TEAL,
     "Pack VMs such that if ALL active VMs simultaneously use their full allocated RAM,\n"
     "total memory ≤ physical RAM.\n\n"
     "This is the conservative floor — zero overcommitment.\n"
     "Serves as simulation baseline (represents current industry practice)."),
    ("PREDICTIVE PACKING\n(Our Model)", C_GOLD,
     "Pack VMs into the statistical headroom.\n\n"
     "Generalized + curated models forecast that VMs will NOT all peak simultaneously.\n"
     "Admission is accepted when predicted aggregate peak ≤ physical RAM.\n\n"
     "Target: push utilization from ~45% → ~80%."),
    ("OVERLOAD RESPONSE\n(Last Resort)", C_RED,
     "When spike is predicted and sharing is insufficient:\n\n"
     "1. Check each VM's remaining SLA budget B_i\n"
     "2. Terminate VM with largest B_i first\n"
     "3. Never terminate a VM with B_i ≤ τ\n"
     "4. Notify cluster manager to relaunch"),
]
for i, (title, col, body) in enumerate(strats):
    x = 0.2 + i * 4.35
    rect(s, x, 1.38, 4.1, 0.62, fill=col)
    tb(s, title, x, 1.38, 4.1, 0.62, sz=Pt(12), bold=True,
       col=C_BG if col != C_RED else C_WHITE, align=PP_ALIGN.CENTER)
    rect(s, x, 2.0, 4.1, 3.8, fill=C_DARK_BOX, border=col)
    tb(s, body, x+0.1, 2.06, 3.9, 3.7, sz=Pt(11.5))

# RAM bar illustration
tb(s, "Memory utilization illustration (128 GB node):", 0.2, 6.0, 12.9, 0.3, sz=Pt(11), bold=True, col=C_LIGHT)
rect(s, 0.2, 6.3, 12.9, 0.55, fill=RGBColor(0x12, 0x26, 0x38), border=C_ACCENT)
rect(s, 0.2, 6.3, 5.8, 0.55, fill=RGBColor(0x00, 0x96, 0x88))
tb(s, "Currently used (safe pack ~45%)", 0.3, 6.3, 5.7, 0.55, sz=Pt(9), col=C_BG, align=PP_ALIGN.CENTER)
rect(s, 6.0, 6.3, 4.5, 0.55, fill=RGBColor(0xCC, 0xA0, 0x00))
tb(s, "Predictive headroom packing (~35%)", 6.05, 6.3, 4.4, 0.55, sz=Pt(9), col=C_BG, align=PP_ALIGN.CENTER)
rect(s, 10.5, 6.3, 2.6, 0.55, fill=RGBColor(0x1A, 0x2E, 0x44), border=C_GRAY)
tb(s, "Safety buffer", 10.55, 6.3, 2.5, 0.55, sz=Pt(9), col=C_GRAY, align=PP_ALIGN.CENTER)

# ══════════════════════════════════════════════════════════════════════════════
# SLIDE 6 — DUAL PREDICTION MODEL
# ══════════════════════════════════════════════════════════════════════════════
s = slide()
header(s, "Dual Prediction Model Architecture",
       "Generalized model for new VMs → Curated model as history accumulates")

rect(s, 0.2, 1.4, 5.8, 4.5, fill=C_DARK_BOX, border=C_GREEN, bw=Pt(1.8))
tb(s, "GENERALIZED MODEL", 0.25, 1.42, 5.7, 0.5, sz=Pt(14), bold=True, col=C_GREEN, align=PP_ALIGN.CENTER)
bullets(s, [
    "Input: VM size, type, CPU/RAM allocation",
    "Input: Temporal patterns across similar VMs (Coach paper approach)",
    "Input: Trend features (e.g., large VMs last longer, use more memory)",
    "Used for: All newly admitted VMs",
    "Provides: Predicted usage curve + peak time estimate",
    "Limitation: Generic — less accurate than curated",
], 0.35, 1.98, 5.5, 3.7, sz=Pt(11.5))

rect(s, 7.3, 1.4, 5.8, 4.5, fill=C_DARK_BOX, border=C_BRONZE, bw=Pt(1.8))
tb(s, "CURATED MODEL", 7.35, 1.42, 5.7, 0.5, sz=Pt(14), bold=True, col=C_BRONZE, align=PP_ALIGN.CENTER)
bullets(s, [
    "Input: This VM's own usage history (Resource Central: VMs show consistency across lifetimes)",
    "Input: Observed peak times, cycles, application patterns",
    "Input: Seasonal and event-triggered variations",
    "Used for: VMs with sufficient history (e.g., 7+ days)",
    "Provides: High-accuracy per-VM spike forecast",
    "Continuously retrained as new data arrives",
], 7.45, 1.98, 5.5, 3.7, sz=Pt(11.5))

tb(s, "➜", 6.3, 3.3, 0.7, 0.5, sz=Pt(28), bold=True, col=C_ACCENT, align=PP_ALIGN.CENTER)
tb(s, "Gradual\ntransition", 6.0, 3.78, 1.3, 0.55, sz=Pt(10), col=C_ACCENT, align=PP_ALIGN.CENTER)

# Timeline
rect(s, 0.2, 6.08, 12.9, 0.06, fill=C_GRAY)
for frac, label, col in [(0.0, "VM\nAdmitted", C_GREEN), (0.35, "7+ days\nhistory", C_ACCENT), (0.65, "Hybrid\nBlend", C_YELLOW), (1.0, "Full\nCurated", C_BRONZE)]:
    x = 0.2 + frac * 12.9
    rect(s, x-0.07, 5.92, 0.14, 0.35, fill=C_ACCENT)
    tb(s, label, x-0.5, 6.22, 1.0, 0.55, sz=Pt(9), col=col, align=PP_ALIGN.CENTER)
tb(s, "Time →", 12.5, 6.02, 0.8, 0.28, sz=Pt(10), col=C_GRAY)

# ══════════════════════════════════════════════════════════════════════════════
# SLIDE 7 — ADMISSION CONTROL FLOWCHART  (visual flowchart)
# ══════════════════════════════════════════════════════════════════════════════
s = slide()
header(s, "Admission Control Flow",
       "Node decides YES or NO — cluster manager routes accordingly")

cx = 6.2
# Flow nodes
label_box(s, "New VM Request\narrives at Cluster Manager", cx-1.5, 1.38, 3.0, 0.55, fill=C_ACCENT, border=C_ACCENT, col=C_BG, bold=True)
arrow(s, cx, 1.93, cx, 2.2)
label_box(s, "Bin Packing selects\nCandidate Node", cx-1.5, 2.2, 3.0, 0.55)
arrow(s, cx, 2.75, cx, 3.0)
label_box(s, "Query Node's\nAdmission Control Model", cx-1.5, 3.0, 3.0, 0.55, border=C_GOLD, col=C_GOLD, bold=True)
arrow(s, cx, 3.55, cx, 3.8)
label_box(s, "Node Analysis:\n• Generalized model → predict new VM demand\n• Curated models → forecast existing VM peaks\n• Temporal conflict check vs physical RAM\n• SLA budget check: can node absorb this VM?",
          cx-1.7, 3.8, 3.4, 1.1, border=C_ACCENT)
arrow(s, cx, 4.9, cx, 5.1)
label_box(s, "Decision:\nAccept?", cx-1.0, 5.1, 2.0, 0.55, border=C_YELLOW, col=C_YELLOW, bold=True)

# YES branch
arrow(s, cx+1.0, 5.38, 10.2, 5.38, col=C_GREEN, w=Pt(2))
tb(s, "YES", 8.4, 5.15, 0.8, 0.28, sz=Pt(11), bold=True, col=C_GREEN)
label_box(s, "ACCEPT:\nAdmit VM to node\nStart curated model learning", 10.2, 5.0, 2.9, 0.75, fill=RGBColor(0x1B, 0x4A, 0x22), border=C_GREEN, col=C_GREEN)

# NO branch
arrow(s, cx-1.0, 5.38, 2.7, 5.38, col=C_RED, w=Pt(2))
tb(s, "NO", 2.9, 5.15, 0.6, 0.28, sz=Pt(11), bold=True, col=C_RED)
label_box(s, "REJECT:\nReturn to Cluster Manager", 0.2, 5.0, 2.5, 0.75, fill=RGBColor(0x4A, 0x1B, 0x1B), border=C_RED, col=C_RED)
arrow(s, 1.45, 5.75, 1.45, 6.15, col=C_RED)
label_box(s, "Try next node from\nbin-pack candidates", 0.2, 6.15, 2.5, 0.55)

# Right side detail
rect(s, 10.2, 5.9, 2.9, 0.95, fill=C_DARK_BOX, border=C_GREEN)
bullets(s, ["Node monitors VM continuously","Updates curated model over time","Tracks SLA downtime budget D_i"], 10.3, 5.95, 2.7, 0.88, sz=Pt(9.5))

# ══════════════════════════════════════════════════════════════════════════════
# SLIDE 8 — MONITORING + MEMORY SHARING + TERMINATION FLOWCHART
# ══════════════════════════════════════════════════════════════════════════════
s = slide()
header(s, "Continuous Monitoring, Memory Sharing & Termination",
       "Predict spike → share memory early → terminate only as last resort, only if SLA budget allows")

phases = [
    ("1. PREDICT SPIKE", C_ACCENT,
     "Both models continuously forecast\naggregate memory demand.\n\nIf predicted total approaches\nphysical RAM → trigger sharing.\n\nDoes NOT wait for overload to occur."),
    ("2. MEMORY SHARING\n(Abstracted)", C_GOLD,
     "Pull available memory from VMs\nnot expected to peak soon.\n\nGive it to VMs about to spike.\n\nUnderlying technique: ballooning\n(Waldspurger, 2002) — treated as\na known, available mechanism.\nWe do not re-derive it."),
    ("3. TERMINATION\n(Last Resort)", C_RED,
     "Only if sharing is insufficient:\n\n1. Compute B_i for all VMs\n   B_i = D_max − D_i\n\n2. Filter: B_i > τ only\n   (relaunch threshold)\n\n3. Terminate VM with max B_i\n\n4. Notify cluster manager:\n   relaunch on another node\n   within threshold τ"),
]
for i, (title, col, body) in enumerate(phases):
    x = 0.2 + i * 4.35
    rect(s, x, 1.38, 4.1, 0.62, fill=col)
    tb(s, title, x, 1.38, 4.1, 0.62, sz=Pt(12.5), bold=True,
       col=C_BG if col != C_RED else C_WHITE, align=PP_ALIGN.CENTER)
    rect(s, x, 2.0, 4.1, 4.2, fill=C_DARK_BOX, border=col)
    tb(s, body, x+0.1, 2.06, 3.9, 4.1, sz=Pt(11))

for ax in [4.3, 8.65]:
    arrow(s, ax, 4.05, ax+0.25, 4.05, col=C_GRAY, w=Pt(2.5))
    tb(s, "if\ninsufficient", ax-0.15, 3.72, 0.65, 0.5, sz=Pt(9), col=C_GRAY, align=PP_ALIGN.CENTER)

rect(s, 0.2, 6.35, 12.9, 0.78, fill=RGBColor(0x2A, 0x15, 0x00), border=C_BRONZE, bw=Pt(1.5))
tb(s,
   "SLA Constraint:  D_max = (1−α) × T_month  |  "
   "VM eligible for termination only if B_i = D_max − D_i > τ  |  "
   "Terminate highest-budget VM first  |  "
   "α = 99.99% → D_max ≈ 4.32 min → very limited termination headroom → model must predict well",
   0.35, 6.37, 12.6, 0.74, sz=Pt(10.5), col=C_BRONZE)

# ══════════════════════════════════════════════════════════════════════════════
# SLIDE 9 — SIMPLIFICATIONS & SCOPE
# ══════════════════════════════════════════════════════════════════════════════
s = slide()
header(s, "Simplifications & Theoretical Scope",
       "What we model vs. what we abstract away — clean, justified boundaries")

in_scope = [
    "Node-level predictive admission control model",
    "Dual prediction models: generalized + curated",
    "Single SLA parameter α with per-VM downtime budget tracking",
    "Memory sharing abstraction (ballooning treated as black box)",
    "SLA budget-aware termination (B_i > τ constraint)",
    "Cluster manager ↔ node communication protocol",
    "Simulation: Azure trace data + synthetic workloads",
]
out_scope = [
    "Bin-packing algorithm design (cluster manager's responsibility)",
    "Swap management — excluded (not used in modern CSPs)",
    "Process-level VM termination (VMs are black boxes)",
    "Multi-node coordination, live migration",
    "Per-VM heterogeneous SLA contracts (future work)",
    "Network and I/O resource management",
]
rect(s, 0.2, 1.38, 6.3, 0.5, fill=C_GREEN)
tb(s, "IN SCOPE", 0.2, 1.38, 6.3, 0.5, sz=Pt(14), bold=True, col=C_BG, align=PP_ALIGN.CENTER)
rect(s, 0.2, 1.88, 6.3, 4.7, fill=C_DARK_BOX, border=C_GREEN)
bullets(s, ["✓  " + i for i in in_scope], 0.35, 2.0, 6.0, 4.5, sz=Pt(11.5), prefix="")

rect(s, 6.8, 1.38, 6.3, 0.5, fill=C_RED)
tb(s, "OUT OF SCOPE (Future Work)", 6.8, 1.38, 6.3, 0.5, sz=Pt(14), bold=True, col=C_WHITE, align=PP_ALIGN.CENTER)
rect(s, 6.8, 1.88, 6.3, 4.7, fill=C_DARK_BOX, border=C_RED)
bullets(s, ["✗  " + i for i in out_scope], 6.95, 2.0, 6.0, 4.5, sz=Pt(11.5), col=C_GRAY, prefix="")

tb(s, "Theoretical model → validated by simulation → findings generalizable to real deployments",
   0.2, 6.82, 12.9, 0.38, sz=Pt(12), col=C_ACCENT, align=PP_ALIGN.CENTER)

# ══════════════════════════════════════════════════════════════════════════════
# SLIDE 10 — SIMULATION METHODOLOGY
# ══════════════════════════════════════════════════════════════════════════════
s = slide()
header(s, "Simulation Methodology",
       "Discrete-event simulation on a single-node model — real + synthetic data")

cols = [
    ("Real Data", C_TEAL,
     "Microsoft Azure Public VM Trace\n(Resource Central, Cortez et al. 2017)\n\n"
     "• VM lifecycle: creation, deletion, CPU/RAM utilization at regular intervals\n"
     "• Behavioral consistency across VM lifetimes\n"
     "• Empirical arrival distributions\n"
     "• Basis for training generalized model trends\n"
     "• Basis for training curated model on per-VM history"),
    ("Synthetic Data", C_GOLD,
     "Generated to stress-test edge cases:\n\n"
     "• Simultaneous peak events across all VMs\n"
     "• Rapidly shifting usage patterns\n"
     "• Adversarial admission sequences\n"
     "• Near-SLA-limit termination scenarios\n"
     "• Scenarios designed to break the model's assumptions"),
    ("Metrics", C_ACCENT,
     "Primary evaluation metrics:\n\n"
     "• DRAM utilization achieved\n  (target ~80% vs baseline ~45%)\n"
     "• SLA violation rate\n  (D_i > D_max per VM per month)\n"
     "• Number of terminations per month\n"
     "• Prediction accuracy (MAE/RMSE)\n"
     "• Admission acceptance rate"),
]
for i, (title, col, body) in enumerate(cols):
    x = 0.2 + i * 4.35
    rect(s, x, 1.38, 4.1, 0.55, fill=col)
    tb(s, title, x, 1.38, 4.1, 0.55, sz=Pt(14), bold=True, col=C_BG, align=PP_ALIGN.CENTER)
    rect(s, x, 1.93, 4.1, 4.55, fill=C_DARK_BOX, border=col)
    tb(s, body, x+0.1, 2.0, 3.9, 4.45, sz=Pt(11.5))

tb(s, "Simulation components: Node model  |  Cluster manager stub  |  Ground truth evaluator",
   0.2, 6.65, 12.9, 0.5, sz=Pt(12), col=C_LIGHT, align=PP_ALIGN.CENTER)

# ══════════════════════════════════════════════════════════════════════════════
# SLIDE 11 — NOVELTY VALIDATION
# ══════════════════════════════════════════════════════════════════════════════
s = slide()
header(s, "Novelty & Research Positioning",
       "Where our work stands relative to existing literature")

points = [
    (C_GREEN,  "Node as active decision-maker — novel architecture",
     "Standard practice: cluster manager makes all admission decisions; the node is passive. Our model reverses this: the node "
     "actively accepts or rejects based on its own predictive state. This decoupled advisory architecture is not the dominant "
     "pattern in the reviewed literature (Coach, VMMB, Blagodurov et al. all operate at the cluster level)."),
    (C_GOLD,   "Dual model (generalized → curated) at the node level — novel combination",
     "Coach exploits temporal patterns cluster-wide. Resource Central demonstrates per-VM behavioral consistency. "
     "Combining both into a single node-resident model that transitions from generalized to curated is a novel framing "
     "not present in the reviewed papers."),
    (C_ACCENT, "SLA downtime budget as a termination constraint — clean and grounded",
     "The B_i > τ eligibility constraint directly reflects real cloud SLA contracts (Akamai 99.99%). "
     "A single α parameter unifying all VMs mirrors production practice. This is defensible against "
     "reviewers because it is empirically grounded, not an arbitrary design choice."),
    (C_BRONZE, "Excluding swap is accurate, not a gap",
     "AWS EC2, Azure VMs, GCP Compute, and Akamai all operate without production swap. The Coach paper's "
     "reclamation chain also avoids swap. This exclusion is the correct modeling choice."),
    (C_SILVER, "Theoretical + simulation is a complete capstone deliverable",
     "Validated by Azure trace + synthetic stress tests. Matches the scope of published research papers "
     "(see Blagodurov et al., Coach). The framework is generalizable; implementation is future work."),
]
y = 1.38
for col, title, body in points:
    rect(s, 0.2, y, 0.35, 0.72, fill=col)
    rect(s, 0.55, y, 12.55, 0.72, fill=C_DARK_BOX, border=col)
    tb(s, title, 0.65, y+0.02, 5.2, 0.35, sz=Pt(11.5), bold=True, col=col)
    tb(s, body, 0.65, y+0.33, 12.3, 0.38, sz=Pt(10), col=C_LIGHT)
    y += 1.0
tb(s, "Bottom line: well-scoped, novel, empirically grounded. The simplifications strengthen, not weaken, the contribution.",
   0.2, 6.52, 12.9, 0.38, sz=Pt(12), bold=True, col=C_GOLD, align=PP_ALIGN.CENTER)

# ══════════════════════════════════════════════════════════════════════════════
# SLIDE 12 — DEFENSE: ANTICIPATING CRITIQUES
# ══════════════════════════════════════════════════════════════════════════════
s = slide()
header(s, "Defending Our Approach",
       "Anticipated critiques and our responses")

critiques = [
    ("'Why not just improve the cluster manager's bin-packing?'",
     "Bin-packing is a placement problem — it decides WHERE, not WHETHER. Adding temporal intelligence "
     "to the node level is orthogonal and additive. Our model works with any bin-packing algorithm without requiring "
     "changes to it. This is an architectural advantage, not a limitation."),
    ("'One SLA % for all VMs is too simplistic.'",
     "It is intentionally simple — and it matches how Akamai, one of the major CSPs, actually structures "
     "its compute SLA (99.99% across all VM types). Simplicity here is a feature: one well-defined, "
     "contractually grounded parameter is easier to reason about and defend than ad-hoc tier definitions."),
    ("'Is simulation sufficient? You need real deployment.'",
     "No published theoretical framework paper requires production deployment as a deliverable. The Coach paper "
     "(Microsoft) uses simulation with real traces. Blagodurov et al. (HP Labs/SFU) use simulation. "
     "Our simulation methodology matches the standard in the field."),
    ("'Your prediction models might not be accurate enough.'",
     "That is precisely what the simulation will measure. The dual-model design (generalized for new VMs, "
     "curated for known VMs) is a hedge against this. Moreover, the SLA budget constraint (B_i > τ) "
     "means the model errs on the side of caution: if prediction confidence is low, it rejects admission."),
    ("'What if multiple VMs are all near their SLA budget?'",
     "This is the key edge case the model handles explicitly: if B_i ≤ τ for all VMs, no termination is possible "
     "and the node rejects new admissions. This prevents cascading SLA violations. The synthetic data "
     "in our simulation will stress-test this exact scenario."),
]
y = 1.38
for question, response in critiques:
    rect(s, 0.2, y, 12.9, 1.0, fill=C_DARK_BOX, border=C_ACCENT)
    tb(s, question, 0.35, y+0.03, 12.6, 0.38, sz=Pt(11.5), bold=True, col=C_GOLD)
    tb(s, response, 0.35, y+0.4, 12.6, 0.55, sz=Pt(10.5), col=C_LIGHT)
    y += 1.1

# ══════════════════════════════════════════════════════════════════════════════
# SLIDE 13 — WAVE-BASED MEMORY DEMAND MODEL
# ══════════════════════════════════════════════════════════════════════════════
s = slide()
header(s, "Wave-Based Memory Demand Modeling",
       "Each VM is a wave — sum all waves to get aggregate node demand")

# Left: concept explanation
rect(s, 0.2, 1.38, 5.7, 5.45, fill=C_DARK_BOX, border=C_ACCENT)
tb(s, "The Core Idea", 0.25, 1.4, 5.6, 0.42, sz=Pt(13), bold=True, col=C_ACCENT)
box = s.shapes.add_textbox(Inches(0.32), Inches(1.88), Inches(5.5), Inches(4.8))
box.word_wrap = True; tf = box.text_frame; tf.word_wrap = True
wave_lines = [
    ("Each VM's memory usage = a periodic wave:", C_LIGHT),
    ("", C_WHITE),
    ("  m_i(t) = A_i0 + Σ_k[ A_ik · sin(2πf_k·t + φ_ik) ]", C_YELLOW),
    ("", C_WHITE),
    ("  A_i0  = baseline (flatline VMs: large A_i0, small A_ik)", C_WHITE),
    ("  A_ik  = spike amplitude (spiky VMs: small A_i0, large A_ik)", C_WHITE),
    ("  f_k   = frequency (daily, hourly cycles)", C_WHITE),
    ("  φ_ik  = phase — WHEN the VM peaks", C_WHITE),
    ("", C_WHITE),
    ("Aggregate node demand (superposition):", C_LIGHT),
    ("", C_WHITE),
    ("  M(t) = Σ_i m_i(t)  ← additive, closed-form", C_YELLOW),
    ("", C_WHITE),
    ("Overload detection:", C_LIGHT),
    ("  Find t* where M(t*) > RAM_physical", C_RED),
    ("", C_WHITE),
    ("Act at:  t_act = t* − τ_lead", C_GREEN),
]
for i, (line, col) in enumerate(wave_lines):
    p = tf.paragraphs[0] if i == 0 else tf.add_paragraph()
    p.space_before = Pt(2)
    r = p.add_run(); r.text = line
    r.font.size = Pt(11); r.font.color.rgb = col

# Right: visual waveform diagram (drawn with shapes)
rect(s, 6.2, 1.38, 6.9, 5.45, fill=RGBColor(0x0A, 0x1A, 0x28), border=C_GOLD, bw=Pt(1.5))
tb(s, "Waveform Illustration", 6.25, 1.4, 6.8, 0.42, sz=Pt(13), bold=True, col=C_GOLD, align=PP_ALIGN.CENTER)

# Draw simplified wave illustration using stacked rectangles to represent waveforms
wave_labels = [
    ("VM 1  (spiky — high amplitude)", C_ACCENT),
    ("VM 2  (stable — flatline)", C_GREEN),
    ("VM 3  (moderate cycle)", C_BRONZE),
    ("─────────────────────────────────────", C_GRAY),
    ("M(t) = combined waveform", C_YELLOW),
    ("RAM limit  ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─", C_RED),
    ("t* = predicted spike crossing", C_RED),
    ("τ_lead before t* → act now", C_GREEN),
]
y_wave = 1.9
for label, col in wave_labels:
    tb(s, label, 6.3, y_wave, 6.7, 0.32, sz=Pt(10.5), col=col)
    y_wave += 0.38

# Bottom: why it works
rect(s, 0.2, 6.95, 12.9, 0.38, fill=RGBColor(0x00, 0x3A, 0x52), border=C_GOLD)
tb(s,
   "Key insight: VMs with OUT-OF-PHASE peaks can be safely co-located even if individual peaks exceed headroom. "
   "The wave model quantifies phase diversity precisely — this is the mathematical basis for safe overcommitment.",
   0.32, 6.97, 12.7, 0.34, sz=Pt(11), col=C_GOLD, align=PP_ALIGN.CENTER)

# ══════════════════════════════════════════════════════════════════════════════
# SLIDE 14 — LEAD TIME VARIABLE τ_lead
# ══════════════════════════════════════════════════════════════════════════════
s = slide()
header(s, "Lead Time Variable τ_lead",
       "How far before a predicted spike should the model act?")

# τ_lead trade-off table
rect(s, 0.2, 1.38, 12.9, 0.5, fill=C_ACCENT)
for x, txt, w in [(0.3, "τ_lead Value", 2.2), (2.5, "Prediction Certainty", 3.0),
                   (5.5, "Time for Memory Sharing", 3.2), (8.7, "Risk / Trade-off", 4.2)]:
    tb(s, txt, x, 1.38, w, 0.5, sz=Pt(12), bold=True, col=C_BG, align=PP_ALIGN.CENTER)

rows = [
    ("60+ min",  "Low — long horizon,\nmore uncertainty",    "Ample — gradual ballooning",         "May act unnecessarily\nif prediction is wrong",          C_BRONZE),
    ("30 min",   "Moderate",                                  "Good — enough for\nmemory sharing",   "Balanced — recommended\nstarting value",                  C_GOLD),
    ("5–10 min", "High — near-term\nspike is clear",         "Limited — must act fast",             "Little margin for error;\nmay need immediate termination", C_ACCENT),
    ("< τ",      "N/A",                                       "Impossible",                          "Never allowed — τ_lead must\nalways exceed relaunch time τ", C_RED),
]
y = 1.88
for val, cert, time, risk, col in rows:
    rect(s, 0.2, y, 12.9, 0.75, fill=C_DARK_BOX, border=col, bw=Pt(1))
    for x, txt, w in [(0.3, val, 2.2), (2.5, cert, 3.0), (5.5, time, 3.2), (8.7, risk, 4.2)]:
        tb(s, txt, x, y+0.05, w, 0.65, sz=Pt(11), col=col if x == 0.3 else C_WHITE)
    y += 0.82

# VM lifetime interaction
rect(s, 0.2, 5.45, 12.9, 1.32, fill=RGBColor(0x00, 0x2A, 0x1A), border=C_GREEN, bw=Pt(1.5))
tb(s, "VM Lifetime Interaction", 0.3, 5.47, 6.0, 0.42, sz=Pt(13), bold=True, col=C_GREEN)
tb(s,
   "If a VM's predicted end-of-life  t_j_end  occurs BEFORE the predicted spike  t*:\n"
   "  → The spike will not materialize → no action needed → do not count this as an overload.\n"
   "This prevents the model from rejecting short-lived VMs or over-triggering memory sharing.\n"
   "VM lifetime estimates come from the generalized model (e.g., large VMs last longer — Coach/Resource Central finding).",
   0.32, 5.93, 12.6, 0.8, sz=Pt(11.5), col=C_WHITE)

# Simulation note
rect(s, 0.2, 6.88, 12.9, 0.45, fill=RGBColor(0x00, 0x3A, 0x52), border=C_GOLD)
tb(s, "In simulation: τ_lead is an experimental variable. We test multiple values to find optimal prediction-response balance.",
   0.32, 6.9, 12.7, 0.4, sz=Pt(12), col=C_GOLD, align=PP_ALIGN.CENTER)

# ══════════════════════════════════════════════════════════════════════════════
# SAVE
# ══════════════════════════════════════════════════════════════════════════════
prs.save(r"C:\Users\alric\Documents\Spring2026\Capstone_ClaudeAI\Team_Concept_Slides.pptx")
print("Saved: Team_Concept_Slides.pptx")
