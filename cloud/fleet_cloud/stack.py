"""Ring 2 cloud: DynamoDB state, S3 data lake, IoT rules, route planner, task dispatch stub.

Message flow (see shared/fleet_shared/topics.py):
  fleet/+/telemetry, fleet/+/event, fleet/+/pose(sampled) -> ingest Lambda -> Forklifts table
  fleet/+/telemetry, fleet/+/event                        -> S3 raw/
  fleet/+/goto                                            -> route_planner Lambda -> fleet/{id}/path
  tasks/new                                               -> dispatch Lambda -> tasks/{id}/status
"""

import aws_cdk as cdk
from aws_cdk import (
    CfnOutput,
    Duration,
    RemovalPolicy,
    Stack,
    aws_dynamodb as ddb,
    aws_iam as iam,
    aws_iot as iot,
    aws_lambda as lambda_,
    aws_logs as logs,
    aws_s3 as s3,
    custom_resources as cr,
)
from constructs import Construct

from .dashboard import Dashboard
from .simfleet import SimFleet


class FleetStack(Stack):
    def __init__(self, scope: Construct, cid: str, *, lambda_code_dir: str, sim_code_dir: str, **kw):
        super().__init__(scope, cid, **kw)

        # ---------------- State: DynamoDB (what's true right now) ----------------
        def table(name: str, pk: str, ttl: str | None = None) -> ddb.TableV2:
            return ddb.TableV2(
                self, name,
                table_name=f"Fleet{name}",
                partition_key=ddb.Attribute(name=pk, type=ddb.AttributeType.STRING),
                billing=ddb.Billing.on_demand(),
                time_to_live_attribute=ttl,
                removal_policy=RemovalPolicy.DESTROY,  # hackathon: cdk destroy cleans up
            )

        forklifts = table("Forklifts", "forklift_id")
        tasks = table("Tasks", "task_id")
        edge_locks = table("EdgeLocks", "edge_id", ttl="expires_at")
        pallets = table("Pallets", "pallet_id")
        episodes = table("Episodes", "task_id")
        planners = table("Planners", "version")

        # ---------------- History: S3 (everything that ever happened) ------------
        bucket = s3.Bucket(
            self, "DataLake",
            block_public_access=s3.BlockPublicAccess.BLOCK_ALL,
            encryption=s3.BucketEncryption.S3_MANAGED,
            enforce_ssl=True,
            removal_policy=RemovalPolicy.DESTROY,
            auto_delete_objects=True,
        )

        # IoT data endpoint (account-specific) so Lambdas can publish back to MQTT.
        endpoint = cr.AwsCustomResource(
            self, "IotEndpoint",
            on_update=cr.AwsSdkCall(
                service="Iot", action="describeEndpoint",
                parameters={"endpointType": "iot:Data-ATS"},
                physical_resource_id=cr.PhysicalResourceId.of("iot-data-ats"),
            ),
            policy=cr.AwsCustomResourcePolicy.from_sdk_calls(
                resources=cr.AwsCustomResourcePolicy.ANY_RESOURCE),
            install_latest_aws_sdk=False,
        ).get_response_field("endpointAddress")

        # ---------------- Lambdas ------------------------------------------------
        code = lambda_.Code.from_asset(lambda_code_dir)
        env = {
            "FORKLIFTS_TABLE": forklifts.table_name,
            "TASKS_TABLE": tasks.table_name,
            "EDGE_LOCKS_TABLE": edge_locks.table_name,
            "PALLETS_TABLE": pallets.table_name,
            "PLANNERS_TABLE": planners.table_name,
            "EPISODES_TABLE": episodes.table_name,
            # demo = 1.5 x 1 m map for the simulated fleet; table = the real tabletop.
            # Switch without redeploying: uv run python cloud/scripts/use_map.py table
            "WAREHOUSE_MAP": self.node.try_get_context("warehouse_map") or "demo",
            "DATA_BUCKET": bucket.bucket_name,
            "IOT_ENDPOINT": endpoint,
        }

        def fn(name: str, handler: str, timeout: int = 10) -> lambda_.Function:
            f = lambda_.Function(
                self, name,
                runtime=lambda_.Runtime.PYTHON_3_12,
                architecture=lambda_.Architecture.ARM_64,
                handler=handler,
                code=code,
                timeout=Duration.seconds(timeout),
                memory_size=256,
                environment=env,
                log_group=logs.LogGroup(self, f"{name}Logs",
                                        retention=logs.RetentionDays.ONE_WEEK,
                                        removal_policy=RemovalPolicy.DESTROY),
            )
            return f

        publish_policy = iam.PolicyStatement(
            actions=["iot:Publish"],
            resources=[
                self.format_arn(service="iot", resource="topic", resource_name="fleet/*"),
                self.format_arn(service="iot", resource="topic", resource_name="tasks/*"),
                self.format_arn(service="iot", resource="topic", resource_name="warehouse/*"),
                self.format_arn(service="iot", resource="topic", resource_name="planners/*"),
            ],
        )

        ingest = fn("Ingest", "ingest.handler")
        forklifts.grant_read_write_data(ingest)
        tasks.grant_read_write_data(ingest)
        pallets.grant_read_write_data(ingest)  # tree runner updates pallet locations on Pick/Drop
        edge_locks.grant_read_write_data(ingest)  # traffic control: reservations
        episodes.grant_write_data(ingest)         # tree runner records episodes
        bucket.grant_put(ingest, "episodes/*")
        ingest.add_to_role_policy(publish_policy)

        planner = fn("RoutePlanner", "route_planner.handler")
        forklifts.grant_read_write_data(planner)
        tasks.grant_read_write_data(planner)
        edge_locks.grant_read_write_data(planner)
        planner.add_to_role_policy(publish_policy)

        dispatch = fn("Dispatch", "dispatch.handler", timeout=60)
        tasks.grant_read_write_data(dispatch)
        forklifts.grant_read_write_data(dispatch)
        pallets.grant_read_write_data(dispatch)
        planners.grant_read_write_data(dispatch)  # shadow win/run counters
        edge_locks.grant_read_write_data(dispatch)
        episodes.grant_write_data(dispatch)
        bucket.grant_put(dispatch, "episodes/*")
        dispatch.add_to_role_policy(publish_policy)
        # Ring 3: Claude on Bedrock writes behavior trees. Converse = bedrock:InvokeModel.
        # `us.` inference profiles route to Claude models in US Regions, so the foundation-model
        # ARN needs a Region wildcard; the profile itself is in this Region.
        dispatch.add_to_role_policy(iam.PolicyStatement(
            actions=["bedrock:InvokeModel"],
            resources=[
                self.format_arn(service="bedrock", resource="inference-profile", resource_name="us.anthropic.claude-*"),
                "arn:aws:bedrock:*::foundation-model/anthropic.claude-*",
            ],
        ))

        api = fn("Api", "api.handler")
        for t in (forklifts, tasks, pallets, edge_locks):
            t.grant_read_write_data(api)  # read for the dashboard; write for POST /demo/reset
        planners.grant_read_write_data(api)  # promote
        api.add_to_role_policy(publish_policy)
        # Demo person / hazard is a retained message on warehouse/detections (needs RetainPublish).
        api.add_to_role_policy(iam.PolicyStatement(
            actions=["iot:RetainPublish"],
            resources=[self.format_arn(service="iot", resource="topic", resource_name="warehouse/*")]))
        Dashboard(self, "Dashboard", api_fn=api)
        SimFleet(self, "SimFleet", code_dir=sim_code_dir, forklifts_table=forklifts, api_fn=api)

        # ---------------- IoT rules ----------------------------------------------
        rule_errors = logs.LogGroup(self, "IotRuleErrors",
                                    retention=logs.RetentionDays.ONE_WEEK,
                                    removal_policy=RemovalPolicy.DESTROY)
        rule_role = iam.Role(self, "IotRuleRole",
                             assumed_by=iam.ServicePrincipal("iot.amazonaws.com"))
        rule_errors.grant_write(rule_role)
        bucket.grant_put(rule_role, "raw/*")
        error_action = iot.CfnTopicRule.ActionProperty(
            cloudwatch_logs=iot.CfnTopicRule.CloudwatchLogsActionProperty(
                log_group_name=rule_errors.log_group_name, role_arn=rule_role.role_arn))

        def lambda_rule(rid: str, sql: str, target: lambda_.Function):
            rule = iot.CfnTopicRule(self, rid, topic_rule_payload=iot.CfnTopicRule.TopicRulePayloadProperty(
                sql=sql, aws_iot_sql_version="2016-03-23",
                actions=[iot.CfnTopicRule.ActionProperty(
                    lambda_=iot.CfnTopicRule.LambdaActionProperty(function_arn=target.function_arn))],
                error_action=error_action,
            ))
            target.add_permission(f"{rid}Invoke",
                                  principal=iam.ServicePrincipal("iot.amazonaws.com"),
                                  source_arn=rule.attr_arn,
                                  source_account=self.account)

        def s3_rule(rid: str, sql: str, prefix: str):
            iot.CfnTopicRule(self, rid, topic_rule_payload=iot.CfnTopicRule.TopicRulePayloadProperty(
                sql=sql, aws_iot_sql_version="2016-03-23",
                actions=[iot.CfnTopicRule.ActionProperty(s3=iot.CfnTopicRule.S3ActionProperty(
                    bucket_name=bucket.bucket_name, role_arn=rule_role.role_arn,
                    key=f"{prefix}/${{parse_time(\"yyyy-MM-dd\", timestamp())}}/${{topic(2)}}/${{timestamp()}}-${{newuuid()}}.json"))],
                error_action=error_action,
            ))

        lambda_rule("IngestTelemetryRule",
                    "SELECT *, topic(2) AS forklift_id, 'telemetry' AS kind FROM 'fleet/+/telemetry'", ingest)
        lambda_rule("IngestEventRule",
                    "SELECT *, topic(2) AS forklift_id, 'event' AS kind FROM 'fleet/+/event'", ingest)
        # Pose is 10 Hz; keep ~1 in 10 for the Forklifts table (the dashboard reads live pose from MQTT).
        lambda_rule("IngestPoseRule",
                    "SELECT *, topic(2) AS forklift_id, 'pose' AS kind FROM 'fleet/+/pose' WHERE ts % 1000 < 100",
                    ingest)
        lambda_rule("GotoRule",
                    "SELECT *, topic(2) AS forklift_id FROM 'fleet/+/goto'", planner)
        lambda_rule("TaskNewRule", "SELECT * FROM 'tasks/new'", dispatch)
        s3_rule("ArchiveTelemetryRule", "SELECT *, topic(2) AS forklift_id FROM 'fleet/+/telemetry'", "raw/telemetry")
        s3_rule("ArchiveEventRule", "SELECT *, topic(2) AS forklift_id FROM 'fleet/+/event'", "raw/events")

        # ---------------- Edge device identity -----------------------------------
        # The certificate itself is created by cloud/scripts/create_edge_cert.py (keeps the
        # private key off CloudFormation). This policy limits the edge to fleet topics.
        topic = lambda p: self.format_arn(service="iot", resource="topic", resource_name=p)
        topicfilter = lambda p: self.format_arn(service="iot", resource="topicfilter", resource_name=p)
        iot.CfnPolicy(self, "EdgePolicy", policy_name="ForkliftEdgePolicy", policy_document={
            "Version": "2012-10-17",
            "Statement": [
                {"Effect": "Allow", "Action": "iot:Connect",
                 "Resource": self.format_arn(service="iot", resource="client", resource_name="edge-*")},
                {"Effect": "Allow", "Action": "iot:Publish",
                 "Resource": [topic("fleet/*"), topic("warehouse/*")]},
                {"Effect": "Allow", "Action": "iot:Subscribe",
                 "Resource": [topicfilter("fleet/*"), topicfilter("warehouse/*")]},
                {"Effect": "Allow", "Action": "iot:Receive",
                 "Resource": [topic("fleet/*"), topic("warehouse/*")]},
            ],
        })
        iot.CfnThing(self, "EdgeThing", thing_name="forklift-edge-01")

        # ---------------- MLOps: SageMaker Processing role (mlops/run_sagemaker.py) -----
        sm_role = iam.Role(self, "SageMakerEvalRole", assumed_by=iam.ServicePrincipal("sagemaker.amazonaws.com"),
                           description="Offline planner evaluation (mlops/evaluate.py) as a SageMaker Processing job")
        bucket.grant_read(sm_role, "episodes/*")
        bucket.grant_read(sm_role, "mlops-code/*")
        bucket.grant_read_write(sm_role, "evalsets/*")
        bucket.grant_read_write(sm_role, "planners/*")
        sm_role.add_to_policy(iam.PolicyStatement(actions=["s3:ListBucket"], resources=[bucket.bucket_arn]))
        planners.grant_read_write_data(sm_role)
        sm_role.add_to_policy(iam.PolicyStatement(
            actions=["bedrock:InvokeModel"],
            resources=[self.format_arn(service="bedrock", resource="inference-profile", resource_name="us.anthropic.claude-*"),
                       "arn:aws:bedrock:*::foundation-model/anthropic.claude-*"]))
        sm_role.add_to_policy(iam.PolicyStatement(
            actions=["logs:CreateLogGroup", "logs:CreateLogStream", "logs:PutLogEvents", "logs:DescribeLogStreams"],
            resources=[self.format_arn(service="logs", resource="log-group", resource_name="/aws/sagemaker/*",
                                       arn_format=cdk.ArnFormat.COLON_RESOURCE_NAME)]))
        sm_role.add_to_policy(iam.PolicyStatement(actions=["cloudwatch:PutMetricData"], resources=["*"]))
        CfnOutput(self, "SageMakerRoleArn", value=sm_role.role_arn)

        CfnOutput(self, "IotEndpointOut", value=endpoint)
        CfnOutput(self, "DataBucketName", value=bucket.bucket_name)
