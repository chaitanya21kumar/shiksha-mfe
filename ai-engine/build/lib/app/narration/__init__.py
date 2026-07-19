"""Module A.3 — narration scripts over a parsed document.

Takes the structured `ParsedDocument` from Module A.1 and derives a spoken
`NarrationScript`: one speakable segment per slide or section, produced through
the same OpenAI-compatible model gateway used for summarisation (Module A.2).

The narration is a separate, generative contract — kept distinct from the
faithful, parser-produced `ParsedDocument` — so it carries its own provenance
and warnings, and later modules (e.g. Module C's interactive video) can consume
the scripts and their per-segment duration estimates without re-deriving them.
"""
