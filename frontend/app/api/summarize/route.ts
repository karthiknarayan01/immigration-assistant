interface SummarizeRequestBody {
  existingSummary: string;
  messages: { role: string; content: string }[];
}

// TODO: replace with a real LLM summarization call once the backend model
// is chosen — this naive concatenation only exists so the rolling-summary
// plumbing (frontend trigger, storage, context assembly) can be tested end
// to end before that model is wired up.
export async function POST(req: Request) {
  const { existingSummary, messages } = (await req.json()) as SummarizeRequestBody;

  const folded = messages.map((m) => `${m.role}: ${m.content}`).join("\n");
  const combined = [existingSummary, folded].filter(Boolean).join("\n");
  const summary = combined.length > 1200 ? combined.slice(combined.length - 1200) : combined;

  return Response.json({ summary });
}
