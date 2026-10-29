#!/bin/bash
# Enable Bedrock model invocation logging in all regions that support Bedrock
# Usage: ./enable-bedrock-logging-all-regions.sh [profile]
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
ACCOUNT=$(aws sts get-caller-identity ${PROFILE:+--profile $PROFILE} --query Account --output text)
LOG_GROUP="/aws/bedrock/model-invocations"
ROLE_NAME="BedrockInvocationLoggingRole"

# Regions where Bedrock is available
BEDROCK_REGIONS=(
  us-east-1
  us-west-2
  eu-west-1
  eu-central-1
  ap-northeast-1
  ap-southeast-1
  ap-southeast-2
  us-east-2
  ap-south-1
  ca-central-1
)

echo "=== Enabling Bedrock Invocation Logging in all regions ==="
echo "Account: $ACCOUNT"
echo ""

# Ensure the IAM role exists (global, created once)
ROLE_CREATED=0
if ! aws iam get-role --role-name "$ROLE_NAME" ${PROFILE:+--profile $PROFILE} 2>/dev/null | grep -q RoleId; then
  echo "Creating IAM role $ROLE_NAME..."
  aws iam create-role \
    --role-name "$ROLE_NAME" \
    --assume-role-policy-document '{
      "Version":"2012-10-17",
      "Statement":[{
        "Effect":"Allow",
        "Principal":{"Service":"bedrock.amazonaws.com"},
        "Action":"sts:AssumeRole"
      }]
    }' \
    ${PROFILE:+--profile $PROFILE} > /dev/null
  ROLE_CREATED=1
fi

# Always (re-)apply the inline policy so pre-existing roles created by an
# older version of this script are upgraded with the missing permissions
# (logs:CreateLogStream in particular — without it Bedrock's validation
# fails with "Failed to validate permissions for log group" and every
# region rejects the put-model-invocation-logging-configuration call).
# Scoping strategy (mirrors build-and-deploy-backend.sh Step 3.5):
#   - LogGroupWrite: pinned to the /aws/bedrock/model-invocations log group
#     ARN pattern (any region, this account).
#   - LogsServiceList: Describe* list APIs that cannot be scoped.
#   - LogDeliveryBootstrap: CreateLogDelivery + PutResourcePolicy are one-time
#     setup actions and not resource-scopeable; the role's trust policy
#     restricts the only principal that can assume it to bedrock.amazonaws.com.
aws iam put-role-policy \
  --role-name "$ROLE_NAME" \
  --policy-name "BedrockLoggingPolicy" \
  --policy-document '{
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
          "arn:aws:logs:*:'"${ACCOUNT}"':log-group:'"${LOG_GROUP}"'",
          "arn:aws:logs:*:'"${ACCOUNT}"':log-group:'"${LOG_GROUP}"':*"
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
        "Resource":"arn:aws:s3:::bedrock-invocation-logs-'"${ACCOUNT}"'/*"
      }
    ]
  }' \
  ${PROFILE:+--profile $PROFILE} > /dev/null

if [ "$ROLE_CREATED" = "1" ]; then
  echo "IAM role created. Waiting 30s for propagation..."
  sleep 30
else
  # Even for a pre-existing role, IAM needs a few seconds to propagate a
  # freshly-updated inline policy before Bedrock's cross-service validation
  # sees it. Short pause is cheap insurance against a first-region failure.
  echo "Refreshed IAM role policy on existing role. Waiting 15s for propagation..."
  sleep 15
fi

ROLE_ARN=$(aws iam get-role --role-name "$ROLE_NAME" ${PROFILE:+--profile $PROFILE} --query 'Role.Arn' --output text)
echo "Using role: $ROLE_ARN"
echo ""

# Ensure the shared S3 bucket for invocation logs exists BEFORE we ask Bedrock
# to write to it — put-model-invocation-logging-configuration validates that
# the bucket exists and is writable by the Bedrock service principal.
BUCKET="bedrock-invocation-logs-${ACCOUNT}"
# Bucket lives in a single region; pick the first Bedrock region as the home
# region (Bedrock in other regions writes across regions via the service, which
# is supported). us-east-1 is the safest bet for cross-region writes.
BUCKET_REGION="us-east-1"
if ! aws s3api head-bucket --bucket "$BUCKET" \
    ${PROFILE:+--profile $PROFILE} 2>/dev/null; then
  echo "Creating shared invocation-log bucket s3://${BUCKET} in ${BUCKET_REGION}..."
  if [ "$BUCKET_REGION" = "us-east-1" ]; then
    aws s3api create-bucket --bucket "$BUCKET" \
      ${PROFILE:+--profile $PROFILE} --region "$BUCKET_REGION" > /dev/null
  else
    aws s3api create-bucket --bucket "$BUCKET" \
      --create-bucket-configuration LocationConstraint="$BUCKET_REGION" \
      ${PROFILE:+--profile $PROFILE} --region "$BUCKET_REGION" > /dev/null
  fi
  aws s3api put-public-access-block --bucket "$BUCKET" \
    --public-access-block-configuration \
      BlockPublicAcls=true,IgnorePublicAcls=true,BlockPublicPolicy=true,RestrictPublicBuckets=true \
    ${PROFILE:+--profile $PROFILE} > /dev/null
  aws s3api put-bucket-encryption --bucket "$BUCKET" \
    --server-side-encryption-configuration \
    '{"Rules":[{"ApplyServerSideEncryptionByDefault":{"SSEAlgorithm":"AES256"}}]}' \
    ${PROFILE:+--profile $PROFILE} > /dev/null
fi

# Bucket policy — allow Bedrock service to PutObject, scoped to this account.
# Applied every run so it stays current if the bucket already existed.
aws s3api put-bucket-policy --bucket "$BUCKET" \
  --policy "{
    \"Version\": \"2012-10-17\",
    \"Statement\": [{
      \"Sid\": \"AllowBedrockPutObject\",
      \"Effect\": \"Allow\",
      \"Principal\": {\"Service\": \"bedrock.amazonaws.com\"},
      \"Action\": \"s3:PutObject\",
      \"Resource\": \"arn:aws:s3:::${BUCKET}/*\",
      \"Condition\": {
        \"StringEquals\": {\"aws:SourceAccount\": \"${ACCOUNT}\"}
      }
    }]
  }" \
  ${PROFILE:+--profile $PROFILE} > /dev/null
echo "Bucket ready: s3://${BUCKET}"
echo ""

ENABLED_REGIONS=()
SKIPPED_REGIONS=()
declare -A REGION_ERROR

for REGION in "${BEDROCK_REGIONS[@]}"; do
  echo -n "[$REGION] Enabling logging... "

  # Create log group if it doesn't exist. Suppress stderr because a pre-existing
  # group is not an error worth surfacing.
  aws logs create-log-group \
    --log-group-name "$LOG_GROUP" \
    ${PROFILE:+--profile $PROFILE} \
    --region "$REGION" 2>/dev/null || true

  # Enable Bedrock invocation logging. Capture stderr into a variable so we
  # can distinguish "Bedrock not available in this region" from real errors
  # (IAM propagation, bucket policy, missing permissions, etc.).
  if ERR_OUT=$(aws bedrock put-model-invocation-logging-configuration \
    --logging-config "{
      \"cloudWatchConfig\": {
        \"logGroupName\": \"${LOG_GROUP}\",
        \"roleArn\": \"${ROLE_ARN}\",
        \"largeDataDeliveryS3Config\": {
          \"bucketName\": \"${BUCKET}\",
          \"keyPrefix\": \"bedrock-logs/${REGION}\"
        }
      },
      \"s3Config\": {
        \"bucketName\": \"${BUCKET}\",
        \"keyPrefix\": \"bedrock-logs/${REGION}\"
      },
      \"textDataDeliveryEnabled\": true,
      \"imageDataDeliveryEnabled\": false,
      \"embeddingDataDeliveryEnabled\": false
    }" \
    ${PROFILE:+--profile $PROFILE} \
    --region "$REGION" 2>&1); then
    echo "✅"
    ENABLED_REGIONS+=("$REGION")
  else
    echo "⚠️  failed"
    SKIPPED_REGIONS+=("$REGION")
    REGION_ERROR[$REGION]="$ERR_OUT"
  fi
done

echo ""
echo "=== Summary ==="
if [ ${#ENABLED_REGIONS[@]} -gt 0 ]; then
  echo "Enabled in: ${ENABLED_REGIONS[*]}"
else
  echo "Enabled in: (none)"
fi
if [ ${#SKIPPED_REGIONS[@]} -gt 0 ]; then
  echo "Failed / skipped:"
  for r in "${SKIPPED_REGIONS[@]}"; do
    echo "  [$r] ${REGION_ERROR[$r]}"
  done
fi
echo ""
if [ ${#ENABLED_REGIONS[@]} -gt 0 ]; then
  echo "Regions to add to CLOUDTRAIL_REGIONS env var:"
  printf '%s\n' "${ENABLED_REGIONS[@]}" | paste -sd ',' -
fi
