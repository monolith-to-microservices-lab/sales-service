"""Entrypoint: `python -m app.cdc`."""

from ..config import get_settings
from ..logging_config import configure_logging
from ..tracing import setup_tracing
from . import metrics
from .consumer import SalesCdcConsumer, build_kafka_consumer

settings = get_settings()
configure_logging(settings.log_level)
setup_tracing("sales-service-cdc")


def main() -> None:
    metrics.start_metrics_server(settings.cdc_metrics_port)
    consumer = SalesCdcConsumer(build_kafka_consumer())
    consumer.run_forever()


if __name__ == "__main__":
    main()
