import { AnimatePresence, motion } from "framer-motion";
import { Brain, CheckCircle2, ChevronDown, Compass, ExternalLink, Globe, Link2, SearchCheck, Sparkles } from "lucide-react";
import { useState } from "react";

import type { ChatModeState, ChatTrace } from "@shared/schema";

import { type ChatModeKey, getChatModeKey, getChatModeTheme } from "@/lib/chatModes";
import { cn } from "@/lib/utils";

interface ChatTracePanelProps {
  trace: ChatTrace;
  mode: ChatModeState;
  warnings?: string[];
  className?: string;
}

// Accent colors per mode for the left-strip inline style
const ACCENT_RGBA: Record<ChatModeKey, string> = {
  default: "rgba(249,115,22,0.55)",
  brain: "rgba(139,92,246,0.55)",
  search: "rgba(30,174,219,0.55)",
  "brain-search": "rgba(255,255,255,0.30)",
};

const PROGRESS_NODES = [
  { label: "Thinking", kind: "thinking" },
  { label: "Steps",    kind: "steps" },
  { label: "Sources",  kind: "sources" },
  { label: "Why",      kind: "why" },
] as const;

function getSectionIcon(kind: string) {
  switch (kind) {
    case "thinking": return Brain;
    case "steps":    return Sparkles;
    case "sources":  return Link2;
    case "why":      return CheckCircle2;
    default:         return Compass;
  }
}

// Framer-motion variants
const flipVariants = {
  hidden:  { opacity: 0, rotateX: -12, y: -10 },
  visible: { opacity: 1, rotateX: 0,   y: 0,
    transition: { type: "spring" as const, stiffness: 340, damping: 28 } },
  exit:    { opacity: 0, rotateX: -8,  y: -6,
    transition: { duration: 0.18 } },
};

const containerVariants = {
  hidden:  {},
  visible: { transition: { staggerChildren: 0.055 } },
};

const cardVariants = {
  hidden:  { opacity: 0, y: 10 },
  visible: { opacity: 1, y: 0, transition: { duration: 0.22, ease: "easeOut" as const } },
};

export function ChatTracePanel({ trace, mode, warnings = [], className }: ChatTracePanelProps) {
  const theme   = getChatModeTheme(mode);
  const modeKey = getChatModeKey(mode);
  const [expanded, setExpanded] = useState(false);

  const accentStripe = ACCENT_RGBA[modeKey];

  return (
    <div className={cn("mt-3 rounded-2xl border p-4 backdrop-blur-sm", theme.tracePanelClass, className)}>
      {/* Header */}
      <div className="space-y-2">
        <div className="flex items-center gap-2">
          {modeKey === "search" || modeKey === "brain-search" ? (
            <SearchCheck className={cn("h-4 w-4", theme.accentTextClass)} />
          ) : (
            <Brain className={cn("h-4 w-4", theme.accentTextClass)} />
          )}
          <span className="text-[11px] font-semibold uppercase tracking-[0.24em] text-white/50">
            {trace.label}
          </span>
        </div>

        <h3 className="text-sm font-semibold text-white leading-5">{trace.headline}</h3>
        <p className="text-xs leading-6 text-white/65">{trace.summary}</p>

        {warnings.length > 0 && (
          <div className="rounded-xl border border-amber-400/25 bg-amber-400/10 px-3 py-2 text-[11px] text-amber-100/90">
            {warnings[0]}
          </div>
        )}

        {/* Toggle pill — its own row, right-aligned */}
        <div className="flex justify-end pt-0.5">
          <button
            onClick={() => setExpanded((v) => !v)}
            className={cn(
              "flex items-center gap-1.5 rounded-full border px-3 py-1 text-[11px] font-medium transition-colors duration-200",
              expanded
                ? cn(theme.accentBorderClass, theme.accentSoftBgClass, theme.accentTextClass)
                : "border-white/15 bg-white/[0.04] text-white/45 hover:border-white/30 hover:text-white/70",
            )}
          >
            {expanded ? "Hide reasoning" : "Show reasoning"}
            <motion.span
              animate={{ rotate: expanded ? 180 : 0 }}
              transition={{ type: "spring", stiffness: 300, damping: 22 }}
              className="flex items-center"
            >
              <ChevronDown className="h-3 w-3" />
            </motion.span>
          </button>
        </div>
      </div>

      {/* Expanded panel with flip animation */}
      <AnimatePresence>
        {expanded && (
          <motion.div
            key="trace-expanded"
            initial="hidden"
            animate="visible"
            exit="exit"
            variants={flipVariants}
            style={{ transformOrigin: "top center", transformPerspective: 800 }}
          >
            {/* Progress nodes */}
            <div className="mt-4 mb-3 flex items-center">
              {PROGRESS_NODES.map((node, i) => {
                const active = trace.sections.some((s) => s.kind === node.kind);
                return (
                  <div key={node.kind} className="flex items-center">
                    <div className="flex flex-col items-center gap-1">
                      <div
                        className={cn(
                          "h-2 w-2 rounded-full transition-colors duration-300",
                          active ? theme.accentStrongBgClass : "bg-white/15",
                        )}
                      />
                      <span
                        className={cn(
                          "text-[9px] uppercase tracking-[0.18em]",
                          active ? theme.accentTextClass : "text-white/25",
                        )}
                      >
                        {node.label}
                      </span>
                    </div>
                    {i < PROGRESS_NODES.length - 1 && (
                      <div
                        className={cn(
                          "mx-2 mb-3 h-px w-8 transition-colors duration-300",
                          active ? theme.accentSoftBgClass : "bg-white/[0.08]",
                        )}
                      />
                    )}
                  </div>
                );
              })}
            </div>

            {/* Section cards — staggered */}
            <motion.div
              className="grid gap-3 md:grid-cols-2"
              variants={containerVariants}
              initial="hidden"
              animate="visible"
            >
              {trace.sections.map((section) => {
                const Icon = getSectionIcon(section.kind);
                return (
                  <motion.div
                    key={section.id}
                    variants={cardVariants}
                    className={cn("relative rounded-2xl border p-3 pl-5 overflow-hidden", theme.traceCardClass)}
                  >
                    {/* Left accent stripe */}
                    <div
                      className="absolute left-0 top-0 bottom-0 w-[3px] rounded-l-2xl"
                      style={{ backgroundColor: accentStripe }}
                    />
                    <div className="flex items-center gap-2">
                      <div className={cn("flex h-6 w-6 items-center justify-center rounded-full", theme.accentSoftBgClass)}>
                        <Icon className={cn("h-3.5 w-3.5", theme.accentTextClass)} />
                      </div>
                      <h4 className="text-xs font-semibold text-white">{section.title}</h4>
                    </div>
                    <p className="mt-2 text-xs leading-6 text-white/65">{section.summary}</p>
                    {section.bullets.length > 0 && (
                      <div className="mt-3 space-y-2">
                        {section.bullets.map((bullet, index) => (
                          <div key={`${section.id}-${index}`} className="flex gap-2 text-xs leading-6 text-white/78">
                            <span className={cn("mt-[9px] h-1.5 w-1.5 shrink-0 rounded-full", theme.accentStrongBgClass)} />
                            <span>{bullet}</span>
                          </div>
                        ))}
                      </div>
                    )}
                  </motion.div>
                );
              })}
            </motion.div>

            {/* Search queries */}
            {trace.searchQueries.length > 0 && (
              <motion.div className="mt-4" variants={cardVariants}>
                <div className="mb-2 flex items-center gap-2 text-[11px] uppercase tracking-[0.24em] text-white/45">
                  <Globe className={cn("h-3.5 w-3.5", theme.accentTextClass)} />
                  <span>What Was Searched</span>
                </div>
                <div className="flex flex-wrap gap-2">
                  {trace.searchQueries.map((entry, index) => (
                    <div
                      key={`${entry.query}-${index}`}
                      className={cn("rounded-full border px-3 py-1.5 text-xs text-white/78", theme.traceCardClass)}
                    >
                      <span>{entry.query}</span>
                    </div>
                  ))}
                </div>
              </motion.div>
            )}

            {/* Search results */}
            {trace.searchResults.length > 0 && (
              <motion.div className="mt-4" variants={cardVariants}>
                <div className="mb-2 flex items-center gap-2 text-[11px] uppercase tracking-[0.24em] text-white/45">
                  <ExternalLink className={cn("h-3.5 w-3.5", theme.accentTextClass)} />
                  <span>Selected Results</span>
                </div>
                <div className="space-y-2">
                  {trace.searchResults.map((result) => (
                    <div
                      key={result.id}
                      className={cn(
                        "rounded-2xl border p-3 transition-colors",
                        result.selected ? theme.resultSelectedClass : theme.resultCardClass,
                      )}
                    >
                      <div className="flex items-start justify-between gap-3">
                        <div className="min-w-0">
                          <a
                            href={result.url}
                            target="_blank"
                            rel="noreferrer"
                            className="text-sm font-medium text-white underline decoration-white/20 underline-offset-4 transition-colors hover:text-white/80"
                          >
                            {result.title}
                          </a>
                          <p className="mt-1 text-[11px] text-white/45">
                            {result.domain} • {result.query}
                          </p>
                        </div>
                        {result.selected && (
                          <span className={cn("rounded-full px-2 py-1 text-[10px] font-semibold uppercase tracking-[0.18em]", theme.badgeClass)}>
                            Used
                          </span>
                        )}
                      </div>
                      <p className="mt-2 text-xs leading-6 text-white/68">{result.snippet}</p>
                      {result.selected && (result.reason || result.extracted) && (
                        <div className="mt-3 grid gap-2 md:grid-cols-2">
                          {result.reason && (
                            <div className={cn("rounded-xl border p-2.5", theme.traceCardClass)}>
                              <p className="text-[10px] uppercase tracking-[0.18em] text-white/40">Why it was selected</p>
                              <p className="mt-1 text-xs leading-6 text-white/78">{result.reason}</p>
                            </div>
                          )}
                          {result.extracted && (
                            <div className={cn("rounded-xl border p-2.5", theme.traceCardClass)}>
                              <p className="text-[10px] uppercase tracking-[0.18em] text-white/40">What was extracted</p>
                              <p className="mt-1 text-xs leading-6 text-white/78">{result.extracted}</p>
                            </div>
                          )}
                        </div>
                      )}
                    </div>
                  ))}
                </div>
              </motion.div>
            )}

            {/* Final why */}
            <motion.div variants={cardVariants} className={cn("mt-4 rounded-2xl border p-3", theme.traceCardClass)}>
              <div className="flex items-center gap-2">
                <CheckCircle2 className={cn("h-3.5 w-3.5", theme.accentTextClass)} />
                <p className="text-xs font-semibold text-white">Why the final answer looks this way</p>
              </div>
              <p className="mt-2 text-xs leading-6 text-white/75">{trace.finalWhy}</p>
            </motion.div>
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  );
}
