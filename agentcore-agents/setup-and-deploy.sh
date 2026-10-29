#!/bin/bash
# Setup and deploy a single agent to AgentCore Runtime
# Usage: ./setup-and-deploy.sh <agent-name>
#
# Account resolution order:
#   1. $AWS_ACCOUNT_ID env var  → AWS_PROFILE = Admin-<AWS_ACCOUNT_ID>
#   2. Interactive prompt        → AWS_PROFILE = Admin-<ACCOUNT_ID>

set -e

AGENT_NAME="$1"
if [ -z "$AGENT_NAME" ]; then
  echo "Usage: ./setup-and-deploy.sh <agent-name>"
  exit 1
fi

# Resolve AWS account ID from env var or interactive prompt.
# An empty response at the prompt means "use current environment credentials"
# (AWS_ACCESS_KEY_ID / AWS_SECRET_ACCESS_KEY / AWS_SESSION_TOKEN); AWS_PROFILE
# will not be exported in that case, and the account ID will be discovered
# from the current caller identity so aws-targets.json can be templated.
if [ -z "${AWS_ACCOUNT_ID:-}" ]; then
  read -r -p "AWS Account ID (12 digits, or press Enter to use current env credentials): " AWS_ACCOUNT_ID
fi
if [ -n "${AWS_ACCOUNT_ID:-}" ]; then
  if [[ ! "$AWS_ACCOUNT_ID" =~ ^[0-9]{12}$ ]]; then
    echo "Error: AWS_ACCOUNT_ID must be exactly 12 digits (got: '$AWS_ACCOUNT_ID')" >&2
    exit 1
  fi
  export AWS_PROFILE="Admin-${AWS_ACCOUNT_ID}"
else
  AWS_ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)
fi
export AWS_DEFAULT_REGION=us-west-2

# Convert agent-name to projectname (no hyphens)
PROJECT_NAME=$(echo "$AGENT_NAME" | tr -d '-')

echo "=== Setting up $AGENT_NAME (project: $PROJECT_NAME) ==="

cd "agentcore-agents/$AGENT_NAME"

# Any pre-existing aws-targets.json for this agent is checked in with the
# placeholder "__AWS_ACCOUNT_ID__". Substitute the resolved account before we
# invoke agentcore, and restore the placeholder on exit so the working tree
# stays account-agnostic across runs.
_restore_placeholders() {
  while IFS= read -r f; do
    [ -f "$f" ] || continue
    python3 -c "
import re, sys
p = sys.argv[1]
with open(p, 'r') as fh:
    s = fh.read()
s = re.sub(r'\"account\"\s*:\s*\"[0-9]{12}\"', '\"account\": \"__AWS_ACCOUNT_ID__\"', s)
with open(p, 'w') as fh:
    fh.write(s)
" "$f"
  done < <(find . -type f -name "aws-targets.json" 2>/dev/null)
}
trap _restore_placeholders EXIT
while IFS= read -r f; do
  python3 -c "
import sys
p, acct = sys.argv[1], sys.argv[2]
with open(p, 'r') as fh:
    s = fh.read()
with open(p, 'w') as fh:
    fh.write(s.replace('__AWS_ACCOUNT_ID__', acct))
" "$f" "$AWS_ACCOUNT_ID"
done < <(find . -type f -name "aws-targets.json" 2>/dev/null)

# Check if already has agentcore project
if [ -d "$PROJECT_NAME/agentcore/cdk" ]; then
  echo "Project already exists. Deploying..."
  cd "$PROJECT_NAME"
  
  # Copy lightweight main.py
  cp ../main_lightweight.py "app/$PROJECT_NAME/main.py"
  
  # Ensure minimal deps in pyproject.toml
  cat > "app/$PROJECT_NAME/pyproject.toml" << PYEOF
[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[project]
name = "$PROJECT_NAME"
version = "0.1.0"
description = "AgentCore Runtime Agent: $AGENT_NAME"
requires-python = ">=3.10"
dependencies = [
    "aws-opentelemetry-distro",
]

[tool.hatch.build.targets.wheel]
packages = ["."]
PYEOF
  
  cd "app/$PROJECT_NAME"
  uv lock
  cd ../..
  
  agentcore deploy --target default -y --json
  exit $?
fi

# Create new project
echo "Creating new agentcore project..."
uv init "$PROJECT_NAME" --python 3.13
cd "$PROJECT_NAME"
uv add bedrock-agentcore

# Run agentcore create (non-interactive)
echo "Running agentcore create..."
echo -e "strands\n$PROJECT_NAME\nbasic\n" | agentcore create 2>/dev/null || {
  echo "agentcore create failed, setting up manually..."
  
  mkdir -p agentcore
  cat > agentcore/agentcore.json << ACEOF
{
  "\$schema": "https://schema.agentcore.aws.dev/v1/agentcore.json",
  "name": "$PROJECT_NAME",
  "version": 1,
  "managedBy": "CDK",
  "tags": {
    "agentcore:created-by": "agentcore-cli",
    "agentcore:project-name": "$PROJECT_NAME"
  },
  "runtimes": [
    {
      "name": "$PROJECT_NAME",
      "build": "CodeZip",
      "entrypoint": "main.py",
      "codeLocation": "app/$PROJECT_NAME/",
      "runtimeVersion": "PYTHON_3_13",
      "networkMode": "PUBLIC",
      "protocol": "HTTP"
    }
  ],
  "memories": [],
  "credentials": [],
  "evaluators": [],
  "onlineEvalConfigs": [],
  "agentCoreGateways": [],
  "policyEngines": []
}
ACEOF

  cat > agentcore/aws-targets.json << ATEOF
[
  {
    "name": "default",
    "account": "${AWS_ACCOUNT_ID}",
    "region": "us-west-2",
    "runtime": {
      "name": "$PROJECT_NAME",
      "deploymentType": "CODE",
      "entrypoint": "main.py"
    }
  }
]
ATEOF
}

# Create app directory with lightweight main.py
mkdir -p "app/$PROJECT_NAME"
cp ../main_lightweight.py "app/$PROJECT_NAME/main.py"

# Create minimal pyproject.toml
cat > "app/$PROJECT_NAME/pyproject.toml" << PYEOF
[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[project]
name = "$PROJECT_NAME"
version = "0.1.0"
description = "AgentCore Runtime Agent: $AGENT_NAME"
requires-python = ">=3.10"
dependencies = [
    "aws-opentelemetry-distro",
]

[tool.hatch.build.targets.wheel]
packages = ["."]
PYEOF

cd "app/$PROJECT_NAME"
uv lock
cd ../..

# Deploy
echo "Deploying $AGENT_NAME..."
agentcore deploy --target default -y --json
