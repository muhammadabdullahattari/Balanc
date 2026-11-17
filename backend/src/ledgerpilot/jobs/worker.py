"""Separate worker process entrypoint for background jobs with graceful shutdown."""

import argparse
import logging
import signal
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, TimeoutError
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy.orm import Session

from ledgerpilot.config import Settings, get_settings
from ledgerpilot.database import build_engine, set_context
from ledgerpilot.jobs import service

logger = logging.getLogger("ledgerpilot.jobs.worker")

JobHandler = Callable[[Session, UUID | None, UUID | None, dict[str, Any]], dict[str, Any] | None]
_REGISTRY: dict[str, JobHandler] = {}


def register_handler(kind: str, handler: JobHandler) -> None:
    """Register a handler function for a specific background job kind."""
    _REGISTRY[kind] = handler


def get_handler(kind: str) -> JobHandler | None:
    """Look up the registered handler for a job kind."""
    return _REGISTRY.get(kind)


class BackgroundWorker:
    """Worker engine that polls configured queues, claims jobs, and executes handlers."""

    def __init__(
        self,
        settings: Settings,
        *,
        queues: list[str] | None = None,
        concurrency: int = 4,
        poll_interval: float = 1.0,
        engine: Any = None,
    ) -> None:
        self.settings = settings
        self.queues = queues or ["default", "webhook", "email", "quickbooks", "reports", "disposal"]
        self.concurrency = concurrency
        self.poll_interval = poll_interval
        self.worker_token = uuid4()
        self.engine = engine if engine is not None else build_engine(settings)
        self.running = True
        self._executor = ThreadPoolExecutor(max_workers=concurrency, thread_name_prefix="JobWorker")
        from ledgerpilot.jobs.handlers import register_default_handlers
        register_default_handlers()

    def stop(self) -> None:
        """Signal the worker to shut down gracefully."""
        self.running = False

    def process_one(self, queue: str) -> bool:
        """Claim and execute at most one job from the queue. Returns True if a job was handled."""
        with Session(self.engine) as claim_db:
            job_data = service.claim_job(
                claim_db,
                queue,
                self.worker_token,
                lease_seconds=300,
                concurrency_limit=self.concurrency,
            )
            claim_db.commit()

        if job_data is None:
            return False

        job_id: UUID = job_data["id"]
        business_id: UUID = job_data["business_id"]
        created_by: UUID = job_data["created_by"]
        kind: str = job_data["kind"]
        payload: dict[str, Any] = job_data.get("payload") or {}
        attempts: int = job_data.get("attempts", 1)
        timeout_sec: int = job_data.get("timeout_seconds", 300)

        handler = get_handler(kind)
        if handler is None:
            with Session(self.engine) as fail_db:
                service.fail_job(
                    fail_db,
                    job_id,
                    self.worker_token,
                    "ERR_UNKNOWN_KIND",
                    f"No worker handler registered for job kind '{kind}'",
                    attempts=attempts,
                )
                fail_db.commit()
            return True

        def _execute() -> dict[str, Any] | None:
            with Session(self.engine) as exec_db:
                if business_id is not None and created_by is not None:
                    set_context(exec_db, created_by, business_id)
                elif created_by is not None:
                    set_context(exec_db, created_by)
                res = handler(exec_db, business_id, created_by, payload)
                service.finish_job(exec_db, job_id, self.worker_token, res)
                exec_db.commit()
                return res

        future = self._executor.submit(_execute)
        try:
            future.result(timeout=timeout_sec)
        except TimeoutError:
            with Session(self.engine) as fail_db:
                service.fail_job(
                    fail_db,
                    job_id,
                    self.worker_token,
                    "ERR_JOB_TIMEOUT",
                    f"Job exceeded timeout of {timeout_sec}s",
                    attempts=attempts,
                )
                fail_db.commit()
        except Exception as exc:
            err_code = getattr(exc, "code", "ERR_JOB_EXECUTION")
            err_msg = getattr(exc, "message", str(exc))
            with Session(self.engine) as fail_db:
                service.fail_job(
                    fail_db,
                    job_id,
                    self.worker_token,
                    err_code,
                    err_msg,
                    attempts=attempts,
                )
                fail_db.commit()

        return True

    def run(self) -> None:
        """Main worker loop with graceful termination handling."""
        logger.info(
            "Worker %s started on queues=%s concurrency=%d",
            self.worker_token,
            self.queues,
            self.concurrency,
        )

        def _handle_signal(signum: int, frame: Any) -> None:
            logger.info("Signal %s received; initiating graceful worker shutdown...", signum)
            self.stop()

        signal.signal(signal.SIGINT, _handle_signal)
        signal.signal(signal.SIGTERM, _handle_signal)

        try:
            while self.running:
                acted = False
                for q in self.queues:
                    if not self.running:
                        break
                    while self.running and self.process_one(q):
                        acted = True
                if not acted and self.running:
                    time.sleep(self.poll_interval)
        finally:
            logger.info("Shutting down worker thread pool...")
            self._executor.shutdown(wait=True, cancel_futures=False)
            self.engine.dispose()
            logger.info("Worker %s gracefully stopped.", self.worker_token)


def main() -> None:
    """CLI entrypoint for running the worker process."""
    parser = argparse.ArgumentParser(description="LedgerPilot Background Job Worker")
    parser.add_argument(
        "--queues",
        default="default,webhook,email,quickbooks,reports,disposal",
        help="Comma-separated queue names to poll",
    )
    parser.add_argument(
        "--concurrency",
        type=int,
        default=4,
        help="Maximum concurrent jobs to execute",
    )
    parser.add_argument(
        "--poll-interval",
        type=float,
        default=1.0,
        help="Seconds to sleep when queues are empty",
    )
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    settings = get_settings()
    queues = [q.strip() for q in args.queues.split(",") if q.strip()]

    worker = BackgroundWorker(
        settings,
        queues=queues,
        concurrency=args.concurrency,
        poll_interval=args.poll_interval,
    )
    worker.run()


if __name__ == "__main__":
    main()
