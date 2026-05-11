import os

if os.environ.get("ENABLE_LOCAL_OTEL") == "1":
    os.environ.setdefault("OTEL_SERVICE_NAME", "multi-agent-local")
    os.environ.setdefault("OTEL_EXPORTER_OTLP_ENDPOINT", "http://localhost:4318")
    os.environ.setdefault("OTEL_EXPORTER_OTLP_PROTOCOL", "http/protobuf")

    from strands.telemetry import StrandsTelemetry
    StrandsTelemetry().setup_otlp_exporter()
