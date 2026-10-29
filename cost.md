# Bedrock Activity Dashboard — AWS Cost Estimate

**Region**: us-west-2 (Oregon)  
**Date**: March 2026  
**Pricing model**: On-Demand (no Reserved Instances or Savings Plans)  
**AI Model**: Claude Sonnet 3.7 (`anthropic.claude-3-7-sonnet-20250219-v1:0`)

> Prices verified against the AWS Pricing API on March 16, 2026 (publication date 2026-03-13).  
> Actual costs vary with usage. Use the [AWS Pricing Calculator](https://calculator.aws/) for precise project-specific estimates.

---

## Architecture Overview

The deployed stack consists of:

| Layer | Service | Config |
|---|---|---|
| Frontend CDN | Amazon CloudFront | React SPA, HTTPS |
| Frontend Storage | Amazon S3 | Static website hosting |
| Backend Compute | Amazon ECS Fargate | 0.5 vCPU / 1 GB RAM (dev) |
| API Gateway | Amazon API Gateway (REST) | Public endpoint, Cognito authorizer, response streaming |
| Load Balancer | Application Load Balancer (ALB) | **Internal** (private subnets), VPC Link v2 from API Gateway, idle timeout 300s |
| Database | Amazon RDS PostgreSQL | db.t3.medium, Single-AZ (dev) |
| Container Registry | Amazon ECR | Private repository |
| AI Agent | Amazon Bedrock | Claude Sonnet 3.7 via Converse API |
| Auth | Amazon Cognito | User Pool + SAML IdP (IAM Identity Center) |
| Monitoring | AWS CloudTrail | 90-day Event History (free tier) |
| Alerts | Amazon SNS | Email notifications |
| IaC | AWS CDK / CloudFormation | 3 stacks |

---

## Service-by-Service Pricing

### 1. Amazon CloudFront

| Component | Unit Price | Dev/Month | Prod/Month |
|---|---|---|---|
| Data transfer out (first 10 TB) | $0.085/GB | 5 GB = **$0.43** | 500 GB = **$42.50** |
| HTTPS requests | $0.0000010/request | 50K = **$0.05** | 5M = **$5.00** |
| **Subtotal** | | **$0.48** | **$47.50** |

> Free tier: 1 TB data transfer + 10M requests/month (always free).  
> Dev usage stays within free tier — cost shown is post-free-tier estimate.

---

### 2. Amazon S3 (Frontend Static Hosting)

| Component | Unit Price | Dev/Month | Prod/Month |
|---|---|---|---|
| Storage (Standard) | $0.023/GB | ~50 MB = **$0.001** | ~100 MB = **$0.002** |
| PUT requests (deploys) | $0.005/1K | ~100 = **$0.001** | ~500 = **$0.003** |
| GET requests (CloudFront origin) | $0.0004/1K | Minimal | Minimal |
| **Subtotal** | | **~$0.01** | **~$0.01** |

> S3 costs for a React SPA are negligible — typically under $0.05/month.

---

### 3. Amazon ECS Fargate (Backend Compute)

Pricing (us-west-2, Linux): **$0.04048/vCPU/hour**, **$0.004445/GB/hour**

| Component | Dev (1 task, 0.5 vCPU / 1 GB) | Prod (2 tasks, 1 vCPU / 2 GB) |
|---|---|---|
| vCPU cost | 0.5 × $0.04048 × 730h = **$14.78** | 2 × 1 × $0.04048 × 730h = **$59.10** |
| Memory cost | 1 × $0.004445 × 730h = **$3.24** | 2 × 2 × $0.004445 × 730h = **$12.98** |
| **Subtotal** | **$18.02** | **$72.08** |

> Fargate Spot can reduce dev/staging costs by up to 70%.  
> A 1-year Compute Savings Plan saves ~20% on Fargate.

---

### 4. Application Load Balancer (ALB)

Pricing (us-west-2): **$0.0225/ALB-hour**, **$0.008/LCU-hour**

| Component | Unit Price | Dev/Month | Prod/Month |
|---|---|---|---|
| ALB hours | $0.0225/hour | 730h = **$16.43** | 730h = **$16.43** |
| LCU hours (estimated) | $0.008/LCU-hour | ~1 LCU = **$5.84** | ~3 LCU = **$17.52** |
| **Subtotal** | | **$22.27** | **$33.95** |

> LCU estimate based on low-traffic dashboard usage (~100 req/min dev, ~500 req/min prod).

---

### 5. Amazon RDS PostgreSQL

Pricing (us-west-2, On-Demand):

| Instance | Hourly Rate | Monthly |
|---|---|---|
| db.t3.medium (Single-AZ) | $0.0720/hour | **$52.56** |
| db.t3.large (Single-AZ) | $0.1450/hour | **$105.85** |
| db.t3.large (Multi-AZ) | $0.2900/hour | **$211.70** |

| Component | Unit Price | Dev/Month | Prod/Month |
|---|---|---|---|
| Instance (db.t3.medium, Single-AZ) | $0.0720/hour | **$52.56** | — |
| Instance (db.t3.large, Multi-AZ) | $0.2900/hour | — | **$211.70** |
| Storage (gp3, 20 GB) | $0.115/GB/month | **$2.30** | — |
| Storage (gp3, 100 GB) | $0.115/GB/month | — | **$11.50** |
| Backup storage | Free up to DB size | $0 | $0 |
| **Subtotal** | | **$54.86** | **$223.20** |

> Production uses Multi-AZ for high availability (2× instance cost).  
> 1-year Reserved Instance saves ~40% (~$31/mo dev, ~$89/mo prod).

---

### 6. Amazon ECR (Container Registry)

| Component | Unit Price | Dev/Month | Prod/Month |
|---|---|---|---|
| Storage | $0.10/GB/month | ~2 GB = **$0.20** | ~5 GB = **$0.50** |
| Data transfer (within AWS) | Free | $0 | $0 |
| **Subtotal** | | **$0.20** | **$0.50** |

> Docker image for this backend is approximately 500 MB–1 GB compressed.

---

### 7. Amazon Bedrock — Claude Sonnet 3.7 (AI Agent)

**Model**: `anthropic.claude-3-7-sonnet-20250219-v1:0`  
**Pricing (us-west-2, verified)**: $3.00/1M input tokens · $15.00/1M output tokens

Each AI Agent chat request includes:
- ~40K tokens of usage context (system prompt + dashboard data)
- ~500 tokens user message
- ~1K–2K tokens response

| Usage Scenario | Tokens/Query | Dev (50 queries/mo) | Prod (500 queries/mo) |
|---|---|---|---|
| Input tokens | ~40.5K/query | 2.025M = **$6.08** | 20.25M = **$60.75** |
| Output tokens | ~1.5K/query | 0.075M = **$1.13** | 0.75M = **$11.25** |
| Daily recommendations | ~2M input/run | 30 runs = **$6.00** | 30 runs = **$6.00** |
| Weekly reports | ~1M input/report | 4 reports = **$3.00** | 4 reports = **$3.00** |
| **Subtotal** | | **~$16.21** | **~$81.00** |

> Claude Sonnet 3.7 is significantly more capable but more expensive than Claude Haiku 3.  
> For cost optimization, consider switching to **Claude Haiku 3** ($0.25/$1.25 per 1M tokens) for routine queries — saves ~92%.

---

### 8. Amazon Cognito (Authentication)

**Setup**: User Pool with SAML IdP (IAM Identity Center federation)  
**Tier**: Essentials — SAML/OIDC federation included at flat rate

| Component | Unit Price | Dev/Month | Prod/Month |
|---|---|---|---|
| MAUs (Essentials, incl. SAML) | $0.015/MAU | 10 users = **$0.15** | 100 users = **$1.50** |
| **Subtotal** | | **$0.15** | **$1.50** |

> Free tier: 50 MAUs/month. Dev usage likely stays within free tier.  
> The Essentials plan bundles SAML/OIDC federation — no separate per-MAU surcharge.

---

### 9. AWS CloudTrail (Activity Data Source)

| Component | Unit Price | Dev/Month | Prod/Month |
|---|---|---|---|
| Event History (90-day lookup) | Free | $0 | $0 |
| Management events (first trail copy) | Free | $0 | $0 |
| Additional trail copies | $2.00/100K events | $0 | ~$2.00 |
| **Subtotal** | | **$0** | **~$2.00** |

> The dashboard uses `lookup_events` API against the free 90-day Event History.  
> No additional paid trail is required for standard usage.

---

### 10. Amazon SNS (Threshold Alerts)

| Component | Unit Price | Dev/Month | Prod/Month |
|---|---|---|---|
| Publishes | $0.50/1M (first 1M free) | Minimal | ~1K = **$0.001** |
| Email deliveries | $2.00/100K (first 1K free) | Minimal | ~500 = **$0.01** |
| **Subtotal** | | **~$0** | **~$0.01** |

> SNS free tier: 1M publishes + 1K email deliveries/month. Alert volumes are negligible.

---

### 11. Data Transfer

| Component | Unit Price | Dev/Month | Prod/Month |
|---|---|---|---|
| Data transfer IN | Free | $0 | $0 |
| Data transfer OUT (first 100 GB) | Free | $0 | $0 |
| Data transfer OUT (beyond 100 GB) | $0.09/GB | $0 | ~$9.00 |
| Inter-AZ (RDS Multi-AZ replication) | $0.01/GB | $0 | ~$1.00 |
| **Subtotal** | | **$0** | **~$10.00** |

---

## Monthly Cost Summary

| Service | Dev/Month | Prod/Month |
|---|---|---|
| CloudFront | $0.48 | $47.50 |
| S3 (static hosting) | $0.01 | $0.01 |
| ECS Fargate | $18.02 | $72.08 |
| ALB | $22.27 | $33.95 |
| RDS PostgreSQL | $54.86 | $223.20 |
| ECR | $0.20 | $0.50 |
| Bedrock (Claude Sonnet 3.7) | $16.21 | $81.00 |
| Cognito | $0.15 | $1.50 |
| CloudTrail | $0.00 | $2.00 |
| SNS | $0.00 | $0.01 |
| Data Transfer | $0.00 | $10.00 |
| **Total Estimated** | **~$112.20** | **~$471.75** |

---

## Annual Cost Projection

| Environment | Monthly | Annual |
|---|---|---|
| Dev | ~$112 | ~$1,344 |
| Production | ~$472 | ~$5,664 |
| **Combined** | **~$584** | **~$7,008** |

---

## Cost Optimization Opportunities

### High Impact

| Optimization | Savings | Effort |
|---|---|---|
| Switch AI Agent to **Claude Haiku 3** | ~$15/mo dev, ~$75/mo prod | Low — change 1 env var |
| Use **Fargate Spot** for dev | ~$12/mo | Low — CDK config change |
| **RDS Reserved Instance** (1-year, no upfront) | ~$22/mo dev, ~$89/mo prod | Low — AWS console |
| Remove ALB in dev (use direct ECS endpoint) | ~$22/mo | Medium |

### Medium Impact

| Optimization | Savings | Effort |
|---|---|---|
| **Compute Savings Plan** (1-year) for Fargate | ~20% on compute | Low |
| Cache CloudTrail responses in Redis/ElastiCache | Reduces Bedrock context build time + cost | Medium |
| Implement **Bedrock prompt caching** | Up to 90% on repeated context | Medium |
| **RDS Multi-AZ** only in prod (already done) | Avoids $100+/mo in dev | Done |

### With Optimizations Applied

| Environment | Current | Optimized | Savings |
|---|---|---|---|
| Dev | ~$112/mo | ~$65/mo | ~42% |
| Production | ~$472/mo | ~$308/mo | ~35% |

---

## Key Cost Drivers

1. **RDS PostgreSQL** is the largest cost (~49% of dev, ~47% of prod). Consider Aurora Serverless v2 for dev to pay only when active.
2. **ALB** is disproportionately expensive for low-traffic dev (~20% of dev cost). Consider removing it in dev.
3. **Bedrock (Claude Sonnet 3.7)** costs scale with usage. Switching to Haiku 3 for routine queries is the fastest cost reduction.
4. **ECS Fargate** is reasonable but Fargate Spot can cut it by 70% for non-production.

---

## Assumptions

- **Region**: us-west-2 (Oregon)
- **Dev**: 1 developer, ~50 AI Agent queries/month, 1 Fargate task (0.5 vCPU / 1 GB), db.t3.medium Single-AZ
- **Production**: 50–100 users, ~500 AI Agent queries/month, 2 Fargate tasks (1 vCPU / 2 GB each), db.t3.large Multi-AZ
- **AI model**: Claude Sonnet 3.7 at $3.00/$15.00 per 1M input/output tokens (shares `Claude3Sonnet` pricing SKU in AWS Pricing API)
- **CloudTrail**: Free 90-day Event History via `lookup_events` API (no paid trail)
- **Cognito**: Essentials plan with SAML/OIDC federation bundled at $0.015/MAU flat
- **All prices**: On-Demand, no Reserved Instances or Savings Plans unless noted

---

## Verified Prices (AWS Pricing API, March 16, 2026)

| Service | SKU / Usage Type | Verified Price |
|---|---|---|
| RDS PostgreSQL db.t3.medium Single-AZ | `db.t3.medium` us-west-2 | $0.0720/hr |
| CloudFront HTTPS requests | US region | $0.0000010/request |
| S3 Standard storage | us-west-2, first 50 TB | $0.023/GB-Mo |
| ALB hours | us-west-2 | $0.0225/hr |
| ALB LCU hours | us-west-2 | $0.008/LCU-hr |
| ECR storage | us-west-2 | $0.10/GB-Mo |
| SNS publishes | us-west-2 | $0.50/1M (first 1M free) |
| SNS email | us-west-2 | $2.00/100K (first 1K free) |
| Cognito Essentials MAU | us-west-2 | $0.015/MAU (SAML included) |
| ECS Fargate vCPU | `USW2-Fargate-vCPU-Hours:perCPU` | $0.04048/hr |
| ECS Fargate memory | `USW2-Fargate-GB-Hours` | $0.004445/hr |
| Bedrock Claude Sonnet 3.7 input | `USW2-Claude3Sonnet-input-tokens` | $0.003/1K = $3.00/1M |
| Bedrock Claude Haiku 3 input | `USW2-Claude3Haiku-input-tokens` | $0.00025/1K = $0.25/1M |

---

## References

- [AWS Fargate Pricing](https://aws.amazon.com/fargate/pricing/) — $0.04048/vCPU-hr, $0.004445/GB-hr (us-west-2)
- [Amazon CloudFront Pricing](https://aws.amazon.com/cloudfront/pricing/) — $0.085/GB, $0.0000010/HTTPS request
- [Amazon S3 Pricing](https://aws.amazon.com/s3/pricing/) — $0.023/GB Standard storage
- [Amazon RDS PostgreSQL Pricing](https://aws.amazon.com/rds/postgresql/pricing/) — db.t3.medium $0.0720/hr Single-AZ
- [Elastic Load Balancing Pricing](https://aws.amazon.com/elasticloadbalancing/pricing/) — $0.0225/ALB-hr, $0.008/LCU-hr
- [Amazon ECR Pricing](https://aws.amazon.com/ecr/pricing/) — $0.10/GB-month
- [Amazon Bedrock Pricing](https://aws.amazon.com/bedrock/pricing/) — Claude Sonnet 3.7: $3.00/$15.00 per 1M tokens
- [Amazon Cognito Pricing](https://aws.amazon.com/cognito/pricing/) — Essentials: $0.015/MAU (SAML/OIDC included)
- [AWS CloudTrail Pricing](https://aws.amazon.com/cloudtrail/pricing/) — Event History free, additional trails $2.00/100K events
- [Amazon SNS Pricing](https://aws.amazon.com/sns/pricing/) — $0.50/1M publishes

*Prices verified against AWS Pricing API on March 16, 2026. Subject to change.*
