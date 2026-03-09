"""Telemetry module: Langfuse tracing via OpenTelemetry."""

import base64
import logging
import os

from livekit.agents.telemetry import set_tracer_provider

logger = logging.getLogger("frontoffice-agent")


def setup_langfuse() -> bool:
    """Export OTel traces to Langfuse. Returns True if enabled."""
    enabled = os.getenv("LANGFUSE_ENABLED", "false").lower() in ("true", "1", "yes")
    if not enabled:
        logger.info("langfuse_disabled reason=LANGFUSE_ENABLED is false")
        return False

    try:
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter

        public_key = os.getenv("LANGFUSE_PUBLIC_KEY")
        secret_key = os.getenv("LANGFUSE_SECRET_KEY")
        host = os.getenv("LANGFUSE_HOST", "https://cloud.langfuse.com")

        if not public_key or not secret_key:
            logger.warning("langfuse_disabled reason=LANGFUSE_PUBLIC_KEY and LANGFUSE_SECRET_KEY must be set")
            return False

        auth = base64.b64encode(f"{public_key}:{secret_key}".encode()).decode()

        provider = TracerProvider()
        provider.add_span_processor(
            BatchSpanProcessor(
                OTLPSpanExporter(
                    endpoint=f"{host.rstrip('/')}/api/public/otel/v1/traces",
                    headers={"Authorization": f"Basic {auth}"},
                )
            )
        )
        set_tracer_provider(provider)
        logger.info("langfuse_enabled host=%s", host)
        return True
    except Exception as e:
        logger.warning("langfuse_disabled reason=%s (install opentelemetry-sdk to enable)", e)
        return False
