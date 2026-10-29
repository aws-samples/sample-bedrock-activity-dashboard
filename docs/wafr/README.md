# AI-Assisted AWS Well-Architected Review

A reusable kit for running an AWS Well-Architected Framework Review (WAFR) of any
AWS workload using an AI coding assistant (Kiro, Claude Code, Cursor, or any tool
with MCP support).

## What's in here

| File | Purpose |
|------|---------|
| [`wafr-prerequisites.md`](./wafr-prerequisites.md) | Checklist of inputs, scoping decisions, and tool setup (incl. MCP config for Kiro / Claude Code / Cursor). |
| [`wafr-prompt.md`](./wafr-prompt.md) | The prompt to paste into your AI tool. Tool-agnostic. |
| [`wafr-output-template.md`](./wafr-output-template.md) | The fixed report structure the model fills in. |

Reports land in `docs/wafr/reports/well-architected-review-<YYYY-MM-DD>.md` by default.

## Prerequisites

See [`wafr-prerequisites.md`](./wafr-prerequisites.md) for the full prerequisites —
required tooling (AI tool with MCP, AWS Documentation MCP server, `uv`/`uvx`),
required context inputs (overview, architecture, specs, IaC, source), recommended
inputs (engineering standards, governance, per-pillar operational signals), and the
per-run scoping decisions (workload, accounts, regions, in-scope, out-of-scope,
compliance context, reviewer, date). If a required input is missing, run the review
anyway — the model records gaps under **Input Gaps**.

## Workflow at a glance

1. Walk through `wafr-prerequisites.md` and configure the AWS Documentation MCP server
   in your AI tool.
2. Fill in the `<...>` placeholders inside the **Prompt** block in `wafr-prompt.md`
   (workload, scope, environment, compliance context, reviewer, date).
3. Paste the prompt into your AI tool's chat / agent session.
4. The model writes the report following `wafr-output-template.md`.
5. Triage the prioritized action items into your normal backlog.

## Why this exists

Off-the-cuff "review my architecture" prompts produce inconsistent reports. This kit
makes the inputs, evidence requirements, scoring rubric, and output shape explicit, so:

- Reviews are repeatable across teams and time.
- Findings are tied to AWS docs and source files, not invented.
- The report is comparable to past reviews via the same scorecard and template.

## Sharing this kit

The four files are self-contained. Drop the `docs/wafr/` folder into any repo and
your team can run a WAFR the same day.

This folder is intentionally repo-agnostic. Copy `docs/wafr/` into any repository
as-is — no paths in the kit reference the host repo.
