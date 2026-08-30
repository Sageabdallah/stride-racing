import fs from "fs";
import path from "path";
import { createHash } from "crypto";
import { sql } from "drizzle-orm";
import { db } from "./db";

type TempoLabel = "HOT" | "GENUINE" | "SOFT" | "UNKNOWN";
type SpeedMapZone = "leader" | "pace" | "off_pace" | "midfield" | "off_midfield" | "backmarker";
type PlacementSource = "historical" | "comment" | "fallback";

interface TipRace {
  track: string;
  race_number: number;
  race_name: string;
  distance: string;
  going: string;
  race_class: string;
  field_size: number;
  full_field: Record<string, unknown>[];
  top_picks: Record<string, unknown>[];
  raw_model_leader?: Record<string, unknown> | null;
  bet_pick?: Record<string, unknown> | null;
  coverage_pick?: Record<string, unknown> | null;
}

interface TipsPayload {
  date: string;
  races?: TipRace[];
}

interface RacecardMeeting {
  course: string;
  races: RacecardRace[];
}

interface RacecardRace {
  course?: string;
  date: string;
  distance?: string;
  going?: string;
  race_name: string;
  race_number: number;
  class?: string;
  runners: RacecardRunner[];
}

interface RacecardRunner {
  horse_id?: string;
  horse: string;
  age?: string;
  comment?: string;
  colour?: string;
  draw?: string | number;
  jockey?: string;
  trainer?: string;
  silk_url?: string;
  sex?: string;
  number?: string | number;
  scratched?: boolean;
  weight?: string | number;
}

interface SpeedMapDbRow {
  horse_id: string | null;
  horse_name: string;
  race_date: string;
  track: string;
  race_number: number | null;
  field_size: number | null;
  position: number | null;
  weight_kg: number | null;
  splits_json: unknown;
  sectional_created_at: string | null;
}

interface HistoricalBandSample {
  raceDate: string;
  fieldSize: number;
  positionAt800: number;
  percentileAt800: number;
  finishPosition: number | null;
  weightCarried: number | null;
}

interface PlacementSeed {
  horse: string;
  horseId: string | null;
  saddleNumber: number | null;
  barrier: number | null;
  jockey: string | null;
  trainer: string | null;
  silkUrl: string | null;
  colour: string | null;
  sex: string | null;
  age: string | null;
  comment: string | null;
  marketOdds: number | null;
  isTipped: boolean;
  tipRank: number | null;
  isHighlighted: boolean;
  desire: number;
  confidence: number;
  placementSource: PlacementSource;
  placementReason: string;
  blendedPercentile: number;
}

export interface RaceSpeedMapRunner {
  horse: string;
  horseId: string | null;
  saddleNumber: number | null;
  barrier: number | null;
  jockey: string | null;
  trainer: string | null;
  silkUrl: string | null;
  colour: string | null;
  sex: string | null;
  age: string | null;
  comment: string | null;
  marketOdds: number | null;
  isTipped: boolean;
  tipRank: number | null;
  isHighlighted: boolean;
  predictedSettlingPosition: number;
  settlingPercentile: number;
  zone: SpeedMapZone;
  lane: number;
  stackRow: number;
  placementSource: PlacementSource;
  placementReason: string;
}

export interface RaceSpeedMap {
  track: string;
  raceNumber: number;
  raceName: string;
  distance: string;
  going: string;
  fieldSize: number;
  tempoLabel: TempoLabel;
  tempoReason: string;
  highlightedHorse: string | null;
  leaders: number;
  onPace: number;
  midfield: number;
  closers: number;
  narrative: string;
  runners: RaceSpeedMapRunner[];
}

interface EnrichedFullFieldRunner extends Record<string, unknown> {
  horse_id?: string | null;
  silk_url?: string | null;
  colour?: string | null;
  sex?: string | null;
  age?: string | null;
  comment?: string | null;
}

interface RaceFieldWithSpeedMap {
  track: string;
  race_number: number;
  race_name: string;
  distance: string;
  going: string;
  race_class: string;
  field_size: number;
  full_field: EnrichedFullFieldRunner[];
  top_picks: Record<string, unknown>[];
  speedMap: RaceSpeedMap | null;
}

const speedMapCache = new Map<string, RaceFieldWithSpeedMap[]>();
const INTERNAL_ZONE_ORDER: SpeedMapZone[] = [
  "backmarker",
  "off_midfield",
  "midfield",
  "off_pace",
  "pace",
  "leader",
];

function normalizeName(value: string | null | undefined) {
  return String(value || "")
    .toLowerCase()
    .replace(/[^a-z0-9]/g, "");
}

function normalizeTrackName(value: string | null | undefined) {
  return normalizeName(String(value || "").replace(/gardens/g, ""));
}

function firstString(...values: unknown[]) {
  for (const value of values) {
    if (typeof value === "string" && value.trim()) {
      return value.trim();
    }
  }
  return "";
}

function toNumber(value: unknown): number | null {
  if (typeof value === "number" && Number.isFinite(value)) {
    return value;
  }
  if (typeof value === "string") {
    const normalized = value.replace(/[^0-9.\-]/g, "");
    if (!normalized) {
      return null;
    }
    const parsed = Number(normalized);
    return Number.isFinite(parsed) ? parsed : null;
  }
  return null;
}

function roundTo(value: number, digits = 1) {
  const scale = 10 ** digits;
  return Math.round(value * scale) / scale;
}

function clamp(value: number, min: number, max: number) {
  return Math.max(min, Math.min(max, value));
}

function escapeSqlString(value: string) {
  return value.replace(/'/g, "''");
}

function normalizedSqlText(expression: string) {
  return `regexp_replace(lower(coalesce(${expression}, '')), '[^a-z0-9]+', '', 'g')`;
}

function buildSectionalTrackMatchSql(sectionalTrackExpr: string, historyTrackExpr: string) {
  const left = normalizedSqlText(sectionalTrackExpr);
  const right = normalizedSqlText(historyTrackExpr);
  return `(${left} = ${right} OR ${left} LIKE '%' || ${right} || '%' OR ${right} LIKE '%' || ${left} || '%')`;
}

function buildSectionalHorseMatchSql(sectionalHorseExpr: string, historyHorseExpr: string) {
  return `${normalizedSqlText(sectionalHorseExpr)} = ${normalizedSqlText(historyHorseExpr)}`;
}

function buildSectionalFallbackJoinSql(sectionalAlias: string, historyAlias: string) {
  return [
    `${sectionalAlias}.race_date::date = ${historyAlias}.race_date::date`,
    `${sectionalAlias}.race_number = ${historyAlias}.race_number`,
    buildSectionalTrackMatchSql(`${sectionalAlias}.track`, `${historyAlias}.track`),
    buildSectionalHorseMatchSql(`${sectionalAlias}.horse_name`, `${historyAlias}.horse_name`),
  ].join(" AND ");
}

function getFileHash(filePath: string) {
  if (!fs.existsSync(filePath)) {
    return "missing";
  }
  const content = fs.readFileSync(filePath);
  return createHash("sha1").update(content).digest("hex");
}

function positionFromPercentile(percentile: number, fieldSize: number) {
  if (fieldSize <= 1) {
    return 1;
  }
  return 1 + (clamp(percentile, 0, 100) / 100) * (fieldSize - 1);
}

function zoneForPosition(position: number, fieldSize: number): SpeedMapZone {
  if (fieldSize <= 1) {
    return "leader";
  }
  const percentile = ((position - 1) / Math.max(1, fieldSize - 1)) * 100;
  if (percentile <= 12) return "leader";
  if (percentile <= 28) return "pace";
  if (percentile <= 46) return "off_pace";
  if (percentile <= 64) return "midfield";
  if (percentile <= 84) return "off_midfield";
  return "backmarker";
}

function barrierLane(barrier: number | null, fieldSize: number) {
  if (barrier == null || fieldSize <= 0) {
    return 1;
  }
  const ratio = barrier / Math.max(1, fieldSize);
  if (ratio <= 0.33) return 0;
  if (ratio <= 0.66) return 1;
  return 2;
}

function extractPositionAt800(value: unknown, depth = 0): number | null {
  if (depth > 5 || value == null) {
    return null;
  }

  if (typeof value === "string") {
    try {
      return extractPositionAt800(JSON.parse(value), depth + 1);
    } catch {
      return null;
    }
  }

  if (Array.isArray(value)) {
    for (const entry of value) {
      const found = extractPositionAt800(entry, depth + 1);
      if (found != null) {
        return found;
      }
    }
    return null;
  }

  if (typeof value !== "object") {
    return null;
  }

  const record = value as Record<string, unknown>;
  for (const [key, entry] of Object.entries(record)) {
    const normalizedKey = normalizeName(key);
    if (!normalizedKey.includes("800")) {
      continue;
    }

    if (typeof entry === "object" && entry != null) {
      const nested = entry as Record<string, unknown>;
      const direct = toNumber(nested.position ?? nested.pos ?? nested.settling_position ?? nested.running_position);
      if (direct != null) {
        return direct;
      }
    }

    const direct = toNumber(entry);
    if (direct != null) {
      return direct;
    }
  }

  for (const entry of Object.values(record)) {
    const found = extractPositionAt800(entry, depth + 1);
    if (found != null) {
      return found;
    }
  }

  return null;
}

function parseHistoricalBandSampleFromDatabase(row: SpeedMapDbRow): HistoricalBandSample | null {
  const fieldSize = toNumber(row.field_size);
  if (fieldSize == null || fieldSize <= 0) {
    return null;
  }

  const positionAt800 = extractPositionAt800(row.splits_json);
  if (positionAt800 == null || positionAt800 <= 0) {
    return null;
  }

  return {
    raceDate: firstString(row.race_date),
    fieldSize,
    positionAt800,
    percentileAt800: (positionAt800 / fieldSize) * 100,
    finishPosition: toNumber(row.position),
    weightCarried: toNumber(row.weight_kg),
  };
}

function estimateDesiredPositionFromStyle(runningStyle: string | null | undefined, fieldSize: number) {
  const normalized = normalizeName(runningStyle);
  if (normalized.includes("leader") || normalized.includes("speed")) {
    return { position: 1.4, reason: "Fallback to STRIDE leader profile because no measured in-run samples were found." };
  }
  if (normalized.includes("onpace") || normalized.includes("pace") || normalized.includes("stalker")) {
    return { position: Math.max(2, fieldSize * 0.22), reason: "Fallback to STRIDE on-pace profile because no measured in-run samples were found." };
  }
  if (normalized.includes("midfield") || normalized.includes("forward")) {
    return { position: Math.max(3, fieldSize * 0.48), reason: "Fallback to STRIDE midfield profile because no measured in-run samples were found." };
  }
  if (normalized.includes("offpace") || normalized.includes("closer")) {
    return { position: Math.max(4, fieldSize * 0.72), reason: "Fallback to STRIDE off-pace profile because no measured in-run samples were found." };
  }
  if (normalized.includes("backmarker")) {
    return { position: Math.max(5, fieldSize * 0.88), reason: "Fallback to STRIDE backmarker profile because no measured in-run samples were found." };
  }
  return { position: Math.max(3, fieldSize * 0.56), reason: "Fallback to neutral settling profile because no measured in-run samples were found." };
}

function parseCommentHint(comment: string | null | undefined, fieldSize: number) {
  const raw = firstString(comment).toLowerCase();
  if (!raw) {
    return null;
  }

  const patterns: Array<{ position: number; reason: string; matches: RegExp[] }> = [
    {
      position: 1.1,
      reason: "Most recent run comment says the horse led or went forward to the top.",
      matches: [/\bto lead\b/, /\blead them\b/, /\bled\b/, /went forward to lead/, /settled down lead/],
    },
    {
      position: 2.1,
      reason: "Most recent run comment maps the horse outside the leader or right on the pace.",
      matches: [/outside lead/, /behind leader/, /\b1-1\b/, /box seat/, /one out one back/],
    },
    {
      position: Math.max(3, fieldSize * 0.32),
      reason: "Most recent run comment maps the horse just behind the speed.",
      matches: [/2back/, /two wide/, /one out/, /settled down 3rd/, /settled down 4th/],
    },
    {
      position: Math.max(4, fieldSize * 0.54),
      reason: "Most recent run comment points to a midfield settling pattern.",
      matches: [/midfield/, /settled down 5/, /settled down 6/, /settled down 7/, /\b5th\b/, /\b6th\b/],
    },
    {
      position: Math.max(5, fieldSize * 0.82),
      reason: "Most recent run comment points to a back-half settling pattern.",
      matches: [/drifted back/, /\bback\b/, /\blast\b/, /\btail\b/, /rearward/, /2back inside/, /back\/solid/, /back\/no threat/],
    },
  ];

  for (const pattern of patterns) {
    if (pattern.matches.some((regex) => regex.test(raw))) {
      return pattern;
    }
  }

  return null;
}

function buildBarrierAdjustment(position: number, barrier: number | null, fieldSize: number) {
  if (barrier == null || fieldSize <= 1) {
    return 0;
  }

  const ratio = barrier / fieldSize;
  const forwardProfile = position <= Math.max(2.5, fieldSize * 0.32);

  if (forwardProfile) {
    if (ratio <= 0.2) return -0.45;
    if (ratio >= 0.8) return 1.25;
    if (ratio >= 0.6) return 0.55;
  }

  if (!forwardProfile && ratio <= 0.18) {
    return -0.15;
  }

  return 0;
}

function determineTempoLabel(leaders: number, pace: number): TempoLabel {
  if (leaders >= 3) {
    return "HOT";
  }
  if (leaders === 2) {
    return "GENUINE";
  }
  if (leaders === 1 && pace >= 3) {
    return "GENUINE";
  }
  if (leaders === 1) {
    return "SOFT";
  }
  return "UNKNOWN";
}

function describeTempo(tempoLabel: TempoLabel, leaderNames: string[], paceNames: string[]) {
  if (tempoLabel === "HOT") {
    return `${leaderNames.slice(0, 3).join(", ")} create a genuine pressure line, so the opening section should be fast rather than controlled.`;
  }
  if (tempoLabel === "GENUINE") {
    if (leaderNames.length === 1) {
      return `${leaderNames[0]} gets forward, but ${paceNames.slice(0, 3).join(", ")} keep enough heat on to stop the race from becoming a crawl.`;
    }
    return `${leaderNames.slice(0, 2).join(" and ")} look set to keep the first half honest with pace runners tracking right behind them.`;
  }
  if (tempoLabel === "SOFT") {
    return `${leaderNames[0] || "The main speed horse"} may get a cheap first half because there is limited pressure outside the leader line.`;
  }
  return "There is no clean standalone leader in the map, so the early pattern looks tactical rather than clearly run to script.";
}

function buildDeterministicNarrative(
  track: string,
  going: string,
  tempoLabel: TempoLabel,
  leaderNames: string[],
  paceNames: string[],
  closerNames: string[],
) {
  const leaderText = leaderNames.length
    ? leaderNames.slice(0, 3).join(", ")
    : "No runner has a clean leader profile";
  const paceText = paceNames.length
    ? paceNames.slice(0, 4).join(", ")
    : "the first chasing line is thin";
  const closerText = closerNames.length
    ? closerNames.slice(0, 3).join(", ")
    : "there is not a deep closing wave";
  const straightBias = tempoLabel === "HOT"
    ? "That gives the back-half runners a stronger chance to build into the race late."
    : tempoLabel === "SOFT"
      ? "That favours runners camped in the first two pairs because the leaders are less likely to come back sharply."
      : "That keeps the race balanced between the on-speed line and the runners stalking just behind them.";

  return `${leaderText} should shape the first 400m at ${track}, while ${paceText} hold the stalking positions rather than gifting a cheap sectional. Through the middle section the tempo reads ${tempoLabel.toLowerCase()} on ${going.toLowerCase()} ground, so the key tension is whether the pace line keeps rolling or hands control to a single runner. ${closerText} are the runners relying on the race to stretch before the bend. ${straightBias}`;
}

async function loadHistoricalBandSamplesForDate(
  runners: Array<{ horseId: string | null; horse: string }>,
  raceDate: string,
) {
  const horseIds = Array.from(new Set(runners.map((runner) => runner.horseId).filter(Boolean))) as string[];
  const horseNames = Array.from(new Set(runners.map((runner) => runner.horse).filter(Boolean)));

  if (horseIds.length === 0 && horseNames.length === 0) {
    return new Map<string, HistoricalBandSample[]>();
  }

  const lookbackDate = new Date(`${raceDate}T00:00:00Z`);
  lookbackDate.setUTCDate(lookbackDate.getUTCDate() - 365);
  const lookbackIso = lookbackDate.toISOString().slice(0, 10);

  const idClause = horseIds.length
    ? `rrh.horse_id IN (${horseIds.map((value) => `'${escapeSqlString(value)}'`).join(", ")})`
    : "FALSE";
  const nameClause = horseNames.length
    ? `rrh.horse_name IN (${horseNames.map((value) => `'${escapeSqlString(value)}'`).join(", ")})`
    : "FALSE";

  try {
    const result = await db.execute(sql.raw(`
      SELECT
        rrh.horse_id,
        rrh.horse_name,
        rrh.race_date,
        rrh.track,
        rrh.race_number,
        rrh.field_size,
        rrh.position,
        rrh.weight_kg,
        st.splits_json,
        st.created_at AS sectional_created_at
      FROM race_results_history rrh
      LEFT JOIN LATERAL (
        SELECT st.splits_json, st.created_at
        FROM sectional_times st
        WHERE st.race_results_history_id = rrh.id
           OR (${buildSectionalFallbackJoinSql("st", "rrh")})
        ORDER BY
          CASE WHEN st.race_results_history_id = rrh.id THEN 0 ELSE 1 END,
          st.created_at DESC NULLS LAST
        LIMIT 1
      ) st ON TRUE
      WHERE rrh.race_date >= '${lookbackIso}'
        AND rrh.race_date < '${escapeSqlString(raceDate)}'
        AND (${idClause} OR ${nameClause})
      ORDER BY rrh.race_date DESC, st.created_at DESC NULLS LAST
    `));

    const rows = ((result.rows || []) as unknown[]) as SpeedMapDbRow[];
    const deduped = new Map<string, Map<string, { sample: HistoricalBandSample; createdAtMs: number }>>();

    for (const row of rows) {
      const sample = parseHistoricalBandSampleFromDatabase(row);
      if (!sample) {
        continue;
      }

      const horseKey = firstString(row.horse_id) || normalizeName(row.horse_name);
      const raceKey = `${row.race_date}|${normalizeTrackName(row.track)}|${row.race_number ?? ""}`;
      const createdAtMs = row.sectional_created_at ? new Date(row.sectional_created_at).getTime() : 0;
      const byRace = deduped.get(horseKey) || new Map<string, { sample: HistoricalBandSample; createdAtMs: number }>();
      const existing = byRace.get(raceKey);
      if (!existing || createdAtMs > existing.createdAtMs) {
        byRace.set(raceKey, { sample, createdAtMs });
      }
      deduped.set(horseKey, byRace);
    }

    const output = new Map<string, HistoricalBandSample[]>();
    for (const runner of runners) {
      const horseKey = runner.horseId || normalizeName(runner.horse);
      const byRace = deduped.get(horseKey);
      if (!byRace) {
        continue;
      }
      output.set(
        horseKey,
        Array.from(byRace.values())
          .map((entry) => entry.sample)
          .sort((left, right) => right.raceDate.localeCompare(left.raceDate))
          .slice(0, 5),
      );
    }

    return output;
  } catch (error) {
    console.warn("Speed map historical sample warning:", error instanceof Error ? error.message : error);
    return new Map<string, HistoricalBandSample[]>();
  }
}

function buildPlacementSeed(input: {
  runner: RacecardRunner;
  tipRunner?: Record<string, unknown>;
  isHighlighted: boolean;
  fieldSize: number;
  samples: HistoricalBandSample[];
}) {
  const { runner, tipRunner, isHighlighted, fieldSize, samples } = input;
  const horse = firstString(runner.horse);
  const horseId = firstString(runner.horse_id) || null;
  const barrier = toNumber(runner.draw);
  const saddleNumber = toNumber(runner.number);
  const historicalPercentile = samples.length
    ? samples.reduce((sum, sample) => sum + sample.percentileAt800, 0) / samples.length
    : null;
  const historicalPosition = historicalPercentile != null
    ? positionFromPercentile(historicalPercentile, fieldSize)
    : null;
  const commentHint = parseCommentHint(runner.comment, fieldSize);
  const fallback = estimateDesiredPositionFromStyle(firstString(tipRunner?.running_style), fieldSize);

  let desire = historicalPosition ?? commentHint?.position ?? fallback.position;
  let placementSource: PlacementSource = historicalPosition != null ? "historical" : commentHint ? "comment" : "fallback";
  const reasonParts: string[] = [];

  if (historicalPosition != null) {
    reasonParts.push(`Historical 800m calls average ${roundTo(historicalPercentile || 0, 1)}% of the field from the front across ${samples.length} measured starts.`);
    if (commentHint) {
      desire = historicalPosition * 0.72 + commentHint.position * 0.28;
      reasonParts.push(commentHint.reason);
    }
  } else if (commentHint) {
    reasonParts.push(commentHint.reason);
  } else {
    reasonParts.push(fallback.reason);
  }

  const adjustment = buildBarrierAdjustment(desire, barrier, fieldSize);
  if (adjustment !== 0) {
    desire += adjustment;
    reasonParts.push(adjustment > 0
      ? `Barrier ${barrier} adds early work before finding the preferred line.`
      : `Barrier ${barrier} helps the horse hold a more economical early position.`);
  }

  desire = clamp(desire, 1, Math.max(1, fieldSize));

  const confidence = historicalPosition != null
    ? (commentHint ? 0.92 : 0.86)
    : commentHint
      ? 0.66
      : 0.36;

  return {
    horse,
    horseId,
    saddleNumber,
    barrier,
    jockey: firstString(runner.jockey) || null,
    trainer: firstString(runner.trainer) || null,
    silkUrl: firstString(runner.silk_url) || null,
    colour: firstString(runner.colour) || null,
    sex: firstString(runner.sex) || null,
    age: firstString(runner.age) || null,
    comment: firstString(runner.comment) || null,
    marketOdds: toNumber(tipRunner?.odds),
    isTipped: Boolean(tipRunner?.is_tipped),
    tipRank: toNumber(tipRunner?.tip_rank),
    isHighlighted,
    desire,
    confidence,
    placementSource,
    placementReason: reasonParts.join(" "),
    blendedPercentile: historicalPercentile ?? (((desire - 1) / Math.max(1, fieldSize - 1)) * 100),
  } satisfies PlacementSeed;
}

function assignSettlingPositions(seeds: PlacementSeed[], fieldSize: number) {
  const sorted = seeds.slice().sort((left, right) => {
    if (left.desire !== right.desire) {
      return left.desire - right.desire;
    }
    if (left.confidence !== right.confidence) {
      return right.confidence - left.confidence;
    }
    const leftBarrier = left.barrier ?? 99;
    const rightBarrier = right.barrier ?? 99;
    if (leftBarrier !== rightBarrier) {
      return leftBarrier - rightBarrier;
    }
    return left.horse.localeCompare(right.horse);
  });

  const usedPositions = new Set<number>();
  const assigned = new Map<string, number>();

  for (const seed of sorted) {
    const ideal = clamp(Math.round(seed.desire), 1, Math.max(1, fieldSize));
    if (!usedPositions.has(ideal)) {
      usedPositions.add(ideal);
      assigned.set(seed.horse, ideal);
      continue;
    }

    let bestPosition = ideal;
    let bestDistance = Number.POSITIVE_INFINITY;
    for (let position = 1; position <= fieldSize; position += 1) {
      if (usedPositions.has(position)) {
        continue;
      }
      const distance = Math.abs(position - ideal);
      if (distance < bestDistance) {
        bestDistance = distance;
        bestPosition = position;
      }
    }

    usedPositions.add(bestPosition);
    assigned.set(seed.horse, bestPosition);
  }

  return sorted
    .map((seed) => {
      const predictedSettlingPosition = assigned.get(seed.horse) || clamp(Math.round(seed.desire), 1, Math.max(1, fieldSize));
      const settlingPercentile = fieldSize <= 1
        ? 0
        : roundTo(((predictedSettlingPosition - 1) / Math.max(1, fieldSize - 1)) * 100, 1);
      return {
        ...seed,
        predictedSettlingPosition,
        settlingPercentile,
        zone: zoneForPosition(predictedSettlingPosition, fieldSize),
      };
    })
    .sort((left, right) => left.predictedSettlingPosition - right.predictedSettlingPosition);
}

function applyZoneStacks(
  runners: Array<PlacementSeed & { predictedSettlingPosition: number; settlingPercentile: number; zone: SpeedMapZone }>,
  fieldSize: number,
): RaceSpeedMapRunner[] {
  const laneStacks = new Map<string, number>();

  return runners.map((runner) => {
    const lane = barrierLane(runner.barrier, fieldSize);
    const stackKey = `${runner.zone}|${lane}`;
    const stackRow = laneStacks.get(stackKey) || 0;
    laneStacks.set(stackKey, stackRow + 1);

    return {
      horse: runner.horse,
      horseId: runner.horseId,
      saddleNumber: runner.saddleNumber,
      barrier: runner.barrier,
      jockey: runner.jockey,
      trainer: runner.trainer,
      silkUrl: runner.silkUrl,
      colour: runner.colour,
      sex: runner.sex,
      age: runner.age,
      comment: runner.comment,
      marketOdds: runner.marketOdds,
      isTipped: runner.isTipped,
      tipRank: runner.tipRank,
      isHighlighted: runner.isHighlighted,
      predictedSettlingPosition: runner.predictedSettlingPosition,
      settlingPercentile: runner.settlingPercentile,
      zone: runner.zone,
      lane,
      stackRow,
      placementSource: runner.placementSource,
      placementReason: runner.placementReason,
    };
  });
}

function buildRaceSpeedMap(
  tipRace: TipRace,
  racecardRace: RacecardRace,
  historySamples: Map<string, HistoricalBandSample[]>,
) {
  const tipRunnerMap = new Map<string, Record<string, unknown>>();
  for (const runner of tipRace.full_field || []) {
    const horse = firstString(runner.horse);
    if (horse) {
      tipRunnerMap.set(normalizeName(horse), runner);
    }
  }

  const highlightedHorse =
    firstString((tipRace as any).bet_pick?.horse)
    || firstString((tipRace as any).coverage_pick?.horse)
    || firstString(tipRace.top_picks?.[0]?.horse)
    || null;
  const activeRunners = (racecardRace.runners || []).filter((runner) => !runner.scratched && firstString(runner.horse));
  const fieldSize = activeRunners.length || tipRace.field_size || 0;

  const seeds = activeRunners.map((runner) => {
    const normalizedHorse = normalizeName(runner.horse);
    const tipRunner = tipRunnerMap.get(normalizedHorse);
    const horseKey = firstString(runner.horse_id) || normalizedHorse;
    return buildPlacementSeed({
      runner,
      tipRunner,
      isHighlighted: normalizeName(highlightedHorse) === normalizedHorse,
      fieldSize,
      samples: historySamples.get(horseKey) || [],
    });
  });

  const positioned = assignSettlingPositions(seeds, fieldSize);
  const runners = applyZoneStacks(positioned, fieldSize);

  const leaderNames = runners.filter((runner) => runner.zone === "leader").map((runner) => runner.horse);
  const paceNames = runners.filter((runner) => runner.zone === "pace" || runner.zone === "off_pace").map((runner) => runner.horse);
  const closerNames = runners.filter((runner) => runner.zone === "off_midfield" || runner.zone === "backmarker").map((runner) => runner.horse);
  const leaders = leaderNames.length;
  const onPace = runners.filter((runner) => runner.zone === "leader" || runner.zone === "pace").length;
  const midfield = runners.filter((runner) => runner.zone === "off_pace" || runner.zone === "midfield").length;
  const closers = closerNames.length;
  const tempoLabel = determineTempoLabel(leaders, paceNames.length);
  const tempoReason = describeTempo(tempoLabel, leaderNames, paceNames);

  return {
    track: tipRace.track,
    raceNumber: tipRace.race_number,
    raceName: tipRace.race_name,
    distance: tipRace.distance,
    going: tipRace.going,
    fieldSize,
    tempoLabel,
    tempoReason,
    highlightedHorse,
    leaders,
    onPace,
    midfield,
    closers,
    narrative: buildDeterministicNarrative(tipRace.track, tipRace.going, tempoLabel, leaderNames, paceNames, closerNames),
    runners,
  } satisfies RaceSpeedMap;
}

function enrichFullField(
  tipRace: TipRace,
  racecardRace: RacecardRace,
) {
  const racecardRunnerMap = new Map<string, RacecardRunner>();
  for (const runner of racecardRace.runners || []) {
    const horse = firstString(runner.horse);
    if (horse) {
      racecardRunnerMap.set(normalizeName(horse), runner);
    }
  }

  return (tipRace.full_field || []).map((runner) => {
    const horse = firstString(runner.horse);
    const racecardRunner = racecardRunnerMap.get(normalizeName(horse));
    return {
      ...runner,
      horse_id: racecardRunner?.horse_id || null,
      silk_url: racecardRunner?.silk_url || null,
      colour: racecardRunner?.colour || null,
      sex: racecardRunner?.sex || null,
      age: racecardRunner?.age || null,
      comment: racecardRunner?.comment || null,
    } satisfies EnrichedFullFieldRunner;
  });
}

function indexRacecardRaces(meetings: RacecardMeeting[]) {
  const byKey = new Map<string, RacecardRace>();
  for (const meeting of meetings) {
    for (const race of meeting.races || []) {
      const track = firstString(race.course, meeting.course);
      const key = `${normalizeTrackName(track)}|${race.race_number}`;
      byKey.set(key, race);
    }
  }
  return byKey;
}

export async function loadRaceFieldWithSpeedMaps(date: string) {
  const tipsPath = path.join(process.cwd(), "racecards", `tips_${date}.json`);
  const racecardPath = path.join(process.cwd(), "racecards", `racecard_${date}.json`);
  if (!fs.existsSync(tipsPath)) {
    return [];
  }

  const cacheKey = `${date}|${getFileHash(tipsPath)}|${getFileHash(racecardPath)}`;
  const cached = speedMapCache.get(cacheKey);
  if (cached) {
    return cached;
  }

  const tipsPayload = JSON.parse(fs.readFileSync(tipsPath, "utf-8")) as TipsPayload;
  const racecardMeetings = fs.existsSync(racecardPath)
    ? (JSON.parse(fs.readFileSync(racecardPath, "utf-8")) as RacecardMeeting[])
    : [];
  const racecardIndex = indexRacecardRaces(racecardMeetings);
  const racecardRunners = racecardMeetings.flatMap((meeting) =>
    (meeting.races || []).flatMap((race) =>
      (race.runners || [])
        .filter((runner) => !runner.scratched && firstString(runner.horse))
        .map((runner) => ({
          horseId: firstString(runner.horse_id) || null,
          horse: firstString(runner.horse),
        })),
    ),
  );
  const historySamples = await loadHistoricalBandSamplesForDate(racecardRunners, date);

  const races = (tipsPayload.races || []).map((tipRace) => {
    const raceKey = `${normalizeTrackName(tipRace.track)}|${tipRace.race_number}`;
    const racecardRace = racecardIndex.get(raceKey);
    const speedMap = racecardRace
      ? buildRaceSpeedMap(tipRace, racecardRace, historySamples)
      : null;

    return {
      track: tipRace.track,
      race_number: tipRace.race_number,
      race_name: tipRace.race_name,
      distance: tipRace.distance,
      going: tipRace.going,
      race_class: tipRace.race_class,
      field_size: tipRace.field_size,
      full_field: racecardRace ? enrichFullField(tipRace, racecardRace) : (tipRace.full_field || []),
      top_picks: tipRace.top_picks || [],
      speedMap,
    } satisfies RaceFieldWithSpeedMap;
  });

  speedMapCache.clear();
  speedMapCache.set(cacheKey, races);
  return races;
}

export { INTERNAL_ZONE_ORDER };
