"""The fleet: the plants this installation created, and the plants it watches.

M8 asks for two things that sound like one and are not. **`fsmes fleet`** is
a command that manages the plants this installation owns - create one from a
pack, start it, stop it, apply a pack to it, show what it is doing.
**The console** is a page that observes every plant in the list, owned or
not, and has no write path in it at all.

The line between them is decision
[0023](../../../docs/decisions/0023-the-fleet-console-observes.md): *the
tooling may act only on plants it owns; for every other plant it observes and
cannot push.* Four modules, in the order a plant meets them:

    observe     the only place this package reaches the network. GETs only.
    owned       ownership.toml, and the gate every write path calls first
    commands    the verbs: create, start, stop, apply, status, list
    console     the page, and the JSON behind it. Reads. Imports no verb.

What `fsmes fleet` never does, even to a plant it owns: write a tag to a
PLC, send anything to an ERP, create or close an order, book production, or
touch master data, people or the audit trail. **It manages plants, not
production.** A plant it owns can be stopped; a plant it owns cannot be made
to say it built something.
"""

from fsmes.fleet.owned import FILE, Entry, NotOwned, Observed, Ownership, gate

__all__ = ["FILE", "Entry", "NotOwned", "Observed", "Ownership", "gate"]
