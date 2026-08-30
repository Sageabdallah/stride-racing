import "dotenv/config";
import fs from "fs";
import path from "path";
import { fileURLToPath } from "url";

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);
const projectRoot = path.resolve(__dirname, "..");
const racecardsDir = path.join(projectRoot, "racecards");
const outputDir = path.join(projectRoot, "data", "forward-testing");

const RACING_API_BASE_URL = "https://api.theracingapi.com";
const RACING_API_USERNAME = process.env.RACING_API_USERNAME;
const RACING_API_PASSWORD = process.env.RACING_API_PASSWORD || "";

if (!RACING_API_USERNAME) {
  throw new Error("RACING_API_USERNAME is required");
}

const authHeader = `Basic ${Buffer.from(`${RACING_API_USERNAME}:${RACING_API_PASSWORD}`).toString("base64")}`;
const wait = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

function normalizeName(value) {
  return String(value || "")
    .toLowerCase()
    .replace(/&/g, "and")
    .replace(/[^a-z0-9]+/g, "")
    .trim();
}

function formatLabel(date) {
  return new Date(`${date}T00:00:00`).toLocaleDateString("en-AU", {
    weekday: "long",
    day: "numeric",
    month: "long",
    year: "numeric",
  });
}

function parseStakeUnits(staking) {
  const match = String(staking || "").match(/([\d.]+)\s*u/i);
  return match ? Number(match[1]) : 0;
}

function toNumber(value) {
  if (value == null || value === "") return null;
  const numeric = Number(String(value).replace(/[^\d.-]/g, ""));
  return Number.isFinite(numeric) ? numeric : null;
}

function toPosition(value) {
  const numeric = toNumber(value);
  if (!numeric) return null;
  return Math.trunc(numeric);
}

function cleanComment(comment) {
  if (!comment) return null;
  return String(comment)
    .replace(/\s+/g, " ")
    .replace(/\s*#\s*/g, ". ")
    .trim();
}

function extractStewardsSnippet(comment) {
  if (!comment) return null;
  const bracketed = String(comment).match(/\[([^\]]+)\]/);
  if (bracketed?.[1]) {
    return bracketed[1].trim();
  }

  const cleaned = cleanComment(comment);
  if (!cleaned) return null;

  return cleaned
    .split(". ")
    .slice(0, 2)
    .join(". ")
    .trim();
}

function buildStewardsSummary(selectedRunner, winnerRunner, selectedWon) {
  const selectedNote = extractStewardsSnippet(selectedRunner?.comment);
  const winnerNote = extractStewardsSnippet(winnerRunner?.comment);

  if (selectedWon && selectedNote) {
    return `${selectedRunner.horse} won after ${selectedNote}`;
  }

  if (selectedNote && winnerNote && winnerRunner?.horse && selectedRunner?.horse) {
    return `${selectedRunner.horse}: ${selectedNote} Winner ${winnerRunner.horse}: ${winnerNote}`;
  }

  if (selectedNote && selectedRunner?.horse) {
    return `${selectedRunner.horse}: ${selectedNote}`;
  }

  if (winnerNote && winnerRunner?.horse) {
    return `Winner ${winnerRunner.horse}: ${winnerNote}`;
  }

  return "Stewards note unavailable from the stored result feed for this race.";
}

async function fetchJson(endpoint, attempt = 0) {
  const response = await fetch(`${RACING_API_BASE_URL}${endpoint}`, {
    headers: {
      Authorization: authHeader,
      Accept: "application/json",
      "User-Agent": "Race-Analytics/forward-test-builder",
    },
  });

  if (response.status === 429 && attempt < 5) {
    const retryAfter = Number(response.headers.get("retry-after")) || (attempt + 1) * 2;
    await wait(retryAfter * 1000);
    return fetchJson(endpoint, attempt + 1);
  }

  if (!response.ok) {
    const body = await response.text();
    throw new Error(`Failed ${endpoint}: ${response.status} ${body.slice(0, 200)}`);
  }

  return response.json();
}

function getMetDataShape(payload) {
  if (Array.isArray(payload)) return payload;
  if (Array.isArray(payload?.meets)) return payload.meets;
  return [];
}

function calculateProfitUnits(stakeUnits, startingPrice, won) {
  if (!stakeUnits) return 0;
  if (!won) return -stakeUnits;
  if (!startingPrice) return 0;
  return Number((stakeUnits * (startingPrice - 1)).toFixed(2));
}

function summarizeGroup(entries, label) {
  const selections = entries.length;
  const winners = entries.filter((entry) => entry.outcome.won).length;
  const placers = entries.filter((entry) => entry.outcome.placed).length;
  const avgPredictedWinPct = selections
    ? Number((entries.reduce((sum, entry) => sum + entry.selection.predictedWinPct, 0) / selections).toFixed(1))
    : 0;
  const profitUnits = Number(entries.reduce((sum, entry) => sum + entry.outcome.profitUnits, 0).toFixed(2));
  const unitsStaked = Number(entries.reduce((sum, entry) => sum + entry.selection.stakeUnits, 0).toFixed(2));

  return {
    label,
    selections,
    winners,
    placers,
    strikeRate: selections ? Number(((winners / selections) * 100).toFixed(1)) : 0,
    avgPredictedWinPct,
    profitUnits,
    unitsStaked,
  };
}

async function buildSnapshotForDate(date) {
  const tipsPath = path.join(racecardsDir, `tips_${date}.json`);
  if (!fs.existsSync(tipsPath)) {
    throw new Error(`Missing tips file for ${date}`);
  }

  const tips = JSON.parse(fs.readFileSync(tipsPath, "utf8"));
  const meetsPayload = await fetchJson(`/v1/australia/meets?date=${date}`);
  const meets = getMetDataShape(meetsPayload);
  const meetByTrack = new Map(
    meets.map((meet) => [normalizeName(meet.course), meet]),
  );

  const raceCache = new Map();
  const results = [];

  for (const race of tips.races || []) {
    const topPick = race.top_picks?.[0];
    if (!topPick) {
      continue;
    }

    const meet = meetByTrack.get(normalizeName(race.track));
    if (!meet?.meet_id) {
      continue;
    }

    const cacheKey = `${meet.meet_id}-${race.race_number}`;
    let raceDetail = raceCache.get(cacheKey);
    if (!raceDetail) {
      raceDetail = await fetchJson(`/v1/australia/meets/${meet.meet_id}/races/${race.race_number}`);
      raceCache.set(cacheKey, raceDetail);
      await wait(250);
    }

    const runners = Array.isArray(raceDetail.runners) ? raceDetail.runners : [];
    const selectedRunner = runners.find((runner) => normalizeName(runner.horse) === normalizeName(topPick.horse)) || null;
    const winnerRunner = runners.find((runner) => toPosition(runner.position) === 1) || null;

    const selectedPosition = toPosition(selectedRunner?.position);
    const selectedWon = selectedPosition === 1;
    const selectedPlaced = selectedPosition != null && selectedPosition <= 3;
    const startingPrice = toNumber(selectedRunner?.sp);
    const winnerPrice = toNumber(winnerRunner?.sp);
    const stakeUnits = parseStakeUnits(topPick.staking);

    results.push({
      track: race.track,
      raceNumber: Number(race.race_number),
      raceName: race.race_name,
      distance: race.distance,
      offTime: raceDetail.off_time || null,
      selection: {
        horse: topPick.horse,
        marketOdds: Number(topPick.odds || 0),
        predictedWinPct: Number(topPick.win_pct || 0),
        edgePct: Number(topPick.edge_pct || 0),
        confidence: String(topPick.confidence || "low"),
        stakeUnits,
        staking: topPick.staking || `${stakeUnits}u`,
        selectionScore: Number(topPick.selection_score || 0),
        valueRating: String(topPick.value_rating || "Poor"),
      },
      outcome: {
        matched: Boolean(selectedRunner),
        position: selectedPosition,
        won: selectedWon,
        placed: selectedPlaced,
        startingPrice,
        winnerHorse: winnerRunner?.horse || null,
        winnerPrice,
        profitUnits: calculateProfitUnits(stakeUnits, startingPrice, selectedWon),
      },
      raceNotes: {
        selectedRunnerComment: cleanComment(selectedRunner?.comment),
        winnerComment: cleanComment(winnerRunner?.comment),
        stewardsSummary: buildStewardsSummary(selectedRunner, winnerRunner, selectedWon),
      },
    });
  }

  results.sort((left, right) => {
    if (left.track !== right.track) return left.track.localeCompare(right.track);
    return left.raceNumber - right.raceNumber;
  });

  const totalSelections = results.length;
  const winners = results.filter((entry) => entry.outcome.won).length;
  const placers = results.filter((entry) => entry.outcome.placed).length;
  const avgPredictedWinPct = totalSelections
    ? Number((results.reduce((sum, entry) => sum + entry.selection.predictedWinPct, 0) / totalSelections).toFixed(1))
    : 0;
  const totalUnitsStaked = Number(results.reduce((sum, entry) => sum + entry.selection.stakeUnits, 0).toFixed(2));
  const totalProfitUnits = Number(results.reduce((sum, entry) => sum + entry.outcome.profitUnits, 0).toFixed(2));
  const strikeRate = totalSelections ? Number(((winners / totalSelections) * 100).toFixed(1)) : 0;

  const tracks = Array.from(new Set(results.map((entry) => entry.track))).map((track) =>
    summarizeGroup(results.filter((entry) => entry.track === track), track),
  );

  const confidenceOrder = ["high", "medium", "low"];
  const confidence = confidenceOrder
    .filter((bucket) => results.some((entry) => entry.selection.confidence === bucket))
    .map((bucket) =>
      summarizeGroup(
        results.filter((entry) => entry.selection.confidence === bucket),
        bucket.toUpperCase(),
      ),
    );

  const snapshot = {
    date,
    label: formatLabel(date),
    generatedAt: new Date().toISOString(),
    source: {
      tipsFile: `racecards/tips_${date}.json`,
      resultsSource: "The Racing API",
    },
    summary: {
      totalSelections,
      resultedSelections: results.filter((entry) => entry.outcome.matched).length,
      winners,
      placers,
      strikeRate,
      avgPredictedWinPct,
      calibrationDelta: Number((strikeRate - avgPredictedWinPct).toFixed(1)),
      totalUnitsStaked,
      totalProfitUnits,
      roiPct: totalUnitsStaked ? Number(((totalProfitUnits / totalUnitsStaked) * 100).toFixed(1)) : 0,
    },
    tracks,
    confidence,
    results,
  };

  fs.mkdirSync(outputDir, { recursive: true });
  const outputPath = path.join(outputDir, `forward_test_${date}.json`);
  fs.writeFileSync(outputPath, JSON.stringify(snapshot, null, 2));
  console.log(`wrote ${path.relative(projectRoot, outputPath)}`);
}

const requestedDates = process.argv.slice(2);
const dates = requestedDates.length > 0
  ? requestedDates
  : fs.readdirSync(racecardsDir)
      .filter((file) => /^tips_\d{4}-\d{2}-\d{2}\.json$/.test(file))
      .map((file) => file.match(/(\d{4}-\d{2}-\d{2})/)?.[1])
      .filter(Boolean)
      .sort();

for (const date of dates) {
  console.log(`building snapshot for ${date}`);
  await buildSnapshotForDate(date);
}
