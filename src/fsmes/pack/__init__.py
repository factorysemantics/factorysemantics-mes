"""Plant packs: the one plant-specific artefact, and the commands that read it.

A pack is one directory of declarative data that answers *which plant is
this?* - identity, clock, profile, which modules this plant serves, what it
calls things, where its data lives, and the files that describe its machines,
its master data and its boundaries. Decision
[0022](../../../docs/decisions/0022-what-a-plant-pack-may-contain.md) fixes
what may be in one; `docs/operate/packs.md` is the page a person reads.

    fsmes pack check <dir>     refuse it before it touches a plant
    fsmes pack apply <dir>     make the plant match the pack
    fsmes pack status <dir>    what the plant runs, and whether it has drifted
    fsmes pack migrate ...     a registry entry, brought forward into a pack

Six modules, in the order a pack meets them: `format` says what a pack may
contain and what it compiles to, `check` refuses one, `apply` applies it and
reports its status, `migrate` writes one from what came before, `masterdata`
reads the data a pack seeds, `fleet` is the list of packs a machine runs, and
`plan` is that list as `deploy/promote.sh` needs it.
"""

from fsmes.pack.format import FORMAT, PLANT_FILE, Pack, PackError, read, settings

__all__ = ["FORMAT", "PLANT_FILE", "Pack", "PackError", "read", "settings"]
