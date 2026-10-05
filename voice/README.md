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
| `LLM_MODEL` | primary model (e.g. `deepseek/deepseek-chat`) |
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
  --set-env-vars LLM_API_KEY=<key>,LLM_MODEL=deepseek/deepseek-chat,TAVILY_API_KEY=<key>
```

Before going public, set `ALLOWED_ORIGINS` to your Vercel domain instead of
`*`.

## Layout

```
app/llm.py               OpenAI-compatible client + streaming + tool schemas
app/text_agent.py        the agent loop (model -> tools -> answer)
app/tools/               search providers and the tool registry
app/knowledge/           local 8 CFR pack (built by scripts/build_knowledge_pack.py)
app/prompts/             one file per task, composed into one system instruction
evals/                   judge + harness + task cases + question generator
```
