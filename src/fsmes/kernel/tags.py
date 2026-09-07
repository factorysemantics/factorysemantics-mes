"""What a machine's tags are called, in one place.

These names have to agree across four modules that have no business importing
each other: the line generator writes the columns, the replay serves them, the
agent decides which of them are worth acting on, and two screens decide which
one is "the process value". They had drifted into four spellings under three
names before this module existed.

Deliberately dependency-free - the generator is stdlib-only by design, so
this file must be importable from anywhere without dragging in the ORM.
"""

from __future__ import annotations

# Written by the line generator beside the CSVs: every tag, its kind, its
# unit, and whether anything may write it.
MANIFEST_NAME = "tags.json"

# Monotonic counters. Int64 on the wire, and subject to the agent's
# reset handling.
COUNTER_TAGS: tuple[str, ...] = ("GoodCount", "ScrapCount", "TotalCount")

# Served as integers rather than floats: a PLC state word and an alarm word
# are bit patterns, and rounding one is meaningless.
INTEGER_TAGS: tuple[str, ...] = ("State", "AlarmWord")

# What the MES *acts on* rather than merely records: these set equipment
# state and book production, so they are never sampled down or batched.
SEMANTIC_TAGS: tuple[str, ...] = ("State", "GoodCount", "ScrapCount")

# Everything every machine carries regardless of what it makes. Anything a
# machine publishes that is NOT in here is a candidate for "the process
# value" - which is why this list must grow whenever a common tag is added.
# It did not, once, and the analysis screen started charting ReadyBit.
STRUCTURAL_TAGS: tuple[str, ...] = (
    "State",
    "GoodCount",
    "ScrapCount",
    "TotalCount",
    "AlarmWord",
    "CycleTimeMs",
    "RunMinutes",
    "ReadyBit",
)

# An inspection station's group: the tags a vision system publishes together
# for one judged unit. The agent takes them as one event, keyed by the
# source timestamp the station stamps on all of them, and writes a unit and
# its inspection - never tag history. The attribute tags are named
# "Insp_<Attribute>" from the manifest; these are the fixed members.
INSPECTION_TAGS: tuple[str, ...] = ("InspSeq", "InspSerial", "InspPass", "InspMembers")
INSPECTION_PREFIX = "Insp_"


def inspection_attribute_tag(name: str) -> str:
    return f"{INSPECTION_PREFIX}{name}"
