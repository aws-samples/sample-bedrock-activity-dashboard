import * as cdk from "aws-cdk-lib";
import * as s3 from "aws-cdk-lib/aws-s3";
import * as cloudfront from "aws-cdk-lib/aws-cloudfront";
import * as cloudfrontOrigins from "aws-cdk-lib/aws-cloudfront-origins";
import * as s3deploy from "aws-cdk-lib/aws-s3-deployment";
import { Construct } from "constructs";

export interface FrontendStackProps extends cdk.StackProps {
  environment: string;
  /**
   * API Gateway invoke hostname (no scheme, no stage, no trailing
   * slash) — e.g. ``abcd1234.execute-api.us-west-2.amazonaws.com``.
   * When provided together with ``apiStage``, CloudFront adds a
   * ``/api/*`` behavior that forwards to the API Gateway over HTTPS.
   * Omit both to skip the behavior entirely (step-1 of the ALB-privacy
   * cutover, where the import must be released before the backend ALB is
   * replaced).
   */
  apiEndpoint?: string;
  /** API Gateway stage name — used as ``originPath = /<stage>``. */
  apiStage?: string;
}

export class FrontendStack extends cdk.Stack {
  public readonly distributionUrl: string;

  constructor(scope: Construct, id: string, props: FrontendStackProps) {
    super(scope, id, props);

    // S3 bucket for static hosting — block all public access
    const siteBucket = new s3.Bucket(this, "SiteBucket", {
      bucketName: `bedrock-dashboard-${props.environment}-${cdk.Stack.of(this).account}`,
      blockPublicAccess: s3.BlockPublicAccess.BLOCK_ALL,
      encryption: s3.BucketEncryption.S3_MANAGED,
      enforceSSL: true,
      removalPolicy: props.environment === "prod"
        ? cdk.RemovalPolicy.RETAIN
        : cdk.RemovalPolicy.DESTROY,
      autoDeleteObjects: props.environment !== "prod",
    });

    // Origin Access Identity for CloudFront → S3
    const oai = new cloudfront.OriginAccessIdentity(this, "OAI", {
      comment: `OAI for bedrock-dashboard ${props.environment}`,
    });
    siteBucket.grantRead(oai);

    // ------------------------------------------------------------------
    // Response headers policy — baseline web security headers injected
    // by CloudFront on every response from this distribution.
    //
    // What each header does, and why these specific values:
    //
    // * Strict-Transport-Security — tells browsers to only ever load the
    //   origin over HTTPS. 1-year max-age with subdomains + preload is
    //   the mozilla.org-recommended production baseline. CloudFront
    //   already redirects HTTP->HTTPS; HSTS makes that enforcement
    //   client-side so a MITM can't downgrade a first request.
    //
    // * Content-Security-Policy — tightens what the browser is allowed
    //   to load. 'self' everywhere by default; 'unsafe-inline' is kept
    //   for style-src ONLY because this project uses inline React style
    //   props everywhere (see frontend-conventions). script-src stays
    //   strict ('self' only) because Vite bundles everything so there
    //   is no inline-script requirement. connect-src 'self' https:
    //   permits the Cognito /oauth2/token exchange without pinning the
    //   Cognito domain into the CSP (which would require threading the
    //   Data stack's Cognito domain here and complicate the sample).
    //   form-action similarly covers the Cognito hosted UI redirect.
    //   frame-ancestors 'none' + object-src 'none' + base-uri 'self'
    //   are the standard clickjacking + base-tag-injection hardening.
    //
    // * X-Content-Type-Options: nosniff — stops MIME-sniffing XSS vectors.
    // * X-Frame-Options: DENY — belt-and-suspenders with frame-ancestors.
    // * Referrer-Policy: strict-origin-when-cross-origin — browser default
    //   on modern Chrome/Firefox but makes it explicit in response.
    //
    // override:true forces these on the response even if the origin set
    // its own; the backend doesn't set any of these, so override is a
    // no-op today, but it's the safe default for a CDN-level policy.
    // ------------------------------------------------------------------
    const securityHeaders = new cloudfront.ResponseHeadersPolicy(
      this,
      "SecurityHeadersPolicy",
      {
        responseHeadersPolicyName: `bedrock-dashboard-${props.environment}-security-headers`,
        comment: "Baseline web security headers for the dashboard SPA + API proxy",
        securityHeadersBehavior: {
          strictTransportSecurity: {
            accessControlMaxAge: cdk.Duration.days(365),
            includeSubdomains: true,
            preload: true,
            override: true,
          },
          contentTypeOptions: { override: true },
          frameOptions: {
            frameOption: cloudfront.HeadersFrameOption.DENY,
            override: true,
          },
          referrerPolicy: {
            referrerPolicy:
              cloudfront.HeadersReferrerPolicy.STRICT_ORIGIN_WHEN_CROSS_ORIGIN,
            override: true,
          },
          contentSecurityPolicy: {
            contentSecurityPolicy: [
              "default-src 'self'",
              "script-src 'self'",
              "style-src 'self' 'unsafe-inline'",
              "img-src 'self' data: https:",
              "font-src 'self' data:",
              "connect-src 'self' https:",
              "frame-ancestors 'none'",
              "form-action 'self' https:",
              "base-uri 'self'",
              "object-src 'none'",
            ].join("; "),
            override: true,
          },
        },
      }
    );

    // Additional behaviors for API proxy. The /api/* origin is the API
    // Gateway REST API — HTTPS end-to-end on the CloudFront -> origin leg,
    // with the ALB kept internal and private. See the
    // backend-stack API Gateway block for the full architecture note.
    const additionalBehaviors: Record<string, cloudfront.BehaviorOptions> = {};

    if (props.apiEndpoint && props.apiStage) {
      const apiOrigin = new cloudfrontOrigins.HttpOrigin(props.apiEndpoint, {
        // HTTPS end-to-end on the CloudFront -> API Gateway leg. The
        // ``execute-api.<region>.amazonaws.com`` endpoint has an AWS-
        // managed public cert, so no custom domain / ACM import is
        // needed to flip this to HTTPS_ONLY.
        protocolPolicy: cloudfront.OriginProtocolPolicy.HTTPS_ONLY,
        originSslProtocols: [cloudfront.OriginSslPolicy.TLS_V1_2],
        // API Gateway stage is NOT part of the request path from the
        // SPA (it still calls ``/api/*``); CloudFront prepends the
        // stage via ``originPath`` so API Gateway sees ``/<stage>/api
        // /<rest>`` and routes correctly.
        originPath: `/${props.apiStage}`,
        // Agent Chat first byte can be ~45s on long-context prompts
        // (backend builds context before streaming starts). 60s is the
        // CloudFront default max read timeout without a quota increase
        // — going higher requires an AWS support ticket. If 60s starts
        // tripping, either request the quota bump or add a keepalive
        // event server-side during pre-stream work.
        readTimeout: cdk.Duration.seconds(60),
      });

      additionalBehaviors["/api/*"] = {
        origin: apiOrigin,
        viewerProtocolPolicy: cloudfront.ViewerProtocolPolicy.REDIRECT_TO_HTTPS,
        allowedMethods: cloudfront.AllowedMethods.ALLOW_ALL,
        cachePolicy: cloudfront.CachePolicy.CACHING_DISABLED,
        // AllViewerExceptHostHeader instead of AllViewer: API Gateway's
        // ``execute-api`` endpoint rejects requests whose ``Host`` header
        // doesn't match the endpoint's own hostname, so CloudFront must
        // NOT forward its viewer-side host header. Everything else from
        // the viewer (query string, cookies, headers) still comes
        // through unchanged.
        originRequestPolicy:
          cloudfront.OriginRequestPolicy.ALL_VIEWER_EXCEPT_HOST_HEADER,
        // Disable compression so SSE streaming responses are not buffered by CloudFront
        compress: false,
        responseHeadersPolicy: securityHeaders,
      };
    }

    // CloudFront distribution with HTTPS (default CloudFront certificate)
    const distribution = new cloudfront.Distribution(this, "Distribution", {
      defaultBehavior: {
        origin: new cloudfrontOrigins.S3Origin(siteBucket, {
          originAccessIdentity: oai,
        }),
        viewerProtocolPolicy: cloudfront.ViewerProtocolPolicy.REDIRECT_TO_HTTPS,
        cachePolicy: cloudfront.CachePolicy.CACHING_OPTIMIZED,
        responseHeadersPolicy: securityHeaders,
      },
      additionalBehaviors,
      defaultRootObject: "index.html",
      // SPA fallback: return index.html for 403/404 so client-side routing works
      errorResponses: [
        {
          httpStatus: 403,
          responseHttpStatus: 200,
          responsePagePath: "/index.html",
          ttl: cdk.Duration.minutes(5),
        },
        {
          httpStatus: 404,
          responseHttpStatus: 200,
          responsePagePath: "/index.html",
          ttl: cdk.Duration.minutes(5),
        },
      ],
    });

    // Deploy frontend build artifacts to S3.
    //
    // Two deployments split cache-control by file type. This is the durable fix
    // for the "stale frontend" problem: index.html must never be cached long-term
    // or browsers/CloudFront keep serving an old index.html that references an
    // outdated JS bundle.
    //
    // 1. Fingerprinted assets (assets/*.js, assets/*.css) are content-hashed, so
    //    they can be cached forever and marked immutable. Each build produces new
    //    hashed filenames, so there is never a stale-content risk.
    const deployAssets = new s3deploy.BucketDeployment(this, "DeployAssets", {
      sources: [s3deploy.Source.asset("../frontend/dist")],
      destinationBucket: siteBucket,
      // Scope this deployment to fingerprinted assets only. Combined with prune,
      // the sync --delete only affects the assets/ prefix, so it will not remove
      // index.html or diagrams managed by the second deployment.
      exclude: ["*"],
      include: ["assets/*"],
      prune: true,
      cacheControl: [
        s3deploy.CacheControl.setPublic(),
        s3deploy.CacheControl.maxAge(cdk.Duration.days(365)),
        s3deploy.CacheControl.immutable(),
      ],
    });

    // 2. index.html and non-fingerprinted static files (diagrams/*) must be
    //    revalidated on every request so new deploys are picked up immediately.
    //    "no-cache" means the browser may cache but must revalidate with the
    //    origin before use. This deployment also triggers the CloudFront
    //    invalidation so edge caches are cleared on deploy.
    const deployHtml = new s3deploy.BucketDeployment(this, "DeployHtml", {
      sources: [s3deploy.Source.asset("../frontend/dist")],
      destinationBucket: siteBucket,
      exclude: ["*"],
      include: ["index.html", "diagrams/*"],
      // prune:false so this second deployment does not delete the fingerprinted
      // assets uploaded by the first deployment.
      prune: false,
      cacheControl: [
        s3deploy.CacheControl.noCache(),
        s3deploy.CacheControl.mustRevalidate(),
      ],
      distribution,
      distributionPaths: ["/*"],
    });

    // Ensure assets are uploaded before index.html so the referenced bundle
    // always exists by the time the new index.html goes live.
    deployHtml.node.addDependency(deployAssets);

    this.distributionUrl = `https://${distribution.distributionDomainName}`;

    // Outputs
    new cdk.CfnOutput(this, "DistributionUrl", {
      value: this.distributionUrl,
      description: "CloudFront distribution URL",
    });

    new cdk.CfnOutput(this, "DistributionId", {
      value: distribution.distributionId,
      description: "CloudFront distribution ID",
    });

    new cdk.CfnOutput(this, "BucketName", {
      value: siteBucket.bucketName,
      description: "S3 bucket name for frontend assets",
    });
  }
}
