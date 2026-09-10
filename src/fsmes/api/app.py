"""The MES-TWIN API application.

Two front doors: `/dashboard` for the plant floor, `/docs` for integrators.
Everything except health, metrics, and login requires a signed-in user.
"""

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from fsmes import __version__
from fsmes.api.deps import current_user
from fsmes.api.routers import adjustments as adjustments_router
from fsmes.api.routers import (
    admin,
    analysis,
    assist,
    auth,
    dashboard,
    design,
    documents,
    equipment,
    execution,
    kpis,
    line,
    maintenance,
    masterdata,
    ops,
    quality,
    scheduling,
    serialization,
    system,
    workorders,
)
from fsmes.api.routers import coa as coa_router
from fsmes.api.routers import erp as erp_router
from fsmes.api.routers import triggers as triggers_router
from fsmes.services import Conflict, Invalid, NotFound

_ERROR_STATUS = {NotFound: 404, Conflict: 409, Invalid: 400}

WEB_DIR = Path(__file__).parent.parent / "web"

# Anything a release can change has to be re-fetched when the release changes.
CODE_TYPES = {"text/html", "text/css", "application/javascript", "text/javascript"}


@asynccontextmanager
def _warn_about_well_known_passwords() -> None:
    """Say it once, loudly, when an installation still runs on lab defaults.

    The defaults exist so a laptop demo needs no setup. A plant that keeps
    them has an admin account anyone can guess, and the log is the one place
    every deployment looks. The check reads settings and the environment only;
    it never touches the database.
    """
    import logging
    import os

    from fsmes.config import get_settings
    from fsmes.plant import LAB_ONLY_AGENT_PASSWORD

    settings = get_settings()
    kept = []
    if settings.admin_password == "admin":
        kept.append("MES_ADMIN_PASSWORD (admin)")
    if settings.operator_password == "operator":
        kept.append("MES_OPERATOR_PASSWORD (operator)")
    if os.environ.get("FSMES_AGENT_PASSWORD", LAB_ONLY_AGENT_PASSWORD) == LAB_ONLY_AGENT_PASSWORD:
        kept.append("FSMES_AGENT_PASSWORD (lab default)")
    if not settings.secret_key:
        kept.append("MES_SECRET_KEY (empty: tokens do not survive a restart)")
    if kept:
        logging.getLogger("fsmes.security").warning(
            "well-known credentials in use: %s. Fine for a laptop demo; set them before this "
            "instance serves a plant or is reachable from anywhere else.",
            "; ".join(kept),
        )


def _record_shadow_mode() -> None:
    """Say in the audit trail when this plant enters or leaves shadow mode.

    Shadow mode is read at start-up and never toggled, so the only way it
    changes is a restart with the setting changed - which means the audit
    trail is the only place that can show when it happened. Written once per
    start-up, and only when it differs from the last thing recorded, so a
    plant that restarts nightly does not accumulate a row a night.

    The actor is `system`: nobody was signed in. Who changed it is in the
    change ticket for the setting; when is here.
    """
    import structlog
    from sqlalchemy import select

    from fsmes import shadow
    from fsmes.db import session_scope
    from fsmes.domain import AuditLog
    from fsmes.services import audit

    on = shadow.enabled()
    log = structlog.get_logger("shadow")
    log.info("shadow mode" if on else "not in shadow mode",
             shadow=on, means=shadow.BANNER if on else None)
    try:
        with session_scope() as db:
            last = db.scalars(
                select(AuditLog).where(AuditLog.action.in_(("shadow.on", "shadow.off")))
                .order_by(AuditLog.id.desc()).limit(1)).first()
            was = None if last is None else (last.action == "shadow.on")
            if was is on:
                return
            audit.record(db, actor="system", action="shadow.on" if on else "shadow.off",
                         entity_type="installation", entity_id=shadow.SETTING,
                         before={"shadow": was}, after={"shadow": on})
    except Exception:  # an unmigrated database must not stop the app starting
        log.warning("could not record shadow mode in the audit trail")


async def _lifespan(app: FastAPI):
    """Built-in roles must exist before the first request gates on them.

    Idempotent, and it back-fills a capability added to a shipped role after a
    plant was installed - otherwise an old database would quietly grant less
    than the product says it does.
    """
    from fsmes.db import session_scope
    from fsmes.services import auth as auth_service

    try:
        with session_scope() as db:
            auth_service.ensure_builtin_roles(db)
    except Exception:  # an unmigrated database must not stop the app from
        pass           # starting and explaining itself

    _warn_about_well_known_passwords()
    _record_shadow_mode()

    # Retention runs in the API process because it is the one long-lived
    # process every deployment has. Hourly, in batches, and it says so on Ops.
    import asyncio

    from fsmes.config import get_settings
    from fsmes.services import retention

    async def _retain() -> None:
        while True:
            days = get_settings().tag_retention_days
            if days > 0:
                try:
                    deleted = await asyncio.to_thread(_prune, days)
                    if deleted:
                        import structlog
                        structlog.get_logger("retention").info("tag history pruned", rows=deleted, keep_days=days)
                except Exception:  # pruning must never take the API down
                    pass
            await asyncio.sleep(3600)

    def _prune(days: float) -> int:
        with session_scope() as db:
            return retention.prune_tag_values(db, days)

    task = asyncio.create_task(_retain())
    try:
        yield
    finally:
        task.cancel()


def create_app() -> FastAPI:
    app = FastAPI(
        lifespan=_lifespan,
        title="FactorySemantics MES",
        version=__version__,
        description=(
            "An open-source, modular, agent-native Manufacturing Execution System. "
            "Work orders, execution with lot traceability, equipment states and OEE, "
            "quality, audit trail — fed by OPC UA below and an ERP above. "
            "Sign in at POST /auth/login, then send `Authorization: Bearer <token>`."
        ),
    )

    from fsmes.api.idempotency import idempotency

    app.middleware("http")(idempotency)

    for exc_type, status in _ERROR_STATUS.items():
        @app.exception_handler(exc_type)
        async def _handle(request: Request, exc: Exception, status=status) -> JSONResponse:
            return JSONResponse(status_code=status, content={"detail": str(exc)})

    # Public: health and metrics (for orchestrators), login (to get a token).
    app.include_router(system.router, tags=["system"])
    app.include_router(auth.router, prefix="/auth", tags=["auth"])

    # Everything else requires a signed-in user; write endpoints add a role check.
    signed_in = [Depends(current_user)]
    app.include_router(admin.router, prefix="/admin", tags=["administration"],
                       dependencies=signed_in)
    app.include_router(assist.router, prefix="/assist", tags=["assistant"],
                       dependencies=signed_in)
    app.include_router(documents.router, prefix="/documents",
                       tags=["work instructions"], dependencies=signed_in)
    app.include_router(ops.router, prefix="/ops", tags=["operations"],
                       dependencies=signed_in)
    app.include_router(design.router, prefix="/design",
                       tags=["design partner"], dependencies=signed_in)
    app.include_router(maintenance.router, prefix="/maintenance",
                       tags=["maintenance"], dependencies=signed_in)
    app.include_router(scheduling.router, prefix="/scheduling",
                       tags=["scheduling"], dependencies=signed_in)
    app.include_router(serialization.router, prefix="/trace",
                       tags=["traceability"], dependencies=signed_in)
    app.include_router(masterdata.router, prefix="/masterdata", tags=["master data"], dependencies=signed_in)
    app.include_router(workorders.router, prefix="/workorders", tags=["work orders"], dependencies=signed_in)
    app.include_router(execution.router, prefix="/execution", tags=["execution"], dependencies=signed_in)
    app.include_router(equipment.router, prefix="/equipment", tags=["equipment"], dependencies=signed_in)
    app.include_router(quality.router, prefix="/quality", tags=["quality"], dependencies=signed_in)
    app.include_router(kpis.router, prefix="/kpis", tags=["kpis"], dependencies=signed_in)
    app.include_router(dashboard.router, prefix="/dashboard", tags=["dashboard"], dependencies=signed_in)
    app.include_router(line.router, prefix="/line", tags=["line view"], dependencies=signed_in)
    app.include_router(analysis.router, prefix="/analysis", tags=["analysis"], dependencies=signed_in)
    app.include_router(erp_router.router, prefix="/erp", tags=["erp"], dependencies=signed_in)
    app.include_router(triggers_router.router, prefix="/triggers", tags=["triggers"], dependencies=signed_in)
    app.include_router(adjustments_router.router, prefix="/adjustments", tags=["adjustments"],
                       dependencies=signed_in)
    app.include_router(coa_router.router, prefix="/coa", tags=["certificates"], dependencies=signed_in)

    # The dashboard itself is a static page; it signs in through /auth/login.
    app.mount("/static", StaticFiles(directory=WEB_DIR), name="static")

    # Cloudflare caches whatever the origin declines to talk about. With no
    # Cache-Control of our own the edge applied its own four-hour default, so a
    # promote left returning browsers on the previous release's JavaScript for
    # hours. That failure is silent: assist.js treats a missing
    # /assist/agent/status as "no agent" and falls back to the local model, so
    # the demo simply looked unchanged after a deploy. Code revalidates on every
    # request; only images and fonts, which change name when they change, may
    # sit in a cache.
    @app.middleware("http")
    async def revalidate_code(request: Request, call_next):
        response = await call_next(request)
        if "cache-control" in response.headers:
            return response
        kind = response.headers.get("content-type", "").split(";")[0].strip()
        if kind in CODE_TYPES:
            # no-cache means "ask first", not "do not store": the ETag that
            # StaticFiles already sends turns an unchanged asset into a 304.
            response.headers["Cache-Control"] = "no-cache"
        elif request.url.path.startswith("/static/"):
            response.headers["Cache-Control"] = "public, max-age=604800"
        return response

    @app.get("/dashboard", include_in_schema=False)
    def dashboard_page() -> FileResponse:
        return FileResponse(WEB_DIR / "index.html")

    @app.get("/dashboard/station", include_in_schema=False)
    def station_page() -> FileResponse:
        """One machine, arm's length: the line-side operator's screen."""
        return FileResponse(WEB_DIR / "station.html")

    @app.get("/dashboard/orders", include_in_schema=False)
    def orders_page() -> FileResponse:
        """Where an order is, what each step yielded, and what went into it."""
        return FileResponse(WEB_DIR / "orders.html")

    @app.get("/dashboard/quality", include_in_schema=False)
    def quality_page() -> FileResponse:
        """Inspection history against the specification that judged it."""
        return FileResponse(WEB_DIR / "quality.html")

    @app.get("/dashboard/ops", include_in_schema=False)
    def ops_page() -> FileResponse:
        """What is running, what it has been saying, and who did what."""
        return FileResponse(WEB_DIR / "ops.html")

    @app.get("/dashboard/instructions", include_in_schema=False)
    def instructions_page() -> FileResponse:
        """Controlled work instructions, and the revision in force."""
        return FileResponse(WEB_DIR / "instructions.html")

    @app.get("/dashboard/admin", include_in_schema=False)
    def admin_page() -> FileResponse:
        """People, roles and routings. The screen gates itself on the
        users.manage capability, as the API does."""
        return FileResponse(WEB_DIR / "admin.html")

    @app.get("/dashboard/line", include_in_schema=False)
    def line_page() -> FileResponse:
        """One line: machine health, work in progress between stations, the
        state timeline, and the 3D view as a tab."""
        return FileResponse(WEB_DIR / "line.html")

    @app.get("/dashboard/line/3d", include_in_schema=False)
    def line_scene_page() -> FileResponse:
        """The 3D line view. Its own page, so nothing else pays for a WebGL
        scene it does not draw; the Line page embeds it as a tab."""
        return FileResponse(WEB_DIR / "line3d.html")

    @app.get("/dashboard/machines", include_in_schema=False)
    def machines_page() -> FileResponse:
        """Engineering: the plant as a tree, with cost centers and alarms."""
        return FileResponse(WEB_DIR / "machines.html")

    @app.get("/dashboard/masterdata", include_in_schema=False)
    def masterdata_page() -> FileResponse:
        """Equipment with cost centers, materials and bills of material,
        specifications, people."""
        return FileResponse(WEB_DIR / "masterdata.html")

    @app.get("/dashboard/trace", include_in_schema=False)
    def trace_page() -> FileResponse:
        """One serial (what is inside it, what went into it) or one lot
        (where it went, as packages a warehouse can pull)."""
        return FileResponse(WEB_DIR / "trace.html")

    @app.get("/dashboard/spc", include_in_schema=False)
    def spc_page() -> FileResponse:
        """The individuals control chart: is the process stable, is it
        capable, and which are two different questions."""
        return FileResponse(WEB_DIR / "spc.html")

    @app.get("/dashboard/gauges", include_in_schema=False)
    def gauges_page() -> FileResponse:
        """The gauge register, calibration, and what a failed one invalidated."""
        return FileResponse(WEB_DIR / "gauges.html")

    @app.get("/dashboard/schedule", include_in_schema=False)
    def schedule_page() -> FileResponse:
        """The board, what the plan promises each order, and the calendar
        every promise rests on."""
        return FileResponse(WEB_DIR / "schedule.html")

    @app.get("/dashboard/maintenance", include_in_schema=False)
    def maintenance_page() -> FileResponse:
        """What has come due on use, what is open and what clearing it costs,
        the plans, and the work that was done."""
        return FileResponse(WEB_DIR / "maintenance.html")

    @app.get("/dashboard/coa", include_in_schema=False)
    def coa_page() -> FileResponse:
        """Certificates of analysis: readable, printable, immutable."""
        return FileResponse(WEB_DIR / "coa.html")

    @app.get("/dashboard/adjustments", include_in_schema=False)
    def adjustments_page() -> FileResponse:
        """Engineering: the recommendation queue - the only path to a PLC."""
        return FileResponse(WEB_DIR / "adjustments.html")

    @app.get("/dashboard/triggers", include_in_schema=False)
    def triggers_page() -> FileResponse:
        """Engineering: what the plant does when a signal crosses a line -
        drafted, approved, withdrawn, and every firing."""
        return FileResponse(WEB_DIR / "triggers.html")

    @app.get("/dashboard/tags", include_in_schema=False)
    def tags_page() -> FileResponse:
        """Engineering: every tag on every machine, and whether each machine
        is still talking."""
        return FileResponse(WEB_DIR / "tags.html")

    @app.get("/dashboard/machine/{code}", include_in_schema=False)
    def machine_page(code: str) -> FileResponse:
        """One machine: every tag it publishes, trends, timeline, OEE,
        maintenance, and what is queued on it. The script reads the code
        from the URL; the page is the same file for every machine."""
        return FileResponse(WEB_DIR / "machine.html")

    @app.get("/dashboard/analysis", include_in_schema=False)
    def analysis_page() -> FileResponse:
        """Shift analysis: OEE losses, the state timeline, downtime pareto and
        tag trends. Its own page because these are questions you sit down with,
        not things you watch."""
        return FileResponse(WEB_DIR / "analysis.html")

    @app.get("/", include_in_schema=False)
    def root() -> RedirectResponse:
        return RedirectResponse("/dashboard")

    return app
