# Immigration Assistant

A voice assistant for US immigration questions — H-1B, F-1, B-1/B-2, L-1 and
employment-based green cards. You talk to it, it talks back.

What makes it different from a search box: it separates **what the law says**
from **what actually happens in practice**, and says so out loud when the two
diverge. That gap is usually the most useful part of the answer, and it's the
part you can't easily find alone at midnight.

[voice agent](../voice/) · [evals](../voice/evals/) · [prompts](../voice/app/prompts/)

> Not legal advice. The agent hands off to an attorney for denials, removal
> proceedings, unlawful presence, criminal history, and anything touching
> misrepresentation.

---

## Measured results

Held out from tuning, 22 cases the prompts were never fitted against.

| | |
|---|---|
| Answer quality | **2.10 / 3** |
| Cases passing the bar | **55%** |
| Unsafe answers | **14%** |
| Time to first token (median) | **1.9s** without search, **6.2s** with |

**What the bar is.** A case passes only if it averages 2.5 out of 3 across
all six factors *and* clears the safety floor — roughly 83% on every
dimension at once, which is deliberately hard. Of the cases that miss it,
about two thirds are safe answers held back by completeness and
groundedness rather than by anything wrong. Those two factors average 1.67
and 1.73; the rest sit above 2.1.

**What unsafe means.** Safety is a gate rather than an average: if a
question involves denial, removal, unlawful presence, criminal history or
misrepresentation and the answer makes no attorney referral, the case fails
regardless of how good the rest of it was.

## What a turn looks like in the logs

Every user turn gets a request id. Filter on it and you get the whole turn.

```
req=8f2a1c4d9e01 | event=user_query text='my H-1B employer is laying me off'
req=8f2a1c4d9e01 | event=tool_call name=search_official_guidance
req=8f2a1c4d9e01 | stage=ttft_segment name=tool_exec ms=147 hits=4
req=8f2a1c4d9e01 | event=tool_result summary={"count": 4}
req=8f2a1c4d9e01 | stage=ttft name=answer ms=2380 tool_calls=1
req=8f2a1c4d9e01 | event=agent_response chars=612
```

A slow or wrong answer in production is otherwise unattributable. You can see
that it was bad; you can't see why.

## Speed

![TTFT by component](../voice/docs/latency.png)

Latency here means **time to first token** — when the user first hears
something. Total response time is experienced as answer length rather than as
lag, because audio streams as it is produced.

| Segment | mean | median | share |
|---|---|---|---|
| Model decides to search | 2150 ms | 1210 ms | 33% |
| Search runs | 1639 ms | 862 ms | 25% |
| Model starts answering | 2683 ms | 1861 ms | 41% |

Median time to first token is **1.9s** without a search and **6.2s** with one.

**Search is the smallest part of a tool-using turn.** Three quarters of it is
inference — once to decide a lookup is needed, then again to read what came
back. Questions the regulations settle avoid that entirely: those are served
from a local copy of 8 CFR in under a millisecond, with no network call.

While the agent works, the client plays a quiet tone, starting the moment the
question lands. Where there is something honest to name, a status line says
what is being looked up, built from the model's own tool arguments and
stripped of tool names, providers and search operators. The agent itself says
nothing until it has an answer.

```bash
uv run python scripts/latency_report.py
```

---

## How it's evaluated

49 cases, six factors each, scored 0–3 by `gemini-2.5-pro` — a different and
stronger model than the agent, which never sees an expected answer.

**21 cases are real questions from immigration forums.** Hand-written eval
questions measure what the author imagined users ask. Real ones carry the
actual phrasing: *"OPT (non-STEM) expired, in grace period, H1B selected. Do I
qualify for…"*

Only the *questions* come from forums. Rubrics are built from authoritative
sources retrieved through the agent's own search tool — grading against forum
answers would encode folklore as correctness. Nine harvested cases were
**excluded** because retrieval couldn't ground a rubric; they're in
`excluded.yaml` with reasons rather than quietly dropped.

**Safety is a gate, not an average.** Miss an attorney referral on a removal
question and the case fails, however articulate it was.

**Cases are split tune/holdout by a stable hash.** Prompt changes only touch
`tune`; the reported number is `holdout`, which the tuning never sees. Without
that separation the score measures how well the prompt was fitted to the
questions rather than how the agent answers new ones.

```bash
PYTHONPATH=. uv run python evals/run_eval.py --split holdout
PYTHONPATH=. uv run python evals/run_eval.py --no-tools   # degraded mode
```

`--no-tools` measures what the agent does when search is unavailable. That
run is not a benchmark of the product and is never quoted as one — comparing
it against a normal run measures the tools rather than the agent.

## Architecture

```
Browser (Next.js, Vercel)
   │  WebSocket, protobuf audio
   ▼
Voice agent (Cloud Run, Pipecat)
   ├── Gemini Live via Vertex AI — speech in, speech out
   ├── Session state in memory, dropped on disconnect
   └── Tools, tiered by latency budget
```

**WebSocket, not WebRTC.** Cloud Run supports no UDP, so a WebRTC media path
can never establish there. Easy to miss, because signalling is plain HTTP and
succeeds — ICE just sits at `checking` and the client looks like it's
connecting slowly.

**Vertex AI, not AI Studio.** Google Cloud credits apply to Vertex but not to
AI Studio keys, which bill a card instead.

**Nothing is stored.** Session state dies with the connection; the browser
keeps its own transcript. For this audience that's a feature, not just a cost
decision.

| Piece | Job |
|---|---|
| Gemini Live (Vertex) | Native speech-to-speech, no separate STT/TTS |
| Pipecat | Audio pipeline, turn detection, tool dispatch, RTVI |
| Silero VAD + Smart Turn v3 | On-device turn detection, so a thinking pause isn't mistaken for a finished sentence |
| Tavily / Exa | Official-source search; Exa finds current USCIS pages, Tavily is cheap and broad |
| Parallel | Community search — 10 relevant Reddit threads where Tavily gets 2 and Exa gets 0 |
| eCFR API | Primary regulation text with real citations, no key needed |

### Source trust

Everything retrieved is tiered before the model sees it.

| Tier | Handling |
|---|---|
| Government, eCFR, Federal Register | Stated as fact, with a date |
| AILA, Murthy, Fragomen, Boundless | Attributed, not asserted |
| Reddit, X, forums | Never fact — only "what people report" |
| Anything unrecognised | Treated as anecdotal. An unfamiliar blog isn't trustworthy just for not being Reddit |

Anecdotes get filtered before the model sees them: spam and hearsay scored
out, scraped page furniture dropped, and a pattern only called one when
**three independent** people report it — four posts by the same author is one
opinion. Stale *numbers* are rejected outright; undated *stories* are allowed
with the date flagged. A stale number is a false fact. An old story is still a
true story.

### When things break

Failures are classified before they're handled, because the right response
differs sharply: **funds**, **auth**, **connectivity**, **other**.

Retries are bounded by a **deadline, not an attempt count**. An attempt count
lets a retry start at 4.5s of a 6s budget and make the turn worse than the
failure would have; the deadline means a retry only happens if there is time
for it to help. One retry, 250ms back-off, and only for failures a retry can
fix — a 500 or a dropped connection may differ next time, a 401 or a 402 will
not, so those fail immediately rather than buying a second of silence.

What the user gets depends on what broke:

| What failed | What happens |
|---|---|
| Official-source search | Answer continues, and says its sources were unavailable |
| Community search | Degrades quietly — a less colourful answer isn't worth an apology |
| The model itself (credit, auth) | Session ends with a plain explanation; no retry button, because retrying a billing failure just reproduces it |
| Connection drops mid-conversation | Shown as reconnecting, not fatal — the transport recovers on its own |

### Turn-taking

Never cutting someone off matters more here than almost anywhere. Most users
are non-native English speakers asking questions that affect their lives, and
they pause mid-sentence to find words. Three layers: Gemini's server VAD with
low end-sensitivity and a 1.5s silence window, a local ONNX turn model, and an
LLM classifier told to bias toward "incomplete" on trailing conjunctions and
dangling numbers.

## Layout

```
voice/app/prompts/         one file per task, composed at load
voice/evals/tasks/<task>/  mirrors it — cases.yaml + factors/*.md
```

Prompts are split by task because each is separately owned and separately
regressible. Editing anecdote handling shouldn't mean scrolling past
escalation rules. The composition was verified word-identical to the single
string it replaced, and tests assert every escalation trigger survives a
refactor.

Factor files exist because "groundedness" means different things for a CFR
citation and a forum post. Each task defines its own.

## Running it

Needs Python 3.12 — pipecat depends on `audioop`, gone in 3.13.

```bash
cd voice
cp .env.example .env        # set GOOGLE_CLOUD_PROJECT_ID
uv sync
uv run python -m app.server
uv run python scripts/check_handshake.py http://localhost:8080
```

That last one drives a real `client-ready` → `bot-ready` exchange. It exists
because an earlier check verified only connectivity, passed happily, and the
browser hung on "connecting" forever.

Push to `main` or `dev` and it deploys itself: tests gate the deploy, the new
revision is smoke-tested, auth is keyless via Workload Identity Federation.

## Known limits

- **Completeness and groundedness are the lowest-scoring factors**, at 1.67
  and 1.73. They are what holds most answers below the pass bar. Part of it is
  structural: on a turn where the agent answers without searching there is no
  source to attribute to.
- **The USCIS Policy Manual is not ingested.** The local pack is regulation
  text, so it does not contain cap-gap or preconceived intent — those are
  USCIS policy and consular doctrine. uscis.gov is reachable from a laptop, so
  this is available work.
- **Forum posts come back undated** from every provider tried; measured
  2026-09-23, none of the community results carried a date. Reddit's own API
  refuses datacenter and residential traffic alike. Undated stories are kept
  with the date flagged; undated *numbers* are discarded.
- **X search is built but inactive.** It needs `XAI_API_KEY`, and is untested
  against the live API until one is set.
