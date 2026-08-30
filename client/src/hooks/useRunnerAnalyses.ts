import { useCallback, useEffect, useMemo, useState } from "react";

export interface RunnerAnalysisSections {
  profile: string;
  pace: string;
  condition: string;
  verdict: string;
  qualityFlag?: string;
  cached?: boolean;
}

export interface RunnerAnalysisState {
  status: "idle" | "loading" | "ready" | "error";
  data?: RunnerAnalysisSections;
  message?: string;
}

interface RunnerAnalysisInput {
  horseName: string;
  currentOdds?: number | null;
}

interface UseRunnerAnalysesParams {
  track?: string;
  raceNumber?: number;
  raceDate?: string;
  runners: RunnerAnalysisInput[];
  enabled?: boolean;
}

const STORAGE_PREFIX = "runner-analysis:";
const analysisCache = new Map<string, RunnerAnalysisSections>();

function normalizeName(value: string): string {
  return value.toLowerCase().replace(/[^a-z0-9]/g, "");
}

function getCacheKey(track: string, raceNumber: number, raceDate: string, horseName: string): string {
  return [raceDate, raceNumber, track.toLowerCase(), normalizeName(horseName)].join("|");
}

function loadCachedAnalysis(key: string): RunnerAnalysisSections | null {
  if (analysisCache.has(key)) {
    return analysisCache.get(key) || null;
  }

  if (typeof window === "undefined") {
    return null;
  }

  const raw = window.sessionStorage.getItem(`${STORAGE_PREFIX}${key}`);
  if (!raw) {
    return null;
  }

  try {
    const parsed = JSON.parse(raw) as RunnerAnalysisSections;
    analysisCache.set(key, parsed);
    return parsed;
  } catch {
    return null;
  }
}

function saveCachedAnalysis(key: string, value: RunnerAnalysisSections): void {
  analysisCache.set(key, value);
  if (typeof window !== "undefined") {
    window.sessionStorage.setItem(`${STORAGE_PREFIX}${key}`, JSON.stringify(value));
  }
}

export function useRunnerAnalyses({
  track,
  raceNumber,
  raceDate,
  runners,
  enabled = true,
}: UseRunnerAnalysesParams) {
  const [states, setStates] = useState<Record<string, RunnerAnalysisState>>({});

  const runnerSignature = useMemo(
    () => runners.map((runner) => normalizeName(runner.horseName)).sort().join("|"),
    [runners],
  );

  const requestAnalysis = useCallback(
    async (runner: RunnerAnalysisInput, force = false): Promise<RunnerAnalysisSections> => {
      if (!track || !raceNumber || !raceDate) {
        throw new Error("Race context is incomplete");
      }

      const response = await fetch("/api/runner-analysis", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          track,
          raceNumber,
          raceDate,
          horseName: runner.horseName,
          currentOdds: runner.currentOdds ?? null,
          force,
        }),
      });

      const payload = await response.json();
      if (!response.ok || !payload.success) {
        throw new Error(payload.error || "Failed to generate runner analysis");
      }

      return payload.analysis as RunnerAnalysisSections;
    },
    [track, raceDate, raceNumber],
  );

  const setRunnerState = useCallback((horseName: string, next: RunnerAnalysisState) => {
    const key = normalizeName(horseName);
    setStates((current) => ({ ...current, [key]: next }));
  }, []);

  const generateAll = useCallback(
    async (force = false) => {
      if (!enabled || !track || !raceNumber || !raceDate || runners.length === 0) {
        setStates({});
        return;
      }

      const initial: Record<string, RunnerAnalysisState> = {};
      const missing: RunnerAnalysisInput[] = [];

      for (const runner of runners) {
        const key = getCacheKey(track, raceNumber, raceDate, runner.horseName);
        const cached = !force ? loadCachedAnalysis(key) : null;
        const stateKey = normalizeName(runner.horseName);

        if (cached) {
          initial[stateKey] = { status: "ready", data: cached };
        } else {
          initial[stateKey] = { status: "loading" };
          missing.push(runner);
        }
      }

      setStates(initial);

      if (missing.length === 0) {
        return;
      }

      const batchSize = 2;
      const delayMs = runners.length >= 20 ? 900 : 650;

      for (let index = 0; index < missing.length; index += batchSize) {
        const batch = missing.slice(index, index + batchSize);

        await Promise.all(
          batch.map(async (runner) => {
            try {
              const analysis = await requestAnalysis(runner, force);
              const cacheKey = getCacheKey(track, raceNumber, raceDate, runner.horseName);
              saveCachedAnalysis(cacheKey, analysis);
              setRunnerState(runner.horseName, { status: "ready", data: analysis });
            } catch (error) {
              const msg = error instanceof Error ? error.message : "Failed to generate analysis";
              const friendly = msg.includes("busy") || msg.includes("429") || msg.includes("rate")
                ? "Analysis temporarily busy — tap retry in a few seconds."
                : msg;
              setRunnerState(runner.horseName, {
                status: "error",
                message: friendly,
              });
            }
          }),
        );

        if (index + batchSize < missing.length) {
          await new Promise((resolve) => setTimeout(resolve, delayMs));
        }
      }
    },
    [enabled, raceDate, raceNumber, requestAnalysis, runners, setRunnerState, track],
  );

  useEffect(() => {
    if (!enabled) {
      setStates({});
      return;
    }
    void generateAll(false);
  }, [enabled, generateAll, runnerSignature]);

  const retryRunner = useCallback(
    async (runner: RunnerAnalysisInput) => {
      if (!track || !raceNumber || !raceDate) {
        return;
      }

      setRunnerState(runner.horseName, { status: "loading" });

      try {
        const analysis = await requestAnalysis(runner, true);
        const cacheKey = getCacheKey(track, raceNumber, raceDate, runner.horseName);
        saveCachedAnalysis(cacheKey, analysis);
        setRunnerState(runner.horseName, { status: "ready", data: analysis });
      } catch (error) {
        const msg = error instanceof Error ? error.message : "Failed to generate analysis";
        const friendly = msg.includes("busy") || msg.includes("429") || msg.includes("rate")
          ? "Analysis temporarily busy — tap retry in a few seconds."
          : msg;
        setRunnerState(runner.horseName, {
          status: "error",
          message: friendly,
        });
      }
    },
    [raceDate, raceNumber, requestAnalysis, setRunnerState, track],
  );

  return {
    states,
    retryRunner,
    regenerateRace: () => generateAll(true),
  };
}
