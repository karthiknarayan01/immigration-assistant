"use client";

import { useState } from "react";
import { useVoiceConversation, VoicePhase } from "@/hooks/useVoiceConversation";

interface VoiceOverlayProps {
  onSend: (text: string) => Promise<string | undefined>;
  onClose: () => void;
}

export default function VoiceOverlay({ onSend, onClose }: VoiceOverlayProps) {
  const { phase, toggleMute, sendText, sttSupported, ttsSupported } = useVoiceConversation({ onSend });
  const [typedText, setTypedText] = useState("");

  const submitTyped = () => {
    const trimmed = typedText.trim();
    if (!trimmed) return;
    sendText(trimmed);
    setTypedText("");
  };

  return (
    <div className="animate-overlay-in absolute inset-0 z-20 flex flex-col bg-background">
      <div className="px-5 py-4 text-sm">
        <span className="font-semibold text-foreground">Immigration Assistant</span>{" "}
        <span className="text-muted">Voice</span>
      </div>

      <div className="flex flex-1 flex-col items-center justify-end gap-6 pb-16">
        <Orb phase={phase} />
        {!sttSupported && (
          <p className="max-w-xs text-center text-xs text-muted">
            Speech input isn&apos;t supported in this browser — type below instead.
          </p>
        )}
        {sttSupported && !ttsSupported && (
          <p className="max-w-xs text-center text-xs text-muted">
            This browser can&apos;t speak replies aloud — you&apos;ll see them in the chat after you exit.
          </p>
        )}
      </div>

      <div className="shrink-0 px-4 pb-6 sm:px-8">
        <div className="flex items-center gap-2 rounded-full border border-border bg-elevated py-2 pl-4 pr-2 shadow-[0_8px_30px_rgba(0,0,0,0.35)]">
          <span className="flex h-5 w-5 shrink-0 items-center justify-center text-muted/60">
            <PlusIcon />
          </span>
          <input
            value={typedText}
            onChange={(e) => setTypedText(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter") {
                e.preventDefault();
                submitTyped();
              }
            }}
            placeholder="Type"
            className="flex-1 bg-transparent py-1.5 text-[15px] text-foreground placeholder:text-muted focus:outline-none"
          />
          <button
            type="button"
            onClick={toggleMute}
            aria-pressed={phase === "muted"}
            title={phase === "muted" ? "Unmute" : "Mute"}
            className="flex h-9 w-9 shrink-0 items-center justify-center rounded-full text-foreground transition-colors hover:bg-surface-hover"
          >
            {phase === "muted" ? <MicOffIcon /> : <MicIcon />}
          </button>
          <button
            type="button"
            onClick={onClose}
            aria-label="Exit voice mode"
            title="Exit voice mode"
            className="flex h-9 w-9 shrink-0 items-center justify-center rounded-full bg-foreground text-background transition-opacity hover:opacity-85"
          >
            <CloseIcon />
          </button>
        </div>
      </div>
    </div>
  );
}

function Orb({ phase }: { phase: VoicePhase }) {
  const orbClass =
    phase === "thinking" ? "orb-thinking" : phase === "speaking" ? "orb-speaking" : phase === "listening" ? "orb-listening" : "";

  return (
    <div
      className={`voice-orb h-32 w-32 transition-opacity ${orbClass} ${phase === "muted" ? "opacity-30 grayscale" : ""}`}
    />
  );
}

function PlusIcon() {
  return (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.75" className="h-4 w-4">
      <path d="M12 5v14M5 12h14" />
    </svg>
  );
}

function MicIcon() {
  return (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" className="h-4 w-4">
      <path d="M12 15a3 3 0 0 0 3-3V6a3 3 0 0 0-6 0v6a3 3 0 0 0 3 3Z" />
      <path d="M19 11a7 7 0 0 1-14 0" />
      <path d="M12 18v4" />
    </svg>
  );
}

function MicOffIcon() {
  return (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" className="h-4 w-4">
      <path d="M9 9v3a3 3 0 0 0 4.6 2.55M15 9.4V6a3 3 0 0 0-5.94-.6" />
      <path d="M19 11a7 7 0 0 1-9.8 6.4M5 11a7 7 0 0 0 1.68 4.56" />
      <path d="M12 18v4" />
      <path d="M3 3l18 18" />
    </svg>
  );
}

function CloseIcon() {
  return (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.25" className="h-4 w-4">
      <path d="M18 6 6 18M6 6l12 12" />
    </svg>
  );
}
