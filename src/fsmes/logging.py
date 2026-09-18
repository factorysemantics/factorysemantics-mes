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


class _NotThePlantTalkingToItself(logging.Filter):
    """Drop the access line for a request this plant made to itself.

    A plant that simulates its own shop floor polls its own API about once
    a second - 21,777 times in six hours on the lab plant - and uvicorn
    writes a line for each. Those lines say nothing a person will read:
    they record this process asking itself a question, and the shop floor
    already logs what it did, once per action, on its own logger. A
    request from anywhere else is still logged, because on a real plant
    that is who is using it.
    """

    def __init__(self, own: set[str]) -> None:
        super().__init__()
        self.own = own

    def filter(self, record: logging.LogRecord) -> bool:
        args = record.args
        if not isinstance(args, tuple) or not args:
            return True
        client = str(args[0])
        return client.rsplit(":", 1)[0] not in self.own


def _own_addresses() -> set[str]:
    """The addresses that mean "this process". Loopback always; whatever
    this plant is bound to as well, because the lab's floor reaches its own
    API through the address the plant publishes rather than through 127.
    """
    own = {"127.0.0.1", "::1", "localhost", ""}
    try:
        from fsmes.config import get_settings

        host = get_settings().api_host
        if host and host not in ("0.0.0.0", "::"):
            own.add(host)
    except Exception:      # logging must come up even if settings cannot
        pass
    return own


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
    # WHY THE CONSOLE TRACEBACK IS PLAIN. structlog's console renderer uses
    # rich when rich is installed: a box per frame, the source around each
    # line, and every local. One `database is locked` rendered that way is
    # 282 lines. A lab plant logging one a second filled 1.1 GB in six
    # hours - 6,348,536 lines, of which the last 150,000 carried 386 actual
    # log events. A picture that good of a bug, repeated ten times a second,
    # is not diagnosis, it is the disk running out. Plain here; set
    # MES_LOG_LEVEL=DEBUG and the whole picture comes back.
    exception_formatter = (structlog.dev.rich_traceback
                           if level.upper() == "DEBUG" else structlog.dev.plain_traceback)
    console_formatter = structlog.stdlib.ProcessorFormatter(
        foreign_pre_chain=_PRE_CHAIN,
        processors=[
            structlog.stdlib.ProcessorFormatter.remove_processors_meta,
            structlog.dev.ConsoleRenderer(exception_formatter=exception_formatter),
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

    # httpx logs an INFO line per request it makes. In a plant process that
    # is the simulated shop floor calling its own API - one a second, all
    # day, saying only that a request happened. The floor logs what it did,
    # once per action, on its own logger; that is the line worth keeping.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("uvicorn.access").addFilter(_NotThePlantTalkingToItself(_own_addresses()))

    structlog.configure(
        processors=[*_PRE_CHAIN, structlog.stdlib.ProcessorFormatter.wrap_for_formatter],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )
