import os

from botocore.session import Session
from opentelemetry import baggage
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import ReadableSpan, SpanProcessor, TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor, SpanExporter
from opentelemetry.trace import Status, set_tracer_provider

from tabvio.config import read_aws_region_setting

SAFE_ATTRIBUTES = {
    "session.id", "tabvio.run.id", "gen_ai.operation.name", "gen_ai.system",
    "gen_ai.provider.name", "gen_ai.agent.name", "gen_ai.agent.id",
    "gen_ai.request.model", "gen_ai.response.model", "gen_ai.response.finish_reasons",
    "gen_ai.tool.name", "gen_ai.tool.call.id", "gen_ai.tool.status",
}
SAFE_PREFIXES = ("gen_ai.usage.", "gen_ai.server.")


def metadata_only(span):
    attributes = {
        key: value for key, value in (span.attributes or {}).items()
        if key in SAFE_ATTRIBUTES or key.startswith(SAFE_PREFIXES)
    }
    return ReadableSpan(
        name=span.name, context=span.context, parent=span.parent,
        resource=span.resource, attributes=attributes,
        events=(), links=(), kind=span.kind,
        status=Status(span.status.status_code),
        start_time=span.start_time, end_time=span.end_time,
        instrumentation_scope=span.instrumentation_scope,
    )


class MetadataExporter(SpanExporter):
    def __init__(self, exporter):
        self.exporter = exporter

    def export(self, spans):
        return self.exporter.export([metadata_only(span) for span in spans])

    def shutdown(self):
        self.exporter.shutdown()

    def force_flush(self, timeout_millis=30000):
        return self.exporter.force_flush(timeout_millis)


class RunCorrelation(SpanProcessor):
    def on_start(self, span, parent_context=None):
        run_id = baggage.get_baggage("session.id", context=parent_context)
        if run_id:
            span.set_attribute("session.id", run_id)
            span.set_attribute("tabvio.run.id", run_id)


def configure_cloudwatch():
    from amazon.opentelemetry.distro.exporter.otlp.aws.traces.otlp_aws_span_exporter import (
        OTLPAwsSpanExporter,
    )

    region = read_aws_region_setting()
    resource = Resource({
        "service.name": "tabvio_agent",
        "aws.local.service": "tabvio_agent",
        "cloud.provider": "aws",
        "cloud.region": region,
        "cloud.platform": "aws_bedrock_agentcore",
        "aws.service.type": "gen_ai_agent",
        "gen_ai.agent.id": os.environ["TABVIO_TELEMETRY_AGENT_ARN"],
        "cloud.resource_id": os.environ["TABVIO_TELEMETRY_AGENT_ARN"],
        "aws.log.group.names": os.environ["TABVIO_TRACE_LOG_GROUP"],
    })
    exporter = OTLPAwsSpanExporter(
        aws_region=region, session=Session(),
        endpoint=f"https://xray.{region}.amazonaws.com/v1/traces",
        headers={
            "x-aws-log-group": os.environ["TABVIO_TRACE_LOG_GROUP"],
            "x-aws-log-stream": "spans",
        },
    )
    provider = TracerProvider(resource=resource)
    provider.add_span_processor(RunCorrelation())
    provider.add_span_processor(BatchSpanProcessor(MetadataExporter(exporter)))
    set_tracer_provider(provider)
    return provider
