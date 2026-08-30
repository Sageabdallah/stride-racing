import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Link } from "wouter";
import {
  Activity,
  AlertTriangle,
  ArrowLeft,
  BarChart3,
  Calendar,
  Percent,
  Target,
  TrendingUp,
  Trophy,
  Zap,
} from "lucide-react";
import {
  Area,
  AreaChart,
  CartesianGrid,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip as RechartsTooltip,
  XAxis,
  YAxis,
} from "recharts";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Skeleton } from "@/components/ui/skeleton";

const REVIEW_DATE = "2026-03-21";

type RegionKey = "all" | "melbourne" | "sydney" | "wa" | "sa" | "qld" | "other";
type OutcomeKey = "all" | "winners" | "placers" | "non_winners";

interface ForwardTestGroup {
  label: string;
  selections: number;
  winners: number;
  placers: number;
  strikeRate: number;
  avgPredictedWinPct: number;
  profitUnits: number;
  unitsStaked: number;
}

interface ForwardTestRaceResult {
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

interface ForwardTestSnapshotResponse {
  date: string;
  label: string;
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

interface ResultSummary {
  selections: number;
  winners: number;
  placers: number;
  strikeRate: number;
  avgPredictedWinPct: number;
  totalUnitsStaked: number;
  totalProfitUnits: number;
  roiPct: number;
  trackCount: number;
  tracks: string[];
}

interface TrackStat extends ResultSummary {
  track: string;
}

interface RegionBucket extends ResultSummary {
  key: RegionKey;
  label: string;
}

const REGION_LABELS: Record<RegionKey, string> = {
  all: "All Australia",
  melbourne: "Melbourne",
  sydney: "Sydney",
  wa: "WA",
  sa: "South Australia",
  qld: "Queensland",
  other: "Other",
};

const REGION_ORDER: RegionKey[] = ["melbourne", "sydney", "wa", "sa", "qld", "other"];

function formatUnits(value: number) {
  const abs = Math.abs(value);
  return `${value >= 0 ? "+" : "-"}${abs.toFixed(2)}u`;
}

function formatPrice(value: number | null) {
  if (value == null || Number.isNaN(value)) return "N/A";
  return `$${value.toFixed(2)}`;
}

function formatReviewDate(date: string) {
  return new Date(`${date}T00:00:00`).toLocaleDateString("en-AU", {
    weekday: "long",
    day: "numeric",
    month: "long",
    year: "numeric",
  });
}

function formatJumpTime(value: string | null) {
  if (!value) return "Time N/A";
  return new Date(value).toLocaleTimeString("en-AU", {
    hour: "numeric",
    minute: "2-digit",
  });
}

function ordinalPosition(position: number | null) {
  if (!position) return "N/A";
  const suffix = position % 10 === 1 && position % 100 !== 11
    ? "st"
    : position % 10 === 2 && position % 100 !== 12
      ? "nd"
      : position % 10 === 3 && position % 100 !== 13
        ? "rd"
        : "th";
  return `${position}${suffix}`;
}

function normalizeTrack(track: string) {
  return track.toLowerCase();
}

function getTrackRegion(track: string): RegionKey {
  const normalized = normalizeTrack(track);

  if (["caulfield", "flemington", "moonee valley", "sandown", "the valley"].some((name) => normalized.includes(name))) {
    return "melbourne";
  }

  if (["rosehill", "randwick", "warwick", "canterbury", "kensington"].some((name) => normalized.includes(name))) {
    return "sydney";
  }

  if (["ascot", "belmont", "pinjarra", "bunbury"].some((name) => normalized.includes(name))) {
    return "wa";
  }

  if (["morphettville", "murray bridge", "balaklava"].some((name) => normalized.includes(name))) {
    return "sa";
  }

  if (["eagle farm", "doomben", "sunshine coast", "gold coast"].some((name) => normalized.includes(name))) {
    return "qld";
  }

  return "other";
}

function getConfidenceBadgeClass(confidence: string) {
  switch (confidence) {
    case "high":
      return "bg-green-500/15 text-green-400 border-green-500/25";
    case "medium":
      return "bg-amber-500/15 text-amber-400 border-amber-500/25";
    case "low":
      return "bg-red-500/15 text-red-400 border-red-500/25";
    default:
      return "bg-white/5 text-white/55 border-white/10";
  }
}

function summarizeResults(results: ForwardTestRaceResult[]): ResultSummary {
  const selections = results.length;
  const winners = results.filter((entry) => entry.outcome.won).length;
  const placers = results.filter((entry) => entry.outcome.placed).length;
  const avgPredictedWinPct = selections
    ? Number((results.reduce((sum, entry) => sum + entry.selection.predictedWinPct, 0) / selections).toFixed(1))
    : 0;
  const totalUnitsStaked = Number(results.reduce((sum, entry) => sum + entry.selection.stakeUnits, 0).toFixed(2));
  const totalProfitUnits = Number(results.reduce((sum, entry) => sum + entry.outcome.profitUnits, 0).toFixed(2));
  const tracks = Array.from(new Set(results.map((entry) => entry.track))).sort();

  return {
    selections,
    winners,
    placers,
    strikeRate: selections ? Number(((winners / selections) * 100).toFixed(1)) : 0,
    avgPredictedWinPct,
    totalUnitsStaked,
    totalProfitUnits,
    roiPct: totalUnitsStaked ? Number(((totalProfitUnits / totalUnitsStaked) * 100).toFixed(1)) : 0,
    trackCount: tracks.length,
    tracks,
  };
}

function buildTrackStats(results: ForwardTestRaceResult[]): TrackStat[] {
  const grouped = new Map<string, ForwardTestRaceResult[]>();

  results.forEach((entry) => {
    const current = grouped.get(entry.track) || [];
    current.push(entry);
    grouped.set(entry.track, current);
  });

  return Array.from(grouped.entries())
    .map(([track, entries]) => ({
      track,
      ...summarizeResults(entries),
    }))
    .sort((left, right) => right.totalProfitUnits - left.totalProfitUnits || left.track.localeCompare(right.track));
}

function compareByOffTime(left: ForwardTestRaceResult, right: ForwardTestRaceResult) {
  const leftTime = left.offTime || "";
  const rightTime = right.offTime || "";
  if (leftTime !== rightTime) {
    return leftTime.localeCompare(rightTime);
  }
  if (left.track !== right.track) {
    return left.track.localeCompare(right.track);
  }
  return left.raceNumber - right.raceNumber;
}

function buildRaceLabel(entry: ForwardTestRaceResult) {
  const shortTrack = entry.track
    .replace(" Gardens", "")
    .split(" ")
    .map((part) => part[0])
    .join("")
    .slice(0, 3)
    .toUpperCase();

  return `${shortTrack} R${entry.raceNumber}`;
}

function CumulativeTooltip({ active, payload }: { active?: boolean; payload?: Array<{ payload: any }> }) {
  if (!active || !payload?.length) return null;

  const point = payload[0].payload;

  return (
    <div className="rounded-2xl border border-white/10 bg-black/90 px-4 py-3 shadow-2xl">
      <p className="text-xs uppercase tracking-[0.18em] text-white/35 mb-2">
        {point.fullLabel}
      </p>
      <div className="space-y-1 text-sm">
        <div className="flex items-center justify-between gap-5">
          <span className="text-white/60">ROI</span>
          <span className={`font-semibold ${point.cumulativeRoi >= 0 ? "text-green-400" : "text-red-400"}`}>
            {point.cumulativeRoi >= 0 ? "+" : ""}{point.cumulativeRoi.toFixed(1)}%
          </span>
        </div>
        <div className="flex items-center justify-between gap-5">
          <span className="text-white/60">Win Rate</span>
          <span className="font-semibold text-racing-gold">{point.cumulativeWinRate.toFixed(1)}%</span>
        </div>
        <div className="flex items-center justify-between gap-5">
          <span className="text-white/60">P/L</span>
          <span className={`font-semibold ${point.cumulativeProfit >= 0 ? "text-green-400" : "text-red-400"}`}>
            {formatUnits(point.cumulativeProfit)}
          </span>
        </div>
      </div>
    </div>
  );
}

export default function CalibrationDashboard() {
  const [selectedRegion, setSelectedRegion] = useState<RegionKey>("all");
  const [selectedOutcome, setSelectedOutcome] = useState<OutcomeKey>("all");

  const apiPath = `/api/calibration?date=${REVIEW_DATE}`;
  const { data, isLoading, error } = useQuery<ForwardTestSnapshotResponse>({
    queryKey: [apiPath],
  });

  const allResults = data?.results || [];
  const filteredByRegion = selectedRegion === "all"
    ? allResults
    : allResults.filter((entry) => getTrackRegion(entry.track) === selectedRegion);

  const summary = summarizeResults(filteredByRegion);
  const trackStats = buildTrackStats(filteredByRegion);
  const topTrack = trackStats[0] || null;

  const regionBuckets: RegionBucket[] = [
    {
      key: "all",
      label: REGION_LABELS.all,
      ...summarizeResults(allResults),
    },
    ...REGION_ORDER.map((region) => ({
      key: region,
      label: REGION_LABELS[region],
      ...summarizeResults(allResults.filter((entry) => getTrackRegion(entry.track) === region)),
    })),
  ];

  const orderedResults = [...filteredByRegion].sort(compareByOffTime);

  let cumulativeStake = 0;
  let cumulativeProfit = 0;
  let cumulativeWins = 0;
  const chartData = orderedResults.map((entry, index) => {
    cumulativeStake += entry.selection.stakeUnits;
    cumulativeProfit += entry.outcome.profitUnits;
    cumulativeWins += entry.outcome.won ? 1 : 0;

    return {
      raceLabel: buildRaceLabel(entry),
      fullLabel: `${entry.track} R${entry.raceNumber}`,
      cumulativeRoi: cumulativeStake > 0 ? (cumulativeProfit / cumulativeStake) * 100 : 0,
      cumulativeWinRate: ((cumulativeWins / (index + 1)) * 100),
      cumulativeProfit,
    };
  });

  const outcomeCounts = {
    all: filteredByRegion.length,
    winners: filteredByRegion.filter((entry) => entry.outcome.won).length,
    placers: filteredByRegion.filter((entry) => entry.outcome.placed && !entry.outcome.won).length,
    non_winners: filteredByRegion.filter((entry) => !entry.outcome.won).length,
  };

  const replayResults = orderedResults.filter((entry) => {
    switch (selectedOutcome) {
      case "winners":
        return entry.outcome.won;
      case "placers":
        return entry.outcome.placed && !entry.outcome.won;
      case "non_winners":
        return !entry.outcome.won;
      default:
        return true;
    }
  });

  return (
    <div className="min-h-screen bg-black font-dm text-white">
      <header className="page-header-liquid">
        <div className="container mx-auto px-4 py-4">
          <div className="flex items-center justify-between gap-3 flex-wrap">
            <div className="flex items-center gap-4">
              <Link href="/bets">
                <Button variant="ghost" size="sm" data-testid="button-back">
                  <ArrowLeft className="h-4 w-4 mr-2" />
                  Back
                </Button>
              </Link>
              <div>
                <h1 className="text-xl font-syne font-extrabold tracking-tight text-white flex items-center gap-2">
                  <Target className="h-5 w-5 text-racing-orange" />
                  Forward Test Review
                </h1>
                <p className="text-sm text-white/40">{formatReviewDate(REVIEW_DATE)}</p>
              </div>
            </div>

            <Badge className="bg-racing-orange/15 text-racing-orange border-racing-orange/30">
              March 21 Snapshot
            </Badge>
          </div>
        </div>
      </header>

      <main className="container mx-auto px-4 py-6 space-y-6">
        {isLoading ? (
          <div className="space-y-4">
            <Card className="glass-card border-0">
              <CardContent className="p-6 space-y-4">
                <Skeleton className="h-6 w-56" />
                <Skeleton className="h-16 w-full" />
              </CardContent>
            </Card>
            <div className="grid grid-cols-2 xl:grid-cols-6 gap-4">
              {Array.from({ length: 6 }).map((_, index) => (
                <Card key={index} className="glass-card border-0">
                  <CardContent className="p-4">
                    <Skeleton className="h-4 w-20 mb-2" />
                    <Skeleton className="h-8 w-16" />
                  </CardContent>
                </Card>
              ))}
            </div>
          </div>
        ) : error ? (
          <Card className="glass-card border-0">
            <CardContent className="p-8 text-center">
              <AlertTriangle className="h-12 w-12 mx-auto mb-4 text-destructive" />
              <p className="text-white/40">Failed to load the forward-test snapshot.</p>
            </CardContent>
          </Card>
        ) : !data ? (
          <Card className="glass-card border-0">
            <CardContent className="p-8 text-center">
              <Activity className="h-12 w-12 mx-auto mb-4 text-white/20" />
              <h3 className="text-lg font-semibold mb-2">No Forward-Test Snapshot Found</h3>
              <p className="text-white/40">
                The calibration page is now driven by the fixed Saturday 21 March review.
              </p>
            </CardContent>
          </Card>
        ) : (
          <>
            <Card className="glass-card border-0 overflow-hidden">
              <CardContent className="p-0">
                <div className="p-6 bg-[radial-gradient(circle_at_top_left,rgba(249,115,22,0.20),transparent_55%),linear-gradient(145deg,rgba(255,255,255,0.05),rgba(255,255,255,0.01))]">
                  <div className="flex items-center gap-2 text-[11px] uppercase tracking-[0.22em] text-racing-orange/80 mb-3">
                    <Calendar className="h-3.5 w-3.5" />
                    Fixed Review Card
                  </div>
                  <h2 className="text-2xl sm:text-[2rem] sm:leading-[1.05] font-syne font-bold text-white mb-3">
                    Saturday 21 March 2026
                  </h2>
                  <p className="max-w-4xl text-sm sm:text-base leading-7 text-white/70">
                    {summary.winners} winners from {summary.selections} reviewed selections in the {REGION_LABELS[selectedRegion]} view.
                    The model returned {formatUnits(summary.totalProfitUnits)} from {summary.totalUnitsStaked.toFixed(0)} units staked, with a {summary.strikeRate.toFixed(1)}% strike rate.
                    {topTrack ? ` ${topTrack.track} is currently the strongest track slice at ${topTrack.strikeRate.toFixed(1)}% strike.` : ""}
                  </p>
                </div>
              </CardContent>
            </Card>

            <div className="grid grid-cols-2 xl:grid-cols-6 gap-4">
              <Card className="glass-card border-0">
                <CardContent className="p-4">
                  <div className="flex items-center gap-2 mb-1">
                    <BarChart3 className="h-3.5 w-3.5 text-racing-orange" />
                    <p className="text-xs text-white/40 uppercase tracking-wider">Tracks</p>
                  </div>
                  <p className="text-2xl font-bold text-white">{summary.trackCount}</p>
                  <p className="text-xs text-white/30 mt-1">Active in this region view</p>
                </CardContent>
              </Card>

              <Card className="glass-card border-0">
                <CardContent className="p-4">
                  <div className="flex items-center gap-2 mb-1">
                    <Target className="h-3.5 w-3.5 text-racing-orange" />
                    <p className="text-xs text-white/40 uppercase tracking-wider">Selections</p>
                  </div>
                  <p className="text-2xl font-bold text-white">{summary.selections}</p>
                  <p className="text-xs text-white/30 mt-1">Headline picks on the card</p>
                </CardContent>
              </Card>

              <Card className="glass-card border-0">
                <CardContent className="p-4">
                  <div className="flex items-center gap-2 mb-1">
                    <Trophy className="h-3.5 w-3.5 text-racing-gold" />
                    <p className="text-xs text-white/40 uppercase tracking-wider">Winners</p>
                  </div>
                  <p className="text-2xl font-bold text-white">{summary.winners}</p>
                  <p className="text-xs text-white/30 mt-1">{summary.placers} placers total</p>
                </CardContent>
              </Card>

              <Card className="glass-card border-0">
                <CardContent className="p-4">
                  <div className="flex items-center gap-2 mb-1">
                    <Percent className="h-3.5 w-3.5 text-racing-orange" />
                    <p className="text-xs text-white/40 uppercase tracking-wider">Strike Rate</p>
                  </div>
                  <p className="text-2xl font-bold text-white">{summary.strikeRate.toFixed(1)}%</p>
                  <p className="text-xs text-white/30 mt-1">Winner conversion on this slice</p>
                </CardContent>
              </Card>

              <Card className="glass-card border-0">
                <CardContent className="p-4">
                  <div className="flex items-center gap-2 mb-1">
                    <TrendingUp className="h-3.5 w-3.5 text-emerald-400" />
                    <p className="text-xs text-white/40 uppercase tracking-wider">Model Avg</p>
                  </div>
                  <p className="text-2xl font-bold text-white">{summary.avgPredictedWinPct.toFixed(1)}%</p>
                  <p className="text-xs text-white/30 mt-1">Average assessed win chance</p>
                </CardContent>
              </Card>

              <Card className="glass-card border-0">
                <CardContent className="p-4">
                  <div className="flex items-center gap-2 mb-1">
                    <Zap className="h-3.5 w-3.5 text-racing-gold" />
                    <p className="text-xs text-white/40 uppercase tracking-wider">P/L</p>
                  </div>
                  <p className={`text-2xl font-bold ${summary.totalProfitUnits >= 0 ? "text-green-400" : "text-red-400"}`}>
                    {formatUnits(summary.totalProfitUnits)}
                  </p>
                  <p className="text-xs text-white/30 mt-1">ROI {summary.roiPct >= 0 ? "+" : ""}{summary.roiPct.toFixed(1)}%</p>
                </CardContent>
              </Card>
            </div>

            <div className="grid grid-cols-1 xl:grid-cols-[1fr_1fr] gap-6">
              <Card className="glass-card border-0">
                <CardContent className="p-5">
                  <div className="flex items-center gap-2 text-[11px] uppercase tracking-[0.18em] text-racing-orange/80 mb-4">
                    <Target className="h-3.5 w-3.5" />
                    Track Categories
                  </div>
                  <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-3">
                    {regionBuckets.map((bucket) => {
                      const selected = selectedRegion === bucket.key;
                      return (
                        <button
                          key={bucket.key}
                          type="button"
                          onClick={() => setSelectedRegion(bucket.key)}
                          className={`rounded-2xl border p-4 text-left transition-all ${
                            selected
                              ? "border-racing-orange/40 bg-[radial-gradient(circle_at_top_left,rgba(249,115,22,0.18),transparent_60%),linear-gradient(180deg,rgba(255,255,255,0.04),rgba(255,255,255,0.02))] shadow-[0_0_40px_rgba(249,115,22,0.12)]"
                              : "border-white/[0.08] bg-[linear-gradient(180deg,rgba(255,255,255,0.04),rgba(255,255,255,0.02))] hover:border-white/15"
                          }`}
                        >
                          <div className="flex items-center justify-between gap-3 mb-3">
                            <p className="text-base font-semibold text-white">{bucket.label}</p>
                            <Badge className="border-white/10 bg-white/[0.03] text-white/65">
                              {bucket.trackCount} track{bucket.trackCount === 1 ? "" : "s"}
                            </Badge>
                          </div>
                          <div className="grid grid-cols-2 gap-3 text-sm">
                            <div>
                              <p className="text-[11px] uppercase tracking-[0.18em] text-white/35">Selections</p>
                              <p className="mt-1 font-semibold text-white">{bucket.selections}</p>
                            </div>
                            <div>
                              <p className="text-[11px] uppercase tracking-[0.18em] text-white/35">Winners</p>
                              <p className="mt-1 font-semibold text-white">{bucket.winners}</p>
                            </div>
                            <div>
                              <p className="text-[11px] uppercase tracking-[0.18em] text-white/35">Strike</p>
                              <p className="mt-1 font-semibold text-white">{bucket.strikeRate.toFixed(1)}%</p>
                            </div>
                            <div>
                              <p className="text-[11px] uppercase tracking-[0.18em] text-white/35">ROI</p>
                              <p className={`mt-1 font-semibold ${bucket.totalProfitUnits >= 0 ? "text-green-400" : "text-red-400"}`}>
                                {bucket.roiPct >= 0 ? "+" : ""}{bucket.roiPct.toFixed(1)}%
                              </p>
                            </div>
                          </div>
                          <p className="mt-3 text-xs leading-6 text-white/45">
                            {bucket.tracks.length > 0 ? bucket.tracks.join(", ") : "No active tracks on this card."}
                          </p>
                        </button>
                      );
                    })}
                  </div>
                </CardContent>
              </Card>

              <Card className="glass-card border-0">
                <CardContent className="p-5">
                  <div className="flex items-center gap-2 text-[11px] uppercase tracking-[0.18em] text-racing-gold/80 mb-4">
                    <BarChart3 className="h-3.5 w-3.5" />
                    Track Scorecards
                  </div>
                  {trackStats.length === 0 ? (
                    <div className="rounded-2xl border border-white/[0.08] bg-white/[0.02] p-6 text-sm text-white/45">
                      No tracks available in the {REGION_LABELS[selectedRegion]} filter for March 21.
                    </div>
                  ) : (
                    <div className="space-y-3">
                      {trackStats.map((track) => (
                        <div
                          key={track.track}
                          className="rounded-2xl border border-white/[0.08] bg-[linear-gradient(180deg,rgba(255,255,255,0.04),rgba(255,255,255,0.02))] p-4"
                        >
                          <div className="flex items-start justify-between gap-3 mb-4">
                            <div>
                              <p className="text-lg font-semibold text-white">{track.track}</p>
                              <p className="text-sm text-white/40">{track.selections} races reviewed</p>
                            </div>
                            <Badge className={`${track.totalProfitUnits >= 0 ? "bg-green-500/15 text-green-400 border-green-500/25" : "bg-red-500/15 text-red-400 border-red-500/25"}`}>
                              {formatUnits(track.totalProfitUnits)}
                            </Badge>
                          </div>
                          <div className="grid grid-cols-4 gap-3 text-sm">
                            <div>
                              <p className="text-[11px] uppercase tracking-[0.18em] text-white/35">Winners</p>
                              <p className="mt-1 font-semibold text-white">{track.winners}</p>
                            </div>
                            <div>
                              <p className="text-[11px] uppercase tracking-[0.18em] text-white/35">Strike</p>
                              <p className="mt-1 font-semibold text-white">{track.strikeRate.toFixed(1)}%</p>
                            </div>
                            <div>
                              <p className="text-[11px] uppercase tracking-[0.18em] text-white/35">Model Avg</p>
                              <p className="mt-1 font-semibold text-white">{track.avgPredictedWinPct.toFixed(1)}%</p>
                            </div>
                            <div>
                              <p className="text-[11px] uppercase tracking-[0.18em] text-white/35">ROI</p>
                              <p className={`mt-1 font-semibold ${track.totalProfitUnits >= 0 ? "text-green-400" : "text-red-400"}`}>
                                {track.roiPct >= 0 ? "+" : ""}{track.roiPct.toFixed(1)}%
                              </p>
                            </div>
                          </div>
                        </div>
                      ))}
                    </div>
                  )}
                </CardContent>
              </Card>
            </div>

            <div className="grid grid-cols-1 xl:grid-cols-[1fr_1fr] gap-6">
              <Card className="glass-card border-0">
                <CardContent className="p-5">
                  <div className="flex items-center gap-2 text-[11px] uppercase tracking-[0.18em] text-racing-orange/80 mb-4">
                    <TrendingUp className="h-3.5 w-3.5" />
                    Cumulative ROI Through The Card
                  </div>
                  {chartData.length === 0 ? (
                    <div className="rounded-2xl border border-white/[0.08] bg-white/[0.02] p-6 text-sm text-white/45">
                      No races available for the selected region.
                    </div>
                  ) : (
                    <div className="h-[280px]">
                      <ResponsiveContainer width="100%" height="100%">
                        <AreaChart data={chartData}>
                          <defs>
                            <linearGradient id="roiFillSingleDay" x1="0" y1="0" x2="0" y2="1">
                              <stop offset="0%" stopColor="#f97316" stopOpacity={0.45} />
                              <stop offset="100%" stopColor="#f97316" stopOpacity={0} />
                            </linearGradient>
                          </defs>
                          <CartesianGrid stroke="rgba(255,255,255,0.08)" vertical={false} />
                          <XAxis
                            dataKey="raceLabel"
                            tick={{ fill: "rgba(255,255,255,0.45)", fontSize: 12 }}
                            tickLine={false}
                            axisLine={false}
                          />
                          <YAxis
                            tick={{ fill: "rgba(255,255,255,0.45)", fontSize: 12 }}
                            tickLine={false}
                            axisLine={false}
                            width={50}
                            tickFormatter={(value) => `${value}%`}
                          />
                          <RechartsTooltip content={<CumulativeTooltip />} />
                          <Area type="monotone" dataKey="cumulativeRoi" stroke="#f97316" fill="url(#roiFillSingleDay)" strokeWidth={3} />
                        </AreaChart>
                      </ResponsiveContainer>
                    </div>
                  )}
                </CardContent>
              </Card>

              <Card className="glass-card border-0">
                <CardContent className="p-5">
                  <div className="flex items-center gap-2 text-[11px] uppercase tracking-[0.18em] text-racing-gold/80 mb-4">
                    <Percent className="h-3.5 w-3.5" />
                    Cumulative Win Rate Through The Card
                  </div>
                  {chartData.length === 0 ? (
                    <div className="rounded-2xl border border-white/[0.08] bg-white/[0.02] p-6 text-sm text-white/45">
                      No races available for the selected region.
                    </div>
                  ) : (
                    <div className="h-[280px]">
                      <ResponsiveContainer width="100%" height="100%">
                        <LineChart data={chartData}>
                          <CartesianGrid stroke="rgba(255,255,255,0.08)" vertical={false} />
                          <XAxis
                            dataKey="raceLabel"
                            tick={{ fill: "rgba(255,255,255,0.45)", fontSize: 12 }}
                            tickLine={false}
                            axisLine={false}
                          />
                          <YAxis
                            tick={{ fill: "rgba(255,255,255,0.45)", fontSize: 12 }}
                            tickLine={false}
                            axisLine={false}
                            width={50}
                            tickFormatter={(value) => `${value}%`}
                          />
                          <RechartsTooltip content={<CumulativeTooltip />} />
                          <Line type="monotone" dataKey="cumulativeWinRate" stroke="#facc15" strokeWidth={3} dot={{ r: 4, strokeWidth: 0, fill: "#facc15" }} activeDot={{ r: 6 }} />
                        </LineChart>
                      </ResponsiveContainer>
                    </div>
                  )}
                </CardContent>
              </Card>
            </div>

            <Card className="glass-card border-0">
              <CardContent className="p-5">
                <div className="flex flex-col gap-3 mb-5">
                  <div className="flex items-center gap-2 text-[11px] uppercase tracking-[0.18em] text-racing-orange/80">
                    <BarChart3 className="h-3.5 w-3.5" />
                    Raceday Replay
                  </div>
                  <p className="text-sm text-white/50">
                    Showing the {REGION_LABELS[selectedRegion]} slice of the March 21 card. Winning plays stay green; every non-winner stays red.
                  </p>
                  <div className="flex flex-wrap gap-2">
                    {([
                      { key: "all", label: "All Picks", count: outcomeCounts.all },
                      { key: "winners", label: "Winners", count: outcomeCounts.winners },
                      { key: "placers", label: "Placers", count: outcomeCounts.placers },
                      { key: "non_winners", label: "Non-Winners", count: outcomeCounts.non_winners },
                    ] as Array<{ key: OutcomeKey; label: string; count: number }>).map((filter) => (
                      <button
                        key={filter.key}
                        type="button"
                        onClick={() => setSelectedOutcome(filter.key)}
                        className={`rounded-full border px-4 py-2 text-xs font-semibold tracking-[0.16em] transition-all ${
                          selectedOutcome === filter.key
                            ? "border-racing-orange/50 bg-racing-orange text-black"
                            : "border-white/10 bg-white/[0.03] text-white/60 hover:border-white/20 hover:text-white/85"
                        }`}
                      >
                        {filter.label} {filter.count > 0 ? `(${filter.count})` : ""}
                      </button>
                    ))}
                  </div>
                </div>

                {replayResults.length === 0 ? (
                  <div className="rounded-2xl border border-white/[0.08] bg-white/[0.02] p-6 text-sm text-white/45">
                    No races match the current region and outcome filters.
                  </div>
                ) : (
                  <div className="grid grid-cols-1 xl:grid-cols-2 gap-4">
                    {replayResults.map((entry) => {
                      const cardTone = entry.outcome.won
                        ? "border-emerald-500/25 bg-[radial-gradient(circle_at_top_left,rgba(34,197,94,0.18),transparent_55%),linear-gradient(180deg,rgba(255,255,255,0.03),rgba(255,255,255,0.01))]"
                        : "border-red-500/20 bg-[radial-gradient(circle_at_top_left,rgba(239,68,68,0.18),transparent_55%),linear-gradient(180deg,rgba(255,255,255,0.03),rgba(255,255,255,0.01))]";

                      return (
                        <div
                          key={`${entry.track}-${entry.raceNumber}`}
                          className={`rounded-[28px] border p-5 ${cardTone}`}
                        >
                          <div className="flex items-start justify-between gap-4">
                            <div>
                              <p className="text-[11px] uppercase tracking-[0.18em] text-white/40">
                                {entry.track} R{entry.raceNumber} • {entry.distance} • {formatJumpTime(entry.offTime)}
                              </p>
                              <h3 className="mt-2 text-2xl font-syne font-bold text-white">{entry.selection.horse}</h3>
                              <p className="mt-1 text-sm text-white/45">{entry.raceName}</p>
                            </div>
                            <div className="flex flex-col items-end gap-2">
                              <Badge className={getConfidenceBadgeClass(entry.selection.confidence)}>
                                {entry.selection.confidence}
                              </Badge>
                              <Badge className={entry.outcome.won ? "bg-green-500/15 text-green-400 border-green-500/25" : "bg-red-500/15 text-red-400 border-red-500/25"}>
                                {entry.outcome.won ? "Won" : "Did Not Win"}
                              </Badge>
                            </div>
                          </div>

                          <div className="mt-5 grid grid-cols-2 md:grid-cols-4 gap-3">
                            <div className="rounded-2xl border border-white/[0.08] bg-black/20 p-3">
                              <p className="text-[11px] uppercase tracking-[0.18em] text-white/35">Pick</p>
                              <p className="mt-1 font-semibold text-white">{entry.selection.horse}</p>
                            </div>
                            <div className="rounded-2xl border border-white/[0.08] bg-black/20 p-3">
                              <p className="text-[11px] uppercase tracking-[0.18em] text-white/35">Finished</p>
                              <p className="mt-1 font-semibold text-white">{ordinalPosition(entry.outcome.position)}</p>
                            </div>
                            <div className="rounded-2xl border border-white/[0.08] bg-black/20 p-3">
                              <p className="text-[11px] uppercase tracking-[0.18em] text-white/35">Winner</p>
                              <p className="mt-1 font-semibold text-white">{entry.outcome.winnerHorse || "N/A"}</p>
                            </div>
                            <div className="rounded-2xl border border-white/[0.08] bg-black/20 p-3">
                              <p className="text-[11px] uppercase tracking-[0.18em] text-white/35">P/L</p>
                              <p className={`mt-1 font-semibold ${entry.outcome.profitUnits >= 0 ? "text-green-400" : "text-red-400"}`}>
                                {formatUnits(entry.outcome.profitUnits)}
                              </p>
                            </div>
                          </div>

                          <div className="mt-4 flex flex-wrap gap-2 text-xs">
                            <Badge className="border-white/10 bg-white/[0.03] text-white/65">
                              Market {formatPrice(entry.selection.marketOdds)}
                            </Badge>
                            <Badge className="border-white/10 bg-white/[0.03] text-white/65">
                              SP {formatPrice(entry.outcome.startingPrice)}
                            </Badge>
                            <Badge className="border-white/10 bg-white/[0.03] text-white/65">
                              Winner SP {formatPrice(entry.outcome.winnerPrice)}
                            </Badge>
                            <Badge className="border-white/10 bg-white/[0.03] text-white/65">
                              {entry.selection.predictedWinPct.toFixed(1)}% model
                            </Badge>
                          </div>

                          <div className="mt-5 rounded-2xl border border-white/[0.08] bg-black/25 p-4">
                            <p className="text-[11px] uppercase tracking-[0.18em] text-racing-gold/80 mb-2">
                              Stewards Snapshot
                            </p>
                            <p className="text-sm leading-7 text-white/72">
                              {entry.raceNotes.stewardsSummary}
                            </p>
                          </div>
                        </div>
                      );
                    })}
                  </div>
                )}
              </CardContent>
            </Card>
          </>
        )}
      </main>
    </div>
  );
}
