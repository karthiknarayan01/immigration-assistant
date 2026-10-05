"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import * as db from "@/lib/db";
import { KEEP_RAW_MESSAGES, SUMMARY_TRIGGER_MESSAGES } from "@/lib/constants";
import { readServerSentEvents } from "@/lib/sse";
import { Message } from "@/lib/types";

const OFFLINE_MESSAGE = "You appear to be offline. Check your connection and try again.";
const SERVER_MESSAGE = "Something went wrong reaching the assistant. Please try again.";

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

  // The browser knows about network loss before any fetch fails, so it is
  // surfaced immediately rather than after a timeout. Mirrored in a ref so
  // in-flight sends can also see it.
  const [isOffline, setIsOffline] = useState(
    typeof navigator !== "undefined" ? !navigator.onLine : false
  );
  const offlineRef = useRef(isOffline);
  useEffect(() => {
    offlineRef.current = isOffline;
  }, [isOffline]);

  useEffect(() => {
    const goOnline = () => setIsOffline(false);
    const goOffline = () => setIsOffline(true);
    window.addEventListener("online", goOnline);
    window.addEventListener("offline", goOffline);
    return () => {
      window.removeEventListener("online", goOnline);
      window.removeEventListener("offline", goOffline);
    };
  }, []);

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

      const fail = (message: string) => {
        setMessages((prev) => prev.map((m) => (m.id === assistantId ? { ...m, content: message } : m)));
      };

      let full = "";
      try {
        if (offlineRef.current) {
          fail(OFFLINE_MESSAGE);
          return;
        }

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
            const data = event.data as { message?: string; kind?: string };
            // Replace rather than append: a partial answer followed by an
            // error reads as though the partial part was checked.
            full = data.message ?? SERVER_MESSAGE;
            fail(full);
          }
        }
      } catch {
        // A thrown fetch means the request never completed — almost always a
        // dropped connection rather than a server response.
        fail(offlineRef.current ? OFFLINE_MESSAGE : SERVER_MESSAGE);
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

  return { messages, sendMessage, clearSession, isSending, isLoaded, statusLabel, isOffline };
}
