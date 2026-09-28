"""Background worker: delayed payment settlement and webhook delivery.

Runs as one daemon thread inside the app process (started from the FastAPI lifespan) or as a
separate process through `mockbank run-worker`. Both use `run_once`, which tests call directly.
"""

import logging
import threading

import httpx

from mockbank.config import get_settings
from mockbank.db import new_session
from mockbank.services import payments, webhooks

log = logging.getLogger("mockbank.worker")

_stop = threading.Event()
_thread: threading.Thread | None = None


def run_once(client: httpx.Client | None = None) -> dict[str, int]:
    """One pass: settle due payments, then deliver due webhooks."""
    own_client = client is None
    client = client or httpx.Client(follow_redirects=False)
    db = new_session()
    try:
        settled = payments.settle_due(db)
        delivered = webhooks.deliver_due(db, client)
        return {"settled": settled, "webhooks_attempted": delivered}
    finally:
        db.close()
        if own_client:
            client.close()


def _loop() -> None:
    interval = get_settings().worker_poll_interval_seconds
    client = httpx.Client(follow_redirects=False)
    log.info("worker started (poll every %.1fs)", interval)
    while not _stop.is_set():
        try:
            run_once(client)
        except Exception:  # noqa: BLE001  keep the loop alive whatever happens
            log.exception("worker pass failed")
        _stop.wait(interval)
    client.close()
    log.info("worker stopped")


def start() -> None:
    global _thread
    if _thread is not None and _thread.is_alive():
        return
    _stop.clear()
    _thread = threading.Thread(target=_loop, name="mockbank-worker", daemon=True)
    _thread.start()


def stop() -> None:
    _stop.set()
    if _thread is not None:
        _thread.join(timeout=5)
