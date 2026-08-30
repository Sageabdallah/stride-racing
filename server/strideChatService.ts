import { randomUUID } from "node:crypto";
import {
  formatStrideAnswerInstructions,
  formatStrideEvidenceForPrompt,
  gatherStrideEvidence,
  type StrideEvidenceItem,
  type StrideEvidenceSource,
  type StrideRetrievalBundle,
  type StrideRetrievalRequest,
} from "./strideChatRetrieval";

type SessionRole = "user" | "assistant";

interface SessionMessage {
  role: SessionRole;
  content: string;
}

export interface StrideChatCitation {
  sourceType: StrideEvidenceSource;
  sourceId: string;
  label: string;
  freshness?: string;
}

export interface StrideChatRetrievalMeta {
  queryType: string;
  answerMode: StrideRetrievalBundle["answerMode"];
  evidenceItems: number;
  sourcesUsed: StrideEvidenceSource[];
  sourceCounts: Record<StrideEvidenceSource, number>;
}

export interface StrideChatTurn {
  turnId: string;
  sessionId: string;
  question: string;
  context?: StrideRetrievalRequest["context"];
  createdAt: number;
  bundle: StrideRetrievalBundle;
  reasoning: string;
  citations: StrideChatCitation[];
  retrieval: StrideChatRetrievalMeta;
}

interface StartStrideTurnInput {
  message: string;
  sessionId?: string;
  context?: StrideRetrievalRequest["context"];
  limit?: number;
}

interface ResolveStrideTurnInput extends StartStrideTurnInput {
  turnId?: string;
}

const TURN_TTL_MS = 30 * 60 * 1000;
const MAX_SESSION_MESSAGES = 12;

const turnCache = new Map<string, StrideChatTurn>();
const sessionHistory = new Map<string, SessionMessage[]>();

function normalizeSessionId(sessionId?: string): string {
  const trimmed = sessionId?.trim();
  return trimmed && trimmed.length > 0 ? trimmed : "anonymous";
}

function pruneStrideChatState(): void {
  const now = Date.now();
  for (const [turnId, turn] of turnCache.entries()) {
    if (now - turn.createdAt > TURN_TTL_MS) {
      turnCache.delete(turnId);
    }
  }
}

function pushSessionMessage(sessionId: string, message: SessionMessage): void {
  const next = [...(sessionHistory.get(sessionId) ?? []), message].slice(-MAX_SESSION_MESSAGES);
  sessionHistory.set(sessionId, next);
}

export function getStrideSessionHistory(sessionId?: string): SessionMessage[] {
  pruneStrideChatState();
  return [...(sessionHistory.get(normalizeSessionId(sessionId)) ?? [])];
}

export function clearStrideChatState(sessionId?: string): void {
  if (sessionId) {
    const key = normalizeSessionId(sessionId);
    sessionHistory.delete(key);
    for (const [turnId, turn] of turnCache.entries()) {
      if (turn.sessionId === key) turnCache.delete(turnId);
    }
    return;
  }

  turnCache.clear();
  sessionHistory.clear();
}

function asRecord(value: unknown): Record<string, unknown> | undefined {
  return value && typeof value === "object" && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : undefined;
}

function asArray(value: unknown): unknown[] {
  return Array.isArray(value) ? value : [];
}

function toNumber(value: unknown): number | undefined {
  if (typeof value === "number" && Number.isFinite(value)) return value;
  if (typeof value === "string") {
    const parsed = Number(value.replace(/[$,%]/g, "").trim());
    if (Number.isFinite(parsed)) return parsed;
  }
  return undefined;
}

function toText(value: unknown): string | undefined {
  if (typeof value === "string" && value.trim()) return value.trim();
  if (typeof value === "number" && Number.isFinite(value)) return String(value);
  return undefined;
}

function formatPct(value: unknown, digits = 1): string | undefined {
  const parsed = toNumber(value);
  return parsed === undefined ? undefined : `${parsed.toFixed(digits)}%`;
}

function formatOdds(value: unknown): string | undefined {
  const parsed = toNumber(value);
  return parsed === undefined ? undefined : `$${parsed.toFixed(2)}`;
}

function formatEvidenceLine(item: StrideEvidenceItem): string {
  const scope = [
    item.raceDate ?? null,
    item.track ?? null,
    item.raceNumber ? `R${item.raceNumber}` : null,
    item.horseName ?? null,
  ]
    .filter(Boolean)
    .join(" • ");
  return `[${item.source}] ${item.title}${scope ? ` — ${scope}` : ""}: ${item.summary}`;
}

function buildScopeLabel(bundle: StrideRetrievalBundle): string {
  return [
    bundle.entities.date ?? null,
    bundle.entities.track ?? null,
    bundle.entities.raceNumber ? `R${bundle.entities.raceNumber}` : null,
    bundle.entities.raceName ?? null,
    bundle.entities.horseName ?? null,
  ]
    .filter(Boolean)
    .join(" • ");
}

function buildCitations(bundle: StrideRetrievalBundle): StrideChatCitation[] {
  return bundle.evidence.slice(0, 8).map((item, index) => ({
    sourceType: item.source,
    sourceId: [item.source, item.raceDate ?? "", item.track ?? "", item.raceNumber ?? "", item.horseName ?? "", index].join("|"),
    label: item.title,
    freshness: item.raceDate,
  }));
}

function buildRetrievalMeta(bundle: StrideRetrievalBundle): StrideChatRetrievalMeta {
  const sourcesUsed = Object.entries(bundle.sourceCounts)
    .filter(([, count]) => count > 0)
    .map(([source]) => source as StrideEvidenceSource);

  return {
    queryType: bundle.classification.intent,
    answerMode: bundle.answerMode,
    evidenceItems: bundle.evidence.length,
    sourcesUsed,
    sourceCounts: bundle.sourceCounts,
  };
}

function scopedItemScore(bundle: StrideRetrievalBundle, item: StrideEvidenceItem): number {
  let score = item.score;
  if (bundle.entities.date && item.raceDate === bundle.entities.date) score += 1000;
  if (bundle.entities.track && item.track && item.track.toLowerCase().includes(bundle.entities.track.toLowerCase())) score += 250;
  if (bundle.entities.raceNumber && item.raceNumber === bundle.entities.raceNumber) score += 180;
  if (bundle.entities.horseName && item.horseName && item.horseName.toLowerCase().includes(bundle.entities.horseName.toLowerCase())) score += 220;
  return score;
}

export function pickEvidence(bundle: StrideRetrievalBundle, source: StrideEvidenceSource): StrideEvidenceItem[] {
  return bundle.evidence
    .filter((item) => item.source === source)
    .sort((a, b) => scopedItemScore(bundle, b) - scopedItemScore(bundle, a));
}

export function extractRaceMeta(bundle: StrideRetrievalBundle) {
  const racecardItem = pickEvidence(bundle, "local_racecard")[0];
  if (racecardItem) {
    const payload = asRecord(racecardItem.payload);
    const race = asRecord(payload?.race);
    return {
      track: racecardItem.track,
      raceNumber: racecardItem.raceNumber,
      raceDate: racecardItem.raceDate,
      raceName: toText(race?.race_name) ?? racecardItem.title,
      distance: toText(race?.distance),
      going: toText(race?.going),
      raceClass: toText(race?.class),
    };
  }

  const tipsItem = pickEvidence(bundle, "local_tips")[0];
  if (tipsItem) {
    const payload = asRecord(tipsItem.payload);
    const race = asRecord(payload?.race);
    return {
      track: tipsItem.track,
      raceNumber: tipsItem.raceNumber,
      raceDate: tipsItem.raceDate,
      raceName: toText(race?.race_name),
      distance: toText(race?.distance),
      going: toText(race?.going),
      raceClass: toText(race?.race_class),
    };
  }

  const dbRace = pickEvidence(bundle, "races")[0];
  if (dbRace) {
    const payload = asRecord(dbRace.payload);
    return {
      track: dbRace.track,
      raceNumber: dbRace.raceNumber,
      raceDate: dbRace.raceDate,
      raceName: toText(payload?.raceName),
      distance: toText(payload?.distance),
      going: toText(payload?.going),
      raceClass: toText(payload?.raceClass),
    };
  }

  return {
    track: bundle.entities.track,
    raceNumber: bundle.entities.raceNumber,
    raceDate: bundle.entities.date,
    raceName: bundle.entities.raceName,
    distance: undefined as string | undefined,
    going: undefined as string | undefined,
    raceClass: undefined as string | undefined,
  };
}

export function extractBestTip(bundle: StrideRetrievalBundle) {
  const tipsItem = pickEvidence(bundle, "local_tips")[0];
  if (tipsItem) {
    const payload = asRecord(tipsItem.payload);
    const betPick = asRecord(payload?.betPick);
    const primaryPick = asRecord(payload?.primaryPick);
    const topPick = asRecord(asArray(payload?.topPicks)[0]);
    const chosen = betPick ?? primaryPick ?? topPick;
    if (chosen) {
      return {
        source: tipsItem.source,
        track: tipsItem.track,
        raceNumber: tipsItem.raceNumber,
        raceDate: tipsItem.raceDate,
        horse: toText(chosen.horse),
        odds: formatOdds(chosen.odds),
        fairOdds: formatOdds(chosen.fair_odds),
        winPct: formatPct(chosen.win_pct),
        edgePct: formatPct(chosen.edge_pct),
        confidence: toText(chosen.confidence),
        stake: toText(chosen.staking),
        selectionScore: toNumber(chosen.selection_score),
        valueRating: toText(chosen.value_rating),
      };
    }
  }

  const selectionItem = pickEvidence(bundle, "selections")
    .sort((a, b) => {
      const aPayload = asRecord(a.payload);
      const bPayload = asRecord(b.payload);
      return (toNumber(bPayload?.edge) ?? Number.NEGATIVE_INFINITY) - (toNumber(aPayload?.edge) ?? Number.NEGATIVE_INFINITY);
    })[0];

  if (selectionItem) {
    const payload = asRecord(selectionItem.payload);
    return {
      source: selectionItem.source,
      track: selectionItem.track,
      raceNumber: selectionItem.raceNumber,
      raceDate: selectionItem.raceDate,
      horse: toText(payload?.horseName),
      odds: formatOdds(payload?.marketOdds),
      fairOdds: undefined,
      winPct: formatPct(payload?.winPercentage),
      edgePct: formatPct(payload?.edge),
      confidence: toText(payload?.confidence),
      stake: undefined,
      selectionScore: undefined,
      valueRating: undefined,
    };
  }

  const analysisItem = pickEvidence(bundle, "race_analyses")[0];
  if (analysisItem) {
    const payload = asRecord(analysisItem.payload);
    return {
      source: analysisItem.source,
      track: analysisItem.track,
      raceNumber: analysisItem.raceNumber,
      raceDate: analysisItem.raceDate,
      horse: toText(payload?.predictedWinner),
      odds: undefined,
      fairOdds: undefined,
      winPct: undefined,
      edgePct: undefined,
      confidence: undefined,
      stake: undefined,
      selectionScore: undefined,
      valueRating: undefined,
    };
  }

  return undefined;
}

export function extractConsensusLeader(bundle: StrideRetrievalBundle) {
  for (const item of pickEvidence(bundle, "local_consensus")) {
    const payload = asRecord(item.payload);
    if (!payload) continue;
    const runners = Object.entries(payload)
      .map(([horse, stats]) => ({ horse, stats: asRecord(stats) }))
      .filter((entry) => entry.stats)
      .sort(
        (a, b) =>
          (toNumber(b.stats?.crowd_score) ?? toNumber(b.stats?.consensus_score) ?? Number.NEGATIVE_INFINITY) -
          (toNumber(a.stats?.crowd_score) ?? toNumber(a.stats?.consensus_score) ?? Number.NEGATIVE_INFINITY),
      );
    if (runners.length > 0) {
      const top = runners[0];
      return {
        horse: top.horse,
        crowdScore: formatPct(top.stats?.crowd_score, 0),
        votePct: formatPct(top.stats?.vote_pct),
        mentions: toNumber(top.stats?.total_mentions),
      };
    }
  }
  return undefined;
}

export function extractMarketLeader(bundle: StrideRetrievalBundle) {
  for (const item of pickEvidence(bundle, "local_market_signals")) {
    const payload = asRecord(item.payload);
    if (!payload) continue;
    const runners = Object.entries(payload)
      .map(([horse, stats]) => ({ horse, stats: asRecord(stats) }))
      .filter((entry) => entry.stats)
      .sort(
        (a, b) =>
          (toNumber(b.stats?.market_signal_score) ?? Number.NEGATIVE_INFINITY) -
          (toNumber(a.stats?.market_signal_score) ?? Number.NEGATIVE_INFINITY),
      );
    if (runners.length > 0) {
      const top = runners[0];
      return {
        horse: top.horse,
        score: toNumber(top.stats?.market_signal_score),
        type: toText(top.stats?.signal_type),
        baseline: formatOdds(top.stats?.baseline_price),
        morning: formatOdds(top.stats?.morning_price),
        movementPct: formatPct(top.stats?.movement_pct),
      };
    }
  }
  return undefined;
}

function buildInsufficientAnswer(bundle: StrideRetrievalBundle): string {
  const scope = buildScopeLabel(bundle);
  const lines = [
    scope
      ? `I couldn't find matching local evidence for ${scope} in your racecards, intelligence files, or database tables.`
      : "I couldn't anchor that to a specific horse, race, track, or date in your local racing dataset.",
  ];

  // Scan evidence for available dates and tracks to suggest
  const availDates = new Set<string>();
  const availTracks = new Set<string>();
  for (const item of bundle.evidence) {
    if (item.raceDate) availDates.add(item.raceDate);
    if (item.track) availTracks.add(item.track);
  }

  if (availDates.size > 0) {
    const sortedDates = [...availDates].sort().slice(-5);
    lines.push(`Available dates: ${sortedDates.join(", ")}.`);
  }
  if (availTracks.size > 0) {
    lines.push(`Available tracks: ${[...availTracks].sort().join(", ")}.`);
  }

  if (bundle.followUps.length > 0) {
    lines.push(bundle.followUps[0]);
  } else if (availDates.size === 0 && availTracks.size === 0) {
    lines.push("Try asking with a horse name, track, race number, and date so I can pull the right evidence.");
  } else {
    lines.push("Try asking about one of the dates or tracks above.");
  }

  return lines.join("\n");
}

function buildHorseHistoryAnswer(bundle: StrideRetrievalBundle): string {
  const rrh = pickEvidence(bundle, "race_results_history").slice(0, 5);
  if (rrh.length === 0) return buildInsufficientAnswer(bundle);

  const latestPayload = asRecord(rrh[0].payload);
  const horseName = rrh[0].horseName ?? bundle.entities.horseName ?? "This runner";
  const lines: string[] = [];

  lines.push(
    `${horseName} — latest: ${rrh[0].track} R${rrh[0].raceNumber ?? "?"} on ${rrh[0].raceDate ?? "?"}, finished ${toText(latestPayload?.position) ?? "?"}${formatOdds(latestPayload?.spOdds) ? ` at $${formatOdds(latestPayload?.spOdds)}` : ""}.`,
  );

  if (rrh.length > 1) {
    const recentRuns = rrh
      .slice(1, 5)
      .map((item) => {
        const payload = asRecord(item.payload);
        return `${item.raceDate} ${item.track}: ${toText(payload?.position) ?? "?"}${formatOdds(payload?.spOdds) ? ` ($${formatOdds(payload?.spOdds)})` : ""}`;
      })
      .join("; ");
    lines.push(`Form line: ${recentRuns}.`);
  }

  // 4-phase analysis from horse_race_analyses
  const analysis = pickEvidence(bundle, "horse_race_analyses")[0];
  if (analysis) {
    const ap = asRecord(analysis.payload);
    const trainOfThought = toText(ap?.trainOfThought);
    const comparative = toText(ap?.phase4Comparative);
    const canWin = toText(ap?.canWin);
    const upsetScenario = toText(ap?.upsetScenario);
    const assessedWinProb = toNumber(ap?.assessedWinProb);

    if (trainOfThought) {
      const snippet = trainOfThought.length > 300 ? trainOfThought.slice(0, 300) + "..." : trainOfThought;
      lines.push(`Analysis: ${snippet}`);
    }
    if (comparative) lines.push(`Comparative edge: ${comparative}`);
    if (canWin) lines.push(`Can win? ${canWin}`);
    if (upsetScenario) lines.push(`Upset scenario: ${upsetScenario}`);
    if (assessedWinProb != null) lines.push(`Assessed win probability: ${(assessedWinProb * 100).toFixed(1)}%`);
  }

  // Upcoming selection data if available
  const selection = pickEvidence(bundle, "selections")
    .find((item) => (item.horseName ?? "").toLowerCase() === horseName.toLowerCase());
  if (selection) {
    const sp = asRecord(selection.payload);
    const selParts = [
      toText(sp?.convergenceTier) ?? toText(sp?.convergenceGate) ? `tier: ${toText(sp?.convergenceTier) ?? toText(sp?.convergenceGate)}` : null,
      formatPct(sp?.edge) ? `edge ${formatPct(sp?.edge)}` : null,
      formatOdds(sp?.marketOdds) ? `odds $${formatOdds(sp?.marketOdds)}` : null,
      toText(sp?.confidence) ? `${toText(sp?.confidence)} confidence` : null,
      toNumber(sp?.convergenceScore) != null ? `convergence ${toNumber(sp?.convergenceScore)?.toFixed(0)}` : null,
    ].filter(Boolean);
    if (selParts.length > 0) {
      lines.push(`Upcoming selection: ${selParts.join(" | ")}.`);
    }
  }

  const sectional = pickEvidence(bundle, "sectional_times")[0];
  if (sectional) {
    const payload = asRecord(sectional.payload);
    const burst = toNumber(payload?.finishingBurst);
    const last600 = toNumber(payload?.last600mSpeed);
    if (burst !== undefined || last600 !== undefined) {
      lines.push(
        `Sectionals: ${burst !== undefined ? `finishing burst ${burst.toFixed(1)}` : ""}${burst !== undefined && last600 !== undefined ? " | " : ""}${last600 !== undefined ? `last 600 speed ${last600.toFixed(1)}` : ""}.`,
      );
    }
  }

  const franking = pickEvidence(bundle, "franking_scores")[0];
  if (franking) {
    const payload = asRecord(franking.payload);
    const frankParts = [
      toText(payload?.frankingScore) ? `score ${toText(payload?.frankingScore)}` : null,
      toText(payload?.frankingConfidence) ? `confidence ${toText(payload?.frankingConfidence)}` : null,
      toNumber(payload?.frankingElo) ? `ELO ${toNumber(payload?.frankingElo)?.toFixed(0)}` : null,
      payload?.antiFranked ? "ANTI-FRANKED" : null,
    ].filter(Boolean);
    if (frankParts.length > 0) lines.push(`Franking: ${frankParts.join(" | ")}.`);
  }

  const blackbook = pickEvidence(bundle, "blackbook_entries")[0] ?? pickEvidence(bundle, "blackbook_entry_runs")[0];
  if (blackbook) {
    const payload = asRecord(blackbook.payload);
    lines.push(
      `Blackbook: ${toText(payload?.status) ?? "tracked"}${toText(payload?.readinessBand) ? ` | readiness ${toText(payload?.readinessBand)}` : ""}${toText(payload?.primaryReason) ? ` — ${String(toText(payload?.primaryReason)).slice(0, 100)}` : ""}.`,
    );
  }

  return lines.join("\n");
}

function buildRacePreviewAnswer(bundle: StrideRetrievalBundle): string {
  const race = extractRaceMeta(bundle);
  const best = extractBestTip(bundle);
  const hasDirectRaceNameEvidence = bundle.entities.raceName
    ? bundle.evidence.some(
        (item) =>
          item.title.toLowerCase().includes(bundle.entities.raceName!.toLowerCase()) ||
          item.summary.toLowerCase().includes(bundle.entities.raceName!.toLowerCase()),
      )
    : false;

  if (!best?.horse && !race.track && bundle.entities.raceName && !hasDirectRaceNameEvidence) {
    return `I couldn't find a saved field, racecard, or tips file for ${bundle.entities.raceName} in your local dataset yet. Ask again once that meeting is in the racecards or give me the exact track and date if you want me to check another saved card.`;
  }

  const lines: string[] = [];

  lines.push(
    `I can answer this from your local ${race.track ?? bundle.entities.track ?? "race"} dataset${race.raceNumber ? ` for R${race.raceNumber}` : ""}${race.raceDate ? ` on ${race.raceDate}` : ""}${race.raceName ? ` — ${race.raceName}` : ""}.`,
  );

  if (race.distance || race.going || race.raceClass) {
    lines.push(
      [race.distance ? `distance ${race.distance}` : null, race.going ? `going ${race.going}` : null, race.raceClass ? `class ${race.raceClass}` : null]
        .filter(Boolean)
        .join(" | ") + ".",
    );
  }

  // Full field ranking by edge, showing convergence tier badges
  const allSelections = pickEvidence(bundle, "selections")
    .map((item) => ({ item, payload: asRecord(item.payload) }))
    .sort((a, b) => (toNumber(b.payload?.edge) ?? -999) - (toNumber(a.payload?.edge) ?? -999));

  if (allSelections.length > 0) {
    // Convergence tier distribution
    const tierCounts: Record<string, number> = {};
    for (const { payload } of allSelections) {
      const tier = toText(payload?.convergenceTier) ?? toText(payload?.convergenceGate) ?? "UNSET";
      tierCounts[tier] = (tierCounts[tier] ?? 0) + 1;
    }
    const tierSummary = Object.entries(tierCounts)
      .filter(([t]) => t !== "UNSET")
      .map(([t, c]) => `${c} ${t}`)
      .join(", ");
    if (tierSummary) {
      lines.push(`Field of ${allSelections.length}: ${tierSummary}.`);
    }

    // Top 5 selections with convergence data
    const top = allSelections.slice(0, 5);
    for (let i = 0; i < top.length; i++) {
      const { payload } = top[i];
      const horse = toText(payload?.horseName) ?? "Unknown";
      const tier = toText(payload?.convergenceTier) ?? toText(payload?.convergenceGate);
      const tierBadge = tier ? `[${tier}]` : "";
      const parts = [
        `${i + 1}. ${horse} ${tierBadge}`.trim(),
        formatOdds(payload?.marketOdds) ? `$${formatOdds(payload?.marketOdds)}` : null,
        formatPct(payload?.edge) ? `edge ${formatPct(payload?.edge)}` : null,
        formatPct(payload?.winPercentage) ? `win ${formatPct(payload?.winPercentage)}` : null,
        toText(payload?.confidence) ? `${toText(payload?.confidence)} conf` : null,
      ].filter(Boolean).join(" | ");
      lines.push(parts);

      // Convergence detail for top 2
      if (i < 2) {
        const convParts = [
          toNumber(payload?.convergenceScore) != null ? `conv ${toNumber(payload?.convergenceScore)?.toFixed(0)}` : null,
          toNumber(payload?.consensusScore) != null ? `consensus ${toNumber(payload?.consensusScore)?.toFixed(0)}` : null,
          toNumber(payload?.marketSignalScore) != null ? `market ${toNumber(payload?.marketSignalScore)?.toFixed(0)}` : null,
          toText(payload?.marketConfidenceLabel) ? `MCS: ${toText(payload?.marketConfidenceLabel)}` : null,
        ].filter(Boolean);
        if (convParts.length > 0) lines.push(`   ↳ ${convParts.join(" | ")}`);
      }
    }

    // Danger runner callout
    if (top.length >= 2) {
      const danger = top[1];
      const dangerHorse = toText(danger.payload?.horseName) ?? "Unknown";
      const dangerTier = toText(danger.payload?.convergenceTier) ?? toText(danger.payload?.convergenceGate);
      lines.push(`Danger: ${dangerHorse}${dangerTier ? ` (${dangerTier})` : ""} is the main threat.`);
    }

    // Fitness note for top pick
    const topPayload = top[0].payload;
    const atPeak = topPayload?.fitnessIsAtPeakRun;
    const fitnessLabel = toText(topPayload?.fitnessRunLabel);
    const fitnessReady = toNumber(topPayload?.fitnessReadinessScore);
    if (atPeak === true || atPeak === "true") {
      lines.push(`${toText(topPayload?.horseName) ?? "Top pick"} is at peak fitness run${fitnessReady != null ? ` (readiness ${fitnessReady.toFixed(0)})` : ""}.`);
    } else if (fitnessLabel) {
      lines.push(`${toText(topPayload?.horseName) ?? "Top pick"} fitness: ${fitnessLabel}${fitnessReady != null ? ` (readiness ${fitnessReady.toFixed(0)})` : ""}.`);
    }

    // Banker flag
    const bankerTier = toText(topPayload?.bankerTier);
    if (bankerTier && bankerTier !== "NONE") {
      lines.push(`${toText(topPayload?.horseName) ?? "Top pick"} carries a ${bankerTier} banker flag.`);
    }
  } else if (best?.horse) {
    const bestLine = [
      `${best.horse} is the strongest local handle`,
      best.odds ? `at ${best.odds}` : null,
      best.winPct ? `win ${best.winPct}` : null,
      best.edgePct ? `edge ${best.edgePct}` : null,
      best.confidence ? `${best.confidence} confidence` : null,
    ]
      .filter(Boolean)
      .join(" | ");
    lines.push(`${bestLine}.`);
  } else {
    lines.push("I can see the race context, but there is no strong saved bet pick in the local evidence bundle for this race yet.");
  }

  const consensus = extractConsensusLeader(bundle);
  if (consensus?.horse) {
    lines.push(
      `Crowd consensus leans to ${consensus.horse}${consensus.crowdScore ? ` (score ${consensus.crowdScore})` : ""}${consensus.votePct ? `, ${consensus.votePct} vote share` : ""}.`,
    );
  }

  const market = extractMarketLeader(bundle);
  if (market?.horse) {
    lines.push(
      `Market signal: ${market.horse}${market.type ? ` (${market.type})` : ""}${market.movementPct ? `, movement ${market.movementPct}` : ""}.`,
    );
  }

  return lines.join("\n");
}

function buildRaceResultAnswer(bundle: StrideRetrievalBundle): string {
  const resultRows = pickEvidence(bundle, "race_results_history")
    .slice(0, 6)
    .map((item) => ({ item, payload: asRecord(item.payload) }));

  if (resultRows.length === 0) return buildInsufficientAnswer(bundle);

  const topThree = resultRows
    .filter(({ payload }) => (toNumber(payload?.position) ?? 99) <= 3)
    .sort((a, b) => (toNumber(a.payload?.position) ?? 99) - (toNumber(b.payload?.position) ?? 99))
    .slice(0, 3);

  if (topThree.length > 0) {
    const placings = topThree
      .map(({ item, payload }) => `${toText(payload?.position)}. ${item.horseName ?? "Unknown"}${formatOdds(payload?.spOdds) ? ` (${formatOdds(payload?.spOdds)})` : ""}`)
      .join(" ");
    const header = topThree[0].item;
    const lines = [`The local result for ${header.track} R${header.raceNumber ?? "?"}${header.raceDate ? ` on ${header.raceDate}` : ""} is: ${placings}.`];

    const tipResult = pickEvidence(bundle, "stride_tip_results")[0] ?? pickEvidence(bundle, "local_tip_results")[0];
    if (tipResult) {
      const payload = asRecord(tipResult.payload);
      lines.push(
        `Stride tipped ${toText(payload?.tippedHorseName) ?? toText(payload?.horse_name) ?? "unknown"} and the stored result is ${toText(payload?.result) ?? "logged"}${toText(payload?.actualWinnerName) ? ` against winner ${toText(payload?.actualWinnerName)}` : ""}.`,
      );
    }

    return lines.join(" ");
  }

  return `I found result rows for ${resultRows[0].item.track} R${resultRows[0].item.raceNumber ?? "?"}, but not a clean top-three summary. The evidence I do have is ${resultRows.slice(0, 3).map(({ item }) => item.summary).join("; ")}.`;
}

function buildSectionalAnswer(bundle: StrideRetrievalBundle): string {
  const sectionals = pickEvidence(bundle, "sectional_times").slice(0, 6);
  if (sectionals.length === 0) return buildInsufficientAnswer(bundle);

  const ranked = sectionals
    .map((item) => {
      const payload = asRecord(item.payload);
      return {
        item,
        last600: toNumber(payload?.last600mSpeed) ?? Number.NEGATIVE_INFINITY,
        burst: toNumber(payload?.finishingBurst) ?? Number.NEGATIVE_INFINITY,
      };
    })
    .sort((a, b) => {
      const burstDelta = b.burst - a.burst;
      if (burstDelta !== 0) return burstDelta;
      return b.last600 - a.last600;
    });

  const top = ranked[0];
  const runner = top.item.horseName ?? bundle.entities.horseName ?? "The top sectional runner";
  const lines = [
    `${runner} is the strongest sectional read in the local bundle for ${top.item.track ?? bundle.entities.track ?? "this race"}${top.item.raceNumber ? ` R${top.item.raceNumber}` : ""}.`,
  ];

  if (Number.isFinite(top.last600)) lines.push(`Its last 600 speed is ${top.last600.toFixed(1)}.`);
  if (Number.isFinite(top.burst)) lines.push(`Finishing burst is ${top.burst.toFixed(1)}.`);

  if (ranked.length > 1) {
    const chasing = ranked
      .slice(1, 3)
      .map((entry) => `${entry.item.horseName ?? "Unknown"}${Number.isFinite(entry.burst) ? ` (burst ${entry.burst.toFixed(1)})` : ""}`)
      .join(", ");
    if (chasing) lines.push(`Next sectional profiles are ${chasing}.`);
  }

  return lines.join(" ");
}

function buildTrackProfileAnswer(bundle: StrideRetrievalBundle): string {
  const intelligence = pickEvidence(bundle, "local_intelligence").slice(0, 4);
  const performance = pickEvidence(bundle, "performance_stats")[0];

  if (intelligence.length === 0 && !performance) return buildInsufficientAnswer(bundle);

  const lines: string[] = [];
  if (intelligence.length > 0) {
    lines.push(`I found track intelligence for ${bundle.entities.track ?? intelligence[0].track ?? "this venue"}: ${intelligence.map((item) => item.title).join(", ")}.`);
    lines.push(intelligence[0].summary);
  }

  if (performance) {
    const payload = asRecord(performance.payload);
    lines.push(
      `The performance snapshot for ${performance.track ?? bundle.entities.track ?? "this track"} shows ${toText(payload?.totalBets) ?? "?"} bets, strike ${formatPct(payload?.strikeRate)}${formatPct(payload?.roi) ? `, ROI ${formatPct(payload?.roi)}` : ""}.`,
    );
  }

  return lines.slice(0, 3).join(" ");
}

function buildConsensusAnswer(bundle: StrideRetrievalBundle): string {
  const lines: string[] = [];

  // DB convergence data (from fetchConvergenceEvidence)
  const convItems = pickEvidence(bundle, "convergence_output")
    .map((item) => ({ item, payload: asRecord(item.payload) }))
    .sort((a, b) => (toNumber(b.payload?.final_convergence_score) ?? -999) - (toNumber(a.payload?.final_convergence_score) ?? -999));

  if (convItems.length > 0) {
    const raceLabel = `${convItems[0].item.track ?? bundle.entities.track ?? "this race"} R${convItems[0].item.raceNumber ?? "?"}`;
    lines.push(`CONVERGENCE PICTURE — ${raceLabel}:`);

    // Top 3 horses with full 3-pillar breakdown
    for (const { payload } of convItems.slice(0, 5)) {
      const horse = toText(payload?.horse_name) ?? "Unknown";
      const tier = toText(payload?.convergence_tier);
      const tierBadge = tier ? `[${tier}]` : "";
      const parts = [
        `${horse} ${tierBadge}`.trim(),
        toNumber(payload?.stride_score) != null ? `STRIDE ${toNumber(payload?.stride_score)?.toFixed(0)}` : null,
        toNumber(payload?.consensus_score) != null ? `consensus ${toNumber(payload?.consensus_score)?.toFixed(0)}` : null,
        toNumber(payload?.market_signal_score) != null ? `market ${toNumber(payload?.market_signal_score)?.toFixed(0)}` : null,
        toNumber(payload?.final_convergence_score) != null ? `final ${toNumber(payload?.final_convergence_score)?.toFixed(0)}` : null,
        toNumber(payload?.vote_pct) != null ? `votes ${toNumber(payload?.vote_pct)?.toFixed(0)}%` : null,
      ].filter(Boolean);
      lines.push(parts.join(" | "));
    }

    // Agreement/divergence analysis
    if (convItems.length >= 2) {
      const top = convItems[0].payload;
      const second = convItems[1].payload;
      const topStride = toNumber(top?.stride_score) ?? 0;
      const topConsensus = toNumber(top?.consensus_score) ?? 0;
      const secondConsensus = toNumber(second?.consensus_score) ?? 0;
      if (topStride > 60 && topConsensus > 60) {
        lines.push(`Model and consensus AGREE on ${toText(top?.horse_name) ?? "the top pick"}.`);
      } else if (topStride > 60 && secondConsensus > topConsensus) {
        lines.push(`Model likes ${toText(top?.horse_name) ?? "top pick"} but consensus favours ${toText(second?.horse_name) ?? "second pick"} — divergence.`);
      }
    }
  }

  // Local consensus JSON data
  const consensus = extractConsensusLeader(bundle);
  if (consensus?.horse && convItems.length === 0) {
    lines.push(
      `Crowd consensus leader: ${consensus.horse}${consensus.crowdScore ? ` (score ${consensus.crowdScore})` : ""}${consensus.votePct ? `, ${consensus.votePct} vote share` : ""}${consensus.mentions != null ? `, ${consensus.mentions} mentions` : ""}.`,
    );
  }

  // Market signals
  const market = extractMarketLeader(bundle);
  if (market?.horse) {
    lines.push(
      `Market signal: ${market.horse}${market.type ? ` (${market.type})` : ""}${market.movementPct ? `, movement ${market.movementPct}` : ""}${market.baseline && market.morning ? `, price ${market.baseline} → ${market.morning}` : ""}.`,
    );
  }

  if (lines.length === 0) return buildInsufficientAnswer(bundle);
  return lines.join("\n");
}

function buildMarketAnswer(bundle: StrideRetrievalBundle): string {
  const market = extractMarketLeader(bundle);
  if (!market) return buildInsufficientAnswer(bundle);
  const lines = [
    `The strongest market signal in the local file is ${market.horse}${market.type ? ` (${market.type})` : ""}.`,
  ];
  if (market.movementPct) lines.push(`Movement is ${market.movementPct}.`);
  if (market.baseline || market.morning) {
    lines.push(`Prices moved from ${market.baseline ?? "unknown"} to ${market.morning ?? "unknown"}.`);
  }
  return lines.join(" ");
}

function buildPerformanceAnswer(bundle: StrideRetrievalBundle): string {
  const lines: string[] = [];

  // Live P/L from selection_results (30-day)
  const liveOverall = pickEvidence(bundle, "selection_results").find(
    (item) => item.title?.includes("30-day") || item.title?.includes("overall"),
  );
  if (liveOverall) {
    const p = asRecord(liveOverall.payload);
    const bets = toNumber(p?.totalBets);
    const winners = toNumber(p?.winners);
    const placers = toNumber(p?.placers);
    const strike = toNumber(p?.strikeRate);
    const roi = toNumber(p?.roi);
    const pl = toNumber(p?.profitLoss);
    lines.push("STRIDE PERFORMANCE (30-DAY):");
    const statParts = [
      bets != null ? `${bets} bets` : null,
      winners != null ? `${winners} winners` : null,
      placers != null ? `${placers} placers` : null,
      strike != null ? `strike ${strike.toFixed(1)}%` : null,
      roi != null ? `ROI ${roi >= 0 ? "+" : ""}${roi.toFixed(1)}%` : null,
      pl != null ? `P/L ${pl >= 0 ? "+$" : "-$"}${Math.abs(pl).toFixed(2)}` : null,
    ].filter(Boolean);
    if (statParts.length > 0) lines.push(statParts.join(" | "));
  }

  // 7-day trend
  const liveTrend = pickEvidence(bundle, "selection_results").find(
    (item) => item.title?.includes("7-day") || item.title?.includes("trend"),
  );
  if (liveTrend) {
    const p = asRecord(liveTrend.payload);
    const bets7 = toNumber(p?.totalBets);
    const strike7 = toNumber(p?.strikeRate);
    const roi7 = toNumber(p?.roi);
    const pl7 = toNumber(p?.profitLoss);
    const trendParts = [
      bets7 != null ? `${bets7} bets` : null,
      strike7 != null ? `strike ${strike7.toFixed(1)}%` : null,
      roi7 != null ? `ROI ${roi7 >= 0 ? "+" : ""}${roi7.toFixed(1)}%` : null,
      pl7 != null ? `P/L ${pl7 >= 0 ? "+$" : "-$"}${Math.abs(pl7).toFixed(2)}` : null,
    ].filter(Boolean);
    if (trendParts.length > 0) lines.push(`7-day trend: ${trendParts.join(" | ")}`);
  }

  // By confidence tier
  const tierResults = pickEvidence(bundle, "selection_results").filter(
    (item) => item.title?.includes("tier") || item.title?.includes("confidence"),
  );
  if (tierResults.length > 0) {
    lines.push("BY CONFIDENCE TIER:");
    for (const tier of tierResults) {
      const p = asRecord(tier.payload);
      const tierName = toText(p?.tier) ?? toText(p?.confidence) ?? "Unknown";
      const tierParts = [
        toNumber(p?.totalBets) != null ? `${toNumber(p?.totalBets)} bets` : null,
        toNumber(p?.strikeRate) != null ? `strike ${toNumber(p?.strikeRate)?.toFixed(1)}%` : null,
        toNumber(p?.roi) != null ? `ROI ${(toNumber(p?.roi) ?? 0) >= 0 ? "+" : ""}${toNumber(p?.roi)?.toFixed(1)}%` : null,
        toNumber(p?.profitLoss) != null ? `P/L ${(toNumber(p?.profitLoss) ?? 0) >= 0 ? "+$" : "-$"}${Math.abs(toNumber(p?.profitLoss) ?? 0).toFixed(2)}` : null,
      ].filter(Boolean);
      if (tierParts.length > 0) lines.push(`  ${tierName}: ${tierParts.join(" | ")}`);
    }
  }

  // By track
  const trackResults = pickEvidence(bundle, "selection_results").filter(
    (item) => item.title?.includes("track") && !item.title?.includes("trend"),
  );
  if (trackResults.length > 0) {
    lines.push("BY TRACK:");
    for (const tr of trackResults.slice(0, 6)) {
      const p = asRecord(tr.payload);
      const trackName = toText(p?.track) ?? tr.track ?? "Unknown";
      const tParts = [
        toNumber(p?.totalBets) != null ? `${toNumber(p?.totalBets)} bets` : null,
        toNumber(p?.strikeRate) != null ? `strike ${toNumber(p?.strikeRate)?.toFixed(1)}%` : null,
        toNumber(p?.roi) != null ? `ROI ${(toNumber(p?.roi) ?? 0) >= 0 ? "+" : ""}${toNumber(p?.roi)?.toFixed(1)}%` : null,
      ].filter(Boolean);
      if (tParts.length > 0) lines.push(`  ${trackName}: ${tParts.join(" | ")}`);
    }
  }

  if (lines.length > 0) return lines.join("\n");

  // Fallback to static snapshot
  const performance = pickEvidence(bundle, "performance_stats")[0];
  if (performance) {
    const payload = asRecord(performance.payload);
    return `Your local performance snapshot${performance.track ? ` for ${performance.track}` : ""} shows ${toText(payload?.totalBets) ?? "?"} bets, ${toText(payload?.winners) ?? "?"} winners, strike ${formatPct(payload?.strikeRate) ?? "?"}${formatPct(payload?.roi) ? `, and ROI ${formatPct(payload?.roi)}` : ""}.`;
  }

  const audits = pickEvidence(bundle, "prediction_audit").slice(0, 3);
  if (audits.length > 0) {
    return `I don't have a rolled-up performance snapshot, but I do have prediction audit rows like ${audits.map((item) => `${item.track} R${item.raceNumber} ${item.horseName ?? ""}`.trim()).join(", ")} in the local database.`;
  }

  return buildInsufficientAnswer(bundle);
}

function buildModelQualityAnswer(bundle: StrideRetrievalBundle): string {
  const lines = [
    "In your pipeline, edge is explicit rather than decorative: raw market implied probability is 100 / odds, then normalized by total overround to get trueMarketProb.",
    "computedEdge is rawModelProb minus trueMarketProb, and expected value is calculated as ((rawModelProb / trueMarketProb) - 1) * 100 before long-odds damping.",
    "Selection score is also code-defined: 40% rawProb, 30% calibratedProb, and 30% clamped edge, with the edge capped to prevent longshot distortion.",
  ];

  if (bundle.sourceCounts.prediction_audit > 0 || bundle.sourceCounts.selections > 0 || bundle.sourceCounts.selection_results > 0) {
    lines.push("That logic is then persisted into your selections, prediction_audit, and result tables, which is the data Stride should be reading back when you ask model-quality questions.");
  }

  return lines.join(" ");
}

function buildGeneralEvidenceAnswer(bundle: StrideRetrievalBundle): string {
  if (bundle.evidence.length === 0) return buildInsufficientAnswer(bundle);

  const scope = buildScopeLabel(bundle);
  const lines = [
    scope ? `I found ${bundle.evidence.length} local evidence items for ${scope}.` : `I found ${bundle.evidence.length} local evidence items tied to your question.`,
    ...bundle.evidence.slice(0, 3).map((item) => formatEvidenceLine(item)),
  ];

  if (bundle.followUps.length > 0) {
    lines.push(bundle.followUps[0]);
  }

  return lines.join(" ");
}

export function buildLocalStrideReasoning(bundle: StrideRetrievalBundle): string {
  if (bundle.classification.intent === "model_quality") {
    return [
      `Intent: model_quality (${Math.round(bundle.classification.confidence * 100)}%).`,
      "This is a code-path question, so the grounded answer should come from the pipeline formulas rather than from generic race commentary.",
      "Use the pipeline definitions: trueMarketProb = (100 / odds) / totalOverround, computedEdge = rawModelProb - trueMarketProb, expectedValue = ((rawModelProb / trueMarketProb) - 1) * 100.",
      "Also mention selectionScore: 40% rawProb, 30% calibratedProb, 30% clamped edge, with long-odds damping above $15 / $20 / $30.",
      bundle.sourceCounts.prediction_audit + bundle.sourceCounts.selections + bundle.sourceCounts.selection_results > 0
        ? "Relevant audit / selection tables are present in the retrieval bundle, so the answer can point back to persisted model outputs as well."
        : "The retrieval bundle is light on audit tables here, so the safest answer is the code-level formula plus what tables should contain the outputs.",
    ].join("\n");
  }

  const scope = buildScopeLabel(bundle);
  const lines = [
    `Intent: ${bundle.classification.intent} (${Math.round(bundle.classification.confidence * 100)}%).`,
    scope ? `Scope: ${scope}.` : "Scope: broad question with no single horse/race anchor.",
    `Evidence: ${bundle.evidence.length} items from ${buildRetrievalMeta(bundle).sourcesUsed.slice(0, 6).join(", ") || "no local sources"}.`,
  ];

  if (bundle.evidence.length === 0) {
    lines.push("No local evidence matched cleanly, so the safe response is to say what is missing and ask for a tighter horse / track / race / date anchor.");
  } else {
    bundle.evidence.slice(0, 5).forEach((item) => lines.push(`- ${formatEvidenceLine(item)}`));
  }

  if (bundle.followUps.length > 0) {
    lines.push(`Next question: ${bundle.followUps[0]}`);
  }

  return lines.join("\n");
}

function buildJockeyTrainerAnswer(bundle: StrideRetrievalBundle): string {
  const items = pickEvidence(bundle, "race_results_history").concat(pickEvidence(bundle, "selections"));
  if (items.length === 0) return buildInsufficientAnswer(bundle);
  const statsItem = items.find(i => (i.payload as Record<string, unknown>)?.jockey || (i.payload as Record<string, unknown>)?.trainer);
  if (!statsItem) return buildGeneralEvidenceAnswer(bundle);
  const p = statsItem.payload as Record<string, unknown>;
  const name = (p.jockey ?? p.trainer ?? "The rider/trainer") as string;
  const lines = [`${name} statistics from the local form database:`];
  if (p.totalRuns) lines.push(`${p.wins ?? 0} wins from ${p.totalRuns} runs (win rate ${p.winPct ?? "?"}%, place rate ${p.placePct ?? "?"}%).`);
  if (p.bestTrack) lines.push(`Best track: ${p.bestTrack}.`);
  if (p.recentRuns) lines.push(`Recent: ${String(p.recentRuns).slice(0, 300)}.`);
  const bookingItem = items.find(i => (i.payload as Record<string, unknown>)?.bookings);
  if (bookingItem) {
    const bookings = ((bookingItem.payload as Record<string, unknown>).bookings as unknown[]);
    if (Array.isArray(bookings) && bookings.length > 0) {
      lines.push(`Has ${bookings.length} booking${bookings.length === 1 ? "" : "s"} today.`);
    }
  }
  return lines.join(" ");
}

function buildQuaddieAnswer(bundle: StrideRetrievalBundle): string {
  const legs = pickEvidence(bundle, "selections").filter(i => (i.payload as Record<string, unknown>)?.legRace != null);
  if (legs.length === 0) return buildInsufficientAnswer(bundle);
  const lines = ["QUADDIE BUILD:"];
  for (const leg of legs) {
    const p = leg.payload as Record<string, unknown>;
    const runners = (p.runners as Array<Record<string, unknown>>) ?? [];
    const legNum = p.legRace;
    const banker = runners[0];
    const backup = runners[1];
    const bankerStr = banker ? `${banker.horseName} (No.${banker.horseNumber ?? "?"}, $${banker.marketOdds ?? "?"})` : "No data";
    const backupStr = backup ? ` + ${backup.horseName} backup` : "";
    lines.push(`Leg ${legNum} — Race ${legNum}: BANKER: ${bankerStr}${backupStr}`);
  }
  lines.push("Single line: " + legs.map(l => { const r = ((l.payload as Record<string, unknown>).runners as Array<Record<string, unknown>>)?.[0]; return r?.horseName ?? "?"; }).join(" / "));
  return lines.join("\n");
}

function buildSpeedMapAnswer(bundle: StrideRetrievalBundle): string {
  const items = pickEvidence(bundle, "selections").filter(i => (i.payload as Record<string, unknown>)?.runners != null);
  if (items.length === 0) return buildInsufficientAnswer(bundle);
  const p = items[0].payload as Record<string, unknown>;
  const runners = (p.runners as Array<Record<string, unknown>>) ?? [];
  if (runners.length === 0) return buildInsufficientAnswer(bundle);

  const styleMap: Record<string, string[]> = { leader: [], on_pace: [], midfield: [], back: [] };
  for (const r of runners) {
    const style = String(r.runningStyle ?? "midfield").toLowerCase().replace(/[^a-z_]/g, "_");
    const label = `${r.horseName} (B${r.barrier ?? "?"})`;
    if (style.includes("lead")) styleMap.leader.push(label);
    else if (style.includes("pace") || style.includes("on_pace")) styleMap.on_pace.push(label);
    else if (style.includes("back") || style.includes("last")) styleMap.back.push(label);
    else styleMap.midfield.push(label);
  }

  const leaderCount = styleMap.leader.length;
  const tempo = leaderCount === 0 ? "No strong leaders — likely to be tactical" : leaderCount === 1 ? "One clear leader — likely to get a soft lead" : leaderCount >= 3 ? "Multiple leaders — genuine pace expected, likely to favour closers" : "Two leaders — moderate tempo likely";

  const lines = ["SPEED MAP:"];
  if (styleMap.leader.length > 0) lines.push(`Leaders: ${styleMap.leader.join(", ")}`);
  if (styleMap.on_pace.length > 0) lines.push(`On-pace: ${styleMap.on_pace.join(", ")}`);
  if (styleMap.midfield.length > 0) lines.push(`Midfield: ${styleMap.midfield.join(", ")}`);
  if (styleMap.back.length > 0) lines.push(`Backmarkers: ${styleMap.back.join(", ")}`);
  lines.push(`Tempo assessment: ${tempo}`);
  return lines.join("\n");
}

function buildMarketScanAnswer(bundle: StrideRetrievalBundle): string {
  const items = pickEvidence(bundle, "selections").filter(i => (i.payload as Record<string, unknown>)?.steamers != null);
  if (items.length === 0) return buildMarketAnswer(bundle);
  const p = items[0].payload as Record<string, unknown>;
  const steamers = (p.steamers as Array<Record<string, unknown>>) ?? [];
  const drifters = (p.drifters as Array<Record<string, unknown>>) ?? [];
  const lines = ["MARKET SCAN:"];
  if (steamers.length > 0) {
    lines.push("Steaming: " + steamers.slice(0, 5).map(s => `${s.horseName} R${s.raceNumber} (${s.steamDriftPct != null ? `+${Number(s.steamDriftPct).toFixed(1)}%` : "sharp money"} @ $${s.marketOdds ?? "?"})`).join(", "));
  } else {
    lines.push("No significant steamers detected today.");
  }
  if (drifters.length > 0) {
    lines.push("Drifting: " + drifters.slice(0, 5).map(d => `${d.horseName} R${d.raceNumber} (${d.steamDriftPct != null ? `${Number(d.steamDriftPct).toFixed(1)}%` : "drifting"} @ $${d.marketOdds ?? "?"})`).join(", "));
  }
  return lines.join("\n");
}

function buildBlackbookTodayAnswer(bundle: StrideRetrievalBundle): string {
  const items = pickEvidence(bundle, "blackbook_entries");
  if (items.length === 0) return "No blackbook horses found running this week. Your blackbook may be empty or none of the tracked horses are nominated.";
  const p = items[0].payload as Record<string, unknown>;
  const matches = (p.matches as Array<Record<string, unknown>>) ?? [];
  if (matches.length === 0) return `No blackbook horses are running this week. Total tracked: ${p.totalBlackbook ?? 0}.`;
  const lines = [`${matches.length} blackbook horse${matches.length === 1 ? "" : "s"} running this week:`];
  for (const m of matches) {
    const readiness = m.readinessBand ? ` [${m.readinessBand}]` : "";
    const reason = m.primaryReason ? ` — ${String(m.primaryReason).slice(0, 80)}` : "";
    lines.push(`${m.horseName}${readiness} — ${m.track} R${m.raceNumber} (${m.raceDate})${reason}`);
  }
  return lines.join("\n");
}

function buildMeetingOverviewAnswer(bundle: StrideRetrievalBundle): string {
  const allSelections = pickEvidence(bundle, "selections")
    .map((item) => ({ item, payload: asRecord(item.payload) }));

  if (allSelections.length === 0) return buildInsufficientAnswer(bundle);

  const track = bundle.entities.track ?? allSelections[0].item.track ?? "this meeting";
  const raceDate = bundle.entities.date ?? allSelections[0].item.raceDate ?? "today";

  // Group by race number
  const byRace = new Map<number, typeof allSelections>();
  for (const entry of allSelections) {
    const rn = entry.item.raceNumber ?? 0;
    if (!byRace.has(rn)) byRace.set(rn, []);
    byRace.get(rn)!.push(entry);
  }

  const lines: string[] = [];
  lines.push(`CARD OVERVIEW — ${track} (${raceDate}):`);
  lines.push(`${allSelections.length} selections across ${byRace.size} races.`);

  // Card-wide tier summary
  const tierCounts: Record<string, number> = {};
  const bankers: string[] = [];
  for (const { payload } of allSelections) {
    const tier = toText(payload?.convergenceTier) ?? toText(payload?.convergenceGate) ?? "UNSET";
    tierCounts[tier] = (tierCounts[tier] ?? 0) + 1;
    const bankerTier = toText(payload?.bankerTier);
    if (bankerTier && bankerTier !== "NONE") {
      bankers.push(`${toText(payload?.horseName) ?? "?"} (${bankerTier})`);
    }
  }
  const tierLine = Object.entries(tierCounts).filter(([t]) => t !== "UNSET").map(([t, c]) => `${c} ${t}`).join(", ");
  if (tierLine) lines.push(`Tiers: ${tierLine}.`);
  if (bankers.length > 0) lines.push(`Bankers: ${bankers.join(", ")}.`);

  // Per-race best pick
  const sortedRaces = [...byRace.entries()].sort((a, b) => a[0] - b[0]);
  for (const [raceNum, entries] of sortedRaces) {
    const sorted = entries.sort((a, b) => (toNumber(b.payload?.edge) ?? -999) - (toNumber(a.payload?.edge) ?? -999));
    const best = sorted[0];
    const bp = best.payload;
    const horse = toText(bp?.horseName) ?? "?";
    const tier = toText(bp?.convergenceTier) ?? toText(bp?.convergenceGate);
    const tierBadge = tier ? `[${tier}]` : "";
    const parts = [
      `R${raceNum}: ${horse} ${tierBadge}`.trim(),
      formatOdds(bp?.marketOdds) ? `$${formatOdds(bp?.marketOdds)}` : null,
      formatPct(bp?.edge) ? `edge ${formatPct(bp?.edge)}` : null,
      toText(bp?.confidence) ? `${toText(bp?.confidence)}` : null,
    ].filter(Boolean);
    lines.push(parts.join(" | "));
  }

  return lines.join("\n");
}

function buildHorseComparisonAnswer(bundle: StrideRetrievalBundle): string {
  const allSelections = pickEvidence(bundle, "selections")
    .map((item) => ({ item, payload: asRecord(item.payload) }));
  const analyses = pickEvidence(bundle, "horse_race_analyses");
  const frankings = pickEvidence(bundle, "franking_scores");
  const sectionals = pickEvidence(bundle, "sectional_times");

  // Try to find two distinct horses
  const horseNames = new Set<string>();
  for (const { payload } of allSelections) {
    const name = toText(payload?.horseName);
    if (name) horseNames.add(name.toUpperCase());
  }
  for (const item of [...analyses, ...frankings, ...sectionals]) {
    if (item.horseName) horseNames.add(item.horseName.toUpperCase());
  }

  if (horseNames.size < 2) {
    return allSelections.length > 0
      ? buildRacePreviewAnswer(bundle)
      : buildInsufficientAnswer(bundle);
  }

  const lines: string[] = [];
  lines.push("HEAD TO HEAD COMPARISON:");

  // Build profile for each horse found
  const horses = [...horseNames].slice(0, 2);
  for (const horseName of horses) {
    const sel = allSelections.find((s) => (toText(s.payload?.horseName) ?? "").toUpperCase() === horseName);
    const anal = analyses.find((a) => (a.horseName ?? "").toUpperCase() === horseName);
    const frank = frankings.find((f) => (f.horseName ?? "").toUpperCase() === horseName);
    const sect = sectionals.find((s) => (s.horseName ?? "").toUpperCase() === horseName);

    lines.push(`\n${horseName}:`);
    if (sel) {
      const sp = sel.payload;
      const selParts = [
        toText(sp?.convergenceTier) ?? toText(sp?.convergenceGate) ? `tier: ${toText(sp?.convergenceTier) ?? toText(sp?.convergenceGate)}` : null,
        formatOdds(sp?.marketOdds) ? `odds $${formatOdds(sp?.marketOdds)}` : null,
        formatPct(sp?.edge) ? `edge ${formatPct(sp?.edge)}` : null,
        formatPct(sp?.winPercentage) ? `win ${formatPct(sp?.winPercentage)}` : null,
        toNumber(sp?.convergenceScore) != null ? `conv ${toNumber(sp?.convergenceScore)?.toFixed(0)}` : null,
        toNumber(sp?.consensusScore) != null ? `consensus ${toNumber(sp?.consensusScore)?.toFixed(0)}` : null,
        toNumber(sp?.marketSignalScore) != null ? `market ${toNumber(sp?.marketSignalScore)?.toFixed(0)}` : null,
      ].filter(Boolean);
      if (selParts.length > 0) lines.push(`  Selection: ${selParts.join(" | ")}`);
    }
    if (anal) {
      const ap = asRecord(anal.payload);
      if (toText(ap?.trainOfThought)) {
        const snippet = String(toText(ap?.trainOfThought)).slice(0, 200);
        lines.push(`  Analysis: ${snippet}${String(toText(ap?.trainOfThought)).length > 200 ? "..." : ""}`);
      }
    }
    if (frank) {
      const fp = asRecord(frank.payload);
      const fParts = [
        toText(fp?.frankingScore) ? `score ${toText(fp?.frankingScore)}` : null,
        toNumber(fp?.frankingElo) ? `ELO ${toNumber(fp?.frankingElo)?.toFixed(0)}` : null,
      ].filter(Boolean);
      if (fParts.length > 0) lines.push(`  Franking: ${fParts.join(" | ")}`);
    }
    if (sect) {
      const secP = asRecord(sect.payload);
      const sParts = [
        toNumber(secP?.last600mSpeed) ? `last600 ${toNumber(secP?.last600mSpeed)?.toFixed(1)}` : null,
        toNumber(secP?.finishingBurst) ? `burst ${toNumber(secP?.finishingBurst)?.toFixed(1)}` : null,
      ].filter(Boolean);
      if (sParts.length > 0) lines.push(`  Sectionals: ${sParts.join(" | ")}`);
    }
  }

  // Verdict based on edge comparison
  const h1Sel = allSelections.find((s) => (toText(s.payload?.horseName) ?? "").toUpperCase() === horses[0]);
  const h2Sel = allSelections.find((s) => (toText(s.payload?.horseName) ?? "").toUpperCase() === horses[1]);
  if (h1Sel && h2Sel) {
    const e1 = toNumber(h1Sel.payload?.edge) ?? 0;
    const e2 = toNumber(h2Sel.payload?.edge) ?? 0;
    const winner = e1 >= e2 ? horses[0] : horses[1];
    lines.push(`\nEdge verdict: ${winner} has the stronger model edge.`);
  }

  return lines.join("\n");
}

function buildAvailableDataAnswer(bundle: StrideRetrievalBundle): string {
  const lines: string[] = [];
  lines.push("AVAILABLE DATA:");

  // Summarize what evidence sources we have
  const sourceCounts = bundle.sourceCounts;
  const dataSources = Object.entries(sourceCounts)
    .filter(([, count]) => count > 0)
    .map(([source, count]) => `${source}: ${count} items`)
    .join(", ");

  if (dataSources) {
    lines.push(`Evidence sources: ${dataSources}.`);
  }

  // List unique dates and tracks from evidence
  const dates = new Set<string>();
  const tracks = new Set<string>();
  for (const item of bundle.evidence) {
    if (item.raceDate) dates.add(item.raceDate);
    if (item.track) tracks.add(item.track);
  }

  if (dates.size > 0) lines.push(`Dates with data: ${[...dates].sort().join(", ")}.`);
  if (tracks.size > 0) lines.push(`Tracks with data: ${[...tracks].sort().join(", ")}.`);

  if (lines.length <= 1) {
    lines.push("No evidence items found in the current retrieval. Try asking about a specific date or track.");
  }

  lines.push("Ask about a specific track, race, or horse and I'll pull the full picture.");
  return lines.join("\n");
}

export function buildLocalStrideResponse(bundle: StrideRetrievalBundle): string {
  if (bundle.answerMode === "insufficient" && bundle.classification.intent !== "model_quality") {
    return buildInsufficientAnswer(bundle);
  }

  switch (bundle.classification.intent) {
    case "horse_history":
      return buildHorseHistoryAnswer(bundle);
    case "race_preview":
      return buildRacePreviewAnswer(bundle);
    case "race_result":
      return buildRaceResultAnswer(bundle);
    case "sectionals":
      return buildSectionalAnswer(bundle);
    case "track_profile":
      return buildTrackProfileAnswer(bundle);
    case "franking":
    case "consensus":
      return buildConsensusAnswer(bundle);
    case "market":
      return buildMarketAnswer(bundle);
    case "performance":
      return buildPerformanceAnswer(bundle);
    case "model_quality":
      return buildModelQualityAnswer(bundle);
    case "meeting_summary":
      return buildRacePreviewAnswer(bundle);
    case "blackbook":
      return buildHorseHistoryAnswer(bundle);
    case "jockey_trainer":
      return buildJockeyTrainerAnswer(bundle);
    case "quaddie_build":
      return buildQuaddieAnswer(bundle);
    case "speed_map":
      return buildSpeedMapAnswer(bundle);
    case "market_scan":
      return buildMarketScanAnswer(bundle);
    case "blackbook_today":
      return buildBlackbookTodayAnswer(bundle);
    case "meeting_overview":
      return buildMeetingOverviewAnswer(bundle);
    case "horse_comparison":
      return buildHorseComparisonAnswer(bundle);
    case "available_data":
      return buildAvailableDataAnswer(bundle);
    default:
      return buildGeneralEvidenceAnswer(bundle);
  }
}

export async function startStrideTurn(input: StartStrideTurnInput): Promise<StrideChatTurn> {
  pruneStrideChatState();
  const sessionId = normalizeSessionId(input.sessionId);
  const bundle = await gatherStrideEvidence({
    question: input.message,
    context: input.context,
    limit: input.limit,
  });
  const turn: StrideChatTurn = {
    turnId: randomUUID(),
    sessionId,
    question: input.message,
    context: input.context,
    createdAt: Date.now(),
    bundle,
    reasoning: buildLocalStrideReasoning(bundle),
    citations: buildCitations(bundle),
    retrieval: buildRetrievalMeta(bundle),
  };

  turnCache.set(turn.turnId, turn);
  pushSessionMessage(sessionId, { role: "user", content: input.message });
  return turn;
}

export async function resolveStrideTurn(input: ResolveStrideTurnInput): Promise<StrideChatTurn> {
  pruneStrideChatState();
  if (input.turnId) {
    const cached = turnCache.get(input.turnId);
    if (cached) return cached;
  }
  return startStrideTurn(input);
}

export function completeStrideTurn(turnId: string, response: string): void {
  const turn = turnCache.get(turnId);
  if (!turn) return;
  pushSessionMessage(turn.sessionId, { role: "assistant", content: response });
}

export function buildStrideReasoningPrompt(turn: StrideChatTurn): string {
  return [
    `Question: ${turn.question}`,
    "",
    "[EVIDENCE BUNDLE]",
    formatStrideEvidenceForPrompt(turn.bundle),
    "",
    "[ANSWER INSTRUCTIONS]",
    formatStrideAnswerInstructions(turn.bundle),
    "",
    "Think step by step from the supplied evidence only. If the evidence is weak, say exactly what is missing.",
  ].join("\n");
}

export function buildStrideResponsePrompt(turn: StrideChatTurn, reasoning: string): string {
  return [
    `Question: ${turn.question}`,
    "",
    "[EVIDENCE BUNDLE]",
    formatStrideEvidenceForPrompt(turn.bundle),
    "",
    "[REASONING]",
    reasoning,
    "",
    "[ANSWER INSTRUCTIONS]",
    formatStrideAnswerInstructions(turn.bundle),
    "",
    "Give a direct Australian horse-racing answer grounded in the evidence bundle. Quote the dataset where useful and do not invent runners, races, or results.",
  ].join("\n");
}
