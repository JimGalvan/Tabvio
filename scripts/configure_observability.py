import json

import boto3

from scripts.deploy_agentcore import REGION

TRACE_GROUP = "/aws/bedrock-agentcore/tabvio/traces"


def configure():
    account = boto3.client("sts", region_name=REGION).get_caller_identity()["Account"]
    logs = boto3.client("logs", region_name=REGION)
    xray = boto3.client("xray", region_name=REGION)
    try:
        logs.create_log_group(logGroupName=TRACE_GROUP)
    except logs.exceptions.ResourceAlreadyExistsException:
        pass
    logs.put_retention_policy(logGroupName=TRACE_GROUP, retentionInDays=7)
    try:
        logs.create_log_stream(logGroupName=TRACE_GROUP, logStreamName="spans")
    except logs.exceptions.ResourceAlreadyExistsException:
        pass
    logs.put_resource_policy(
        policyName="TabvioTraceDelivery",
        policyDocument=json.dumps({"Version": "2012-10-17", "Statement": [{
            "Effect": "Allow",
            "Principal": {"Service": "xray.amazonaws.com"},
            "Action": "logs:PutLogEvents",
            "Resource": [
                f"arn:aws:logs:{REGION}:{account}:log-group:{name}:*"
                for name in [TRACE_GROUP, "aws/spans", "/aws/application-signals/data"]
            ],
            "Condition": {
                "StringEquals": {"aws:SourceAccount": account},
                "ArnLike": {"aws:SourceArn": f"arn:aws:xray:{REGION}:{account}:*"},
            },
        }]}),
    )
    destination = xray.get_trace_segment_destination()
    if destination["Destination"] != "CloudWatchLogs":
        xray.update_trace_segment_destination(Destination="CloudWatchLogs")
    print(json.dumps({"trace_log_group": TRACE_GROUP, "retention_days": 7,
                      "transaction_search": xray.get_trace_segment_destination()}, default=str))


if __name__ == "__main__":
    configure()
