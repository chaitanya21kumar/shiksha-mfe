# ADR 0010 — Checking the spelling of prose we generated, and only prose we generated

- **Status:** Accepted
- **Date:** 2026-08-01
- **Deciders:** Manu Gupta (external reviewer, midpoint evaluation); Chaitanya Kumar (contributor)
- **Context ref:** Midpoint evaluation, 31 July 2026 — "add a gateway for grammar and
  spelling check". Sits alongside the grounding gate in
  [ADR-0003](0003-neutral-assessment-contract-and-grounding.md), which enforces a different
  kind of correctness.

## Context

Every module returns text a learner will read: a summary, a glossary definition, a
narration script, a question prompt, a chapter title. Pydantic proves that text is
the right *shape*. Nothing proved it was well written.

The reviewer's point at the midpoint was that a lesson with a typo in it reads as
unfinished regardless of how good the pipeline behind it is, and that a teacher
should not be the first person to notice.

This is a different problem from grounding, and conflating them would weaken both.
Grounding is a correctness guarantee: a question that cannot be traced to the source
is dropped, because shipping it would teach something the document never said.
Spelling is a quality signal: a misspelt but correct question is still a correct
question. One blocks, the other advises.

## Decision

**1. Flag; never rewrite.** A correction is a guess. The checker cannot tell a typo
from a term it has not met, and silently editing generated text would change meaning
on that guess. It would also make the same input stop producing the same package,
which the byte-reproducibility tests in packaging depend on. Every issue carries a
suggestion for a person to accept or reject, and the artefact is returned exactly as
generated.

**2. Only text the model composed is checked.** Each artefact mixes our words with
the author's, and the split is enforced field by field in `app/validation/artefacts.py`:

| Checked (ours) | Not checked (the author's) |
|---|---|
| `summary`, `key_takeaways` | the source document's title and filename |
| `glossary[].definition` | `glossary[].term` — lifted from the document |
| `outline[].title`, `outline[].points` | — |
| `segments[].script` | `segments[].title` — carried across from the slide |
| question `prompt`, `explanation`, choice text, key points, `model_answer` | the evidence quote — verified by the grounding gate, never stored |
| `chapters[].title` | the transcript — a record of what a speaker said |

Marking a glossary term would be telling a teacher their own textbook is misspelt.
"Correcting" an evidence quote would break the grounding guarantee outright.

**3. The source document is the allow-list.** A lesson about photosynthesis
legitimately contains words no general dictionary carries. Every word in the source
is therefore accepted: if the author wrote it, it is correct for this document by
definition. This is the single decision that makes the feature usable rather than
noisy, and it needs no configuration from anyone.

**4. Compounds are judged by their parts.** English forms compounds freely and
dictionaries do not list them. Checking `light-independent` as one token flags a
perfectly good phrase from a biology lesson; splitting it and checking `light` and
`independent` does not. Productive prefixes that are not words on their own —
`multi`, `non`, `pre`, `self` and the rest — are excused, but only once a word has
actually been split, so `post` standing alone is still checked like anything else.

**5. A language we have no dictionary for is skipped, and says so.** The library
ships twelve languages and no Indic language is among them. Running the English
dictionary over Hindi would flag every word in the document. Membership is tested
*before* the checker is constructed, because `SpellChecker` raises on an unknown
language — so without that test a tenant teaching in Hindi would turn a quality
check into a 500. The report distinguishes `not_run` from `passed`: a skipped check
is not a passed check, and that difference has to survive into what a caller sees.

**6. The dependency is optional.** The suite must keep running with no API key, no
network and no extra install. `pyspellchecker` lives in the `spelling` extra; absent,
the check records itself as skipped with the reason and everything else proceeds.

## Alternatives considered

**LanguageTool** (`language-tool-python`) would have given real *grammar* checking,
not just spelling, which is closer to what was asked for. Rejected for three
independent reasons, any one of which would be enough: it is **GPL-3.0**, which is
not a licence to pull into a service Tekdi ships to a tenant; it requires a **Java 17
runtime** in what is deliberately a single-runtime Python image
([ADR-0001](0001-standalone-lms-agnostic-engine.md)); and it **downloads a language
pack on first use**, which breaks the offline guarantee the whole test suite rests on.

**A second model to check the first.** Rejected on principle. Asking a model to
grade another model's output re-uses the faculty that produced the error, costs a
second call per artefact, and gives a non-deterministic answer to a question with a
deterministic one. The same reasoning already governs the grounding gate, where the
verifier is a plain string search with no model in it.

**`symspellpy`** is MIT and fast, but ships English frequency data only, which fails
requirement 5 immediately. **`autocorrect`** is LGPLv3 and its API rewrites text
rather than reporting on it, which is the opposite of decision 1. **`spylls`** is a
faithful Hunspell port but its licensing is contradictory — an MIT classifier on
PyPI, an empty licence field, and an MPL-2.0 `LICENSE` file whose own README argues
the code is a derivative work of Hunspell.

**Blocking on a spelling failure.** Rejected. A pipeline that refused to return a
lesson over one flagged word would be worse than one that returns it with a note
attached, and the flagged word is as likely to be a proper noun as a mistake.

## Consequences

- Warnings surface through the `warnings` list each artefact already carries, so a
  caller who checks one place sees these too. `ValidationReport` is available for a
  caller that wants structure — severity, field path, suggestion — but is never put
  inside a package.
- Grammar proper is **not** covered. This checks spelling. Saying "grammar and
  spelling gateway" would overstate it, and the honest scope is worth more than the
  broader claim: subject-verb agreement, tense and register are not checked, and the
  only credible offline option for them was ruled out above.
- The allow-list is per artefact, deliberately. Sharing one across documents would
  let one lesson's vocabulary silently excuse another's typos.
- Real defects found while building this, both from the checker's own output: a
  compound-adjective false positive that would have fired on ordinary educational
  prose, and a test fixture using `Rhizobium` — a word the dictionary already knows —
  which meant the glossary allow-list test passed whether or not the allow-list
  worked.
