# Bedrock Activity Dashboard

A monitoring and governance dashboard for Amazon Bedrock usage. Tracks token consumption, model usage, and cost; runs 13 agents on Bedrock AgentCore Runtime for security, compliance, cost optimization, and usage intelligence.

Deployable to any AWS account — Cognito, VPC, RDS, ECS, and CloudFront are provisioned by CDK against the account the deploy script prompts for. Nothing is hardcoded to a specific account, and the frontend fetches its Cognito configuration at runtime so the same SPA build works across accounts.

> **Disclaimer.** Sample code, software libraries, command line tools, proofs of concept, templates, or other related technology are provided as AWS Content or Third-Party Content under the AWS Customer Agreement, or the relevant written agreement between you and AWS (whichever applies). You should not use this AWS Content or Third-Party Content in your production accounts, or on production or other critical data. You are responsible for testing, securing, and optimizing the AWS Content or Third-Party Content, such as sample code, as appropriate for production grade use based on your specific quality control practices and standards. Deploying AWS Content or Third-Party Content may incur AWS charges for creating or using AWS chargeable resources, such as running Amazon EC2 instances or using Amazon S3 storage.

## Features

- **Analytics Dashboard** — Overview, token usage, model usage, cost analysis, and prompt history with date range filtering
- **13 AI Agents** — Security, compliance, cost optimization, and usage intelligence agents running on AgentCore Runtime
- **Chargeback Reconciliation** — Upload AWS Cost Explorer CSV reports and generate per-user/per-team cost allocation with LLM-powered variance analysis
- **Prompt History Toggle + At-Rest Encryption** — Admins flip a single switch in Settings that gates visibility of prompt/response text across every record source (Agent Chat, demo, CloudWatch Logs, S3, CloudTrail). Enabling it is blocked unless a Fernet encryption key is loaded. See [Prompt History & Encryption](#prompt-history--encryption) for the full model
- **Guardrail Recommendations** — Generates Bedrock Guardrail configurations from caller-supplied or legacy PII type inputs
- **Cost Optimization** — Identifies expensive models used for simple tasks and recommends cheaper alternatives
- **Team Management** — Create teams, assign users, and track per-team costs
- **Agent Chat** — Interactive AI assistant for querying your Bedrock usage data
- **Hybrid Data Ingestion** — CloudWatch Logs (real-time, last 48h) + S3 (historical) + CloudTrail (fallback)

## Architecture

- **Frontend**: React + TypeScript, deployed to S3 + CloudFront. Cognito config is fetched at runtime from `GET /api/health/config` — never baked into the build, so the same bundle works across accounts.
- **Backend**: FastAPI (Python) on ECS Fargate, behind an **internal ALB** (private subnets, no public IP). All inbound HTTPS terminates at **API Gateway** (REST API, VPC Link v2 private integration to the ALB) — the ALB itself is not reachable from the internet. Cognito authorizer gates every path except `GET /api/health/config` (the SPA needs it before the user has a bearer token). Response streaming is enabled on the proxy integration so Server-Sent Events for Agent Chat work end-to-end.
- **Database**: PostgreSQL on RDS
- **AI Agents**: 13 agents on Bedrock AgentCore Runtime (lightweight HTTP server pattern) with inline-agent fallback
- **Data Sources**: CloudWatch Logs, S3 invocation logs, CloudTrail
- **Auth**: AWS Cognito — the User Pool, App Client, hosted-UI domain, and `admin` group are provisioned by the CDK Data stack in the target account.

The dashboard's **Architecture** tab renders every service diagram inline (System Architecture, Data Flow, Agent Invocation, Billed Cost Pipeline, Per-Category Agents, Prompt History & Encryption) — code is the source of truth, so what you see in the running UI always matches the deployed stack. A trust-boundary-focused ASCII diagram lives further down under [Architecture Deep Dive](#architecture-deep-dive).

## Project Structure

```
backend/                    # FastAPI backend
  app/
    routes/                 # API endpoints (activity, prompts, teams, pricing, chargeback, health)
    services/               # CloudTrail, Cost, CSV parser, Chargeback engine
    v2/
      agents/               # 14 AI agent definitions
      models.py             # SQLAlchemy models (21 tables)
      services/             # AgentCore invoker, agent execution service
frontend/                   # React frontend
  src/
    auth/cognito.ts         # Cognito PKCE flow — loads config from /api/health/config at runtime
    components/             # Analytics tabs (Overview, Token, Model, Cost, Prompts)
    components/v2/          # V2 components (Insights, AgentManagement, Chargeback, FAQ, Architecture)
infrastructure/             # CDK stacks (AppRegistry, Data, Backend, Frontend)
  lib/data-stack.ts         # VPC, RDS, ECR, Secrets Manager, Cognito User Pool + Client + Domain
agentcore-agents/           # 13 self-contained AgentCore deployment packages
```

## Prompt History & Encryption

The dashboard surfaces prompt and response text from several places — Agent
Chat (persisted here), admin-triggered demo generation (persisted here),
and Bedrock invocation logs from CloudWatch Logs / S3 / CloudTrail
(fetched live from AWS, not stored by this app). Because any of them can
carry regulated content (PII, PHI, financial, etc.), there's a single
admin control that governs whether users can see that text at all —
backed by a specific set of code paths, failure modes, and guarantees
documented below.

### Where the state lives

The toggle itself is a single row in the `app_runtime_settings` KV table
(migration `021_app_runtime_settings.py`):

| Column       | Type       | Example                              |
| ------------ | ---------- | ------------------------------------ |
| `key`        | PK, string | `"prompt_history_enabled"`           |
| `value`      | text       | `"true"` / `"false"`                 |
| `updated_at` | timestamp  | `2026-10-06 19:18:56+00:00`          |
| `updated_by` | string     | `"admin@example.com"` (audit)        |

Reads tolerate a missing row via a code-level default of `True`
(`backend/app/services/settings_service.py::get_prompt_history_enabled`),
so a freshly-migrated DB serves the historical behavior until an admin
explicitly flips it off.

The encryption **key** is a separate thing — not in the DB. It lives in
AWS Secrets Manager as a construct called `PromptHistoryEncryptionKey`
owned by `infrastructure/lib/backend-stack.ts`, auto-generated on first
deploy (`generateSecretString { passwordLength: 64 }`), and injected into
the ECS task as the `PROMPT_HISTORY_ENCRYPTION_KEY` environment variable
via `ecs.Secret.fromSecretsManager()`. The container reads it at startup
through `settings.prompt_history_encryption_key` and never persists it.

### How the toggle gates visibility (every record source)

The toggle is a **universal body-visibility gate**: it controls whether
the prompt and response **text** is returned by any API path the UI
calls, regardless of where the record came from. The list view (metadata
— timestamp, user, model, cost, tokens) is always visible so admins can
still monitor activity.

The gate is enforced in two places:

1. **`GET /api/activity/prompts/{id}`** — the Prompt Detail endpoint.
   `backend/app/routes/prompts.py::get_prompt_detail` calls
   `settings_service.get_prompt_history_enabled(db)` on every request:
   - **ON**: returns `promptText` / `responseText` populated,
     `promptBodyHidden: false`.
   - **OFF**: returns empty strings for both and `promptBodyHidden: true`.
     The frontend's `PromptHistoryTab.tsx` detail modal checks that flag
     and renders the yellow "Prompt and response content is hidden"
     banner instead of the body.

   This applies **before** the per-source branching, so a CloudWatch
   Logs record and an Agent Chat record are gated identically.

2. **`AgentService.chat` / `chat_stream`** — the write path for Agent
   Chat (`backend/app/services/agent_service.py`). Before adding a
   `ChatMessage` row, the service calls
   `settings_service.get_prompt_history_enabled(db)`:
   - **ON**: persists both the user and assistant messages (with
     encryption, see below), returns the response.
   - **OFF**: emits an INFO log (`"Prompt history disabled; skipping
     chat_messages persistence for session=…"`), returns the response,
     writes nothing. The chat still works in-session — the next turn's
     history-replay to the LLM is just empty.

   Demo generation (`demo_service.generate_demo_data`) goes through the
   same encryption helpers at write time, but doesn't short-circuit on
   the toggle — demo creation is already an explicit admin action.

Flips take effect immediately across every ECS task without a redeploy:
the service re-reads the KV on every call rather than caching the value
process-wide.

### How encryption at rest works

Two columns in two tables are encrypted: `chat_messages.content` and
`demo_activity_records.prompt_text` / `response_text`. Everything else
stays plaintext.

The wrapper is `backend/app/services/crypto_service.py::PromptHistoryCrypto`,
a thin layer over the `cryptography` library's `Fernet` primitive
(AES-128-CBC for confidentiality + HMAC-SHA256 for integrity + a built-in
timestamp). Format:

```
<plaintext>  →  "enc:v1:<base64-fernet-token>"
```

The `enc:v1:` prefix is a self-describing envelope — a reader never has
to carry an `is_encrypted` sidecar column, and a future `v2` can
coexist with old rows during a rotation.

**Write path.** Both `agent_service.chat` and `demo_service.generate_demo_data`
construct the row's content by calling `get_crypto().encrypt(plaintext)`,
which:

1. Returns the plaintext unchanged if it's empty or the key is not loaded
   (so writes don't fail during a misconfigured deploy — see Gatekeeper
   below for why that case won't normally happen in production).
2. Otherwise returns `"enc:v1:" + Fernet.encrypt(plaintext).decode("ascii")`.

**Read path.** Every reader goes through `get_crypto().decrypt(stored)`:

- `chat_message_service._pair_to_record` for Agent Chat records flowing
  into Prompt History.
- `AgentService._load_history` consumers inside `chat()` /
  `chat_stream()` — otherwise the LLM would receive ciphertext envelopes
  as prior turns.
- `demo_service.get_demo_records` for demo rows flowing into Prompt
  History + the analytics pipeline.

`decrypt()` returns one of three things:

1. If the string doesn't start with `enc:v1:` → returns it unchanged.
   This is how legacy plaintext rows from before the encryption rollout
   render correctly.
2. If the prefix matches and the current key decrypts it → returns the
   plaintext.
3. If the prefix matches but the current key **cannot** decrypt it (bad
   token, key rotated without a backfill, key not loaded in this task) →
   returns the placeholder string `"[encrypted content — <reason>]"`.
   The UI never sees raw ciphertext, so a key loss is a legibility
   problem instead of a security exposure.

**Key sourcing.** `PromptHistoryCrypto` accepts both forms of key
material: a native `Fernet.generate_key()` string (used verbatim), or
any other string (SHA-256-derived to 32 bytes, then
`base64.urlsafe_b64encode`-encoded, then handed to Fernet). The second
path is what makes CDK's `generateSecretString` work directly — Secrets
Manager's random alphanumeric isn't a valid Fernet key on its own, and
the derivation avoids a Lambda-backed custom resource just to shape key
material.

**Key rotation.** Replace the Secrets Manager value, then run
`aws ecs update-service --force-new-deployment` so a new task-set pulls
the new value. New writes use the new key. Existing rows written under
the old key stop decrypting cleanly and render the placeholder from
case 3 above. There is intentionally no automatic re-encryption — a
backfill script is one SQL `UPDATE … WHERE content LIKE 'enc:v1:%'`
loop away but belongs with the operator, not with this app, because it
needs both the old and new keys loaded simultaneously.

**What is NOT encrypted by this layer.** CloudWatch Logs, S3 invocation
logs, and CloudTrail content. Those are AWS-managed services with their
own at-rest encryption (KMS / SSE-S3), and this app only reads them —
never writes to or stores them locally. Confusingly, the dashboard
*displays* their content in the same Prompt Detail modal, but only
because it's fetching live from AWS at render time. The toggle still
hides the body for those records when OFF (see point 1 of the gate
above); the AWS-side encryption is what actually protects the raw bytes
in those services.

### Gatekeeper: enabling the toggle requires a usable key

The backend refuses `PUT /api/settings/prompt-history {enabled: true}`
with **HTTP 400** (`error: "encryption_key_unavailable"`) unless
`get_crypto().is_available` returns true at request time. The precise
check lives in `backend/app/routes/settings.py::update_prompt_history_settings`:

```python
crypto = get_crypto()
if body.enabled and not crypto.is_available:
    raise HTTPException(
        status_code=400,
        detail={
            "error": "encryption_key_unavailable",
            "message": "Prompt History cannot be enabled because the backend has no usable at-rest encryption key. …",
            "encryption_error": crypto.init_error,
        },
    )
```

Disabling the toggle is always allowed, even without a key.

The Settings UI mirrors this: `frontend/src/components/v2/Settings.tsx`
reads `GET /api/settings/prompt-history` on mount, derives
`toggleBlocked = !enabled && !encryption_available`, and when that's
true it disables the toggle control, adds `cursor: not-allowed`, and
renders a prominent orange notice ("Enabling blocked — encryption key
not loaded. Configure `PROMPT_HISTORY_ENCRYPTION_KEY` and try again.").
`extractToggleError` surfaces the backend's `detail.message` verbatim
if the server rejects the request anyway (e.g. a client with a stale
`encryption_available` cache).

The toggle cannot be turned on while the backend is missing its
encryption key, so a user never sees prompt bodies through a "Prompt
History is on" state without at-rest encryption being in effect.

### Behavior matrix

| Toggle | Source                                   | List metadata | Detail body in UI                           | Write path                           |
| ------ | ---------------------------------------- | ------------- | ------------------------------------------- | ------------------------------------ |
| ON     | Agent Chat                               | visible       | plaintext (Fernet-decrypted)                | encrypted write to `chat_messages`   |
| ON     | Demo                                     | visible       | plaintext (Fernet-decrypted)                | encrypted write to `demo_activity_records` |
| ON     | CloudWatch Logs / S3 / CloudTrail        | visible       | plaintext (as AWS returned, AWS-side crypto)| n/a (not persisted by this app)      |
| OFF    | any of the above                         | visible       | yellow "content hidden" banner              | Agent Chat & demo stop persisting    |

### Disclaimer

The AWS Content disclaimer is surfaced inline on the Settings tab (as a
warning-variant panel next to the toggle), the Prompt History tab (above
the list), and the Agent Chat tab (above the composer). Verbatim wording
is the standard AWS Content / Third-Party Content clause; it lives in
`frontend/src/components/v2/DeploymentDisclaimer.tsx` as a single
exported constant so there's exactly one place to update it. No admin
can enable persistence or interact with the data surface without seeing
it.

Because this is distributed as an open-source sample, the operator who
deploys this dashboard is responsible for all regulatory compliance
(HIPAA, GDPR, PCI, etc.) for the content they choose to retain and
surface. Encryption at rest only protects stored bytes — it does not
constitute a HIPAA Business Associate Agreement, a PCI assessment, or
any other regulatory certification.

## AI Agents (13)

> The PII Detector agent was retired from the solution. Prompt/response content
> is instead protected via the Prompt History toggle + at-rest encryption
> controlled from the admin **Settings** tab — see
> [Prompt History & Encryption](#prompt-history--encryption) above.

| Category | Agent | Type | Description |
|----------|-------|------|-------------|
| Security & Guardrails | Cost Anomaly Alerter | ⚙️ | Flags daily spend spikes vs rolling average |
| Security & Guardrails | Guardrail Recommender | 🤖 | LLM-powered guardrail config generation |
| Security & Guardrails | Model Cost Optimizer | ⚙️ | Identifies expensive models for simple tasks |
| Usage Intelligence | Prompt Quality Analyzer | ⚙️ | Detects anti-patterns in prompts |
| Usage Intelligence | Model Adoption Tracker | ⚙️ | Compares model capabilities and costs |
| Usage Intelligence | Usage Forecaster | ⚙️ | Projects 30/60/90-day token consumption |
| Compliance & Operations | Data Residency Auditor | ⚙️ | Checks invocation regions vs policies |
| Compliance & Operations | Latency & Error Monitor | ⚙️ | Computes p95 latency and error rates |
| Compliance & Operations | Chargeback Report Generator | ⚙️ | Per-team monthly cost reports |
| Compliance & Operations | Onboarding Assistant | ⚙️ | Detects and welcomes new users |
| Compliance & Operations | Chargeback Reconciliation | 🤖 | LLM-powered billing vs usage analysis |
| Orchestration | Policy Compliance Checker | ⚙️ | Evaluates invocations against policies |
| Orchestration | Weekly Digest Orchestrator | 🤖 | Compiles weekly findings summary |

🤖 = Agentic (LLM-powered) · ⚙️ = Deterministic (direct tool execution)

## Deployment

### Prerequisites at a glance

| Tool | Version | Purpose |
| --- | --- | --- |
| Node.js | **22 LTS** (recommended), 20 still works | CDK (`npx cdk`) and frontend `npm run build`. Node 18 and 20 are past Node's own LTS end-of-life. The AWS SDK for JavaScript v3 (used transitively by `aws-cdk-lib`) will require Node 22 for versions published after the first week of January 2027 — until then, Node 20 keeps working with an informational warning on every CDK command. On Node 22 the warning doesn't appear and no future CDK upgrade will surprise you. |
| Python | **3.x** | Version bumps, ECS rollout polling, and Cognito user bootstrap in the deploy scripts |
| AWS CLI | **v2.15 or newer** | Talks to AWS APIs used by the deploy scripts. v1 (and very old v2 builds) omit the `rolloutState` field on ECS `describe-services`, which the backend rollout polling loop reads — older CLIs still work via the `/api/health/config` fallback probe through API Gateway but exit the polling loop later. Verified against `aws-cli/2.36.5`. |
| Git | any recent | Cloning the repo |
| AWS credentials | any | Named profile OR env vars (`AWS_ACCESS_KEY_ID` / `AWS_SECRET_ACCESS_KEY` / `AWS_SESSION_TOKEN`) with rights to create VPC/RDS/ECS/ECR/IAM/S3/CloudFront/CodeBuild/Cognito in the target account |

The deploy scripts prompt for the target AWS account ID at runtime, or read it from `$AWS_ACCOUNT_ID`. Press Enter at the prompt to use the AWS credentials already in your environment.

---

### Caveat — deploying to a shared AWS account with other API Gateway apps

**TL;DR — this only matters if you share the AWS account with another CDK/CFN/Terraform/SAM app that also uses API Gateway.** On a dedicated account for this dashboard (the recommended path, and the only path this README's examples assume), skip this section.

The Backend stack provisions the **account-level** API Gateway CloudWatch logging role via `AWS::ApiGateway::Account`. This resource is a singleton — AWS allows exactly one logging role per account per region, and whichever CDK app / CFN template / Terraform state deploys it last wins ownership. By default this stack takes ownership (`cloudWatchRole: true` under the hood). That's the correct behaviour on a dedicated account and on the vast majority of multi-app accounts (two CDK apps both setting it is benign — same role permissions, no functional break).

What to be aware of if your account already has a different owner for that role:

| Situation | What happens | What to do |
|---|---|---|
| Fresh / dedicated account for this dashboard | We provision + manage the role. No conflict possible. | Nothing. Deploy normally. |
| Account also runs other API Gateway apps that **set the logging role themselves** (another CDK app, Terraform's `aws_api_gateway_account`, SAM, etc.) | Each deploy wins-the-race on the role setting. Both apps keep logging because the role's `AmazonAPIGatewayPushToCloudWatchLogs` policy is identical. | Nothing — benign tug-of-war. |
| Account runs API Gateway apps that **expect a specific role ARN to be the account-level one** (rare — a monitoring / compliance app that reads `aws apigateway get-account`) | Our deploy flips the ARN under them. | Set `SKIP_API_GW_LOGGING_ROLE=1` on the backend deploy — this stack piggybacks on the existing role. |
| **Partial teardown + redeploy** of this dashboard on a shared account (delete Frontend + Backend, keep Data, then redeploy — see [Partial teardown](#partial-teardown--data-preserved)) | During the ~5-10 min redeploy window, the account-level role is temporarily unset. Any **other API Gateway stage creates** (not stage updates — existing stages keep logging) in the account during that window will fail with `CloudWatch Logs role ARN must be set`. | Either (a) full teardown instead of partial, or (b) coordinate the maintenance window with other API Gateway owners, or (c) set `SKIP_API_GW_LOGGING_ROLE=1` so another app owns the role. |

Full teardown + redeploy from scratch (the main teardown runbook) does NOT trigger the window-of-outage issue even on shared accounts — the role is unset for the whole account anyway during that longer window because the dashboard is going away, so there's nothing for other apps to depend on.

---

### Preflight checks (automatic)

Both deploy scripts (`build-and-deploy-backend.sh` and `build-and-deploy-frontend.sh`) source `infrastructure/preflight.sh` at the top and run five ordered checks before touching any AWS resource. If your environment already satisfies every prerequisite, the checks add ~1–2 seconds to the deploy and you'll see a compact "detected + OK" block for each one. If anything is missing or misconfigured, the preflight fails fast with an actionable message — and for four of the five checks, offers to fix the issue in-place after y/N confirmation. This turns Steps 1–3 below into "documentation of what the preflight verifies" rather than mandatory manual work.

| # | Check | Detects | Auto-install path | Runs |
| --- | --- | --- | --- | --- |
| 1 | Node.js | `node` / `npm` / `npx` on PATH + Node major ≥ 18 | nvm (glibc ≥ 2.28) or unofficial glibc-2.17 build (glibc < 2.28), auto-selected by `ldd` version | before profile prompt |
| 2 | AWS CLI | `aws` on PATH + major ≥ 2 (warns if v2 < 2.15 per prereq table) | Downloads the official `awscli-exe-linux-x86_64.zip` and installs to `~/.local/aws-cli/` — no sudo required | before profile prompt |
| 3 | Python 3 | `python3` on PATH | Detected package manager (`dnf` / `yum` / `apt-get` / `brew`) via passwordless sudo when available | before profile prompt |
| 4 | `zip` | `zip` on PATH (used to package the backend source in Step 5) | Same package-manager pattern as Python 3 | before profile prompt |
| 5 | AWS credentials | `sts:GetCallerIdentity` returns 0 for the resolved profile | None — cannot be "installed". Failure prints AWS-native refresh commands (`aws sso login`, `aws configure`, environment-variable export) | **after** profile resolution |

Every check follows the same three-phase pattern:

1. **Detect + print.** Always outputs a compact block showing what was checked and its status. Users see the detected state either way.
2. **On failure, describe + confirm.** Prints the failure reason plus a "Proposed install" summary of what the auto-installer would do, then asks `Proceed? [y/N]:`.
3. **Install (if confirmed) and re-check.** The installer updates `PATH` in the current shell so the deploy continues in the same run, persists the change to `~/.bashrc` or `~/.zshrc` (detected from `$SHELL`), and re-runs the detection to confirm success before letting the deploy proceed. If the re-check still fails, the deploy exits with the manual copy-paste instructions.

**Environment overrides**

- `PREFLIGHT_AUTO_INSTALL=1` — skip every y/N prompt and proceed with install automatically. Intended for non-interactive runs (CI pipelines, scripted deploys).
- Without that variable, non-interactive shells (no TTY on stdin) skip auto-install entirely and print manual instructions.

**What you'll see at the start of every deploy**

```
=== Preflight: Node.js environment ===
  glibc:           2.26
  node:            /home/user/node-glibc217/.../bin/node
  npm:             /home/user/node-glibc217/.../bin/npm
  npx:             /home/user/node-glibc217/.../bin/npx
  node --version:  v20.18.0
  Status:          OK — Node 20 meets the 18+ requirement.

=== Preflight: AWS CLI ===
  aws:             /usr/local/bin/aws
  version:         aws-cli/2.36.5 Python/3.11.6 ...
  Status:          OK

=== Preflight: Python 3 ===
  python3:         /usr/bin/python3
  version:         Python 3.9.16
  Status:          OK

=== Preflight: zip ===
  zip:             /usr/bin/zip
  Status:          OK

  [profile resolution / account-ID prompt happens here]

=== Preflight: AWS credentials ===
  profile:         <env credentials>
  account:         123456789012
  arn:             arn:aws:sts::123456789012:assumed-role/Admin/user
  Status:          OK
```

If you're on a fresh machine and want to preinstall the tools yourself first, Steps 1–3 below cover the same install paths the preflight would run — they're kept for reference, offline hosts, and cases where an admin wants to install once for many users.

---

### Step 1 — Install Node.js

> **Optional if you're OK with the preflight installing it.** The deploy script's preflight (see [Preflight checks](#preflight-checks-automatic)) detects missing Node and offers to auto-install Node 20 after y/N confirmation — using the same install path this step documents. Do this step manually only if you want Node available outside the deploy scripts, or if you prefer to preinstall on a locked-down host.

Node 18 or 20 is required. Skip this step if `node --version` already prints one of those.

**1a. Modern Linux / macOS (glibc ≥ 2.28) — use nvm:**

```bash
curl -o- https://raw.githubusercontent.com/nvm-sh/nvm/v0.39.7/install.sh | bash
export NVM_DIR="$HOME/.nvm" && . "$NVM_DIR/nvm.sh"
nvm install 20
nvm use 20
node --version   # v20.x.x
```

**1b. Older Linux (glibc < 2.28, e.g. Amazon Linux 2) — use the unofficial glibc-2.17-compat builds:**

```bash
# Check your glibc version
ldd --version | head -1

# Install Node 20 from unofficial-builds
NODE_VER=v20.18.0
mkdir -p ~/node-glibc217 && cd ~/node-glibc217
curl -LO "https://unofficial-builds.nodejs.org/download/release/${NODE_VER}/node-${NODE_VER}-linux-x64-glibc-217.tar.xz"
tar xf "node-${NODE_VER}-linux-x64-glibc-217.tar.xz"

# Persist to PATH
echo "export PATH=\"\$HOME/node-glibc217/node-${NODE_VER}-linux-x64-glibc-217/bin:\$PATH\"" >> ~/.bashrc
export PATH="$HOME/node-glibc217/node-${NODE_VER}-linux-x64-glibc-217/bin:$PATH"

node --version   # v20.18.0
```

---

### Step 2 — Install other tooling

> **Optional for AWS CLI, Python 3, and `zip`.** The preflight will detect any of these that are missing and offer to auto-install them (AWS CLI to `~/.local/aws-cli` without sudo; Python 3 and `zip` via the detected package manager with passwordless sudo). Git is not preflight-checked because you already used it to clone the repo, but the version check below is still a good sanity step.

Most Linux images ship Python 3 and Git. Confirm with:

```bash
python3 --version    # 3.x
git --version
```

Install/upgrade the AWS CLI v2 if missing (or if `aws --version` reports v1):

```bash
# Linux x86_64
curl "https://awscli.amazonaws.com/awscli-exe-linux-x86_64.zip" -o /tmp/awscliv2.zip
unzip -q /tmp/awscliv2.zip -d /tmp
sudo /tmp/aws/install --update
hash -r          # drop bash's cached path to any older /usr/bin/aws
aws --version    # expect aws-cli/2.15+ (verified with 2.36.5)
```

If `aws --version` still reports v1 after install, an older `/usr/bin/aws` is earlier in `PATH`. Either prepend `/usr/local/bin` to `PATH` (`export PATH=/usr/local/bin:$PATH`) or call `/usr/local/bin/aws` directly.

---

### Step 3 — Configure AWS credentials

> **Required — this is the one preflight check with no auto-install path.** Credentials are user-specific, so if the preflight's `sts:GetCallerIdentity` smoke-test fails, it prints AWS-native refresh commands (`aws sso login`, `aws configure`, or environment-variable export) but exits without touching anything. Get credentials working before running the deploy scripts.

Any of these work — the deploy scripts adapt to whichever you have.

**Option A — Environment variables (AWS Credentials / SSO short-lived credentials):**

```bash
export AWS_ACCESS_KEY_ID=...
export AWS_SECRET_ACCESS_KEY=...
export AWS_SESSION_TOKEN=...
aws sts get-caller-identity   # should print your account + role
```

**Option B — Named profile** matching the pattern the scripts default to:

```bash
aws configure sso --profile Admin-<12-digit-account-id>
# or
aws configure --profile Admin-<12-digit-account-id>
```

**Option C — Short-lived AWS credentials from an external source** (AWS SSO CLI, SAML federation helper, or any other credential-issuing utility your organization provides): fetch temporary credentials and export them as env vars, then follow Option A.

Confirm before continuing:

```bash
aws sts get-caller-identity
```

---

### Step 4 — Clone the repo

```bash
git clone <your-repo-url>
cd bedrock-activity-dashboard
```

Avoid working directories with spaces in the path — some CDK tooling handles them, but a few edge cases do not. `~/bedrock-activity-dashboard` is the safest choice.

---

### Step 5 — Deploy the backend (fresh account, end-to-end)

```bash
bash infrastructure/build-and-deploy-backend.sh
```

**Optional — override the AppRegistry metadata** (owner email and source repo URL recorded on the AppRegistry Application):

```bash
export APP_OWNER=alice@yourcompany.com
export APP_REPOSITORY=https://github.com/your-org/bedrock-activity-dashboard
bash infrastructure/build-and-deploy-backend.sh
```

Defaults are generic placeholders so nothing personal is required for a first deploy.

**Non-interactive / CI use** — set `PREFLIGHT_AUTO_INSTALL=1` to bypass every preflight y/N prompt and let missing tools install automatically:

```bash
PREFLIGHT_AUTO_INSTALL=1 bash infrastructure/build-and-deploy-backend.sh
```

The Cognito admin user is bootstrapped in Step 6 (frontend deploy) — see that section for the `COGNITO_ADMIN_USERNAME` / `COGNITO_ADMIN_EMAIL` env vars.

**Execution order:**

1. Preflight — Node.js, AWS CLI, Python 3, and `zip` checks run first (see [Preflight checks](#preflight-checks-automatic)). Any missing tool triggers a confirmation prompt for auto-install, unless `PREFLIGHT_AUTO_INSTALL=1` is set.
2. Account-ID prompt:

    ```
    AWS Account ID (12 digits, or press Enter to use current env credentials):
    ```

3. AWS credentials preflight — `sts:GetCallerIdentity` against the resolved profile. Failure prints refresh commands and exits before any CloudFormation change is attempted.
4. Steps 0–9 (below) run the actual deploy.

- Press **Enter** if you configured Option A (env-based credentials) above.
- Type your **12-digit account ID** to derive `Admin-<id>` as the profile name.
- Pass a custom profile as the first CLI arg to skip the prompt entirely: `bash infrastructure/build-and-deploy-backend.sh my-profile`.

The script is idempotent and runs these steps automatically:

| Step | What it does | Fresh-account time |
| --- | --- | --- |
| 0 | Bump the frontend `package.json` patch version to track the deploy | seconds |
| — | Run `npm install` in `infrastructure/` if `node_modules/` is missing (one-time) | ~1 min |
| — | Create a placeholder `frontend/dist/` so CDK synth can evaluate the FrontendStack | seconds |
| 1 | `cdk bootstrap` the account/region | ~1 min |
| 2 | Deploy `BedrockDashboard-AppRegistry-<env>` (auto-cleans up if stuck in `ROLLBACK_COMPLETE`) | ~30 s |
| 3 | Deploy `BedrockDashboard-Data-<env>` (VPC, RDS, ECR, Secrets Manager, **Cognito User Pool + App Client + Domain + admin group**) — auto-cleanup as above | **~15 min for RDS** |
| 3.5 | Enable **Bedrock model invocation logging** in the deploy region — creates the invocation-log bucket (block-public-access + SSE-S3 + Bedrock `PutObject` bucket policy), creates/reuses `BedrockInvocationLoggingRole`, creates the `/aws/bedrock/model-invocations` log group, and calls `put-model-invocation-logging-configuration`. Idempotent — skips if logging is already configured, so custom setups aren't stomped on. Without this the dashboard shows all zeros because no invocation logs exist. | ~30 s (or seconds if already configured) |
| 4 | Create the S3 build artifact bucket | seconds |
| 5 | Zip and upload the backend source | seconds |
| 6 | Create/update the CodeBuild project + IAM role | seconds |
| 7 | Run the build in CodeBuild (docker build + push to ECR) | 2–4 min |
| 8 | Deploy `BedrockDashboard-Backend-<env>` (auto-cleanup as above) and resolve the ECS cluster/service from stack outputs | 3–5 min |
| 9 | Force a new ECS deployment and wait for rollout | 3–8 min |

The admin user + temp password are provisioned in Step 6 (frontend deploy) once the Login URL exists — so nothing dead-ends this script.

**Auto-cleanup of failed stacks.** Before each CDK deploy the script checks the target CloudFormation stack's status. If it's in `ROLLBACK_COMPLETE`, `ROLLBACK_FAILED`, or `CREATE_FAILED` — states that block updates — it calls `aws cloudformation delete-stack` and waits for the delete before letting CDK recreate the stack. Healthy stacks are left alone.

On success you'll see:

```
=== ✅ Backend deployment complete ===
API Gateway:      https://<rest-api-id>.execute-api.us-west-2.amazonaws.com/<env>
Internal ALB:     internal-<alb-id>.us-west-2.elb.amazonaws.com (private — reachable only via API Gateway)
Cognito pool:     us-west-2_XXXXXXXXX

Next: run infrastructure/build-and-deploy-frontend.sh to deploy the SPA,
register the CloudFront callback URL with the Cognito app client, and
provision a temporary admin login.
```

---

### Step 6 — Deploy the frontend (and bootstrap admin sign-in)

The Backend stack must exist first because the FrontendStack reads the API Gateway invoke URL from it.

```bash
bash infrastructure/build-and-deploy-frontend.sh
```

**Optional — drive the Cognito admin user bootstrap non-interactively:**

```bash
export COGNITO_ADMIN_USERNAME=admin
export COGNITO_ADMIN_EMAIL=you@yourcompany.com
bash infrastructure/build-and-deploy-frontend.sh
```

If unset, the script prompts for them in Step 5.

**Non-interactive / CI use** — same `PREFLIGHT_AUTO_INSTALL=1` bypass as the backend script:

```bash
PREFLIGHT_AUTO_INSTALL=1 bash infrastructure/build-and-deploy-frontend.sh
```

Like the backend script, the frontend script runs the same preflight checks (see [Preflight checks](#preflight-checks-automatic)) before the account-ID prompt, and runs the AWS credentials preflight after profile resolution. Steps 0–5 below are the deploy work that happens once every preflight passes.

The script runs these steps in order:

| Step | What it does |
| --- | --- |
| 0 | `cdk bootstrap` the account/region (idempotent) |
| — | Run `npm install`/`npm ci` in `frontend/` if `node_modules/` is missing (one-time, ~30 s) |
| 1 | Bump the frontend `package.json` patch version |
| 2 | Build the SPA (`npm run build`) — no Cognito values baked in, they are fetched at runtime |
| 3 | Deploy `BedrockDashboard-Frontend-<env>` (S3 bucket + CloudFront distribution + asset uploads + cache invalidation) |
| 4 | Register the CloudFront callback + logout URLs on the Cognito app client (localhost entries preserved) |
| 5 | Bootstrap the Cognito admin user with a temporary password and add it to the `admin` group |

Step 5 is idempotent: an existing user is reset to a fresh temporary password (still forced to change on next sign-in) rather than duplicated.

#### How the temporary password is delivered

Up front — before Step 0 — the script prompts for the admin username (default `admin`) and the admin email address. Both can be preset via env vars (`COGNITO_ADMIN_USERNAME`, `COGNITO_ADMIN_EMAIL`) for non-interactive runs. The prompts run first so a 25-minute CDK deploy doesn't block waiting for input mid-way.

**Email-only delivery, no opt-outs.** Cognito itself generates the one-time temporary password and emails it directly to the admin user from its built-in email service (`no-reply@verificationemail.com`). The password **never** touches the deploying host — not stdout, not a file, not shell history, not CI logs. There is no env-var escape hatch to print it inline or write it to disk. The account lands in Cognito's `FORCE_CHANGE_PASSWORD` state so the user is forced to set a new permanent password on first sign-in.

The one Cognito API that gets called depends on the current state of the admin user:

| Current state | Cognito API | What arrives in the inbox | Final state |
|---|---|---|---|
| User does not exist | `admin-create-user` (no SUPPRESS, no `--temporary-password`) | Welcome email with the one-time password | `FORCE_CHANGE_PASSWORD` |
| `FORCE_CHANGE_PASSWORD` (prior deploy, never signed in) | `admin-create-user --message-action RESEND` | Fresh welcome email; any prior temp password is invalidated | `FORCE_CHANGE_PASSWORD` |
| `CONFIRMED` / `RESET_REQUIRED` (user has set a password before) | `admin-reset-user-password` | Password-reset verification code | `RESET_REQUIRED` |

All three paths force a password change on next sign-in via Cognito's hosted UI.

**Operator workflow:**

1. Run the deploy script. Enter the admin email when prompted (or set `COGNITO_ADMIN_EMAIL` beforehand).
2. Wait for the deploy to finish.
3. Check the inbox at the email you supplied for a message from `no-reply@verificationemail.com`.
4. Visit the Login URL printed in the deploy summary. Sign in with the admin username + the password (or reset code) from the email.
5. Cognito's hosted UI immediately prompts for a new permanent password. Set it.

Example successful stdout (no password anywhere in the output):

```
╔══════════════════════════════════════════════════════════════════════════╗
║  ✅ Frontend deployment complete                                          ║
╠══════════════════════════════════════════════════════════════════════════╣
║  Frontend:            https://<distribution-id>.cloudfront.net
║  Version:             1.14.x
║  Login URL:           https://<distribution-id>.cloudfront.net
╠══════════════════════════════════════════════════════════════════════════╣
║  Cognito admin credentials (one-time; forced change on first sign-in)    ║
║  User pool:           us-west-2_XXXXXXXXX
║  Username:            admin
║  Email:               you@yourcompany.com
║  Temporary password:  emailed to you@yourcompany.com
╚══════════════════════════════════════════════════════════════════════════╝

Check the inbox at you@yourcompany.com for a Cognito welcome email with
your one-time password (from no-reply@verificationemail.com — look in Spam
/ Promotions too).

Sign in once with that password — Cognito will immediately require you to
set a new one.

Login URL: https://<distribution-id>.cloudfront.net

Cognito's default welcome-email temporary password expires in 7 days. If
the message never arrives, or has expired, re-run this script to resend;
the account stays in FORCE_CHANGE_PASSWORD so Cognito will still force a
change on first sign-in.
```

**Required IAM for the deploying principal** (admin role already has these; included for operators who build a scoped deploy role):

- `cognito-idp:AdminCreateUser`
- `cognito-idp:AdminResetUserPassword` (used when an existing user is `CONFIRMED`)
- `cognito-idp:AdminGetUser`
- `cognito-idp:ListUsers` (email-alias conflict resolution)
- `cognito-idp:AdminAddUserToGroup` + `cognito-idp:CreateGroup`

**Email deliverability**: the sample relies on Cognito's built-in email service. It sends from `no-reply@verificationemail.com`, is capped at **50 emails per account per day**, and goes to spam more often than a verified domain would. For production, attach SES: configure the User Pool's `EmailConfiguration.SourceArn` to a verified SES identity (CDK: `email: cognito.UserPoolEmail.withSES({...})`). Documented but not required for a sample deploy.

---

### Step 7 — (Optional) Deploy AgentCore agents

Each of the 13 agents is a self-contained package under `agentcore-agents/{agent-name}/`. The `aws-targets.json` files are committed with the placeholder `__AWS_ACCOUNT_ID__`; the deploy scripts substitute the resolved account ID at deploy time and restore the placeholder on exit so the working tree stays account-agnostic.

**One agent:**

```bash
bash agentcore-agents/setup-and-deploy.sh guardrail-recommender
```

**All 13 non-chargeback agents:**

```bash
bash agentcore-agents/deploy-all.sh
```

Both scripts prompt for the AWS account ID (or read `$AWS_ACCOUNT_ID`). The runtime IDs generated during deploy land in `agentcore-agents/deployment-results.json`. The backend picks these up automatically on startup — either from the file (if baked into the container) or from the `AGENTCORE_RUNTIME_IDS` env var (populated by the deploy pipeline). When neither is present the backend falls back to inline agent invocation.

---

### Re-deploying an existing environment

If all three stacks are already running and you want to pick up the latest commits:

```bash
git pull
bash infrastructure/build-and-deploy-backend.sh
bash infrastructure/build-and-deploy-frontend.sh
```

Both scripts are idempotent. CDK runs a diff and applies only what changed; CodeBuild rebuilds and pushes a new image under `:latest`; ECS rolls the service with the deployment circuit breaker on; Alembic migrations run on container start.

Three things worth knowing before you redeploy:

- **AWS profile naming.** `ada credentials update` writes to the `default` profile unless you pass `--profile=Admin-<account-id>`. The deploy scripts expect `Admin-<account-id>`. Set it explicitly or supply credentials via `AWS_ACCESS_KEY_ID` + `AWS_SECRET_ACCESS_KEY` + `AWS_SESSION_TOKEN` env vars (the preflight accepts either).
- **Package version bump.** `build-and-deploy-backend.sh` Step 0 auto-bumps the patch version in `frontend/package.json` and the `APP_VERSION` string in `frontend/src/App.tsx` even on backend-only runs. If a previous aborted run left those files dirty, discard them before pulling: `git checkout -- frontend/package.json frontend/src/App.tsx`.
- **Watch the `cdk diff` for RDS replacement.** Any change on the Data stack that marks `AWS::RDS::DBInstance PostgresDb replace` (toggling `storageEncrypted`, changing engine family or storage type in a replacement-triggering way) cannot land on a live deployment because the Data stack's RDS endpoint is exported and imported by Backend. See the Troubleshooting entry on "Cannot update export … as it is in use by …" for the fix. Fresh greenfield deploys do not hit this.

To preview the diff before touching anything:

```bash
cd infrastructure
export AWS_PROFILE=Admin-<account-id>
export AWS_REGION=us-west-2
npx --yes cdk diff BedrockDashboard-Data-dev
npx --yes cdk diff BedrockDashboard-Backend-dev
npx --yes cdk diff BedrockDashboard-Frontend-dev
```

The deploy scripts show the same diff before `cdk deploy` is called; running it standalone first just lets you review without side effects.

### One-time cutover: public ALB → internal ALB + API Gateway

The dashboard puts an API Gateway REST API in front of an internal ALB (private subnets, no public IP). Fresh deploys get this architecture automatically — no special procedure. **Only existing deployments that pre-date this architecture need the three-step cutover below, once.** The reason is a CloudFormation export deadlock:

- A pre-cutover Backend stack exported `AlbDnsName`, which the Frontend stack imported for its `/api/*` origin.
- The new architecture replaces that export with `ApiEndpoint` + `ApiStage` and also replaces the ALB (scheme flip: `internet-facing` → `internal`).
- CloudFormation refuses to update or delete an export that another stack is actively importing. A direct back-to-back deploy deadlocks on the first `cdk deploy BedrockDashboard-Backend` call.

Fix: release the import first, then update the Backend, then re-add the import pointing at the new API Gateway.

```bash
# Step 1 — release the import.
# Deploy the Frontend with /api/* REMOVED. The /api endpoints become
# unavailable until Step 3, so plan a short maintenance window.
SKIP_API_ORIGIN=1 bash infrastructure/build-and-deploy-frontend.sh

# Step 2 — flip the ALB scheme and bring up API Gateway + VPC Link.
# ALB replacement runs here (~5-10 min total deploy time). The ECS
# service is also replaced because its load-balancer target reference
# changes; expect a brief task start-up window. Health check now
# probes /api/health/config through API Gateway (no laptop → ALB curl).
bash infrastructure/build-and-deploy-backend.sh

# Step 3 — point /api/* at the new API Gateway.
# Normal frontend deploy, no SKIP_API_ORIGIN flag.
bash infrastructure/build-and-deploy-frontend.sh
```

**Expected downtime:** `/api/*` returns CloudFront 503 or similar from the moment Step 1 completes until the moment Step 3 is deployed and CloudFront's cache is warm (~1 min after Step 3). Static SPA assets (default behavior → S3) stay available throughout.

**Evidence the cutover landed:**

```bash
# 1. ALB scheme is now 'internal'
aws cloudformation describe-stack-resource \
  --stack-name BedrockDashboard-Backend-dev \
  --logical-resource-id <ALB-logical-id> \
  --query 'StackResourceDetail.PhysicalResourceId' --output text \
  --region us-west-2 | xargs -I{} aws elbv2 describe-load-balancers \
  --load-balancer-arns {} --region us-west-2 \
  --query 'LoadBalancers[0].Scheme' --output text
# -> internal

# 2. Direct curl from the internet fails (unroutable DNS or no public IP)
INTERNAL_ALB_DNS=$(aws cloudformation describe-stacks \
  --stack-name BedrockDashboard-Backend-dev \
  --query 'Stacks[0].Outputs[?OutputKey==`InternalAlbDnsName`].OutputValue' --output text \
  --region us-west-2)
curl -m 5 "http://${INTERNAL_ALB_DNS}/health"
# -> curl: (28) Operation timed out  (expected — no public reach)

# 3. Public path works via CloudFront or API Gateway
curl "https://<cloudfront-domain>/api/health/config"
# -> {"cognito":{"userPoolId":"...","clientId":"..."}, ...}
```

Everyday deploys after the cutover do not need any special flag — `SKIP_API_ORIGIN` stays unset.

### Tearing down a deployment

To fully uninstall from an account, delete the four stacks in reverse dependency order (Frontend → Backend → Data → AppRegistry), then clean up residuals the stacks do not own. The runbook below was validated end-to-end on 2026-10-29 — each phase is **conditional** on what the preceding one left behind, so always run the inspection command before the delete.

**Set your deploy context once:**

```bash
export AWS_REGION=us-west-2
# Pick ONE of the two auth modes:
#   (a) Named profile for a long-lived or SSO account:
export AWS_PROFILE=Admin-<YOUR_AWS_ACCOUNT_ID>
#   (b) Env credentials (current STS session) — leave AWS_PROFILE unset.
```

> The commands below quote `--region $AWS_REGION` explicitly so the same block works for either auth mode. If you set `AWS_PROFILE`, every `aws` call picks it up automatically; otherwise it falls back to env credentials.

#### Phase 1 — Delete stacks (reverse dependency order)

Each `wait` call blocks until CFN reports `DELETE_COMPLETE`; it returns 0 on success, non-zero on failure, so chaining with `&& echo "✅ ..."` makes success obvious.

```bash
# 1a. Frontend (~3-5 min — CloudFront teardown is the slow part)
aws cloudformation delete-stack --stack-name BedrockDashboard-Frontend-dev --region $AWS_REGION
aws cloudformation wait stack-delete-complete --stack-name BedrockDashboard-Frontend-dev --region $AWS_REGION && echo "✅ Frontend deleted"

# 1b. Backend (~5-10 min — ECS service drain + ALB teardown)
aws cloudformation delete-stack --stack-name BedrockDashboard-Backend-dev --region $AWS_REGION
aws cloudformation wait stack-delete-complete --stack-name BedrockDashboard-Backend-dev --region $AWS_REGION && echo "✅ Backend deleted"

# 1c. Data (~5-10 min — RDS + VPC + NAT teardown)
#     WARNING: dev RDS data is lost here (dev stacks use removalPolicy: DESTROY).
#     If you want to keep RDS/Cognito, stop at 1b — see "Partial teardown" below.
aws cloudformation delete-stack --stack-name BedrockDashboard-Data-dev --region $AWS_REGION
aws cloudformation wait stack-delete-complete --stack-name BedrockDashboard-Data-dev --region $AWS_REGION && echo "✅ Data deleted"

# 1d. AppRegistry (<1 min — just an AppRegistry application)
aws cloudformation delete-stack --stack-name BedrockDashboard-AppRegistry-dev --region $AWS_REGION
aws cloudformation wait stack-delete-complete --stack-name BedrockDashboard-AppRegistry-dev --region $AWS_REGION && echo "✅ AppRegistry deleted"
```

Confirm all four are gone before proceeding to Phase 2:

```bash
aws cloudformation list-stacks --region $AWS_REGION \
  --stack-status-filter CREATE_COMPLETE UPDATE_COMPLETE CREATE_FAILED UPDATE_FAILED ROLLBACK_COMPLETE UPDATE_ROLLBACK_COMPLETE DELETE_FAILED \
  --query 'StackSummaries[?contains(StackName, `BedrockDashboard`)].{Name:StackName,Status:StackStatus}' \
  --output table
```

**Expected:** empty table (just the column header). **If any stack shows `DELETE_FAILED`,** inspect `aws cloudformation describe-stack-events --stack-name <StackName> --max-items 20 --region $AWS_REGION` for the blocker — typically another stack is still importing one of its exports, or a resource like S3 is non-empty.

#### Phase 2 — Residual cleanup (inspect-then-delete, each is conditional)

The CDK stacks don't own every AWS resource they interact with. The items below are created outside CDK (either by the deploy scripts or by historical deploy paths) and need manual cleanup for a fully clean account.

For each: run the inspection command, then run the delete command **only if** the inspection confirms the resource exists. The deploy scripts recreate everything on the next deploy, so none of these are destructive in a context where you're about to redeploy.

**2a. Bedrock invocation log S3 bucket** — created by `build-and-deploy-backend.sh` Step 3.5. The bucket is kept on stack delete so historical logs survive a redeploy, but a full teardown should purge it.

```bash
aws s3 ls | grep bedrock-invocation-logs
```
*If a bucket named `bedrock-invocation-logs-<YOUR_AWS_ACCOUNT_ID>` appears:*
```bash
aws s3 rb s3://bedrock-invocation-logs-$(aws sts get-caller-identity --query Account --output text) --force --region $AWS_REGION \
  && echo "✅ invocation-logs bucket deleted"
```

**2b. `BedrockInvocationLoggingRole` IAM role** — also from Step 3.5. Has an inline policy named `BedrockLoggingPolicy` that must be dropped before the role can be deleted.

```bash
aws iam list-role-policies --role-name BedrockInvocationLoggingRole 2>&1 | head -5
```
*If the role exists (returns `BedrockLoggingPolicy` under `PolicyNames`):*
```bash
aws iam delete-role-policy --role-name BedrockInvocationLoggingRole --policy-name BedrockLoggingPolicy \
  && aws iam delete-role --role-name BedrockInvocationLoggingRole \
  && echo "✅ BedrockInvocationLoggingRole deleted"
```

**2c. Bedrock model-invocation-logging configuration** — account-level setting that Step 3.5 configures. Deleting the role above leaves this pointing at a dead role; the next deploy fixes it, but the clean state is to delete it.

```bash
aws bedrock get-model-invocation-logging-configuration --region $AWS_REGION 2>&1 | head -5
```
*If it returns a `loggingConfig` block:*
```bash
aws bedrock delete-model-invocation-logging-configuration --region $AWS_REGION \
  && echo "✅ logging config deleted"
```

**2d. CodeBuild project `bedrock-dashboard-backend-build-dev`** — deployed imperatively by the backend script (not CDK), so it survives the stack delete.

```bash
aws codebuild batch-get-projects --names bedrock-dashboard-backend-build-dev --region $AWS_REGION \
  --query 'projects[0].name' --output text 2>&1
```
*If it returns the project name (not a `RESOURCE_NOT_FOUND_EXCEPTION`):*
```bash
aws codebuild delete-project --name bedrock-dashboard-backend-build-dev --region $AWS_REGION \
  && echo "✅ CodeBuild project deleted"
```

**2e. CodeBuild build-artifacts S3 bucket** — created by the backend script to upload the source zip for CodeBuild.

```bash
aws s3 ls | grep -E 'bedrock-dashboard-build'
```
*If a bucket named `bedrock-dashboard-build-dev-<YOUR_AWS_ACCOUNT_ID>` appears:*
```bash
aws s3 rb s3://bedrock-dashboard-build-dev-$(aws sts get-caller-identity --query Account --output text) --force --region $AWS_REGION \
  && echo "✅ build-artifacts bucket deleted"
```

**2f. ECR repository** — usually auto-cleaned by the Data stack delete (CDK removes the repo on `removalPolicy: DESTROY`), but check in case images are left behind.

```bash
aws ecr describe-repositories --repository-names bedrock-dashboard-backend-dev --region $AWS_REGION 2>&1 | head -3
```
*If it does not return a `RepositoryNotFoundException`:*
```bash
aws ecr delete-repository --repository-name bedrock-dashboard-backend-dev --force --region $AWS_REGION \
  && echo "✅ ECR repo deleted"
```

**2g. Secrets Manager residuals** — three secrets have historically lived in Secrets Manager for this app. The Data stack and Backend stack's `removalPolicy: DESTROY` usually honors immediate deletion, but historical deploy paths (pre-`f4038b6` for the admin password, for example) could leave a secret in the 7-30 day planned-deletion recovery window. A planned-deletion secret with the same logical name blocks the next deploy.

```bash
# List any Bedrock-Dashboard-owned secrets, including ones already scheduled for deletion
aws secretsmanager list-secrets --region $AWS_REGION --include-planned-deletion \
  --query "SecretList[?contains(Name, 'bedrock-dashboard') || contains(Name, 'PromptHistoryEncryptionKey')].{Name:Name,DeletedAt:DeletedDate}" \
  --output table
```
*If anything appears,* force-delete each one by ARN (replace `<arn>` with the ARN listed in the table above):
```bash
aws secretsmanager list-secrets --region $AWS_REGION --include-planned-deletion \
  --query "SecretList[?contains(Name, 'bedrock-dashboard') || contains(Name, 'PromptHistoryEncryptionKey')].ARN" --output text \
  | xargs -I{} aws secretsmanager delete-secret --secret-id {} --force-delete-without-recovery --region $AWS_REGION
echo "✅ Secrets Manager residuals force-deleted"
```
Secrets to expect in the result set:
- `bedrock-dashboard/<env>/db-credentials` — RDS admin credentials (owned by Data stack; usually auto-deleted)
- `PromptHistoryEncryptionKey<suffix>` — Agent Chat Fernet key (owned by Backend stack; usually auto-deleted)
- `bedrock-dashboard/<env>/initial-admin-password-<suffix>` — leftover from a pre-`f4038b6` deploy that used Secrets Manager to deliver the Cognito admin password; this flow has been removed in favor of email delivery, but the secret can persist from an older stack generation. **This one is a known doc-gap residual** — not visible to current deploys but present on accounts that were first deployed before 2026-10 Cognito-email migration.

**2h. CDK bootstrap stack `CDKToolkit`** — optional. Delete only if you want to wipe CDK state entirely for the account/region (deploys to any CDK app in this account/region will need to re-bootstrap).

```bash
# Keep by default. To delete:
# aws cloudformation delete-stack --stack-name CDKToolkit --region $AWS_REGION
# (You'll also need to empty the CDK staging bucket first — list via `aws s3 ls | grep cdk`.)
```

**2i. API Gateway account-level logging role** — the Backend stack provisions the account-level API Gateway CloudWatch logging role via `AWS::ApiGateway::Account`. CFN cleans this up on stack delete (the `Account` resource reverts the account to "no logging role set"), so this is usually a no-op. **Only check this if** another API Gateway deploy in the account later fails with `CloudWatch Logs role ARN must be set` — which means the setting was somehow orphaned.

```bash
# Shows the current account-level API Gateway CloudWatch role ARN, if any
aws apigateway get-account --region $AWS_REGION --query 'cloudwatchRoleArn' --output text
```
*If it returns an ARN that points at a non-existent IAM role,* clear it:
```bash
aws apigateway update-account --region $AWS_REGION --patch-operations op=replace,path=/cloudwatchRoleArn,value=
echo "✅ API Gateway account-level logging role cleared"
```

**2j. Lingering CloudWatch log groups** — the API Gateway access-log group has `RemovalPolicy: DESTROY` for dev and `RETAIN` for prod. Prod leftovers stay. ECS container log groups are also usually auto-cleaned. Check for any stragglers:

```bash
aws logs describe-log-groups --region $AWS_REGION \
  --query 'logGroups[?contains(logGroupName, `bedrock-dashboard`) || contains(logGroupName, `/aws/bedrock/`) || contains(logGroupName, `/aws/apigateway/bedrock-dashboard`)].logGroupName' \
  --output text
```
*If anything is listed:*
```bash
# Replace <log-group-name> with each one printed above
# aws logs delete-log-group --log-group-name <log-group-name> --region $AWS_REGION
```

#### Phase 3 — Verify shell env is clean before any fresh redeploy

Export vars from an earlier deploy can silently steer the next one into BYO-Cognito-pool mode (importing a pool from a different account) or skip bootstrap steps you actually need. Clear them explicitly:

```bash
unset EXISTING_USER_POOL_ID EXISTING_USER_POOL_CLIENT_ID EXISTING_USER_POOL_DOMAIN_URL \
      COGNITO_IDENTITY_PROVIDER SKIP_COGNITO_BOOTSTRAP SKIP_API_ORIGIN
env | grep -E '^(EXISTING_|COGNITO_|SKIP_)'
```
**Expected:** no output. If anything prints, STOP — the next deploy will inherit those env vars and the CDK synth will reflect it (BYO pool mode destroys the CDK-managed pool, for example).

#### Phase 4 — Fresh deploy

Only after the stack list is empty and the env is clean, redeploy:

```bash
# Set the admin email so the Cognito bootstrap at the end of the frontend script
# doesn't block on an interactive prompt mid-deploy.
export COGNITO_ADMIN_EMAIL=<your-email@your-domain.com>

# Backend first — provisions VPC, RDS, ECR, Cognito, ECS/Fargate, API Gateway,
# VPC Link v2. First-run takes ~15-20 min (RDS provisioning is the long tail).
bash infrastructure/build-and-deploy-backend.sh

# Then frontend — provisions S3, CloudFront, bootstraps the Cognito admin user,
# emails the one-time password. ~5-10 min.
bash infrastructure/build-and-deploy-frontend.sh
```

#### Partial teardown — Data preserved

The most common partial-teardown case is **unblocking a replacement change on the Data stack**: delete Frontend and Backend only, then re-run both deploy scripts. The Data stack stays in place and the next `cdk deploy BedrockDashboard-Data-dev` can replace RDS freely once the cross-stack import is gone.

This path preserves RDS data, the Cognito User Pool (and its users), the VPC, ECR, and NAT gateways. It's noticeably faster than a full teardown (~15-20 min total) and the only app-state loss is in-flight ECS tasks.

**Shared-account caveat.** When the Backend stack is deleted, the `AWS::ApiGateway::Account` resource it owns (the account-level API Gateway CloudWatch logging role) is destroyed, and the account-level setting reverts to "no role". During the ~5-10 min window before the Backend redeploy restores it, **any OTHER API Gateway in the same region cannot create new stages** (existing stages keep logging — they cache the role ARN). On a dedicated account this doesn't matter; on a shared engineering account with multiple API Gateway projects, coordinate the partial teardown or set `SKIP_API_GW_LOGGING_ROLE=1` on this stack so another app owns the role instead.

```bash
# Phase 1a + 1b from above — delete Frontend + Backend, stop before Data.
aws cloudformation delete-stack --stack-name BedrockDashboard-Frontend-dev --region $AWS_REGION
aws cloudformation wait stack-delete-complete --stack-name BedrockDashboard-Frontend-dev --region $AWS_REGION
aws cloudformation delete-stack --stack-name BedrockDashboard-Backend-dev --region $AWS_REGION
aws cloudformation wait stack-delete-complete --stack-name BedrockDashboard-Backend-dev --region $AWS_REGION

# Phase 2 residual cleanup is OPTIONAL here (log bucket, invocation-logging config,
# CodeBuild project all survive the Backend-stack delete). Rebuild from scratch:
bash infrastructure/build-and-deploy-backend.sh
bash infrastructure/build-and-deploy-frontend.sh
```

---

### Troubleshooting

| Symptom | Cause / fix |
| --- | --- |
| `Preflight: Node.js environment` shows `Status: FAILED — Node.js is not installed` | The deploy-script preflight detected missing Node and printed a `Proceed? [y/N]:` prompt. Answer `y` to auto-install Node 20 (path auto-picked from glibc version) or `N` to install manually per Step 1. Set `PREFLIGHT_AUTO_INSTALL=1` to skip the prompt entirely on CI hosts. |
| `Preflight: AWS credentials` shows `Status: FAILED` | The `sts:GetCallerIdentity` smoke-test failed — credentials are missing or expired. The preflight prints AWS-native refresh commands (`aws sso login`, `aws configure`, environment-variable export). Get credentials working before re-running the deploy. |
| `node: /lib64/libc.so.6: version 'GLIBC_2.28' not found` | Node prebuilt binary needs a newer glibc than your OS provides. The preflight auto-detects glibc < 2.28 and uses the unofficial glibc-2.17 build path when auto-installing; manual install path is Step 1b. |
| `npx cdk` → `TypeError: Cannot read properties of undefined (reading 'fileExists')` | `infrastructure/node_modules/` missing or stale. Delete it and re-run the script: `rm -rf infrastructure/node_modules && bash infrastructure/build-and-deploy-backend.sh`. |
| CodeBuild `POST_BUILD` fails with `docker push … Reason: exit status 1` | Usually the ECR repo does not exist yet. Run the full deploy script (which deploys the Data stack first) rather than a partial one. |
| `The config profile (Admin-<id>) could not be found` | You don't have that named profile configured. Either configure it (Step 3 Option B), pass an existing profile as arg 1, or press Enter at the prompt to use env-based credentials. |
| `ValidationError: Cannot find asset at .../frontend/dist` | The script normally creates a placeholder — if you deleted `frontend/dist/` mid-run, either re-run the backend script or `mkdir -p frontend/dist/assets`. |
| CDK Node.js EOL notice (34635) | Node 18 is EOL Nov 2025. Not fatal — plan to migrate to Node 22 LTS (Step 1). |
| CDK output includes `Warning: NodeVersionSupportWarning: The AWS SDK for JavaScript (v3) versions published after the first week of January 2027 will require node >=22. You are running node v20.x` | Informational warning only — not a failure. Node 20 is already past its own LTS end-of-life; the SDK is dropping support in the Jan 2027 release window. Three real-world cases: **(a)** You're on Node 20 and deploying before Jan 2027 → warning appears, deploy succeeds, no action needed. **(b)** You're on Node 20 and running a fresh `npm install` after Jan 2027 → `npm install` may pull a newer SDK that errors out with "unsupported Node version" on actual commands. Upgrade to Node 22 to resolve. **(c)** You're on Node 22+ → warning doesn't appear. Nothing to do. Upgrading from Node 20 to Node 22 is backward-compatible for this codebase; no code changes needed. |
| CloudFormation `ROLLBACK_COMPLETE` blocks next deploy | The deploy scripts auto-detect this state before each `cdk deploy` and run `aws cloudformation delete-stack` first. Just re-run the script. To clean up manually: `aws cloudformation delete-stack --stack-name <StackName> --region us-west-2 && aws cloudformation wait stack-delete-complete --stack-name <StackName> --region us-west-2`. |
| Data stack `cdk deploy` rolls back with `Cannot update export BedrockDashboard-Data-dev:ExportsOutputFnGetAttPostgresDb...EndpointAddress... as it is in use by BedrockDashboard-Backend-dev` | A replacement change on `AWS::RDS::DBInstance PostgresDb` (toggling `storageEncrypted`, changing the engine family, or any other property that CFN flags as `requires replacement`) cannot be applied on a live deployment: the Data stack exports the RDS endpoint and Backend imports it to populate the ECS task's `DB_HOST`. CFN refuses to replace an export while an active importer holds it, so the update rolls back. Greenfield deploys do not hit this because Backend does not exist yet when Data first creates the (now-encrypted) RDS. Remediation: tear down Frontend then Backend (see [Tearing down a deployment](#tearing-down-a-deployment)), then re-run `bash infrastructure/build-and-deploy-backend.sh` followed by `bash infrastructure/build-and-deploy-frontend.sh`. The Data stack re-applies the replacing change cleanly on the first run with no live importer; Backend and Frontend recreate against the new RDS endpoint. All dev RDS data is lost in this process (dev uses `removalPolicy: DESTROY`); seed-on-startup repopulates config tables. |
| Backend stack `delete-stack` returns silently but the stack status stays at `UPDATE_COMPLETE` | CFN rejected the delete because Backend's `ApiEndpoint` / `ApiStage` exports are still imported by the Frontend stack. Check with `aws cloudformation describe-stack-events --stack-name BedrockDashboard-Backend-dev --max-items 5 --region us-west-2` — look for an event with `Reason: Delete canceled. Cannot delete export ... as it is in use by BedrockDashboard-Frontend-dev`. Fix: delete the Frontend stack first (`aws cloudformation delete-stack --stack-name BedrockDashboard-Frontend-dev --region us-west-2 && aws cloudformation wait stack-delete-complete ...`), then retry the Backend delete. |
| Backend deploy rolls back on the ALB with `Cannot update export ... AlbDnsName ... as it is in use` or `Target group is already associated with another load balancer` | You're deploying the ALB-scheme change (public → internal) over an existing stack that pre-dates it and skipped the three-step cutover. See [One-time cutover: public ALB → internal ALB + API Gateway](#one-time-cutover-public-alb--internal-alb--api-gateway) above — you must deploy the Frontend with `SKIP_API_ORIGIN=1` first to release the `AlbDnsName` import, then re-deploy Backend. |
| First deploy after a Backend-stack teardown fails with Secrets Manager `InvalidRequestException: You can't create this secret because a secret with this name is already scheduled for deletion` | Secrets Manager keeps deleted secrets in a 7-30 day recovery window by default; CDK's `removalPolicy: DESTROY` isn't always honored on the Fernet key secret. Clear it with `aws secretsmanager delete-secret --secret-id PromptHistoryEncryptionKey<suffix> --force-delete-without-recovery --region us-west-2`. Find the exact ID with `aws secretsmanager list-secrets --region us-west-2 --query 'SecretList[?contains(Name, \`PromptHistoryEncryptionKey\`)].Name' --output text`. |
| Backend task crashes on startup with `ERROR [alembic.util.messaging] Multiple head revisions are present for given argument 'head'` and the ECS deployment circuit breaker rolls back the Backend stack | Two Alembic migrations list the same `down_revision`, so `alembic upgrade head` sees two heads and refuses to pick one. Common after a `main → feature-branch` merge where both branches added a migration off the same parent. Inspect with `grep -E '^(revision\|down_revision)' backend/alembic/versions/*.py` — the pair with matching `down_revision` is the fork. Fix: add a new merge revision in `backend/alembic/versions/` whose `down_revision` is a tuple of both head revision IDs and whose `upgrade()`/`downgrade()` bodies are no-ops (standard Alembic merge pattern; see `023_merge_heads.py` for an example). Do not edit either of the forked migrations themselves — the merge-revision approach keeps both intact. |
| `Failed to load /api/health/config` in the browser | The SPA cannot reach the backend. The request path is: browser → CloudFront → API Gateway → VPC Link v2 → internal ALB → Fargate task. Check each hop: `aws cloudfront get-distribution-config` (origin should be `<api>.execute-api.<region>.amazonaws.com`, HTTPS_ONLY), `aws apigateway get-rest-apis` + `aws apigateway get-stages` (stage deployed, VPC Link `Available`), `aws elbv2 describe-target-health` (targets healthy), `aws ecs describe-services`. The deploy script's final-step probe hits this same path, so a successful deploy proves it works. |
| Streaming chat (`/api/agent/chat/stream`) returns the final answer in one shot instead of token-by-token | API Gateway's integration ``ResponseTransferMode`` is defaulting back to ``BUFFERED``. The CDK Backend stack sets it to ``STREAM`` via a Cfn property override; verify by running `aws apigateway get-integration --rest-api-id <id> --resource-id <proxy-id> --http-method ANY --query 'responseTransferMode'` — expected `STREAM`. If missing, re-deploy the Backend stack. If still missing, the override may need to be applied through the resource's method directly rather than the integration (CDK may be updated). CloudFront `readTimeout` also must be at or above 60s on the `/api/*` behavior. |
| API Gateway returns `{"message": "Unauthorized"}` on every request including `/api/health/config` | The Cognito authorizer is attached to the catch-all `/{proxy+}` route; `/api/health/config` has its own method with `AuthorizationType: NONE`. If `/api/health/config` is also returning Unauthorized, the specific resource is missing from the deployed stage — re-deploy the Backend stack to re-create the method and the stage deployment. |
| Streaming chat cuts off after exactly 29–30s with a 500 or `Endpoint request timed out` | The integration's `TimeoutInMillis` is still at the 29s REST API default. The CDK stack raises it to 900000ms (15 min) via `addPropertyOverride`. Verify: `aws apigateway get-integration ... --query 'timeoutInMillis'` — expected `900000`. CloudFront `readTimeout` is the other half of this; max without a quota increase is 60s. Longer streams need the AWS support ticket to raise the CloudFront origin read-timeout quota. |
| Backend deploy fails on `AWS::ApiGateway::Stage BackendApi/DeploymentStage.<env>` with `CloudWatch Logs role ARN must be set in account settings to enable logging` | The REST API stage enables access logging, which requires an account-level API Gateway CloudWatch logging role (set via `AWS::ApiGateway::Account`). By default this stack creates it (`cloudWatchRole: true` in CDK). The failure means another CDK app or stack in the same account owns the role and the deploy is being blocked, OR the account's existing role has been deleted but the account-level setting still points at the deleted ARN. Fix: either (a) accept CDK ownership — delete whatever manages the account's existing role, then redeploy — or (b) opt-out from this stack and rely on the existing role: `SKIP_API_GW_LOGGING_ROLE=1 bash infrastructure/build-and-deploy-backend.sh`. |
| Backend deploy fails on `AWS::ApiGateway::Method` with `is not a valid ALB or NLB arn` | The REST API integration points at the ALB via the `IntegrationTarget` property. The CFN docs describe this field as "the ALB or NLB listener", but the API Gateway service actually validates for the load-balancer ARN (`arn:aws:elasticloadbalancing:region:acct:loadbalancer/app/name/id`), not the listener ARN. The CDK stack passes `loadBalancerArn` to match the service behaviour; if you ever see this error, the override may have regressed to `listenerArn`. |
| Cognito sign-in error `redirect_mismatch` | The CloudFront URL isn't in the app client's `CallbackURLs`. Re-run `bash infrastructure/build-and-deploy-frontend.sh` — Step 4 patches the client. |
| Forgot the temp password or the welcome email never arrived | Re-run `bash infrastructure/build-and-deploy-frontend.sh`. Step 5 detects the existing user and resends (via `admin-create-user --message-action RESEND` if still in `FORCE_CHANGE_PASSWORD`) or sends a password-reset verification code (via `admin-reset-user-password` if the user is already `CONFIRMED`). Welcome-email temp passwords expire 7 days after send. Re-running is cheap — Steps 3 and 4 are no-ops when nothing changed. |
| `ExpiredTokenException` during Step 5 (Cognito bootstrap) | Short-lived credentials (SSO, SAML, or other STS-based temporary credentials) can expire mid-deploy when the CDK stack takes several minutes. The frontend deploy itself succeeds — only the user bootstrap is skipped. Refresh credentials and re-run the frontend script; it will fly through Steps 0–4 as no-ops and land back on Step 5. |
| `sh: tsc: command not found` during Step 2 (frontend build) | `frontend/node_modules/` was missing and pre-c64e5df scripts did not install it. Pull the latest script (it now runs `npm ci`/`npm install` automatically) or install manually: `(cd frontend && npm ci)`. |
| `Need to install the following packages: cdk@2.x. Ok to proceed?` prompt stalls the script | Older versions of the frontend script omitted `--yes` on `npx cdk deploy`. Pull the latest, or just answer `y`. |
| Backend deploy Step 9 exits via `/health` fallback instead of `rolloutState=COMPLETED` | Deploy succeeded — the ALB is serving 200s. The path is a workaround for older AWS CLI versions (v1.x) that don't return `rolloutState`. Upgrade to AWS CLI v2 (see Step 2) to get the clean `COMPLETED` signal. |
| Dashboard shows all zeros (Total Invocations 0, Users 0) even after using Bedrock | Bedrock model invocation logging isn't enabled in the region you invoked from. Check with `aws bedrock get-model-invocation-logging-configuration --region us-west-2` — an empty response means it's off. Deploys from `2a77169` onward auto-enable it in the deploy region (Step 3.5). For older deploys or additional regions, run `bash infrastructure/enable-bedrock-logging-all-regions.sh`. Also confirm the Playground call happened in the region the dashboard reads (`us-west-2` by default). |
| `enable-bedrock-logging-all-regions.sh` reports every region as "skipped (Bedrock not available)" | Old versions of the script assumed the shared invocation-log bucket already existed and swallowed real errors. Pull the latest — the script now auto-creates the bucket and prints the real API error per region if anything fails. |
| Pipeline `lint:backend` fails on a commit that only touched docs (or on any commit) with rules like `UP045` / `UP007` / `I001` / `BLE001` | `.gitlab-ci.yml` used to install ruff unpinned; newer releases add default rules that flag pre-existing style. Deploys from `2b821a4` onward pin `ruff==0.16.0` and use the repo-root `pyproject.toml` for the rule set. If lint fails again, either an upstream contributor bumped the pin or added a rule to `pyproject.toml`. Run locally with the same version to reproduce: `python -m pip install ruff==<version-in-ci> && python -m ruff check backend/`. Fix real bugs the rule flags, or if the rule is over-eager for this codebase, add its code to the `ignore` list in `pyproject.toml`. |
| Prompt History shows recent activity but nothing older than ~48 hours (Bedrock invocations from CloudWatch Logs / S3 disappear, only in-app "Agent Chat" rows remain) | The running ECS task's `CloudTrailService` cached a stale S3 `keyPrefix` at boot — most commonly because it initialized before Step 3.5 finished enabling model-invocation-logging on a fresh deploy. Bedrock writes to `bedrock-logs/<region>/AWSLogs/...` but the reader scans `bedrock-logs/AWSLogs/...` and finds nothing. Deploys from `14f7ded` onward inject `BEDROCK_S3_LOG_BUCKET` and `BEDROCK_S3_LOG_KEY_PREFIX` from CDK so the failure mode is structurally prevented. On an older deployment either redeploy the backend or force-restart the task: `aws ecs update-service --cluster <cluster> --service <service> --force-new-deployment --region us-west-2`. Verify the running task carries the env vars: `aws ecs describe-services --cluster <cluster> --services <service> --region us-west-2 --query 'services[0].taskDefinition' --output text` then `aws ecs describe-task-definition --task-definition <arn> --region us-west-2 --query "taskDefinition.containerDefinitions[0].environment[?name==\`BEDROCK_S3_LOG_BUCKET\` \|\| name==\`BEDROCK_S3_LOG_KEY_PREFIX\`]" --output table`. Expected: two rows showing `bedrock-invocation-logs-<account>` and `bedrock-logs/<region>`. Confirm actual S3 layout with `aws s3 ls s3://bedrock-invocation-logs-<account>/ --recursive --region us-west-2 \| head` — paths should look like `bedrock-logs/<region>/AWSLogs/<account>/BedrockModelInvocationLogs/<region>/YYYY/MM/DD/HH/*.json.gz`. |
| `deploy-all.sh` or `setup-agent.sh` fails immediately with `tr: when not truncating set1, string2 must be non-empty` | Pre-9fad5ed versions of these scripts had `tr '-' ''` where they meant `tr -d '-'` (delete hyphens from agent name to derive the AgentCore project directory). Empty `project_name` then breaks the downstream `cd`. Sibling scripts `deploy-remaining.sh` and `setup-and-deploy.sh` already had the correct form. Fixed in commit `9fad5ed` — pull latest `main`. |
| Backend task crashes on startup with `ModuleNotFoundError: No module named 'psycopg'` right after a fresh Docker build | SQLAlchemy 2.x's `postgresql://` scheme default DBAPI changed from `psycopg2` to `psycopg` v3 in a later release, and `requirements.txt` only installs `psycopg2-binary`. On a freshly-rebuilt image, SA tries to import the v3 driver and the task exits with ExitCode 1 before FastAPI even starts. Deploys from `0501924` onward pin the DBAPI explicitly in `backend/app/config.py` by generating `postgresql+psycopg2://` URLs — pull latest `main`. The old 2026-09 task kept running happily because it had the pre-change SA cached from its original build, so an in-place rebuild looks "sudden." |
| ECS keeps spawning new tasks that come up healthy then get killed ~40 seconds later, CloudFormation stuck in `UPDATE_ROLLBACK_IN_PROGRESS` for 15+ minutes, and every stopped task has the exact same `imageDigest` even after a fresh `latest` push | Fargate resolves the `latest` tag to a specific image digest when a task-set is created and reuses that digest for every task the task-set spawns — it does **not** re-resolve `latest` per task launch. If you push a new `latest` after a task-set has started (e.g. you fixed a bug mid-rollback), the live task-set keeps pulling the old broken digest and crashes forever. Resolution: force a NEW ECS deployment so a fresh task-set is created and re-resolves `latest`: `aws ecs update-service --cluster <cluster> --service <service> --force-new-deployment --region us-west-2`. Confirm with `aws ecr describe-images --repository-name bedrock-dashboard-backend-dev --image-ids imageTag=latest --query 'imageDetails[0].{Digest:imageDigest,PushedAt:imagePushedAt}'` (what `latest` points to right now) vs `aws ecs describe-tasks --cluster <cluster> --tasks <task-arn> --query 'tasks[0].containers[0].imageDigest'` (what a running task actually pulled). If they don't match, the task-set is stale. |

## Configuration

- **Bedrock Logging**: CloudWatch Logs (`/aws/bedrock/model-invocations`) + S3 (`bedrock-invocation-logs-<YOUR_AWS_ACCOUNT_ID>`). The backend deploy script now enables this automatically in the deploy region (Step 3.5) — no manual step required for a fresh deploy. To enable it in additional regions run `bash infrastructure/enable-bedrock-logging-all-regions.sh`; that script auto-creates the shared invocation-log bucket (with block-public-access + SSE-S3 + Bedrock `PutObject` bucket policy) and configures every Bedrock region, surfacing the real error per region if anything fails.
- **Cross-region inference profiles**: Bedrock's cross-region inference profiles (`global.*` and regional variants like `us.anthropic.claude-*`) can route an invocation to a different AWS region than the one you called from — the log entry records both `region` (the API caller's region) and `inferenceRegion` (where the model actually ran). Model-invocation-logging always writes to the **caller's** region, so you only need logging enabled in the regions you invoke Bedrock **from**, not in every region a cross-region profile might route to. Practical effect: with logging enabled in `us-west-2`, a call to `global.anthropic.claude-haiku-4-5-*` that transparently executes in `ap-southeast-4` still appears on the dashboard with full token counts and cost.
- **Model Pricing**: Configurable per-model token prices in the Pricing tab, or synced from the AWS Price List API (the region synced follows `AWS_REGION` on the ECS task, not a hardcoded value).
- **Teams**: Create teams and assign IAM user identities for cost attribution
- **Budget Thresholds**: Set daily/weekly/monthly spend alerts per user, team, or account
- **Cognito**: User Pool ID and App Client ID are outputs on the `BedrockDashboard-Data-<env>` stack. Retrieve them with `aws cloudformation describe-stacks --stack-name BedrockDashboard-Data-dev --query 'Stacks[0].Outputs'`.

## Portability notes

If you fork this repo, the per-account moving pieces are:

- `infrastructure/cdk.context.json` is gitignored — CDK re-resolves per-account lookups (AZs, hosted zones) on each deploy.
- `agentcore-agents/**/aws-targets.json` uses the `__AWS_ACCOUNT_ID__` placeholder; deploy scripts substitute it at deploy time and restore it on exit.
- Cognito settings are never baked into the frontend build — they are served from `GET /api/health/config` at runtime.
- `CORS_ORIGINS`, `CLOUDTRAIL_REGIONS`, `BEDROCK_REGION`, and the pricing sync region all follow the deploy region rather than being pinned.

## Architecture Deep Dive

The [Architecture](#architecture) section above is the orienting summary. This section covers what talks to what, where data lives, and where the trust boundaries are.

### Three CDK stacks + one governance stack

Four CloudFormation stacks, deployed in dependency order:

| Stack | Scope | Lifetime |
|---|---|---|
| `BedrockDashboard-AppRegistry-<env>` | AWS Service Catalog AppRegistry application + attribute group. Associates the other three stacks so they appear as a single application in the AWS console's myApplications view. | Independent — never rebuilt. |
| `BedrockDashboard-Data-<env>` | VPC, RDS, ECR, Secrets Manager (DB creds), Cognito User Pool + App Client + Hosted UI domain, VPC Flow Logs, shared access-logs S3 bucket | Durable — survives Backend/Frontend rebuilds. Prod uses `RemovalPolicy.RETAIN`. |
| `BedrockDashboard-Backend-<env>` | ECS Fargate cluster + ALB + task definition, task role IAM, Secrets Manager (Fernet key), CloudWatch alarms, SNS alert topic, EventBridge Scheduler for CUR + AgentCore Discovery | Rebuilt on every backend deploy. |
| `BedrockDashboard-Frontend-<env>` | S3 origin bucket, CloudFront distribution, response-headers policy, SPA deployment custom resources | Rebuilt on every frontend deploy. |

Durable data resources (RDS, Cognito, ECR, VPC) live in `Data` so a bad Backend or Frontend deploy cannot destroy them. The deploy scripts enforce the order via `--exclusively` flags in `cdk deploy`.

### Network topology and trust boundaries

```
Internet
   │
   ▼  (HTTPS, viewer certificate = default CloudFront cert)
┌─────────────────────────────────────────────────────────────┐
│ CloudFront distribution                                     │
│   ├── default behavior → S3 bucket (SPA assets, OAI-gated)  │
│   ├── /api/* behavior  → API Gateway (HTTPS, TLSv1.2)       │
│   └── ResponseHeadersPolicy (HSTS, CSP, X-CTO, X-Frame-O)   │
└──────────────────────┬──────────────────────────────────────┘
                       │  (HTTPS end-to-end, execute-api.<region> default cert)
                       ▼
┌─────────────────────────────────────────────────────────────┐
│ API Gateway REST API (regional)                             │
│   ├── Cognito user-pool authorizer on /{proxy+} ANY         │
│   ├── GET /api/health/config — unauthenticated (login page) │
│   ├── Stage: throttling + access logs + STREAM mode (SSE)   │
│   └── Private integration via VPC Link v2                   │
└──────────────────────┬──────────────────────────────────────┘
                       │  (VPC Link v2 ENIs, private subnets)
                       ▼
┌─────────────────────────────────────────────────────────────┐
│ VPC (10.x.x.x/16, 2 AZs)                                    │
│                                                             │
│   PUBLIC subnets          — NAT Gateways only               │
│   PRIVATE_WITH_EGRESS     — Internal ALB + ECS Fargate tasks│
│                             + VPC Link v2 ENIs              │
│   PRIVATE_ISOLATED        — RDS PostgreSQL (no route out)   │
│                                                             │
│   Flow Logs → CloudWatch (ALL traffic, env-aware retention) │
└─────────────────────────────────────────────────────────────┘
```

Security groups:

- **VPC Link SG** — the SG assigned to the API Gateway VPC Link v2 ENIs. Egress scoped to `albSg :80` only; `allowAllOutbound: false`.
- **ALB SG** — accepts HTTP:80 **only from the VPC Link SG** (closed to the internet). Forwards to task SG on 8000. The ALB itself has no public IP (`publicLoadBalancer: false`).
- **Task SG** — accepts 8000 from ALB SG only; egress is unrestricted (talks to RDS, Bedrock, Secrets Manager, S3, CloudWatch, STS).
- **DB SG** — accepts 5432 from the VPC CIDR; `allowAllOutbound: false`.

Trust boundaries:

- **Cognito ↔ API Gateway**: API Gateway's Cognito user-pool authorizer validates the `Authorization` header's ID token against the User Pool's JWKS before any backend code runs. Requests without a valid token (except `GET /api/health/config`) are rejected at the edge.
- **Cognito ↔ Backend**: FastAPI *also* verifies the token in `app/auth.py` (defense in depth — the authorizer and FastAPI both validate independently).
- **ECS task ↔ Secrets Manager**: `ecs.Secret.fromSecretsManager()` resolves at task-start time; the actual secret never appears in the task definition JSON or CloudFormation output. Two secrets today: DB credentials (owned by Data stack) and the Prompt History Fernet key (owned by Backend stack — never leaves the backend, never touches the Data stack).
- **CloudFront ↔ S3**: Origin Access Identity (OAI). The bucket policy grants read only to the OAI principal; the bucket itself is private.
- **CloudFront ↔ API Gateway**: HTTPS end-to-end, TLSv1.2. The ``execute-api.<region>.amazonaws.com`` endpoint has an AWS-managed publicly-trusted cert — no custom domain or ACM import needed.
- **API Gateway ↔ ALB**: Private integration over VPC Link v2. The ALB is reachable only from VPC Link ENIs inside the VPC. Direct `curl http://<alb-dns>/...` from the public internet is unroutable.

### Data flow — ingestion

```
Bedrock API caller
    │   (any invocation in us-west-2 — SDK, Playground, Agent Chat, demo)
    ▼
┌─────────────────────────────────────────────────────────────┐
│  Bedrock Model Invocation Logging                           │
│    ├─ CloudWatch Logs  (/aws/bedrock/model-invocations)     │
│    └─ S3               (bedrock-invocation-logs-<account>)  │
└──────────────┬──────────────────────────────────────────────┘
               │
               ▼
┌─────────────────────────────────────────────────────────────┐
│  CloudTrailService (app/services/cloudtrail_service.py)     │
│    Hybrid priority:                                         │
│      1. CloudWatch Logs — real-time, last 48h               │
│      2. S3 invocation logs — historical, days-old           │
│      3. CloudTrail LookupEvents — metadata-only fallback    │
└──────────────┬──────────────────────────────────────────────┘
               │
               ▼
┌─────────────────────────────────────────────────────────────┐
│  Merge pipeline (app/routes/prompts.py, routes/activity.py) │
│    + Agent Chat records (chat_messages table, decrypted)    │
│    + Demo records (demo_activity_records, decrypted)        │
│    + Cost application (CostService)                         │
│    + Dimension enrichment (ProfileTagService)               │
│    + Dedup (chat placeholders vs real; demo vs real)        │
└──────────────┬──────────────────────────────────────────────┘
               │
               ▼
      Prompt History / Analytics tabs
```

The deduplication steps are what makes Agent Chat and Demo rows show up exactly once instead of twice — each Agent Chat and demo invocation is also a real Bedrock call that independently appears in CloudWatch Logs. See `suppress_chat_placeholders_with_real_invocation` and `suppress_real_duplicates_of_demo_invocations` for the matching heuristics.

### Data flow — Agent Chat (write path with Prompt History toggle)

```
User sends message
    │
    ▼
POST /api/agent/chat (or /chat/stream)
    │
    ▼
AgentService.chat(...)
    ├─ build_usage_context()      # scoped per caller role
    ├─ bedrock-runtime:Converse   # with scoped-managed guardrail if any
    │
    ├─ settings_service.get_prompt_history_enabled(db)
    │
    ├── enabled=True:
    │     crypto.encrypt(message)     → "enc:v1:<Fernet-token>"
    │     crypto.encrypt(assistant_text)
    │     INSERT INTO chat_messages …
    │
    └── enabled=False:
          log INFO, return response, nothing persisted
```

The Prompt History & Encryption section earlier in this README is the full walkthrough of this flow including the gatekeeper that refuses to enable the toggle without a usable encryption key.

### Deployment flow

```
Developer / CI
    │
    ▼
build-and-deploy-backend.sh
    ├─ Preflight (Node, AWS CLI, Python, zip, credentials)
    ├─ cdk deploy AppRegistry → Data (no-op after first deploy)
    ├─ Zip backend/ to S3 build bucket
    ├─ Trigger CodeBuild → docker build → push to ECR
    ├─ cdk deploy Backend → updates ECS task definition
    └─ aws ecs update-service --force-new-deployment, poll health

build-and-deploy-frontend.sh
    ├─ Preflight
    ├─ npm run build in frontend/
    ├─ cdk deploy Frontend → S3 upload + CloudFront invalidation
    ├─ Re-deploy Data with frontendUrl=<cloudfront> to register Cognito callback
    └─ Bootstrap or reset the admin Cognito user (temp password)
```

CodeBuild lives outside CDK because a fresh-account deploy needs an image in ECR before the ECS service is created. The deploy script creates/updates the CodeBuild project imperatively and CDK consumes the resulting `:latest` tag. See [Portability notes](#portability-notes) for the one-time changeover gotcha.

### Related configuration surfaces

- **Prompt History toggle (universal body-visibility gate + at-rest encryption):** see the [Prompt History & Encryption](#prompt-history--encryption) section above.
- **Bedrock model-invocation logging:** auto-enabled by `build-and-deploy-backend.sh` Step 3.5 in the deploy region; use `enable-bedrock-logging-all-regions.sh` to extend.
- **CUR 2.0 billed-cost ingestion:** optional and all-or-nothing — set `ATHENA_DATABASE` + `ATHENA_TABLE` + `ATHENA_OUTPUT_S3` to activate; absent config degrades to estimated-only costs.
- **Multi-region ingestion:** controlled by `cloudtrailRegions` CDK context — the deploy region is always included, additional regions are additive.

---

## Security

Controls enforced in code today, grouped by concern. For gaps that are out of scope for the sample (and the compliance obligations they create for an operator), see the AWS Content disclaimer at the top of this README and the Compliance section below.

### Identity and access

- **Cognito-backed authN.** User Pool, App Client, Hosted UI, and `admin` group all provisioned by CDK (`infrastructure/lib/data-stack.ts`). Public PKCE-only SPA client — no client secret ever ships in the JS bundle. `selfSignUpEnabled: false`; admins are bootstrapped by the frontend deploy script with a temp password + forced change on first sign-in.
- **JWT verification.** `backend/app/auth.py` verifies Cognito ID tokens with RS256 against the User Pool's JWKS, enforces `issuer`, `audience`, `exp`, and `token_use == "id"`. No stack traces or library errors leak to the caller — a 401 always returns a short `code + message` payload.
- **Role separation.** `is_admin` (admin mutations: toggle flips, team edits, pricing changes, demo generation) and `can_view_all` (payer / superuser / admin — read-only cross-user visibility) are deliberately separate dimensions, mirrored in both the backend `CurrentUser` dataclass and the frontend `canViewAll()` helper.
- **Local-dev auth bypass.** `AUTH_ENABLED=false` short-circuits every request to a synthetic admin for local development. Documented in `.env.example`. Do not set it in a deployed environment.

### Data at rest

| Data | Store | Encryption |
|---|---|---|
| Agent Chat prompt / response bodies | `chat_messages.content` | Fernet envelope `enc:v1:<token>` using key from Secrets Manager (`PromptHistoryEncryptionKey`) — see [Prompt History & Encryption](#prompt-history--encryption) |
| Demo prompt / response bodies | `demo_activity_records.prompt_text` / `response_text` | Same Fernet envelope as above |
| All other DB content (teams, pricing, findings, settings, policies, etc.) | PostgreSQL on RDS | **RDS storage encryption at rest** via the AWS-managed `aws/rds` KMS key (CDK `storageEncrypted: true`) |
| Frontend assets | S3 (`bedrock-dashboard-<env>-<account>`) | SSE-S3 (`BucketEncryption.S3_MANAGED`), `BlockPublicAccess.BLOCK_ALL`, `enforceSSL: true`, CloudFront-only read via OAI |
| Access logs (ALB + future CloudFront) | S3 (`bedrock-dashboard-access-logs-<env>-<account>`) | SSE-S3, same block + enforceSSL as above, 90-day lifecycle expiration with Infrequent Access transition at 30 days |
| Database credentials | AWS Secrets Manager | AWS-managed `aws/secretsmanager` KMS key, injected into ECS tasks via `ecs.Secret.fromSecretsManager()` — never a plaintext env var |
| Prompt History encryption key | AWS Secrets Manager | Same — never leaves the backend task's memory |

Customer-managed KMS keys (CMK) with rotation policies are the recommended upgrade for workloads with HIPAA / PCI / FedRAMP obligations. Operators are responsible for provisioning and attaching their own CMK via `kmsKey: ...` on the RDS instance and Secrets Manager constructs.

### Data in transit

| Hop | Protocol | Notes |
|---|---|---|
| Browser ↔ CloudFront | **HTTPS** | `REDIRECT_TO_HTTPS` on both the default and `/api/*` behaviors. HSTS header forces client-side enforcement (`max-age=31536000, includeSubdomains, preload`). |
| CloudFront ↔ S3 | **HTTPS** | AWS internal, OAI-authenticated. |
| CloudFront ↔ API Gateway (`/api/*`) | **HTTPS, TLSv1.2** | `OriginProtocolPolicy.HTTPS_ONLY` with `OriginSslPolicy.TLS_V1_2`. The API Gateway `execute-api.<region>.amazonaws.com` endpoint ships with an AWS-managed publicly-trusted cert, so no custom domain / ACM import is needed. |
| API Gateway ↔ ALB (VPC Link v2) | **HTTP, VPC-internal only** | The ALB is `publicLoadBalancer: false` and lives in private subnets. Its SG accepts :80 only from the VPC Link SG. The listener itself is still HTTP because the ALB's AWS-owned hostname can't carry a publicly-trusted cert without a custom domain, but there is no public reach to it — direct `curl http://<alb-dns>/...` from the internet is unroutable. |
| ECS task ↔ RDS | **TLS available** (not currently enforced) | PostgreSQL supports TLS; the backend uses `psycopg2` which negotiates TLS when the server offers it, but RDS does not currently require it. Enforcing it is a `rds.force_ssl=1` parameter-group change. |
| ECS task ↔ Bedrock / Secrets Manager / S3 / CloudWatch / STS | **HTTPS** | All AWS SDK calls over HTTPS. |

### Network isolation

- **Three-tier VPC** — public (ALB only), private-with-egress (ECS tasks via NAT), private-isolated (RDS, no internet route). RDS subnets have no NAT or IGW route, so there is no network path to the internet.
- **Security groups** scoped narrowly: ALB → tasks on 8000, tasks → RDS on 5432, RDS has `allowAllOutbound: false`.
- **VPC Flow Logs** → CloudWatch Logs with env-aware retention (one month non-prod, one year prod). Queryable via CloudWatch Logs Insights.

### Audit and observability

- **ALB access logs** → the shared `AccessLogsBucket` under the `alb/` prefix. 90-day retention with Infrequent Access transition at 30 days.
- **Container logs** → CloudWatch Logs (auto-created log group per task definition).
- **CloudWatch alarms** on three failure modes (ALB 5xx, ALB unhealthy targets, RDS CPU > 80% for 15 min) → SNS topic (`bedrock-dashboard-alerts-<env>`). Email subscription is opt-in via `ALERT_EMAIL` env var at deploy time.
- **API Gateway access logs** → the CDK Backend stack creates a CloudWatch log group `/aws/apigateway/bedrock-dashboard-<env>` with 1-month retention (dev) or `RemovalPolicy.RETAIN` (prod). The stack also provisions the account-level API Gateway CloudWatch logging role via `AWS::ApiGateway::Account` (CDK's `cloudWatchRole: true` default). **Shared-account deploys may need to flip this off** — see [Caveat — deploying to a shared AWS account with other API Gateway apps](#caveat--deploying-to-a-shared-aws-account-with-other-api-gateway-apps) for when to set `SKIP_API_GW_LOGGING_ROLE=1` and what trade-offs that makes.
- **ECS deployment circuit breaker with `rollback: true`** — a crash-looping task definition is detected and rolled back automatically within ~5 minutes instead of lingering as a stuck UPDATE_IN_PROGRESS. Paired with `minHealthyPercent: 100` so the old task stays up until the new one is fully healthy.
- **ECR image inspection on push** — enabled on the ECR repository so vulnerability reports are available in the ECR console for each pushed image.

### Prompt-content protection

The Prompt History toggle and encryption model are documented in detail earlier in this README. The relevant summary from a Security perspective:

- Toggle is a **universal body-visibility gate** across all record sources (Agent Chat, demo, CloudWatch Logs, S3 invocation logs, CloudTrail). Metadata stays visible; bodies get a "content hidden" placeholder when off.
- **Gatekeeper refuses to enable** the toggle with HTTP 400 unless a usable Fernet key is loaded in the backend — this app will not persist or surface prompt bodies while advertising "encryption at rest" without that encryption actually being in effect.
- Fernet envelope (`enc:v1:<token>`) is self-describing so legacy plaintext rows pass through unchanged on read, and rows under a rotated key render a safe placeholder instead of raw ciphertext.

---

## Compliance against AWS Best Practices

The dashboard was reviewed against the [AWS Well-Architected Framework](https://aws.amazon.com/architecture/well-architected/) six pillars. The AWS Content disclaimer at the top of this README governs what any of this means for a workload with regulatory obligations.

### Pillar detail

#### Security pillar

See [Security](#security) above. Mapped to the Well-Architected security design principles:

- **Implement a strong identity foundation** — Cognito PKCE flow, group-based RBAC with separated admin vs read-all dimensions, no shared credentials in code or environment.
- **Apply security at all layers** — VPC isolation, security-group least-privilege, HTTPS at the edge, HSTS + CSP + X-Content-Type-Options + X-Frame-Options + Referrer-Policy via CloudFront response-headers policy, task-level IAM scoped per-action with resource-level ARNs where the API supports it, application-level RBAC + Prompt History body gate.
- **Automate security best practices** — ECR scan-on-push, deploy-script preflight, Alembic migrations auto-run on container start, CloudWatch alarms routed to SNS.
- **Protect data in transit and at rest** — see the two tables in [Security](#data-at-rest). RDS storage encryption, Fernet envelope for prompt bodies, Secrets Manager for credentials and keys, SSE-S3 on every bucket, HTTPS to viewers.
- **Keep people away from data** — admin surfaces require `require_admin`; non-admin users are automatically scoped to their own identity across every analytics tab.
- **Prepare for security events** — VPC Flow Logs, ALB access logs, CloudWatch alarms on 5xx / target health / DB CPU, SNS alert topic ready for subscriptions.

#### Reliability pillar

- **Automatic recovery from failure** — ECS deployment circuit breaker rolls back a crash-looping task definition automatically. Multi-AZ RDS in prod. Three CloudWatch alarms (ALB 5xx, unhealthy targets, RDS CPU).
- **Horizontal scalability (partial)** — ALB-fronted Fargate with configurable `desiredCount` per environment. No auto-scaling policy; adding one is a `scaleOnCpuUtilization` call on the Fargate service.
- **Change management** — three-stack separation (`AppRegistry`, `Data`, `Backend`, `Frontend`) with explicit dependencies; durable data in `Data` survives Backend/Frontend rebuilds. CDK removal policies protect prod resources from accidental teardown.
- **Backup and restore** — RDS backup retention 14 days prod / 7 days non-prod. Deletion protection on prod. Alembic migrations forward-only and idempotent.

#### Performance Efficiency pillar

- **Edge caching** — CloudFront with `CACHING_OPTIMIZED` default, explicit `CACHING_DISABLED` on `/api/*`. Fingerprinted assets are `max-age=365d, immutable`; `index.html` is `no-cache, must-revalidate`.
- **Connection reuse** — Shared service instances (CloudTrailService, CostService, etc.) across requests. SQLAlchemy `pool_pre_ping=True` prevents stale connection errors.
- **Parallelism** — S3 scan for the Overview tab is parallelized. CUR ingestion and AgentCore discovery run as scheduled ECS tasks, not as foreground calls.

#### Cost Optimization pillar

- **Match supply to demand (environment-aware)** — `t3.micro` RDS in dev, caller-configurable in prod. `natGateways: 2` prod, `1` dev. Container Insights on in prod only.
- **Lifecycle management** — ECR keeps the last 10 images, access-logs bucket transitions to Infrequent Access at 30 days and expires at 90, VPC Flow Logs retention is env-aware.
- **Measure efficiency** — CUR 2.0 billed-cost ingestion reconciles AWS billing against the dashboard's own token-based estimates; variance surfaces in the Cost Governance tab.

#### Operational Excellence pillar

- **Operations as code** — Everything is CDK. The deploy scripts are the operator interface; they're idempotent, re-runnable, and include preflight auto-install for Node / AWS CLI / Python / zip / credentials.
- **Observe** — CloudWatch Logs (ECS tasks + VPC Flow Logs), CloudWatch alarms (ALB 5xx, target health, RDS CPU), SNS alert topic, ALB access logs, ECR scan-on-push.
- **Learn from failure** — The [Troubleshooting](#troubleshooting) table documents known failure modes (psycopg2 dialect drift, Fargate task-set digest pinning, Bedrock logging S3 prefix caching, deploy-script zip corruption, etc.) with diagnostic commands and fix steps.

#### Sustainability pillar

- Right-sized compute (Fargate 0.5 vCPU / 1 GB per task).
- ECR lifecycle rules prevent image accumulation.
- CloudFront compression enabled on static assets (disabled on `/api/*` only because SSE streaming responses need to not be buffered).

### What this section does NOT claim

- **Not a HIPAA / PCI DSS / FedRAMP / SOC 2 attestation.** The AWS Content disclaimer at the top of this README is the governing legal position.
- **Not a security certification.** Zero external audit. A `cdk-nag` sweep + manual Well-Architected review is the extent of the review.
- **Not a performance SLA.** No load testing, no latency targets, no RPO/RTO.
- **Not a replacement for an operator's own compliance program.** The secure-by-default posture is a starting point; an operator with a regulated workload still owns the delta.

## Reporting Security Issues

Please do not open public GitHub issues for security vulnerabilities. See [CONTRIBUTING](CONTRIBUTING.md#security-issue-notifications) for the AWS vulnerability-reporting process.

## License

This project is licensed under the MIT No Attribution License (MIT-0). See the [LICENSE](LICENSE) file.
