"""Module A.2 — local-LLM summarisation over a parsed document.

Takes the structured `ParsedDocument` from Module A.1 and derives learner-facing
insights (a summary, key takeaways, a glossary, and a course outline) using a
configurable OpenAI-compatible model gateway — a local model such as Ollama, or a
hosted provider serving the same contract. The output is a separate
`DocumentInsights` contract, kept distinct from the faithful, parser-produced
`ParsedDocument`.
"""
