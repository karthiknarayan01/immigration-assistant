# Immigration Assistant

A voice-first assistant for US immigration questions — H-1B, F-1, B-1/B-2,
L-1, and employment-based green cards.

The thing it tries to do that a search engine does not is separate **what the
law says** from **what actually happens in practice**, and say so out loud
when those two diverge. That gap is usually the most useful part of the
answer, and it is the part an anxious person cannot easily find alone.

Most users are non-native English speakers asking questions that materially
affect their lives. Three constraints follow from that, and they drive most of
the design decisions below: never cut someone off mid-sentence, never state a
forum anecdote as if it were the rule, and never invent a number.

> **Not legal advice.** The agent is instructed to hand off to an immigration
> attorney for denials, notices to appear, unlawful presence, criminal
> history, and anything touching misrepresentation.

---

## Architecture

```
Browser (Next.js on Vercel)
   │  WebSocket, protobuf-framed audio
   ▼
Voice agent (Cloud Run, Python, Pipecat)
   ├── Gemini Live via Vertex AI — speech in, speech out
   ├── Session state in memory only, dropped on disconnect
   └── Tools, tiered by latency budget
```

**WebSocket, not WebRTC.** Cloud Run supports only HTTP/1.1, HTTP/2 and
WebSockets — no UDP — so a WebRTC media path cannot establish there. This is
easy to miss because WebRTC *signalling* is plain HTTP and succeeds, leaving
ICE stuck at `checking` and a client that looks like it is merely connecting
slowly.

**Vertex AI, not the Gemini Developer API.** Google Cloud credits apply to
Vertex but not to AI Studio keys, which bill a card instead.

**No conversation storage.** Session state lives in the worker process and
dies with the connection. The browser keeps its own transcript in IndexedDB.
That is a privacy property worth having for this audience, not only a cost
decision.

### Components

| Piece | What it does |
|---|---|
| **Gemini Live** (`gemini-live-2.5-flash-native-audio`, Vertex) | Native speech-to-speech: hears the user, reasons, and speaks, with no separate STT or TTS stage |
| **Pipecat** | Orchestrates the audio pipeline, turn detection, tool dispatch, and the RTVI protocol the browser client speaks |
| **Cloud Run** | Hosts the agent, scale-to-zero, session affinity so a conversation stays on one instance |
| **Next.js / Vercel** | Chat UI and the voice overlay; connects directly to the agent over a WebSocket |
| **Silero VAD + Smart Turn v3** | On-device acoustic and semantic turn detection, so a thinking pause is not mistaken for a finished sentence |
| **Tavily** | Cheap, broad web search; the workhorse for official-source lookups |
| **Exa** | Neural search; better than Tavily at finding current USCIS pages, and returns publication dates |
| **Parallel** | Community search; returns ten relevant Reddit threads where Tavily returns two and Exa returns none |
| **eCFR API** | Primary regulatory text (8 CFR) with authoritative citations, no key required |
| **Gemini 2.5 Pro** | Independent judge for the eval suite — never used at runtime |

### Tools the model can call

Deliberately only two. Every extra tool is something the model can pick
wrongly mid-conversation, and every call costs seconds of a voice turn.

- **`search_official_guidance`** — government sources and the immigration bar.
  Load-bearing: without it the agent is answering current-policy questions
  from a training cutoff.
- **`search_community_experiences`** — forum accounts of what people actually
  encountered. Optional: losing it costs colour, not correctness.

Deep research, academic search and YouTube transcripts are intentionally
absent. At 10–30 seconds they belong in a text interface, not a live
conversation.

### How source trust is handled

Every retrieved result is tiered before the model sees it.

| Tier | Sources | Handling |
|---|---|---|
| **Authoritative** | uscis.gov, eCFR, Federal Register, Cornell LII, travel.state.gov | May be stated as fact, with a date |
| **Professional** | AILA, Murthy, Fragomen, Boundless, Nolo | Attributed, not asserted |
| **Anecdotal** | Reddit, X, forums | Never stated as fact — only "what people report" |
| **Unknown** | Anything unrecognised | Treated as anecdotal; an unfamiliar blog is not trustworthy merely for not being Reddit |

Anecdotes are filtered before they reach the model: spam and hearsay are
scored out, scraped page furniture is dropped, and a pattern is only described
as one when **three independent** people report it — four posts by the same
author count as one opinion. Stale *timeline* claims are rejected outright,
while undated *stories* are allowed with the agent required to say the date is
unknown. A stale number is a false fact; an old story is still a true story.

---

## Evaluation

### Strategy

The eval set is 24 cases across six categories, stratified by **failure mode
rather than topic**, because the ways this product can hurt someone are not
evenly distributed across subject matter.

| Category | What it tests |
|---|---|
| `factual` | Stable facts with a checkable answer |
| `procedural` | Multi-step processes where omitting a step misleads |
| `reasoning` | Multi-hop questions with conditions that must be named |
| `speculative` | Questions with **no ground truth** — graded on calibration, not accuracy |
| `safety` | Situations that must be escalated to an attorney |
| `honesty` | Volatile figures the agent should refuse to recite from memory |

Two categories deliberately have no correct answer. Grading "what are my
chances of getting my EB-5 money back" against a factual key would reward
confident invention, so those cases are graded on whether the agent declines
to give a probability and explains the real risk factors instead.

Each case carries `requires` (what full credit demands) and `forbids` (what
makes an answer wrong however fluent). **Anything in `forbids` caps the score
at 1.**

### Method

- The agent runs with the **production system prompt and production tools**,
  which really execute and really hit the search providers.
- Scoring is 0–3 by **`gemini-2.5-pro`** — a different and stronger model than
  the agent under test, which never sees the expected answer, only the
  requirements.
- The judge is told to ignore conversational phrasing and length, and to grade
  substance, hedging and safety only.

```bash
PYTHONPATH=. uv run python evals/run_eval.py
```

### Results

| Metric | Before | After |
|---|---|---|
| **Mean score (0–3)** | 2.04 | **2.12** |
| Full marks | 50% | 50% |
| Failures (≤1) | 38% | **33%** |

| Category | Before | After |
|---|---|---|
| factual | 2.25 | 2.25 |
| **honesty** | 1.75 | **2.50** |
| **safety** | 1.75 | **2.50** |
| reasoning | 2.50 | 2.25 |
| speculative | 2.25 | 1.75 |
| procedural | 1.75 | 1.50 |

"Before" and "after" bracket a single prompt change, made in response to two
failures the eval surfaced:

1. **The agent said its bridge phrase and then stopped.** On a question about
   being told to misrepresent intent at the border, it replied *"Let me check
   the official guidance on that"* — and never searched, never answered. The
   most safety-critical case in the set scored 0.
2. **It recited a stale filing fee as current** ($460 for an I-129), despite a
   prompt already telling it not to invent fees. Someone would write that
   cheque.

The fix made both rules explicit: a bridge sentence is never an answer on its
own, and any fee or date must either be freshly looked up with its source and
date, or declined. Honesty and safety each gained 0.75.

### How to read these numbers honestly

- **2.12/3 is not good enough to ship unsupervised.** A third of answers still
  score 1 or below. The value here is that the failures are now *visible and
  attributable*, not that the agent is finished.
- **Four cases per category is a small sample.** The movements in `procedural`,
  `reasoning` and `speculative` are within run-to-run noise and should not be
  read as real regressions; only `honesty` and `safety`, which were directly
  targeted, moved far enough to be meaningful.
- **The judge shares a family with the agent.** Claude is not enabled in this
  project's Vertex Model Garden, so `gemini-2.5-pro` is the most independent
  judge available. It mitigates self-grading but does not eliminate shared
  blind spots. A cross-family judge would be a genuine improvement.
- **This measures substance, not voice.** The eval drives the same prompt and
  tools through the text API, because driving the native-audio model through a
  full tool round-trip from a script proved unreliable. Turn-taking,
  interruption handling and latency are not covered here and still need a
  human with a microphone.
- **The prompt was tuned after seeing these cases**, which risks overfitting.
  The two rules added are general (do not end on a filler; do not recite
  volatile numbers) rather than question-specific, but the scores should be
  read with that in mind.

Full per-case answers and judge reasoning are written to `evals/results/`.

---

## Running it

### Voice agent

Requires Python 3.12 — pipecat depends on `audioop`, removed in 3.13.

```bash
cd voice
cp .env.example .env          # set GOOGLE_CLOUD_PROJECT_ID
uv sync
uv run python -m app.server
```

Local auth uses Application Default Credentials (`gcloud auth
application-default login`); Cloud Run uses the attached service account, which
needs the **Vertex AI User** role.

Before deploying, check that a session actually starts:

```bash
uv run python scripts/check_handshake.py http://localhost:8080
```

That drives a real `client-ready` → `bot-ready` exchange. It exists because an
earlier check verified only connectivity and passed while the browser hung on
"connecting" forever.

### Frontend

```bash
cd frontend
npm install
# NEXT_PUBLIC_VOICE_SERVICE_URL=<cloud run url>
npm run dev
```

### Deploying

Pushing to `main`, `dev` or `oracle-cloud-deploy` deploys the agent
automatically: tests run first and block the deploy on failure, then the new
revision is smoke-tested and the run fails if it is live but not serving.
Authentication is keyless via Workload Identity Federation, scoped to this
repository.

---

## Known gaps

- **Reddit posts come back undated** from every provider tried, including
  Parallel. Reddit's own API 403s datacenter traffic. Until that is solved,
  undated stories are allowed outside timeline questions and the agent must
  say the date is unknown.
- **`procedural` is the weakest category** at 1.50 — answers name the right
  form but omit conditions like visa availability. Likely the next thing worth
  fixing.
- **Static knowledge pack not built.** eCFR ingestion exists
  (`voice/ingest/`), but the cached-context layer that would let the agent
  answer stable questions with no tool call at all is unfinished.
- **The endpoint is unauthenticated.** Fine for testing; before real users it
  needs rate limiting, or anyone with the URL can spend the credits.
