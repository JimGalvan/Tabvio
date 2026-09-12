import json

import boto3

from scripts.deploy_agentcore import REGION

TRACE_GROUP = "/aws/bedrock-agentcore/tabvio/traces"
RUN_QUERY = """fields jsonParse(@message) as span
| fields @timestamp, span.attributes.`tabvio.run.id` as Run, span.traceId as Trace,
span.durationNano / 1000000 as DurationMs, span.status.code as Status
| filter span.attributes.`gen_ai.operation.name` = 'invoke_agent'
| sort @timestamp desc
| limit 100
| display @timestamp, Run, Trace, DurationMs, Status"""
STEP_QUERY = """fields jsonParse(@message) as span
| fields @timestamp, span.attributes.`tabvio.run.id` as Run, span.name as Step,
span.durationNano / 1000000 as DurationMs, span.attributes.`gen_ai.usage.input_tokens` as InputTokens,
span.attributes.`gen_ai.usage.output_tokens` as OutputTokens, span.status.code as Status, span.traceId as Trace
| filter span.attributes.`gen_ai.operation.name` in ['chat', 'execute_tool']
| sort @timestamp desc
| limit 200
| display @timestamp, Run, Step, DurationMs, InputTokens, OutputTokens, Status, Trace"""


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
    existing = {}
    request = {"queryDefinitionNamePrefix": "Tabvio"}
    while True:
        page = logs.describe_query_definitions(**request)
        existing.update({item["name"]: item["queryDefinitionId"] for item in page["queryDefinitions"]})
        if not page.get("nextToken"):
            break
        request["nextToken"] = page["nextToken"]
    for name, query in [("Tabvio/Recent runs", RUN_QUERY), ("Tabvio/Model and tool steps", STEP_QUERY)]:
        params = {"name": name, "queryString": query, "logGroupNames": [TRACE_GROUP]}
        if name in existing:
            params["queryDefinitionId"] = existing[name]
        logs.put_query_definition(**params)
    dashboard = boto3.client("cloudwatch", region_name=REGION)
    result = dashboard.put_dashboard(DashboardName="Tabvio-runs", DashboardBody=json.dumps({
        "start": "-PT3H",
        "widgets": [
            {"type": "log", "x": 0, "y": index * 8, "width": 24, "height": 8,
             "properties": {"title": title, "region": REGION, "view": "table",
                            "query": f"SOURCE '{TRACE_GROUP}' | {query}"}}
            for index, (title, query) in enumerate([
                ("Recent agent turns — Run matches the Tabvio run ID", RUN_QUERY),
                ("Model and tool steps — obvious secrets are redacted", STEP_QUERY),
            ])
        ],
    }))
    if result.get("DashboardValidationMessages"):
        raise RuntimeError(result["DashboardValidationMessages"])
    print(json.dumps({"trace_log_group": TRACE_GROUP, "retention_days": 7,
                      "transaction_search": xray.get_trace_segment_destination()}, default=str))


if __name__ == "__main__":
    configure()
