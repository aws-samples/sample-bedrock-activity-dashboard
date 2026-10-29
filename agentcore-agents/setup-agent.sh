#!/bin/bash
# Setup a single agent for AgentCore deployment
# Usage: ./setup-agent.sh <agent-name>
# This creates the agentcore project structure inside the agent directory
#
# Account resolution order:
#   1. $AWS_ACCOUNT_ID env var  → AWS_PROFILE = Admin-<AWS_ACCOUNT_ID>
#   2. Interactive prompt        → AWS_PROFILE = Admin-<ACCOUNT_ID>

set -e

AGENT_NAME="$1"
if [ -z "$AGENT_NAME" ]; then
  echo "Usage: ./setup-agent.sh <agent-name>"
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

PROJECT_NAME=$(echo "$AGENT_NAME" | tr -d '-')

echo "Setting up AgentCore project for: $AGENT_NAME (project: $PROJECT_NAME)"

cd "$AGENT_NAME"

# Skip if already set up
if [ -d "$PROJECT_NAME/agentcore" ]; then
  echo "Already set up. Skipping."
  exit 0
fi

# Initialize uv project
uv init "$PROJECT_NAME" --python 3.13
cd "$PROJECT_NAME"
uv add bedrock-agentcore

# Create agentcore config
mkdir -p agentcore
cat > agentcore/agentcore.json << EOF
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
EOF

cat > agentcore/aws-targets.json << EOF
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
EOF

# Create app directory
mkdir -p "app/$PROJECT_NAME"

# Copy the lightweight main.py from parent
cp ../main_lightweight.py "app/$PROJECT_NAME/main.py"

# Create minimal pyproject.toml for the app
cat > "app/$PROJECT_NAME/pyproject.toml" << EOF
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
EOF

# Create lock file
cd "app/$PROJECT_NAME"
uv lock
cd ../..

# Run agentcore create to set up CDK
agentcore create --name "$PROJECT_NAME" --framework strands --template basic 2>/dev/null || true

echo "Setup complete for $AGENT_NAME"
