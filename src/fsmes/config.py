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

from fsmes import modules as module_registry


def _rule_list(written: str) -> tuple[int, ...]:
    """A comma list of Western Electric rule numbers, as the four they can be.

    Parsed rather than trusted: anything that is not one of the four rule
    numbers is dropped, because the alternative is a plant refusing to start
    over a typo in a list that only ever narrows a set. `fsmes pack check` is
    where a person is told about the typo, offline, and the chart payload
    states what was actually parsed so nobody has to guess which reading
    applied.
    """
    rules: list[int] = []
    for part in (written or "").split(","):
        part = part.strip()
        if not part.isdigit():
            continue
        rule = int(part)
        if rule in (1, 2, 3, 4) and rule not in rules:
            rules.append(rule)
    return tuple(sorted(rules))


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="MES_", env_file=".env", extra="ignore")

    database_url: str = "sqlite:///fsmes.db"

    # --- SQLite's one writer ----------------------------------------------
    # How long a write waits for the lock before it gives up, in
    # milliseconds. SQLite only; PostgreSQL has its own locking and is not
    # given this.
    #
    # Fifteen seconds, not the five it was. Five was chosen when reads took
    # the write lock too, so raising it would only have made a doomed wait
    # longer. Now that a read holds no write lock, the only thing a writer
    # ever waits for is another writer, and the longest of those on a plant
    # is the hourly retention prune deleting a batch of tag history. This is
    # not a number a healthy plant reaches; it is the margin before the MES
    # decides it cannot record the plant's own production, and that decision
    # should be slow.
    sqlite_busy_timeout_ms: int = 15_000

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

    # --- Which plant is this ----------------------------------------------
    # What this plant is called, everywhere a reader can see: /health,
    # /shadow, the metrics labels, the dashboard header, `fsmes info`, the
    # backup manifest and every namespace event. `fsmes plant <name> run`
    # sets it from the registry; a plant sets it itself.
    #
    # Validated as a code, not free text: it is published as one segment of
    # the namespace topic, and a name that had to be cleaned up there would
    # stop matching the one on this plant's own screens. Required for every
    # profile but `laptop`, where it defaults to the demo plant's own name -
    # see `fsmes.identity`.
    plant_name: str = ""
    # The deployment shape, from docs/design/m8-packs-and-fleet.md §7:
    #   laptop - one process, SQLite, the evaluation profile
    #   plant  - a real plant: PostgreSQL, several processes
    #   fleet  - a plant node with a console over it
    # It is what lets a plant node say "I am a plant, not a laptop" in one
    # word, to its own start-up checks and to anything reading it.
    plant_profile: str = "laptop"
    # What time zone this plant works in, as an IANA name (Europe/Berlin).
    # Every wall-clock boundary the MES draws is drawn here: the shift
    # calendar, "today" on a gauge register, the clock on every screen.
    # Empty means this machine's own zone, and `fsmes info`, /health and the
    # screens all say it was defaulted rather than chosen.
    plant_timezone: str = ""

    # --- This plant's own words -------------------------------------------
    # What this plant calls things, as JSON: the product's display term ->
    # the plant's own word. `{"work order": "job"}` puts "job" on the screens
    # and in the reports of a plant that says job.
    #
    # Display only, and the boundary is load-bearing (decision 0022 clause 3):
    # it may not rename a state, a KPI, a capability, a role, an event kind or
    # anything a topic or an API field is built from, because two plants whose
    # events mean different things while saying the same word are two plants a
    # fleet view compares as though they were one. The refusal lives in
    # `fsmes pack check`, which is where the words are written; this setting is
    # what a checked pack compiles to, and it is checked here for shape only.
    words: str = "{}"

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
    # The SQL poller (`fsmes inbound poll-sql`): this plant's own read-only
    # queries against systems it already has, one per event type, with the
    # connection, the SQL, the column mapping and the cursor column. Config,
    # not code, and emphatically so: the query belongs to whoever owns the
    # system being read. The packaged example reads a SQLite file the docs
    # tell you how to make, and names no product.
    inbound_sql_file: Path = Path("config/inbound_sql.json")

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
    # How long to wait between cycles when the plant is quiet. A cycle that
    # fills its batch without a failure does not wait at all, so a backlog
    # after an outage drains at the broker's speed rather than at
    # uns_batch / uns_poll_seconds.
    uns_poll_seconds: float = 2.0
    # How many due events one cycle publishes before going back for more.
    uns_batch: int = 200
    # How many of those are in flight at once. QoS 1 waits for the broker to
    # acknowledge each publish, so one at a time is one network round trip
    # per event. Keep this under the client's own in-flight limit (paho, and
    # so aiomqtt, allows 20 by default); 1 publishes strictly one at a time.
    uns_inflight: int = 10

    sim_speed: float = 1.0
    # How often the agent asks the OPC server what changed. This is the
    # plant's observation resolution: nothing shorter than a couple of these
    # can be seen at all. 500 ms is right for a real line, but a replay at
    # 60x compresses a 12-second jam into 200 ms, so a scored run turns this
    # down in proportion to its speed rather than blaming the MES for
    # missing what it was never sampled fast enough to catch.
    opc_publish_ms: int = 500

    # How often the agent asks the server whether the session is still alive,
    # counted in publish intervals. Three is about a second and a half on a
    # real line - long enough not to add traffic, short enough that the gap
    # between "the link died" and "the MES noticed" stays small and is
    # reported rather than guessed (decision 0030).
    #
    # It is a *positive* check, not an inference from silence: OPC UA
    # publishes on change, so a machine standing idle sends nothing for an
    # hour and is perfectly connected. Anything below one second is treated
    # as one second; a plant that needs it faster than that has a different
    # problem.
    opc_health_periods: int = 3

    # Tag history is evidence: kept this many days, then pruned hourly by the
    # API process in batches. Zero keeps everything. Bookings, states, checks
    # and the audit trail are never pruned.
    tag_retention_days: float = 14.0

    # One plant, many OPC servers: a JSON list of
    #   {"endpoint": ..., "tag_map": ..., "namespace": ...}
    # runs one agent per entry in the one agent process, each with its own
    # tag map. Empty means the single opc_endpoint above.
    opc_endpoints: str = ""

    # --- The coverage floor (fsmes.services.coverage) ---------------------
    # How much of a window this MES must have watched before it will report a
    # KPI for it, as a share between 0 and 1. Pack data, not code: it is
    # `[oee] coverage_floor` in plant.toml, and this is where it lands.
    #
    # **Zero is the default and it means no floor** — every figure prints with
    # its coverage beside it and nothing is withheld. There is no silent
    # default floor: a number vanishing off a screen because of a threshold
    # nobody chose is its own kind of dishonesty. A plant that writes a floor
    # is asking, deliberately, to be told *unknown* with the ledger attached
    # rather than shown a figure measured over eleven minutes of a shift.
    oee_coverage_floor: float = 0.0

    # --- Which SPC rules raise a hold (fsmes.services.spc) ----------------
    # Decision 0036: **the chart draws and records every rule; the plant
    # chooses which of them raise a hold.** A comma list of Western Electric
    # rule numbers, and `[quality] hold_rules` in plant.toml is where it comes
    # from.
    #
    # All four is the default and is what this product has always done, so a
    # plant that writes nothing behaves exactly as it does now. The numbering
    # itself is never the plant's: a plant that renumbered the rules would
    # publish `SpcSignal.rule = 3` meaning something nobody else means by it.
    #
    # An empty value is a real answer - record and draw every rule, raise a
    # hold on none - and is never silent: the chart states which rules raise a
    # hold on this plant whatever this holds.
    quality_hold_rules: str = "1,2,3,4"

    # --- This plant's own quality numbers (fsmes.services.spc, .gauges,
    # --- .coa, .serialization, .quality) ---------------------------------
    # Every one of these is a judgment that was a literal in the source until
    # the configuration audit of 2026-09-21 named it, and **every default
    # below is the literal that was there**, so a plant that writes none of
    # them behaves exactly as it did. They arrive as `[quality]` keys in
    # plant.toml; `fsmes pack check` validates their ranges and the two pairs
    # that have to stay in order.
    #
    # What is not here, deliberately: the Western Electric rule numbers and
    # their windows, the four dispositions, and the arithmetic behind Cp, Cpk
    # and Pp. Those are the product's, because a plant that changed one would
    # publish a figure meaning something nobody else means by it.

    # Which rules open a *major* non-conformance rather than a minor one. The
    # two words themselves come from the plant's severity vocabulary; this is
    # which rule earns which.
    quality_major_rules: str = "1"

    # The Cpk at which a process is called capable, and the one below it at
    # which it is called marginal. Only the English word beside the figure
    # moves: the Cpk is arithmetic and is the same number on every plant.
    quality_cpk_capable: float = 1.33
    quality_cpk_marginal: float = 1.0

    # The fewest readings control limits are drawn from, and how far back a
    # chart and the rules look.
    quality_spc_min_points: int = 12
    quality_spc_history: int = 200

    # The gauge rule of ten and its floor of four. AIAG says 10:1, ANSI Z540
    # says 4:1, and a plant follows one standard for every gauge it owns.
    quality_gauge_ratio_adequate: float = 10.0
    quality_gauge_ratio_floor: float = 4.0

    # The interval a newly registered gauge gets when nobody says otherwise.
    # Each gauge's own interval is the engineer's and is untouched by this.
    quality_gauge_default_interval_days: int = 365

    # How many serials a pallet certificate prints before it says how many
    # more there are.
    quality_coa_serials_listed: int = 200

    # How many digits a generated serial carries after its prefix. The hyphen
    # between them stays the product's: `next_serial`'s recovery scan reads
    # `PREFIX-digits` to find where a plant's own numbering had got to, and a
    # plant that changed the separator would restart from one over serials it
    # had already issued.
    quality_serial_digits: int = 6

    # What this plant calls a non-conformance on the record. The width of the
    # number after it stays the product's.
    quality_nc_code_prefix: str = "NC"

    # How deep a containment tree may go. It is also the guard that stops a
    # walk running away, so the product keeps a hard ceiling above it -
    # `fsmes.services.serialization.DEPTH_CEILING`.
    quality_containment_max_depth: int = 6

    # --- The judgment model (fsmes.integrations.jev) ----------------------
    # A hosted model that answers fixed, typed questions about state it is
    # given and writes no text. Used in the development build loop only: the
    # run-log triage asks a battery of questions about one simulated run's
    # log, beside the local model's open-ended pass, and both are recorded.
    # Nothing in the product asks it anything, and a judgment is a proposal -
    # it may not be an input to any number the scoring harness grades.
    #
    # Off unless a key is set, and refused outright in shadow mode: the
    # register entry is `llm.jev`. The key is read from the environment or
    # the settings file the same way MES_ERPNEXT_API_SECRET is, never from
    # this repository, and it is never printed - `fsmes info` and every
    # status line say only whether one is set.
    jev_api_key: str = ""
    # Pinned, never `-latest`: a judgment stored against a moving version
    # cannot be reproduced, and the version actually served is stored with
    # every answer so a change shows up as a change rather than as noise.
    # `jev-1.13.0` is what the service answered as on 2026-09-17, measured by
    # asking: its own model list offers only `jev-latest` and `jev-preview`,
    # and a call made as `jev-latest` came back naming `jev-1.13.0`, which is
    # then accepted as a pin by name. (`jev-1.12` was the survey's example
    # and is not served.) `fsmes jev models --resolve` makes that one call
    # again, so moving this pin is a setting change and a re-validation
    # somebody asked for, not something that happens by itself.
    jev_model: str = "jev-1.13.0"
    # Empty means the client's own endpoint. Set it to point a build that
    # may not egress at something it may reach, which is how a refusing
    # installation can be shown to be refusing.
    jev_base_url: str = ""
    # One attempt, and short. The service's median is about 100 ms; the
    # client's own default of ten seconds with two retries is a thirty-second
    # worst case, and a nightly pass that hangs for half a minute has cost
    # more than the answer is worth. A timeout is recorded as "not asked".
    jev_timeout_seconds: float = 5.0

    # --- Modules ----------------------------------------------------------
    # Which modules this plant serves. Read left to right, comma-separated:
    # `all` is every module, `-<name>` switches one off, `<name>` switches one
    # on. The default is `all`, so a plant that never sets this serves exactly
    # what it served before the setting existed.
    #
    #     MES_MODULES=all,-quality          everything except quality
    #     MES_MODULES=quality,maintenance   those two, and the kernel
    #
    # A module that is off mounts no routes, serves no pages and registers no
    # agent tools. Its tables are still created and its rows are still there:
    # off means not served, not not-stored. `fsmes.modules` is the list, and
    # naming something that is not on it is refused rather than ignored.
    #
    # This is a plain setting on purpose. M8 piece 3 moves it into the plant
    # pack's `[modules]` table, which will compile down to this same string.
    modules: str = module_registry.DEFAULT

    @model_validator(mode="after")
    def _modules_exist(self) -> "Settings":
        """Refuse to start on a module list this version cannot honour.

        At start-up rather than at the first request, for the same reason
        shadow mode is checked here: a plant that believes it switched a
        module off should find out at the moment it says so, not the first
        time somebody looks for the screen.
        """
        module_registry.resolve(self.modules)
        return self

    def major_rules(self) -> tuple[int, ...]:
        """Which SPC rules open a major non-conformance rather than a minor
        one. Parsed the same way `hold_rules` is, and for the same reason."""
        return _rule_list(self.quality_major_rules)

    def hold_rules(self) -> tuple[int, ...]:
        """Which SPC rules raise a quality hold on this plant, in order."""
        return _rule_list(self.quality_hold_rules)

    def enabled_modules(self) -> tuple[module_registry.Module, ...]:
        """The modules this plant serves, in registry order."""
        return module_registry.enabled(self.modules)

    def disabled_modules(self) -> tuple[module_registry.Module, ...]:
        """The modules this plant does not serve. The other half of the
        answer, so a report of the state can state its total."""
        return module_registry.disabled(self.modules)

    @model_validator(mode="after")
    def _the_words_are_words(self) -> "Settings":
        """Refuse to start on a vocabulary that is not one.

        Shape only - that it is an object of term to word, and that no word is
        blank. What a plant may rename is decided where a pack is written, by
        `fsmes pack check`, against a protected list read from the product.
        """
        import json

        try:
            table = json.loads(self.words or "{}")
        except ValueError:
            raise ValueError(
                "MES_WORDS is not JSON. It is an object of the product's term to this "
                'plant\'s word, e.g. {"work order": "job"}.') from None
        if not isinstance(table, dict) or not all(
                isinstance(k, str) and isinstance(v, str) and v.strip() for k, v in table.items()):
            raise ValueError(
                "MES_WORDS is an object of the product's term to this plant's word, and "
                "every word is a non-empty string.")
        return self

    @model_validator(mode="after")
    def _the_plant_says_who_it_is(self) -> "Settings":
        """Refuse to start on an identity a reader could not trust.

        Here rather than at each surface, because a plant that is only
        half-named is worse than one that did not start: the events are
        already on the broker by the time anybody notices.
        """
        from fsmes import identity

        problem = identity.check(plant_name=self.plant_name,
                                 plant_profile=self.plant_profile,
                                 plant_timezone=self.plant_timezone)
        if problem:
            raise ValueError(problem)
        if not self.plant_name and self.plant_profile == "laptop":
            # The evaluation profile's plant is the demo plant, and it has a
            # name. Set here rather than as the field default so every path
            # that builds Settings gets it - an empty name reaching the
            # namespace as `plant: null` is the bug this closes.
            self.plant_name = identity.LAPTOP_PLANT_NAME
        return self

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
        # The shadow-specific class where the sentence is about shadow mode,
        # because callers already catch it by name; the general one
        # otherwise.
        from fsmes.identity import Misconfigured

        wrong = shadow.ShadowMisconfigured if shadow.SETTING in plain else Misconfigured
        raise wrong(plain) from None
    if not settings.secret_key:
        settings.secret_key = secrets.token_urlsafe(32)
    for field in ("tag_map_file", "line_layout_file", "inbound_mapping_file", "inbound_sql_file"):
        if field not in settings.model_fields_set:
            setattr(settings, field, packaged_default(getattr(settings, field)))
    return settings
