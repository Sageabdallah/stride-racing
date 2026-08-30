import { useCallback, useEffect, useState } from "react";

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

export interface RaceShapeRunner {
  horse: string;
  barrier: number | null;
  jockey: string | null;
  weight: number | null;
  band: number;
  bandSource: "calculated" | "estimated";
  sampleCount: number;
  avg800mPct: number | null;
  zone: "leader" | "on_pace" | "on_pace_wide" | "midfield" | "back_half" | "backmarker";
  paceRole: "Leader" | "On-Pace" | "On-Pace Wide" | "Midfield" | "Back Half" | "Backmarker";
  reasonSummary: string;
  positionConflict: boolean;
  positionConflictReason: string | null;
  tempoAdvantage: boolean;
  tempoAdvantageReason: string;
}

export interface RaceShapeZone {
  key: "leader" | "on_pace" | "on_pace_wide" | "midfield" | "back_half" | "backmarker";
  label: string;
  bandRange: string;
  colorKey: "red" | "orange" | "amber" | "green" | "blue" | "purple";
}

export interface RaceShapeBlock {
  track: string;
  distance: number | null;
  condition: string | null;
  rail: string | null;
  trackBias: string | null;
  tempoLabel: "HOT" | "GENUINE" | "SOFT" | "UNKNOWN";
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

export interface RaceOverviewData {
  overview: RaceOverviewSections;
  raceShape: RaceShapeBlock | null;
}

export interface RaceOverviewState {
  status: "idle" | "loading" | "ready" | "error";
  data?: RaceOverviewData;
  message?: string;
}

interface UseRaceOverviewParams {
  track?: string;
  raceNumber?: number;
  raceDate?: string;
  enabled?: boolean;
}

const STORAGE_PREFIX = "race-overview:";
const CACHE_TTL_MS = 5 * 60 * 1000;
const overviewCache = new Map<string, RaceOverviewData>();

function getCacheKey(track: string, raceNumber: number, raceDate: string): string {
  return [raceDate, raceNumber, track.toLowerCase(), "overview"].join("|");
}

function loadCachedOverview(key: string): RaceOverviewData | null {
  if (overviewCache.has(key)) {
    return overviewCache.get(key) || null;
  }

  if (typeof window === "undefined") {
    return null;
  }

  const raw = window.sessionStorage.getItem(`${STORAGE_PREFIX}${key}`);
  if (!raw) {
    return null;
  }

  try {
    const parsed = JSON.parse(raw) as { data?: RaceOverviewData; savedAt?: number } | RaceOverviewData;
    if ("savedAt" in parsed && parsed.savedAt && Date.now() - parsed.savedAt > CACHE_TTL_MS) {
      window.sessionStorage.removeItem(`${STORAGE_PREFIX}${key}`);
      return null;
    }

    const data = "data" in parsed && parsed.data ? parsed.data : (parsed as RaceOverviewData);
    if (!data?.overview) {
      return null;
    }

    overviewCache.set(key, data);
    return data;
  } catch {
    return null;
  }
}

function saveCachedOverview(key: string, value: RaceOverviewData): void {
  overviewCache.set(key, value);
  if (typeof window !== "undefined") {
    window.sessionStorage.setItem(`${STORAGE_PREFIX}${key}`, JSON.stringify({ data: value, savedAt: Date.now() }));
  }
}

export function useRaceOverview({
  track,
  raceNumber,
  raceDate,
  enabled = true,
}: UseRaceOverviewParams) {
  const [state, setState] = useState<RaceOverviewState>({ status: "idle" });

  const requestOverview = useCallback(
    async (force = false): Promise<RaceOverviewData> => {
      if (!track || !raceNumber || !raceDate) {
        throw new Error("Race context is incomplete");
      }

      const response = await fetch("/api/race-overview", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          track,
          raceNumber,
          raceDate,
          force,
        }),
      });

      const payload = await response.json();
      if (!response.ok || !payload.success) {
        throw new Error(payload.error || "Failed to generate race overview");
      }

      return {
        overview: payload.overview as RaceOverviewSections,
        raceShape: (payload.raceShape as RaceShapeBlock | undefined) || null,
      };
    },
    [track, raceDate, raceNumber],
  );

  useEffect(() => {
    if (!enabled || !track || !raceNumber || !raceDate) {
      setState({ status: "idle" });
      return;
    }

    const cacheKey = getCacheKey(track, raceNumber, raceDate);
    const cached = loadCachedOverview(cacheKey);
    if (cached) {
      setState({ status: "ready", data: cached });
      return;
    }

    setState({ status: "loading" });
    void requestOverview(false)
      .then((overview) => {
        saveCachedOverview(cacheKey, overview);
        setState({ status: "ready", data: overview });
      })
      .catch((error) => {
        setState({
          status: "error",
          message: error instanceof Error ? error.message : "Failed to generate race overview",
        });
      });
  }, [enabled, raceDate, raceNumber, requestOverview, track]);

  const retryOverview = useCallback(async () => {
    if (!track || !raceNumber || !raceDate) {
      return;
    }

    const cacheKey = getCacheKey(track, raceNumber, raceDate);
    setState({ status: "loading" });

    try {
      const overview = await requestOverview(true);
      saveCachedOverview(cacheKey, overview);
      setState({ status: "ready", data: overview });
    } catch (error) {
      setState({
        status: "error",
        message: error instanceof Error ? error.message : "Failed to generate race overview",
      });
    }
  }, [raceDate, raceNumber, requestOverview, track]);

  return {
    state,
    retryOverview,
  };
}
