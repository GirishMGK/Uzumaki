"""Structured logging setup for SanGir Automations (desktop + web).

Four rotating file handlers:
- app.log: startup, shutdown, user actions
- processing.log: job start/end, row counts, file names (NO PII)
- error.log: exceptions and failures
- update.log: version checks, downloads, install events

All PII (PAN, Aadhaar, names, account numbers) is NEVER logged.
"""

import logging
import logging.handlers

from fcmr_core.config import settings


def get_logger(name: str) -> logging.Logger:
    """Get a logger with the given name. Logs are written to data/logs/."""
    logger = logging.getLogger(name)

    # Skip if handlers already attached (avoid duplication on re-import)
    if logger.handlers:
        return logger

    logger.setLevel(logging.DEBUG)

    # Ensure logs directory exists
    settings.ensure_dirs()  # This now creates logs_dir too
    logs_dir = settings.logs_dir

    # Formatter: timestamp | level | logger name | message (no PII in message)
    formatter = logging.Formatter(
        "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    # Route different loggers to different files
    if "processing" in name or "run" in name:
        # processing.log for analytics/ingestion logs
        handler = logging.handlers.RotatingFileHandler(
            logs_dir / "processing.log",
            maxBytes=10 * 1024 * 1024,  # 10 MB
            backupCount=5,
        )
        handler.setLevel(logging.INFO)
        handler.setFormatter(formatter)
        logger.addHandler(handler)
    elif "error" in name or logger.level == logging.ERROR:
        # error.log for exceptions
        handler = logging.handlers.RotatingFileHandler(
            logs_dir / "error.log",
            maxBytes=10 * 1024 * 1024,
            backupCount=5,
        )
        handler.setLevel(logging.ERROR)
        handler.setFormatter(formatter)
        logger.addHandler(handler)
    elif "update" in name:
        # update.log for auto-updater events
        handler = logging.handlers.RotatingFileHandler(
            logs_dir / "update.log",
            maxBytes=5 * 1024 * 1024,
            backupCount=3,
        )
        handler.setLevel(logging.INFO)
        handler.setFormatter(formatter)
        logger.addHandler(handler)
    else:
        # app.log for general app events (default)
        handler = logging.handlers.RotatingFileHandler(
            logs_dir / "app.log",
            maxBytes=10 * 1024 * 1024,
            backupCount=5,
        )
        handler.setLevel(logging.INFO)
        handler.setFormatter(formatter)
        logger.addHandler(handler)

    # Also add a console handler (for dev/debugging)
    console_handler = logging.StreamHandler()
    console_handler.setLevel(logging.INFO)
    console_handler.setFormatter(formatter)
    logger.addHandler(console_handler)

    return logger
