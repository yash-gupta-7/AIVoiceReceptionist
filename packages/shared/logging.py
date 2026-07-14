"""JSON logging with a per-request correlation id."""
import json
import logging
import sys
import uuid
from contextvars import ContextVar

correlation_id: ContextVar[str] = ContextVar("correlation_id", default="-")


def new_correlation_id() -> str:
    cid = uuid.uuid4().hex[:12]
    correlation_id.set(cid)
    return cid


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        entry: dict = {
            "ts": self.formatTime(record, "%Y-%m-%dT%H:%M:%S%z"),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
            "cid": correlation_id.get(),
        }
        if record.exc_info:
            entry["exc"] = self.formatException(record.exc_info)
        extra = getattr(record, "data", None)
        if extra:
            entry.update(extra)
        return json.dumps(entry, default=str)


def setup_logging(level: int = logging.INFO) -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(level)


def log(logger: logging.Logger, msg: str, **data) -> None:
    """Structured log helper: log(logger, "booked", intent="book", latency_ms=42)."""
    logger.info(msg, extra={"data": data})
