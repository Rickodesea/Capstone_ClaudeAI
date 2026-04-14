# Claude Project Summary - Memory Overcommitment Capstone

**Date Created:** 2026-04-14
**Last Updated:** 2026-04-14

## Project Overview

This is a capstone project focused on **Optimal shared memory utilization with service level guarantees in multi-tenant clusters**.

### Problem Statement

In online shared clusters, all tasks on a node compete for limited DRAM. Memory overcommitment (exceeding physical DRAM capacity) can improve utilization and reduce costs by assuming workloads won't peak simultaneously. However, when all workloads attempt to peak simultaneously, the system must either:
- Swap to slower storage (greatly reducing performance and increasing latency)
- Terminate processes (affecting predetermined service level guarantees)

This creates a multi-objective business challenge:
1. Realizing high utilization
2. Meeting service level guarantees
3. Ensuring fairness among tenants
4. Avoiding disruptive spikes from one workload affecting another

### Solution Approach

The problem will be addressed using mathematical programming techniques with operations and prescriptive analytical tools.

## Project Scope

- **Focus:** DRAM resource type, Virtual Machine (VM) processes, and DRAM utilization on a single node
- **Technique:** Overcommitment (also known as oversubscription, overbooking, or multiplexing)
- **Definition:** Total allocated DRAM for VMs exceeds available physical capacity on the node

## Work Completed

### Documents Created
1. Proposal draft (`Pre_Proposal_A1.docx`) - Initial draft discussing DRAM price increases and overcommitment
2. Research papers extracted to text files (13 papers total in `ResearchPapers/Extracted_TextFiles/`)
3. Python extraction script (`ResearchPapers/pdf_to_text.py`)

### Research Papers Available (13 Total)

1. **A Study on Overcommitment in Cloud Providers** (Zhi, 2025)
2. **A Survey of Memory Management Techniques in Virtualized Systems** (Mishra & Kulkarni, 2018)
3. **Coach: Exploiting Temporal Patterns for All-Resource Oversubscription** (Reidys et al.)
4. **CPU and RAM Energy-Based SLA-Aware** (incomplete metadata)
5. **Dominant Resource Fairness** (Ghodsi et al., UC Berkeley)
6. **Intelligent Resource Allocation Optimization for Cloud** (Wang & Yang, 2025)
7. **Maximizing Server Utilization while Meeting Critical SLAs** (Blagodurov et al., SFU/HP Labs)
8. **Memory Resource Management in VMware ESX Server** (Waldspurger, 2002)
9. **Mitigating Application Resource Overload with Targeted Task Cancellation** (Hu et al., 2025)
10. **Optimizing Server and Memory Utilization in Cloud Computing** (Krishnaiah & Rao, 2025)
11. **Enhanced Virtual Machine Resource Optimization** (Doukha & Ez-zahout, 2025)
12. **Resource Central: Understanding and Predicting Workloads** (Cortez et al., Microsoft, 2017)
13. **Virtual Machine Memory Balancing (VMMB)** (Min et al., 2012)

## Current Task List

- [x] Extract proposal draft content
- [x] Read all 13 extracted research papers
- [ ] Create Section B - Research papers list table
- [ ] Create Section C - Research papers detailed summary table
- [ ] Clean up and enhance proposal draft with:
  - Proper APA references
  - More references from research papers
  - Academic writing style
  - Fleshed out explanation of problem and solution
  - Maintain user's writing voice

## Key Context from Current Proposal

The proposal currently discusses:
- DRAM price increases (54% to 116% year-over-year) due to HBM production shift
- Impact on cloud infrastructure costs
- Motivation for CSPs to optimize DRAM utilization
- Introduction to overcommitment concept
- Focus on DRAM, VMs, and single-node utilization

### References Currently Used
1. S&P Global (2026) - AI memory boom article
2. Catalin Voicu - AI memory crisis blog
3. Zhi, J. (2025) - Overcommitment dissertation

## Next Steps

1. Create comprehensive research papers list (Section B)
2. Create detailed research papers summary (Section C)
3. Enhance proposal draft with academic rigor and additional references
4. Ensure proper APA formatting throughout
