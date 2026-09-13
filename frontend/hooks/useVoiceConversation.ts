"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { useSpeechRecognition } from "./useSpeechRecognition";
import { useSpeechSynthesis } from "./useSpeechSynthesis";

export type VoicePhase = "listening" | "thinking" | "speaking" | "muted";

interface UseVoiceConversationOptions {
  onSend: (text: string) => Promise<string | undefined>;
}

// Drives the hands-free conversation loop: listen -> send -> speak -> listen
// again, entirely through the browser's native speech APIs. Only ever used
// while the voice overlay is mounted, so mount = start, unmount = stop.
export function useVoiceConversation({ onSend }: UseVoiceConversationOptions) {
  const [phase, setPhase] = useState<VoicePhase>("listening");
  const [liveTranscript, setLiveTranscript] = useState("");

  const phaseRef = useRef(phase);
  useEffect(() => {
    phaseRef.current = phase;
  }, [phase]);

  // Broken by a ref because `useSpeechRecognition` needs a stable callback
  // up front, but that callback needs to call `submit`, which itself needs
  // `stopListening` from the same hook call.
  const submitRef = useRef<(text: string) => void>(() => {});

  const handleResult = useCallback((transcript: string, isFinal: boolean) => {
    if (phaseRef.current !== "listening") return;
    setLiveTranscript(transcript);
    if (isFinal && transcript.trim()) {
      submitRef.current(transcript.trim());
    }
  }, []);

  const {
    start: startListening,
    stop: stopListening,
    isListening,
    isSupported: sttSupported,
  } = useSpeechRecognition(handleResult);
  const { speak, stop: stopSpeaking, isSupported: ttsSupported } = useSpeechSynthesis();

  const submit = useCallback(
    (text: string) => {
      stopListening();
      setLiveTranscript("");
      setPhase("thinking");
      void (async () => {
        const reply = await onSend(text);
        if (reply && ttsSupported) {
          setPhase("speaking");
          speak(reply, () => setPhase("listening"));
        } else {
          setPhase("listening");
        }
      })();
    },
    [onSend, speak, ttsSupported, stopListening]
  );

  useEffect(() => {
    submitRef.current = submit;
  }, [submit]);

  // Start listening as soon as the overlay mounts, and stop everything when
  // it unmounts so nothing keeps talking after it's dismissed.
  useEffect(() => {
    startListening();
    return () => {
      stopListening();
      stopSpeaking();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps -- mount/unmount only, not on every listener identity change
  }, []);

  // Keep the mic alive: browsers stop even `continuous` recognition after a
  // silence timeout, so restart it whenever we should be listening but aren't.
  useEffect(() => {
    if (phase !== "listening" || isListening) return;
    const id = setTimeout(() => startListening(), 250);
    return () => clearTimeout(id);
  }, [phase, isListening, startListening]);

  const toggleMute = useCallback(() => {
    if (phase === "muted") {
      setPhase("listening");
    } else {
      stopListening();
      stopSpeaking();
      setPhase("muted");
    }
  }, [phase, stopListening, stopSpeaking]);

  return { phase, liveTranscript, toggleMute, sendText: submit, sttSupported, ttsSupported };
}
