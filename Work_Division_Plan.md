# Capstone Proposal — Work Division Plan
## Group 3 | DAMO 699 | Spring 2026

---

> **APA Style Reminder (everyone reads this):**
> - All in-text citations: Author (Year) or (Author, Year) format
> - All sources must appear in the References section in full APA format
> - Example: Ghodsi, A., Zaharia, M., Hindman, B., Konwinski, A., Shenker, S., & Stoica, I. (2011). Dominant resource fairness: Fair allocation of multiple resource types. *Proceedings of NSDI '11*, 323–336.
> - Use academic/scientific writing: third person, past tense for prior work, present tense for your own claims
> - No informal language. Write in complete sentences. Tables and figures must have captions.
> - Every claim that is not your own must be cited.

---

## Proposal Structure (Template: DAMO 699_Template Proposal)

| Section | Title | Assigned To | Estimated Pages |
|---|---|---|---|
| 1 | Introduction | Member 3 | ~0.75 page |
| 2 | Problem Definition | Member 3 | ~0.50 page |
| 3 | Analytical Objective (Research Question + Hypothesis) | Member 4 | ~0.50 page |
| 4 | Proposed Data Sources | Member 4 | ~0.50 page |
| 5 | Proposed Analytical Approach | **Member 1 (Alrick)** | ~1.50 pages |
| 6 | Expected Outcomes and Contributions | Member 2 | ~0.75 page |
| 7 | Project Plan and Timeline | Member 5 | ~0.25 page |
| 8 | Ethical Considerations | Member 5 | ~0.25 page |
| — | References (APA) | **Member 5 assembles** | Not counted |
| — | Cover Page | Member 5 | Not counted |
| **Total** | | | **~4.50–5.00 pages** |

> **Note for the group:** The template cap is 5 pages (content only, excluding cover and references). This is tight for a technical project with 2 heavy sections (5 and 6). Consider asking the instructor if 6–7 pages is acceptable given the technical depth. The content below is based on 5 pages for now.

---

## Member 1 — Alrick

### Section 5: Proposed Analytical Approach (~1.5 pages)

**What to write:**
Explain the two analytical components of the system — the Predictive Model and the Optimization Model (Prescriptive) — and justify why these methods are appropriate for the research question.

**Required subsections (suggested):**
- Overview of the two-component pipeline (Predictive → Optimization)
- Predictive Model: Random Forest, features used, training approach, why RF over alternatives
- Optimization Model: objective function, decision variables, constraints (memory, fairness, SLA), greedy implementation for simulation
- Why this combination is appropriate: prediction enables safe overcommitment; optimization ensures no constraint is violated

**Key papers to draw from:**

| Paper | What to cite |
|---|---|
| Kovalenko & Zhdanova (2024) | Mathematical model structure — binary assignment variables, capacity constraints, multi-objective form; the skeleton your optimization model extends |
| Resource Central — Cortez (2017) | RF prediction architecture; tenant history as dominant feature set; safe oversubscription result |
| Doukha (2025) | RF vs LSTM comparison (MAPE 2.65% vs 17.43%) — justify RF as model choice |
| DRF — Ghodsi (2011) | Fairness constraint formalization — dominant share, proven properties |
| Coach — Reidys (2025) | Temporal co-location strategy — additive peak model, pulse shape |
| Kofi (2025) | Preprocessing pipeline (Savitzky-Golay + min-max normalization); Google Cluster Trace v3 validation |
| Priya (2025) | Kubernetes scheduler plugin architecture — admission webhook + score + runtime controller |

**Writing guidance:** Present this as an "analytical direction" — the template says final model selection is not required. Describe the method and justify the choice. Use the objective function, feature set, and constraint list from the Proposal Draft as the technical backbone.

---

## Member 2

### Section 6: Expected Outcomes and Contributions (~0.75 page)

**What to write:**
Describe what the project will produce, what improvements are expected over the baseline, and what the contribution to the field is. This section works from the *outputs* of Member 1's analytical approach — you don't need to understand the internals, just know: the system produces (a) a trained RF model, (b) a scheduling algorithm, and (c) simulation results comparing against the default K8s scheduler.

**Required subsections (suggested):**
- Expected quantitative outcomes (utilization %, SLA %, Gini coefficient, MAPE)
- What the system contributes that does not currently exist in the literature
- How practitioners (cloud operators, tenants) would benefit
- Limitations and scope boundaries

**Key papers to draw from:**

| Paper | What to cite |
|---|---|
| Priya (2025) | Benchmark target: <5% SLA violation rate — use as the bar to match or exceed |
| Alatawi (2025) | Gini coefficient improvement range (0.25 → 0.10) — your fairness target |
| Wang & Yang (2025) | 32.5% utilization improvement and 43.3% latency reduction — use as a comparable result your system should rival |
| Chaudhari (2025) | The unbuilt framework this project fills — name your contribution explicitly |
| Heracles — Lo et al. (2015) | ~90% utilization + <5% SLA is achievable — prior work proof that your targets are realistic |
| Jiang Zhi (2025) | Simulation evaluation methodology — justify that simulation on real traces is valid evaluation |
| Zhao et al. (2021) | Profit = utilization framing — use for cloud provider stakeholder benefit argument |

**Specific numbers to include:**
- Utilization: ~45–60% (default K8s) → ≥ 85% (target)
- SLA violation rate: < 5%
- Gini coefficient: ~0.25 → ~0.10
- Prediction MAPE: < 5%

---

## Member 3

### Sections 1 + 2: Introduction + Problem Definition (~1.25 pages total)

**What to write:**
Establish the industry context, explain why the problem matters, and formally define the analytical challenge. These two sections flow together as the opening of the proposal — they must hook the reader and justify the project.

**Section 1 — Introduction (~0.75 page):** Context of multi-tenant cloud clusters, the cost of low utilization, why this problem is timely (DRAM pricing, AI workload growth), who the stakeholders are (cloud operators, tenants, organizations running shared clusters).

**Section 2 — Problem Definition (~0.5 page):** Precisely describe the problem — the declared-vs-actual usage gap, memory contention from simultaneous peaks, lack of native fairness in Kubernetes, SLA violations from reactive (not predictive) scheduling. Explain how data analytics (prediction + optimization) is the right tool to address it.

**Key papers to draw from:**

| Paper | What to cite |
|---|---|
| Chaudhari (2025) | 40–60% utilization waste in production; Kubernetes fails for multi-tenant AI/ML workloads — the gap anchor |
| Verma et al. — Borg (2015) | Foundational evidence: shared clusters save 20–30% infrastructure at scale; overcommitment is the right strategy |
| Heracles — Lo et al. (2015) | Memory contention is the primary co-location interference mechanism; memory matters more than CPU |
| Quasar — Delimitrou & Kozyrakis (2014) | <20% CPU utilization, ~40% memory with static reservations — validates the problem in the literature |
| DRF — Ghodsi (2011) | Lack of multi-resource fairness as a structural gap in shared cluster schedulers |
| Pinnapareddy (2025) | Kubernetes right-sizing gap; sustainability and cost motivation |

**Writing guidance:** Use the Suggested Introduction from the Proposal Draft as a reference. You can use those paragraphs as a starting draft — refine, tighten, and add APA citations throughout.

---

## Member 4

### Sections 3 + 4: Analytical Objective + Proposed Data Sources (~1.0 page total)

**What to write:**

**Section 3 — Analytical Objective (~0.5 page):**
- State the research question clearly (see Proposal Draft → Suggested Topic Sentences, Option 1 is the working version)
- State the hypothesis (see Proposal Draft → Suggested Hypothesis)
- Briefly describe the type of analytical project: predictive + prescriptive analytics combined

**Section 4 — Proposed Data Sources (~0.5 page):**
- Primary dataset: Google Cluster Trace v3 — describe size, contents, public availability, why it is appropriate
- Secondary dataset (optional): Azure VM trace (Resource Central) — validate RF model generalization
- Justify why these sources are appropriate for the analytical objective

**Key papers to draw from:**

| Paper | What to cite |
|---|---|
| Kofi (2025) | Google Cluster Trace v3 validation for this exact type of research; preprocessing pipeline |
| Jiang Zhi (2025) | Google Borg + Azure + Alibaba trace datasets; dataset composition and size |
| Resource Central — Cortez (2017) | Azure VM trace as secondary validation; tenant history features available in trace data |
| Wang & Yang (2025) | Kubernetes multi-tenant scheduling as the deployment context |
| Doukha (2025) | MAPE and MSE as primary evaluation metrics for the prediction model |

**Research question to use (from Proposal Draft Option 1):**
"By using the output of a predictive model as the input of an optimization model that is constrained by available resources and SLA requirements, we can optimally schedule workloads on a cluster such that resource idleness is kept minimal and fairness to tenants is high."

---

## Member 5

### Sections 7 + 8: Project Plan & Timeline + Ethical Considerations (~0.5 page total)
### + Final Integration and References Assembly

**What to write:**

**Section 7 — Project Plan and Timeline (~0.25 page):**
Describe the project phases using the 5-phase structure from the template:
1. Problem definition and proposal approval
2. Data collection and preprocessing (Google Cluster Trace v3)
3. Predictive model development (RF training + validation)
4. Optimization model development (scheduling simulation)
5. Results analysis, visualization, and final report/presentation

Include a rough timeline (weeks or months).

**Section 8 — Ethical Considerations (~0.25 page):**
- Confirm TCPS certification status
- Google Cluster Trace v3 is publicly available, no privacy concerns
- No human subjects
- No proprietary data
- Responsible use: results are for research only, not for deployment in production without further validation

**Final Integration (not a section, but a responsibility):**
- Assemble all 5 members' sections into a single cohesive Word document
- Ensure consistent terminology throughout (e.g., "Predictive Model" and "Optimization Model" are the terms — not "admission control" in the main narrative)
- Build the References section in APA format, compiling all citations from all sections
- Ensure the cover page includes: title, course, program, names, group number, supervisor, institution, date
- Check total page count (content only, not cover or references) — target ≤ 5 pages

**Papers likely cited across the document (to include in References):**
Alatawi, Chaudhari, Coach/Reidys, DRF/Ghodsi, Doukha, Heracles/Lo, Jiang Zhi, Kovalenko, Kofi, Liu & Guitart, Perera, Pinnapareddy, Priya, Quasar/Delimitrou, Resource Central/Cortez, Verma/Borg, Wang & Yang, Zhao.

---

## Notes

- Everyone reviews all sections once assembled and suggests improvements.
- Use "Predictive Model" and "Optimization Model (Prescriptive)" as the standard terms in the written proposal — avoid jargon like "admission control" in the main narrative (define it once in a footnote or concepts section if needed).
- Refer to the `Proposal Draft.md` file for suggested introduction paragraphs, hypothesis wording, literature table, and model design details.
- The `Research_Paper_Reviews_Jobs.md` file has full summaries of all 21 papers — use it to find specific quotes, numbers, and details for each section.
