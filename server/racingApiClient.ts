/**
 * Live race data from theracingapi.com
 * Used to inject actual declared fields, barriers, jockeys, and odds into chat
 * when local pipeline data doesn't exist for a future race.
 */

const BASE_URL = process.env.RACING_API_BASE ?? "https://api.theracingapi.com";
const USERNAME = process.env.RACING_API_USERNAME ?? "";
const PASSWORD = process.env.RACING_API_PASSWORD ?? "";

export const hasRacingApiCreds = Boolean(USERNAME && PASSWORD);

function authHeader(): string {
  return "Basic " + Buffer.from(`${USERNAME}:${PASSWORD}`).toString("base64");
}

// 15-min in-memory cache — avoids hammering the API on repeated questions
const cache = new Map<string, { data: string; ts: number }>();
const TTL = 15 * 60 * 1000;

async function apiGet(path: string, params?: Record<string, string>): Promise<unknown> {
  const url = new URL(`${BASE_URL}${path}`);
  if (params) Object.entries(params).forEach(([k, v]) => url.searchParams.set(k, v));
  try {
    const res = await fetch(url.toString(), {
      headers: { Authorization: authHeader(), Accept: "application/json" },
      signal: AbortSignal.timeout(12000),
    });
    if (!res.ok) return null;
    return res.json();
  } catch {
    return null;
  }
}

const TARGET_TRACKS = [
  "flemington", "caulfield", "moonee valley", "sandown", "randwick", "royal randwick",
  "rosehill", "warwick farm", "canterbury", "eagle farm", "doomben", "ascot", "belmont",
  "pinjarra", "kensington", "gold coast", "aquis park", "geelong", "ballarat",
  "cranbourne", "ipswich", "newcastle", "morphettville",
];

function trackMatch(course: string, target: string): boolean {
  const c = course.toLowerCase();
  const t = target.toLowerCase();
  return c.includes(t) || t.includes(c) || TARGET_TRACKS.some(tr => c.includes(tr) && t.includes(tr));
}

interface RacingApiRunner {
  horse?: string; number?: number; draw?: number;
  jockey?: string; trainer?: string; weight?: string;
  form?: string; scratched?: boolean; comment?: string;
  odds?: Array<{ bookmaker?: string; win_odds?: number }>;
  stats?: {
    course_stats?: { total?: number; first?: number };
    distance_stats?: { total?: number; first?: number };
    career_win_percent?: number;
    last_raced?: string;
  };
}

interface RacingApiRace {
  race_number?: string | number;
  race_name?: string;
  distance?: string;
  going?: string;
  class?: string;
  off_time?: string;
  runners?: RacingApiRunner[];
}

function formatRunner(r: RacingApiRunner, index: number): string {
  const tab = r.number ?? (index + 1);
  const barrier = r.draw ? ` B${r.draw}` : "";
  const jock = r.jockey ? ` | ${r.jockey}` : "";
  const trainer = r.trainer ? ` | T: ${r.trainer}` : "";
  const wt = r.weight ? ` | ${r.weight}` : "";
  const form = r.form ? ` | Form: ${r.form}` : "";
  const bestOdds = r.odds?.find(o => o.win_odds)?.win_odds;
  const oddsStr = bestOdds ? ` | $${bestOdds}` : "";
  const cw = r.stats?.course_stats;
  const dw = r.stats?.distance_stats;
  const records: string[] = [];
  if (cw?.total) records.push(`${cw.first ?? 0}/${cw.total} course`);
  if (dw?.total) records.push(`${dw.first ?? 0}/${dw.total} dist`);
  const recordStr = records.length ? ` | ${records.join(", ")}` : "";
  const comment = r.comment ? `\n      "${r.comment.slice(0, 140)}"` : "";
  return `  ${tab}. ${r.horse ?? "Unknown"}${barrier}${jock}${trainer}${wt}${oddsStr}${form}${recordStr}${comment}`;
}

/**
 * Fetch a live race card for a track + date.
 * Returns a formatted string for injection into Claude's synthesis prompt.
 */
export async function fetchLiveRaceCard(track: string, date: string): Promise<string | null> {
  if (!hasRacingApiCreds) return null;

  const key = `${track.toLowerCase()}|${date}`;
  const cached = cache.get(key);
  if (cached && Date.now() - cached.ts < TTL) return cached.data;

  const meetsRaw = await apiGet("/v1/australia/meets", { date }) as Record<string, unknown> | null;
  if (!meetsRaw) return null;

  const meets: RacingApiRace[] = Array.isArray(meetsRaw)
    ? (meetsRaw as RacingApiRace[])
    : Array.isArray((meetsRaw as Record<string, unknown>).meets)
      ? ((meetsRaw as Record<string, unknown>).meets as RacingApiRace[])
      : [];

  // Find the meet that matches the requested track
  const targetMeet = (meets as Array<Record<string, unknown>>).find(m => {
    const course = String(m.course ?? m.track ?? "");
    return trackMatch(course, track);
  }) as Record<string, unknown> | undefined;

  if (!targetMeet) return null;

  const meetId = String(targetMeet.meet_id ?? targetMeet.id ?? "");
  if (!meetId) return null;

  const racesRaw = await apiGet(`/v1/australia/meets/${meetId}/races`) as Record<string, unknown> | null;
  if (!racesRaw) return null;

  const races: RacingApiRace[] = Array.isArray(racesRaw)
    ? (racesRaw as RacingApiRace[])
    : Array.isArray((racesRaw as Record<string, unknown>).races)
      ? ((racesRaw as Record<string, unknown>).races as RacingApiRace[])
      : [];

  // Fetch each race's full details in parallel (barriers, full runner list)
  const detailedRaces = await Promise.all(
    races.slice(0, 12).map(async (r) => {
      const num = r.race_number;
      if (!num) return r;
      const detail = await apiGet(`/v1/australia/meets/${meetId}/races/${num}`) as RacingApiRace | null;
      return detail ?? r;
    })
  );

  const courseName = String(targetMeet.course ?? targetMeet.track ?? track);
  const lines: string[] = [
    `LIVE RACE CARD — ${courseName}, ${date}`,
    `Data: theracingapi.com (declared field, barriers, early odds)`,
    "",
  ];

  for (const race of detailedRaces) {
    if (!race) continue;
    const rn = race.race_number ?? "?";
    const name = race.race_name ?? "";
    const dist = race.distance ?? "";
    const going = race.going ?? "";
    const cls = race.class ?? "";
    const off = race.off_time ? new Date(race.off_time).toLocaleTimeString("en-AU", { hour: "2-digit", minute: "2-digit", hour12: true }) : "";
    lines.push(`Race ${rn}${off ? ` (${off})` : ""}: ${name} | ${dist} | ${cls}${going ? ` | Going: ${going}` : ""}`);

    const runners = (race.runners ?? []).filter(r => !r.scratched);
    if (runners.length === 0) {
      lines.push("  (no runners declared yet)");
    } else {
      runners.forEach((r, i) => lines.push(formatRunner(r, i)));
    }
    lines.push("");
  }

  const result = lines.join("\n");
  cache.set(key, { data: result, ts: Date.now() });
  return result;
}

/**
 * Get early odds snapshot for a specific race — useful for market awareness questions.
 */
export async function fetchEarlyOdds(track: string, date: string, raceNumber?: number): Promise<string | null> {
  const card = await fetchLiveRaceCard(track, date);
  if (!card || !raceNumber) return card;
  // Filter to just the requested race
  const lines = card.split("\n");
  const start = lines.findIndex(l => l.startsWith(`Race ${raceNumber}`));
  if (start === -1) return card;
  const end = lines.findIndex((l, i) => i > start && l.startsWith("Race "));
  return lines.slice(start, end === -1 ? undefined : end).join("\n");
}
