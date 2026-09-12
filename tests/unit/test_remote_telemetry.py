from opentelemetry import baggage, context
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.trace import Status, StatusCode

from tabvio.remote.telemetry import MetadataExporter, RunCorrelation


def test_traces_keep_run_steps_without_sensitive_payloads():
    destination = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(RunCorrelation())
    provider.add_span_processor(SimpleSpanProcessor(MetadataExporter(destination)))
    tracer = provider.get_tracer("test")
    trace_context = baggage.set_baggage("session.id", "test-session")
    trace_context = baggage.set_baggage("tabvio.run.id", "test-run", context=trace_context)
    token = context.attach(trace_context)
    try:
        with tracer.start_as_current_span("invoke_agent Tabvio") as root:
            root.set_attribute("gen_ai.input.messages", "private prompt")
            with tracer.start_as_current_span("execute_tool fill_sensitive") as tool:
                tool.set_attributes({
                    "gen_ai.tool.name": "fill_sensitive",
                    "gen_ai.tool.call.arguments": "private password",
                    "gen_ai.tool.call.result": "private page",
                    "gen_ai.usage.input_tokens": 42,
                })
                tool.add_event("input", {"value": "private code"})
                tool.record_exception(ValueError("private exception"))
                tool.set_status(Status(StatusCode.ERROR, "private failure"))
        spans = destination.get_finished_spans()
        assert len(spans) == 2
        assert spans[0].parent.span_id == spans[1].context.span_id
        assert spans[0].attributes["gen_ai.tool.name"] == "fill_sensitive"
        assert spans[0].attributes["gen_ai.usage.input_tokens"] == 42
        assert spans[0].status.status_code == StatusCode.ERROR
        for span in spans:
            assert span.attributes["session.id"] == "test-session"
            assert span.attributes["tabvio.run.id"] == "test-run"
            assert not span.events
            assert "private" not in span.to_json()
    finally:
        context.detach(token)
        provider.shutdown()
