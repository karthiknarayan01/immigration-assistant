"use client";

import { useEffect, useRef } from "react";
import MessageBubble from "@/components/MessageBubble";
import { Message } from "@/lib/types";

const SUGGESTIONS = [
  "How long is H-1B premium processing?",
  "Can I switch from F-1 OPT to H-1B without a gap in status?",
  "What are my chances of getting a green card faster through EB-1?",
  "What happens to my status if my H-1B petition is denied?",
];

interface MessageListProps {
  messages: Message[];
  isLoaded: boolean;
  isSending: boolean;
  onSuggestion: (text: string) => void;
}

export default function MessageList({ messages, isLoaded, isSending, onSuggestion }: MessageListProps) {
  const bottomRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages]);

  if (isLoaded && messages.length === 0) {
    return (
      <div className="flex flex-1 flex-col items-center justify-center px-4 text-center">
        <h2 className="text-2xl font-medium text-foreground">How can I help with your immigration question?</h2>
        <p className="mt-2 max-w-md text-sm text-muted">
          Ask about H-1B, F-1, B-1/B-2, L-1, or employment-based green cards. Answers draw on official sources
          and community-reported outcomes.
        </p>
        <div className="mt-8 grid w-full max-w-2xl grid-cols-1 gap-2 sm:grid-cols-2">
          {SUGGESTIONS.map((s) => (
            <button
              key={s}
              type="button"
              onClick={() => onSuggestion(s)}
              className="rounded-2xl border border-border bg-surface px-4 py-3 text-left text-sm text-foreground/90 transition-colors hover:border-border-strong hover:bg-surface-hover"
            >
              {s}
            </button>
          ))}
        </div>
      </div>
    );
  }

  return (
    <div className="flex-1 overflow-y-auto px-4 py-6 sm:px-8">
      <div className="flex flex-col gap-6">
        {messages.map((message, i) => (
          <MessageBubble
            key={message.id}
            message={message}
            isStreaming={isSending && i === messages.length - 1 && message.role === "assistant"}
          />
        ))}
      </div>
      <div ref={bottomRef} />
    </div>
  );
}
