# Immigration Assistant

Ask US immigration questions out loud and get an answer back in seconds — H-1B,
F-1, B-1/B-2, L-1 and employment-based green cards.

**[Try it](https://immigration-assistant-karthik-s-projects-56f2.vercel.app)**

> Not legal advice. The assistant hands you to an attorney for denials, removal
> proceedings, unlawful presence, criminal history, and anything involving
> misrepresentation.

---

## See it work

<!-- Replace this block with the demo video.
     Drag the .mp4 into any GitHub issue comment, copy the generated
     https://github.com/user-attachments/... URL, and paste it on its own
     line here. GitHub renders it as an inline player.
     Suggested take, about 40 seconds:
       1. Open the app, press the mic
       2. Ask "I'm on H-1B and I just got laid off, how long do I have?"
       3. Let the status line and the spoken "let me check" play
       4. Show the 60-day answer and the attorney hand-off -->

_Demo video goes here._

## What it does

1. You ask a question by voice or by typing.
2. It searches official sources — USCIS, the Federal Register, the eCFR.
3. It separately checks what people actually report going through the same
   thing, on forums like Reddit.
4. It answers, and tells you which parts are official rule and which parts are
   other people's experience.
5. If your situation is risky, it stops and tells you to see an attorney.

Step 4 is the part a search box won't do for you. What the rule says and what
happens in practice often differ, and knowing that gap exists is usually the
most useful thing you can learn at midnight.

## What it won't do

- Tell you what *will* happen in your case. It isn't a lawyer.
- Treat a forum post as fact. Anecdotes are labelled as anecdotes.
- Guess at current processing times or fees when it can't check them.
- Store your conversation. Nothing is saved on the server; the transcript
  lives only in your own browser.

## How much you can trust an answer

Every source is ranked before the assistant is allowed to use it.

| Source | How it's used |
|---|---|
| USCIS, eCFR, Federal Register | Stated as fact, with a date |
| Immigration law firms, AILA | Attributed — "according to…" |
| Reddit, forums, social media | Only ever "what people report" |
| Anything unrecognised | Treated as anecdotal |

A pattern from forums is only mentioned when **three different people** report
it. Four posts by the same person is one opinion. Out-of-date numbers are
thrown away entirely; out-of-date stories are kept but flagged as old.

### Numbers get looked up, never recalled

Any figure — a grace period, a deadline, a filing fee, a day count — is
searched for before it's said. The assistant is not allowed to answer those
from memory, because a remembered number is often a year or two out of date
and sounds exactly as confident as a correct one.

If it searches and can't find the exact figure, it tells you that and points
you at the official page, rather than filling the gap with its best guess.
That makes some answers less satisfying. It also means a number you're given
is one it actually found.

---

# Run your own

The whole thing is one repo — voice backend and web frontend. Fork it, bring
your own accounts, and it's yours.

## 1. What you'll need

| Service | What for | Cost |
|---|---|---|
| **Google Cloud** | The model (Gemini via Vertex AI) | Pay per minute of conversation; new accounts get free credit |
| **Tavily** | Official-source search | Free tier, then ~$0.008/search |
| **Exa** | Better semantic search | Free tier, then ~$7 per 1,000 |
| **Parallel** | Forum and Reddit search | Free tier available |
| **Vercel** | Hosting the web app | Free tier is enough |

Only Google Cloud and **one** search key are required to get going. Everything
else degrades gracefully — with no search keys at all, the assistant still
answers but tells you it couldn't check a live source.

> Use **Vertex AI**, not an AI Studio key. Google Cloud credits apply to Vertex
> AI; an AI Studio key bills your card instead.

## 2. Fork and clone

```bash
git clone https://github.com/<your-username>/immigration-assistant
cd immigration-assistant
```

## 3. Set up Google Cloud

1. Create a project at [console.cloud.google.com](https://console.cloud.google.com).
2. Note the **project ID** (not the display name).
3. Enable the Vertex AI API:
   ```bash
   gcloud services enable aiplatform.googleapis.com
   ```
4. Create a service account with the **Vertex AI User** role, and download its
   JSON key.

## 4. Get your search keys

Sign up and copy the API key from each:

- [tavily.com](https://tavily.com)
- [exa.ai](https://exa.ai)
- [parallel.ai](https://parallel.ai)

## 5. Run the backend

Needs **Python 3.12** — not 3.13, which removed a module the audio pipeline
depends on.

```bash
cd voice
cp .env.example .env
```

Fill in `.env`:

```bash
GOOGLE_CLOUD_PROJECT_ID=your-project-id
GOOGLE_VERTEX_CREDENTIALS_PATH=/path/to/service-account.json
TAVILY_API_KEY=your-key
EXA_API_KEY=your-key
PARALLEL_API_KEY=your-key
```

Then:

```bash
uv sync
uv run python -m app.server
```

Check it's alive:

```bash
curl http://localhost:8080/health
uv run python scripts/check_handshake.py http://localhost:8080
```

Then check your accounts and keys actually work:

```bash
PYTHONPATH=. uv run python scripts/check_services.py
```

It tells you which services are reachable and, when one isn't, whether that's
a missing key, an empty balance, or just the network. Worth running before you
conclude the assistant is giving bad answers — a search provider that's out of
credit looks exactly like an assistant that's got worse.

## 6. Run the frontend

```bash
cd frontend
cp .env.example .env.local
```

Set both to your backend:

```bash
NEXT_PUBLIC_VOICE_SERVICE_URL=http://localhost:8080
VOICE_SERVICE_URL=http://localhost:8080
```

Then:

```bash
npm install
npm run dev
```

Open [localhost:3000](http://localhost:3000) and press the mic.

## 7. Generate the spoken filler clips

The assistant speaks a short phrase while it searches, so you aren't left in
silence. Render those once, in your own chosen voice:

```bash
cd voice
PYTHONPATH=. uv run python scripts/generate_fillers.py
```

## 8. Deploy it

**Backend → Cloud Run:**

```bash
cd voice
gcloud run deploy immigration-voice \
  --source . \
  --region us-east4 \
  --allow-unauthenticated \
  --timeout 3600 \
  --session-affinity \
  --set-env-vars "GOOGLE_CLOUD_PROJECT_ID=your-project-id,TAVILY_API_KEY=...,EXA_API_KEY=...,PARALLEL_API_KEY=..."
```

Leave the credentials variables blank in production — Cloud Run uses its own
attached service account.

**Frontend → Vercel:**

1. Import your fork at [vercel.com/new](https://vercel.com/new).
2. Set the root directory to `frontend`.
3. Add `NEXT_PUBLIC_VOICE_SERVICE_URL` and `VOICE_SERVICE_URL`, both set to
   your Cloud Run URL.
4. Deploy.

Finally, lock the backend to your own domain by setting `ALLOWED_ORIGINS` to
your Vercel URL instead of `*`.

## 9. Before you let anyone else use it

The backend is open to whoever has the URL, and every question spends your
credit. Put a rate limit in front of it, or keep the URL private.

---

## Running the tests

```bash
cd voice
uv run pytest tests/
```

## Checking answer quality

Answers are graded 0–3 by a second, stronger model against 49 questions — many
of them real questions taken from immigration forums — on correctness,
completeness, sourcing, confidence, safety and usefulness.

```bash
cd voice
PYTHONPATH=. uv run python evals/run_eval.py
```

Safety is a gate, not an average: if a question involves denial, removal or
misrepresentation and the answer doesn't send you to an attorney, the case
fails no matter how good the rest of it was.

---

Engineering notes — architecture, measurements and the reasoning behind the
design — are in [docs/engineering.md](docs/engineering.md).
