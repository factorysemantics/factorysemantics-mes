"""Structured logging: pretty console output + rotating JSON-lines files.

Every process (API, OPC agent, ERP sync, simulator) calls setup_logging once
with its component name; stdlib loggers (uvicorn, sqlalchemy, asyncua) are
routed through the same formatters so log files stay uniform.
"""

import logging
import logging.handlers
from pathlib import Path

import structlog

_TIMESTAMPER = structlog.processors.TimeStamper(fmt="iso", utc=True)

_PRE_CHAIN = [
    structlog.contextvars.merge_contextvars,
    structlog.stdlib.add_log_level,
    structlog.stdlib.add_logger_name,
    _TIMESTAMPER,
]


def setup_logging(level: str = "INFO", log_dir: Path = Path("logs"), component: str = "mes") -> None:
    log_dir.mkdir(parents=True, exist_ok=True)

    json_formatter = structlog.stdlib.ProcessorFormatter(
        foreign_pre_chain=_PRE_CHAIN,
        processors=[
            structlog.stdlib.ProcessorFormatter.remove_processors_meta,
            structlog.processors.dict_tracebacks,
            structlog.processors.JSONRenderer(),
        ],
    )
    console_formatter = structlog.stdlib.ProcessorFormatter(
        foreign_pre_chain=_PRE_CHAIN,
        processors=[
            structlog.stdlib.ProcessorFormatter.remove_processors_meta,
            structlog.dev.ConsoleRenderer(),
        ],
    )

    file_handler = logging.handlers.RotatingFileHandler(
        log_dir / f"{component}.jsonl", maxBytes=5_000_000, backupCount=5, encoding="utf-8"
    )
    file_handler.setFormatter(json_formatter)
    console_handler = logging.StreamHandler()
    console_handler.setFormatter(console_formatter)

    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(file_handler)
    root.addHandler(console_handler)
    root.setLevel(level.upper())

    # asyncua logs its own INFO line on every OPC publish callback, and the
    # PublishResult repr it logs grows with every subscribed tag whose value
    # changed that tick - fine at a handful of stations, but a scale spike
    # (27 stations, ~270 tags) turned one publish tick into a ~9 MB log
    # line, ~150 ticks into 200+ MB in a 70-second run, and the formatting
    # cost of that starved the agent enough that it missed every scripted
    # breakdown in the window. Left at WARNING regardless of our own level -
    # a library logging every value it receives was never useful at any
    # scale, it was just small enough to go unnoticed at two plants.
    logging.getLogger("asyncua").setLevel(logging.WARNING)

    structlog.configure(
        processors=[*_PRE_CHAIN, structlog.stdlib.ProcessorFormatter.wrap_for_formatter],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )
