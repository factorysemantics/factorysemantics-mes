"""The ERP connector port, and the factory that picks one from settings.

A connector is transport only: fetch production requests, acknowledge
receipt, deliver confirmations. The models it speaks are in `contract.py`;
all MES-side logic lives in services.erp, so adding an SAP/Odoo/NetSuite
connector means implementing the port against a typed contract.

The port has six methods, not three. The first three move the work. The
other three exist because three methods moving work say nothing about the
things that actually bite an integration, all of which the ERPNext
connector learned the hard way on 2026-09-09:

    requirements()  what this connector needs to exist on the ERP side.
                    The ERPNext connector needs five custom fields on Work
                    Order, and the documentation said it needed nothing.
    setup()         create or verify those requirements, idempotently.
    check()         say in plain words whether this would work right now,
                    so a person can find out before trusting it.

`fsmes erp requirements`, `fsmes erp setup` and `fsmes erp check` are these
three methods and nothing else — they no longer know that ERPNext exists.

All three have defaults, because a transport can genuinely need nothing:
the file adapter's folders are its own business. A connector written
against the older three-method port keeps working; the module-level
`requirements()`, `setup()` and `check()` below fall back for it and say so
rather than claiming it is ready.

`docs/develop/erp-connectors.md` is this page for a person who is writing
one, and `conformance.py` is the suite that checks they got it right.
"""

from importlib.metadata import entry_points
from typing import Literal, Protocol, runtime_checkable

from pydantic import BaseModel, Field

from fsmes.config import Settings
from fsmes.integrations.erp.contract import Confirmation, ProductionRequest


class Requirement(BaseModel):
    """One thing that must exist on the ERP side before this connector works.

    A service exposed, an extension field, a staging table, a folder, a
    permission. Data rather than prose, so `fsmes erp requirements` can
    print it, `setup()` can act on it and a person can put it in a ticket
    for whoever administers the ERP.
    """

    name: str
    """Short identifier, as the ERP names it: `custom_mes_synced`, `/orders`."""

    where: str
    """Where it lives on the far side: `Work Order doctype`, `the HTTP API`."""

    what: str
    """What it is, in a sentence a person who knows the ERP can act on."""

    why: str
    """What the MES cannot do without it. No requirement is self-evident."""

    created_by_setup: bool = True
    """Can `fsmes erp setup` make this? False means a human must, and
    `setup()` must not pretend otherwise."""


class SetupOutcome(BaseModel):
    """What `setup()` did to one requirement. Never a count: a person can
    check `created` against a named field, and cannot check `5 fields`."""

    name: str
    outcome: str
    """Plain words: `created`, `already there`, `must be done by hand`."""


class CheckLine(BaseModel):
    """One finding from `check()`, with its own verdict.

    `unknown` is a first-class answer and the reason this is not a bool.
    The REST connector can prove it reached the ERP's order list; it cannot
    prove the confirmation endpoint accepts a confirmation without posting
    one. Reporting that as `ok` would be inventing a fact, and reporting it
    as `NOT OK` would fail a connector that is fine.
    """

    status: Literal["ok", "not ok", "unknown", "note"]
    text: str

    def render(self) -> str:
        """One line, with the verdict first so it survives being pasted
        into an issue. `note` is a continuation of the line above it."""
        prefix = {"ok": "ok     ", "not ok": "NOT OK ", "unknown": "unknown", "note": "       "}
        return f"{prefix[self.status]} {self.text}"


class CheckResult(BaseModel):
    """Everything `check()` found. `ok` gates the exit code; `unverified`
    is what stops a green check from being read as a guarantee."""

    lines: list[CheckLine] = Field(default_factory=list)

    @property
    def ok(self) -> bool:
        """Nothing found that would stop the connector working. Not the
        same as "everything was checked" — see `unverified`."""
        return not any(line.status == "not ok" for line in self.lines)

    @property
    def unverified(self) -> bool:
        """Something could not be checked. A person reading `Ready.` is
        entitled to know which part of it nobody proved."""
        return any(line.status == "unknown" for line in self.lines)

    def render(self) -> list[str]:
        return [line.render() for line in self.lines]

    @classmethod
    def of(cls, *pairs: tuple[str, str]) -> "CheckResult":
        """`CheckResult.of(("ok", "reached the ERP"), ...)` — the shorthand
        the adapters use."""
        return cls(lines=[CheckLine(status=status, text=text) for status, text in pairs])


@runtime_checkable
class ErpAdapter(Protocol):
    """The port. Three methods move the work, three describe the far side."""

    def fetch_orders(self) -> list[ProductionRequest]:
        """New production requests from the ERP."""
        ...

    def acknowledge(self, order_code: str) -> None:
        """Tell the ERP an order was received (no-op where the transport implies it)."""
        ...

    def send_confirmation(self, confirmation: Confirmation) -> None:
        """Deliver one confirmation. Raise on failure - the outbox retries with backoff.

        "Failure" means the ERP did not keep what was sent, which is not
        the same as an HTTP error. ERPNext answers `200` to a write naming
        a field its doctype does not have and drops the value; measured on
        v15.120.0, not inferred. A connector that reports that as success
        makes the MES believe a number reached the ERP when it did not.
        Read the write back where the ERP will let you.
        """
        ...

    def requirements(self) -> list[Requirement]:
        """What must exist on the ERP side before this connector works."""
        ...

    def setup(self) -> list[SetupOutcome]:
        """Create or verify the requirements. Idempotent: running it twice
        must change nothing the second time and must not fail."""
        ...

    def check(self) -> CheckResult:
        """Connectivity, credentials and requirements, in plain language.
        No side effects: `check` must be safe to run against production."""
        ...


class ErpConnector:
    """Optional base class carrying the defaults for the three new methods.

    A transport that needs nothing on the far side inherits this and
    implements the original three. Inheriting is not required — the port is
    structural — but it is the shortest honest way to be complete.
    """

    def requirements(self) -> list[Requirement]:
        return []

    def setup(self) -> list[SetupOutcome]:
        return [
            SetupOutcome(name=requirement.name,
                         outcome="must be done by hand" if not requirement.created_by_setup else "already there")
            for requirement in self.requirements()
        ]

    def check(self) -> CheckResult:
        return CheckResult.of(
            ("unknown", f"{type(self).__name__} does not check anything; nothing here has been verified"),
        )


def requirements(adapter: object) -> list[Requirement]:
    """What the configured connector needs on the ERP side."""
    method = getattr(adapter, "requirements", None)
    return list(method()) if callable(method) else []


def setup(adapter: object) -> list[SetupOutcome]:
    """Prepare the ERP side for the configured connector."""
    method = getattr(adapter, "setup", None)
    if callable(method):
        return list(method())
    return [SetupOutcome(name=_name(adapter), outcome="this connector has no setup step")]


def check(adapter: object) -> CheckResult:
    """Is the configured connector usable right now?

    A connector written against the older three-method port has no answer
    to that, and the honest report of no answer is `unknown` rather than a
    cheerful `ok`.
    """
    method = getattr(adapter, "check", None)
    if callable(method):
        return method()
    return CheckResult.of(
        ("unknown", f"{_name(adapter)} does not implement check(); nothing about it has been verified"),
        ("note", "it may work perfectly. Nobody has asked it."),
    )


def _name(adapter: object) -> str:
    return type(adapter).__name__


def make_adapter(settings: Settings) -> ErpAdapter | None:
    if settings.erp_mode == "rest":
        from fsmes.integrations.erp.rest_adapter import RestErpAdapter

        return RestErpAdapter(settings.erp_base_url)
    if settings.erp_mode == "file":
        from fsmes.integrations.erp.file_adapter import FileErpAdapter

        return FileErpAdapter(settings.erp_inbox, settings.erp_outbox, settings.erp_archive)
    if settings.erp_mode == "off":
        return None
    # Every other mode is a module: a `fsmes.modules` entry point whose name is
    # the mode and whose target builds the adapter from settings. ERPNext ships
    # in this package and registers that way; a connector published on its own
    # (SAP, Odoo, NetSuite) plugs in identically.
    for ep in entry_points(group="fsmes.modules"):
        if ep.name == settings.erp_mode:
            return ep.load()(settings)
    known = ", ".join(sorted(ep.name for ep in entry_points(group="fsmes.modules")))
    raise ValueError(
        f"unknown ERP mode {settings.erp_mode!r} (built in: rest, file, off; installed modules: {known or 'none'})"
    )
