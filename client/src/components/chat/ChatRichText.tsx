import React from "react";

import { cn } from "@/lib/utils";

interface ChatRichTextProps {
  content: string;
  className?: string;
  linkClassName?: string;
}

interface LinkToken {
  label: string;
  url: string;
}

interface LabelToken {
  label: string;
  rest: string;
}

const LABEL_PATTERNS: Array<{ prefix: string; match: RegExp }> = [
  { prefix: "SELECTION", match: /^SELECTION\s{2,}(.*)$/ },
  { prefix: "Confidence", match: /^Confidence\s{2,}(.*)$/ },
  { prefix: "Consensus", match: /^Consensus\s{2,}(.*)$/ },
  { prefix: "Danger", match: /^Danger\s{2,}(.*)$/ },
  { prefix: "Sources:", match: /^Sources:\s+(.*)$/ },
];

function parseMarkdownLinks(line: string): Array<string | LinkToken> {
  const pattern = /\[([^\]]+)\]\((https?:\/\/[^\s)]+)\)/g;
  const parts: Array<string | LinkToken> = [];
  let lastIndex = 0;

  for (const match of line.matchAll(pattern)) {
    const start = match.index ?? 0;
    if (start > lastIndex) {
      parts.push(line.slice(lastIndex, start));
    }

    parts.push({
      label: match[1],
      url: match[2],
    });

    lastIndex = start + match[0].length;
  }

  if (lastIndex < line.length) {
    parts.push(line.slice(lastIndex));
  }

  return parts.length > 0 ? parts : [line];
}

function detectLabel(line: string): LabelToken | null {
  const trimmed = line.trim();
  for (const { match, prefix } of LABEL_PATTERNS) {
    const m = trimmed.match(match);
    if (m) {
      return { label: prefix, rest: m[1] ?? "" };
    }
  }
  return null;
}

function isLabelToken(token: unknown): token is LabelToken {
  return typeof token === "object" && token !== null && "label" in token && "rest" in token;
}

function isLinkToken(token: unknown): token is LinkToken {
  return typeof token === "object" && token !== null && "url" in token && "label" in token && !("rest" in token);
}

export function ChatRichText({ content, className, linkClassName }: ChatRichTextProps) {
  const lines = content.split("\n");

  return (
    <div className={cn("space-y-2 text-sm leading-7 text-white/92", className)}>
      {lines.map((line, lineIndex) => {
        if (!line.trim()) {
          return <p key={`${lineIndex}-empty`} className="h-3" />;
        }

        const labelToken = detectLabel(line);
        if (labelToken) {
          const restParts = parseMarkdownLinks(labelToken.rest);
          return (
            <p key={`${lineIndex}-${line}`}>
              <span className="mr-2 text-[10px] font-medium uppercase tracking-widest text-white/45">
                {labelToken.label}
              </span>
              {restParts.map((part, partIndex) =>
                typeof part === "string" ? (
                  <React.Fragment key={`${lineIndex}-${partIndex}`}>{part}</React.Fragment>
                ) : isLinkToken(part) ? (
                  <a
                    key={`${lineIndex}-${partIndex}-${part.url}`}
                    href={part.url}
                    target="_blank"
                    rel="noreferrer"
                    className={cn(
                      "underline decoration-current/70 underline-offset-2 transition-colors hover:text-white",
                      linkClassName,
                    )}
                  >
                    {part.label}
                  </a>
                ) : null,
              )}
            </p>
          );
        }

        // Regular line with markdown link parsing
        return (
          <p key={`${lineIndex}-${line}`}>
            {parseMarkdownLinks(line).map((part, partIndex) =>
              typeof part === "string" ? (
                <React.Fragment key={`${lineIndex}-${partIndex}`}>{part}</React.Fragment>
              ) : isLinkToken(part) ? (
                <a
                  key={`${lineIndex}-${partIndex}-${part.url}`}
                  href={part.url}
                  target="_blank"
                  rel="noreferrer"
                  className={cn(
                    "underline decoration-current/70 underline-offset-2 transition-colors hover:text-white",
                    linkClassName,
                  )}
                >
                  {part.label}
                </a>
              ) : null,
            )}
          </p>
        );
      })}
    </div>
  );
}
