"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { PipecatClient } from "@pipecat-ai/client-js";
import { ProtobufFrameSerializer, WebSocketTransport } from "@pipecat-ai/websocket-transport";
import { Role } from "@/lib/types";

export type VoicePhase =
  | "connecting"
  | "listening"
  | "thinking"
  | "speaking"
  | "muted"
  | "reconnecting"
  | "error";

/**
 * How long to wait before calling a connection dead. The transport retries
 * on its own, so a drop is not immediately fatal — but without a deadline a
 * connection that never succeeds is indistinguishable from a slow one, which
 * is how a broken server looked like a permanently "connecting" client.
 */
const CONNECT_TIMEOUT_MS = 20_000;

interface UseVoiceConversationOptions {
  // Voice turns are mirrored into the text transcript so the conversation is
  // still there after the user exits voice mode.
  onTranscript: (role: Role, text: string) => void;
}

export function useVoiceConversation({ onTranscript }: UseVoiceConversationOptions) {
  const [serviceUrl] = useState(() => process.env.NEXT_PUBLIC_VOICE_SERVICE_URL ?? "");
  const [phase, setPhase] = useState<VoicePhase>(() => (serviceUrl ? "connecting" : "error"));
  const [liveTranscript, setLiveTranscript] = useState("");
  const [error, setError] = useState(() =>
    serviceUrl ? "" : "Voice service is not configured. Set NEXT_PUBLIC_VOICE_SERVICE_URL."
  );
  // Bumping this tears down the client and builds a fresh one.
  const [attempt, setAttempt] = useState(0);

  const clientRef = useRef<PipecatClient | null>(null);
  const mutedRef = useRef(false);

  // Some failures (out of credit, misconfiguration) will not fix themselves.
  // Held as state because the UI decides whether to offer a retry button, and
  // mirrored into a ref because the transport callbacks need a fresh value.
  const [isTerminal, setIsTerminal] = useState(false);
  const terminalRef = useRef(false);
  useEffect(() => {
    terminalRef.current = isTerminal;
  }, [isTerminal]);

  const phaseRef = useRef(phase);
  useEffect(() => {
    phaseRef.current = phase;
  }, [phase]);

  const onTranscriptRef = useRef(onTranscript);
  useEffect(() => {
    onTranscriptRef.current = onTranscript;
  }, [onTranscript]);

  const fail = useCallback((message: string, { terminal = false } = {}) => {
    terminalRef.current = terminal;
    setIsTerminal(terminal);
    setPhase("error");
    setError(message);
  }, []);

  useEffect(() => {
    if (!serviceUrl) return;

    const client = new PipecatClient({
      // WebSocket, not WebRTC: Cloud Run accepts no UDP, so a WebRTC media
      // path can never establish there.
      transport: new WebSocketTransport({ serializer: new ProtobufFrameSerializer() }),
      enableMic: true,
      enableCam: false,
      callbacks: {
        onConnected: () => {
          terminalRef.current = false;
          setIsTerminal(false);
          setError("");
          setPhase("listening");
        },
        onUserStartedSpeaking: () => {
          if (!mutedRef.current) setPhase("listening");
        },
        // The agent is composing a reply between the user stopping and the
        // first audio frame coming back.
        onUserStoppedSpeaking: () => {
          if (!mutedRef.current) setPhase("thinking");
        },
        onBotStartedSpeaking: () => setPhase("speaking"),
        onBotStoppedSpeaking: () => setPhase(mutedRef.current ? "muted" : "listening"),
        onUserTranscript: (data) => {
          setLiveTranscript(data.text);
          if (data.final && data.text.trim()) {
            onTranscriptRef.current("user", data.text.trim());
            setLiveTranscript("");
          }
        },
        onBotTranscript: (data) => {
          if (data.text?.trim()) onTranscriptRef.current("assistant", data.text.trim());
        },
        onDisconnected: () => {
          setLiveTranscript("");
          // The transport reconnects by itself, so a drop mid-conversation is
          // shown as recoverable rather than fatal — unless we already know
          // the cause is something retrying cannot fix.
          if (!terminalRef.current && phaseRef.current !== "error") {
            setPhase("reconnecting");
          }
        },
        // Sent when the model itself failed (out of credit, for instance).
        // Nothing can generate speech at that point, so it has to surface here.
        onServerMessage: (data: unknown) => {
          const payload = data as
            | { type?: string; message?: string; retryable?: boolean }
            | null;
          if (payload?.type === "session-failure" && payload.message) {
            terminalRef.current = !payload.retryable;
            setIsTerminal(!payload.retryable);
            setPhase("error");
            setError(payload.message);
          }
        },
        onError: (message) => {
          setPhase("error");
          setError(typeof message === "string" ? message : "Something went wrong.");
        },
      },
    });

    clientRef.current = client;

    // https:// -> wss:// so the page's scheme decides the socket's.
    const wsUrl = `${serviceUrl.replace(/\/$/, "").replace(/^http/, "ws")}/ws`;
    client.connect({ wsUrl }).catch(() => {
      fail("Couldn't reach the assistant. Check your connection and try again.");
    });

    // Watchdog: if we never reach a working session, say so rather than
    // spinning forever.
    const watchdog = window.setTimeout(() => {
      const current = phaseRef.current;
      if (current === "connecting" || current === "reconnecting") {
        fail(
          current === "reconnecting"
            ? "Lost connection to the assistant and couldn't reconnect."
            : "Couldn't reach the assistant. Check your connection and try again."
        );
      }
    }, CONNECT_TIMEOUT_MS);

    return () => {
      window.clearTimeout(watchdog);
      // Tearing the connection down is what ends the session server-side, and
      // with it the in-memory conversation state.
      void client.disconnect();
      clientRef.current = null;
    };
  }, [serviceUrl, attempt, fail]);

  // The browser knows about network loss before the socket notices.
  useEffect(() => {
    const handleOffline = () => fail("You appear to be offline. Check your connection and try again.");
    window.addEventListener("offline", handleOffline);
    return () => window.removeEventListener("offline", handleOffline);
  }, [fail]);

  const retry = useCallback(() => {
    terminalRef.current = false;
    setIsTerminal(false);
    setError("");
    setPhase("connecting");
    setAttempt((n) => n + 1);
  }, []);

  const toggleMute = useCallback(() => {
    const client = clientRef.current;
    if (!client) return;
    const nextMuted = !mutedRef.current;
    mutedRef.current = nextMuted;
    client.enableMic(!nextMuted);
    setPhase(nextMuted ? "muted" : "listening");
  }, []);

  const sendText = useCallback((text: string) => {
    const client = clientRef.current;
    if (!client || !text.trim()) return;
    onTranscriptRef.current("user", text.trim());
    setPhase("thinking");
    void client.sendText(text.trim());
  }, []);

  return {
    phase,
    liveTranscript,
    error,
    // Retrying a billing failure just reproduces it, so the UI hides the
    // button rather than inviting a pointless loop.
    canRetry: phase === "error" && !isTerminal,
    retry,
    toggleMute,
    sendText,
  };
}
