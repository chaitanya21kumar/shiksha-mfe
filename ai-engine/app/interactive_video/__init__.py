"""Module C.3 — Interactive video.

Assembles a chaptered transcript and its knowledge checks into an importable
`H5P.InteractiveVideo` package: the chapters become bookmarks in the player's
navigation bar, and the questions become interactions that pause the video. See
ADR-0009.
"""

__all__ = ["schema", "emit"]
