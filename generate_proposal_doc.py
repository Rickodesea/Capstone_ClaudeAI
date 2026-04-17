from docx import Document
from docx.shared import Pt, Inches, RGBColor
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.enum.table import WD_TABLE_ALIGNMENT, WD_ALIGN_VERTICAL
from docx.oxml.ns import qn
from docx.oxml import OxmlElement
import copy

doc = Document()

# ── Page margins ──────────────────────────────────────────────────────────────
for section in doc.sections:
    section.top_margin    = Inches(1.0)
    section.bottom_margin = Inches(1.0)
    section.left_margin   = Inches(1.15)
    section.right_margin  = Inches(1.15)

# ── Color helpers ─────────────────────────────────────────────────────────────
NAVY   = RGBColor(0x0D, 0x1B, 0x2A)
BLUE   = RGBColor(0x00, 0x6A, 0xA6)
TEAL   = RGBColor(0x00, 0x7A, 0x8A)
DARK   = RGBColor(0x1A, 0x2E, 0x44)
GOLD   = RGBColor(0xB8, 0x86, 0x00)
GREEN  = RGBColor(0x1E, 0x6B, 0x2E)
GRAY   = RGBColor(0x50, 0x50, 0x50)
BLACK  = RGBColor(0x00, 0x00, 0x00)
WHITE  = RGBColor(0xFF, 0xFF, 0xFF)
LGRAY  = RGBColor(0xF4, 0xF6, 0xF8)
MGRAY  = RGBColor(0xE0, 0xE6, 0xED)
DBLUE  = RGBColor(0xE8, 0xF4, 0xFB)

def shade_cell(cell, hex_color):
    tc = cell._tc
    tcPr = tc.get_or_add_tcPr()
    shd = OxmlElement('w:shd')
    shd.set(qn('w:val'), 'clear')
    shd.set(qn('w:color'), 'auto')
    shd.set(qn('w:fill'), hex_color)
    tcPr.append(shd)

def set_cell_border(cell, **kwargs):
    tc = cell._tc
    tcPr = tc.get_or_add_tcPr()
    tcBorders = OxmlElement('w:tcBorders')
    for edge in ('top','left','bottom','right'):
        tag = OxmlElement(f'w:{edge}')
        tag.set(qn('w:val'), 'single')
        tag.set(qn('w:sz'), '4')
        tag.set(qn('w:color'), kwargs.get(edge, 'FFFFFF'))
        tcBorders.append(tag)
    tcPr.append(tcBorders)

def para(text, bold=False, italic=False, size=11, color=BLACK,
         align=WD_ALIGN_PARAGRAPH.LEFT, space_before=0, space_after=6):
    p = doc.add_paragraph()
    p.alignment = align
    p.paragraph_format.space_before = Pt(space_before)
    p.paragraph_format.space_after  = Pt(space_after)
    run = p.add_run(text)
    run.bold   = bold
    run.italic = italic
    run.font.size  = Pt(size)
    run.font.color.rgb = color
    return p

def heading(text, level=1):
    sizes   = {1: 16, 2: 13, 3: 11.5}
    colors  = {1: BLUE, 2: TEAL, 3: DARK}
    p = doc.add_paragraph()
    p.paragraph_format.space_before = Pt(14 if level == 1 else 10)
    p.paragraph_format.space_after  = Pt(4)
    run = p.add_run(text)
    run.bold = True
    run.font.size  = Pt(sizes.get(level, 11))
    run.font.color.rgb = colors.get(level, BLACK)
    # bottom border for level-1
    if level == 1:
        pPr = p._p.get_or_add_pPr()
        pBdr = OxmlElement('w:pBdr')
        bottom = OxmlElement('w:bottom')
        bottom.set(qn('w:val'), 'single')
        bottom.set(qn('w:sz'), '6')
        bottom.set(qn('w:color'), '0084AA')
        pBdr.append(bottom)
        pPr.append(pBdr)
    return p

def bullet(text, level=0, size=11, color=BLACK, bold_prefix=None):
    p = doc.add_paragraph(style='List Bullet')
    p.paragraph_format.left_indent   = Inches(0.25 + level * 0.25)
    p.paragraph_format.space_before  = Pt(1)
    p.paragraph_format.space_after   = Pt(3)
    if bold_prefix:
        run = p.add_run(bold_prefix)
        run.bold = True; run.font.size = Pt(size); run.font.color.rgb = color
    run = p.add_run(text)
    run.font.size = Pt(size); run.font.color.rgb = color
    return p

def add_callout(lines, bg_hex='EAF4FB', border_color=TEAL):
    """A shaded paragraph block used for callout boxes."""
    for line in lines:
        p = doc.add_paragraph()
        p.paragraph_format.left_indent  = Inches(0.3)
        p.paragraph_format.right_indent = Inches(0.3)
        p.paragraph_format.space_before = Pt(2)
        p.paragraph_format.space_after  = Pt(2)
        run = p.add_run(line)
        run.font.size = Pt(10.5)
        run.font.color.rgb = DARK

def hline():
    p = doc.add_paragraph()
    p.paragraph_format.space_before = Pt(4)
    p.paragraph_format.space_after  = Pt(4)
    pPr = p._p.get_or_add_pPr()
    pBdr = OxmlElement('w:pBdr')
    bottom = OxmlElement('w:bottom')
    bottom.set(qn('w:val'), 'single')
    bottom.set(qn('w:sz'), '4')
    bottom.set(qn('w:color'), 'CCCCCC')
    pBdr.append(bottom)
    pPr.append(pBdr)

# ══════════════════════════════════════════════════════════════════════════════
# COVER / TITLE
# ══════════════════════════════════════════════════════════════════════════════
p = doc.add_paragraph()
p.alignment = WD_ALIGN_PARAGRAPH.CENTER
p.paragraph_format.space_before = Pt(8)
p.paragraph_format.space_after  = Pt(4)
run = p.add_run("Optimal Shared Memory Utilization with Service Level\nGuarantees in Multi-Tenant Clusters")
run.bold = True; run.font.size = Pt(18); run.font.color.rgb = NAVY

para("(Working Title — see Suggested Topic Sentences section for alternatives)",
     italic=True, size=10, color=GRAY, align=WD_ALIGN_PARAGRAPH.CENTER, space_after=10)
para("DAMO 699 Capstone Project  ·  Master of Data Analytics",
     size=11, color=GRAY, align=WD_ALIGN_PARAGRAPH.CENTER, space_after=2)
para("Supervisor: Hany Osman  ·  University of Niagara Falls  ·  Spring 2026",
     size=11, color=GRAY, align=WD_ALIGN_PARAGRAPH.CENTER, space_after=2)
para("Group 3: Tha Pyay Hmu · Lhagii Tsogtbayar · Nadia Ríos · Jorge Mendoza · Alrick Grandison",
     size=10.5, color=GRAY, align=WD_ALIGN_PARAGRAPH.CENTER, space_after=16)
hline()

# ── Note on teammate draft ─────────────────────────────────────────────────
heading("Note on Team Draft Contributions", level=2)
para(
    "The team's initial proposal draft established several important framing decisions "
    "that are carried through in this document: (1) memory as the primary constraint "
    "in a multi-tenant cluster setting; (2) the scope boundary — we are not building a "
    "full cloud platform like AWS but a scheduling model and simulation; (3) the core "
    "objective tuple: maximize utilization, minimize SLA violations, maintain fairness; "
    "and (4) the use of mathematical optimization and real cluster data (Google Cluster Trace) "
    "as the analytical approach. Those anchor points shape every section below.",
    size=10.5, color=GRAY, italic=True, space_after=8
)
hline()

# ══════════════════════════════════════════════════════════════════════════════
# SECTION 1 — BASIC IDEA
# ══════════════════════════════════════════════════════════════════════════════
heading("Basic Idea  (simple explanation for the team)")
para(
    "Imagine a shared office building where multiple companies rent desk space. Each company tells "
    "the building manager 'I need 20 desks,' but on any given day they actually use 12. The building "
    "manager, following the rules, keeps 20 desks reserved per company — so half the building sits "
    "empty even when other companies are waiting for a single desk. To make things worse, some "
    "companies have urgent meetings (latency-sensitive jobs) while others are doing background filing "
    "(batch jobs), but they all wait in the same queue with no priority distinction.",
    space_after=6
)
para(
    "Now imagine a smarter building manager who: (1) looks at how much desk space each company has "
    "historically used — not just what they asked for — and reserves only what they will likely "
    "actually need; (2) gives priority to the company that has been waiting longest or has used the "
    "least space so far; and (3) guarantees that companies with urgent meetings always get their "
    "desks on time, even if the filing team has to wait.",
    space_after=6
)
para(
    "That is what this project builds — but for cloud computing. The 'building' is a Kubernetes "
    "cluster. The 'companies' are tenants. The 'desks' are memory (and CPU). The smarter building "
    "manager is our custom Kubernetes scheduler. We test it by replaying real workload data from "
    "Google's production cluster through a simulation — no live cloud account needed.",
    space_after=4
)

# ══════════════════════════════════════════════════════════════════════════════
# SECTION 2 — CONCEPTS
# ══════════════════════════════════════════════════════════════════════════════
heading("Key Concepts")
para("Definitions for team members who are new to cluster scheduling.", size=10.5, color=GRAY, italic=True, space_after=6)

concepts = [
    ("Multi-Tenant Cluster",
     "A shared pool of servers where multiple organizations ('tenants') submit workloads. "
     "They share the same physical resources but expect isolation and fairness. "
     "Example: a university research cluster shared by several research groups."),
    ("Kubernetes",
     "The dominant open-source cluster manager — same category as Google Borg, Apache Mesos. "
     "It accepts workload submissions, decides which server runs each one, and keeps them running. "
     "Think of it as the operating system for a cluster. It does not care whether the servers "
     "underneath are on AWS, GCP, or a university datacenter."),
    ("Pod / Container",
     "The unit of work in Kubernetes. A container holds one application and its dependencies, "
     "starts in seconds, and shares the host's OS kernel. A pod is one or more containers "
     "scheduled together. When we say 'job,' we mean a pod submitted by a tenant."),
    ("Scheduler vs. Optimizer — what is the difference?",
     "The SCHEDULER is the mechanism: the Kubernetes component that assigns pods to nodes "
     "(it answers 'which server runs this job?'). "
     "The OPTIMIZER is the decision logic: the mathematical/ML model that tells the scheduler "
     "which choice is best (it answers 'given all options, what is the best placement?'). "
     "In our project, we build the optimizer — a prediction model + fairness model + "
     "SLA enforcement logic — and plug it into Kubernetes as a scheduler plugin. "
     "The scheduler runs our optimizer's recommendations."),
    ("Admission Control",
     "The front-door check before the scheduler even sees a job. "
     "It asks: 'Should we accept this job right now?' "
     "If predicted memory usage would overflow the node, the job is queued. "
     "Our predictive admission control is what makes safe overcommitment possible."),
    ("Resource Overcommitment",
     "Accepting more workloads than the server's declared capacity on paper, "
     "relying on the fact that not all jobs will peak simultaneously. "
     "Safe when done with accurate prediction. Dangerous without it."),
    ("SLA (Service Level Agreement)",
     "A formal contract stating what level of service a tenant is guaranteed — "
     "e.g., 'your job completes within X minutes' or 'less than 5 minutes of downtime per month.' "
     "Violating the SLA has financial and contractual consequences. "
     "Our system prevents violations proactively, not reactively."),
    ("Memory OOM Kill",
     "When a container exceeds its memory limit, the Linux kernel kills it immediately — no warning. "
     "This is fundamentally different from CPU throttling, which just slows the job down. "
     "Memory violations are fatal and directly violate SLAs. "
     "This is why memory is the primary constraint in our model."),
    ("Dominant Resource Fairness (DRF)",
     "A fairness algorithm from Ghodsi et al. (2011). For each tenant, find which resource "
     "(CPU or memory) they consume the largest fraction of across the cluster. "
     "DRF ensures no tenant's dominant share grows disproportionately compared to others. "
     "Proven properties: no tenant prefers another's allocation, everyone gets their fair share, "
     "the system cannot be gamed by lying about needs."),
    ("Gini Coefficient",
     "A number between 0 and 1 measuring inequality in resource distribution across tenants. "
     "0 = perfect equality. 1 = total inequality. "
     "Alatawi (2025) reduces it from 0.25 to 0.10 — that improvement range is our target."),
    ("Google Cluster Trace v3",
     "A publicly available dataset from Google containing real job records from their production "
     "cluster: arrival times, declared resource requests, actual measured usage, job duration, "
     "tenant IDs, priority levels. This is our training and simulation dataset."),
]
for term, defn in concepts:
    p = doc.add_paragraph()
    p.paragraph_format.space_before = Pt(5)
    p.paragraph_format.space_after  = Pt(3)
    r = p.add_run(term + "  ")
    r.bold = True; r.font.size = Pt(11); r.font.color.rgb = TEAL
    r2 = p.add_run(defn)
    r2.font.size = Pt(11); r2.font.color.rgb = BLACK

# ══════════════════════════════════════════════════════════════════════════════
# SECTION 3 — POINTERS
# ══════════════════════════════════════════════════════════════════════════════
heading("Pointers  (scope, platform, assumptions)")

pointer_items = [
    ("Platform", "We focus on Kubernetes as our cluster management platform. "
     "It is the standard for multi-tenant workloads in production cloud systems, "
     "is well-documented, and has a formal Scheduling Framework that lets us plug in "
     "custom logic without modifying Kubernetes itself."),
    ("Assumptions", None),
    (None, "(1) All workloads are containerized Kubernetes Jobs or Deployments — no VM-level scheduling."),
    (None, "(2) Google Cluster Trace v3 is our primary dataset."),
    (None, "(3) Simulation is the primary evaluation method — no live cluster required."),
    (None, "(4) Memory is the primary scarce resource driving SLA violations — CPU throttling slows jobs down, memory OOM kills terminate them."),
    (None, "(5) Tenants over-declare memory requests as a safety buffer — actual usage is consistently lower, and that gap is the overcommitment opportunity."),
    ("Scope boundary", "We are not building a full cloud platform like AWS. "
     "We build the scheduling decision model (optimizer + admission control) and evaluate it via simulation."),
    ("Possible simplification", "Start with memory as the single constrained resource and CPU as secondary. "
     "This matches the group's original angle and keeps the math tractable for capstone scope."),
    ("Additive peaks insight", "Model each job's memory usage as a pulse (ramps up, plateaus, ramps down). "
     "Total memory on a node = sum of all active pulses. "
     "Admission decision: will adding this new pulse cause the sum to cross the node's memory limit at any point? "
     "If yes → deny or delay."),
    ("K8s hook points", "Our scheduler plugs into three Kubernetes phases: "
     "(1) Admission Webhook — ML-based admit/deny before scheduling; "
     "(2) Score phase — DRF fairness + temporal co-location ranking; "
     "(3) Runtime Controller — Prometheus-driven cgroup adjustments every 5 seconds."),
]
for label, text in pointer_items:
    if label and text:
        p = doc.add_paragraph()
        p.paragraph_format.left_indent  = Inches(0.0)
        p.paragraph_format.space_before = Pt(4)
        p.paragraph_format.space_after  = Pt(2)
        r = p.add_run(f"{label}:  ")
        r.bold = True; r.font.size = Pt(11); r.font.color.rgb = BLUE
        r2 = p.add_run(text)
        r2.font.size = Pt(11); r2.font.color.rgb = BLACK
    elif label and not text:
        p = doc.add_paragraph()
        p.paragraph_format.space_before = Pt(5)
        p.paragraph_format.space_after  = Pt(1)
        r = p.add_run(f"{label}:")
        r.bold = True; r.font.size = Pt(11); r.font.color.rgb = BLUE
    elif not label and text:
        bullet(text, level=1, size=10.5)

# ══════════════════════════════════════════════════════════════════════════════
# SECTION 4 — SUGGESTED TOPIC SENTENCES
# ══════════════════════════════════════════════════════════════════════════════
heading("Suggested Topic Sentences")

options = [
    ("Option 1 — technical, precise",
     "\"This project designs a predictive scheduling and admission control system for multi-tenant Kubernetes clusters "
     "that closes the gap between declared resource requests and actual memory usage, enabling safe overcommitment "
     "while enforcing per-tenant SLA compliance and fairness.\""),
    ("Option 2 — optimization framing (matches group's angle)",
     "\"We propose an optimization-based scheduling model for multi-tenant Kubernetes clusters that focuses on shared "
     "memory utilization as the primary constraint, using machine learning to dynamically allocate resources, maximize "
     "cluster utilization, minimize SLA violations, and maintain fairness across tenants.\""),
    ("Option 3 — problem-first, accessible",
     "\"Cloud clusters waste up to 40–60% of available memory because schedulers rely on declared requests rather than "
     "actual usage — this project builds a smarter Kubernetes scheduler that uses tenant workload history to predict "
     "real memory demand, safely pack more jobs onto each node, and prevent SLA violations before they occur.\""),
]
for label, text in options:
    p = doc.add_paragraph()
    p.paragraph_format.space_before = Pt(6)
    p.paragraph_format.space_after  = Pt(2)
    r = p.add_run(label + "  ")
    r.bold = True; r.font.size = Pt(11); r.font.color.rgb = GREEN
    p2 = doc.add_paragraph()
    p2.paragraph_format.left_indent  = Inches(0.3)
    p2.paragraph_format.space_before = Pt(1)
    p2.paragraph_format.space_after  = Pt(4)
    r2 = p2.add_run(text)
    r2.italic = True; r2.font.size = Pt(11); r2.font.color.rgb = DARK

# ══════════════════════════════════════════════════════════════════════════════
# SECTION 5 — SUGGESTED HYPOTHESIS
# ══════════════════════════════════════════════════════════════════════════════
heading("Suggested Hypothesis")
para(
    "We hypothesize that a Kubernetes scheduler augmented with (1) machine learning-based predictive "
    "admission control, (2) Dominant Resource Fairness-aware placement, and (3) dynamic cgroup-based "
    "SLA enforcement will achieve significantly higher cluster memory utilization than the default "
    "Kubernetes scheduler while maintaining SLA compliance rates above 95% and reducing inter-tenant "
    "fairness inequality, as measured by Gini coefficient and dominant share variance, on workloads "
    "replayed from the Google Cluster Trace v3.",
    space_after=6
)
para("Specifically, we expect:", bold=True, size=11, space_after=2)
bullet("Cluster memory utilization to increase from ~45–60% (default K8s) to ≥ 85%")
bullet("SLA violation rate to remain below 5% — matching or improving on Priya (2025)'s benchmark")
bullet("Gini coefficient across tenants to decrease from ~0.25 toward ~0.10 — matching Alatawi (2025)'s range")
bullet("Prediction accuracy (MAPE) below 5% using Random Forest on tenant history features")

# ══════════════════════════════════════════════════════════════════════════════
# SECTION 6 — SUGGESTED INTRODUCTION
# ══════════════════════════════════════════════════════════════════════════════
heading("Suggested Introduction")
para(
    "Cloud infrastructure costs are escalating on multiple fronts. Conventional DRAM supplies are "
    "contracting as manufacturers redirect production toward high-bandwidth memory for AI accelerators, "
    "with prices projected to rise 54–116% year-over-year (S&P Global, 2026). The operational cost of "
    "leaving compute and memory resources idle compounds these pressures. This creates a compelling "
    "imperative for cloud providers: extract significantly more value from existing hardware before "
    "buying more. The bottleneck is not hardware availability — it is the scheduler.",
    space_after=8
)
para(
    "Current cluster memory utilization in production environments averages between 40% and 60%, meaning "
    "that on any given node, nearly half its memory sits idle while tenants wait for their jobs to start "
    "(Chaudhari, 2025). This waste exists not because hardware is unavailable, but because schedulers are "
    "conservative. The default Kubernetes scheduler places workloads based solely on declared resource "
    "requests and current availability, with no awareness of how much memory jobs will actually use, when "
    "co-located jobs will peak, or whether admitting a new job will cause a neighbor's SLA to be violated "
    "in the next thirty minutes. The result is predictable: either the scheduler over-admits and memory "
    "contention causes OOM kills, or it under-admits and expensive hardware idles.",
    space_after=8
)
para(
    "Multi-tenancy sharpens the problem. When multiple teams share the same cluster, a single greedy "
    "tenant can monopolize memory, causing others to queue arbitrarily long. Kubernetes has no native "
    "fairness mechanism — it processes requests in arrival order without regard to whether one tenant has "
    "already consumed a disproportionate share of cluster memory. Latency-sensitive jobs compete against "
    "batch workloads with no systematic prioritization, and memory violations are discovered after the "
    "fact rather than prevented at admission time.",
    space_after=8
)
para(
    "This project addresses the gap between what the Kubernetes scheduler currently does and what a "
    "production multi-tenant cluster requires. The gap is precise: no existing system combines predictive "
    "admission control, multi-resource fairness, and runtime SLA enforcement into a single coherent "
    "Kubernetes scheduler. Chaudhari (2025) describes the need for exactly this combination — a Workload "
    "Classifier, Fairness Engine, and Predictive Resource Manager — but the framework exists only as a "
    "proposal with no implementation or evaluation. We build it.",
    space_after=8
)
para(
    "The proposed system is a custom Kubernetes scheduler plugin with three integrated components. First, "
    "a predictive admission layer trains a Random Forest model on tenant workload history from the Google "
    "Cluster Trace to predict the actual peak memory utilization of each submitted job. Tenants routinely "
    "over-declare memory requests as a safety buffer; the actual usage is consistently lower. The model "
    "learns this gap per tenant. A new job is admitted only if the predicted combined memory usage of all "
    "co-located jobs stays below a safe threshold. Second, a fairness-aware placement layer applies "
    "Dominant Resource Fairness (Ghodsi et al., 2011) to rank candidate nodes during scheduling. The "
    "tenant with the smallest share of their dominant resource gets priority for the next scheduling slot, "
    "ensuring no team waits disproportionately long. Third, a runtime enforcement layer assigns memory "
    "priority weights to pods via Kubernetes QoS classes and dynamic cgroup controls. Critical jobs "
    "receive guaranteed memory access; batch jobs operate on remaining capacity and are dynamically "
    "throttled when a co-located critical job approaches its SLA boundary.",
    space_after=8
)
para(
    "The system is evaluated against the Google Cluster Trace v3 using discrete-event simulation, "
    "comparing against the default Kubernetes scheduler and a static DRF baseline. Target outcomes are "
    "cluster memory utilization above 85%, SLA compliance above 95%, and inter-tenant fairness variance "
    "below 10%.",
    space_after=4
)

# ══════════════════════════════════════════════════════════════════════════════
# SECTION 7 — BRIEF LITERATURE REVIEW TABLE
# ══════════════════════════════════════════════════════════════════════════════
heading("Brief Literature Review")
para("18 papers reviewed. Table below summarizes each paper's contribution, the gap it addresses, what we take from it, and what kind of model or algorithm it uses.", size=10.5, color=GRAY, italic=True, space_after=8)

col_widths = [Inches(1.55), Inches(1.7), Inches(1.35), Inches(1.7), Inches(1.85)]
col_headers = ["Paper", "Core Contribution", "Gap Addressed", "What We Include", "Model Type"]

tbl = doc.add_table(rows=1 + 18, cols=5)
tbl.style = 'Table Grid'
tbl.alignment = WD_TABLE_ALIGNMENT.CENTER

# Header row
hdr_row = tbl.rows[0]
for i, (cell, w, hdr) in enumerate(zip(hdr_row.cells, col_widths, col_headers)):
    cell.width = w
    shade_cell(cell, '1A2E44')
    cell.vertical_alignment = WD_ALIGN_VERTICAL.CENTER
    p = cell.paragraphs[0]
    p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    run = p.add_run(hdr)
    run.bold = True; run.font.size = Pt(9.5); run.font.color.rgb = WHITE

rows_data = [
    ("Chaudhari (2025)",
     "K8s fails for AI/ML; proposes 4-component framework — never built",
     "Gap anchor",
     "Architecture blueprint; utilization baselines",
     "None — conceptual framework"),
    ("Jiang Zhi (2025)",
     "Overcommitment study; Clovers Python simulator; real trace datasets",
     "Simulation environment",
     "Simulator design + trace datasets",
     "None — rule-based simulator"),
    ("Coach / Reidys (2025)",
     "Co-locate workloads with non-overlapping temporal memory peaks; +26% capacity",
     "Overcommitment strategy",
     "Temporal co-location scoring",
     "Prediction: temporal pattern forecasting"),
    ("DRF — Ghodsi (2011)",
     "Dominant Resource Fairness: LP-proven multi-resource fair allocation",
     "Fairness backbone",
     "DRF algorithm as Score phase",
     "Fairness algorithm: LP-proven dominance"),
    ("Resource Central (2017)",
     "RF predicts P95 CPU/memory from tenant subscription history; 79–90% accuracy",
     "Prediction architecture",
     "RF model + tenant history features",
     "Prediction model: Random Forest (P95 util.)"),
    ("Blagodurov et al.",
     "cgroups weights for critical vs batch; near-100% server util. with SLA maintained",
     "Runtime SLA enforcement",
     "Dynamic cgroup weight controller",
     "Scheduler optimizer: cgroups weight equations"),
    ("Wang & Yang (2025)",
     "LSTM+DQN on Kubernetes; 32.5% util. gain, 43.3% latency reduction",
     "K8s deployment blueprint",
     "Plugin architecture; comparison baseline",
     "Prediction (LSTM) + RL scheduler (DQN)"),
    ("Doukha (2025)",
     "RF beats LSTM clearly: MAPE 2.65% vs 17.43% on CPU/memory prediction",
     "Model selection proof",
     "RF as primary predictor; MAPE metric",
     "Prediction comparison: RF vs LSTM"),
    ("Atropos / Hu (2025)",
     "Throttle culprit workload during overload — not innocent victim",
     "Overload fallback",
     "Targeted pod throttling on overload",
     "None — monitoring-based detection"),
    ("Priya (2025)",
     "SloPolicy CRD + K8s plugin + Prometheus loop; 45% P99 reduction, <5% SLA violation",
     "K8s architecture reference",
     "CRD design; plugin structure; eval metrics",
     "Scheduler optimizer: QoS scoring function"),
    ("Kofi (2025)",
     "LSTM on Google Cluster Trace v3; R²=0.99 with preprocessing pipeline",
     "Dataset + preprocessing",
     "Google Trace + Savitzky-Golay pipeline",
     "Prediction model: LSTM (R²=0.99)"),
    ("Perera (2025/2026)",
     "RL schedulers suffer model drift and interpretability issues in production",
     "Model choice justification",
     "RF interpretability arg.; drift detection",
     "None — review / landscape paper"),
    ("Pinnapareddy (2025)",
     "Right-sizing, bin packing, autoscaling in K8s; cost and sustainability framing",
     "Motivation + tooling",
     "Kubecost for per-tenant eval.",
     "None — practitioner analysis"),
    ("Patchamatla",
     "K8s on OpenStack; VM-hosted vs bare-metal containers; scheduler coordination",
     "Deployment context",
     "Architecture reference",
     "None — experimental comparison"),
    ("Liu & Guitart (2025)",
     "In-node DRC: group-aware cgroup tuning; 242–319% throughput improvement",
     "In-node enforcement",
     "DRC concept post-placement",
     "Scheduler optimizer: in-node cgroup assignment"),
    ("Kovalenko (2024)",
     "Formal discrete combinatorial optimization model for K8s scheduling",
     "Math formalization",
     "Constraint + objective structure",
     "Scheduler optimizer: discrete combinatorial LP"),
    ("Alatawi (2025)",
     "RL MDP for serverless multitenancy; Gini fairness; 98% SLA, 50% latency reduction",
     "Fairness metric + RL compare",
     "Gini coefficient metric; RL baseline",
     "RL policy model: MDP-based allocation"),
    ("Zhao et al. (2021)",
     "Formal admission control + profit optimization for AaaS; deadline + budget SLA",
     "Admission formalization",
     "Admission algorithm structure",
     "Scheduler optimizer: admission control + profit LP"),
]

for j, row_data in enumerate(rows_data):
    row = tbl.rows[j + 1]
    bg = 'F4F6F8' if j % 2 == 0 else 'FFFFFF'
    for i, (cell, val, w) in enumerate(zip(row.cells, row_data, col_widths)):
        cell.width = w
        shade_cell(cell, bg)
        p = cell.paragraphs[0]
        p.alignment = WD_ALIGN_PARAGRAPH.LEFT
        run = p.add_run(val)
        run.font.size = Pt(9)
        if i == 0:
            run.bold = True; run.font.color.rgb = TEAL
        elif i == 4:
            run.font.color.rgb = BLUE
        else:
            run.font.color.rgb = BLACK

doc.add_paragraph()  # spacing after table

# ══════════════════════════════════════════════════════════════════════════════
# SAVE
# ══════════════════════════════════════════════════════════════════════════════
out = r"C:\Users\alric\Documents\Spring2026\Capstone_ClaudeAI\Proposal_Draft.docx"
doc.save(out)
print(f"Saved: {out}")
