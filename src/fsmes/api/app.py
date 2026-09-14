"""The MES-TWIN API application.

Two front doors: `/dashboard` for the plant floor, `/docs` for integrators.
Everything except health, metrics, and login requires a signed-in user.

This file is the skeleton and nothing else. Which routers it mounts and which
dashboard pages it serves come from `fsmes.modules`, filtered by `MES_MODULES`
- so a plant that switches a module off gets an app with no routes for it,
rather than an app that has them and refuses. The skeleton imports no router
by name; that is what the `core_purity` guard test holds it to.
"""

from contextlib import asynccontextmanager
from importlib import import_module
from pathlib import Path

from fastapi import Depends, FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from fsmes import __version__
from fsmes.api.deps import current_user
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
    except Exception as exc:  # an unmigrated database must not stop the app starting
        log.warning("could not record shadow mode in the audit trail", error=str(exc))


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

    # Routers come from the module registry, filtered by MES_MODULES. Nothing
    # is mounted by name here: a module that is off has no routes, which is
    # what makes "off" answer 404 rather than answer differently.
    #
    # Public means before sign-in: health and metrics (for orchestrators) and
    # the login endpoint itself. Everything else requires a signed-in user;
    # write endpoints add a role check of their own.
    from fsmes.config import get_settings

    signed_in = [Depends(current_user)]
    served = get_settings().enabled_modules()
    for module in served:
        for mount in module.routers:
            router = import_module(mount.module).router
            app.include_router(router, prefix=mount.prefix, tags=list(mount.tags),
                               dependencies=[] if mount.public else signed_in)

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

    # Dashboard pages come from the same registry, so a module that is off
    # takes its screens with it rather than serving a page whose every fetch
    # answers 404. The docstring a reader wants is on the Page entry.
    for module in served:
        for page in module.pages:
            def _serve(page=page) -> FileResponse:
                return FileResponse(WEB_DIR / page.file)

            # A distinct name and the page's own sentence, so /docs-style
            # introspection and a traceback both say which screen this is.
            _serve.__name__ = "page_" + page.file.removesuffix(".html").replace("-", "_")
            _serve.__doc__ = page.about
            app.get(page.path, include_in_schema=False)(_serve)

    @app.get("/", include_in_schema=False)
    def root() -> RedirectResponse:
        return RedirectResponse("/dashboard")

    return app
