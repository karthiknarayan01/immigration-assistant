/**
 * Minimal server-sent-events reader for a streamed fetch response.
 *
 * `EventSource` can't be used here: it only issues GET requests, and a chat
 * turn has to POST the question and the transcript.
 *
 * Network chunks do not align with event boundaries — an event can be split
 * across two reads, and two events can arrive in one — so the buffer is only
 * consumed up to the last complete `\n\n`.
 */
export interface ServerSentEvent {
  name: string;
  data: unknown;
}

export async function* readServerSentEvents(
  body: ReadableStream<Uint8Array>
): AsyncGenerator<ServerSentEvent> {
  const reader = body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";

  try {
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });

      let boundary = buffer.indexOf("\n\n");
      while (boundary !== -1) {
        const block = buffer.slice(0, boundary);
        buffer = buffer.slice(boundary + 2);
        const event = parseBlock(block);
        if (event) yield event;
        boundary = buffer.indexOf("\n\n");
      }
    }
  } finally {
    reader.releaseLock();
  }
}

function parseBlock(block: string): ServerSentEvent | null {
  let name = "message";
  const dataLines: string[] = [];

  for (const line of block.split("\n")) {
    if (line.startsWith("event:")) name = line.slice(6).trim();
    else if (line.startsWith("data:")) dataLines.push(line.slice(5).trim());
  }
  if (!dataLines.length) return null;

  try {
    return { name, data: JSON.parse(dataLines.join("\n")) };
  } catch {
    // A malformed frame should drop that event, not kill the stream.
    return null;
  }
}
