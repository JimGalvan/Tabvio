import logging
import os

logger = logging.getLogger(__name__)
_telemetry_configured = False
_cloudwatch_provider = None


def configure_telemetry() -> None:
    global _telemetry_configured, _cloudwatch_provider

    if not _telemetry_configured and os.getenv("TABVIO_TRACE_LOG_GROUP"):
        from tabvio.remote.telemetry import configure_cloudwatch

        _cloudwatch_provider = configure_cloudwatch()
        _telemetry_configured = True
        return

    endpoint = os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT", "").strip()
    if _telemetry_configured or not endpoint:
        return

    from strands.telemetry import StrandsTelemetry

    try:
        telemetry = StrandsTelemetry()
        telemetry.setup_otlp_exporter()
        telemetry.setup_meter(enable_otlp_exporter=True)
    except Exception:
        # Losing traces should never stop a run from starting.
        logger.warning("Could not start Strands telemetry", exc_info=True)
        return

    _telemetry_configured = True
    logger.info("Sending Strands traces and metrics to %s", endpoint)


def flush_telemetry():
    if _cloudwatch_provider:
        _cloudwatch_provider.force_flush(timeout_millis=5000)
