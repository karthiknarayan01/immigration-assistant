# Voice agent service

Real-time speech-to-speech agent for US immigration questions. Gemini Live on
Vertex AI, orchestrated with Pipecat, served over WebRTC.

The Next.js frontend in `../frontend` stays on Vercel and connects to this
service; Vercel can't host it, because voice needs a persistent connection.

## Why Vertex AI and not an AI Studio key

Google Cloud credits apply to **Vertex AI** but **not** to Gemini Developer
API (AI Studio) keys — an AI Studio key bills your linked card instead. This
service authenticates as a GCP service account for that reason. Passing an
`api_key` to the Vertex service raises an error by design.

## Local development

Requires Python 3.12 (pipecat depends on `audioop`, removed in 3.13).

```bash
cp .env.example .env     # then fill in GOOGLE_CLOUD_PROJECT_ID
uv sync
uv run python -m app.server
```

For local auth, either set `GOOGLE_VERTEX_CREDENTIALS_PATH` to a service
account JSON with the **Vertex AI User** role, or run `gcloud auth
application-default login` and leave both credential fields blank.

Check it came up:

```bash
curl localhost:8080/health
```

## Deploy to Cloud Run

```bash
gcloud run deploy immigration-voice \
  --source . \
  --region us-east4 \
  --timeout 3600 \
  --session-affinity \
  --set-env-vars GOOGLE_CLOUD_PROJECT_ID=<project>,GOOGLE_CLOUD_LOCATION=us-east4 \
  --allow-unauthenticated
```

`--timeout 3600` lets a voice session outlive the default 5-minute request
cap. `--session-affinity` keeps a session pinned to one instance, which
matters because conversation state is held in memory on that instance.

The attached service account needs the **Vertex AI User** role; credentials
are then picked up automatically, so no keys go in the environment.

Before going public, set `ALLOWED_ORIGINS` to your Vercel domain instead of
`*`.

## Turn detection

The agent should never talk over a user. Three layers handle that, tuned in
`app/bot.py` and `app/prompt.py`:

1. **Gemini server-side VAD** — `END_SENSITIVITY_LOW` plus a 1000ms silence
   window, so it waits longer before deciding the user finished.
   `START_SENSITIVITY_HIGH` keeps barge-in instant.
2. **Local Smart Turn v3** — an ONNX turn-detection model bundled with
   pipecat, running on-device (no API cost, no added latency).
3. **Semantic turn completion** — an LLM classifier with custom instructions
   in `TURN_COMPLETION_INSTRUCTIONS`, told to bias toward "incomplete" for
   hesitant or non-native speech, trailing conjunctions, and dangling numbers.

If testing shows the agent still interrupting, raise
`VAD_SILENCE_DURATION_MS` first.

## Not built yet

Tools (web/Reddit/X search), the cached static knowledge pack, and the
retrieval index are later phases. The agent currently answers from the
model's own knowledge and the prompt tells it to say so rather than claim it
is looking things up.
