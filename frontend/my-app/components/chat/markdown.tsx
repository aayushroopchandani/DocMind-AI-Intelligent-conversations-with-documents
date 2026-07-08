"use client";

import { memo } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";

interface MarkdownProps {
  content: string;
}

/**
 * Chat-flavored markdown renderer. Styles are scoped inline (no typography
 * plugin) so answers look right inside the dark chat bubbles.
 *
 * Memoized so the transcript doesn't re-render every markdown tree on each
 * streamed token — only the currently streaming message changes.
 */
export const Markdown = memo(function Markdown({ content }: MarkdownProps) {
  return (
    <div className="chat-markdown min-w-0 text-sm leading-relaxed">
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        components={{
          h1: ({ children }) => (
            <h3 className="mt-4 mb-2 text-base font-semibold first:mt-0">{children}</h3>
          ),
          h2: ({ children }) => (
            <h4 className="mt-4 mb-2 text-sm font-semibold first:mt-0">{children}</h4>
          ),
          h3: ({ children }) => (
            <h5 className="mt-3 mb-1.5 text-sm font-semibold first:mt-0">{children}</h5>
          ),
          p: ({ children }) => <p className="mb-2.5 last:mb-0">{children}</p>,
          ul: ({ children }) => (
            <ul className="mb-2.5 list-disc space-y-1 pl-5 last:mb-0">{children}</ul>
          ),
          ol: ({ children }) => (
            <ol className="mb-2.5 list-decimal space-y-1 pl-5 last:mb-0">{children}</ol>
          ),
          li: ({ children }) => <li className="[&>p]:mb-1">{children}</li>,
          a: ({ href, children }) => (
            <a
              href={href}
              target="_blank"
              rel="noreferrer"
              className="underline decoration-dotted underline-offset-2 hover:text-foreground"
            >
              {children}
            </a>
          ),
          blockquote: ({ children }) => (
            <blockquote className="mb-2.5 border-l-2 border-border pl-3 text-muted-foreground last:mb-0">
              {children}
            </blockquote>
          ),
          code: ({ className, children }) => {
            const isBlock = /language-/.test(className ?? "");
            if (isBlock) {
              return <code className={className}>{children}</code>;
            }
            return (
              <code className="rounded-md border border-border bg-background/80 px-1.5 py-0.5 font-mono text-[12px]">
                {children}
              </code>
            );
          },
          pre: ({ children }) => (
            <pre className="scrollbar-thin mb-2.5 overflow-x-auto rounded-xl border border-border bg-background/80 p-3 font-mono text-[12px] leading-relaxed last:mb-0">
              {children}
            </pre>
          ),
          table: ({ children }) => (
            <div className="scrollbar-thin mb-2.5 overflow-x-auto last:mb-0">
              <table className="w-full border-collapse text-left text-xs">{children}</table>
            </div>
          ),
          th: ({ children }) => (
            <th className="border-b border-border px-2 py-1.5 font-semibold">{children}</th>
          ),
          td: ({ children }) => (
            <td className="border-b border-border/50 px-2 py-1.5 align-top">{children}</td>
          ),
          hr: () => <hr className="my-3 border-border" />,
        }}
      >
        {content}
      </ReactMarkdown>
    </div>
  );
});
