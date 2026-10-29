#!/bin/bash
# Build frontend and deploy to S3/CloudFront via CDK
# Automatically bumps the patch version on each deploy
# Usage: ./build-and-deploy-frontend.sh [profile] [region] [env]
#
# Profile resolution order:
#   1. First CLI arg (if provided) — used verbatim
#   2. $AWS_ACCOUNT_ID env var  → profile = Admin-<AWS_ACCOUNT_ID>
#   3. Interactive prompt for  → profile = Admin-<ACCOUNT_ID>

set -euo pipefail

# Preflight: fail fast (with actionable install / auto-install-with-prompt)
# for every tool the deploy relies on. Runs before the account prompt so the
# user isn't asked to type an account id only to hit `npx: command not found`
# (or `python3: command not found`) mid-deploy.
# Set PREFLIGHT_AUTO_INSTALL=1 to skip the y/N prompts in non-interactive use.
_PREFLIGHT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=preflight.sh
. "${_PREFLIGHT_DIR}/preflight.sh"

# Non-AWS-credential checks — run before profile resolution.
preflight_check_node
preflight_check_aws_cli
preflight_check_python3
# zip isn't used by the frontend script, but keeping the check for parity
# doesn't hurt — it's <1s and gives users a single deploy-readiness signal.
preflight_check_zip

# Resolve AWS profile from CLI arg, env var, or interactive prompt.
# An empty response at the prompt means "use current environment credentials"
# (AWS_ACCESS_KEY_ID / AWS_SECRET_ACCESS_KEY / AWS_SESSION_TOKEN).
if [ -n "${1:-}" ]; then
  PROFILE="$1"
elif [ -n "${AWS_ACCOUNT_ID:-}" ]; then
  if [[ ! "$AWS_ACCOUNT_ID" =~ ^[0-9]{12}$ ]]; then
    echo "Error: AWS_ACCOUNT_ID must be exactly 12 digits (got: '$AWS_ACCOUNT_ID')" >&2
    exit 1
  fi
  PROFILE="Admin-${AWS_ACCOUNT_ID}"
else
  read -r -p "AWS Account ID (12 digits, or press Enter to use current env credentials): " AWS_ACCOUNT_ID
  if [ -z "$AWS_ACCOUNT_ID" ]; then
    PROFILE=""
  else
    if [[ ! "$AWS_ACCOUNT_ID" =~ ^[0-9]{12}$ ]]; then
      echo "Error: AWS_ACCOUNT_ID must be exactly 12 digits (got: '$AWS_ACCOUNT_ID')" >&2
      exit 1
    fi
    PROFILE="Admin-${AWS_ACCOUNT_ID}"
  fi
fi
REGION="${2:-us-west-2}"
ENV="${3:-dev}"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
FRONTEND_DIR="${SCRIPT_DIR}/../frontend"
PKG="${FRONTEND_DIR}/package.json"
APP_TSX="${FRONTEND_DIR}/src/App.tsx"

# AWS credential check — runs after profile resolution so it can use $PROFILE
# to talk to the right account. Failing fast here (with AWS-native refresh
# commands: aws sso login / aws configure / env-var export) is much friendlier
# than the cryptic ExpiredToken error that would otherwise surface from the
# very next aws call.
preflight_check_aws_credentials

ACCOUNT=$(aws sts get-caller-identity ${PROFILE:+--profile $PROFILE} --query Account --output text)

echo "=== Bedrock Dashboard Frontend Build & Deploy ==="
echo "Profile: ${PROFILE:-<env creds>} | Region: $REGION | Env: $ENV | Account: $ACCOUNT"
echo ""
echo "Note: this deploys only the Frontend stack. The Backend stack must be"
echo "deployed first (run build-and-deploy-backend.sh)."
echo ""

# -----------------------------------------------------------------------------
# Up-front admin identity prompts
# -----------------------------------------------------------------------------
# Collect the Cognito admin username and email NOW — not later during Step 5 —
# so a 25-minute CDK deploy can run unattended. These values flow into
# _bootstrap_cognito via COGNITO_ADMIN_USERNAME / COGNITO_ADMIN_EMAIL.
# Honors env vars if the operator pre-set them (CI, non-interactive runs).
#
# Credential delivery is email-only. Cognito itself generates the one-time
# temporary password and emails it to the admin user from its built-in
# email service (no-reply@verificationemail.com). The password never exists
# on this host — not in stdout, not in a file, not in shell history, not in
# CI logs. The account lands in FORCE_CHANGE_PASSWORD so Cognito forces a
# password change on first sign-in.

# Username prompt (default: admin). Taken from env var if set.
if [ -z "${COGNITO_ADMIN_USERNAME:-}" ]; then
  read -r -p "Cognito admin username [admin]: " _cognito_username_input || true
  export COGNITO_ADMIN_USERNAME="${_cognito_username_input:-admin}"
else
  echo "Cognito admin username:  ${COGNITO_ADMIN_USERNAME} (from env)"
fi

# Email prompt — ALWAYS required. Cognito needs a destination to send the
# one-time password to; there is no non-email delivery path.
_email_regex='^[^@[:space:]]+@[^@[:space:]]+\.[^@[:space:]]+$'
if [ -z "${COGNITO_ADMIN_EMAIL:-}" ]; then
  while :; do
    read -r -p "Cognito admin email (Cognito will send the one-time password here): " _cognito_email_input || true
    if [ -n "$_cognito_email_input" ] && echo "$_cognito_email_input" | grep -Eq "$_email_regex"; then
      export COGNITO_ADMIN_EMAIL="$_cognito_email_input"
      break
    fi
    echo "  Not a valid email address. Try again, or set COGNITO_ADMIN_EMAIL env var and re-run."
  done
else
  if echo "$COGNITO_ADMIN_EMAIL" | grep -Eq "$_email_regex"; then
    echo "Cognito admin email:     ${COGNITO_ADMIN_EMAIL} (from env)"
  else
    echo "⚠️  COGNITO_ADMIN_EMAIL='${COGNITO_ADMIN_EMAIL}' is not a valid email address."
    echo "    Cognito needs a valid destination for the one-time password. Unset the env var and re-run."
    exit 1
  fi
fi
echo ""

# Delete a CloudFormation stack if it is in a state that blocks updates
# (ROLLBACK_COMPLETE from a failed initial create, ROLLBACK_FAILED, or
# CREATE_FAILED). Idempotent: does nothing if the stack does not exist or is
# already in a healthy state.
cleanup_failed_stack() {
  local stack_name="$1"
  local status
  status=$(aws cloudformation describe-stacks \
    --stack-name "$stack_name" \
    ${PROFILE:+--profile $PROFILE} --region "$REGION" \
    --query 'Stacks[0].StackStatus' --output text 2>/dev/null) || return 0

  case "$status" in
    ROLLBACK_COMPLETE|ROLLBACK_FAILED|CREATE_FAILED)
      echo "  Stack '${stack_name}' is in ${status}. Deleting so CDK can recreate it..."
      aws cloudformation delete-stack \
        --stack-name "$stack_name" \
        ${PROFILE:+--profile $PROFILE} --region "$REGION" > /dev/null
      aws cloudformation wait stack-delete-complete \
        --stack-name "$stack_name" \
        ${PROFILE:+--profile $PROFILE} --region "$REGION"
      echo "  Deleted '${stack_name}'."
      ;;
  esac
}

# Ensure the pinned versions of ts-node, typescript, and aws-cdk-lib are
# installed locally. Without this, npx falls back to downloading unpinned
# versions into ~/.npm which can conflict with the versions cdk.json expects.
if [ ! -d "${SCRIPT_DIR}/node_modules" ]; then
  echo "--- Installing CDK dependencies (one-time setup) ---"
  (cd "${SCRIPT_DIR}" && npm install)
  echo ""
fi

# Step 0: CDK bootstrap (idempotent — required for CDK deploys in a fresh
# account/region combo; safe to re-run on already-bootstrapped environments).
echo "--- Step 0: CDK bootstrap ---"
(
  cd "${SCRIPT_DIR}"
  npx --yes cdk bootstrap "aws://${ACCOUNT}/${REGION}" \
    -c env="$ENV" -c region="$REGION" \
    ${PROFILE:+--profile $PROFILE}
)
echo ""

# Step 1: Bump patch version
echo "--- Step 1: Bumping patch version ---"
CURRENT_VERSION=$(python3 -c "import json,sys; print(json.load(open('${PKG}'))['version'])")
IFS='.' read -r MAJOR MINOR PATCH <<< "$CURRENT_VERSION"
NEW_PATCH=$((PATCH + 1))
NEW_VERSION="${MAJOR}.${MINOR}.${NEW_PATCH}"

# Update package.json
python3 - <<PYEOF
import json
with open("${PKG}", "r") as f:
    pkg = json.load(f)
pkg["version"] = "${NEW_VERSION}"
with open("${PKG}", "w") as f:
    json.dump(pkg, f, indent=2)
    f.write("\n")
PYEOF

# Update APP_VERSION constant in App.tsx
sed -i.bak "s/const APP_VERSION = '[0-9]*\.[0-9]*\.[0-9]*'/const APP_VERSION = '${NEW_VERSION}'/" "${APP_TSX}"
rm -f "${APP_TSX}.bak"

echo "Version: ${CURRENT_VERSION} → ${NEW_VERSION}"

# Step 2: Build frontend
echo ""
echo "--- Step 2: Building frontend ---"
# Install frontend dependencies on first run (or if node_modules was cleaned).
# Without this, tsc/vite from devDependencies are missing and `npm run build`
# fails with 'tsc: command not found'. Prefer `npm ci` when a lockfile exists
# so we get a reproducible install matching package-lock.json.
if [ ! -d "${FRONTEND_DIR}/node_modules" ]; then
  echo "  Installing frontend dependencies (one-time setup)..."
  if [ -f "${FRONTEND_DIR}/package-lock.json" ]; then
    (cd "${FRONTEND_DIR}" && npm ci)
  else
    (cd "${FRONTEND_DIR}" && npm install)
  fi
fi
(cd "${FRONTEND_DIR}" && npm run build)
echo "Build complete: ${FRONTEND_DIR}/dist"

# Step 3: Deploy via CDK (uploads to S3 + invalidates CloudFront)
echo ""
echo "--- Step 3: Deploying to S3 + CloudFront via CDK ---"
cleanup_failed_stack "BedrockDashboard-Frontend-${ENV}"
# Three-step cutover support for the ALB privacy flip.
#
# The ALB scheme flip (internet-facing -> internal) in the Backend stack
# replaces the ALB, which used to export its DNS name for the Frontend
# stack to import as the /api/* origin. CloudFormation refuses to update
# an export another stack imports, so a direct back-to-back deploy will
# deadlock. The one-time cutover goes:
#
#   Step 1: SKIP_API_ORIGIN=1 bash build-and-deploy-frontend.sh
#       -> Deploys Frontend with the /api/* behavior removed, releasing
#          the import. API becomes unavailable.
#   Step 2: bash build-and-deploy-backend.sh
#       -> ALB replacement runs (internet-facing -> internal), new API
#          Gateway + VPC Link come up.
#   Step 3: bash build-and-deploy-frontend.sh    (NO SKIP_API_ORIGIN)
#       -> Re-adds /api/* with the new API Gateway as the origin.
#          API becomes available again.
#
# After the one-time cutover, SKIP_API_ORIGIN stays unset for every
# subsequent deploy.
SKIP_API_ORIGIN_FLAG=""
if [ "${SKIP_API_ORIGIN:-0}" = "1" ]; then
  echo "  SKIP_API_ORIGIN=1 — frontend will be deployed WITHOUT /api/* behavior (cutover step 1)."
  SKIP_API_ORIGIN_FLAG="-c skipApiOrigin=true"
fi
(
  cd "${SCRIPT_DIR}"
  # shellcheck disable=SC2086  # SKIP_API_ORIGIN_FLAG is intentionally un-quoted so empty = no flag
  npx --yes cdk deploy "BedrockDashboard-Frontend-${ENV}" \
    --require-approval never \
    -c env="$ENV" -c region="$REGION" \
    $SKIP_API_ORIGIN_FLAG \
    ${PROFILE:+--profile $PROFILE}
)

FRONTEND_URL=$(aws cloudformation describe-stacks \
  --stack-name "BedrockDashboard-Frontend-${ENV}" \
  ${PROFILE:+--profile $PROFILE} --region "$REGION" \
  --query 'Stacks[0].Outputs[?OutputKey==`DistributionUrl`].OutputValue' \
  --output text 2>/dev/null || echo "pending")

# Step 4: Register the CloudFront URL as an allowed callback / logout URL on
# the Cognito SPA client so the hosted UI accepts sign-ins from this frontend.
#
# The Data stack seeds the client with only localhost URLs (the CloudFront URL
# does not exist yet on the first deploy). Rather than re-deploying Data with
# a new context each time, this step patches the client in place — safe and
# idempotent because we merge with the existing list.
echo ""
# Step 4: Register the CloudFront URL with the Cognito app client via CDK.
#
# The Data stack's Cognito SPA client is declared with an optional frontendUrl
# prop that, when set, adds `<url>/callback` to CallbackURLs and `<url>` to
# LogoutURLs. On the FIRST deploy the CloudFront URL doesn't exist yet, so
# the Data stack initially seeds only localhost entries. This step re-deploys
# Data with the now-known CloudFront URL passed as CDK context, letting CDK
# author the CallbackURLs/LogoutURLs update declaratively.
#
# Why this replaced the previous imperative `aws cognito-idp
# update-user-pool-client` call: that CLI action resets every field the
# caller doesn't explicitly replay to its API default (token validity,
# read/write attributes, revocation, etc.). Today CDK's defaults happen to
# match those API defaults, so nothing visibly drifts — but the first time
# someone tightens accessTokenValidity or similar in CDK, the next frontend
# deploy would silently revert it. Having CDK own the client contract
# end-to-end eliminates that class of drift.
#
# Idempotent: subsequent runs with the same CloudFront URL produce no CFN
# diff on the Cognito client and complete in ~30-60s (data stack synth +
# CFN no-op). The very first run after this change lands may show a small
# CFN diff (adding the CloudFront URL to the CallbackURLs/LogoutURLs list).
echo ""
echo "--- Step 4: Registering CloudFront URL with Cognito app client (via CDK) ---"
if [ "$FRONTEND_URL" = "pending" ] || [ -z "$FRONTEND_URL" ]; then
  echo "  ⚠️  Frontend URL not available yet — skipping Cognito client update."
  echo "     Re-run this script once the Frontend stack has published its"
  echo "     DistributionUrl output, or set FRONTEND_URL manually and re-run."
else
  echo "  Re-deploying Data stack with frontendUrl=${FRONTEND_URL} so CDK sets the"
  echo "  Cognito app client's CallbackURLs/LogoutURLs declaratively."
  (
    cd "${SCRIPT_DIR}"
    npx --yes cdk deploy "BedrockDashboard-Data-${ENV}" \
      --require-approval never \
      -c env="$ENV" -c region="$REGION" \
      -c frontendUrl="$FRONTEND_URL" \
      ${PROFILE:+--profile $PROFILE}
  )
  echo "  ✅ Cognito app client CallbackURLs/LogoutURLs updated via CDK."
fi

# Step 5: Bootstrap the Cognito admin user. Cognito generates the one-time
# temporary password itself and emails it directly to the admin's inbox from
# its built-in email service. The account lands in FORCE_CHANGE_PASSWORD so
# Cognito forces a password change on first sign-in.
#
# Existing-user handling:
#   - FORCE_CHANGE_PASSWORD → admin-create-user --message-action RESEND
#     rotates the temp password and resends the welcome email.
#   - CONFIRMED / RESET_REQUIRED → admin-reset-user-password sends a password
#     reset verification code; the hosted UI's "Forgot password?" flow uses
#     the code to let the user set a new permanent password.
#
# The password NEVER touches this script, stdout, local disk, or deploy
# logs. There is no opt-out path.
#
# Runs *after* Step 4 registers the CloudFront callback URL with the Cognito
# app client, so the Login URL printed at the end of this script is
# guaranteed to work end-to-end.
echo ""

# Filled in by _bootstrap_cognito on success; consumed by the final summary.
# Declared unconditionally (even when Step 5 is skipped below) since the
# summary block always reads them under `set -u`.
COGNITO_ADMIN_USERNAME_FINAL=""
COGNITO_ADMIN_EMAIL_FINAL=""
COGNITO_POOL_ID_FINAL=""
# Which Cognito API got called — drives the inbox-expectation hint in the
# final summary. One of: "" | "welcome-email" | "welcome-email-resent" | "reset-code"
COGNITO_EMAIL_ACTION=""

if [ "${SKIP_COGNITO_BOOTSTRAP:-}" = "1" ]; then
  echo "--- Step 5: Bootstrapping Cognito admin user (SKIPPED — SKIP_COGNITO_BOOTSTRAP=1) ---"
  echo "  Set SKIP_COGNITO_BOOTSTRAP=1 whenever the target user pool is shared with"
  echo "  other applications/real accounts — this step can create or RESET a"
  echo "  password for a user named 'admin' (or \$COGNITO_ADMIN_USERNAME) with no"
  echo "  further confirmation, which is unsafe on a pool you don't own outright."
else
echo "--- Step 5: Bootstrapping Cognito admin user ---"

_bootstrap_cognito() {
  # Resolve pool + client from Data stack outputs. These are the same values
  # the backend was configured with, so a bootstrapped user works everywhere.
  local cognito_pool
  cognito_pool=$(aws cloudformation describe-stacks \
    --stack-name "BedrockDashboard-Data-${ENV}" \
    ${PROFILE:+--profile $PROFILE} --region "$REGION" \
    --query 'Stacks[0].Outputs[?OutputKey==`UserPoolId`].OutputValue' --output text 2>/dev/null) || return 1
  if [ -z "$cognito_pool" ] || [ "$cognito_pool" = "None" ]; then
    echo "  ⚠️  Could not resolve Cognito pool from Data stack outputs. Skipping."
    return 1
  fi
  echo "  Cognito pool: $cognito_pool"

  # Guard against a pool that lives elsewhere / can't be reached.
  if ! aws cognito-idp describe-user-pool \
      --user-pool-id "$cognito_pool" \
      ${PROFILE:+--profile $PROFILE} --region "$REGION" >/dev/null 2>&1; then
    echo "  ⚠️  Pool '$cognito_pool' is not reachable with the current credentials. Skipping."
    return 1
  fi

  # Username + email were resolved up front (top of script) before any slow
  # work ran. If somehow they're empty here, bail — we don't want to prompt
  # at the end of a 25-minute deploy.
  local username="${COGNITO_ADMIN_USERNAME:-admin}"
  local email="${COGNITO_ADMIN_EMAIL:-}"

  if [ -z "$email" ]; then
    echo "  ⚠️  Admin email is required (Cognito needs a destination for the one-time password). Skipping."
    return 1
  fi

  # Probe for existence. Capture stderr so a real failure (permissions,
  # throttling, wrong pool id, alias vs raw-username lookup quirk) surfaces
  # instead of being silently treated as "user does not exist".
  local user_exists=0
  local get_user_err=""
  if aws cognito-idp admin-get-user \
      --user-pool-id "$cognito_pool" \
      --username "$username" \
      ${PROFILE:+--profile $PROFILE} --region "$REGION" >/dev/null 2>/dev/null; then
    user_exists=1
  else
    get_user_err=$(aws cognito-idp admin-get-user \
      --user-pool-id "$cognito_pool" \
      --username "$username" \
      ${PROFILE:+--profile $PROFILE} --region "$REGION" 2>&1 >/dev/null || true)
    if [ -n "$get_user_err" ] && ! echo "$get_user_err" | grep -qi "UserNotFoundException"; then
      echo "  ℹ️  admin-get-user probe returned an unexpected error; will still attempt create and fall back if the user turns out to exist."
      echo "     Details: $get_user_err"
    fi
  fi

  # Resolve the current UserStatus — matters for cognito-email mode because
  # Cognito's RESEND flow only works for FORCE_CHANGE_PASSWORD users. For
  # CONFIRMED / RESET_REQUIRED users we trigger an admin-reset-user-password
  # instead, which sends a verification code and puts the user back into
  # RESET_REQUIRED so Cognito forces a password change on next sign-in.
  local user_status=""
  if [ "$user_exists" = "1" ]; then
    user_status=$(aws cognito-idp admin-get-user \
      --user-pool-id "$cognito_pool" \
      --username "$username" \
      ${PROFILE:+--profile $PROFILE} --region "$REGION" \
      --query 'UserStatus' --output text 2>/dev/null || echo "")
  fi

  # Resolves the actual username that owns a given verified email alias.
  # The Data stack sets signInAliases.email=true, so any user with
  # email_verified=true claims that email as an alias — and creating a second
  # user with the same email fails with UsernameExistsException even though
  # the target username itself does not exist yet.
  _find_username_by_email() {
    local target_email="$1"
    aws cognito-idp list-users \
      --user-pool-id "$cognito_pool" \
      --filter "email = \"${target_email}\"" \
      ${PROFILE:+--profile $PROFILE} --region "$REGION" \
      --query 'Users[0].Username' --output text 2>/dev/null | grep -v '^None$' || true
  }

  # Email-only delivery: Cognito generates + emails the one-time password
  # directly to the admin's inbox. Account lands in FORCE_CHANGE_PASSWORD so
  # first sign-in forces a password change. No code path on this host ever
  # sees the password.
  if [ "$user_exists" = "0" ]; then
    echo "  Creating user '$username' — Cognito will generate the one-time password and email it to ${email}..."
    local create_err
    if create_err=$(aws cognito-idp admin-create-user \
        --user-pool-id "$cognito_pool" \
        --username "$username" \
        --user-attributes Name=email,Value="$email" Name=email_verified,Value=true \
        ${PROFILE:+--profile $PROFILE} --region "$REGION" 2>&1 >/dev/null); then
      COGNITO_EMAIL_ACTION="welcome-email"
    elif echo "$create_err" | grep -qi "UsernameExistsException"; then
      # Email alias conflict: the email is already a verified alias on a different
      # username. Fall back to that account and re-run the email flow against it.
      local existing_username
      existing_username=$(_find_username_by_email "$email")
      if [ -n "$existing_username" ] && [ "$existing_username" != "$username" ]; then
        echo "  Email '${email}' is already the verified alias of user '${existing_username}'."
        echo "  Switching to that account; sending the one-time password email to '${existing_username}' instead."
        username="$existing_username"
        user_status=$(aws cognito-idp admin-get-user \
          --user-pool-id "$cognito_pool" --username "$username" \
          ${PROFILE:+--profile $PROFILE} --region "$REGION" \
          --query 'UserStatus' --output text 2>/dev/null || echo "")
        user_exists=1
        # Fall through to the user_exists=1 branch below.
      else
        echo "  ⚠️  admin-create-user reports UsernameExistsException but no user matches the username or email. Manual investigation required."
        return 1
      fi
    else
      echo "  ⚠️  admin-create-user failed: $create_err"
      return 1
    fi
  fi

  if [ "$user_exists" = "1" ]; then
    case "$user_status" in
      FORCE_CHANGE_PASSWORD)
        echo "  User '${username}' exists (status: ${user_status}). Resending the welcome email with a fresh one-time password..."
        aws cognito-idp admin-create-user \
          --user-pool-id "$cognito_pool" \
          --username "$username" \
          --message-action RESEND \
          ${PROFILE:+--profile $PROFILE} --region "$REGION" >/dev/null || return 1
        COGNITO_EMAIL_ACTION="welcome-email-resent"
        ;;
      CONFIRMED|RESET_REQUIRED|"")
        # Confirmed users already picked their own password; the hosted UI's
        # RESEND flow won't apply. Send a password-reset verification code
        # instead — Cognito emails it, user enters it in the hosted UI's
        # "Forgot your password?" screen, picks a new permanent password.
        # Still one-time (code expires after use/time) and still forces a
        # password change on next sign-in attempt.
        echo "  User '${username}' exists (status: ${user_status:-UNKNOWN}). Sending password-reset verification code to ${email}..."
        aws cognito-idp admin-reset-user-password \
          --user-pool-id "$cognito_pool" \
          --username "$username" \
          ${PROFILE:+--profile $PROFILE} --region "$REGION" >/dev/null || return 1
        COGNITO_EMAIL_ACTION="reset-code"
        ;;
      *)
        echo "  ⚠️  Unexpected UserStatus='${user_status}' for '${username}'. Not sending email; manual action required."
        return 1
        ;;
    esac
  fi

  # Ensure an 'admin' group exists and the user is in it. backend/app/auth.py
  # maps any Cognito group name containing "admin" (case-insensitive) to the
  # admin role, so this is what unlocks the admin dashboard on first sign-in.
  # The Data stack already creates the group, but re-run this defensively in
  # case someone deleted it out-of-band.
  aws cognito-idp create-group \
    --user-pool-id "$cognito_pool" \
    --group-name admin \
    --description "Bedrock Dashboard administrators" \
    ${PROFILE:+--profile $PROFILE} --region "$REGION" >/dev/null 2>&1 || true
  aws cognito-idp admin-add-user-to-group \
    --user-pool-id "$cognito_pool" \
    --username "$username" \
    --group-name admin \
    ${PROFILE:+--profile $PROFILE} --region "$REGION" >/dev/null 2>&1 || true

  COGNITO_ADMIN_USERNAME_FINAL="$username"
  COGNITO_ADMIN_EMAIL_FINAL="$email"
  COGNITO_POOL_ID_FINAL="$cognito_pool"
  return 0
}

if ! _bootstrap_cognito; then
  echo "  (Cognito user bootstrap was skipped; frontend deploy itself succeeded.)"
fi
fi

# Final summary — boxed for readability. The temporary Cognito password is
# NEVER printed here (or anywhere on this host); it is delivered only via
# Cognito's email to the admin's inbox.
echo ""
cat <<EOF
╔══════════════════════════════════════════════════════════════════════════╗
║  ✅ Frontend deployment complete                                          ║
╠══════════════════════════════════════════════════════════════════════════╣
║  Frontend:            ${FRONTEND_URL}
║  Version:             ${NEW_VERSION}
║  Login URL:           ${FRONTEND_URL}
EOF
if [ -n "$COGNITO_POOL_ID_FINAL" ]; then
  # Email-only credential delivery: Cognito generated the one-time password
  # and sent it directly to the admin's inbox. Nothing on this host ever
  # saw it. The user is in FORCE_CHANGE_PASSWORD (or RESET_REQUIRED for an
  # existing confirmed user), so Cognito forces a password change on first
  # sign-in.
  _inbox_hint="a Cognito welcome email with your one-time password"
  _signin_hint="Sign in once with that password — Cognito will immediately require you to set a new one."
  case "$COGNITO_EMAIL_ACTION" in
    welcome-email)         _inbox_hint="a Cognito welcome email with your one-time password" ;;
    welcome-email-resent)  _inbox_hint="a Cognito welcome email with a FRESH one-time password (any prior temp password is now invalid)" ;;
    reset-code)
      _inbox_hint="a Cognito password-reset verification code"
      _signin_hint="On the sign-in page, click \"Forgot password?\", enter the code from the email, and set your new password."
      ;;
  esac
  cat <<EOF
╠══════════════════════════════════════════════════════════════════════════╣
║  Cognito admin credentials (one-time; forced change on first sign-in)    ║
║  User pool:           ${COGNITO_POOL_ID_FINAL}
║  Username:            ${COGNITO_ADMIN_USERNAME_FINAL}
║  Email:               ${COGNITO_ADMIN_EMAIL_FINAL}
║  Temporary password:  emailed to ${COGNITO_ADMIN_EMAIL_FINAL}
╚══════════════════════════════════════════════════════════════════════════╝

Check the inbox at ${COGNITO_ADMIN_EMAIL_FINAL} for ${_inbox_hint}
(from no-reply@verificationemail.com — look in Spam / Promotions too).

${_signin_hint}

Login URL: ${FRONTEND_URL}

Cognito's default welcome-email temporary password expires in 7 days. If the
message never arrives, or has expired, re-run this script to resend; the
account stays in FORCE_CHANGE_PASSWORD so Cognito will still force a change
on first sign-in.
EOF
else
  echo "╚══════════════════════════════════════════════════════════════════════════╝"
fi
