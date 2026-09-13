import { Message } from "@/lib/types";

export default function MessageBubble({ message, isStreaming }: { message: Message; isStreaming: boolean }) {
  const isUser = message.role === "user";
  const isEmpty = !isUser && message.content.length === 0;

  return (
    <div className={`animate-message-in flex w-full ${isUser ? "justify-end" : "justify-start"}`}>
      <div
        className={`max-w-[min(75ch,100%)] whitespace-pre-wrap rounded-2xl px-4 py-2.5 text-[15px] leading-relaxed ${
          isUser ? "bg-accent text-accent-foreground" : "border border-border bg-surface text-foreground"
        } ${isStreaming && !isEmpty ? "streaming-caret" : ""}`}
      >
        {isEmpty ? (
          <span className="flex gap-1 py-1">
            <span className="h-1.5 w-1.5 animate-bounce rounded-full bg-muted [animation-delay:-0.3s]" />
            <span className="h-1.5 w-1.5 animate-bounce rounded-full bg-muted [animation-delay:-0.15s]" />
            <span className="h-1.5 w-1.5 animate-bounce rounded-full bg-muted" />
          </span>
        ) : (
          message.content
        )}
      </div>
    </div>
  );
}
