"""Central configuration — every knob is an environment variable (prefix MES_).

Defaults are chosen so the whole system runs on a laptop with zero setup:
SQLite database, local simulator endpoints, relative folders.
"""

import secrets
from functools import lru_cache
from importlib import resources
from pathlib import Path

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="MES_", env_file=".env", extra="ignore")

    database_url: str = "sqlite:///fsmes.db"

    # --- Shadow mode ------------------------------------------------------
    # The MES watches a real plant and can change nothing in it. On: it reads
    # the OPC UA tags and books production exactly as it would in charge, and
    # every path that could change anything outside its own database is shut
    # - no PLC write-back, no live ERP, no MQTT publish, and nothing about
    # this plant sent off the box. This is how a plant runs it beside the MES
    # already in charge there without the two of them fighting.
    #
    # Read once at start-up and never toggled at runtime: leaving shadow mode
    # is a restart with this setting changed, and the API records that in the
    # audit trail. `fsmes.shadow.REGISTER` lists every outbound path and what
    # this does to each; docs/operate/shadow-mode.md is the page.
    shadow: bool = False

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

    # --- Inbound events (fsmes inbound watch) ------------------------------
    # The second front door, beside the OPC agent: downtime labels, quality
    # results and counts that people typed into some other system. One inbox
    # folder per event type under this root, plus processed/ and rejected/.
    inbound_dir: Path = Path("inbound")
    # What this plant's columns are called, mapped onto the inbound contract.
    # Config, not code: a new supplier is a new mapping, never a new release.
    # The packaged example maps the demo plant's own CSV shape.
    inbound_mapping_file: Path = Path("config/inbound_mapping.json")
    inbound_poll_seconds: float = 10.0

    # --- Inbound over MQTT (fsmes inbound subscribe) -----------------------
    # The other half of the unified namespace: this MES listening to the
    # broker it already publishes to. Tag values (a gateway's counter, state
    # word or process value) are wired in the `mqtt` section of the tag map,
    # beside the OPC machines; inbound events are the streams in the mapping
    # file above that name a `topic`.
    # Off by default: a plant with no broker should not have a worker trying
    # to reach one. `mqtt` needs the [mqtt] extra, the publisher's.
    inbound_mqtt_mode: str = "off"  # off | mqtt
    inbound_mqtt_broker_url: str = "mqtt://127.0.0.1:1883"
    inbound_mqtt_username: str = ""
    inbound_mqtt_password: str = ""
    # A different id from the publisher's on purpose. Brokers disconnect the
    # older session when two clients share an id, so one id for both workers
    # would have them evicting each other all shift.
    inbound_mqtt_client_id: str = "fsmes-inbound"
    inbound_mqtt_qos: int = 1
    # What this plant calls the broker, as its operators would say it out
    # loud: `uns`, `gateway:line1`, `nodered`. It is written onto every unit
    # booked and every state set from a broker message, so a person reading
    # the production log a month later can see which pipe it came down.
    # Empty means `mqtt:<the broker's host>`, which is a description rather
    # than a name — set it. Inbound *events* carry their own source, from the
    # stream's mapping; this names the tag values.
    inbound_mqtt_source: str = ""
    # How often the subscriber says what it has taken in. It is a worker with
    # no end, so the report is the only way to see it working.
    inbound_mqtt_report_seconds: float = 60.0

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
    # Which company's work orders to import. Empty means every company on the
    # site, which is what a single-company ERPNext wants; a shared bench must
    # set this or one company's MES runs another company's orders.
    erpnext_company: str = ""
    # Post finished quantities as real Manufacture stock entries. Turn off to
    # record what the MES counted without moving ERPNext's stock.
    erpnext_post_stock_entry: bool = True

    # --- The outbox as an event log ---------------------------------------
    # The outbox carries ERP confirmations and, when this is on, the plant
    # events no ERP asked for: equipment state changes, order holds and
    # resumes. They are what a unified namespace, a historian or a Node-RED
    # flow reads. The ERP sync ignores them; it selects the kinds its own
    # contract can parse.
    # Turning this off keeps the log to what the ERP is owed - one row per
    # operation and per order instead of one per state change - at the cost
    # of a namespace that shows only the ERP's half of the plant.
    outbox_domain_events: bool = True

    # --- Unified namespace (fsmes uns publish) ----------------------------
    # The MES's outbox, relayed to an MQTT broker as JSON under an ISA-95
    # topic tree. Off by default: a plant that has no broker should not have
    # a worker trying to reach one.
    #   mqtt  - a real broker (needs the [mqtt] extra)
    #   log   - build and log every topic and payload, publish nothing.
    #           The way to see the topic tree before a broker exists.
    #   off   - `fsmes uns publish` refuses to start
    uns_mode: str = "off"  # off | mqtt | log
    uns_broker_url: str = "mqtt://127.0.0.1:1883"
    uns_username: str = ""
    uns_password: str = ""
    # Identifies this MES to the broker. Brokers disconnect the older session
    # when two clients share an id, so a plant running two MES instances must
    # set this per instance.
    uns_client_id: str = "fsmes"
    # Everything the MES publishes hangs under this. `umh/v1` puts it in a
    # United Manufacturing Hub namespace; `plant` or `mes` are fine elsewhere.
    uns_topic_prefix: str = "umh/v1"
    # The top two levels of the tree. Empty means "use the enterprise and site
    # the equipment model already holds"; a level the MES does not hold at all
    # is published as the literal `unknown`, never guessed and never dropped.
    uns_enterprise: str = ""
    uns_site: str = ""
    # The schema segment before the event name, UMH's `_historian` convention.
    # One schema, because one kind of thing is published: MES events.
    uns_schema: str = "_mes"
    # At-least-once: the outbox already retries, and a duplicate is harmless
    # because every payload carries the event id a consumer dedupes on.
    uns_qos: int = 1
    # Events are not retained by default. A retained event is replayed to
    # every new subscriber as though it had just happened, which is a lie
    # about the present the moment anyone reconnects.
    uns_retain: bool = False
    uns_poll_seconds: float = 2.0
    # How many due events one cycle publishes before going back for more.
    uns_batch: int = 200

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

    @model_validator(mode="after")
    def _shadow_mode_holds(self) -> "Settings":
        """Refuse to start on a configuration shadow mode cannot honour.

        Here rather than at each adapter, because a live adapter that is
        never constructed cannot be reached by accident, by a new caller, or
        by a module somebody installs later. Every process builds Settings,
        so every process gets the same refusal.
        """
        from fsmes import shadow

        return shadow.check_settings(self)


def packaged_default(path: Path) -> Path:
    """The path as given if it exists; otherwise the copy the wheel carries.

    The defaults are relative (`config/tag_map.json`) because a checkout has
    them and a laptop should run with zero setup. An install from PyPI has no
    checkout, so the same files ship as package data under `fsmes/data/`.
    A path someone set deliberately is returned unchanged whether or not it
    exists: a wrong path should fail loudly, not silently use the demo's.
    """
    if path.exists() or path.is_absolute():
        return path
    candidate = resources.files("fsmes") / "data" / path.as_posix()
    try:
        if candidate.is_file():
            return Path(str(candidate))
    except (OSError, TypeError):
        pass
    return path


@lru_cache
def get_settings() -> Settings:
    from pydantic import ValidationError

    from fsmes import shadow

    try:
        settings = Settings()
    except ValidationError as exc:
        # One sentence, not a validation report: the person who set one
        # environment variable wrongly is standing next to a plant.
        plain = shadow.plain_error(exc)
        if plain is None:
            raise
        raise shadow.ShadowMisconfigured(plain) from None
    if not settings.secret_key:
        settings.secret_key = secrets.token_urlsafe(32)
    for field in ("tag_map_file", "line_layout_file", "inbound_mapping_file"):
        if field not in settings.model_fields_set:
            setattr(settings, field, packaged_default(getattr(settings, field)))
    return settings
