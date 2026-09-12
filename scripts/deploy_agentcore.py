import argparse
import io
import json
import time
import zipfile
from pathlib import Path
from uuid import uuid4

import boto3

REGION = "us-west-2"
NAME = "tabvio_agent"
REPOSITORY = "tabvio-agentcore"
ROOT = Path(__file__).resolve().parent.parent


def allow(actions, resources):
    return {"Effect": "Allow", "Action": actions, "Resource": resources}


def role(iam, name, service, statements, account):
    trust = {"Version": "2012-10-17", "Statement": [{
        "Effect": "Allow", "Principal": {"Service": service}, "Action": "sts:AssumeRole",
        "Condition": {"StringEquals": {"aws:SourceAccount": account}},
    }]}
    try:
        result = iam.create_role(RoleName=name, AssumeRolePolicyDocument=json.dumps(trust))["Role"]
    except iam.exceptions.EntityAlreadyExistsException:
        result = iam.get_role(RoleName=name)["Role"]
    iam.put_role_policy(RoleName=name, PolicyName="TabvioAccess", PolicyDocument=json.dumps({
        "Version": "2012-10-17", "Statement": statements,
    }))
    return result["Arn"]


def build():
    account = boto3.client("sts", region_name=REGION).get_caller_identity()["Account"]
    iam = boto3.client("iam", region_name=REGION)
    ecr = boto3.client("ecr", region_name=REGION)
    s3 = boto3.client("s3", region_name=REGION)
    codebuild = boto3.client("codebuild", region_name=REGION)
    bucket = f"tabvio-agentcore-build-{account}-{REGION}"
    try:
        s3.create_bucket(Bucket=bucket, CreateBucketConfiguration={"LocationConstraint": REGION})
    except s3.exceptions.BucketAlreadyOwnedByYou:
        pass
    s3.put_public_access_block(Bucket=bucket, PublicAccessBlockConfiguration={
        "BlockPublicAcls": True, "IgnorePublicAcls": True,
        "BlockPublicPolicy": True, "RestrictPublicBuckets": True,
    })
    s3.put_bucket_lifecycle_configuration(Bucket=bucket, LifecycleConfiguration={"Rules": [{
        "ID": "ExpireBuildSources", "Status": "Enabled", "Filter": {"Prefix": "source/"},
        "Expiration": {"Days": 7},
    }]})
    try:
        repository = ecr.create_repository(repositoryName=REPOSITORY)["repository"]
    except ecr.exceptions.RepositoryAlreadyExistsException:
        repository = ecr.describe_repositories(repositoryNames=[REPOSITORY])["repositories"][0]
    image = f"{repository['repositoryUri']}:{uuid4().hex[:12]}"
    build_role = role(iam, "TabvioAgentCoreBuild", "codebuild.amazonaws.com", [
        allow(["s3:GetObject", "s3:GetObjectVersion"], [f"arn:aws:s3:::{bucket}/source/*"]),
        allow(["s3:GetBucketLocation"], [f"arn:aws:s3:::{bucket}"]),
        allow(["ecr:GetAuthorizationToken"], "*"),
        allow(["ecr:BatchCheckLayerAvailability", "ecr:InitiateLayerUpload", "ecr:UploadLayerPart",
               "ecr:CompleteLayerUpload", "ecr:PutImage"], repository["repositoryArn"]),
        allow(["logs:CreateLogGroup", "logs:CreateLogStream", "logs:PutLogEvents"],
              f"arn:aws:logs:{REGION}:{account}:log-group:/aws/codebuild/tabvio-agentcore*"),
    ], account)
    archive = io.BytesIO()
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as bundle:
        for name in ["pyproject.toml", "uv.lock", "Dockerfile.agentcore"]:
            bundle.write(ROOT / name, name)
        for path in (ROOT / "tabvio").rglob("*"):
            if path.is_file() and path.suffix in {".py", ".js", ".html", ".css", ".md"}:
                bundle.write(path, path.relative_to(ROOT).as_posix())
    key = f"source/{uuid4().hex}.zip"
    s3.put_object(Bucket=bucket, Key=key, Body=archive.getvalue(), ServerSideEncryption="AES256")
    registry = repository["repositoryUri"].split("/")[0]
    specification = {"version": 0.2, "phases": {"build": {"commands": [
        f"aws ecr get-login-password --region {REGION} | docker login --username AWS --password-stdin {registry}",
        f"docker build -f Dockerfile.agentcore -t {image} .",
        f"docker push {image}",
    ]}}}
    config = dict(name="tabvio-agentcore", serviceRole=build_role,
                  source={"type": "S3", "location": f"{bucket}/{key}", "buildspec": json.dumps(specification)},
                  artifacts={"type": "NO_ARTIFACTS"}, timeoutInMinutes=20,
                  environment={"type": "ARM_CONTAINER", "image": "aws/codebuild/amazonlinux-aarch64-standard:3.0",
                               "computeType": "BUILD_GENERAL1_SMALL", "privilegedMode": True})
    if codebuild.batch_get_projects(names=["tabvio-agentcore"])["projects"]:
        codebuild.update_project(**config)
    else:
        time.sleep(10)
        codebuild.create_project(**config)
    build_id = codebuild.start_build(projectName="tabvio-agentcore")["build"]["id"]
    print(json.dumps({"build_id": build_id, "image": image}), flush=True)


def deploy(image):
    account = boto3.client("sts", region_name=REGION).get_caller_identity()["Account"]
    iam = boto3.client("iam", region_name=REGION)
    role_arn = role(iam, "TabvioAgentCoreRuntime", "bedrock-agentcore.amazonaws.com", [
        allow(["ecr:GetAuthorizationToken"], "*"),
        allow(["ecr:BatchGetImage", "ecr:GetDownloadUrlForLayer"], f"arn:aws:ecr:{REGION}:{account}:repository/{REPOSITORY}"),
        allow(["logs:CreateLogGroup", "logs:CreateLogStream", "logs:PutLogEvents", "logs:DescribeLogStreams"],
              f"arn:aws:logs:{REGION}:{account}:log-group:/aws/bedrock-agentcore/runtimes/{NAME}*"),
        allow(["logs:DescribeLogGroups"], f"arn:aws:logs:{REGION}:{account}:log-group:*"),
        allow(["bedrock:InvokeModel", "bedrock:InvokeModelWithResponseStream"], [
            f"arn:aws:bedrock:{REGION}:{account}:inference-profile/us.anthropic.claude-haiku-4-5-20251001-v1:0",
            "arn:aws:bedrock:us-*::foundation-model/anthropic.claude-haiku-4-5-20251001-v1:0",
        ]),
        allow(["bedrock-agentcore:StartBrowserSession", "bedrock-agentcore:StopBrowserSession",
               "bedrock-agentcore:GetBrowserSession", "bedrock-agentcore:ListBrowserSessions",
               "bedrock-agentcore:ConnectBrowserAutomationStream", "bedrock-agentcore:ConnectBrowserLiveViewStream"],
              f"arn:aws:bedrock-agentcore:{REGION}:aws:browser/aws.browser.v1"),
    ], account)
    client = boto3.client("bedrock-agentcore-control", region_name=REGION)
    config = dict(agentRuntimeArtifact={"containerConfiguration": {"containerUri": image}},
                  roleArn=role_arn, networkConfiguration={"networkMode": "PUBLIC"},
                  protocolConfiguration={"serverProtocol": "HTTP"},
                  lifecycleConfiguration={"idleRuntimeSessionTimeout": 300, "maxLifetime": 1800})
    runtimes = client.list_agent_runtimes()["agentRuntimes"]
    existing = next((item for item in runtimes if item["agentRuntimeName"] == NAME), None)
    time.sleep(10)
    if existing:
        result = client.update_agent_runtime(agentRuntimeId=existing["agentRuntimeId"], **config)
    else:
        result = client.create_agent_runtime(agentRuntimeName=NAME, **config)
    print(json.dumps({key: result[key] for key in ["agentRuntimeArn", "agentRuntimeId", "status"]}), flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=["build", "deploy"])
    parser.add_argument("--image")
    args = parser.parse_args()
    if args.action == "build":
        build()
    elif args.image:
        deploy(args.image)
    else:
        parser.error("deploy requires --image")
