"""Central configuration — every knob is an environment variable (prefix MES_).

Defaults are chosen so the whole system runs on a laptop with zero setup:
SQLite database, local simulator endpoints, relative folders.
"""

import secrets
from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="MES_", env_file=".env", extra="ignore")

    database_url: str = "sqlite:///fsmes.db"

    log_level: str = "INFO"
    log_dir: Path = Path("logs")

    api_host: str = "127.0.0.1"
    api_port: int = 8000
    # The registry name of this plant, set by `fsmes plant <name> run`. The
    # in-process agent addresses its tools to it.
    plant_name: str = ""

    # Signs session tokens. Generated per process if unset — fine for a laptop,
    # but a real deployment must set it (otherwise a restart logs everyone out,
    # and scaled-out API instances won't accept each other's tokens).
    secret_key: str = ""
    token_ttl_seconds: int = 12 * 3600  # one shift
    # Seed/bootstrap credentials for the built-in accounts.
    admin_password: str = "admin"
    operator_password: str = "operator"

    opc_endpoint: str = "opc.tcp://127.0.0.1:4840/mes-twin/sim"
    opc_namespace: str = "urn:mes-twin:sim"
    tag_map_file: Path = Path("config/tag_map.json")

    # Where the 3D line view gets its geometry. Optional: a line with no entry
    # is laid out from its routing order, so the view works before anyone draws
    # anything.
    line_layout_file: Path = Path("config/line_layout.json")

    # Client security. Empty means anonymous and unencrypted, which is all the
    # bundled simulator needs. A real server (KEPServerEX and friends) usually
    # offers only SignAndEncrypt with a named user, e.g.
    #   MES_OPC_SECURITY=Basic256Sha256,SignAndEncrypt
    # The certificate is minted on first use if the paths do not exist.
    opc_security: str = ""
    opc_user: str = ""
    opc_password: str = ""
    opc_cert_dir: Path = Path("certs")
    opc_application_uri: str = "urn:mes-twin:agent"

    # CSV replay (fsmes run-opc-sim --replay): the KepSim line's generated tables.
    replay_dir: Path = Path("labs/kepsim/out")

    erp_mode: str = "rest"  # rest | file | erpnext | off
    erp_base_url: str = "http://127.0.0.1:8001"
    erp_inbox: Path = Path("erp_exchange/inbox")
    erp_outbox: Path = Path("erp_exchange/outbox")
    erp_archive: Path = Path("erp_exchange/archive")
    erp_poll_seconds: float = 5.0

    # --- ERPNext (erp_mode=erpnext) ---------------------------------------
    # The bench serving this URL may host several sites; erpnext_site is sent
    # as the Host header to pick one. Leaving it empty uses the default site,
    # which on a shared bench is somebody else's company.
    erpnext_base_url: str = "http://localhost:8090"
    erpnext_site: str = "mes.localhost"
    erpnext_user: str = "Administrator"
    erpnext_password: str = "admin"
    # Token auth is preferred for a long-running worker; set both to use it.
    erpnext_api_key: str = ""
    erpnext_api_secret: str = ""
    erpnext_company: str = "ACME Beverages"
    # Post finished quantities as real Manufacture stock entries. Turn off to
    # record what the MES counted without moving ERPNext's stock.
    erpnext_post_stock_entry: bool = True

    sim_speed: float = 1.0
    # How often the agent asks the OPC server what changed. This is the
    # plant's observation resolution: nothing shorter than a couple of these
    # can be seen at all. 500 ms is right for a real line, but a replay at
    # 60x compresses a 12-second jam into 200 ms, so a scored run turns this
    # down in proportion to its speed rather than blaming the MES for
    # missing what it was never sampled fast enough to catch.
    opc_publish_ms: int = 500

    # Tag history is evidence: kept this many days, then pruned hourly by the
    # API process in batches. Zero keeps everything. Bookings, states, checks
    # and the audit trail are never pruned.
    tag_retention_days: float = 14.0

    # One plant, many OPC servers: a JSON list of
    #   {"endpoint": ..., "tag_map": ..., "namespace": ...}
    # runs one agent per entry in the one agent process, each with its own
    # tag map. Empty means the single opc_endpoint above.
    opc_endpoints: str = ""


@lru_cache
def get_settings() -> Settings:
    settings = Settings()
    if not settings.secret_key:
        settings.secret_key = secrets.token_urlsafe(32)
    return settings
