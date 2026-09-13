"use client";

interface HeaderProps {
  onNewChat: () => void;
}

export default function Header({ onNewChat }: HeaderProps) {
  return (
    <header className="flex shrink-0 items-center justify-between px-4 py-3 sm:px-8">
      <div className="flex items-center gap-2">
        <span className="h-2 w-2 rounded-full bg-accent" />
        <h1 className="text-sm font-medium text-foreground">Immigration Assistant</h1>
      </div>
      <button
        type="button"
        onClick={onNewChat}
        title="New chat"
        aria-label="New chat"
        className="flex h-8 w-8 items-center justify-center rounded-full text-muted transition-colors hover:bg-surface-hover hover:text-foreground"
      >
        <NewChatIcon />
      </button>
    </header>
  );
}

function NewChatIcon() {
  return (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.75" className="h-4 w-4">
      <path d="M12 5v14M5 12h14" />
    </svg>
  );
}
