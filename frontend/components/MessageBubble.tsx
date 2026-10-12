import { Message } from "@/lib/types";
import ReactMarkdown, { type Components } from "react-markdown";
import remarkGfm from "remark-gfm";

/**
 * The assistant answers in markdown — headings, bold labels, bulleted lists,
 * links — and this bubble used to render it as plain text, so readers saw
 * literal "###" and "**" characters. The model's formatting was never the
 * problem; nothing was parsing it.
 *
 * Two deliberate choices:
 *
 * Tailwind's preflight strips heading sizes, list markers and link styling, so
 * every element the model actually reaches for is styled below rather than
 * left looking like body text.
 *
 * `rehype-raw` is NOT used and no `dangerouslySetInnerHTML` appears here. The
 * answer is written from web search results, so HTML arriving inside those
 * results must stay inert rather than become markup. react-markdown escapes it
 * by default, which is the right behaviour for text we did not author.
 */

const components: Components = {
  h1: ({ children }) => <h2 className="mt-5 mb-2 text-[17px] font-semibold first:mt-0">{children}</h2>,
  h2: ({ children }) => <h2 className="mt-5 mb-2 text-[17px] font-semibold first:mt-0">{children}</h2>,
  h3: ({ children }) => <h3 className="mt-5 mb-2 text-[16px] font-semibold first:mt-0">{children}</h3>,
  h4: ({ children }) => <h4 className="mt-4 mb-1.5 text-[15px] font-semibold first:mt-0">{children}</h4>,
  // First and last blocks must not add padding inside the bubble.
  p: ({ children }) => <p className="my-2.5 leading-relaxed first:mt-0 last:mb-0">{children}</p>,
  ul: ({ children }) => <ul className="my-2.5 list-disc space-y-1.5 pl-5 marker:text-muted">{children}</ul>,
  ol: ({ children }) => <ol className="my-2.5 list-decimal space-y-1.5 pl-5 marker:text-muted">{children}</ol>,
  li: ({ children }) => <li className="leading-relaxed [&>p]:my-0">{children}</li>,
  strong: ({ children }) => <strong className="font-semibold text-foreground">{children}</strong>,
  em: ({ children }) => <em className="italic">{children}</em>,
  // Citations are the point of the product, so a link looks like one.
  a: ({ href, children }) => (
    <a
      href={href}
      target="_blank"
      rel="noopener noreferrer"
      className="font-medium text-accent-strong underline underline-offset-2 hover:opacity-80"
    >
      {children}
    </a>
  ),
  blockquote: ({ children }) => (
    <blockquote className="my-3 border-l-2 border-border-strong pl-3 text-muted">{children}</blockquote>
  ),
  hr: () => <hr className="my-4 border-border" />,
  code: ({ children }) => (
    <code className="rounded bg-elevated px-1.5 py-0.5 font-mono text-[13px]">{children}</code>
  ),
  pre: ({ children }) => (
    <pre className="my-3 overflow-x-auto rounded-lg bg-elevated p-3 font-mono text-[13px]">{children}</pre>
  ),
  // GFM tables, which the model reaches for when comparing categories.
  table: ({ children }) => (
    <div className="my-3 overflow-x-auto">
      <table className="w-full border-collapse text-[14px]">{children}</table>
    </div>
  ),
  th: ({ children }) => (
    <th className="border border-border bg-elevated px-2.5 py-1.5 text-left font-semibold">{children}</th>
  ),
  td: ({ children }) => <td className="border border-border px-2.5 py-1.5 align-top">{children}</td>,
};

export default function MessageBubble({ message, isStreaming }: { message: Message; isStreaming: boolean }) {
  const isUser = message.role === "user";
  const isEmpty = !isUser && message.content.length === 0;

  return (
    <div className={`animate-message-in flex w-full ${isUser ? "justify-end" : "justify-start"}`}>
      <div
        className={`max-w-[min(75ch,100%)] rounded-2xl px-4 py-2.5 text-[15px] leading-relaxed ${
          isUser ? "bg-accent text-accent-foreground" : "border border-border bg-surface text-foreground"
        } ${isStreaming && !isEmpty ? "streaming-caret" : ""}`}
      >
        {isEmpty ? (
          <span className="flex gap-1 py-1">
            <span className="h-1.5 w-1.5 animate-bounce rounded-full bg-muted [animation-delay:-0.3s]" />
            <span className="h-1.5 w-1.5 animate-bounce rounded-full bg-muted [animation-delay:-0.15s]" />
            <span className="h-1.5 w-1.5 animate-bounce rounded-full bg-muted" />
          </span>
        ) : isUser ? (
          // A person's own words are text, not markup: their line breaks should
          // survive, and parsing them would mangle a question containing
          // asterisks or underscores.
          <span className="whitespace-pre-wrap">{message.content}</span>
        ) : (
          <ReactMarkdown remarkPlugins={[remarkGfm]} components={components}>
            {message.content}
          </ReactMarkdown>
        )}
      </div>
    </div>
  );
}
