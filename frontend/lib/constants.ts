// Once this many messages have piled up since the last summary, fold the
// older ones in so the context sent with each query stays small.
export const SUMMARY_TRIGGER_MESSAGES = 8;

// Always keep this many of the most recent messages verbatim (never folded
// into the rolling summary), so recent turns stay exact rather than lossy.
export const KEEP_RAW_MESSAGES = 4;
