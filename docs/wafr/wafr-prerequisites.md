# WAFR Prerequisites

Before running [`wafr-prompt.md`](./wafr-prompt.md), confirm every item below. Anything
missing degrades the review quality and forces the model to guess. Use this as a
checklist on each run.

This document is tool-agnostic. It works with any AI coding assistant that can read
files and call MCP servers — for example Kiro, Claude Code, or Cursor. Tool-specific
setup notes are at the bottom.

---

## 1. Required context inputs

The model needs enough context to understand **what the workload is, how it's built,
and what's deployed**. The exact filenames vary by project — what matters is that the
model can find each category below.

| # | Category | Required? | What it provides | Common locations |
|---|----------|-----------|------------------|------------------|
| 1 | Product / workload overview | Required | What the system does, who uses it, key metrics | `README.md`, `product.md`, `docs/overview.md` |
| 2 | Architecture description | Required | Components, data flow, AWS services used, trust boundaries | `docs/architecture.md`, design docs, ADRs, Mermaid diagrams in specs |
| 3 | Engineering standards | Recommended | Backend / frontend / infra conventions and constraints | `docs/conventions/`, `CONTRIBUTING.md`, `.kiro/steering/`, `.cursor/rules/`, `CLAUDE.md` |
| 4 | Operational / governance standards | Recommended | Compliance posture, on-call practices, change management | `docs/governance.md`, runbooks, internal wikis |
| 5 | Spec or design documents for in-scope features | Required | Requirements and design that define current architecture | `.kiro/specs/`, `docs/specs/`, `docs/rfcs/`, `docs/adrs/` |
| 6 | Infrastructure-as-Code | Required | What's actually deployed (CDK, Terraform, CloudFormation, Pulumi, SAM) | `infrastructure/`, `cdk/`, `terraform/`, `cloudformation/`, `template.yaml` |
| 7 | Application source | Required | Implementation reality check vs. the design docs | `backend/`, `frontend/`, `services/`, `apps/`, etc. |
| 8 | AWS Documentation MCP server | Required | Pulls current pillar guidance during the review | See "Tool-specific setup" below |

> If product or architecture docs are missing, do not block the review. The IaC plus
> source code are sufficient ground truth. Note any missing inputs under
> **Input Gaps** in the report.

## 2. Scoping inputs (must be supplied per run)

The reviewer must declare these before running the prompt. Without them the review is
either too shallow (everything skimmed) or too long (every historical artifact covered).

- **In-Scope**: explicit list of components, services, specs, or modules that
  represent the current architecture being reviewed.
- **Out-of-Scope**: anything excluded — typically archived, retired, prototype, or
  unrelated work that lives in the same repo.
- **Workload boundary**: which AWS account(s), region(s), and environment (dev / staging
  / prod) the review covers.
- **Review date**: used in the output filename and header.
- **Reviewer**: human owner accountable for the report and for triaging action items.
- **Compliance context** (optional): standards that apply (e.g. SOC 2, HIPAA, PCI-DSS,
  GDPR, FedRAMP). The reviewer will weight findings against these.

## 3. Quality bar for design content

For each in-scope component or feature, confirm there's at least:

- A statement of **what it does** and **who consumes it**.
- A description of **AWS services used** and **how they connect**.
- A note on **data classification** (public / internal / confidential / regulated).

If a component is only a stub or only has tasks/issues with no design, list it under
Out-of-Scope or fix the docs before running the review. Stubs lead to invented findings.

## 4. Architectural artifacts

Lightweight diagrams or descriptions help the review converge faster. Have at least one
of these for the platform overall:

- A component diagram (Mermaid, draw.io, Lucidchart export, or PNG with a description).
- A data-flow description (prose is fine).
- A list of trust boundaries (auth providers, VPC boundaries, data stores, third-party
  integrations).

If none exist, the model will reconstruct from IaC and source code, which is slower and
more error-prone.

## 5. Tooling and access

- **AWS Documentation MCP server** is configured and reachable in the AI tool you're
  using. Verify by asking the model to do a trivial doc-search call before the full review.
- **Workspace tools**: file read, file search, and file write are available so the
  reviewer can read inputs and write the report.
- The reviewer has write access to wherever the report will land (default:
  `docs/wafr/reports/`). The model can create the directory on first run.

## 6. Operational signals (optional but improves quality)

If available, drop links or excerpts into the prompt to ground specific pillars:

| Pillar | Useful signal |
|--------|---------------|
| Operational Excellence | CI/CD config, deployment frequency, runbooks, on-call rotation, post-incident reviews |
| Security | IAM policies, identity provider config, KMS / encryption-at-rest decisions, network ACLs, security findings backlog |
| Reliability | RDS / DynamoDB backup config, multi-AZ status, SLOs / SLAs, recent incidents, dependency map |
| Performance Efficiency | Compute sizing, DB instance class, p50 / p95 / p99 latency, frontend Lighthouse scores, load test results |
| Cost Optimization | Cost Explorer trends, monthly spend by service, Savings Plans / RI coverage, idle resources |
| Sustainability | Region choice rationale, idle resource cleanup policy, right-sizing posture |

Missing optional signals just means the relevant pillar finding is more generic; it
does not block the review.

## 7. Pre-flight checklist

Run through this quickly before invoking the prompt:

- [ ] Product / workload overview is readable by the model.
- [ ] At least one architecture description exists.
- [ ] In-Scope and Out-of-Scope lists are written down.
- [ ] Each in-scope component has a design or design-equivalent doc.
- [ ] IaC reflects what is actually deployed.
- [ ] AWS Documentation MCP server is responsive in your AI tool.
- [ ] Output directory exists or model has permission to create it.
- [ ] Reviewer name, review date, and target environment are decided.
- [ ] You have read [`wafr-output-template.md`](./wafr-output-template.md) so you know
      what the output should look like.

---

## Tool-specific setup

The prompt and template are identical across tools. Only MCP configuration differs.

### AWS Documentation MCP server

All three tools below support MCP. Configure the AWS Documentation MCP server (or any
equivalent that exposes AWS docs search and fetch) so the model can pull current
pillar guidance.

A typical config block — tool-specific JSON wrappers go around it:

```json
{
  "command": "uvx",
  "args": ["awslabs.aws-documentation-mcp-server@latest"],
  "env": { "FASTMCP_LOG_LEVEL": "ERROR" }
}
```

Requires `uv` / `uvx` installed locally. See https://docs.astral.sh/uv/getting-started/installation/.

### Kiro

- Add the MCP server to `~/.kiro/settings/mcp.json` (user-level) or
  `.kiro/settings/mcp.json` (workspace-level):
  ```json
  {
    "mcpServers": {
      "aws-docs": {
        "command": "uvx",
        "args": ["awslabs.aws-documentation-mcp-server@latest"],
        "env": { "FASTMCP_LOG_LEVEL": "ERROR" },
        "disabled": false,
        "autoApprove": []
      }
    }
  }
  ```
- Optional: drop `wafr-prerequisites.md`, `wafr-prompt.md`, and `wafr-output-template.md`
  into `.kiro/steering/` (with `inclusion: manual` front-matter) so they can be pulled
  in via `#` context references.
- Run the WAFR by pasting the prompt from `wafr-prompt.md` into a Vibe session.

### Claude Code

- Add the MCP server with the CLI:
  ```bash
  claude mcp add aws-docs -- uvx awslabs.aws-documentation-mcp-server@latest
  ```
  or edit `~/.claude/claude_desktop_config.json` / project-level `.mcp.json` directly.
- Optional: keep a `CLAUDE.md` at the repo root that points to these three WAFR docs so
  Claude Code auto-loads context.
- Run the WAFR by pasting the prompt from `wafr-prompt.md` into the Claude Code session.

### Cursor

- Add the MCP server in **Settings → Cursor Settings → MCP** or via
  `.cursor/mcp.json` at the project root:
  ```json
  {
    "mcpServers": {
      "aws-docs": {
        "command": "uvx",
        "args": ["awslabs.aws-documentation-mcp-server@latest"]
      }
    }
  }
  ```
- Optional: add a project rule under `.cursor/rules/` referencing the three WAFR docs
  so they're available as context.
- Run the WAFR by pasting the prompt from `wafr-prompt.md` into the Cursor chat (Agent
  mode recommended so it can read files and call MCP tools).

### Other tools

Any AI assistant with: (a) file read/write, (b) MCP support, and (c) the ability to
follow long structured prompts can run this WAFR. Replicate the MCP config pattern
above using whatever the tool's MCP configuration mechanism is.
