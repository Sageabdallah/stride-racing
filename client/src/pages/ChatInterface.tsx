import { AnimatePresence, motion } from "framer-motion";
import { useEffect, useMemo, useRef, useState } from "react";
import { Link } from "wouter";
import {
  ArrowLeft,
  Brain,
  ChevronDown,
  ChevronRight,
  Clock3,
  Flag,
  Globe,
  Loader2,
  MapPin,
  MessageSquare,
  MoreHorizontal,
  PanelLeft,
  Plus,
  Sparkles,
  ThumbsDown,
  ThumbsUp,
  Trash2,
  Trophy,
  X,
} from "lucide-react";
import { useMutation, useQuery } from "@tanstack/react-query";
import type {
  ChatAnswerSource,
  ChatCitationLink,
  ChatCompletionResponse,
  ChatModeState,
  ChatTrace,
} from "@shared/schema";

import { ChatRichText } from "@/components/chat/ChatRichText";
import { ChatTracePanel } from "@/components/chat/ChatTracePanel";
import { SearchThinkingStream } from "@/components/chat/SearchThinkingStream";
import { PromptInputBox } from "@/components/ui/ai-prompt-box";
import { GLSLHills } from "@/components/ui/glsl-hills";
import { ScrollArea } from "@/components/ui/scroll-area";
import { toast } from "@/hooks/use-toast";
import {
  getChatModeTheme,
  getDeepThoughtLoadingStream,
  getSearchThoughtStream,
  getModeExperienceCopy,
  getModeLoadingPanelLabel,
  getModeLoadingSteps,
} from "@/lib/chatModes";
import { cn } from "@/lib/utils";
import { apiRequest, readJsonResponse } from "@/lib/queryClient";

// ── Types ────────────────────────────────────────────────────────────────────

interface ActiveRaceContext {
  date: string;
  track: string;
  raceNumber: number;
  raceName?: string;
  distance?: string;
  going?: string;
}

interface Message {
  id: string;
  role: "user" | "assistant";
  content: string;
  mode?: ChatModeState;
  trace?: ChatTrace;
  citations?: ChatCitationLink[];
  warnings?: string[];
  answerSource?: ChatAnswerSource;
  reasoning?: string;
  promptVersion?: string;
  feedbackRating?: 1 | -1;
}

interface ChatSession {
  id: string;
  title: string;
  createdAt: number;
  updatedAt: number;
  messages: Message[];
}

interface ChatState {
  sessions: ChatSession[];
  currentSessionId: string;
}

interface RaceEntry {
  race_number: string;
  race_name: string;
  distance: string;
  going?: string;
  class?: string;
  off_time?: string;
  runners?: { horse: string; number: string; jockey?: string }[];
}

interface MeetEntry {
  course: string;
  meet_id: string;
  date: string;
  races: RaceEntry[];
}

interface RacecardDay {
  filename: string;
  date: string;
  meets: MeetEntry[];
  meetCount: number;
  raceCount: number;
}

interface RacecardSection {
  id: "metro" | "provincial";
  label: string;
  description: string;
  days: RacecardDay[];
}

// ── Constants ─────────────────────────────────────────────────────────────────

const CHAT_STORAGE_KEY = "stride-chat-sessions-v1";
const ACTIVE_CHAT_STORAGE_KEY = "stride-chat-active-session-v1";

const suggestedPrompts = [
  "Explain my algorithm's edge calculation",
  "What factors matter most at Flemington?",
  "How does track condition affect my model?",
  "Analyze today's feature race",
];

const METRO_TRACKS = [
  "randwick",
  "royal randwick",
  "rosehill",
  "rosehill gardens",
  "canterbury",
  "canterbury park",
  "warwick farm",
  "kensington",
  "newcastle",
  "kembla grange",
  "gosford",
  "wyong",
  "flemington",
  "caulfield",
  "moonee valley",
  "sandown",
  "sandown lakeside",
  "sandown hillside",
  "sportsbet sandown",
  "eagle farm",
  "doomben",
  "gold coast",
  "aquis park",
  "morphettville",
  "ascot",
  "belmont",
];

// ── Helpers ───────────────────────────────────────────────────────────────────

function createChatSession(): ChatSession {
  const now = Date.now();
  return {
    id: `stride-chat-${now}-${Math.random().toString(36).slice(2, 8)}`,
    title: "New chat",
    createdAt: now,
    updatedAt: now,
    messages: [],
  };
}

function buildSessionTitle(content: string) {
  const normalized = content
    .replace(/^\[(Search|Think|Canvas):\s*/i, "")
    .replace(/\]\s*$/, "")
    .split("\n[Attached image")[0]
    .replace(/\s+/g, " ")
    .trim();
  if (!normalized) return "New chat";
  return normalized.length > 52 ? `${normalized.slice(0, 52).trim()}...` : normalized;
}

function cleanPreviewText(content: string) {
  return content
    .replace(/\[([^\]]+)\]\((https?:\/\/[^\s)]+)\)/g, "$1")
    .replace(/\*\*([^*]+)\*\*/g, "$1")
    .replace(/__([^_]+)__/g, "$1")
    .replace(/`([^`]+)`/g, "$1")
    .replace(/\s*\[Attached image[s]?:[^\]]+\]\s*/gi, " ")
    .replace(/(^|\n)\s*-\s+/g, "$1")
    .replace(/\s+/g, " ")
    .trim();
}

function trimPreviewText(content: string, maxLength = 148) {
  const cleaned = cleanPreviewText(content);
  if (cleaned.length <= maxLength) {
    return cleaned;
  }

  const sliced = cleaned.slice(0, maxLength);
  const lastSpace = sliced.lastIndexOf(" ");
  const safeSlice = lastSpace > 72 ? sliced.slice(0, lastSpace) : sliced;
  return `${safeSlice.trim()}...`;
}

function buildSessionPreview(session: ChatSession) {
  const last = session.messages[session.messages.length - 1];
  return last?.content ? trimPreviewText(last.content) : "Fresh chat ready for a new question.";
}

function SessionListItem({
  session,
  isActive,
  disabled,
  theme,
  onSelect,
  onDelete,
  compact = false,
}: {
  session: ChatSession;
  isActive: boolean;
  disabled?: boolean;
  theme: ReturnType<typeof getChatModeTheme>;
  onSelect: () => void;
  onDelete: () => void;
  compact?: boolean;
}) {
  const [menuOpen, setMenuOpen] = useState(false);
  const menuRef = useRef<HTMLDivElement>(null);
  const preview = buildSessionPreview(session);

  useEffect(() => {
    if (!menuOpen) return;

    const handlePointerDown = (event: MouseEvent) => {
      if (menuRef.current && !menuRef.current.contains(event.target as Node)) {
        setMenuOpen(false);
      }
    };

    document.addEventListener("pointerdown", handlePointerDown);
    return () => document.removeEventListener("pointerdown", handlePointerDown);
  }, [menuOpen]);

  return (
    <div
      className={cn(
        "group relative overflow-visible rounded-2xl border transition-all",
        isActive
          ? `${theme.accentBorderClass} ${theme.accentSoftBgClass}`
          : "border-white/[0.04] hover:border-white/[0.10] hover:bg-white/[0.03]",
        disabled && "opacity-60",
      )}
    >
      <div className="flex items-start gap-2 overflow-hidden pr-2">
        <button
          onClick={onSelect}
          disabled={disabled}
          className={cn("min-w-0 flex-1 px-3 py-3 text-left", compact ? "px-3 py-3" : "px-3 py-3.5")}
        >
          <div className="flex min-w-0 items-start gap-2.5">
            <div
              className={cn(
                "mt-0.5 shrink-0 rounded-lg p-1.5",
                isActive
                  ? `${theme.accentStrongBgClass} ${theme.accentTextClass}`
                  : "bg-white/[0.06] text-white/40",
              )}
            >
              <MessageSquare className="h-3.5 w-3.5" />
            </div>
            <div className={cn("min-w-0 flex-1 overflow-hidden", compact ? "space-y-1.5" : "space-y-2")}>
              <div className="min-w-0">
                <p
                  className={cn(
                    "min-w-0 break-words font-medium text-white",
                    compact ? "line-clamp-2 text-sm leading-5" : "line-clamp-2 text-[13px] leading-5",
                  )}
                >
                  {session.title}
                </p>
              </div>
              <p
                className={cn(
                  "overflow-hidden break-words text-white/38",
                  compact ? "text-xs leading-5" : "text-[11px] leading-5",
                )}
              >
                {preview}
              </p>
              <div className="flex items-center gap-2 pt-0.5">
                <span className="shrink-0 text-[10px] uppercase tracking-[0.16em] text-white/28">
                  {formatSessionTime(session.updatedAt)}
                </span>
              </div>
            </div>
          </div>
        </button>

        <div ref={menuRef} className={cn("relative shrink-0", compact ? "pt-2.5" : "pt-3")}>
          <button
            type="button"
            title="More actions"
            disabled={disabled}
            onClick={(event) => {
              event.stopPropagation();
              setMenuOpen((current) => !current);
            }}
            className={cn(
              "rounded-lg p-1.5 text-white/28 transition-colors hover:bg-white/[0.06] hover:text-white/80",
              menuOpen && "bg-white/[0.06] text-white/80",
            )}
          >
            <MoreHorizontal className="h-4 w-4" />
          </button>

          {menuOpen && (
            <div className="absolute right-0 top-10 z-20 w-36 rounded-xl border border-white/[0.08] bg-black/95 p-1.5 shadow-[0_12px_28px_rgba(0,0,0,0.42)] backdrop-blur-xl">
              <button
                type="button"
                onClick={(event) => {
                  event.stopPropagation();
                  setMenuOpen(false);
                  onDelete();
                }}
                className="flex w-full items-center gap-2 rounded-lg px-3 py-2 text-left text-sm text-rose-200 transition-colors hover:bg-rose-500/10 hover:text-rose-100"
              >
                <Trash2 className="h-3.5 w-3.5" />
                Delete chat
              </button>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

function AssistantMessageCard({
  message,
  sessionId,
  onFeedback,
}: {
  message: Message;
  sessionId: string;
  onFeedback: (messageId: string, rating: 1 | -1) => void;
}) {
  const mode = message.mode ?? { brain: false, search: false };
  const theme = getChatModeTheme(mode);
  const modeLabel = theme.label;
  const hasWebSources = Boolean(message.citations && message.citations.length > 0);

  const handleFeedback = async (rating: 1 | -1) => {
    onFeedback(message.id, rating);
    try {
      await fetch("/api/chat/feedback", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          sessionId,
          messageId: message.id,
          rating,
          promptVersion: message.promptVersion,
        }),
      });
    } catch {
      // Feedback is best-effort — don't disrupt UX on failure
    }
  };

  return (
    <div className="max-w-[92%] space-y-3">
      <div
        className={cn(
          "rounded-2xl border px-4 py-3 text-white backdrop-blur-sm transition-colors duration-300",
          theme.assistantBubbleClass,
        )}
      >
        <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
          <div className="flex items-center gap-2">
            {mode.search ? (
              <Globe className={cn("h-3.5 w-3.5", theme.accentTextClass)} />
            ) : (
              <Brain className={cn("h-3.5 w-3.5", theme.accentTextClass)} />
            )}
            <span className={cn("text-[11px] font-semibold uppercase tracking-[0.24em]", theme.accentTextClass)}>
              {modeLabel}
            </span>
          </div>
          <div className="flex flex-wrap items-center gap-2 text-[10px] uppercase tracking-[0.18em] text-white/45">
            {message.answerSource && <span>{message.answerSource}</span>}
            {hasWebSources && <span>{message.citations?.length} web source{message.citations?.length === 1 ? "" : "s"}</span>}
          </div>
        </div>

        <ChatRichText content={message.content} linkClassName={theme.linkClass} />

        {/* Feedback buttons */}
        <div className="mt-3 flex items-center gap-1 border-t border-white/10 pt-2">
          <button
            onClick={() => handleFeedback(1)}
            className={cn(
              "rounded-md p-1.5 transition-colors",
              message.feedbackRating === 1
                ? "text-green-400"
                : "text-white/30 hover:text-white/60",
            )}
            title="Helpful"
          >
            <ThumbsUp className="h-3.5 w-3.5" />
          </button>
          <button
            onClick={() => handleFeedback(-1)}
            className={cn(
              "rounded-md p-1.5 transition-colors",
              message.feedbackRating === -1
                ? "text-red-400"
                : "text-white/30 hover:text-white/60",
            )}
            title="Not helpful"
          >
            <ThumbsDown className="h-3.5 w-3.5" />
          </button>
        </div>
      </div>

      {message.trace && (
        <ChatTracePanel trace={message.trace} mode={mode} warnings={message.warnings} />
      )}
    </div>
  );
}

function LoadingTraceCard({
  mode,
  question,
  stepIndex,
}: {
  mode: ChatModeState;
  question: string | null;
  stepIndex: number;
}) {
  const theme = getChatModeTheme(mode);
  const steps = getModeLoadingSteps(mode);
  const isDeepThought = mode.brain && !mode.search;
  const isSearch = mode.search;
  const stream = isDeepThought ? getDeepThoughtLoadingStream(question) : [];
  const searchThoughts = isSearch ? getSearchThoughtStream(question) : [];
  const activeIndex = isDeepThought
    ? stepIndex % Math.max(stream.length, 1)
    : isSearch
      ? Math.min(stepIndex, searchThoughts.length - 1)
      : stepIndex % Math.max(steps.length, 1);
  const revealedThoughts = isDeepThought ? Math.min(stream.length, stepIndex + 1) : 0;
  const workingLabel = question
    ? isDeepThought
      ? `Deep Thought is working through "${question}"`
      : `Working on "${question}"`
    : isDeepThought
      ? "Deep Thought is working through the request"
      : "Working through the request";

  return (
    <div className="max-w-[92%] space-y-3">
      <div className={cn("rounded-2xl border px-4 py-3 backdrop-blur-sm", theme.assistantBubbleClass)}>
        <div className="flex items-center gap-2">
          {mode.search ? (
            <Globe className={cn("h-4 w-4", theme.accentTextClass)} />
          ) : (
            <Brain className={cn("h-4 w-4", theme.accentTextClass)} />
          )}
          <AnimatePresence mode="wait">
            <motion.span
              key={workingLabel}
              initial={{ opacity: 0, y: 3 }}
              animate={{ opacity: 1, y: 0 }}
              exit={{ opacity: 0, y: -3 }}
              transition={{ duration: 0.18 }}
              className={cn("text-xs font-medium", theme.accentTextClass)}
            >
              {workingLabel}
            </motion.span>
          </AnimatePresence>
          <Loader2 className={cn("h-3.5 w-3.5 animate-spin", theme.accentTextClass)} />
        </div>
      </div>

      <div className={cn("rounded-2xl border p-4 backdrop-blur-sm", theme.tracePanelClass)}>
        <div className="mb-3 flex items-center gap-2">
          {mode.search ? (
            <Globe className={cn("h-4 w-4", theme.accentTextClass)} />
          ) : (
            <Brain className={cn("h-4 w-4", theme.accentTextClass)} />
          )}
          <span className="text-[11px] font-semibold uppercase tracking-[0.24em] text-white/50">
            {getModeLoadingPanelLabel(mode)}
          </span>
        </div>

        {isDeepThought ? (
          <div className="space-y-3">
            <div className={cn("rounded-2xl border px-3 py-3", theme.traceCardClass)}>
              <p className="text-xs leading-6 text-white/72">
                Deep Thought is showing a live reasoning stream while it breaks the problem down, checks assumptions, and shapes the final answer.
              </p>
            </div>

            <div className="space-y-2">
              {stream.map((thought, index) => {
                const visible = index < revealedThoughts;
                const active  = visible && index === activeIndex;
                return (
                  <motion.div
                    key={thought}
                    animate={{
                      opacity: visible ? 1 : 0.42,
                      y: visible ? 0 : 3,
                      scale: active ? [1, 1.01, 1] : 1,
                    }}
                    transition={
                      active
                        ? { scale: { duration: 2, repeat: Infinity, ease: "easeInOut" }, opacity: { duration: 0.28 }, y: { duration: 0.28 } }
                        : { duration: 0.28 }
                    }
                    className={cn(
                      "rounded-2xl border px-3 py-2.5",
                      visible ? theme.resultSelectedClass : theme.traceCardClass,
                    )}
                  >
                    <div className="flex items-start gap-2">
                      {active ? (
                        <Loader2 className={cn("mt-0.5 h-3.5 w-3.5 animate-spin", theme.accentTextClass)} />
                      ) : visible ? (
                        <span className={cn("mt-[7px] h-1.5 w-1.5 rounded-full", theme.loadingBarClass)} />
                      ) : (
                        <span className="mt-[7px] h-1.5 w-1.5 rounded-full bg-white/18" />
                      )}
                      <div className="min-w-0">
                        <p className={cn("text-xs leading-6", visible ? "text-white/88" : "text-white/42")}>
                          {thought}
                        </p>
                        {active && (
                          <p className="text-[11px] leading-5 text-white/42">
                            Live thinking stays visible until the final answer is ready.
                          </p>
                        )}
                      </div>
                    </div>
                  </motion.div>
                );
              })}
            </div>
          </div>
        ) : isSearch ? (
          <div className="space-y-4">
            <div className={cn("rounded-2xl border px-4 py-3", theme.traceCardClass)}>
              <SearchThinkingStream
                thoughts={searchThoughts}
                activeIndex={activeIndex}
                theme={theme}
              />
            </div>
            <div className="flex flex-wrap gap-1.5">
              {steps.map((step, index) => (
                <div
                  key={step}
                  className={cn(
                    "rounded-full border px-2.5 py-1 text-[10px] transition-colors",
                    index <= activeIndex ? theme.resultSelectedClass : theme.traceCardClass,
                    index <= activeIndex ? "text-white/75" : "text-white/30",
                  )}
                >
                  {step}
                </div>
              ))}
            </div>
          </div>
        ) : (
          <div className="space-y-2">
            {steps.map((step, index) => {
              const active = index === activeIndex;
              return (
                <motion.div
                  key={step}
                  initial={{ opacity: 0, y: 6 }}
                  animate={{ opacity: 1, y: 0 }}
                  transition={{ duration: 0.22, delay: index * 0.07, ease: "easeOut" }}
                  className={cn(
                    "rounded-2xl border px-3 py-2.5 transition-colors duration-300",
                    active ? theme.resultSelectedClass : theme.traceCardClass,
                  )}
                >
                  <div className="flex items-center gap-2">
                    {active ? (
                      <Loader2 className={cn("h-3.5 w-3.5 animate-spin", theme.accentTextClass)} />
                    ) : (
                      <div className="h-2 w-2 rounded-full bg-white/20" />
                    )}
                    <span className={cn("text-xs", active ? "text-white" : "text-white/60")}>{step}</span>
                  </div>
                </motion.div>
              );
            })}
          </div>
        )}
      </div>
    </div>
  );
}

function formatSessionTime(timestamp: number) {
  const date = new Date(timestamp);
  const now = new Date();
  const diffHours = (now.getTime() - date.getTime()) / (1000 * 60 * 60);
  if (diffHours < 24 && now.toDateString() === date.toDateString()) {
    return new Intl.DateTimeFormat("en-AU", { hour: "numeric", minute: "2-digit" }).format(date);
  }
  if (diffHours < 24 * 7) {
    return new Intl.DateTimeFormat("en-AU", { weekday: "short" }).format(date);
  }
  return new Intl.DateTimeFormat("en-AU", { day: "numeric", month: "short" }).format(date);
}

function formatRaceDate(dateStr: string) {
  try {
    const d = new Date(dateStr);
    const today = new Date();
    const yesterday = new Date(today);
    yesterday.setDate(yesterday.getDate() - 1);
    if (d.toDateString() === today.toDateString()) return "Today";
    if (d.toDateString() === yesterday.toDateString()) return "Yesterday";
    return new Intl.DateTimeFormat("en-AU", { weekday: "short", day: "numeric", month: "short" }).format(d);
  } catch {
    return dateStr;
  }
}

function isMetroTrack(trackName: string) {
  const normalizedTrack = trackName.toLowerCase().trim();
  return METRO_TRACKS.some((metro) => normalizedTrack.includes(metro) || metro.includes(normalizedTrack));
}

function filterRacecardDay(day: RacecardDay, predicate: (meet: MeetEntry) => boolean): RacecardDay | null {
  const meets = day.meets.filter(predicate);
  if (meets.length === 0) return null;

  return {
    ...day,
    meets,
    meetCount: meets.length,
    raceCount: meets.reduce((total, meet) => total + (meet.races?.length ?? 0), 0),
  };
}

function getInitialChatState(): ChatState {
  const fallback = createChatSession();
  if (typeof window === "undefined") return { sessions: [fallback], currentSessionId: fallback.id };
  try {
    const stored = window.localStorage.getItem(CHAT_STORAGE_KEY);
    const parsed: ChatSession[] = stored ? JSON.parse(stored) : [];
    const valid = parsed.filter(
      (s) => s && typeof s.id === "string" && Array.isArray(s.messages) && typeof s.updatedAt === "number",
    );
    if (valid.length === 0) return { sessions: [fallback], currentSessionId: fallback.id };
    const sorted = [...valid].sort((a, b) => b.updatedAt - a.updatedAt);
    const storedActive = window.localStorage.getItem(ACTIVE_CHAT_STORAGE_KEY);
    const currentSessionId = sorted.some((s) => s.id === storedActive)
      ? (storedActive as string)
      : sorted[0].id;
    return { sessions: sorted, currentSessionId };
  } catch {
    return { sessions: [fallback], currentSessionId: fallback.id };
  }
}

// ── Race Sidebar ──────────────────────────────────────────────────────────────

function RaceSidebar({
  onSelectRace,
  onSetContext,
  activeContext,
  mode,
}: {
  onSelectRace: (text: string) => void;
  onSetContext: (ctx: ActiveRaceContext | null) => void;
  activeContext: ActiveRaceContext | null;
  mode: ChatModeState;
}) {
  const theme = getChatModeTheme(mode);
  const [expandedDates, setExpandedDates] = useState<Set<string>>(new Set());
  const [expandedMeets, setExpandedMeets] = useState<Set<string>>(new Set());
  const [expandedRaces, setExpandedRaces] = useState<Set<string>>(new Set());

  const { data: racecards, isLoading } = useQuery<RacecardDay[]>({
    queryKey: ["racecards-by-date"],
    queryFn: async () => {
      // Build list of the last 10 days to probe
      const dates: string[] = [];
      for (let i = 0; i < 14; i++) {
        const d = new Date();
        d.setDate(d.getDate() - i);
        dates.push(d.toISOString().slice(0, 10));
      }

      const results = await Promise.allSettled(
        dates.map(async (date) => {
          const res = await fetch(`/api/local/racecards/${date}`);
          if (!res.ok) return null;
          const json = await res.json();
          // Response: { date, meets[], meetCount, raceCount }
          if (!json.meets || json.meets.length === 0) return null;
          return {
            filename: `racecard_${date}.json`,
            date: json.date ?? date,
            meets: json.meets,
            meetCount: json.meetCount ?? json.meets.length,
            raceCount: json.raceCount ?? json.meets.reduce((acc: number, m: any) => acc + (m.races?.length ?? 0), 0),
          } as RacecardDay;
        })
      );

      return results
        .filter((r): r is PromiseFulfilledResult<RacecardDay> => r.status === "fulfilled" && r.value !== null)
        .map((r) => r.value)
        .sort((a, b) => (b.date > a.date ? 1 : -1));
    },
    staleTime: 5 * 60 * 1000,
  });

  const sections = useMemo<RacecardSection[]>(() => {
    const days = racecards ?? [];
    const metroDays = days
      .map((day) => filterRacecardDay(day, (meet) => isMetroTrack(meet.course)))
      .filter((day): day is RacecardDay => day !== null);
    const provincialDays = days
      .map((day) => filterRacecardDay(day, (meet) => !isMetroTrack(meet.course)))
      .filter((day): day is RacecardDay => day !== null);

    return [
      {
        id: "metro",
        label: "Metro Tracks",
        description: "City and premium meetings",
        days: metroDays,
      },
      {
        id: "provincial",
        label: "Provincial Tracks",
        description: "Provincial and country meetings",
        days: provincialDays,
      },
    ];
  }, [racecards]);

  const toggleDate = (date: string) => {
    setExpandedDates((prev) => {
      const next = new Set(prev);
      next.has(date) ? next.delete(date) : next.add(date);
      return next;
    });
  };

  const toggleMeet = (key: string) => {
    setExpandedMeets((prev) => {
      const next = new Set(prev);
      next.has(key) ? next.delete(key) : next.add(key);
      return next;
    });
  };

  const toggleRace = (key: string) => {
    setExpandedRaces((prev) => {
      const next = new Set(prev);
      next.has(key) ? next.delete(key) : next.add(key);
      return next;
    });
  };

  const handleRaceClick = (day: RacecardDay, meet: MeetEntry, race: RaceEntry) => {
    const label = `${formatRaceDate(day.date)} — ${meet.course} Race ${race.race_number}`;
    onSelectRace(
      `Analyze ${meet.course} Race ${race.race_number} on ${day.date} — ${race.race_name} (${race.distance}${race.going ? `, ${race.going}` : ""})`,
    );
  };

  if (isLoading) {
    return (
      <div className="flex h-full items-center justify-center">
        <Loader2 className="h-5 w-5 animate-spin text-white/30" />
      </div>
    );
  }

  if (!racecards || racecards.length === 0) {
    return (
      <div className="flex h-full flex-col items-center justify-center gap-2 px-4 text-center">
        <Flag className="h-8 w-8 text-white/20" />
        <p className="text-xs text-white/35">No race data available</p>
      </div>
    );
  }

  return (
    <ScrollArea className="h-full">
      <div className="space-y-4 p-2">
        {sections.map((section) => {
          const sectionRaceCount = section.days.reduce((total, day) => total + day.raceCount, 0);
          const sectionMeetCount = section.days.reduce((total, day) => total + day.meetCount, 0);

          return (
            <div key={section.id} className="rounded-2xl border border-white/[0.06] bg-black/20 p-2">
              <div className="mb-2 rounded-xl border border-white/[0.06] bg-white/[0.03] px-3 py-3">
                <div className="flex items-center justify-between gap-3">
                  <div className="min-w-0">
                    <p className={cn("text-xs font-semibold uppercase tracking-[0.24em]", theme.accentTextClass)}>
                      {section.label}
                    </p>
                    <p className="mt-1 text-[11px] text-white/40">{section.description}</p>
                  </div>
                  <div className="shrink-0 text-right">
                    <p className="text-sm font-semibold text-white">{sectionRaceCount}R</p>
                    <p className="text-[10px] text-white/35">{sectionMeetCount} meets</p>
                  </div>
                </div>
              </div>

              {section.days.length === 0 ? (
                <div className="px-3 py-4 text-center text-xs text-white/30">No saved meetings in this group</div>
              ) : (
                <div className="space-y-1">
                  {section.days.map((day) => {
                    const dateKey = `${section.id}__${day.date}`;
                    const dateOpen = expandedDates.has(dateKey);

                    return (
                      <div key={dateKey}>
                        <button
                          onClick={() => toggleDate(dateKey)}
                          className="flex w-full items-center justify-between rounded-xl px-3 py-2.5 text-left transition-colors hover:bg-white/[0.04]"
                        >
                          <div className="flex items-center gap-2 min-w-0">
                            <Clock3 className={cn("h-3.5 w-3.5 shrink-0", theme.accentTextClass)} />
                            <span className="text-sm font-semibold text-white truncate">
                              {formatRaceDate(day.date)}
                            </span>
                            <span className="text-[10px] text-white/30 shrink-0">
                              {day.raceCount}R
                            </span>
                          </div>
                          {dateOpen ? (
                            <ChevronDown className="h-3.5 w-3.5 shrink-0 text-white/30" />
                          ) : (
                            <ChevronRight className="h-3.5 w-3.5 shrink-0 text-white/30" />
                          )}
                        </button>

                        {dateOpen && day.meets?.map((meet) => {
                          const meetKey = `${section.id}__${day.date}__${meet.meet_id ?? meet.course}`;
                          const meetOpen = expandedMeets.has(meetKey);
                          return (
                            <div key={meetKey} className="ml-3 border-l border-white/[0.06] pl-2">
                              <button
                                onClick={() => toggleMeet(meetKey)}
                                className="flex w-full items-center justify-between rounded-lg px-2 py-2 text-left transition-colors hover:bg-white/[0.04]"
                              >
                                <div className="flex items-center gap-2 min-w-0">
                                  <MapPin className="h-3 w-3 shrink-0 text-white/40" />
                                  <span className="text-xs font-medium text-white/80 truncate">
                                    {meet.course}
                                  </span>
                                  <span className="text-[10px] text-white/25 shrink-0">
                                    {meet.races?.length ?? 0}R
                                  </span>
                                </div>
                                {meetOpen ? (
                                  <ChevronDown className="h-3 w-3 shrink-0 text-white/25" />
                                ) : (
                                  <ChevronRight className="h-3 w-3 shrink-0 text-white/25" />
                                )}
                              </button>

                              {meetOpen && meet.races?.map((race) => {
                                const raceKey = `${meetKey}__R${race.race_number}`;
                                const raceOpen = expandedRaces.has(raceKey);
                                const isActive =
                                  activeContext?.date === day.date &&
                                  activeContext?.track === meet.course &&
                                  String(activeContext?.raceNumber) === String(race.race_number);

                                return (
                                  <div key={raceKey} className="ml-2 border-l border-white/[0.04] pl-2">
                                    <div className={cn("flex items-center gap-1 rounded-lg", isActive && `${theme.accentSoftBgClass} ring-1 ${theme.accentBorderClass}`)}>
                                      <button
                                        onClick={() => handleRaceClick(day, meet, race)}
                                        className="flex flex-1 min-w-0 items-center gap-2 px-2 py-1.5 text-left group"
                                      >
                                        <span
                                          className={cn(
                                            "flex h-5 w-5 shrink-0 items-center justify-center rounded-md text-[10px] font-bold transition-colors",
                                            isActive
                                              ? `${theme.accentStrongBgClass} ${theme.accentTextClass}`
                                              : "bg-white/[0.06] text-white/60",
                                          )}
                                        >
                                          {race.race_number}
                                        </span>
                                        <span className={cn("truncate text-xs transition-colors", isActive ? theme.accentTextClass : "text-white/55 group-hover:text-white/90")}>
                                          {race.race_name || `Race ${race.race_number}`}
                                        </span>
                                      </button>
                                      <button
                                        title={isActive ? "Active context — click to clear" : "Set as chat context"}
                                        onClick={() =>
                                          isActive
                                            ? onSetContext(null)
                                            : onSetContext({
                                                date: day.date,
                                                track: meet.course,
                                                raceNumber: Number(race.race_number),
                                                raceName: race.race_name,
                                                distance: race.distance,
                                                going: race.going,
                                              })
                                        }
                                        className={cn("shrink-0 rounded p-1 transition-colors", isActive ? theme.accentTextClass : "text-white/20 hover:text-white")}
                                      >
                                        <Sparkles className="h-3 w-3" />
                                      </button>
                                      {race.runners && race.runners.length > 0 && (
                                        <button
                                          onClick={() => toggleRace(raceKey)}
                                          className="shrink-0 rounded p-1 text-white/20 hover:text-white/60 transition-colors"
                                        >
                                          {raceOpen ? (
                                            <ChevronDown className="h-3 w-3" />
                                          ) : (
                                            <ChevronRight className="h-3 w-3" />
                                          )}
                                        </button>
                                      )}
                                    </div>

                                    {(race.distance || race.going) && (
                                      <div className="ml-7 flex gap-1.5 pb-0.5">
                                        {race.distance && (
                                          <span className="text-[9px] text-white/25">{race.distance}</span>
                                        )}
                                        {race.going && (
                                          <span className="text-[9px] text-white/20">· {race.going}</span>
                                        )}
                                      </div>
                                    )}

                                    {raceOpen && race.runners && (
                                      <div className="ml-7 mb-1 space-y-0.5">
                                        {race.runners.slice(0, 12).map((runner, ri) => (
                                          <button
                                            key={ri}
                                            onClick={() =>
                                              onSelectRace(
                                                `Analyze ${runner.horse} in Race ${race.race_number} at ${meet.course} on ${day.date}`,
                                              )
                                            }
                                            className="flex w-full items-center gap-2 rounded px-1.5 py-1 text-left hover:bg-white/[0.04] transition-colors group"
                                          >
                                            <span className="w-4 text-[10px] text-white/25 text-center">
                                              {runner.number}
                                            </span>
                                            <Trophy className="h-2.5 w-2.5 shrink-0 text-white/15 group-hover:text-white/60 transition-colors" />
                                            <span className="truncate text-[11px] text-white/45 group-hover:text-white/80 transition-colors">
                                              {runner.horse}
                                            </span>
                                          </button>
                                        ))}
                                        {race.runners.length > 12 && (
                                          <p className="pl-1 text-[10px] text-white/20">
                                            +{race.runners.length - 12} more runners
                                          </p>
                                        )}
                                      </div>
                                    )}
                                  </div>
                                );
                              })}
                            </div>
                          );
                        })}
                      </div>
                    );
                  })}
                </div>
              )}
            </div>
          );
        })}
      </div>
    </ScrollArea>
  );
}

// ── Main Component ────────────────────────────────────────────────────────────

export default function ChatInterface() {
  const [initialState] = useState(getInitialChatState);
  const [sessions, setSessions] = useState<ChatSession[]>(initialState.sessions);
  const [currentSessionId, setCurrentSessionId] = useState(initialState.currentSessionId);
  const [input, setInput] = useState("");
  const [selectedMode, setSelectedMode] = useState<ChatModeState>({ brain: false, search: false });
  const [mobileSidebarOpen, setMobileSidebarOpen] = useState(false);
  const [mobileRacesOpen, setMobileRacesOpen] = useState(false);
  const [activeRaceContext, setActiveRaceContext] = useState<ActiveRaceContext | null>(null);
  const scrollRef = useRef<HTMLDivElement>(null);
  const shouldStickToBottomRef = useRef(true);

  const [currentQuestion, setCurrentQuestion] = useState<string | null>(null);
  const [pendingMode, setPendingMode] = useState<ChatModeState | null>(null);
  const [loadingStepIndex, setLoadingStepIndex] = useState(0);

  const sortedSessions = useMemo(
    () => [...sessions].sort((a, b) => b.updatedAt - a.updatedAt),
  [sessions],
  );

  const currentSession =
    sessions.find((s) => s.id === currentSessionId) ?? sortedSessions[0] ?? null;
  const messages = currentSession?.messages ?? [];
  const activeTheme = getChatModeTheme(selectedMode);

  useEffect(() => {
    if (!currentSession && sortedSessions[0]) setCurrentSessionId(sortedSessions[0].id);
  }, [currentSession, sortedSessions]);

  useEffect(() => {
    if (typeof window !== "undefined")
      window.localStorage.setItem(CHAT_STORAGE_KEY, JSON.stringify(sessions));
  }, [sessions]);

  useEffect(() => {
    if (typeof window !== "undefined" && currentSessionId)
      window.localStorage.setItem(ACTIVE_CHAT_STORAGE_KEY, currentSessionId);
  }, [currentSessionId]);

  const appendMessageToSession = (sessionId: string, message: Message) => {
    setSessions((prev) =>
      prev.map((session) => {
        if (session.id !== sessionId) return session;
        const isFirstUserMessage = message.role === "user" && session.messages.length === 0;
        return {
          ...session,
          title: isFirstUserMessage ? buildSessionTitle(message.content) : session.title,
          updatedAt: Date.now(),
          messages: [...session.messages, message],
        };
      }),
    );
  };

  const handleMessageFeedback = (messageId: string, rating: 1 | -1) => {
    setSessions((prev) =>
      prev.map((session) => {
        if (session.id !== currentSessionId) return session;
        return {
          ...session,
          messages: session.messages.map((m) =>
            m.id === messageId ? { ...m, feedbackRating: rating } : m,
          ),
        };
      }),
    );
  };

  const sendMessage = useMutation({
    mutationFn: async ({
      message,
      sessionId,
      raceCtx,
      mode,
    }: {
      message: string;
      sessionId: string;
      raceCtx: ActiveRaceContext | null;
      mode: ChatModeState;
    }) => {
      setCurrentQuestion(message);
      setPendingMode(mode);
      setLoadingStepIndex(0);

      const res = await apiRequest("POST", "/api/chat", {
        message,
        sessionId,
        modes: mode,
        raceContext: raceCtx
          ? {
              date: raceCtx.date,
              track: raceCtx.track,
              raceNumber: raceCtx.raceNumber,
              raceName: raceCtx.raceName,
              distance: raceCtx.distance,
              going: raceCtx.going,
            }
          : undefined,
      });
      return await readJsonResponse<ChatCompletionResponse>(res);
    },
    onSuccess: (data, variables) => {
      appendMessageToSession(variables.sessionId, {
        id: `${Date.now()}-assistant`,
        role: "assistant",
        content: data.response,
        mode: data.mode,
        trace: data.trace,
        citations: data.citations,
        warnings: data.warnings,
        answerSource: data.answerSource,
        promptVersion: data.promptVersion,
      });
      setPendingMode(null);
      setLoadingStepIndex(0);
      setCurrentQuestion(null);
    },
    onError: (error, variables) => {
      const errorMessage = error instanceof Error ? error.message : "";
      const fallback = errorMessage.includes("429")
        ? "You're sending messages too quickly. Wait a moment and try again."
        : errorMessage.includes("404")
          ? "The running app server does not have the new chat route yet. Restart the app backend and try Search again."
          : errorMessage.includes("500")
            ? "Stride couldn't complete the live search pipeline just then. Try the question again in a moment."
            : errorMessage.includes("Expected JSON but received text/html")
              ? "The app received the website shell instead of the chat API response. Reload the app and try again; if it persists, restart the backend."
              : errorMessage.includes("Failed to fetch")
                ? "The browser could not reach the chat backend. Check that the app server is running, then try again."
                : "Stride hit a chat error before it could answer. Try sending the question again.";
      appendMessageToSession(variables.sessionId, {
        id: `${Date.now()}-error`,
        role: "assistant",
        content: fallback,
        mode: variables.mode,
      });
      toast({
        title: "Stride chat hit an error",
        description: errorMessage ? errorMessage.slice(0, 180) : "The request failed before a response came back.",
      });
      setPendingMode(null);
      setLoadingStepIndex(0);
      setCurrentQuestion(null);
    },
  });

  useEffect(() => {
    if (!sendMessage.isPending || !pendingMode) return;
    const timer = window.setInterval(() => {
      setLoadingStepIndex((current) => current + 1);
    }, 1350);
    return () => window.clearInterval(timer);
  }, [pendingMode, sendMessage.isPending]);

  useEffect(() => {
    const vp = scrollRef.current?.querySelector("[data-radix-scroll-area-viewport]");
    if (!(vp instanceof HTMLElement)) return;

    const updateStickiness = () => {
      const distanceFromBottom = vp.scrollHeight - vp.clientHeight - vp.scrollTop;
      shouldStickToBottomRef.current = distanceFromBottom < 96;
    };

    updateStickiness();
    vp.addEventListener("scroll", updateStickiness, { passive: true });

    return () => vp.removeEventListener("scroll", updateStickiness);
  }, [currentSessionId, messages.length]);

  useEffect(() => {
    const vp = scrollRef.current?.querySelector("[data-radix-scroll-area-viewport]");
    if (!(vp instanceof HTMLElement) || !shouldStickToBottomRef.current) return;

    window.requestAnimationFrame(() => {
      vp.scrollTop = vp.scrollHeight;
    });
  }, [currentSessionId, messages.length, loadingStepIndex, pendingMode]);

  const handleSend = (message = input, files: File[] = []) => {
    if (!currentSession || sendMessage.isPending) return;
    const trimmed = message.trim();
    const attachment =
      files.length > 0
        ? `\n[Attached image${files.length > 1 ? "s" : ""}: ${files.map((f) => f.name).join(", ")}]`
        : "";
    const payload = `${trimmed}${attachment}`.trim();
    if (!payload) return;
    appendMessageToSession(currentSession.id, {
      id: `${Date.now()}-user`,
      role: "user",
      content: payload,
      mode: selectedMode,
    });
    sendMessage.mutate({ message: payload, sessionId: currentSession.id, raceCtx: activeRaceContext, mode: selectedMode });
    setInput("");
  };

  const handleCreateNewChat = () => {
    if (sendMessage.isPending) return;
    const next = createChatSession();
    setSessions((prev) => [next, ...prev]);
    setCurrentSessionId(next.id);
    setInput("");
    setMobileSidebarOpen(false);
  };

  const handleSelectSession = (sessionId: string) => {
    if (sendMessage.isPending) return;
    setCurrentSessionId(sessionId);
    setInput("");
    setMobileSidebarOpen(false);
  };

  const handleDeleteSession = (sessionId: string) => {
    if (sendMessage.isPending) return;

    let nextCurrentSessionId = currentSessionId;
    let createdReplacement = false;

    setSessions((prev) => {
      const remaining = prev.filter((session) => session.id !== sessionId);

      if (remaining.length === 0) {
        const nextSession = createChatSession();
        nextCurrentSessionId = nextSession.id;
        createdReplacement = true;
        return [nextSession];
      }

      if (currentSessionId === sessionId) {
        nextCurrentSessionId = [...remaining].sort((a, b) => b.updatedAt - a.updatedAt)[0].id;
      }

      return remaining;
    });

    if (currentSessionId === sessionId || createdReplacement) {
      setCurrentSessionId(nextCurrentSessionId);
      setInput("");
    }

    toast({
      title: "Chat deleted",
      description: "The conversation was removed from your sidebar history.",
    });
  };

  const handleSelectRace = (text: string) => {
    setInput(text);
    setMobileRacesOpen(false);
  };

  const handleSetContext = (ctx: ActiveRaceContext | null) => {
    setActiveRaceContext(ctx);
    if (ctx) {
      toast({
        title: `Context locked: ${ctx.track} Race ${ctx.raceNumber}`,
        description: "All messages will use live race data until you clear the context.",
      });
    }
  };

  const handleModeChange = (mode: ChatModeState) => {
    if (mode.search) {
      setSelectedMode({ brain: false, search: true });
      return;
    }

    if (mode.brain) {
      setSelectedMode({ brain: true, search: false });
      return;
    }

    setSelectedMode({ brain: false, search: false });
  };

  const activeModeLabel = activeTheme.label;

  // ── Render ─────────────────────────────────────────────────────────────────

  return (
    <div className="relative flex h-screen overflow-hidden bg-black font-dm" data-testid="page-chat">

      {/* Animated background */}
      <div className="pointer-events-none absolute inset-0 z-0">
        <GLSLHills className="h-full w-full" width="100%" height="100%" cameraZ={132} planeSize={256} speed={0.42} />
      </div>
      <div className={cn("pointer-events-none absolute inset-0 z-0 transition-all duration-300", activeTheme.glowClass)} />

      {/* ── LEFT: Chat history sidebar ────────────────────────────────────── */}
      <aside className="relative z-10 hidden w-80 shrink-0 flex-col border-r border-white/[0.06] lg:flex">
        <div className="shrink-0 border-b border-white/[0.06] px-4 py-4">
          <p className="text-[10px] uppercase tracking-[0.22em] text-white/30">Stride Workspace</p>
          <h2 className="mt-1 text-base font-semibold text-white">Previous Chats</h2>
          <button
            onClick={handleCreateNewChat}
            disabled={sendMessage.isPending}
            className={cn(
              "mt-3 flex w-full items-center gap-2 rounded-xl border px-3 py-2 text-sm transition-colors disabled:opacity-50",
              activeTheme.buttonClass,
            )}
          >
            <Plus className={cn("h-4 w-4", activeTheme.accentTextClass)} />
            New chat
          </button>
        </div>
        <ScrollArea className="flex-1 min-h-0">
          <div className="space-y-2 p-3">
            {sortedSessions.map((session) => {
              const isActive = session.id === currentSession?.id;
              return (
                <SessionListItem
                  key={session.id}
                  session={session}
                  isActive={isActive}
                  disabled={sendMessage.isPending}
                  theme={activeTheme}
                  onSelect={() => handleSelectSession(session.id)}
                  onDelete={() => handleDeleteSession(session.id)}
                />
              );
            })}
          </div>
        </ScrollArea>
      </aside>

      {/* ── CENTRE: Main chat area ────────────────────────────────────────── */}
      <div className="relative z-10 flex flex-1 flex-col min-w-0">

        {/* Header */}
        <header className="relative z-10 shrink-0 border-b border-white/[0.06] bg-black/40 px-4 py-3 backdrop-blur-md">
          <div className="flex items-center justify-between gap-4">
            <div className="flex items-center gap-3">
              <Link href="/">
                <button className="flex h-8 w-8 items-center justify-center rounded-lg border border-white/[0.08] bg-white/[0.04] text-white/60 transition-colors hover:bg-white/[0.08] hover:text-white">
                  <ArrowLeft className="h-4 w-4" />
                </button>
              </Link>
              {/* Mobile: open chat sidebar */}
              <button
                className="flex h-8 w-8 items-center justify-center rounded-lg border border-white/[0.08] bg-white/[0.04] text-white/60 transition-colors hover:bg-white/[0.08] hover:text-white lg:hidden"
                onClick={() => setMobileSidebarOpen(true)}
              >
                <PanelLeft className="h-4 w-4" />
              </button>
              <div className="flex items-center gap-2.5">
                <div className={cn("flex h-8 w-8 items-center justify-center rounded-lg border", activeTheme.accentBorderClass, activeTheme.accentSoftBgClass)}>
                  {selectedMode.search ? (
                    <Globe className={cn("h-4 w-4", activeTheme.accentTextClass)} />
                  ) : (
                    <Sparkles className={cn("h-4 w-4", activeTheme.accentTextClass)} />
                  )}
                </div>
                <div>
                  <h1 className="text-sm font-bold tracking-wide text-white font-syne">ASK STRIDE</h1>
                  <p className="text-[11px] text-white/30">{currentSession?.title ?? "New chat"}</p>
                </div>
              </div>
            </div>

            <div className="flex items-center gap-2">
              <div className={cn("hidden rounded-lg border px-2.5 py-1.5 text-xs font-medium md:flex", activeTheme.badgeClass)}>
                {activeModeLabel}
              </div>
              {/* Active race context badge */}
              {activeRaceContext && (
                <div className={cn("flex items-center gap-1.5 rounded-lg border px-2.5 py-1.5 text-xs", activeTheme.accentBorderClass, activeTheme.accentSoftBgClass)}>
                  <Sparkles className={cn("h-3 w-3 shrink-0", activeTheme.accentTextClass)} />
                  <span className={cn("font-medium truncate max-w-[160px]", activeTheme.accentTextClass)}>
                    {activeRaceContext.track} R{activeRaceContext.raceNumber}
                    {activeRaceContext.distance ? ` · ${activeRaceContext.distance}` : ""}
                    {activeRaceContext.going ? ` · ${activeRaceContext.going}` : ""}
                  </span>
                  <button
                    onClick={() => setActiveRaceContext(null)}
                    className={cn("ml-0.5 transition-colors hover:text-white", activeTheme.accentTextClass)}
                    title="Clear race context"
                  >
                    <X className="h-3 w-3" />
                  </button>
                </div>
              )}
              {/* Mobile: open races panel */}
              <button
                className="flex items-center gap-1.5 rounded-lg border border-white/[0.08] bg-white/[0.03] px-3 py-1.5 text-xs text-white/50 transition-colors hover:bg-white/[0.06] hover:text-white lg:hidden"
                onClick={() => setMobileRacesOpen(true)}
              >
                <Flag className={cn("h-3.5 w-3.5", activeTheme.accentTextClass)} />
                Races
              </button>
              <div className="hidden items-center gap-2 rounded-lg border border-white/[0.06] bg-white/[0.03] px-3 py-1.5 text-xs text-white/40 md:flex">
                <Clock3 className="h-3.5 w-3.5" />
                <span>{messages.length} messages</span>
              </div>
            </div>
          </div>
        </header>

        {/* Chat body */}
        <div className="relative flex-1 min-h-0">
          {messages.length === 0 ? (
            /* Empty state */
            <div className="flex h-full flex-col items-center justify-center gap-6 px-4 pb-28">
              <div className="text-center">
                <div className={cn("mx-auto mb-4 flex h-14 w-14 items-center justify-center rounded-2xl border", activeTheme.accentBorderClass, activeTheme.accentSoftBgClass)}>
                  {selectedMode.search ? (
                    <Globe className={cn("h-7 w-7", activeTheme.accentTextClass)} />
                  ) : selectedMode.brain ? (
                    <Brain className={cn("h-7 w-7", activeTheme.accentTextClass)} />
                  ) : (
                    <Sparkles className={cn("h-7 w-7", activeTheme.accentTextClass)} />
                  )}
                </div>
                <h2 className="mb-2 text-2xl font-bold text-white font-syne" data-testid="text-welcome">
                  {activeRaceContext
                    ? `${activeRaceContext.track} Race ${activeRaceContext.raceNumber}`
                    : "Racing Intelligence at Your Fingertips"}
                </h2>
                <p className="text-sm text-white/40">
                  {activeRaceContext
                    ? `${activeRaceContext.raceName ?? ""}${activeRaceContext.distance ? ` · ${activeRaceContext.distance}` : ""}${activeRaceContext.going ? ` · ${activeRaceContext.going}` : ""} — Live data loaded`
                    : getModeExperienceCopy(selectedMode)}
                </p>
              </div>
              <div className="grid w-full max-w-2xl grid-cols-1 gap-2 sm:grid-cols-2">
                {(activeRaceContext ? [
                  `Who is the best bet in this race and why?`,
                  `Break down the pace map and how the race will be run`,
                  `Which runners are luckless and deserve another chance?`,
                  `Compare the track bias fit for each runner`,
                  `Who has the best fitness profile in this field?`,
                  `What's the value play at current market odds?`,
                  `Recommend an exotic bet structure for this race`,
                  `Which jockey/trainer combinations stand out?`,
                ] : suggestedPrompts).map((prompt, i) => (
                  <button
                    key={i}
                    onClick={() => setInput(prompt)}
                    className={cn(
                      "rounded-xl border px-4 py-3 text-left text-sm transition-all",
                      activeRaceContext
                        ? `${activeTheme.accentBorderClass} ${activeTheme.accentSoftBgClass} text-white/75 hover:text-white`
                        : "border-white/[0.07] bg-white/[0.03] text-white/55 hover:text-white/90",
                    )}
                    data-testid={`card-prompt-${i}`}
                  >
                    {prompt}
                  </button>
                ))}
              </div>
              {activeRaceContext && (
                <p className="text-[11px] text-white/25">
                  Stride has access to the full field, model probabilities, track bias, fitness, luckless scores and AI insights for this race
                </p>
              )}
            </div>
          ) : (
            /* Active conversation */
            <ScrollArea className="h-full min-h-0" ref={scrollRef}>
              <div className="mx-auto max-w-3xl space-y-4 px-4 py-6 pb-40 md:pb-44">
                {messages.map((message) => (
                  <div
                    key={message.id}
                    className={`flex ${message.role === "user" ? "justify-end" : "justify-start"}`}
                    data-testid={`message-${message.role}-${message.id}`}
                  >
                    {message.role === "assistant" ? (
                      <AssistantMessageCard
                        message={message}
                        sessionId={currentSessionId}
                        onFeedback={handleMessageFeedback}
                      />
                    ) : (() => {
                      const theme = getChatModeTheme(message.mode);
                      return (
                        <div
                          className={cn(
                            "max-w-[88%] rounded-2xl border px-4 py-3 text-white backdrop-blur-sm transition-colors duration-300",
                            theme.userBubbleClass,
                          )}
                        >
                          <div className="mb-2 flex items-center gap-2">
                            {message.mode?.search ? (
                              <Globe className={cn("h-3.5 w-3.5", theme.accentTextClass)} />
                            ) : (
                              <Brain className={cn("h-3.5 w-3.5", theme.accentTextClass)} />
                            )}
                            <span className={cn("text-[11px] font-semibold uppercase tracking-[0.24em]", theme.accentTextClass)}>
                              {theme.label}
                            </span>
                          </div>
                          <p className="text-sm whitespace-pre-wrap">{message.content}</p>
                        </div>
                      );
                    })()}
                  </div>
                ))}

                {sendMessage.isPending && pendingMode && (
                  <div className="flex justify-start" data-testid="loading-response">
                    <LoadingTraceCard mode={pendingMode} question={currentQuestion} stepIndex={loadingStepIndex} />
                  </div>
                )}
              </div>
            </ScrollArea>
          )}
        </div>

        {/* Input — fixed bottom centre */}
        <div className="fixed bottom-0 left-0 right-0 z-20 bg-gradient-to-t from-black/80 to-transparent pb-5 pt-4 px-4 lg:left-80 xl:left-80 xl:right-72">
          {/* Race context bar — shown above input when active */}
          {activeRaceContext && (
            <div className={cn("mx-auto mb-2 flex w-full max-w-3xl items-center justify-between gap-3 rounded-xl border px-3 py-2 backdrop-blur-md", activeTheme.accentBorderClass, activeTheme.accentSoftBgClass)}>
              <div className="flex items-center gap-2 min-w-0">
                <Sparkles className={cn("h-3.5 w-3.5 shrink-0", activeTheme.accentTextClass)} />
                <span className={cn("text-xs font-medium truncate", activeTheme.accentTextClass)}>
                  Context: {activeRaceContext.track} Race {activeRaceContext.raceNumber}
                  {activeRaceContext.raceName ? ` — ${activeRaceContext.raceName}` : ""}
                </span>
                {activeRaceContext.distance && (
                  <span className="text-[11px] text-white/55 shrink-0">{activeRaceContext.distance}</span>
                )}
                {activeRaceContext.going && (
                  <span className="text-[11px] text-white/55 shrink-0">· {activeRaceContext.going}</span>
                )}
              </div>
              <div className="flex items-center gap-2 shrink-0">
                <span className="text-[10px] text-white/50">Local race context attached</span>
                <button
                  onClick={() => setActiveRaceContext(null)}
                  className={cn("transition-colors hover:text-white", activeTheme.accentTextClass)}
                >
                  <X className="h-3.5 w-3.5" />
                </button>
              </div>
            </div>
          )}
          <PromptInputBox
            value={input}
            onValueChange={setInput}
            mode={selectedMode}
            onModeChange={handleModeChange}
            onSend={handleSend}
            isLoading={sendMessage.isPending}
            placeholder={
              activeRaceContext
                ? `Ask anything about ${activeRaceContext.track} R${activeRaceContext.raceNumber}...`
                : "Ask about racing strategy, form analysis, or algorithm insights..."
            }
            className={cn(
              "mx-auto w-full max-w-3xl shadow-[0_8px_32px_rgba(0,0,0,0.4)] backdrop-blur-xl transition-colors duration-300",
              activeTheme.inputClass,
            )}
          />
        </div>
      </div>

      {/* ── RIGHT: Race reference sidebar ────────────────────────────────── */}
      <aside className="relative z-10 hidden w-72 shrink-0 flex-col border-l border-white/[0.06] xl:flex">
        <div className="shrink-0 border-b border-white/[0.06] px-4 py-4">
          <div className="flex items-center gap-2">
            <Flag className={cn("h-4 w-4", activeTheme.accentTextClass)} />
            <div>
              <p className="text-[10px] uppercase tracking-[0.22em] text-white/30">Race Reference</p>
              <h2 className="text-sm font-semibold text-white">Select a Race</h2>
            </div>
          </div>
          {activeRaceContext ? (
            <div className={cn("mt-2 flex items-center gap-1.5 rounded-lg border px-2.5 py-1.5", activeTheme.accentBorderClass, activeTheme.accentSoftBgClass)}>
              <Sparkles className={cn("h-3 w-3 shrink-0", activeTheme.accentTextClass)} />
              <span className={cn("text-[11px] truncate font-medium", activeTheme.accentTextClass)}>
                {activeRaceContext.track} R{activeRaceContext.raceNumber} active
              </span>
              <button onClick={() => setActiveRaceContext(null)} className="ml-auto text-white/45 hover:text-white">
                <X className="h-3 w-3" />
              </button>
            </div>
          ) : (
            <p className="mt-1.5 text-[11px] text-white/30">
              Lock a race to blend saved local context into Deep Thought or Search mode
            </p>
          )}
        </div>
        <div className="flex-1 min-h-0">
          <RaceSidebar onSelectRace={handleSelectRace} onSetContext={handleSetContext} activeContext={activeRaceContext} mode={selectedMode} />
        </div>
      </aside>

      {/* ── Mobile: chat history drawer ──────────────────────────────────── */}
      {mobileSidebarOpen && (
        <div className="fixed inset-0 z-50 lg:hidden">
          <div className="absolute inset-0 bg-black/70 backdrop-blur-sm" onClick={() => setMobileSidebarOpen(false)} />
          <div className="absolute inset-y-0 left-0 flex w-72 flex-col border-r border-white/[0.08] bg-black/95 backdrop-blur-xl">
            <div className="flex items-center justify-between border-b border-white/[0.06] px-4 py-4">
              <h2 className="font-semibold text-white">Previous Chats</h2>
              <button onClick={() => setMobileSidebarOpen(false)} className="text-white/40 hover:text-white">
                <X className="h-5 w-5" />
              </button>
            </div>
            <button onClick={handleCreateNewChat} className={cn("mx-3 mt-3 flex items-center gap-2 rounded-xl border px-3 py-2 text-sm transition-colors", activeTheme.buttonClass)}>
              <Plus className={cn("h-4 w-4", activeTheme.accentTextClass)} /> New chat
            </button>
            <ScrollArea className="flex-1 min-h-0 mt-2">
              <div className="space-y-2 p-3">
                {sortedSessions.map((session) => {
                  const isActive = session.id === currentSession?.id;
                  return (
                    <SessionListItem
                      key={session.id}
                      session={session}
                      isActive={isActive}
                      disabled={sendMessage.isPending}
                      theme={activeTheme}
                      compact
                      onSelect={() => handleSelectSession(session.id)}
                      onDelete={() => handleDeleteSession(session.id)}
                    />
                  );
                })}
              </div>
            </ScrollArea>
          </div>
        </div>
      )}

      {/* ── Mobile: race reference drawer ────────────────────────────────── */}
      {mobileRacesOpen && (
        <div className="fixed inset-0 z-50 xl:hidden">
          <div className="absolute inset-0 bg-black/70 backdrop-blur-sm" onClick={() => setMobileRacesOpen(false)} />
          <div className="absolute inset-y-0 right-0 flex w-72 flex-col border-l border-white/[0.08] bg-black/95 backdrop-blur-xl">
            <div className="flex items-center justify-between border-b border-white/[0.06] px-4 py-4">
              <div className="flex items-center gap-2">
                <Flag className={cn("h-4 w-4", activeTheme.accentTextClass)} />
                <h2 className="font-semibold text-white">Race Reference</h2>
              </div>
              <button onClick={() => setMobileRacesOpen(false)} className="text-white/40 hover:text-white">
                <X className="h-5 w-5" />
              </button>
            </div>
            <div className="flex-1 min-h-0">
              <RaceSidebar onSelectRace={handleSelectRace} onSetContext={handleSetContext} activeContext={activeRaceContext} mode={selectedMode} />
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
