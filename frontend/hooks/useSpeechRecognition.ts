"use client";

import { useCallback, useEffect, useRef, useState } from "react";

interface UseSpeechRecognitionOptions {
  /** Called once the browser produces a final transcript. */
  onFinal: (text: string) => void;
}

interface SpeechRecognitionLike {
  lang: string;
  interimResults: boolean;
  continuous: boolean;
  maxAlternatives: number;
  onresult: ((event: SpeechRecognitionEventLike) => void) | null;
  onerror: ((event: SpeechRecognitionErrorLike) => void) | null;
  onend: (() => void) | null;
  start: () => void;
  stop: () => void;
  abort: () => void;
}

interface SpeechRecognitionEventLike {
  resultIndex: number;
  results: {
    length: number;
    [index: number]: { isFinal: boolean; 0: { transcript: string } };
  };
}

interface SpeechRecognitionErrorLike {
  error: string;
}

/**
 * Browser speech-to-text via the Web Speech API.
 *
 * In Chrome this uses Google's recognition built into the browser — free, no
 * API key, and the audio never leaves the browser to our backend. Safari
 * 16.4+/iOS runs it on-device. Browsers without support (or without
 * permission) fall back to typing, which is why `isSupported` is exposed.
 *
 * This is the replacement for the old Pipecat speech-to-speech pipeline: the
 * browser converts speech to text, and the text is sent to the chat backend.
 */
export function useSpeechRecognition({ onFinal }: UseSpeechRecognitionOptions) {
  const [isSupported] = useState<boolean>(() => {
    if (typeof window === "undefined") return false;
    const w = window as unknown as { SpeechRecognition?: unknown; webkitSpeechRecognition?: unknown };
    return Boolean(w.SpeechRecognition || w.webkitSpeechRecognition);
  });
  const [isListening, setIsListening] = useState(false);
  const [interim, setInterim] = useState("");
  const [error, setError] = useState("");

  const recognitionRef = useRef<SpeechRecognitionLike | null>(null);
  const onFinalRef = useRef(onFinal);
  useEffect(() => {
    onFinalRef.current = onFinal;
  }, [onFinal]);

  const stop = useCallback(() => {
    recognitionRef.current?.stop();
  }, []);

  const start = useCallback(() => {
    if (!isSupported) {
      setError("Speech input isn't supported in this browser — please type instead.");
      return;
    }
    const w = window as unknown as {
      SpeechRecognition?: new () => SpeechRecognitionLike;
      webkitSpeechRecognition?: new () => SpeechRecognitionLike;
    };
    const SR = w.SpeechRecognition ?? w.webkitSpeechRecognition;
    if (!SR) return;

    const recognition = new SR();
    recognition.lang = "en-US";
    recognition.interimResults = true;
    recognition.continuous = false;
    recognition.maxAlternatives = 1;

    recognition.onresult = (event: SpeechRecognitionEventLike) => {
      let interimText = "";
      let finalText = "";
      for (let i = event.resultIndex; i < event.results.length; i++) {
        const result = event.results[i];
        if (result.isFinal) finalText += result[0].transcript;
        else interimText += result[0].transcript;
      }
      if (finalText.trim()) {
        setInterim("");
        onFinalRef.current(finalText.trim());
      } else {
        setInterim(interimText);
      }
    };

    recognition.onerror = (event: SpeechRecognitionErrorLike) => {
      if (event.error === "not-allowed") setError("Microphone permission was denied.");
      else if (event.error === "no-speech") setError("I didn't hear anything — try again.");
      else if (event.error !== "aborted") setError("Speech input failed — please type instead.");
      setIsListening(false);
      setInterim("");
    };

    recognition.onend = () => {
      setIsListening(false);
      setInterim("");
    };

    recognitionRef.current = recognition;
    setIsListening(true);
    setError("");
    setInterim("");
    recognition.start();
  }, [isSupported]);

  const toggle = useCallback(() => {
    if (isListening) stop();
    else start();
  }, [isListening, start, stop]);

  useEffect(() => {
    return () => {
      recognitionRef.current?.abort();
    };
  }, []);

  return { isSupported, isListening, interim, error, start, stop, toggle };
}
