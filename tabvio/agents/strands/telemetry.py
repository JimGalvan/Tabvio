import logging
import os

logger = logging.getLogger(__name__)

# Set once per process. Strands registers a global tracer provider, so calling
# the setup twice would replace a working one.
_telemetry_configured = False


def configure_telemetry() -> None:
    global _telemetry_configured

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
