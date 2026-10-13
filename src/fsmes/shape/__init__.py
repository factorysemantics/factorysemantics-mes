"""A plant shaped like somebody else's, from one file that carries none of theirs.

    read_shape("plant-shape.toml") -> Shape        what the file says
    from_shape(shape, name=..., buffer=...) -> Plant   the typed plant in the middle
    to_pack(plant, "./acme") -> Path              line.json, tag_map.json, masterdata/...

The model in the middle is the point. A converter that went straight from a
shape file to `line.json` would have to throw away everything `line.json` has
no column for - which area a machine sits in, what feeds what, how the tags
are organised above the machine - and would have to be rewritten the first
time a plant was not one straight line. Here those are fields on a typed
object graph, the pack files are outputs of it, and a new routing style is a
shape field and a model field rather than a new converter.
"""

from .model import BUFFER_DEFAULT, SHIFT_SECONDS, Plant, from_shape
from .pack import to_pack
from .read import HIGHEST, NotAShape, Shape, read_shape

__all__ = [
    "BUFFER_DEFAULT",
    "HIGHEST",
    "SHIFT_SECONDS",
    "NotAShape",
    "Plant",
    "Shape",
    "from_shape",
    "read_shape",
    "to_pack",
]
