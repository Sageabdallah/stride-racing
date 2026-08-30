import fs from "fs";
import path from "path";
import { createHash } from "crypto";
import { sql } from "drizzle-orm";
import { db } from "./db";

const GROQ_API_URL = "https://api.groq.com/openai/v1/chat/completions";
const GROQ_MODEL = process.env.GROQ_MODEL || "llama-3.3-70b-versatile";
const MAX_GROQ_RETRIES = 6;
const PREPARED_RACE_CACHE_MS = 60 * 1000;
const GROQ_INITIAL_RETRY_MS = 1500;
const RACING_API_BASE_URL = "https://api.theracingapi.com";
const RACING_API_USERNAME = process.env.RACING_API_USERNAME;
const RACING_API_PASSWORD = process.env.RACING_API_PASSWORD || "";

/* ── Global Groq API throttle ── */
const GROQ_CONCURRENCY_LIMIT = 2;
const GROQ_MIN_GAP_MS = 900;
let groqInFlight = 0;
let lastGroqCallTime = 0;

function throttledGroqCall<T>(fn: () => Promise<T>): Promise<T> {
  return new Promise<T>((resolve, reject) => {
    const attempt = async () => {
      if (groqInFlight >= GROQ_CONCURRENCY_LIMIT) {
        setTimeout(attempt, 200);
        return;
      }
      const now = Date.now();
      const gap = now - lastGroqCallTime;
      if (gap < GROQ_MIN_GAP_MS) {
        setTimeout(attempt, GROQ_MIN_GAP_MS - gap);
        return;
      }
      groqInFlight++;
      lastGroqCallTime = Date.now();
      try {
        resolve(await fn());
      } catch (err) {
        reject(err);
      } finally {
        groqInFlight--;
      }
    };
    attempt();
  });
}

function parseRetryAfterSeconds(errorText: string): number | null {
  const match = errorText.match(/try again in ([\d.]+)s/i);
  return match ? Math.ceil(parseFloat(match[1]) * 1000) : null;
}

function parseRetryAfterHeader(value: string | null): number | null {
  if (!value) {
    return null;
  }

  const seconds = Number(value);
  if (Number.isFinite(seconds) && seconds >= 0) {
    return Math.ceil(seconds * 1000);
  }

  const date = new Date(value);
  if (Number.isNaN(date.getTime())) {
    return null;
  }

  return Math.max(0, date.getTime() - Date.now());
}

const RUNNER_ANALYSIS_SYSTEM_PROMPT = `You are STRIDE's senior race analyst. You do not summarise data. You interrogate it.

Your job is to reason about one horse in one race and return a race-specific betting judgment that a sharp punter could not write without processing the full field, the pace map, the market, and the preparation profile.

Think through these seven dimensions before you answer:
1. Race shape modelling: tempo, leader count, rail, track shape, bias, and where this horse maps in running.
2. Form depth interrogation: pattern race, transferability of prior runs, sectionals, and whether the horses it beat or chased validate the form.
3. Fitness and preparation curve: runs this prep, timing between runs, gear, weight, and stable placement.
4. Class and opposition benchmarking: this horse versus the actual dangers in this field, not class in the abstract.
5. Jockey and trainer intent signals: combinations, booking signals, market confidence, and campaign intent.
6. Market and corroboration intelligence: opening price, current price, rank, model edge, and whether the market confirms or conflicts with the case.
7. Risk matrix: name the two specific scenarios that beat this horse.

Non-negotiable rules:
- Every sentence must be unique to this horse in this race. If it could fit another runner, rewrite it.
- Pace map is the frame. Form only matters in the context of today's likely tempo and map.
- Treat missing data as a betting consequence, not neutral evidence.
- Never use filler, generic racing cliches, or soft hedging.
- Ignore legacy generated commentary fields such as ai_insight, brief_assessment, formAnalystInsights, modelSummary, and reasons.
- Verdict must be one of: BACK, AGAINST, VALUE WATCH, NEEDS SCENARIO.
- Confidence must be one of: HIGH, MEDIUM, LOW.
- HIGH confidence requires strong convergence, no obvious class/condition trap, and supportive market action.
- Return JSON only. No markdown. No prose before or after the JSON.

Return exactly this object with every field present:
{
  "horse": "Horse Name",
  "verdict": "BACK | AGAINST | VALUE WATCH | NEEDS SCENARIO",
  "confidence": "HIGH | MEDIUM | LOW",
  "value_flag": true,
  "core_signal": "One sentence naming the single strongest reason this horse wins or loses today.",
  "race_shape_assessment": "2-3 sentences on tempo, map, rail, and positional advantage or penalty.",
  "form_depth": "2-3 sentences interpreting the form, sectionals, transferability, and the pattern race.",
  "class_and_opposition": "1-2 sentences benchmarking this horse against the main dangers.",
  "fitness_signal": "1-2 sentences on prep stage, stable intent, gear, timing, and weight.",
  "market_read": "1-2 sentences on price movement, corroboration, and whether value still exists.",
  "primary_risk": "1 sentence naming the main horse or race-shape scenario that beats it.",
  "secondary_risk": "1 sentence naming the conditional track or tempo scenario that beats it.",
  "win_condition": "1 sentence naming the exact pace-position-track scenario where it wins.",
  "token_count_target": "180-240 words across all fields"
}`;

const RACE_OVERVIEW_SYSTEM_PROMPT = `You are a senior racing analyst for a major Australian racing publication.
You have covered Australian thoroughbred racing for 30 years, from country maidens to Group 1 features.
You write the race-level overview that serious punters read before they study individual runners.

Your job is to do three things:
1. Map the race tactically: pace, pressure, barriers, clashes, and likely settling order
2. Explain what that race shape means: which profiles are suited, which profiles are compromised
3. Make a betting call: commit to how the race is run and who it favours

Your standards:
- Write with authority. No filler, no soft hedging, no generic racing cliches.
- Name the actual horses driving the pace and the horses likely to get the right or wrong run.
- Treat missing data as a limitation you must work around, not an excuse to be vague.
- If running styles are mostly unknown, infer the tactical picture from barriers, distance, market rank, form profile, and track shape.
- If betting is thin or unavailable, say that clearly and shift the focus to form and conditions.
- When the favourite is vulnerable, say why specifically. When the favourite is deserving, say why specifically.

Write exactly these six sections, in this order:
RACE MAP
PACE ANALYSIS
TRACK & CONDITIONS
FORM CYCLE
MARKET INTELLIGENCE
RACE PREDICTION

Each section must be 80-120 words, specific to this race, and dense with concrete detail.

Banned phrases:
- "It should be a good race"
- "All runners will need to be at their best"
- "The outcome is uncertain"
- "Anything can happen in racing"
- "Watch for [horse] to improve"
- "pace scenario unclear"
- "can't be ruled out"
- "hard to assess"
- "difficult to evaluate"`;

const RACE_SHAPE_SYSTEM_PROMPT = `You are STRIDE's race shape analyst. Your job is to produce a single precise paragraph that describes how this specific race is expected to physically unfold from barrier rise to finish line, based on the Running & Settling band distribution of the field.

You are describing race mechanics, not picking winners. Every sentence must be grounded in the band data provided. Nothing should be generic.

Reason through:
1. How many runners sit in bands 1-2 and which of them is best placed by barrier to lead.
2. Whether the bands 3-6 cluster applies enough pressure to force a genuine tempo or lets one horse control it.
3. Where the weight of the field sits: midfield-heavy, on-pace heavy, or closer-heavy.
4. Which barriers reinforce natural positions and which create position conflicts.
5. The structural tension point in the race: the first 400m battle, the mid-race breather, or the final 200m convergence.

Output rules:
- 80-120 words
- Three phases: first 400m, middle section, home straight
- Name actual horses when explaining the leader line or pressure line
- State whether the shape favours on-pace or off-pace runners, and why
- No tipping language
- No filler or generic openers`;

const BANNED_PHRASES = [
  "pace scenario unclear",
  "about correct odds",
  "would need a significant upset",
  "no realistic hope",
  "can't be dismissed",
  "watch for improvement",
  "hard to assess",
  "difficult to evaluate",
];

const RACE_OVERVIEW_BANNED_PHRASES = [
  "it should be a good race",
  "all runners will need to be at their best",
  "the outcome is uncertain",
  "anything can happen in racing",
  "watch for improvement",
  "pace scenario unclear",
  "can't be ruled out",
  "hard to assess",
  "difficult to evaluate",
];

const RACE_OVERVIEW_SECTION_ORDER = [
  "RACE MAP",
  "PACE ANALYSIS",
  "TRACK & CONDITIONS",
  "FORM CYCLE",
  "MARKET INTELLIGENCE",
  "RACE PREDICTION",
] as const;

const LEGACY_GENERATED_FIELDS = new Set([
  "ai_insight",
  "brief_assessment",
  "formanalystinsights",
  "modelsummary",
  "reasons",
]);

const TRACK_CONFIGS: Record<string, string> = {
  ascot: "Left-handed, roomy turns, long straight",
  belmont: "Left-handed, spacious circuit, long home straight",
  caulfield: "Left-handed, tighter turns, tactical position matters",
  doomben: "Left-handed, tight turning circuit, on-pace runners often hold an edge",
  "eagle farm": "Left-handed, roomy layout, long straight suits horses building momentum",
  flemington: "Left-handed, sweeping turns, very long straight rewards strong finishers",
  "gold coast": "Left-handed, turning track, barriers can matter over shorter trips",
  "kembla grange": "Left-handed, fair circuit with a long enough straight to build into the race",
  mooneevalley: "Left-handed, tight turning circuit, barrier and tactical speed are critical",
  "moonee valley": "Left-handed, tight turning circuit, barrier and tactical speed are critical",
  morphettville: "Left-handed, roomy enough, long straight gives backmarkers some chance",
  newcastle: "Left-handed, turning track, position can matter before the straight",
  randwick: "Right-handed, sweeping turns, long straight gives runners time to wind up",
  rosehill: "Right-handed, tighter than Randwick, tactical position and barriers matter more",
  sandown: "Left-handed, spacious track, longer straight than most metro circuits",
  "sandown hillside": "Left-handed, spacious track, long straight and sweeping turns",
  "sandown lakeside": "Left-handed, tighter than Hillside, barriers and early position matter",
  "sportsbet sandown": "Left-handed, spacious track, long straight and sweeping turns",
  "sportsbet sandown hillside": "Left-handed, spacious track, long straight and sweeping turns",
  "warwick farm": "Left-handed, flat circuit with a short run to some bends, tactical position matters",
};

type MaybeNumber = number | null;

interface RunnerAnalysisRequest {
  track: string;
  raceNumber: number;
  raceDate: string;
  horseName: string;
  currentOdds?: number | null;
  force?: boolean;
}

interface RaceOverviewRequest {
  track: string;
  raceNumber: number;
  raceDate: string;
  force?: boolean;
}

export interface RunnerAnalysisSections {
  profile: string;
  pace: string;
  condition: string;
  verdict: string;
  structured?: StrideAnalysisResult;
  qualityFlag?: string;
  cached?: boolean;
}

interface StrideAnalysisResult {
  horse: string;
  verdict: "BACK" | "AGAINST" | "VALUE WATCH" | "NEEDS SCENARIO";
  confidence: "HIGH" | "MEDIUM" | "LOW";
  value_flag: boolean;
  core_signal: string;
  race_shape_assessment: string;
  form_depth: string;
  class_and_opposition: string;
  fitness_signal: string;
  market_read: string;
  primary_risk: string;
  secondary_risk: string;
  win_condition: string;
  token_count_target: string;
}

export interface RaceOverviewSections {
  raceMap: string;
  paceAnalysis: string;
  trackConditions: string;
  formCycle: string;
  marketIntelligence: string;
  racePrediction: string;
  qualityFlag?: string;
  cached?: boolean;
}

type RaceShapeBandSource = "calculated" | "estimated";
type RaceShapeTempo = "HOT" | "GENUINE" | "SOFT" | "UNKNOWN";
type RaceShapeZoneKey =
  | "leader"
  | "on_pace"
  | "on_pace_wide"
  | "midfield"
  | "back_half"
  | "backmarker";

interface RaceShapeZone {
  key: RaceShapeZoneKey;
  label: string;
  bandRange: string;
  colorKey: "red" | "orange" | "amber" | "green" | "blue" | "purple";
}

interface RaceShapeRunner {
  horse: string;
  barrier: number | null;
  jockey: string | null;
  weight: number | null;
  band: number;
  bandSource: RaceShapeBandSource;
  sampleCount: number;
  avg800mPct: number | null;
  zone: RaceShapeZoneKey;
  paceRole: "Leader" | "On-Pace" | "On-Pace Wide" | "Midfield" | "Back Half" | "Backmarker";
  reasonSummary: string;
  positionConflict: boolean;
  positionConflictReason: string | null;
  tempoAdvantage: boolean;
  tempoAdvantageReason: string;
}

export interface RaceShapeBlock {
  track: string;
  distance: number | null;
  condition: string | null;
  rail: string | null;
  trackBias: string | null;
  tempoLabel: RaceShapeTempo;
  tempoReason: string;
  leaderCount: number;
  pressureCount: number;
  leaders: number;
  onPace: number;
  midfield: number;
  closers: number;
  zones: RaceShapeZone[];
  runners: RaceShapeRunner[];
  narrative: string;
}

interface RaceOverviewPayload {
  overview: RaceOverviewSections;
  raceShape: RaceShapeBlock;
}

interface HistoricalBandSample {
  raceDate: string;
  fieldSize: number;
  positionAt800: number;
  percentileAt800: number;
  finishPosition: number | null;
  weightCarried: number | null;
}

interface HistoricalBandDbRow {
  horse_name: string;
  race_date: string;
  track: string;
  race_number: number | null;
  horse_id: string | null;
  field_size: number | null;
  position: number | null;
  weight_kg: number | null;
  splits_json: unknown;
  sectional_created_at: string | Date | null;
}

interface AnalysisCacheEntry {
  analysis: RunnerAnalysisSections;
  currentOdds: MaybeNumber;
  generatedAt: number;
}

interface RaceOverviewCacheEntry {
  payload: RaceOverviewPayload;
  generatedAt: number;
}

interface RaceShapeCacheEntry {
  raceShape: RaceShapeBlock;
  generatedAt: number;
}

interface PreparedRaceCacheEntry {
  prepared: PreparedRace;
  createdAt: number;
}

interface PreparedRace {
  race: PreparedRaceContext;
  runners: PreparedRunner[];
}

interface PreparedRaceContext {
  track: string;
  raceName: string;
  raceNumber: number;
  raceDate: string;
  raceTime: string;
  distanceMetres: number | null;
  distanceLabel: string;
  going: string;
  raceClass: string;
  fieldSize: number;
  railPosition: string;
  trackConfig: string;
  weatherConditions: string;
  prizeMoney: MaybeNumber;
  weightType: string;
  ageRestriction: string;
  sexRestriction: string;
  likelyPaceScenario: string;
  topWeight: MaybeNumber;
  bottomWeight: MaybeNumber;
  rawRaceData: Record<string, unknown>;
}

interface PreparedRunner {
  horseId: string | null;
  horseName: string;
  normalizedHorseName: string;
  saddleClothNumber: number | null;
  barrier: number | null;
  currentOdds: MaybeNumber;
  marketRank: number | null;
  runningStyle: string;
  modelRunner: Record<string, unknown> | null;
  racecardRunner: Record<string, unknown> | null;
  mergedRunner: Record<string, unknown>;
  statsSummary: Record<string, unknown>;
}

interface RivalSummary {
  name: string;
  barrier: number | null;
  odds: MaybeNumber;
  marketRank: number | null;
  runningStyle: string;
  recentForm: string;
  lastRaceClass: string;
  keyFact: string;
  raw: Record<string, unknown>;
}

const analysisCache = new Map<string, AnalysisCacheEntry>();
const analysisInFlight = new Map<string, Promise<RunnerAnalysisSections>>();
const raceOverviewCache = new Map<string, RaceOverviewCacheEntry>();
const raceShapeCache = new Map<string, RaceShapeCacheEntry>();
const preparedRaceCache = new Map<string, PreparedRaceCacheEntry>();
const raceOverviewInFlight = new Map<string, Promise<RaceOverviewPayload>>();
const raceShapeInFlight = new Map<string, Promise<RaceShapeBlock>>();
let horseResultsAccessState: "unknown" | "available" | "unavailable" = "unknown";

export async function generateRunnerAnalysis(
  request: RunnerAnalysisRequest,
): Promise<RunnerAnalysisSections> {
  const cacheKey = getAnalysisCacheKey(request);
  const cached = analysisCache.get(cacheKey);

  if (
    cached &&
    !request.force &&
    !oddsChangedSignificantly(cached.currentOdds, request.currentOdds ?? null)
  ) {
    return { ...cached.analysis, cached: true };
  }

  if (!request.force) {
    const inFlight = analysisInFlight.get(cacheKey);
    if (inFlight) {
      return inFlight;
    }
  }

  const task = generateRunnerAnalysisInternal(request, cacheKey, cached);
  analysisInFlight.set(cacheKey, task);

  try {
    return await task;
  } finally {
    analysisInFlight.delete(cacheKey);
  }
}

async function generateRunnerAnalysisInternal(
  request: RunnerAnalysisRequest,
  cacheKey: string,
  cached: AnalysisCacheEntry | undefined,
): Promise<RunnerAnalysisSections> {
  const prepared = loadPreparedRace(request.track, request.raceNumber, request.raceDate);
  primeRaceOverviewGeneration({
    track: request.track,
    raceNumber: request.raceNumber,
    raceDate: request.raceDate,
  }, prepared);
  const runner = prepared.runners.find(
    (entry) => entry.normalizedHorseName === normalizeName(request.horseName),
  );

  if (!runner) {
    throw new Error(`Runner not found: ${request.horseName}`);
  }

  const rivals = buildRivals(prepared, runner);
  const overviewContext = await getRaceOverviewContext(prepared);
  let analysis: RunnerAnalysisSections;

  try {
    const prompt = buildGroqPrompt(prepared, runner, rivals, overviewContext);
    const generated = await generateWithValidation(prompt, runner.horseName);
    analysis = generated.qualityFlag
      ? buildFallbackRunnerAnalysis(prepared, runner, rivals, overviewContext, generated.qualityFlag)
      : generated;
  } catch (error) {
    if (cached) {
      return { ...cached.analysis, cached: true };
    }

    analysis = buildFallbackRunnerAnalysis(
      prepared,
      runner,
      rivals,
      overviewContext,
      error instanceof Error ? error.message : "Groq analysis failed",
    );
  }

  analysisCache.set(cacheKey, {
    analysis,
    currentOdds: runner.currentOdds,
    generatedAt: Date.now(),
  });

  return analysis;
}

export async function generateRaceOverview(
  request: RaceOverviewRequest,
): Promise<RaceOverviewPayload> {
  const prepared = loadPreparedRace(request.track, request.raceNumber, request.raceDate);
  const cacheKey = getRaceOverviewCacheKey(prepared);
  const raceShapeCacheKey = getRaceShapeCacheKey(prepared);

  if (request.force) {
    raceOverviewCache.delete(cacheKey);
    raceShapeCache.delete(raceShapeCacheKey);
    raceOverviewInFlight.delete(cacheKey);
    raceShapeInFlight.delete(raceShapeCacheKey);
  }

  const cached = raceOverviewCache.get(cacheKey);

  if (cached && !request.force) {
    return {
      overview: { ...cached.payload.overview, cached: true },
      raceShape: cached.payload.raceShape,
    };
  }

  if (!request.force) {
    const inFlight = raceOverviewInFlight.get(cacheKey);
    if (inFlight) {
      return inFlight;
    }
  }

  const task = generateRaceOverviewWithPrepared(prepared, cacheKey);
  raceOverviewInFlight.set(cacheKey, task);

  try {
    return await task;
  } finally {
    raceOverviewInFlight.delete(cacheKey);
  }
}

async function generateRaceOverviewWithPrepared(
  prepared: PreparedRace,
  cacheKey: string,
) : Promise<RaceOverviewPayload> {
  const raceShape = await getOrBuildRaceShape(prepared);
  const prompt = buildRaceOverviewPrompt(prepared, raceShape);
  const overview = await generateRaceOverviewWithValidation(prompt);
  const payload: RaceOverviewPayload = { overview, raceShape };

  raceOverviewCache.set(cacheKey, {
    payload,
    generatedAt: Date.now(),
  });

  return payload;
}

function primeRaceOverviewGeneration(
  request: RaceOverviewRequest,
  prepared?: PreparedRace,
): void {
  const resolvedPrepared = prepared || loadPreparedRace(request.track, request.raceNumber, request.raceDate);
  const cacheKey = getRaceOverviewCacheKey(resolvedPrepared);
  if (raceOverviewCache.has(cacheKey) || raceOverviewInFlight.has(cacheKey)) {
    return;
  }

  const promise = generateRaceOverviewWithPrepared(
    resolvedPrepared,
    cacheKey,
  ).catch((error) => {
    console.warn("Race overview prime warning:", error instanceof Error ? error.message : error);
    throw error;
  });

  raceOverviewInFlight.set(cacheKey, promise);
  void promise.catch(() => undefined).finally(() => {
    raceOverviewInFlight.delete(cacheKey);
  });
}

async function getRaceOverviewContext(prepared: PreparedRace): Promise<RaceOverviewPayload> {
  const cacheKey = getRaceOverviewCacheKey(prepared);
  const cached = raceOverviewCache.get(cacheKey)?.payload;
  if (cached) {
    return cached;
  }

  const raceShape = await getOrBuildRaceShape(prepared);
  return {
    overview: buildDerivedRaceOverviewContext(prepared, raceShape),
    raceShape,
  };
}

function loadPreparedRace(track: string, raceNumber: number, raceDate: string): PreparedRace {
  const cacheKey = `${raceDate}|${normalizeTrackName(track)}|${raceNumber}`;
  const cached = preparedRaceCache.get(cacheKey);
  if (cached && Date.now() - cached.createdAt < PREPARED_RACE_CACHE_MS) {
    return cached.prepared;
  }

  const prepared = prepareRace(track, raceNumber, raceDate);
  preparedRaceCache.set(cacheKey, { prepared, createdAt: Date.now() });
  return prepared;
}

function prepareRace(track: string, raceNumber: number, raceDate: string): PreparedRace {
  const tipsPath = path.join(process.cwd(), "racecards", `tips_${raceDate}.json`);
  const racecardPath = path.join(process.cwd(), "racecards", `racecard_${raceDate}.json`);

  const tipsData = readJsonFile(tipsPath);
  const racecardData = readJsonFile(racecardPath);

  const tipsRace = findTipsRace(tipsData, track, raceNumber);
  const racecardRace = findRacecardRace(racecardData, track, raceNumber);

  if (!tipsRace && !racecardRace) {
    throw new Error(`Race not found: ${track} R${raceNumber} on ${raceDate}`);
  }

  const modelRunners = Array.isArray(tipsRace?.full_field)
    ? (tipsRace.full_field as Record<string, unknown>[])
    : [];
  const cardRunnersRaw = Array.isArray(racecardRace?.runners)
    ? (racecardRace.runners as Record<string, unknown>[])
    : [];
  const cardRunners = cardRunnersRaw.filter(
    (runner) => !Boolean(runner.scratched),
  );

  const combined = mergeRunners(modelRunners, cardRunners, raceDate);
  const activeRunners = combined.filter((runner) => runner.horseName);

  if (activeRunners.length === 0) {
    throw new Error(`No active runners found for ${track} R${raceNumber} on ${raceDate}`);
  }

  const rankedRunners = assignMarketRanks(activeRunners);
  const topWeight = maxByNumber(rankedRunners.map((runner) => toNumber(runner.mergedRunner.weightCarried)));
  const bottomWeight = minByNumber(rankedRunners.map((runner) => toNumber(runner.mergedRunner.weightCarried)));

  const race = buildRaceContext({
    track,
    raceNumber,
    raceDate,
    tipsRace,
    racecardRace,
    runners: rankedRunners,
    topWeight,
    bottomWeight,
  });

  return {
    race,
    runners: rankedRunners,
  };
}

function buildRaceContext(input: {
  track: string;
  raceNumber: number;
  raceDate: string;
  tipsRace: Record<string, unknown> | null;
  racecardRace: Record<string, unknown> | null;
  runners: PreparedRunner[];
  topWeight: MaybeNumber;
  bottomWeight: MaybeNumber;
}): PreparedRaceContext {
  const { track, raceNumber, raceDate, tipsRace, racecardRace, runners, topWeight, bottomWeight } = input;

  const raceName = firstString(
    tipsRace?.race_name,
    racecardRace?.race_name,
    `Race ${raceNumber}`,
  );
  const distanceLabel = firstString(
    tipsRace?.distance,
    racecardRace?.distance,
    "Unknown",
  );
  const distanceMetres = parseDistanceMetres(distanceLabel);
  const going = firstString(
    tipsRace?.going,
    racecardRace?.going,
    "Unknown",
  );
  const raceClass = firstString(
    tipsRace?.race_class,
    tipsRace?.class,
    racecardRace?.class,
    "Unknown",
  );
  const fieldSize = runners.length;
  const trackConfig = getTrackConfig(track);
  const raceTime = formatRaceTime(firstString(racecardRace?.off_time, tipsRace?.off_time, ""));
  const weightType = deriveWeightType(raceClass);
  const ageRestriction = deriveAgeRestriction(raceClass, runners);
  const sexRestriction = deriveSexRestriction(runners);
  const likelyPaceScenario = deriveLikelyPaceScenario(runners);
  const prizeMoney = toNumber(racecardRace?.prize_total);

  return {
    track,
    raceName,
    raceNumber,
    raceDate,
    raceTime,
    distanceMetres,
    distanceLabel,
    going,
    raceClass,
    fieldSize,
    railPosition: "Unavailable",
    trackConfig,
    weatherConditions: "Unavailable",
    prizeMoney,
    weightType,
    ageRestriction,
    sexRestriction,
    likelyPaceScenario,
    topWeight,
    bottomWeight,
    rawRaceData: {
      tipsRace: stripLargeRaceArrays(tipsRace),
      racecardRace: stripLargeRaceArrays(racecardRace),
    },
  };
}

function buildRivals(prepared: PreparedRace, runner: PreparedRunner): RivalSummary[] {
  const sortedByRank = [...prepared.runners]
    .filter((entry) => entry.normalizedHorseName !== runner.normalizedHorseName)
    .sort((a, b) => compareMarketRank(a.marketRank, b.marketRank));

  const limit = prepared.runners.length <= 3 ? sortedByRank.length : 5;
  return sortedByRank.slice(0, limit).map((entry) => {
    const stats = entry.statsSummary;
    const keyFact = deriveRivalKeyFact(entry);
    return {
      name: entry.horseName,
      barrier: entry.barrier,
      odds: entry.currentOdds,
      marketRank: entry.marketRank,
      runningStyle: stringifyValue(entry.mergedRunner.runningStyle) || "Unknown",
      recentForm: stringifyValue(entry.mergedRunner.formString) || "No exposed form",
      lastRaceClass: stringifyValue(entry.mergedRunner.currentRaceClass) || "Unknown",
      keyFact,
      raw: {
        modelRunner: entry.modelRunner,
        racecardRunner: entry.racecardRunner,
        mergedRunner: entry.mergedRunner,
        statsSummary: stats,
      },
    };
  });
}

function buildGroqPrompt(
  prepared: PreparedRace,
  runner: PreparedRunner,
  rivals: RivalSummary[],
  context: RaceOverviewPayload,
): string {
  const race = prepared.race;
  const merged = runner.mergedRunner;
  const stats = runner.statsSummary;
  const currentOdds = toNumber(merged.currentOdds);
  const marketRank = toNumber(merged.marketRank);
  const age = stringifyValue(merged.horseAge) || "Unknown age";
  const sex = stringifyValue(merged.horseSex) || "Unknown sex";
  const weight = toNumber(merged.weightCarried);
  const bestAvailable = toNumber(merged.bestAvailableOdds);
  const formString = stringifyValue(merged.formString) || "No exposed form";
  const runningStyle = stringifyValue(merged.runningStyle) || "Unknown";
  const runningStyleConfidence = stringifyValue(merged.runningStyleConfidence) || "Low";
  const daysSinceLastRun = stringifyValue(merged.daysSinceLastRun) || "Unavailable";
  const workReports = stringifyValue(merged.workReports) || "Unavailable";
  const gearChanges = stringifyList(merged.gearChanges) || "Unavailable";
  const currentGear = stringifyList(merged.currentGear) || "Unavailable";
  const marketMovement = stringifyValue(merged.marketMovement) || "Unavailable";
  const openingOdds = stringifyValue(merged.openingOdds) || "Unavailable";
  const classChange = stringifyValue(merged.classChange) || "Unavailable";
  const lastRaceClass = stringifyValue(merged.lastRaceClass) || "Unavailable";
  const currentRaceClass = stringifyValue(merged.currentRaceClass) || race.raceClass;
  const careerWinPercent = toNumber(merged.careerWinPercent);
  const careerPlacePercent = toNumber(merged.careerPlacePercent);
  const careerPrize = toNumber(merged.careerPrize);
  const strideInput = buildStrideInputPayload(prepared, runner, rivals, context);
  const raceOverview = context.overview;
  const runnerRaceShape = getRunnerRaceShapeContext(context.raceShape, runner.horseName);

  const fieldSnapshot = buildFieldSnapshot(race, runner, rivals);
  const rivalsText = rivals.length > 0
    ? rivals
        .map(
          (rival) =>
            `${rival.name}: Barrier ${displayNumber(rival.barrier)}, ${displayOdds(rival.odds)}, ${rival.runningStyle}, Form: ${rival.recentForm}. ${rival.keyFact}`,
        )
        .join("\n")
    : "No rivals summary available.";

  const raceOverviewContext = `RACE CONTEXT FROM OVERVIEW:
Pace scenario: ${raceOverview.paceAnalysis}
Race map: ${raceOverview.raceMap}`;
  const raceShapeContext = runnerRaceShape
    ? `RACE SHAPE CONTEXT:
Tempo: ${context.raceShape.tempoLabel} (${context.raceShape.tempoReason})
Shared narrative: ${context.raceShape.narrative}
${runner.horseName}: band ${runnerRaceShape.band} (${runnerRaceShape.paceRole}), source ${runnerRaceShape.bandSource}, conflict ${runnerRaceShape.positionConflict ? runnerRaceShape.positionConflictReason : "none"}, tempo advantage ${runnerRaceShape.tempoAdvantage ? "yes" : "no"} - ${runnerRaceShape.tempoAdvantageReason}`
    : `RACE SHAPE CONTEXT:
Tempo: ${context.raceShape.tempoLabel} (${context.raceShape.tempoReason})
Shared narrative: ${context.raceShape.narrative}`;

  return `Use the normalized STRIDE input first. If a field is null or thin, treat that as missing evidence and use the raw payloads to understand the betting consequence.

STRIDE_INPUT_JSON:
${JSON.stringify(strideInput, null, 2)}

SUPPLEMENTAL RACE CONTEXT:
${race.raceName} | ${race.track} | ${race.distanceMetres ?? race.distanceLabel}m | ${race.going} | ${race.raceClass}
Field: ${race.fieldSize} runners | Rail: ${race.railPosition} | Track config: ${race.trackConfig}
Weight type: ${race.weightType} | Likely pace: ${race.likelyPaceScenario}
Race time/date: ${race.raceTime} | ${race.raceDate}
Weather: ${race.weatherConditions}
Prize money: ${race.prizeMoney != null ? `$${race.prizeMoney.toLocaleString("en-AU")}` : "Unavailable"}
Age restriction: ${race.ageRestriction} | Sex restriction: ${race.sexRestriction}

RUNNER TO ANALYSE:
${runner.horseName} | Barrier ${displayNumber(runner.barrier)}/${race.fieldSize} | ${age} ${sex}
Jockey: ${stringifyValue(merged.jockey) || "Unavailable"}
Trainer: ${stringifyValue(merged.trainer) || "Unavailable"}
Weight: ${displayWeight(weight)} (top weight ${displayWeight(race.topWeight)}, bottom ${displayWeight(race.bottomWeight)})
Market: ${displayOdds(currentOdds)} | Opened: ${openingOdds} | Best available: ${displayOdds(bestAvailable)} | Movement: ${marketMovement}
Market rank: ${marketRank ?? "Unrated"}/${race.fieldSize}
Model view: Win ${displayPercent(toNumber(merged.modelWinPct))} | Place ${displayPercent(toNumber(merged.modelPlacePct))} | Edge ${displaySignedPercent(toNumber(merged.edgePct))} | Selection score ${displayNumber(toNumber(merged.selectionScore))}
Form string: ${formString}
Running style: ${runningStyle} (${runningStyleConfidence} confidence)
Debutant: ${merged.isDebutant ? "Yes" : "No"}
Days since last run: ${daysSinceLastRun}
Runs this prep: ${displayNumber(toNumber(merged.runsThisPrep))}
Spell length: ${displayWeeks(toNumber(merged.spellLengthWeeks))}
Last raced: ${stats.lastRaced}
Career: Win ${displayPercent(careerWinPercent)} | Place ${displayPercent(careerPlacePercent)} | Prize ${careerPrize != null ? `$${careerPrize.toLocaleString("en-AU")}` : "Unavailable"}
Track record: ${stats.trackRecord}
Distance record: ${stats.distanceRecord}
Course-distance record: ${stats.courseDistanceRecord}
Going record today: ${stats.goingRecord}
Jockey at this course: ${stats.jockeyRecord}
Breeding: Sire ${stringifyValue(merged.sire) || "Unavailable"} | Dam ${stringifyValue(merged.dam) || "Unavailable"} | Dam sire ${stringifyValue(merged.damSire) || "Unavailable"}
Gear changes: ${gearChanges}
Current gear: ${currentGear}
Gear history: ${stringifyValue(merged.gearHistory) || "Unavailable"}
Class: ${lastRaceClass} -> ${currentRaceClass} (${classChange})
Public work/trial data: ${workReports}

FIELD SNAPSHOT:
${fieldSnapshot}

KEY RIVALS FOR CONTEXT:
${rivalsText}

${raceOverviewContext}

${raceShapeContext}

RAW RACE DATA:
${JSON.stringify(race.rawRaceData, null, 2)}

RAW RUNNER MODEL DATA:
${JSON.stringify(sanitizePromptPayload(runner.modelRunner), null, 2)}

RAW RUNNER RACECARD DATA:
${JSON.stringify(sanitizePromptPayload(runner.racecardRunner), null, 2)}

RAW MERGED RUNNER DATA:
${JSON.stringify(sanitizePromptPayload(runner.mergedRunner), null, 2)}

RAW RIVALS:
${JSON.stringify(rivals.map((rival) => sanitizePromptPayload(rival.raw)), null, 2)}

---
Return one JSON object only for ${runner.horseName}.
Make the analysis unreproducible: every sentence must anchor to this horse's barrier, map, price, rivals, prep, or track setup.
Name the actual rival in the risk fields when possible.
Keep the total response in the 180-240 word range across all fields.
Do not return labelled prose sections. Do not wrap the JSON in markdown.`;
}

function buildStrideInputPayload(
  prepared: PreparedRace,
  runner: PreparedRunner,
  rivals: RivalSummary[],
  context: RaceOverviewPayload,
): Record<string, unknown> {
  const race = prepared.race;
  const merged = runner.mergedRunner;
  const racecardRunner = toRecord(runner.racecardRunner);
  const stats = toRecord(racecardRunner.stats);
  const courseStats = toRecord(stats.course_stats);
  const distanceStats = toRecord(stats.distance_stats);
  const softStats = toRecord(stats.ground_soft_stats);
  const heavyStats = toRecord(stats.ground_heavy_stats);
  const raceShapeContext = getRunnerRaceShapeContext(context.raceShape, runner.horseName);

  return {
    race: {
      name: race.raceName,
      track: race.track,
      distance: race.distanceMetres ?? race.distanceLabel,
      condition: nullIfUnavailable(race.going),
      rail: nullIfUnavailable(race.railPosition),
      grade: nullIfUnavailable(race.raceClass),
      prize_money: race.prizeMoney,
      field_size: race.fieldSize,
      pace_map: buildStridePaceMap(context.raceShape),
      track_bias_today: context.raceShape.trackBias,
      track_bias_confidence: null,
      meeting_context: buildMeetingContext(race, context.overview),
    },
    horse: {
      name: runner.horseName,
      age: toNumber(merged.horseAge),
      sex: nullIfUnavailable(stringifyValue(merged.horseSex)),
      barrier: runner.barrier,
      weight: toNumber(merged.weightCarried),
      weight_change: null,
      jockey: nullIfUnavailable(stringifyValue(merged.jockey)),
      trainer: nullIfUnavailable(stringifyValue(merged.trainer)),
      days_since_last_run: toNumber(merged.daysSinceLastRun),
      spell_length_runs_back: toNumber(merged.spellLengthWeeks),
      runs_this_prep: toNumber(merged.runsThisPrep),
      class_change: nullIfUnavailable(stringifyValue(merged.classChange)),
      gear_changes: toStringArray(merged.gearChanges),
      breeding: {
        sire: nullIfUnavailable(stringifyValue(merged.sire)),
        dam_sire: nullIfUnavailable(stringifyValue(merged.damSire)),
        wet_track_breeding: null,
        stamina_index: null,
      },
    },
    form: {
      last_6_starts: [],
      track_record: statRecordObject(courseStats),
      distance_record: statRecordObject(distanceStats),
      wet_track_record: mergeStatRecordObjects(statRecordObject(softStats), statRecordObject(heavyStats)),
      first_up_record: null,
      second_up_record: null,
      class_record: null,
      best_sectional_last_600: null,
      average_sectional_last_600: null,
      weight_adjusted_rating: firstNumber(merged.weightAdjustedRating, merged.weightedFormScore, merged.selectionScore, null),
    },
    jockey_trainer: {
      combination_strike_rate: null,
      combination_roi: null,
      jockey_track_strike_rate: null,
      trainer_second_up_strike_rate: null,
      trainer_group1_strike_rate: null,
      trainer_gear_change_strike_rate: null,
      market_intent_signal: deriveMarketIntentSignal(merged),
    },
    market: {
      opening_price: toNumber(merged.openingOdds),
      current_price: toNumber(merged.currentOdds),
      price_movement: nullIfUnavailable(stringifyValue(merged.marketMovement)),
      market_rank: toNumber(merged.marketRank),
      corroboration_score: null,
      tipster_mentions: (merged.isTipped as boolean) ? 1 : 0,
      tipster_sources: [],
      stride_score: firstNumber(merged.aiScore, null),
      value_flag: (toNumber(merged.edgePct) ?? 0) > 0,
    },
    race_shape: raceShapeContext
      ? {
          predicted_tempo: context.raceShape.tempoLabel,
          leader_count: context.raceShape.leaderCount,
          on_pace_count: context.raceShape.onPace,
          closer_count: context.raceShape.closers,
          track_bias: context.raceShape.trackBias,
          this_horse_band: raceShapeContext.band,
          this_horse_role: raceShapeContext.paceRole,
          this_horse_position_conflict: raceShapeContext.positionConflict,
          position_conflict_reason: raceShapeContext.positionConflictReason,
          tempo_advantage: raceShapeContext.tempoAdvantage,
          tempo_advantage_reason: raceShapeContext.tempoAdvantageReason,
        }
      : null,
    opposition: rivals.slice(0, 3).map((rival) => ({
      name: rival.name,
      barrier: rival.barrier,
      weight: null,
      pace_map_role: normalizeRunningStyleForOverview(rival.runningStyle),
      stride_score: null,
      market_rank: rival.marketRank,
      key_threat: rival.keyFact,
    })),
    model_context: {
      win_probability_pct: toNumber(merged.modelWinPct),
      place_probability_pct: toNumber(merged.modelPlacePct),
      raw_model_probability_pct: toNumber(merged.rawModelPct),
      edge_pct: toNumber(merged.edgePct),
      selection_score: toNumber(merged.selectionScore),
      confidence: nullIfUnavailable(stringifyValue(merged.confidence)),
      running_style: nullIfUnavailable(stringifyValue(merged.runningStyle)),
      running_style_confidence: nullIfUnavailable(stringifyValue(merged.runningStyleConfidence)),
      weighted_form_score: toNumber(merged.weightedFormScore),
      is_improving: merged.isImproving === true,
      course_strike_rate: toNumber(merged.courseStrikeRate),
      distance_strike_rate: toNumber(merged.distanceStrikeRate),
      track_record_text: nullIfUnavailable(stringifyValue(runner.statsSummary.trackRecord)),
      distance_record_text: nullIfUnavailable(stringifyValue(runner.statsSummary.distanceRecord)),
      going_record_text: nullIfUnavailable(stringifyValue(runner.statsSummary.goingRecord)),
      field_snapshot: fieldSnapshotFromPrepared(prepared.runners),
      pace_overview: context.overview.paceAnalysis,
      track_overview: context.overview.trackConditions,
      market_overview: context.overview.marketIntelligence,
      race_prediction: context.overview.racePrediction,
      race_shape_narrative: context.raceShape.narrative,
    },
  };
}

function buildStridePaceMap(raceShape: RaceShapeBlock) {
  const leaders: string[] = [];
  const onPace: string[] = [];
  const midfield: string[] = [];
  const back: string[] = [];

  for (const entry of raceShape.runners) {
    switch (entry.paceRole) {
      case "Leader":
        leaders.push(entry.horse);
        break;
      case "On-Pace":
      case "On-Pace Wide":
        onPace.push(entry.horse);
        break;
      case "Midfield":
        midfield.push(entry.horse);
        break;
      case "Back Half":
      case "Backmarker":
        back.push(entry.horse);
        break;
      default:
        break;
    }
  }

  return {
    leaders,
    on_pace: onPace,
    midfield,
    back,
    predicted_tempo: raceShape.tempoLabel.toLowerCase(),
    leader_count: leaders.length,
  };
}

const RACE_SHAPE_ZONES: RaceShapeZone[] = [
  { key: "leader", label: "Leader", bandRange: "1-2", colorKey: "red" },
  { key: "on_pace", label: "On-Pace", bandRange: "3-4", colorKey: "orange" },
  { key: "on_pace_wide", label: "On-Pace Wide", bandRange: "5-6", colorKey: "amber" },
  { key: "midfield", label: "Midfield", bandRange: "7-8", colorKey: "green" },
  { key: "back_half", label: "Back Half", bandRange: "9-10", colorKey: "blue" },
  { key: "backmarker", label: "Backmarker", bandRange: "11-12", colorKey: "purple" },
];

async function getOrBuildRaceShape(prepared: PreparedRace): Promise<RaceShapeBlock> {
  const cacheKey = getRaceShapeCacheKey(prepared);
  const cached = raceShapeCache.get(cacheKey);
  if (cached) {
    return cached.raceShape;
  }

  const inFlight = raceShapeInFlight.get(cacheKey);
  if (inFlight) {
    return inFlight;
  }

  const task = buildRaceShape(prepared);
  raceShapeInFlight.set(cacheKey, task);

  try {
    const raceShape = await task;
    raceShapeCache.set(cacheKey, { raceShape, generatedAt: Date.now() });
    return raceShape;
  } finally {
    raceShapeInFlight.delete(cacheKey);
  }
}

async function buildRaceShape(prepared: PreparedRace): Promise<RaceShapeBlock> {
  const databaseSamplesByRunner = await loadHistoricalBandSamples(prepared);
  const sampled = await Promise.all(
    prepared.runners.map(async (entry) => {
      const localSamples = databaseSamplesByRunner.get(entry.normalizedHorseName) ?? [];
      const samples = localSamples.length > 0
        ? localSamples
        : await loadHistoricalBandSamplesFromApi(entry, prepared.race.raceDate);
      const calculated = buildBandFromSamples(entry, samples);
      return {
        entry,
        ...calculated,
      };
    }),
  );

  const leaderCount = sampled.filter((runner) => runner.band <= 2).length;
  const pressureCount = sampled.filter((runner) => runner.band >= 3 && runner.band <= 4).length;
  const tempoLabel = deriveRaceShapeTempo(leaderCount, pressureCount);
  const tempoReason = describeRaceShapeTempo(tempoLabel, leaderCount, pressureCount, sampled);
  const leaders = sampled.filter((runner) => runner.band <= 2).length;
  const onPace = sampled.filter((runner) => runner.band <= 4).length;
  const midfield = sampled.filter((runner) => runner.band >= 5 && runner.band <= 8).length;
  const closers = sampled.filter((runner) => runner.band >= 9).length;
  const insideMostLeaderBarrier = minByNumber(
    sampled
      .filter((runner) => runner.band <= 2)
      .map((runner) => runner.entry.barrier),
  );

  const runners = sampled
    .map((runner) =>
      buildRaceShapeRunner({
        prepared,
        runner,
        tempoLabel,
        leaderCount,
        pressureCount,
        insideMostLeaderBarrier,
      }),
    )
    .sort((a, b) => {
      if (a.band !== b.band) {
        return a.band - b.band;
      }
      return compareNullableNumbers(a.barrier, b.barrier) || a.horse.localeCompare(b.horse);
    });

  const narrative = await generateRaceShapeNarrative(prepared, {
    track: prepared.race.track,
    distance: prepared.race.distanceMetres,
    condition: nullIfUnavailable(prepared.race.going),
    rail: nullIfUnavailable(prepared.race.railPosition),
    trackBias: deriveExplicitRaceTrackBias(prepared),
    tempoLabel,
    tempoReason,
    leaderCount,
    pressureCount,
    leaders,
    onPace,
    midfield,
    closers,
    zones: RACE_SHAPE_ZONES,
    runners,
    narrative: "",
  });

  return {
    track: prepared.race.track,
    distance: prepared.race.distanceMetres,
    condition: nullIfUnavailable(prepared.race.going),
    rail: nullIfUnavailable(prepared.race.railPosition),
    trackBias: deriveExplicitRaceTrackBias(prepared),
    tempoLabel,
    tempoReason,
    leaderCount,
    pressureCount,
    leaders,
    onPace,
    midfield,
    closers,
    zones: RACE_SHAPE_ZONES,
    runners,
    narrative,
  };
}

function buildBandFromSamples(
  runner: PreparedRunner,
  samples: HistoricalBandSample[],
): {
  band: number;
  bandSource: RaceShapeBandSource;
  sampleCount: number;
  avg800mPct: number | null;
  reasonSummary: string;
  bestRecentWinningWeight: number | null;
} {
  if (samples.length > 0) {
    const avg800mPct = samples.reduce((sum, sample) => sum + sample.percentileAt800, 0) / samples.length;
    const band = Math.max(1, Math.min(12, Math.ceil(avg800mPct / 8.33)));
    const bestRecentWinningWeight = minByNumber(
      samples
        .filter((sample) => sample.finishPosition === 1)
        .map((sample) => sample.weightCarried),
    );
    return {
      band,
      bandSource: "calculated",
      sampleCount: samples.length,
      avg800mPct: roundTo(avg800mPct, 1),
      reasonSummary: `Averaged ${roundTo(avg800mPct, 1)}% of field position at the 800m call across ${samples.length} measured starts.`,
      bestRecentWinningWeight,
    };
  }

  const band = estimatedBandFromRunningStyle(runner.runningStyle);
  return {
    band,
    bandSource: "estimated",
    sampleCount: 0,
    avg800mPct: null,
    reasonSummary: estimatedBandReasonSummary(runner.runningStyle),
    bestRecentWinningWeight: null,
  };
}

function buildRaceShapeRunner(input: {
  prepared: PreparedRace;
  runner: {
    entry: PreparedRunner;
    band: number;
    bandSource: RaceShapeBandSource;
    sampleCount: number;
    avg800mPct: number | null;
    reasonSummary: string;
    bestRecentWinningWeight: number | null;
  };
  tempoLabel: RaceShapeTempo;
  leaderCount: number;
  pressureCount: number;
  insideMostLeaderBarrier: number | null;
}): RaceShapeRunner {
  const { prepared, runner, tempoLabel, leaderCount, insideMostLeaderBarrier } = input;
  const barrierConflict = getBarrierConflictReason(runner.band, runner.entry.barrier, prepared.race.fieldSize);
  const leaderContest = getLeaderContestReason(
    runner.band,
    runner.entry.barrier,
    leaderCount,
    insideMostLeaderBarrier,
  );
  const tempoConflict = getTempoConflictReason(runner.band, tempoLabel);
  const weightConflict = getWeightConflictReason(runner, runner.entry);
  const positionConflictReason = [barrierConflict, leaderContest, tempoConflict, weightConflict]
    .filter((value): value is string => Boolean(value))
    .join(" ");
  const tempoAdvantage = deriveTempoAdvantage(runner.band, tempoLabel);

  return {
    horse: runner.entry.horseName,
    barrier: runner.entry.barrier,
    jockey: nullIfUnavailable(stringifyValue(runner.entry.mergedRunner.jockey)),
    weight: toNumber(runner.entry.mergedRunner.weightCarried),
    band: runner.band,
    bandSource: runner.bandSource,
    sampleCount: runner.sampleCount,
    avg800mPct: runner.avg800mPct,
    zone: zoneForBand(runner.band),
    paceRole: paceRoleForBand(runner.band),
    reasonSummary: runner.reasonSummary,
    positionConflict: Boolean(positionConflictReason),
    positionConflictReason: positionConflictReason || null,
    tempoAdvantage: tempoAdvantage.advantage,
    tempoAdvantageReason: tempoAdvantage.reason,
  };
}

async function loadHistoricalBandSamples(
  prepared: PreparedRace,
): Promise<Map<string, HistoricalBandSample[]>> {
  const runnerNames = Array.from(
    new Set(
      prepared.runners
        .map((runner) => runner.horseName.trim())
        .filter(Boolean),
    ),
  );
  if (runnerNames.length === 0) {
    return new Map();
  }

  const oneYearAgo = new Date(`${prepared.race.raceDate}T00:00:00Z`);
  oneYearAgo.setUTCDate(oneYearAgo.getUTCDate() - 365);

  try {
    const escapedNames = runnerNames
      .map((name) => `'${name.replace(/'/g, "''")}'`)
      .join(", ");
    const result = await db.execute(sql.raw(`
      SELECT
        rrh.horse_name,
        rrh.race_date,
        rrh.track,
        rrh.race_number,
        rrh.horse_id,
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
      WHERE rrh.race_date >= '${formatIsoDate(oneYearAgo)}'
        AND rrh.race_date < '${prepared.race.raceDate}'
        AND rrh.horse_name IN (${escapedNames})
      ORDER BY rrh.horse_name ASC, rrh.race_date DESC, st.created_at DESC NULLS LAST
    `));

    const rows = ((result.rows || []) as unknown) as HistoricalBandDbRow[];
    const dedupedByHorse = new Map<
      string,
      Map<string, { sample: HistoricalBandSample; createdAtMs: number }>
    >();

    for (const row of rows) {
      const normalizedHorse = normalizeName(row.horse_name);
      const sample = parseHistoricalBandSampleFromDatabase(row);
      if (!sample) {
        continue;
      }

      const horseMap = dedupedByHorse.get(normalizedHorse) || new Map<string, { sample: HistoricalBandSample; createdAtMs: number }>();
      const raceKey = [
        row.race_date,
        normalizeTrackName(row.track),
        row.race_number ?? "",
      ].join("|");
      const createdAtMs = row.sectional_created_at ? new Date(row.sectional_created_at).getTime() : 0;
      const existing = horseMap.get(raceKey);
      if (!existing || createdAtMs > existing.createdAtMs) {
        horseMap.set(raceKey, { sample, createdAtMs });
      }
      dedupedByHorse.set(normalizedHorse, horseMap);
    }

    const samplesByHorse = new Map<string, HistoricalBandSample[]>();
    for (const runner of prepared.runners) {
      const horseMap = dedupedByHorse.get(runner.normalizedHorseName);
      if (!horseMap) {
        continue;
      }
      const samples = Array.from(horseMap.values())
        .map((entry) => entry.sample)
        .sort((a, b) => b.raceDate.localeCompare(a.raceDate))
        .slice(0, 5);
      samplesByHorse.set(runner.normalizedHorseName, samples);
    }

    return samplesByHorse;
  } catch (error) {
    console.warn("Race-shape database sample warning:", error instanceof Error ? error.message : error);
    return new Map();
  }
}

function parseHistoricalBandSampleFromDatabase(
  row: HistoricalBandDbRow,
): HistoricalBandSample | null {
  const fieldSize = toNumber(row.field_size);
  if (fieldSize == null || fieldSize <= 0) {
    return null;
  }

  const positionAt800 = extractPositionAt800(row.splits_json);
  if (positionAt800 == null || positionAt800 <= 0) {
    return null;
  }

  return {
    raceDate: firstString(row.race_date, ""),
    fieldSize,
    positionAt800,
    percentileAt800: (positionAt800 / fieldSize) * 100,
    finishPosition: toNumber(row.position),
    weightCarried: toNumber(row.weight_kg),
  };
}

async function loadHistoricalBandSamplesFromApi(
  runner: PreparedRunner,
  raceDate: string,
): Promise<HistoricalBandSample[]> {
  if (!runner.horseId || horseResultsAccessState === "unavailable" || !RACING_API_USERNAME) {
    return [];
  }

  const raceDay = new Date(`${raceDate}T00:00:00Z`);
  const startDate = new Date(raceDay.getTime());
  startDate.setUTCDate(startDate.getUTCDate() - 365);

  try {
    const payload = await fetchRacingApiJson(
      `/v1/horses/${encodeURIComponent(runner.horseId)}/results?start_date=${formatIsoDate(startDate)}&end_date=${raceDate}&region=aus`,
    );
    horseResultsAccessState = "available";

    const results = Array.isArray(payload?.results) ? (payload.results as Record<string, unknown>[]) : [];
    const samples = results
      .map((result: Record<string, unknown>) => parseHistoricalBandSample(result, runner))
      .filter((sample): sample is HistoricalBandSample => Boolean(sample))
      .sort((a: HistoricalBandSample, b: HistoricalBandSample) => b.raceDate.localeCompare(a.raceDate))
      .slice(0, 5);

    return samples;
  } catch (error) {
    const message = error instanceof Error ? error.message : String(error);
    if (/plan required|401|403|not configured/i.test(message)) {
      horseResultsAccessState = "unavailable";
    }
    return [];
  }
}

function parseHistoricalBandSample(
  result: Record<string, unknown>,
  runner: PreparedRunner,
): HistoricalBandSample | null {
  const runners = Array.isArray(result.runners) ? (result.runners as Record<string, unknown>[]) : [];
  if (runners.length === 0) {
    return null;
  }

  const historicalRunner = runners.find((entry) => {
    const entryHorseId = firstString(entry.horse_id, entry.horseId, "");
    if (runner.horseId && entryHorseId && entryHorseId === runner.horseId) {
      return true;
    }
    return normalizeName(firstString(entry.horse, entry.horse_name, "")) === runner.normalizedHorseName;
  });

  if (!historicalRunner) {
    return null;
  }

  const positionAt800 = extractPositionAt800(historicalRunner);
  if (positionAt800 == null || positionAt800 <= 0) {
    return null;
  }

  const fieldSize = runners.length;
  if (fieldSize <= 0) {
    return null;
  }

  const percentileAt800 = (positionAt800 / fieldSize) * 100;

  return {
    raceDate: firstString(result.date, ""),
    fieldSize,
    positionAt800,
    percentileAt800,
    finishPosition: toNumber(historicalRunner.position),
    weightCarried: parseWeightKg(firstString(historicalRunner.weight, historicalRunner.weight_lbs, "")),
  };
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
      const direct = firstNumber(
        nested.position,
        nested.pos,
        nested.settling_position,
        nested.running_position,
        null,
      );
      if (direct != null) {
        return direct;
      }
    }

    const direct = toNumber(entry);
    if (direct != null) {
      return direct;
    }
  }

  for (const [key, entry] of Object.entries(record)) {
    const normalizedKey = normalizeName(key);
    if (
      normalizedKey.includes("800") &&
      (normalizedKey.includes("position")
        || normalizedKey.includes("pos")
        || normalizedKey.includes("settling")
        || normalizedKey.includes("running"))
    ) {
      const parsed = toNumber(entry);
      if (parsed != null) {
        return parsed;
      }
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

function estimatedBandFromRunningStyle(value: string): number {
  const normalized = normalizeName(value);
  if (normalized.includes("leader") || normalized.includes("speed")) {
    return 2;
  }
  if (normalized.includes("onpace") || normalized.includes("stalker")) {
    return 4;
  }
  if (normalized.includes("forward")) {
    return 6;
  }
  if (normalized.includes("midfield")) {
    return 8;
  }
  if (normalized.includes("backmarker")) {
    return 12;
  }
  if (normalized.includes("closer") || normalized.includes("offpace")) {
    return 10;
  }
  return 8;
}

function estimatedBandReasonSummary(value: string): string {
  const style = normalizeRunningStyleForOverview(value);
  if (style === "Unknown") {
    return "Estimated from the current map profile because measured 800m-call data is unavailable.";
  }

  return `Estimated from STRIDE's current running-style model: ${style.toLowerCase()} profile.`;
}

function zoneForBand(band: number): RaceShapeZoneKey {
  if (band <= 2) return "leader";
  if (band <= 4) return "on_pace";
  if (band <= 6) return "on_pace_wide";
  if (band <= 8) return "midfield";
  if (band <= 10) return "back_half";
  return "backmarker";
}

function paceRoleForBand(band: number): RaceShapeRunner["paceRole"] {
  if (band <= 2) return "Leader";
  if (band <= 4) return "On-Pace";
  if (band <= 6) return "On-Pace Wide";
  if (band <= 8) return "Midfield";
  if (band <= 10) return "Back Half";
  return "Backmarker";
}

function deriveRaceShapeTempo(leaderCount: number, pressureCount: number): RaceShapeTempo {
  if (leaderCount >= 3) {
    return "HOT";
  }
  if (leaderCount === 2) {
    return "GENUINE";
  }
  if (leaderCount === 1 && pressureCount >= 3) {
    return "GENUINE";
  }
  if (leaderCount === 1) {
    return "SOFT";
  }
  return "UNKNOWN";
}

function describeRaceShapeTempo(
  tempo: RaceShapeTempo,
  leaderCount: number,
  pressureCount: number,
  runners: Array<{ entry: PreparedRunner; band: number }>,
): string {
  const leaderNames = runners.filter((runner) => runner.band <= 2).map((runner) => runner.entry.horseName);
  switch (tempo) {
    case "HOT":
      return `${leaderCount} confirmed leaders - genuine to hot tempo expected. Strong pace will suit midfield runners and backmarkers.`;
    case "GENUINE":
      if (leaderCount === 1) {
        return `${leaderNames[0] || "One leader"} gets on with it but ${pressureCount} on-pace runners keep the race honest.`;
      }
      return `${leaderCount} leaders should ensure an honest tempo without one horse completely controlling the first section.`;
    case "SOFT":
      return `${leaderNames[0] || "The lone leader"} may control the speed with limited early pressure. On-pace runners are favoured if the leader pinches a cheap middle split.`;
    default:
      return `No confirmed leader in the measured map, so tempo is more tactical than trustworthy.`;
  }
}

function getBarrierConflictReason(band: number, barrier: number | null, fieldSize: number): string | null {
  if (barrier == null || band > 4) {
    return null;
  }

  const threshold = fieldSize <= 12 ? 9 : fieldSize <= 16 ? 11 : 13;
  if (barrier < threshold) {
    return null;
  }

  return `Barrier conflict: band ${band} wants a forward spot, but gate ${barrier} risks early work or a wider, deeper run than the map wants.`;
}

function getLeaderContestReason(
  band: number,
  barrier: number | null,
  leaderCount: number,
  insideMostLeaderBarrier: number | null,
): string | null {
  if (band > 2 || leaderCount <= 1) {
    return null;
  }
  if (barrier == null || insideMostLeaderBarrier == null || barrier === insideMostLeaderBarrier) {
    return null;
  }
  return `Leader contest: there are ${leaderCount} leaders and the inside draw advantage sits with a rival drawn closer to the rail.`;
}

function getTempoConflictReason(band: number, tempo: RaceShapeTempo): string | null {
  if (band < 9 || (tempo !== "SOFT" && tempo !== "UNKNOWN")) {
    return null;
  }
  return `Tempo conflict: a back-half runner in a ${tempo.toLowerCase()} race needs the pace to overachieve before the turn.`;
}

function getWeightConflictReason(
  runner: {
    band: number;
    bestRecentWinningWeight: number | null;
  },
  entry: PreparedRunner,
): string | null {
  const currentWeight = toNumber(entry.mergedRunner.weightCarried);
  if (runner.bestRecentWinningWeight == null || currentWeight == null) {
    return null;
  }
  if (currentWeight <= runner.bestRecentWinningWeight + 3) {
    return null;
  }
  return `Weight conflict: ${displayWeight(currentWeight)} is more than 3kg above the best recent winning weight this profile has carried.`;
}

function deriveTempoAdvantage(
  band: number,
  tempo: RaceShapeTempo,
): { advantage: boolean; reason: string } {
  if (tempo === "HOT") {
    if (band >= 9) {
      return { advantage: true, reason: "the stronger early pressure gives backmarkers a better chance to close into the race" };
    }
    if (band <= 2) {
      return { advantage: false, reason: "a leader drawn into a hot pressure race can overdo the first section" };
    }
  }

  if (tempo === "GENUINE") {
    if (band >= 3 && band <= 8) {
      return { advantage: true, reason: "it can settle in its natural run without conceding the race shape" };
    }
  }

  if (tempo === "SOFT") {
    if (band <= 4) {
      return { advantage: true, reason: "a softer tempo keeps the race in reach for runners close to the speed" };
    }
    return { advantage: false, reason: "softer fractions make it harder for back-half runners to reel in the leaders" };
  }

  if (tempo === "UNKNOWN" && band >= 9) {
    return { advantage: false, reason: "an uncertain tempo is a poor setup for a horse that needs genuine pressure" };
  }

  return { advantage: false, reason: "the map does not create an obvious race-shape edge for this pattern" };
}

function getRunnerRaceShapeContext(raceShape: RaceShapeBlock, horseName: string): RaceShapeRunner | null {
  const normalizedHorse = normalizeName(horseName);
  return raceShape.runners.find((runner) => normalizeName(runner.horse) === normalizedHorse) || null;
}

async function generateRaceShapeNarrative(
  prepared: PreparedRace,
  raceShape: RaceShapeBlock,
): Promise<string> {
  const prompt = buildRaceShapePrompt(prepared, raceShape);

  try {
    const text = await callGroqChatCompletion({
      system: RACE_SHAPE_SYSTEM_PROMPT,
      user: prompt,
      temperature: 0.35,
      maxTokens: 220,
    });
    const cleaned = text.replace(/\s+/g, " ").trim();
    const wordCount = countWords(cleaned);
    if (wordCount >= 70 && wordCount <= 150 && countNamedEntities(cleaned) >= 1) {
      return cleaned;
    }
  } catch {
    // fall through to deterministic narrative
  }

  return buildFallbackRaceShapeNarrative(prepared, raceShape);
}

function buildRaceShapePrompt(prepared: PreparedRace, raceShape: RaceShapeBlock): string {
  return `RACE:
${prepared.race.raceName} | ${prepared.race.track} | ${prepared.race.distanceLabel} | ${prepared.race.going}
Rail: ${prepared.race.railPosition} | Track bias: ${raceShape.trackBias || "Not reported"}
Predicted tempo: ${raceShape.tempoLabel} - ${raceShape.tempoReason}

FIELD WITH R&S BANDS:
${raceShape.runners
  .map(
    (runner) =>
      `${runner.horse} | barrier ${displayNumber(runner.barrier)} | band ${runner.band} | ${runner.paceRole} | ${runner.bandSource}${runner.positionConflictReason ? ` | conflict ${runner.positionConflictReason}` : ""}`,
  )
  .join("\n")}

Write the race-shape paragraph now.`;
}

function buildFallbackRaceShapeNarrative(prepared: PreparedRace, raceShape: RaceShapeBlock): string {
  const leaders = raceShape.runners.filter((runner) => runner.band <= 2).slice(0, 3);
  const onPace = raceShape.runners.filter((runner) => runner.band >= 3 && runner.band <= 6).slice(0, 4);
  const closers = raceShape.runners.filter((runner) => runner.band >= 9).slice(0, 3);
  const leaderText = leaders.length > 0
    ? leaders.map((runner) => `${runner.horse} (band ${runner.band})`).join(", ")
    : "no measured leader";
  const onPaceText = onPace.length > 0
    ? onPace.map((runner) => runner.horse).join(", ")
    : "the midfield line";
  const closerText = closers.length > 0
    ? closers.map((runner) => runner.horse).join(", ")
    : "the back-half runners";
  const straightBias = raceShape.tempoLabel === "SOFT"
    ? "That shape keeps the advantage with horses already in the first half of the field turning for home."
    : raceShape.tempoLabel === "HOT"
      ? `That should bring ${closerText} into the race late if the first half-mile is genuinely contested.`
      : `That should keep both the stalking line and the back half in play rather than handing the race to a lone leader.`;

  return `${leaderText} should define the first 400 metres, with ${onPaceText} trying to hold the first two layers rather than conceding cheap control. Through the middle section the tempo reads ${raceShape.tempoLabel.toLowerCase()}, so the key tension is whether the leader line gets a breather or keeps the race rolling into the bend. ${straightBias} The rail is ${prepared.race.railPosition.toLowerCase() !== "unavailable" ? prepared.race.railPosition.toLowerCase() : "not clearly reported"}, so barrier execution matters as much as the raw band map.`;
}

function deriveExplicitRaceTrackBias(prepared: PreparedRace): string | null {
  const racecardRace = toRecord(prepared.race.rawRaceData.racecardRace);
  const tipsRace = toRecord(prepared.race.rawRaceData.tipsRace);
  return nullIfUnavailable(firstString(
    racecardRace.trackBias,
    racecardRace.track_bias,
    tipsRace.trackBias,
    tipsRace.track_bias,
    "",
  ));
}

function fetchRacingApiJson(endpoint: string): Promise<any> {
  if (!RACING_API_USERNAME) {
    throw new Error("Racing API credentials not configured");
  }

  const credentials = Buffer.from(`${RACING_API_USERNAME}:${RACING_API_PASSWORD}`).toString("base64");
  return fetch(`${RACING_API_BASE_URL}${endpoint}`, {
    method: "GET",
    headers: {
      Authorization: `Basic ${credentials}`,
      Accept: "application/json",
      "User-Agent": "Mozilla/5.0",
    },
  }).then(async (response) => {
    const text = await response.text();
    if (!response.ok) {
      throw new Error(text || `Racing API error ${response.status}`);
    }
    return JSON.parse(text);
  });
}

function formatIsoDate(value: Date): string {
  return value.toISOString().slice(0, 10);
}

function roundTo(value: number, decimals: number): number {
  const factor = 10 ** decimals;
  return Math.round(value * factor) / factor;
}

function compareNullableNumbers(a: number | null, b: number | null): number {
  if (a == null && b == null) return 0;
  if (a == null) return 1;
  if (b == null) return -1;
  return a - b;
}

function buildMeetingContext(race: PreparedRaceContext, raceOverview: RaceOverviewSections): string | null {
  const parts = [
    nullIfUnavailable(race.weatherConditions),
    nullIfUnavailable(race.railPosition) ? `Rail ${race.railPosition}` : null,
    raceOverview.trackConditions || null,
  ].filter((value): value is string => Boolean(value));

  return parts.length > 0 ? parts.join(" | ") : null;
}

function statRecordObject(stats: Record<string, unknown>): { starts: number; wins: number; places: number } | null {
  const starts = statCount(stats.total);
  if (starts <= 0) {
    return null;
  }

  return {
    starts,
    wins: statCount(stats.first),
    places: statCount(stats.second) + statCount(stats.third),
  };
}

function mergeStatRecordObjects(
  first: { starts: number; wins: number; places: number } | null,
  second: { starts: number; wins: number; places: number } | null,
): { starts: number; wins: number; places: number } | null {
  if (!first && !second) {
    return null;
  }

  return {
    starts: (first?.starts ?? 0) + (second?.starts ?? 0),
    wins: (first?.wins ?? 0) + (second?.wins ?? 0),
    places: (first?.places ?? 0) + (second?.places ?? 0),
  };
}

function deriveMarketIntentSignal(merged: Record<string, unknown>): string | null {
  const movement = normalizeName(stringifyValue(merged.marketMovement));
  const opening = toNumber(merged.openingOdds);
  const current = toNumber(merged.currentOdds);
  const marketRank = toNumber(merged.marketRank);

  if (movement.includes("firm") && opening != null && current != null && current < opening * 0.9) {
    return marketRank != null && marketRank <= 3 ? "strong_stable_confidence" : "firm_market_support";
  }

  if (movement.includes("drift") && opening != null && current != null && current > opening * 1.1) {
    return "market_resistance";
  }

  if (marketRank === 1) {
    return "market_leader";
  }

  return null;
}

function fieldSnapshotFromPrepared(runners: PreparedRunner[]): string[] {
  return runners
    .slice()
    .sort((a, b) => compareMarketRank(a.marketRank, b.marketRank))
    .slice(0, 8)
    .map((entry) => {
      const merged = entry.mergedRunner;
      return `${entry.horseName}: barrier ${displayNumber(entry.barrier)}, ${displayOdds(entry.currentOdds)}, ${stringifyValue(merged.runningStyle) || "Unknown"}, model win ${displayPercent(toNumber(merged.modelWinPct))}`;
    });
}

function buildFallbackRunnerAnalysis(
  prepared: PreparedRace,
  runner: PreparedRunner,
  rivals: RivalSummary[],
  context: RaceOverviewPayload,
  failureReason: string,
): RunnerAnalysisSections {
  const race = prepared.race;
  const merged = runner.mergedRunner;
  const raceOverview = context.overview;
  const raceShapeContext = getRunnerRaceShapeContext(context.raceShape, runner.horseName);
  const barrier = runner.barrier;
  const odds = toNumber(merged.currentOdds);
  const marketRank = toNumber(merged.marketRank);
  const modelWinPct = toNumber(merged.modelWinPct);
  const edgePct = toNumber(merged.edgePct);
  const runsThisPrep = toNumber(merged.runsThisPrep);
  const daysSinceLastRun = toNumber(merged.daysSinceLastRun);
  const spellLengthWeeks = toNumber(merged.spellLengthWeeks);
  const runningStyle = stringifyValue(merged.runningStyle) || "Unknown";
  const trackRecord = stringifyValue(runner.statsSummary.trackRecord) || "No exposed track record";
  const distanceRecord = stringifyValue(runner.statsSummary.distanceRecord) || "No exposed distance record";
  const goingRecord = stringifyValue(runner.statsSummary.goingRecord) || "No exposed going record";
  const classLine = `${stringifyValue(merged.lastRaceClass) || "Unknown"} -> ${stringifyValue(merged.currentRaceClass) || race.raceClass}`;
  const rivalNames = rivals.slice(0, 3).map((rival) => rival.name).join(", ") || "the main market hopes";
  const mapPosition = raceShapeContext
    ? `${raceShapeContext.paceRole.toLowerCase()} band ${raceShapeContext.band}`
    : describeRunnerMapPosition(runner, prepared.runners.length);
  const paceRisk = raceShapeContext?.positionConflictReason
    || describeRunnerPaceRisk(runner, prepared.runners.length, race.trackConfig);
  const fitnessLine = runsThisPrep != null
    ? `This is run ${runsThisPrep} of the prep after ${daysSinceLastRun != null ? `${daysSinceLastRun} days` : "an unknown break"}`
    : spellLengthWeeks != null
      ? `The horse resumes from a ${spellLengthWeeks}-week spell`
      : `Fitness data is thin, which matters more than usual in a ${race.fieldSize}-runner race`;
  const marketLine = odds != null
    ? `${runner.horseName} is ${displayOdds(odds)}${marketRank != null ? ` and market rank ${marketRank}` : ""}`
    : "Betting is not available, so the call leans on the model and map";
  const winCall = getFallbackWinCall({
    runnerName: runner.horseName,
    odds,
    marketRank,
    modelWinPct,
    edgePct,
    barrier,
    runningStyle,
  });

  return {
    profile: `${runner.horseName} brings ${displayPercent(modelWinPct)} model win probability with ${edgePct != null ? `${edgePct > 0 ? "+" : ""}${edgePct.toFixed(1)}% edge` : "no clear edge"}, and the exposed profile reads ${trackRecord} at the track plus ${distanceRecord} at the trip. ${marketLine}, while the main opposition for context is ${rivalNames}.`,
    pace: `${context.raceShape.narrative} From barrier ${displayNumber(barrier)}, ${runner.horseName} maps as a ${mapPosition}${raceShapeContext?.tempoAdvantage ? ` and the ${context.raceShape.tempoLabel.toLowerCase()} tempo helps because ${raceShapeContext.tempoAdvantageReason.toLowerCase()}` : ""}. ${paceRisk}. ${runningStyle !== "Unknown" ? `${runner.horseName}'s exposed style is ${runningStyle}, so the shared race shape matters directly.` : `The running-style data is thin, so barrier, rider intent, and ${race.trackConfig.toLowerCase()} become the key map clues.`}`,
    condition: `${race.going} ground at ${race.track} puts the focus on ${goingRecord}, while the class line is ${classLine} and the weight setup is ${displayWeight(toNumber(merged.weightCarried))}. ${fitnessLine}, so this is more about suitability and readiness than blind upside.`,
    verdict: `${winCall} If ${runner.horseName} wins, it is because the horse lands a cleaner run than ${rivalNames} from gate ${displayNumber(barrier)} and the ${displayPercent(modelWinPct)} model case holds up under race pressure. If the race shape turns against that profile, leave it alone in the win market.`,
    qualityFlag: `Fallback analysis used: ${failureReason}`,
  };
}

function describeRunnerMapPosition(runner: PreparedRunner, fieldSize: number): string {
  const style = runner.runningStyle.toLowerCase();
  const barrier = runner.barrier ?? Math.ceil(fieldSize / 2);

  if (style.includes("leader") || style.includes("speed")) {
    return barrier <= Math.max(4, Math.ceil(fieldSize / 3))
      ? "in the first pair and capable of holding a forward line"
      : "forward but likely forced to burn fuel early";
  }

  if (style.includes("on_pace") || style.includes("on-pace") || style.includes("stalker")) {
    return barrier <= Math.max(5, Math.ceil(fieldSize / 2))
      ? "just behind the speed with cover if the rider is positive"
      : "midfield with the risk of covering extra ground";
  }

  if (style.includes("backmarker") || style.includes("closer")) {
    return barrier <= Math.max(4, Math.ceil(fieldSize / 3))
      ? "behind midfield on the rail needing the gaps to open"
      : "back in the run and relying on tempo plus luck";
  }

  return barrier <= Math.max(4, Math.ceil(fieldSize / 3))
    ? "somewhere around midfield from a draw that gives options"
    : "midfield or worse from a gate that can force a decision early";
}

function describeRunnerPaceRisk(
  runner: PreparedRunner,
  fieldSize: number,
  trackConfig: string,
): string {
  const barrier = runner.barrier ?? Math.ceil(fieldSize / 2);
  const normalizedTrack = trackConfig.toLowerCase();

  if (barrier >= Math.max(10, fieldSize - 3)) {
    return `wide gates on ${normalizedTrack} often mean a longer trip or a forced drag back`;
  }

  if (barrier <= 3) {
    return `the inside draw can save ground, but it also demands the rider find clear air at the right time`;
  }

  return `the middle draw gives the jockey tactical options, but not enough room to drift into the wrong lane without consequence`;
}

function getFallbackWinCall(input: {
  runnerName: string;
  odds: number | null;
  marketRank: number | null;
  modelWinPct: number | null;
  edgePct: number | null;
  barrier: number | null;
  runningStyle: string;
}): string {
  const { runnerName, odds, marketRank, modelWinPct, edgePct, barrier, runningStyle } = input;
  const wideBarrier = barrier != null && barrier >= 10;
  const style = runningStyle.toLowerCase();

  if (edgePct != null && edgePct >= 2 && modelWinPct != null && modelWinPct >= 12 && !wideBarrier) {
    return `${runnerName} is backable to win at ${displayOdds(odds)} because the model edge is real and the draw is not doing the horse any obvious damage.`;
  }

  if (marketRank === 1 && edgePct != null && edgePct < 0) {
    return `${runnerName} looks too short to back at ${displayOdds(odds)} because the market has already charged for the positives.`;
  }

  if (wideBarrier && (style.includes("backmarker") || style.includes("closer") || style.includes("unknown"))) {
    return `${runnerName} is a no win bet from gate ${displayNumber(barrier)} because the map is asking for too much luck.`;
  }

  if (modelWinPct != null && modelWinPct >= 10 && odds != null && odds >= 6) {
    return `${runnerName} has enough of a case to win, but only if the rider secures the right run at ${displayOdds(odds)}.`;
  }

  return `${runnerName} is a no win bet at ${displayOdds(odds)} because the profile does not justify taking the current price on trust.`;
}

function buildRaceOverviewPrompt(prepared: PreparedRace, raceShape: RaceShapeBlock): string {
  const payload = buildRaceOverviewPayload(prepared);
  const leaders = payload.runners.filter((runner) => runner.runningStyle === "Leader");
  const onPace = payload.runners.filter((runner) => runner.runningStyle === "On-pace");
  const midfield = payload.runners.filter((runner) => runner.runningStyle === "Midfield");
  const backmarkers = payload.runners.filter((runner) => runner.runningStyle === "Backmarker");
  const unknownStyles = payload.runners.filter((runner) => runner.runningStyle === "Unknown");
  const favourite = payload.runners.find((runner) => runner.marketRank === 1);
  const ourTip = payload.runners.find((runner) => runner.isTip);
  const significantFirmers = payload.runners.filter(
    (runner) =>
      runner.marketMovement === "Firming" &&
      runner.openingOdds != null &&
      runner.odds != null &&
      runner.openingOdds > runner.odds * 1.3,
  );
  const significantDrifters = payload.runners.filter(
    (runner) =>
      runner.marketMovement === "Drifting" &&
      runner.openingOdds != null &&
      runner.odds != null &&
      runner.odds > runner.openingOdds * 1.3,
  );
  const firstUpRunners = payload.runners.filter((runner) => runner.runsThisPrep === 1);
  const classDroppers = payload.runners.filter((runner) => runner.classChange === "Dropping in class");
  const gearChangers = payload.runners.filter((runner) => runner.gearChanges.length > 0);
  const smallFieldNote = payload.race.fieldSize <= 4
    ? "This is a very small field. Tactical control and mid-race positioning matter more than raw talent."
    : "";
  const bigFieldNote = payload.race.fieldSize >= 18
    ? "This is a big field. Traffic, barrier pressure, and getting dragged into the right lane are major factors."
    : "";
  const heavyTrackNote = /^heavy/i.test(payload.race.going)
    ? "Heavy ground is a primary filter in this race. Eliminate horses without a wet-track profile if the data supports it."
    : "";
  const railOutNote = /out\s*(?:[9-9]|1\d)/i.test(payload.race.railPosition)
    ? "The rail is significantly out, so normal inside-draw logic may flip. Address that directly in the map and pace sections."
    : "";
  const oddsUnavailable = payload.runners.every((runner) => runner.odds == null);

  return `RACE TO ANALYSE:
${payload.race.name} | Race ${payload.race.number} | ${payload.race.track}
${payload.race.distance} | ${payload.race.raceClass} | ${payload.race.weightType}
Going: ${payload.race.going} (${payload.race.goingTrend}) | Rail: ${payload.race.railPosition}
Track: ${payload.race.trackConfig} | Bias: ${payload.race.trackBias}
Weather: ${payload.race.weather}
Field: ${payload.race.fieldSize} runners | Race time: ${payload.race.raceTime} | Race date: ${payload.race.raceDate}

SHARED RACE SHAPE:
Tempo: ${raceShape.tempoLabel} - ${raceShape.tempoReason}
Leaders ${raceShape.leaders} | On-pace ${raceShape.onPace} | Midfield ${raceShape.midfield} | Closers ${raceShape.closers}
Band leaders: ${raceShape.runners.filter((runner) => runner.band <= 2).map((runner) => `${runner.horse} (B${runner.band}, gate ${displayNumber(runner.barrier)})`).join(", ") || "None"}
Position conflicts: ${raceShape.runners.filter((runner) => runner.positionConflict).map((runner) => `${runner.horse}: ${runner.positionConflictReason}`).join(" | ") || "None"}

TACTICAL BREAKDOWN:
Likely leaders: ${leaders.map((runner) => runner.name).join(", ") || "None exposed in the data"}
On-pace runners: ${onPace.map((runner) => runner.name).join(", ") || "None exposed"}
Midfield runners: ${midfield.map((runner) => runner.name).join(", ") || "None exposed"}
Backmarkers: ${backmarkers.map((runner) => runner.name).join(", ") || "None exposed"}
Unknown running styles: ${unknownStyles.map((runner) => runner.name).join(", ") || "None"}
Favourite: ${favourite ? `${favourite.name} at ${displayOdds(favourite.odds)}` : "No clear market leader"}
Our system tip: ${ourTip ? `${ourTip.name} at ${displayOdds(ourTip.odds)}` : "No flagged tip"}

NOTABLE MARKET MOVES:
Significant firmers (30%+): ${significantFirmers.map((runner) => `${runner.name} (${displayOdds(runner.openingOdds)} -> ${displayOdds(runner.odds)})`).join(", ") || "None"}
Significant drifters (30%+): ${significantDrifters.map((runner) => `${runner.name} (${displayOdds(runner.openingOdds)} -> ${displayOdds(runner.odds)})`).join(", ") || "None"}
${oddsUnavailable ? "Betting is not available yet. Market analysis must acknowledge that and lean on form, map, and conditions instead." : ""}

SITUATIONAL FACTORS:
First-up runners: ${firstUpRunners.map((runner) => `${runner.name} (${displayWeeks(runner.spellLengthWeeks)} spell)`).join(", ") || "None"}
Class droppers: ${classDroppers.map((runner) => runner.name).join(", ") || "None"}
Gear changes: ${gearChangers.map((runner) => `${runner.name}: ${runner.gearChanges.join(", ")}`).join(" | ") || "None"}
${smallFieldNote}
${bigFieldNote}
${heavyTrackNote}
${railOutNote}

STRUCTURED PAYLOAD:
${JSON.stringify(payload, null, 2)}

Write the six-section race overview now.
- Be specific and name horses.
- Commit to a pace call: genuine, moderate, or false.
- Commit to a 1-2-3 race prediction with reasons.
- If our tip is not the favourite, address that tension explicitly.
- If most running styles are unknown, infer the tactical picture from barriers, field size, distance, track shape, and market position instead of writing a generic disclaimer.`;
}

function buildRaceOverviewPayload(prepared: PreparedRace) {
  const tipsRace = toRecord(prepared.race.rawRaceData.tipsRace);
  const racecardRace = toRecord(prepared.race.rawRaceData.racecardRace);

  return {
    race: {
      name: prepared.race.raceName,
      number: prepared.race.raceNumber,
      track: prepared.race.track,
      trackConfig: prepared.race.trackConfig,
      distance: prepared.race.distanceLabel,
      going: prepared.race.going,
      goingTrend: firstString(
        racecardRace.goingTrend,
        racecardRace.going_trend,
        tipsRace.goingTrend,
        "Not reported",
      ),
      weather: firstString(
        racecardRace.weather,
        racecardRace.weather_conditions,
        tipsRace.weather,
        prepared.race.weatherConditions,
      ),
      railPosition: firstString(
        racecardRace.railPosition,
        racecardRace.rail_position,
        tipsRace.railPosition,
        prepared.race.railPosition,
      ),
      trackBias: deriveRaceTrackBias(prepared),
      raceClass: prepared.race.raceClass,
      weightType: prepared.race.weightType,
      prizeMoney: prepared.race.prizeMoney,
      fieldSize: prepared.race.fieldSize,
      raceTime: prepared.race.raceTime,
      raceDate: prepared.race.raceDate,
      ageRestriction: prepared.race.ageRestriction,
      sexRestriction: prepared.race.sexRestriction,
    },
    runners: prepared.runners.map((runner) => {
      const merged = runner.mergedRunner;
      const age = toNumber(merged.horseAge);
      const sex = stringifyValue(merged.horseSex) || "Unknown";
      return {
        name: runner.horseName,
        barrier: runner.barrier,
        runningStyle: normalizeRunningStyleForOverview(stringifyValue(merged.runningStyle)),
        runningStyleConfidence: stringifyValue(merged.runningStyleConfidence) || "Low",
        odds: toNumber(merged.currentOdds),
        openingOdds: toNumber(merged.openingOdds),
        marketMovement: stringifyValue(merged.marketMovement) || "Unavailable",
        marketRank: toNumber(merged.marketRank),
        jockey: stringifyValue(merged.jockey) || "Unavailable",
        jockeyWinRate: null,
        trainer: stringifyValue(merged.trainer) || "Unavailable",
        trainerWinRate: null,
        weight: toNumber(merged.weightCarried),
        ageAndSex: [age != null ? `${age}yo` : null, sex].filter(Boolean).join(" "),
        recentFormString: stringifyValue(merged.formString) || "No exposed form",
        careerWinRate: toNumber(merged.careerWinPercent),
        winsAtTrack: toNumber(merged.winsAtThisTrack),
        startsAtTrack: toNumber(merged.startsAtThisTrack),
        winsAtDistance: toNumber(merged.winsAtThisDistance),
        startsAtDistance: toNumber(merged.startsAtThisDistance),
        goingRecord: {
          good: { starts: toNumber(merged.startsOnGood), wins: toNumber(merged.winsOnGood) },
          soft: { starts: toNumber(merged.startsOnSoft), wins: toNumber(merged.winsOnSoft) },
          heavy: { starts: toNumber(merged.startsOnHeavy), wins: toNumber(merged.winsOnHeavy) },
        },
        daysSinceLastRun: toNumber(merged.daysSinceLastRun),
        runsThisPrep: toNumber(merged.runsThisPrep),
        spellLengthWeeks: toNumber(merged.spellLengthWeeks),
        gearChanges: Array.isArray(merged.gearChanges) ? (merged.gearChanges as string[]) : [],
        classChange: stringifyValue(merged.classChange) || "Unavailable",
        averageLast600m: toNumber(merged.averageLast600m),
        bestLast600m: toNumber(merged.bestLast600m),
        barrierHistory: stringifyValue(merged.barrierDrawHistory) || "Unavailable",
        isTip: Boolean(merged.isTipped),
        confidence: stringifyValue(merged.confidence) || "Unavailable",
        modelWinPct: toNumber(merged.modelWinPct),
        modelPlacePct: toNumber(merged.modelPlacePct),
        edgePct: toNumber(merged.edgePct),
        trackBiasSummary: deriveRunnerTrackBiasSummary(runner),
        lastRaced: stringifyValue(merged.lastRaced) || "Unavailable",
        isFirstUp: Boolean(merged.isFirstUp),
        isImproving: Boolean(merged.isImproving),
      };
    }),
  };
}

function buildDerivedRaceOverviewContext(
  prepared: PreparedRace,
  raceShape?: RaceShapeBlock,
): RaceOverviewSections {
  const leaders = prepared.runners.filter((runner) => {
    const style = runner.runningStyle.toLowerCase();
    return style.includes("leader") || style.includes("speed");
  });
  const onPace = prepared.runners.filter((runner) => {
    const style = runner.runningStyle.toLowerCase();
    return style.includes("on_pace") || style.includes("on-pace");
  });
  const wideBarriers = prepared.runners
    .filter((runner) => runner.barrier != null && runner.barrier >= Math.max(10, prepared.race.fieldSize - 3))
    .map((runner) => runner.horseName);
  const topMarket = [...prepared.runners]
    .filter((runner) => runner.marketRank != null && runner.marketRank <= 4)
    .sort((a, b) => compareMarketRank(a.marketRank, b.marketRank))
    .map((runner) => runner.horseName);
  const leaderNames = leaders.map((runner) => runner.horseName);
  const onPaceNames = onPace.map((runner) => runner.horseName);
  const paceScenario = prepared.race.likelyPaceScenario;
  const derivedTempo = raceShape ? `${raceShape.tempoLabel}. ${raceShape.tempoReason}` : paceScenario;
  const leaderClause = leaderNames.length > 0
    ? `Early speed points to ${leaderNames.join(", ")} rolling forward.`
    : "No exposed leader appears in the raw running-style data, so barrier and tactical intent matter more than usual.";
  const pressureClause = onPaceNames.length > 0
    ? `${onPaceNames.join(", ")} also want the first half of the field and add tactical pressure around the bend.`
    : "There is no obvious second wave of on-pace runners exposed in the source data.";
  const wideClause = wideBarriers.length > 0
    ? `Wide gates for ${wideBarriers.join(", ")} raise the chance of horses being trapped deep or dragged back.`
    : "There is no major wide-draw cluster forcing the map to break open early.";
  const marketClause = topMarket.length > 0
    ? `The market is centred on ${topMarket.join(", ")}.`
    : "There is no reliable market hierarchy yet.";

  return {
    raceMap: `Field of ${prepared.race.fieldSize} at ${prepared.race.track} over ${prepared.race.distanceLabel}. ${leaderClause} ${pressureClause} ${wideClause}`,
    paceAnalysis: `${derivedTempo} ${leaderNames.length >= 3 ? "That points to a genuinely run race with pressure on the leaders." : leaderNames.length === 1 ? "That gives the likely leader a chance to control the race if the rider gets it right." : "That keeps the race tactical and places more weight on barriers and mid-race positioning."}`,
    trackConditions: `${prepared.race.going} ground, rail ${prepared.race.railPosition}, track shape ${prepared.race.trackConfig}. Treat any unknown condition data conservatively.`,
    formCycle: `Use days since last run, first-up flags, and recent market rank to separate fit, exposed runners from fresh or speculative profiles.`,
    marketIntelligence: marketClause,
    racePrediction: raceShape
      ? `Race prediction pending full overview generation, but the shared race-shape read is ${raceShape.tempoLabel.toLowerCase()} with ${raceShape.leaders} leaders and ${raceShape.closers} closers waiting for pressure.`
      : `Race prediction pending full overview generation.`,
  };
}

async function generateRaceOverviewWithValidation(
  prompt: string,
): Promise<RaceOverviewSections> {
  let lastParsed: RaceOverviewSections | null = null;
  let retryPrompt = prompt;

  for (let attempt = 0; attempt < 3; attempt += 1) {
    const text = await callGroqChatCompletion({
      system: RACE_OVERVIEW_SYSTEM_PROMPT,
      user: retryPrompt,
      temperature: 0.6,
      maxTokens: 1200,
    });
    const parsed = parseAndValidateRaceOverview(text);
    lastParsed = parsed;

    if (!parsed.qualityFlag) {
      return parsed;
    }

    retryPrompt = `${prompt}

Your previous response was rejected for these reasons: ${parsed.qualityFlag}
Rewrite the overview from scratch.
Repair rules:
- Keep all six sections present and in the correct order.
- Each section must be 80-120 words.
- Name actual horses in RACE MAP, PACE ANALYSIS, MARKET INTELLIGENCE, and RACE PREDICTION.
- PACE ANALYSIS must make a definitive tempo call and say who benefits.
- RACE PREDICTION must commit to a winner, runner-up, and one roughie or upset horse.
- Do not use generic racing filler or banned phrases.`;
  }

  return lastParsed || {
    raceMap: "Race overview unavailable.",
    paceAnalysis: "Race overview unavailable.",
    trackConditions: "Race overview unavailable.",
    formCycle: "Race overview unavailable.",
    marketIntelligence: "Race overview unavailable.",
    racePrediction: "Race overview unavailable.",
    qualityFlag: "Groq returned no valid race overview.",
  };
}

async function generateWithValidation(
  prompt: string,
  horseName: string,
): Promise<RunnerAnalysisSections> {
  let lastParsed: RunnerAnalysisSections | null = null;
  let retryPrompt = prompt;

  for (let attempt = 0; attempt < 4; attempt += 1) {
    const text = await callGroqChatCompletion({
      system: RUNNER_ANALYSIS_SYSTEM_PROMPT,
      user: retryPrompt,
      maxTokens: 1000,
      responseFormat: "json_object",
    });
    const parsed = parseAndValidateAnalysis(text, horseName);
    lastParsed = parsed;

    if (!parsed.qualityFlag) {
      return parsed;
    }

    retryPrompt = `${prompt}

Your previous response was rejected for these reasons: ${parsed.qualityFlag}
Rewrite the analysis from scratch. Keep all four sections present, specific, and compliant.
Mandatory repair rules:
- Return valid JSON only that matches the required schema exactly.
- Keep every field present and non-empty.
- race_shape_assessment must mention the barrier or draw plus the tempo/map consequence.
- form_depth must interpret the exposed form rather than describing bare results.
- market_read must mention price, market rank, or movement.
- primary_risk and secondary_risk must be specific scenarios, not generic warnings.
- Do not return markdown fences, headings, or labelled prose sections.`; 
  }

  return lastParsed || {
    profile: "Analysis unavailable.",
    pace: "Analysis unavailable.",
    condition: "Analysis unavailable.",
    verdict: "Analysis unavailable.",
    qualityFlag: "Groq returned no valid analysis.",
  };
}

async function callGroqChatCompletion(input: {
  system: string;
  user: string;
  temperature?: number;
  maxTokens?: number;
  responseFormat?: "json_object";
}): Promise<string> {
  return throttledGroqCall(async () => {
    const apiKey = process.env.GROQ_API_KEY;
    if (!apiKey) {
      throw new Error("GROQ_API_KEY is not configured.");
    }

    let delayMs = GROQ_INITIAL_RETRY_MS;
    let lastError = "Unknown Groq error";

    for (let attempt = 0; attempt < MAX_GROQ_RETRIES; attempt += 1) {
      try {
        const response = await fetch(GROQ_API_URL, {
          method: "POST",
          headers: {
            Authorization: `Bearer ${apiKey}`,
            "Content-Type": "application/json",
          },
          body: JSON.stringify({
            model: GROQ_MODEL,
            temperature: input.temperature ?? 0.25,
            max_tokens: input.maxTokens ?? 700,
            ...(input.responseFormat ? { response_format: { type: input.responseFormat } } : {}),
            messages: [
              { role: "system", content: input.system },
              { role: "user", content: input.user },
            ],
          }),
        });

        if (!response.ok) {
          const errorText = await response.text();
          lastError = `Groq API ${response.status}: ${errorText}`;

          if ((response.status === 429 || response.status >= 500) && attempt < MAX_GROQ_RETRIES - 1) {
            const retryAfter = response.status === 429
              ? parseRetryAfterHeader(response.headers.get("retry-after")) ?? parseRetryAfterSeconds(errorText)
              : null;
            await sleep(Math.max(retryAfter ?? delayMs, delayMs));
            delayMs *= 2;
            continue;
          }

          if (response.status === 429) {
            throw new Error("Analysis service is busy. Please wait a moment and try again.");
          }
          throw new Error(lastError);
        }

        const payload = (await response.json()) as {
          choices?: Array<{ message?: { content?: string | null } }>;
        };
        const content = payload.choices?.[0]?.message?.content?.trim();
        if (!content) {
          throw new Error("Groq returned an empty response.");
        }
        return content;
      } catch (error) {
        lastError = error instanceof Error ? error.message : String(error);
        if (attempt < MAX_GROQ_RETRIES - 1) {
          await sleep(delayMs);
          delayMs *= 2;
          continue;
        }
      }
    }

    throw new Error(lastError);
  });
}

function parseAndValidateRaceOverview(text: string): RaceOverviewSections {
  const sections: RaceOverviewSections = {
    raceMap: extractOverviewSection(text, "RACE MAP", "PACE ANALYSIS"),
    paceAnalysis: extractOverviewSection(text, "PACE ANALYSIS", "TRACK & CONDITIONS"),
    trackConditions: extractOverviewSection(text, "TRACK & CONDITIONS", "FORM CYCLE"),
    formCycle: extractOverviewSection(text, "FORM CYCLE", "MARKET INTELLIGENCE"),
    marketIntelligence: extractOverviewSection(text, "MARKET INTELLIGENCE", "RACE PREDICTION"),
    racePrediction: extractOverviewSection(text, "RACE PREDICTION"),
  };

  const issues: string[] = [];
  const fullText = [
    sections.raceMap,
    sections.paceAnalysis,
    sections.trackConditions,
    sections.formCycle,
    sections.marketIntelligence,
    sections.racePrediction,
  ].join(" ").toLowerCase();
  const banned = RACE_OVERVIEW_BANNED_PHRASES.filter((phrase) => fullText.includes(phrase));

  if (banned.length > 0) {
    issues.push(`Template phrases detected: ${banned.join(", ")}`);
  }

  const entries: Array<[keyof RaceOverviewSections, string]> = [
    ["raceMap", sections.raceMap],
    ["paceAnalysis", sections.paceAnalysis],
    ["trackConditions", sections.trackConditions],
    ["formCycle", sections.formCycle],
    ["marketIntelligence", sections.marketIntelligence],
    ["racePrediction", sections.racePrediction],
  ];

  for (const [key, value] of entries) {
    if (!value || value.length < 60) {
      issues.push(`${key} is too brief or missing`);
      continue;
    }

    const wordCount = countWords(value);
    if (wordCount < 55) {
      issues.push(`${key} is too short`);
    }
    if (wordCount > 150) {
      issues.push(`${key} is too long`);
    }
    if (countSentences(value) < 2) {
      issues.push(`${key} needs at least 2 sentences`);
    }
    if (countConcreteSignals(value, "") < 2) {
      issues.push(`${key} needs more concrete facts`);
    }

    const roleIssue = getRaceOverviewRoleIssue(key, value);
    if (roleIssue) {
      issues.push(roleIssue);
    }
  }

  if (issues.length > 0) {
    sections.qualityFlag = issues.join(" | ");
  }

  return sections;
}

function extractOverviewSection(
  text: string,
  sectionName: string,
  nextSection?: string,
): string {
  const escapedCurrent = escapeRegExp(sectionName);
  const regex = nextSection
    ? new RegExp(`${escapedCurrent}\\s*:?\\s*([\\s\\S]*?)(?=\\n\\s*${escapeRegExp(nextSection)}\\s*:?)`, "i")
    : new RegExp(`${escapedCurrent}\\s*:?\\s*([\\s\\S]*)$`, "i");
  const match = text.match(regex);
  return match ? match[1].trim().replace(/\s+/g, " ") : "";
}

function getRaceOverviewRoleIssue(
  sectionKey: keyof RaceOverviewSections,
  value: string,
): string | null {
  if (
    sectionKey === "raceMap" &&
    (
      !/(barrier|draw|gate|settle|map|lead|on-pace|backmarker|midfield|wide|rail|box-seat|cover|trap)/i.test(value) ||
      countNamedEntities(value) < 1
    )
  ) {
    return "RACE MAP does not define the tactical shape clearly enough";
  }

  if (
    sectionKey === "paceAnalysis" &&
    (
      !/(genuine|moderate|false|tempo|speed|pressure|sit-and-sprint|soft lead|hot pace)/i.test(value) ||
      !/(favours|benefits|hurts|suits|buries|compromises)/i.test(value)
    )
  ) {
    return "PACE ANALYSIS does not commit to a tempo and beneficiary";
  }

  if (
    sectionKey === "trackConditions" &&
    countPatternHits(value, /(track|going|good|soft|heavy|rail|bias|draw|inside|wide|straight|turn)/gi) < 3
  ) {
    return "TRACK & CONDITIONS does not cover the race-day setup";
  }

  if (
    sectionKey === "formCycle" &&
    countPatternHits(value, /(first-up|spell|prep|run|campaign|peaking|fitness|last start|fresh|class)/gi) < 3
  ) {
    return "FORM CYCLE does not address prep and fitness strongly enough";
  }

  if (
    sectionKey === "marketIntelligence" &&
    (
      !/(market|favourite|price|odds|firm|drift|backed|betting|overlay|under the odds|overbet|deserving|vulnerable)/i.test(value) ||
      countNamedEntities(value) < 1
    )
  ) {
    return "MARKET INTELLIGENCE does not read the market properly";
  }

  if (
    sectionKey === "racePrediction" &&
    (
      countNamedEntities(value) < 2 ||
      !/(wins|winner|second|runner-up|roughie|upset|beats|finishes over|lands the race)/i.test(value)
    )
  ) {
    return "RACE PREDICTION does not make a committed 1-2-3 call";
  }

  return null;
}

function parseAndValidateAnalysis(text: string, horseName: string): RunnerAnalysisSections {
  const structured = parseStructuredAnalysis(text, horseName);
  if (structured) {
    return structured;
  }

  const legacy = parseLegacyRunnerAnalysis(text, horseName);
  legacy.qualityFlag = legacy.qualityFlag
    ? `Response was not valid JSON | ${legacy.qualityFlag}`
    : "Response was not valid JSON";
  return legacy;
}

function parseStructuredAnalysis(text: string, horseName: string): RunnerAnalysisSections | null {
  const parsedJson = parseJsonObject(text);
  if (!parsedJson) {
    return null;
  }

  const normalized = normalizeStructuredAnalysis(unwrapStructuredAnalysisRoot(parsedJson), horseName);
  if (!normalized) {
    return {
      profile: "",
      pace: "",
      condition: "",
      verdict: "",
      qualityFlag: "Structured analysis did not match the required schema",
    };
  }

  const issues = validateStructuredAnalysis(normalized, horseName);
  return buildStructuredSections(normalized, issues);
}

function unwrapStructuredAnalysisRoot(value: Record<string, unknown>): Record<string, unknown> {
  const nested = [value.analysis, value.result, value.data].find(
    (entry) => entry && typeof entry === "object" && !Array.isArray(entry),
  );
  return nested ? (nested as Record<string, unknown>) : value;
}

function parseLegacyRunnerAnalysis(text: string, horseName: string): RunnerAnalysisSections {
  const sections: RunnerAnalysisSections = {
    profile: extractSection(text, "PROFILE"),
    pace: extractSection(text, "PACE"),
    condition: extractSection(text, "CONDITION"),
    verdict: extractSection(text, "VERDICT"),
  };

  const issues: string[] = [];
  const fullText = `${sections.profile} ${sections.pace} ${sections.condition} ${sections.verdict}`.toLowerCase();
  const banned = BANNED_PHRASES.filter((phrase) => fullText.includes(phrase));

  if (banned.length > 0) {
    issues.push(`Template phrases detected: ${banned.join(", ")}`);
  }

  for (const [key, value] of Object.entries(sections)) {
    if (!value || value.length < 35) {
      issues.push(`${key.toUpperCase()} is too brief or missing`);
      continue;
    }

    if (countWords(value) < 28) {
      issues.push(`${key.toUpperCase()} is too short`);
    }

    if (countWords(value) > 90) {
      issues.push(`${key.toUpperCase()} is too long`);
    }

    if (countSentences(value) < 2) {
      issues.push(`${key.toUpperCase()} needs at least 2 sentences`);
    }

    if (!mentionsHorseOrSpecifics(value, horseName)) {
      issues.push(`${key.toUpperCase()} lacks specificity`);
    }

    if (countConcreteSignals(value, horseName) < 2) {
      issues.push(`${key.toUpperCase()} needs more concrete facts`);
    }

    const roleIssue = getSectionRoleIssue(key, value);
    if (roleIssue) {
      issues.push(roleIssue);
    }
  }

  const priceIssue = getPriceLogicIssue(sections.verdict);
  if (priceIssue) {
    issues.push(priceIssue);
  }

  if (issues.length > 0) {
    sections.qualityFlag = issues.join(" | ");
  }

  return sections;
}

function parseJsonObject(text: string): Record<string, unknown> | null {
  const direct = tryParseObject(text);
  if (direct) {
    return direct;
  }

  const fenced = text.match(/```(?:json)?\s*([\s\S]*?)```/i);
  if (fenced?.[1]) {
    const parsed = tryParseObject(fenced[1]);
    if (parsed) {
      return parsed;
    }
  }

  const extracted = extractFirstJsonObject(text);
  return extracted ? tryParseObject(extracted) : null;
}

function tryParseObject(value: string): Record<string, unknown> | null {
  try {
    const parsed = JSON.parse(value.trim()) as unknown;
    return parsed && typeof parsed === "object" && !Array.isArray(parsed)
      ? (parsed as Record<string, unknown>)
      : null;
  } catch {
    return null;
  }
}

function extractFirstJsonObject(text: string): string | null {
  const start = text.indexOf("{");
  if (start === -1) {
    return null;
  }

  let depth = 0;
  let inString = false;
  let escaped = false;

  for (let index = start; index < text.length; index += 1) {
    const char = text[index];

    if (inString) {
      if (escaped) {
        escaped = false;
        continue;
      }
      if (char === "\\") {
        escaped = true;
        continue;
      }
      if (char === "\"") {
        inString = false;
      }
      continue;
    }

    if (char === "\"") {
      inString = true;
      continue;
    }

    if (char === "{") {
      depth += 1;
    } else if (char === "}") {
      depth -= 1;
      if (depth === 0) {
        return text.slice(start, index + 1);
      }
    }
  }

  return null;
}

function normalizeStructuredAnalysis(
  value: Record<string, unknown>,
  horseName: string,
): StrideAnalysisResult | null {
  const horse = stringifyValue(firstStructuredValue(value, "horse", "horseName")) || horseName;
  const verdict = normalizeStrideVerdict(firstStructuredValue(value, "verdict", "call", "decision"));
  const confidence = normalizeStrideConfidence(firstStructuredValue(value, "confidence", "confidence_level"));
  const valueFlag = toBoolean(firstStructuredValue(value, "value_flag", "valueFlag", "valueflag"))
    ?? (verdict === "VALUE WATCH");
  const coreSignal = stringifyValue(firstStructuredValue(value, "core_signal", "coreSignal", "signal"));
  const raceShapeAssessment = stringifyValue(firstStructuredValue(
    value,
    "race_shape_assessment",
    "raceShapeAssessment",
    "race_shape",
  ));
  const formDepth = stringifyValue(firstStructuredValue(value, "form_depth", "formDepth", "form"));
  const classAndOpposition = stringifyValue(firstStructuredValue(
    value,
    "class_and_opposition",
    "classAndOpposition",
    "class_opposition",
  ));
  const fitnessSignal = stringifyValue(firstStructuredValue(value, "fitness_signal", "fitnessSignal", "fitness"));
  const marketRead = stringifyValue(firstStructuredValue(value, "market_read", "marketRead", "market"));
  const primaryRisk = stringifyValue(firstStructuredValue(value, "primary_risk", "primaryRisk"));
  const secondaryRisk = stringifyValue(firstStructuredValue(value, "secondary_risk", "secondaryRisk"));
  const winCondition = stringifyValue(firstStructuredValue(value, "win_condition", "winCondition"));
  const tokenCountTarget = stringifyValue(firstStructuredValue(value, "token_count_target", "tokenCountTarget"))
    || "180-240 words across all fields";

  if (
    !horse ||
    !verdict ||
    !confidence ||
    valueFlag == null ||
    !coreSignal ||
    !raceShapeAssessment ||
    !formDepth ||
    !classAndOpposition ||
    !fitnessSignal ||
    !marketRead ||
    !primaryRisk ||
    !secondaryRisk ||
    !winCondition ||
    !tokenCountTarget
  ) {
    return null;
  }

  return {
    horse,
    verdict,
    confidence,
    value_flag: valueFlag,
    core_signal: cleanStructuredText(coreSignal),
    race_shape_assessment: cleanStructuredText(raceShapeAssessment),
    form_depth: cleanStructuredText(formDepth),
    class_and_opposition: cleanStructuredText(classAndOpposition),
    fitness_signal: cleanStructuredText(fitnessSignal),
    market_read: cleanStructuredText(marketRead),
    primary_risk: cleanStructuredText(primaryRisk),
    secondary_risk: cleanStructuredText(secondaryRisk),
    win_condition: cleanStructuredText(winCondition),
    token_count_target: cleanStructuredText(tokenCountTarget),
  };
}

function firstStructuredValue(source: Record<string, unknown>, ...keys: string[]): unknown {
  for (const key of keys) {
    if (key in source && source[key] != null) {
      return source[key];
    }
  }
  return null;
}

function cleanStructuredText(value: string): string {
  return value.replace(/\s+/g, " ").trim();
}

function normalizeStrideVerdict(value: unknown): StrideAnalysisResult["verdict"] | null {
  const normalized = stringifyValue(value).trim().toUpperCase().replace(/\s+/g, " ");
  if (normalized === "BACK" || normalized === "AGAINST" || normalized === "VALUE WATCH" || normalized === "NEEDS SCENARIO") {
    return normalized as StrideAnalysisResult["verdict"];
  }
  if (normalized === "VALUE_WATCH") {
    return "VALUE WATCH";
  }
  if (normalized === "NEEDS_SCENARIO") {
    return "NEEDS SCENARIO";
  }
  return null;
}

function normalizeStrideConfidence(value: unknown): StrideAnalysisResult["confidence"] | null {
  const normalized = stringifyValue(value).trim().toUpperCase();
  if (normalized === "HIGH" || normalized === "MEDIUM" || normalized === "LOW") {
    return normalized as StrideAnalysisResult["confidence"];
  }
  return null;
}

function toBoolean(value: unknown): boolean | null {
  if (typeof value === "boolean") {
    return value;
  }
  if (typeof value === "string") {
    const normalized = value.trim().toLowerCase();
    if (normalized === "true") {
      return true;
    }
    if (normalized === "false") {
      return false;
    }
  }
  return null;
}

function validateStructuredAnalysis(
  analysis: StrideAnalysisResult,
  horseName: string,
): string[] {
  const issues: string[] = [];
  const totalWords = countWords([
    analysis.core_signal,
    analysis.race_shape_assessment,
    analysis.form_depth,
    analysis.class_and_opposition,
    analysis.fitness_signal,
    analysis.market_read,
    analysis.primary_risk,
    analysis.secondary_risk,
    analysis.win_condition,
  ].join(" "));

  if (normalizeName(analysis.horse) !== normalizeName(horseName)) {
    issues.push("Structured analysis named the wrong horse");
  }

  if (totalWords < 140) {
    issues.push("Structured analysis is too brief");
  }
  if (totalWords > 320) {
    issues.push("Structured analysis is too long");
  }

  if (!/(tempo|pace|rail|inside|outside|barrier|draw|gate|map|settle|cover|wide)/i.test(analysis.race_shape_assessment)) {
    issues.push("race_shape_assessment does not explain the map clearly enough");
  }
  if (!/(form|sectional|last-600|last 600|pattern|beat|beater|subsequent|rating|figure|tempo)/i.test(analysis.form_depth)) {
    issues.push("form_depth does not interrogate the form");
  }
  if (!/(danger|market rank|rating|class|group|listed|bm|benchmark|rival|opposition|field)/i.test(analysis.class_and_opposition)) {
    issues.push("class_and_opposition does not benchmark the field clearly enough");
  }
  if (!/(prep|run|second-up|first-up|days|gear|blinkers|weight|fresh|spell|trainer)/i.test(analysis.fitness_signal)) {
    issues.push("fitness_signal does not read the prep strongly enough");
  }
  if (!/(market|price|odds|firm|drift|rank|\$|value|overlay|corroboration)/i.test(analysis.market_read)) {
    issues.push("market_read does not read the market properly");
  }
  if (countNamedEntities(analysis.primary_risk) < 1 && !/(tempo|track|rail|bias|pace|inside|wide|barrier)/i.test(analysis.primary_risk)) {
    issues.push("primary_risk is not specific enough");
  }
  if (countNamedEntities(analysis.secondary_risk) < 1 && !/(tempo|track|rail|bias|pace|inside|wide|barrier)/i.test(analysis.secondary_risk)) {
    issues.push("secondary_risk is not specific enough");
  }
  if (!/(wins|win|lands|if|tempo|track|settle|sectional|run|rail|pace)/i.test(analysis.win_condition)) {
    issues.push("win_condition does not name the winning scenario");
  }
  if (!/180-240/i.test(analysis.token_count_target)) {
    issues.push("token_count_target is incorrect");
  }

  const fullText = [
    analysis.core_signal,
    analysis.race_shape_assessment,
    analysis.form_depth,
    analysis.class_and_opposition,
    analysis.fitness_signal,
    analysis.market_read,
    analysis.primary_risk,
    analysis.secondary_risk,
    analysis.win_condition,
  ].join(" ").toLowerCase();
  const banned = BANNED_PHRASES.filter((phrase) => fullText.includes(phrase));
  if (banned.length > 0) {
    issues.push(`Template phrases detected: ${banned.join(", ")}`);
  }

  return issues;
}

function buildStructuredSections(
  analysis: StrideAnalysisResult,
  issues: string[],
): RunnerAnalysisSections {
  return {
    profile: joinAnalysisParts(analysis.core_signal, analysis.form_depth),
    pace: joinAnalysisParts(analysis.race_shape_assessment, analysis.win_condition),
    condition: joinAnalysisParts(
      analysis.class_and_opposition,
      analysis.fitness_signal,
      analysis.market_read,
    ),
    verdict: joinAnalysisParts(
      `${analysis.verdict} | ${analysis.confidence} confidence${analysis.value_flag ? " | value flagged" : ""}.`,
      analysis.primary_risk,
      analysis.secondary_risk,
    ),
    structured: analysis,
    qualityFlag: issues.length > 0 ? issues.join(" | ") : undefined,
  };
}

function joinAnalysisParts(...parts: Array<string | null | undefined>): string {
  return parts
    .map((value) => cleanStructuredText(value || ""))
    .filter(Boolean)
    .join(" ");
}

function countNamedEntities(value: string): number {
  const multiWord = value.match(/\b[A-Z][A-Za-z'’.-]+(?:\s+[A-Z][A-Za-z'’.-]+)+\b/g) || [];
  const singleWord = value.match(/\b[A-Z][A-Za-z'’.-]{3,}\b/g) || [];
  const stopWords = new Set([
    "RACE",
    "PACE",
    "TRACK",
    "FORM",
    "MARKET",
    "PREDICTION",
    "ANALYSIS",
    "CONDITIONS",
    "FAVOURITE",
  ]);

  const names = new Set<string>();
  multiWord.forEach((entry) => names.add(entry));
  singleWord.forEach((entry) => {
    if (!stopWords.has(entry.toUpperCase())) {
      names.add(entry);
    }
  });

  return names.size;
}

function extractSection(text: string, sectionName: string): string {
  const regex = new RegExp(`${sectionName}\\s*:\\s*([\\s\\S]*?)(?=\\n[A-Z]{4,}\\s*:|$)`, "i");
  const match = text.match(regex);
  return match ? match[1].trim().replace(/\s+/g, " ") : "";
}

function mentionsHorseOrSpecifics(value: string, horseName: string): boolean {
  const normalizedHorse = normalizeName(horseName);
  const normalizedValue = normalizeName(value);
  if (normalizedValue.includes(normalizedHorse)) {
    return true;
  }

  return /\d/.test(value) || /(barrier|gate|track|distance|odds|price|first-up|spell|form|tempo|pace|weight|market|draw)/i.test(value);
}

function countSentences(value: string): number {
  return value
    .split(/[.!?]+/)
    .map((segment) => segment.trim())
    .filter(Boolean).length;
}

function countConcreteSignals(value: string, horseName: string): number {
  let score = 0;
  const normalizedHorse = normalizeName(horseName);
  const normalizedValue = normalizeName(value);

  if (normalizedHorse && normalizedValue.includes(normalizedHorse)) {
    score += 1;
  }
  if ((value.match(/\b\d+(?:\.\d+)?\b/g) || []).length >= 1) {
    score += 1;
  }
  if (/\$\d/.test(value)) {
    score += 1;
  }
  if (/(barrier|gate|draw|tempo|pace|speed|lead|settle|map|track|distance|heavy|soft|good|class|weight|first-up|spell|prep|market|odds|jockey|trainer|gear|blinkers)/i.test(value)) {
    score += 1;
  }
  if (/\b(?:Warwick Farm|Rosehill|Randwick|Sandown|Flemington|Caulfield|Moonee Valley|Eagle Farm|Doomben)\b/i.test(value)) {
    score += 1;
  }

  return score;
}

function getSectionRoleIssue(sectionKey: string, value: string): string | null {
  const upper = sectionKey.toUpperCase();

  if (
    upper === "PACE" &&
    (
      !/(barrier|gate|draw)/i.test(value) ||
      !/(pace|tempo|speed|lead|settle|map|pattern|on-pace|backmarker|midfield)/i.test(value) ||
      !/(track|turn|bend|straight|cover|rail|traffic|wide|inside|field|rival)/i.test(value)
    )
  ) {
    return "PACE does not explain the race map";
  }

  if (
    upper === "CONDITION" &&
    countPatternHits(value, /(track|distance|going|heavy|soft|good|class|first-up|spell|prep|fitness|trial|gear|blinkers|weight)/gi) < 2
  ) {
    return "CONDITION does not cover the key suitability factors";
  }

  if (
    upper === "VERDICT" &&
    !/(can win|wins|backable|main danger|value|short enough|hard to back|risk|eliminate|no bet|better than the price|too short|out of depth|can't win|won't win)/i.test(value)
  ) {
    return "VERDICT does not make a committed betting call";
  }

  if (
    upper === "VERDICT" &&
    /(place|placing|place-only|each-way|each way|saver|exotic|quinella|exacta|trifecta)/i.test(value)
  ) {
    return "VERDICT must stay in the win market only";
  }

  return null;
}

function getPriceLogicIssue(verdict: string): string | null {
  const priceMatch = verdict.match(/\$([0-9]+(?:\.[0-9]+)?)/);
  if (!priceMatch) {
    return null;
  }

  const price = Number(priceMatch[1]);
  if (!Number.isFinite(price)) {
    return null;
  }

  if (price >= 10 && /(too short|under the odds|underpriced)/i.test(verdict)) {
    return "VERDICT uses incoherent price logic";
  }

  if (price <= 2.2 && /\bvalue\b/i.test(verdict)) {
    return "VERDICT uses incoherent price logic";
  }

  return null;
}

function countPatternHits(value: string, pattern: RegExp): number {
  const matches = value.match(pattern);
  if (!matches) {
    return 0;
  }
  return new Set(matches.map((match) => match.toLowerCase())).size;
}

function escapeRegExp(value: string): string {
  return value.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}

function buildFieldSnapshot(
  race: PreparedRaceContext,
  runner: PreparedRunner,
  rivals: RivalSummary[],
): string {
  const currentLine = `${runner.horseName}: Barrier ${displayNumber(runner.barrier)}, ${displayOdds(runner.currentOdds)}, ${stringifyValue(runner.mergedRunner.runningStyle) || "Unknown style"}, model win ${displayPercent(toNumber(runner.mergedRunner.modelWinPct))}, form ${stringifyValue(runner.mergedRunner.formString) || "none"}`;

  const rivalLines = rivals.map(
    (rival) =>
      `${rival.name}: Barrier ${displayNumber(rival.barrier)}, ${displayOdds(rival.odds)}, ${rival.runningStyle}, market rank ${rival.marketRank ?? "NA"}, form ${rival.recentForm}`,
  );

  return [currentLine, ...rivalLines].join("\n");
}

function mergeRunners(
  modelRunners: Record<string, unknown>[],
  racecardRunners: Record<string, unknown>[],
  raceDate: string,
): PreparedRunner[] {
  const byName = new Map<string, PreparedRunner>();

  const ensure = (horseName: string) => {
    const normalizedHorseName = normalizeName(horseName);
    const existing = byName.get(normalizedHorseName);
    if (existing) {
      return existing;
    }

    const created: PreparedRunner = {
      horseId: null,
      horseName,
      normalizedHorseName,
      saddleClothNumber: null,
      barrier: null,
      currentOdds: null,
      marketRank: null,
      runningStyle: "Unknown",
      modelRunner: null,
      racecardRunner: null,
      mergedRunner: {},
      statsSummary: {},
    };
    byName.set(normalizedHorseName, created);
    return created;
  };

  for (const modelRunner of modelRunners) {
    const horseName = firstString(modelRunner.horse, modelRunner.name, "");
    if (!horseName) {
      continue;
    }

    const entry = ensure(horseName);
    entry.modelRunner = modelRunner;
  }

  for (const racecardRunner of racecardRunners) {
    const horseName = firstString(racecardRunner.horse, "");
    if (!horseName) {
      continue;
    }

    const entry = ensure(horseName);
    entry.racecardRunner = racecardRunner;
  }

  const prepared: PreparedRunner[] = [];
  for (const entry of Array.from(byName.values())) {
    const modelRunner = entry.modelRunner || {};
    const racecardRunner = entry.racecardRunner || {};
    const stats = toRecord(racecardRunner.stats);
    const courseStats = toRecord(stats.course_stats);
    const distanceStats = toRecord(stats.distance_stats);
    const courseDistanceStats = toRecord(stats.course_distance_stats);
    const goodStats = toRecord(stats.ground_good_stats);
    const softStats = toRecord(stats.ground_soft_stats);
    const heavyStats = toRecord(stats.ground_heavy_stats);
    const jockeyStats = toRecord(stats.jockey_stats);

    const bestAvailableOdds = bestOddsFromRacecard(racecardRunner);
    const currentOdds = firstNumber(modelRunner.odds, sportsbetOdds(racecardRunner), bestAvailableOdds);
    const lastRaced = stringifyValue(stats.last_raced);
    const daysSinceLastRun = lastRaced ? diffDays(lastRaced, raceDate) : null;
    const spellLengthWeeks = daysSinceLastRun != null && daysSinceLastRun >= 84
      ? Math.round(daysSinceLastRun / 7)
      : null;
    const formString = firstString(modelRunner.form, racecardRunner.form, "");
    const isDebutant = !formString && !lastRaced;
    const runningStyle = firstString(modelRunner.running_style, "Unknown");
    const horseAge = parseAge(firstString(racecardRunner.age, ""));
    const jockeyClaim = toNumber(racecardRunner.jockey_claim);
    const weightCarried = parseWeightKg(firstString(racecardRunner.weight, ""));
    const winsAtTrack = statCount(courseStats.first);
    const placesAtTrack = statCount(courseStats.second) + statCount(courseStats.third);
    const startsAtTrack = statCount(courseStats.total);
    const winsAtDistance = statCount(distanceStats.first);
    const placesAtDistance = statCount(distanceStats.second) + statCount(distanceStats.third);
    const startsAtDistance = statCount(distanceStats.total);
    const runsThisPrep = deriveRunsThisPrep(formString);
    const currentRaceClass = firstString(modelRunner.current_race_class, racecardRunner.class, "");
    const lastRaceClass = firstString(modelRunner.last_race_class, "");

    entry.horseName = firstString(racecardRunner.horse, modelRunner.horse, entry.horseName);
    entry.horseId = firstString(racecardRunner.horse_id, modelRunner.horse_id, "") || null;
    entry.saddleClothNumber = firstNumber(racecardRunner.number, modelRunner.saddle_number, null);
    entry.barrier = firstNumber(racecardRunner.draw, modelRunner.barrier, null);
    entry.currentOdds = currentOdds;
    entry.runningStyle = runningStyle;
    entry.statsSummary = {
      trackRecord: formatRecord(startsAtTrack, winsAtTrack, placesAtTrack),
      distanceRecord: formatRecord(startsAtDistance, winsAtDistance, placesAtDistance),
      goingRecord: formatGoingRecords(goodStats, softStats, heavyStats),
      lastRaced: lastRaced || "Unavailable",
      jockeyRecord: formatRecord(
        statCount(jockeyStats.total),
        statCount(jockeyStats.first),
        statCount(jockeyStats.second) + statCount(jockeyStats.third),
      ),
      courseDistanceRecord: formatRecord(
        statCount(courseDistanceStats.total),
        statCount(courseDistanceStats.first),
        statCount(courseDistanceStats.second) + statCount(courseDistanceStats.third),
      ),
    };
    entry.mergedRunner = {
      horseName: entry.horseName,
      horseId: entry.horseId,
      horseAge,
      horseSex: firstString(racecardRunner.sex, ""),
      horseColour: firstString(racecardRunner.colour, ""),
      barrier: entry.barrier,
      saddleClothNumber: entry.saddleClothNumber,
      jockey: firstString(racecardRunner.jockey, modelRunner.jockey, ""),
      jockeyClaim,
      trainer: firstString(racecardRunner.trainer, modelRunner.trainer, ""),
      weightCarried,
      currentOdds,
      openingOdds: null,
      bestAvailableOdds,
      marketMovement: "Unavailable",
      marketRank: null,
      impliedWinProbability: currentOdds && currentOdds > 1 ? Number((1 / currentOdds).toFixed(4)) : null,
      modelWinPct: toNumber(modelRunner.win_pct),
      modelPlacePct: toNumber(modelRunner.place_pct),
      rawModelPct: toNumber(modelRunner.raw_model_pct),
      edgePct: toNumber(modelRunner.edge_pct),
      selectionScore: toNumber(modelRunner.selection_score),
      formString,
      careerWinPercent: toNumber(stats.career_win_percent),
      careerPlacePercent: toNumber(stats.career_place_percent),
      careerPrize: toNumber(stats.career_prize),
      startsAtThisTrack: startsAtTrack,
      winsAtThisTrack: winsAtTrack,
      placesAtThisTrack: placesAtTrack,
      startsAtThisDistance: startsAtDistance,
      winsAtThisDistance: winsAtDistance,
      placesAtThisDistance: placesAtDistance,
      startsOnGood: statCount(goodStats.total),
      winsOnGood: statCount(goodStats.first),
      startsOnSoft: statCount(softStats.total),
      winsOnSoft: statCount(softStats.first),
      startsOnHeavy: statCount(heavyStats.total),
      winsOnHeavy: statCount(heavyStats.first),
      preferredGoing: derivePreferredGoing(goodStats, softStats, heavyStats),
      daysSinceLastRun,
      runsThisPrep,
      spellLengthWeeks,
      trialResult: null,
      trialsThisPrep: null,
      workReports: "Unavailable",
      gearChanges: [],
      currentGear: [],
      gearHistory: "Unavailable",
      barrierDrawHistory: "Unavailable",
      lastRaceClass,
      currentRaceClass,
      classChange: "Unavailable",
      sire: firstString(racecardRunner.sire, ""),
      dam: firstString(racecardRunner.dam, ""),
      damSire: firstString(racecardRunner.dam_sire, ""),
      breedingDistanceOptimal: "Unavailable",
      runningStyle,
      runningStyleConfidence: runningStyle.toLowerCase() === "unknown" ? "Low" : "Medium",
      averageLast600m: null,
      bestLast600m: null,
      averageLast200m: null,
      averageSPvsMarket: null,
      isFirstUp: Boolean(modelRunner.is_first_up),
      isImproving: Boolean(modelRunner.is_improving),
      isTipped: Boolean(modelRunner.is_tipped),
      tipRank: toNumber(modelRunner.tip_rank),
      confidence: firstString(modelRunner.confidence, ""),
      weightedFormScore: toNumber(modelRunner.weighted_form_score),
      lastRaced,
      rating: firstString(racecardRunner.rating, ""),
      owner: firstString(racecardRunner.owner, ""),
      comment: firstString(racecardRunner.comment, ""),
      isDebutant,
    };

    prepared.push(entry);
  }

  return prepared;
}

function assignMarketRanks(runners: PreparedRunner[]): PreparedRunner[] {
  const ordered = [...runners].sort((a, b) => {
    const aOdds = a.currentOdds ?? Number.POSITIVE_INFINITY;
    const bOdds = b.currentOdds ?? Number.POSITIVE_INFINITY;
    if (aOdds !== bOdds) {
      return aOdds - bOdds;
    }
    return a.horseName.localeCompare(b.horseName);
  });

  ordered.forEach((runner, index) => {
    const rank = runner.currentOdds != null ? index + 1 : null;
    runner.marketRank = rank;
    runner.mergedRunner.marketRank = rank;
  });

  return ordered;
}

function deriveRaceTrackBias(prepared: PreparedRace): string {
  const racecardRace = toRecord(prepared.race.rawRaceData.racecardRace);
  const tipsRace = toRecord(prepared.race.rawRaceData.tipsRace);
  const explicitBias = firstString(
    racecardRace.trackBias,
    racecardRace.track_bias,
    tipsRace.trackBias,
    tipsRace.track_bias,
    "",
  );

  if (explicitBias) {
    return explicitBias;
  }

  const summaries = prepared.runners
    .map((runner) => deriveRunnerTrackBiasSummary(runner))
    .filter(Boolean)
    .join(" | ");

  return summaries || "Not reported";
}

function deriveRunnerTrackBiasSummary(runner: PreparedRunner): string {
  const merged = runner.mergedRunner;
  const modelRunner = toRecord(runner.modelRunner);
  const mcData = toRecord(modelRunner._mc_data);
  const summary = stringifyValue(mcData.trackBiasSummary);
  if (summary) {
    return `${runner.horseName}: ${summary}`;
  }

  const points = firstNumber(modelRunner.trackBiasPoints, mcData.trackBiasPoints, null);
  const fit = firstString(modelRunner.trackBiasFit, mcData.trackBiasFit, "");
  if (points != null || fit) {
    return `${runner.horseName}: ${fit || "track fit"} ${points != null ? `(${points > 0 ? "+" : ""}${points}pts)` : ""}`.trim();
  }

  const barrier = toNumber(merged.barrier);
  if (barrier != null && barrier <= 3) {
    return `${runner.horseName}: inside draw`;
  }
  if (barrier != null && barrier >= 10) {
    return `${runner.horseName}: wide draw`;
  }
  return "";
}

function normalizeRunningStyleForOverview(value: string | null | undefined): "Leader" | "On-pace" | "Midfield" | "Backmarker" | "Unknown" {
  const normalized = normalizeName(value || "");
  if (!normalized) {
    return "Unknown";
  }
  if (normalized.includes("leader") || normalized.includes("speed")) {
    return "Leader";
  }
  if (normalized.includes("onpace") || normalized.includes("stalker") || normalized.includes("forward")) {
    return "On-pace";
  }
  if (normalized.includes("midfield")) {
    return "Midfield";
  }
  if (normalized.includes("backmarker") || normalized.includes("closer")) {
    return "Backmarker";
  }
  return "Unknown";
}

function deriveRivalKeyFact(runner: PreparedRunner): string {
  const merged = runner.mergedRunner;
  const winsAtTrack = toNumber(merged.winsAtThisTrack) ?? 0;
  const winsAtDistance = toNumber(merged.winsAtThisDistance) ?? 0;
  const edge = toNumber(merged.edgePct) ?? 0;
  const barrier = toNumber(merged.barrier);
  const style = stringifyValue(merged.runningStyle)?.toLowerCase() || "unknown";
  const daysSinceLastRun = toNumber(merged.daysSinceLastRun);

  if ((merged.isDebutant as boolean) === true) {
    return "Debutant - profile rests on stable intent, draw and market confidence";
  }
  if ((merged.isFirstUp as boolean) === true && (merged.spellLengthWeeks as number | null) != null) {
    return `First-up from a ${merged.spellLengthWeeks}-week spell`;
  }
  if (winsAtTrack >= 2) {
    return `${winsAtTrack} wins at this track`;
  }
  if (winsAtDistance >= 2) {
    return `${winsAtDistance} wins at this distance`;
  }
  if (edge >= 3) {
    return `Model edge ${edge > 0 ? "+" : ""}${edge.toFixed(1)}%`;
  }
  if (barrier != null && barrier >= 10 && /backmarker|closer/.test(style)) {
    return "Wide gate hurts this pattern";
  }
  if (daysSinceLastRun != null && daysSinceLastRun <= 14) {
    return `Backs up quickly after ${daysSinceLastRun} days`;
  }
  return `Form ${stringifyValue(merged.formString) || "not exposed"}`;
}

function deriveLikelyPaceScenario(runners: PreparedRunner[]): string {
  const leaders = runners.filter((runner) => {
    const style = runner.runningStyle.toLowerCase();
    return style.includes("leader") || style.includes("speed") || style.includes("on_pace");
  });

  const leaderNames = leaders
    .slice(0, 4)
    .map((runner) => runner.horseName);

  if (runners.length <= 3) {
    if (leaderNames.length === 0) {
      return "Small field, tactical race with no obvious leader";
    }
    if (leaderNames.length === 1) {
      return `Small field, tactical race - ${leaderNames[0]} looks the lone leader`;
    }
    return `Small field, tactical race - ${leaderNames.join(", ")} should sort the lead out early`;
  }

  if (leaderNames.length >= 3) {
    return `Genuine speed - ${leaderNames.join(", ")} likely roll forward`;
  }
  if (leaderNames.length === 2) {
    return `Solid tempo - ${leaderNames.join(" and ")} should keep each other honest`;
  }
  if (leaderNames.length === 1) {
    return `Moderate tempo - ${leaderNames[0]} may control the lead`;
  }
  return "Tactical tempo - no obvious speed horse exposed in the data";
}

function deriveWeightType(raceClass: string): string {
  const normalized = raceClass.toLowerCase();
  if (normalized.includes("hcp") || normalized.includes("handicap")) {
    return "Handicap";
  }
  if (normalized.includes("plate")) {
    return "Plate";
  }
  if (normalized.includes("mdn") || normalized.includes("maiden")) {
    return "Maiden conditions";
  }
  if (normalized.includes("set weights")) {
    return "Set weights";
  }
  return "Unknown";
}

function deriveAgeRestriction(raceClass: string, runners: PreparedRunner[]): string {
  const normalized = raceClass.toLowerCase();
  if (normalized.includes("2y")) return "2yo";
  if (normalized.includes("3y")) return "3yo";
  if (normalized.includes("4y")) return "4yo";

  const ages = new Set(
    runners
      .map((runner) => toNumber(runner.mergedRunner.horseAge))
      .filter((age): age is number => age != null),
  );
  const ageList = Array.from(ages);
  return ageList.length === 1 ? `${ageList[0]}yo` : "Open";
}

function deriveSexRestriction(runners: PreparedRunner[]): string {
  const sexes = new Set(
    runners
      .map((runner) => stringifyValue(runner.mergedRunner.horseSex))
      .filter((sex): sex is string => Boolean(sex)),
  );

  const sexList = Array.from(sexes);

  if (sexList.length === 1) {
    return sexList[0];
  }

  if (sexList.every((sex) => sex.toLowerCase().includes("filly") || sex.toLowerCase().includes("mare"))) {
    return "Fillies and mares";
  }

  return "Open";
}

function derivePreferredGoing(
  goodStats: Record<string, unknown>,
  softStats: Record<string, unknown>,
  heavyStats: Record<string, unknown>,
): string {
  const candidates = [
    { label: "Good", total: statCount(goodStats.total), wins: statCount(goodStats.first) },
    { label: "Soft", total: statCount(softStats.total), wins: statCount(softStats.first) },
    { label: "Heavy", total: statCount(heavyStats.total), wins: statCount(heavyStats.first) },
  ].filter((entry) => entry.total > 0);

  if (candidates.length === 0) {
    return "Unavailable";
  }

  candidates.sort((a, b) => {
    const aRate = a.total > 0 ? a.wins / a.total : 0;
    const bRate = b.total > 0 ? b.wins / b.total : 0;
    if (aRate !== bRate) return bRate - aRate;
    return b.total - a.total;
  });

  return candidates[0].label;
}

function deriveRunsThisPrep(form: string): number | null {
  if (!form) {
    return null;
  }

  const segments = form.split("-");
  const currentPrep = segments[segments.length - 1] || "";
  const digits = currentPrep.replace(/[^0-9xX]/g, "");
  return digits.length > 0 ? digits.length : null;
}

function formatRaceTime(offTime: string): string {
  if (!offTime) {
    return "Unavailable";
  }

  const date = new Date(offTime);
  if (Number.isNaN(date.getTime())) {
    return "Unavailable";
  }

  return date.toLocaleTimeString("en-AU", {
    hour: "2-digit",
    minute: "2-digit",
    timeZone: "Australia/Sydney",
    timeZoneName: "short",
  });
}

function getTrackConfig(track: string): string {
  const normalized = normalizeTrackName(track);
  for (const [key, value] of Object.entries(TRACK_CONFIGS)) {
    if (normalized.includes(normalizeTrackName(key))) {
      return value;
    }
  }
  return "Track configuration unavailable - treat barrier and map assumptions more cautiously";
}

function findTipsRace(
  tipsData: unknown,
  track: string,
  raceNumber: number,
): Record<string, unknown> | null {
  const races = toRecord(tipsData)?.races;
  if (!Array.isArray(races)) {
    return null;
  }

  const normalizedTrack = normalizeTrackName(track);
  return (
    (races as Record<string, unknown>[]).find((race) => {
      const raceTrack = normalizeTrackName(firstString(race.track, ""));
      const raceNo = firstNumber(race.race_number, race.raceNumber, null);
      return raceTrack === normalizedTrack && raceNo === raceNumber;
    }) || null
  );
}

function findRacecardRace(
  racecardData: unknown,
  track: string,
  raceNumber: number,
): Record<string, unknown> | null {
  const meets = Array.isArray(racecardData)
    ? (racecardData as Record<string, unknown>[])
    : Array.isArray(toRecord(racecardData)?.meets)
      ? (toRecord(racecardData)?.meets as Record<string, unknown>[])
      : [];

  const normalizedTrack = normalizeTrackName(track);

  for (const meet of meets) {
    if (normalizeTrackName(firstString(meet.course, "")) !== normalizedTrack) {
      continue;
    }

    const races = Array.isArray(meet.races) ? (meet.races as Record<string, unknown>[]) : [];
    const found = races.find((race) => firstNumber(race.race_number, null, null) === raceNumber);
    if (found) {
      return found;
    }
  }

  return null;
}

function readJsonFile(filePath: string): unknown {
  if (!fs.existsSync(filePath)) {
    return null;
  }

  return JSON.parse(fs.readFileSync(filePath, "utf8"));
}

function stripLargeRaceArrays(race: Record<string, unknown> | null): Record<string, unknown> | null {
  if (!race) {
    return null;
  }

  const { runners, full_field, top_picks, raw_model_leader, bet_pick, coverage_pick, ...rest } = race;
  return rest;
}

function sanitizePromptPayload(value: unknown): unknown {
  if (Array.isArray(value)) {
    return value.map((entry) => sanitizePromptPayload(entry));
  }

  if (value && typeof value === "object") {
    const record = value as Record<string, unknown>;
    const sanitized: Record<string, unknown> = {};

    for (const [key, entry] of Object.entries(record)) {
      if (LEGACY_GENERATED_FIELDS.has(normalizeName(key))) {
        continue;
      }
      sanitized[key] = sanitizePromptPayload(entry);
    }

    return sanitized;
  }

  return value;
}

function normalizeTrackName(value: string): string {
  return value.toLowerCase().replace(/[^a-z0-9]/g, "");
}

function normalizeName(value: string): string {
  return value.toLowerCase().replace(/[^a-z0-9]/g, "");
}

function normalizedSqlText(expression: string): string {
  return `regexp_replace(lower(coalesce(${expression}, '')), '[^a-z0-9]+', '', 'g')`;
}

function buildSectionalTrackMatchSql(sectionalTrackExpr: string, historyTrackExpr: string): string {
  const left = normalizedSqlText(sectionalTrackExpr);
  const right = normalizedSqlText(historyTrackExpr);
  return `(${left} = ${right} OR ${left} LIKE '%' || ${right} || '%' OR ${right} LIKE '%' || ${left} || '%')`;
}

function buildSectionalHorseMatchSql(sectionalHorseExpr: string, historyHorseExpr: string): string {
  return `${normalizedSqlText(sectionalHorseExpr)} = ${normalizedSqlText(historyHorseExpr)}`;
}

function buildSectionalFallbackJoinSql(sectionalAlias: string, historyAlias: string): string {
  return [
    `${sectionalAlias}.race_date::date = ${historyAlias}.race_date::date`,
    `${sectionalAlias}.race_number = ${historyAlias}.race_number`,
    buildSectionalTrackMatchSql(`${sectionalAlias}.track`, `${historyAlias}.track`),
    buildSectionalHorseMatchSql(`${sectionalAlias}.horse_name`, `${historyAlias}.horse_name`),
  ].join(" AND ");
}

function sportsbetOdds(racecardRunner: Record<string, unknown>): MaybeNumber {
  const odds = Array.isArray(racecardRunner.odds) ? (racecardRunner.odds as Record<string, unknown>[]) : [];
  const sportsbet = odds.find((entry) => firstString(entry.bookmaker, "").toLowerCase() === "sportsbet");
  return toNumber(sportsbet?.win_odds);
}

function bestOddsFromRacecard(racecardRunner: Record<string, unknown>): MaybeNumber {
  const odds = Array.isArray(racecardRunner.odds) ? (racecardRunner.odds as Record<string, unknown>[]) : [];
  const values = odds
    .map((entry) => toNumber(entry.win_odds))
    .filter((value): value is number => value != null);

  return values.length > 0 ? Math.max(...values) : null;
}

function parseDistanceMetres(value: string): number | null {
  const match = value.match(/(\d{3,4})/);
  return match ? Number(match[1]) : null;
}

function parseAge(value: string): number | null {
  const match = value.match(/(\d+)/);
  return match ? Number(match[1]) : null;
}

function parseWeightKg(value: string): number | null {
  const match = value.match(/(\d+(?:\.\d+)?)/);
  return match ? Number(match[1]) : null;
}

function diffDays(fromDate: string, toDate: string): number | null {
  const from = new Date(fromDate);
  const to = new Date(toDate);
  if (Number.isNaN(from.getTime()) || Number.isNaN(to.getTime())) {
    return null;
  }
  return Math.round((to.getTime() - from.getTime()) / (1000 * 60 * 60 * 24));
}

function formatRecord(starts: number, wins: number, places: number): string {
  if (starts <= 0) {
    return "No exposed record";
  }
  return `${starts} starts, ${wins} wins, ${places} minor placings`;
}

function formatGoingRecords(
  goodStats: Record<string, unknown>,
  softStats: Record<string, unknown>,
  heavyStats: Record<string, unknown>,
): string {
  const parts = [
    formatGoingLine("Good", goodStats),
    formatGoingLine("Soft", softStats),
    formatGoingLine("Heavy", heavyStats),
  ].filter(Boolean);

  return parts.length > 0 ? parts.join(" | ") : "No exposed going record";
}

function formatGoingLine(label: string, stats: Record<string, unknown>): string {
  const total = statCount(stats.total);
  if (total <= 0) {
    return "";
  }
  const wins = statCount(stats.first);
  const placings = statCount(stats.second) + statCount(stats.third);
  return `${label} ${total}:${wins}-${placings}`;
}

function statCount(value: unknown): number {
  const parsed = toNumber(value);
  return parsed ?? 0;
}

function countWords(value: string): number {
  return value.trim().split(/\s+/).filter(Boolean).length;
}

function oddsChangedSignificantly(previous: MaybeNumber, current: MaybeNumber): boolean {
  if (previous == null && current == null) {
    return false;
  }
  if (previous == null || current == null) {
    return true;
  }

  const absolute = Math.abs(previous - current);
  const relative = absolute / Math.max(previous, current);
  return absolute >= 1 || relative >= 0.1;
}

function getAnalysisCacheKey(request: RunnerAnalysisRequest): string {
  return [
    request.raceDate,
    request.raceNumber,
    normalizeTrackName(request.track),
    normalizeName(request.horseName),
  ].join("|");
}

function getRaceOverviewCacheKey(prepared: PreparedRace): string {
  return [
    prepared.race.raceDate,
    prepared.race.raceNumber,
    normalizeTrackName(prepared.race.track),
    buildRaceFieldSnapshotHash(prepared),
    "overview",
  ].join("|");
}

function getRaceShapeCacheKey(prepared: PreparedRace): string {
  return [
    prepared.race.raceDate,
    prepared.race.raceNumber,
    normalizeTrackName(prepared.race.track),
    buildRaceFieldSnapshotHash(prepared),
    "shape",
  ].join("|");
}

function buildRaceFieldSnapshotHash(prepared: PreparedRace): string {
  const snapshot = {
    raceDate: prepared.race.raceDate,
    track: normalizeTrackName(prepared.race.track),
    raceNumber: prepared.race.raceNumber,
    going: prepared.race.going,
    railPosition: prepared.race.railPosition,
    runners: prepared.runners.map((runner) => ({
      horse: runner.normalizedHorseName,
      horseId: runner.horseId,
      barrier: runner.barrier,
      jockey: stringifyValue(runner.mergedRunner.jockey),
      trainer: stringifyValue(runner.mergedRunner.trainer),
      weight: toNumber(runner.mergedRunner.weightCarried),
      odds: runner.currentOdds,
      runningStyle: runner.runningStyle,
    })),
  };

  return createHash("sha1").update(JSON.stringify(snapshot)).digest("hex").slice(0, 12);
}

function compareMarketRank(a: number | null, b: number | null): number {
  if (a == null && b == null) return 0;
  if (a == null) return 1;
  if (b == null) return -1;
  return a - b;
}

function firstString(...values: unknown[]): string {
  for (const value of values) {
    if (typeof value === "string" && value.trim()) {
      return value.trim();
    }
  }
  return "";
}

function firstNumber(...values: unknown[]): number | null {
  for (const value of values) {
    const parsed = toNumber(value);
    if (parsed != null) {
      return parsed;
    }
  }
  return null;
}

function toNumber(value: unknown): number | null {
  if (typeof value === "number" && Number.isFinite(value)) {
    return value;
  }
  if (typeof value === "string" && value.trim()) {
    const cleaned = value.replace(/[^0-9.+-]/g, "");
    if (!cleaned) {
      return null;
    }
    const parsed = Number(cleaned);
    return Number.isFinite(parsed) ? parsed : null;
  }
  return null;
}

function toRecord(value: unknown): Record<string, unknown> {
  return value && typeof value === "object" && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : {};
}

function stringifyValue(value: unknown): string {
  if (value == null) {
    return "";
  }
  if (typeof value === "string") {
    return value.trim();
  }
  if (typeof value === "number" || typeof value === "boolean") {
    return String(value);
  }
  return "";
}

function stringifyList(value: unknown): string {
  if (!Array.isArray(value)) {
    return "";
  }
  const items = value.map((entry) => stringifyValue(entry)).filter(Boolean);
  return items.join(", ");
}

function toStringArray(value: unknown): string[] {
  if (!Array.isArray(value)) {
    return [];
  }

  return value
    .map((entry) => stringifyValue(entry))
    .filter(Boolean);
}

function nullIfUnavailable(value: string | null | undefined): string | null {
  if (!value) {
    return null;
  }

  const trimmed = value.trim();
  if (!trimmed) {
    return null;
  }

  if (/^(unavailable|unknown|not reported|n\/a)$/i.test(trimmed)) {
    return null;
  }

  return trimmed;
}

function displayOdds(value: MaybeNumber): string {
  return value != null ? `$${value.toFixed(2)}` : "Market suspended";
}

function displayPercent(value: MaybeNumber): string {
  return value != null ? `${value.toFixed(1)}%` : "Unavailable";
}

function displaySignedPercent(value: MaybeNumber): string {
  if (value == null) {
    return "Unavailable";
  }
  return `${value > 0 ? "+" : ""}${value.toFixed(1)}%`;
}

function displayNumber(value: MaybeNumber): string {
  return value != null ? `${value}` : "Unavailable";
}

function displayWeight(value: MaybeNumber): string {
  return value != null ? `${value.toFixed(1)}kg` : "Unavailable";
}

function displayWeeks(value: MaybeNumber): string {
  return value != null ? `${value} weeks` : "Unavailable";
}

function maxByNumber(values: Array<number | null>): number | null {
  const filtered = values.filter((value): value is number => value != null);
  return filtered.length > 0 ? Math.max(...filtered) : null;
}

function minByNumber(values: Array<number | null>): number | null {
  const filtered = values.filter((value): value is number => value != null);
  return filtered.length > 0 ? Math.min(...filtered) : null;
}

function sleep(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms));
}
