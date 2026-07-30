"""Module C.2 — Auto-chaptering.

Groups a `Transcript` into timed chapters and gives each one a title, so a
recording gains a navigable structure. The boundaries are computed
deterministically in Python; only the titles are generated. See ADR-0008.
"""

__all__ = ["schema", "pipeline"]
