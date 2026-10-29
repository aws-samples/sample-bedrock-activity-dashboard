# WAFR Review Prompt

Use this prompt to run an AWS Well-Architected Framework Review (WAFR) of an AWS
workload. It is tool-agnostic and works with any AI coding assistant that supports
MCP and can read repo files (Kiro, Claude Code, Cursor, and others).

It assumes the prerequisites in [`wafr-prerequisites.md`](./wafr-prerequisites.md) are
met and instructs the model to produce output in the exact structure defined in
[`wafr-output-template.md`](./wafr-output-template.md).

---

## How to use

1. Open [`wafr-prerequisites.md`](./wafr-prerequisites.md) and confirm every item is
   satisfied. Configure the AWS Documentation MCP server in your AI tool if you haven't
   already (Kiro / Claude Code / Cursor instructions are in that file).
2. Fill in every `<...>` placeholder inside the **Prompt** block below before pasting.
3. Copy the entire fenced **Prompt** block and paste it into your AI tool's chat /
   agent session.
4. The model writes the report to `docs/wafr/reports/well-architected-review-<date>.md`.
5. Review the report against the output template — every section should be filled.
6. Track the action items list in your normal backlog.

---

## Prompt

Copy everything between the fenced block, with your run parameter values filled in.

```
Run an AWS Well-Architected Framework Review of this workload.

Run parameters
- Workload: <short name, e.g. "Order Processing Platform">
- Environment: <dev / staging / prod>
- AWS account(s): <account IDs or "see IaC">
- AWS region(s): <e.g. us-west-2, eu-west-1>
- In-Scope: <components, services, specs, or modules>
- Out-of-Scope: <things explicitly excluded>
- Compliance context: <e.g. SOC 2, HIPAA, PCI-DSS, GDPR, FedRAMP, or "none">
- Reviewer: <your name / team>
- Review date: <YYYY-MM-DD>
- Output path: <docs/wafr/reports/well-architected-review-<YYYY-MM-DD>.md>

Inputs to read (in this order, skip silently if a category is missing):
1. Product / workload overview docs (e.g. README.md, product.md, docs/overview.md).
2. Architecture descriptions (e.g. docs/architecture.md, ADRs, RFCs, design docs,
   Mermaid diagrams in specs).
3. Engineering standards (e.g. CONTRIBUTING.md, docs/conventions/, .kiro/steering/,
   .cursor/rules/, CLAUDE.md).
4. Governance / operational standards (e.g. docs/governance.md, runbooks).
5. Spec / design documents for the In-Scope items only.
6. Infrastructure-as-Code (CDK / Terraform / CloudFormation / SAM / Pulumi).
7. Application source folders for the In-Scope items.

Method
- For each of the 6 WAFR pillars (Operational Excellence, Security, Reliability,
  Performance Efficiency, Cost Optimization, Sustainability), use the AWS
  Documentation MCP server to pull current pillar guidance and design principles.
  Cite every doc URL you use.
- Map findings to the architecture you observed in the inputs. Do not invent
  components that aren't documented or in the codebase.
- For each pillar produce: strengths, gaps / risks, recommendations, open questions.
- Score each pillar 1–5 using the rubric in the output template. The Overall score
  in the scorecard equals the lowest pillar score (weakest link), not the average.
- Build a prioritized action-items list across all pillars (P0 / P1 / P2) with effort
  estimates (S / M / L).
- If a compliance context was provided, weight findings against those standards and
  call out any controls that look unmet. Structure the Compliance Mapping table per
  the named framework's native control taxonomy (SOC 2 by Trust Service Criteria;
  HIPAA by Administrative / Physical / Technical Safeguard; PCI-DSS by Requirement
  number; GDPR by Article; FedRAMP by control family).
- For large workloads (many IaC stacks or source modules), sample representative
  files first — top-level entry points, one module per service, and the most-changed
  files — then deep-read only what your findings depend on. Record what you sampled
  in the report's Assumptions section.

Where to record uncertainty (use exactly one bucket per item):
- Input Gaps: missing or low-quality inputs you needed.
- Open questions (per pillar): things only a human can answer.
- Assumptions: things you inferred from partial evidence and proceeded with.

Output
- Save the report to the Output path above.
- Follow the structure and section order in docs/wafr/wafr-output-template.md
  exactly. Do not omit sections; if a section has no content, write "No findings"
  with one sentence of context.
- Include a Sources section at the end listing every AWS doc URL you cited and
  every repository file you referenced as evidence.

Constraints
- Do not modify any source files, infra, or specs during the review. The only file
  you should write is the report at the Output path.
- Flag any prerequisite gaps from docs/wafr/wafr-prerequisites.md you encountered
  in the report's Input Gaps section instead of guessing.
- Keep evidence concrete: quote file paths, doc sections, or AWS docs. Avoid
  generic advice that isn't tied to something you read.
- If the AWS Documentation MCP server is unavailable or returns no results for a
  pillar, note this under Input Gaps, mark the pillar's "AWS guidance referenced"
  as "MCP unavailable", and base findings on the workload evidence only. Do not
  fabricate AWS doc URLs.
- Do not exceed ~5,000 words unless the workload genuinely warrants more.
```

---

## What the prompt is doing (and why)

Short rationale per section so customers can adapt it confidently:

- **Run parameters** force scoping decisions up front. WAFR is only useful when the
  reviewer commits to a specific workload, environment, and boundary. They live
  inside the fenced block so the pasted prompt is fully self-contained.
- **Inputs to read** lists categories, not specific filenames, so the prompt works
  across project layouts. The "skip silently if missing" rule prevents the model
  from hallucinating context.
- **Method** ties findings to current AWS guidance via the AWS Documentation MCP
  server, uses weakest-link scoring to keep the Overall honest, and adds explicit
  sampling guidance for large repos so the context window doesn't blow up.
- **Where to record uncertainty** disambiguates the three buckets (Input Gaps,
  Open questions, Assumptions) so the same fact doesn't get logged in two places.
- **Output** pins a deterministic structure (the template) and a date-stamped path
  so reviews are comparable over time instead of overwriting one file.
- **Constraints** keep the model in read-only mode against the codebase, surface
  missing context instead of fabricating findings, and prevent fabricated AWS doc
  URLs when the MCP server is unavailable.
