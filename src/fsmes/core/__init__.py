"""Cross-cutting primitives with no domain knowledge.

Nothing in here may import from ``fsmes.kernel`` or ``fsmes.modules`` — this is
the bottom of the dependency graph, and keeping it there is what lets a module
reuse a primitive without dragging the kernel in behind it.
"""
