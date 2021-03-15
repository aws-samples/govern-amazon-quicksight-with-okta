"""
AWS CDK Stack to deploy QuickSight Governance solution in an AWS Account.
Consists of:
    - S3 Bucket to
    - ListRoles IAM Policy
    - Federated QuickSight IAM Policy + Role
    - OktaSSOUser IAM User
    - Lambda Layer
    - 3 Lambda Functions
    - 2 S3 Event Sources
    - 1 Event Rule
"""

import os
import subprocess as sp
from aws_cdk import (
    aws_iam as iam,
    aws_s3 as s3,
    aws_lambda as _lambda,
    aws_lambda_event_sources as lambda_event_sources,
    aws_events as events,
    aws_events_targets as events_targets,
    aws_s3_deployment as s3_deploy,
    core,
)
import config as cf


class QSGovernanceStack(core.Stack):
    """
    AWS CDK Stack to deploy QuickSight Governance solution in an AWS Account
    """

    def __init__(self, scope: core.Construct, construct_id: str, **kwargs) -> None:
        """
        initialize function for CDK
        """
        super().__init__(scope, construct_id, **kwargs)

        # -------------------------------
        # S3 Bucket for Manifests
        # -------------------------------

        qs_gov_bucket = s3.Bucket(
            self,
            id=f"{cf.PROJECT}-ManifestBucket",
        )
        bucket_name = qs_gov_bucket.bucket_name

        # -------------------------------
        # IAM
        # -------------------------------

        list_roles_policy = iam.ManagedPolicy(
            self,
            id=f"{cf.PROJECT}-ListRolesPolicy",
            description=None,
            managed_policy_name=None,
            path="/",
            statements=[
                iam.PolicyStatement(
                    effect=iam.Effect.ALLOW,
                    resources=["*"],
                    actions=["iam:ListRoles", "iam:ListAccountAliases"],
                )
            ],
        )

        federated_quicksight_policy = iam.ManagedPolicy(
            self,
            id=f"{cf.PROJECT}-FederatedQuickSightPolicy",
            managed_policy_name=f"{cf.PROJECT}-FederatedQuickSightPolicy",
            statements=[
                iam.PolicyStatement(
                    effect=iam.Effect.ALLOW,
                    resources=[
                        f"arn:aws:iam::{cf.ACCOUNT}:saml-provider/{cf.OKTA_IDP_NAME}"
                    ],
                    actions=["sts:AssumeRoleWithSAML"],
                    conditions={
                        "StringEquals": {
                            "saml:aud": "https://signin.aws.amazon.com/saml"
                        }
                    },
                )
            ],
        )

        okta_federated_principal = iam.FederatedPrincipal(
            federated=f"arn:aws:iam::{cf.ACCOUNT}:saml-provider/{cf.OKTA_IDP_NAME}",
            assume_role_action="sts:AssumeRoleWithSAML",
            conditions={
                "StringEquals": {"SAML:aud": "https://signin.aws.amazon.com/saml"}
            },
        )

        federated_quicksight_role = iam.Role(
            self,
            id=f"{cf.PROJECT}-{cf.OKTA_ROLE_NAME}",
            role_name=f"{cf.PROJECT}-{cf.OKTA_ROLE_NAME}",
            assumed_by=okta_federated_principal,
            description="Allow Okta to Federate Login & User Creation to QuickSight",
            managed_policies=[federated_quicksight_policy],
        )


        iam.User(
            self,
            id=f"{cf.PROJECT}-OktaSSOUser",
            user_name=f"{cf.PROJECT}-OktaSSOUser",
            managed_policies=[list_roles_policy],
        )


        # -------------------------------
        # Lambda Functions
        # -------------------------------

        # iam role for Lambdas

        qs_governance_policy = iam.ManagedPolicy(
            self,
            id=f"{cf.PROJECT}-QuickSightGovernancePolicy",
            managed_policy_name=f"{cf.PROJECT}-QuickSightGovernancePolicy",
            statements=[
                iam.PolicyStatement(
                    effect=iam.Effect.ALLOW,
                    resources=[
                        f"arn:aws:secretsmanager:{cf.REGION}:{cf.ACCOUNT}:secret:{cf.OKTA_SECRET}*"
                    ],
                    actions=[
                        "secretsmanager:GetSecretValue",
                    ],
                ),
                iam.PolicyStatement(
                    effect=iam.Effect.ALLOW,
                    resources=["*"],
                    actions=["quicksight:*", "ds:*"],
                ),
                iam.PolicyStatement(
                    effect=iam.Effect.ALLOW,
                    resources=[f"arn:aws:s3:::{bucket_name}/*"],
                    actions=["s3:Get*", "s3:Put*"],
                ),
            ],
        )

        quicksight_permission_mapping_role = iam.Role(
            self,
            id=f"{cf.PROJECT}-QuickSightPermissionMappingRole",
            assumed_by=iam.ServicePrincipal("lambda.amazonaws.com"),
            managed_policies=[
                iam.ManagedPolicy.from_aws_managed_policy_name(
                    "service-role/AWSLambdaBasicExecutionRole"
                ),
                qs_governance_policy,
            ],
        )

        # Lambdas

        get_okta_info_lambda = _lambda.Function(
            self,
            id=f"{cf.PROJECT}-GetOktaInfo",
            handler="get_okta_info.handler",
            role=quicksight_permission_mapping_role,
            runtime=_lambda.Runtime.PYTHON_3_8,
            code=_lambda.Code.asset(os.path.join(cf.PATH_SRC, "pkg")),
            function_name=f"{cf.PROJECT}-GetOktaInfo",
            environment={
                "OKTA_SECRET": cf.OKTA_SECRET,
                "OKTA_ROLE_NAME": cf.OKTA_ROLE_NAME,
                "QS_GOVERNANCE_BUCKET": bucket_name,
                "QS_USER_GOVERNANCE_KEY": cf.QS_USER_GOVERNANCE_KEY,
            },
            memory_size=256,
            timeout=core.Duration.seconds(180),
        )

        # Lamda Okta to QuickSight Mappers

        qs_user_governance_lambda = _lambda.Function(
            self,
            id=f"{cf.PROJECT}-QSUserGovernance",
            handler="qs_user_gov.handler",
            role=quicksight_permission_mapping_role,
            runtime=_lambda.Runtime.PYTHON_3_8,
            code=_lambda.Code.asset(os.path.join(cf.PATH_SRC, "pkg")),
            function_name=f"{cf.PROJECT}-QSUserGovernance",
            environment={
                "OKTA_ROLE_NAME": f"{cf.PROJECT}-{cf.OKTA_ROLE_NAME}",
                "QS_GOVERNANCE_BUCKET": bucket_name,
                "QS_USER_GOVERNANCE_KEY": cf.QS_USER_GOVERNANCE_KEY,
                "OKTA_GROUP_QS_PREFIX": cf.OKTA_GROUP_QS_PREFIX,
                "QS_ADMIN_OKTA_GROUP": cf.QS_ADMIN_OKTA_GROUP,
                "QS_AUTHOR_OKTA_GROUP": cf.QS_AUTHOR_OKTA_GROUP,
                "QS_READER_OKTA_GROUP": cf.QS_READER_OKTA_GROUP
            },
            memory_size=256,
            timeout=core.Duration.seconds(180),
        )

        qs_asset_governance_lambda = _lambda.Function(
            self,
            id=f"{cf.PROJECT}-QSAssetGovernance",
            handler="qs_asset_gov.handler",
            role=quicksight_permission_mapping_role,
            runtime=_lambda.Runtime.PYTHON_3_8,
            code=_lambda.Code.asset(os.path.join(cf.PATH_SRC, "pkg")),
            function_name=f"{cf.PROJECT}-QSAssetGovernance",
            environment={
                "QS_GOVERNANCE_BUCKET": bucket_name,
                "QS_ASSET_GOVERNANCE_KEY": cf.QS_ASSET_GOVERNANCE_KEY,
            },
            memory_size=256,
            timeout=core.Duration.seconds(180),
        )

        # -------------------------------
        # Events
        # -------------------------------

        qs_user_governance_lambda.add_event_source(
            lambda_event_sources.S3EventSource(
                bucket=qs_gov_bucket,
                events=[s3.EventType.OBJECT_CREATED],
                filters=[s3.NotificationKeyFilter(prefix=cf.QS_USER_GOVERNANCE_KEY)],
            )
        )

        qs_asset_governance_lambda.add_event_source(
            lambda_event_sources.S3EventSource(
                bucket=qs_gov_bucket,
                events=[s3.EventType.OBJECT_CREATED],
                filters=[s3.NotificationKeyFilter(prefix=cf.QS_ASSET_GOVERNANCE_KEY)],
            )
        )

        lambda_schedule = events.Schedule.rate(core.Duration.days(1))
        get_okta_info_target = events_targets.LambdaFunction(
            handler=get_okta_info_lambda
        )
        events.Rule(
            self,
            id=f"{cf.PROJECT}-GetOktaInfoScheduledEvent",
            description="The once per day CloudWatch event trigger for the Lambda",
            enabled=True,
            schedule=lambda_schedule,
            targets=[get_okta_info_target],
        )

        # -------------------------------
        # S3 Object Deployment - QS Asset Manifest
        # -------------------------------

        asset_manifest_deploy = s3_deploy.BucketDeployment(
            self,
            id=f"{cf.PROJECT}-AssetManifestDeploy",
            sources=[s3_deploy.Source.asset(
                os.path.join(cf.PATH_ROOT, 'qs_config')
            )],
            destination_bucket=qs_gov_bucket
        )
