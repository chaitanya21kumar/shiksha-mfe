# Benchmarks

What this engine costs to run, measured rather than estimated.

Reproduce any figure here with:

```bash
python -m benchmarks.run              # everything, including live model calls
python -m benchmarks.run --offline    # only the parts that need no model
```

## How to read these

**Total wall-clock time is not a useful number on its own.** Most of it is a hosted
model answering over the internet, which describes the provider and the network rather
than this code. Every measurement is therefore split in two:

| | what it is | who controls it |
|---|---|---|
| **Engine time** | parsing, sectioning, grounding, validation, packaging, zip | this service |
| **Provider time** | waiting on an HTTP call to the model gateway | the provider, the network, the model |

Engine time is what a tenant sizes a server against, and it is the only half that
stays the same when an operator swaps providers or self-hosts a model.

The split is measured at the HTTP transport rather than by timing code inside the
pipeline. That puts no instrumentation in the shipped path, and it counts every call
including retries the caller never sees.

Medians, with the range beside them. One slow call to a shared free tier drags a mean
somewhere misleading, and hiding that variance behind a single number would
misrepresent what a tenant should expect.

## The documents

Generated to order, structured like a real teaching document — headings, paragraphs,
sections ending where a teacher would end them — and byte-identical on every machine,
so a figure measured today is comparable with one measured next month.

| Size | Pages | Sections | Words |
|---|---|---|---|
| Small | 2 | 4 | 606 |
| Medium | 20 | 58 | 8,498 |
| Large | 60 | 178 | 26,066 |

The large one is deliberately well past everyday use, so these figures describe the
engine at its heaviest rather than at its most flattering.

## Ingestion — no model involved

Turning a PDF into structured content: pages, headings, and the text beneath each.

| Document | Median | Range |
|---|---|---|
| 2-page | 11 ms | 11–12 ms |
| 20-page | 142 ms | 142–151 ms |
| 60-page | 435 ms | 433–445 ms |

Roughly linear in page count, at about **7 ms per page**. A sixty-page chapter is
structured in under half a second on a laptop.

## Packaging — no model involved

Writing a finished lesson out in each of the three formats. One twelve-step lesson,
which is a long micro-lesson.

| Format | Median | Output |
|---|---|---|
| H5P Course Presentation | 236 µs | 2.0 KB |
| Standalone HTML5 deck | 24 µs | 11.2 KB |
| SCORM 1.2 course | 302 µs | 6.7 KB |

Microseconds. Packaging is not a cost worth planning for.

## End to end — where the time actually goes

One upload through every stage, the way `POST /course/file` runs it.

| Document | Total | Engine | Provider | Range |
|---|---|---|---|---|
| 2-page | 16.86 s | 154 ms | 16.71 s | 15.64–18.64 s |
| 20-page | 29.33 s | 183 ms | 29.15 s | 29.23–50.44 s |
| 60-page | 26.32 s | 76 ms | 26.24 s | 25.41–29.09 s |

**Engine time stays between 76 and 183 milliseconds regardless of document size —
under 1% of the wall clock.** Everything else is the model.

Two consequences worth stating plainly:

* Capacity planning is about **concurrent waiting**, not CPU. A server running this
  spends its time idle on network calls, so it is bound by the provider's rate limits
  and by how many requests it may have in flight, not by cores.
* Making the engine faster would not move the total. If a deployment needs a faster
  answer, the lever is the model and where it runs, not this code.

### Why the 60-page document is not slower than the 20-page one

Because the input is bounded before generation, and **the engine says so**. A document
larger than the configured limits is capped at `max_source_chars` (24,000) and
`MAX_STEPS` (40 sections), and every cap produces a warning naming the stage it came
from:

```
documentinsights: Source text was truncated to 24000 characters before summarising.
microlesson:      Document had 176 sections; used the first 40.
assessment:       Document had 60 sections; used the first 40.
```

That is the design working, not a measurement artefact. A silent cap would make a
sixty-page document look like it had been fully processed; a reported one lets a
teacher decide whether to split the chapter.

### Variance comes from the provider, not the engine

The widest range above — 29.23 s to 50.44 s on the 20-page document — is a single run
in which the primary gateway rate-limited under nine back-to-back builds and the
configured fallback took over mid-run. The build still completed and still produced
every stage. Engine time across those same runs varied by tens of milliseconds.

## What is not measured here

Stated so the table is not read as covering more than it does:

* **Transcription and interactive video.** Both are dominated by the same provider
  wait, and both take a media file rather than a document, so they do not belong on
  the same axis as the figures above.
* **Concurrency.** These are single-request measurements. What happens at fifty
  simultaneous uploads is a property of the deployment and its rate limits, and
  measuring it against a free tier would say more about the tier than the engine.
* **A self-hosted model.** Every provider figure here is a hosted gateway. Self-hosted
  numbers depend entirely on the hardware and are for whoever deploys it to measure on
  theirs — which is exactly why engine time is reported separately.

## Environment

| | |
|---|---|
| Machine | Apple M1, 8 GB |
| Python | 3.12 |
| Model | `openai/gpt-oss-20b` through an OpenAI-compatible hosted gateway |
| Repeats | 5 per CPU measurement, 3 per pipeline measurement |
