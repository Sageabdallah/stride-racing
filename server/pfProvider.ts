/**
 * Live race data from Punting Form — the same provider the pipeline uses
 * (see PUNTINGFORM_MIGRATION.md). Replaces the dead The Racing API client
 * for live cards, racecard discovery, results and speedmaps.
 *
 * All fetching goes through server/python/pf_card_api.py so the field
 * mapping (racecard contract, scratchings fold, horse-id bridge) is
 * single-sourced with the pipeline. Responses are cached for 15 minutes,
 * matching the old racingApiClient behaviour.
 */

import { spawn } from "child_process";
import path from "path";
import { logPythonTaskFailure, parsePythonJson, resolvePythonBin, resolvePythonScriptDir } from "./pythonBin";

export const hasPuntingFormKey = Boolean(process.env.PUNTINGFORM_API_KEY?.trim());

const cache = new Map<string, { data: unknown; ts: number }>();
const TTL = 15 * 60 * 1000;

function runPfBridge(request: Record<string, unknown>, timeoutMs = 180_000): Promise<any | null> {
  return new Promise((resolve) => {
    const script = path.join(resolvePythonScriptDir(), "pf_card_api.py");
    const python = spawn(resolvePythonBin(), [script]);

    let stdout = "";
    let stderr = "";
    const timer = setTimeout(() => python.kill("SIGKILL"), timeoutMs);

    python.stdout.on("data", (data) => { stdout += data.toString(); });
    python.stderr.on("data", (data) => { stderr += data.toString(); });
    python.on("error", (err) => {
      clearTimeout(timer);
      logPythonTaskFailure("pf_card_api.py", null, String(err?.message ?? err));
      resolve(null);
    });
    python.on("close", (code) => {
      clearTimeout(timer);
      if (code !== 0) {
        logPythonTaskFailure("pf_card_api.py", code, stderr);
        return resolve(null);
      }
      try {
        const parsed = parsePythonJson(stdout) as Record<string, unknown>;
        if (parsed && typeof parsed === "object" && "error" in parsed && parsed.error) {
          console.warn(`[PuntingForm] ${String(parsed.error)}`);
          return resolve(null);
        }
        resolve(parsed);
      } catch {
        logPythonTaskFailure("pf_card_api.py", code, `unparseable stdout (${stdout.length} bytes)`);
        resolve(null);
      }
    });

    python.stdin.write(JSON.stringify(request));
    python.stdin.end();
  });
}

async function cached<T>(key: string, fetcher: () => Promise<T | null>): Promise<T | null> {
  const hit = cache.get(key);
  if (hit && Date.now() - hit.ts < TTL) return hit.data as T;
  const data = await fetcher();
  if (data !== null) cache.set(key, { data, ts: Date.now() });
  return data;
}

/** Racecard-contract meets ({course, meet_id, date, races[].runners[]}) for a date, optionally filtered to one track. */
export async function fetchPfRacecardMeets(date: string, track?: string): Promise<any[] | null> {
  if (!hasPuntingFormKey) return null;
  const key = `racecard|${date}|${(track ?? "").toLowerCase()}`;
  const out = await cached(key, () => runPfBridge({ action: "racecard", date, track }));
  return out && Array.isArray((out as any).meets) ? (out as any).meets : null;
}

/** Meeting discovery list ({meet_id, course}) for a date. */
export async function fetchPfMeets(date: string): Promise<any[] | null> {
  if (!hasPuntingFormKey) return null;
  const out = await cached(`meets|${date}`, () => runPfBridge({ action: "meets", date }));
  return out && Array.isArray((out as any).meets) ? (out as any).meets : null;
}

/** Results blocks ({course, race_number, runners[{horse, position, sp}]}) for a date. */
export async function fetchPfResults(date: string): Promise<any[] | null> {
  if (!hasPuntingFormKey) return null;
  const out = await cached(`results|${date}`, () => runPfBridge({ action: "results", date }));
  return out && Array.isArray((out as any).results) ? (out as any).results : null;
}

/** Punting Form speedmap rows (pfScore, neuralPrice, speed, settle, mapA2E, jockeyA2E). raceNo 0 = whole meeting. */
export async function fetchPfSpeedmaps(meetingId: string, raceNo = 0): Promise<any[] | null> {
  if (!hasPuntingFormKey) return null;
  const out = await cached(`speedmaps|${meetingId}|${raceNo}`, () =>
    runPfBridge({ action: "speedmaps", meeting_id: meetingId, race_no: raceNo }));
  return out && Array.isArray((out as any).speedmaps) ? (out as any).speedmaps : null;
}

function bestOdds(runner: Record<string, any>): number | null {
  if (typeof runner.sp === "number" && runner.sp > 0) return runner.sp;
  const odds = runner.odds;
  if (Array.isArray(odds)) {
    const entry = odds.find((o: any) => typeof o?.win_odds === "number" && o.win_odds > 0);
    if (entry) return entry.win_odds;
  }
  if (typeof odds === "number" && odds > 0) return odds;
  return null;
}

function formatRunnerLine(runner: Record<string, any>, index: number): string {
  const tab = runner.number ?? index + 1;
  const barrier = runner.draw ? ` B${runner.draw}` : "";
  const jockey = runner.jockey ? ` | ${runner.jockey}` : "";
  const trainer = runner.trainer ? ` | T: ${runner.trainer}` : "";
  const weight = runner.weight ? ` | ${runner.weight}` : "";
  const form = runner.form ? ` | Form: ${runner.form}` : "";
  const odds = bestOdds(runner);
  const oddsStr = odds ? ` | $${odds}` : "";
  const comment = runner.comment ? `\n      "${String(runner.comment).slice(0, 140)}"` : "";
  return `  ${tab}. ${runner.horse ?? "Unknown"}${barrier}${jockey}${trainer}${weight}${oddsStr}${form}${comment}`;
}

/**
 * Pure formatter: one racecard-contract meet -> the live-card text layout
 * the chat synthesis prompt expects (same layout the old client produced).
 */
export function formatLiveRaceCard(meet: Record<string, any>, date: string): string {
  const lines: string[] = [
    `LIVE RACE CARD — ${meet.course ?? "Unknown"}, ${date}`,
    `Data: Punting Form (declared field, barriers, scratchings folded in)`,
    "",
  ];

  for (const race of meet.races ?? []) {
    const off = race.off_time
      ? new Date(race.off_time).toLocaleTimeString("en-AU", { hour: "2-digit", minute: "2-digit", hour12: true })
      : "";
    lines.push(
      `Race ${race.race_number ?? "?"}${off ? ` (${off})` : ""}: ${race.race_name ?? ""} | ${race.distance ?? ""} | ${race.class ?? ""}${race.going ? ` | Going: ${race.going}` : ""}`,
    );
    const runners = (race.runners ?? []).filter((r: any) => !r.scratched);
    if (runners.length === 0) {
      lines.push("  (no runners declared yet)");
    } else {
      runners.forEach((r: any, i: number) => lines.push(formatRunnerLine(r, i)));
    }
    lines.push("");
  }

  return lines.join("\n");
}

/**
 * Formatted live race card for one track+date, for injection into the chat
 * synthesis prompt, fed by Punting Form's racecard contract.
 */
export async function fetchLiveRaceCard(track: string, date: string): Promise<string | null> {
  const meets = await fetchPfRacecardMeets(date, track);
  const meet = meets?.[0];
  if (!meet) return null;
  return formatLiveRaceCard(meet, date);
}

/** Early-odds view for one race — same slicing the old client offered. */
export async function fetchEarlyOdds(track: string, date: string, raceNumber?: number): Promise<string | null> {
  const card = await fetchLiveRaceCard(track, date);
  if (!card || !raceNumber) return card;
  const lines = card.split("\n");
  const start = lines.findIndex((l) => l.startsWith(`Race ${raceNumber}`));
  if (start === -1) return card;
  const end = lines.findIndex((l, i) => i > start && l.startsWith("Race "));
  return lines.slice(start, end === -1 ? undefined : end).join("\n");
}
