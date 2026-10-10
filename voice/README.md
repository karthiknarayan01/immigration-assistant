# Assistant backend

Text-first US immigration assistant. The Next.js frontend in `../frontend`
converts speech to text in the browser and posts the text here; answers stream
back over server-sent events. There is no audio pipeline, no WebSocket, and no
speech-to-speech model — the model is any OpenAI-compatible endpoint named in
configuration.

## Model routing

The agent talks to whatever `LLM_*` variables name, with an automatic fallback
model when the primary fails transiently:

| Variable | Meaning |
|---|---|
| `LLM_BASE_URL` | OpenAI-compatible endpoint (default: OpenRouter) |
| `LLM_API_KEY` | API key for that endpoint |
| `LLM_MODEL` | primary model (e.g. `google/gemini-2.5-flash`) |
| `LLM_FALLBACK_MODEL` | tried when the primary fails transiently |
| `REASONER_MODEL` | optional reasoning model for strategy questions |
| `JUDGE_MODEL` | judge model used by the evals |

## Local development

```bash
cp .env.example .env     # set LLM_API_KEY and at least one search key
uv sync
uv run python -m app.server
```

Check it came up:

```bash
curl localhost:8080/health
PYTHONPATH=. uv run python scripts/check_services.py
```

## Deploy to Cloud Run

```bash
gcloud run deploy immigration-voice \
  --source . \
  --region us-east4 \
  --timeout 3600 \
  --allow-unauthenticated \
  --set-env-vars LLM_API_KEY=<key>,LLM_MODEL=google/gemini-2.5-flash,TAVILY_API_KEY=<key>
```

Before going public, set `ALLOWED_ORIGINS` to your Vercel domain instead of
`*`.

## The local corpus

`app/knowledge/` holds a pre-built pack of authoritative text, so a lookup is a
local query rather than a network call:

| Source | Chunks | Covers |
|---|---|---|
| 8 CFR | 679 | DHS rules: status, grace periods, work authorisation, adjustment |
| 22 CFR | 403 | State Department visa regulations |
| 20 CFR 655 | 605 | Labor certification (PERM) |
| 9 FAM | 1,667 | State Department consular guidance |
| USCIS Policy Manual | 2,255 | How USCIS applies the regulations |

Rebuild it offline (it fetches from eCFR, `fam.state.gov` and uscis.gov):

```bash
PYTHONPATH=. uv run python scripts/build_knowledge_pack.py
```

## Layout

```
app/llm.py               OpenAI-compatible client + streaming + tool schemas
app/text_agent.py        the agent loop (model -> tools -> answer)
app/tools/               search providers, Federal Register, tool registry
app/knowledge/           local corpus (BM25 over ~5,600 chunks)
ingest/                  offline fetchers: eCFR, 9 FAM, USCIS Policy Manual
app/prompts/             one file per task, composed into one system instruction
evals/taxonomy.py        the coverage grid
evals/                   judge + harness + task cases + question generator
```
