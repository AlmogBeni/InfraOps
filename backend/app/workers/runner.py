"""Worker process entrypoint: ``python -m app.workers.runner``."""

from __future__ import annotations

import asyncio
import signal

from app.core.config import get_settings
from app.core.logging import configure_logging, get_logger
from app.workers.engine import JobEngine

log = get_logger(__name__)


async def run() -> None:
    settings = get_settings()
    configure_logging(settings.log_level, settings.log_format)
    engine = JobEngine()

    loop = asyncio.get_running_loop()
    for sig_name in ("SIGTERM", "SIGINT"):
        sig = getattr(signal, sig_name, None)
        if sig is None:  # Windows lacks SIGTERM handling in ProactorEventLoop
            continue
        try:
            loop.add_signal_handler(sig, lambda: asyncio.create_task(engine.stop()))
        except (NotImplementedError, RuntimeError):  # pragma: no cover
            pass

    await engine.run_forever()


if __name__ == "__main__":
    asyncio.run(run())
