import { and, asc, desc, eq, ilike, inArray, or, sql } from "drizzle-orm";
import fs from "node:fs";
import { readFile } from "node:fs/promises";
import path from "node:path";
import { db } from "./db";
import {
  blackbookEntries,
  blackbookEntryRuns,
  frankingScores,
  horseRaceAnalyses,
  predictionAudit,
  raceAnalyses,
  raceResultsHistory,
  races,
  sectionalTimes,
  selections,
  selectionResults,
  strideTipResults,
  performanceStats,
} from "@shared/schema";

type JsonRecord = Record<string, unknown>;

export type StrideIntent =
  | "horse_history"
  | "race_preview"
  | "race_result"
  | "track_profile"
  | "meeting_summary"
  | "sectionals"
  | "franking"
  | "consensus"
  | "market"
  | "blackbook"
  | "performance"
  | "model_quality"
  | "jockey_trainer"
  | "quaddie_build"
  | "speed_map"
  | "market_scan"
  | "blackbook_today"
  | "meeting_overview"
  | "horse_comparison"
  | "available_data"
  | "general";

export type StrideEvidenceSource =
  | "local_racecard"
  | "local_tips"
  | "local_tip_results"
  | "local_consensus"
  | "local_market_signals"
  | "local_intelligence"
  | "race_results_history"
  | "sectional_times"
  | "franking_scores"
  | "prediction_audit"
  | "stride_tip_results"
  | "race_analyses"
  | "horse_race_analyses"
  | "races"
  | "selections"
  | "selection_results"
  | "blackbook_entries"
  | "blackbook_entry_runs"
  | "performance_stats"
  | "convergence_output";

export interface StrideEntityHints {
  track?: string;
  trackKey?: string;
  raceNumber?: number;
  raceName?: string;
  horseName?: string;
  date?: string;
  dateSource?: "explicit" | "relative" | "context" | "current" | "latest-local";
  jockey?: string;
  trainer?: string;
}

export interface StrideQuestionClassification {
  intent: StrideIntent;
  confidence: number;
  tags: string[];
  rationale: string[];
  needsClarification: boolean;
}

export interface StrideEvidenceItem {
  source: StrideEvidenceSource;
  title: string;
  summary: string;
  track?: string;
  raceNumber?: number;
  raceDate?: string;
  horseName?: string;
  score: number;
  payload?: JsonRecord;
}

export interface StrideRetrievalRequest {
  question: string;
  context?: {
    track?: string;
    raceNumber?: number;
    raceDate?: string;
    horseName?: string;
    raceName?: string;
    jockey?: string;
    trainer?: string;
  };
  limit?: number;
}

export interface StrideLocalArtifactBundle {
  datesLoaded: string[];
  latestLocalDate?: string;
  racecards: LocalRacecardMatch[];
  tips: LocalTipMatch[];
  tipResults: LocalTipResultMatch[];
  consensus: LocalJsonMatch[];
  marketSignals: LocalJsonMatch[];
  intelligence: LocalJsonMatch[];
}

export interface StrideRetrievalBundle {
  question: string;
  classification: StrideQuestionClassification;
  entities: StrideEntityHints;
  dateCandidates: string[];
  trackCandidates: string[];
  horseCandidates: string[];
  localArtifacts: StrideLocalArtifactBundle;
  evidence: StrideEvidenceItem[];
  answerMode: "evidence-rich" | "evidence-light" | "insufficient";
  summary: string;
  promptContext: string;
  sourceCounts: Record<StrideEvidenceSource, number>;
  followUps: string[];
}

interface LocalRacecardMeeting {
  date?: string;
  course?: string;
  meet_id?: string;
  races?: LocalRacecardRace[];
}

interface LocalRacecardRace {
  course?: string;
  date?: string;
  distance?: string;
  going?: string;
  class?: string;
  race_name?: string;
  race_number?: string | number;
  off_time?: string;
  field_size?: number;
  runners?: LocalRacecardRunner[];
}

interface LocalRacecardRunner {
  horse_id?: string;
  horse?: string;
  number?: string | number;
  draw?: string | number;
  jockey?: string;
  trainer?: string;
  form?: string;
  comment?: string;
  scratched?: boolean;
  odds?: Array<Record<string, unknown>>;
  silk_url?: string;
  rating?: string | number;
  sp?: string | number | null;
}

interface LocalTipsFile {
  date?: string;
  generated_at?: string;
  races?: LocalTipsRace[];
  best_bets?: unknown[];
  bankers?: unknown[];
  value_plays?: unknown[];
}

interface LocalTipsRace {
  track?: string;
  race_number?: string | number;
  race_name?: string;
  race_class?: string;
  going?: string;
  distance?: string;
  full_field?: LocalTipsRunner[];
  top_picks?: LocalTipsRunner[];
  bet_pick?: LocalTipsRunner | null;
  primary_pick?: LocalTipsRunner | null;
  bet_status?: string;
  pick_contract?: string;
  best_bets?: LocalTipsRunner[];
}

interface LocalTipsRunner {
  horse?: string;
  saddle_number?: string | number;
  number?: string | number;
  rank?: number;
  tip_rank?: number;
  odds?: number | string;
  win_pct?: number;
  place_pct?: number;
  edge_pct?: number;
  confidence?: string;
  selection_origin?: string;
  selection_origin_reason?: string;
  should_bet?: boolean;
  market_rank?: number;
  crowd_score?: number;
  crowd_classification?: string;
  crowd_gate_reason?: string;
}

interface LocalTipResultFile {
  date?: string;
  collected_at?: string;
  results?: unknown[];
}

interface LocalJsonMatch {
  source: StrideEvidenceSource;
  title: string;
  summary: string;
  track?: string;
  raceNumber?: number;
  horseName?: string;
  payload?: JsonRecord;
  score: number;
}

interface LocalRacecardMatch extends LocalJsonMatch {
  raceDate?: string;
}

interface LocalTipMatch extends LocalJsonMatch {
  raceDate?: string;
}

interface LocalTipResultMatch extends LocalJsonMatch {
  raceDate?: string;
}

const PROJECT_ROOT = process.cwd();
const RACECARDS_DIR = path.join(PROJECT_ROOT, "racecards");
const INTELLIGENCE_DIR = path.join(PROJECT_ROOT, "server", "python", "intelligence");

const TEXT_STOPWORDS = new Set([
  "how",
  "who",
  "what",
  "when",
  "where",
  "why",
  "which",
  "is",
  "are",
  "am",
  "do",
  "does",
  "did",
  "can",
  "could",
  "would",
  "should",
  "will",
  "explain",
  "tell",
  "show",
  "give",
  "ask stride",
  "current conversation",
  "new chat",
  "the track board",
  "best bets",
  "race day",
  "race day board",
  "execution board",
  "track board",
  "ask about",
  "analyze",
  "analyse",
  "what factors",
  "how does",
  "explain",
  "today's",
  "todays",
  "flemington",
  "randwick",
  "rosehill",
  "caulfield",
  "moonee valley",
  "warwick farm",
  "sandown",
  "geelong",
  "doomben",
  "eagle farm",
  "ascot",
]);

const COMMON_TRACK_ALIASES: Record<string, string[]> = {
  randwick: ["randwick", "royal randwick", "randwick-kensington", "kensington"],
  rosehill: ["rosehill", "rosehill gardens", "rosehill gardens racecourse"],
  flemington: ["flemington", "tab flemington"],
  caulfield: ["caulfield"],
  "moonee valley": ["moonee valley", "mooneevalley"],
  sandown: ["sandown", "sportsbet sandown lakeside", "sandown lakeside"],
  geelong: ["geelong", "ladbrokes geelong"],
  "warwick farm": ["warwick farm"],
  doomben: ["doomben"],
  "eagle farm": ["eagle farm", "eaglefarm"],
  ascot: ["ascot", "ascot wa", "ascotwa"],
  morphettville: ["morphettville"],
  "sunshine coast": ["sunshine coast"],
  "gold coast": ["gold coast"],
  ipswich: ["ipswich"],
  toowoomba: ["toowoomba"],
  ballarat: ["ballarat"],
  bendigo: ["bendigo"],
  mornington: ["mornington"],
  cranbourne: ["cranbourne"],
  pakenham: ["pakenham"],
  gosford: ["gosford"],
  newcastle: ["newcastle"],
  kembla: ["kembla grange", "kembla"],
  hawkesbury: ["hawkesbury"],
  wagga: ["wagga", "wagga wagga"],
  goulburn: ["goulburn"],
  tamworth: ["tamworth"],
  wyong: ["wyong"],
  bunbury: ["bunbury"],
  albany: ["albany"],
  geraldton: ["geraldton"],
  launceston: ["launceston"],
  hobart: ["hobart"],
};

const QUESTION_INTENT_PRIORITY: Array<{ intent: StrideIntent; keywords: string[] }> = [
  { intent: "race_result", keywords: ["result", "who won", "finish", "placed", "place", "sp", "starting price", "won at"] },
  { intent: "horse_history", keywords: ["horse", "runner", "how did", "what about", "profile", "form", "career", "last start", "last run"] },
  { intent: "sectionals", keywords: ["sectional", "last 600", "last 400", "last 200", "speed map", "pace", "run on", "closing"] },
  { intent: "franking", keywords: ["franking", "deep franked", "group listed", "crowd", "consensus", "crowd score"] },
  { intent: "blackbook", keywords: ["blackbook", "follow", "tracker", "alert"] },
  { intent: "market", keywords: ["market", "odds", "price", "steam", "drift", "fluc", "shorten", "suspend"] },
  { intent: "track_profile", keywords: ["track bias", "track condition", "barrier", "distance", "going", "rail", "tempo", "bias"] },
  { intent: "performance", keywords: ["roi", "strike rate", "hit rate", "performance", "calibration", "backtest", "roi by"] },
  { intent: "consensus", keywords: ["consensus", "crowd", "tipster", "best bet", "best bets", "tipsters", "crowd-only"] },
  { intent: "model_quality", keywords: ["algorithm", "model", "edge calculation", "selection score", "confidence", "why did"] },
];

const RACE_NAME_TOKENS = ["stakes", "handicap", "mile", "cup", "plate", "derby", "guineas", "classic", "sprint", "challenge"];

const jsonCache = new Map<string, { mtimeMs: number; data: unknown }>();
let knownTrackCatalogCache: Promise<string[]> | null = null;

function normalizeText(value: unknown): string {
  return String(value ?? "")
    .toLowerCase()
    .replace(/[\u2018\u2019\u201c\u201d]/g, '"')
    .replace(/[^a-z0-9]+/g, " ")
    .trim();
}

function normalizeCompact(value: unknown): string {
  return normalizeText(value).replace(/\s+/g, "");
}

function isNonEmptyString(value: unknown): value is string {
  return typeof value === "string" && value.trim().length > 0;
}

function uniqueStrings(values: Array<string | undefined | null>): string[] {
  return [...new Set(values.filter(isNonEmptyString).map((value) => value.trim()))];
}

function parseIsoDate(value: string | undefined | null): string | undefined {
  if (!value) return undefined;
  const match = String(value).trim().match(/^(\d{4}-\d{2}-\d{2})/);
  return match?.[1];
}

function getSydneyDate(offsetDays = 0): string {
  const now = new Date();
  const syd = new Date(
    new Intl.DateTimeFormat("en-CA", {
      timeZone: "Australia/Sydney",
      year: "numeric",
      month: "2-digit",
      day: "2-digit",
    }).format(now),
  );
  syd.setDate(syd.getDate() + offsetDays);
  return syd.toLocaleDateString("en-CA", { timeZone: "Australia/Sydney" });
}

function compactSqlParts<T>(parts: Array<T | null | undefined | false>): T[] {
  return parts.filter(Boolean) as T[];
}

async function safeArrayQuery<T>(label: string, query: Promise<T[]>): Promise<T[]> {
  try {
    return await query;
  } catch (error) {
    const message = error instanceof Error ? error.message : String(error);
    console.warn(`[Stride Retrieval] Skipping ${label}: ${message}`);
    return [];
  }
}

function scoreMatch(haystack: string, needles: string[]): number {
  const hay = normalizeCompact(haystack);
  let score = 0;
  for (const needle of needles) {
    const compact = normalizeCompact(needle);
    if (!compact) continue;
    if (hay === compact) score += 20;
    else if (hay.includes(compact) || compact.includes(hay)) score += 10;
    else if (normalizeText(haystack).includes(normalizeText(needle))) score += 6;
  }
  return score;
}

function buildTrackVariants(track?: string): string[] {
  if (!track) return [];
  const base = normalizeText(track);
  const compact = normalizeCompact(track);
  const pieces = base.split(/\s+/).filter(Boolean);
  const variants = new Set<string>([track, base, compact, ...pieces]);

  for (const [canonical, aliases] of Object.entries(COMMON_TRACK_ALIASES)) {
    const aliasSet = [canonical, ...aliases];
    const match = aliasSet.some((alias) => {
      const aliasNorm = normalizeCompact(alias);
      return compact.includes(aliasNorm) || aliasNorm.includes(compact);
    });
    if (match) {
      aliasSet.forEach((alias) => variants.add(alias));
    }
  }

  return [...variants].filter(Boolean);
}

async function readJsonIfExists<T = unknown>(filePath: string): Promise<T | undefined> {
  try {
    const stat = await fs.promises.stat(filePath);
    const cached = jsonCache.get(filePath);
    if (cached && cached.mtimeMs === stat.mtimeMs) {
      return cached.data as T;
    }
    const raw = await readFile(filePath, "utf-8");
    const parsed = JSON.parse(raw) as T;
    jsonCache.set(filePath, { mtimeMs: stat.mtimeMs, data: parsed });
    return parsed;
  } catch {
    return undefined;
  }
}

function readJsonIfExistsSync<T = unknown>(filePath: string): T | undefined {
  try {
    const stat = fs.statSync(filePath);
    const cached = jsonCache.get(filePath);
    if (cached && cached.mtimeMs === stat.mtimeMs) {
      return cached.data as T;
    }
    const raw = fs.readFileSync(filePath, "utf-8");
    const parsed = JSON.parse(raw) as T;
    jsonCache.set(filePath, { mtimeMs: stat.mtimeMs, data: parsed });
    return parsed;
  } catch {
    return undefined;
  }
}

function listJsonDatesFromDir(dir: string, prefix: string): string[] {
  try {
    return fs
      .readdirSync(dir)
      .map((fileName) => fileName.match(new RegExp(`^${prefix}_(\\d{4}-\\d{2}-\\d{2})\\.json$`))?.[1] ?? null)
      .filter((dateStr): dateStr is string => Boolean(dateStr))
      .sort();
  } catch {
    return [];
  }
}

function listLocalTipDates(): string[] {
  return listJsonDatesFromDir(RACECARDS_DIR, "tips");
}

function listLocalRacecardDates(): string[] {
  return listJsonDatesFromDir(RACECARDS_DIR, "racecard");
}

function getLatestLocalDate(): string | undefined {
  const dates = uniqueStrings([...listLocalTipDates(), ...listLocalRacecardDates()]);
  return dates.at(-1);
}

async function loadKnownTrackCatalog(): Promise<string[]> {
  if (knownTrackCatalogCache) return knownTrackCatalogCache;
  knownTrackCatalogCache = (async () => {
    const fromLocal = new Set<string>();
    for (const date of [...listLocalRacecardDates().slice(-21), ...listLocalTipDates().slice(-21)]) {
      const racecard = await readJsonIfExists<LocalRacecardMeeting[]>(path.join(RACECARDS_DIR, `racecard_${date}.json`));
      for (const meeting of racecard ?? []) {
        if (meeting.course) fromLocal.add(meeting.course);
      }
    }

    const fromDb = new Set<string>();
    try {
      const recentTracks = await db
        .selectDistinct({ track: races.track })
        .from(races)
        .limit(250);
      for (const row of recentTracks) {
        if (row.track) fromDb.add(row.track);
      }
    } catch {
      // Non-fatal: track catalog is just a hint layer.
    }

    return [...new Set([...fromLocal, ...fromDb])].sort((a, b) => a.localeCompare(b));
  })();
  return knownTrackCatalogCache;
}

function resolveRelativeDateTokens(question: string): string[] {
  const q = normalizeText(question);
  const current = getSydneyDate();
  const yesterday = getSydneyDate(-1);
  const tomorrow = getSydneyDate(1);
  const dates = new Set<string>();
  if (/\b(today|tonight|this morning|this arvo)\b/.test(q)) dates.add(current);
  if (/\b(yesterday|last night)\b/.test(q)) dates.add(yesterday);
  if (/\b(tomorrow|next day)\b/.test(q)) dates.add(tomorrow);

  // Day-of-week resolution
  const dayNames = ["sunday", "monday", "tuesday", "wednesday", "thursday", "friday", "saturday"];
  const sydneyNow = new Date(current + "T12:00:00+10:00");
  const currentDayIndex = sydneyNow.getDay();

  for (let i = 0; i < dayNames.length; i++) {
    const dayName = dayNames[i];
    // "last saturday" → most recent past occurrence
    const lastPattern = new RegExp(`\\blast\\s+${dayName}\\b`);
    // "next saturday" → next occurrence after this week
    const nextPattern = new RegExp(`\\bnext\\s+${dayName}\\b`);
    // "this saturday" or just "saturday" → upcoming occurrence (or today if it matches)
    const thisPattern = new RegExp(`\\b(this\\s+)?${dayName}\\b`);

    if (lastPattern.test(q)) {
      let diff = currentDayIndex - i;
      if (diff <= 0) diff += 7;
      dates.add(getSydneyDate(-diff));
    } else if (nextPattern.test(q)) {
      let diff = i - currentDayIndex;
      if (diff <= 0) diff += 7;
      // "next" means the one AFTER this week
      if (diff <= 7) diff += 7;
      dates.add(getSydneyDate(diff));
    } else if (thisPattern.test(q)) {
      let diff = i - currentDayIndex;
      if (diff < 0) diff += 7;
      if (diff === 0) dates.add(current);
      else dates.add(getSydneyDate(diff));
    }
  }

  const explicit = question.match(/\b\d{4}-\d{2}-\d{2}\b/g) ?? [];
  explicit.forEach((d) => dates.add(d));
  return [...dates];
}

async function resolveTrackCandidates(question: string, context?: StrideRetrievalRequest["context"]): Promise<string[]> {
  const text = normalizeText(question);
  const catalog = await loadKnownTrackCatalog();
  const variants = new Set<string>();
  const contextTrack = context?.track?.trim();
  if (contextTrack) variants.add(contextTrack);
  for (const track of catalog) {
    const trackNorm = normalizeCompact(track);
    if (trackNorm && normalizeCompact(text).includes(trackNorm)) {
      variants.add(track);
    }
    for (const alias of buildTrackVariants(track)) {
      if (normalizeCompact(text).includes(normalizeCompact(alias))) {
        variants.add(track);
        variants.add(alias);
      }
    }
  }
  if (contextTrack) {
    variants.add(contextTrack);
    buildTrackVariants(contextTrack).forEach((alias) => variants.add(alias));
  }
  return [...variants];
}

function extractHorseCandidates(question: string, context?: StrideRetrievalRequest["context"]): string[] {
  const candidates = new Set<string>();
  if (context?.horseName) candidates.add(context.horseName.trim());

  const quoted = question.match(/["'“”]([^"'“”]{2,80})["'“”]/g) ?? [];
  for (const phrase of quoted) {
    const clean = phrase.replace(/["'“”]/g, "").trim();
    if (clean && clean.length <= 80) candidates.add(clean);
  }

  const capitalised = question.match(/\b([A-Z][A-Za-z'’-]*(?:\s+[A-Z][A-Za-z'’-]*){0,4})\b/g) ?? [];
  for (const phrase of capitalised) {
    const norm = normalizeText(phrase);
    if (!norm || TEXT_STOPWORDS.has(norm)) continue;
    if (norm.length < 3) continue;
    if (/\b(race|track|day|board|bet|chat|stride|best)\b/.test(norm)) continue;
    if (RACE_NAME_TOKENS.some((token) => norm.includes(token))) continue;
    if (buildTrackSearchTerms().some((track) => normalizeCompact(track) === normalizeCompact(phrase))) continue;
    candidates.add(phrase.trim());
  }

  return [...candidates];
}

// Intent pattern groups with weighted regex matching
const INTENT_PATTERNS: Array<{ intent: StrideIntent; patterns: Array<{ regex: RegExp; weight: number }>; baseConfidence: number }> = [
  {
    intent: "quaddie_build",
    baseConfidence: 0.93,
    patterns: [
      { regex: /\b(quaddie|first four|trifecta|exacta|exotic)\b/, weight: 15 },
      { regex: /\bbuild me a\b/, weight: 15 },
      { regex: /\bleg\s*[1-4]\b/, weight: 12 },
      { regex: /\b(build|construct).*(multi|banker|exotic)\b/, weight: 12 },
    ],
  },
  {
    intent: "meeting_overview",
    baseConfidence: 0.92,
    patterns: [
      { regex: /\b(summary|overview|rundown)\b.*(card|meeting|today|day)\b/, weight: 15 },
      { regex: /\b(card|meeting)\b.*(summary|overview|rundown)\b/, weight: 15 },
      { regex: /\bbest plays?\b.*(across|card|meeting|today)\b/, weight: 14 },
      { regex: /\bwhat.?s on today\b/, weight: 14 },
      { regex: /\bgive me the card\b/, weight: 13 },
      { regex: /\b(full|all|whole|every)\s*(meeting|card|races?)\b/, weight: 13 },
      { regex: /\bwhat.?s (racing|running)\b/, weight: 10 },
      { regex: /\bcard (analysis|breakdown)\b/, weight: 12 },
    ],
  },
  {
    intent: "horse_comparison",
    baseConfidence: 0.92,
    patterns: [
      { regex: /\bcompare\b/, weight: 15 },
      { regex: /\bhead to head\b/, weight: 15 },
      { regex: /\bvs\.?\b/, weight: 12 },
      { regex: /\bwhich is better\b/, weight: 12 },
      { regex: /\bbetween\b.+\band\b/, weight: 8 },
      { regex: /\b(\w+)\s+or\s+(\w+)\b.*\b(better|back|prefer)\b/, weight: 10 },
    ],
  },
  {
    intent: "market_scan",
    baseConfidence: 0.9,
    patterns: [
      { regex: /\b(steaming|who.?s steaming|who is steaming)\b/, weight: 15 },
      { regex: /\bsharp money\b/, weight: 14 },
      { regex: /\b(who drifted|most backed|market moves)\b/, weight: 13 },
      { regex: /\b(steam|firming)\b.*(today|this)\b/, weight: 12 },
      { regex: /\boverlays?\b/, weight: 10 },
      { regex: /\bwhere.?s the money\b/, weight: 12 },
      { regex: /\bwho.?s being backed\b/, weight: 12 },
    ],
  },
  {
    intent: "blackbook_today",
    baseConfidence: 0.9,
    patterns: [
      { regex: /\bblackbook\b.*(running|today|this week|nominated)\b/, weight: 15 },
      { regex: /\b(tracked|watch\s*list)\s*horses?\b/, weight: 13 },
      { regex: /\bunlucky horses?\s*running\b/, weight: 12 },
      { regex: /\bany of my blackbook\b/, weight: 14 },
    ],
  },
  {
    intent: "speed_map",
    baseConfidence: 0.9,
    patterns: [
      { regex: /\b(speed map|pace map|map the race|race shape)\b/, weight: 15 },
      { regex: /\b(who leads|who sets the pace|front runners?)\b/, weight: 13 },
      { regex: /\btempo\b/, weight: 10 },
      { regex: /\bbackmarkers?\b/, weight: 10 },
      { regex: /\bon pace\b/, weight: 8 },
    ],
  },
  {
    intent: "performance",
    baseConfidence: 0.9,
    patterns: [
      { regex: /\b(roi|strike rate|hit rate|backtest)\b/, weight: 15 },
      { regex: /\bcalibration\b/, weight: 12 },
      { regex: /\b(how.?s stride|how is stride)\b.*(going|doing|performing)\b/, weight: 15 },
      { regex: /\bhow (am i|are we) (going|doing)\b/, weight: 14 },
      { regex: /\b(profit|loss|p.?l)\b/, weight: 12 },
      { regex: /\bresults? so far\b/, weight: 12 },
      { regex: /\bare we (winning|profitable|making)\b/, weight: 13 },
      { regex: /\b(performance|track record)\b.*(this|last|month|week)\b/, weight: 13 },
      { regex: /\bhow.?s (the|my) model (doing|going|performing)\b/, weight: 14 },
      { regex: /\bshow me.*(performance|results|stats|numbers)\b/, weight: 12 },
    ],
  },
  {
    intent: "jockey_trainer",
    baseConfidence: 0.88,
    patterns: [
      { regex: /\b(jockey|trainer|strapper)\b/, weight: 12 },
      { regex: /\b(waller|freedman|mcevoy|pike|bowman|mcdonald|lane|oliver|moody|price|hawkes|maher|eustace|lyons|cummings|waterhouse|lees|thompson|smith)\b/, weight: 10 },
      { regex: /\bstable\b(?!.*\b(track|condition|ground)\b)/, weight: 8 },
      { regex: /\bbooking\b(?!.*odds)/, weight: 8 },
    ],
  },
  {
    intent: "consensus",
    baseConfidence: 0.92,
    patterns: [
      { regex: /\bconsensus\b/, weight: 15 },
      { regex: /\b(crowd|tipsters?)\b/, weight: 12 },
      { regex: /\bwhat do the tipsters\b/, weight: 15 },
      { regex: /\bcrowd fav(ou)?rite\b/, weight: 13 },
      { regex: /\bwho.?s popular\b/, weight: 11 },
      { regex: /\bexternal (support|confirmation)\b/, weight: 12 },
      { regex: /\bconvergence\b/, weight: 14 },
      { regex: /\bthree.?pillar\b/, weight: 14 },
    ],
  },
  {
    intent: "franking",
    baseConfidence: 0.9,
    patterns: [
      { regex: /\bfranking\b/, weight: 15 },
      { regex: /\bdeep franked\b/, weight: 15 },
      { regex: /\b(form graph|form quality|elo)\b/, weight: 10 },
    ],
  },
  {
    intent: "sectionals",
    baseConfidence: 0.9,
    patterns: [
      { regex: /\bsectional\b/, weight: 15 },
      { regex: /\blast (600|400|200)\b/, weight: 14 },
      { regex: /\b(closing split|finishing burst|late speed)\b/, weight: 12 },
      { regex: /\brun on\b/, weight: 6 },
    ],
  },
  {
    intent: "blackbook",
    baseConfidence: 0.88,
    patterns: [
      { regex: /\bblackbook\b/, weight: 12 },
      { regex: /\b(follow|tracker|watch\s*list)\b/, weight: 8 },
      { regex: /\bluckless\b/, weight: 10 },
    ],
  },
  {
    intent: "market",
    baseConfidence: 0.84,
    patterns: [
      { regex: /\b(market|odds|drift|steam|price)\b/, weight: 10 },
      { regex: /\b(firming|shortening|lengthening)\b/, weight: 10 },
      { regex: /\bprice movement\b/, weight: 12 },
      { regex: /\bwho.?s (firming|drifting)\b/, weight: 12 },
    ],
  },
  {
    intent: "model_quality",
    baseConfidence: 0.84,
    patterns: [
      { regex: /\b(algorithm|selection score|edge calculation)\b/, weight: 14 },
      { regex: /\bhow does.*(model|algorithm|stride).*(work|calculate)\b/, weight: 13 },
      { regex: /\bexplain.*(model|edge|algorithm)\b/, weight: 12 },
      { regex: /\bmonte carlo\b/, weight: 12 },
    ],
  },
  {
    intent: "race_result",
    baseConfidence: 0.8,
    patterns: [
      { regex: /\bresult\b/, weight: 10 },
      { regex: /\bwho won\b/, weight: 14 },
      { regex: /\bfinish(ed)?\b/, weight: 8 },
      { regex: /\bstarting price\b/, weight: 10 },
      { regex: /\bhow did.*(go|run|finish)\b/, weight: 12 },
    ],
  },
  {
    intent: "race_preview",
    baseConfidence: 0.88,
    patterns: [
      { regex: /\bwho should i back\b/, weight: 15 },
      { regex: /\bwho.?s the go\b/, weight: 14 },
      { regex: /\bwhat.?s the (play|bet|pick)\b/, weight: 14 },
      { regex: /\bwho (wins?|do you like)\b/, weight: 13 },
      { regex: /\b(tip|pick|selection)\s+(for|in)\b/, weight: 12 },
      { regex: /\bbest (chance|bet|play)\b/, weight: 12 },
      { regex: /\bany value in\b/, weight: 10 },
      { regex: /\bwho.?s (worth|good)\b/, weight: 10 },
      { regex: /\bworth backing\b/, weight: 11 },
      { regex: /\bfield\b/, weight: 6 },
      { regex: /\bnomination\b/, weight: 6 },
    ],
  },
  {
    intent: "horse_history",
    baseConfidence: 0.82,
    patterns: [
      { regex: /\btell me about\b/, weight: 10 },
      { regex: /\bwhat do you know about\b/, weight: 10 },
      { regex: /\b(form|career|record|profile)\b/, weight: 8 },
      { regex: /\bhow.?s .+ going\b/, weight: 8 },
      { regex: /\blast (start|run)\b/, weight: 10 },
    ],
  },
  {
    intent: "track_profile",
    baseConfidence: 0.72,
    patterns: [
      { regex: /\btrack (bias|condition|profile)\b/, weight: 12 },
      { regex: /\bbarrier (bias|draw|advantage)\b/, weight: 11 },
      { regex: /\b(going|rail|surface)\b/, weight: 6 },
      { regex: /\bwhat (matters|factors)\b.*(at|track)\b/, weight: 12 },
    ],
  },
  {
    intent: "available_data",
    baseConfidence: 0.88,
    patterns: [
      { regex: /\bwhat (dates?|tracks?|data)\b.*(have|loaded|available|show)\b/, weight: 15 },
      { regex: /\bwhat.?s loaded\b/, weight: 14 },
      { regex: /\bwhat can you (tell|show|give)\b/, weight: 10 },
      { regex: /\bdo you have (data|info|racecards?)\b/, weight: 12 },
    ],
  },
];

export function classifyStrideQuestion(
  question: string,
  context?: StrideRetrievalRequest["context"],
): StrideQuestionClassification {
  const text = normalizeText(question);
  const tags = new Set<string>();
  const rationale: string[] = [];

  const entities = extractStrideEntities(question, context);
  if (entities.track) tags.add("track");
  if (entities.raceNumber) tags.add("race");
  if (entities.horseName) tags.add("horse");
  if (entities.date) tags.add("date");

  // Legacy keyword tagging for backwards compat
  for (const group of QUESTION_INTENT_PRIORITY) {
    if (group.keywords.some((keyword) => text.includes(normalizeText(keyword)))) {
      tags.add(group.intent);
    }
  }

  // Score each intent by summing matched pattern weights
  const intentScores = new Map<StrideIntent, number>();
  for (const group of INTENT_PATTERNS) {
    let totalWeight = 0;
    for (const pattern of group.patterns) {
      if (pattern.regex.test(text)) {
        totalWeight += pattern.weight;
      }
    }
    if (totalWeight > 0) {
      intentScores.set(group.intent, totalWeight);
    }
  }

  const hasTrackRace = Boolean(entities.track || entities.raceNumber);
  const hasHorse = Boolean(entities.horseName);
  const hasDate = Boolean(entities.date);

  // Entity-based boosts
  if (hasHorse && !intentScores.has("horse_history")) {
    intentScores.set("horse_history", (intentScores.get("horse_history") ?? 0) + 8);
  }
  if (hasTrackRace && hasDate && !intentScores.has("race_preview")) {
    intentScores.set("race_preview", (intentScores.get("race_preview") ?? 0) + 6);
  }
  if (hasTrackRace && !intentScores.has("race_preview")) {
    intentScores.set("race_preview", (intentScores.get("race_preview") ?? 0) + 4);
  }

  // Pick the highest scoring intent
  let intent: StrideIntent = "general";
  let confidence = 0.45;
  let bestScore = 0;

  for (const [candidateIntent, score] of intentScores) {
    if (score > bestScore) {
      bestScore = score;
      intent = candidateIntent;
      const group = INTENT_PATTERNS.find(g => g.intent === candidateIntent);
      confidence = group?.baseConfidence ?? 0.8;
      rationale.length = 0;
      rationale.push(`scored:${candidateIntent}=${score}`);
    }
  }

  // Context-based refinement for horse-anchored race_preview patterns
  if (intent === "race_preview" && hasHorse && !hasTrackRace && bestScore < 12) {
    intent = "horse_history";
    confidence = 0.8;
    rationale.push("downgraded to horse_history: horse entity without race anchor");
  }

  // If still general but have entities, try entity-based fallback
  if (intent === "general") {
    if (entities.raceName && (hasDate || text.includes("field") || text.includes("nomination"))) {
      intent = "race_preview";
      confidence = 0.8;
      rationale.push("entity-fallback: raceName + date/field");
    } else if (hasHorse) {
      intent = "horse_history";
      confidence = 0.8;
      rationale.push("entity-fallback: horse entity");
    } else if (hasTrackRace && hasDate) {
      intent = "race_preview";
      confidence = 0.76;
      rationale.push("entity-fallback: track + date");
    } else if (hasTrackRace) {
      intent = "race_preview";
      confidence = 0.7;
      rationale.push("entity-fallback: track or race number");
    } else if (text.includes("track") || text.includes("barrier") || text.includes("going") || text.includes("bias")) {
      intent = "track_profile";
      confidence = 0.72;
      rationale.push("entity-fallback: track keyword");
    }
  }

  const needsClarification =
    !entities.track &&
    !entities.raceNumber &&
    !entities.horseName &&
    !entities.date &&
    intent === "general";

  if (needsClarification) {
    rationale.push("no explicit racing entity found");
  }

  return {
    intent,
    confidence,
    tags: [...tags],
    rationale,
    needsClarification,
  };
}

export function extractStrideEntities(
  question: string,
  context?: StrideRetrievalRequest["context"],
): StrideEntityHints {
  const q = question.trim();
  const lower = normalizeText(q);
  const entities: StrideEntityHints = {};

  if (context?.track) entities.track = context.track.trim();
  if (context?.raceNumber) entities.raceNumber = context.raceNumber;
  if (context?.raceDate) {
    entities.date = parseIsoDate(context.raceDate);
    entities.dateSource = "context";
  }
  if (context?.horseName) entities.horseName = context.horseName.trim();
  if (context?.raceName) entities.raceName = context.raceName.trim();
  if (context?.jockey) entities.jockey = context.jockey.trim();
  if (context?.trainer) entities.trainer = context.trainer.trim();

  const explicitDate = q.match(/\b\d{4}-\d{2}-\d{2}\b/)?.[0];
  if (explicitDate) {
    entities.date = explicitDate;
    entities.dateSource = "explicit";
  }

  if (!entities.date) {
    const relativeDates = resolveRelativeDateTokens(q);
    if (relativeDates.length > 0) {
      entities.date = relativeDates[0];
      entities.dateSource = "relative";
    }
  }

  const raceNumberMatch = q.match(/\b(?:race\s*)?r?(\d{1,2})\b/i);
  if (raceNumberMatch) {
    entities.raceNumber = Number(raceNumberMatch[1]);
  }

  const raceNameMatch = q.match(/\b([A-Z][A-Za-z'’-]*(?:\s+[A-Z][A-Za-z'’-]*){0,5}\s+(?:Stakes|Handicap|Mile|Cup|Plate|Derby|Guineas|Classic|Sprint|Challenge))\b/);
  if (raceNameMatch) {
    entities.raceName = raceNameMatch[1].trim();
  }

  const horseCandidates = extractHorseCandidates(q, context);
  if (horseCandidates.length > 0 && !entities.horseName) {
    entities.horseName = horseCandidates[0];
  }

  if (entities.raceName && entities.horseName && normalizeText(entities.raceName) === normalizeText(entities.horseName)) {
    delete entities.horseName;
  }

  const trackMatch = buildTrackSearchTerms().find((trackCandidate) => {
    const trackNorm = normalizeCompact(trackCandidate);
    return Boolean(trackNorm && normalizeCompact(lower).includes(trackNorm));
  });
  if (trackMatch && !entities.track) {
    entities.track = trackMatch;
    entities.trackKey = normalizeCompact(trackMatch);
  }

  if (!entities.track && context?.track) {
    entities.track = context.track;
    entities.trackKey = normalizeCompact(context.track);
  }

  return entities;
}

function buildTrackSearchTerms(): string[] {
  const fromAliases = Object.values(COMMON_TRACK_ALIASES).flat();
  const canonical = Object.keys(COMMON_TRACK_ALIASES);
  return uniqueStrings([...canonical, ...fromAliases]);
}

function buildDateCandidates(question: string, entities: StrideEntityHints, context?: StrideRetrievalRequest["context"]): string[] {
  const candidates = new Set<string>();
  if (context?.raceDate) {
    const parsed = parseIsoDate(context.raceDate);
    if (parsed) candidates.add(parsed);
  }
  if (entities.date) candidates.add(entities.date);
  resolveRelativeDateTokens(question).forEach((d) => candidates.add(d));

  const latestLocal = getLatestLocalDate();
  if (latestLocal) candidates.add(latestLocal);
  candidates.add(getSydneyDate());

  return [...candidates].filter(Boolean);
}

function buildHorseCandidates(entities: StrideEntityHints, question: string, context?: StrideRetrievalRequest["context"]): string[] {
  const candidates = new Set<string>();
  if (context?.horseName) candidates.add(context.horseName.trim());
  if (entities.horseName) candidates.add(entities.horseName.trim());
  for (const candidate of extractHorseCandidates(question, context)) {
    candidates.add(candidate.trim());
  }
  return [...candidates].filter(Boolean);
}

function buildPromptContext(bundle: StrideRetrievalBundle): string {
  const lines: string[] = [];
  lines.push(`Intent: ${bundle.classification.intent} (${Math.round(bundle.classification.confidence * 100)}%)`);
  lines.push(
    `Entities: ${
      [
        bundle.entities.date ? `date=${bundle.entities.date}` : null,
        bundle.entities.track ? `track=${bundle.entities.track}` : null,
        bundle.entities.raceNumber ? `race=${bundle.entities.raceNumber}` : null,
        bundle.entities.horseName ? `horse=${bundle.entities.horseName}` : null,
      ].filter(Boolean).join(", ") || "none"
    }`,
  );

  const topEvidence = bundle.evidence.slice(0, 12);
  if (topEvidence.length > 0) {
    lines.push("Evidence:");
    topEvidence.forEach((item, index) => {
      const where = [
        item.raceDate ? item.raceDate : null,
        item.track ? item.track : null,
        item.raceNumber ? `R${item.raceNumber}` : null,
        item.horseName ? item.horseName : null,
      ]
        .filter(Boolean)
        .join(" • ");
      lines.push(`${index + 1}. [${item.source}] ${item.title}${where ? ` — ${where}` : ""}`);
      lines.push(`   ${item.summary}`);
    });
  } else {
    lines.push("Evidence: none found in local files or database tables.");
  }

  return lines.join("\n");
}

function buildSummary(bundle: StrideRetrievalBundle): string {
  const evidenceCount = bundle.evidence.length;
  if (evidenceCount === 0) {
    return "No strong local evidence was found. Ask a more specific horse, race, track, or date question.";
  }
  const byTrack = bundle.evidence
    .map((item) => item.track)
    .filter(Boolean)
    .slice(0, 3)
    .join(", ");
  const scope = byTrack || bundle.entities.track || "the current racing dataset";
  return `Built ${evidenceCount} evidence items across ${scope}.`;
}

function countEvidenceBySource(items: StrideEvidenceItem[]): Record<StrideEvidenceSource, number> {
  const counts: Record<StrideEvidenceSource, number> = {
    local_racecard: 0,
    local_tips: 0,
    local_tip_results: 0,
    local_consensus: 0,
    local_market_signals: 0,
    local_intelligence: 0,
    race_results_history: 0,
    sectional_times: 0,
    franking_scores: 0,
    prediction_audit: 0,
    stride_tip_results: 0,
    race_analyses: 0,
    horse_race_analyses: 0,
    races: 0,
    selections: 0,
    selection_results: 0,
    blackbook_entries: 0,
    blackbook_entry_runs: 0,
    performance_stats: 0,
    convergence_output: 0,
  };
  for (const item of items) {
    counts[item.source] += 1;
  }
  return counts;
}

function buildFollowUps(bundle: StrideRetrievalBundle): string[] {
  const followUps: string[] = [];
  if (!bundle.entities.track && !bundle.entities.horseName && !bundle.entities.raceNumber) {
    followUps.push("Which track, horse, or race do you want me to anchor on?");
  }
  if (bundle.classification.intent === "race_preview") {
    followUps.push("Do you want me to answer from the model, the market, or the historical results?");
  }
  if (bundle.classification.intent === "horse_history") {
    followUps.push("Do you want the horse's last-start, full prep, or track-by-track profile?");
  }
  return followUps;
}

function racecardMeetingsToRaces(meetings: LocalRacecardMeeting[]): Array<{
  meeting: LocalRacecardMeeting;
  race: LocalRacecardRace;
}> {
  const rows: Array<{ meeting: LocalRacecardMeeting; race: LocalRacecardRace }> = [];
  for (const meeting of meetings) {
    for (const race of meeting.races ?? []) {
      rows.push({ meeting, race });
    }
  }
  return rows;
}

function parseRaceNumber(value: unknown): number | undefined {
  const parsed = Number(String(value ?? "").replace(/^R/i, "").trim());
  return Number.isFinite(parsed) && parsed > 0 ? parsed : undefined;
}

function findMatchingTrack(trackCandidate: string | undefined, tracks: string[]): string | undefined {
  if (!trackCandidate) return undefined;
  const candidateNorm = normalizeCompact(trackCandidate);
  for (const track of tracks) {
    const trackNorm = normalizeCompact(track);
    if (!trackNorm) continue;
    if (trackNorm === candidateNorm || trackNorm.includes(candidateNorm) || candidateNorm.includes(trackNorm)) {
      return track;
    }
  }
  return undefined;
}

function summarizeRunner(runner: LocalRacecardRunner): string {
  const parts = [
    runner.comment ? runner.comment.replace(/\s+/g, " ").trim() : undefined,
    runner.jockey ? `jockey ${runner.jockey}` : undefined,
    runner.trainer ? `trainer ${runner.trainer}` : undefined,
    runner.draw ? `draw ${runner.draw}` : undefined,
    runner.form ? `form ${runner.form}` : undefined,
  ].filter(Boolean);
  return parts.join(" | ") || "No runner note available.";
}

async function loadLocalArtifacts(
  dates: string[],
  trackCandidates: string[],
  horseCandidates: string[],
  raceNumber?: number,
): Promise<StrideLocalArtifactBundle> {
  const racecards: LocalRacecardMatch[] = [];
  const tips: LocalTipMatch[] = [];
  const tipResults: LocalTipResultMatch[] = [];
  const consensus: LocalJsonMatch[] = [];
  const marketSignals: LocalJsonMatch[] = [];
  const intelligence: LocalJsonMatch[] = [];

  for (const date of dates) {
    const racecardPath = path.join(RACECARDS_DIR, `racecard_${date}.json`);
    const tipsPath = path.join(RACECARDS_DIR, `tips_${date}.json`);
    const tipResultsPath = path.join(RACECARDS_DIR, `tip_results_${date}.json`);
    const consensusPath = path.join(INTELLIGENCE_DIR, `consensus_${date}.json`);
    const marketSignalsPath = path.join(INTELLIGENCE_DIR, `market_signals_${date}.json`);

    const [racecardFile, tipsFile, tipResultsFile, consensusFile, marketSignalsFile] = await Promise.all([
      readJsonIfExists<LocalRacecardMeeting[]>(racecardPath),
      readJsonIfExists<LocalTipsFile>(tipsPath),
      readJsonIfExists<LocalTipResultFile>(tipResultsPath),
      readJsonIfExists<Record<string, JsonRecord>>(consensusPath),
      readJsonIfExists<Record<string, JsonRecord>>(marketSignalsPath),
    ]);

    if (racecardFile) {
      for (const { meeting, race } of racecardMeetingsToRaces(racecardFile)) {
        const track = meeting.course || race.course || "";
        const raceNum = parseRaceNumber(race.race_number);
        const horseMatches = horseCandidates.length > 0
          ? (race.runners ?? []).filter((runner) =>
              horseCandidates.some((horse) => normalizeCompact(runner.horse).includes(normalizeCompact(horse)) || normalizeCompact(horse).includes(normalizeCompact(runner.horse))),
            )
          : [];
        const trackMatches =
          trackCandidates.length === 0 || trackCandidates.some((trackCandidate) => scoreMatch(track, [trackCandidate]) > 0);
        const raceMatches = raceNumber ? raceNum === raceNumber : true;
        if (trackMatches && raceMatches && (horseMatches.length > 0 || trackCandidates.length > 0 || horseCandidates.length > 0 || raceNumber !== undefined)) {
          racecards.push({
            source: "local_racecard",
            title: `${track} R${raceNum ?? "?"} ${race.race_name || ""}`.trim(),
            summary: [
              race.distance ? `distance ${race.distance}` : undefined,
              race.going ? `going ${race.going}` : undefined,
              race.class ? `class ${race.class}` : undefined,
              race.off_time ? `off ${race.off_time}` : undefined,
              horseMatches.length ? `${horseMatches.length} matching runner(s)` : undefined,
            ]
              .filter(Boolean)
              .join(" | ") || "Racecard race loaded.",
            track,
            raceNumber: raceNum,
            raceDate: race.date || meeting.date,
            payload: {
              meeting,
              race,
              runners: race.runners ?? [],
              matchingRunners: horseMatches,
            },
            score: 100 + (horseMatches.length > 0 ? 20 : 0) + (raceNumber ? 10 : 0),
          });
        }
      }
    }

    if (tipsFile?.races) {
      for (const race of tipsFile.races) {
        const track = race.track || "";
        const raceNum = parseRaceNumber(race.race_number);
        const horseMatches = horseCandidates.length > 0
          ? (race.full_field ?? []).filter((runner) =>
              horseCandidates.some((horse) => normalizeCompact(runner.horse).includes(normalizeCompact(horse)) || normalizeCompact(horse).includes(normalizeCompact(runner.horse))),
            )
          : [];
        const trackMatches =
          trackCandidates.length === 0 || trackCandidates.some((trackCandidate) => scoreMatch(track, [trackCandidate]) > 0);
        const raceMatches = raceNumber ? raceNum === raceNumber : true;
        if (trackMatches && raceMatches && (horseMatches.length > 0 || trackCandidates.length > 0 || horseCandidates.length > 0 || raceNumber !== undefined)) {
          tips.push({
            source: "local_tips",
            title: `${track} R${raceNum ?? "?"} tips`,
            summary: [
              race.race_name ? `race ${race.race_name}` : undefined,
              race.distance ? `distance ${race.distance}` : undefined,
              race.going ? `going ${race.going}` : undefined,
              horseMatches.length ? `${horseMatches.length} matching runner(s)` : undefined,
              race.bet_status ? `bet status ${race.bet_status}` : undefined,
            ]
              .filter(Boolean)
              .join(" | ") || "Tips file race loaded.",
            track,
            raceNumber: raceNum,
            raceDate: tipsFile.date || date,
            payload: {
              race,
              betPick: race.bet_pick ?? null,
              primaryPick: race.primary_pick ?? null,
              topPicks: race.top_picks ?? [],
              fullField: race.full_field ?? [],
            },
            score: 95 + (horseMatches.length > 0 ? 20 : 0) + (raceNumber ? 10 : 0),
          });
        }
      }
    }

    if (tipResultsFile?.results) {
      for (const result of tipResultsFile.results as Array<Record<string, unknown>>) {
        const track = String(result.track || "");
        const raceNum = parseRaceNumber(result.race_number);
        const horseName = String(result.horse_name || result.tipped_horse_name || "");
        const horseMatches = horseCandidates.length > 0
          ? horseCandidates.some((horse) => normalizeCompact(horseName).includes(normalizeCompact(horse)) || normalizeCompact(horse).includes(normalizeCompact(horseName)))
          : false;
        const trackMatches =
          trackCandidates.length === 0 || trackCandidates.some((trackCandidate) => scoreMatch(track, [trackCandidate]) > 0);
        const raceMatches = raceNumber ? raceNum === raceNumber : true;
        if (trackMatches && raceMatches && (horseMatches || trackCandidates.length > 0 || horseCandidates.length > 0 || raceNumber !== undefined)) {
          tipResults.push({
            source: "local_tip_results",
            title: `${track} R${raceNum ?? "?"} tip result${horseName ? ` — ${horseName}` : ""}`,
            summary: [
              result.result ? `result ${result.result}` : undefined,
              result.actual_position ? `position ${result.actual_position}` : undefined,
              result.tipped_horse_sp ? `tipped SP ${result.tipped_horse_sp}` : undefined,
            ]
              .filter(Boolean)
              .join(" | ") || "Tip result loaded.",
            track,
            raceNumber: raceNum,
            raceDate: tipResultsFile.date || date,
            horseName,
            payload: result,
            score: 90,
          });
        }
      }
    }

    for (const [key, value] of Object.entries(consensusFile ?? {})) {
      const trackMatch = trackCandidates.length === 0 || trackCandidates.some((trackCandidate) => scoreMatch(key, [trackCandidate]) > 0);
      const horseMatch = horseCandidates.length > 0
        ? horseCandidates.some((horse) => Object.keys(value ?? {}).some((entryKey) => normalizeCompact(entryKey).includes(normalizeCompact(horse))))
        : true;
      const raceMatch = raceNumber ? new RegExp(`_r?${raceNumber}\\b`, "i").test(key) : true;
      if (trackMatch && horseMatch && raceMatch) {
        consensus.push({
          source: "local_consensus",
          title: `${key} consensus`,
          summary: `Consensus data loaded for ${Object.keys(value ?? {}).length} runner(s).`,
          payload: value,
          score: 85,
        });
      }
    }

    for (const [key, value] of Object.entries(marketSignalsFile ?? {})) {
      const trackMatch = trackCandidates.length === 0 || trackCandidates.some((trackCandidate) => scoreMatch(key, [trackCandidate]) > 0);
      const horseMatch = horseCandidates.length > 0
        ? horseCandidates.some((horse) => Object.keys(value ?? {}).some((entryKey) => normalizeCompact(entryKey).includes(normalizeCompact(horse))))
        : true;
      const raceMatch = raceNumber ? new RegExp(`_r?${raceNumber}\\b`, "i").test(key) : true;
      if (trackMatch && horseMatch && raceMatch) {
        marketSignals.push({
          source: "local_market_signals",
          title: `${key} market signals`,
          summary: `Market signals loaded for ${Object.keys(value ?? {}).length} runner(s).`,
          payload: value,
          score: 80,
        });
      }
    }
  }

  const stableFiles: Array<{ file: string; source: StrideEvidenceSource; title: string }> = [
    { file: "barrier_map.json", source: "local_intelligence", title: "Barrier intelligence" },
    { file: "class_distance_patterns.json", source: "local_intelligence", title: "Class / distance intelligence" },
    { file: "flemington_straight.json", source: "local_intelligence", title: "Flemington straight intelligence" },
    { file: "form_franking.json", source: "local_intelligence", title: "Form franking intelligence" },
    { file: "market_overlays.json", source: "local_intelligence", title: "Market overlays intelligence" },
    { file: "prep_cycles.json", source: "local_intelligence", title: "Prep cycle intelligence" },
    { file: "sectional_trends.json", source: "local_intelligence", title: "Sectional trends intelligence" },
    { file: "trainer_patterns.json", source: "local_intelligence", title: "Trainer patterns intelligence" },
  ];

  const loadedStable = await Promise.all(
    stableFiles.map(async ({ file, source, title }) => {
      const json = await readJsonIfExists<JsonRecord>(path.join(INTELLIGENCE_DIR, file));
      return json ? { file, source, title, json } : null;
    }),
  );

  for (const entry of loadedStable.filter(Boolean) as Array<{ file: string; source: StrideEvidenceSource; title: string; json: JsonRecord }>) {
    const lowerTitle = normalizeText(entry.title);
    if (entry.file === "barrier_map.json" && trackCandidates.length > 0) {
      for (const trackCandidate of trackCandidates) {
        const key = findMatchingKey(Object.keys(entry.json), trackCandidate);
        if (key) {
          const trackObj = entry.json[key] as JsonRecord;
          intelligence.push({
            source: "local_intelligence",
            title: `${entry.title}: ${key}`,
            summary: `Barrier / barrier-edge profile available for ${key}.`,
            track: key,
            payload: trackObj,
            score: 70 + scoreMatch(key, [trackCandidate]),
          });
        }
      }
    } else if (entry.file === "market_overlays.json" && trackCandidates.length > 0) {
      for (const trackCandidate of trackCandidates) {
        const key = findMatchingKey(Object.keys(entry.json), trackCandidate);
        if (key) {
          intelligence.push({
            source: "local_intelligence",
            title: `${entry.title}: ${key}`,
            summary: `Market overlay intelligence available for ${key}.`,
            track: key,
            payload: entry.json[key] as JsonRecord,
            score: 68 + scoreMatch(key, [trackCandidate]),
          });
        }
      }
    } else if (entry.file === "form_franking.json" && horseCandidates.length > 0) {
      const horses = extractHorseKeyMatches(entry.json, horseCandidates);
      for (const horse of horses) {
        intelligence.push({
          source: "local_intelligence",
          title: `${entry.title}: ${horse.key}`,
          summary: `Franking record available for ${horse.key}.`,
          horseName: horse.key,
          payload: horse.value,
          score: 70,
        });
      }
    } else if (entry.file === "prep_cycles.json" && horseCandidates.length > 0) {
      const horses = extractHorseKeyMatches(entry.json, horseCandidates);
      for (const horse of horses) {
        intelligence.push({
          source: "local_intelligence",
          title: `${entry.title}: ${horse.key}`,
          summary: `Preparation cycle record available for ${horse.key}.`,
          horseName: horse.key,
          payload: horse.value,
          score: 68,
        });
      }
    } else if (entry.file === "sectional_trends.json" && horseCandidates.length > 0) {
      const horses = extractHorseKeyMatches(entry.json, horseCandidates);
      for (const horse of horses) {
        intelligence.push({
          source: "local_intelligence",
          title: `${entry.title}: ${horse.key}`,
          summary: `Sectional trend record available for ${horse.key}.`,
          horseName: horse.key,
          payload: horse.value,
          score: 68,
        });
      }
    } else if (entry.file === "trainer_patterns.json" && contextHasTrainer(trackCandidates, horseCandidates)) {
      const trainerMatches = extractTrainerKeyMatches(entry.json, [questionTextForHorseCandidates(horseCandidates)]);
      for (const match of trainerMatches) {
        intelligence.push({
          source: "local_intelligence",
          title: `${entry.title}: ${match.key}`,
          summary: `Trainer pattern intelligence available for ${match.key}.`,
          horseName: match.key,
          payload: match.value,
          score: 66,
        });
      }
    } else if (entry.file === "class_distance_patterns.json") {
      const classHier = entry.json.class_hierarchy;
      const movement = entry.json.movement_patterns;
      intelligence.push({
        source: "local_intelligence",
        title: entry.title,
        summary: `Class hierarchy and movement pattern intelligence loaded (${Object.keys(classHier ?? {}).length} class keys, ${Object.keys(movement ?? {}).length} movement patterns).`,
        payload: {
          classHierarchy: classHier,
          movementPatterns: movement,
        },
        score: 55,
      });
    } else if (entry.file === "flemington_straight.json" && trackCandidates.some((trackCandidate) => normalizeCompact(trackCandidate).includes("flemington"))) {
      intelligence.push({
        source: "local_intelligence",
        title: entry.title,
        summary: "Flemington straight-course intelligence loaded.",
        payload: entry.json,
        score: 65,
      });
    }
  }

  return {
    datesLoaded: dates,
    latestLocalDate: dates.at(-1),
    racecards,
    tips,
    tipResults,
    consensus,
    marketSignals,
    intelligence,
  };
}

function findMatchingKey(keys: string[], trackCandidate: string): string | undefined {
  const target = normalizeCompact(trackCandidate);
  for (const key of keys) {
    const keyNorm = normalizeCompact(key);
    if (keyNorm.includes(target) || target.includes(keyNorm)) {
      return key;
    }
  }
  return undefined;
}

function extractHorseKeyMatches(source: JsonRecord, horseCandidates: string[]): Array<{ key: string; value: JsonRecord }> {
  const matches: Array<{ key: string; value: JsonRecord }> = [];
  for (const [key, value] of Object.entries(source)) {
    if (typeof value !== "object" || value === null) continue;
    const keyNorm = normalizeCompact(key);
    if (horseCandidates.some((horse) => keyNorm.includes(normalizeCompact(horse)) || normalizeCompact(horse).includes(keyNorm))) {
      matches.push({ key, value: value as JsonRecord });
    }
  }
  return matches;
}

function extractTrainerKeyMatches(source: JsonRecord, trainerHints: string[]): Array<{ key: string; value: JsonRecord }> {
  const matches: Array<{ key: string; value: JsonRecord }> = [];
  for (const [key, value] of Object.entries(source)) {
    if (typeof value !== "object" || value === null) continue;
    const keyNorm = normalizeCompact(key);
    if (trainerHints.some((hint) => keyNorm.includes(normalizeCompact(hint)) || normalizeCompact(hint).includes(keyNorm))) {
      matches.push({ key, value: value as JsonRecord });
    }
  }
  return matches;
}

function contextHasTrainer(trackCandidates: string[], horseCandidates: string[]): boolean {
  return trackCandidates.length > 0 || horseCandidates.length > 0;
}

function questionTextForHorseCandidates(horseCandidates: string[]): string {
  return horseCandidates.join(" ");
}

async function fetchHorseEvidence(
  entities: StrideEntityHints,
  dateCandidates: string[],
  limit = 8,
): Promise<StrideEvidenceItem[]> {
  const horse = entities.horseName?.trim();
  if (!horse) return [];
  const horsePattern = `%${horse}%`;
  const rrhDateClause = dateCandidates.length > 0 ? inArray(raceResultsHistory.raceDate, dateCandidates) : undefined;
  const hraDateClause = dateCandidates.length > 0 ? inArray(horseRaceAnalyses.raceDate, dateCandidates) : undefined;
  const bbrDateClause = dateCandidates.length > 0 ? inArray(blackbookEntryRuns.raceDate, dateCandidates) : undefined;
  const paDateClause = dateCandidates.length > 0 ? inArray(predictionAudit.raceDate, dateCandidates) : undefined;

  const tasks = [
    safeArrayQuery("race_results_history", db
      .select({
        id: raceResultsHistory.id,
        horseId: raceResultsHistory.horseId,
        horseName: raceResultsHistory.horseName,
        track: raceResultsHistory.track,
        raceDate: raceResultsHistory.raceDate,
        raceNumber: raceResultsHistory.raceNumber,
        raceName: raceResultsHistory.raceName,
        distanceM: raceResultsHistory.distanceM,
        raceClass: raceResultsHistory.raceClass,
        going: raceResultsHistory.going,
        position: raceResultsHistory.position,
        spOdds: raceResultsHistory.spOdds,
        fieldSize: raceResultsHistory.fieldSize,
        barrier: raceResultsHistory.barrier,
        jockey: raceResultsHistory.jockey,
        marginLengths: raceResultsHistory.marginLengths,
      })
      .from(raceResultsHistory)
      .where(and(ilike(raceResultsHistory.horseName, horsePattern), rrhDateClause))
      .orderBy(desc(raceResultsHistory.raceDate), desc(raceResultsHistory.raceNumber))
      .limit(limit)),
    safeArrayQuery("horse_race_analyses", db
      .select({
        horseName: horseRaceAnalyses.horseName,
        track: horseRaceAnalyses.track,
        raceDate: horseRaceAnalyses.raceDate,
        raceNumber: horseRaceAnalyses.raceNumber,
        raceAnalysisId: horseRaceAnalyses.raceAnalysisId,
        category: horseRaceAnalyses.category,
        confidencePct: horseRaceAnalyses.confidencePct,
        marketAssessment: horseRaceAnalyses.marketAssessment,
        valueEdgePct: horseRaceAnalyses.valueEdgePct,
        comparativeAdvantage: horseRaceAnalyses.comparativeAdvantage,
        // 4-phase analysis
        phase1Profile: horseRaceAnalyses.phase1Profile,
        phase2Pace: horseRaceAnalyses.phase2Pace,
        phase3Class: horseRaceAnalyses.phase3Class,
        phase4Comparative: horseRaceAnalyses.phase4Comparative,
        trainOfThought: horseRaceAnalyses.trainOfThought,
        canWin: horseRaceAnalyses.canWin,
        upsetScenario: horseRaceAnalyses.upsetScenario,
        assessedWinProb: horseRaceAnalyses.assessedWinProb,
        predictedFinishPosition: horseRaceAnalyses.predictedFinishPosition,
      })
      .from(horseRaceAnalyses)
      .where(and(ilike(horseRaceAnalyses.horseName, horsePattern), hraDateClause))
      .orderBy(desc(horseRaceAnalyses.raceDate), desc(horseRaceAnalyses.raceNumber))
      .limit(limit)),
    safeArrayQuery("franking_scores", db
      .select({
        horseName: frankingScores.horseName,
        horseId: frankingScores.horseId,
        globalElo: frankingScores.globalElo,
        frankingScore: frankingScores.frankingScore,
        frankingConfidence: frankingScores.frankingConfidence,
        antiFranked: frankingScores.antiFranked,
        fieldStrengthAvg: frankingScores.fieldStrengthAvg,
        formQualityTrend: frankingScores.formQualityTrend,
        bestAdjustedMargin: frankingScores.bestAdjustedMargin,
        collateralAdvantage: frankingScores.collateralAdvantage,
        lastRaceDate: frankingScores.lastRaceDate,
      })
      .from(frankingScores)
      .where(ilike(frankingScores.horseName, horsePattern))
      .orderBy(desc(frankingScores.computedAt))
      .limit(limit)),
    safeArrayQuery("blackbook_entries", db
      .select({
        horseName: blackbookEntries.horseName,
        sourceTrack: blackbookEntries.sourceTrack,
        sourceRaceDate: blackbookEntries.sourceRaceDate,
        sourceRaceNumber: blackbookEntries.sourceRaceNumber,
        sourceRaceName: blackbookEntries.sourceRaceName,
        status: blackbookEntries.status,
        primaryReason: blackbookEntries.primaryReason,
        readinessBand: blackbookEntries.readinessBand,
        intakeConfidence: blackbookEntries.intakeConfidence,
      })
      .from(blackbookEntries)
      .where(ilike(blackbookEntries.horseName, horsePattern))
      .orderBy(desc(blackbookEntries.updatedAt))
      .limit(limit)),
    safeArrayQuery("blackbook_entry_runs", db
      .select({
        horseName: blackbookEntryRuns.horseName,
        track: blackbookEntryRuns.track,
        raceDate: blackbookEntryRuns.raceDate,
        raceNumber: blackbookEntryRuns.raceNumber,
        raceName: blackbookEntryRuns.raceName,
        verdict: blackbookEntryRuns.verdict,
        readinessBand: blackbookEntryRuns.readinessBand,
        valueEdgePct: blackbookEntryRuns.valueEdgePct,
        modelWinProb: blackbookEntryRuns.modelWinProb,
        marketPrice: blackbookEntryRuns.marketPrice,
        truePrice: blackbookEntryRuns.truePrice,
      })
      .from(blackbookEntryRuns)
      .where(and(ilike(blackbookEntryRuns.horseName, horsePattern), bbrDateClause))
      .orderBy(desc(blackbookEntryRuns.raceDate), desc(blackbookEntryRuns.raceNumber))
      .limit(limit)),
    safeArrayQuery("prediction_audit", db
      .select({
        horseName: predictionAudit.horseName,
        track: predictionAudit.track,
        raceDate: predictionAudit.raceDate,
        raceNumber: predictionAudit.raceNumber,
        predictedWinProb: predictionAudit.predictedWinProb,
        marketOdds: predictionAudit.marketOdds,
        confidence: predictionAudit.confidence,
        edge: predictionAudit.edge,
        actualPosition: predictionAudit.actualPosition,
        won: predictionAudit.won,
        placed: predictionAudit.placed,
        startingPrice: predictionAudit.startingPrice,
        resultStatus: predictionAudit.resultStatus,
      })
      .from(predictionAudit)
      .where(and(ilike(predictionAudit.horseName, horsePattern), paDateClause))
      .orderBy(desc(predictionAudit.raceDate), desc(predictionAudit.raceNumber))
      .limit(limit)),
  ] as const;

  const [rrhRows, hraRows, frankRows, bbRows, bbrRows, paRows] = await Promise.all(tasks);
  const evidence: StrideEvidenceItem[] = [];

  for (const row of rrhRows) {
    evidence.push({
      source: "race_results_history",
      title: `${row.horseName} result at ${row.track}`,
      summary: [
        row.raceDate ? `date ${row.raceDate}` : undefined,
        row.raceNumber ? `R${row.raceNumber}` : undefined,
        row.position ? `position ${row.position}` : undefined,
        row.spOdds ? `SP ${row.spOdds}` : undefined,
        row.raceClass ? `class ${row.raceClass}` : undefined,
        row.going ? `going ${row.going}` : undefined,
      ]
        .filter(Boolean)
        .join(" | "),
      track: row.track,
      raceDate: row.raceDate,
      raceNumber: row.raceNumber ?? undefined,
      horseName: row.horseName,
      payload: row as unknown as JsonRecord,
      score: 95,
    });
  }

  for (const row of hraRows) {
    const trainSnippet = typeof row.trainOfThought === "string" && row.trainOfThought.length > 0
      ? row.trainOfThought.slice(0, 300)
      : undefined;
    evidence.push({
      source: "horse_race_analyses",
      title: `${row.horseName} race analysis`,
      summary: [
        row.track ? row.track : undefined,
        row.raceDate ? row.raceDate : undefined,
        row.raceNumber ? `R${row.raceNumber}` : undefined,
        row.category ? `category ${row.category}` : undefined,
        row.assessedWinProb !== null && row.assessedWinProb !== undefined ? `assessed win ${row.assessedWinProb}%` : undefined,
        row.marketAssessment ? `market ${row.marketAssessment}` : undefined,
        row.valueEdgePct !== null && row.valueEdgePct !== undefined ? `edge ${row.valueEdgePct}%` : undefined,
        row.canWin === false ? "cannot win" : undefined,
        row.comparativeAdvantage ? `advantage: ${String(row.comparativeAdvantage).slice(0, 120)}` : undefined,
        trainSnippet ? `analysis: ${trainSnippet}` : undefined,
      ]
        .filter(Boolean)
        .join(" | "),
      track: row.track,
      raceDate: row.raceDate,
      raceNumber: row.raceNumber ?? undefined,
      horseName: row.horseName,
      payload: row as unknown as JsonRecord,
      score: 88,
    });
  }

  for (const row of frankRows) {
    evidence.push({
      source: "franking_scores",
      title: `${row.horseName} franking`,
      summary: [
        row.frankingScore !== null && row.frankingScore !== undefined ? `franking ${row.frankingScore}` : undefined,
        row.frankingConfidence !== null && row.frankingConfidence !== undefined ? `confidence ${row.frankingConfidence}` : undefined,
        row.antiFranked ? "anti-franked" : undefined,
        row.formQualityTrend !== null && row.formQualityTrend !== undefined ? `trend ${row.formQualityTrend}` : undefined,
      ]
        .filter(Boolean)
        .join(" | "),
      horseName: row.horseName,
      payload: row as unknown as JsonRecord,
      score: 82,
    });
  }

  for (const row of bbRows) {
    evidence.push({
      source: "blackbook_entries",
      title: `${row.horseName} blackbook entry`,
      summary: [
        row.sourceTrack ? `from ${row.sourceTrack}` : undefined,
        row.sourceRaceDate ? row.sourceRaceDate : undefined,
        row.status ? `status ${row.status}` : undefined,
        row.readinessBand ? `readiness ${row.readinessBand}` : undefined,
        row.primaryReason ? row.primaryReason : undefined,
      ]
        .filter(Boolean)
        .join(" | "),
      track: row.sourceTrack,
      raceDate: row.sourceRaceDate,
      raceNumber: row.sourceRaceNumber ?? undefined,
      horseName: row.horseName,
      payload: row as unknown as JsonRecord,
      score: 80,
    });
  }

  for (const row of bbrRows) {
    evidence.push({
      source: "blackbook_entry_runs",
      title: `${row.horseName} blackbook run`,
      summary: [
        row.track ? row.track : undefined,
        row.raceDate ? row.raceDate : undefined,
        row.raceNumber ? `R${row.raceNumber}` : undefined,
        row.verdict ? `verdict ${row.verdict}` : undefined,
        row.readinessBand ? `readiness ${row.readinessBand}` : undefined,
        row.valueEdgePct !== null && row.valueEdgePct !== undefined ? `edge ${row.valueEdgePct}` : undefined,
      ]
        .filter(Boolean)
        .join(" | "),
      track: row.track,
      raceDate: row.raceDate,
      raceNumber: row.raceNumber ?? undefined,
      horseName: row.horseName,
      payload: row as unknown as JsonRecord,
      score: 82,
    });
  }

  for (const row of paRows) {
    evidence.push({
      source: "prediction_audit",
      title: `${row.horseName} prediction audit`,
      summary: [
        row.track ? row.track : undefined,
        row.raceDate ? row.raceDate : undefined,
        row.raceNumber ? `R${row.raceNumber}` : undefined,
        row.confidence ? `confidence ${row.confidence}` : undefined,
        row.edge !== null && row.edge !== undefined ? `edge ${row.edge}` : undefined,
        row.resultStatus ? `status ${row.resultStatus}` : undefined,
      ]
        .filter(Boolean)
        .join(" | "),
      track: row.track,
      raceDate: row.raceDate,
      raceNumber: row.raceNumber ?? undefined,
      horseName: row.horseName,
      payload: row as unknown as JsonRecord,
      score: 78,
    });
  }

  return evidence;
}

async function fetchRaceEvidence(
  entities: StrideEntityHints,
  dateCandidates: string[],
  trackCandidates: string[],
  limit = 8,
): Promise<StrideEvidenceItem[]> {
  const raceNumber = entities.raceNumber;
  const raceNamePattern = entities.raceName ? `%${entities.raceName}%` : undefined;

  const racesDateClause = dateCandidates.length > 0 ? inArray(races.raceDate, dateCandidates) : undefined;
  const racesTrackClause = trackCandidates.length > 0
    ? or(...trackCandidates.map((track) => ilike(races.track, `%${track}%`)))
    : undefined;
  const racesRaceNumberClause = raceNumber ? eq(races.raceNumber, raceNumber) : undefined;
  const racesRaceNameClause = raceNamePattern ? ilike(races.raceName, raceNamePattern) : undefined;

  const rrhDateClause = dateCandidates.length > 0 ? inArray(raceResultsHistory.raceDate, dateCandidates) : undefined;
  const rrhTrackClause = trackCandidates.length > 0
    ? or(...trackCandidates.map((track) => ilike(raceResultsHistory.track, `%${track}%`)))
    : undefined;
  const rrhRaceNumberClause = raceNumber ? eq(raceResultsHistory.raceNumber, raceNumber) : undefined;
  const rrhRaceNameClause = raceNamePattern ? ilike(raceResultsHistory.raceName, raceNamePattern) : undefined;

  const sectionalDateClause = dateCandidates.length > 0 ? inArray(sectionalTimes.raceDate, dateCandidates) : undefined;
  const sectionalTrackClause = trackCandidates.length > 0
    ? or(...trackCandidates.map((track) => ilike(sectionalTimes.track, `%${track}%`)))
    : undefined;
  const sectionalRaceNumberClause = raceNumber ? eq(sectionalTimes.raceNumber, raceNumber) : undefined;
  const sectionalRaceNameClause = raceNamePattern ? ilike(sectionalTimes.raceName, raceNamePattern) : undefined;

  const predictionDateClause = dateCandidates.length > 0 ? inArray(predictionAudit.raceDate, dateCandidates) : undefined;
  const predictionTrackClause = trackCandidates.length > 0
    ? or(...trackCandidates.map((track) => ilike(predictionAudit.track, `%${track}%`)))
    : undefined;
  const predictionRaceNumberClause = raceNumber ? eq(predictionAudit.raceNumber, raceNumber) : undefined;

  const analysesDateClause = dateCandidates.length > 0 ? inArray(raceAnalyses.raceDate, dateCandidates) : undefined;
  const analysesTrackClause = trackCandidates.length > 0
    ? or(...trackCandidates.map((track) => ilike(raceAnalyses.track, `%${track}%`)))
    : undefined;
  const analysesRaceNumberClause = raceNumber ? eq(raceAnalyses.raceNumber, raceNumber) : undefined;
  const analysesRaceNameClause = raceNamePattern ? ilike(raceAnalyses.raceName, raceNamePattern) : undefined;

  const selectionsDateClause = dateCandidates.length > 0 ? inArray(selections.raceDate, dateCandidates) : undefined;
  const selectionsTrackClause = trackCandidates.length > 0
    ? or(...trackCandidates.map((track) => ilike(selections.track, `%${track}%`)))
    : undefined;
  const selectionsRaceNumberClause = raceNumber ? eq(selections.raceNumber, raceNumber) : undefined;
  const selectionsRaceNameClause = raceNamePattern ? ilike(selections.raceName, raceNamePattern) : undefined;

  const selectionResultsDateClause = dateCandidates.length > 0 ? inArray(selectionResults.raceDate, dateCandidates) : undefined;
  const selectionResultsTrackClause = trackCandidates.length > 0
    ? or(...trackCandidates.map((track) => ilike(selectionResults.track, `%${track}%`)))
    : undefined;
  const selectionResultsRaceNumberClause = raceNumber ? eq(selectionResults.raceNumber, raceNumber) : undefined;

  const tasks = [
    safeArrayQuery("races", db
      .select({
        id: races.id,
        track: races.track,
        raceDate: races.raceDate,
        raceNumber: races.raceNumber,
        raceName: races.raceName,
        distance: races.distance,
        raceClass: races.raceClass,
        going: races.going,
        offTime: races.offTime,
        status: races.status,
      })
      .from(races)
      .where(and(racesDateClause, racesTrackClause, racesRaceNumberClause, racesRaceNameClause))
      .orderBy(desc(races.raceDate), desc(races.raceNumber))
      .limit(limit)),
    safeArrayQuery("race_results_history", db
      .select({
        track: raceResultsHistory.track,
        raceDate: raceResultsHistory.raceDate,
        raceNumber: raceResultsHistory.raceNumber,
        raceName: raceResultsHistory.raceName,
        horseName: raceResultsHistory.horseName,
        position: raceResultsHistory.position,
        spOdds: raceResultsHistory.spOdds,
        fieldSize: raceResultsHistory.fieldSize,
        raceClass: raceResultsHistory.raceClass,
        going: raceResultsHistory.going,
      })
      .from(raceResultsHistory)
      .where(and(rrhDateClause, rrhTrackClause, rrhRaceNumberClause, rrhRaceNameClause))
      .orderBy(desc(raceResultsHistory.raceDate), desc(raceResultsHistory.raceNumber), asc(raceResultsHistory.position))
      .limit(limit * 2)),
    safeArrayQuery("sectional_times", db
      .select({
        track: sectionalTimes.track,
        raceDate: sectionalTimes.raceDate,
        raceNumber: sectionalTimes.raceNumber,
        raceName: sectionalTimes.raceName,
        horseName: sectionalTimes.horseName,
        distanceM: sectionalTimes.distanceM,
        last200mSpeed: sectionalTimes.last200mSpeed,
        last400mSpeed: sectionalTimes.last400mSpeed,
        last600mSpeed: sectionalTimes.last600mSpeed,
        last800mSpeed: sectionalTimes.last800mSpeed,
        finishingBurst: sectionalTimes.finishingBurst,
        source: sectionalTimes.source,
      })
      .from(sectionalTimes)
      .where(and(sectionalDateClause, sectionalTrackClause, sectionalRaceNumberClause, sectionalRaceNameClause))
      .orderBy(desc(sectionalTimes.raceDate), desc(sectionalTimes.raceNumber))
      .limit(limit * 2)),
    safeArrayQuery("stride_tip_results", db
      .select({
        track: strideTipResults.track,
        tipDate: strideTipResults.tipDate,
        raceNumber: strideTipResults.raceNumber,
        raceId: strideTipResults.raceId,
        tippedHorseName: strideTipResults.tippedHorseName,
        actualWinnerName: strideTipResults.actualWinnerName,
        result: strideTipResults.result,
        tippedHorseSp: strideTipResults.tippedHorseSp,
        winnerSp: strideTipResults.winnerSp,
        edgePct: strideTipResults.edgePct,
      })
      .from(strideTipResults)
      .where(
        and(
          dateCandidates.length > 0 ? inArray(strideTipResults.tipDate, dateCandidates) : undefined,
          trackCandidates.length > 0
            ? or(...trackCandidates.map((track) => ilike(strideTipResults.track, `%${track}%`)))
            : undefined,
          raceNumber !== undefined ? eq(strideTipResults.raceNumber, raceNumber) : undefined,
        ),
      )
      .orderBy(desc(strideTipResults.tipDate), desc(strideTipResults.raceNumber))
      .limit(limit * 2)),
    safeArrayQuery("prediction_audit", db
      .select({
        track: predictionAudit.track,
        raceDate: predictionAudit.raceDate,
        raceNumber: predictionAudit.raceNumber,
        horseName: predictionAudit.horseName,
        predictedWinProb: predictionAudit.predictedWinProb,
        marketOdds: predictionAudit.marketOdds,
        confidence: predictionAudit.confidence,
        edge: predictionAudit.edge,
        actualPosition: predictionAudit.actualPosition,
        fieldSize: predictionAudit.fieldSize,
      })
      .from(predictionAudit)
      .where(and(predictionDateClause, predictionTrackClause, predictionRaceNumberClause))
      .orderBy(desc(predictionAudit.raceDate), desc(predictionAudit.raceNumber))
      .limit(limit * 2)),
    safeArrayQuery("race_analyses", db
      .select({
        track: raceAnalyses.track,
        raceDate: raceAnalyses.raceDate,
        raceNumber: raceAnalyses.raceNumber,
        raceName: raceAnalyses.raceName,
        raceClass: raceAnalyses.raceClass,
        predictedWinner: raceAnalyses.predictedWinner,
        raceSummary: raceAnalyses.raceSummary,
        keyDynamics: raceAnalyses.keyDynamics,
        bettingRecommendation: raceAnalyses.bettingRecommendation,
      })
      .from(raceAnalyses)
      .where(and(analysesDateClause, analysesTrackClause, analysesRaceNumberClause, analysesRaceNameClause))
      .orderBy(desc(raceAnalyses.raceDate), desc(raceAnalyses.raceNumber))
      .limit(limit)),
    safeArrayQuery("selections", db
      .select({
        track: selections.track,
        raceDate: selections.raceDate,
        raceNumber: selections.raceNumber,
        raceName: selections.raceName,
        horseName: selections.horseName,
        horseNumber: selections.horseNumber,
        barrier: selections.barrier,
        jockey: selections.jockey,
        trainer: selections.trainer,
        runningStyle: selections.runningStyle,
        winPercentage: selections.winPercentage,
        marketOdds: selections.marketOdds,
        edge: selections.edge,
        confidence: selections.confidence,
        selectionType: selections.selectionType,
        bankerFlag: selections.bankerFlag,
        bankerTier: selections.bankerTier,
        // Convergence intelligence
        convergenceTier: selections.convergenceTier,
        convergenceScore: selections.convergenceScore,
        consensusScore: selections.consensusScore,
        consensusMentionCount: selections.consensusMentionCount,
        marketSignalScore: selections.marketSignalScore,
        marketSignalCategory: selections.marketSignalCategory,
        marketConfidenceScore: selections.marketConfidenceScore,
        marketConfidenceLabel: selections.marketConfidenceLabel,
        consensusVotePct: selections.consensusVotePct,
        consensusInjection: selections.consensusInjection,
        marketInjection: selections.marketInjection,
        convergenceGate: selections.convergenceGate,
        // Probabilities
        rawWinProb: selections.rawWinProb,
        calibratedWinProb: selections.calibratedWinProb,
        expectedValue: selections.expectedValue,
        // Fitness
        fitnessRunLabel: selections.fitnessRunLabel,
        fitnessIsAtPeakRun: selections.fitnessIsAtPeakRun,
        fitnessReadinessScore: selections.fitnessReadinessScore,
        fitnessPrepTrajectory: selections.fitnessPrepTrajectory,
        // Luckless
        lucklessFlag: selections.lucklessFlag,
        lucklessScore: selections.lucklessScore,
        // Franking
        frankingElo: selections.frankingElo,
        frankingScore: selections.frankingScore,
        pagerankAuthority: selections.pagerankAuthority,
        graphFrankingScore: selections.graphFrankingScore,
        communityStrength: selections.communityStrength,
        // Speed
        speedRating: selections.speedRating,
      })
      .from(selections)
      .where(and(selectionsDateClause, selectionsTrackClause, selectionsRaceNumberClause, selectionsRaceNameClause))
      .orderBy(desc(selections.raceDate), desc(selections.raceNumber))
      .limit(limit * 2)),
    safeArrayQuery("selection_results", db
      .select({
        track: selectionResults.track,
        raceDate: selectionResults.raceDate,
        raceNumber: selectionResults.raceNumber,
        horseName: selectionResults.horseName,
        predictedWinProb: selectionResults.predictedWinProb,
        marketOdds: selectionResults.marketOdds,
        edge: selectionResults.edge,
        confidence: selectionResults.confidence,
        actualPosition: selectionResults.actualPosition,
        won: selectionResults.won,
        placed: selectionResults.placed,
        startingPrice: selectionResults.startingPrice,
      })
      .from(selectionResults)
      .where(and(selectionResultsDateClause, selectionResultsTrackClause, selectionResultsRaceNumberClause))
      .orderBy(desc(selectionResults.raceDate), desc(selectionResults.raceNumber))
      .limit(limit * 2)),
    safeArrayQuery("performance_stats", db
      .select({
        dimension: performanceStats.dimension,
        dimensionValue: performanceStats.dimensionValue,
        periodStart: performanceStats.periodStart,
        periodEnd: performanceStats.periodEnd,
        totalBets: performanceStats.totalBets,
        winners: performanceStats.winners,
        strikeRate: performanceStats.strikeRate,
        roi: performanceStats.roi,
        averageEdge: performanceStats.averageEdge,
      })
      .from(performanceStats)
      .where(
        and(
          trackCandidates.length > 0 ? eq(performanceStats.dimension, "track") : undefined,
          trackCandidates.length > 0
            ? or(...trackCandidates.map((track) => ilike(performanceStats.dimensionValue, `%${track}%`)))
            : undefined,
        ),
      )
      .orderBy(desc(performanceStats.updatedAt))
      .limit(limit)),
  ] as const;

  const [racesRows, rrhRows, sectionalRows, strideTipRows, predictionRows, analysesRows, selectionRows, selectionResultRows, performanceRows] = await Promise.all(tasks);
  const evidence: StrideEvidenceItem[] = [];

  for (const row of racesRows) {
    evidence.push({
      source: "races",
      title: `${row.track} R${row.raceNumber}`,
      summary: [
        row.raceDate ? `date ${row.raceDate}` : undefined,
        row.raceName ? `name ${row.raceName}` : undefined,
        row.distance ? `distance ${row.distance}` : undefined,
        row.raceClass ? `class ${row.raceClass}` : undefined,
        row.going ? `going ${row.going}` : undefined,
        row.status ? `status ${row.status}` : undefined,
      ]
        .filter(Boolean)
        .join(" | "),
      track: row.track,
      raceDate: row.raceDate,
      raceNumber: row.raceNumber ?? undefined,
      payload: row as unknown as JsonRecord,
      score: 92,
    });
  }

  for (const row of rrhRows) {
    evidence.push({
      source: "race_results_history",
      title: `${row.track} R${row.raceNumber} result row`,
      summary: [
        row.horseName ? `horse ${row.horseName}` : undefined,
        row.position ? `position ${row.position}` : undefined,
        row.spOdds ? `SP ${row.spOdds}` : undefined,
        row.raceClass ? `class ${row.raceClass}` : undefined,
        row.going ? `going ${row.going}` : undefined,
      ]
        .filter(Boolean)
        .join(" | "),
      track: row.track,
      raceDate: row.raceDate,
      raceNumber: row.raceNumber ?? undefined,
      horseName: row.horseName,
      payload: row as unknown as JsonRecord,
      score: 90,
    });
  }

  for (const row of sectionalRows) {
    evidence.push({
      source: "sectional_times",
      title: `${row.track} R${row.raceNumber} sectionals`,
      summary: [
        row.horseName ? `horse ${row.horseName}` : undefined,
        row.distanceM ? `${row.distanceM}m` : undefined,
        row.last200mSpeed ? `last200 ${row.last200mSpeed}` : undefined,
        row.last400mSpeed ? `last400 ${row.last400mSpeed}` : undefined,
        row.last600mSpeed ? `last600 ${row.last600mSpeed}` : undefined,
        row.finishingBurst ? `burst ${row.finishingBurst}` : undefined,
        row.source ? `source ${row.source}` : undefined,
      ]
        .filter(Boolean)
        .join(" | "),
      track: row.track,
      raceDate: row.raceDate,
      raceNumber: row.raceNumber ?? undefined,
      horseName: row.horseName,
      payload: row as unknown as JsonRecord,
      score: 88,
    });
  }

  for (const row of strideTipRows) {
    evidence.push({
      source: "stride_tip_results",
      title: `${row.track} R${row.raceNumber} tip result`,
      summary: [
        row.tippedHorseName ? `tipped ${row.tippedHorseName}` : undefined,
        row.actualWinnerName ? `winner ${row.actualWinnerName}` : undefined,
        row.result ? `result ${row.result}` : undefined,
        row.tippedHorseSp ? `tipped SP ${row.tippedHorseSp}` : undefined,
        row.winnerSp ? `winner SP ${row.winnerSp}` : undefined,
        row.edgePct !== null && row.edgePct !== undefined ? `edge ${row.edgePct}` : undefined,
      ]
        .filter(Boolean)
        .join(" | "),
      track: row.track,
      raceDate: row.tipDate,
      raceNumber: row.raceNumber ?? undefined,
      horseName: row.tippedHorseName,
      payload: row as unknown as JsonRecord,
      score: 85,
    });
  }

  for (const row of predictionRows) {
    evidence.push({
      source: "prediction_audit",
      title: `${row.track} R${row.raceNumber} prediction audit`,
      summary: [
        row.horseName ? `horse ${row.horseName}` : undefined,
        row.predictedWinProb !== null && row.predictedWinProb !== undefined ? `win ${row.predictedWinProb}` : undefined,
        row.marketOdds !== null && row.marketOdds !== undefined ? `odds ${row.marketOdds}` : undefined,
        row.confidence ? `confidence ${row.confidence}` : undefined,
        row.edge !== null && row.edge !== undefined ? `edge ${row.edge}` : undefined,
        row.actualPosition !== null && row.actualPosition !== undefined ? `finish ${row.actualPosition}` : undefined,
      ]
        .filter(Boolean)
        .join(" | "),
      track: row.track,
      raceDate: row.raceDate,
      raceNumber: row.raceNumber ?? undefined,
      horseName: row.horseName,
      payload: row as unknown as JsonRecord,
      score: 83,
    });
  }

  for (const row of analysesRows) {
    evidence.push({
      source: "race_analyses",
      title: `${row.track} R${row.raceNumber} analysis`,
      summary: [
        row.raceName ? `name ${row.raceName}` : undefined,
        row.predictedWinner ? `winner ${row.predictedWinner}` : undefined,
        row.raceSummary ? row.raceSummary : undefined,
        row.keyDynamics ? row.keyDynamics : undefined,
        row.bettingRecommendation ? row.bettingRecommendation : undefined,
      ]
        .filter(Boolean)
        .join(" | "),
      track: row.track,
      raceDate: row.raceDate,
      raceNumber: row.raceNumber ?? undefined,
      payload: row as unknown as JsonRecord,
      score: 82,
    });
  }

  for (const row of selectionRows) {
    evidence.push({
      source: "selections",
      title: `${row.track} R${row.raceNumber} selection`,
      summary: [
        row.horseName ? `horse ${row.horseName}` : undefined,
        row.horseNumber ? `#${row.horseNumber}` : undefined,
        row.winPercentage !== null && row.winPercentage !== undefined ? `win ${row.winPercentage}%` : undefined,
        row.marketOdds !== null && row.marketOdds !== undefined ? `odds $${row.marketOdds}` : undefined,
        row.edge !== null && row.edge !== undefined ? `edge ${row.edge}%` : undefined,
        row.confidence ? `confidence ${row.confidence}` : undefined,
        row.convergenceTier ? `tier ${row.convergenceTier}` : undefined,
        row.convergenceScore !== null && row.convergenceScore !== undefined ? `conv ${row.convergenceScore}` : undefined,
        row.consensusScore !== null && row.consensusScore !== undefined ? `consensus ${row.consensusScore}` : undefined,
        row.marketSignalCategory ? `market ${row.marketSignalCategory}` : undefined,
        row.fitnessRunLabel ? `fitness ${row.fitnessRunLabel}` : undefined,
        row.fitnessIsAtPeakRun ? "PEAK RUN" : undefined,
        row.bankerFlag ? `banker ${row.bankerTier ?? ""}`.trim() : undefined,
        row.lucklessFlag ? "luckless" : undefined,
        row.selectionType ? `type ${row.selectionType}` : undefined,
      ]
        .filter(Boolean)
        .join(" | "),
      track: row.track,
      raceDate: row.raceDate,
      raceNumber: row.raceNumber ?? undefined,
      horseName: row.horseName,
      payload: row as unknown as JsonRecord,
      score: 80,
    });
  }

  for (const row of selectionResultRows) {
    evidence.push({
      source: "selection_results",
      title: `${row.track} R${row.raceNumber} selection result`,
      summary: [
        row.horseName ? `horse ${row.horseName}` : undefined,
        row.won ? "won" : undefined,
        row.placed ? "placed" : undefined,
        row.startingPrice !== null && row.startingPrice !== undefined ? `SP ${row.startingPrice}` : undefined,
        row.edge !== null && row.edge !== undefined ? `edge ${row.edge}` : undefined,
        row.confidence ? `confidence ${row.confidence}` : undefined,
      ]
        .filter(Boolean)
        .join(" | "),
      track: row.track,
      raceDate: row.raceDate,
      raceNumber: row.raceNumber ?? undefined,
      horseName: row.horseName,
      payload: row as unknown as JsonRecord,
      score: 79,
    });
  }

  for (const row of performanceRows) {
    evidence.push({
      source: "performance_stats",
      title: `${row.dimension} / ${row.dimensionValue}`,
      summary: [
        row.periodStart && row.periodEnd ? `${row.periodStart} → ${row.periodEnd}` : undefined,
        row.totalBets !== null && row.totalBets !== undefined ? `bets ${row.totalBets}` : undefined,
        row.winners !== null && row.winners !== undefined ? `winners ${row.winners}` : undefined,
        row.strikeRate !== null && row.strikeRate !== undefined ? `strike ${row.strikeRate}` : undefined,
        row.roi !== null && row.roi !== undefined ? `ROI ${row.roi}` : undefined,
        row.averageEdge !== null && row.averageEdge !== undefined ? `avg edge ${row.averageEdge}` : undefined,
      ]
        .filter(Boolean)
        .join(" | "),
      track: row.dimensionValue,
      payload: row as unknown as JsonRecord,
      score: 70,
    });
  }

  return evidence;
}

// ─── New intent-specific evidence fetchers ───────────────────────────────────

async function fetchJockeyTrainerEvidence(
  entities: StrideEntityHints,
  dateCandidates: string[],
  limit = 8,
): Promise<StrideEvidenceItem[]> {
  const jockeyName = entities.jockey?.trim();
  const trainerName = entities.trainer?.trim();
  if (!jockeyName && !trainerName) return [];

  const evidence: StrideEvidenceItem[] = [];
  const today = new Date().toISOString().split("T")[0];

  if (jockeyName) {
    const pattern = `%${jockeyName}%`;
    const rows = await safeArrayQuery("race_results_history", db
      .select({
        horseName: raceResultsHistory.horseName,
        track: raceResultsHistory.track,
        raceDate: raceResultsHistory.raceDate,
        raceNumber: raceResultsHistory.raceNumber,
        position: raceResultsHistory.position,
        fieldSize: raceResultsHistory.fieldSize,
        going: raceResultsHistory.going,
        distanceM: raceResultsHistory.distanceM,
        raceClass: raceResultsHistory.raceClass,
        spOdds: raceResultsHistory.spOdds,
        jockey: raceResultsHistory.jockey,
      })
      .from(raceResultsHistory)
      .where(ilike(raceResultsHistory.jockey, pattern))
      .orderBy(desc(raceResultsHistory.raceDate))
      .limit(limit * 3));

    const wins = rows.filter(r => r.position === 1).length;
    const places = rows.filter(r => r.position != null && r.position <= 3).length;
    const total = rows.length;
    const winPct = total > 0 ? ((wins / total) * 100).toFixed(1) : "0";
    const placePct = total > 0 ? ((places / total) * 100).toFixed(1) : "0";

    // Recent form (last 10 runs)
    const recentRuns = rows.slice(0, 10)
      .map(r => `${r.horseName} (${r.track} R${r.raceNumber}, ${r.raceDate}, pos ${r.position ?? "?"}/${r.fieldSize ?? "?"})`).join("; ");

    // Track breakdown
    const trackWins: Record<string, { wins: number; total: number }> = {};
    for (const r of rows) {
      const t = (r.track ?? "Unknown").toLowerCase();
      if (!trackWins[t]) trackWins[t] = { wins: 0, total: 0 };
      trackWins[t].total++;
      if (r.position === 1) trackWins[t].wins++;
    }
    const topTrack = Object.entries(trackWins).sort((a, b) => b[1].wins - a[1].wins)[0];

    evidence.push({
      source: "race_results_history",
      title: `Jockey stats: ${jockeyName}`,
      summary: `${jockeyName}: ${wins}/${total} wins (${winPct}%). Best track: ${topTrack?.[0] ?? "n/a"}.`,
      track: topTrack?.[0],
      raceDate: today,
      payload: {
        jockey: jockeyName,
        totalRuns: total,
        wins,
        places,
        winPct: `${winPct}%`,
        placePct: `${placePct}%`,
        bestTrack: topTrack ? `${topTrack[0]} (${topTrack[1].wins}/${topTrack[1].total} wins)` : undefined,
        recentRuns,
      } as JsonRecord,
      score: 85,
    });

    // Current bookings
    const currentBookings = await safeArrayQuery("selections", db
      .select({ horseName: selections.horseName, raceNumber: selections.raceNumber, track: selections.track, raceDate: selections.raceDate, marketOdds: selections.marketOdds, convergenceTier: selections.convergenceTier })
      .from(selections)
      .where(and(ilike(selections.jockey, pattern), eq(selections.raceDate, today)))
      .limit(5));

    if (currentBookings.length > 0) {
      evidence.push({
        source: "selections",
        title: `Current bookings today: ${jockeyName}`,
        summary: `${jockeyName} has ${currentBookings.length} booking${currentBookings.length === 1 ? "" : "s"} today.`,
        raceDate: today,
        payload: { jockey: jockeyName, bookings: currentBookings } as JsonRecord,
        score: 90,
      });
    }
  }

  if (trainerName) {
    const pattern = `%${trainerName}%`;
    const rows = await safeArrayQuery("selections", db
      .select({ horseName: selections.horseName, raceNumber: selections.raceNumber, track: selections.track, raceDate: selections.raceDate, marketOdds: selections.marketOdds, convergenceTier: selections.convergenceTier, winPercentage: selections.winPercentage })
      .from(selections)
      .where(and(ilike(selections.trainer, pattern), inArray(selections.raceDate, dateCandidates.length > 0 ? dateCandidates : [today])))
      .limit(limit));

    if (rows.length > 0) {
      evidence.push({
        source: "selections",
        title: `Trainer entries: ${trainerName}`,
        summary: `${trainerName} has ${rows.length} runner${rows.length === 1 ? "" : "s"} today.`,
        raceDate: today,
        payload: { trainer: trainerName, entries: rows } as JsonRecord,
        score: 85,
      });
    }
  }

  return evidence;
}

async function fetchQuaddieEvidence(
  entities: StrideEntityHints,
  dateCandidates: string[],
  question: string,
  limit = 12,
): Promise<StrideEvidenceItem[]> {
  const today = dateCandidates[0] ?? new Date().toISOString().split("T")[0];
  const trackKey = entities.trackKey ?? entities.track?.toLowerCase();

  // Detect leg race numbers from question: "R4/R6/R7/R8", "race 4, 6, 7, 8", "legs 4-8"
  const raceMatches = question.match(/\b(?:r(?:ace\s*)?(\d{1,2}))\b/gi) ?? [];
  const specifiedRaces = raceMatches.map(m => parseInt(m.replace(/\D/g, ""), 10)).filter(n => !isNaN(n));

  let legRaces: number[];
  if (specifiedRaces.length >= 2) {
    legRaces = specifiedRaces.slice(0, 4);
  } else {
    // Default: last 4 races of the meeting (query top race numbers from selections)
    const raceNums = await safeArrayQuery("selections", db
      .select({ raceNumber: selections.raceNumber })
      .from(selections)
      .where(and(
        inArray(selections.raceDate, [today]),
        trackKey ? ilike(selections.track, `%${trackKey}%`) : sql`1=1`,
      ))
      .orderBy(desc(selections.raceNumber))
      .limit(4));
    const nums = [...new Set(raceNums.map(r => r.raceNumber))].sort((a, b) => a - b);
    legRaces = nums.slice(-4);
  }

  if (legRaces.length === 0) return [];

  const legEvidence: StrideEvidenceItem[] = [];
  for (const raceNum of legRaces) {
    const rows = await safeArrayQuery("selections", db
      .select({
        horseName: selections.horseName,
        horseNumber: selections.horseNumber,
        raceNumber: selections.raceNumber,
        track: selections.track,
        raceDate: selections.raceDate,
        convergenceTier: selections.convergenceTier,
        convergenceScore: selections.convergenceScore,
        marketOdds: selections.marketOdds,
        winPercentage: selections.winPercentage,
        selectionType: selections.selectionType,
        bankerFlag: selections.bankerFlag,
      })
      .from(selections)
      .where(and(
        inArray(selections.raceDate, [today]),
        eq(selections.raceNumber, raceNum),
        trackKey ? ilike(selections.track, `%${trackKey}%`) : sql`1=1`,
        inArray(selections.convergenceTier as Parameters<typeof inArray>[0], ["LOCK", "CONFIRM"]),
      ))
      .orderBy(desc(selections.convergenceScore as Parameters<typeof desc>[0]))
      .limit(3));

    legEvidence.push({
      source: "selections",
      title: `Quaddie Leg — Race ${raceNum}`,
      summary: `Race ${raceNum}: ${rows.length} BET runner${rows.length === 1 ? "" : "s"} available.`,
      raceNumber: raceNum,
      raceDate: today,
      track: trackKey,
      payload: { legRace: raceNum, runners: rows, legCount: legRaces.length, allLegs: legRaces } as JsonRecord,
      score: 88,
    });
  }

  return legEvidence;
}

async function fetchSpeedMapEvidence(
  entities: StrideEntityHints,
  dateCandidates: string[],
  trackCandidates: string[],
  limit = 15,
): Promise<StrideEvidenceItem[]> {
  const today = dateCandidates[0] ?? new Date().toISOString().split("T")[0];
  const trackKey = trackCandidates[0] ?? entities.trackKey ?? entities.track?.toLowerCase();
  const raceNum = entities.raceNumber;

  if (!trackKey && !raceNum) return [];

  const rows = await safeArrayQuery("selections", db
    .select({
      horseName: selections.horseName,
      horseNumber: selections.horseNumber,
      barrier: selections.barrier,
      runningStyle: selections.runningStyle,
      raceNumber: selections.raceNumber,
      track: selections.track,
      raceDate: selections.raceDate,
      marketOdds: selections.marketOdds,
      jockey: selections.jockey,
    })
    .from(selections)
    .where(and(
      inArray(selections.raceDate, dateCandidates.length > 0 ? dateCandidates : [today]),
      raceNum ? eq(selections.raceNumber, raceNum) : sql`1=1`,
      trackKey ? ilike(selections.track, `%${trackKey}%`) : sql`1=1`,
    ))
    .orderBy(asc(selections.barrier as Parameters<typeof asc>[0]))
    .limit(limit));

  if (rows.length === 0) return [];

  return [{
    source: "selections",
    title: `Speed map data — ${entities.track ?? trackKey ?? "Race"} R${raceNum ?? "?"}`,
    summary: `${rows.length} runners with running style and barrier data for the pace map.`,
    raceNumber: raceNum,
    raceDate: today,
    track: trackKey,
    payload: { runners: rows, raceNumber: raceNum } as JsonRecord,
    score: 88,
  }];
}

async function fetchMarketScanEvidence(
  dateCandidates: string[],
  trackCandidates: string[],
  localArtifacts: Awaited<ReturnType<typeof loadLocalArtifacts>>,
  limit = 10,
): Promise<StrideEvidenceItem[]> {
  const today = dateCandidates[0] ?? new Date().toISOString().split("T")[0];
  const trackKey = trackCandidates[0];

  const rows = await safeArrayQuery("selections", db
    .select({
      horseName: selections.horseName,
      raceNumber: selections.raceNumber,
      track: selections.track,
      raceDate: selections.raceDate,
      marketOdds: selections.marketOdds,
      steamDriftPct: selections.steamDriftPct,
      isSharpMoney: selections.isSharpMoney,
      marketMoveCategory: selections.marketMoveCategory,
      convergenceTier: selections.convergenceTier,
      winPercentage: selections.winPercentage,
    })
    .from(selections)
    .where(and(
      inArray(selections.raceDate, [today]),
      trackKey ? ilike(selections.track, `%${trackKey}%`) : sql`1=1`,
      sql`abs(coalesce(${selections.steamDriftPct}, 0)) > 5 OR ${selections.isSharpMoney} = true`,
    ))
    .orderBy(desc(sql`abs(coalesce(${selections.steamDriftPct}, 0))`))
    .limit(limit));

  if (rows.length === 0 && localArtifacts.marketSignals.length > 0) {
    // Fall back to local market signals file
    return localArtifacts.marketSignals.slice(0, 5);
  }

  const steamers = rows.filter(r => (r.steamDriftPct ?? 0) > 0 || r.isSharpMoney);
  const drifters = rows.filter(r => (r.steamDriftPct ?? 0) < 0);

  if (rows.length === 0) return [];

  return [{
    source: "selections",
    title: `Market movers — ${today}`,
    summary: `${steamers.length} steamers and ${drifters.length} drifters detected today.`,
    raceDate: today,
    payload: { steamers, drifters, allMovers: rows, scanDate: today } as JsonRecord,
    score: 88,
  }];
}

async function fetchBlackbookTodayEvidence(
  dateCandidates: string[],
  trackCandidates: string[],
  limit = 10,
): Promise<StrideEvidenceItem[]> {
  const today = new Date().toISOString().split("T")[0];
  const weekAhead = new Date(Date.now() + 7 * 24 * 60 * 60 * 1000).toISOString().split("T")[0];
  const trackKey = trackCandidates[0];

  // Get active blackbook horses
  const bbEntries = await safeArrayQuery("blackbook_entries", db
    .select({ id: blackbookEntries.id, horseName: blackbookEntries.horseName, status: blackbookEntries.status, primaryReason: blackbookEntries.primaryReason, readinessBand: blackbookEntries.readinessBand, intakeConfidence: blackbookEntries.intakeConfidence })
    .from(blackbookEntries)
    .where(inArray(blackbookEntries.status, ["waiting", "nominated"]))
    .limit(50));

  if (bbEntries.length === 0) return [];

  const bbNames = bbEntries.map(e => e.horseName.toLowerCase());

  // Find any of those horses in upcoming selections
  const upcoming = await safeArrayQuery("selections", db
    .select({ horseName: selections.horseName, raceNumber: selections.raceNumber, track: selections.track, raceDate: selections.raceDate, marketOdds: selections.marketOdds, convergenceTier: selections.convergenceTier, winPercentage: selections.winPercentage })
    .from(selections)
    .where(and(
      sql`${selections.raceDate} >= ${today}`,
      sql`${selections.raceDate} <= ${weekAhead}`,
      trackKey ? ilike(selections.track, `%${trackKey}%`) : sql`1=1`,
    ))
    .limit(100));

  const matched = upcoming.filter(s => bbNames.includes(s.horseName.toLowerCase()));
  if (matched.length === 0) return [];

  const enriched = matched.map(s => {
    const bbEntry = bbEntries.find(e => e.horseName.toLowerCase() === s.horseName.toLowerCase());
    return { ...s, primaryReason: bbEntry?.primaryReason, readinessBand: bbEntry?.readinessBand, intakeConfidence: bbEntry?.intakeConfidence };
  });

  return [{
    source: "blackbook_entries",
    title: `Blackbook horses running this week`,
    summary: `${enriched.length} blackbook horse${enriched.length === 1 ? "" : "s"} found running this week out of ${bbEntries.length} tracked.`,
    raceDate: today,
    payload: { matches: enriched, totalBlackbook: bbEntries.length } as JsonRecord,
    score: 90,
  }];
}

async function fetchConvergenceEvidence(
  entities: StrideEntityHints,
  dateCandidates: string[],
  trackCandidates: string[],
  limit = 10,
): Promise<StrideEvidenceItem[]> {
  // convergence_output table is not in Drizzle schema — use raw SQL
  const dateFilter = dateCandidates.length > 0
    ? sql`race_date = ANY(${dateCandidates})`
    : sql`race_date >= ${getSydneyDate(-7)}`;
  const trackFilter = trackCandidates.length > 0
    ? sql`lower(track) LIKE ${`%${trackCandidates[0].toLowerCase()}%`}`
    : sql`1=1`;
  const raceFilter = entities.raceNumber
    ? sql`race_number = ${entities.raceNumber}`
    : sql`1=1`;

  const rows = await safeArrayQuery("convergence_output", db.execute(sql`
    SELECT horse_name, track, race_date, race_number,
           stride_score, consensus_score, market_signal_score,
           final_convergence_score, convergence_tier,
           vote_pct, tipsters_polled, pillars_strong,
           consensus_injection, market_injection,
           market_confidence_score, market_confidence_label,
           stride_score_raw, stride_score_final
    FROM convergence_output
    WHERE ${dateFilter} AND ${trackFilter} AND ${raceFilter}
    ORDER BY final_convergence_score DESC
    LIMIT ${limit * 2}
  `) as unknown as Promise<Array<Record<string, unknown>>>);

  const evidence: StrideEvidenceItem[] = [];
  for (const row of rows) {
    const horseName = String(row.horse_name ?? "");
    const track = String(row.track ?? "");
    const raceDate = String(row.race_date ?? "");
    const raceNumber = Number(row.race_number ?? 0);
    const tier = String(row.convergence_tier ?? "");
    const strideScore = row.stride_score != null ? Number(row.stride_score).toFixed(1) : "?";
    const consensusScore = row.consensus_score != null ? Number(row.consensus_score).toFixed(1) : "?";
    const marketScore = row.market_signal_score != null ? Number(row.market_signal_score).toFixed(1) : "?";
    const finalScore = row.final_convergence_score != null ? Number(row.final_convergence_score).toFixed(1) : "?";
    const votePct = row.vote_pct != null ? `${Number(row.vote_pct).toFixed(0)}%` : "";
    const pillarsStrong = row.pillars_strong ?? "";

    evidence.push({
      source: "convergence_output",
      title: `${track} R${raceNumber} convergence — ${horseName}`,
      summary: `tier ${tier} | final ${finalScore} | STRIDE ${strideScore} | consensus ${consensusScore} | market ${marketScore}${votePct ? ` | vote ${votePct}` : ""}${pillarsStrong ? ` | pillars ${pillarsStrong}` : ""}`,
      track,
      raceDate,
      raceNumber: raceNumber || undefined,
      horseName,
      payload: row as unknown as JsonRecord,
      score: 92,
    });
  }
  return evidence;
}

async function fetchPerformanceEvidence(
  trackCandidates: string[],
  limit = 8,
): Promise<StrideEvidenceItem[]> {
  const today = getSydneyDate(0);
  const thirtyDaysAgo = getSydneyDate(-30);
  const sevenDaysAgo = getSydneyDate(-7);
  const trackFilter = trackCandidates.length > 0
    ? or(...trackCandidates.map((t) => ilike(selectionResults.track, `%${t}%`)))
    : undefined;

  // Overall 30-day P/L
  const overallRows = await safeArrayQuery("selection_results_overall", db
    .select({
      totalBets: sql<number>`count(*)`.as("total_bets"),
      winners: sql<number>`sum(case when ${selectionResults.won} then 1 else 0 end)`.as("winners"),
      placers: sql<number>`sum(case when ${selectionResults.placed} then 1 else 0 end)`.as("placers"),
      strikeRate: sql<number>`round(avg(case when ${selectionResults.won} then 1.0 else 0.0 end) * 100, 1)`.as("strike_rate"),
      placeRate: sql<number>`round(avg(case when ${selectionResults.placed} then 1.0 else 0.0 end) * 100, 1)`.as("place_rate"),
      totalPL: sql<number>`round(sum(${selectionResults.profitLoss})::numeric, 2)`.as("total_pl"),
      roi: sql<number>`round(sum(${selectionResults.profitLoss})::numeric / nullif(sum(${selectionResults.stake}), 0) * 100, 1)`.as("roi"),
      avgOdds: sql<number>`round(avg(${selectionResults.marketOdds})::numeric, 2)`.as("avg_odds"),
    })
    .from(selectionResults)
    .where(and(
      sql`${selectionResults.raceDate} >= ${thirtyDaysAgo}`,
      sql`${selectionResults.raceDate} <= ${today}`,
      trackFilter,
    )));

  // 7-day P/L for trend
  const weekRows = await safeArrayQuery("selection_results_7d", db
    .select({
      totalBets: sql<number>`count(*)`.as("total_bets"),
      winners: sql<number>`sum(case when ${selectionResults.won} then 1 else 0 end)`.as("winners"),
      strikeRate: sql<number>`round(avg(case when ${selectionResults.won} then 1.0 else 0.0 end) * 100, 1)`.as("strike_rate"),
      totalPL: sql<number>`round(sum(${selectionResults.profitLoss})::numeric, 2)`.as("total_pl"),
      roi: sql<number>`round(sum(${selectionResults.profitLoss})::numeric / nullif(sum(${selectionResults.stake}), 0) * 100, 1)`.as("roi"),
    })
    .from(selectionResults)
    .where(and(
      sql`${selectionResults.raceDate} >= ${sevenDaysAgo}`,
      sql`${selectionResults.raceDate} <= ${today}`,
      trackFilter,
    )));

  // Breakdown by confidence tier
  const tierRows = await safeArrayQuery("selection_results_by_tier", db
    .select({
      confidence: selectionResults.confidence,
      totalBets: sql<number>`count(*)`.as("total_bets"),
      winners: sql<number>`sum(case when ${selectionResults.won} then 1 else 0 end)`.as("winners"),
      strikeRate: sql<number>`round(avg(case when ${selectionResults.won} then 1.0 else 0.0 end) * 100, 1)`.as("strike_rate"),
      totalPL: sql<number>`round(sum(${selectionResults.profitLoss})::numeric, 2)`.as("total_pl"),
      roi: sql<number>`round(sum(${selectionResults.profitLoss})::numeric / nullif(sum(${selectionResults.stake}), 0) * 100, 1)`.as("roi"),
    })
    .from(selectionResults)
    .where(and(
      sql`${selectionResults.raceDate} >= ${thirtyDaysAgo}`,
      sql`${selectionResults.raceDate} <= ${today}`,
      trackFilter,
    ))
    .groupBy(selectionResults.confidence));

  // Breakdown by track
  const trackRows = await safeArrayQuery("selection_results_by_track", db
    .select({
      track: selectionResults.track,
      totalBets: sql<number>`count(*)`.as("total_bets"),
      winners: sql<number>`sum(case when ${selectionResults.won} then 1 else 0 end)`.as("winners"),
      strikeRate: sql<number>`round(avg(case when ${selectionResults.won} then 1.0 else 0.0 end) * 100, 1)`.as("strike_rate"),
      totalPL: sql<number>`round(sum(${selectionResults.profitLoss})::numeric, 2)`.as("total_pl"),
      roi: sql<number>`round(sum(${selectionResults.profitLoss})::numeric / nullif(sum(${selectionResults.stake}), 0) * 100, 1)`.as("roi"),
    })
    .from(selectionResults)
    .where(and(
      sql`${selectionResults.raceDate} >= ${thirtyDaysAgo}`,
      sql`${selectionResults.raceDate} <= ${today}`,
    ))
    .groupBy(selectionResults.track)
    .orderBy(sql`sum(case when ${selectionResults.won} then 1 else 0 end) desc`)
    .limit(limit));

  const evidence: StrideEvidenceItem[] = [];
  const overall = overallRows[0];
  const week = weekRows[0];

  if (overall && Number(overall.totalBets) > 0) {
    evidence.push({
      source: "selection_results",
      title: "Performance — 30-day overall",
      summary: `${overall.totalBets} bets | ${overall.winners} winners | ${overall.strikeRate}% strike | $${overall.totalPL} P/L | ${overall.roi}% ROI | avg odds $${overall.avgOdds}`,
      payload: { period: "30d", ...overall } as unknown as JsonRecord,
      score: 95,
    });
  }

  if (week && Number(week.totalBets) > 0) {
    evidence.push({
      source: "selection_results",
      title: "Performance — 7-day trend",
      summary: `${week.totalBets} bets | ${week.winners} winners | ${week.strikeRate}% strike | $${week.totalPL} P/L | ${week.roi}% ROI`,
      payload: { period: "7d", ...week } as unknown as JsonRecord,
      score: 93,
    });
  }

  for (const row of tierRows) {
    if (Number(row.totalBets) > 0) {
      evidence.push({
        source: "selection_results",
        title: `Performance — ${row.confidence ?? "unknown"} confidence`,
        summary: `${row.totalBets} bets | ${row.winners} winners | ${row.strikeRate}% strike | $${row.totalPL} P/L | ${row.roi}% ROI`,
        payload: { period: "30d", dimension: "confidence", dimensionValue: row.confidence, ...row } as unknown as JsonRecord,
        score: 88,
      });
    }
  }

  for (const row of trackRows) {
    if (Number(row.totalBets) >= 3) {
      evidence.push({
        source: "selection_results",
        title: `Performance — ${row.track}`,
        summary: `${row.totalBets} bets | ${row.winners} winners | ${row.strikeRate}% strike | $${row.totalPL} P/L | ${row.roi}% ROI`,
        track: row.track,
        payload: { period: "30d", dimension: "track", dimensionValue: row.track, ...row } as unknown as JsonRecord,
        score: 85,
      });
    }
  }

  return evidence;
}

async function fetchMeetingOverviewEvidence(
  entities: StrideEntityHints,
  dateCandidates: string[],
  trackCandidates: string[],
  limit = 30,
): Promise<StrideEvidenceItem[]> {
  // Pull ALL selections for date+track (no race number filter) to give card-wide view
  const dateClause = dateCandidates.length > 0 ? inArray(selections.raceDate, dateCandidates) : undefined;
  const trackClause = trackCandidates.length > 0
    ? or(...trackCandidates.map((t) => ilike(selections.track, `%${t}%`)))
    : undefined;

  const rows = await safeArrayQuery("selections", db
    .select({
      track: selections.track,
      raceDate: selections.raceDate,
      raceNumber: selections.raceNumber,
      raceName: selections.raceName,
      horseName: selections.horseName,
      horseNumber: selections.horseNumber,
      barrier: selections.barrier,
      jockey: selections.jockey,
      trainer: selections.trainer,
      winPercentage: selections.winPercentage,
      marketOdds: selections.marketOdds,
      edge: selections.edge,
      confidence: selections.confidence,
      selectionType: selections.selectionType,
      bankerFlag: selections.bankerFlag,
      bankerTier: selections.bankerTier,
      convergenceTier: selections.convergenceTier,
      convergenceScore: selections.convergenceScore,
      convergenceGate: selections.convergenceGate,
      consensusScore: selections.consensusScore,
      marketSignalScore: selections.marketSignalScore,
      marketConfidenceLabel: selections.marketConfidenceLabel,
      fitnessRunLabel: selections.fitnessRunLabel,
      fitnessIsAtPeakRun: selections.fitnessIsAtPeakRun,
    })
    .from(selections)
    .where(and(dateClause, trackClause))
    .orderBy(selections.raceNumber, desc(selections.edge))
    .limit(limit));

  return rows.map((row) => ({
    source: "selections" as const,
    title: `${row.track} R${row.raceNumber} — ${row.horseName}`,
    summary: `${row.horseName} | ${row.convergenceTier ?? row.convergenceGate ?? "?"} | edge ${row.edge ?? "?"} | odds $${row.marketOdds ?? "?"} | ${row.confidence ?? "?"} conf`,
    track: row.track ?? undefined,
    raceDate: row.raceDate ?? undefined,
    raceNumber: row.raceNumber ?? undefined,
    horseName: row.horseName ?? undefined,
    payload: row as unknown as JsonRecord,
    score: 90,
  }));
}

async function fetchComparisonEvidence(
  entities: StrideEntityHints,
  dateCandidates: string[],
  trackCandidates: string[],
  horseCandidates: string[],
  limit = 20,
): Promise<StrideEvidenceItem[]> {
  if (horseCandidates.length < 2) return [];

  const evidence: StrideEvidenceItem[] = [];
  const dateClause = dateCandidates.length > 0 ? inArray(selections.raceDate, dateCandidates) : undefined;
  const trackClause = trackCandidates.length > 0
    ? or(...trackCandidates.map((t) => ilike(selections.track, `%${t}%`)))
    : undefined;

  // Query selections for both horses
  for (const horse of horseCandidates.slice(0, 2)) {
    const selRows = await safeArrayQuery("selections", db
      .select({
        track: selections.track,
        raceDate: selections.raceDate,
        raceNumber: selections.raceNumber,
        horseName: selections.horseName,
        winPercentage: selections.winPercentage,
        marketOdds: selections.marketOdds,
        edge: selections.edge,
        confidence: selections.confidence,
        convergenceTier: selections.convergenceTier,
        convergenceScore: selections.convergenceScore,
        consensusScore: selections.consensusScore,
        marketSignalScore: selections.marketSignalScore,
        fitnessRunLabel: selections.fitnessRunLabel,
        fitnessIsAtPeakRun: selections.fitnessIsAtPeakRun,
        lucklessFlag: selections.lucklessFlag,
        lucklessScore: selections.lucklessScore,
        frankingElo: selections.frankingElo,
        frankingScore: selections.frankingScore,
        speedRating: selections.speedRating,
      })
      .from(selections)
      .where(and(dateClause, trackClause, ilike(selections.horseName, `%${horse}%`)))
      .orderBy(desc(selections.raceDate))
      .limit(3));

    for (const row of selRows) {
      evidence.push({
        source: "selections",
        title: `Selection: ${row.horseName}`,
        summary: `${row.horseName} | ${row.convergenceTier ?? "?"} | edge ${row.edge ?? "?"} | odds $${row.marketOdds ?? "?"}`,
        track: row.track ?? undefined,
        raceDate: row.raceDate ?? undefined,
        raceNumber: row.raceNumber ?? undefined,
        horseName: row.horseName ?? undefined,
        payload: row as unknown as JsonRecord,
        score: 95,
      });
    }

    // Franking scores for horse
    const frankRows = await safeArrayQuery("franking_scores", db
      .select({
        horseName: frankingScores.horseName,
        frankingScore: frankingScores.frankingScore,
        frankingConfidence: frankingScores.frankingConfidence,
        globalElo: frankingScores.globalElo,
        antiFranked: frankingScores.antiFranked,
        lastRaceDate: frankingScores.lastRaceDate,
      })
      .from(frankingScores)
      .where(ilike(frankingScores.horseName, `%${horse}%`))
      .limit(2));

    for (const row of frankRows) {
      evidence.push({
        source: "franking_scores",
        title: `Franking: ${row.horseName}`,
        summary: `score ${row.frankingScore ?? "?"} | ELO ${row.globalElo ?? "?"}${row.antiFranked ? " | ANTI-FRANKED" : ""}`,
        horseName: row.horseName ?? undefined,
        raceDate: row.lastRaceDate ?? undefined,
        payload: row as unknown as JsonRecord,
        score: 88,
      });
    }

    // Sectional times for horse
    const sectRows = await safeArrayQuery("sectional_times", db
      .select({
        track: sectionalTimes.track,
        raceDate: sectionalTimes.raceDate,
        raceNumber: sectionalTimes.raceNumber,
        horseName: sectionalTimes.horseName,
        last600mSpeed: sectionalTimes.last600mSpeed,
        finishingBurst: sectionalTimes.finishingBurst,
      })
      .from(sectionalTimes)
      .where(ilike(sectionalTimes.horseName, `%${horse}%`))
      .orderBy(desc(sectionalTimes.raceDate))
      .limit(2));

    for (const row of sectRows) {
      evidence.push({
        source: "sectional_times",
        title: `Sectionals: ${row.horseName}`,
        summary: `last600 ${row.last600mSpeed ?? "?"} | burst ${row.finishingBurst ?? "?"}`,
        track: row.track ?? undefined,
        raceDate: row.raceDate ?? undefined,
        raceNumber: row.raceNumber ?? undefined,
        horseName: row.horseName ?? undefined,
        payload: row as unknown as JsonRecord,
        score: 85,
      });
    }
  }

  // Head-to-head history from race_results_history
  const h2hResults: StrideEvidenceItem[] = [];
  for (const horse of horseCandidates.slice(0, 2)) {
    const rrhRows = await safeArrayQuery("race_results_history", db
      .select({
        track: raceResultsHistory.track,
        raceDate: raceResultsHistory.raceDate,
        raceNumber: raceResultsHistory.raceNumber,
        horseName: raceResultsHistory.horseName,
        position: raceResultsHistory.position,
        spOdds: raceResultsHistory.spOdds,
      })
      .from(raceResultsHistory)
      .where(ilike(raceResultsHistory.horseName, `%${horse}%`))
      .orderBy(desc(raceResultsHistory.raceDate))
      .limit(5));

    for (const row of rrhRows) {
      h2hResults.push({
        source: "race_results_history",
        title: `History: ${row.horseName}`,
        summary: `${row.raceDate} ${row.track} R${row.raceNumber}: pos ${row.position ?? "?"}${row.spOdds ? ` ($${row.spOdds})` : ""}`,
        track: row.track ?? undefined,
        raceDate: row.raceDate ?? undefined,
        raceNumber: row.raceNumber ?? undefined,
        horseName: row.horseName ?? undefined,
        payload: row as unknown as JsonRecord,
        score: 80,
      });
    }
  }

  return [...evidence, ...h2hResults];
}

// ─── End new intent evidence fetchers ────────────────────────────────────────

export async function buildStrideRetrievalBundle(request: StrideRetrievalRequest): Promise<StrideRetrievalBundle> {
  const classification = classifyStrideQuestion(request.question, request.context);
  const entities = extractStrideEntities(request.question, request.context);
  const dateCandidates = buildDateCandidates(request.question, entities, request.context);
  const trackCandidates = await resolveTrackCandidates(request.question, request.context);
  const horseCandidates = buildHorseCandidates(entities, request.question, request.context);
  const limit = Math.max(1, Math.min(20, request.limit ?? 10));

  const localArtifacts = await loadLocalArtifacts(dateCandidates, trackCandidates, horseCandidates, entities.raceNumber);

  const [horseEvidence, raceEvidence] = await Promise.all([
    fetchHorseEvidence(entities, dateCandidates, limit),
    fetchRaceEvidence(entities, dateCandidates, trackCandidates, limit),
  ]);

  // Intent-specific supplemental evidence for new intent types
  let intentEvidence: StrideEvidenceItem[] = [];
  switch (classification.intent) {
    case "jockey_trainer":
      intentEvidence = await fetchJockeyTrainerEvidence(entities, dateCandidates, limit);
      break;
    case "quaddie_build":
      intentEvidence = await fetchQuaddieEvidence(entities, dateCandidates, request.question, limit);
      break;
    case "speed_map":
      intentEvidence = await fetchSpeedMapEvidence(entities, dateCandidates, trackCandidates, limit);
      break;
    case "market_scan":
      intentEvidence = await fetchMarketScanEvidence(dateCandidates, trackCandidates, localArtifacts, limit);
      break;
    case "blackbook_today":
      intentEvidence = await fetchBlackbookTodayEvidence(dateCandidates, trackCandidates, limit);
      break;
    case "performance":
      intentEvidence = await fetchPerformanceEvidence(trackCandidates, limit);
      break;
    case "consensus":
    case "franking":
      intentEvidence = await fetchConvergenceEvidence(entities, dateCandidates, trackCandidates, limit);
      break;
    case "meeting_overview":
      intentEvidence = await fetchMeetingOverviewEvidence(entities, dateCandidates, trackCandidates, limit * 3);
      break;
    case "horse_comparison":
      intentEvidence = await fetchComparisonEvidence(entities, dateCandidates, trackCandidates, horseCandidates, limit * 2);
      break;
  }

  const combinedEvidence = mergeEvidence([
    ...localArtifacts.racecards,
    ...localArtifacts.tips,
    ...localArtifacts.tipResults,
    ...localArtifacts.consensus,
    ...localArtifacts.marketSignals,
    ...localArtifacts.intelligence,
    ...horseEvidence,
    ...raceEvidence,
    ...intentEvidence,
  ]).slice(0, limit * 3);

  const bundle: StrideRetrievalBundle = {
    question: request.question,
    classification,
    entities,
    dateCandidates,
    trackCandidates,
    horseCandidates,
    localArtifacts,
    evidence: combinedEvidence,
    answerMode:
      combinedEvidence.length >= 6 ? "evidence-rich" : combinedEvidence.length > 0 ? "evidence-light" : "insufficient",
    summary: "",
    promptContext: "",
    sourceCounts: countEvidenceBySource(combinedEvidence),
    followUps: [],
  };

  bundle.summary = buildSummary(bundle);
  bundle.promptContext = buildPromptContext(bundle);
  bundle.followUps = buildFollowUps(bundle);
  return bundle;
}

function mergeEvidence(items: StrideEvidenceItem[]): StrideEvidenceItem[] {
  const deduped = new Map<string, StrideEvidenceItem>();
  for (const item of items) {
    const key = [
      item.source,
      normalizeCompact(item.track),
      item.raceDate || "",
      item.raceNumber || "",
      normalizeCompact(item.horseName),
      normalizeCompact(item.title),
    ].join("|");
    const existing = deduped.get(key);
    if (!existing || item.score > existing.score) {
      deduped.set(key, item);
    }
  }

  return [...deduped.values()].sort((a, b) => {
    const scoreDelta = b.score - a.score;
    if (scoreDelta !== 0) return scoreDelta;
    const dateDelta = String(b.raceDate ?? "").localeCompare(String(a.raceDate ?? ""));
    if (dateDelta !== 0) return dateDelta;
    return String(a.title).localeCompare(String(b.title));
  });
}

export function formatStrideEvidenceForPrompt(bundle: StrideRetrievalBundle): string {
  return bundle.promptContext;
}

export function formatStrideAnswerInstructions(bundle: StrideRetrievalBundle): string {
  const evidenceState =
    bundle.answerMode === "evidence-rich"
      ? "You have strong local racing evidence."
      : bundle.answerMode === "evidence-light"
        ? "You have some local racing evidence, but keep it concise."
        : "You do not have enough local racing evidence to guess. Ask a clarifying question or say what is missing.";

  return [
    "You are Ask Stride, a data-backed Australian horse racing assistant.",
    "Use the supplied evidence bundle before relying on general racing knowledge.",
    "If local data is absent or weak, say exactly what is missing instead of inventing facts.",
    "Always prefer local racecards, tips, intelligence files, and database evidence over generic commentary.",
    evidenceState,
  ].join(" ");
}

export async function gatherStrideEvidence(request: StrideRetrievalRequest): Promise<StrideRetrievalBundle> {
  return buildStrideRetrievalBundle(request);
}

export async function getStrideEvidenceSummary(request: StrideRetrievalRequest): Promise<string> {
  const bundle = await buildStrideRetrievalBundle(request);
  return bundle.summary;
}

export async function getStridePromptContext(request: StrideRetrievalRequest): Promise<string> {
  const bundle = await buildStrideRetrievalBundle(request);
  return bundle.promptContext;
}
