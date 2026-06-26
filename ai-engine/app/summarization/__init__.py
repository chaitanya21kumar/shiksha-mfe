"""Module A.2 — local-LLM summarisation over a parsed document.

Takes the structured `ParsedDocument` from Module A.1 and derives learner-facing
insights (a summary, key takeaways, a glossary, and a course outline) using a
locally hosted model via Ollama. The output is a separate `DocumentInsights`
contract, kept distinct from the faithful, parser-produced `ParsedDocument`.
"""
