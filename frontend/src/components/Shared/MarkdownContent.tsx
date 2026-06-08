import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { cn } from "@/lib/utils";

interface MarkdownContentProps {
  content?: string | null;
  className?: string;
  emptyText?: string;
}

export function MarkdownContent({ content, className, emptyText = "(空)" }: MarkdownContentProps) {
  const markdown = content?.trim() || emptyText;

  return (
    <div className={cn("min-w-0 max-w-full overflow-x-auto rounded-md bg-muted/30 p-4 text-sm leading-relaxed break-words", className)}>
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        components={{
          h1: ({ children }) => <h1 className="mb-3 mt-1 text-lg font-semibold leading-snug">{children}</h1>,
          h2: ({ children }) => <h2 className="mb-2 mt-4 text-base font-semibold leading-snug">{children}</h2>,
          h3: ({ children }) => <h3 className="mb-2 mt-3 text-sm font-semibold leading-snug">{children}</h3>,
          p: ({ children }) => <p className="my-2 whitespace-pre-wrap break-words">{children}</p>,
          strong: ({ children }) => <strong className="font-semibold text-foreground">{children}</strong>,
          ul: ({ children }) => <ul className="my-2 list-disc space-y-1 pl-5">{children}</ul>,
          ol: ({ children }) => <ol className="my-2 list-decimal space-y-1 pl-5">{children}</ol>,
          li: ({ children }) => <li className="break-words">{children}</li>,
          hr: () => <hr className="my-4 border-border" />,
          blockquote: ({ children }) => (
            <blockquote className="my-3 border-l-2 border-primary/30 pl-3 text-muted-foreground">
              {children}
            </blockquote>
          ),
          code: ({ children }) => (
            <code className="rounded bg-muted px-1 py-0.5 font-mono text-xs break-words">{children}</code>
          ),
          pre: ({ children }) => (
            <pre className="my-3 overflow-x-auto rounded-md bg-muted p-3 font-mono text-xs">
              {children}
            </pre>
          ),
          table: ({ children }) => (
            <div className="my-3 max-w-full overflow-x-auto rounded-md border">
              <table className="w-full min-w-max border-collapse text-left text-xs">{children}</table>
            </div>
          ),
          thead: ({ children }) => <thead className="bg-muted/70">{children}</thead>,
          th: ({ children }) => <th className="border-b border-r px-3 py-2 font-medium last:border-r-0">{children}</th>,
          td: ({ children }) => <td className="border-b border-r px-3 py-2 align-top last:border-r-0">{children}</td>,
        }}
      >
        {markdown}
      </ReactMarkdown>
    </div>
  );
}
