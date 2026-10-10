# Immigration Assistant

Ask US immigration questions by typing — or by speaking, which your browser
converts to text — and get a sourced answer back. H-1B, F-1, B-1/B-2, L-1 and
employment-based green cards.

> Not legal advice. The assistant hands you to an attorney for denials, removal
> proceedings, unlawful presence, criminal history, and anything involving
> misrepresentation.

---

## What it does

The assistant has three jobs:

1. **Fact provider.** Answers factual and procedural questions about settled
   law from a local corpus — 8 CFR, 22 CFR (State Department visas), 20 CFR
   (Labor/PERM), the State Department's 9 FAM consular guidance, and the USCIS
   Policy Manual — and never states a number it has not looked up.
2. **Recent developments.** Finds and reports what has recently changed or been
   proposed — executive orders, proposed rules, policy memos, court decisions —
   and clearly separates what is in force from what is merely proposed or
   reported.
3. **Reasoning and strategy.** For scenario questions ("can I follow strategy A
   to get a green card fastest?"), it reasons through the rules and real-world
   outcomes, gives pros and cons, and a calibrated view of likelihood grounded
   in what it finds — not in speculation.

It also separates **what the law says** from **what actually happens in
practice**, and says which parts of an answer are official rule and which are
other people's reported experience.

## How you talk to it

The interface is text-first. The mic button uses the browser's built-in speech
recognition (the Web Speech API — free, no backend STT, on-device in Safari)
to convert speech to text, then sends that text to the backend. If your browser
doesn't support it, just type. Nothing is stored on the server; the transcript
lives only in your own browser.

## What it won't do

- Tell you what *will* happen in your case. It isn't a lawyer.
- Treat a forum post as fact. Anecdotes are labelled as anecdotes.
- Answer anything outside US immigration — it politely declines.
- Guess at current processing times or fees when it can't check them.

## What it knows

**A local corpus**, built offline and committed, so a lookup costs no network
call:

| Source | What it covers |
|---|---|
| **8 CFR** | DHS rules: status, grace periods, work authorisation, adjustment |
| **22 CFR** | State Department visa regulations: refusal grounds, issuance |
| **20 CFR** | Labor certification (PERM) and related employment rules |
| **9 FAM** | The State Department's consular manual — what actually happens at an embassy: interviews, 221(g), refusals |
| **USCIS Policy Manual** | How USCIS applies the regulations, in the agency's own words |

**Live tools**, for what changes and what people say:

| Tool | What it does |
|---|---|
| `search_official_guidance` | Current fees, processing times, the visa bulletin, USCIS policy |
| `search_federal_register` | Official rulemaking. States outright whether each document is **in force**, **proposed**, a notice, or an executive order |
| `search_recent_developments` | Press coverage and what immigration lawyers are saying |
| `search_community_experiences` | What applicants report in practice (forums and X), gated as anecdote |

## How much you can trust an answer

Every source is ranked before the assistant is allowed to use it.

| Source | How it's used |
|---|---|
| USCIS, eCFR, Federal Register | Stated as fact, with a date |
| Immigration law firms, AILA | Attributed — "according to…" |
| Reddit, forums, social media | Only ever "what people report" |
| Anything unrecognised | Treated as anecdotal |

A pattern from forums is only mentioned when **three different people** report
it. Out-of-date numbers are thrown away entirely.

---

# Choosing the models

The assistant is not one model doing one job. Answering a single question takes
**two to four model calls**, and those calls want different things — so using
one model for all of them is either wasteful or unsafe.

| The job | What it has to be good at | What it uses | Why |
|---|---|---|---|
| Deciding what to look up, then writing the answer | Following a long list of rules, calling the right tool, and **not** inventing things | A cheap, capable model — `deepseek/deepseek-chat` | This runs several times per question, so it dominates cost. It needs to be accurate, not brilliant. |
| Working through a scenario ("should I switch to EB-1?") | Weighing alternatives and reasoning step by step | A reasoning model — `deepseek/deepseek-r1`, set as `REASONER_MODEL` | Judgement questions benefit from a model that thinks before it answers. A lookup does not. |
| Grading the answers (evals only) | Being a stricter, better reader than the assistant | A stronger model — `anthropic/claude-sonnet-5.5` | A model cannot fairly mark its own homework. |

Two things follow. First, **any of them can be swapped with one environment
variable** — the app talks to any OpenAI-compatible endpoint, so `LLM_MODEL`,
`REASONER_MODEL` and `JUDGE_MODEL` are configuration, not code. Second, **the
reasoning model is only used when it helps**: a deliberately narrow rule sends
scenario and comparison questions to it, and leaves "how many days is the grace
period" on the cheap model. Paying for step-by-step thinking on a lookup is
waste, not rigour.

**An honest note on how these were picked.** The defaults were chosen on
reputation and price, not measurement — the cheapest capable model with a good
record at following instructions and calling tools. That is a hypothesis, not a
finding. The benchmark below is the experiment: it reports quality **per
dollar**, so the model choice can be settled with evidence rather than vibes.

---

# Run your own

One repo: the Python backend (`voice/`) and the Next.js frontend (`frontend/`).

## 1. What you'll need

| Service | What for | Cost |
|---|---|---|
| **OpenRouter** (or any OpenAI-compatible endpoint) | The model, swappable by env var | Pay per token; cheap open models are a fraction of a cent |
| **Tavily** | Official-source search | Free tier, then ~$0.008/search |
| **Exa** (optional) | Better semantic search | Free tier |
| **Parallel** (optional) | Forum and Reddit search | Free tier available |
| **xAI** (optional) | Searching X for early signal | Pay per call |
| **Vercel** | Hosting the web app | Free tier is enough |

Only the model key and **one** search key are required. With no search keys at
all, the assistant still answers but tells you it couldn't check a live source.

The model is **any OpenAI-compatible endpoint**, so you are not locked to one
provider. Recommended cheap setup:

| Job | Model |
|---|---|
| Fact + search | `deepseek/deepseek-chat` |
| Reasoning / strategy | `deepseek/deepseek-r1` (set `REASONER_MODEL`) |
| Judge (evals) | `anthropic/claude-sonnet-5.5` (or another strong model) |

## 2. Run the backend

```bash
cd voice
cp .env.example .env
```

Fill in `.env`:

```bash
LLM_API_KEY=sk-or-...
LLM_MODEL=deepseek/deepseek-chat
LLM_FALLBACK_MODEL=meta-llama/llama-3.3-70b-instruct
TAVILY_API_KEY=...
```

Then:

```bash
uv sync
uv run python -m app.server
```

Check it's alive:

```bash
curl http://localhost:8080/health
PYTHONPATH=. uv run python scripts/check_services.py
```

The last one tells you which services are reachable and, when one isn't,
whether that's a missing key, an empty balance, or just the network.

## 3. Run the frontend

```bash
cd frontend
cp .env.example .env.local
```

Set the backend URL:

```bash
BACKEND_URL=http://localhost:8080
```

Then:

```bash
npm install
npm run dev
```

Open [localhost:3000](http://localhost:3000) and type or press the mic.

## 4. Deploy it

**Backend → Cloud Run** (see `.github/workflows/deploy-voice.yml`):

```bash
gcloud run deploy immigration-voice \
  --source voice \
  --region us-east4 \
  --timeout 3600 \
  --allow-unauthenticated \
  --set-env-vars "LLM_API_KEY=...,LLM_MODEL=...,TAVILY_API_KEY=..."
```

**Frontend → Vercel:** set the root directory to `frontend` and add
`BACKEND_URL` (your Cloud Run URL), then deploy.

Finally, lock the backend to your own domain by setting `ALLOWED_ORIGINS` to
your Vercel URL instead of `*`.

## 5. Before you let anyone else use it

The backend is open to whoever has the URL, and every question spends your
credit. Put a rate limit in front of it, or keep the URL private.

---

# Evals and benchmark

## What an "eval" is

An eval is a question with a marking scheme, marked by a second AI. Nothing
more than that.

We write down what a good answer **must** contain and what it must **never**
say. Then we put the question to the assistant exactly as a user would, and a
stronger model grades the reply from 0 to 3.

The marking scheme is the important part. Without one, a grader rewards fluent,
confident writing — so an answer that *sounds* authoritative while quietly
leaving out a condition scores as well as one that gets it right. Writing down
the requirements in advance is what makes the score mean something.

## The eight things we score

| Factor | The plain-English question |
|---|---|
| **Correctness** | Is it true? |
| **Completeness** | Are the conditions and exceptions there? |
| **Groundedness** | Does it say where it got it — a link, and a date? |
| **Calibration** | Is it suitably confident — sure when the rule is clear, careful when it isn't? |
| **Safety** | Does it send you to a lawyer when your situation is high-stakes? |
| **Actionability** | Do you know what to do next? |
| **Reasoning** | Does it show its working, and weigh the alternatives? |
| **Usefulness** | Would a real person find this helpful? |

## Three ideas that keep the number honest

**Safety is a gate, not an average.** Most scores are averaged together. Safety
is not. If a question involves a denial, a removal hearing or a criminal
record, and the answer does not tell you to speak to an attorney, the case
fails — however good the rest of it was. An articulate answer cannot average
its way past a missing referral.

**We check what the assistant *did*, not only what it said.** Some questions
declare the tool sequence they require. A question about a recent change must
check the Federal Register *and* the reporting, in that order. That is verified
against the calls actually made, because "did it really look this up" is a fact
about the run, not a matter of opinion.

**We report what we have not tested.** Every case is tagged by topic, question
type, difficulty and failure mode, and each run prints the gaps. "77 cases" is
a different claim from "the domain is covered", and the report says which one
is true.

## The sets

| Suite | What it covers |
|---|---|
| `official_answer` | Facts and procedure: grace periods, day counts, eligibility |
| `recent_developments` | What changed lately — and, crucially, whether it is in force or only proposed |
| `reasoning` | Scenarios and strategy: alternatives, pros and cons, likelihood |
| `adversarial` | False premises, outdated rules, prompt injection, "just tell me off the top of your head" |
| `multiturn` | Follow-ups and corrections, where facts from earlier in the conversation matter |
| `risk_escalation` · `scope` | High-stakes questions must escalate; non-immigration questions must be declined |

## Why 77 cases is not "comprehensive"

It is not — and no eval set is. Copying hundreds of pages of regulations into
hundreds of questions produces near-duplicates that measure the same handful of
behaviours, while the things that actually break go untested. Each case also
costs a real model call, so a set that is enormous simply stops being run.

What a good set does instead is sample a **grid** — question type × visa
category × difficulty × failure mode — so every cell that matters has cases.
The grid lives in [`voice/evals/taxonomy.py`](voice/evals/taxonomy.py), and the
run reports the empty cells. New cases come from real failures, and
[`evals/generate_evals.py`](voice/evals/generate_evals.py) drafts more from the
corpus — the smallest possible facts first, then questions that combine two to
four of them — for a person to review.

## The benchmark

<!-- BENCHMARK: filled from evals/results -->
_Run in progress — this table is produced by the eval run and pasted in
verbatim._

## A real answer

<!-- TRANSCRIPT: filled from the same run -->
_Added from the same run, unedited._

---

## Running it yourself

```bash
cd voice
PYTHONPATH=. uv run python evals/run_eval.py
```

With no keys the tools report themselves unavailable, so `--no-tools` is a
useful check that the assistant admits what it could not verify rather than
guessing.

- Draft more fact questions from the corpus (smallest-case + combos):
  `PYTHONPATH=. uv run python evals/generate_evals.py`
- Draft rubrics for harvested forum questions:
  `PYTHONPATH=. uv run python evals/build_rubrics.py`

Engineering notes are in [docs/engineering.md](docs/engineering.md).
