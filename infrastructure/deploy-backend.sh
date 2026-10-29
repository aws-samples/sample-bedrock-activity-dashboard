#!/bin/bash
# Deploy backend: build Docker image via CodeBuild, then deploy CDK stack
# Usage: ./deploy-backend.sh [profile] [region] [env]
#
# Profile resolution order:
#   1. First CLI arg (if provided) — used verbatim
#   2. $AWS_ACCOUNT_ID env var  → profile = Admin-<AWS_ACCOUNT_ID>
#   3. Interactive prompt for  → profile = Admin-<ACCOUNT_ID>

set -euo pipefail

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
ACCOUNT=$(aws sts get-caller-identity ${PROFILE:+--profile $PROFILE} --query Account --output text)

echo "=== Bedrock Dashboard Backend Deploy ==="
echo "Profile: $PROFILE | Region: $REGION | Env: $ENV | Account: $ACCOUNT"
echo ""
echo "This script is safe to re-run — CDK bootstrap and stack deploys are idempotent."
echo "On a fresh account/region, the Data stack deploy takes ~15 min to provision RDS."

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

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

# Ensure the pinned versions of ts-node, typescript, and aws-cdk-lib are
# installed locally. Without this, npx falls back to downloading unpinned
# versions into ~/.npm which can conflict with the versions cdk.json expects.
if [ ! -d "${SCRIPT_DIR}/node_modules" ]; then
  echo ""
  echo "--- Installing CDK dependencies (one-time setup) ---"
  npm install
fi

# CDK synthesizes ALL stacks when running any cdk command, so the FrontendStack's
# reference to frontend/dist is evaluated even when we are only deploying the
# Data/Backend stacks. On a fresh checkout the directory does not exist yet.
# Create an empty placeholder so synth succeeds — real content is populated
# later by build-and-deploy-frontend.sh.
FRONTEND_DIST="${SCRIPT_DIR}/../frontend/dist"
if [ ! -d "$FRONTEND_DIST" ]; then
  echo "Creating placeholder ${FRONTEND_DIST} for CDK synth..."
  mkdir -p "${FRONTEND_DIST}/assets"
  echo "" > "${FRONTEND_DIST}/.placeholder"
fi

# Step 0: CDK bootstrap (idempotent — required for CDK deploys in a fresh
# account/region combo; safe to re-run on already-bootstrapped environments).
echo ""
echo "--- Step 0: CDK bootstrap ---"
npx --yes cdk bootstrap "aws://${ACCOUNT}/${REGION}" \
  -c env="$ENV" -c region="$REGION" \
  ${PROFILE:+--profile $PROFILE}

# Step 1a: Deploy AppRegistry governance stack
echo ""
echo "--- Step 1a: Deploying AppRegistry stack ---"
cleanup_failed_stack "BedrockDashboard-AppRegistry-${ENV}"
npx --yes cdk deploy "BedrockDashboard-AppRegistry-${ENV}" \
  --require-approval never \
  -c env="$ENV" -c region="$REGION" \
  ${PROFILE:+--profile $PROFILE}

# Step 1b: Deploy Data stack (VPC, RDS, ECR, Secrets Manager)
echo ""
echo "--- Step 1b: Deploying Data stack (VPC, RDS, ECR) ---"
cleanup_failed_stack "BedrockDashboard-Data-${ENV}"
npx --yes cdk deploy "BedrockDashboard-Data-${ENV}" \
  --require-approval never \
  -c env="$ENV" -c region="$REGION" \
  ${PROFILE:+--profile $PROFILE}

# Get ECR repo URI
ECR_REPO_URI=$(aws ecr describe-repositories \
  --repository-names "bedrock-dashboard-backend-${ENV}" \
  ${PROFILE:+--profile $PROFILE} --region "$REGION" \
  --query 'repositories[0].repositoryUri' --output text)
echo "ECR Repo: $ECR_REPO_URI"

# Step 2: Build and push Docker image via CodeBuild
echo ""
echo "--- Step 2: Building Docker image via CodeBuild ---"

# Deploy Backend stack first (creates CodeBuild project)
# Use a placeholder image for initial deploy — ECS service will update after build
cleanup_failed_stack "BedrockDashboard-Backend-${ENV}"
npx --yes cdk deploy "BedrockDashboard-Backend-${ENV}" \
  --require-approval never \
  -c env="$ENV" -c region="$REGION" \
  ${PROFILE:+--profile $PROFILE} || true

# Get CodeBuild project name
CB_PROJECT="bedrock-dashboard-backend-build-${ENV}"

# Create a zip of the backend source and upload to S3 for CodeBuild
BUCKET="bedrock-dashboard-build-${ENV}-${ACCOUNT}"
aws s3 mb "s3://${BUCKET}" ${PROFILE:+--profile $PROFILE} --region "$REGION" 2>/dev/null || true

echo "Packaging backend source..."
cd ../backend
zip -r /tmp/backend-source.zip . -x "__pycache__/*" ".pytest_cache/*" "*.pyc" ".venv/*"
aws s3 cp /tmp/backend-source.zip "s3://${BUCKET}/backend-source.zip" \
  ${PROFILE:+--profile $PROFILE} --region "$REGION"

echo "Starting CodeBuild..."
BUILD_ID=$(aws codebuild start-build \
  --project-name "$CB_PROJECT" \
  --source-type-override S3 \
  --source-location-override "${BUCKET}/backend-source.zip" \
  ${PROFILE:+--profile $PROFILE} --region "$REGION" \
  --query 'build.id' --output text)

echo "Build started: $BUILD_ID"
echo "Waiting for build to complete..."

# Poll build status
while true; do
  STATUS=$(aws codebuild batch-get-builds \
    --ids "$BUILD_ID" \
    ${PROFILE:+--profile $PROFILE} --region "$REGION" \
    --query 'builds[0].buildStatus' --output text)
  
  if [ "$STATUS" = "SUCCEEDED" ]; then
    echo "Build SUCCEEDED"
    break
  elif [ "$STATUS" = "FAILED" ] || [ "$STATUS" = "FAULT" ] || [ "$STATUS" = "STOPPED" ] || [ "$STATUS" = "TIMED_OUT" ]; then
    echo "Build $STATUS"
    echo "Check logs: aws codebuild batch-get-builds --ids $BUILD_ID ${PROFILE:+--profile $PROFILE} --region $REGION"
    exit 1
  fi
  
  echo "  Build status: $STATUS ..."
  sleep 15
done

# Step 3: Force ECS service to pick up new image
echo ""
echo "--- Step 3: Updating ECS service ---"
CLUSTER_ARN=$(aws ecs list-clusters ${PROFILE:+--profile $PROFILE} --region "$REGION" \
  --query "clusterArns[?contains(@, 'BackendCluster')]" --output text)

if [ -n "$CLUSTER_ARN" ]; then
  SERVICE_ARN=$(aws ecs list-services --cluster "$CLUSTER_ARN" \
    ${PROFILE:+--profile $PROFILE} --region "$REGION" \
    --query 'serviceArns[0]' --output text)
  
  if [ -n "$SERVICE_ARN" ] && [ "$SERVICE_ARN" != "None" ]; then
    aws ecs update-service --cluster "$CLUSTER_ARN" --service "$SERVICE_ARN" \
      --force-new-deployment \
      ${PROFILE:+--profile $PROFILE} --region "$REGION" > /dev/null
    echo "ECS service update triggered. Waiting for rollout (cap ~10 minutes)..."

    # Service name derived from the ARN (basename after the last '/') for list-tasks
    SERVICE_NAME="${SERVICE_ARN##*/}"

    MAX_POLLS=60
    POLL=0
    while true; do
      POLL=$((POLL + 1))

      # Guard the query so a transient non-zero from the CLI does not kill the script (set -e)
      SVC_JSON=$(aws ecs describe-services \
        --cluster "$CLUSTER_ARN" --services "$SERVICE_ARN" \
        ${PROFILE:+--profile $PROFILE} --region "$REGION" \
        --query 'services[0].deployments[?status==`PRIMARY`]|[0].{rolloutState:rolloutState,running:runningCount,desired:desiredCount,failed:failedTasks}' \
        --output json 2>/dev/null) || SVC_JSON=""

      if [ -n "$SVC_JSON" ] && [ "$SVC_JSON" != "null" ]; then
        ROLLOUT=$(echo "$SVC_JSON" | python3 -c 'import sys,json; d=json.load(sys.stdin) or {}; print(d.get("rolloutState") or "UNKNOWN")' 2>/dev/null || echo "UNKNOWN")
        RUNNING=$(echo "$SVC_JSON" | python3 -c 'import sys,json; d=json.load(sys.stdin) or {}; print(d.get("running") if d.get("running") is not None else 0)' 2>/dev/null || echo "0")
        DESIRED=$(echo "$SVC_JSON" | python3 -c 'import sys,json; d=json.load(sys.stdin) or {}; print(d.get("desired") if d.get("desired") is not None else 0)' 2>/dev/null || echo "0")
        FAILED=$(echo "$SVC_JSON" | python3 -c 'import sys,json; d=json.load(sys.stdin) or {}; print(d.get("failed") if d.get("failed") is not None else 0)' 2>/dev/null || echo "0")
      else
        ROLLOUT="UNKNOWN"; RUNNING="0"; DESIRED="0"; FAILED="0"
      fi

      if [ "$ROLLOUT" = "COMPLETED" ] && [ "$RUNNING" = "$DESIRED" ] && [ "$RUNNING" != "0" ]; then
        printf "  ✅ %s  running=%s/%s  failed=%s          \n" "$ROLLOUT" "$RUNNING" "$DESIRED" "$FAILED"
        echo "✅ ECS rollout COMPLETED (${RUNNING}/${DESIRED})"
        break
      fi

      if [ "$ROLLOUT" = "FAILED" ]; then
        echo ""
        echo "❌ ECS rollout FAILED (running=${RUNNING}/${DESIRED}, failedTasks=${FAILED})"
        echo "--- Recent service events ---"
        aws ecs describe-services \
          --cluster "$CLUSTER_ARN" --services "$SERVICE_ARN" \
          ${PROFILE:+--profile $PROFILE} --region "$REGION" \
          --query 'services[0].events[:5].[createdAt,message]' --output text 2>/dev/null || true
        echo "--- Stopped task reasons ---"
        STOPPED_TASKS=$(aws ecs list-tasks \
          --cluster "$CLUSTER_ARN" --service-name "$SERVICE_NAME" --desired-status STOPPED \
          ${PROFILE:+--profile $PROFILE} --region "$REGION" \
          --query 'taskArns' --output text 2>/dev/null || echo "")
        if [ -n "$STOPPED_TASKS" ] && [ "$STOPPED_TASKS" != "None" ]; then
          aws ecs describe-tasks \
            --cluster "$CLUSTER_ARN" --tasks $STOPPED_TASKS \
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
        echo "❌ Timed out waiting for ECS rollout after ${MAX_POLLS} polls (rolloutState=${ROLLOUT}, running=${RUNNING}/${DESIRED})"
        echo "--- Recent service events ---"
        aws ecs describe-services \
          --cluster "$CLUSTER_ARN" --services "$SERVICE_ARN" \
          ${PROFILE:+--profile $PROFILE} --region "$REGION" \
          --query 'services[0].events[:5].[createdAt,message]' --output text 2>/dev/null || true
        echo "--- Stopped task reasons ---"
        STOPPED_TASKS=$(aws ecs list-tasks \
          --cluster "$CLUSTER_ARN" --service-name "$SERVICE_NAME" --desired-status STOPPED \
          ${PROFILE:+--profile $PROFILE} --region "$REGION" \
          --query 'taskArns' --output text 2>/dev/null || echo "")
        if [ -n "$STOPPED_TASKS" ] && [ "$STOPPED_TASKS" != "None" ]; then
          aws ecs describe-tasks \
            --cluster "$CLUSTER_ARN" --tasks $STOPPED_TASKS \
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
  fi
fi

echo ""
echo "=== Deploy complete ==="
ALB_DNS=$(aws cloudformation describe-stacks \
  --stack-name "BedrockDashboard-Backend-${ENV}" \
  ${PROFILE:+--profile $PROFILE} --region "$REGION" \
  --query 'Stacks[0].Outputs[?OutputKey==`AlbDnsName`].OutputValue' --output text)
echo "Backend API: http://${ALB_DNS}"
