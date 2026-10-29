#!/bin/bash
# Build backend Docker image via CodeBuild and deploy to ECS Fargate
# No local Docker required — everything runs in AWS
# Usage: ./build-and-deploy-backend.sh [profile] [region] [env]
#
# Profile resolution order:
#   1. First CLI arg (if provided) — used verbatim
#   2. $AWS_ACCOUNT_ID env var  → profile = Admin-<AWS_ACCOUNT_ID>
#   3. Interactive prompt for  → profile = Admin-<ACCOUNT_ID>

set -euo pipefail

# Preflight: fail fast (with actionable install / auto-install-with-prompt)
# for every tool the deploy relies on. Runs before the account prompt so the
# user isn't asked to type an account id only to hit `npx: command not found`
# (or `python3: command not found`, or `zip: command not found`) mid-Step 1.
# Set PREFLIGHT_AUTO_INSTALL=1 to skip the y/N prompts in non-interactive use.
_PREFLIGHT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=preflight.sh
. "${_PREFLIGHT_DIR}/preflight.sh"

# Non-AWS-credential checks — run before profile resolution.
preflight_check_node
preflight_check_aws_cli
preflight_check_python3
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

# AWS credential check — runs after profile resolution so it can use $PROFILE
# to talk to the right account. Failing fast here (with AWS-native refresh
# commands: aws sso login / aws configure / env-var export) is much friendlier
# than the cryptic ExpiredToken error that would otherwise surface from the
# very next aws call.
preflight_check_aws_credentials

ACCOUNT=$(aws sts get-caller-identity ${PROFILE:+--profile $PROFILE} --query Account --output text)
SCRIPT_DIR="$(dirname "$0")"
ECR_REPO="bedrock-dashboard-backend-${ENV}"
ECR_URI="${ACCOUNT}.dkr.ecr.${REGION}.amazonaws.com/${ECR_REPO}"
CB_PROJECT="bedrock-dashboard-backend-build-${ENV}"
BUILD_BUCKET="bedrock-dashboard-build-${ENV}-${ACCOUNT}"

# ---------------------------------------------------------------------------
# Guard-rail: refuse to create a new Cognito User Pool on an account that
# already has one, unless the operator explicitly opts in.
#
# The CDK Data stack creates a fresh Cognito User Pool by default. On an
# account that already has a Cognito pool with real users (federated
# identity providers, custom password policies, existing user database),
# a naive re-deploy would create a SECOND pool and repoint the app at it —
# locking out every existing user because Cognito cannot migrate users
# between pools.
#
# The safe path on such accounts is "bring your own": set the three
# EXISTING_USER_POOL_* env vars (see infrastructure/lib/data-stack.ts
# DataStackProps for the full contract). This guard-rail catches the case
# where none of them are set AND the account already has one or more
# unrelated Cognito pools that a fresh deploy might inadvertently shadow.
#
# Bypass with FORCE_NEW_USER_POOL=1 when you're certain the existing pools
# are unrelated (e.g. throwaway pools from other projects on the account,
# no real users) or when running against a genuinely fresh account where
# the list-user-pools call returned false positives due to eventual
# consistency.
# ---------------------------------------------------------------------------
if [ -z "${EXISTING_USER_POOL_ID:-}" ] && [ "${FORCE_NEW_USER_POOL:-}" != "1" ]; then
  # If the Data stack already exists, this is a normal re-deploy — no
  # guard-rail action needed (whatever pool ownership was chosen the first
  # time will re-apply).
  DATA_STACK_STATUS=$(aws cloudformation describe-stacks \
    --stack-name "BedrockDashboard-Data-${ENV}" \
    ${PROFILE:+--profile $PROFILE} --region "$REGION" \
    --query 'Stacks[0].StackStatus' --output text 2>/dev/null) || DATA_STACK_STATUS=""

  if [ -z "$DATA_STACK_STATUS" ]; then
    # First-time deploy on this account/region combo. Enumerate any
    # existing Cognito pools that a fresh CDK deploy would potentially
    # shadow. list-user-pools requires the max-results parameter.
    EXISTING_POOLS=$(aws cognito-idp list-user-pools \
      --max-results 60 \
      ${PROFILE:+--profile $PROFILE} --region "$REGION" \
      --query 'UserPools[].{Id:Id,Name:Name}' --output text 2>/dev/null) || EXISTING_POOLS=""

    if [ -n "$EXISTING_POOLS" ]; then
      echo ""
      echo "=========================================================================="
      echo " GUARD-RAIL: Refusing to create a new Cognito User Pool"
      echo "=========================================================================="
      echo ""
      echo "Target account (${ACCOUNT}) region (${REGION}) already has one or more"
      echo "Cognito User Pools:"
      # Print each pool as "  - <id>  <name>" — list-user-pools --output text"
      # returns tab-separated Id and Name columns, one pool per line.
      echo "$EXISTING_POOLS" | awk -F'\t' '{ printf "  - %s  (%s)\n", $1, $2 }'
      echo ""
      echo "A fresh deploy would create ANOTHER User Pool. If any of the pools"
      echo "listed above holds real users, this would lock them out — Cognito"
      echo "cannot migrate users between pools."
      echo ""
      echo "Two safe options:"
      echo ""
      echo "  1. Import an existing pool ('bring your own') — recommended:"
      echo "       export EXISTING_USER_POOL_ID=<pool-id>"
      echo "       export EXISTING_USER_POOL_CLIENT_ID=<client-id>"
      echo "       export EXISTING_USER_POOL_DOMAIN_URL=https://<hosted-ui-url>"
      echo "       bash infrastructure/build-and-deploy-backend.sh"
      echo ""
      echo "  2. Force creation of a new pool (only if the existing pools are"
      echo "     unrelated / have no users you care about):"
      echo "       FORCE_NEW_USER_POOL=1 bash infrastructure/build-and-deploy-backend.sh"
      echo ""
      echo "=========================================================================="
      exit 1
    fi
  fi
fi
# ECS cluster and service names are discovered dynamically after the Backend
# CDK stack is deployed (Step 8), because CDK appends account-specific
# suffixes to resource names.
ECS_CLUSTER=""
ECS_SERVICE=""

echo "=== Bedrock Dashboard Backend Build & Deploy ==="
echo "Account: $ACCOUNT | Region: $REGION | Env: $ENV"
echo "ECR: $ECR_URI"
echo ""
echo "This script is safe to re-run — CDK bootstrap and stack deploys are idempotent."
echo "On a fresh account/region, the Data stack deploy (Step 3) takes ~15 min to"
echo "provision RDS. Subsequent runs skip resources that already exist."
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

# Step 0: Bump frontend patch version to track the deploy
echo "--- Step 0: Bumping frontend patch version ---"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PKG="${SCRIPT_DIR}/../frontend/package.json"
APP_TSX="${SCRIPT_DIR}/../frontend/src/App.tsx"
CURRENT_VERSION=$(python3 -c "import json,sys; print(json.load(open('${PKG}'))['version'])")
IFS='.' read -r MAJOR MINOR PATCH <<< "$CURRENT_VERSION"
NEW_PATCH=$((PATCH + 1))
NEW_VERSION="${MAJOR}.${MINOR}.${NEW_PATCH}"
python3 - <<PYEOF
import json
with open("${PKG}", "r") as f:
    pkg = json.load(f)
pkg["version"] = "${NEW_VERSION}"
with open("${PKG}", "w") as f:
    json.dump(pkg, f, indent=2)
    f.write("\n")
PYEOF
sed -i.bak "s/const APP_VERSION = '[0-9]*\.[0-9]*\.[0-9]*'/const APP_VERSION = '${NEW_VERSION}'/" "${APP_TSX}"
rm -f "${APP_TSX}.bak"
echo "Version: ${CURRENT_VERSION} → ${NEW_VERSION}"
echo ""

# Ensure the pinned versions of ts-node, typescript, and aws-cdk-lib are
# installed locally. Without this, npx falls back to downloading unpinned
# versions into ~/.npm which can conflict with the versions cdk.json expects.
if [ ! -d "${SCRIPT_DIR}/node_modules" ]; then
  echo "--- Installing CDK dependencies (one-time setup) ---"
  (cd "$SCRIPT_DIR" && npm install)
  echo ""
fi

# CDK synthesizes ALL stacks when running any cdk command (bootstrap, deploy,
# etc.), so the FrontendStack's reference to frontend/dist is evaluated even
# when we are only deploying the Data/Backend stacks. On a fresh checkout the
# directory does not exist yet. Create an empty placeholder so synth succeeds
# — the real content is populated later by build-and-deploy-frontend.sh.
FRONTEND_DIST="${SCRIPT_DIR}/../frontend/dist"
if [ ! -d "$FRONTEND_DIST" ]; then
  echo "Creating placeholder ${FRONTEND_DIST} for CDK synth..."
  mkdir -p "${FRONTEND_DIST}/assets"
  echo "" > "${FRONTEND_DIST}/.placeholder"
fi

# Step 1: CDK bootstrap (idempotent — required for CDK deploys in a fresh
# account/region combo; safe to re-run on already-bootstrapped environments).
echo "--- Step 1: CDK bootstrap ---"
(
  cd "$SCRIPT_DIR"
  npx --yes cdk bootstrap "aws://${ACCOUNT}/${REGION}" \
    -c env="$ENV" -c region="$REGION" \
    ${PROFILE:+--profile $PROFILE}
)
echo ""

# Step 2: Deploy AppRegistry governance stack (fast — only creates the
# AppRegistry Application; must exist before Data/Backend stacks reference it).
echo "--- Step 2: Deploying AppRegistry stack ---"
cleanup_failed_stack "BedrockDashboard-AppRegistry-${ENV}"
(
  cd "$SCRIPT_DIR"
  npx --yes cdk deploy "BedrockDashboard-AppRegistry-${ENV}" \
    --require-approval never \
    -c env="$ENV" -c region="$REGION" \
    ${PROFILE:+--profile $PROFILE}
)
echo ""

# Step 3: Deploy Data stack (VPC, RDS, ECR, Secrets Manager). On a fresh
# account this takes ~15 minutes to provision RDS. Creates the ECR repo that
# subsequent CodeBuild steps push into.
echo "--- Step 3: Deploying Data stack (VPC, RDS, ECR) ---"
echo "Note: first-time deploy takes ~15 min for RDS. Re-runs are much faster."
cleanup_failed_stack "BedrockDashboard-Data-${ENV}"
(
  cd "$SCRIPT_DIR"
  npx --yes cdk deploy "BedrockDashboard-Data-${ENV}" \
    --require-approval never \
    -c env="$ENV" -c region="$REGION" \
    ${PROFILE:+--profile $PROFILE}
)
echo ""

# Step 3.5: Enable Bedrock model invocation logging in the deploy region.
#
# Without this, Bedrock never writes invocation logs to CloudWatch/S3 and the
# dashboard shows all zeros no matter how many prompts are sent. The
# standalone infrastructure/enable-bedrock-logging-all-regions.sh script
# handles this globally; here we do the deploy-region-only variant so a fresh
# deploy is truly zero-touch.
#
# Idempotent — if logging is already configured (possibly pointing at a
# different bucket/role), we skip and leave existing config untouched.
echo "--- Step 3.5: Enabling Bedrock model invocation logging in ${REGION} ---"

BEDROCK_LOG_BUCKET="bedrock-invocation-logs-${ACCOUNT}"
BEDROCK_LOG_GROUP="/aws/bedrock/model-invocations"
BEDROCK_LOG_ROLE="BedrockInvocationLoggingRole"

# Shared inline-policy definition for the BedrockInvocationLoggingRole.
# Kept in a variable (rather than inlined twice) so rolling least-privilege
# improvements apply consistently across both paths below.
#
# Scoping strategy:
#   - LogGroupWrite: CreateLogGroup/CreateLogStream/PutLogEvents/DescribeLogStreams
#     pinned to the /aws/bedrock/model-invocations log group in any region
#     (the role is account-scoped but used per-region).
#   - LogsServiceList: DescribeLogGroups + DescribeResourcePolicies are list
#     APIs that cannot be scoped to a resource, so they remain on "*".
#   - LogDeliveryBootstrap: CreateLogDelivery + PutResourcePolicy are one-time
#     setup actions used by Bedrock to attach itself as a delivery source;
#     neither IAM action is scopeable to a specific log group. The role's
#     trust policy restricts it to bedrock.amazonaws.com, so no other
#     principal can exploit these grants.
BEDROCK_LOG_POLICY='{
  "Version":"2012-10-17",
  "Statement":[
    {
      "Sid":"LogGroupWrite",
      "Effect":"Allow",
      "Action":[
        "logs:CreateLogGroup",
        "logs:CreateLogStream",
        "logs:PutLogEvents",
        "logs:DescribeLogStreams"
      ],
      "Resource":[
        "arn:aws:logs:*:'"${ACCOUNT}"':log-group:'"${BEDROCK_LOG_GROUP}"'",
        "arn:aws:logs:*:'"${ACCOUNT}"':log-group:'"${BEDROCK_LOG_GROUP}"':*"
      ]
    },
    {
      "Sid":"LogsServiceList",
      "Effect":"Allow",
      "Action":[
        "logs:DescribeLogGroups",
        "logs:DescribeResourcePolicies"
      ],
      "Resource":"*"
    },
    {
      "Sid":"LogDeliveryBootstrap",
      "Effect":"Allow",
      "Action":[
        "logs:CreateLogDelivery",
        "logs:PutResourcePolicy"
      ],
      "Resource":"*"
    },
    {
      "Sid":"S3Write",
      "Effect":"Allow",
      "Action":["s3:PutObject"],
      "Resource":"arn:aws:s3:::'"${BEDROCK_LOG_BUCKET}"'/*"
    }
  ]
}'

# If the invocation-logging role already exists (i.e., this script has run
# on this account before), unconditionally refresh its inline policy. The
# put-role-policy call is fully idempotent, so this is safe. Without this
# block, accounts that already have Bedrock logging configured would
# short-circuit past the else-branch below and never receive rolling
# policy improvements (which is what happened on the dev account when L8
# initially shipped — the policy still had s3:PutObject on "*").
if aws iam get-role --role-name "$BEDROCK_LOG_ROLE" \
    ${PROFILE:+--profile $PROFILE} > /dev/null 2>&1; then
  aws iam put-role-policy --role-name "$BEDROCK_LOG_ROLE" \
    --policy-name "BedrockLoggingPolicy" \
    --policy-document "$BEDROCK_LOG_POLICY" \
    ${PROFILE:+--profile $PROFILE} > /dev/null
  echo "  Refreshed ${BEDROCK_LOG_ROLE} inline policy (idempotent)."
fi

EXISTING_LOGGING=$(aws bedrock get-model-invocation-logging-configuration \
  ${PROFILE:+--profile $PROFILE} --region "$REGION" \
  --query 'loggingConfig' --output json 2>/dev/null || echo "null")

if [ -n "$EXISTING_LOGGING" ] && [ "$EXISTING_LOGGING" != "null" ]; then
  echo "  Bedrock logging already configured in ${REGION} — leaving put-model-invocation-logging-configuration untouched."
else
  # Create S3 bucket for invocation logs if missing.
  if ! aws s3api head-bucket --bucket "$BEDROCK_LOG_BUCKET" \
      ${PROFILE:+--profile $PROFILE} 2>/dev/null; then
    echo "  Creating S3 bucket s3://${BEDROCK_LOG_BUCKET}..."
    if [ "$REGION" = "us-east-1" ]; then
      aws s3api create-bucket --bucket "$BEDROCK_LOG_BUCKET" \
        ${PROFILE:+--profile $PROFILE} --region "$REGION" > /dev/null
    else
      aws s3api create-bucket --bucket "$BEDROCK_LOG_BUCKET" \
        --create-bucket-configuration LocationConstraint="$REGION" \
        ${PROFILE:+--profile $PROFILE} --region "$REGION" > /dev/null
    fi
    aws s3api put-public-access-block --bucket "$BEDROCK_LOG_BUCKET" \
      --public-access-block-configuration \
        BlockPublicAcls=true,IgnorePublicAcls=true,BlockPublicPolicy=true,RestrictPublicBuckets=true \
      ${PROFILE:+--profile $PROFILE} > /dev/null
    aws s3api put-bucket-encryption --bucket "$BEDROCK_LOG_BUCKET" \
      --server-side-encryption-configuration \
      '{"Rules":[{"ApplyServerSideEncryptionByDefault":{"SSEAlgorithm":"AES256"}}]}' \
      ${PROFILE:+--profile $PROFILE} > /dev/null
  fi

  # Bucket policy — allow Bedrock service to PutObject, scoped to this account.
  # Applied every run to stay current if the bucket already existed.
  aws s3api put-bucket-policy --bucket "$BEDROCK_LOG_BUCKET" \
    --policy "{
      \"Version\": \"2012-10-17\",
      \"Statement\": [{
        \"Sid\": \"AllowBedrockPutObject\",
        \"Effect\": \"Allow\",
        \"Principal\": {\"Service\": \"bedrock.amazonaws.com\"},
        \"Action\": \"s3:PutObject\",
        \"Resource\": \"arn:aws:s3:::${BEDROCK_LOG_BUCKET}/*\",
        \"Condition\": {
          \"StringEquals\": {\"aws:SourceAccount\": \"${ACCOUNT}\"}
        }
      }]
    }" \
    ${PROFILE:+--profile $PROFILE} > /dev/null

  # Create IAM role for Bedrock to write logs (idempotent).
  ROLE_CREATED=0
  if ! aws iam get-role --role-name "$BEDROCK_LOG_ROLE" \
      ${PROFILE:+--profile $PROFILE} > /dev/null 2>&1; then
    echo "  Creating IAM role ${BEDROCK_LOG_ROLE}..."
    aws iam create-role --role-name "$BEDROCK_LOG_ROLE" \
      --assume-role-policy-document '{
        "Version":"2012-10-17",
        "Statement":[{"Effect":"Allow","Principal":{"Service":"bedrock.amazonaws.com"},"Action":"sts:AssumeRole"}]
      }' \
      ${PROFILE:+--profile $PROFILE} > /dev/null
    ROLE_CREATED=1
  fi

  # Always (re-)apply the inline policy so a pre-existing role gets the
  # complete permission set. logs:CreateLogStream is the one Bedrock's
  # cross-service validation requires — without it the put-config call
  # fails with "Failed to validate permissions for log group". Uses the
  # shared $BEDROCK_LOG_POLICY defined above (same policy is also refreshed
  # unconditionally at the top of Step 3.5 for the already-configured path).
  aws iam put-role-policy --role-name "$BEDROCK_LOG_ROLE" \
    --policy-name "BedrockLoggingPolicy" \
    --policy-document "$BEDROCK_LOG_POLICY" \
    ${PROFILE:+--profile $PROFILE} > /dev/null

  if [ "$ROLE_CREATED" = "1" ]; then
    echo "  Waiting 30s for IAM role propagation..."
    sleep 30
  else
    echo "  Refreshed IAM role policy. Waiting 15s for propagation..."
    sleep 15
  fi
  BEDROCK_LOG_ROLE_ARN=$(aws iam get-role --role-name "$BEDROCK_LOG_ROLE" \
    ${PROFILE:+--profile $PROFILE} --query 'Role.Arn' --output text)

  # CloudWatch log group (idempotent).
  aws logs create-log-group --log-group-name "$BEDROCK_LOG_GROUP" \
    ${PROFILE:+--profile $PROFILE} --region "$REGION" 2>/dev/null || true

  echo "  Configuring model invocation logging in ${REGION}..."
  # Capture stderr so failures surface a real message instead of silently
  # skipping — the standalone script previously masked all errors as
  # "Bedrock not available".
  if ! ERR_OUT=$(aws bedrock put-model-invocation-logging-configuration \
      --logging-config "{
        \"cloudWatchConfig\": {
          \"logGroupName\": \"${BEDROCK_LOG_GROUP}\",
          \"roleArn\": \"${BEDROCK_LOG_ROLE_ARN}\",
          \"largeDataDeliveryS3Config\": {
            \"bucketName\": \"${BEDROCK_LOG_BUCKET}\",
            \"keyPrefix\": \"bedrock-logs/${REGION}\"
          }
        },
        \"s3Config\": {
          \"bucketName\": \"${BEDROCK_LOG_BUCKET}\",
          \"keyPrefix\": \"bedrock-logs/${REGION}\"
        },
        \"textDataDeliveryEnabled\": true,
        \"imageDataDeliveryEnabled\": false,
        \"embeddingDataDeliveryEnabled\": false
      }" \
      ${PROFILE:+--profile $PROFILE} --region "$REGION" 2>&1); then
    echo "  ⚠️  Failed to enable Bedrock logging: $ERR_OUT"
    echo "  Deploy will continue, but no invocations will be captured until this is resolved."
  else
    echo "  ✅ Model invocation logging enabled."
    echo "     Log group: ${BEDROCK_LOG_GROUP}"
    echo "     S3 bucket: s3://${BEDROCK_LOG_BUCKET}/bedrock-logs/${REGION}/"
  fi
fi
echo ""

# Step 4: Create S3 bucket for build artifacts
echo "--- Step 4: Preparing build bucket ---"
aws s3 mb "s3://${BUILD_BUCKET}" ${PROFILE:+--profile $PROFILE} --region "$REGION" 2>/dev/null || true

# Step 5: Package and upload backend source
echo "--- Step 5: Packaging backend source ---"
TMPZIP="/tmp/backend-source-$(date +%s).zip"
(
  cd "${SCRIPT_DIR}/../backend"
  zip -r "$TMPZIP" . -x "__pycache__/*" ".pytest_cache/*" "*.pyc" ".venv/*" "tests/*" > /dev/null
)
aws s3 cp "$TMPZIP" "s3://${BUILD_BUCKET}/backend-source.zip" \
  ${PROFILE:+--profile $PROFILE} --region "$REGION"
rm -f "$TMPZIP"
echo "Source uploaded to s3://${BUILD_BUCKET}/backend-source.zip"

# Step 6: Create or update CodeBuild project
echo ""
echo "--- Step 6: Setting up CodeBuild project ---"
CB_ROLE_NAME="codebuild-${CB_PROJECT}-role"

# Create IAM role for CodeBuild if it doesn't exist
if ! aws iam get-role --role-name "$CB_ROLE_NAME" ${PROFILE:+--profile $PROFILE} >/dev/null 2>&1; then
  echo "Creating CodeBuild IAM role..."
  aws iam create-role \
    --role-name "$CB_ROLE_NAME" \
    --assume-role-policy-document '{
      "Version": "2012-10-17",
      "Statement": [{
        "Effect": "Allow",
        "Principal": {"Service": "codebuild.amazonaws.com"},
        "Action": "sts:AssumeRole"
      }]
    }' \
    ${PROFILE:+--profile $PROFILE} > /dev/null

  CB_ROLE_IS_NEW=1
else
  CB_ROLE_IS_NEW=0
fi

# Scoped inline policy for the CodeBuild role. Re-applied on every deploy so
# redeploys against a previously-provisioned role tighten the policy in place
# rather than inheriting an older, broader version.
#   - ecr:GetAuthorizationToken requires Resource "*" per AWS docs.
#   - All ECR push/layer actions are pinned to THIS repo ARN only.
#   - ecs:UpdateService / ecs:DescribeServices are pinned to services under
#     our Backend stack's cluster naming prefix.
#   - CloudWatch Logs actions are pinned to this CodeBuild project's log group.
CB_ECR_ARN="arn:aws:ecr:${REGION}:${ACCOUNT}:repository/${ECR_REPO}"
CB_ECS_SERVICE_ARN="arn:aws:ecs:${REGION}:${ACCOUNT}:service/BedrockDashboard-Backend-${ENV}-*/BedrockDashboard-Backend-${ENV}-*"
CB_LOG_GROUP_ARN="arn:aws:logs:${REGION}:${ACCOUNT}:log-group:/aws/codebuild/${CB_PROJECT}"
aws iam put-role-policy \
  --role-name "$CB_ROLE_NAME" \
  --policy-name "codebuild-policy" \
  --policy-document "{
    \"Version\": \"2012-10-17\",
    \"Statement\": [
      {
        \"Sid\": \"EcrAuthToken\",
        \"Effect\": \"Allow\",
        \"Action\": \"ecr:GetAuthorizationToken\",
        \"Resource\": \"*\"
      },
      {
        \"Sid\": \"EcrPushToBackendRepo\",
        \"Effect\": \"Allow\",
        \"Action\": [
          \"ecr:BatchCheckLayerAvailability\",
          \"ecr:GetDownloadUrlForLayer\",
          \"ecr:BatchGetImage\",
          \"ecr:PutImage\",
          \"ecr:InitiateLayerUpload\",
          \"ecr:UploadLayerPart\",
          \"ecr:CompleteLayerUpload\"
        ],
        \"Resource\": \"${CB_ECR_ARN}\"
      },
      {
        \"Sid\": \"EcsForceDeployBackendService\",
        \"Effect\": \"Allow\",
        \"Action\": [
          \"ecs:UpdateService\",
          \"ecs:DescribeServices\"
        ],
        \"Resource\": \"${CB_ECS_SERVICE_ARN}\"
      },
      {
        \"Sid\": \"S3ReadBuildSource\",
        \"Effect\": \"Allow\",
        \"Action\": [
          \"s3:GetObject\",
          \"s3:GetObjectVersion\"
        ],
        \"Resource\": \"arn:aws:s3:::${BUILD_BUCKET}/*\"
      },
      {
        \"Sid\": \"CloudWatchLogsForBuild\",
        \"Effect\": \"Allow\",
        \"Action\": [
          \"logs:CreateLogGroup\",
          \"logs:CreateLogStream\",
          \"logs:PutLogEvents\"
        ],
        \"Resource\": [
          \"${CB_LOG_GROUP_ARN}\",
          \"${CB_LOG_GROUP_ARN}:*\"
        ]
      }
    ]
  }" \
  ${PROFILE:+--profile $PROFILE}

if [ "$CB_ROLE_IS_NEW" = "1" ]; then
  echo "Waiting for role to propagate..."
  sleep 10
fi

CB_ROLE_ARN=$(aws iam get-role --role-name "$CB_ROLE_NAME" ${PROFILE:+--profile $PROFILE} \
  --query 'Role.Arn' --output text)

# Configure the CodeBuild environment. ENV is passed through as an env var so
# backend/buildspec.yml can compose the ECR repo name (bedrock-dashboard-backend-<env>).
# The buildspec derives the account ID from $CODEBUILD_BUILD_ARN, so this
# project spec is account-agnostic.
ENVIRONMENT_CONFIG=$(python3 -c "import json,sys; print(json.dumps({
  'type': 'LINUX_CONTAINER',
  'image': 'aws/codebuild/amazonlinux2-x86_64-standard:5.0',
  'computeType': 'BUILD_GENERAL1_SMALL',
  'privilegedMode': True,
  'environmentVariables': [
    {'name': 'ENV', 'value': '${ENV}', 'type': 'PLAINTEXT'}
  ]
}))")

if aws codebuild batch-get-projects --names "$CB_PROJECT" ${PROFILE:+--profile $PROFILE} --region "$REGION" \
  --query 'projects[0].name' --output text 2>/dev/null | grep -q "$CB_PROJECT"; then
  echo "CodeBuild project exists, updating..."
  aws codebuild update-project \
    --name "$CB_PROJECT" \
    --source "{\"type\": \"S3\", \"location\": \"${BUILD_BUCKET}/backend-source.zip\"}" \
    --artifacts '{"type": "NO_ARTIFACTS"}' \
    --environment "$ENVIRONMENT_CONFIG" \
    --service-role "$CB_ROLE_ARN" \
    ${PROFILE:+--profile $PROFILE} --region "$REGION" > /dev/null
else
  echo "Creating CodeBuild project..."
  aws codebuild create-project \
    --name "$CB_PROJECT" \
    --source "{\"type\": \"S3\", \"location\": \"${BUILD_BUCKET}/backend-source.zip\"}" \
    --artifacts '{"type": "NO_ARTIFACTS"}' \
    --environment "$ENVIRONMENT_CONFIG" \
    --service-role "$CB_ROLE_ARN" \
    ${PROFILE:+--profile $PROFILE} --region "$REGION" > /dev/null
fi

# Step 7: Start build
echo ""
echo "--- Step 7: Building Docker image in CodeBuild ---"
BUILD_ID=$(aws codebuild start-build \
  --project-name "$CB_PROJECT" \
  ${PROFILE:+--profile $PROFILE} --region "$REGION" \
  --query 'build.id' --output text)

echo "Build ID: $BUILD_ID"
echo "Waiting for build to complete (this takes 2-4 minutes)..."

while true; do
  STATUS=$(aws codebuild batch-get-builds \
    --ids "$BUILD_ID" \
    ${PROFILE:+--profile $PROFILE} --region "$REGION" \
    --query 'builds[0].buildStatus' --output text)

  if [ "$STATUS" = "SUCCEEDED" ]; then
    echo "✅ Build SUCCEEDED"
    break
  elif [ "$STATUS" = "FAILED" ] || [ "$STATUS" = "FAULT" ] || [ "$STATUS" = "STOPPED" ] || [ "$STATUS" = "TIMED_OUT" ]; then
    echo "❌ Build $STATUS"
    PHASE=$(aws codebuild batch-get-builds --ids "$BUILD_ID" \
      ${PROFILE:+--profile $PROFILE} --region "$REGION" \
      --query 'builds[0].phases[?phaseStatus==`FAILED`].{phase:phaseType,ctx:contexts}' --output json)
    echo "Failed phase: $PHASE"
    exit 1
  fi

  printf "  ⏳ %s ...\r" "$STATUS"
  sleep 10
done

# Step 8: Deploy Backend CDK stack
echo ""
echo "--- Step 8: Deploying Backend CDK stack ---"
cleanup_failed_stack "BedrockDashboard-Backend-${ENV}"
(
  cd "$SCRIPT_DIR"
  npx --yes cdk deploy "BedrockDashboard-Backend-${ENV}" \
    --require-approval never \
    -c env="$ENV" -c region="$REGION" \
    ${PROFILE:+--profile $PROFILE}
)

# Discover the ECS cluster and service that CDK just created/updated. CDK
# appends account-specific suffixes to physical resource names, so we look
# them up via CloudFormation stack resources rather than hardcoding.
echo ""
echo "Discovering ECS cluster and service from CloudFormation..."
ECS_CLUSTER=$(aws cloudformation list-stack-resources \
  --stack-name "BedrockDashboard-Backend-${ENV}" \
  ${PROFILE:+--profile $PROFILE} --region "$REGION" \
  --query "StackResourceSummaries[?ResourceType=='AWS::ECS::Cluster'].PhysicalResourceId" \
  --output text)
ECS_SERVICE_ARN=$(aws cloudformation list-stack-resources \
  --stack-name "BedrockDashboard-Backend-${ENV}" \
  ${PROFILE:+--profile $PROFILE} --region "$REGION" \
  --query "StackResourceSummaries[?ResourceType=='AWS::ECS::Service'].PhysicalResourceId" \
  --output text)
# Reduce the ARN to just the service name so it works with both --service and
# --service-name (list-tasks) downstream.
ECS_SERVICE="${ECS_SERVICE_ARN##*/}"
if [ -z "$ECS_CLUSTER" ] || [ -z "$ECS_SERVICE" ]; then
  echo "❌ Could not resolve ECS cluster or service from Backend stack resources." >&2
  exit 1
fi
echo "  Cluster: $ECS_CLUSTER"
echo "  Service: $ECS_SERVICE"

# Step 9: Force ECS deployment and wait for rollout
echo ""
echo "--- Step 9: Forcing new ECS deployment and waiting for rollout ---"
aws ecs update-service \
  --cluster "$ECS_CLUSTER" \
  --service "$ECS_SERVICE" \
  --force-new-deployment \
  ${PROFILE:+--profile $PROFILE} --region "$REGION" > /dev/null
echo "Forced new deployment on $ECS_SERVICE. Waiting for rollout (cap ~20 minutes)..."

# Resolve the public API Gateway invoke URL. The ALB is INTERNAL
# (no public IP, unroutable from the deploy host), so we probe
# /api/health/config through API Gateway instead — that path is unauth, so
# a 200 confirms the full chain: API Gateway -> VPC Link -> internal ALB
# -> Fargate FastAPI. Older AWS CLI versions (e.g. aws-cli/1.18.x) don't
# include rolloutState in describe-services output, so we cannot rely on
# rolloutState == COMPLETED alone.
API_ENDPOINT_POLL=$(aws cloudformation describe-stacks \
  --stack-name "BedrockDashboard-Backend-${ENV}" \
  ${PROFILE:+--profile $PROFILE} --region "$REGION" \
  --query 'Stacks[0].Outputs[?OutputKey==`ApiEndpoint`].OutputValue' --output text 2>/dev/null || echo "")
API_STAGE_POLL=$(aws cloudformation describe-stacks \
  --stack-name "BedrockDashboard-Backend-${ENV}" \
  ${PROFILE:+--profile $PROFILE} --region "$REGION" \
  --query 'Stacks[0].Outputs[?OutputKey==`ApiStage`].OutputValue' --output text 2>/dev/null || echo "")
HEALTH_URL_POLL=""
if [ -n "$API_ENDPOINT_POLL" ] && [ "$API_ENDPOINT_POLL" != "None" ] && [ -n "$API_STAGE_POLL" ] && [ "$API_STAGE_POLL" != "None" ]; then
  HEALTH_URL_POLL="https://${API_ENDPOINT_POLL}/${API_STAGE_POLL}/api/health/config"
fi

# Cap at 120 polls × 10s = 20 minutes. Fresh deploys need extra headroom for
# image pull + Alembic migrations + ALB health check dwell time (default
# 5 consecutive 30s checks). Ten minutes was too tight in practice.
MAX_POLLS=120
POLL=0
# Track the last-known good deployment snapshot so a transient CLI failure
# on any single poll does not clobber real progress (previously an empty
# response would reset the display to "UNKNOWN 0/0" and mask that rollout
# had actually completed between the previous poll and the timeout).
ROLLOUT="UNKNOWN"; RUNNING="0"; DESIRED="0"; FAILED="0"
while true; do
  POLL=$((POLL + 1))

  # Fetch the full service JSON (no server-side --query) so a single Python
  # block can extract everything from the PRIMARY deployment. This avoids
  # subtle CLI/JMESPath version quirks that were occasionally returning
  # rolloutState as null on the dev host while runningCount/desiredCount
  # parsed fine — masking a completed rollout as UNKNOWN.
  SVC_JSON=$(aws ecs describe-services \
    --cluster "$ECS_CLUSTER" --services "$ECS_SERVICE" \
    ${PROFILE:+--profile $PROFILE} --region "$REGION" \
    --output json 2>/dev/null) || SVC_JSON=""

  if [ -n "$SVC_JSON" ]; then
    # One python3 call → prints 4 lines: rolloutState, running, desired, failed.
    # If parsing fails, prints nothing and the read below leaves the previous
    # poll's values in place.
    PARSED=$(printf '%s' "$SVC_JSON" | python3 -c '
import sys, json
try:
    data = json.load(sys.stdin)
    svc = (data.get("services") or [{}])[0]
    primary = next((d for d in svc.get("deployments", []) if d.get("status") == "PRIMARY"), {})
    print(primary.get("rolloutState") or "UNKNOWN")
    print(primary.get("runningCount", 0))
    print(primary.get("desiredCount", 0))
    print(primary.get("failedTasks", 0))
except Exception:
    pass
' 2>/dev/null || true)
    if [ -n "$PARSED" ]; then
      { IFS= read -r ROLLOUT || true
        IFS= read -r RUNNING || true
        IFS= read -r DESIRED || true
        IFS= read -r FAILED  || true; } <<< "$PARSED"
    fi
  fi
  # If SVC_JSON was empty or parsing failed, keep the previous poll's values
  # instead of resetting to zeros — a single flaky CLI call should not mask
  # real progress.

  # Primary success path (newer AWS CLI where rolloutState is reported).
  if [ "$ROLLOUT" = "COMPLETED" ] && [ "$RUNNING" = "$DESIRED" ] && [ "$RUNNING" != "0" ]; then
    printf "  ✅ %s  running=%s/%s  failed=%s          \n" "$ROLLOUT" "$RUNNING" "$DESIRED" "$FAILED"
    echo "✅ ECS rollout COMPLETED (${RUNNING}/${DESIRED})"
    break
  fi

  # Fallback success path (older AWS CLI without rolloutState field).
  # Once running == desired > 0 we inline-probe /api/health/config
  # through API Gateway — a 200 confirms the full chain is live
  # (API Gateway + VPC Link + internal ALB + Fargate FastAPI).
  if [ "$RUNNING" = "$DESIRED" ] && [ "$RUNNING" != "0" ] && [ -n "$HEALTH_URL_POLL" ]; then
    HEALTH_CODE=$(curl -sS -o /dev/null -m 5 -w "%{http_code}" "$HEALTH_URL_POLL" 2>/dev/null || echo "000")
    if [ "$HEALTH_CODE" = "200" ]; then
      printf "  ✅ HEALTHY  running=%s/%s  failed=%s          \n" "$RUNNING" "$DESIRED" "$FAILED"
      echo "✅ ECS task healthy via API Gateway (${RUNNING}/${DESIRED}, /api/health/config=200)"
      break
    fi
  fi

  if [ "$ROLLOUT" = "FAILED" ]; then
    echo ""
    echo "❌ ECS rollout FAILED (running=${RUNNING}/${DESIRED}, failedTasks=${FAILED})"
    echo "--- Recent service events ---"
    aws ecs describe-services \
      --cluster "$ECS_CLUSTER" --services "$ECS_SERVICE" \
      ${PROFILE:+--profile $PROFILE} --region "$REGION" \
      --query 'services[0].events[:5].[createdAt,message]' --output text 2>/dev/null || true
    echo "--- Stopped task reasons ---"
    STOPPED_TASKS=$(aws ecs list-tasks \
      --cluster "$ECS_CLUSTER" --service-name "$ECS_SERVICE" --desired-status STOPPED \
      ${PROFILE:+--profile $PROFILE} --region "$REGION" \
      --query 'taskArns' --output text 2>/dev/null || echo "")
    if [ -n "$STOPPED_TASKS" ] && [ "$STOPPED_TASKS" != "None" ]; then
      aws ecs describe-tasks \
        --cluster "$ECS_CLUSTER" --tasks $STOPPED_TASKS \
        ${PROFILE:+--profile $PROFILE} --region "$REGION" \
        --query 'tasks[].{task:taskArn,stoppedReason:stoppedReason,containers:containers[].{name:name,reason:reason}}' \
        --output json 2>/dev/null || true
    else
      echo "(no stopped tasks found)"
    fi
    exit 1
  fi

  if [ "$POLL" -ge "$MAX_POLLS" ]; then
    echo ""
    echo "⚠️  Polling window exhausted after ${MAX_POLLS} polls (rolloutState=${ROLLOUT}, running=${RUNNING}/${DESIRED})."
    echo "Checking /api/health/config through API Gateway as a last-resort signal before failing..."
    HEALTH_CODE=""
    if [ -n "$HEALTH_URL_POLL" ]; then
      HEALTH_CODE=$(curl -sS -o /dev/null -m 5 -w "%{http_code}" "$HEALTH_URL_POLL" 2>/dev/null || echo "000")
    fi
    if [ "$HEALTH_CODE" = "200" ]; then
      echo "✅ /api/health/config via API Gateway returned 200 — treating deployment as successful despite non-COMPLETED rolloutState."
      break
    fi
    echo "❌ Timed out waiting for ECS rollout (last health probe returned ${HEALTH_CODE:-unknown})"
    echo "--- Recent service events ---"
    aws ecs describe-services \
      --cluster "$ECS_CLUSTER" --services "$ECS_SERVICE" \
      ${PROFILE:+--profile $PROFILE} --region "$REGION" \
      --query 'services[0].events[:5].[createdAt,message]' --output text 2>/dev/null || true
    echo "--- Stopped task reasons ---"
    STOPPED_TASKS=$(aws ecs list-tasks \
      --cluster "$ECS_CLUSTER" --service-name "$ECS_SERVICE" --desired-status STOPPED \
      ${PROFILE:+--profile $PROFILE} --region "$REGION" \
      --query 'taskArns' --output text 2>/dev/null || echo "")
    if [ -n "$STOPPED_TASKS" ] && [ "$STOPPED_TASKS" != "None" ]; then
      aws ecs describe-tasks \
        --cluster "$ECS_CLUSTER" --tasks $STOPPED_TASKS \
        ${PROFILE:+--profile $PROFILE} --region "$REGION" \
        --query 'tasks[].{task:taskArn,stoppedReason:stoppedReason,containers:containers[].{name:name,reason:reason}}' \
        --output json 2>/dev/null || true
    else
      echo "(no stopped tasks found)"
    fi
    exit 1
  fi

  printf "  ⏳ %s  running=%s/%s  failed=%s  (poll %s/%s)\r" "$ROLLOUT" "$RUNNING" "$DESIRED" "$FAILED" "$POLL" "$MAX_POLLS"
  sleep 10
done

echo ""
echo "=== ✅ Backend deployment complete ==="
API_ENDPOINT=$(aws cloudformation describe-stacks \
  --stack-name "BedrockDashboard-Backend-${ENV}" \
  ${PROFILE:+--profile $PROFILE} --region "$REGION" \
  --query 'Stacks[0].Outputs[?OutputKey==`ApiEndpoint`].OutputValue' --output text 2>/dev/null || echo "pending")
API_STAGE=$(aws cloudformation describe-stacks \
  --stack-name "BedrockDashboard-Backend-${ENV}" \
  ${PROFILE:+--profile $PROFILE} --region "$REGION" \
  --query 'Stacks[0].Outputs[?OutputKey==`ApiStage`].OutputValue' --output text 2>/dev/null || echo "pending")
INTERNAL_ALB_DNS=$(aws cloudformation describe-stacks \
  --stack-name "BedrockDashboard-Backend-${ENV}" \
  ${PROFILE:+--profile $PROFILE} --region "$REGION" \
  --query 'Stacks[0].Outputs[?OutputKey==`InternalAlbDnsName`].OutputValue' --output text 2>/dev/null || echo "pending")
USER_POOL_ID=$(aws cloudformation describe-stacks \
  --stack-name "BedrockDashboard-Data-${ENV}" \
  ${PROFILE:+--profile $PROFILE} --region "$REGION" \
  --query 'Stacks[0].Outputs[?OutputKey==`UserPoolId`].OutputValue' --output text 2>/dev/null || echo "pending")
echo "API Gateway:      https://${API_ENDPOINT}/${API_STAGE}"
echo "Internal ALB:     ${INTERNAL_ALB_DNS} (private — reachable only via API Gateway)"
echo "Cognito pool:     ${USER_POOL_ID}"
echo ""
echo "Next: run infrastructure/build-and-deploy-frontend.sh to deploy the SPA,"
echo "register the CloudFront callback URL with the Cognito app client, and"
echo "provision a temporary admin login."
