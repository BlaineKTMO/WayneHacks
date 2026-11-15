"""Dashboard backend: Cognito sign-in, browser MQTT access, HTTP API, Amplify Hosting app.

Browser flow:
  1. Sign in (user pool) -> ID token.
  2. Identity pool swaps the token for temporary AWS credentials (DashboardUserRole).
  3. POST /iot/attach once: attaches DashboardIotPolicy to the caller's Cognito identity
     (IoT Core requires both the IAM role AND an IoT policy for Cognito identities).
  4. Connect to IoT Core over WebSocket (SigV4) for live data; call the HTTP API for state.
"""

from aws_cdk import (
    CfnOutput,
    Duration,
    RemovalPolicy,
    Stack,
    aws_amplify as amplify,
    aws_apigatewayv2 as apigw,
    aws_apigatewayv2_authorizers as authorizers,
    aws_apigatewayv2_integrations as integrations,
    aws_cognito as cognito,
    aws_iam as iam,
    aws_iot as iot,
    aws_lambda as lambda_,
)
from constructs import Construct


class Dashboard(Construct):
    def __init__(self, scope: Construct, cid: str, *, api_fn: lambda_.Function):
        super().__init__(scope, cid)
        stack = Stack.of(self)
        arn = lambda kind, name: stack.format_arn(service="iot", resource=kind, resource_name=name)

        # ---------------- Sign-in: team members only -----------------------------
        pool = cognito.UserPool(
            self, "Users",
            user_pool_name="ForkliftDashboardUsers",
            self_sign_up_enabled=False,
            sign_in_aliases=cognito.SignInAliases(email=True),
            password_policy=cognito.PasswordPolicy(min_length=12),
            removal_policy=RemovalPolicy.DESTROY,
        )
        client = pool.add_client(
            "Web",
            auth_flows=cognito.AuthFlow(user_srp=True),
            prevent_user_existence_errors=True,
            id_token_validity=Duration.hours(8),
        )

        # ---------------- Browser AWS credentials (identity pool) ----------------
        identity_pool = cognito.CfnIdentityPool(
            self, "Identities",
            allow_unauthenticated_identities=False,
            cognito_identity_providers=[cognito.CfnIdentityPool.CognitoIdentityProviderProperty(
                client_id=client.user_pool_client_id,
                provider_name=pool.user_pool_provider_name)],
        )
        user_role = iam.Role(
            self, "UserRole",
            assumed_by=iam.FederatedPrincipal(
                "cognito-identity.amazonaws.com",
                conditions={
                    "StringEquals": {"cognito-identity.amazonaws.com:aud": identity_pool.ref},
                    "ForAnyValue:StringLike": {"cognito-identity.amazonaws.com:amr": "authenticated"},
                },
                assume_role_action="sts:AssumeRoleWithWebIdentity"),
        )
        # Read everything live; publish only commands. Browsers can never fake telemetry.
        iot_statements = [
            {"Effect": "Allow", "Action": "iot:Connect", "Resource": arn("client", "dash-*")},
            {"Effect": "Allow", "Action": "iot:Subscribe",
             "Resource": [arn("topicfilter", "fleet/*"), arn("topicfilter", "warehouse/*"), arn("topicfilter", "tasks/*"),
                          arn("topicfilter", "planners/*")]},
            {"Effect": "Allow", "Action": "iot:Receive",
             "Resource": [arn("topic", "fleet/*"), arn("topic", "warehouse/*"), arn("topic", "tasks/*"),
                          arn("topic", "planners/*")]},
            {"Effect": "Allow", "Action": "iot:Publish",
             "Resource": [arn("topic", "tasks/new"), arn("topic", "fleet/*/goto")]},
        ]
        for s in iot_statements:
            user_role.add_to_policy(iam.PolicyStatement(
                actions=[s["Action"]],
                resources=s["Resource"] if isinstance(s["Resource"], list) else [s["Resource"]]))
        cognito.CfnIdentityPoolRoleAttachment(
            self, "Roles", identity_pool_id=identity_pool.ref,
            roles={"authenticated": user_role.role_arn})
        iot_policy = iot.CfnPolicy(self, "IotPolicy", policy_name="DashboardIotPolicy",
                                   policy_document={"Version": "2012-10-17", "Statement": iot_statements})

        # ---------------- HTTP API ----------------------------------------------
        api_fn.add_environment("USER_POOL_PROVIDER", pool.user_pool_provider_name)
        api_fn.add_environment("IDENTITY_POOL_ID", identity_pool.ref)
        api_fn.add_environment("DASHBOARD_IOT_POLICY", iot_policy.policy_name)
        # The policy name is fixed in code (api.py reads it from env); the target is the caller's
        # identity, which api.py derives from their own token.
        api_fn.add_to_role_policy(iam.PolicyStatement(actions=["iot:AttachPolicy"], resources=["*"]))

        http = apigw.HttpApi(
            self, "Api",
            api_name="ForkliftDashboardApi",
            cors_preflight=apigw.CorsPreflightOptions(
                allow_origins=["*"],
                allow_headers=["authorization", "content-type"],
                allow_methods=[apigw.CorsHttpMethod.GET, apigw.CorsHttpMethod.POST]),
            default_authorizer=authorizers.HttpUserPoolAuthorizer(
                "Jwt", pool, user_pool_clients=[client]),
        )
        for method, path in [
            (apigw.HttpMethod.GET, "/graph"),
            (apigw.HttpMethod.GET, "/forklifts"),
            (apigw.HttpMethod.GET, "/pallets"),
            (apigw.HttpMethod.GET, "/tasks"),
            (apigw.HttpMethod.GET, "/tasks/{task_id}"),
            (apigw.HttpMethod.POST, "/forklifts/{forklift_id}/stop"),
            (apigw.HttpMethod.POST, "/iot/attach"),
            (apigw.HttpMethod.GET, "/planners"),
            (apigw.HttpMethod.GET, "/planners/compare"),
            (apigw.HttpMethod.POST, "/planners/{version}/promote"),
            (apigw.HttpMethod.GET, "/sim"),
            (apigw.HttpMethod.POST, "/sim"),
            (apigw.HttpMethod.POST, "/hazard"),
            (apigw.HttpMethod.POST, "/demo/reset"),
        ]:
            http.add_routes(path=path, methods=[method],
                            integration=integrations.HttpLambdaIntegration(f"R{method.value}{path}", api_fn))

        # ---------------- Hosting (deployed by dashboard/deploy.py) -------------
        app = amplify.CfnApp(self, "Hosting", name="forklift-dashboard", platform="WEB",
                             custom_rules=[amplify.CfnApp.CustomRuleProperty(
                                 source="</^[^.]+$|\\.(?!(css|gif|ico|jpg|js|png|txt|svg|woff2?|ttf|map|json)$)([^.]+$)/>",
                                 target="/index.html", status="200")])
        branch = amplify.CfnBranch(self, "Main", app_id=app.attr_app_id, branch_name="main", stage="PRODUCTION")

        CfnOutput(stack, "UserPoolId", value=pool.user_pool_id)
        CfnOutput(stack, "UserPoolClientId", value=client.user_pool_client_id)
        CfnOutput(stack, "IdentityPoolId", value=identity_pool.ref)
        CfnOutput(stack, "DashboardApiUrl", value=http.api_endpoint)
        CfnOutput(stack, "AmplifyAppId", value=app.attr_app_id)
        CfnOutput(stack, "DashboardUrl", value=f"https://{branch.branch_name}.{app.attr_default_domain}")
