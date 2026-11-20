"""Publish committed graph outbox rows independently of Kafka intake."""

import logging
import os
import signal
from threading import Event

from pipeline.neptune import FileProjection, NeptuneProjection, project_pending
from pipeline.store import Store


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    store = Store(os.environ["DATABASE_URL"])
    endpoint = os.getenv("NEPTUNE_ENDPOINT")
    projection = (
        NeptuneProjection(endpoint, os.environ["AWS_REGION"])
        if endpoint
        else FileProjection(os.getenv("GRAPH_EXPORT_DIR", "var/graphs"))
    )
    stopping = Event()

    def stop(*_):
        stopping.set()

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    try:
        while not stopping.is_set():
            if project_pending(store, projection, limit=1) == 0:
                stopping.wait(2)
    finally:
        if isinstance(projection, NeptuneProjection):
            projection.client.close()
        store.engine.dispose()


if __name__ == "__main__":
    main()
