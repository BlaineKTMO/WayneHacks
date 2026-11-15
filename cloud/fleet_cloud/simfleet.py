"""Simulated forklifts hosted in AWS (ECS Fargate), so a demo needs no laptop running a script.

Runs tools/sim_fleet.py unchanged. No Docker build: the stock Python image installs two
packages, downloads the code (a CDK asset) and starts. Desired count is 0; the dashboard's
"Simulated fleet" switch (POST /sim) sets it to 1. Public subnets only, no NAT gateway, so
there is no idle cost. Never run this and a local sim_fleet.py at the same time.
"""

from aws_cdk import (
    Stack,
    aws_ec2 as ec2,
    aws_ecs as ecs,
    aws_iam as iam,
    aws_lambda as lambda_,
    aws_logs as logs,
    aws_s3_assets as s3_assets,
    RemovalPolicy,
)
from constructs import Construct

BOOT = (
    "set -e; pip install -q --root-user-action=ignore boto3 awsiotsdk; "
    "python - <<'PY'\n"
    "import boto3, io, os, zipfile\n"
    "b = boto3.client('s3').get_object(Bucket=os.environ['CODE_BUCKET'], Key=os.environ['CODE_KEY'])['Body'].read()\n"
    "zipfile.ZipFile(io.BytesIO(b)).extractall('/app')\n"
    "PY\n"
    "cd /app && exec python -u sim_fleet.py"
)


class SimFleet(Construct):
    def __init__(self, scope: Construct, cid: str, *, code_dir: str, forklifts_table, api_fn: lambda_.Function,
                 site: str = "demo"):
        super().__init__(scope, cid)
        stack = Stack.of(self)
        arn = lambda kind, name: stack.format_arn(service="iot", resource=kind, resource_name=name)

        vpc = ec2.Vpc(self, "Vpc", max_azs=2, nat_gateways=0,
                      subnet_configuration=[ec2.SubnetConfiguration(name="public", subnet_type=ec2.SubnetType.PUBLIC)])
        cluster = ecs.Cluster(self, "Cluster", vpc=vpc)
        code = s3_assets.Asset(self, "Code", path=code_dir)

        task = ecs.FargateTaskDefinition(
            self, "Task", cpu=256, memory_limit_mib=512,
            runtime_platform=ecs.RuntimePlatform(cpu_architecture=ecs.CpuArchitecture.ARM64,
                                                 operating_system_family=ecs.OperatingSystemFamily.LINUX))
        code.grant_read(task.task_role)
        forklifts_table.grant_read_data(task.task_role)
        task.task_role.add_to_policy(iam.PolicyStatement(actions=["iot:DescribeEndpoint"], resources=["*"]))
        task.task_role.add_to_policy(iam.PolicyStatement(actions=["iot:Connect"], resources=[arn("client", "sim-*")]))
        task.task_role.add_to_policy(iam.PolicyStatement(actions=["iot:Publish"], resources=[arn("topic", f"{site}/fleet/*"), arn("topic", f"{site}/warehouse/health/*")]))
        task.task_role.add_to_policy(iam.PolicyStatement(actions=["iot:RetainPublish"], resources=[arn("topic", f"{site}/warehouse/health/*")]))
        task.task_role.add_to_policy(iam.PolicyStatement(actions=["iot:Receive"],
                                                         resources=[arn("topic", f"{site}/fleet/*"), arn("topic", f"{site}/warehouse/*")]))
        task.task_role.add_to_policy(iam.PolicyStatement(actions=["iot:Subscribe"],
                                                         resources=[arn("topicfilter", f"{site}/fleet/*"), arn("topicfilter", f"{site}/warehouse/*")]))
        task.add_container(
            "sim",
            image=ecs.ContainerImage.from_registry("public.ecr.aws/docker/library/python:3.12-slim"),
            command=["sh", "-c", BOOT],
            environment={"CODE_BUCKET": code.s3_bucket_name, "CODE_KEY": code.s3_object_key, "AWS_DEFAULT_REGION": stack.region,
                         "SITE": site, "FORKLIFTS_TABLE": forklifts_table.table_name},
            logging=ecs.LogDrivers.aws_logs(stream_prefix="sim", log_group=logs.LogGroup(
                self, "Logs", retention=logs.RetentionDays.ONE_WEEK, removal_policy=RemovalPolicy.DESTROY)),
        )
        service = ecs.FargateService(
            self, "Service", cluster=cluster, task_definition=task, desired_count=0, assign_public_ip=True,
            vpc_subnets=ec2.SubnetSelection(subnet_type=ec2.SubnetType.PUBLIC),
            min_healthy_percent=0, max_healthy_percent=100,  # never two sims at once during a deploy
            circuit_breaker=ecs.DeploymentCircuitBreaker(rollback=False))

        api_fn.add_environment("SIM_CLUSTER", cluster.cluster_name)
        api_fn.add_environment("SIM_SERVICE", service.service_name)
        api_fn.add_to_role_policy(iam.PolicyStatement(actions=["ecs:UpdateService", "ecs:DescribeServices"],
                                                      resources=[service.service_arn]))
