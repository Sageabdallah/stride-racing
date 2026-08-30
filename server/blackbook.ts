import type { Express } from "express";
import fs from "fs";
import path from "path";
import { fileURLToPath } from "url";
import { createHash } from "crypto";
import { sql } from "drizzle-orm";
import { db } from "./db";

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);
const projectRoot = path.resolve(__dirname, "..");
const racecardsDir = path.join(projectRoot, "racecards");

const HISTORY_LOOKBACK_DAYS = 35;
const UPCOMING_LOOKAHEAD_DAYS = 21;
const SYNC_TTL_MS = 15 * 60 * 1000;

type ReasonCode =
  | "CHECKED"
  | "HELD_UP"
  | "ELITE_CLOSING_SPLIT_NO_GAP"
  | "THREE_WIDE_NO_COVER"
  | "BARRIER_DISASTER_ON_BIASED_DAY"
  | "TRACK_BIAS_VICTIM"
  | "TEMPO_VICTIM"
  | "WET_TRACK_EXPOSURE"
  | "CLASS_PREPARATION_RUN";

type ReadinessBand = "REVENGE RACE" | "PRIMED" | "WATCHING" | "MONITORING" | "WAITING";
type EntryStatus = "active" | "monitoring" | "waiting" | "expired";
type AlertType = "nomination" | "barrier" | "market" | "condition" | "tempo" | "race_day_brief" | "expiry_warning";

interface ResultsRow {
  id: string;
  horse_id: string | null;
  horse_name: string;
  race_id: string | null;
  track: string;
  race_date: string;
  distance_m: number | null;
  race_class: string | null;
  going: string | null;
  position: number | null;
  margin_lengths: number | null;
  barrier: number | null;
  sp_odds: number | null;
  field_size: number | null;
  race_name: string | null;
  race_number: number | null;
}

interface SectionalRow {
  race_results_history_id: string | null;
  track: string;
  race_date: string;
  race_number: number;
  race_name: string | null;
  horse_name: string;
  distance_m: number | null;
  winning_time: string | null;
  splits_json: Record<string, number> | null;
  last_200m_time: number | null;
  last_400m_time: number | null;
  last_600m_time: number | null;
  last_800m_time: number | null;
  finishing_burst: number | null;
  rsi: number | null;
  source: string | null;
}

interface FrankingRow {
  horse_id: string | null;
  horse_name: string;
  franking_score: number | null;
  franking_confidence: number | null;
  field_strength_avg: number | null;
  form_quality_trend: number | null;
  collateral_advantage: number | null;
}

interface CommentEvidence {
  horseId: string | null;
  horseName: string;
  raceDate: string;
  raceNumber: number;
  track: string;
  comment: string | null;
  incidentTags: string[];
  incidentSummary: string;
}

interface RaceFieldStats {
  raceKey: string;
  avgLast600: number | null;
  avgLast400: number | null;
  avgLast200: number | null;
  avgRsi: number | null;
  winnerLast600: number | null;
  winnerLast400: number | null;
  winnerLast200: number | null;
}

interface BiasSnapshot {
  id: string;
  track: string;
  raceDate: string;
  biasLabel: "inside_advantage" | "outside_advantage" | "neutral";
  confidence: "high" | "medium" | "low";
  racesSampled: number;
  insideWinRate: number;
  middleWinRate: number;
  outsideWinRate: number;
  avgWinnerBarrierRatio: number;
  statsJson: Record<string, unknown>;
}

interface IdealConditions {
  barrier: { max: number; softMax: number };
  distance: { target: number | null; tolerance: number; softTolerance: number };
  tempoNeed: "genuine" | "genuine_plus" | "soft" | "neutral";
  trackCondition: "good" | "good_to_soft" | "neutral";
  daysBetweenRuns: { min: number; max: number };
  classRule: "same_or_lower" | "same_or_slightly_higher" | "neutral";
  summary: string;
}

interface HistoricalCandidate {
  id: string;
  canonicalHorseKey: string;
  horseId: string | null;
  horseName: string;
  sourceRaceId: string | null;
  sourceTrack: string;
  sourceRaceDate: string;
  sourceRaceNumber: number;
  sourceRaceName: string | null;
  sourceDistanceM: number | null;
  sourceGoing: string | null;
  sourceRaceClass: string | null;
  sourceFieldSize: number | null;
  sourcePosition: number | null;
  sourceBarrier: number | null;
  sourceMarginLengths: number | null;
  primaryReason: ReasonCode;
  secondaryEvidenceTags: string[];
  sourceComment: string | null;
  sourceIncidentsJson: unknown[];
  incidentSummary: string;
  last600mTime: number | null;
  last400mTime: number | null;
  last200mTime: number | null;
  fieldAvgLast600m: number | null;
  fieldAvgLast400m: number | null;
  fieldAvgLast200m: number | null;
  sectionalDelta600m: number | null;
  sectionalDelta400m: number | null;
  sectionalDelta200m: number | null;
  winnerLast600m: number | null;
  winnerLast400m: number | null;
  winnerLast200m: number | null;
  frankingScore: number | null;
  frankingConfidence: number | null;
  idealConditions: IdealConditions;
  evidenceJson: Record<string, unknown>;
  entryAnalysis: Record<string, unknown>;
  intakeConfidence: number;
  readinessBand: ReadinessBand;
  status: EntryStatus;
  expiryReason: string | null;
}

interface UpcomingRunContext {
  id: string;
  blackbookEntryId: string;
  canonicalHorseKey: string;
  horseName: string;
  track: string;
  raceDate: string;
  raceNumber: number;
  raceName: string | null;
  offTime: string | null;
  distanceM: number | null;
  going: string | null;
  raceClass: string | null;
  fieldSize: number | null;
  barrier: number | null;
  daysSinceSourceRun: number | null;
  tempoSummary: string;
  tempoLeaderCount: number;
  marketPrice: number | null;
  truePrice: number | null;
  marketImpliedProb: number | null;
  modelWinProb: number | null;
  valueEdgePct: number | null;
  readinessScore: number;
  readinessBand: ReadinessBand;
  verdict: "BACK" | "WATCH" | "NEEDS SCENARIO";
  status: "nominated" | "raceday";
  breakdownJson: Record<string, unknown>;
  conditionAlignmentJson: Record<string, unknown>;
  raceDayBriefJson: Record<string, unknown>;
}

interface UpcomingNomination {
  horseId: string | null;
  horseName: string;
  canonicalHorseKey: string;
  track: string;
  raceDate: string;
  raceNumber: number;
  raceName: string | null;
  offTime: string | null;
  distanceM: number | null;
  going: string | null;
  raceClass: string | null;
  fieldSize: number | null;
  barrier: number | null;
  marketPrice: number | null;
  modelWinProb: number | null;
  truePrice: number | null;
  marketImpliedProb: number | null;
  valueEdgePct: number | null;
  tempoSummary: string;
  tempoLeaderCount: number;
}

interface AlertRecord {
  id: string;
  eventKey: string;
  blackbookEntryId: string;
  blackbookEntryRunId: string | null;
  horseName: string;
  track: string | null;
  raceDate: string | null;
  raceNumber: number | null;
  alertType: AlertType;
  severity: "info" | "positive" | "warning";
  title: string;
  message: string;
  readinessScore: number | null;
}

interface BlackbookSyncState {
  generatedAt: string;
  totalEntries: number;
  totalRuns: number;
  totalAlerts: number;
}

let lastSyncAt = 0;
let syncPromise: Promise<BlackbookSyncState> | null = null;

const regionMatchers: Array<{ region: string; patterns: RegExp[] }> = [
  { region: "Sydney", patterns: [/rosehill/i, /randwick/i, /kensington/i, /canterbury/i, /warwick/i, /newcastle/i] },
  { region: "Melbourne", patterns: [/caulfield/i, /flemington/i, /moonee/i, /sandown/i, /cranbourne/i, /geelong/i, /ballarat/i] },
  { region: "WA", patterns: [/ascot/i, /belmont/i, /pinjarra/i] },
  { region: "Queensland", patterns: [/eagle farm/i, /doomben/i, /sunshine/i, /gold coast/i, /aquis/i, /ipswich/i] },
  { region: "South Australia", patterns: [/morphettville/i, /murray bridge/i, /oakbank/i, /gawler/i] },
];

const incidentPatterns: Array<{ tag: string; regex: RegExp; summary: string }> = [
  { tag: "CHECKED", regex: /\b(checked|steadied|hampered|crowded|tightened|clipped heels)\b/i, summary: "checked or interfered in running" },
  { tag: "HELD_UP", regex: /\b(held up|blocked|no room|couldn't get clear|unable to improve|no clear run)\b/i, summary: "held up for a run" },
  { tag: "WIDE_NO_COVER", regex: /\b(no cover|wide without cover|wide throughout|posted wide)\b/i, summary: "raced without cover" },
  { tag: "THREE_WIDE_PLUS", regex: /\b(three wide|four wide|five wide|covered extra ground)\b/i, summary: "covered extra ground out wide" },
  { tag: "WET_TRACK_EXPOSURE", regex: /\b(didn't handle|failed on|unsuited by).*(soft|heavy|wet)\b/i, summary: "didn't handle the wet ground" },
  { tag: "CLASS_PREPARATION_RUN", regex: /\b(target|improve next|strips fitter|needed the run|looking for further)\b/i, summary: "looked like a setup run" },
];

function stableId(...parts: Array<string | number | null | undefined>) {
  const joined = parts.map((part) => String(part ?? "")).join("|");
  return createHash("sha1").update(joined).digest("hex");
}

function normalizeName(value: string | null | undefined) {
  return (value || "").toLowerCase().replace(/[^a-z0-9]/g, "");
}

function normalizeTrack(value: string | null | undefined) {
  return normalizeName(value).replace(/^(sportsbet|tab|theagency|aquispark)/, "");
}

function tracksRoughlyMatch(left: string | null | undefined, right: string | null | undefined) {
  const a = normalizeTrack(left);
  const b = normalizeTrack(right);
  return a === b || a.includes(b) || b.includes(a);
}

function parseDateOnly(value: string) {
  return new Date(`${value}T00:00:00+11:00`);
}

function todayInSydney() {
  return new Date(new Date().toLocaleString("en-US", { timeZone: "Australia/Sydney" }));
}

function formatDateOnly(date: Date) {
  return date.toLocaleDateString("en-CA", { timeZone: "Australia/Sydney" });
}

function addDays(date: Date, days: number) {
  const copy = new Date(date);
  copy.setDate(copy.getDate() + days);
  return copy;
}

function clamp(value: number, min: number, max: number) {
  return Math.min(max, Math.max(min, value));
}

function roundTo(value: number | null, places = 2) {
  if (value === null || Number.isNaN(value)) {
    return null;
  }
  const factor = 10 ** places;
  return Math.round(value * factor) / factor;
}

function parseDistance(value: string | number | null | undefined) {
  if (typeof value === "number") {
    return Number.isFinite(value) ? value : null;
  }
  if (!value) {
    return null;
  }
  const match = String(value).match(/(\d{3,4})/);
  return match ? Number(match[1]) : null;
}

function parsePrice(value: unknown) {
  if (typeof value === "number") {
    return Number.isFinite(value) ? value : null;
  }
  if (typeof value === "string") {
    const numeric = Number(value.replace(/[^0-9.]/g, ""));
    return Number.isFinite(numeric) && numeric > 0 ? numeric : null;
  }
  if (value && typeof value === "object") {
    const maybeOdds = value as Record<string, unknown>;
    return parsePrice(maybeOdds.win_odds ?? maybeOdds.odds ?? maybeOdds.sp);
  }
  return null;
}

function parseWinProbPct(value: unknown) {
  const numeric = typeof value === "number" ? value : Number(value);
  if (!Number.isFinite(numeric)) {
    return null;
  }
  return numeric > 1 ? numeric : numeric * 100;
}

function broadGoingCategory(going: string | null | undefined): "good" | "soft" | "heavy" | "unknown" {
  const value = (going || "").toLowerCase();
  if (value.includes("good") || value.includes("firm")) return "good";
  if (value.includes("soft")) return "soft";
  if (value.includes("heavy") || value.includes("slow")) return "heavy";
  return "unknown";
}

function classRank(raceClass: string | null | undefined) {
  const value = (raceClass || "").toLowerCase();
  if (value.includes("group 1") || value.includes("g1")) return 6;
  if (value.includes("group 2") || value.includes("g2")) return 5;
  if (value.includes("group 3") || value.includes("g3")) return 4;
  if (value.includes("listed")) return 3;
  if (value.includes("benchmark") || value.includes("bm") || value.includes("handicap")) return 2;
  if (value.includes("maiden")) return 1;
  return 2;
}

function trackRegion(track: string | null | undefined) {
  const value = track || "";
  const match = regionMatchers.find(({ patterns }) => patterns.some((pattern) => pattern.test(value)));
  return match?.region || "Other";
}

function readinessBand(score: number): ReadinessBand {
  if (score >= 9) return "REVENGE RACE";
  if (score >= 7) return "PRIMED";
  if (score >= 5) return "WATCHING";
  if (score >= 3) return "MONITORING";
  return "WAITING";
}

function reasonLabel(reason: ReasonCode) {
  switch (reason) {
    case "CHECKED": return "Checked In Running";
    case "HELD_UP": return "Held Up For A Run";
    case "ELITE_CLOSING_SPLIT_NO_GAP": return "Elite Closing Split, No Gap";
    case "THREE_WIDE_NO_COVER": return "Three-Wide, No Cover";
    case "BARRIER_DISASTER_ON_BIASED_DAY": return "Barrier Disaster On A Biased Day";
    case "TRACK_BIAS_VICTIM": return "Track Bias Victim";
    case "TEMPO_VICTIM": return "Tempo Victim";
    case "WET_TRACK_EXPOSURE": return "Wet Track Exposure";
    case "CLASS_PREPARATION_RUN": return "Class Preparation Run";
  }
}

function extractIncidentTags(comment: string | null | undefined) {
  if (!comment) {
    return [];
  }

  return incidentPatterns
    .filter(({ regex }) => regex.test(comment))
    .map(({ tag }) => tag);
}

function incidentSummaryFromTags(tags: string[]) {
  if (tags.length === 0) {
    return "";
  }

  return incidentPatterns
    .filter(({ tag }) => tags.includes(tag))
    .map(({ summary }) => summary)
    .join("; ");
}

async function ensureTables() {
  await db.execute(sql`
    CREATE TABLE IF NOT EXISTS blackbook_entries (
      id text PRIMARY KEY,
      canonical_horse_key text NOT NULL,
      horse_id text,
      horse_name text NOT NULL,
      source_race_id text,
      source_track text NOT NULL,
      source_race_date text NOT NULL,
      source_race_number integer NOT NULL,
      source_race_name text,
      source_distance_m integer,
      source_going text,
      source_race_class text,
      source_field_size integer,
      source_position integer,
      source_barrier integer,
      source_margin_lengths real,
      primary_reason text NOT NULL,
      secondary_evidence_tags jsonb,
      source_comment text,
      source_incidents_json jsonb,
      incident_summary text,
      last_600m_time real,
      last_400m_time real,
      last_200m_time real,
      field_avg_last_600m real,
      field_avg_last_400m real,
      field_avg_last_200m real,
      sectional_delta_600m real,
      sectional_delta_400m real,
      sectional_delta_200m real,
      winner_last_600m real,
      winner_last_400m real,
      winner_last_200m real,
      franking_score real,
      franking_confidence real,
      ideal_conditions_json jsonb,
      evidence_json jsonb,
      entry_analysis_json jsonb,
      intake_confidence real,
      readiness_band text,
      status text DEFAULT 'waiting',
      expiry_reason text,
      created_at timestamp DEFAULT now(),
      updated_at timestamp DEFAULT now()
    )
  `);

  await db.execute(sql`
    CREATE TABLE IF NOT EXISTS blackbook_entry_runs (
      id text PRIMARY KEY,
      blackbook_entry_id text NOT NULL REFERENCES blackbook_entries(id) ON DELETE CASCADE,
      canonical_horse_key text NOT NULL,
      horse_name text NOT NULL,
      track text NOT NULL,
      race_date text NOT NULL,
      race_number integer NOT NULL,
      race_name text,
      off_time text,
      distance_m integer,
      going text,
      race_class text,
      field_size integer,
      barrier integer,
      days_since_source_run integer,
      tempo_summary text,
      tempo_leader_count integer,
      market_price real,
      true_price real,
      market_implied_prob real,
      model_win_prob real,
      value_edge_pct real,
      readiness_score integer,
      readiness_band text,
      verdict text,
      status text DEFAULT 'nominated',
      breakdown_json jsonb,
      condition_alignment_json jsonb,
      race_day_brief_json jsonb,
      created_at timestamp DEFAULT now(),
      updated_at timestamp DEFAULT now()
    )
  `);

  await db.execute(sql`
    CREATE TABLE IF NOT EXISTS blackbook_alerts (
      id text PRIMARY KEY,
      event_key text NOT NULL UNIQUE,
      blackbook_entry_id text NOT NULL REFERENCES blackbook_entries(id) ON DELETE CASCADE,
      blackbook_entry_run_id text REFERENCES blackbook_entry_runs(id) ON DELETE CASCADE,
      horse_name text NOT NULL,
      track text,
      race_date text,
      race_number integer,
      alert_type text NOT NULL,
      severity text DEFAULT 'info',
      title text NOT NULL,
      message text NOT NULL,
      readiness_score integer,
      created_at timestamp DEFAULT now(),
      updated_at timestamp DEFAULT now()
    )
  `);

  await db.execute(sql`
    CREATE TABLE IF NOT EXISTS track_day_bias (
      id text PRIMARY KEY,
      track text NOT NULL,
      race_date text NOT NULL,
      bias_label text NOT NULL,
      confidence text NOT NULL,
      races_sampled integer DEFAULT 0,
      inside_win_rate real,
      middle_win_rate real,
      outside_win_rate real,
      avg_winner_barrier_ratio real,
      stats_json jsonb,
      created_at timestamp DEFAULT now(),
      updated_at timestamp DEFAULT now()
    )
  `);
}

async function queryRows<T>(statement: ReturnType<typeof sql>): Promise<T[]> {
  const result = await db.execute(statement);
  return (result.rows ?? []) as T[];
}

async function loadHistoricalRows() {
  const today = todayInSydney();
  const fromDate = formatDateOnly(addDays(today, -HISTORY_LOOKBACK_DAYS));

  const results = await queryRows<ResultsRow>(sql`
    SELECT
      id,
      horse_id,
      horse_name,
      race_id,
      track,
      race_date,
      distance_m,
      race_class,
      going,
      position,
      margin_lengths,
      barrier,
      sp_odds,
      field_size,
      race_name,
      race_number
    FROM race_results_history
    WHERE race_date >= ${fromDate}
    ORDER BY race_date DESC, track ASC, race_number ASC
  `);

  const sectionals = await queryRows<SectionalRow>(sql`
    SELECT
      race_results_history_id,
      track,
      race_date,
      race_number,
      race_name,
      horse_name,
      distance_m,
      winning_time,
      splits_json,
      last_200m_time,
      last_400m_time,
      last_600m_time,
      last_800m_time,
      finishing_burst,
      rsi,
      source
    FROM sectional_times
    WHERE race_date >= ${fromDate}
    ORDER BY race_date DESC, track ASC, race_number ASC
  `);

  const franking = await queryRows<FrankingRow>(sql`
    SELECT
      horse_id,
      horse_name,
      franking_score,
      franking_confidence,
      field_strength_avg,
      form_quality_trend,
      collateral_advantage
    FROM franking_scores
  `);

  return { results, sectionals, franking };
}

function buildSectionalMaps(sectionals: SectionalRow[]) {
  const byHorseKey = new Map<string, SectionalRow>();
  const byRaceKey = new Map<string, SectionalRow[]>();

  for (const row of sectionals) {
    const raceKey = `${row.race_date}|${normalizeTrack(row.track)}|${row.race_number}`;
    const horseKey = `${raceKey}|${normalizeName(row.horse_name)}`;
    byHorseKey.set(horseKey, row);
    byRaceKey.set(raceKey, [...(byRaceKey.get(raceKey) || []), row]);
  }

  return { byHorseKey, byRaceKey };
}

function average(values: Array<number | null | undefined>) {
  const valid = values.filter((value): value is number => typeof value === "number" && Number.isFinite(value));
  if (valid.length === 0) {
    return null;
  }
  return valid.reduce((sum, value) => sum + value, 0) / valid.length;
}

function buildRaceStats(results: ResultsRow[], sectionals: SectionalRow[]) {
  const { byHorseKey, byRaceKey } = buildSectionalMaps(sectionals);
  const winnerLookup = new Map<string, ResultsRow>();

  for (const row of results) {
    if (row.position === 1) {
      winnerLookup.set(`${row.race_date}|${normalizeTrack(row.track)}|${row.race_number}`, row);
    }
  }

  const stats = new Map<string, RaceFieldStats>();
  for (const [raceKey, raceRows] of byRaceKey.entries()) {
    const winner = winnerLookup.get(raceKey);
    const winnerSectional = winner
      ? byHorseKey.get(`${raceKey}|${normalizeName(winner.horse_name)}`)
      : undefined;

    stats.set(raceKey, {
      raceKey,
      avgLast600: average(raceRows.map((row) => row.last_600m_time)),
      avgLast400: average(raceRows.map((row) => row.last_400m_time)),
      avgLast200: average(raceRows.map((row) => row.last_200m_time)),
      avgRsi: average(raceRows.map((row) => row.rsi)),
      winnerLast600: winnerSectional?.last_600m_time ?? null,
      winnerLast400: winnerSectional?.last_400m_time ?? null,
      winnerLast200: winnerSectional?.last_200m_time ?? null,
    });
  }

  return { byHorseKey, stats };
}

function buildFrankingMap(rows: FrankingRow[]) {
  const byHorseId = new Map<string, FrankingRow>();
  const byHorseName = new Map<string, FrankingRow>();

  for (const row of rows) {
    if (row.horse_id) {
      byHorseId.set(row.horse_id, row);
    }
    byHorseName.set(normalizeName(row.horse_name), row);
  }

  return {
    get(horseId: string | null, horseName: string) {
      if (horseId && byHorseId.has(horseId)) {
        return byHorseId.get(horseId) || null;
      }
      return byHorseName.get(normalizeName(horseName)) || null;
    },
  };
}

function buildResultsByHorse(results: ResultsRow[]) {
  const byHorse = new Map<string, ResultsRow[]>();
  for (const row of results) {
    const key = row.horse_id || normalizeName(row.horse_name);
    byHorse.set(key, [...(byHorse.get(key) || []), row]);
  }

  for (const rows of byHorse.values()) {
    rows.sort((a, b) => a.race_date.localeCompare(b.race_date));
  }

  return byHorse;
}

function compareResultChronology(left: ResultsRow, right: ResultsRow) {
  const dateCompare = left.race_date.localeCompare(right.race_date);
  if (dateCompare !== 0) {
    return dateCompare;
  }
  return (left.race_number || 0) - (right.race_number || 0);
}

function latestResultPerHorse(results: ResultsRow[]) {
  const latest = new Map<string, ResultsRow>();

  for (const row of results) {
    const key = row.horse_id || normalizeName(row.horse_name);
    const current = latest.get(key);
    if (!current || compareResultChronology(row, current) > 0) {
      latest.set(key, row);
    }
  }

  return Array.from(latest.values()).sort((a, b) => compareResultChronology(b, a));
}

function loadJsonFile(filePath: string) {
  try {
    return JSON.parse(fs.readFileSync(filePath, "utf8"));
  } catch (error) {
    return null;
  }
}

function buildCommentEvidenceIndex() {
  const evidence = new Map<string, CommentEvidence>();
  const files = fs.existsSync(racecardsDir)
    ? fs.readdirSync(racecardsDir).filter((file) => file.startsWith("racecard_") && file.endsWith(".json"))
    : [];

  for (const file of files) {
    const payload = loadJsonFile(path.join(racecardsDir, file));
    if (!Array.isArray(payload)) {
      continue;
    }

    for (const meet of payload) {
      for (const race of meet.races || []) {
        const raceNumber = Number(race.race_number);
        for (const runner of race.runners || []) {
          const tags = extractIncidentTags(runner.comment);
          if (!runner.comment && tags.length === 0) {
            continue;
          }

          const record: CommentEvidence = {
            horseId: runner.horse_id || null,
            horseName: runner.horse || runner.name || "",
            raceDate: race.date || meet.date,
            raceNumber,
            track: race.course || meet.course,
            comment: runner.comment || null,
            incidentTags: tags,
            incidentSummary: incidentSummaryFromTags(tags),
          };

          const key = `${record.raceDate}|${normalizeTrack(record.track)}|${record.raceNumber}|${record.horseId || normalizeName(record.horseName)}`;
          evidence.set(key, record);
        }
      }
    }
  }

  return evidence;
}

function computeTrackBias(results: ResultsRow[]) {
  const grouped = new Map<string, ResultsRow[]>();

  for (const row of results) {
    if (row.position !== 1 || !row.field_size || !row.barrier) {
      continue;
    }

    const key = `${row.race_date}|${normalizeTrack(row.track)}`;
    grouped.set(key, [...(grouped.get(key) || []), row]);
  }

  const snapshots: BiasSnapshot[] = [];
  for (const [key, rows] of grouped.entries()) {
    const barrierRatios = rows
      .map((row) => (row.field_size && row.barrier ? row.barrier / row.field_size : null))
      .filter((value): value is number => typeof value === "number" && Number.isFinite(value));

    if (barrierRatios.length === 0) {
      continue;
    }

    const inside = barrierRatios.filter((value) => value <= 0.33).length;
    const middle = barrierRatios.filter((value) => value > 0.33 && value < 0.67).length;
    const outside = barrierRatios.filter((value) => value >= 0.67).length;
    const avgRatio = average(barrierRatios) || 0.5;

    let biasLabel: BiasSnapshot["biasLabel"] = "neutral";
    if (avgRatio <= 0.38 && inside >= Math.max(outside + 1, Math.ceil(barrierRatios.length * 0.45))) {
      biasLabel = "inside_advantage";
    } else if (avgRatio >= 0.62 && outside >= Math.max(inside + 1, Math.ceil(barrierRatios.length * 0.45))) {
      biasLabel = "outside_advantage";
    }

    const confidence = barrierRatios.length >= 8
      ? "high"
      : barrierRatios.length >= 5
        ? "medium"
        : "low";

    const sampleRow = rows[0];
    snapshots.push({
      id: stableId("bias", sampleRow.track, sampleRow.race_date),
      track: sampleRow.track,
      raceDate: sampleRow.race_date,
      biasLabel,
      confidence,
      racesSampled: barrierRatios.length,
      insideWinRate: roundTo(inside / barrierRatios.length, 3) || 0,
      middleWinRate: roundTo(middle / barrierRatios.length, 3) || 0,
      outsideWinRate: roundTo(outside / barrierRatios.length, 3) || 0,
      avgWinnerBarrierRatio: roundTo(avgRatio, 3) || 0.5,
      statsJson: {
        winnerBarrierRatios: barrierRatios.map((value) => roundTo(value, 3)),
      },
    });
  }

  return snapshots;
}

function buildIdealConditions(reason: ReasonCode, sourceDistanceM: number | null): IdealConditions {
  switch (reason) {
    case "CHECKED":
      return {
        barrier: { max: 10, softMax: 12 },
        distance: { target: sourceDistanceM, tolerance: 150, softTolerance: 250 },
        tempoNeed: "neutral",
        trackCondition: "neutral",
        daysBetweenRuns: { min: 7, max: 35 },
        classRule: "same_or_slightly_higher",
        summary: "Clean run and a similar setup are enough; the excuse was momentum rather than suitability.",
      };
    case "HELD_UP":
      return {
        barrier: { max: 8, softMax: 10 },
        distance: { target: sourceDistanceM, tolerance: 150, softTolerance: 250 },
        tempoNeed: "genuine",
        trackCondition: "neutral",
        daysBetweenRuns: { min: 7, max: 28 },
        classRule: "same_or_lower",
        summary: "Needs a cleaner inside-to-mid draw and a race shape that creates genuine tempo without the same traffic risk.",
      };
    case "THREE_WIDE_NO_COVER":
      return {
        barrier: { max: 7, softMax: 9 },
        distance: { target: sourceDistanceM, tolerance: 150, softTolerance: 300 },
        tempoNeed: "neutral",
        trackCondition: "good_to_soft",
        daysBetweenRuns: { min: 7, max: 28 },
        classRule: "same_or_lower",
        summary: "The key is map relief: a kinder draw and no repeat wide trip.",
      };
    case "BARRIER_DISASTER_ON_BIASED_DAY":
    case "TRACK_BIAS_VICTIM":
      return {
        barrier: { max: 6, softMax: 8 },
        distance: { target: sourceDistanceM, tolerance: 150, softTolerance: 250 },
        tempoNeed: "neutral",
        trackCondition: "good_to_soft",
        daysBetweenRuns: { min: 7, max: 35 },
        classRule: "same_or_lower",
        summary: "Needs the previous draw-and-bias combination removed more than anything else.",
      };
    case "TEMPO_VICTIM":
      return {
        barrier: { max: 10, softMax: 12 },
        distance: { target: sourceDistanceM, tolerance: 200, softTolerance: 400 },
        tempoNeed: "genuine_plus",
        trackCondition: "neutral",
        daysBetweenRuns: { min: 7, max: 28 },
        classRule: "same_or_lower",
        summary: "Needs pressure in front so the closing speed can actually bite.",
      };
    case "WET_TRACK_EXPOSURE":
      return {
        barrier: { max: 10, softMax: 12 },
        distance: { target: sourceDistanceM, tolerance: 200, softTolerance: 300 },
        tempoNeed: "neutral",
        trackCondition: "good",
        daysBetweenRuns: { min: 7, max: 35 },
        classRule: "same_or_lower",
        summary: "Dry ground is the unlock; everything else is secondary.",
      };
    case "CLASS_PREPARATION_RUN":
      return {
        barrier: { max: 10, softMax: 12 },
        distance: { target: sourceDistanceM, tolerance: 200, softTolerance: 400 },
        tempoNeed: "neutral",
        trackCondition: "neutral",
        daysBetweenRuns: { min: 10, max: 35 },
        classRule: "same_or_slightly_higher",
        summary: "The intent signal matters most; the next run needs to look like the target, not another tune-up.",
      };
    case "ELITE_CLOSING_SPLIT_NO_GAP":
    default:
      return {
        barrier: { max: 8, softMax: 10 },
        distance: { target: sourceDistanceM, tolerance: 150, softTolerance: 300 },
        tempoNeed: "genuine",
        trackCondition: "neutral",
        daysBetweenRuns: { min: 7, max: 28 },
        classRule: "same_or_lower",
        summary: "This is the pure Blackbook setup: same trip, clear air, and enough pressure for the late speed to matter.",
      };
  }
}

function determinePrimaryReason(
  result: ResultsRow,
  sectional: SectionalRow,
  raceStats: RaceFieldStats | undefined,
  evidence: CommentEvidence | undefined,
  bias: BiasSnapshot | undefined,
) {
  const tags = evidence?.incidentTags || [];
  const delta600 = raceStats?.avgLast600 !== null && raceStats?.avgLast600 !== undefined && sectional.last_600m_time !== null
    ? sectional.last_600m_time - raceStats.avgLast600
    : null;
  const delta400 = raceStats?.avgLast400 !== null && raceStats?.avgLast400 !== undefined && sectional.last_400m_time !== null
    ? sectional.last_400m_time - raceStats.avgLast400
    : null;
  const delta200 = raceStats?.avgLast200 !== null && raceStats?.avgLast200 !== undefined && sectional.last_200m_time !== null
    ? sectional.last_200m_time - raceStats.avgLast200
    : null;
  const winner600Gap = raceStats?.winnerLast600 !== null && raceStats?.winnerLast600 !== undefined && sectional.last_600m_time !== null
    ? sectional.last_600m_time - raceStats.winnerLast600
    : null;

  const strongClosing = delta600 !== null && delta600 <= -0.28;
  const eliteClosing = delta600 !== null && delta600 <= -0.45;
  const finishingBurstPositive = typeof sectional.finishing_burst === "number" && sectional.finishing_burst >= 102;
  const softTempoSignal = typeof sectional.rsi === "number"
    ? sectional.rsi >= 1.035
    : (typeof raceStats?.avgRsi === "number" ? raceStats.avgRsi >= 1.035 : false);
  const barrierRatio = result.barrier && result.field_size ? result.barrier / result.field_size : null;
  const barrierDisadvantaged = bias && barrierRatio !== null
    ? (
        (bias.biasLabel === "inside_advantage" && barrierRatio >= 0.72)
        || (bias.biasLabel === "outside_advantage" && barrierRatio <= 0.28)
      )
    : false;
  const barrierExtreme = bias && barrierRatio !== null
    ? (
        (bias.biasLabel === "inside_advantage" && barrierRatio >= 0.82)
        || (bias.biasLabel === "outside_advantage" && barrierRatio <= 0.18)
      )
    : false;

  if (tags.includes("CHECKED") && strongClosing) return "CHECKED" as ReasonCode;
  if ((tags.includes("HELD_UP") || tags.includes("BLOCKED_RUN")) && strongClosing) return "HELD_UP" as ReasonCode;
  if ((tags.includes("WIDE_NO_COVER") || tags.includes("THREE_WIDE_PLUS")) && (strongClosing || finishingBurstPositive)) {
    return "THREE_WIDE_NO_COVER" as ReasonCode;
  }
  if (barrierExtreme && strongClosing && result.position && result.position > 2) {
    return "BARRIER_DISASTER_ON_BIASED_DAY" as ReasonCode;
  }
  if (barrierDisadvantaged && strongClosing && result.position && result.position > 2) {
    return "TRACK_BIAS_VICTIM" as ReasonCode;
  }
  if (eliteClosing && winner600Gap !== null && winner600Gap <= -0.18 && result.position && result.position > 1 && (result.margin_lengths ?? 0) <= 3.0) {
    return "ELITE_CLOSING_SPLIT_NO_GAP" as ReasonCode;
  }
  if (softTempoSignal && strongClosing && result.position && result.position >= 4 && (result.margin_lengths ?? 0) <= 3.0) {
    return "TEMPO_VICTIM" as ReasonCode;
  }
  if (tags.includes("WET_TRACK_EXPOSURE") && broadGoingCategory(result.going) !== "good") {
    return "WET_TRACK_EXPOSURE" as ReasonCode;
  }
  if (tags.includes("CLASS_PREPARATION_RUN")) {
    return "CLASS_PREPARATION_RUN" as ReasonCode;
  }

  return null;
}

function buildEntryAnalysis(entry: Omit<HistoricalCandidate, "entryAnalysis" | "id" | "readinessBand" | "status" | "expiryReason">) {
  const delta = entry.sectionalDelta600m !== null ? Math.abs(entry.sectionalDelta600m).toFixed(2) : null;
  const stewardLine = entry.sourceComment?.trim();
  const priceSignal = entry.frankingScore !== null && entry.frankingScore >= 55
    ? "The franking layer is backing the run up as live form rather than noise."
    : "The form line still needs market respect to catch up with what the sectionals already showed.";
  const mechanism = stewardLine
    ? `${entry.horseName}'s latest run was ${entry.sourceTrack} R${entry.sourceRaceNumber}, where the stewards line reads: "${stewardLine}". Even through that, it still closed ${delta || "0.00"}s faster than the race average over the last 600m.`
    : entry.incidentSummary
      ? `${entry.horseName}'s latest run was ${entry.sourceTrack} R${entry.sourceRaceNumber}, and the run profile pointed to ${entry.incidentSummary.toLowerCase()}. It still closed ${delta || "0.00"}s faster than the race average over the last 600m.`
      : `${entry.horseName}'s latest run hid how well it actually finished, with a closing split ${delta || "0.00"}s faster than the race average over the last 600m.`;
  const summary = `${mechanism} ${priceSignal}`;

  return {
    moment: "entry_analysis",
    verdict: "WATCH",
    summary,
    mechanism,
    validation: priceSignal,
    conditionsNeeded: entry.idealConditions.summary,
  };
}

function detectExpiry(result: ResultsRow, byHorse: Map<string, ResultsRow[]>) {
  const horseKey = result.horse_id || normalizeName(result.horse_name);
  const runs = byHorse.get(horseKey) || [];
  const laterRuns = runs.filter((row) => row.race_date > result.race_date);
  if (laterRuns.length < 3) {
    return { status: "waiting" as EntryStatus, expiryReason: null };
  }

  const anyWin = laterRuns.slice(0, 3).some((row) => row.position === 1);
  if (anyWin) {
    return { status: "monitoring" as EntryStatus, expiryReason: null };
  }

  return {
    status: "expired" as EntryStatus,
    expiryReason: "The excuse has had three further runs to convert and still hasn’t landed.",
  };
}

function buildHistoricalEntries(results: ResultsRow[], sectionals: SectionalRow[], franking: FrankingRow[]) {
  const { byHorseKey, stats } = buildRaceStats(results, sectionals);
  const commentEvidence = buildCommentEvidenceIndex();
  const frankingMap = buildFrankingMap(franking);
  const biasSnapshots = computeTrackBias(results);
  const biasMap = new Map(biasSnapshots.map((snapshot) => [`${snapshot.raceDate}|${normalizeTrack(snapshot.track)}`, snapshot]));
  const resultsByHorse = buildResultsByHorse(results);
  const latestResults = latestResultPerHorse(results);
  const entries: HistoricalCandidate[] = [];

  for (const result of latestResults) {
    const raceKey = `${result.race_date}|${normalizeTrack(result.track)}|${result.race_number}`;
    const horseKey = `${raceKey}|${normalizeName(result.horse_name)}`;
    const sectional = byHorseKey.get(horseKey);

    if (!sectional || !result.race_number || !result.position || result.position <= 1) {
      continue;
    }

    const evidenceKey = `${result.race_date}|${normalizeTrack(result.track)}|${result.race_number}|${result.horse_id || normalizeName(result.horse_name)}`;
    const evidence = commentEvidence.get(evidenceKey);
    const raceStats = stats.get(raceKey);
    const bias = biasMap.get(`${result.race_date}|${normalizeTrack(result.track)}`);
    const reason = determinePrimaryReason(result, sectional, raceStats, evidence, bias);
    const delta600 = raceStats?.avgLast600 !== null && raceStats?.avgLast600 !== undefined && sectional.last_600m_time !== null
      ? sectional.last_600m_time - raceStats.avgLast600
      : null;
    const delta400 = raceStats?.avgLast400 !== null && raceStats?.avgLast400 !== undefined && sectional.last_400m_time !== null
      ? sectional.last_400m_time - raceStats.avgLast400
      : null;
    const delta200 = raceStats?.avgLast200 !== null && raceStats?.avgLast200 !== undefined && sectional.last_200m_time !== null
      ? sectional.last_200m_time - raceStats.avgLast200
      : null;

    if (!reason || delta600 === null || delta600 > -0.18) {
      continue;
    }

    const tags = [
      ...(evidence?.incidentTags || []),
      delta600 <= -0.45 ? "FASTEST_LAST600_PROFILE" : "ABOVE_AVG_LAST600",
      delta200 !== null && delta200 <= -0.08 ? "STRONG_LAST200" : "NEUTRAL_LAST200",
      bias?.biasLabel === "inside_advantage" ? "INSIDE_BIAS_DAY" : bias?.biasLabel === "outside_advantage" ? "OUTSIDE_BIAS_DAY" : "NEUTRAL_BIAS_DAY",
    ];

    const strongIncident = tags.some((tag) => ["CHECKED", "HELD_UP", "WIDE_NO_COVER", "THREE_WIDE_PLUS"].includes(tag));
    if (!strongIncident && delta600 > -0.28) {
      continue;
    }

    const idealConditions = buildIdealConditions(reason, result.distance_m);
    const frankingData = frankingMap.get(result.horse_id, result.horse_name);
    const intakeConfidence = clamp(
      0.62
      + (strongIncident ? 0.12 : 0)
      + (delta600 <= -0.45 ? 0.08 : 0.04)
      + ((frankingData?.franking_score || 0) >= 55 ? 0.04 : 0)
      + ((result.margin_lengths ?? 4) <= 1.8 ? 0.04 : 0),
      0.58,
      0.93,
    );
    const expiry = detectExpiry(result, resultsByHorse);

    const baseEntry = {
      canonicalHorseKey: result.horse_id || normalizeName(result.horse_name),
      horseId: result.horse_id,
      horseName: result.horse_name,
      sourceRaceId: result.race_id,
      sourceTrack: result.track,
      sourceRaceDate: result.race_date,
      sourceRaceNumber: result.race_number,
      sourceRaceName: result.race_name,
      sourceDistanceM: result.distance_m,
      sourceGoing: result.going,
      sourceRaceClass: result.race_class,
      sourceFieldSize: result.field_size,
      sourcePosition: result.position,
      sourceBarrier: result.barrier,
      sourceMarginLengths: result.margin_lengths,
      primaryReason: reason,
      secondaryEvidenceTags: Array.from(new Set(tags)),
      sourceComment: evidence?.comment || null,
      sourceIncidentsJson: (evidence?.incidentTags || []).map((tag) => ({ tag })),
      incidentSummary: evidence?.incidentSummary || reasonLabel(reason),
      last600mTime: sectional.last_600m_time,
      last400mTime: sectional.last_400m_time,
      last200mTime: sectional.last_200m_time,
      fieldAvgLast600m: raceStats?.avgLast600 ?? null,
      fieldAvgLast400m: raceStats?.avgLast400 ?? null,
      fieldAvgLast200m: raceStats?.avgLast200 ?? null,
      sectionalDelta600m: roundTo(delta600, 3),
      sectionalDelta400m: roundTo(delta400, 3),
      sectionalDelta200m: roundTo(delta200, 3),
      winnerLast600m: raceStats?.winnerLast600 ?? null,
      winnerLast400m: raceStats?.winnerLast400 ?? null,
      winnerLast200m: raceStats?.winnerLast200 ?? null,
      frankingScore: frankingData?.franking_score ?? null,
      frankingConfidence: frankingData?.franking_confidence ?? null,
      idealConditions,
      evidenceJson: {
        biasLabel: bias?.biasLabel || "neutral",
        biasConfidence: bias?.confidence || "low",
        sectionalSource: sectional.source,
        finishingBurst: sectional.finishing_burst,
        rsi: sectional.rsi,
      },
      intakeConfidence: roundTo(intakeConfidence, 3) || 0.65,
    };

    const entryAnalysis = buildEntryAnalysis(baseEntry);
    const id = stableId("blackbook-entry", baseEntry.canonicalHorseKey, baseEntry.sourceRaceDate, normalizeTrack(baseEntry.sourceTrack), baseEntry.sourceRaceNumber);
    entries.push({
      id,
      ...baseEntry,
      entryAnalysis,
      readinessBand: expiry.status === "expired" ? "WAITING" : "MONITORING",
      status: expiry.status,
      expiryReason: expiry.expiryReason,
    });
  }

  entries.sort((a, b) => {
    const deltaA = a.sectionalDelta600m ?? 0;
    const deltaB = b.sectionalDelta600m ?? 0;
    return deltaA - deltaB;
  });

  return { entries, biasSnapshots };
}

interface TipsRaceContext {
  track: string;
  raceDate: string;
  raceNumber: number;
  raceName: string | null;
  distanceM: number | null;
  going: string | null;
  raceClass: string | null;
  fieldSize: number | null;
  offTime: string | null;
  leaderCount: number;
  tempoSummary: string;
  runners: Array<{
    horseName: string;
    modelWinProb: number | null;
    marketPrice: number | null;
    runningStyle: string | null;
  }>;
}

function inferTempoSummary(runners: TipsRaceContext["runners"]) {
  const leaderCount = runners.filter((runner) => {
    const style = (runner.runningStyle || "").toLowerCase();
    return style.includes("leader") || style.includes("onpace") || style.includes("on_pace");
  }).length;

  if (leaderCount >= 4) return { leaderCount, tempoSummary: "hot" };
  if (leaderCount >= 2) return { leaderCount, tempoSummary: "genuine" };
  if (leaderCount === 1) return { leaderCount, tempoSummary: "soft" };
  return { leaderCount, tempoSummary: "unknown" };
}

function loadUpcomingContexts() {
  const today = todayInSydney();
  const lastDate = formatDateOnly(addDays(today, UPCOMING_LOOKAHEAD_DAYS));
  const racecardFiles = fs.existsSync(racecardsDir)
    ? fs.readdirSync(racecardsDir).filter((file) => file.startsWith("racecard_") && file.endsWith(".json"))
    : [];
  const tipsFiles = fs.existsSync(racecardsDir)
    ? fs.readdirSync(racecardsDir).filter((file) => file.startsWith("tips_") && file.endsWith(".json"))
    : [];

  const tipsRaceMap = new Map<string, TipsRaceContext>();
  for (const file of tipsFiles) {
    const match = file.match(/tips_(\d{4}-\d{2}-\d{2})/);
    const fileDate = match?.[1];
    if (!fileDate || fileDate < formatDateOnly(today) || fileDate > lastDate) {
      continue;
    }

    const payload = loadJsonFile(path.join(racecardsDir, file));
    if (!payload || Array.isArray(payload) || !Array.isArray(payload.races)) {
      continue;
    }

    for (const race of payload.races || []) {
      const runners = (race.full_field || []).map((runner: any) => ({
        horseName: runner.horse,
        modelWinProb: parseWinProbPct(runner.win_pct),
        marketPrice: parsePrice(runner.odds),
        runningStyle: runner.running_style || null,
      }));
      const { leaderCount, tempoSummary } = inferTempoSummary(runners);
      const key = `${fileDate}|${normalizeTrack(race.track)}|${race.race_number}`;
      tipsRaceMap.set(key, {
        track: race.track,
        raceDate: fileDate,
        raceNumber: Number(race.race_number),
        raceName: race.race_name || null,
        distanceM: parseDistance(race.distance),
        going: race.going || null,
        raceClass: race.race_class || null,
        fieldSize: race.field_size || runners.length || null,
        offTime: null,
        leaderCount,
        tempoSummary,
        runners,
      });
    }
  }

  const nominations = new Map<string, UpcomingNomination[]>();

  for (const file of racecardFiles) {
    const match = file.match(/racecard_(\d{4}-\d{2}-\d{2})/);
    const fileDate = match?.[1];
    if (!fileDate || fileDate < formatDateOnly(today) || fileDate > lastDate) {
      continue;
    }

    const payload = loadJsonFile(path.join(racecardsDir, file));
    if (!Array.isArray(payload)) {
      continue;
    }

    for (const meet of payload) {
      for (const race of meet.races || []) {
        const raceNumber = Number(race.race_number);
        const tipsContext = Array.from(tipsRaceMap.values()).find((item) => (
          item.raceDate === fileDate
          && item.raceNumber === raceNumber
          && tracksRoughlyMatch(item.track, race.course || meet.course)
        ));

        for (const runner of race.runners || []) {
          if (runner.scratched) {
            continue;
          }

          const tipsRunner = tipsContext?.runners.find((item) => normalizeName(item.horseName) === normalizeName(runner.horse));
          const modelWinProb = tipsRunner?.modelWinProb ?? null;
          const marketPrice = tipsRunner?.marketPrice ?? parsePrice(Array.isArray(runner.odds) ? runner.odds[0] : runner.sp);
          const marketImpliedProb = marketPrice && marketPrice > 1 ? roundTo(100 / marketPrice, 2) : null;
          const truePrice = modelWinProb && modelWinProb > 0 ? roundTo(100 / modelWinProb, 2) : null;
          const valueEdgePct = modelWinProb !== null && marketImpliedProb !== null ? roundTo(modelWinProb - marketImpliedProb, 2) : null;

          const record = {
            horseId: runner.horse_id || null,
            horseName: runner.horse,
            canonicalHorseKey: runner.horse_id || normalizeName(runner.horse),
            track: race.course || meet.course,
            raceDate: race.date || meet.date,
            raceNumber,
            raceName: race.race_name || null,
            offTime: race.off_time || null,
            distanceM: parseDistance(race.distance),
            going: race.going || null,
            raceClass: race.class || null,
            fieldSize: Array.isArray(race.runners) ? race.runners.filter((item: any) => !item.scratched).length : null,
            barrier: parseDistance(runner.draw),
            marketPrice,
            modelWinProb,
            truePrice,
            marketImpliedProb,
            valueEdgePct,
            tempoSummary: tipsContext?.tempoSummary || "unknown",
            tempoLeaderCount: tipsContext?.leaderCount || 0,
          };

          const key = record.horseId || normalizeName(record.horseName);
          nominations.set(key, [...(nominations.get(key) || []), record]);
        }
      }
    }
  }

  for (const records of nominations.values()) {
    records.sort((a, b) => {
      const left = a.offTime || `${a.raceDate}T00:00:00Z`;
      const right = b.offTime || `${b.raceDate}T00:00:00Z`;
      return left.localeCompare(right);
    });
  }

  return nominations;
}

function scoreTrackCondition(ideal: IdealConditions, nextGoing: string | null) {
  if (!nextGoing) return 0;
  const category = broadGoingCategory(nextGoing);
  if (ideal.trackCondition === "neutral") return 2;
  if (ideal.trackCondition === "good" && category === "good") return 2;
  if (ideal.trackCondition === "good_to_soft" && (category === "good" || category === "soft")) return 2;
  if (ideal.trackCondition === "good" && category === "soft") return 1;
  return 0;
}

function scoreTempo(ideal: IdealConditions, tempoSummary: string, leaderCount: number) {
  if (ideal.tempoNeed === "neutral") return 2;
  if (ideal.tempoNeed === "genuine_plus") {
    if (tempoSummary === "hot") return 2;
    if (tempoSummary === "genuine" || leaderCount >= 2) return 2;
    return leaderCount === 1 ? 1 : 0;
  }
  if (ideal.tempoNeed === "genuine") {
    if (tempoSummary === "genuine" || tempoSummary === "hot") return 2;
    return leaderCount === 1 ? 1 : 0;
  }
  if (ideal.tempoNeed === "soft") {
    if (tempoSummary === "soft") return 2;
    return tempoSummary === "genuine" ? 1 : 0;
  }
  return 0;
}

function scoreClass(ideal: IdealConditions, sourceClass: string | null, nextClass: string | null) {
  if (ideal.classRule === "neutral") return 1;
  const sourceRank = classRank(sourceClass);
  const nextRank = classRank(nextClass);
  if (ideal.classRule === "same_or_lower") {
    return nextRank <= sourceRank ? 1 : 0;
  }
  return nextRank <= sourceRank + 1 ? 1 : 0;
}

function buildConditionAlignment(entry: HistoricalCandidate, run: Omit<UpcomingRunContext, "id" | "blackbookEntryId" | "canonicalHorseKey" | "horseName" | "readinessScore" | "readinessBand" | "verdict" | "status" | "breakdownJson" | "conditionAlignmentJson" | "raceDayBriefJson">, breakdown: Record<string, number>) {
  return {
    moment: "condition_alignment",
    summary: `${entry.horseName} moves from ${entry.sourceTrack} R${entry.sourceRaceNumber} into ${run.track} R${run.raceNumber} with ${run.barrier ? `barrier ${run.barrier}` : "no final draw yet"} and a ${run.tempoSummary} tempo map.`,
    delta: `Barrier ${breakdown.barrier}/2, tempo ${breakdown.tempo}/2, and distance ${breakdown.distance}/2 are doing the heavy lifting in the readiness score.`,
    verdict: breakdown.total >= 9 ? "BACK" : breakdown.total >= 7 ? "WATCH" : "NEEDS SCENARIO",
  };
}

function buildRaceDayBrief(entry: HistoricalCandidate, run: Omit<UpcomingRunContext, "id" | "blackbookEntryId" | "canonicalHorseKey" | "horseName" | "readinessScore" | "readinessBand" | "verdict" | "status" | "breakdownJson" | "conditionAlignmentJson" | "raceDayBriefJson">, breakdown: Record<string, number>) {
  const verdict = breakdown.total >= 9 ? "BACK" : breakdown.total >= 7 ? "WATCH" : "NEEDS SCENARIO";
  const risk = run.tempoSummary === "soft"
    ? "The main danger is the race backing up into a soft lead and turning the late split into empty ground."
    : run.barrier !== null && run.barrier > entry.idealConditions.barrier.softMax
      ? "The draw can still recreate the same traffic or map problem if the rider has to concede ground early."
      : "The risk is simply the market correcting fast enough that the value edge disappears before jump.";

  return {
    moment: "race_day_brief",
    verdict,
    winCondition: `${entry.horseName} wins if the race is run at ${run.tempoSummary} pressure, the draw lets it avoid the original ${reasonLabel(entry.primaryReason).toLowerCase()} mechanism, and the late-speed profile holds.`,
    primaryRisk: risk,
  };
}

function buildRunForEntry(entry: HistoricalCandidate, nomination: UpcomingNomination): UpcomingRunContext {
  const track = scoreTrackCondition(entry.idealConditions, nomination.going);
  const barrier = nomination.barrier !== null
    ? nomination.barrier <= entry.idealConditions.barrier.max
      ? 2
      : nomination.barrier <= entry.idealConditions.barrier.softMax
        ? 1
        : 0
    : 0;
  const distanceGap = entry.sourceDistanceM !== null && nomination.distanceM !== null
    ? Math.abs(entry.sourceDistanceM - nomination.distanceM)
    : null;
  const distance = distanceGap === null
    ? 0
    : distanceGap <= entry.idealConditions.distance.tolerance
      ? 2
      : distanceGap <= entry.idealConditions.distance.softTolerance
        ? 1
        : 0;
  const tempo = scoreTempo(entry.idealConditions, nomination.tempoSummary, nomination.tempoLeaderCount);
  const days = nomination.raceDate && entry.sourceRaceDate
    ? Math.round((parseDateOnly(nomination.raceDate).getTime() - parseDateOnly(entry.sourceRaceDate).getTime()) / 86400000)
    : null;
  const daysScore = days !== null && days >= entry.idealConditions.daysBetweenRuns.min && days <= entry.idealConditions.daysBetweenRuns.max ? 1 : 0;
  const classScore = scoreClass(entry.idealConditions, entry.sourceRaceClass, nomination.raceClass);
  const readinessScore = track + barrier + distance + tempo + daysScore + classScore;
  const valueDetected = nomination.valueEdgePct !== null && nomination.valueEdgePct >= 3;
  const verdict = readinessScore >= 9 && valueDetected
    ? "BACK"
    : readinessScore >= 7
      ? "WATCH"
      : "NEEDS SCENARIO";

  const breakdown = {
    track,
    barrier,
    distance,
    tempo,
    days: daysScore,
    class: classScore,
    total: readinessScore,
  };

  const baseRun = {
    track: nomination.track,
    raceDate: nomination.raceDate,
    raceNumber: nomination.raceNumber,
    raceName: nomination.raceName,
    offTime: nomination.offTime,
    distanceM: nomination.distanceM,
    going: nomination.going,
    raceClass: nomination.raceClass,
    fieldSize: nomination.fieldSize,
    barrier: nomination.barrier,
    daysSinceSourceRun: days,
    tempoSummary: nomination.tempoSummary,
    tempoLeaderCount: nomination.tempoLeaderCount,
    marketPrice: nomination.marketPrice,
    truePrice: nomination.truePrice,
    marketImpliedProb: nomination.marketImpliedProb,
    modelWinProb: nomination.modelWinProb,
    valueEdgePct: nomination.valueEdgePct,
  };

  return {
    id: stableId("blackbook-run", entry.id, nomination.raceDate, normalizeTrack(nomination.track), nomination.raceNumber),
    blackbookEntryId: entry.id,
    canonicalHorseKey: entry.canonicalHorseKey,
    horseName: entry.horseName,
    ...baseRun,
    readinessScore,
    readinessBand: readinessBand(readinessScore),
    verdict,
    status: nomination.raceDate === formatDateOnly(todayInSydney()) ? "raceday" : "nominated",
    breakdownJson: breakdown,
    conditionAlignmentJson: buildConditionAlignment(entry, baseRun, breakdown),
    raceDayBriefJson: buildRaceDayBrief(entry, baseRun, breakdown),
  };
}

function buildRuns(entries: HistoricalCandidate[]) {
  const nominations = loadUpcomingContexts();
  const runs: UpcomingRunContext[] = [];

  for (const entry of entries) {
    if (entry.status === "expired") {
      continue;
    }

    const possibleRuns = nominations.get(entry.horseId || entry.canonicalHorseKey) || nominations.get(normalizeName(entry.horseName)) || [];
    const nomination = possibleRuns.find((run) => run.raceDate >= entry.sourceRaceDate);
    if (!nomination) {
      continue;
    }

    const run = buildRunForEntry(entry, nomination);
    runs.push(run);
    entry.readinessBand = run.readinessBand;
    entry.status = run.readinessScore >= 7 ? "active" : "monitoring";
  }

  return runs;
}

function buildAlerts(entries: HistoricalCandidate[], runs: UpcomingRunContext[]) {
  const alerts: AlertRecord[] = [];
  const runsByEntry = new Map(runs.map((run) => [run.blackbookEntryId, run]));
  const today = formatDateOnly(todayInSydney());

  for (const entry of entries) {
    const run = runsByEntry.get(entry.id);
    if (!run) {
      if (entry.status === "expired" && entry.expiryReason) {
        const eventKey = stableId("blackbook-alert", entry.id, "expiry");
        alerts.push({
          id: eventKey,
          eventKey,
          blackbookEntryId: entry.id,
          blackbookEntryRunId: null,
          horseName: entry.horseName,
          track: null,
          raceDate: null,
          raceNumber: null,
          alertType: "expiry_warning",
          severity: "warning",
          title: `${entry.horseName} excuse looks stale`,
          message: entry.expiryReason,
          readinessScore: null,
        });
      }
      continue;
    }

    const nominationKey = stableId("blackbook-alert", entry.id, run.id, "nomination");
    alerts.push({
      id: nominationKey,
      eventKey: nominationKey,
      blackbookEntryId: entry.id,
      blackbookEntryRunId: run.id,
      horseName: entry.horseName,
      track: run.track,
      raceDate: run.raceDate,
      raceNumber: run.raceNumber,
      alertType: "nomination",
      severity: run.readinessScore >= 7 ? "positive" : "info",
      title: `${entry.horseName} has landed a race`,
      message: `${entry.horseName} is nominated at ${run.track} R${run.raceNumber} with a ${run.readinessBand.toLowerCase()} setup.`,
      readinessScore: run.readinessScore,
    });

    if (run.barrier !== null && run.barrier <= entry.idealConditions.barrier.max) {
      const barrierKey = stableId("blackbook-alert", entry.id, run.id, "barrier");
      alerts.push({
        id: barrierKey,
        eventKey: barrierKey,
        blackbookEntryId: entry.id,
        blackbookEntryRunId: run.id,
        horseName: entry.horseName,
        track: run.track,
        raceDate: run.raceDate,
        raceNumber: run.raceNumber,
        alertType: "barrier",
        severity: "positive",
        title: `${entry.horseName} has drawn to unwind the excuse`,
        message: `${entry.horseName} has drawn barrier ${run.barrier}, which fits the Blackbook trigger for ${reasonLabel(entry.primaryReason).toLowerCase()}.`,
        readinessScore: run.readinessScore,
      });
    }

    if (run.valueEdgePct !== null && run.valueEdgePct >= 3) {
      const marketKey = stableId("blackbook-alert", entry.id, run.id, "market");
      alerts.push({
        id: marketKey,
        eventKey: marketKey,
        blackbookEntryId: entry.id,
        blackbookEntryRunId: run.id,
        horseName: entry.horseName,
        track: run.track,
        raceDate: run.raceDate,
        raceNumber: run.raceNumber,
        alertType: "market",
        severity: "positive",
        title: `${entry.horseName} is still a price`,
        message: `${entry.horseName} is priced at ${run.marketPrice ? `$${run.marketPrice.toFixed(2)}` : "market pending"} against a STRIDE true price of ${run.truePrice ? `$${run.truePrice.toFixed(2)}` : "N/A"}.`,
        readinessScore: run.readinessScore,
      });
    }

    if (run.tempoSummary === "genuine" || run.tempoSummary === "hot") {
      const tempoKey = stableId("blackbook-alert", entry.id, run.id, "tempo");
      alerts.push({
        id: tempoKey,
        eventKey: tempoKey,
        blackbookEntryId: entry.id,
        blackbookEntryRunId: run.id,
        horseName: entry.horseName,
        track: run.track,
        raceDate: run.raceDate,
        raceNumber: run.raceNumber,
        alertType: "tempo",
        severity: "positive",
        title: `${entry.horseName} gets the race shape it needs`,
        message: `${entry.horseName} maps into a ${run.tempoSummary} tempo with ${run.tempoLeaderCount} likely pace influences. That is a live unwind for this profile.`,
        readinessScore: run.readinessScore,
      });
    }

    if (run.raceDate === today) {
      const raceDayKey = stableId("blackbook-alert", entry.id, run.id, "race_day");
      alerts.push({
        id: raceDayKey,
        eventKey: raceDayKey,
        blackbookEntryId: entry.id,
        blackbookEntryRunId: run.id,
        horseName: entry.horseName,
        track: run.track,
        raceDate: run.raceDate,
        raceNumber: run.raceNumber,
        alertType: "race_day_brief",
        severity: run.verdict === "BACK" ? "positive" : "info",
        title: `${entry.horseName} race-day brief`,
        message: run.raceDayBriefJson.winCondition as string,
        readinessScore: run.readinessScore,
      });
    }
  }

  return alerts;
}

async function persistBiasSnapshots(snapshots: BiasSnapshot[]) {
  for (const snapshot of snapshots) {
    await db.execute(sql`
      INSERT INTO track_day_bias (
        id, track, race_date, bias_label, confidence, races_sampled,
        inside_win_rate, middle_win_rate, outside_win_rate, avg_winner_barrier_ratio, stats_json, updated_at
      ) VALUES (
        ${snapshot.id}, ${snapshot.track}, ${snapshot.raceDate}, ${snapshot.biasLabel}, ${snapshot.confidence}, ${snapshot.racesSampled},
        ${snapshot.insideWinRate}, ${snapshot.middleWinRate}, ${snapshot.outsideWinRate}, ${snapshot.avgWinnerBarrierRatio}, ${JSON.stringify(snapshot.statsJson)}::jsonb, now()
      )
      ON CONFLICT (id) DO UPDATE SET
        bias_label = EXCLUDED.bias_label,
        confidence = EXCLUDED.confidence,
        races_sampled = EXCLUDED.races_sampled,
        inside_win_rate = EXCLUDED.inside_win_rate,
        middle_win_rate = EXCLUDED.middle_win_rate,
        outside_win_rate = EXCLUDED.outside_win_rate,
        avg_winner_barrier_ratio = EXCLUDED.avg_winner_barrier_ratio,
        stats_json = EXCLUDED.stats_json,
        updated_at = now()
    `);
  }
}

async function persistEntries(entries: HistoricalCandidate[]) {
  for (const entry of entries) {
    await db.execute(sql`
      INSERT INTO blackbook_entries (
        id, canonical_horse_key, horse_id, horse_name, source_race_id, source_track, source_race_date,
        source_race_number, source_race_name, source_distance_m, source_going, source_race_class, source_field_size,
        source_position, source_barrier, source_margin_lengths, primary_reason, secondary_evidence_tags, source_comment,
        source_incidents_json, incident_summary, last_600m_time, last_400m_time, last_200m_time, field_avg_last_600m,
        field_avg_last_400m, field_avg_last_200m, sectional_delta_600m, sectional_delta_400m, sectional_delta_200m,
        winner_last_600m, winner_last_400m, winner_last_200m, franking_score, franking_confidence,
        ideal_conditions_json, evidence_json, entry_analysis_json, intake_confidence, readiness_band, status, expiry_reason, updated_at
      ) VALUES (
        ${entry.id}, ${entry.canonicalHorseKey}, ${entry.horseId}, ${entry.horseName}, ${entry.sourceRaceId}, ${entry.sourceTrack}, ${entry.sourceRaceDate},
        ${entry.sourceRaceNumber}, ${entry.sourceRaceName}, ${entry.sourceDistanceM}, ${entry.sourceGoing}, ${entry.sourceRaceClass}, ${entry.sourceFieldSize},
        ${entry.sourcePosition}, ${entry.sourceBarrier}, ${entry.sourceMarginLengths}, ${entry.primaryReason}, ${JSON.stringify(entry.secondaryEvidenceTags)}::jsonb, ${entry.sourceComment},
        ${JSON.stringify(entry.sourceIncidentsJson)}::jsonb, ${entry.incidentSummary}, ${entry.last600mTime}, ${entry.last400mTime}, ${entry.last200mTime}, ${entry.fieldAvgLast600m},
        ${entry.fieldAvgLast400m}, ${entry.fieldAvgLast200m}, ${entry.sectionalDelta600m}, ${entry.sectionalDelta400m}, ${entry.sectionalDelta200m},
        ${entry.winnerLast600m}, ${entry.winnerLast400m}, ${entry.winnerLast200m}, ${entry.frankingScore}, ${entry.frankingConfidence},
        ${JSON.stringify(entry.idealConditions)}::jsonb, ${JSON.stringify(entry.evidenceJson)}::jsonb, ${JSON.stringify(entry.entryAnalysis)}::jsonb,
        ${entry.intakeConfidence}, ${entry.readinessBand}, ${entry.status}, ${entry.expiryReason}, now()
      )
      ON CONFLICT (id) DO UPDATE SET
        canonical_horse_key = EXCLUDED.canonical_horse_key,
        horse_id = EXCLUDED.horse_id,
        horse_name = EXCLUDED.horse_name,
        source_track = EXCLUDED.source_track,
        source_race_date = EXCLUDED.source_race_date,
        source_race_number = EXCLUDED.source_race_number,
        source_race_name = EXCLUDED.source_race_name,
        source_distance_m = EXCLUDED.source_distance_m,
        source_going = EXCLUDED.source_going,
        source_race_class = EXCLUDED.source_race_class,
        source_field_size = EXCLUDED.source_field_size,
        source_position = EXCLUDED.source_position,
        source_barrier = EXCLUDED.source_barrier,
        source_margin_lengths = EXCLUDED.source_margin_lengths,
        primary_reason = EXCLUDED.primary_reason,
        secondary_evidence_tags = EXCLUDED.secondary_evidence_tags,
        source_comment = EXCLUDED.source_comment,
        source_incidents_json = EXCLUDED.source_incidents_json,
        incident_summary = EXCLUDED.incident_summary,
        last_600m_time = EXCLUDED.last_600m_time,
        last_400m_time = EXCLUDED.last_400m_time,
        last_200m_time = EXCLUDED.last_200m_time,
        field_avg_last_600m = EXCLUDED.field_avg_last_600m,
        field_avg_last_400m = EXCLUDED.field_avg_last_400m,
        field_avg_last_200m = EXCLUDED.field_avg_last_200m,
        sectional_delta_600m = EXCLUDED.sectional_delta_600m,
        sectional_delta_400m = EXCLUDED.sectional_delta_400m,
        sectional_delta_200m = EXCLUDED.sectional_delta_200m,
        winner_last_600m = EXCLUDED.winner_last_600m,
        winner_last_400m = EXCLUDED.winner_last_400m,
        winner_last_200m = EXCLUDED.winner_last_200m,
        franking_score = EXCLUDED.franking_score,
        franking_confidence = EXCLUDED.franking_confidence,
        ideal_conditions_json = EXCLUDED.ideal_conditions_json,
        evidence_json = EXCLUDED.evidence_json,
        entry_analysis_json = EXCLUDED.entry_analysis_json,
        intake_confidence = EXCLUDED.intake_confidence,
        readiness_band = EXCLUDED.readiness_band,
        status = EXCLUDED.status,
        expiry_reason = EXCLUDED.expiry_reason,
        updated_at = now()
    `);
  }
}

async function persistRuns(runs: UpcomingRunContext[]) {
  for (const run of runs) {
    await db.execute(sql`
      INSERT INTO blackbook_entry_runs (
        id, blackbook_entry_id, canonical_horse_key, horse_name, track, race_date, race_number, race_name,
        off_time, distance_m, going, race_class, field_size, barrier, days_since_source_run, tempo_summary,
        tempo_leader_count, market_price, true_price, market_implied_prob, model_win_prob, value_edge_pct,
        readiness_score, readiness_band, verdict, status, breakdown_json, condition_alignment_json, race_day_brief_json, updated_at
      ) VALUES (
        ${run.id}, ${run.blackbookEntryId}, ${run.canonicalHorseKey}, ${run.horseName}, ${run.track}, ${run.raceDate}, ${run.raceNumber}, ${run.raceName},
        ${run.offTime}, ${run.distanceM}, ${run.going}, ${run.raceClass}, ${run.fieldSize}, ${run.barrier}, ${run.daysSinceSourceRun}, ${run.tempoSummary},
        ${run.tempoLeaderCount}, ${run.marketPrice}, ${run.truePrice}, ${run.marketImpliedProb}, ${run.modelWinProb}, ${run.valueEdgePct},
        ${run.readinessScore}, ${run.readinessBand}, ${run.verdict}, ${run.status}, ${JSON.stringify(run.breakdownJson)}::jsonb,
        ${JSON.stringify(run.conditionAlignmentJson)}::jsonb, ${JSON.stringify(run.raceDayBriefJson)}::jsonb, now()
      )
      ON CONFLICT (id) DO UPDATE SET
        market_price = EXCLUDED.market_price,
        true_price = EXCLUDED.true_price,
        market_implied_prob = EXCLUDED.market_implied_prob,
        model_win_prob = EXCLUDED.model_win_prob,
        value_edge_pct = EXCLUDED.value_edge_pct,
        readiness_score = EXCLUDED.readiness_score,
        readiness_band = EXCLUDED.readiness_band,
        verdict = EXCLUDED.verdict,
        status = EXCLUDED.status,
        barrier = EXCLUDED.barrier,
        field_size = EXCLUDED.field_size,
        tempo_summary = EXCLUDED.tempo_summary,
        tempo_leader_count = EXCLUDED.tempo_leader_count,
        days_since_source_run = EXCLUDED.days_since_source_run,
        breakdown_json = EXCLUDED.breakdown_json,
        condition_alignment_json = EXCLUDED.condition_alignment_json,
        race_day_brief_json = EXCLUDED.race_day_brief_json,
        updated_at = now()
    `);
  }
}

async function persistAlerts(alerts: AlertRecord[]) {
  for (const alert of alerts) {
    await db.execute(sql`
      INSERT INTO blackbook_alerts (
        id, event_key, blackbook_entry_id, blackbook_entry_run_id, horse_name, track, race_date, race_number,
        alert_type, severity, title, message, readiness_score, updated_at
      ) VALUES (
        ${alert.id}, ${alert.eventKey}, ${alert.blackbookEntryId}, ${alert.blackbookEntryRunId}, ${alert.horseName},
        ${alert.track}, ${alert.raceDate}, ${alert.raceNumber}, ${alert.alertType}, ${alert.severity}, ${alert.title},
        ${alert.message}, ${alert.readinessScore}, now()
      )
      ON CONFLICT (event_key) DO UPDATE SET
        severity = EXCLUDED.severity,
        title = EXCLUDED.title,
        message = EXCLUDED.message,
        readiness_score = EXCLUDED.readiness_score,
        updated_at = now()
    `);
  }
}

function chunkItems<T>(items: T[], size = 25) {
  const chunks: T[][] = [];
  for (let index = 0; index < items.length; index += size) {
    chunks.push(items.slice(index, index + size));
  }
  return chunks;
}

async function replaceBlackbookData(entries: HistoricalCandidate[], runs: UpcomingRunContext[], alerts: AlertRecord[], biasSnapshots: BiasSnapshot[]) {
  await db.execute(sql`DELETE FROM blackbook_alerts`);
  await db.execute(sql`DELETE FROM blackbook_entry_runs`);
  await db.execute(sql`DELETE FROM blackbook_entries`);
  await db.execute(sql`DELETE FROM track_day_bias`);

  for (const batch of chunkItems(biasSnapshots)) {
    if (!batch.length) continue;
    const values = sql.join(batch.map((snapshot) => sql`(
      ${snapshot.id}, ${snapshot.track}, ${snapshot.raceDate}, ${snapshot.biasLabel}, ${snapshot.confidence}, ${snapshot.racesSampled},
      ${snapshot.insideWinRate}, ${snapshot.middleWinRate}, ${snapshot.outsideWinRate}, ${snapshot.avgWinnerBarrierRatio}, ${JSON.stringify(snapshot.statsJson)}::jsonb, now(), now()
    )`), sql`, `);
    await db.execute(sql`
      INSERT INTO track_day_bias (
        id, track, race_date, bias_label, confidence, races_sampled,
        inside_win_rate, middle_win_rate, outside_win_rate, avg_winner_barrier_ratio, stats_json, created_at, updated_at
      ) VALUES ${values}
    `);
  }

  for (const batch of chunkItems(entries)) {
    if (!batch.length) continue;
    const values = sql.join(batch.map((entry) => sql`(
      ${entry.id}, ${entry.canonicalHorseKey}, ${entry.horseId}, ${entry.horseName}, ${entry.sourceRaceId}, ${entry.sourceTrack}, ${entry.sourceRaceDate},
      ${entry.sourceRaceNumber}, ${entry.sourceRaceName}, ${entry.sourceDistanceM}, ${entry.sourceGoing}, ${entry.sourceRaceClass}, ${entry.sourceFieldSize},
      ${entry.sourcePosition}, ${entry.sourceBarrier}, ${entry.sourceMarginLengths}, ${entry.primaryReason}, ${JSON.stringify(entry.secondaryEvidenceTags)}::jsonb,
      ${entry.sourceComment}, ${JSON.stringify(entry.sourceIncidentsJson)}::jsonb, ${entry.incidentSummary}, ${entry.last600mTime}, ${entry.last400mTime},
      ${entry.last200mTime}, ${entry.fieldAvgLast600m}, ${entry.fieldAvgLast400m}, ${entry.fieldAvgLast200m}, ${entry.sectionalDelta600m}, ${entry.sectionalDelta400m},
      ${entry.sectionalDelta200m}, ${entry.winnerLast600m}, ${entry.winnerLast400m}, ${entry.winnerLast200m}, ${entry.frankingScore}, ${entry.frankingConfidence},
      ${JSON.stringify(entry.idealConditions)}::jsonb, ${JSON.stringify(entry.evidenceJson)}::jsonb, ${JSON.stringify(entry.entryAnalysis)}::jsonb,
      ${entry.intakeConfidence}, ${entry.readinessBand}, ${entry.status}, ${entry.expiryReason}, now(), now()
    )`), sql`, `);
    await db.execute(sql`
      INSERT INTO blackbook_entries (
        id, canonical_horse_key, horse_id, horse_name, source_race_id, source_track, source_race_date,
        source_race_number, source_race_name, source_distance_m, source_going, source_race_class, source_field_size,
        source_position, source_barrier, source_margin_lengths, primary_reason, secondary_evidence_tags, source_comment,
        source_incidents_json, incident_summary, last_600m_time, last_400m_time, last_200m_time, field_avg_last_600m,
        field_avg_last_400m, field_avg_last_200m, sectional_delta_600m, sectional_delta_400m, sectional_delta_200m,
        winner_last_600m, winner_last_400m, winner_last_200m, franking_score, franking_confidence,
        ideal_conditions_json, evidence_json, entry_analysis_json, intake_confidence, readiness_band, status, expiry_reason, created_at, updated_at
      ) VALUES ${values}
    `);
  }

  for (const batch of chunkItems(runs)) {
    if (!batch.length) continue;
    const values = sql.join(batch.map((run) => sql`(
      ${run.id}, ${run.blackbookEntryId}, ${run.canonicalHorseKey}, ${run.horseName}, ${run.track}, ${run.raceDate}, ${run.raceNumber}, ${run.raceName},
      ${run.offTime}, ${run.distanceM}, ${run.going}, ${run.raceClass}, ${run.fieldSize}, ${run.barrier}, ${run.daysSinceSourceRun}, ${run.tempoSummary},
      ${run.tempoLeaderCount}, ${run.marketPrice}, ${run.truePrice}, ${run.marketImpliedProb}, ${run.modelWinProb}, ${run.valueEdgePct},
      ${run.readinessScore}, ${run.readinessBand}, ${run.verdict}, ${run.status}, ${JSON.stringify(run.breakdownJson)}::jsonb,
      ${JSON.stringify(run.conditionAlignmentJson)}::jsonb, ${JSON.stringify(run.raceDayBriefJson)}::jsonb, now(), now()
    )`), sql`, `);
    await db.execute(sql`
      INSERT INTO blackbook_entry_runs (
        id, blackbook_entry_id, canonical_horse_key, horse_name, track, race_date, race_number, race_name,
        off_time, distance_m, going, race_class, field_size, barrier, days_since_source_run, tempo_summary,
        tempo_leader_count, market_price, true_price, market_implied_prob, model_win_prob, value_edge_pct,
        readiness_score, readiness_band, verdict, status, breakdown_json, condition_alignment_json, race_day_brief_json, created_at, updated_at
      ) VALUES ${values}
    `);
  }

  for (const batch of chunkItems(alerts)) {
    if (!batch.length) continue;
    const values = sql.join(batch.map((alert) => sql`(
      ${alert.id}, ${alert.eventKey}, ${alert.blackbookEntryId}, ${alert.blackbookEntryRunId}, ${alert.horseName}, ${alert.track}, ${alert.raceDate},
      ${alert.raceNumber}, ${alert.alertType}, ${alert.severity}, ${alert.title}, ${alert.message}, ${alert.readinessScore}, now(), now()
    )`), sql`, `);
    await db.execute(sql`
      INSERT INTO blackbook_alerts (
        id, event_key, blackbook_entry_id, blackbook_entry_run_id, horse_name, track, race_date, race_number,
        alert_type, severity, title, message, readiness_score, created_at, updated_at
      ) VALUES ${values}
    `);
  }
}

async function syncBlackbookData(force = false): Promise<BlackbookSyncState> {
  const now = Date.now();
  await ensureTables();

  const current = !force
    ? await queryRows<{ generated_at: string; total_entries: number; total_runs: number; total_alerts: number }>(sql`
        SELECT
          COALESCE(
            GREATEST(
              (SELECT max(updated_at) FROM blackbook_entries),
              (SELECT max(updated_at) FROM blackbook_entry_runs),
              (SELECT max(updated_at) FROM blackbook_alerts)
            )::text,
            now()::text
          ) AS generated_at,
          (SELECT count(*)::int FROM blackbook_entries) AS total_entries,
          (SELECT count(*)::int FROM blackbook_entry_runs) AS total_runs,
          (SELECT count(*)::int FROM blackbook_alerts) AS total_alerts
      `)
    : [];

  const storedState = current[0];
  const hasStoredData = Boolean(
    (storedState?.total_entries || 0) > 0 ||
    (storedState?.total_runs || 0) > 0 ||
    (storedState?.total_alerts || 0) > 0,
  );

  if (!force && syncPromise && hasStoredData) {
    return {
      generatedAt: storedState?.generated_at || new Date().toISOString(),
      totalEntries: storedState?.total_entries || 0,
      totalRuns: storedState?.total_runs || 0,
      totalAlerts: storedState?.total_alerts || 0,
    };
  }

  if (!force && hasStoredData && lastSyncAt && now - lastSyncAt < SYNC_TTL_MS) {
    return {
      generatedAt: storedState?.generated_at || new Date().toISOString(),
      totalEntries: storedState?.total_entries || 0,
      totalRuns: storedState?.total_runs || 0,
      totalAlerts: storedState?.total_alerts || 0,
    };
  }

  if (!force && hasStoredData && !lastSyncAt) {
    lastSyncAt = now;
    return {
      generatedAt: storedState?.generated_at || new Date().toISOString(),
      totalEntries: storedState?.total_entries || 0,
      totalRuns: storedState?.total_runs || 0,
      totalAlerts: storedState?.total_alerts || 0,
    };
  }

  if (!force && syncPromise) {
    return syncPromise;
  }

  syncPromise = (async () => {
    const { results, sectionals, franking } = await loadHistoricalRows();
    const { entries, biasSnapshots } = buildHistoricalEntries(results, sectionals, franking);
    const runs = buildRuns(entries);
    const alerts = buildAlerts(entries, runs);

    await replaceBlackbookData(entries, runs, alerts, biasSnapshots);

    lastSyncAt = Date.now();
    return {
      generatedAt: new Date().toISOString(),
      totalEntries: entries.length,
      totalRuns: runs.length,
      totalAlerts: alerts.length,
    };
  })();

  try {
    return await syncPromise;
  } finally {
    syncPromise = null;
  }
}

async function getStoredEntries() {
  return queryRows<any>(sql`SELECT * FROM blackbook_entries ORDER BY updated_at DESC, horse_name ASC`);
}

async function getStoredRuns() {
  return queryRows<any>(sql`SELECT * FROM blackbook_entry_runs ORDER BY race_date ASC, race_number ASC`);
}

async function getStoredAlerts() {
  return queryRows<any>(sql`SELECT * FROM blackbook_alerts ORDER BY readiness_score DESC NULLS LAST, updated_at DESC`);
}

function matchesFilter(entry: any, run: any | undefined, filters: {
  search?: string;
  track?: string;
  region?: string;
  reason?: string;
  readinessBand?: string;
  status?: string;
  valueOnly?: boolean;
}) {
  const search = (filters.search || "").trim().toLowerCase();
  if (search) {
    const haystack = [
      entry.horse_name,
      entry.source_track,
      run?.track,
      entry.primary_reason,
      entry.incident_summary,
    ].join(" ").toLowerCase();
    if (!haystack.includes(search)) {
      return false;
    }
  }

  if (filters.track && filters.track !== "all") {
    const track = run?.track || entry.source_track;
    if (!tracksRoughlyMatch(track, filters.track)) {
      return false;
    }
  }

  if (filters.region && filters.region !== "all") {
    const region = trackRegion(run?.track || entry.source_track);
    if (region !== filters.region) {
      return false;
    }
  }

  if (filters.reason && filters.reason !== "all" && entry.primary_reason !== filters.reason) {
    return false;
  }

  if (filters.readinessBand && filters.readinessBand !== "all") {
    const band = run?.readiness_band || entry.readiness_band;
    if (band !== filters.readinessBand) {
      return false;
    }
  }

  if (filters.status && filters.status !== "all") {
    if ((entry.status || "waiting") !== filters.status) {
      return false;
    }
  }

  if (filters.valueOnly && !(typeof run?.value_edge_pct === "number" && run.value_edge_pct >= 3)) {
    return false;
  }

  return true;
}

function buildBlackbookCard(entry: any, run: any | undefined, alerts: any[]) {
  const delta600 = typeof entry.sectional_delta_600m === "number" ? Math.abs(entry.sectional_delta_600m) : null;
  return {
    id: entry.id,
    horseName: entry.horse_name,
    horseId: entry.horse_id,
    primaryReason: entry.primary_reason,
    reasonLabel: reasonLabel(entry.primary_reason as ReasonCode),
    secondaryEvidenceTags: entry.secondary_evidence_tags || [],
    sourceRun: {
      track: entry.source_track,
      raceDate: entry.source_race_date,
      raceNumber: entry.source_race_number,
      raceName: entry.source_race_name,
      position: entry.source_position,
      marginLengths: entry.source_margin_lengths,
      distanceM: entry.source_distance_m,
      going: entry.source_going,
      barrier: entry.source_barrier,
      raceClass: entry.source_race_class,
    },
    region: trackRegion(run?.track || entry.source_track),
    readinessBand: run?.readiness_band || entry.readiness_band || "WAITING",
    readinessScore: run?.readiness_score || 0,
    status: entry.status,
    intakeConfidence: entry.intake_confidence,
    metric: {
      last600Delta: entry.sectional_delta_600m,
      last400Delta: entry.sectional_delta_400m,
      last200Delta: entry.sectional_delta_200m,
      display: delta600 !== null ? `${delta600.toFixed(2)}s faster than field avg` : "Sectional signal unavailable",
    },
    incidentSummary: entry.incident_summary,
    frankingScore: entry.franking_score,
    nextRun: run ? {
      track: run.track,
      raceDate: run.race_date,
      raceNumber: run.race_number,
      raceName: run.race_name,
      offTime: run.off_time,
      barrier: run.barrier,
      distanceM: run.distance_m,
      going: run.going,
      tempoSummary: run.tempo_summary,
      leaderCount: run.tempo_leader_count,
      marketPrice: run.market_price,
      truePrice: run.true_price,
      valueEdgePct: run.value_edge_pct,
      modelWinProb: run.model_win_prob,
      verdict: run.verdict,
    } : null,
    alertCount: alerts.length,
  };
}

export async function initializeBlackbook() {
  await ensureTables();
  await syncBlackbookData(true);
}

export async function refreshBlackbook() {
  return syncBlackbookData(true);
}

export async function registerBlackbookRoutes(app: Express) {
  await ensureTables();

  app.get("/api/blackbook/alerts", async (_req, res) => {
    try {
      const force = _req.query.refresh === "true";
      await syncBlackbookData(force);
      const alerts = await getStoredAlerts();
      res.json({
        generatedAt: new Date().toISOString(),
        alerts: alerts.map((alert) => ({
          id: alert.id,
          type: alert.alert_type,
          severity: alert.severity,
          title: alert.title,
          message: alert.message,
          horseName: alert.horse_name,
          track: alert.track,
          raceDate: alert.race_date,
          raceNumber: alert.race_number,
          readinessScore: alert.readiness_score,
          updatedAt: alert.updated_at,
        })),
      });
    } catch (error: any) {
      console.error("Blackbook alerts error:", error);
      res.status(500).json({ error: error.message || "Failed to load Blackbook alerts" });
    }
  });

  app.get("/api/blackbook/heatmap", async (req, res) => {
    try {
      const force = req.query.refresh === "true";
      await syncBlackbookData(force);
      const requestedDate = typeof req.query.date === "string" ? req.query.date : undefined;
      const runs = await getStoredRuns();
      const filteredRuns = requestedDate ? runs.filter((run) => run.race_date === requestedDate) : runs;
      const dates = Array.from(new Set(runs.map((run) => run.race_date))).sort();
      const selectedDate = requestedDate && dates.includes(requestedDate) ? requestedDate : dates[0] || null;
      const activeRuns = selectedDate ? filteredRuns.filter((run) => run.race_date === selectedDate) : [];
      const grouped = new Map<string, any[]>();

      for (const run of activeRuns) {
        grouped.set(run.track, [...(grouped.get(run.track) || []), run]);
      }

      res.json({
        selectedDate,
        availableDates: dates,
        meetings: Array.from(grouped.entries()).map(([track, trackRuns]) => ({
          track,
          region: trackRegion(track),
          races: trackRuns
            .sort((a, b) => (a.off_time || "").localeCompare(b.off_time || ""))
            .map((run) => ({
              raceNumber: run.race_number,
              raceName: run.race_name,
              offTime: run.off_time,
              readinessScore: run.readiness_score,
              readinessBand: run.readiness_band,
              horseName: run.horse_name,
              verdict: run.verdict,
              valueDetected: typeof run.value_edge_pct === "number" && run.value_edge_pct >= 3,
            })),
        })),
      });
    } catch (error: any) {
      console.error("Blackbook heatmap error:", error);
      res.status(500).json({ error: error.message || "Failed to load Blackbook heatmap" });
    }
  });

  app.get("/api/blackbook/:id", async (req, res) => {
    try {
      const force = req.query.refresh === "true";
      await syncBlackbookData(force);
      const [entry] = await queryRows<any>(sql`SELECT * FROM blackbook_entries WHERE id = ${req.params.id} LIMIT 1`);
      if (!entry) {
        return res.status(404).json({ error: "Blackbook entry not found" });
      }

      const [run] = await queryRows<any>(sql`SELECT * FROM blackbook_entry_runs WHERE blackbook_entry_id = ${req.params.id} ORDER BY race_date ASC, race_number ASC LIMIT 1`);
      const alerts = await queryRows<any>(sql`SELECT * FROM blackbook_alerts WHERE blackbook_entry_id = ${req.params.id} ORDER BY updated_at DESC`);

      res.json({
        entry: buildBlackbookCard(entry, run, alerts),
        detail: {
          entryAnalysis: entry.entry_analysis_json,
          idealConditions: entry.ideal_conditions_json,
          evidence: entry.evidence_json,
          sourceComment: entry.source_comment,
          sourceIncidents: entry.source_incidents_json,
          conditionAlignment: run?.condition_alignment_json || null,
          raceDayBrief: run?.race_day_brief_json || null,
          readinessBreakdown: run?.breakdown_json || null,
          alerts: alerts.map((alert) => ({
            id: alert.id,
            type: alert.alert_type,
            severity: alert.severity,
            title: alert.title,
            message: alert.message,
            updatedAt: alert.updated_at,
          })),
        },
      });
    } catch (error: any) {
      console.error("Blackbook detail error:", error);
      res.status(500).json({ error: error.message || "Failed to load Blackbook detail" });
    }
  });

  app.get("/api/blackbook", async (req, res) => {
    try {
      const force = req.query.refresh === "true";
      const syncState = await syncBlackbookData(force);
      const [entries, runs, alerts] = await Promise.all([
        getStoredEntries(),
        getStoredRuns(),
        getStoredAlerts(),
      ]);

      const filters = {
        search: typeof req.query.search === "string" ? req.query.search : undefined,
        track: typeof req.query.track === "string" ? req.query.track : undefined,
        region: typeof req.query.region === "string" ? req.query.region : undefined,
        reason: typeof req.query.reason === "string" ? req.query.reason : undefined,
        readinessBand: typeof req.query.readinessBand === "string" ? req.query.readinessBand : undefined,
        status: typeof req.query.status === "string" ? req.query.status : undefined,
        valueOnly: req.query.valueOnly === "true",
      };

      const runsByEntry = new Map(runs.map((run) => [run.blackbook_entry_id, run]));
      const alertsByEntry = new Map<string, any[]>();
      for (const alert of alerts) {
        alertsByEntry.set(alert.blackbook_entry_id, [...(alertsByEntry.get(alert.blackbook_entry_id) || []), alert]);
      }

      const filtered = entries
        .filter((entry) => matchesFilter(entry, runsByEntry.get(entry.id), filters))
        .map((entry) => buildBlackbookCard(entry, runsByEntry.get(entry.id), alertsByEntry.get(entry.id) || []))
        .sort((left, right) => {
          if ((right.readinessScore || 0) !== (left.readinessScore || 0)) {
            return (right.readinessScore || 0) - (left.readinessScore || 0);
          }
          return (left.nextRun?.offTime || "9999").localeCompare(right.nextRun?.offTime || "9999");
        });

      const readinessCounts = filtered.reduce<Record<string, number>>((acc, entry) => {
        acc[entry.readinessBand] = (acc[entry.readinessBand] || 0) + 1;
        return acc;
      }, {});

      const activeDates = Array.from(new Set(runs.map((run) => run.race_date))).sort();
      const nextAction = filtered.find((entry) => entry.nextRun && entry.readinessScore >= 7) || filtered[0] || null;

      res.json({
        generatedAt: syncState.generatedAt,
        summary: {
          totalEntries: filtered.length,
          activeAlerts: alerts.length,
          readinessCounts,
          actionableCount: filtered.filter((entry) => entry.readinessScore >= 7).length,
          valueDetectedCount: filtered.filter((entry) => (entry.nextRun?.valueEdgePct || 0) >= 3).length,
        },
        filters: {
          tracks: Array.from(new Set(entries.map((entry) => runsByEntry.get(entry.id)?.track || entry.source_track))).sort(),
          regions: Array.from(new Set(entries.map((entry) => trackRegion(runsByEntry.get(entry.id)?.track || entry.source_track)))).sort(),
          reasons: Array.from(new Set(entries.map((entry) => entry.primary_reason))).sort(),
          readinessBands: ["REVENGE RACE", "PRIMED", "WATCHING", "MONITORING", "WAITING"],
          statuses: ["active", "monitoring", "waiting", "expired"],
          upcomingDates: activeDates,
        },
        nextAction,
        entries: filtered,
      });
    } catch (error: any) {
      console.error("Blackbook list error:", error);
      res.status(500).json({ error: error.message || "Failed to load Blackbook entries" });
    }
  });
}
