"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import * as db from "@/lib/db";
import { KEEP_RAW_MESSAGES, SUMMARY_TRIGGER_MESSAGES } from "@/lib/constants";
import { readServerSentEvents } from "@/lib/sse";
import { Message, Role } from "@/lib/types";

export function useChatSession() {
  const [messages, setMessages] = useState<Message[]>([]);
  const [summary, setSummary] = useState("");
  const [summarizedUpTo, setSummarizedUpTo] = useState(0);
  const [isLoaded, setIsLoaded] = useState(false);
  const [isSending, setIsSending] = useState(false);
  // What the agent is doing right now, in words meant for the person
  // waiting. Empty when there is nothing outstanding.
  const [statusLabel, setStatusLabel] = useState("");
  const summarizingRef = useRef(false);

  useEffect(() => {
    (async () => {
      const [storedMessages, meta] = await Promise.all([db.getMessages(), db.getMeta()]);
      setMessages(storedMessages);
      setSummary(meta?.summary ?? "");
      setSummarizedUpTo(meta?.summarizedUpTo ?? 0);
      setIsLoaded(true);
    })();
  }, []);

  // Runs in the background after a reply finishes — never blocks the next
  // query. Folds everything except the most recent messages into a rolling
  // summary so the context sent per-request stays small as the chat grows.
  const maybeSummarize = useCallback(
    async (allMessages: Message[], currentSummary: string, currentSummarizedUpTo: number) => {
      const unsummarizedCount = allMessages.length - currentSummarizedUpTo;
      if (unsummarizedCount < SUMMARY_TRIGGER_MESSAGES || summarizingRef.current) return;

      const cutoff = allMessages.length - KEEP_RAW_MESSAGES;
      const toFold = allMessages.slice(currentSummarizedUpTo, cutoff);
      if (toFold.length === 0) return;

      summarizingRef.current = true;
      try {
        const res = await fetch("/api/summarize", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            existingSummary: currentSummary,
            messages: toFold.map((m) => ({ role: m.role, content: m.content })),
          }),
        });
        if (!res.ok) return;
        const { summary: newSummary } = (await res.json()) as { summary: string };
        setSummary(newSummary);
        setSummarizedUpTo(cutoff);
        await db.setMeta({ summary: newSummary, summarizedUpTo: cutoff });
      } finally {
        summarizingRef.current = false;
      }
    },
    []
  );

  const sendMessage = useCallback(
    async (text: string) => {
      const trimmed = text.trim();
      if (!trimmed || isSending) return;

      const userMessage: Message = {
        id: crypto.randomUUID(),
        role: "user",
        content: trimmed,
        createdAt: Date.now(),
      };
      const priorMessages = messages;
      setIsSending(true);
      setMessages((prev) => [...prev, userMessage]);
      await db.addMessage(userMessage);

      const assistantId = crypto.randomUUID();
      setMessages((prev) => [...prev, { id: assistantId, role: "assistant", content: "", createdAt: Date.now() }]);

      let full = "";
      try {
        const recentMessages = priorMessages.slice(summarizedUpTo).map((m) => ({ role: m.role, content: m.content }));
        const res = await fetch("/api/chat", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ summary, recentMessages, newMessage: trimmed }),
        });
        if (!res.body) throw new Error("No response body");

        // Tokens and status updates share one ordered stream, so both are
        // read from the same reader rather than from two sources that could
        // show a status after the answer it describes.
        for await (const event of readServerSentEvents(res.body)) {
          if (event.name === "token") {
            full += (event.data as { text?: string }).text ?? "";
            setMessages((prev) =>
              prev.map((m) => (m.id === assistantId ? { ...m, content: full } : m))
            );
          } else if (event.name === "status") {
            const data = event.data as { state?: string; label?: string };
            setStatusLabel(data.state === "working" ? (data.label ?? "") : "");
          } else if (event.name === "error") {
            const data = event.data as { message?: string };
            // Replace rather than append: a partial answer followed by an
            // error reads as though the partial part was checked.
            full = data.message ?? "Something went wrong. Please try again.";
            setMessages((prev) =>
              prev.map((m) => (m.id === assistantId ? { ...m, content: full } : m))
            );
          }
        }
      } catch {
        full = "Something went wrong reaching the assistant. Please try again.";
        setMessages((prev) => prev.map((m) => (m.id === assistantId ? { ...m, content: full } : m)));
      } finally {
        setStatusLabel("");
        setIsSending(false);
      }

      const finalAssistant: Message = { id: assistantId, role: "assistant", content: full, createdAt: Date.now() };
      await db.addMessage(finalAssistant);

      const allMessages = [...priorMessages, userMessage, finalAssistant];
      void maybeSummarize(allMessages, summary, summarizedUpTo);

      return full;
    },
    [isSending, messages, summary, summarizedUpTo, maybeSummarize]
  );

  const clearSession = useCallback(async () => {
    await db.clearAll();
    setMessages([]);
    setSummary("");
    setSummarizedUpTo(0);
  }, []);

  // Voice turns already happened on the voice service, so they're recorded
  // straight into the transcript rather than sent anywhere.
  const appendMessage = useCallback(async (role: Role, content: string) => {
    const message: Message = { id: crypto.randomUUID(), role, content, createdAt: Date.now() };
    setMessages((prev) => [...prev, message]);
    await db.addMessage(message);
  }, []);

  return { messages, sendMessage, appendMessage, clearSession, isSending, isLoaded, statusLabel };
}
