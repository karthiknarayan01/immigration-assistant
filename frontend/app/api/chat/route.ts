export const runtime = "nodejs";

interface ChatRequestBody {
  summary?: string;
  recentMessages?: { role: string; content: string }[];
  newMessage: string;
}

// TODO: replace this stub with a call to the real backend — e.g. the ADK
// agent exposed via `adk api_server`, or a thin FastAPI wrapper around
// agents/*.py — forwarding `summary` + `recentMessages` + `newMessage` as
// the model's context and streaming its output back the same way.
export async function POST(req: Request) {
  const { newMessage } = (await req.json()) as ChatRequestBody;

  const reply =
    `This is a placeholder response — the frontend isn't connected to the immigration agent yet. ` +
    `You asked: "${newMessage}". Once the backend API is wired up here, real answers grounded in ` +
    `USCIS sources will stream in instead.`;

  const encoder = new TextEncoder();
  const stream = new ReadableStream({
    async start(controller) {
      for (const word of reply.split(" ")) {
        controller.enqueue(encoder.encode(word + " "));
        await new Promise((resolve) => setTimeout(resolve, 25));
      }
      controller.close();
    },
  });

  return new Response(stream, { headers: { "Content-Type": "text/plain; charset=utf-8" } });
}
