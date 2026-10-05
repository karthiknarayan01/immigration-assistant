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
   law, reading the regulations (a local copy of 8 CFR) and current official
   policy, and never stating a number it has not looked up.
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
| Judge (evals) | `anthropic/claude-3.5-sonnet` (or another strong model) |

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

The agent is developed eval-first. Answers are graded 0–3 by a separate, strong
judge model across the three jobs, on correctness, completeness, groundedness
(links and dates), calibration, safety, actionability, reasoning and
usefulness. Cases that declare an expected tool sequence are also checked
deterministically — did the agent actually call the right tools in the right
order?

```bash
cd voice
PYTHONPATH=. uv run python evals/run_eval.py
```

- Generate new fact questions from the 8 CFR pack (smallest-case + combos):
  `PYTHONPATH=. uv run python evals/generate_evals.py`
- Draft rubrics for harvested forum questions:
  `PYTHONPATH=. uv run python evals/build_rubrics.py`

Safety is a gate, not an average: an answer to a high-stakes question that
doesn't send the user to an attorney fails no matter how good the rest of it
was. Out-of-scope questions must be declined.

Engineering notes are in [docs/engineering.md](docs/engineering.md).
