# Operate

For the person who installs, upgrades and connects it.

**Standing this up beside the MES a plant already runs?** Start at
[Running FactorySemantics MES beside your existing MES](first-plant.md) —
one page, in order, for a controls person and an IT person together. Every
page below is linked from it.

| Page | Type |
|---|---|
| [Beside your existing MES](first-plant.md) | tutorial — the whole path, one afternoon, for a plant engineer |
| [Deploy](deploy.md) | tutorial — Compose on one box, or systemd units for test and promoted environments |
| [Connecting read-only to an OPC UA server you do not own](opc-readonly.md) | how-to — commissioning against a server that already serves another system |
| [Plants from a registry](registry.md) | how-to — several independent plants, one file |
| [Running beside an existing MES](shadow-mode.md) | how-to — shadow mode: one setting, and this MES can change nothing in the plant |
| [The ERPNext connector](erpnext.md) | how-to |
| [The unified namespace (MQTT)](uns.md) | how-to — MES events onto a plant's broker |
| [Backup and restore](backup.md) | how-to — what to copy, and how to prove the copy is any good |
| [TLS in front of the API](tls.md) | how-to — the reverse proxy, in one config each |
| [Upgrading between versions](upgrade.md) | how-to — the procedure, the rollback, and the gap |
| [Feeding the MES what people typed elsewhere](inbound.md) | how-to — downtime labels, quality results and counts from another system |
| [Settings](../reference/settings.md) | reference, generated |
| [CLI](../reference/cli.md) | reference, generated |
| [REST API](../reference/api.md) | reference, generated |
| [What to expect in the first week](first-week.md) | explanation — what a shadow run gets wrong at first, and what is worth comparing |
| [Compatibility](compatibility.md) | explanation — what has been tested against what, honestly |
| [Security](security.md) | explanation — threat model, lab defaults, what not to expose |

Not written yet (as of 2026-09-10): a Node-RED example flow over the
[unified namespace](uns.md). Ask in Discussions; a question asked twice
becomes a page.
