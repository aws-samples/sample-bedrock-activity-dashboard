#!/usr/bin/env node
import "source-map-support/register";
import * as cdk from "aws-cdk-lib";
import * as appreg from "aws-cdk-lib/aws-servicecatalogappregistry";
import { DataStack } from "../lib/data-stack";
import { BackendStack } from "../lib/backend-stack";
import { FrontendStack } from "../lib/frontend-stack";
import { AppRegistryStack } from "../lib/app-registry-stack";

const app = new cdk.App();

// Read environment from CDK context (default: "dev")
const environment = app.node.tryGetContext("env") ?? "dev";
const region = app.node.tryGetContext("region") ?? "us-west-2";
const envConfigs = app.node.tryGetContext("environments") as Record<
  string,
  { dbInstanceClass: string; desiredCount: number }
>;
const envConfig = envConfigs[environment] ?? envConfigs["dev"];

// Optional public URL of the deployed frontend (CloudFront URL). When set,
// the Data stack adds `<url>/callback` to the Cognito app client's
// CallbackURLs and `<url>` to LogoutURLs. Passed as `-c frontendUrl=<url>`
// by build-and-deploy-frontend.sh (Step 4) after CloudFront has been created,
// so CDK owns the client contract end-to-end.
//
// On the very first deploy, CloudFront doesn't exist yet — the initial Data
// stack deploy seeds only localhost URLs, and Step 4 re-deploys Data with
// the now-known CloudFront URL. On subsequent deploys this is a CFN no-op
// once the URL is already in the client's URL lists.
//
// The Backend stack also declares an optional frontendUrl prop that would
// add the URL to CORS_ORIGINS if passed. Since Step 4 only re-deploys the
// Data stack (not Backend), Backend's CORS remains localhost-only by design;
// production traffic hits the API through the same-origin CloudFront proxy
// so CORS is not exercised in prod.
const frontendUrl: string | undefined =
  app.node.tryGetContext("frontendUrl") ?? process.env.FRONTEND_URL;

// Optional "bring your own" Cognito inputs. When ALL THREE are set, the
// Data stack imports references to an existing pool/client/domain instead
// of creating new ones. Use this when deploying to an account that already
// has a live Cognito pool you want to keep. When any of the three is unset,
// the fresh-account path runs (CDK creates and manages the pool). See
// DataStackProps for the full contract.
const existingUserPoolId: string | undefined =
  app.node.tryGetContext("existingUserPoolId") ?? process.env.EXISTING_USER_POOL_ID;
const existingUserPoolClientId: string | undefined =
  app.node.tryGetContext("existingUserPoolClientId") ??
  process.env.EXISTING_USER_POOL_CLIENT_ID;
const existingUserPoolDomainUrl: string | undefined =
  app.node.tryGetContext("existingUserPoolDomainUrl") ??
  process.env.EXISTING_USER_POOL_DOMAIN_URL;

// Optional Cognito federated identity provider name. When set, the SPA
// deep-links users into this IdP via the `identity_provider` param on the
// Cognito hosted-UI /oauth2/authorize call, bypassing the account chooser.
// Only meaningful when the target User Pool has a federated IdP configured
// (typically the BYO path). Empty default preserves the standard hosted-UI
// login flow.
const cognitoIdentityProvider: string | undefined =
  app.node.tryGetContext("cognitoIdentityProvider") ??
  process.env.COGNITO_IDENTITY_PROVIDER;

// Optional ADDITIONAL regions (beyond the deploy region, always included)
// for CloudTrailService to fan Bedrock activity ingestion out across (F1).
// e.g. `-c cloudtrailRegions=us-east-1` or CLOUDTRAIL_ADDITIONAL_REGIONS
// env var. Each region needs Bedrock model invocation logging enabled
// separately (infrastructure/enable-bedrock-logging-all-regions.sh).
// Defaults to "us-east-1" — this dashboard's account already has Bedrock
// invocation logging enabled there. Pass an explicit empty string
// (`-c cloudtrailRegions=`) to opt back into single-region-only ingestion.
const cloudtrailRegions: string | undefined =
  app.node.tryGetContext("cloudtrailRegions") ??
  process.env.CLOUDTRAIL_ADDITIONAL_REGIONS ??
  "us-east-1";

// Explicit env for all stacks
const stackEnv: cdk.Environment = {
  account: process.env.CDK_DEFAULT_ACCOUNT,
  region,
};

// Common tags applied to all resources
const commonTags: Record<string, string> = {
  Environment: environment,
  Application: "bedrock-activity-dashboard",
  ManagedBy: "CDK",
};

// Owner and repository can be supplied at deploy time via CDK context
// (`-c owner=...` / `-c repository=...`) or the APP_OWNER / APP_REPOSITORY
// environment variables. Falls back to generic placeholders inside the stack.
const owner: string | undefined =
  app.node.tryGetContext("owner") ?? process.env.APP_OWNER;
const repository: string | undefined =
  app.node.tryGetContext("repository") ?? process.env.APP_REPOSITORY;

// Governance stack — AppRegistry Application (deployed first so the other
// stacks can associate themselves with it). Independent lifecycle.
const appRegistryStack = new AppRegistryStack(
  app,
  `BedrockDashboard-AppRegistry-${environment}`,
  {
    env: stackEnv,
    environment,
    owner,
    repository,
    description: `Bedrock Activity Dashboard - AppRegistry Governance Stack (${environment})`,
  }
);

// Data stack — VPC, RDS, Secrets Manager, Cognito
const dataStack = new DataStack(app, `BedrockDashboard-Data-${environment}`, {
  env: stackEnv,
  environment,
  dbInstanceClass: envConfig.dbInstanceClass,
  frontendUrl,
  existingUserPoolId,
  existingUserPoolClientId,
  existingUserPoolDomainUrl,
  description: `Bedrock Activity Dashboard - Data Stack (${environment})`,
});

// Backend stack — ECS Fargate, ALB, CodeBuild, security groups
const backendStack = new BackendStack(app, `BedrockDashboard-Backend-${environment}`, {
  env: stackEnv,
  environment,
  desiredCount: envConfig.desiredCount,
  vpc: dataStack.vpc,
  dbInstance: dataStack.dbInstance,
  dbSecret: dataStack.dbSecret,
  dbSecurityGroup: dataStack.dbSecurityGroup,
  ecrRepository: dataStack.ecrRepository,
  accessLogsBucket: dataStack.accessLogsBucket,
  userPool: dataStack.userPool,
  userPoolClient: dataStack.userPoolClient,
  userPoolDomainUrl: dataStack.userPoolDomainUrl,
  frontendUrl,
  cognitoIdentityProvider,
  cloudtrailRegions,
  description: `Bedrock Activity Dashboard - Backend Stack (${environment})`,
});
backendStack.addDependency(dataStack);

// Three-step deploy support for the ALB privacy flip. The scheme flip (internet-facing ->
// internal) replaces the ALB, which also replaces the DNS-name export the
// Frontend stack used to import. CloudFormation refuses to update an
// export that another stack imports, so the one-time cutover goes:
//
//   Step 1: deploy Frontend with `-c skipApiOrigin=true` — this drops
//           the CloudFront /api/* behavior entirely, releasing the import.
//   Step 2: deploy Backend — ALB replacement completes, new API Gateway
//           + VPC Link go live.
//   Step 3: deploy Frontend normally — new /api/* behavior points at
//           API Gateway over HTTPS.
//
// The API is unavailable between steps 1 and 3; plan a window. After the
// cutover is complete, normal deploys don't need this flag.
const skipApiOrigin: boolean =
  (app.node.tryGetContext("skipApiOrigin") ?? "").toString().toLowerCase() ===
    "true" || process.env.SKIP_API_ORIGIN === "1";

// Frontend stack — S3, CloudFront, API proxy via CloudFront behavior
const frontendStack = new FrontendStack(app, `BedrockDashboard-Frontend-${environment}`, {
  env: stackEnv,
  environment,
  apiEndpoint: skipApiOrigin ? undefined : backendStack.apiEndpoint,
  apiStage: skipApiOrigin ? undefined : backendStack.apiStage,
  description: `Bedrock Activity Dashboard - Frontend Stack (${environment})`,
});
frontendStack.addDependency(backendStack);

// Associate all three stacks with the AppRegistry Application. Each association
// is scoped into its target stack (resource = that stack's own stackId), so no
// fragile cross-stack export is added to the existing stacks — only the
// Application id flows in. AppRegistry auto-applies the `awsApplication` tag to
// associated stack resources, enabling the unified myApplications view.
const associationTargets: Array<{ stack: cdk.Stack; label: string }> = [
  { stack: dataStack, label: "Data" },
  { stack: backendStack, label: "Backend" },
  { stack: frontendStack, label: "Frontend" },
];

for (const { stack, label } of associationTargets) {
  const association = new appreg.CfnResourceAssociation(
    stack,
    "AppRegistryAssociation",
    {
      application: appRegistryStack.applicationId,
      resource: stack.stackId,
      resourceType: "CFN_STACK",
    }
  );
  // Ensure the Application exists before the association is created.
  stack.addDependency(appRegistryStack, `AppRegistry association (${label})`);
  void association;
}

// Apply tags to all stacks
for (const stack of [appRegistryStack, dataStack, backendStack, frontendStack]) {
  for (const [key, value] of Object.entries(commonTags)) {
    cdk.Tags.of(stack).add(key, value);
  }
}

app.synth();
