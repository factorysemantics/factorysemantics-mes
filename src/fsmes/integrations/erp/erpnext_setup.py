"""Preparing the ERPNext side, and saying plainly whether it is prepared.

The connector needs five custom fields on ERPNext's Work Order doctype. They
are not optional and they are not created by ERPNext: without them the MES
cannot tell which orders it has taken, and the numbers it counted have
nowhere to land. This module is the one place they are defined, so the
seeder in `labs/`, the `fsmes erp setup` command and the check the sync
worker runs at start-up all agree about what "prepared" means.

It lives in the package rather than in `labs/` because a person who
installed a wheel has no `labs/`. That was the actual defect: the fields
existed only in a demo seeding script outside the wheel, while the
documentation said nothing was needed on the ERPNext side.
"""

from fsmes.integrations.erp.erpnext_adapter import ErpNextClient, ErpNextError

DOCTYPE = "Work Order"

# What ERPNext has nowhere to put: whether the MES has taken this order, and
# what the machines actually counted. `allow_on_submit` because a submitted
# work order is precisely when these change.
CUSTOM_FIELDS: list[dict] = [
    {
        "fieldname": "custom_mes_synced",
        "label": "Sent to MES",
        "fieldtype": "Check",
        "insert_after": "status",
        "allow_on_submit": 1,
        "read_only": 1,
        "description": "Set by the MES when it imports this order. Clear it to re-send.",
    },
    {
        "fieldname": "custom_mes_good_qty",
        "label": "MES Good Qty",
        "fieldtype": "Float",
        "insert_after": "custom_mes_synced",
        "allow_on_submit": 1,
        "read_only": 1,
    },
    {
        "fieldname": "custom_mes_scrap_qty",
        "label": "MES Scrap Qty",
        "fieldtype": "Float",
        "insert_after": "custom_mes_good_qty",
        "allow_on_submit": 1,
        "read_only": 1,
        "description": "Machine-counted scrap. ERPNext has no native field for this.",
    },
    {
        "fieldname": "custom_mes_over_qty",
        "label": "MES Over Qty",
        "fieldtype": "Float",
        "insert_after": "custom_mes_scrap_qty",
        "allow_on_submit": 1,
        "read_only": 1,
        "description": "How far past the ordered quantity the line actually ran. A counter "
                       "delta can carry more than one unit, so an order for 15 can finish at "
                       "16 good; this is the number that says so, rather than leaving a "
                       "reader to notice that the good qty exceeds the order.",
    },
    {
        "fieldname": "custom_mes_lot",
        "label": "MES Lot",
        "fieldtype": "Data",
        "insert_after": "custom_mes_over_qty",
        "allow_on_submit": 1,
        "read_only": 1,
    },
]

FIELD_NAMES = [field["fieldname"] for field in CUSTOM_FIELDS]


def installed_fields(client: ErpNextClient) -> dict[str, dict]:
    """What of ours is already on the Work Order doctype, by fieldname.

    One query, so a site with none of them costs the same as a site with all
    of them. Fields somebody else created with our names come back too — that
    is the point, since a Data field where we expect a Float loses numbers.
    """
    rows = client.list(
        "Custom Field",
        filters=[["dt", "=", DOCTYPE], ["fieldname", "in", FIELD_NAMES]],
        fields=["fieldname", "fieldtype", "allow_on_submit"],
        limit=len(FIELD_NAMES),
    )
    return {row["fieldname"]: row for row in rows}


def field_problems(client: ErpNextClient) -> list[str]:
    """Every reason the ERPNext side is not ready, in plain language.

    Empty means ready. Each string names one field and says what is wrong
    with it, because "something is misconfigured" is not a thing anyone can
    act on.
    """
    present = installed_fields(client)
    problems: list[str] = []
    for field in CUSTOM_FIELDS:
        name = field["fieldname"]
        row = present.get(name)
        if row is None:
            problems.append(f"{name} is missing from {DOCTYPE}")
            continue
        if row.get("fieldtype") != field["fieldtype"]:
            problems.append(
                f"{name} is a {row.get('fieldtype')} field on {DOCTYPE}, "
                f"but the MES writes a {field['fieldtype']}"
            )
        if not row.get("allow_on_submit"):
            problems.append(
                f"{name} does not allow changes on submit, so the MES cannot write it "
                f"to a submitted work order"
            )
    return problems


def ensure_custom_fields(client: ErpNextClient) -> list[tuple[str, str]]:
    """Create any of the fields that are not there. Idempotent.

    Returns one `(fieldname, what happened)` pair per field, so the caller can
    print exactly what it did rather than a count nobody can check.
    """
    present = installed_fields(client)
    outcome: list[tuple[str, str]] = []
    for field in CUSTOM_FIELDS:
        name = field["fieldname"]
        if name in present:
            outcome.append((name, "already there"))
            continue
        client.insert("Custom Field", {"dt": DOCTYPE, **field})
        outcome.append((name, "created"))
    return outcome


def check(client: ErpNextClient, *, company: str = "") -> tuple[bool, list[str]]:
    """Is this ERPNext usable by the MES? Returns (ok, lines to print).

    Every line begins `ok` or `NOT OK` so the answer survives being pasted
    into an issue. The first failure that makes the rest meaningless — the
    site being unreachable — stops the check there rather than reporting four
    more failures that are all the same failure.
    """
    lines: list[str] = []
    try:
        client.list(DOCTYPE, limit=1)
    except ErpNextError as exc:
        lines.append(f"NOT OK  cannot read {DOCTYPE} at {client.base_url}: {exc}")
        lines.append("        check MES_ERPNEXT_BASE_URL, MES_ERPNEXT_SITE and the credentials.")
        return False, lines
    except Exception as exc:  # a connection refused, a DNS failure, a timeout
        lines.append(f"NOT OK  cannot reach {client.base_url}: {type(exc).__name__}: {exc}")
        return False, lines
    lines.append(f"ok      reached {client.base_url} and read {DOCTYPE} as an authorised user")

    problems = field_problems(client)
    if problems:
        for problem in problems:
            lines.append(f"NOT OK  {problem}")
        lines.append(f"        run `fsmes erp setup` to create the {len(CUSTOM_FIELDS)} fields the MES needs.")
    else:
        lines.append(f"ok      all {len(CUSTOM_FIELDS)} custom fields present: {', '.join(FIELD_NAMES)}")

    if company:
        if client.exists("Company", company):
            lines.append(f"ok      company {company!r} exists; only its work orders will be imported")
        else:
            problems.append(f"company {company!r} does not exist on this site")
            lines.append(f"NOT OK  company {company!r} does not exist on this site — no order would ever be imported")
    else:
        lines.append("ok      MES_ERPNEXT_COMPANY is empty: work orders of every company on this site are imported")

    return not problems, lines
