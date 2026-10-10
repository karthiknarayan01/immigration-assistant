# Engineering notes

A text-first assistant for US immigration questions. The browser converts
speech to text (Web Speech API) and posts the text here; the backend runs a
model-agnostic agent that searches official sources and community reports,
then streams an answer back with a status line for the waiting user.

> Not legal advice. The agent hands off to an attorney for denials, removal
> proceedings, unlawful presence, criminal history, and anything touching
> misrepresentation.

## The three jobs

1. **Fact provider** — settled law and current official policy. Served first by
   the local 8 CFR pack (no network, full section text, a citation), then by
   official-source search for fees, processing times and the visa bulletin.
2. **Recent developments** — proposals, executive orders, rule changes. Served
   by a general web search tool; the prompt forces a hard separation between
   what is in force, what is proposed, and what is merely reported.
3. **Reasoning and strategy** — scenario questions. The prompt drives a
   multi-tool pass (rules → current policy → reported outcomes → recent
   changes) and requires alternatives, pros and cons, and a calibrated,
   sourced view of likelihood.

## Architecture

```
Browser (Next.js, Vercel)
   │  Web Speech API → text; POST /api/chat (SSE)
   ▼
Backend (Cloud Run, FastAPI)
   ├── /chat streams tokens + status events over SSE
   ├── text_agent loop: model ⇄ tools, up to 4 rounds
   ├── llm: any OpenAI-compatible endpoint, fallback + reasoner routing
   ├── knowledge: local corpus (8/20/22 CFR · 9 FAM · USCIS Policy Manual)
   └── tools: official search · Federal Register · developments · community
```

**No audio, no WebSocket.** The earlier version was a Pipecat speech-to-speech
pipeline locked to Gemini Live on Vertex. That tied the model choice to one
provider and forced Python 3.12 (pipecat needs `audioop`, gone in 3.13). Moving
speech-to-text into the browser removed the whole audio path: the backend is
now a plain HTTP service over any OpenAI-compatible model.

**Nothing is stored.** The browser keeps the transcript; the server holds state
only for the duration of a request.

### The local corpus

Built offline by `scripts/build_knowledge_pack.py` and committed, because the
sources it draws from are either slow, rate-limited, or actively hostile to
programmatic access:

| Source | How | Notes |
|---|---|---|
| 8 CFR, 22 CFR, 20 CFR | eCFR API, per part | Free, no key. `/full/` 406s without `Accept-Encoding`; repeated `?part=` params do not OR — one request per part |
| 9 FAM | `fam.state.gov` tree JSON + 149 section pages | Its own host, **not** behind the `state.gov` Cloudflare block. Serves an incomplete cert chain, so TLS verification is delegated to the OS trust store |
| USCIS Policy Manual | one Drupal book export | All 12 volumes in a single GET — a 667-page crawl becomes one request |

`travel.state.gov` (Visa Bulletin) and `dol.gov` are behind Cloudflare and
Akamai respectively and return **403 to programmatic requests**, so the Visa
Bulletin is not in the corpus; it still comes from live search, which is the
right place for a monthly number anyway.

Retrieval is BM25 over ~5,600 chunks: ~1s to index at import, ~25ms a query.
Primary law is weighted above agency guidance (CFR 1.0, USCIS Policy Manual
0.7, 9 FAM 0.6), and results are de-duplicated by citation — without both, one
long 9 FAM section outscored 8 CFR on term overlap alone for a question about
what the law provides. The weights are tuned against the retrieval assertions
in `tests/test_knowledge.py`, not chosen by taste.

### Retrieval is the bottleneck, and tuning has run out

The generated benchmark made this measurable for the first time. Of 144
questions drawn directly from the corpus, the agent's tools returned the very
section the question came from only **60% of the time** — and in a further third
of those cases it retrieved the section and still did not state the fact.

So roughly **40% of failures are retrieval**: the agent cannot state a fact it
never saw. That is a different problem from reasoning, and it needs a different
fix.

Two lexical fixes were tried, measured, and **rejected**:

- **Source weights.** FAM and Policy Manual were weighted down to 0.7/0.6
  against a single retrieval case, in a corpus where 60% of the generated
  questions come from the Policy Manual. Raising them to 0.95/0.9 moved
  retrieval to 64.6% and lifted naturalization from 25% to 41.7%, but left
  overall accuracy flat and broke two retrieval tests.
- **Morphological expansion.** BM25 treats "revoked", "revocation" and "revoke"
  as unrelated, which is wrong for legal text. Adding weighted family heads
  widened recall but measured *worse* on retrieval (56.9%) and was reverted.

Across three runs the numbers were 51.4%, 52.8% and 50.7% — with 144 cases the
standard error is about four points, so **none of these differences is
distinguishable from noise**. The honest conclusion is that lexical tuning is
exhausted: the remaining failures are vocabulary mismatch between a
natural-language question and statutory text, and that needs semantic
retrieval.

That means an embedding dependency — `onnxruntime` with a small ONNX model
(~90 MB) is the lightest option, `sentence-transformers` needs torch — fused
with the existing BM25. It is a real dependency decision rather than a tweak,
which is why it has not been made unilaterally.

Two caveats on the measurement itself: 144 cases cannot resolve a four-point
effect, so the full 707 should be run before any of this is called settled; and
`citation appears in the tool results` is a coarse proxy for "the agent found
the passage".

### Grounding is enforced, not requested

The first real benchmark found that **38 of 77 answers consulted no tool at
all** and were written from memory. Two different models did it, so it was not
a model failing: every strong instruction in the prompt was about *numbers*
("never state a figure you have not looked up"), which leaves every non-numeric
fact — eligibility rules, form requirements, definitions — to memory.

A probe (`scripts/probe_tool_use.py`) measures it directly, and shows the
prompt is the cause rather than an excuse: with no system prompt, both models
call a tool on 3 of 3 factual questions; with the prompt, 1-2 of 3. Rewording
the rule did not restore it.

So the first tool call is now **required** (`tool_choice="required"`), and later
rounds are free. Whether a lookup happened is a product promise, not a mood —
and the choice of model follows from it, because forced lookups make some models
emit raw tool-call tokens into the answer text while others stay clean.

### Source trust

Everything retrieved is tiered before the model sees it.

| Tier | Handling |
|---|---|
| Government, eCFR, Federal Register | Stated as fact, with a date |
| AILA, Murthy, Fragomen, Boundless | Attributed, not asserted |
| Reddit, X, forums | Never fact — only "what people report" |
| Anything unrecognised | Treated as anecdotal |

Anecdotes are filtered before the model sees them: a pattern is only called one
when **three independent** people report it. Stale *numbers* are rejected
outright; undated *stories* are allowed with the date flagged.

### When things break

Failures are classified before they're handled — **funds**, **auth**,
**connectivity**, **other** — because the right response differs sharply.

Retries are bounded by a **deadline, not an attempt count**, and only for
failures a retry can fix (a 500 or a dropped connection, not a 401 or 402).
The model call retries once, then falls back to the configured fallback model,
then fails. A tool failure never ends the turn: the model is told the lookup
failed so it says so instead of guessing.

| What failed | What happens |
|---|---|
| Official-source search | Answer continues, and says its sources were unavailable |
| Community search | Degrades quietly |
| The model (credit, auth) | Stream ends with a plain, classified message |
| User goes offline mid-request | The client detects it and says so |

### Status while you wait

The status line is built from the model's own tool arguments — "checking
official guidance on the H-1B grace period" — never from a tool name, provider,
or URL. It is deliberately not generated by a model, because that would add
latency to the thing that exists to cover latency and be the one part of the
turn that could hallucinate about itself.

## How it's evaluated

Answers are graded by a separate, stronger model (the `JUDGE_MODEL`) against a
rubric written in advance. The judge never sees an expected answer — only the
question, the rubric, and what the agent said.

Eight factors: **correctness, completeness, groundedness, calibration, safety,
actionability, reasoning, usefulness.** Each factor means something different
per task, so the judge is given the task's own definition.

**Tool use is verified deterministically.** Cases that declare an expected tool
sequence are checked against the tools the agent actually called, in order —
"did it look this up" is a fact about the run, not a judgement call. This is
what makes the recent-developments and reasoning functions testable: a recent
question must check the Federal Register *and* the reporting; a strategy
question must sweep the rules, policy and reported outcomes.

**Cost and latency are reported beside the score.** A model choice is a
score-per-dollar question and a score alone cannot answer it, so the report
carries tokens, p50/p95 latency and, when prices are configured, cost per case.
Prices are left unset by default: a hardcoded price goes stale silently and
then the report lies about cost.

**Coverage is reported, not assumed.** Cases are tagged along a taxonomy —
task x domain x difficulty x failure mode — and the run prints the gaps. "79
cases" is not "the domain is covered", and the report should say which is true.

Cases are split `tune`/`holdout` by a stable hash; prompt changes are made
against `tune`, and the headline number is `holdout`. Safety is a gate, not an
average. Out-of-scope questions must be declined.

The suites are stratified rather than exhaustive. `adversarial` covers false
and outdated premises, prompt injection and memory traps; `multiturn` exercises
the transcript path, which is otherwise untested; `scope` checks that
non-immigration questions are declined. Question generation is model-driven:
`evals/generate_evals.py` reads the corpus and writes the smallest possible
"atomic" questions (one fact each, with a rubric), then builds combos from two
to four atomics — atomic questions make each fact separately checkable, combos
verify the agent holds several at once.

## Layout

```
voice/app/llm.py           OpenAI-compatible client, streaming, tool schemas
voice/app/text_agent.py    the agent loop (model ⇄ tools → answer)
voice/app/prompts/         one file per task, composed at load
voice/app/tools/           search providers, tool registry, source tiering
voice/app/knowledge/       local corpus: BM25 over 8/20/22 CFR, 9 FAM, Policy Manual
voice/ingest/              offline fetchers: eCFR, 9 FAM, USCIS Policy Manual
voice/evals/taxonomy.py    the coverage grid
voice/evals/tasks/<task>/  cases.yaml + factors/*.md, mirroring prompts
```

Prompts are split by task because each is separately owned and separately
regressible. Factor files exist because "groundedness" means different things
for a CFR citation and a forum post.
