"use client";

import { useCallback, useState } from "react";
import Composer from "@/components/Composer";
import Header from "@/components/Header";
import MessageList from "@/components/MessageList";
import VoiceOverlay from "@/components/VoiceOverlay";
import { useChatSession } from "@/hooks/useChatSession";
import { Role } from "@/lib/types";

export default function ChatShell() {
  const { messages, sendMessage, appendMessage, clearSession, isSending, isLoaded, statusLabel } =
    useChatSession();
  const [voiceOpen, setVoiceOpen] = useState(false);

  const handleSend = useCallback(
    (text: string) => {
      void sendMessage(text);
    },
    [sendMessage]
  );

  const handleVoiceTranscript = useCallback(
    (role: Role, text: string) => {
      void appendMessage(role, text);
    },
    [appendMessage]
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
      <Composer onSend={handleSend} onOpenVoice={() => setVoiceOpen(true)} disabled={isSending} />
      {voiceOpen && (
        <VoiceOverlay onTranscript={handleVoiceTranscript} onClose={() => setVoiceOpen(false)} />
      )}
    </div>
  );
}
