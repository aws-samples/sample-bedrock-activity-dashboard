# WAFR Output Template

This is the structure the model must follow when producing
`docs/wafr/reports/well-architected-review-<YYYY-MM-DD>.md` (or whatever output path
the reviewer specified). Sections are mandatory and must appear in this order. If a
section has no content, write "No findings" with one sentence of context — do not
delete the section.

This template is tool-agnostic and is used together with
[`wafr-prerequisites.md`](./wafr-prerequisites.md) and [`wafr-prompt.md`](./wafr-prompt.md).

Replace every `<placeholder>` and example row with real content during the review.

---

```markdown
# AWS Well-Architected Framework Review — <Workload Name>

**Review date:** <YYYY-MM-DD>
**Reviewer:** <name / team>
**Workload:** <name>
**Environment:** <dev / staging / prod>
**AWS account(s):** <account IDs or "see IaC">
**AWS region(s):** <regions>
**In-Scope:** <comma-separated list>
**Out-of-Scope:** <comma-separated list>
**Compliance context:** <standards or "none">
**WAFR guidance version:** <date the AWS pillar docs were retrieved, e.g. "AWS docs as of YYYY-MM-DD">

---

## Input Gaps

List any prerequisite from `docs/wafr/wafr-prerequisites.md` that was missing or low
quality and how it affected the review. If none, write "None."

- <gap and impact>

---

## Executive Summary

A 5–10 sentence summary written for a tech lead or engineering manager:
- What the workload does (one sentence).
- Overall posture across the 6 pillars (one sentence).
- Top 3 risks (one sentence each).
- Top 3 strengths (one sentence each).
- Recommended next 30 days (one sentence).

---

## Scorecard

Use the rubric below for every pillar. Score is an integer 1–5. The **Overall** row
equals the **lowest** pillar score (weakest link), not the average — averaging hides
variance and makes a critical pillar look acceptable when paired with strong ones.

| Pillar | Score (1–5) | Trend vs. last review | One-line rationale |
|--------|-------------|-----------------------|--------------------|
| Operational Excellence | <n> | <↑ / → / ↓ / N/A> | <rationale> |
| Security | <n> | <↑ / → / ↓ / N/A> | <rationale> |
| Reliability | <n> | <↑ / → / ↓ / N/A> | <rationale> |
| Performance Efficiency | <n> | <↑ / → / ↓ / N/A> | <rationale> |
| Cost Optimization | <n> | <↑ / → / ↓ / N/A> | <rationale> |
| Sustainability | <n> | <↑ / → / ↓ / N/A> | <rationale> |
| **Overall (= lowest pillar)** | **<min>** | — | Overall = lowest pillar score (weakest link). See pillar findings for variance. |

### Scoring rubric

| Score | Meaning |
|-------|---------|
| 5 | Fully aligned with current AWS pillar guidance. No material gaps. |
| 4 | Mostly aligned. Minor gaps with low impact. |
| 3 | Partially aligned. Some gaps with moderate impact; mitigations exist. |
| 2 | Significant gaps. Likely to cause incidents, cost overruns, or compliance issues. |
| 1 | Critical gaps. Workload should not run in production in current state. |

---

## Pillar Findings

For each pillar use exactly this sub-structure. Repeat the block 6 times in the order:
Operational Excellence → Security → Reliability → Performance Efficiency → Cost Optimization → Sustainability.

### <Pillar Name>

**Score:** <n>/5
**AWS guidance referenced:** <doc title> (<URL>), <doc title> (<URL>)

**Strengths**
- <Specific strength tied to a file path, doc section, or service config>
- <Specific strength>

**Gaps and risks**
- <Gap or risk, with evidence — file path, doc, or AWS design principle it violates>
- <Gap or risk>

**Recommendations**
- <Concrete change, with target file or service. Example: "Enable RDS automated backups with 7-day retention in `<infra-file>`.">
- <Concrete change>

**Open questions**
- <Anything you couldn't determine from the inputs and need a human to answer>

---

## Cross-Cutting Themes

Findings that span more than one pillar (e.g. "logging strategy is inconsistent across
services" hits Operational Excellence, Security, and Cost Optimization). List 3–7 themes,
each with the pillars it touches.

| Theme | Pillars touched | Why it matters |
|-------|-----------------|----------------|
| <theme> | <pillars> | <one sentence> |

---

## Compliance Mapping (only if a compliance context was provided)

If the **Compliance context** in the run parameters was non-empty, list the controls
or requirements you assessed and whether the workload appears to meet them based on
the inputs reviewed. If no compliance context was provided, write "Not applicable."

Structure this table using the named framework's native control taxonomy:
- **SOC 2** — group rows by Trust Service Criteria (Security, Availability, Processing
  Integrity, Confidentiality, Privacy) and use the criterion ID (e.g. CC6.1, A1.2).
- **HIPAA** — group rows by Safeguard (Administrative, Physical, Technical) and use
  the §164.30x reference.
- **PCI-DSS** — use the Requirement number (e.g. 3.4, 8.2.1).
- **GDPR** — use the Article number (e.g. Art. 32, Art. 25).
- **FedRAMP** — group rows by control family (AC, AU, CM, IA, SC, etc.) and use the
  NIST SP 800-53 control ID.

| Standard | Control / requirement | Status | Evidence | Notes |
|----------|-----------------------|--------|----------|-------|
| <e.g. SOC 2> | <CC6.1 logical access> | Met / Partially met / Not met / Unknown | <file path or doc> | <notes> |

---

## Prioritized Action Items

Single combined backlog across all pillars. Sort by priority then effort.

| ID | Priority | Pillar(s) | Action | Owner | Effort | Target |
|----|----------|-----------|--------|-------|--------|--------|
| A1 | P0 | Security | <action> | <role / team> | S/M/L | <sprint or date> |
| A2 | P0 | Reliability | <action> | <role / team> | S/M/L | <sprint or date> |
| A3 | P1 | Cost Optimization | <action> | <role / team> | S/M/L | <sprint or date> |
| ... | ... | ... | ... | ... | ... | ... |

**Priority definitions**
- **P0** — fix before next deploy / within 2 weeks. Direct security, reliability, or compliance risk.
- **P1** — fix this quarter. Material impact but not immediate.
- **P2** — track in backlog. Improvements, hygiene, longer-term refactors.

**Effort definitions**
- **S** — under 2 days of one engineer.
- **M** — 2–10 days of one engineer.
- **L** — multi-week or cross-team.

---

## Assumptions

Document everything the review assumed because it wasn't explicitly verifiable from the
inputs. This makes the review auditable and gives reviewers a quick way to challenge
findings. Also record sampling decisions here for large workloads (e.g. "Read top-level
CDK stack and one Lambda module per service; did not deep-read individual handlers.").

- <assumption>
- <assumption>

---

## Sources

Every AWS Documentation URL cited above, plus any internal file paths used as evidence.

**AWS documentation**
- <pillar> — <doc title> — <URL>
- ...

**Repository evidence**
- `<path/to/file>` — <what was cited>
- ...

---

## Appendix A — Pillar question coverage (optional)

If you used the AWS Well-Architected Tool's standard questions, list **every question
you covered** (one row per question, not one per pillar) so future reviews can diff
against this one. If you didn't use the standard question set, write "Not used."

| Pillar | Question ID | Question summary | Covered? | Notes |
|--------|-------------|------------------|----------|-------|
| <e.g. SEC> | <e.g. SEC03> | <one-line question summary> | Y / N / Partial | <notes> |

---

## Appendix B — Sample filled-in pillar (illustrative only)

The block below is a worked example of the **Security** pillar so reviewers can
calibrate tone, evidence quality, and concreteness. The file paths and AWS doc URLs
are placeholders for illustration; do not copy them into a real report.

### Security (illustrative)

**Score:** 3/5
**AWS guidance referenced:** Security Pillar — AWS Well-Architected Framework
(`https://docs.aws.amazon.com/wellarchitected/latest/security-pillar/welcome.html`),
SEC02 — Identity and Access Management
(`https://docs.aws.amazon.com/wellarchitected/latest/security-pillar/sec02-bp.html`)

**Strengths**
- All workload data buckets enable SSE-KMS with customer-managed keys
  (`infrastructure/data_stack.py:142`).
- Cognito user pool enforces MFA and a 12-character password policy
  (`infrastructure/auth_stack.py:78`); aligns with SEC02 BP "Use strong sign-in mechanisms".

**Gaps and risks**
- ECS task role grants `s3:*` on the application bucket
  (`infrastructure/backend_stack.py:201`); violates least privilege per SEC03 BP
  "Grant least privilege access".
- No automated rotation policy on the Secrets Manager database secret
  (`infrastructure/data_stack.py:188`); rotation Lambda is configured but disabled.
- CloudFront distribution is missing a WAF web ACL
  (`infrastructure/frontend_stack.py:55`); SEC05 BP "Implement inspection-based protection"
  is unmet.

**Recommendations**
- Replace `s3:*` with the explicit action set the application uses
  (`s3:GetObject`, `s3:PutObject`, `s3:DeleteObject`) scoped to the bucket and prefix
  the workload writes to. Target: `infrastructure/backend_stack.py`.
- Enable rotation on the database secret with a 30-day schedule.
- Attach an AWS-managed WAF rule set (`AWSManagedRulesCommonRuleSet`,
  `AWSManagedRulesKnownBadInputsRuleSet`) to the CloudFront distribution.

**Open questions**
- Is there an existing security incident-response runbook outside the repo? None of
  the inputs reviewed referenced one.
```

---

## Notes for the model writing the report

- Do not change section order or rename sections.
- Every "Strengths", "Gaps and risks", and "Recommendations" bullet must cite either an
  AWS doc URL or a repository path. Generic advice without evidence should be cut.
- Keep the whole report under ~5,000 words unless the workload genuinely warrants more.
- If you find yourself filling a section with vague text, that's a signal the prerequisite
  inputs were thin — record it under **Input Gaps** instead.
- Use Mermaid only if it adds clarity over prose. Don't pad with diagrams.
- Skip the **Compliance Mapping** table content (write "Not applicable") only when no
  compliance context was provided in the run parameters.
- Where to record uncertainty (each item lives in exactly one bucket): **Input Gaps**
  for missing or low-quality inputs you needed; **Open questions** (per pillar) for
  things only a human can answer; **Assumptions** for things you inferred and proceeded
  with.
- The **Overall** scorecard row equals the lowest pillar score (weakest link), not the
  average.
- Appendix B is illustrative only — do not copy its file paths or URLs into a real
  report. Replace with real evidence from the workload under review.
