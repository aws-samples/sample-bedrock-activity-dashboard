#!/bin/bash
# Deploy remaining agents to AgentCore Runtime
# NOTE: the 11 deterministic-agent Runtimes were removed in main; only guardrail-recommender
# and weekly-digest-orchestrator (the two agentic agents) get deployed here.
#
# Account resolution order:
#   1. $AWS_ACCOUNT_ID env var  ÃƒÂ¢Ã¢â‚¬Â Ã¢â‚¬â„¢ AWS_PROFILE = Admin-<AWS_ACCOUNT_ID>
#   2. Interactive prompt        ÃƒÂ¢Ã¢â‚¬Â Ã¢â‚¬â„¢ AWS_PROFILE = Admin-<ACCOUNT_ID>

set -e

# Resolve AWS account ID from env var or interactive prompt.
# An empty response at the prompt means "use current environment credentials"
# (AWS_ACCESS_KEY_ID / AWS_SECRET_ACCESS_KEY / AWS_SESSION_TOKEN); AWS_PROFILE
# will not be exported in that case.
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
  # Discover account ID from current credentials so aws-targets.json can be templated
  AWS_ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)
fi
export AWS_DEFAULT_REGION=us-west-2

AGENTS=(
  "guardrail-recommender"
  "weekly-digest-orchestrator"
)

RESULTS_FILE="agentcore-agents/deployment-results.json"
echo "{" > "$RESULTS_FILE"

for AGENT_NAME in "${AGENTS[@]}"; do
  echo ""
  echo "=========================================="
  echo "Setting up and deploying: $AGENT_NAME"
  echo "=========================================="
  
  PROJECT_NAME=$(echo "$AGENT_NAME" | tr -d '-')
  AGENT_DIR="agentcore-agents/$AGENT_NAME"
  PROJECT_DIR="$AGENT_DIR/$PROJECT_NAME"
  INNER_DIR="$PROJECT_DIR/$PROJECT_NAME"
  
  # Step 1: Init uv project if needed
  if [ ! -d "$PROJECT_DIR" ]; then
    echo "Step 1: uv init..."
    (cd "$AGENT_DIR" && uv init "$PROJECT_NAME" --python 3.13)
    (cd "$PROJECT_DIR" && uv add bedrock-agentcore)
  fi
  
  # Step 2: agentcore create if needed
  if [ ! -d "$INNER_DIR/agentcore/cdk" ]; then
    echo "Step 2: agentcore create..."
    (cd "$PROJECT_DIR" && agentcore create --name "$PROJECT_NAME" --defaults --skip-git --skip-python-setup --json)
  fi
  
  # Step 3: Copy lightweight main.py
  echo "Step 3: Copy lightweight main.py..."
  cp "$AGENT_DIR/main_lightweight.py" "$INNER_DIR/app/$PROJECT_NAME/main.py"
  
  # Step 4: Set minimal pyproject.toml
  echo "Step 4: Set minimal pyproject.toml..."
  cat > "$INNER_DIR/app/$PROJECT_NAME/pyproject.toml" << PYEOF
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
  
  # Step 5: Lock deps
  echo "Step 5: uv lock..."
  (cd "$INNER_DIR/app/$PROJECT_NAME" && uv lock)
  
  # Step 6: Set aws-targets.json
  echo "Step 6: Set aws-targets.json..."
  cat > "$INNER_DIR/agentcore/aws-targets.json" << ATEOF
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
  
  # Step 7: Deploy
  echo "Step 7: Deploying..."
  RESULT=$(cd "$INNER_DIR" && agentcore deploy --target default -y --json 2>&1) || true
  echo "$RESULT"
  
  # Extract runtime ID
  RUNTIME_ID=$(echo "$RESULT" | python3 -c "
import sys, json
try:
    d = json.load(sys.stdin)
    for k, v in d.get('outputs', {}).items():
        if 'RuntimeId' in k:
            print(v)
            break
except:
    print('FAILED')
" 2>/dev/null)
  
  echo "Runtime ID: $RUNTIME_ID"
  echo "  \"$AGENT_NAME\": \"$RUNTIME_ID\"," >> "$RESULTS_FILE"
done

echo "  \"_deployed_at\": \"$(date -u +%Y-%m-%dT%H:%M:%SZ)\"" >> "$RESULTS_FILE"
echo "}" >> "$RESULTS_FILE"

echo ""
echo "=========================================="
echo "All deployments complete!"
echo "=========================================="
cat "$RESULTS_FILE"
