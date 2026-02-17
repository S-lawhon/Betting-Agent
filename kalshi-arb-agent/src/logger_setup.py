"""Structured JSON logging configuration for the Kalshi arbitrage agent."""

import json
import logging
import os
import sys
from datetime import datetime, timezone


class JSONFormatter(logging.Formatter):
    """Formats log records as single-line JSON objects."""

    def format(self, record: logging.LogRecord) -> str:
        log_entry = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }

        if record.exc_info and record.exc_info[0] is not None:
            log_entry["exception"] = self.formatException(record.exc_info)

        # Include any extra fields attached to the record
        for key in ("trade_id", "ticker", "sport", "edge", "side", "action",
                     "reason", "pnl", "position_size", "order_id", "error"):
            val = getattr(record, key, None)
            if val is not None:
                log_entry[key] = val

        return json.dumps(log_entry, default=str)


def setup_logging(log_dir: str = "logs", level: str = "INFO") -> logging.Logger:
    """Configure and return the root application logger.

    Args:
        log_dir: Directory for log files.
        level: Logging level string.

    Returns:
        Configured root logger.
    """
    os.makedirs(log_dir, exist_ok=True)

    logger = logging.getLogger("kalshi_agent")
    logger.setLevel(getattr(logging, level.upper(), logging.INFO))

    # Prevent duplicate handlers on repeated calls
    if logger.handlers:
        return logger

    # Console handler — human-readable
    console = logging.StreamHandler(sys.stdout)
    console.setLevel(logging.INFO)
    console_fmt = logging.Formatter(
        "%(asctime)s [%(levelname)-8s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    console.setFormatter(console_fmt)
    logger.addHandler(console)

    # File handler — structured JSON
    log_path = os.path.join(log_dir, f"agent_{datetime.now(timezone.utc).strftime('%Y%m%d')}.jsonl")
    file_handler = logging.FileHandler(log_path)
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(JSONFormatter())
    logger.addHandler(file_handler)

    return logger


def get_logger(name: str) -> logging.Logger:
    """Return a child logger under the main application logger."""
    return logging.getLogger(f"kalshi_agent.{name}")
