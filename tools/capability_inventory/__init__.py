"""Capability inventory harness.

Produces an annotated inventory of the media-api HTTP surface: what each
endpoint is, what it costs, what data is reliably present behind it, and what a
front-end can responsibly build on top of it.

The phases are independent and each one degrades to an explicit ``UNKNOWN``
rather than a guess when its inputs are unavailable:

* Phase 1 (:mod:`.static_surface`) reads the OpenAPI document generated in
  process from the app object. No running server required.
* Phase 2 (:mod:`.annotate`) walks the router -> service -> repository call
  graph with :mod:`ast` and cross-references SQLAlchemy metadata for indexes.
* Phase 3 (:mod:`.data_shape`) issues read-only SQL for row counts, fill rates,
  cardinality and collection-size distributions.
* Phase 4 (:mod:`.probes`) times HTTP probes declared in ``probes.yaml``.
* Phase 5 (:mod:`.dead_surface`) looks for evidence that an endpoint is used.
"""

__all__ = ["__doc__"]
