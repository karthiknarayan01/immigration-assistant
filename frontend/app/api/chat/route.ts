export const runtime = "nodejs";

interface ChatRequestBody {
  summary?: string;
  recentMessages?: { role: string; content: string }[];
  newMessage: string;
}

/**
 * Proxies chat to the voice service, which runs the same system prompt and
 * the same tools as the voice agent.
 *
 * A proxy rather than a direct browser call so the service URL and any future
 * credentials stay server-side, and so the page talks to a single origin.
 *
 * The upstream speaks server-sent events: answer tokens and "what am I doing"
 * status updates share one ordered stream. That ordering is the point — a
 * status must never arrive after the answer it describes — so the body is
 * passed through untouched rather than re-chunked here.
 */
export async function POST(req: Request) {
  const body = (await req.json()) as ChatRequestBody;

  const serviceUrl = process.env.VOICE_SERVICE_URL ?? process.env.NEXT_PUBLIC_VOICE_SERVICE_URL;
  if (!serviceUrl) {
    return sseError(
      "The assistant isn't configured yet. Set VOICE_SERVICE_URL and try again.",
      false
    );
  }

  let upstream: Response;
  try {
    upstream = await fetch(`${serviceUrl.replace(/\/$/, "")}/chat`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
  } catch {
    return sseError("Couldn't reach the assistant. Check your connection and try again.", true);
  }

  if (!upstream.ok || !upstream.body) {
    return sseError(
      upstream.status === 429
        ? "The assistant is over its usage limit right now."
        : "The assistant isn't responding right now. Please try again shortly.",
      upstream.status !== 429
    );
  }

  return new Response(upstream.body, {
    headers: {
      "Content-Type": "text/event-stream; charset=utf-8",
      "Cache-Control": "no-cache",
      // Without this some proxies buffer the whole stream and deliver the
      // answer in one lump at the end, which defeats streaming entirely.
      "X-Accel-Buffering": "no",
    },
  });
}

/** Report a failure in the upstream's shape, so the client has one path to handle. */
function sseError(message: string, retryable: boolean) {
  const payload =
    `event: error\ndata: ${JSON.stringify({ message, retryable })}\n\n` +
    `event: done\ndata: {}\n\n`;
  return new Response(payload, {
    headers: { "Content-Type": "text/event-stream; charset=utf-8", "Cache-Control": "no-cache" },
  });
}
