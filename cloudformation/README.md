# Bedrock Activity Dashboard — CloudFormation Deployment

> ⚠️ **DEPRECATED.** These CloudFormation templates do **not** include the
> current API Gateway + internal-ALB architecture. They still reflect the
> legacy design with a publicly-exposed ALB on HTTP:80, and are retained
> for diff reference only.
>
> **The canonical deployment path is the CDK under `infrastructure/`.** Use:
>
> ```bash
> bash infrastructure/build-and-deploy-backend.sh
> bash infrastructure/build-and-deploy-frontend.sh
> ```
>
> See the root README's "One-time cutover" section for the deploy procedure
> on existing legacy deployments.

The infrastructure can be deployed either as **one project stack** (`00-main-stack.yaml`, recommended) or as the **three modular stacks** individually.

## Stack Overview

| Stack | Template | Resources |
|-------|----------|-----------|
| **Main (root)** | `00-main-stack.yaml` | Nested stack that deploys Data + Backend + Frontend together |
| Data | `01-data-stack.yaml` | VPC, RDS PostgreSQL, Secrets Manager, ECR, S3 invocation logs bucket |
| Backend | `02-backend-stack.yaml` | ECS Fargate, ALB, CodeBuild, IAM roles |
| Frontend | `03-frontend-stack.yaml` | S3 static hosting, CloudFront with API proxy |

## Prerequisites

- AWS CLI configured with appropriate permissions
- Docker (for building the backend image)
- Node.js 18+ (for building the frontend)

## Option A — Deploy as one stack (recommended)

`00-main-stack.yaml` composes the three stacks as nested stacks and wires their
cross-stack references automatically. Because it uses nested stacks, you must
`package` it first (this uploads the child templates to S3 and rewrites their
`TemplateURL`s):

```bash
# 1. One-time: an S3 bucket to hold packaged templates
ACCOUNT=$(aws sts get-caller-identity --query Account --output text)
aws s3 mb s3://bedrock-dashboard-cfn-artifacts-$ACCOUNT

# 2. Package
aws cloudformation package \
  --template-file cloudformation/00-main-stack.yaml \
  --s3-bucket bedrock-dashboard-cfn-artifacts-$ACCOUNT \
  --output-template-file cloudformation/packaged-main.yaml

# 3. Deploy the whole project
aws cloudformation deploy \
  --template-file cloudformation/packaged-main.yaml \
  --stack-name BedrockDashboard-dev \
  --parameter-overrides Environment=dev DbInstanceClass=db.t3.micro \
    BedrockModelId=us.amazon.nova-pro-v1:0 BedrockRegion=us-west-2 \
  --capabilities CAPABILITY_NAMED_IAM CAPABILITY_AUTO_EXPAND
```

Stack outputs (`aws cloudformation describe-stacks --stack-name BedrockDashboard-dev`)
include `CodeBuildProjectName`, `FrontendBucketName`, `DistributionId`, and `DashboardUrl`.

> This provisions **infrastructure only**. You still need to build/push the
> backend image and upload the frontend build — see "Post-deploy: application
> artifacts" below.

### Post-deploy: application artifacts

```bash
# Backend image
aws s3 mb s3://bedrock-dashboard-build-dev-$ACCOUNT
cd backend && zip -r /tmp/backend-source.zip . -x "__pycache__/*" "*.pyc" ".venv/*" "tests/*"
aws s3 cp /tmp/backend-source.zip s3://bedrock-dashboard-build-dev-$ACCOUNT/backend-source.zip
aws codebuild start-build --project-name bedrock-dashboard-backend-build-dev

# Frontend assets
cd frontend && npm install && npm run build
BUCKET=$(aws cloudformation describe-stacks --stack-name BedrockDashboard-dev --query 'Stacks[0].Outputs[?OutputKey==`FrontendBucketName`].OutputValue' --output text)
aws s3 sync frontend/dist s3://$BUCKET --delete
DIST_ID=$(aws cloudformation describe-stacks --stack-name BedrockDashboard-dev --query 'Stacks[0].Outputs[?OutputKey==`DistributionId`].OutputValue' --output text)
aws cloudfront create-invalidation --distribution-id $DIST_ID --paths "/*"
```

## Option B — Deploy the modular stacks individually

Deploy in order — each stack depends on the previous one.

### 1. Data Stack

```bash
aws cloudformation deploy \
  --template-file cloudformation/01-data-stack.yaml \
  --stack-name BedrockDashboard-Data-dev \
  --parameter-overrides Environment=dev DbInstanceClass=db.t3.micro \
  --capabilities CAPABILITY_IAM
```

### 2. Backend Stack

First, create the build artifacts bucket and push the backend source:
```bash
aws s3 mb s3://bedrock-dashboard-build-dev-$(aws sts get-caller-identity --query Account --output text)
cd backend && zip -r /tmp/backend-source.zip . -x "__pycache__/*" "*.pyc" ".venv/*" "tests/*"
aws s3 cp /tmp/backend-source.zip s3://bedrock-dashboard-build-dev-$(aws sts get-caller-identity --query Account --output text)/backend-source.zip
```

Then deploy the stack:
```bash
aws cloudformation deploy \
  --template-file cloudformation/02-backend-stack.yaml \
  --stack-name BedrockDashboard-Backend-dev \
  --parameter-overrides \
    Environment=dev \
    DataStackName=BedrockDashboard-Data-dev \
    BedrockModelId=us.amazon.nova-pro-v1:0 \
    BedrockRegion=us-west-2 \
  --capabilities CAPABILITY_NAMED_IAM
```

Build and push the Docker image:
```bash
aws codebuild start-build --project-name bedrock-dashboard-backend-build-dev
```

### 3. Frontend Stack

Build the frontend first:
```bash
cd frontend && npm install && npm run build
```

Deploy the stack:
```bash
aws cloudformation deploy \
  --template-file cloudformation/03-frontend-stack.yaml \
  --stack-name BedrockDashboard-Frontend-dev \
  --parameter-overrides \
    Environment=dev \
    BackendStackName=BedrockDashboard-Backend-dev
```

Upload frontend assets:
```bash
BUCKET=$(aws cloudformation describe-stacks --stack-name BedrockDashboard-Frontend-dev --query 'Stacks[0].Outputs[?OutputKey==`BucketName`].OutputValue' --output text)
aws s3 sync frontend/dist s3://$BUCKET --delete

DIST_ID=$(aws cloudformation describe-stacks --stack-name BedrockDashboard-Frontend-dev --query 'Stacks[0].Outputs[?OutputKey==`DistributionId`].OutputValue' --output text)
aws cloudfront create-invalidation --distribution-id $DIST_ID --paths "/*"
```

## Enable Bedrock Invocation Logging

After deployment, enable Bedrock model invocation logging to both CloudWatch Logs and S3:

```bash
aws bedrock put-model-invocation-logging-configuration --region us-west-2 --logging-config '{
  "cloudWatchConfig": {
    "logGroupName": "/aws/bedrock/model-invocations",
    "roleArn": "arn:aws:iam::ACCOUNT_ID:role/BedrockInvocationLoggingRole"
  },
  "s3Config": {
    "bucketName": "bedrock-invocation-logs-us-west-2-ACCOUNT_ID",
    "keyPrefix": "bedrock-logs"
  },
  "textDataDeliveryEnabled": true,
  "imageDataDeliveryEnabled": false,
  "embeddingDataDeliveryEnabled": false,
  "videoDataDeliveryEnabled": true
}'
```

## Cleanup

Delete in reverse order:
```bash
aws cloudformation delete-stack --stack-name BedrockDashboard-Frontend-dev
aws cloudformation delete-stack --stack-name BedrockDashboard-Backend-dev
aws cloudformation delete-stack --stack-name BedrockDashboard-Data-dev
```
