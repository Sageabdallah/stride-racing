import Anthropic from "@anthropic-ai/sdk";
import * as Sentry from "@sentry/node";
import {
  type ChatAnswerSource,
  type ChatCitationLink,
  type ChatCompletionRequest,
  type ChatCompletionResponse,
  type ChatModeState,
  type ChatSearchQueryTrace,
  type ChatSearchResultTrace,
  type ChatTrace,
  type ChatTraceSection,
} from "@shared/schema";
import {
  buildLocalStrideResponse,
  extractBestTip,
  extractConsensusLeader,
  extractMarketLeader,
  extractRaceMeta,
  getStrideSessionHistory,
  pickEvidence,
  startStrideTurn,
  type StrideChatTurn,
} from "./strideChatService";
import {
  buildConceptualStrideResponse,
  buildConceptualStrideTrace,
  identifyConceptualStrideQuestion,
} from "./strideConceptualChat";
import { STRIDE_LOCAL_SYNTHESIS_PROMPT, STRIDE_PROMPT_VERSION, getTrackProfile } from "./stridePrompts";
import type { StrideIntent } from "./strideChatRetrieval";
import { fetchLiveRaceCard, hasRacingApiCreds } from "./racingApiClient";
import { CLAUDE_MODEL } from "./claudeConfig";

const anthropic = new Anthropic({
  apiKey: process.env.ANTHROPIC_API_KEY ?? "anthropic-key-not-configured",
});

const hasAnthropicApiKey = Boolean(process.env.ANTHROPIC_API_KEY?.trim());
const hasPerplexityApiKey = Boolean(process.env.PERPLEXITY_API_KEY?.trim());
const PERPLEXITY_SEARCH_ENDPOINT = "https://api.perplexity.ai/search";

const AUSTRALIAN_TRACKS = [
  "randwick",
  "rosehill",
  "caulfield",
  "flemington",
  "eagle farm",
  "doomben",
  "moonee valley",
  "warwick farm",
  "sandown",
  "ascot",
  "morphettville",
];

const LOCAL_SOURCE_LABELS: Record<string, string> = {
  local_racecard: "saved racecards",
  local_tips: "saved tips",
  local_tip_results: "saved tip results",
  local_consensus: "consensus files",
  local_market_signals: "market signal files",
  local_intelligence: "track intelligence files",
  race_results_history: "race results history",
  sectional_times: "sectional times",
  franking_scores: "franking scores",
  prediction_audit: "prediction audit rows",
  stride_tip_results: "Stride tip results",
  race_analyses: "race analyses",
  horse_race_analyses: "horse analyses",
  races: "race database rows",
  selections: "selection rows",
  selection_results: "selection result rows",
  blackbook_entries: "blackbook entries",
  blackbook_entry_runs: "blackbook runs",
  performance_stats: "performance snapshots",
};

interface SearchPlan {
  objective: string;
  thinkingSummary: string;
  steps: string[];
  sourcesToCheck: string[];
  searchQueries: ChatSearchQueryTrace[];
}

interface SearchSelection {
  id: string;
  reason: string;
  extracted: string;
}

interface ClaudeSynthesisPayload {
  answer: string;
  headline: string;
  summary: string;
  final_why: string;
  sections: Array<{
    kind: ChatTraceSection["kind"];
    title: string;
    summary: string;
    bullets: string[];
  }>;
  selected_results: SearchSelection[];
  warnings?: string[];
}

interface SearchDocument {
  resultId: string;
  title: string;
  url: string;
  excerpt: string;
}

interface PerplexitySearchResult {
  title: string;
  url: string;
  snippet?: string;
  date?: string;
  last_updated?: string;
}

interface PerplexitySearchResponse {
  id?: string;
  results: PerplexitySearchResult[] | PerplexitySearchResult[][];
}

interface NormalizedQuestion {
  message: string;
  assumptions: string[];
}

type TipCandidateBetType = "best-bet" | "top-pick" | "each-way" | "value" | "selection";

interface TipCandidate {
  id: string;
  horse: string;
  betType: TipCandidateBetType;
  evidence: string;
  resultId: string;
  resultTitle: string;
  resultUrl: string;
  score: number;
  raceNumber?: number;
  horseNumber?: number;
  meetingRaceCount?: number;
}

export interface OrchestratedChatTurn extends ChatCompletionResponse {
  localTurnId?: string;
}

const TRACK_NORMALIZATION_ENTRIES = [
  {
    canonical: "Hawkesbury",
    region: "NSW",
    aliases: ["hawkesbury", "hawksberry", "hawkesbury", "hawkesburry", "hawkesbery"],
  },
  { canonical: "Randwick", region: "NSW", aliases: ["randwick", "randwik"] },
  { canonical: "Rosehill", region: "NSW", aliases: ["rosehill", "rose hil"] },
  { canonical: "Warwick Farm", region: "NSW", aliases: ["warwick farm", "warick farm"] },
  { canonical: "Canterbury", region: "NSW", aliases: ["canterbury", "canterburry"] },
  { canonical: "Kembla Grange", region: "NSW", aliases: ["kembla grange", "kembla"] },
  { canonical: "Wyong", region: "NSW", aliases: ["wyong"] },
  { canonical: "Gosford", region: "NSW", aliases: ["gosford"] },
  { canonical: "Newcastle", region: "NSW", aliases: ["newcastle"] },
  { canonical: "Flemington", region: "VIC", aliases: ["flemington", "fleminton"] },
  { canonical: "Caulfield", region: "VIC", aliases: ["caulfield", "caulfied"] },
  { canonical: "Moonee Valley", region: "VIC", aliases: ["moonee valley", "moonie valley"] },
  { canonical: "Sandown", region: "VIC", aliases: ["sandown"] },
  { canonical: "Eagle Farm", region: "QLD", aliases: ["eagle farm"] },
  { canonical: "Doomben", region: "QLD", aliases: ["doomben"] },
  { canonical: "Ascot", region: "WA", aliases: ["ascot"] },
  { canonical: "Belmont", region: "WA", aliases: ["belmont"] },
  { canonical: "Morphettville", region: "SA", aliases: ["morphettville", "morphetville"] },
];

const MONTH_PATTERN =
  "(january|february|march|april|may|june|july|august|september|october|november|december)";

const NUMBER_WORDS: Record<string, number> = {
  one: 1,
  two: 2,
  three: 3,
  four: 4,
  five: 5,
  six: 6,
  seven: 7,
  eight: 8,
  nine: 9,
  ten: 10,
  eleven: 11,
  twelve: 12,
};

const COMMON_TIP_STOPWORDS = new Set([
  "and",
  "april",
  "august",
  "bet",
  "betting",
  "best",
  "december",
  "february",
  "friday",
  "hawkesbury",
  "horse",
  "january",
  "july",
  "june",
  "last",
  "march",
  "monday",
  "november",
  "october",
  "before",
  "preview",
  "race",
  "racing",
  "sale",
  "saturday",
  "search",
  "september",
  "source",
  "sports",
  "sunday",
  "thursday",
  "tips",
  "top",
  "track",
  "tuesday",
  "wednesday",
  "you",
]);

const TIP_PATTERNS: Array<{ betType: TipCandidateBetType; regex: RegExp; weight: number }> = [
  {
    betType: "best-bet",
    regex:
      /(?:best bet(?!s)|best of the day|top bet(?!s)|main bet)(?:\s*(?:is|:|-|on))\s*([A-Z][A-Za-z'’.-]*(?:\s+[A-Z][A-Za-z'’.-]*){0,3})/gi,
    weight: 100,
  },
  {
    betType: "top-pick",
    regex:
      /(?:top pick(?!s)|top selection(?!s)|main pick)(?:\s*(?:is|:|-|on))\s*([A-Z][A-Za-z'’.-]*(?:\s+[A-Z][A-Za-z'’.-]*){0,3})/gi,
    weight: 90,
  },
  {
    betType: "each-way",
    regex:
      /each[- ]way(?:\s*(?:play|bet|selection))?(?:\s*(?:is|:|-|on))\s*([A-Z][A-Za-z'’.-]*(?:\s+[A-Z][A-Za-z'’.-]*){0,3})/gi,
    weight: 82,
  },
  {
    betType: "value",
    regex:
      /value(?:\s*(?:play|bet|selection))?(?:\s*(?:is|:|-|on))\s*([A-Z][A-Za-z'’.-]*(?:\s+[A-Z][A-Za-z'’.-]*){0,3})/gi,
    weight: 72,
  },
  {
    betType: "selection",
    regex:
      /([A-Z][A-Za-z'’.-]*(?:\s+[A-Z][A-Za-z'’.-]*){0,3})\s+(?:looks|rates|profiles)\s+(?:the\s+)?bet\b/gi,
    weight: 68,
  },
];

function normalizeModes(modes?: Partial<ChatModeState>): ChatModeState {
  return {
    brain: Boolean(modes?.brain),
    search: Boolean(modes?.search),
  };
}

function isAustralianRacingQuery(message: string, track?: string): boolean {
  const lower = `${message} ${track ?? ""}`.toLowerCase();
  return AUSTRALIAN_TRACKS.some((name) => lower.includes(name)) || /\brace\b|\bhorse\b|\bracing\b|\bjockey\b|\btrack\b/.test(lower);
}

function toDomain(url: string): string {
  try {
    return new URL(url).hostname.replace(/^www\./, "");
  } catch {
    return url;
  }
}

function extractText(response: Anthropic.Messages.Message): string {
  return response.content
    .filter((block) => block.type === "text")
    .map((block) => block.text)
    .join("\n")
    .trim();
}

function extractJsonObject<T>(text: string): T | null {
  const withoutFences = text.replace(/```json|```/g, "").trim();
  const firstBrace = withoutFences.indexOf("{");
  const lastBrace = withoutFences.lastIndexOf("}");
  if (firstBrace === -1 || lastBrace === -1 || lastBrace <= firstBrace) {
    return null;
  }

  try {
    return JSON.parse(withoutFences.slice(firstBrace, lastBrace + 1)) as T;
  } catch {
    return null;
  }
}

function formatLocalEvidence(turn: StrideChatTurn, limit = 5): string[] {
  return turn.bundle.evidence.slice(0, limit).map((item) => {
    const scope = [item.raceDate, item.track, item.raceNumber ? `R${item.raceNumber}` : null, item.horseName]
      .filter(Boolean)
      .join(" • ");
    return `${item.title}${scope ? ` — ${scope}` : ""}: ${item.summary}`;
  });
}

function uniqueStrings(values: string[]): string[] {
  return [...new Set(values.filter(Boolean))];
}

function escapeRegExp(value: string): string {
  return value.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}

function decodeHtmlEntities(value: string): string {
  return value
    .replace(/&nbsp;/gi, " ")
    .replace(/&amp;/gi, "&")
    .replace(/&quot;/gi, '"')
    .replace(/&#39;|&apos;/gi, "'")
    .replace(/&lt;/gi, "<")
    .replace(/&gt;/gi, ">")
    .replace(/&#(\d+);/g, (_, code) => String.fromCharCode(Number(code)));
}

function stripHtmlToText(html: string): string {
  return decodeHtmlEntities(
    html
      .replace(/<script[\s\S]*?<\/script>/gi, " ")
      .replace(/<style[\s\S]*?<\/style>/gi, " ")
      .replace(/<noscript[\s\S]*?<\/noscript>/gi, " ")
      .replace(/<!--[\s\S]*?-->/g, " ")
      .replace(/<[^>]+>/g, " ")
      .replace(/\s+/g, " "),
  ).trim();
}

function getSydneyNowParts(): { isoDate: string; year: string } {
  const now = new Date();
  const isoDate = new Intl.DateTimeFormat("en-CA", {
    timeZone: "Australia/Sydney",
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
  }).format(now);
  const year = new Intl.DateTimeFormat("en-AU", {
    timeZone: "Australia/Sydney",
    year: "numeric",
  }).format(now);

  return { isoDate, year };
}

function levenshteinDistance(a: string, b: string): number {
  if (a === b) return 0;
  if (a.length === 0) return b.length;
  if (b.length === 0) return a.length;

  const rows = Array.from({ length: a.length + 1 }, () => new Array<number>(b.length + 1).fill(0));
  for (let i = 0; i <= a.length; i += 1) rows[i][0] = i;
  for (let j = 0; j <= b.length; j += 1) rows[0][j] = j;

  for (let i = 1; i <= a.length; i += 1) {
    for (let j = 1; j <= b.length; j += 1) {
      const cost = a[i - 1] === b[j - 1] ? 0 : 1;
      rows[i][j] = Math.min(
        rows[i - 1][j] + 1,
        rows[i][j - 1] + 1,
        rows[i - 1][j - 1] + cost,
      );
    }
  }

  return rows[a.length][b.length];
}

function normalizeTrackMentions(message: string): NormalizedQuestion {
  let normalized = message;
  const assumptions: string[] = [];
  const lower = normalized.toLowerCase();

  for (const entry of TRACK_NORMALIZATION_ENTRIES) {
    for (const alias of entry.aliases) {
      if (alias === entry.canonical.toLowerCase()) continue;
      const pattern = new RegExp(`\\b${escapeRegExp(alias)}\\b`, "i");
      if (pattern.test(lower)) {
        normalized = normalized.replace(pattern, entry.canonical);
        assumptions.push(`Interpreted "${alias}" as ${entry.canonical}${entry.region ? ` (${entry.region})` : ""}.`);
        return { message: normalized, assumptions: uniqueStrings(assumptions) };
      }
    }
  }

  const words = [...normalized.matchAll(/[A-Za-z]+/g)].map((match) => ({
    text: match[0],
    index: match.index ?? 0,
    end: (match.index ?? 0) + match[0].length,
  }));

  let best:
    | {
        start: number;
        end: number;
        canonical: string;
        region?: string;
        score: number;
      }
    | undefined;

  for (let start = 0; start < words.length; start += 1) {
    for (let length = 1; length <= 2 && start + length <= words.length; length += 1) {
      const slice = words.slice(start, start + length);
      const phrase = normalized.slice(slice[0].index, slice[slice.length - 1].end).toLowerCase();
      if (phrase.length < 5) continue;

      for (const entry of TRACK_NORMALIZATION_ENTRIES) {
        const candidates = uniqueStrings([entry.canonical.toLowerCase(), ...entry.aliases]);
        for (const candidate of candidates) {
          const distance = levenshteinDistance(phrase, candidate);
          const threshold = candidate.length >= 9 ? 2 : 1;
          if (distance > threshold) continue;
          if (phrase === entry.canonical.toLowerCase()) continue;

          if (!best || distance < best.score) {
            best = {
              start: slice[0].index,
              end: slice[slice.length - 1].end,
              canonical: entry.canonical,
              region: entry.region,
              score: distance,
            };
          }
        }
      }
    }
  }

  if (best) {
    normalized = `${normalized.slice(0, best.start)}${best.canonical}${normalized.slice(best.end)}`;
    assumptions.push(`Interpreted that track reference as ${best.canonical}${best.region ? ` (${best.region})` : ""}.`);
  }

  return { message: normalized, assumptions: uniqueStrings(assumptions) };
}

function normalizeYearlessDateMentions(message: string): NormalizedQuestion {
  const assumptions: string[] = [];
  const { year } = getSydneyNowParts();
  if (/\b20\d{2}\b/.test(message)) {
    return { message, assumptions };
  }

  let normalized = message;
  const patterns = [
    new RegExp(`\\b(?:on\\s+)?the\\s+(\\d{1,2})(?:st|nd|rd|th)?\\s+of\\s+${MONTH_PATTERN}\\b(?!\\s+\\d{4})`, "i"),
    new RegExp(`\\b${MONTH_PATTERN}\\s+(\\d{1,2})(?:st|nd|rd|th)?\\b(?!,?\\s+\\d{4})`, "i"),
  ];

  for (const pattern of patterns) {
    const match = normalized.match(pattern);
    if (!match) continue;
    const original = match[0];
    if (original.includes(year)) continue;
    normalized = normalized.replace(original, `${original} ${year}`);
    assumptions.push(`Assumed the year ${year} for "${original}".`);
    break;
  }

  return { message: normalized, assumptions: uniqueStrings(assumptions) };
}

function normalizeQuestion(message: string): NormalizedQuestion {
  const trackNormalized = normalizeTrackMentions(message);
  const dateNormalized = normalizeYearlessDateMentions(trackNormalized.message);
  return {
    message: dateNormalized.message,
    assumptions: uniqueStrings([...trackNormalized.assumptions, ...dateNormalized.assumptions]),
  };
}

function inferTrackFromMessage(message: string): string | null {
  const lower = message.toLowerCase();
  for (const entry of TRACK_NORMALIZATION_ENTRIES) {
    if (lower.includes(entry.canonical.toLowerCase())) {
      return entry.canonical;
    }
  }
  return null;
}

function defaultSearchPlan(input: ChatCompletionRequest, localTurn?: StrideChatTurn | null): SearchPlan {
  const context = input.raceContext;
  // Extract track/date from free-text message when no raceContext
  const msgLower = input.message.toLowerCase();
  const trackMatch = msgLower.match(/\b(randwick|flemington|caulfield|rosehill|eagle farm|doomben|morphettville|moonee valley|sandown|hawkesbury|kembla|gold coast)\b/);
  const dateMatch = msgLower.match(/\b(jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|jun(?:e)?|jul(?:y)?|aug(?:ust)?|sep(?:tember)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)\s+\d{1,2}\b|\b(monday|tuesday|wednesday|thursday|friday|saturday|sunday)\b|\btoday\b|\btomorrow\b/);
  const detectedTrack = context?.track ?? (trackMatch ? trackMatch[0] : null);
  const detectedDate = context?.date ?? (dateMatch ? dateMatch[0] : null);

  // Detect whether the race is in the future vs today/past to choose better query templates
  const { isoDate: todayIso } = getSydneyNowParts();
  const resolvedDate = context?.date ?? detectedDate ?? "";
  const isFuture = resolvedDate > todayIso;

  let primaryQuery: string;
  let secondaryQuery: string;
  let tertiaryQuery: string;

  if (detectedTrack && detectedDate) {
    if (isFuture) {
      // Future race: tips not published yet — search for declared field, barriers, and early market
      primaryQuery = `${detectedTrack} ${detectedDate} 2026 barrier draw final field declared runners`;
      secondaryQuery = `${detectedTrack} ${detectedDate} 2026 best bets form guide early market TAB previews`;
      tertiaryQuery = `${detectedTrack} ${detectedDate} 2026 early odds favourites punters racenet preview`;
    } else {
      // Race day or past: tips and results should be available
      primaryQuery = `${detectedTrack} best bets tips selections ${detectedDate} 2026 racing`;
      secondaryQuery = `${detectedTrack} race card barriers form guide ${detectedDate} 2026 preview`;
      tertiaryQuery = `${detectedTrack} ${detectedDate} 2026 odds favourites TAB racenet punters`;
    }
  } else if (context) {
    primaryQuery = `${context.track} race ${context.raceNumber} ${context.date} ${context.raceName ?? ""} tips best bet`;
    secondaryQuery = `${context.track} race ${context.raceNumber} ${context.date} early odds market preview`;
    tertiaryQuery = `${context.track} ${context.date} barrier draw runners jockey bookings`;
  } else {
    primaryQuery = input.message;
    secondaryQuery = `${input.message} expert tips analysis`;
    tertiaryQuery = `${input.message} form guide runners`;
  }

  const localSources = localTurn
    ? uniqueStrings(localTurn.retrieval.sourcesUsed.map((source) => LOCAL_SOURCE_LABELS[source] ?? source)).slice(0, 4)
    : [];

  return {
    objective: context
      ? `Answer the question using live web coverage for ${context.track} R${context.raceNumber} on ${context.date}.`
      : "Answer the question with current web-grounded information and clear supporting links.",
    thinkingSummary: context
      ? "Focus the search on race-day previews, market movement, and analyst coverage that match the exact race."
      : "Search the live web for the strongest current sources, then synthesize them into a clean answer.",
    steps: [
      "Translate the question into a few precise search queries.",
      "Pull ranked live web results from Perplexity.",
      "Select the strongest results and synthesize them into a concise answer.",
    ],
    sourcesToCheck: uniqueStrings([
      ...localSources,
      context ? "live web search via Perplexity" : "live web sources",
      "Claude synthesis",
    ]),
    searchQueries: uniqueStrings([primaryQuery, secondaryQuery, tertiaryQuery]).map((query, index) => ({
      query,
      rationale:
        index === 0
          ? "Primary query matching the user request."
          : index === 1
            ? "Checks pace, market, or supporting analysis."
            : "Adds secondary source coverage.",
    })),
  };
}

async function buildSearchPlan(input: ChatCompletionRequest, localTurn?: StrideChatTurn | null): Promise<SearchPlan> {
  const fallback = defaultSearchPlan(input, localTurn);
  if (!hasAnthropicApiKey) {
    return fallback;
  }
  const { isoDate } = getSydneyNowParts();

  const localSummary = localTurn
    ? [
        `Local answer draft: ${buildLocalStrideResponse(localTurn.bundle)}`,
        `Local evidence summary: ${localTurn.bundle.summary}`,
        ...formatLocalEvidence(localTurn, 4).map((line) => `- ${line}`),
      ].join("\n")
    : "No local racing evidence was injected for this turn.";

  const prompt = [
    "Create a concise public-facing search plan for a horse racing chat assistant.",
    "Do not reveal hidden chain-of-thought. Return only a short, user-friendly planning summary in JSON.",
    "",
    `Question: ${input.message}`,
    `Current mode: brain=${Boolean(input.modes?.brain)}, search=${Boolean(input.modes?.search)}`,
    `Current date: ${isoDate}`,
    input.raceContext
      ? `Race context: ${input.raceContext.track} R${input.raceContext.raceNumber} on ${input.raceContext.date}${input.raceContext.raceName ? ` — ${input.raceContext.raceName}` : ""}`
      : "Race context: none",
    "",
    "[LOCAL CONTEXT]",
    localSummary,
    "",
    "Return valid JSON only with this shape:",
    `{
  "objective": "one sentence",
  "thinkingSummary": "one short sentence explaining what the model is focusing on",
  "steps": ["3 short bullets max"],
  "sourcesToCheck": ["short list of likely source/tool categories"],
  "searchQueries": [
    { "query": "specific web search query", "rationale": "why this query matters" }
  ]
}`,
    "Rules:",
    "- Create 2 to 3 search queries.",
    "- Use exact dates, track names, race numbers, or entities when available.",
    "- Keep every field concise and user-friendly.",
    "- For upcoming races (1-7 days away) where day-of tips may not yet be published, generate queries that find: race entries and barrier draws, recent form of leading contenders, early market prices or pre-post favourites, trainer/jockey bookings, and expert previews or early assessments. Do not only search for 'tips' — search for the underlying form and market data that an expert would use.",
    "- Always include at least one query for the race card/entries and one for early market or expert preview.",
    "- Australian racing sources include: racenet.com.au, tab.com.au, racing.com, punters.com.au, form.racingaustralia.horse, bettingodds.com.au, sportingbet, betfair.",
  ].join("\n");

  try {
    const response = await anthropic.messages.create({
      model: CLAUDE_MODEL,
      max_tokens: 900,
      temperature: 0,
      system: "You plan search queries for a chat assistant. Return valid JSON only.",
      messages: [{ role: "user", content: prompt }],
    });
    const parsed = extractJsonObject<SearchPlan>(extractText(response));
    if (!parsed || !Array.isArray(parsed.searchQueries) || parsed.searchQueries.length === 0) {
      return fallback;
    }

    return {
      objective: parsed.objective || fallback.objective,
      thinkingSummary: parsed.thinkingSummary || fallback.thinkingSummary,
      steps: Array.isArray(parsed.steps) && parsed.steps.length > 0 ? parsed.steps.slice(0, 4) : fallback.steps,
      sourcesToCheck:
        Array.isArray(parsed.sourcesToCheck) && parsed.sourcesToCheck.length > 0
          ? parsed.sourcesToCheck.slice(0, 6)
          : fallback.sourcesToCheck,
      searchQueries: parsed.searchQueries.slice(0, 3).map((query, index) => ({
        query: query.query?.trim() || fallback.searchQueries[index]?.query || fallback.searchQueries[0].query,
        rationale: query.rationale?.trim() || fallback.searchQueries[index]?.rationale,
      })),
    };
  } catch (error) {
    Sentry.captureException(error, { tags: { component: "chat_synthesis", type: "search_plan" } });
    return fallback;
  }
}

async function runPerplexitySearch(queries: ChatSearchQueryTrace[], country?: string): Promise<ChatSearchResultTrace[]> {
  if (!hasPerplexityApiKey || queries.length === 0) {
    return [];
  }

  const response = await fetch(PERPLEXITY_SEARCH_ENDPOINT, {
    method: "POST",
    headers: {
      Authorization: `Bearer ${process.env.PERPLEXITY_API_KEY}`,
      "Content-Type": "application/json",
    },
    body: JSON.stringify({
      query: queries.length === 1 ? queries[0].query : queries.map((entry) => entry.query),
      max_results: 8,
      max_tokens: 20000,
      max_tokens_per_page: 2500,
      search_language_filter: ["en"],
      search_recency_filter: "month",
      ...(country ? { country } : {}),
    }),
  });

  if (!response.ok) {
    const text = await response.text();
    throw new Error(`Perplexity search failed: ${response.status} ${text}`);
  }

  const payload = (await response.json()) as PerplexitySearchResponse;
  const groupedResults = Array.isArray(payload.results[0])
    ? (payload.results as PerplexitySearchResult[][])
    : [payload.results as PerplexitySearchResult[]];

  const seenUrls = new Set<string>();
  const traces: ChatSearchResultTrace[] = [];

  groupedResults.forEach((group, groupIndex) => {
    const query = queries[groupIndex]?.query ?? queries[0]?.query ?? "Search query";
    group.forEach((result, resultIndex) => {
      if (!result?.url || seenUrls.has(result.url)) {
        return;
      }
      seenUrls.add(result.url);
      traces.push({
        id: `q${groupIndex + 1}-r${resultIndex + 1}`,
        title: result.title || result.url,
        url: result.url,
        domain: toDomain(result.url),
        query,
        snippet: result.snippet?.trim() || "No snippet returned from search.",
        selected: false,
      });
    });
  });

  return traces;
}

async function fetchResultExcerpt(url: string): Promise<string | null> {
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), 8000);

  try {
    const response = await fetch(url, {
      headers: {
        "User-Agent":
          "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/135.0.0.0 Safari/537.36",
        Accept: "text/html,application/xhtml+xml",
      },
      redirect: "follow",
      signal: controller.signal,
    });

    if (!response.ok) {
      return null;
    }

    const contentType = response.headers.get("content-type") || "";
    if (!contentType.includes("text/html")) {
      return null;
    }

    const html = await response.text();
    const text = stripHtmlToText(html);
    if (!text) {
      return null;
    }

    return text.slice(0, 5000);
  } catch {
    return null;
  } finally {
    clearTimeout(timeout);
  }
}

async function fetchSearchDocuments(searchResults: ChatSearchResultTrace[], limit = 6): Promise<SearchDocument[]> {
  const prioritizedResults = searchResults.slice(0, limit);
  const documents = await Promise.all(
    prioritizedResults.map(async (result) => {
      const excerpt = await fetchResultExcerpt(result.url);
      if (!excerpt) {
        return null;
      }

      return {
        resultId: result.id,
        title: result.title,
        url: result.url,
        excerpt,
      } satisfies SearchDocument;
    }),
  );

  return documents.filter((document): document is SearchDocument => Boolean(document));
}

function cleanTipHorseName(raw: string): string | null {
  const cleaned = raw
    .replace(/^[^A-Za-z]+/, "")
    .replace(/[^A-Za-z'’.\-\s]+$/g, "")
    .replace(/\s+(?:in|for|at|from|on|with|after|before|over|under)\b.*$/i, "")
    .replace(/\s+/g, " ")
    .trim();

  if (!cleaned) {
    return null;
  }

  if (cleaned.length < 2) {
    return null;
  }

  const words = cleaned.split(" ");
  if (words.length > 4) {
    return null;
  }

  if (words[0] && words[0].length < 2) {
    return null;
  }

  if (words.every((word) => COMMON_TIP_STOPWORDS.has(word.toLowerCase()))) {
    return null;
  }

  const lower = cleaned.toLowerCase();
  if (COMMON_TIP_STOPWORDS.has(lower)) {
    return null;
  }

  return cleaned;
}

function normalizeLooseName(value: string): string {
  return value.toLowerCase().replace(/[^a-z0-9]+/g, "");
}

function parseMeetingRaceCount(text: string): number | null {
  const numericMatch = text.match(/\b(\d{1,2})[- ]race card\b/i);
  if (numericMatch) {
    return Number(numericMatch[1]);
  }

  const wordMatch = text.match(
    /\b(one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve)[- ]race card\b/i,
  );
  if (wordMatch) {
    return NUMBER_WORDS[wordMatch[1].toLowerCase()] ?? null;
  }

  return null;
}

function parseRaceNumberFromText(text: string, meetingRaceCount?: number | null): number | null {
  const explicitMatch = text.match(/\brace\s+(\d{1,2})\b/i);
  if (explicitMatch) {
    return Number(explicitMatch[1]);
  }

  if (meetingRaceCount && /\bin the last/i.test(text)) {
    return meetingRaceCount;
  }

  if (/\bin the opener\b/i.test(text)) {
    return 1;
  }

  return null;
}

function tipImpliesLastRace(candidate: TipCandidate): boolean {
  return /\bin the last/i.test(candidate.evidence) || /\blast race\b/i.test(candidate.evidence);
}

function extractMeetingRaceCount(
  searchResults: ChatSearchResultTrace[],
  searchDocuments: SearchDocument[],
  tipCandidate?: TipCandidate,
): number | undefined {
  const sources = [
    tipCandidate?.evidence ?? "",
    ...searchResults.flatMap((result) => [result.title, result.snippet]),
    ...searchDocuments.map((document) => document.excerpt),
  ];

  for (const source of sources) {
    if (!source) continue;
    const count = parseMeetingRaceCount(source);
    if (count) {
      return count;
    }
  }

  return undefined;
}

function inferTipRaceNumber(
  candidate: TipCandidate,
  searchResults: ChatSearchResultTrace[],
  searchDocuments: SearchDocument[],
): number | undefined {
  const meetingRaceCount =
    candidate.meetingRaceCount ?? extractMeetingRaceCount(searchResults, searchDocuments, candidate);
  const sources = [
    candidate.evidence,
    candidate.resultTitle,
    ...searchResults
      .filter((result) => result.id === candidate.resultId)
      .flatMap((result) => [result.title, result.snippet]),
    ...searchDocuments
      .filter((document) => document.resultId === candidate.resultId)
      .map((document) => document.excerpt),
  ];

  for (const source of sources) {
    if (!source) continue;
    const raceNumber = parseRaceNumberFromText(source, meetingRaceCount);
    if (raceNumber) {
      return raceNumber;
    }
  }

  return meetingRaceCount && /\bin the last\b/i.test(candidate.evidence) ? meetingRaceCount : undefined;
}

function extractSnippetWindow(text: string, start: number, end: number): string {
  const snippet = text
    .slice(Math.max(0, start - 90), Math.min(text.length, end + 90))
    .replace(/\s+/g, " ")
    .trim();
  return snippet.length > 260 ? `${snippet.slice(0, 257)}...` : snippet;
}

function extractTipCandidates(
  searchResults: ChatSearchResultTrace[],
  searchDocuments: SearchDocument[],
  input: ChatCompletionRequest,
): TipCandidate[] {
  const documentsById = new Map(searchDocuments.map((document) => [document.resultId, document]));
  const targetTrack = input.raceContext?.track ?? inferTrackFromMessage(input.message);
  const targetYear = input.message.match(/\b20\d{2}\b/)?.[0] ?? null;
  const candidates = new Map<string, TipCandidate>();

  searchResults.forEach((result, resultIndex) => {
    const document = documentsById.get(result.id);
    const sources = uniqueStrings([result.title, result.snippet, document?.excerpt ?? ""]).filter(Boolean);

    sources.forEach((source, sourceIndex) => {
      TIP_PATTERNS.forEach((patternConfig) => {
        const pattern = new RegExp(patternConfig.regex.source, patternConfig.regex.flags);
        for (const match of source.matchAll(pattern)) {
          const capturedHorse = cleanTipHorseName(match[1] ?? "");
          if (!capturedHorse) {
            continue;
          }

          const haystack = `${result.title} ${result.snippet} ${document?.excerpt ?? ""}`.toLowerCase();
          let score = patternConfig.weight - resultIndex * 2 - sourceIndex;
          if (targetTrack && haystack.includes(targetTrack.toLowerCase())) {
            score += 14;
          }
          if (targetYear && haystack.includes(targetYear)) {
            score += 6;
          }
          if (document) {
            score += 4;
          }

          const evidence = extractSnippetWindow(source, match.index ?? 0, (match.index ?? 0) + match[0].length);
          const key = `${capturedHorse.toLowerCase()}|${patternConfig.betType}|${result.id}`;
          const nextCandidate: TipCandidate = {
            id: `tip-${result.id}-${patternConfig.betType}-${capturedHorse.toLowerCase().replace(/\s+/g, "-")}`,
            horse: capturedHorse,
            betType: patternConfig.betType,
            evidence,
            resultId: result.id,
            resultTitle: result.title,
            resultUrl: result.url,
            score,
            meetingRaceCount: extractMeetingRaceCount([result], document ? [document] : []),
          };

          const existing = candidates.get(key);
          if (!existing || nextCandidate.score > existing.score) {
            candidates.set(key, nextCandidate);
          }
        }
      });
    });
  });

  return [...candidates.values()]
    .map((candidate) => ({
      ...candidate,
      meetingRaceCount: candidate.meetingRaceCount ?? extractMeetingRaceCount(searchResults, searchDocuments, candidate),
      raceNumber: inferTipRaceNumber(candidate, searchResults, searchDocuments),
    }))
    .sort((left, right) => right.score - left.score)
    .slice(0, 6);
}

function buildTipDetailQueries(candidate: TipCandidate, input: ChatCompletionRequest): ChatSearchQueryTrace[] {
  const track = input.raceContext?.track ?? inferTrackFromMessage(input.message);
  const dateLabel = input.raceContext?.date ?? inferDateContextFromMessage(input.message);
  if (!track || !dateLabel) {
    return [];
  }

  const raceBit = candidate.raceNumber ? ` race ${candidate.raceNumber}` : "";
  const horseBit = candidate.horse;

  return [
    {
      query: `${track} ${dateLabel}${raceBit} ${horseBit} horse number`,
      rationale: "Resolve the runner number and fuller horse name for the selected tip.",
    },
    {
      query: `${track} ${dateLabel}${raceBit} ${horseBit} racecard`,
      rationale: "Look for a racecard or odds page that names the runner in the field.",
    },
  ];
}

function extractTipDetailMatches(candidate: TipCandidate, detailResults: ChatSearchResultTrace[]): TipCandidate {
  const candidateToken = normalizeLooseName(candidate.horse);
  let bestMatch:
    | {
        horse: string;
        horseNumber?: number;
        raceNumber?: number;
        evidence: string;
        score: number;
      }
    | undefined;

  for (const result of detailResults) {
    const haystacks = [result.title, result.snippet];
    const raceFromUrl = result.url.match(/\/race-(\d+)\//i)?.[1];

    for (const haystack of haystacks) {
      if (!haystack) continue;

      const compact = haystack.replace(/\s+/g, " ");
      const numberedPattern = /(?:^|[\s|])(\d{1,2})[.\s|]+([A-Z][A-Za-z'’.-]*(?:\s+[A-Z][A-Za-z'’.-]*){0,4})/g;
      for (const match of compact.matchAll(numberedPattern)) {
        const horseName = cleanTipHorseName(match[2] ?? "");
        if (!horseName) continue;
        const normalizedHorse = normalizeLooseName(horseName);
        if (!normalizedHorse.includes(candidateToken) && !candidateToken.includes(normalizedHorse)) {
          continue;
        }

        const score =
          100 +
          (raceFromUrl ? 5 : 0) +
          (horseName.split(" ").length > candidate.horse.split(" ").length ? 5 : 0) +
          (result.domain.includes("oddschecker") ? 4 : 0);

        const evidence = extractSnippetWindow(compact, match.index ?? 0, (match.index ?? 0) + match[0].length);
        if (!bestMatch || score > bestMatch.score) {
          bestMatch = {
            horse: horseName,
            horseNumber: Number(match[1]),
            raceNumber: raceFromUrl ? Number(raceFromUrl) : undefined,
            evidence,
            score,
          };
        }
      }

      if (!bestMatch && candidateToken.length >= 4) {
        const fuzzyPattern = new RegExp(
          `(?:^|[\\s|])(\\d{1,2})[.\\s|]+([A-Z][A-Za-z'’.-]*(?:\\s+[A-Z][A-Za-z'’.-]*){0,4}${escapeRegExp(candidate.horse)}[A-Za-z'’.-]*)`,
          "i",
        );
        const fuzzyMatch = compact.match(fuzzyPattern);
        if (fuzzyMatch) {
          const horseName = cleanTipHorseName(fuzzyMatch[2] ?? "");
          if (horseName) {
            bestMatch = {
              horse: horseName,
              horseNumber: Number(fuzzyMatch[1]),
              raceNumber: raceFromUrl ? Number(raceFromUrl) : undefined,
              evidence: extractSnippetWindow(compact, fuzzyMatch.index ?? 0, (fuzzyMatch.index ?? 0) + fuzzyMatch[0].length),
              score: 92,
            };
          }
        }
      }
    }
  }

  if (!bestMatch) {
    return candidate;
  }

  return {
    ...candidate,
    horse: bestMatch.horse,
    horseNumber: bestMatch.horseNumber ?? candidate.horseNumber,
    raceNumber: bestMatch.raceNumber ?? candidate.raceNumber,
    evidence: bestMatch.evidence || candidate.evidence,
    score: candidate.score + 10,
  };
}

async function enrichTipCandidates(
  candidates: TipCandidate[],
  input: ChatCompletionRequest,
  country?: string,
): Promise<TipCandidate[]> {
  if (!hasPerplexityApiKey || candidates.length === 0) {
    return candidates;
  }

  const enriched = await Promise.all(
    candidates.map(async (candidate) => {
      const baseCandidate = {
        ...candidate,
        raceNumber: candidate.raceNumber,
      };

      const detailQueries = buildTipDetailQueries(baseCandidate, input);
      if (detailQueries.length === 0) {
        return baseCandidate;
      }

      try {
        const detailResults = await runPerplexitySearch(detailQueries, country);
        return extractTipDetailMatches(baseCandidate, detailResults);
      } catch {
        return baseCandidate;
      }
    }),
  );

  return enriched
    .map((candidate) => ({ ...candidate }))
    .sort((left, right) => right.score - left.score);
}

function buildTipCandidatesSection(candidates: TipCandidate[]): string {
  if (candidates.length === 0) {
    return "No explicit best-bet or each-way candidate phrases were extracted from the live results.";
  }

  return candidates
    .map(
      (candidate, index) =>
        `${index + 1}. horse: ${candidate.horse}\n   horse_number: ${candidate.horseNumber ?? "unknown"}\n   race_number: ${candidate.raceNumber ?? "unknown"}\n   bet_type: ${candidate.betType}\n   source_result_id: ${candidate.resultId}\n   evidence: ${candidate.evidence}\n   url: ${candidate.resultUrl}`,
    )
    .join("\n");
}

/**
 * Extract recent entity mentions (horse, track, race) from session history for context continuity.
 */
function buildSessionContextBlock(sessionId?: string): string | null {
  if (!sessionId) return null;
  const history = getStrideSessionHistory(sessionId);
  if (history.length === 0) return null;

  const recentMessages = history.slice(-6).map(m => m.content);
  const TRACK_NAMES = ["randwick","flemington","caulfield","rosehill","moonee valley","eagle farm","doomben","ascot","morphettville","sandown","warwick farm","gold coast","ballarat","geelong","cranbourne","canterbury","newcastle","kembla grange","gosford","wyong","hawkesbury","scone","tamworth","grafton","murwillumbah"];
  const foundTracks: string[] = [];
  const foundHorses: string[] = [];
  const raceNums: string[] = [];
  let focusHorse: string | null = null;
  let focusTrack: string | null = null;
  let focusRace: string | null = null;
  let dangerHorse: string | null = null;

  for (const msg of recentMessages) {
    const lower = msg.toLowerCase();
    for (const t of TRACK_NAMES) {
      if (lower.includes(t) && !foundTracks.includes(t)) foundTracks.push(t);
    }
    const raceMatch = msg.match(/\bR(?:ace\s*)?(\d{1,2})\b/gi);
    if (raceMatch) raceNums.push(...raceMatch.slice(0, 2));

    // Extract structured entities from SELECTION responses
    const selectionMatch = msg.match(/SELECTION\s+(?:No\.\s*\d+\s+)?([A-Z][A-Za-z\s'']+?)(?:\n|$)/);
    if (selectionMatch?.[1]) {
      focusHorse = selectionMatch[1].trim();
      if (!foundHorses.includes(focusHorse)) foundHorses.push(focusHorse);
    }

    // Extract danger runner
    const dangerMatch = msg.match(/Danger\s+(?:No\.\s*\d+\s+)?([A-Z][A-Za-z\s'']+?)(?:\n|$)/);
    if (dangerMatch?.[1]) {
      dangerHorse = dangerMatch[1].trim();
      if (!foundHorses.includes(dangerHorse)) foundHorses.push(dangerHorse);
    }

    // Extract horse names from numbered lists (e.g., "1. HORSE NAME [LOCK]")
    const numberedHorses = msg.match(/^\d+\.\s+([A-Z][A-Za-z\s'']+?)(?:\s*\[|\s*\||\s*$)/gm);
    if (numberedHorses) {
      for (const nh of numberedHorses.slice(0, 5)) {
        const hMatch = nh.match(/^\d+\.\s+([A-Z][A-Za-z\s'']+?)(?:\s*\[|\s*\||\s*$)/);
        if (hMatch?.[1]) {
          const name = hMatch[1].trim();
          if (name.length > 2 && !foundHorses.includes(name)) foundHorses.push(name);
        }
      }
    }

    // Extract horse-like names (Title Case, 2+ words)
    const horseMatches = msg.match(/\b[A-Z][a-z]+ [A-Z][a-z]+(?:\s[A-Z][a-z]+)?\b/g);
    if (horseMatches) foundHorses.push(...horseMatches.filter(h => h.split(" ").length >= 2 && !["Race Day", "Best Bet", "Last Run", "Race Card", "Market Scan", "Card Overview"].includes(h)).slice(0, 3));
  }

  // Track focus entity — most recently mentioned
  if (foundTracks.length > 0) focusTrack = foundTracks[foundTracks.length - 1];
  if (raceNums.length > 0) focusRace = raceNums[raceNums.length - 1];

  const parts: string[] = [];
  if (focusHorse) parts.push(`Focus horse (last discussed): ${focusHorse}`);
  if (focusTrack) parts.push(`Focus track: ${focusTrack}`);
  if (focusRace) parts.push(`Focus race: ${focusRace}`);
  if (dangerHorse) parts.push(`Danger runner: ${dangerHorse}`);
  if (foundTracks.length > 1) parts.push(`Other tracks mentioned: ${[...new Set(foundTracks)].join(", ")}`);
  if (raceNums.length > 1) parts.push(`Other races mentioned: ${[...new Set(raceNums)].join(", ")}`);
  if (foundHorses.length > 0) parts.push(`Horses in conversation: ${[...new Set(foundHorses)].slice(0, 8).join(", ")}`);

  if (parts.length === 0) return null;

  // Add conversation history for Claude synthesis
  const historySnippet = history.slice(-4).map(m =>
    `${m.role === "user" ? "User" : "Assistant"}: ${m.content.slice(0, 200)}${m.content.length > 200 ? "..." : ""}`
  ).join("\n");

  return `[CONVERSATION CONTEXT]\n${parts.join("\n")}\n\n[CONVERSATION HISTORY]\n${historySnippet}\n\nUse this to resolve references like 'that horse', 'the danger', 'the same race', 'what about the second pick' when the current question is ambiguous.`;
}

function resolveSessionReferences(message: string, sessionId?: string): string {
  if (!sessionId) return message;
  const history = getStrideSessionHistory(sessionId);
  if (history.length === 0) return message;

  const lower = message.toLowerCase();
  const hasReference = /\b(that horse|it|the favourite|the favorite|the danger|those two|the same race|what about|the second pick|the first pick|the top pick|him|her|this one)\b/i.test(lower);
  if (!hasReference) return message;

  // Find focus entities from recent assistant responses
  let focusHorse: string | null = null;
  let dangerHorse: string | null = null;
  let focusTrack: string | null = null;
  let focusRace: string | null = null;
  let secondPick: string | null = null;

  const assistantMsgs = history.filter(m => m.role === "assistant").slice(-3);
  for (const msg of assistantMsgs) {
    const content = msg.content;

    // SELECTION line
    const selMatch = content.match(/SELECTION\s+(?:No\.\s*\d+\s+)?([A-Z][A-Za-z\s'']+?)(?:\n|$)/);
    if (selMatch?.[1] && !focusHorse) focusHorse = selMatch[1].trim();

    // Danger line
    const dangerMatch = content.match(/Danger\s+(?:No\.\s*\d+\s+)?([A-Z][A-Za-z\s'']+?)(?:\n|$)/);
    if (dangerMatch?.[1]) dangerHorse = dangerMatch[1].trim();

    // Numbered list — 1st and 2nd
    const numbered = content.match(/^\d+\.\s+([A-Z][A-Za-z\s'']+?)(?:\s*\[|\s*\||\s*$)/gm);
    if (numbered && numbered.length >= 1 && !focusHorse) {
      const m1 = numbered[0].match(/^\d+\.\s+([A-Z][A-Za-z\s'']+?)(?:\s*\[|\s*\||\s*$)/);
      if (m1?.[1]) focusHorse = m1[1].trim();
    }
    if (numbered && numbered.length >= 2 && !secondPick) {
      const m2 = numbered[1].match(/^\d+\.\s+([A-Z][A-Za-z\s'']+?)(?:\s*\[|\s*\||\s*$)/);
      if (m2?.[1]) secondPick = m2[1].trim();
    }

    // Track and race
    const raceLabel = content.match(/(\w[\w\s]+?)\s+R(\d{1,2})/);
    if (raceLabel && !focusTrack) {
      focusTrack = raceLabel[1].trim();
      focusRace = `R${raceLabel[2]}`;
    }
  }

  let resolved = message;

  // Replace references with actual names
  if (focusHorse) {
    resolved = resolved.replace(/\b(that horse|the favourite|the favorite|the top pick|the first pick|this one)\b/gi, focusHorse);
    // "it" only when it's clearly a horse reference
    if (/^(is it|can it|will it|does it|how does it|what about it)\b/i.test(resolved)) {
      resolved = resolved.replace(/\bit\b/i, focusHorse);
    }
  }
  if (dangerHorse) {
    resolved = resolved.replace(/\bthe danger\b/gi, dangerHorse);
  }
  if (secondPick) {
    resolved = resolved.replace(/\bthe second pick\b/gi, secondPick);
  }
  if (focusTrack && focusRace) {
    resolved = resolved.replace(/\bthe same race\b/gi, `${focusTrack} ${focusRace}`);
  }

  return resolved;
}

/**
 * Parse declared runner names from a live race card string returned by fetchLiveRaceCard().
 * Lines look like: "  1. Horse Name B3 | Jockey | T: Trainer | ..."
 */
function extractHorsesFromLiveCard(card: string): string[] {
  const horses: string[] = [];
  for (const line of card.split("\n")) {
    const match = line.match(/^\s+\d+\.\s+([A-Z][A-Za-z\s'']+?)(?:\s+B\d|\s+\||\s*$)/);
    if (match?.[1]) {
      const name = match[1].trim();
      if (name.length > 2) horses.push(name);
    }
  }
  return horses;
}

function answerNeedsDirectSelection(answer: string): boolean {
  return /not fully available|weren't fully available|aren't fully detailed|not clearly listed|limited information|recommend checking closer to race day|would need direct access|couldn'?t find/i.test(
    answer,
  );
}

function sanitizeAnswerFormatting(answer: string): string {
  return answer
    .replace(/\*\*([^*]+)\*\*/g, "$1")
    .replace(/\*([^*]+)\*/g, "$1")
    .replace(/__([^_]+)__/g, "$1")
    .replace(/^#{1,3}\s+/gm, "")
    .replace(/(^|\n)\s*-\s+/g, "$1")
    .replace(/\n{3,}/g, "\n\n")
    .trim();
}

function answerNeedsStructuredTipFallback(answer: string, candidate: TipCandidate): boolean {
  const impliedRaceNumber =
    candidate.raceNumber ??
    (candidate.meetingRaceCount && tipImpliesLastRace(candidate) ? candidate.meetingRaceCount : undefined);

  if (impliedRaceNumber && !new RegExp(`\\bRace\\s+${impliedRaceNumber}\\b`, "i").test(answer)) {
    return true;
  }

  if (candidate.horseNumber && !new RegExp(`\\b(?:No\\.?\\s*)?${candidate.horseNumber}\\b`, "i").test(answer)) {
    return true;
  }

  return false;
}

function formatBetTypeLabel(betType: TipCandidateBetType): string {
  switch (betType) {
    case "best-bet":
      return "best bet";
    case "top-pick":
      return "top pick";
    case "each-way":
      return "each-way play";
    case "value":
      return "value play";
    default:
      return "selection";
  }
}

function inferDateContextFromMessage(message: string): string | null {
  const patterns = [
    new RegExp(`\\b(?:the\\s+)?\\d{1,2}(?:st|nd|rd|th)?\\s+of\\s+${MONTH_PATTERN}\\s+\\d{4}\\b`, "i"),
    new RegExp(`\\b${MONTH_PATTERN}\\s+\\d{1,2}(?:st|nd|rd|th)?(?:,)?\\s+\\d{4}\\b`, "i"),
  ];

  for (const pattern of patterns) {
    const match = message.match(pattern);
    if (match) {
      return match[0];
    }
  }

  return null;
}

function prettifyDateLabel(dateLabel: string | null): string | null {
  if (!dateLabel) {
    return null;
  }

  return dateLabel
    .replace(/\bapril\b/i, "April")
    .replace(/\baugust\b/i, "August")
    .replace(/\bdecember\b/i, "December")
    .replace(/\bfebruary\b/i, "February")
    .replace(/\bjanuary\b/i, "January")
    .replace(/\bjuly\b/i, "July")
    .replace(/\bjune\b/i, "June")
    .replace(/\bmarch\b/i, "March")
    .replace(/\bmay\b/i, "May")
    .replace(/\bnovember\b/i, "November")
    .replace(/\boctober\b/i, "October")
    .replace(/\bseptember\b/i, "September");
}

function buildTipCandidateFallbackAnswer(candidate: TipCandidate, input: ChatCompletionRequest): string {
  const domain = toDomain(candidate.resultUrl);
  const track = input.raceContext?.track ?? inferTrackFromMessage(input.message);
  const dateLabel = prettifyDateLabel(input.raceContext?.date ?? inferDateContextFromMessage(input.message));
  const runnerLabel = candidate.horseNumber ? `No. ${candidate.horseNumber} ${candidate.horse}` : candidate.horse;
  const impliedRaceNumber =
    candidate.raceNumber ??
    (candidate.meetingRaceCount && tipImpliesLastRace(candidate) ? candidate.meetingRaceCount : undefined);
  const locationLabel =
    track && dateLabel
      ? `${track} on ${dateLabel}`
      : track
        ? track
        : dateLabel
          ? dateLabel
          : "the meeting";
  const raceLabel =
    impliedRaceNumber && candidate.meetingRaceCount && impliedRaceNumber === candidate.meetingRaceCount
      ? `Race ${impliedRaceNumber}, the last race on the ${candidate.meetingRaceCount}-race card`
      : impliedRaceNumber
        ? `Race ${impliedRaceNumber}`
        : candidate.meetingRaceCount
          ? `the ${candidate.meetingRaceCount}-race card`
          : "the meeting";
  const betLabel =
    candidate.betType === "each-way"
      ? "an each-way play"
      : candidate.betType === "best-bet" || candidate.betType === "top-pick"
        ? "the clearest best bet"
        : `the clearest ${formatBetTypeLabel(candidate.betType)}`;
  const supportSentence =
    impliedRaceNumber && candidate.horseNumber
      ? `The accessible source points to ${runnerLabel} as ${betLabel} in Race ${impliedRaceNumber}.`
      : impliedRaceNumber
        ? `The accessible source points to ${candidate.horse} as ${betLabel} in Race ${impliedRaceNumber}.`
        : `The accessible source points to ${candidate.horse} as ${betLabel} for the meeting.`;

  return `For ${locationLabel}, the strongest published betting angle I found is ${betLabel} on ${runnerLabel} in ${raceLabel} [${domain}](${candidate.resultUrl}). ${supportSentence}`;
}

function buildLocalTrace(
  mode: ChatModeState,
  localTurn: StrideChatTurn | null,
  warnings: string[],
  assumptions: string[] = [],
): ChatTrace {
  if (!localTurn) {
    return {
      label: mode.brain ? "Deep Thought Summary" : "Answer flow",
      headline: "No local racing context was available for this turn.",
      summary: "The answer had to stay lightweight because no saved Stride evidence was attached.",
      sections: [
        {
          id: "thinking",
          kind: "thinking",
          title: "What it focused on",
          summary: "The model stayed close to the wording of the user question.",
          bullets: uniqueStrings([...assumptions, "No local race or horse context was injected into this turn."]),
        },
        {
          id: "steps",
          kind: "steps",
          title: "What it did",
          summary: "It answered without a saved local evidence bundle.",
          bullets: ["No local retrieval step ran for this request."],
        },
        {
          id: "sources",
          kind: "sources",
          title: "Sources and tools",
          summary: "This turn did not use local database or file retrieval.",
          bullets: warnings.length > 0 ? warnings : ["No sources were attached."],
        },
        {
          id: "why",
          kind: "why",
          title: "Why it landed here",
          summary: "The answer remained conservative because there was no grounded local evidence.",
          bullets: ["Add a race context or use Search mode if you want live web retrieval."],
        },
      ],
      searchQueries: [],
      searchResults: [],
      finalWhy: "No grounded local evidence was attached to the turn.",
    };
  }

  const scopeBits = [
    localTurn.bundle.entities.date,
    localTurn.bundle.entities.track,
    localTurn.bundle.entities.raceNumber ? `R${localTurn.bundle.entities.raceNumber}` : null,
    localTurn.bundle.entities.horseName,
  ].filter(Boolean);
  const sources = localTurn.retrieval.sourcesUsed.map((source) => LOCAL_SOURCE_LABELS[source] ?? source);
  const evidenceLines = formatLocalEvidence(localTurn, 4);

  // Intent-aware step copy
  const intent = localTurn.bundle.classification.intent.toLowerCase();
  const intentBullet = intent.includes("form")
    ? "Prioritised most recent starts and class progression."
    : intent.includes("selection") || intent.includes("bet") || intent.includes("value")
      ? "Weighted edge, EV, and convergence tier before surfacing picks."
      : intent.includes("market") || intent.includes("price") || intent.includes("odds")
        ? "Cross-referenced model probability against live market price."
        : "Weighted the answer toward the most specific race, runner, and date matches.";

  return {
    label: mode.brain ? "Deep Thought Summary" : "Answer flow",
    headline: mode.brain
      ? "Deep Thought mapped the question, checked the saved Stride evidence, and built a grounded answer."
      : "Stride checked the saved racing data and answered from the strongest local evidence.",
    summary: localTurn.bundle.summary,
    sections: [
      {
        id: "thinking",
        kind: "thinking",
        title: "What it focused on",
        summary: `The question was classified as ${localTurn.bundle.classification.intent}.`,
        bullets: uniqueStrings([
          ...assumptions,
          scopeBits.length > 0 ? `Scope: ${scopeBits.join(" • ")}.` : "Scope: broad question without a single race anchor.",
          `Confidence: ${Math.round(localTurn.bundle.classification.confidence * 100)}%.`,
          ...localTurn.bundle.classification.rationale.slice(0, 3),
        ]),
      },
      {
        id: "steps",
        kind: "steps",
        title: "What it did",
        summary: `Scanned ${localTurn.retrieval.evidenceItems} item${localTurn.retrieval.evidenceItems === 1 ? "" : "s"} across ${sources.length} source type${sources.length === 1 ? "" : "s"}${sources.length > 0 ? ` — ${sources.slice(0, 3).join(", ")}` : ""}.`,
        bullets: uniqueStrings([
          "Matched the request to the most likely racing intent.",
          `Pulled evidence from ${sources.length > 0 ? sources.slice(0, 4).join(", ") : "the local Stride dataset"}.`,
          intentBullet,
        ]),
      },
      {
        id: "sources",
        kind: "sources",
        title: "Sources and tools",
        summary: sources.length > 0 ? `This turn used ${sources.length} local source type${sources.length === 1 ? "" : "s"}.` : "This turn had limited local source coverage.",
        bullets: uniqueStrings([
          ...sources.slice(0, 6),
          ...warnings,
        ]),
      },
      {
        id: "why",
        kind: "why",
        title: "Why it landed here",
        summary: evidenceLines.length > 0
          ? `The answer draws from ${evidenceLines.length} specific evidence item${evidenceLines.length === 1 ? "" : "s"} — see below.`
          : "The final answer mirrors the strongest grounded evidence rather than speculative commentary.",
        bullets: evidenceLines.length > 0 ? evidenceLines : ["The bundle did not contain a strong, direct evidence match."],
      },
    ],
    searchQueries: [],
    searchResults: [],
    finalWhy:
      evidenceLines[0] ??
      "The answer stayed conservative because the local bundle was too light to support a stronger claim.",
  };
}

function buildSearchFallbackTrace(
  mode: ChatModeState,
  plan: SearchPlan,
  searchResults: ChatSearchResultTrace[],
  warnings: string[],
  assumptions: string[] = [],
): ChatTrace {
  return {
    label: mode.brain ? "Search and reasoning trace" : "Search trace",
    headline: searchResults.length > 0
      ? "Search mode retrieved live web results, but the synthesis step could not complete."
      : "Search mode could not complete the live retrieval path.",
    summary:
      warnings[0] ??
      "The app stopped before it could form a fully cited web-grounded answer.",
    sections: [
      {
        id: "thinking",
        kind: "thinking",
        title: "What it focused on",
        summary: plan.thinkingSummary,
        bullets: uniqueStrings([...assumptions, plan.objective]),
      },
      {
        id: "steps",
        kind: "steps",
        title: "What it did",
        summary: "The app planned a search and attempted retrieval before the synthesis step halted.",
        bullets: plan.steps,
      },
      {
        id: "sources",
        kind: "sources",
        title: "Sources and tools",
        summary: "Search mode uses Perplexity for retrieval and Claude for synthesis.",
        bullets: uniqueStrings([...plan.sourcesToCheck, ...warnings]),
      },
      {
        id: "why",
        kind: "why",
        title: "Why it stopped here",
        summary: "The answer stayed transparent instead of pretending a web-grounded synthesis had happened.",
        bullets: warnings.length > 0 ? warnings : ["The synthesis step did not produce a valid result."],
      },
    ],
    searchQueries: plan.searchQueries,
    searchResults,
    finalWhy: warnings[0] ?? "The search trace is visible, but the cited synthesis step did not complete.",
  };
}

function buildCitationLinks(results: ChatSearchResultTrace[]): ChatCitationLink[] {
  return results
    .filter((result) => result.selected)
    .map((result) => ({
      id: result.id,
      label: result.title,
      url: result.url,
      domain: result.domain,
    }));
}

// ── Local LLM Synthesis ──────────────────────────────────────────────────────

interface LocalSynthesisPayload {
  selection: { horse: string; runnerNumber?: number; raceNumber?: number };
  raceLabel: string;
  why: string;
  confidence: "high" | "medium" | "low";
  confidenceBasis: string;
  consensus?: { horse: string; mentions?: number; votePct?: string; narrative?: string };
  modelVsConsensus?: string;
  danger?: { horse: string; runnerNumber?: number; reason: string };
  uncertaintyNote?: string;
  sources: string[];
}

function getIntentPromptOverride(intent: StrideIntent): string {
  switch (intent) {
    case "race_preview":
    case "meeting_summary":
      return "Structure this as a race preview: lead with the selection and race conditions, explain why it stands out, then name the danger. If asked about a full meeting, briefly cover the best play across the card.";
    case "horse_history":
      return "Focus on this horse's trajectory: recent form cycle, sectional trend, franking status, readiness band. Do not name a bet unless the question explicitly asks for one.";
    case "race_result":
      return "Report the result cleanly: placings with prices, whether STRIDE's tip hit or missed and by how much. Only add analysis if explicitly asked.";
    case "consensus":
      return "Lead with the consensus leader, their mention count, vote share, and the recurring narrative theme. Then state whether the STRIDE model agrees or diverges.";
    case "market":
      return "Lead with the market signal: which horse, direction (steam/drift/stable), magnitude. Then note whether consensus and model align with the market move.";
    case "sectionals":
      return "Lead with the strongest sectional profile in the field. Rank by finishing burst then last 600. Compare the top two or three runners.";
    case "performance":
      return "Report the performance numbers cleanly: strike rate, ROI, total bets, winners. Do not editorialize beyond what the data shows.";
    case "model_quality":
      return "Explain the pipeline formula and method. Reference: trueMarketProb = (100 / odds) / totalOverround, computedEdge = rawModelProb - trueMarketProb, selectionScore = 40% rawProb + 30% calibratedProb + 30% clamped edge.";
    case "blackbook":
      return "Focus on why this horse is in the blackbook: the luckless run, closing sectionals, and when it next races. Frame as a watch-list entry, not a bet.";
    default:
      return "";
  }
}

function buildLocalSynthesisPrompt(turn: StrideChatTurn): string {
  const bundle = turn.bundle;
  const race = extractRaceMeta(bundle);
  const best = extractBestTip(bundle);
  const consensus = extractConsensusLeader(bundle);
  const market = extractMarketLeader(bundle);

  const sections: string[] = [];

  sections.push(`Question: ${turn.question}`);
  sections.push(`Intent: ${bundle.classification.intent} (${Math.round(bundle.classification.confidence * 100)}% confidence)`);

  // Race context
  const raceLines: string[] = [];
  if (race.track) raceLines.push(`Track: ${race.track}`);
  if (race.raceNumber) raceLines.push(`Race: ${race.raceNumber}`);
  if (race.raceDate) raceLines.push(`Date: ${race.raceDate}`);
  if (race.raceName) raceLines.push(`Name: ${race.raceName}`);
  if (race.distance) raceLines.push(`Distance: ${race.distance}`);
  if (race.going) raceLines.push(`Going: ${race.going}`);
  if (race.raceClass) raceLines.push(`Class: ${race.raceClass}`);
  if (raceLines.length > 0) {
    sections.push(`\n[RACE CONTEXT]\n${raceLines.join("\n")}`);
  }

  // Track profile
  if (race.track) {
    const profile = getTrackProfile(race.track);
    if (profile) {
      sections.push(`\n[TRACK PROFILE]\n${profile}`);
    }
  }

  // Session context (entity continuity)
  const sessionCtx = buildSessionContextBlock(turn.sessionId);
  if (sessionCtx) {
    sections.push(`\n${sessionCtx}`);
  }

  // Model tip
  if (best) {
    const tipLines: string[] = [];
    if (best.horse) tipLines.push(`Horse: ${best.horse}`);
    if (best.odds) tipLines.push(`Market odds: ${best.odds}`);
    if (best.fairOdds) tipLines.push(`Fair odds: ${best.fairOdds}`);
    if (best.winPct) tipLines.push(`Win probability: ${best.winPct}`);
    if (best.edgePct) tipLines.push(`Edge: ${best.edgePct}`);
    if (best.confidence) tipLines.push(`Confidence: ${best.confidence}`);
    if (best.selectionScore) tipLines.push(`Selection score: ${best.selectionScore}`);
    if (best.valueRating) tipLines.push(`Value rating: ${best.valueRating}`);
    sections.push(`\n[MODEL TIP]\n${tipLines.join("\n")}`);
  }

  // Consensus
  if (consensus) {
    const consLines: string[] = [];
    consLines.push(`Leader: ${consensus.horse}`);
    if (consensus.crowdScore) consLines.push(`Crowd score: ${consensus.crowdScore}`);
    if (consensus.votePct) consLines.push(`Vote share: ${consensus.votePct}`);
    if (consensus.mentions !== undefined) consLines.push(`Total mentions: ${consensus.mentions}`);
    sections.push(`\n[CONSENSUS INTELLIGENCE]\n${consLines.join("\n")}`);
  }

  // Market signals
  if (market) {
    const mktLines: string[] = [];
    mktLines.push(`Horse: ${market.horse}`);
    if (market.type) mktLines.push(`Signal: ${market.type}`);
    if (market.baseline) mktLines.push(`Baseline price: ${market.baseline}`);
    if (market.morning) mktLines.push(`Morning price: ${market.morning}`);
    if (market.movementPct) mktLines.push(`Movement: ${market.movementPct}`);
    sections.push(`\n[MARKET SIGNALS]\n${mktLines.join("\n")}`);
  }

  // Graph franking intelligence (from selections or tips data)
  const selItems = pickEvidence(bundle, "selections");
  const bestSel = selItems.sort((a, b) => {
    const aP = (a.payload as Record<string, unknown>) ?? {};
    const bP = (b.payload as Record<string, unknown>) ?? {};
    return ((bP.edge as number) ?? -Infinity) - ((aP.edge as number) ?? -Infinity);
  })[0];
  if (bestSel) {
    const p = (bestSel.payload as Record<string, unknown>) ?? {};
    const graphLines: string[] = [];
    if (p.pagerankAuthority != null) graphLines.push(`PageRank authority: ${p.pagerankAuthority}`);
    if (p.graphFrankingScore != null) graphLines.push(`Graph franking score: ${p.graphFrankingScore}`);
    if (p.graphFrankingDepth != null) graphLines.push(`Graph franking depth: ${p.graphFrankingDepth}`);
    if (p.graphFrankingIndependence != null) graphLines.push(`Graph franking independence: ${p.graphFrankingIndependence}`);
    if (p.communityStrength != null) graphLines.push(`Community strength: ${p.communityStrength}`);
    if (p.formStability != null) graphLines.push(`Form stability: ${p.formStability}`);
    if (p.marketValidatedFranking) graphLines.push(`Market-validated franking: true`);
    if (p.bridgeScore != null) graphLines.push(`Bridge score: ${p.bridgeScore}`);
    if (p.collateralAdvantage != null) graphLines.push(`Collateral advantage: ${p.collateralAdvantage} ELO points vs field`);
    if (p.frankingElo != null) graphLines.push(`Franking ELO: ${p.frankingElo}`);
    if (graphLines.length > 0) {
      sections.push(`\n[FORM GRAPH]\n${graphLines.join("\n")}`);
    }
  }

  // Raw evidence items (top ranked)
  const evidenceLines = formatLocalEvidence(turn, 8);
  if (evidenceLines.length > 0) {
    sections.push(`\n[EVIDENCE BUNDLE]\n${evidenceLines.map((line, i) => `${i + 1}. ${line}`).join("\n")}`);
  }

  // Consensus detail for all runners (if available)
  const consensusItems = pickEvidence(bundle, "local_consensus");
  if (consensusItems.length > 0) {
    const detailLines: string[] = [];
    for (const item of consensusItems.slice(0, 2)) {
      const payload = item.payload as Record<string, unknown> | undefined;
      if (!payload) continue;
      for (const [horse, stats] of Object.entries(payload)) {
        const s = stats as Record<string, unknown> | undefined;
        if (!s) continue;
        const mentions = s.total_mentions ?? s.mentions;
        const score = s.crowd_score ?? s.consensus_score;
        const votePct = s.vote_pct;
        if (mentions || score) {
          detailLines.push(`${horse}: score ${score ?? "?"}, mentions ${mentions ?? "?"}, vote ${votePct ?? "?"}%`);
        }
      }
    }
    if (detailLines.length > 0) {
      sections.push(`\n[CONSENSUS DETAIL BY RUNNER]\n${detailLines.slice(0, 10).join("\n")}`);
    }
  }

  // Intent-specific instruction
  const intentOverride = getIntentPromptOverride(bundle.classification.intent);
  if (intentOverride) {
    sections.push(`\n[INTENT INSTRUCTION]\n${intentOverride}`);
  }

  // Source summary
  const sourcesUsed = Object.entries(bundle.sourceCounts)
    .filter(([, count]) => count > 0)
    .map(([source]) => LOCAL_SOURCE_LABELS[source] ?? source);
  if (sourcesUsed.length > 0) {
    sections.push(`\n[SOURCES AVAILABLE]\n${sourcesUsed.join(", ")}`);
  }

  // Data freshness warning — check if tips date is older than today
  const tipsForFreshness = pickEvidence(bundle, "local_tips")[0] ?? pickEvidence(bundle, "selections")[0];
  if (tipsForFreshness?.raceDate) {
    const tipsDate = new Date(tipsForFreshness.raceDate + "T00:00:00+10:00"); // AEST
    const now = new Date();
    const ageHours = (now.getTime() - tipsDate.getTime()) / (1000 * 60 * 60);
    if (ageHours > 36) {
      sections.push(`\n[DATA FRESHNESS]\nThese selections are from ${tipsForFreshness.raceDate}, which is more than a day old. This data may not reflect current conditions.`);
    }
  }

  // Intent-aware JSON schema
  const intent = bundle.classification.intent;
  if (intent === "performance") {
    sections.push(`\nReturn valid JSON matching this schema:
{
  "headline": "one-line performance summary",
  "stats": { "totalBets": number, "winners": number, "strikeRate": number, "roi": number, "profitLoss": number },
  "tierBreakdown": [{ "tier": "string", "bets": number, "strikeRate": number, "roi": number }],
  "trend": "one sentence on 7-day vs 30-day trend",
  "interpretation": "2-3 sentence analysis of what the numbers mean",
  "sources": ["list of source types used"]
}`);
  } else if (intent === "meeting_overview") {
    sections.push(`\nReturn valid JSON matching this schema:
{
  "headline": "one-line card summary",
  "races": [{ "raceNumber": number, "bestPick": "horse name", "tier": "convergence tier", "edge": "edge string", "odds": "odds string" }],
  "topPlays": "1-2 sentences highlighting the best plays across the card",
  "bankers": ["list of banker horse names"],
  "sources": ["list of source types used"]
}`);
  } else if (intent === "horse_comparison") {
    sections.push(`\nReturn valid JSON matching this schema:
{
  "headline": "one-line comparison summary",
  "horseA": { "name": "string", "edge": "string", "tier": "string", "strengths": "one sentence", "weaknesses": "one sentence" },
  "horseB": { "name": "string", "edge": "string", "tier": "string", "strengths": "one sentence", "weaknesses": "one sentence" },
  "verdict": "2-3 sentence verdict on which horse is the better play and why",
  "sources": ["list of source types used"]
}`);
  } else if (intent === "consensus" || intent === "franking") {
    sections.push(`\nReturn valid JSON matching this schema:
{
  "headline": "one-line consensus summary",
  "topHorses": [{ "horse": "string", "strideScore": number_or_null, "consensusScore": number_or_null, "marketScore": number_or_null, "tier": "string", "votePct": "string_or_null" }],
  "agreement": "one sentence on model vs consensus alignment or divergence",
  "marketNarrative": "one sentence on what market signals suggest, or null",
  "sources": ["list of source types used"]
}`);
  } else if (intent === "horse_history" || intent === "blackbook") {
    sections.push(`\nReturn valid JSON matching this schema:
{
  "selection": { "horse": "string", "runnerNumber": number_or_null, "raceNumber": number_or_null },
  "formSummary": "2-3 sentence form analysis",
  "analysis": "2-3 sentence deeper analysis from model data (4-phase if available)",
  "upcomingPlay": { "tier": "string_or_null", "edge": "string_or_null", "confidence": "string_or_null" },
  "franking": "one sentence on franking status, or null",
  "blackbook": "one sentence on blackbook status, or null",
  "verdict": "1-2 sentence verdict on whether to back this horse",
  "sources": ["list of source types used"]
}`);
  } else {
    sections.push(`\nReturn valid JSON matching this schema:
{
  "selection": { "horse": "string", "runnerNumber": number_or_null, "raceNumber": number_or_null },
  "raceLabel": "track, race number, distance, going, class as a compact string",
  "why": "2-4 sentence synthesis of why this selection stands out",
  "confidence": "high | medium | low",
  "confidenceBasis": "one sentence explaining what drives the confidence level",
  "consensus": { "horse": "consensus leader name", "mentions": number_or_null, "votePct": "string_or_null", "narrative": "one sentence common theme or null" },
  "modelVsConsensus": "one sentence on alignment or divergence, or null if no consensus data",
  "danger": { "horse": "danger runner name", "runnerNumber": number_or_null, "reason": "one sentence" },
  "uncertaintyNote": "optional string if edge is unclear or data is thin, otherwise null",
  "sources": ["list of source types used"]
}`);
  }

  return sections.join("\n");
}

export async function synthesizeLocalWithClaude(turn: StrideChatTurn): Promise<LocalSynthesisPayload | null> {
  if (!hasAnthropicApiKey) return null;

  const prompt = buildLocalSynthesisPrompt(turn);
  const maxAttempts = 3;
  const backoffMs = [1000, 3000, 6000];

  for (let attempt = 1; attempt <= maxAttempts; attempt++) {
    try {
      const response = await anthropic.messages.create({
        model: CLAUDE_MODEL,
        max_tokens: 2000,
        temperature: 0.2,
        system: STRIDE_LOCAL_SYNTHESIS_PROMPT,
        messages: [{ role: "user", content: prompt }],
      });
      const text = extractText(response);
      let parsed = extractJsonObject<LocalSynthesisPayload>(text);
      if (!parsed) {
        // Attempt JSON repair: fix trailing comma, missing closing brace
        const repaired = text
          .replace(/,\s*([}\]])/g, "$1")
          .replace(/\n/g, " ")
          .replace(/^[^{]*({.*})[^}]*$/s, "$1");
        parsed = extractJsonObject<LocalSynthesisPayload>(repaired);
      }
      if (parsed) return parsed;
      // If still null on last attempt, fall through
      if (attempt === maxAttempts) return null;
    } catch (error) {
      if (attempt === maxAttempts) {
        Sentry.captureException(error, { tags: { component: "chat_synthesis", type: "local" } });
        return null;
      }
    }
    await new Promise((resolve) => setTimeout(resolve, backoffMs[attempt - 1] ?? 3000));
  }
  return null;
}

export function formatLocalSynthesisResponse(payload: LocalSynthesisPayload): string {
  const p = payload as unknown as Record<string, unknown>;

  // Performance schema
  if (p.headline && p.stats && p.interpretation) {
    const lines: string[] = [];
    lines.push(String(p.headline));
    const stats = p.stats as Record<string, unknown>;
    const statParts = [
      stats.totalBets != null ? `${stats.totalBets} bets` : null,
      stats.winners != null ? `${stats.winners} winners` : null,
      stats.strikeRate != null ? `strike ${stats.strikeRate}%` : null,
      stats.roi != null ? `ROI ${Number(stats.roi) >= 0 ? "+" : ""}${stats.roi}%` : null,
      stats.profitLoss != null ? `P/L ${Number(stats.profitLoss) >= 0 ? "+$" : "-$"}${Math.abs(Number(stats.profitLoss)).toFixed(2)}` : null,
    ].filter(Boolean);
    if (statParts.length > 0) lines.push(statParts.join(" | "));
    const tiers = p.tierBreakdown as Array<Record<string, unknown>> | undefined;
    if (tiers && tiers.length > 0) {
      lines.push("");
      for (const t of tiers) {
        lines.push(`${t.tier}: ${t.bets ?? "?"} bets, strike ${t.strikeRate ?? "?"}%, ROI ${t.roi ?? "?"}%`);
      }
    }
    if (p.trend) { lines.push(""); lines.push(String(p.trend)); }
    lines.push("");
    lines.push(String(p.interpretation));
    if (Array.isArray(p.sources) && p.sources.length > 0) { lines.push(""); lines.push(`Sources: ${p.sources.join(", ")}`); }
    return lines.join("\n");
  }

  // Meeting overview schema
  if (p.headline && p.races && p.topPlays) {
    const lines: string[] = [];
    lines.push(String(p.headline));
    lines.push("");
    const races = p.races as Array<Record<string, unknown>>;
    for (const r of races) {
      lines.push(`R${r.raceNumber}: ${r.bestPick} [${r.tier ?? "?"}] | edge ${r.edge ?? "?"} | ${r.odds ?? "?"}`);
    }
    lines.push("");
    lines.push(String(p.topPlays));
    const bankers = p.bankers as string[] | undefined;
    if (bankers && bankers.length > 0) { lines.push(`Bankers: ${bankers.join(", ")}`); }
    if (Array.isArray(p.sources) && p.sources.length > 0) { lines.push(""); lines.push(`Sources: ${p.sources.join(", ")}`); }
    return lines.join("\n");
  }

  // Comparison schema
  if (p.headline && p.horseA && p.horseB && p.verdict) {
    const lines: string[] = [];
    lines.push(String(p.headline));
    const hA = p.horseA as Record<string, unknown>;
    const hB = p.horseB as Record<string, unknown>;
    lines.push("");
    lines.push(`${hA.name} [${hA.tier ?? "?"}] edge ${hA.edge ?? "?"}`);
    if (hA.strengths) lines.push(`  + ${hA.strengths}`);
    if (hA.weaknesses) lines.push(`  - ${hA.weaknesses}`);
    lines.push("");
    lines.push(`${hB.name} [${hB.tier ?? "?"}] edge ${hB.edge ?? "?"}`);
    if (hB.strengths) lines.push(`  + ${hB.strengths}`);
    if (hB.weaknesses) lines.push(`  - ${hB.weaknesses}`);
    lines.push("");
    lines.push(String(p.verdict));
    if (Array.isArray(p.sources) && p.sources.length > 0) { lines.push(""); lines.push(`Sources: ${p.sources.join(", ")}`); }
    return lines.join("\n");
  }

  // Consensus schema
  if (p.headline && p.topHorses && p.agreement) {
    const lines: string[] = [];
    lines.push(String(p.headline));
    lines.push("");
    const horses = p.topHorses as Array<Record<string, unknown>>;
    for (const h of horses) {
      const parts = [
        h.horse,
        h.tier ? `[${h.tier}]` : null,
        h.strideScore != null ? `STRIDE ${h.strideScore}` : null,
        h.consensusScore != null ? `consensus ${h.consensusScore}` : null,
        h.marketScore != null ? `market ${h.marketScore}` : null,
        h.votePct ? `votes ${h.votePct}` : null,
      ].filter(Boolean);
      lines.push(parts.join(" | "));
    }
    lines.push("");
    lines.push(String(p.agreement));
    if (p.marketNarrative) lines.push(String(p.marketNarrative));
    if (Array.isArray(p.sources) && p.sources.length > 0) { lines.push(""); lines.push(`Sources: ${p.sources.join(", ")}`); }
    return lines.join("\n");
  }

  // Horse history schema
  if (p.formSummary && p.selection) {
    const lines: string[] = [];
    const sel = p.selection as Record<string, unknown>;
    lines.push(`${sel.horse ?? "Horse"}`);
    lines.push("");
    lines.push(String(p.formSummary));
    if (p.analysis) { lines.push(""); lines.push(String(p.analysis)); }
    const upcoming = p.upcomingPlay as Record<string, unknown> | undefined;
    if (upcoming && (upcoming.tier || upcoming.edge)) {
      lines.push("");
      lines.push(`Upcoming: ${upcoming.tier ? `[${upcoming.tier}]` : ""} edge ${upcoming.edge ?? "?"} | ${upcoming.confidence ?? "?"} conf`.trim());
    }
    if (p.franking) { lines.push(""); lines.push(`Franking: ${p.franking}`); }
    if (p.blackbook) lines.push(`Blackbook: ${p.blackbook}`);
    if (p.verdict) { lines.push(""); lines.push(String(p.verdict)); }
    if (Array.isArray(p.sources) && p.sources.length > 0) { lines.push(""); lines.push(`Sources: ${p.sources.join(", ")}`); }
    return lines.join("\n");
  }

  // Default SELECTION schema (race_preview and general)
  const lines: string[] = [];

  // Selection line
  const runnerBit = payload.selection?.runnerNumber ? `No. ${payload.selection.runnerNumber} ` : "";
  lines.push(`SELECTION  ${runnerBit}${payload.selection?.horse ?? "Unknown"}`);

  // Race label
  if (payload.raceLabel) {
    lines.push(payload.raceLabel);
  }

  // Why paragraph
  lines.push("");
  lines.push(payload.why ?? "");

  // Confidence
  if (payload.confidence) {
    lines.push("");
    const confLabel = payload.confidence.charAt(0).toUpperCase() + payload.confidence.slice(1);
    lines.push(`Confidence  ${confLabel}`);
    if (payload.confidenceBasis) lines.push(payload.confidenceBasis);
  }

  // Uncertainty note
  if (payload.uncertaintyNote) {
    lines.push(payload.uncertaintyNote);
  }

  // Model vs consensus divergence
  if (payload.modelVsConsensus) {
    lines.push("");
    lines.push(payload.modelVsConsensus);
  }

  // Consensus
  if (payload.consensus) {
    lines.push("");
    const mentionBit = payload.consensus.mentions ? `${payload.consensus.mentions} mentions` : "";
    const voteBit = payload.consensus.votePct ? `${payload.consensus.votePct} vote share` : "";
    const detailBit = [mentionBit, voteBit].filter(Boolean).join(", ");
    lines.push(`Consensus  ${payload.consensus.horse}${detailBit ? ` (${detailBit})` : ""}`);
    if (payload.consensus.narrative) {
      lines.push(`Common theme: ${payload.consensus.narrative}`);
    }
  }

  // Danger
  if (payload.danger) {
    lines.push("");
    const dangerRunner = payload.danger.runnerNumber ? `No. ${payload.danger.runnerNumber} ` : "";
    lines.push(`Danger  ${dangerRunner}${payload.danger.horse}`);
    lines.push(payload.danger.reason);
  }

  // Sources
  if (payload.sources?.length > 0) {
    lines.push("");
    lines.push(`Sources: ${payload.sources.join(", ")}`);
  }

  return lines.join("\n");
}

function buildSearchAnswerUnavailableMessage(warnings: string[], localTurn?: StrideChatTurn | null): string {
  if (localTurn) {
    return `${buildLocalStrideResponse(localTurn.bundle)}\n\nSearch mode could not add live web grounding: ${warnings.join(" ")}`;
  }

  return `Search mode could not complete the live retrieval flow. ${warnings.join(" ")}`.trim();
}

async function synthesizeWithClaude(params: {
  input: ChatCompletionRequest;
  mode: ChatModeState;
  localTurn?: StrideChatTurn | null;
  plan: SearchPlan;
  searchResults: ChatSearchResultTrace[];
  searchDocuments: SearchDocument[];
  tipCandidates: TipCandidate[];
  liveRaceCard?: string | null;
}): Promise<ClaudeSynthesisPayload | null> {
  if (!hasAnthropicApiKey) {
    return null;
  }

  const localSection = params.localTurn
    ? [
        `Local answer draft: ${buildLocalStrideResponse(params.localTurn.bundle)}`,
        `Local bundle summary: ${params.localTurn.bundle.summary}`,
        ...formatLocalEvidence(params.localTurn, 5).map((line, index) => `${index + 1}. ${line}`),
      ].join("\n")
    : "No local racing bundle was attached to this request.";

  const searchSection = params.searchResults
    .map(
      (result) =>
        `- id: ${result.id}\n  title: ${result.title}\n  url: ${result.url}\n  domain: ${result.domain}\n  query: ${result.query}\n  snippet: ${result.snippet}`,
    )
    .join("\n");

  const documentSection = params.searchDocuments
    .map(
      (document) =>
        `- result_id: ${document.resultId}\n  title: ${document.title}\n  url: ${document.url}\n  excerpt: ${document.excerpt}`,
    )
    .join("\n");

  const prompt = [
    "Write a public-facing answer and a clean reasoning/search trace for a chat UI.",
    "Do not reveal hidden chain-of-thought. Summarize the process in user-friendly language only.",
    "",
    `Question: ${params.input.message}`,
    `Current mode: brain=${params.mode.brain}, search=${params.mode.search}`,
    params.input.raceContext
      ? `Race context: ${params.input.raceContext.track} R${params.input.raceContext.raceNumber} on ${params.input.raceContext.date}${params.input.raceContext.raceName ? ` — ${params.input.raceContext.raceName}` : ""}`
      : "Race context: none",
    "",
    "[SEARCH PLAN]",
    JSON.stringify(params.plan, null, 2),
    "",
    "[LOCAL CONTEXT]",
    localSection,
    "",
    ...(params.liveRaceCard
      ? ["[LIVE RACE CARD — from theracingapi.com]", params.liveRaceCard, "Use this declared field as your primary reference for horse names, barriers, jockeys, and trainers. When no local tips exist, reason from this data like an expert form analyst.", ""]
      : []),
    ...(params.input.raceContext?.track
      ? (() => {
          const profile = getTrackProfile(params.input.raceContext!.track!);
          return profile ? ["[TRACK PROFILE]", profile, ""] : [];
        })()
      : []),
    ...(params.input.sessionId
      ? (() => {
          const ctx = buildSessionContextBlock(params.input.sessionId);
          return ctx ? [ctx, ""] : [];
        })()
      : []),
    "[WEB RESULTS]",
    searchSection || "No search results were retrieved.",
    "",
    "[FETCHED PAGE EXCERPTS]",
    documentSection || "No page excerpts were fetched from the live results.",
    "",
    "[DIRECT TIP CANDIDATES]",
    buildTipCandidatesSection(params.tipCandidates),
    "",
    "Return valid JSON only with this shape:",
    `{
  "answer": "final answer in plain prose with inline markdown links like [Source](https://example.com) directly after web-backed claims",
  "headline": "short trace headline",
  "summary": "one short trace summary",
  "final_why": "one sentence on why the answer landed where it did",
  "sections": [
    {
      "kind": "thinking",
      "title": "What it focused on",
      "summary": "short summary",
      "bullets": ["short bullet", "short bullet"]
    },
    {
      "kind": "steps",
      "title": "What it did",
      "summary": "short summary",
      "bullets": ["short bullet", "short bullet"]
    },
    {
      "kind": "sources",
      "title": "Sources and tools",
      "summary": "short summary",
      "bullets": ["short bullet", "short bullet"]
    },
    {
      "kind": "why",
      "title": "Why it reached the answer",
      "summary": "short summary",
      "bullets": ["short bullet", "short bullet"]
    }
  ],
  "selected_results": [
    {
      "id": "id from the web results block",
      "reason": "why this result was selected",
      "extracted": "specific information taken from this result"
    }
  ],
  "warnings": ["optional warning"]
}`,
    "Rules:",
    "- Lead with the selection (horse name, runner number, race number) in the first sentence. Never bury the pick.",
    "- Only cite URLs that appear in the WEB RESULTS block.",
    "- Use inline markdown links in the answer for any web-backed claim.",
    "- Write the answer as clean prose, not bullet points. No markdown bold, italics, hashes, dashes, or asterisks.",
    "- Include a confidence assessment (strong, moderate, lean, or uncertain) based on source agreement and edge clarity.",
    "- Name a danger runner if the evidence supports one.",
    "- When local evidence and web sources agree on a pick, say so. When they disagree, note the disagreement in one sentence.",
    "- If the local racing bundle contributes, say so in the trace, but do not invent local citations.",
    "- Keep the trace polished and concise. Keep the answer to 80-150 words.",
    "- Prefer the strongest results — if a specific date was requested, prioritise results for that exact date first.",
    "- If the user asked about a specific date and no published tips exist yet, do NOT substitute a different date's tips. Instead, reason from whatever form data, entries, market prices, or previews are available for the requested date and give your expert assessment. Clearly note: 'Published tips for [date] aren't available yet — here's my form-based assessment:'",
    "- For upcoming races, draw on: race entries, barrier draws, recent form lines, early market prices, trainer/jockey bookings, track/distance records, and class comparisons. This is exactly what a knowledgeable punter would do.",
    "- If you genuinely find zero information about the requested race, say so briefly and suggest the user check back closer to race day — but still offer any form-based observations you can make.",
    "- You MUST name at least one horse as your selection when asked for a best bet, tip, or recommendation. Saying 'I can't find tips' is not an acceptable answer to a direct betting question. Reason from whatever data exists — entries, early market, form, class, barriers — and commit to a selection with your reasoning.",
    "- If DIRECT TIP CANDIDATES contains a horse name, answer the user's betting question directly instead of saying the tips were unavailable.",
    "- Prefer a direct named selection when the results include explicit wording like best bet, top pick, or each-way.",
    "- Only fall back to a cautious 'not enough detail' answer if DIRECT TIP CANDIDATES is empty, the [LIVE RACE CARD] is empty, and the fetched excerpts contain zero horse names.",
    "- If the evidence provides or strongly implies race number, horse number, full horse name, or whether it is the last race on the card, include that detail in the answer.",
    "- Distinguish between most likely winner, best betting value, and best play when the evidence supports it. These are not always the same horse.",
  ].join("\n");

  try {
    const response = await anthropic.messages.create({
      model: CLAUDE_MODEL,
      max_tokens: 1200,
      temperature: 0.2,
      system:
        "You are STRIDE, the definitive Australian horse racing AI. You answer every racing question with the confidence and depth of a 25-year professional form analyst and punter.\n\nFUTURE RACE PROTOCOL (CRITICAL — READ FIRST):\nWhen a user asks about a race that is days away and day-of tips are not yet published:\n1. The [LIVE RACE CARD] section (if present) contains the ACTUAL declared runners, barriers, jockeys, trainers, and early odds — this is your primary source. Treat it as authoritative.\n2. Use [WEB RESULTS] and [FETCHED PAGE EXCERPTS] to find form, sectionals, market moves, and expert commentary for runners in that field.\n3. Apply [TRACK PROFILE] to determine which barrier, running style, and distance suits the track.\n4. You MUST commit to a specific selection. Name a horse, give the barrier and jockey, then explain in 3-4 sentences: recent form trend, distance/track suitability, barrier advantage, and market position.\n5. Start your answer with the selection: 'My best bet for Race X at [Track] on [Date]: [HORSE NAME] (Barrier Y, Jockey Z).'\n6. NEVER say 'I couldn't find published tips.' NEVER hedge with 'check back closer to race day.' If a declared field exists, that field is your raw material — reason from it like an expert.\n7. If the [LIVE RACE CARD] is empty but web results mention horse names for that meeting, use those names.\n8. If no horses can be identified at all, provide the best-guess market leader based on what you know about the track/distance, and say so.\n\nReturn valid JSON only.",
      messages: [{ role: "user", content: prompt }],
    });
    return extractJsonObject<ClaudeSynthesisPayload>(extractText(response));
  } catch (error) {
    Sentry.captureException(error, { tags: { component: "chat_synthesis", type: "web" } });
    return null;
  }
}

/**
 * Extract race context (track, race number, date) from a user's natural-language question.
 * Falls back to null for any field that can't be detected.
 */
function inferRaceContextFromQuestion(message: string): { track?: string; raceNumber?: number; date?: string } | null {
  const lower = message.toLowerCase();

  // Track detection — match against known Australian tracks
  let track: string | undefined;
  for (const t of AUSTRALIAN_TRACKS) {
    if (lower.includes(t)) {
      // Capitalise track name properly
      track = t.split(" ").map((w) => w.charAt(0).toUpperCase() + w.slice(1)).join(" ");
      break;
    }
  }

  // Race number detection: "race 5", "R5", "race five", "5th race"
  let raceNumber: number | undefined;
  const raceNumMatch = lower.match(/\b(?:race\s*(\d{1,2})|r(\d{1,2})(?:\s|$|,|\?)|(\d{1,2})(?:st|nd|rd|th)\s*race)\b/);
  if (raceNumMatch) {
    raceNumber = parseInt(raceNumMatch[1] ?? raceNumMatch[2] ?? raceNumMatch[3], 10);
  }

  // Date detection: "today", "tomorrow", "saturday", explicit dates
  let date: string | undefined;
  const now = new Date();
  // Use AEST (UTC+10)
  const aestOffset = 10 * 60 * 60 * 1000;
  const aestNow = new Date(now.getTime() + aestOffset);
  if (/\btoday\b|\btonight\b/.test(lower)) {
    date = aestNow.toISOString().split("T")[0];
  } else if (/\btomorrow\b/.test(lower)) {
    const tomorrow = new Date(aestNow.getTime() + 24 * 60 * 60 * 1000);
    date = tomorrow.toISOString().split("T")[0];
  } else if (/\byesterday\b/.test(lower)) {
    const yesterday = new Date(aestNow.getTime() - 24 * 60 * 60 * 1000);
    date = yesterday.toISOString().split("T")[0];
  } else {
    // Day of week: "saturday", "sunday", etc.
    const days = ["sunday", "monday", "tuesday", "wednesday", "thursday", "friday", "saturday"];
    for (let i = 0; i < days.length; i++) {
      if (lower.includes(days[i])) {
        const currentDay = aestNow.getUTCDay();
        let diff = i - currentDay;
        if (diff <= 0) diff += 7;
        const target = new Date(aestNow.getTime() + diff * 24 * 60 * 60 * 1000);
        date = target.toISOString().split("T")[0];
        break;
      }
    }

    // Explicit date: "April 18", "18th of April", "18 April", "the 18th"
    if (!date) {
      const months: Record<string, number> = {
        january: 1, february: 2, march: 3, april: 4, may: 5, june: 6,
        july: 7, august: 8, september: 9, october: 10, november: 11, december: 12,
        jan: 1, feb: 2, mar: 3, apr: 4, jun: 6, jul: 7, aug: 8, sep: 9, oct: 10, nov: 11, dec: 12,
      };
      const explicitMatch = lower.match(/\b(\d{1,2})(?:st|nd|rd|th)?\s+(?:of\s+)?([a-z]+)\b|\b([a-z]+)\s+(\d{1,2})(?:st|nd|rd|th)?\b/);
      if (explicitMatch) {
        const day = parseInt(explicitMatch[1] ?? explicitMatch[4], 10);
        const monthStr = (explicitMatch[2] ?? explicitMatch[3] ?? "").toLowerCase();
        const month = months[monthStr];
        if (month && day >= 1 && day <= 31) {
          const year = aestNow.getUTCFullYear();
          const candidate = new Date(Date.UTC(year, month - 1, day));
          // If date already passed this year, use next year
          const adjusted = candidate < aestNow ? new Date(Date.UTC(year + 1, month - 1, day)) : candidate;
          date = adjusted.toISOString().split("T")[0];
        }
      }
    }
  }

  if (!track && !raceNumber) return null;

  // Default to today if track/race detected but no date mentioned
  if (!date) {
    date = aestNow.toISOString().split("T")[0];
  }

  return { track, raceNumber, date };
}

export async function orchestrateChatTurn(input: ChatCompletionRequest): Promise<OrchestratedChatTurn> {
  const mode = normalizeModes(input.modes);
  const warnings: string[] = [];
  // Resolve pronouns/references from conversation history before classification
  const resolvedMessage = resolveSessionReferences(input.message, input.sessionId);
  const normalizedQuestion = normalizeQuestion(resolvedMessage);
  const effectiveInput: ChatCompletionRequest = {
    ...input,
    message: normalizedQuestion.message,
  };

  // Auto-detect race context from question when client didn't provide it
  if (!effectiveInput.raceContext) {
    const inferred = inferRaceContextFromQuestion(input.message);
    if (inferred && (inferred.track || inferred.raceNumber)) {
      effectiveInput.raceContext = {
        date: inferred.date ?? new Date().toISOString().split("T")[0],
        track: inferred.track ?? "",
        raceNumber: inferred.raceNumber ?? 0,
      };
    }
  }
  const shouldSearch = mode.search;
  const conceptualQuestion = !shouldSearch
    ? identifyConceptualStrideQuestion(effectiveInput.message, input.raceContext ? {
        raceDate: input.raceContext.date,
        track: input.raceContext.track,
        raceNumber: input.raceContext.raceNumber,
        raceName: input.raceContext.raceName,
      } : undefined)
    : null;

  if (conceptualQuestion) {
    return {
      response: buildConceptualStrideResponse(conceptualQuestion, effectiveInput.message),
      mode,
      answerSource: "local",
      trace: buildConceptualStrideTrace(conceptualQuestion, mode, normalizedQuestion.assumptions),
      citations: [],
      warnings,
      promptVersion: STRIDE_PROMPT_VERSION,
    };
  }

  const shouldUseLocal = Boolean(input.raceContext) || mode.brain || !shouldSearch;

  let localTurn: StrideChatTurn | null = null;
  if (shouldUseLocal) {
    localTurn = await startStrideTurn({
      message: effectiveInput.message,
      sessionId: input.sessionId,
      context: input.raceContext
        ? {
            raceDate: input.raceContext.date,
            track: input.raceContext.track,
            raceNumber: input.raceContext.raceNumber,
            raceName: input.raceContext.raceName,
          }
        : undefined,
    });
  }

  if (!shouldSearch) {
    let response = localTurn ? buildLocalStrideResponse(localTurn.bundle) : effectiveInput.message;

    if (hasAnthropicApiKey && localTurn && localTurn.bundle.answerMode !== "insufficient") {
      const synthesis = await synthesizeLocalWithClaude(localTurn);
      if (synthesis) {
        response = formatLocalSynthesisResponse(synthesis);
      } else {
        warnings.push("STRIDE's AI synthesis is temporarily unavailable. Showing raw model data instead.");
      }
    }

    return {
      response,
      mode,
      answerSource: "local",
      trace: buildLocalTrace(mode, localTurn, warnings, normalizedQuestion.assumptions),
      citations: [],
      warnings,
      turnId: localTurn?.turnId,
      localTurnId: localTurn?.turnId,
      promptVersion: STRIDE_PROMPT_VERSION,
    };
  }

  const plan = await buildSearchPlan(effectiveInput, localTurn);

  if (!hasPerplexityApiKey) {
    warnings.push("PERPLEXITY_API_KEY is not configured, so live web search could not run.");
  }
  if (!hasAnthropicApiKey) {
    warnings.push("ANTHROPIC_API_KEY is not configured, so Claude could not analyze the retrieved results.");
  }

  const country = isAustralianRacingQuery(effectiveInput.message, input.raceContext?.track) ? "AU" : undefined;
  let searchResults: ChatSearchResultTrace[] = [];
  let searchDocuments: SearchDocument[] = [];
  let tipCandidates: TipCandidate[] = [];

  if (hasPerplexityApiKey) {
    try {
      searchResults = await runPerplexitySearch(plan.searchQueries, country);
      if (searchResults.length === 0) {
        warnings.push("Perplexity did not return any search results for this request.");
      } else {
        searchDocuments = await fetchSearchDocuments(searchResults);
        tipCandidates = extractTipCandidates(searchResults, searchDocuments, effectiveInput);
        tipCandidates = await enrichTipCandidates(tipCandidates, effectiveInput, country);
      }
    } catch (error) {
      warnings.push(error instanceof Error ? error.message : "Perplexity search failed.");
    }
  }

  // Fetch live race card BEFORE synthesis — needed for field discovery when no tip candidates
  let liveRaceCard: string | null = null;
  if (hasRacingApiCreds && effectiveInput.raceContext?.track && effectiveInput.raceContext?.date) {
    liveRaceCard = await fetchLiveRaceCard(effectiveInput.raceContext.track, effectiveInput.raceContext.date);
  }

  if (!hasAnthropicApiKey || searchResults.length === 0) {
    const trace = buildSearchFallbackTrace(mode, plan, searchResults, warnings, normalizedQuestion.assumptions);
    const topCandidate = tipCandidates[0];
    return {
      response: topCandidate ? buildTipCandidateFallbackAnswer(topCandidate, effectiveInput) : buildSearchAnswerUnavailableMessage(warnings, localTurn),
      mode,
      answerSource: topCandidate ? "web" : localTurn ? "local" : "error",
      trace,
      citations: buildCitationLinks(searchResults),
      warnings,
      turnId: localTurn?.turnId,
      localTurnId: localTurn?.turnId,
      promptVersion: STRIDE_PROMPT_VERSION,
    };
  }

  // Fix A/B: When no tip candidates found, run extra searches to get the declared field and horse form
  if (hasPerplexityApiKey && tipCandidates.length === 0) {
    const { isoDate: todayIso } = getSydneyNowParts();
    const raceDate = effectiveInput.raceContext?.date ?? "";
    const isFutureRace = raceDate > todayIso;
    const track = effectiveInput.raceContext?.track ?? inferTrackFromMessage(effectiveInput.message);

    // Fix A: Form searches for each horse in the live race card
    if (liveRaceCard) {
      const declaredHorses = extractHorsesFromLiveCard(liveRaceCard).slice(0, 5);
      if (declaredHorses.length > 0) {
        const formQueries: ChatSearchQueryTrace[] = declaredHorses.map(horse => ({
          query: `${horse} form guide racing Australia 2026`,
          rationale: `Form search for declared runner ${horse}`,
        }));
        try {
          const formResults = await runPerplexitySearch(formQueries.slice(0, 3), country);
          const formDocs = await fetchSearchDocuments(formResults);
          searchResults = [...searchResults, ...formResults];
          searchDocuments = [...searchDocuments, ...formDocs];
        } catch { /* form searches are best-effort */ }
      }
    }

    // Fix B: Field discovery search for future races with no candidates
    if (isFutureRace && track) {
      const discoveryQueries: ChatSearchQueryTrace[] = [
        {
          query: `${track} ${raceDate || effectiveInput.message} final field barrier draw declared runners 2026`,
          rationale: "Find the declared field and barrier draw for this meeting",
        },
        {
          query: `${track} ${raceDate || ""} best bets early market TAB favourites preview 2026`,
          rationale: "Find early market movers and expert previews",
        },
      ];
      try {
        const discoveryResults = await runPerplexitySearch(discoveryQueries, country);
        const discoveryDocs = await fetchSearchDocuments(discoveryResults);
        searchResults = [...searchResults, ...discoveryResults];
        searchDocuments = [...searchDocuments, ...discoveryDocs];
        // Re-extract tip candidates from the expanded result set
        const newCandidates = extractTipCandidates(discoveryResults, discoveryDocs, effectiveInput);
        tipCandidates = [...tipCandidates, ...newCandidates];
      } catch { /* discovery searches are best-effort */ }
    }
  }

  const synthesis = await synthesizeWithClaude({
    input: effectiveInput,
    mode,
    localTurn,
    plan,
    searchResults,
    searchDocuments,
    tipCandidates,
    liveRaceCard,
  });

  if (!synthesis) {
    warnings.push("Claude did not return a valid synthesis payload.");
    const trace = buildSearchFallbackTrace(mode, plan, searchResults, warnings, normalizedQuestion.assumptions);
    return {
      response: buildSearchAnswerUnavailableMessage(warnings, localTurn),
      mode,
      answerSource: localTurn ? "local" : "error",
      trace,
      citations: buildCitationLinks(searchResults),
      warnings,
      turnId: localTurn?.turnId,
      localTurnId: localTurn?.turnId,
      promptVersion: STRIDE_PROMPT_VERSION,
    };
  }

  const selectedMap = new Map(synthesis.selected_results.map((entry) => [entry.id, entry]));
  const topCandidate = tipCandidates[0];
  const enrichedResults = searchResults.map((result) => {
    const selection = selectedMap.get(result.id);
    const isTopCandidate = topCandidate?.resultId === result.id;
    if (selection || isTopCandidate) {
      return {
        ...result,
        selected: true,
        reason:
          selection?.reason ||
          `Selected because the source contains a direct ${formatBetTypeLabel(topCandidate.betType)} phrase for ${topCandidate.horse}.`,
        extracted: selection?.extracted || topCandidate?.evidence,
      };
    }

    return result;
  });

  const sections: ChatTraceSection[] = synthesis.sections
    .filter((section) => Array.isArray(section.bullets) && section.title)
    .slice(0, 4)
    .map((section, index) => ({
      id: `${section.kind}-${index}`,
      kind: section.kind,
      title: section.title,
      summary: section.summary,
      bullets: section.bullets.slice(0, 4),
    }));

  if (sections[0]) {
    sections[0] = {
      ...sections[0],
      bullets: uniqueStrings([...normalizedQuestion.assumptions, ...sections[0].bullets]).slice(0, 4),
    };
  }

  if (topCandidate && sections[3]) {
    const impliedRaceNumber =
      topCandidate.raceNumber ??
      (topCandidate.meetingRaceCount && tipImpliesLastRace(topCandidate) ? topCandidate.meetingRaceCount : undefined);
    const candidateDetail =
      topCandidate.horseNumber && impliedRaceNumber
        ? `Direct tip phrase found: No. ${topCandidate.horseNumber} ${topCandidate.horse} in Race ${impliedRaceNumber} as the ${formatBetTypeLabel(topCandidate.betType)}.`
        : impliedRaceNumber
          ? `Direct tip phrase found: ${topCandidate.horse} in Race ${impliedRaceNumber} as the ${formatBetTypeLabel(topCandidate.betType)}.`
          : `Direct tip phrase found: ${topCandidate.horse} as the ${formatBetTypeLabel(topCandidate.betType)}.`;
    sections[3] = {
      ...sections[3],
      bullets: uniqueStrings([
        candidateDetail,
        ...sections[3].bullets,
      ]).slice(0, 4),
    };
  }

  let trace: ChatTrace = {
    label: mode.brain ? "Search and reasoning trace" : "Search trace",
    headline: synthesis.headline,
    summary: synthesis.summary,
    sections,
    searchQueries: plan.searchQueries,
    searchResults: enrichedResults,
    finalWhy: synthesis.final_why,
  };

  const answerSource: ChatAnswerSource =
    localTurn && enrichedResults.some((result) => result.selected) ? "hybrid" : "web";
  const citations = buildCitationLinks(enrichedResults);
  const candidateDrivenAnswer =
    topCandidate && answerNeedsDirectSelection(synthesis.answer)
      ? buildTipCandidateFallbackAnswer(topCandidate, effectiveInput)
      : synthesis.answer;
  const fallbackAnswer =
    topCandidate && (
      answerNeedsStructuredTipFallback(candidateDrivenAnswer, topCandidate) ||
      (!answerNeedsDirectSelection(candidateDrivenAnswer) && (candidateDrivenAnswer.length < 120 || !topCandidate.raceNumber))
    )
      ? buildTipCandidateFallbackAnswer(topCandidate, effectiveInput)
      : candidateDrivenAnswer;
  const sanitizedAnswer = sanitizeAnswerFormatting(fallbackAnswer);
  const usedTipFallback = fallbackAnswer !== synthesis.answer;
  if (usedTipFallback && topCandidate) {
    const impliedRaceNumber =
      topCandidate.raceNumber ??
      (topCandidate.meetingRaceCount && tipImpliesLastRace(topCandidate) ? topCandidate.meetingRaceCount : undefined);
    trace = {
      ...trace,
      headline: `Search found a direct ${formatBetTypeLabel(topCandidate.betType)} and answered with it.`,
      summary:
        topCandidate.horseNumber && impliedRaceNumber
          ? `The live results included an explicit ${formatBetTypeLabel(topCandidate.betType)} phrase for No. ${topCandidate.horseNumber} ${topCandidate.horse} in Race ${impliedRaceNumber}, so the answer now names that selection directly.`
          : impliedRaceNumber
            ? `The live results included an explicit ${formatBetTypeLabel(topCandidate.betType)} phrase for ${topCandidate.horse} in Race ${impliedRaceNumber}, so the answer now names that selection directly.`
          : `The live results included an explicit ${formatBetTypeLabel(topCandidate.betType)} phrase for ${topCandidate.horse}, so the answer now names that selection directly.`,
      finalWhy:
        topCandidate.horseNumber && impliedRaceNumber
          ? `A selected live source explicitly identified No. ${topCandidate.horseNumber} ${topCandidate.horse} in Race ${impliedRaceNumber} as the ${formatBetTypeLabel(topCandidate.betType)}.`
          : impliedRaceNumber
            ? `A selected live source explicitly identified ${topCandidate.horse} in Race ${impliedRaceNumber} as the ${formatBetTypeLabel(topCandidate.betType)}.`
          : `A selected live source explicitly named ${topCandidate.horse} as the ${formatBetTypeLabel(topCandidate.betType)}.`,
    };
  }
  const answerHasInlineLinks = /\[[^\]]+\]\(https?:\/\/[^\s)]+\)/.test(sanitizedAnswer);
  const fallbackSourceLine =
    !answerHasInlineLinks && citations.length > 0
      ? `\n\nSources: ${citations
          .slice(0, 3)
          .map((citation) => `[${citation.domain}](${citation.url})`)
          .join(", ")}`
      : "";

  return {
    response: `${sanitizedAnswer}${fallbackSourceLine}`,
    mode,
    answerSource,
    trace,
    citations,
    warnings: uniqueStrings([...(synthesis.warnings ?? []), ...warnings]),
    turnId: localTurn?.turnId,
    localTurnId: localTurn?.turnId,
    promptVersion: STRIDE_PROMPT_VERSION,
  };
}
