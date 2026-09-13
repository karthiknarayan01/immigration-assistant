"use client";

import { useCallback, useState } from "react";
import Composer from "@/components/Composer";
import Header from "@/components/Header";
import MessageList from "@/components/MessageList";
import VoiceOverlay from "@/components/VoiceOverlay";
import { useChatSession } from "@/hooks/useChatSession";

export default function ChatShell() {
  const { messages, sendMessage, clearSession, isSending, isLoaded } = useChatSession();
  const [voiceOpen, setVoiceOpen] = useState(false);

  const handleSend = useCallback(
    (text: string) => {
      void sendMessage(text);
    },
    [sendMessage]
  );

  return (
    <div className="relative flex h-dvh flex-col overflow-hidden bg-background text-foreground">
      <Header onNewChat={clearSession} />
      <MessageList messages={messages} isLoaded={isLoaded} isSending={isSending} onSuggestion={handleSend} />
      <Composer onSend={handleSend} onOpenVoice={() => setVoiceOpen(true)} disabled={isSending} />
      {voiceOpen && <VoiceOverlay onSend={sendMessage} onClose={() => setVoiceOpen(false)} />}
    </div>
  );
}
