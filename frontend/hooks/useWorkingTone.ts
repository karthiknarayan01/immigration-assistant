"use client";

import { useEffect, useRef } from "react";

/**
 * A quiet tone under the pause while the agent looks something up.
 *
 * This lives on the client rather than in the audio pipeline because the
 * agent's own audio frames play in queue order: a tone pushed from the server
 * would sit *in front of* the answer and delay it, instead of underneath it.
 *
 * It is deliberately almost inaudible. The job is to make a six-second pause
 * feel like a machine working rather than a machine that has died, and a tone
 * loud enough to notice properly is a tone that grates by the third question.
 */

/** Low enough to sit under speech without masking it. */
const CARRIER_HZ = 174;

/** Peak gain. Roughly a whisper under the agent's voice. */
const PEAK_GAIN = 0.022;

/** A slow swell, so it reads as "alive" rather than as a dial tone. */
const LFO_HZ = 0.32;
const LFO_DEPTH = 0.45;

const FADE_IN_SECS = 0.35;
const FADE_OUT_SECS = 0.5;

export function useWorkingTone(active: boolean) {
  const contextRef = useRef<AudioContext | null>(null);
  const gainRef = useRef<GainNode | null>(null);
  const stopRef = useRef<(() => void) | null>(null);

  useEffect(() => {
    if (!active) {
      // Fade out and tear down, rather than cutting the tone dead.
      stopRef.current?.();
      stopRef.current = null;
      return;
    }

    let cancelled = false;
    let timer: ReturnType<typeof setTimeout> | null = null;

    // Safari and Chrome both refuse an AudioContext without a user gesture.
    // Entering voice mode is one, so by here we have it — but a failure is
    // still not worth breaking the overlay over.
    let context = contextRef.current;
    try {
      if (!context || context.state === "closed") {
        context = new AudioContext();
        contextRef.current = context;
      }
      if (context.state === "suspended") void context.resume();
    } catch {
      return;
    }

    const carrier = context.createOscillator();
    carrier.type = "sine";
    carrier.frequency.value = CARRIER_HZ;

    const gain = context.createGain();
    gain.gain.setValueAtTime(0, context.currentTime);
    gain.gain.linearRampToValueAtTime(PEAK_GAIN, context.currentTime + FADE_IN_SECS);
    gainRef.current = gain;

    // Amplitude swell, so the tone breathes instead of sitting flat.
    const lfo = context.createOscillator();
    lfo.type = "sine";
    lfo.frequency.value = LFO_HZ;
    const lfoDepth = context.createGain();
    lfoDepth.gain.value = PEAK_GAIN * LFO_DEPTH;
    lfo.connect(lfoDepth).connect(gain.gain);

    carrier.connect(gain).connect(context.destination);
    carrier.start();
    lfo.start();

    stopRef.current = () => {
      if (cancelled) return;
      cancelled = true;
      const ctx = contextRef.current;
      if (!ctx) return;
      const now = ctx.currentTime;
      try {
        gain.gain.cancelScheduledValues(now);
        gain.gain.setValueAtTime(gain.gain.value, now);
        gain.gain.linearRampToValueAtTime(0, now + FADE_OUT_SECS);
        carrier.stop(now + FADE_OUT_SECS);
        lfo.stop(now + FADE_OUT_SECS);
      } catch {
        // Already stopped; nothing to unwind.
      }
      timer = setTimeout(() => {
        carrier.disconnect();
        lfo.disconnect();
        lfoDepth.disconnect();
        gain.disconnect();
      }, FADE_OUT_SECS * 1000 + 50);
    };

    return () => {
      stopRef.current?.();
      stopRef.current = null;
      if (timer) clearTimeout(timer);
    };
  }, [active]);

  // Close the context only when the component goes away, not on every toggle:
  // browsers cap how many can be opened, and a long conversation toggles this
  // once per tool call.
  useEffect(() => {
    return () => {
      void contextRef.current?.close().catch(() => {});
      contextRef.current = null;
    };
  }, []);
}
