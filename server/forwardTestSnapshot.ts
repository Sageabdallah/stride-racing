import fs from "fs";
import path from "path";

export interface ForwardTestGroup {
  label: string;
  selections: number;
  winners: number;
  placers: number;
  strikeRate: number;
  avgPredictedWinPct: number;
  profitUnits: number;
  unitsStaked: number;
}

export interface ForwardTestRaceResult {
  track: string;
  raceNumber: number;
  raceName: string;
  distance: string;
  offTime: string | null;
  selection: {
    horse: string;
    marketOdds: number;
    predictedWinPct: number;
    edgePct: number;
    confidence: string;
    stakeUnits: number;
    staking: string;
    selectionScore: number;
    valueRating: string;
  };
  outcome: {
    matched: boolean;
    position: number | null;
    won: boolean;
    placed: boolean;
    startingPrice: number | null;
    winnerHorse: string | null;
    winnerPrice: number | null;
    profitUnits: number;
  };
  raceNotes: {
    selectedRunnerComment: string | null;
    winnerComment: string | null;
    stewardsSummary: string;
  };
}

export interface ForwardTestSnapshot {
  date: string;
  label: string;
  generatedAt: string;
  source: {
    tipsFile: string;
    resultsSource: string;
  };
  summary: {
    totalSelections: number;
    resultedSelections: number;
    winners: number;
    placers: number;
    strikeRate: number;
    avgPredictedWinPct: number;
    calibrationDelta: number;
    totalUnitsStaked: number;
    totalProfitUnits: number;
    roiPct: number;
  };
  tracks: ForwardTestGroup[];
  confidence: ForwardTestGroup[];
  results: ForwardTestRaceResult[];
}

const SNAPSHOT_DIR = path.join(process.cwd(), "data", "forward-testing");
const SNAPSHOT_FILE_PATTERN = /^forward_test_(\d{4}-\d{2}-\d{2})\.json$/;
const DEFAULT_FORWARD_TEST_DATE = "2026-03-21";

function readSnapshotFile(snapshotPath: string): ForwardTestSnapshot {
  return JSON.parse(fs.readFileSync(snapshotPath, "utf8")) as ForwardTestSnapshot;
}

export function listForwardTestSnapshotDates(): string[] {
  if (!fs.existsSync(SNAPSHOT_DIR)) {
    return [];
  }

  return fs.readdirSync(SNAPSHOT_DIR)
    .map((entry) => entry.match(SNAPSHOT_FILE_PATTERN)?.[1] || null)
    .filter((entry): entry is string => Boolean(entry))
    .sort();
}

export function loadForwardTestSnapshot(requestedDate?: string) {
  const availableDates = listForwardTestSnapshotDates();
  if (availableDates.length === 0) {
    return null;
  }

  const selectedDate = requestedDate && availableDates.includes(requestedDate)
    ? requestedDate
    : (availableDates.includes(DEFAULT_FORWARD_TEST_DATE)
      ? DEFAULT_FORWARD_TEST_DATE
      : availableDates[availableDates.length - 1]);

  const snapshotPath = path.join(SNAPSHOT_DIR, `forward_test_${selectedDate}.json`);
  if (!fs.existsSync(snapshotPath)) {
    return null;
  }

  const snapshot = readSnapshotFile(snapshotPath);

  return {
    snapshot,
    selectedDate,
    availableDates,
    sourceFile: path.relative(process.cwd(), snapshotPath),
  };
}

export function loadAllForwardTestSnapshots() {
  return listForwardTestSnapshotDates().map((date) => {
    const snapshotPath = path.join(SNAPSHOT_DIR, `forward_test_${date}.json`);
    return {
      date,
      snapshot: readSnapshotFile(snapshotPath),
      sourceFile: path.relative(process.cwd(), snapshotPath),
    };
  });
}
