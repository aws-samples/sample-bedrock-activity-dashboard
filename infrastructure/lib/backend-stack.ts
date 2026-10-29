import * as cdk from "aws-cdk-lib";
import * as apigateway from "aws-cdk-lib/aws-apigateway";
import * as apigatewayv2 from "aws-cdk-lib/aws-apigatewayv2";
import * as cloudwatch from "aws-cdk-lib/aws-cloudwatch";
import * as cwActions from "aws-cdk-lib/aws-cloudwatch-actions";
import * as ec2 from "aws-cdk-lib/aws-ec2";
import * as ecr from "aws-cdk-lib/aws-ecr";
import * as ecs from "aws-cdk-lib/aws-ecs";
import * as ecsPatterns from "aws-cdk-lib/aws-ecs-patterns";
import * as elbv2 from "aws-cdk-lib/aws-elasticloadbalancingv2";
import * as logs from "aws-cdk-lib/aws-logs";
import * as secretsmanager from "aws-cdk-lib/aws-secretsmanager";
import * as iam from "aws-cdk-lib/aws-iam";
import * as rds from "aws-cdk-lib/aws-rds";
import * as cognito from "aws-cdk-lib/aws-cognito";
import * as codebuild from "aws-cdk-lib/aws-codebuild";
import * as s3 from "aws-cdk-lib/aws-s3";
import * as scheduler from "aws-cdk-lib/aws-scheduler";
import * as schedulerTargets from "aws-cdk-lib/aws-scheduler-targets";
import * as sns from "aws-cdk-lib/aws-sns";
import * as snsSubs from "aws-cdk-lib/aws-sns-subscriptions";
import { Construct } from "constructs";

export interface BackendStackProps extends cdk.StackProps {
  environment: string;
  desiredCount: number;
  vpc: ec2.IVpc;
  dbInstance: rds.DatabaseInstance;
  dbSecret: secretsmanager.ISecret;
  dbSecurityGroup: ec2.SecurityGroup;
  ecrRepository: ecr.Repository;
  /**
   * Shared S3 bucket for AWS-service access logs. Owned by the Data stack
   * (lives longer than the Backend stack); the Backend stack writes the
   * ALB access logs here under the ``alb/`` prefix.
   */
  accessLogsBucket: s3.IBucket;
  userPool: cognito.IUserPool;
  userPoolClient: cognito.IUserPoolClient;
  /** Cognito hosted UI URL (e.g., https://<prefix>.auth.<region>.amazoncognito.com) */
  userPoolDomainUrl: string;
  /** Optional public URL of the deployed frontend (CloudFront URL). Added to CORS_ORIGINS when set. */
  frontendUrl?: string;
  /**
   * Optional Cognito federated identity provider name. When set, exposed to
   * the SPA via /api/health/config as `identityProvider` and passed to the
   * hosted UI's /oauth2/authorize call as `identity_provider`, deep-linking
   * users into that IdP (SAML, OIDC, or IAM Identity Center) instead of the
   * hosted UI account chooser. Only meaningful on imported pools that have
   * a federated IdP configured (see the BYO path in data-stack.ts). Empty
   * default preserves the standard hosted-UI login flow.
   */
  cognitoIdentityProvider?: string;
  /**
   * Optional comma-separated list of ADDITIONAL regions (beyond the deploy
   * region, which is always included automatically) for CloudTrailService
   * to fan Bedrock activity ingestion out across (F1 — multi-region
   * ingestion + region filter). E.g. "us-east-1" or "us-east-1,eu-west-1".
   * Each region must have Bedrock model invocation logging enabled
   * separately — see infrastructure/enable-bedrock-logging-all-regions.sh.
   * Empty/unset keeps single-region (deploy-region-only) behavior.
   */
  cloudtrailRegions?: string;
}

export class BackendStack extends cdk.Stack {
  /**
   * API Gateway invoke hostname (no scheme, no stage, no trailing slash),
   * e.g. ``abcd1234.execute-api.us-west-2.amazonaws.com``. The frontend
   * stack uses this as the CloudFront ``/api/*`` origin.
   */
  public readonly apiEndpoint: string;
  /** API Gateway stage name (e.g. ``dev``). CloudFront uses this as ``originPath``. */
  public readonly apiStage: string;
  public readonly backendSecurityGroup: ec2.ISecurityGroup;

  constructor(scope: Construct, id: string, props: BackendStackProps) {
    super(scope, id, props);

    // NOTE: The CodeBuild project used to build and push the backend Docker
    // image is fully managed by infrastructure/build-and-deploy-backend.sh,
    // not by this stack. Keeping it out of CDK avoids a name collision on
    // fresh-account deploys (the script creates the project before CDK runs
    // so the script has an image in ECR before the ECS service starts).

    // ECS Cluster
    const cluster = new ecs.Cluster(this, "BackendCluster", {
      vpc: props.vpc,
      containerInsights: props.environment === "prod",
    });

    // Task execution role
    const taskRole = new iam.Role(this, "BackendTaskRole", {
      assumedBy: new iam.ServicePrincipal("ecs-tasks.amazonaws.com"),
    });

    // Grant least-privilege permissions
    taskRole.addToPolicy(
      new iam.PolicyStatement({
        effect: iam.Effect.ALLOW,
        actions: ["cloudtrail:LookupEvents"],
        resources: ["*"],
      })
    );

    taskRole.addToPolicy(
      new iam.PolicyStatement({
        effect: iam.Effect.ALLOW,
        actions: [
          "logs:FilterLogEvents",
          "logs:GetLogEvents",
          "logs:DescribeLogGroups",
        ],
        resources: ["*"],
      })
    );

    // Bedrock model invocations — Agent Chat, demo generation, and V2
    // agent fallback paths. Scoped to foundation models + inference profiles
    // across all regions (cross-region inference profiles can transparently
    // execute in a different region than the caller's; see steering).
    taskRole.addToPolicy(
      new iam.PolicyStatement({
        sid: "BedrockModelInvoke",
        effect: iam.Effect.ALLOW,
        actions: [
          "bedrock:InvokeModel",
          "bedrock:InvokeModelWithResponseStream",
          "bedrock:Converse",
          "bedrock:ConverseStream",
          "bedrock:InvokeInlineAgent",
        ],
        resources: [
          "arn:aws:bedrock:*::foundation-model/*",
          "arn:aws:bedrock:*:*:inference-profile/*",
          "arn:aws:bedrock:*:*:application-inference-profile/*",
        ],
      })
    );

    // Bedrock guardrail management + read — scoped to guardrail ARNs. These
    // actions are destructive (CreateGuardrailVersion, UpdateGuardrail,
    // DeleteGuardrail), so wildcarding the resource would let the backend
    // modify or delete any guardrail in the account.
    taskRole.addToPolicy(
      new iam.PolicyStatement({
        sid: "BedrockGuardrailManage",
        effect: iam.Effect.ALLOW,
        actions: [
          "bedrock:CreateGuardrail",
          "bedrock:CreateGuardrailVersion",
          "bedrock:UpdateGuardrail",
          "bedrock:GetGuardrail",
          "bedrock:DeleteGuardrail",
          "bedrock:ApplyGuardrail",
        ],
        resources: ["arn:aws:bedrock:*:*:guardrail/*"],
      })
    );

    // bedrock:ListGuardrails is a list API with no scopeable resource.
    taskRole.addToPolicy(
      new iam.PolicyStatement({
        sid: "BedrockGuardrailList",
        effect: iam.Effect.ALLOW,
        actions: ["bedrock:ListGuardrails"],
        resources: ["*"],
      })
    );

    // AgentCore Runtime invocation — scoped to runtimes in THIS account.
    taskRole.addToPolicy(
      new iam.PolicyStatement({
        sid: "AgentCoreRuntimeInvoke",
        effect: iam.Effect.ALLOW,
        actions: ["bedrock-agentcore:InvokeAgentRuntime"],
        resources: [`arn:aws:bedrock-agentcore:*:${this.account}:runtime/*`],
      })
    );

    // Account-level read-only check for model-invocation logging config.
    taskRole.addToPolicy(
      new iam.PolicyStatement({
        sid: "BedrockReadLoggingConfig",
        effect: iam.Effect.ALLOW,
        actions: ["bedrock:GetModelInvocationLoggingConfiguration"],
        resources: ["*"],
      })
    );

    taskRole.addToPolicy(
      new iam.PolicyStatement({
        effect: iam.Effect.ALLOW,
        actions: [
          "s3:GetObject",
          "s3:ListBucket",
          "s3:HeadBucket",
        ],
        resources: ["*"],
      })
    );

    taskRole.addToPolicy(
      new iam.PolicyStatement({
        effect: iam.Effect.ALLOW,
        actions: ["sts:GetCallerIdentity"],
        resources: ["*"],
      })
    );

    // AWS Price List API — used by PricingSyncService to sync Bedrock
    // foundation-model on-demand token prices. The Price List API does not
    // support resource-level permissions, so resources must be "*".
    taskRole.addToPolicy(
      new iam.PolicyStatement({
        effect: iam.Effect.ALLOW,
        actions: ["pricing:GetProducts", "pricing:DescribeServices"],
        resources: ["*"],
      })
    );

    // sns:Publish is granted on the specific alertTopic ARN only, via
    // alertTopic.grantPublish(taskRole) further below (right after the topic
    // is constructed). No account-wide SNS publish.

    // Profile-tag resolution for inference-profile attribution (Req 4.2).
    // Granted regardless of CUR config so live tag lookups still work.
    taskRole.addToPolicy(
      new iam.PolicyStatement({
        effect: iam.Effect.ALLOW,
        actions: ["bedrock:ListTagsForResource"],
        resources: ["arn:aws:bedrock:*:*:application-inference-profile/*"],
      })
    );

    // Demo Data "tagged application spend" option: lets the backend create
    // (or reuse) a small set of its OWN application inference profiles,
    // tagged with Application/Environment/Team, and invoke through them so
    // Cost Governance's Spend-by-Application view has non-"unknown" data to
    // show.
    // Scoped to inference-profile resources only — this does NOT grant the
    // ability to create/modify arbitrary account resources. Per the AWS
    // Bedrock inference-profile prerequisites, CreateInferenceProfile DOES
    // support resource-level permissions: creating an application inference
    // profile references a source foundation-model (single region) or a
    // system-defined inference-profile (multi region) and produces an
    // application-inference-profile, so the action is scoped to those three
    // ARN patterns rather than "*".
    // https://docs.aws.amazon.com/bedrock/latest/userguide/inference-profiles-prereq.html
    taskRole.addToPolicy(
      new iam.PolicyStatement({
        effect: iam.Effect.ALLOW,
        actions: ["bedrock:CreateInferenceProfile"],
        resources: [
          "arn:aws:bedrock:*::foundation-model/*",
          "arn:aws:bedrock:*:*:inference-profile/*",
          "arn:aws:bedrock:*:*:application-inference-profile/*",
        ],
      })
    );
    taskRole.addToPolicy(
      new iam.PolicyStatement({
        effect: iam.Effect.ALLOW,
        actions: [
          "bedrock:GetInferenceProfile",
          "bedrock:ListInferenceProfiles",
          "bedrock:DeleteInferenceProfile",
          "bedrock:TagResource",
        ],
        resources: ["arn:aws:bedrock:*:*:application-inference-profile/*"],
      })
    );

    // AgentCore Runtime consumption cost (live CloudWatch metrics, no
    // Athena/CUR dependency). GetMetricData does not support resource-level
    // permissions, so resources must be "*"; read-only.
    taskRole.addToPolicy(
      new iam.PolicyStatement({
        effect: iam.Effect.ALLOW,
        actions: ["cloudwatch:GetMetricData"],
        resources: ["*"],
      })
    );

    // AgentCore Runtime discovery: account-wide, read-only visibility scan
    // (AgentCoreDiscoveryService). ListAgentRuntimes/GetAgentRuntime do not
    // support resource-level permissions, so resources must be "*". This is
    // intentionally broader than the governed-agent permissions above — it
    // can see ANY AgentCore Runtime in a scanned region, not just the 14
    // this dashboard governs. Read-only; never creates, updates, or invokes.
    taskRole.addToPolicy(
      new iam.PolicyStatement({
        effect: iam.Effect.ALLOW,
        actions: [
          "bedrock-agentcore:ListAgentRuntimes",
          "bedrock-agentcore:GetAgentRuntime",
        ],
        resources: ["*"],
      })
    );

    // ---- CUR 2.0 / Athena billed-cost ingestion (least-privilege) ----
    // Scope the Athena/Glue/S3 permissions tightly, and only attach them when a
    // CUR database is configured in the deploy environment. Absent config =>
    // no CUR permissions are granted (mirrors the CloudFormation
    // HasAthenaConfig condition and preserves graceful degradation).
    const athenaDatabase = process.env.ATHENA_DATABASE ?? "";
    const athenaTable = process.env.ATHENA_TABLE ?? "";
    const athenaWorkgroup = process.env.ATHENA_WORKGROUP ?? "primary";
    const curExportBucket = process.env.CUR_EXPORT_BUCKET ?? "";
    const curExportPrefix = process.env.CUR_EXPORT_PREFIX ?? "";
    const athenaResultsBucket = process.env.ATHENA_RESULTS_BUCKET ?? "";
    const athenaResultsPrefix = process.env.ATHENA_RESULTS_PREFIX ?? "";

    if (athenaDatabase !== "" && athenaTable !== "") {
      // Athena query lifecycle, scoped to the workgroup
      taskRole.addToPolicy(
        new iam.PolicyStatement({
          effect: iam.Effect.ALLOW,
          actions: [
            "athena:StartQueryExecution",
            "athena:GetQueryExecution",
            "athena:GetQueryResults",
            "athena:StopQueryExecution",
          ],
          resources: [`arn:aws:athena:*:*:workgroup/${athenaWorkgroup}`],
        })
      );

      // Glue catalog metadata for the CUR database/table
      taskRole.addToPolicy(
        new iam.PolicyStatement({
          effect: iam.Effect.ALLOW,
          actions: ["glue:GetTable", "glue:GetDatabase", "glue:GetPartitions"],
          resources: [
            "arn:aws:glue:*:*:catalog",
            `arn:aws:glue:*:*:database/${athenaDatabase}`,
            `arn:aws:glue:*:*:table/${athenaDatabase}/${athenaTable}`,
          ],
        })
      );

      // CUR export prefix — Athena reads the Parquet in place with the
      // caller's credentials, so it also needs s3:GetBucketLocation.
      if (curExportBucket !== "") {
        taskRole.addToPolicy(
          new iam.PolicyStatement({
            effect: iam.Effect.ALLOW,
            actions: ["s3:GetObject", "s3:ListBucket", "s3:GetBucketLocation"],
            resources: [
              `arn:aws:s3:::${curExportBucket}`,
              `arn:aws:s3:::${curExportBucket}/${curExportPrefix}*`,
            ],
          })
        );
      }

      // Athena query-results bucket/prefix — Athena verifies and writes with
      // the caller's credentials, which requires s3:GetBucketLocation plus the
      // multipart-upload actions in addition to Get/Put/List
      // (https://docs.aws.amazon.com/athena/latest/ug/s3-permissions.html).
      if (athenaResultsBucket !== "") {
        taskRole.addToPolicy(
          new iam.PolicyStatement({
            effect: iam.Effect.ALLOW,
            actions: [
              "s3:GetObject",
              "s3:PutObject",
              "s3:ListBucket",
              "s3:GetBucketLocation",
              "s3:ListBucketMultipartUploads",
              "s3:ListMultipartUploadParts",
              "s3:AbortMultipartUpload",
            ],
            resources: [
              `arn:aws:s3:::${athenaResultsBucket}`,
              `arn:aws:s3:::${athenaResultsBucket}/${athenaResultsPrefix}*`,
            ],
          })
        );
      }
    }


    // Grant read access to DB secret
    props.dbSecret.grantRead(taskRole);

    // Prompt-history at-rest encryption key. Owned by this stack rather
    // than the Data stack because it's purely a backend concern and never
    // leaves the ECS task. generateSecretString produces a 64-char random
    // alphanumeric string; the backend accepts any material and SHA-256
    // derives a 32-byte key when the raw value isn't a native Fernet key
    // (see crypto_service.PromptHistoryCrypto._build_fernet), so the random
    // bytes here work directly. Rotate by replacing the Secrets Manager
    // value — ciphertext written under the old key renders as a
    // placeholder for anything the new key can no longer decrypt.
    const promptHistoryKeySecret = new secretsmanager.Secret(
      this,
      "PromptHistoryEncryptionKey",
      {
        description:
          "Key material used to encrypt Agent Chat prompt/response text at rest",
        generateSecretString: {
          passwordLength: 64,
          excludePunctuation: true,
          includeSpace: false,
        },
      }
    );
    promptHistoryKeySecret.grantRead(taskRole);

    // Build DATABASE_URL from secret and endpoint
    const dbEndpoint = props.dbInstance.dbInstanceEndpointAddress;
    const dbPort = props.dbInstance.dbInstanceEndpointPort;

    // ALB-backed Fargate service using ECR image.
    //
    // Deployment safety:
    //
    //   circuitBreaker: { rollback: true } tells ECS to AUTOMATICALLY
    //   cancel a stuck deployment and roll back to the previous task
    //   definition if the new task fails to reach steady state. Without
    //   this the service happily keeps spawning crash-looping tasks
    //   forever (we watched exactly this happen during an earlier deploy
    //   on this project — 15+ minutes of task placement, health-check
    //   pass, 40-second-later deregister cycle because the new image
    //   crashed on startup). The circuit breaker makes that failure mode
    //   observable in <5 minutes and self-heals back to the known-good
    //   revision instead of leaving the stack UPDATE_IN_PROGRESS.
    //
    //   minHealthyPercent: 100 keeps the old task running until the new
    //   one is fully healthy, preventing the brief Running: 0 window the
    //   default (50% with desiredCount=1 = can go to zero) would allow.
    //   maxHealthyPercent: 200 gives ECS headroom to start the new task
    //   alongside the old before draining. The warning CDK emits about
    //   minHealthyPercent being unset is also silenced.
    //
    //   Both settings are strongly recommended by ECS reference
    //   architectures for ALB-fronted services and have zero cost impact
    //   beyond the brief ~2x task overlap during a rolling deploy.
    const fargateService = new ecsPatterns.ApplicationLoadBalancedFargateService(
      this,
      "BackendService",
      {
        cluster,
        desiredCount: props.desiredCount,
        cpu: 512,
        memoryLimitMiB: 1024,
        idleTimeout: cdk.Duration.seconds(300),
        circuitBreaker: { rollback: true },
        minHealthyPercent: 100,
        maxHealthyPercent: 200,
        taskImageOptions: {
          image: ecs.ContainerImage.fromEcrRepository(props.ecrRepository, "latest"),
          containerPort: 8000,
          taskRole,
          environment: {
            AWS_REGION: cdk.Stack.of(this).region,
            DATABASE_HOST: dbEndpoint,
            DATABASE_PORT: dbPort,
            DATABASE_NAME: "bedrock_dashboard",
            BEDROCK_MODEL_ID: "us.anthropic.claude-haiku-4-5-20251001-v1:0",
            // Inject the invocation-log S3 destination that Step 3.5 of
            // build-and-deploy-backend.sh configures. Matching values here
            // means the backend never has to guess or race the put-config
            // API call on a fresh deploy — it just reads env vars and
            // trusts them. Bucket name follows the deploy-script pattern
            // (bedrock-invocation-logs-<account>); prefix mirrors the
            // keyPrefix Step 3.5 sets (bedrock-logs/<region>).
            BEDROCK_S3_LOG_BUCKET: `bedrock-invocation-logs-${cdk.Stack.of(this).account}`,
            BEDROCK_S3_LOG_KEY_PREFIX: `bedrock-logs/${cdk.Stack.of(this).region}`,
            // Localhost is always allowed for dev; when a frontend URL is
            // known at deploy time we add it, but production traffic hits the
            // API through the CloudFront proxy (same origin) so CORS is not
            // actually exercised in prod.
            CORS_ORIGINS: [
              props.frontendUrl,
              "http://localhost:5173",
              "http://localhost:3000",
            ]
              .filter(Boolean)
              .join(","),
            APP_NAME: "Bedrock Activity Dashboard",
            // Regions CloudTrailService fans ingestion out across (F1). The
            // deploy region is always included and listed first (it's also
            // the default single-region fallback if this ever ends up
            // empty). Additional regions can be layered in via CDK context
            // (`-c cloudtrailRegions=us-east-1,eu-west-1`) — each one MUST
            // have Bedrock model invocation logging enabled separately (see
            // infrastructure/enable-bedrock-logging-all-regions.sh) or its
            // ingestion will simply return no records, not an error.
            // De-duplicated so a deploy TO us-east-1 with the "us-east-1"
            // default additional region doesn't produce "us-east-1,us-east-1".
            CLOUDTRAIL_REGIONS: Array.from(
              new Set(
                [cdk.Stack.of(this).region, ...(props.cloudtrailRegions ?? "").split(",")]
                  .map((r) => r.trim())
                  .filter(Boolean)
              )
            ).join(","),
            BEDROCK_REGION: cdk.Stack.of(this).region,
            // Always-on request-time synthetic injection is disabled. Demo data
            // is now generated on demand by an admin and persisted to the
            // demo_activity_records table (see /api/demo).
            DEMO_MODE: "false",
            // Cognito config comes from the Data stack (User Pool + SPA
            // client) so nothing is pinned to a specific AWS account.
            COGNITO_USER_POOL_ID: props.userPool.userPoolId,
            COGNITO_CLIENT_ID: props.userPoolClient.userPoolClientId,
            COGNITO_REGION: cdk.Stack.of(this).region,
            COGNITO_DOMAIN: props.userPoolDomainUrl,
            // Empty default when unset — SPA falls back to the standard
            // hosted-UI account chooser. See BackendStackProps for details.
            COGNITO_IDENTITY_PROVIDER: props.cognitoIdentityProvider ?? "",
            AUTH_ENABLED: process.env.AUTH_ENABLED ?? "true",
            // ---- CUR 2.0 / Athena billed-cost ingestion (all optional) ----
            // Read from the deploy environment; empty/unset => the backend
            // degrades gracefully to estimated-only cost figures. The app never
            // configures these AWS prerequisites, it only consumes them.
            ATHENA_DATABASE: process.env.ATHENA_DATABASE ?? "",
            ATHENA_TABLE: process.env.ATHENA_TABLE ?? "",
            ATHENA_WORKGROUP: process.env.ATHENA_WORKGROUP ?? "primary",
            ATHENA_OUTPUT_S3: process.env.ATHENA_OUTPUT_S3 ?? "",
            ATHENA_REGION: cdk.Stack.of(this).region,
            CUR_STALENESS_HOURS: process.env.CUR_STALENESS_HOURS ?? "48",
            CUR_INGEST_ENABLED: process.env.CUR_INGEST_ENABLED ?? "true",
            // ---- AgentCore Runtime consumption cost tracking (optional) ----
            // Computed live from CloudWatch, no Athena/CUR dependency. Rates
            // default to the published on-demand prices and are overridable.
            AGENTCORE_COST_TRACKING_ENABLED:
              process.env.AGENTCORE_COST_TRACKING_ENABLED ?? "true",
            AGENTCORE_VCPU_HOURLY_RATE: process.env.AGENTCORE_VCPU_HOURLY_RATE ?? "0.0895",
            AGENTCORE_MEMORY_GB_HOURLY_RATE:
              process.env.AGENTCORE_MEMORY_GB_HOURLY_RATE ?? "0.00945",
            // ---- AgentCore Runtime discovery (account-wide, read-only) ----
            AGENTCORE_DISCOVERY_ENABLED: process.env.AGENTCORE_DISCOVERY_ENABLED ?? "true",
            AGENTCORE_DISCOVERY_REGIONS:
              process.env.AGENTCORE_DISCOVERY_REGIONS ?? "us-west-2,us-east-1",
          },
          secrets: {
            DB_SECRET_ARN: ecs.Secret.fromSecretsManager(props.dbSecret),
            // Fernet key for Agent Chat at-rest encryption. See
            // PromptHistoryEncryptionKey above; backend reads it as
            // settings.prompt_history_encryption_key.
            PROMPT_HISTORY_ENCRYPTION_KEY: ecs.Secret.fromSecretsManager(
              promptHistoryKeySecret
            ),
          },
        },
        // ALB privacy. The ALB is INTERNAL (private subnets only, no
        // public IP, not routable from the internet). API Gateway sits in
        // front and talks to this ALB over a VPC Link v2 — see the API
        // Gateway block below. ``openListener: false`` prevents the
        // ApplicationLoadBalancedFargateService pattern from auto-adding
        // the usual ``0.0.0.0/0 -> :80`` ingress rule; we add a scoped
        // rule allowing only the VPC Link SG below. ``publicLoadBalancer:
        // false`` also flips the ALB subnet selection from public to
        // private-with-egress automatically.
        publicLoadBalancer: false,
        openListener: false,
      }
    );

    // CDK issue #847 workaround. Changing the ALB scheme
    // (internet-facing -> internal) is a logical replacement — CFN
    // provisions the new ALB first, then tries to attach the EXISTING
    // target group to it. That fails because a target group can only be
    // bound to one ALB. Forcing a new logical ID on the target group
    // makes CFN create a fresh target group alongside the new ALB; the
    // old target group is deleted once nothing references it. The ECS
    // service is replaced too because its load-balancer reference
    // (TargetGroupArn) changes — expect ~1-2 min API unavailability
    // during the one-time cutover.
    // The logical ID is a stable CloudFormation identifier — do NOT
    // rename it, as renaming would trigger another target-group
    // replacement (and therefore an ALB replacement) on the next deploy.
    (fargateService.targetGroup.node.defaultChild as elbv2.CfnTargetGroup)
      .overrideLogicalId("BackendServiceTargetGroupF3");

    // Health check configuration
    fargateService.targetGroup.configureHealthCheck({
      path: "/health",
      healthyHttpCodes: "200",
    });

    // ALB access logs — written by the AWS ELB service account into the
    // shared Data-stack access-logs bucket under the `alb/` prefix.
    // Required for incident response and most compliance frameworks
    // (HIPAA, PCI DSS 10.x) and genuinely useful for debugging a stuck
    // deploy — same reason this project added them to its own
    // troubleshooting runbook. logAccessLogs() is non-destructive: it
    // flips the ELB attributes and adds the bucket policy statement the
    // service account needs. Prefix is scoped so the same bucket can
    // later also receive CloudFront logs without collision.
    fargateService.loadBalancer.logAccessLogs(props.accessLogsBucket, "alb");

    // Allow Fargate tasks to connect to RDS on port 5432
    this.backendSecurityGroup = fargateService.service.connections.securityGroups[0];
    props.dbSecurityGroup.addIngressRule(
      ec2.Peer.ipv4(props.vpc.vpcCidrBlock),
      ec2.Port.tcp(5432),
      "Allow VPC traffic to connect to RDS"
    );

    // -----------------------------------------------------------------
    // API Gateway in front of internal ALB (VPC Link v2 private
    // integration).
    //
    // Trust graph after this block:
    //
    //   CloudFront ---HTTPS---> API Gateway (execute-api default cert)
    //       |
    //       +-- Cognito user-pool authorizer on /{proxy+} ANY
    //       +-- GET /api/health/config unauthenticated (login page needs it)
    //       |
    //       --- VPC Link v2 (ENIs in private-with-egress subnets)
    //              -- vpcLinkSg SG (allows egress to ALB SG on :80 only)
    //                     |
    //                     v
    //                 Internal ALB (private-with-egress subnets)
    //                  -- albSg: :80 open only to vpcLinkSg
    //                  -- :80 listener forwards to Fargate target group
    //                     |
    //                     v
    //                 Fargate task SG (:8000 open only to albSg)
    //
    // Nothing on the public internet can hit the ALB anymore. Direct
    // ``curl http://<alb-dns>`` from any host outside the VPC fails
    // (unroutable). Agent Chat SSE streaming is preserved via
    // ``ResponseTransferMode: STREAM`` on the integration (REST APIs
    // only; HTTP APIs don't support streaming).
    // -----------------------------------------------------------------

    // Dedicated SG for the VPC Link v2 ENIs. API Gateway creates these
    // ENIs in the subnets we specify; this SG governs what they can
    // reach inside the VPC. Egress is intentionally restricted — the
    // VPC Link only needs to reach the ALB on port 80.
    const vpcLinkSg = new ec2.SecurityGroup(this, "ApiGwVpcLinkSg", {
      vpc: props.vpc,
      description:
        "API Gateway VPC Link v2 ENIs - egress to internal ALB on :80 only",
      allowAllOutbound: false,
    });

    // Tighten the ALB SG: close the default internet exposure and only
    // accept :80 from the VPC Link's SG. ``openListener: false`` above
    // already suppressed the auto-added 0.0.0.0/0 rule; this is the
    // replacement scoped rule.
    const albSg = fargateService.loadBalancer.connections.securityGroups[0];
    albSg.addIngressRule(
      vpcLinkSg,
      ec2.Port.tcp(80),
      "HTTP from API Gateway VPC Link v2 only"
    );
    // Mirror the egress on the VPC Link SG so the connection is
    // explicit at both ends.
    vpcLinkSg.connections.allowTo(
      albSg,
      ec2.Port.tcp(80),
      "to internal ALB"
    );

    // VPC Link v2. Created with ``AWS::ApiGatewayV2::VpcLink`` because
    // REST API ALB private integrations require v2 — the older v1 VPC
    // Link resource only supports NLB targets. The CDK stable
    // ``apigateway.VpcLink`` L2 is still v1-only, so we use the Cfn
    // resource directly here.
    //
    // Subnets: private-with-egress across every AZ the ALB uses. VPC
    // Link ENIs don't need internet egress themselves, but routing to
    // the ALB targets requires the same AZ coverage.
    //
    // Immutability caveat: AWS does NOT allow changing a VPC Link v2's
    // subnets or security groups after creation. If this ever needs to
    // change, the resource gets replaced (brief re-wire window on all
    // integrations referencing it).
    const vpcLink = new apigatewayv2.CfnVpcLink(this, "ApiGwVpcLink", {
      name: `bedrock-dashboard-${props.environment}`,
      subnetIds: props.vpc.selectSubnets({
        subnetType: ec2.SubnetType.PRIVATE_WITH_EGRESS,
      }).subnetIds,
      securityGroupIds: [vpcLinkSg.securityGroupId],
    });

    // Import the v2 VPC Link ID as the v1 L2 ``IVpcLink`` interface so
    // the standard REST API ``Integration`` ``vpcLink`` field accepts
    // it. ``fromVpcLinkId`` imports by string ID — it doesn't care
    // whether the underlying resource is v1 or v2.
    const importedVpcLink = apigateway.VpcLink.fromVpcLinkId(
      this,
      "ImportedApiGwVpcLink",
      vpcLink.ref
    );

    // Access log group for the REST API stage.
    const apiAccessLogs = new logs.LogGroup(this, "ApiGwAccessLogs", {
      logGroupName: `/aws/apigateway/bedrock-dashboard-${props.environment}`,
      retention: logs.RetentionDays.ONE_MONTH,
      removalPolicy:
        props.environment === "prod"
          ? cdk.RemovalPolicy.RETAIN
          : cdk.RemovalPolicy.DESTROY,
    });

    // Cognito user-pool authorizer. Validates the ``Authorization`` header
    // against the data stack's User Pool (ID tokens signed with RS256).
    // FastAPI STILL verifies the token after API Gateway accepts it — this
    // authorizer is defense in depth, not a replacement for app-side auth.
    //
    // We rebuild a lightweight ``IUserPool`` from the pool ID instead of
    // using ``props.userPool`` directly. ``CognitoUserPoolsAuthorizer``
    // reads ``userPoolArn`` off each pool it's given; using the Construct
    // reference would make CDK auto-generate a NEW cross-stack export in
    // the Data stack (``ExportsOutputFnGetAttUserPool...Arn``), and that
    // new export can't publish until the Data stack updates, which can't
    // happen if any OTHER Backend ↔ Data import is deadlocked during the
    // ALB-scheme cutover. ``fromUserPoolId`` synthesizes the ARN locally from
    // account + region + pool ID (all already available via the pool-ID
    // import), so no new cross-stack reference is introduced.
    const userPoolForAuthorizer = cognito.UserPool.fromUserPoolId(
      this,
      "UserPoolForAuthorizer",
      props.userPool.userPoolId
    );
    const cognitoAuthorizer = new apigateway.CognitoUserPoolsAuthorizer(
      this,
      "ApiCognitoAuthorizer",
      {
        cognitoUserPools: [userPoolForAuthorizer],
        authorizerName: `bedrock-dashboard-${props.environment}-cognito`,
        identitySource: "method.request.header.Authorization",
      }
    );

    // REST API. ``cloudWatchRole`` controls whether CDK creates and
    // sets the account-level API Gateway CloudWatch logging role via
    // ``UpdateAccount``. The stage below enables access logging, which
    // requires that account-level role to be set — otherwise the
    // stage fails to create with "CloudWatch Logs role ARN must be set
    // in account settings to enable logging".
    //
    // Default here is ``true`` (CDK owns the role, which is correct for
    // ~every greenfield account). Set ``SKIP_API_GW_LOGGING_ROLE=1`` at
    // deploy time if the target account already has an API Gateway
    // logging role owned by a DIFFERENT CDK app or stack — turning
    // ``cloudWatchRole`` on would overwrite it (API Gateway's logging
    // role is per-account, not per-API). In that opt-out case, this
    // stack's stage access logging piggybacks on the existing role.
    const api = new apigateway.RestApi(this, "BackendApi", {
      restApiName: `bedrock-dashboard-${props.environment}`,
      description:
        "REST API in front of the internal backend ALB.",
      cloudWatchRole: process.env.SKIP_API_GW_LOGGING_ROLE !== "1",
      endpointConfiguration: {
        types: [apigateway.EndpointType.REGIONAL],
      },
      // Default-off CORS — the ALB-backed FastAPI handles CORS itself,
      // and production traffic reaches the API through the same-origin
      // CloudFront proxy anyway.
      deployOptions: {
        stageName: props.environment,
        // Stage throttling. Limits cost-abuse risk on the agent /execute
        // endpoints (/api/v2/agents/*/execute can run tool-heavy agent
        // workflows). Values are generous for a dashboard with handful-of-
        // users; tighten for a public high-traffic API.
        throttlingBurstLimit: 100,
        throttlingRateLimit: 50,
        accessLogDestination: new apigateway.LogGroupLogDestination(apiAccessLogs),
        accessLogFormat: apigateway.AccessLogFormat.jsonWithStandardFields({
          caller: true,
          httpMethod: true,
          ip: true,
          protocol: true,
          requestTime: true,
          resourcePath: true,
          responseLength: true,
          status: true,
          user: true,
        }),
        // Error-level logging only — never enable ``dataTraceEnabled``
        // on a stage that carries prompt/response bodies. That setting
        // writes the request + response payloads verbatim to CloudWatch
        // Logs, which is a prompt-content disclosure vector.
        loggingLevel: apigateway.MethodLoggingLevel.ERROR,
        dataTraceEnabled: false,
      },
      // Pass bytes through for SSE streaming responses. Without this,
      // API Gateway can misinterpret the ``text/event-stream`` body as a
      // text payload that requires a specific content-type match.
      binaryMediaTypes: ["*/*"],
    });

    // Catch-all proxy integration — forwards every incoming
    // ``/{proxy+}`` request to the ALB on the same path.
    const proxyIntegration = new apigateway.Integration({
      type: apigateway.IntegrationType.HTTP_PROXY,
      integrationHttpMethod: "ANY",
      uri: `http://${fargateService.loadBalancer.loadBalancerDnsName}/{proxy}`,
      options: {
        connectionType: apigateway.ConnectionType.VPC_LINK,
        vpcLink: importedVpcLink,
        requestParameters: {
          "integration.request.path.proxy": "method.request.path.proxy",
        },
      },
    });

    // Specific integration for the unauth health-config path — same
    // backend, static URI (no ``{proxy}`` substitution).
    const healthConfigIntegration = new apigateway.Integration({
      type: apigateway.IntegrationType.HTTP_PROXY,
      integrationHttpMethod: "GET",
      uri: `http://${fargateService.loadBalancer.loadBalancerDnsName}/api/health/config`,
      options: {
        connectionType: apigateway.ConnectionType.VPC_LINK,
        vpcLink: importedVpcLink,
      },
    });

    // Applies two Cfn property overrides to a method's underlying
    // ``AWS::ApiGateway::Method`` resource:
    //
    //   1. ``Integration.IntegrationTarget`` — the ALB (load balancer)
    //      ARN. Required for REST API + v2 VPC Link + ALB target. The
    //      CFN schema docs describe this field as "the ALB or NLB
    //      listener to send the request to", but the API Gateway
    //      service actually validates for the load-balancer ARN format
    //      (``arn:aws:elasticloadbalancing:region:acct:loadbalancer/
    //      app/name/id``), NOT the listener ARN format (which adds a
    //      ``/listener-id`` segment at the end). Passing a listener
    //      ARN produces the misleading error "is not a valid ALB or
    //      NLB arn". The AWS announcement blog's CLI example also
    //      uses ``<your-ALB-arn>`` for this field — the CFN prose is
    //      inconsistent with the actual service behaviour. The CDK
    //      stable ``Integration`` class doesn't expose this field yet,
    //      so we inject it via ``addPropertyOverride``.
    //   2. ``Integration.ResponseTransferMode: STREAM`` — enables SSE /
    //      incremental response delivery for ``/api/agent/chat/stream``.
    //      REST API default is BUFFERED (API Gateway holds the full
    //      response before returning), which would break streaming.
    //      STREAM mode permits integration timeouts up to 15 minutes;
    //      Regional endpoint idle timeout is 5 minutes.
    //
    // ``TimeoutInMillis`` is also raised to the 15-minute max to cover
    // long-running Agent Chat sessions. The 29-second REST API default
    // would cut off any multi-step agent workflow mid-stream.
    const applyAlbIntegrationOverrides = (method: apigateway.Method) => {
      const cfnMethod = method.node.defaultChild as apigateway.CfnMethod;
      cfnMethod.addPropertyOverride(
        "Integration.IntegrationTarget",
        fargateService.loadBalancer.loadBalancerArn
      );
      cfnMethod.addPropertyOverride(
        "Integration.ResponseTransferMode",
        "STREAM"
      );
      cfnMethod.addPropertyOverride("Integration.TimeoutInMillis", 900_000);
    };

    // Unauthenticated: /api/health/config. The SPA calls this before the
    // user has a bearer token (it tells the SPA which Cognito pool to
    // redirect to). All other paths require a valid Cognito ID token.
    const apiRes = api.root.addResource("api");
    const healthRes = apiRes.addResource("health");
    const configRes = healthRes.addResource("config");
    const healthConfigMethod = configRes.addMethod(
      "GET",
      healthConfigIntegration,
      {
        authorizationType: apigateway.AuthorizationType.NONE,
      }
    );
    applyAlbIntegrationOverrides(healthConfigMethod);

    // Catch-all proxy, authenticated with Cognito. Specific routes above
    // (like /api/health/config) take precedence over this greedy proxy.
    const proxyResource = api.root.addResource("{proxy+}");
    const proxyMethod = proxyResource.addMethod("ANY", proxyIntegration, {
      authorizationType: apigateway.AuthorizationType.COGNITO,
      authorizer: cognitoAuthorizer,
      requestParameters: {
        "method.request.path.proxy": true,
      },
    });
    applyAlbIntegrationOverrides(proxyMethod);

    // Export hostname + stage separately so FrontendStack can configure
    // CloudFront with ``originPath = /<stage>`` and the hostname as the
    // origin domain. ``api.url`` is ``https://<id>.execute-api.<region>
    // .amazonaws.com/<stage>/`` — not directly usable as a CloudFront
    // origin domain.
    this.apiEndpoint = `${api.restApiId}.execute-api.${this.region}.amazonaws.com`;
    this.apiStage = props.environment;

    // ------------------------------------------------------------------
    // Alerting — SNS topic + minimum CloudWatch alarms.
    //
    // Three alarms with high signal-to-noise ratio: ALB target 5xx rate,
    // ECS running task count below desired, and RDS CPU sustained high.
    // All three are things an operator genuinely needs paged on; they
    // don't fire on normal operation. Each has a conservative
    // evaluation window (5 min x 2 datapoints = ~10 min) to avoid
    // flapping on transient hiccups.
    //
    // SNS subscription is OPT-IN: the topic is always created, but an
    // email subscription is added only when ALERT_EMAIL is set at
    // deploy time. Without it the topic exists silently and the operator
    // can add themselves via the Console. This keeps the deploy script
    // single-command for someone who just wants to try the sample
    // without receiving email.
    //
    // treatMissingData: NOT_BREACHING means "no datapoints = alarm OK",
    // which is the right default for the ALB 5xx alarm (no requests at
    // all = no 5xx) but we leave the ECS/RDS alarms using the SDK
    // default (MISSING) because missing datapoints on those usually
    // means the task is dead or the DB is unreachable — exactly the
    // state we want to know about.
    // ------------------------------------------------------------------
    // SNS alert topic for CloudWatch alarms. The task role gets sns:Publish
    // scoped to this topic ARN only (replaces the earlier broad grant that
    // allowed publish to any SNS topic in the account).
    const alertTopic = new sns.Topic(this, "AlertTopic", {
      displayName: `Bedrock Dashboard Alerts (${props.environment})`,
      topicName: `bedrock-dashboard-alerts-${props.environment}`,
    });

    const alertEmail = process.env.ALERT_EMAIL;
    if (alertEmail && alertEmail.trim() !== "") {
      alertTopic.addSubscription(new snsSubs.EmailSubscription(alertEmail.trim()));
    }

    alertTopic.grantPublish(taskRole);

    const alarmAction = new cwActions.SnsAction(alertTopic);

    // ALB target 5xx — anything the application returned as a server
    // error, or anything the ALB rejected on the application's behalf.
    // 5 across 10 minutes is a conservative threshold for a dashboard
    // with ~1-10 users; tighten for a public high-traffic service.
    const alb5xxAlarm = new cloudwatch.Alarm(this, "Alb5xxAlarm", {
      alarmName: `bedrock-dashboard-${props.environment}-alb-5xx`,
      alarmDescription:
        "ALB target 5XX responses - the backend task is returning errors.",
      metric: fargateService.loadBalancer.metrics.httpCodeTarget(
        elbv2.HttpCodeTarget.TARGET_5XX_COUNT,
        {
          period: cdk.Duration.minutes(5),
          statistic: cloudwatch.Stats.SUM,
        }
      ),
      threshold: 5,
      evaluationPeriods: 2,
      comparisonOperator:
        cloudwatch.ComparisonOperator.GREATER_THAN_OR_EQUAL_TO_THRESHOLD,
      treatMissingData: cloudwatch.TreatMissingData.NOT_BREACHING,
    });
    alb5xxAlarm.addAlarmAction(alarmAction);

    // Unhealthy host count on the target group — the ALB's direct view
    // of task health. Fires when a backend task fails its /health check
    // for 10+ minutes, which covers both crash loops (ECS keeps spawning
    // tasks but none pass health check) and the "task is up but
    // deadlocked" case (hits health-check port but doesn't return 200).
    // This is a more reliable signal than ECS service-level metrics
    // because it works without Container Insights and uses the same
    // health signal ECS uses to decide whether to replace a task.
    const unhealthyHostsAlarm = new cloudwatch.Alarm(
      this,
      "UnhealthyHostsAlarm",
      {
        alarmName: `bedrock-dashboard-${props.environment}-alb-unhealthy-targets`,
        alarmDescription:
          "ALB target group has unhealthy targets for 10+ minutes - the backend task is failing its /health check.",
        metric: fargateService.targetGroup.metrics.unhealthyHostCount({
          period: cdk.Duration.minutes(5),
          statistic: cloudwatch.Stats.AVERAGE,
        }),
        threshold: 1,
        evaluationPeriods: 2,
        comparisonOperator:
          cloudwatch.ComparisonOperator.GREATER_THAN_OR_EQUAL_TO_THRESHOLD,
        treatMissingData: cloudwatch.TreatMissingData.NOT_BREACHING,
      }
    );
    unhealthyHostsAlarm.addAlarmAction(alarmAction);

    // RDS CPU sustained high. 80% / 15-min threshold is the standard
    // "something is wrong, go investigate" signal — short bursts are
    // normal, 15 minutes above 80% is not.
    const rdsCpuAlarm = new cloudwatch.Alarm(this, "RdsCpuAlarm", {
      alarmName: `bedrock-dashboard-${props.environment}-rds-cpu`,
      alarmDescription:
        "RDS CPUUtilization has been above 80% for 15+ minutes.",
      metric: props.dbInstance.metricCPUUtilization({
        period: cdk.Duration.minutes(5),
        statistic: cloudwatch.Stats.AVERAGE,
      }),
      threshold: 80,
      evaluationPeriods: 3,
      comparisonOperator:
        cloudwatch.ComparisonOperator.GREATER_THAN_OR_EQUAL_TO_THRESHOLD,
      treatMissingData: cloudwatch.TreatMissingData.NOT_BREACHING,
    });
    rdsCpuAlarm.addAlarmAction(alarmAction);

    new cdk.CfnOutput(this, "AlertTopicArn", {
      value: alertTopic.topicArn,
      description:
        "SNS topic for CloudWatch alarms - subscribe additional endpoints as needed.",
    });

    // ---- Daily CUR billed-cost ingestion (EventBridge Scheduler -> ECS RunTask) ----
    // Runs daily at 09:00 UTC, after the 08:00 UTC Glue crawler has refreshed the
    // CUR table partitions. Reuses the backend service's task definition with a
    // container command override to the module entrypoint, which exits 0 on
    // success/skip (Athena unconfigured => graceful no-op) and 1 on failure so
    // failed runs are observable in ECS task history. Nothing is gated on the
    // Athena config — the entrypoint no-ops safely when unconfigured.
    // The EcsRunFargateTask target auto-creates the scheduler execution role
    // with ecs:RunTask on the task definition and iam:PassRole for the task and
    // execution roles (via TaskDefinition.grantRun).
    new scheduler.Schedule(this, "CurIngestionSchedule", {
      scheduleName: `bedrock-dashboard-cur-ingestion-${props.environment}`,
      description:
        "Daily CUR 2.0 billed-cost ingestion (python -m app.services.cur_ingestion_service)",
      schedule: scheduler.ScheduleExpression.cron({ minute: "0", hour: "9" }), // UTC
      target: new schedulerTargets.EcsRunFargateTask(cluster, {
        taskDefinition: fargateService.taskDefinition,
        // Same networking as the backend service: private subnets, the
        // service's security group, no public IP (Fargate default).
        vpcSubnets: { subnetType: ec2.SubnetType.PRIVATE_WITH_EGRESS },
        securityGroups: [fargateService.service.connections.securityGroups[0]],
        input: scheduler.ScheduleTargetInput.fromObject({
          containerOverrides: [
            {
              // Default container name assigned by ApplicationLoadBalancedFargateService.
              name: "web",
              command: ["python", "-m", "app.services.cur_ingestion_service"],
            },
          ],
        }),
      }),
    });

    // ---- Daily AgentCore Runtime discovery (EventBridge Scheduler -> ECS RunTask) ----
    // Runs daily at 10:00 UTC (after the CUR ingestion schedule at 09:00), so
    // this dashboard's own AgentCore-related state stays fresh once a day
    // without requiring a manual trigger. Same reuse pattern as
    // CurIngestionSchedule: the backend service's existing task definition,
    // command override to the discovery module entrypoint. Exits 0 on
    // success/partial_failure/skipped, 1 only when every configured region
    // failed, so a fully-failed run is observable in ECS task history.
    new scheduler.Schedule(this, "AgentCoreDiscoverySchedule", {
      scheduleName: `bedrock-dashboard-agentcore-discovery-${props.environment}`,
      description:
        "Daily AgentCore Runtime discovery scan (python -m app.services.agentcore_discovery_service)",
      schedule: scheduler.ScheduleExpression.cron({ minute: "0", hour: "10" }), // UTC
      target: new schedulerTargets.EcsRunFargateTask(cluster, {
        taskDefinition: fargateService.taskDefinition,
        vpcSubnets: { subnetType: ec2.SubnetType.PRIVATE_WITH_EGRESS },
        securityGroups: [fargateService.service.connections.securityGroups[0]],
        input: scheduler.ScheduleTargetInput.fromObject({
          containerOverrides: [
            {
              name: "web",
              command: ["python", "-m", "app.services.agentcore_discovery_service"],
            },
          ],
        }),
      }),
    });

    // Outputs
    new cdk.CfnOutput(this, "ApiEndpoint", {
      value: this.apiEndpoint,
      description:
        "API Gateway invoke hostname (used as the CloudFront /api/* origin domain)",
      exportName: `${this.stackName}-ApiEndpoint`,
    });

    new cdk.CfnOutput(this, "ApiStage", {
      value: this.apiStage,
      description:
        "API Gateway stage name (used as CloudFront /api/* origin path)",
      exportName: `${this.stackName}-ApiStage`,
    });

    new cdk.CfnOutput(this, "ApiInvokeUrl", {
      value: `https://${this.apiEndpoint}/${this.apiStage}`,
      description:
        "Full API Gateway invoke URL (deploy script summary + manual testing)",
    });

    new cdk.CfnOutput(this, "InternalAlbDnsName", {
      value: fargateService.loadBalancer.loadBalancerDnsName,
      description:
        "Internal ALB DNS (private - reachable only from inside the VPC via the API Gateway VPC Link)",
    });

    new cdk.CfnOutput(this, "ServiceArn", {
      value: fargateService.service.serviceArn,
      description: "ECS Fargate service ARN",
    });

  }
}
