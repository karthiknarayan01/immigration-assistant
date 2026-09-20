"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { PipecatClient } from "@pipecat-ai/client-js";
import { ProtobufFrameSerializer, WebSocketTransport } from "@pipecat-ai/websocket-transport";
import { Role } from "@/lib/types";

export type VoicePhase = "connecting" | "listening" | "thinking" | "speaking" | "muted" | "error";

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

  const clientRef = useRef<PipecatClient | null>(null);
  const mutedRef = useRef(false);

  const onTranscriptRef = useRef(onTranscript);
  useEffect(() => {
    onTranscriptRef.current = onTranscript;
  }, [onTranscript]);

  useEffect(() => {
    if (!serviceUrl) return;

    const client = new PipecatClient({
      // WebSocket, not WebRTC: Cloud Run accepts no UDP, so a WebRTC media
      // path can never establish there.
      transport: new WebSocketTransport({ serializer: new ProtobufFrameSerializer() }),
      enableMic: true,
      enableCam: false,
      callbacks: {
        onConnected: () => setPhase("listening"),
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
        onDisconnected: () => setLiveTranscript(""),
        onError: (message) => {
          setPhase("error");
          setError(typeof message === "string" ? message : "Voice connection failed.");
        },
      },
    });

    clientRef.current = client;
    // https:// -> wss:// so the page's scheme decides the socket's.
    const wsUrl = `${serviceUrl.replace(/\/$/, "").replace(/^http/, "ws")}/ws`;
    client.connect({ wsUrl }).catch((e: unknown) => {
      setPhase("error");
      setError(e instanceof Error ? e.message : "Could not reach the voice service.");
    });

    return () => {
      // Tearing the connection down is what ends the session server-side, and
      // with it the in-memory conversation state.
      void client.disconnect();
      clientRef.current = null;
    };
  }, [serviceUrl]);

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

  return { phase, liveTranscript, error, toggleMute, sendText };
}
