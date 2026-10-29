#!/bin/bash
# Deploy all 13 agents to AgentCore Runtime
# Usage: ./deploy-all.sh
# Requires: agentcore CLI, uv
#
# Account resolution order:
#   1. $AWS_ACCOUNT_ID env var  â†’ AWS_PROFILE = Admin-<AWS_ACCOUNT_ID>
#   2. Interactive prompt        â†’ AWS_PROFILE = Admin-<ACCOUNT_ID>

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
fi
export AWS_DEFAULT_REGION=us-west-2

# Every agentcore-agents/*/agentcore/aws-targets.json is committed with the
# marker "__AWS_ACCOUNT_ID__" instead of a real account. Substitute the marker
# with the resolved account (from env var or STS) before invoking agentcore,
# and set it back afterwards so the working tree stays clean and portable.
if [ -z "${AWS_ACCOUNT_ID:-}" ]; then
  AWS_ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)
fi
_TARGETS_FILES=()
while IFS= read -r f; do
  _TARGETS_FILES+=("$f")
done < <(find . -type f -name "aws-targets.json")
_restore_placeholders() {
  for f in "${_TARGETS_FILES[@]}"; do
    [ -f "$f" ] || continue
    # Replace whichever account is present with the placeholder so the diff
    # stays clean regardless of interrupted runs.
    python3 -c "
import re, sys
p = sys.argv[1]
with open(p, 'r') as fh:
    s = fh.read()
s = re.sub(r'\"account\"\s*:\s*\"[0-9]{12}\"', '\"account\": \"__AWS_ACCOUNT_ID__\"', s)
with open(p, 'w') as fh:
    fh.write(s)
" "$f"
  done
}
trap _restore_placeholders EXIT
for f in "${_TARGETS_FILES[@]}"; do
  python3 -c "
import sys
p, acct = sys.argv[1], sys.argv[2]
with open(p, 'r') as fh:
    s = fh.read()
with open(p, 'w') as fh:
    fh.write(s.replace('__AWS_ACCOUNT_ID__', acct))
" "$f" "$AWS_ACCOUNT_ID"
done

AGENTS=(
  "guardrail-recommender"
  "weekly-digest-orchestrator"
)

RESULTS_FILE="deployment-results.json"
echo "{" > "$RESULTS_FILE"

for agent in "${AGENTS[@]}"; do
  echo ""
  echo "=========================================="
  echo "Deploying: $agent"
  echo "=========================================="
  
  # Convert agent name to project dir name (remove hyphens for agentcore project name)
  project_name=$(echo "$agent" | tr -d '-')
  project_dir="$agent/$project_name"
  
  if [ ! -d "$project_dir/agentcore" ]; then
    echo "SKIP: $agent â€” no agentcore project found at $project_dir"
    continue
  fi
  
  cd "$project_dir"
  
  # Deploy
  echo "Deploying $agent..."
  result=$(agentcore deploy --target default -y --json 2>&1) || true
  echo "$result"
  
  # Extract runtime ID
  runtime_id=$(echo "$result" | python3 -c "import sys,json; d=json.load(sys.stdin); print(list(v for k,v in d.get('outputs',{}).items() if 'RuntimeId' in k)[0])" 2>/dev/null || echo "FAILED")
  
  echo "Runtime ID: $runtime_id"
  cd ../..
  
  # Append to results
  echo "  \"$agent\": \"$runtime_id\"," >> "$RESULTS_FILE"
done

echo "  \"_deployed_at\": \"$(date -u +%Y-%m-%dT%H:%M:%SZ)\"" >> "$RESULTS_FILE"
echo "}" >> "$RESULTS_FILE"

echo ""
echo "=========================================="
echo "Deployment complete! Results in $RESULTS_FILE"
echo "=========================================="
cat "$RESULTS_FILE"
