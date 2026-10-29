import * as cdk from "aws-cdk-lib";
import * as ec2 from "aws-cdk-lib/aws-ec2";
import * as ecr from "aws-cdk-lib/aws-ecr";
import * as logs from "aws-cdk-lib/aws-logs";
import * as rds from "aws-cdk-lib/aws-rds";
import * as s3 from "aws-cdk-lib/aws-s3";
import * as secretsmanager from "aws-cdk-lib/aws-secretsmanager";
import * as cognito from "aws-cdk-lib/aws-cognito";
import { Construct } from "constructs";

export interface DataStackProps extends cdk.StackProps {
  environment: string;
  dbInstanceClass: string;
  /**
   * Optional public URL of the frontend (e.g., the CloudFront distribution URL).
   * When set, it is added to the User Pool Client as an allowed callback and
   * logout URL. On the very first deploy this is not yet known — the Frontend
   * stack hasn't been created — so the deploy script re-deploys the Data stack
   * once the CloudFront URL is available. Never blocks the initial deploy:
   * localhost URLs are always allowed so local dev works out of the box.
   *
   * Only applied to CDK-created User Pool clients (fresh-account path).
   * For imported pools (see `existingUserPoolId` below), the pool owner is
   * responsible for managing the client's callback and logout URLs.
   */
  frontendUrl?: string;

  /**
   * Optional: "bring your own" existing Cognito User Pool ID.
   *
   * When all three `existingUserPool*` props are set, the Data stack does NOT
   * create a new User Pool / App Client / hosted-UI domain / admin group.
   * Instead it references the operator-supplied resources via
   * `UserPool.fromUserPoolId` and `UserPoolClient.fromUserPoolClientId`, and
   * publishes their identifiers through the same `CfnOutput` names it would
   * publish for a CDK-created pool. This lets the Backend stack consume
   * identical env vars either way.
   *
   * Use this path when deploying to an account that already has a live
   * Cognito pool you want to keep (existing users, federated identity
   * providers, custom password policies, etc.). Managing the imported pool
   * — callback URLs, password policy, hosted-UI domain, admin group
   * membership — remains the operator's responsibility; CDK does not
   * mutate imported resources.
   *
   * Leave unset for a fresh-account deploy where CDK creates and manages
   * the pool end-to-end (default behaviour, unchanged from previous
   * releases).
   */
  existingUserPoolId?: string;

  /**
   * Optional: existing Cognito App Client ID paired with `existingUserPoolId`.
   * Must be a public (PKCE-only) client authorized for the Authorization Code
   * flow, matching what the SPA expects.
   */
  existingUserPoolClientId?: string;

  /**
   * Optional: existing Cognito hosted-UI domain URL paired with
   * `existingUserPoolId`. Must be the full URL including scheme, e.g.
   * `https://my-app.auth.us-west-2.amazoncognito.com` or a custom domain
   * like `https://auth.example.com`. The URL is not derivable from an
   * imported User Pool, so the operator must supply it explicitly.
   */
  existingUserPoolDomainUrl?: string;
}

export class DataStack extends cdk.Stack {
  public readonly vpc: ec2.Vpc;
  public readonly dbSecurityGroup: ec2.SecurityGroup;
  public readonly dbInstance: rds.DatabaseInstance;
  public readonly dbSecret: secretsmanager.ISecret;
  public readonly ecrRepository: ecr.Repository;
  /**
   * Shared S3 bucket that collects AWS-service access logs for this
   * deployment — ALB access logs (written by the Backend stack) and
   * CloudFront access logs if the operator opts in later. VPC Flow Logs
   * go to CloudWatch Logs (not this bucket) because they are the most
   * voluminous and are cheaper to retain + query in CloudWatch Logs
   * Insights than in S3.
   *
   * Bucket policy: block all public access, SSE-S3 at rest, enforce TLS
   * in transit, 90-day lifecycle expiration (configurable later). Owned
   * by the Data stack so its lifetime matches the VPC and RDS instance
   * — losing the Backend stack never loses historical logs.
   */
  public readonly accessLogsBucket: s3.Bucket;
  // Typed as interfaces so both CDK-created (concrete) resources and
  // imported (readonly reference) resources fit. The Backend stack only
  // reads `userPoolId` / `userPoolClientId` from these, which are on the
  // `IUserPool` / `IUserPoolClient` interfaces.
  public readonly userPool: cognito.IUserPool;
  public readonly userPoolClient: cognito.IUserPoolClient;
  // Only populated on the fresh-account path (where CDK creates the domain).
  // On the BYO path the operator's existing domain is referenced via
  // `userPoolDomainUrl` alone.
  public readonly userPoolDomain?: cognito.UserPoolDomain;
  public readonly userPoolDomainUrl: string;

  constructor(scope: Construct, id: string, props: DataStackProps) {
    super(scope, id, props);

    // VPC with public and private subnets
    this.vpc = new ec2.Vpc(this, "DashboardVpc", {
      maxAzs: 2,
      natGateways: props.environment === "prod" ? 2 : 1,
      subnetConfiguration: [
        {
          cidrMask: 24,
          name: "Public",
          subnetType: ec2.SubnetType.PUBLIC,
        },
        {
          cidrMask: 24,
          name: "Private",
          subnetType: ec2.SubnetType.PRIVATE_WITH_EGRESS,
        },
        {
          cidrMask: 24,
          name: "Isolated",
          subnetType: ec2.SubnetType.PRIVATE_ISOLATED,
        },
      ],
    });

    // ------------------------------------------------------------------
    // Access-log collection — VPC Flow Logs (CloudWatch) + shared S3
    // bucket for AWS-service access logs (ALB, future CloudFront).
    //
    // VPC Flow Logs are the lowest-friction incident-response + security-
    // posture signal AWS offers for a VPC. Written to a CloudWatch Logs
    // group so Logs Insights queries work out of the box — cheaper and
    // faster to query than S3 Athena for the volume Flow Logs produce.
    // Retention trimmed per-environment to keep CW storage cost bounded.
    //
    // accessLogsBucket collects the "noisier but less voluminous" logs
    // from AWS services that write to S3: ALB access logs (consumed by
    // the Backend stack) and CloudFront access logs (if the operator
    // opts in later). Secure-by-default: BLOCK_ALL public, SSE-S3,
    // enforceSSL, 90-day expiration lifecycle, versioning off (access
    // logs are append-only and immutable per request — versioning would
    // just double the storage cost without a recovery story that matters).
    // ------------------------------------------------------------------
    this.vpc.addFlowLog("VpcFlowLog", {
      destination: ec2.FlowLogDestination.toCloudWatchLogs(
        new logs.LogGroup(this, "VpcFlowLogGroup", {
          logGroupName: `/aws/vpc/flowlogs/bedrock-dashboard-${props.environment}`,
          retention: props.environment === "prod"
            ? logs.RetentionDays.ONE_YEAR
            : logs.RetentionDays.ONE_MONTH,
          removalPolicy: props.environment === "prod"
            ? cdk.RemovalPolicy.RETAIN
            : cdk.RemovalPolicy.DESTROY,
        })
      ),
      trafficType: ec2.FlowLogTrafficType.ALL,
    });

    this.accessLogsBucket = new s3.Bucket(this, "AccessLogsBucket", {
      bucketName: `bedrock-dashboard-access-logs-${props.environment}-${cdk.Stack.of(this).account}`,
      blockPublicAccess: s3.BlockPublicAccess.BLOCK_ALL,
      encryption: s3.BucketEncryption.S3_MANAGED,
      enforceSSL: true,
      // ALB access logs are written by the ELB service account directly
      // (not via a signed PutObject the task role controls), so SSE-S3
      // is the maximum supported — SSE-KMS requires either disabling
      // Object Ownership controls or a bucket-key setup that conflicts
      // with ALB's writer identity. Document this gap in README.
      lifecycleRules: [
        {
          id: "ExpireAccessLogsAfter90Days",
          enabled: true,
          expiration: cdk.Duration.days(90),
          // Transition to cheaper storage classes once logs are past the
          // typical incident-response window but still within retention.
          transitions: [
            {
              storageClass: s3.StorageClass.INFREQUENT_ACCESS,
              transitionAfter: cdk.Duration.days(30),
            },
          ],
        },
      ],
      removalPolicy: props.environment === "prod"
        ? cdk.RemovalPolicy.RETAIN
        : cdk.RemovalPolicy.DESTROY,
      autoDeleteObjects: props.environment !== "prod",
    });

    // Security group for RDS — no inbound by default; backend SG will be added later
    this.dbSecurityGroup = new ec2.SecurityGroup(this, "DbSecurityGroup", {
      vpc: this.vpc,
      description: "Security group for RDS PostgreSQL",
      allowAllOutbound: false,
    });

    // Database credentials in Secrets Manager
    this.dbSecret = new secretsmanager.Secret(this, "DbCredentials", {
      secretName: `bedrock-dashboard/${props.environment}/db-credentials`,
      generateSecretString: {
        secretStringTemplate: JSON.stringify({ username: "dashboard_admin" }),
        generateStringKey: "password",
        excludePunctuation: true,
        passwordLength: 30,
      },
    });

    // Map environment instance class to CDK instance type
    const instanceTypeMap: Record<string, ec2.InstanceType> = {
      "t3.micro": ec2.InstanceType.of(ec2.InstanceClass.T3, ec2.InstanceSize.MICRO),
      "t3.small": ec2.InstanceType.of(ec2.InstanceClass.T3, ec2.InstanceSize.SMALL),
      "t3.medium": ec2.InstanceType.of(ec2.InstanceClass.T3, ec2.InstanceSize.MEDIUM),
    };

    const instanceType = instanceTypeMap[props.dbInstanceClass] ??
      ec2.InstanceType.of(ec2.InstanceClass.T3, ec2.InstanceSize.MICRO);

    // RDS PostgreSQL instance.
    //
    // storageEncrypted:true is a hard requirement for an open-source AWS
    // sample — anyone forking this and pointing it at real data gets an
    // encrypted-at-rest DB by default. Uses the AWS-managed "aws/rds" KMS
    // key when kmsKey is not supplied; operators with a HIPAA/PCI/etc.
    // obligation should supply their own customer-managed CMK (see README
    // "Prompt History & Encryption" → key rotation discussion for the
    // same pattern).
    //
    // IMPORTANT — upgrading an existing (unencrypted) instance: toggling
    // storageEncrypted from false to true is NOT an in-place change in
    // CloudFormation. The next `cdk deploy` on a stack that previously
    // ran with storageEncrypted:false will REPLACE the DB instance, which
    // drops all data. Non-prod stacks already have removalPolicy:DESTROY
    // + deletionProtection:false so the replace is possible; prod stacks
    // (removalPolicy:RETAIN + deletionProtection:true) will fail the
    // deploy and need a manual snapshot-restore-into-encrypted flow —
    // treat this as a planned maintenance operation, not a background
    // upgrade.
    this.dbInstance = new rds.DatabaseInstance(this, "PostgresDb", {
      engine: rds.DatabaseInstanceEngine.postgres({
        version: rds.PostgresEngineVersion.VER_15,
      }),
      instanceType,
      vpc: this.vpc,
      vpcSubnets: { subnetType: ec2.SubnetType.PRIVATE_ISOLATED },
      securityGroups: [this.dbSecurityGroup],
      credentials: rds.Credentials.fromSecret(this.dbSecret),
      databaseName: "bedrock_dashboard",
      multiAz: props.environment === "prod",
      allocatedStorage: 20,
      maxAllocatedStorage: props.environment === "prod" ? 100 : 50,
      backupRetention: cdk.Duration.days(props.environment === "prod" ? 14 : 7),
      deletionProtection: props.environment === "prod",
      storageEncrypted: true,
      removalPolicy: props.environment === "prod"
        ? cdk.RemovalPolicy.RETAIN
        : cdk.RemovalPolicy.DESTROY,
    });

    // ECR repository for backend container images.
    //
    // imageScanOnPush:true enables ECR Basic image inspection on every
    // push. Non-blocking — the push itself is never rejected, and
    // vulnerability reports surface in the ECR console. Zero added cost
    // on the Basic tier. Setting this on an existing repository is
    // non-destructive (UpdateRepository API, in-place).
    //
    // imageTagMutability intentionally left as the default (MUTABLE) in
    // this commit — switching to IMMUTABLE requires the deploy script to
    // push a unique per-build tag instead of the current `latest`, and
    // that change ships as its own commit so the two land together.
    this.ecrRepository = new ecr.Repository(this, "BackendRepo", {
      repositoryName: `bedrock-dashboard-backend-${props.environment}`,
      imageScanOnPush: true,
      removalPolicy: props.environment === "prod"
        ? cdk.RemovalPolicy.RETAIN
        : cdk.RemovalPolicy.DESTROY,
      emptyOnDelete: props.environment !== "prod",
      lifecycleRules: [
        {
          maxImageCount: 10,
          description: "Keep only last 10 images",
        },
      ],
    });

    // ---------------------------------------------------------------------
    // Cognito — User Pool, App Client, Hosted UI Domain, Admin group
    //
    // Two paths:
    //  A. Fresh-account (default) — CDK creates and manages the pool, client,
    //     hosted-UI domain, and admin group. User Pool ID + Client ID are
    //     stable cross-stack references the Backend stack consumes as ECS
    //     env vars.
    //  B. Bring-your-own (BYO) — set all three of `existingUserPoolId`,
    //     `existingUserPoolClientId`, and `existingUserPoolDomainUrl` (via
    //     CDK context or environment variables). CDK imports references to
    //     the existing pool and client and publishes them through the same
    //     `CfnOutput` names as path A, so the Backend stack is agnostic to
    //     which path is active. CDK does NOT mutate imported resources —
    //     the operator owns callback URLs, password policy, admin group
    //     membership, and federated identity providers.
    //
    // Password policy (fresh-account only) matches Cognito's defaults so
    // the temporary passwords generated by build-and-deploy-backend.sh
    // (uppercase, lowercase, digit, symbol) always satisfy the policy.
    // ---------------------------------------------------------------------
    const useExistingPool =
      props.existingUserPoolId !== undefined &&
      props.existingUserPoolClientId !== undefined &&
      props.existingUserPoolDomainUrl !== undefined;

    // Sanity check: partial BYO configuration is a common misconfiguration.
    // Fail synth with a clear message rather than silently falling back to
    // creating a new pool (which would lock out the operator's existing
    // users).
    const anyExistingSet =
      props.existingUserPoolId !== undefined ||
      props.existingUserPoolClientId !== undefined ||
      props.existingUserPoolDomainUrl !== undefined;
    if (anyExistingSet && !useExistingPool) {
      const missing: string[] = [];
      if (!props.existingUserPoolId) missing.push("existingUserPoolId");
      if (!props.existingUserPoolClientId) missing.push("existingUserPoolClientId");
      if (!props.existingUserPoolDomainUrl) missing.push("existingUserPoolDomainUrl");
      throw new Error(
        `Partial BYO Cognito configuration detected. To import an existing ` +
        `User Pool, all three inputs are required. Missing: ${missing.join(", ")}. ` +
        `Set them via CDK context (-c existingUserPoolId=... etc.) or the ` +
        `EXISTING_USER_POOL_ID / EXISTING_USER_POOL_CLIENT_ID / ` +
        `EXISTING_USER_POOL_DOMAIN_URL environment variables. Alternatively, ` +
        `unset all three to fall back to the fresh-account path (CDK creates ` +
        `a new pool).`
      );
    }

    let domainPrefix: string | undefined;

    if (useExistingPool) {
      // Path B: Bring-your-own. Reference-only, no mutation.
      this.userPool = cognito.UserPool.fromUserPoolId(
        this,
        "ImportedUserPool",
        props.existingUserPoolId!,
      );
      this.userPoolClient = cognito.UserPoolClient.fromUserPoolClientId(
        this,
        "ImportedUserPoolClient",
        props.existingUserPoolClientId!,
      );
      this.userPoolDomainUrl = props.existingUserPoolDomainUrl!;
      // userPoolDomain remains undefined — CDK does not manage the imported
      // pool's hosted-UI domain. Its full URL flows through via
      // userPoolDomainUrl above.
    } else {
      // Path A: Fresh-account. CDK creates and manages everything.
      const createdPool = new cognito.UserPool(this, "UserPool", {
        userPoolName: `bedrock-dashboard-${props.environment}`,
        // Admin-only user creation. First-time users are provisioned by
        // build-and-deploy-backend.sh with a temporary password and forced
        // to change it on first sign-in.
        selfSignUpEnabled: false,
        signInAliases: { username: true, email: true },
        autoVerify: { email: true },
        standardAttributes: {
          email: { required: true, mutable: true },
        },
        passwordPolicy: {
          minLength: 8,
          requireLowercase: true,
          requireUppercase: true,
          requireDigits: true,
          requireSymbols: true,
        },
        accountRecovery: cognito.AccountRecovery.EMAIL_ONLY,
        removalPolicy: props.environment === "prod"
          ? cdk.RemovalPolicy.RETAIN
          : cdk.RemovalPolicy.DESTROY,
      });
      this.userPool = createdPool;

      // The admin group is what unlocks the admin dashboard. See
      // backend/app/auth.py::build_current_user — any Cognito group whose
      // name contains "admin" (case-insensitive) maps to the admin role.
      new cognito.CfnUserPoolGroup(this, "AdminGroup", {
        userPoolId: createdPool.userPoolId,
        groupName: "admin",
        description: "Bedrock Dashboard administrators (full access).",
        precedence: 1,
      });

      // Allowed callback/logout URLs. Localhost is always allowed so
      // `npm run dev` works out of the box; the CloudFront URL is added
      // when known (build-and-deploy-frontend.sh re-deploys with
      // -c frontendUrl=<url> once CloudFront is up).
      const callbackUrls = [
        "http://localhost:5173/callback",
        "http://localhost:3000/callback",
      ];
      const logoutUrls = ["http://localhost:5173", "http://localhost:3000"];
      if (props.frontendUrl) {
        callbackUrls.push(`${props.frontendUrl}/callback`);
        logoutUrls.push(props.frontendUrl);
      }

      // Public SPA client — no secret; PKCE-only via Authorization Code flow.
      this.userPoolClient = createdPool.addClient("SpaClient", {
        userPoolClientName: `bedrock-dashboard-${props.environment}-spa`,
        generateSecret: false,
        authFlows: {
          userPassword: true,
          userSrp: true,
        },
        oAuth: {
          flows: { authorizationCodeGrant: true },
          scopes: [
            cognito.OAuthScope.OPENID,
            cognito.OAuthScope.EMAIL,
            cognito.OAuthScope.PROFILE,
          ],
          callbackUrls,
          logoutUrls,
        },
        preventUserExistenceErrors: true,
        accessTokenValidity: cdk.Duration.hours(1),
        idTokenValidity: cdk.Duration.hours(1),
        refreshTokenValidity: cdk.Duration.days(30),
      });

      // Hosted UI domain — globally-unique prefix keyed off the account
      // ID so it is deterministic per-account and never collides across
      // deployments.
      domainPrefix = `bedrock-dashboard-${props.environment}-${cdk.Stack.of(this).account}`;
      this.userPoolDomain = createdPool.addDomain("HostedUiDomain", {
        cognitoDomain: { domainPrefix },
      });
      this.userPoolDomainUrl = `https://${domainPrefix}.auth.${cdk.Stack.of(this).region}.amazoncognito.com`;
    }

    // Outputs
    new cdk.CfnOutput(this, "DbEndpoint", {
      value: this.dbInstance.dbInstanceEndpointAddress,
      description: "RDS PostgreSQL endpoint",
    });

    new cdk.CfnOutput(this, "DbSecretArn", {
      value: this.dbSecret.secretArn,
      description: "Database credentials secret ARN",
    });

    new cdk.CfnOutput(this, "EcrRepositoryUri", {
      value: this.ecrRepository.repositoryUri,
      description: "ECR repository URI for backend images",
    });

    new cdk.CfnOutput(this, "UserPoolId", {
      value: this.userPool.userPoolId,
      description: "Cognito User Pool ID",
      exportName: `BedrockDashboard-${props.environment}-UserPoolId`,
    });

    new cdk.CfnOutput(this, "UserPoolClientId", {
      value: this.userPoolClient.userPoolClientId,
      description: "Cognito User Pool Client ID (public SPA client, PKCE)",
      exportName: `BedrockDashboard-${props.environment}-UserPoolClientId`,
    });

    // Only emitted on the fresh-account path — on BYO the domain prefix
    // may be a Cognito domain, a custom domain, or something else the
    // operator provides only as a full URL. `UserPoolDomainUrl` below is
    // always populated and carries the value the SPA actually needs.
    if (domainPrefix !== undefined) {
      new cdk.CfnOutput(this, "UserPoolDomain", {
        value: domainPrefix,
        description: "Cognito Hosted UI domain prefix",
      });
    }

    new cdk.CfnOutput(this, "UserPoolDomainUrl", {
      value: this.userPoolDomainUrl,
      description: "Cognito Hosted UI full URL (used by the SPA for sign-in)",
      exportName: `BedrockDashboard-${props.environment}-UserPoolDomainUrl`,
    });

    // Indicator output for operators / downstream tooling to detect which
    // Cognito ownership path was taken. "cdk-managed" = CDK created the
    // pool; "imported" = operator supplied EXISTING_USER_POOL_* inputs and
    // CDK is only referencing the pool.
    new cdk.CfnOutput(this, "UserPoolOwnership", {
      value: useExistingPool ? "imported" : "cdk-managed",
      description: "Whether the Cognito User Pool is CDK-managed or imported (BYO)",
    });
  }
}
