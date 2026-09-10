"""Generated reference pages, written at build time so they cannot drift.

Runs as a MkDocs hook before files are collected. Everything here is read
from the installed package: the CLI from Typer, the REST API from the
OpenAPI document, the MCP tools from the two servers' registries, the
settings from the pydantic model. If generation fails, the build fails —
that is the point.
"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path

DOCS = Path(__file__).resolve().parents[1]
OUT = DOCS / "reference"


def _cli() -> str:
    import click
    from typer.main import get_command

    from fsmes.cli import app

    root = get_command(app)
    lines = ["# CLI reference", "",
             "Generated from `fsmes --help` at build time. Every command is `fsmes <command>`.", ""]
    for name, cmd in sorted(root.commands.items()):
        ctx = click.Context(cmd, info_name=f"fsmes {name}")
        lines += [f"## `{name}`", "", "```text", cmd.get_help(ctx).rstrip(), "```", ""]
    return "\n".join(lines)


def _api() -> str:
    from fsmes.api.app import create_app

    spec = create_app().openapi()
    by_tag: dict[str, list[tuple[str, str, str]]] = {}
    for path, methods in spec["paths"].items():
        for method, op in methods.items():
            tag = (op.get("tags") or ["other"])[0]
            summary = op.get("summary") or op.get("description", "").split("\n")[0]
            by_tag.setdefault(tag, []).append((method.upper(), path, summary))
    lines = ["# REST API reference", "",
             f"Generated from the OpenAPI document at build time: {len(spec['paths'])} paths. "
             "A running instance serves the interactive version at `/docs` and the raw document at `/openapi.json`. "
             "Sign in at `POST /auth/login` and send `Authorization: Bearer <token>`; every write needs a role.", ""]
    for tag in sorted(by_tag):
        lines += [f"## {tag}", "", "| Method | Path | Summary |", "|---|---|---|"]
        for method, path, summary in sorted(by_tag[tag], key=lambda r: (r[1], r[0])):
            lines.append(f"| `{method}` | `{path}` | {summary} |")
        lines.append("")
    return "\n".join(lines)


def _tools() -> str:
    from fsmes import mcp_server as product
    from fsmes.sim import mcp_server as sim

    def section(title: str, server, intro: str) -> list[str]:
        tools = asyncio.run(server.list_tools())
        lines = [f"## {title} ({len(tools)} tools)", "", intro, ""]
        for t in sorted(tools, key=lambda t: t.name):
            schema = getattr(t, "inputSchema", None) or {}
            props = schema.get("properties", {}) if isinstance(schema, dict) else {}
            required = set(schema.get("required", [])) if isinstance(schema, dict) else set()
            args = ", ".join(f"`{k}`" + ("" if k in required else "?") for k in props) or "no arguments"
            desc = (t.description or "").strip().split("\n")[0]
            lines.append(f"- **`{t.name}`** — {desc}  \n  {args}")
        lines.append("")
        return lines

    lines = ["# MCP tools reference", "",
             "Generated from the two servers' registries at build time. `?` marks an optional argument.", ""]
    lines += section("Product", product.mcp,
                     "The MES as tools. Everything goes through a plant's HTTP API as the `AGENT` role; "
                     "writes accept `dry_run`, `on_behalf_of` and `client_ref` (see the write discipline).")
    lines += section("Simulation", sim.mcp,
                     "The simulator and scoring harness as tools, for driving and judging fake plants. "
                     "Not for a real one.")
    return "\n".join(lines)


def _settings() -> str:
    from fsmes.config import Settings

    lines = ["# Settings reference", "",
             "Generated from the settings model at build time. Every setting is an environment variable "
             "prefixed `MES_` (or a line in `.env`); the table shows the default a laptop gets.", "",
             "| Setting | Environment variable | Default |", "|---|---|---|"]
    for name, field in Settings.model_fields.items():
        default = field.default
        if default in ("", None) or repr(default) == "PydanticUndefined":
            shown = ""
        elif isinstance(default, str):
            shown = f"`{default}`"
        elif isinstance(default, (bool, int, float)):
            shown = f"`{json.dumps(default)}`"
        else:
            shown = f"`{default}`"
        lines.append(f"| `{name}` | `MES_{name.upper()}` | {shown} |")
    lines += ["", "Plant registries (`fsmes plant`) carry per-plant values that override these; "
              "see [plants from a registry](../operate/registry.md).", ""]
    return "\n".join(lines)


def _type_of(prop: dict) -> str:
    """The JSON Schema type of one field, as a person would say it."""
    if "const" in prop:
        return f"always `{prop['const']}`"
    if "anyOf" in prop:
        named = [_type_of(one) for one in prop["anyOf"] if one.get("type") != "null"]
        nullable = any(one.get("type") == "null" for one in prop["anyOf"])
        return " or ".join(named) + (", or null" if nullable else "")
    if prop.get("type") == "array":
        item = prop.get("items", {})
        return f"list of {item['$ref'].rsplit('/', 1)[-1]}" if "$ref" in item else "list"
    if prop.get("format") == "date-time":
        return "timestamp"
    return prop.get("type", "any")


def _confirmations() -> str:
    """The ERP contract, field by field, and the schema file beside it."""
    from fsmes.integrations.erp import examples
    from fsmes.integrations.erp.schema import SCHEMA_ID, confirmation_schema

    published = confirmation_schema()
    (OUT / "erp-confirmation.schema.json").write_text(
        json.dumps(published, indent=2) + "\n", encoding="utf-8")

    lines = ["# The ERP confirmation contract", "",
             "Generated from the contract models at build time, so this page and the software "
             "cannot drift apart. The same models are published as a JSON Schema — "
             "[`erp-confirmation.schema.json`](erp-confirmation.schema.json) "
             f"(`{SCHEMA_ID}`) — which your own tooling can validate against with no "
             "connection to this MES.", "",
             published["description"], "",
             "Every field below says three things: what it means on the floor, where the MES "
             "gets it, and when it is null. Null is never a placeholder for zero. See "
             "[the confirmation handoff](../operate/confirmation-files.md) for the folder "
             "these documents are written into and for `fsmes erp validate`.", "",
             "## Worked examples", "",
             "Generated from a run of the demo plant, not written by hand.", "",
             "| Example | JSON | B2MML | What it shows |", "|---|---|---|---|"]
    for name, title, why in examples.PUBLISHED.values():
        folder = f"../operate/confirmation-examples/{name}"
        lines.append(f"| {title} | [`{name}.json`]({folder}.json) | "
                     f"[`{name}.xml`]({folder}.xml) | {why} |")
    lines.append("")
    for name in ("OperationConfirmation", "OrderCompletion", "ComponentUse", "ProductionRequest"):
        definition = published["$defs"][name]
        required = set(definition.get("required", []))
        lines += [f"## `{name}`", "", definition["description"], "",
                  "| Field | Type | On the floor | Where the MES gets it | When it is null |",
                  "|---|---|---|---|---|"]
        for field, prop in definition["properties"].items():
            mark = "" if field in required else " *(optional)*"
            lines.append(f"| `{field}`{mark} | {_type_of(prop)} | {prop['x-fsmes-floor']} | "
                         f"{prop['x-fsmes-source']} | {prop['x-fsmes-null']} |")
        lines.append("")
    return "\n".join(lines)


def _llms(config) -> str:
    pages = []
    for item in _flatten(config["nav"]):
        pages.append(item)
    lines = ["# FactorySemantics MES", "",
             "> An open-source, modular, agent-native Manufacturing Execution System. "
             "Every number in these docs comes from a simulated plant unless a page says otherwise.", "",
             "## Pages", ""]
    for title, path in pages:
        url = config["site_url"].rstrip("/") + "/" + path.replace("index.md", "").replace(".md", "/")
        lines.append(f"- [{title}]({url})")
    return "\n".join(lines) + "\n"


def _flatten(nav, prefix=""):
    out = []
    for entry in nav:
        if isinstance(entry, str):
            out.append((prefix.removesuffix(" — ") or entry, entry))
        elif isinstance(entry, dict):
            for title, value in entry.items():
                if isinstance(value, str):
                    out.append((f"{prefix}{title}", value))
                else:
                    out += _flatten(value, f"{prefix}{title} — ")
    return out


def on_pre_build(config, **kwargs):
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "cli.md").write_text(_cli(), encoding="utf-8")
    (OUT / "api.md").write_text(_api(), encoding="utf-8")
    (OUT / "tools.md").write_text(_tools(), encoding="utf-8")
    (OUT / "settings.md").write_text(_settings(), encoding="utf-8")
    (OUT / "erp-confirmations.md").write_text(_confirmations(), encoding="utf-8")
    (DOCS / "llms.txt").write_text(_llms(config), encoding="utf-8")
