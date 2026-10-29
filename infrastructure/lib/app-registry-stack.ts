import * as cdk from "aws-cdk-lib";
import * as appreg from "aws-cdk-lib/aws-servicecatalogappregistry";
import { Construct } from "constructs";

export interface AppRegistryStackProps extends cdk.StackProps {
  environment: string;
  /**
   * Contact email for the application owner. Recorded on the AppRegistry
   * AttributeGroup as `owner`. Falls back to a generic placeholder when
   * not supplied via CDK context (`-c owner=...`) or the `APP_OWNER` env var.
   */
  owner?: string;
  /**
   * URL of the source repository. Recorded on the AppRegistry AttributeGroup
   * as `repository`. Falls back to a generic placeholder when not supplied
   * via CDK context (`-c repository=...`) or the `APP_REPOSITORY` env var.
   */
  repository?: string;
}

/**
 * Governance stack that registers the entire Bedrock Activity Dashboard as an
 * AWS Service Catalog AppRegistry Application. Keeping this in its own stack
 * means the Application's lifecycle is independent of the Data/Backend/Frontend
 * stacks, and the Application id/arn can be shared into those stacks so each one
 * can associate itself.
 *
 * Uses the stable L1 constructs from aws-cdk-lib/aws-servicecatalogappregistry
 * (no alpha package dependency).
 */
export class AppRegistryStack extends cdk.Stack {
  public readonly applicationId: string;
  public readonly applicationArn: string;

  constructor(scope: Construct, id: string, props: AppRegistryStackProps) {
    super(scope, id, props);

    // The AppRegistry Application — the unified "myApplications" view for the
    // whole dashboard (cost + operations across all three stacks).
    const application = new appreg.CfnApplication(this, "Application", {
      name: "BedrockActivityDashboard",
      description:
        "Bedrock Activity Dashboard - centralized monitoring and governance " +
        "platform for Amazon Bedrock usage (token consumption, model usage, " +
        "cost chargeback, and 14 governance AI agents).",
    });

    const owner = props.owner ?? "owner@example.com";
    const repository =
      props.repository ?? "https://example.com/your-org/bedrock-activity-dashboard";

    // Attribute group capturing machine-readable application metadata.
    const attributeGroup = new appreg.CfnAttributeGroup(this, "MetadataGroup", {
      name: "bedrock-dashboard-metadata",
      description: "Ownership, environment, and repository metadata for the Bedrock Activity Dashboard.",
      attributes: {
        applicationName: "Bedrock Activity Dashboard",
        owner,
        environment: props.environment,
        team: "Bedrock Governance",
        costCenter: "genai-governance",
        repository,
        appType: "GenAI cost governance dashboard",
      },
    });

    // Attach the attribute group to the Application.
    const attributeGroupAssociation = new appreg.CfnAttributeGroupAssociation(
      this,
      "MetadataGroupAssociation",
      {
        application: application.attrId,
        attributeGroup: attributeGroup.attrId,
      }
    );
    attributeGroupAssociation.addDependency(application);
    attributeGroupAssociation.addDependency(attributeGroup);

    this.applicationId = application.attrId;
    this.applicationArn = application.attrArn;

    // Outputs
    new cdk.CfnOutput(this, "ApplicationId", {
      value: this.applicationId,
      description: "AppRegistry Application ID for the Bedrock Activity Dashboard",
    });

    new cdk.CfnOutput(this, "ApplicationArn", {
      value: this.applicationArn,
      description: "AppRegistry Application ARN for the Bedrock Activity Dashboard",
    });
  }
}
