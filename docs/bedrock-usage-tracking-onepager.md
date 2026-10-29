# Bedrock Activity Dashboard — What It Captures and How

_Last updated: 2026-08-19_

## Summary

The dashboard ingests Amazon Bedrock invocation activity account-wide (not just its own
calls) directly from AWS's own logging surfaces, then reconstructs per-user, per-model,
per-application, and per-team cost/usage views on top of that. It does **not** rely on
per-IAM-user billing tags — it works around the fact that most Bedrock traffic in a
typical account runs under a small number of shared execution roles.

## Data sources (hybrid ingestion, priority order)

| Source | Coverage | Latency | Notes |
|---|---|---|---|
| **CloudWatch Logs** (`/aws/bedrock/model-invocations`) | Last 48h | Near real-time | Primary source |
| **S3** (`bedrock-invocation-logs-{region}-{account}`) | Historical, beyond 48h | Batched | Same log schema as CloudWatch, gzip JSON files |
| **CloudTrail** (`LookupEvents`) | Fallback only | On-demand | Used only if CloudWatch/S3 return nothing for the window |

All three are queried for the account's single configured region (`AWS_REGION` /
`BEDROCK_REGION`, currently `us-west-2`). Results are deduplicated by invocation ID
(or a content-hash fallback when no request ID is present) and cached for 60 seconds,
process-wide, keyed only by time window — never by user or filter — so per-user scoping
always happens after the shared fetch.

## What's captured per invocation

- Model ID (normalized — cross-region prefix and inference-profile ARN path stripped
  for aggregation; the raw inference-profile ARN is preserved separately)
- Input/output/cache-read/cache-write token counts (tolerant of Anthropic, Converse API,
  Nova/Titan, and Meta Llama field-naming differences)
- Caller identity and display name
- Source application (best-effort, see below)
- Source IP, timestamp, prompt/response text (when included in the log)
- Cost estimate, computed from the configurable per-model pricing table

## Caller attribution — three layers

1. **IAM identity from the invocation log itself** (`identity.arn` / CloudTrail
   `userIdentity`). This is the AWS-native signal and reflects whichever IAM
   user/role actually called `bedrock-runtime`. Accurate whenever distinct
   IAM identities make Bedrock calls directly.

2. **`requestMetadata` tags** (added 2026-08). Because this dashboard's own backend
   makes every one of its Bedrock calls under a single shared credential
   (ECS task role), the IAM-ARN signal above collapses to that one shared identity for
   *all* of this app's own traffic — Agent Chat, the 14 AgentCore agents, etc. To recover
   real per-end-user/per-agent attribution for its own calls, the dashboard now attaches
   Bedrock's per-request `requestMetadata` tagging (Converse/InvokeModel's documented
   mechanism for this) — e.g. `{"UserIdentity": "alice@example.com", "SessionId": "..."}`
   for Agent Chat, `{"AgentName": "guardrail-recommender", "TriggerType": "scheduled"}`
   for agent executions. The ingestion parser prefers `requestMetadata` over the shared
   IAM ARN when present, and falls back to the ARN otherwise.

3. **Source-application detection** (heuristic). `_identify_source_application()`
   pattern-matches the CloudTrail/CloudWatch `userAgent` string against known
   signatures (Kiro, Q Developer, VS Code, Cursor, AWS CLI, Python/Java/JS SDKs, AWS
   Lambda, SageMaker, Bedrock Agent, AWS Console browsers). Unmatched user agents show
   up as "Unknown." This is best-effort, not authoritative — it's the mechanism that
   lets *other* applications in the account show up as distinct rows in Model Usage/Cost
   views without those apps doing anything special, provided their user-agent string is
   recognizable.

## Access control

Cognito-authenticated. Two independent checks per caller (`CurrentUser` in
`app/auth.py`): `is_admin` gates mutating actions (demo data, teams, pricing, guardrail
approval); `can_view_all` (admin/payer/superuser groups) gates whether the caller sees
everyone's activity or only records matching their own identity
(`_scope_to_user()` in `app/routes/activity.py`). A caller without `can_view_all`
will never see another user's — or another application's — usage, even if the data was
ingested.

## Known limitations

- **Single-region ingestion.** All three sources query one configured region. A
  `cloudtrail_regions` setting exists in `app/config.py` intended for multi-region
  ingestion but is not currently read by any query path — it's effectively dead
  configuration today.
- **`requestMetadata` is opt-in per caller.** The dashboard tags its own calls; it
  cannot make another unrelated application in the account tag itself. Untagged calls
  from other apps fall back to IAM-ARN + user-agent heuristics only.
- **Model invocation logging is an account+region setting, not app-scoped.** It captures
  everything Bedrock-side in that account/region — this is what makes account-wide
  visibility possible at all — but it must be enabled, and its S3/CloudWatch
  destinations must match what this dashboard is configured to read.
- **No cross-account visibility.** Only the account this stack is deployed in.

## Fix landed 2026-08-19

Agent Chat and the 14 AgentCore V2 agents now attribute their own invocations to real
end-user/agent identity (previously every Agent Chat turn was recorded under a
`"agent-chat"` placeholder, indistinguishable across users). See the
"Per-Caller Attribution via requestMetadata" section of the backend conventions
for the full implementation detail.
