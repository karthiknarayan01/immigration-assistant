"use client";

import { useEffect, useRef, useState } from "react";
import { useSpeechRecognition } from "@/hooks/useSpeechRecognition";

interface ComposerProps {
  onSend: (text: string) => void | Promise<void>;
  disabled: boolean;
}

export default function Composer({ onSend, disabled }: ComposerProps) {
  const [text, setText] = useState("");
  const textareaRef = useRef<HTMLTextAreaElement>(null);

  // The mic button drives browser speech-to-text. On a final transcript the
  // text is sent straight to the backend — speech in, text out, same as typing.
  const { isSupported, isListening, interim, error, toggle } = useSpeechRecognition({
    onFinal: (transcript) => {
      setText("");
      onSend(transcript);
    },
  });

  useEffect(() => {
    const el = textareaRef.current;
    if (!el) return;
    el.style.height = "auto";
    el.style.height = `${Math.min(el.scrollHeight, 200)}px`;
  }, [text, interim]);

  const submit = () => {
    const trimmed = text.trim();
    if (!trimmed || disabled) return;
    onSend(trimmed);
    setText("");
  };

  const shown = isListening ? interim : text;

  return (
    <div className="shrink-0 px-4 pb-6 pt-2 sm:px-8">
      <div className="flex items-end gap-2 rounded-full border border-border bg-elevated py-2 pl-4 pr-2 shadow-[0_8px_30px_rgba(0,0,0,0.35)] transition-colors focus-within:border-border-strong">
        {/* Decorative for now — attachments aren't supported yet. */}
        <span className="mb-1.5 flex h-5 w-5 shrink-0 items-center justify-center text-muted/60">
          <PlusIcon />
        </span>
        <textarea
          ref={textareaRef}
          rows={1}
          value={shown}
          onChange={(e) => setText(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && !e.shiftKey) {
              e.preventDefault();
              submit();
            }
          }}
          placeholder={isListening ? "Listening…" : "Message Immigration Assistant"}
          disabled={disabled || isListening}
          className="max-h-[200px] flex-1 resize-none bg-transparent py-1.5 text-[15px] text-foreground placeholder:text-muted focus:outline-none"
        />
        {text.trim() ? (
          <button
            type="button"
            onClick={submit}
            disabled={disabled}
            className="flex h-9 w-9 shrink-0 items-center justify-center rounded-full bg-accent text-accent-foreground transition-all hover:bg-accent-strong disabled:opacity-30"
          >
            <SendIcon />
          </button>
        ) : isSupported ? (
          <button
            type="button"
            onClick={toggle}
            title={isListening ? "Stop listening" : "Speak instead of typing"}
            aria-label={isListening ? "Stop listening" : "Speak instead of typing"}
            aria-pressed={isListening}
            className={`flex h-9 w-9 shrink-0 items-center justify-center rounded-full transition-colors ${
              isListening
                ? "bg-red-500 text-white hover:bg-red-600"
                : "bg-accent text-accent-foreground hover:bg-accent-strong"
            }`}
          >
            {isListening ? <StopIcon /> : <WaveformIcon />}
          </button>
        ) : null}
      </div>
      {error && <p className="mt-2 text-center text-xs text-red-400">{error}</p>}
      <p className="mt-2 text-center text-xs text-muted">
        Informational only — not a substitute for advice from a licensed immigration attorney.
      </p>
    </div>
  );
}

function PlusIcon() {
  return (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.75" className="h-4 w-4">
      <path d="M12 5v14M5 12h14" />
    </svg>
  );
}

function WaveformIcon() {
  return (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" className="h-4 w-4">
      <line x1="4" y1="10" x2="4" y2="14" />
      <line x1="8" y1="6" x2="8" y2="18" />
      <line x1="12" y1="3" x2="12" y2="21" />
      <line x1="16" y1="6" x2="16" y2="18" />
      <line x1="20" y1="10" x2="20" y2="14" />
    </svg>
  );
}

function StopIcon() {
  return (
    <svg viewBox="0 0 24 24" fill="currentColor" className="h-4 w-4">
      <rect x="6" y="6" width="12" height="12" rx="2" />
    </svg>
  );
}

function SendIcon() {
  return (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" className="h-4 w-4">
      <path d="m5 12 14-7-7 14-2-6-6-2Z" />
    </svg>
  );
}
