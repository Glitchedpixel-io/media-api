"""Front-end API contract generator.

Reads the capability inventory (``docs/capability-inventory.json``) and a
hand-maintained surface map (``surfaces.yaml``) and writes a single Markdown
document describing the API as the six design surfaces use it.

The output is a generated artefact. It is never hand-edited: correcting it
means correcting ``surfaces.yaml`` or the inventory and regenerating.

The document is written to sit permanently in a design tool's context, so
density is a hard requirement rather than a preference. ``--max-bytes``
enforces it.
"""

__all__ = ["__doc__"]
