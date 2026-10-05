"use client";

import { useCallback } from "react";
import Composer from "@/components/Composer";
import Header from "@/components/Header";
import MessageList from "@/components/MessageList";
import { useChatSession } from "@/hooks/useChatSession";

export default function ChatShell() {
  const { messages, sendMessage, clearSession, isSending, isLoaded, statusLabel } = useChatSession();

  const handleSend = useCallback(
    (text: string) => {
      void sendMessage(text);
    },
    [sendMessage]
  );

  return (
    <div className="relative flex h-dvh flex-col overflow-hidden bg-background text-foreground">
      <Header onNewChat={clearSession} />
      <MessageList
        messages={messages}
        isLoaded={isLoaded}
        isSending={isSending}
        statusLabel={statusLabel}
        onSuggestion={handleSend}
      />
      <Composer onSend={handleSend} disabled={isSending} />
    </div>
  );
}
