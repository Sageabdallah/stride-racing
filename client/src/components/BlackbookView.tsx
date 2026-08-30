import { useEffect, useMemo, useState } from "react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Skeleton } from "@/components/ui/skeleton";
import { AlertTriangle, BellRing, BookMarked, Calendar, ChevronRight, Clock3, Flame, LayoutGrid, MapPin, Search, Sparkles, Target, TrendingUp } from "lucide-react";

interface BlackbookEntryCard {
  id: string;
  horseName: string;
  primaryReason: string;
  reasonLabel: string;
  secondaryEvidenceTags: string[];
  region: string;
  readinessBand: string;
  readinessScore: number;
  status: string;
  intakeConfidence: number;
  metric: {
    last600Delta: number | null;
    last400Delta: number | null;
    last200Delta: number | null;
    display: string;
  };
  incidentSummary: string;
  frankingScore: number | null;
  sourceRun: {
    track: string;
    raceDate: string;
    raceNumber: number;
    raceName: string | null;
    position: number | null;
    marginLengths: number | null;
    distanceM: number | null;
    going: string | null;
    barrier: number | null;
    raceClass: string | null;
  };
  nextRun: {
    track: string;
    raceDate: string;
    raceNumber: number;
    raceName: string | null;
    offTime: string | null;
    barrier: number | null;
    distanceM: number | null;
    going: string | null;
    tempoSummary: string;
    leaderCount: number;
    marketPrice: number | null;
    truePrice: number | null;
    valueEdgePct: number | null;
    modelWinProb: number | null;
    verdict: string;
  } | null;
  alertCount: number;
}

interface BlackbookResponse {
  generatedAt: string;
  summary: {
    totalEntries: number;
    activeAlerts: number;
    readinessCounts: Record<string, number>;
    actionableCount: number;
    valueDetectedCount: number;
  };
  filters: {
    tracks: string[];
    regions: string[];
    reasons: string[];
    readinessBands: string[];
    statuses: string[];
    upcomingDates: string[];
  };
  nextAction: BlackbookEntryCard | null;
  entries: BlackbookEntryCard[];
}

interface HeatmapResponse {
  selectedDate: string | null;
  availableDates: string[];
  meetings: Array<{
    track: string;
    region: string;
    races: Array<{
      raceNumber: number;
      raceName: string | null;
      offTime: string | null;
      readinessScore: number;
      readinessBand: string;
      horseName: string;
      verdict: string;
      valueDetected: boolean;
    }>;
  }>;
}

interface BlackbookDetailResponse {
  entry: BlackbookEntryCard;
  detail: {
    entryAnalysis: {
      summary?: string;
      mechanism?: string;
      validation?: string;
      conditionsNeeded?: string;
    } | null;
    idealConditions: {
      summary?: string;
      barrier?: { max: number; softMax: number };
      distance?: { target: number | null; tolerance: number; softTolerance: number };
      tempoNeed?: string;
      trackCondition?: string;
      daysBetweenRuns?: { min: number; max: number };
      classRule?: string;
    } | null;
    evidence: {
      biasLabel?: string;
      biasConfidence?: string;
      sectionalSource?: string;
      finishingBurst?: number;
      rsi?: number;
    } | null;
    sourceComment: string | null;
    sourceIncidents: Array<{ tag?: string }> | null;
    conditionAlignment: {
      summary?: string;
      delta?: string;
      verdict?: string;
    } | null;
    raceDayBrief: {
      verdict?: string;
      winCondition?: string;
      primaryRisk?: string;
    } | null;
    readinessBreakdown: Record<string, number> | null;
    alerts: Array<{
      id: string;
      type: string;
      severity: string;
      title: string;
      message: string;
      updatedAt: string;
    }>;
  };
}

function formatBandColor(band: string) {
  switch (band) {
    case "REVENGE RACE":
      return "bg-emerald-500/15 text-emerald-300 border-emerald-500/40";
    case "PRIMED":
      return "bg-green-500/15 text-green-300 border-green-500/40";
    case "WATCHING":
      return "bg-amber-500/15 text-amber-300 border-amber-500/40";
    case "MONITORING":
      return "bg-orange-500/15 text-orange-300 border-orange-500/40";
    default:
      return "bg-white/10 text-white/60 border-white/10";
  }
}

function formatReasonTone(reason: string) {
  if (reason.includes("HELD") || reason.includes("CHECKED")) return "text-racing-gold";
  if (reason.includes("CLOSING")) return "text-emerald-300";
  if (reason.includes("BIAS")) return "text-sky-300";
  return "text-white/80";
}

function formatValueEdge(value: number | null) {
  if (typeof value !== "number") return "No live market edge yet";
  return value >= 0 ? `+${value.toFixed(1)}% vs market` : `${value.toFixed(1)}% vs market`;
}

function formatOffTime(value: string | null) {
  if (!value) return "TBA";
  return new Date(value).toLocaleTimeString("en-AU", {
    hour: "numeric",
    minute: "2-digit",
  });
}

function formatRaceDate(value: string) {
  return new Date(`${value}T00:00:00`).toLocaleDateString("en-AU", {
    weekday: "short",
    day: "numeric",
    month: "short",
  });
}

function formatReadinessSummary(entry: BlackbookEntryCard) {
  switch (entry.readinessBand) {
    case "REVENGE RACE":
      return "The setup is aligned now. This has moved from memory to execution.";
    case "PRIMED":
      return "Most of the trigger conditions are in place. One more push and this becomes live.";
    case "WATCHING":
      return "The latest run still rates as false form, but the next setup is only partially lining up.";
    case "MONITORING":
      return "The latest run still qualifies, but there is not enough next-start support to escalate it yet.";
    default:
      return "The horse stays on the sheet until a later run invalidates the excuse or a cleaner setup appears.";
  }
}

function buildWhyItStillMatters(entry: BlackbookEntryCard, sourceComment?: string | null) {
  const stewardLine = sourceComment?.trim();
  if (entry.nextRun) {
    if (entry.readinessBand === "REVENGE RACE" || entry.readinessBand === "PRIMED") {
      return `${entry.incidentSummary} The next start now looks cleaner with barrier ${entry.nextRun.barrier ?? "TBA"}, ${entry.nextRun.tempoSummary.toLowerCase()} tempo, and ${formatValueEdge(entry.nextRun.valueEdgePct).toLowerCase()}.`;
    }
    return `${entry.incidentSummary} The excuse still stands, but the next start is not fully solved yet: barrier ${entry.nextRun.barrier ?? "TBA"}, ${entry.nextRun.tempoSummary.toLowerCase()} tempo, and ${formatValueEdge(entry.nextRun.valueEdgePct).toLowerCase()}.`;
  }

  if (stewardLine) {
    return `${entry.incidentSummary} There is no later matched run yet, so the most recent stewards line still carries the case: ${stewardLine}`;
  }

  return `${entry.incidentSummary} There is no later matched run yet, so this latest start remains the current false-form evidence on file.`;
}

function buildNextActionCopy(entry: BlackbookEntryCard) {
  if (!entry.nextRun) {
    return "Wait for the next nomination. The profile stays live because no later start has invalidated the excuse.";
  }

  if (entry.readinessBand === "REVENGE RACE") {
    return `This is the moment to act: ${entry.nextRun.track} R${entry.nextRun.raceNumber}, ${formatOffTime(entry.nextRun.offTime)}, barrier ${entry.nextRun.barrier ?? "TBA"}, ${formatValueEdge(entry.nextRun.valueEdgePct).toLowerCase()}.`;
  }

  if (entry.readinessBand === "PRIMED") {
    return `Keep this near the top of the sheet: ${entry.nextRun.track} R${entry.nextRun.raceNumber} is close, but still needs the last condition to hold.`;
  }

  return `Stay with the setup rather than the horse alone: ${entry.nextRun.track} R${entry.nextRun.raceNumber} is the current monitoring run, not an automatic bet yet.`;
}

function bandFrame(band: string) {
  switch (band) {
    case "REVENGE RACE":
      return "border-emerald-500/35 bg-[linear-gradient(135deg,rgba(16,185,129,0.16),rgba(255,255,255,0.03))]";
    case "PRIMED":
      return "border-green-500/30 bg-[linear-gradient(135deg,rgba(34,197,94,0.12),rgba(255,255,255,0.03))]";
    case "WATCHING":
      return "border-amber-500/30 bg-[linear-gradient(135deg,rgba(245,158,11,0.12),rgba(255,255,255,0.03))]";
    case "MONITORING":
      return "border-orange-500/28 bg-[linear-gradient(135deg,rgba(249,115,22,0.12),rgba(255,255,255,0.03))]";
    default:
      return "border-white/10 bg-white/[0.03]";
  }
}

async function fetchJson<T>(url: string) {
  const response = await fetch(url, { credentials: "include" });
  if (!response.ok) {
    throw new Error(`${response.status}`);
  }
  return response.json() as Promise<T>;
}

export function BlackbookView() {
  const [searchInput, setSearchInput] = useState("");
  const [search, setSearch] = useState("");
  const [track, setTrack] = useState("all");
  const [region, setRegion] = useState("all");
  const [reason, setReason] = useState("all");
  const [readinessBand, setReadinessBand] = useState("all");
  const [status, setStatus] = useState("all");
  const [valueOnly, setValueOnly] = useState(false);
  const [selectedEntryId, setSelectedEntryId] = useState<string>("");
  const [selectedHeatmapDate, setSelectedHeatmapDate] = useState<string>("");
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [data, setData] = useState<BlackbookResponse | null>(null);
  const [heatmap, setHeatmap] = useState<HeatmapResponse | null>(null);
  const [detail, setDetail] = useState<BlackbookDetailResponse | null>(null);
  const [detailLoading, setDetailLoading] = useState(false);

  const buildListUrl = (force = false) => {
    const params = new URLSearchParams();
    if (search.trim()) params.set("search", search.trim());
    if (track !== "all") params.set("track", track);
    if (region !== "all") params.set("region", region);
    if (reason !== "all") params.set("reason", reason);
    if (readinessBand !== "all") params.set("readinessBand", readinessBand);
    if (status !== "all") params.set("status", status);
    if (valueOnly) params.set("valueOnly", "true");
    if (force) params.set("refresh", "true");
    return `/api/blackbook${params.toString() ? `?${params.toString()}` : ""}`;
  };

  const loadMain = async (force = false) => {
    const showFullLoading = !data;
    if (showFullLoading) {
      setLoading(true);
    } else {
      setRefreshing(true);
    }
    setError(null);
    try {
      const nextData = await fetchJson<BlackbookResponse>(buildListUrl(force));
      setData(nextData);
      if (!selectedHeatmapDate) {
        setSelectedHeatmapDate(nextData.filters.upcomingDates[0] || "");
      }
      const availableIds = new Set(nextData.entries.map((entry) => entry.id));
      const preferred = selectedEntryId && availableIds.has(selectedEntryId)
        ? selectedEntryId
        : nextData.nextAction?.id || nextData.entries[0]?.id || "";
      setSelectedEntryId(preferred);
    } catch (loadError: any) {
      setError(loadError.message || "Failed to load Blackbook");
    } finally {
      setLoading(false);
      setRefreshing(false);
    }
  };

  const loadHeatmap = async (force = false) => {
    const params = new URLSearchParams();
    if (selectedHeatmapDate) params.set("date", selectedHeatmapDate);
    if (force) params.set("refresh", "true");
    const url = `/api/blackbook/heatmap${params.toString() ? `?${params.toString()}` : ""}`;
    try {
      setHeatmap(await fetchJson<HeatmapResponse>(url));
    } catch (loadError) {
      console.error("Blackbook heatmap load error:", loadError);
      setHeatmap((current) => current ?? { selectedDate: null, availableDates: [], meetings: [] });
    }
  };

  const loadDetail = async (entryId: string, force = false) => {
    if (!entryId) {
      setDetail(null);
      return;
    }
    setDetailLoading(true);
    try {
      const suffix = force ? "?refresh=true" : "";
      setDetail(await fetchJson<BlackbookDetailResponse>(`/api/blackbook/${entryId}${suffix}`));
    } catch (loadError) {
      console.error("Blackbook detail load error:", loadError);
      setDetail(null);
    } finally {
      setDetailLoading(false);
    }
  };

  const loadEverything = async (force = false) => {
    await loadMain(force);
    void loadHeatmap(force);
  };

  useEffect(() => {
    const timeout = window.setTimeout(() => {
      setSearch(searchInput.trim());
    }, 350);

    return () => window.clearTimeout(timeout);
  }, [searchInput]);

  useEffect(() => {
    void loadEverything(false);
  }, [search, track, region, reason, readinessBand, status, valueOnly]);

  useEffect(() => {
    if (!selectedHeatmapDate) return;
    void loadHeatmap(false);
  }, [selectedHeatmapDate]);

  useEffect(() => {
    void loadDetail(selectedEntryId, false);
  }, [selectedEntryId]);

  useEffect(() => {
    const handler = () => {
      void loadEverything(true);
      if (selectedEntryId) {
        void loadDetail(selectedEntryId, true);
      }
    };

    window.addEventListener("stride:blackbook-refresh", handler);
    return () => window.removeEventListener("stride:blackbook-refresh", handler);
  }, [selectedEntryId, search, track, region, reason, readinessBand, status, valueOnly, selectedHeatmapDate]);

  const heroCards = useMemo(() => {
    return [
      {
        label: "Actionable",
        value: data?.summary.actionableCount || 0,
        tone: "bg-racing-gold/10 border-racing-gold/20",
        icon: Flame,
      },
      {
        label: "Value Detected",
        value: data?.summary.valueDetectedCount || 0,
        tone: "bg-green-500/10 border-green-500/20",
        icon: TrendingUp,
      },
      {
        label: "Active Alerts",
        value: data?.summary.activeAlerts || 0,
        tone: "bg-sky-500/10 border-sky-500/20",
        icon: BellRing,
      },
    ];
  }, [data]);

  const readinessStages = useMemo(
    () => [
      {
        label: "Monitoring",
        band: "MONITORING",
        count: data?.summary.readinessCounts["MONITORING"] || 0,
        text: "Valid latest-start excuse, but no live setup yet.",
      },
      {
        label: "Watching",
        band: "WATCHING",
        count: data?.summary.readinessCounts["WATCHING"] || 0,
        text: "Some triggers are arriving, but not enough to strike.",
      },
      {
        label: "Primed",
        band: "PRIMED",
        count: data?.summary.readinessCounts["PRIMED"] || 0,
        text: "Most conditions align. This is close to live.",
      },
      {
        label: "Revenge Race",
        band: "REVENGE RACE",
        count: data?.summary.readinessCounts["REVENGE RACE"] || 0,
        text: "The reason, setup, and price are finally lining up.",
      },
    ],
    [data],
  );

  return (
    <div className="space-y-5" data-testid="blackbook-view">
      {loading ? (
        <>
          <div className="grid grid-cols-2 xl:grid-cols-4 gap-3">
            {Array.from({ length: 4 }).map((_, index) => (
              <Card key={index} className="bg-white/[0.03] border-white/10">
                <CardContent className="p-4 space-y-3">
                  <Skeleton className="h-4 w-24" />
                  <Skeleton className="h-8 w-16" />
                </CardContent>
              </Card>
            ))}
          </div>
          <Card className="bg-white/[0.03] border-white/10">
            <CardContent className="p-6 space-y-4">
              <Skeleton className="h-10 w-full" />
              <Skeleton className="h-32 w-full" />
            </CardContent>
          </Card>
        </>
      ) : error ? (
        <Card className="bg-white/[0.03] border-white/10">
          <CardContent className="py-16 text-center">
            <AlertTriangle className="h-12 w-12 mx-auto mb-4 text-red-400/70" />
            <p className="text-white/70">Blackbook couldn’t be loaded right now.</p>
            <p className="text-sm text-white/40 mt-2">{error}</p>
          </CardContent>
        </Card>
      ) : (
        <>
          <Card className="bg-gradient-to-br from-racing-gold/12 via-white/[0.03] to-white/[0.02] border-racing-gold/20">
            <CardContent className="p-5 space-y-4">
              <div className="grid gap-5 xl:grid-cols-[minmax(0,1.45fr)_minmax(320px,0.95fr)]">
                <div className="space-y-4">
                  <div className="flex items-center gap-2 text-racing-gold">
                    <BookMarked className="h-4 w-4" />
                    <span className="text-xs uppercase tracking-[0.24em]">STRIDE Blackbook</span>
                  </div>
                  <div className="space-y-3">
                    <h2 className="text-2xl font-syne font-bold text-white">
                      Blackbook is where false form becomes a future bet.
                    </h2>
                    <p className="max-w-3xl text-white/65 leading-7">
                      Only the most recent completed run can keep a horse alive here. If a later start fails to validate the excuse, the profile dies.
                      That is why the page matters: it turns memory into discipline, then waits for the setup to actually deserve your money.
                    </p>
                  </div>
                  <div className="flex flex-wrap gap-3">
                    {heroCards.map(({ label, value, tone, icon: Icon }) => (
                      <div key={label} className={`inline-flex items-center gap-3 rounded-2xl border px-4 py-3 ${tone}`}>
                        <div className="h-9 w-9 rounded-xl bg-black/30 border border-white/10 flex items-center justify-center">
                          <Icon className="h-4 w-4 text-racing-gold" />
                        </div>
                        <div>
                          <p className="text-[11px] uppercase tracking-[0.2em] text-white/40">{label}</p>
                          <p className="text-xl font-syne font-bold text-white">{value}</p>
                        </div>
                      </div>
                    ))}
                  </div>
                </div>

                {data?.nextAction ? (
                  <div className={`rounded-3xl border p-5 ${bandFrame(data.nextAction.readinessBand)}`}>
                    <div className="flex items-start justify-between gap-3">
                      <div className="space-y-1">
                        <p className="text-xs uppercase tracking-[0.24em] text-white/40">Next Action</p>
                        <p className="text-2xl font-syne font-bold text-white">{data.nextAction.horseName}</p>
                        <p className="text-sm text-white/55">
                          {data.nextAction.nextRun
                            ? `${data.nextAction.nextRun.track} R${data.nextAction.nextRun.raceNumber} • ${formatOffTime(data.nextAction.nextRun.offTime)}`
                            : `${data.nextAction.sourceRun.track} R${data.nextAction.sourceRun.raceNumber} • latest false-form run`}
                        </p>
                      </div>
                      <Badge className={formatBandColor(data.nextAction.readinessBand)}>
                        {data.nextAction.readinessBand}
                      </Badge>
                    </div>
                    <div className="mt-4 space-y-3">
                      <p className="text-sm leading-7 text-white/72">
                        {buildNextActionCopy(data.nextAction)}
                      </p>
                      <div className="flex flex-wrap gap-2">
                        <Badge variant="outline" className="border-emerald-500/25 text-emerald-300">
                          {data.nextAction.metric.display}
                        </Badge>
                        <Badge variant="outline" className="border-white/10 text-white/60">
                          {data.nextAction.readinessScore}/10 readiness
                        </Badge>
                        <Badge variant="outline" className="border-white/10 text-white/60">
                          {data.nextAction.alertCount} alerts
                        </Badge>
                      </div>
                    </div>
                  </div>
                ) : null}
              </div>

              <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-4 gap-3">
                {readinessStages.map((stage, index) => (
                  <div
                    key={stage.band}
                    className={`rounded-2xl border px-4 py-4 ${bandFrame(stage.band)}`}
                  >
                    <div className="flex items-center justify-between gap-3">
                      <p className="text-sm font-semibold text-white">{stage.label}</p>
                      <div className="flex items-center gap-2 text-white/40">
                        <span className="text-lg font-syne font-bold text-white">{stage.count}</span>
                        {index < readinessStages.length - 1 ? <ChevronRight className="h-4 w-4" /> : null}
                      </div>
                    </div>
                    <p className="mt-2 text-sm leading-6 text-white/55">{stage.text}</p>
                  </div>
                ))}
              </div>

              <div className="grid grid-cols-1 lg:grid-cols-6 gap-3">
                <div className="lg:col-span-2 relative">
                  <Search className="h-4 w-4 text-white/35 absolute left-3 top-1/2 -translate-y-1/2" />
                  <Input
                    value={searchInput}
                    onChange={(event) => setSearchInput(event.target.value)}
                    placeholder="Search horse, track, reason"
                    className="pl-10 bg-black/40 border-white/10"
                  />
                </div>
                <Select value={region} onValueChange={setRegion}>
                  <SelectTrigger className="bg-black/40 border-white/10">
                    <SelectValue placeholder="Region" />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="all">All Regions</SelectItem>
                    {data?.filters.regions.map((value) => (
                      <SelectItem key={value} value={value}>{value}</SelectItem>
                    ))}
                  </SelectContent>
                </Select>
                <Select value={track} onValueChange={setTrack}>
                  <SelectTrigger className="bg-black/40 border-white/10">
                    <SelectValue placeholder="Track" />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="all">All Tracks</SelectItem>
                    {data?.filters.tracks.map((value) => (
                      <SelectItem key={value} value={value}>{value}</SelectItem>
                    ))}
                  </SelectContent>
                </Select>
                <Select value={reason} onValueChange={setReason}>
                  <SelectTrigger className="bg-black/40 border-white/10">
                    <SelectValue placeholder="Reason" />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="all">All Reasons</SelectItem>
                    {data?.filters.reasons.map((value) => (
                      <SelectItem key={value} value={value}>{value.replaceAll("_", " ")}</SelectItem>
                    ))}
                  </SelectContent>
                </Select>
                <Button
                  type="button"
                  variant={valueOnly ? "default" : "outline"}
                  onClick={() => setValueOnly((current) => !current)}
                  className="gap-2"
                >
                  <TrendingUp className="h-4 w-4" />
                  Value Only
                </Button>
              </div>

              <div className="grid grid-cols-1 md:grid-cols-3 gap-3">
                <Select value={readinessBand} onValueChange={setReadinessBand}>
                  <SelectTrigger className="bg-black/40 border-white/10">
                    <SelectValue placeholder="Readiness" />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="all">All Readiness</SelectItem>
                    {data?.filters.readinessBands.map((value) => (
                      <SelectItem key={value} value={value}>{value}</SelectItem>
                    ))}
                  </SelectContent>
                </Select>

                <Select value={status} onValueChange={setStatus}>
                  <SelectTrigger className="bg-black/40 border-white/10">
                    <SelectValue placeholder="Status" />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="all">All Statuses</SelectItem>
                    {data?.filters.statuses.map((value) => (
                      <SelectItem key={value} value={value}>{value}</SelectItem>
                    ))}
                  </SelectContent>
                </Select>

                <div className="rounded-xl border border-white/10 bg-black/40 px-4 py-3 text-sm text-white/55 flex items-center justify-between">
                  <span>{data?.entries.length || 0} live entries</span>
                  <span>{refreshing ? "Updating..." : new Date(data?.generatedAt || Date.now()).toLocaleTimeString("en-AU", { hour: "numeric", minute: "2-digit" })}</span>
                </div>
              </div>
            </CardContent>
          </Card>

          <Card className="bg-white/[0.03] border-white/10">
            <CardHeader className="pb-3">
              <div className="flex flex-col md:flex-row md:items-center md:justify-between gap-3">
                <div className="space-y-1">
                  <div className="flex items-center gap-2 text-racing-gold">
                    <LayoutGrid className="h-4 w-4" />
                    <span className="text-xs uppercase tracking-[0.24em]">Meeting Heat Map</span>
                  </div>
                  <p className="text-sm text-white/60">See which meetings have live Blackbook runners before you drill into a horse.</p>
                </div>

                <div className="flex flex-wrap gap-2">
                  {heatmap?.availableDates.map((value) => (
                    <Button
                      key={value}
                      type="button"
                      size="sm"
                      variant={selectedHeatmapDate === value ? "default" : "outline"}
                      onClick={() => setSelectedHeatmapDate(value)}
                    >
                      {new Date(`${value}T00:00:00`).toLocaleDateString("en-AU", { weekday: "short", day: "numeric", month: "short" }).toUpperCase()}
                    </Button>
                  ))}
                </div>
              </div>
            </CardHeader>
            <CardContent className="grid grid-cols-1 xl:grid-cols-3 gap-4">
              {heatmap?.meetings.length ? (
                heatmap.meetings.map((meeting) => (
                  <div key={meeting.track} className="rounded-2xl border border-white/10 bg-black/30 p-4 space-y-4">
                    <div className="flex items-center justify-between">
                      <div>
                        <p className="text-white font-semibold">{meeting.track}</p>
                        <p className="text-xs text-white/40">{meeting.region}</p>
                      </div>
                      <Badge variant="secondary">{meeting.races.length} races</Badge>
                    </div>

                    <div className="space-y-2">
                      {meeting.races.map((race) => (
                        <div key={`${meeting.track}-${race.raceNumber}`} className={`rounded-xl border px-3 py-3 ${bandFrame(race.readinessBand)}`}>
                          <div className="flex items-center justify-between gap-3">
                            <div>
                              <p className="text-sm font-medium text-white">R{race.raceNumber} {race.horseName}</p>
                              <p className="text-xs text-white/45">{formatOffTime(race.offTime)} • {race.raceName || "Blackbook runner"}</p>
                            </div>
                            <Badge className={formatBandColor(race.readinessBand)}>
                              {race.readinessScore}/10
                            </Badge>
                          </div>
                          <div className="mt-3 flex items-center gap-2 text-xs">
                            <Badge variant="outline" className="border-white/15 text-white/60">{race.verdict}</Badge>
                            {race.valueDetected ? (
                              <Badge className="bg-green-500/15 text-green-300 border-green-500/40">VALUE</Badge>
                            ) : null}
                          </div>
                        </div>
                      ))}
                    </div>
                  </div>
                ))
              ) : (
                <div className="xl:col-span-3 rounded-2xl border border-white/10 bg-black/20 p-10 text-center text-white/50">
                  No nominated Blackbook runners for the selected date yet.
                </div>
              )}
            </CardContent>
          </Card>

          <div className="grid grid-cols-1 xl:grid-cols-[minmax(0,1.55fr)_minmax(340px,0.9fr)] gap-5 items-start">
            <div className="space-y-4">
              {data?.entries.length ? (
                data.entries.map((entry) => (
                  <Card
                    key={entry.id}
                    className={`cursor-pointer transition-colors border ${selectedEntryId === entry.id ? `border-racing-gold/45 ${bandFrame(entry.readinessBand)}` : `${bandFrame(entry.readinessBand)} hover:border-white/20`}`}
                    onClick={() => setSelectedEntryId(entry.id)}
                    data-testid={`blackbook-card-${entry.id}`}
                  >
                    <CardContent className="p-5 space-y-4">
                      <div className="flex flex-col gap-4 xl:grid xl:grid-cols-[minmax(0,1fr)_240px] xl:items-start">
                        <div className="space-y-3">
                          <div className="flex items-center gap-2 flex-wrap">
                            <Badge className="bg-black/50 border-white/10 text-white/70">{entry.sourceRun.track} R{entry.sourceRun.raceNumber}</Badge>
                            <Badge className={formatBandColor(entry.readinessBand)}>{entry.readinessBand}</Badge>
                            {entry.nextRun && entry.nextRun.valueEdgePct !== null && entry.nextRun.valueEdgePct >= 3 ? (
                              <Badge className="bg-green-500/15 text-green-300 border-green-500/40">VALUE DETECTED</Badge>
                            ) : null}
                          </div>
                          <div className="space-y-1">
                            <h3 className="text-2xl font-syne font-bold text-white">{entry.horseName}</h3>
                            <p className={`text-sm ${formatReasonTone(entry.primaryReason)}`}>{entry.reasonLabel}</p>
                          </div>
                          <div className="space-y-2">
                            <p className="text-[11px] uppercase tracking-[0.22em] text-white/35">Why It Still Matters</p>
                            <p className="text-sm leading-7 text-white/72">
                              {buildWhyItStillMatters(entry)}
                            </p>
                          </div>
                          <div className="rounded-2xl border border-white/10 bg-black/25 px-4 py-3">
                            <p className="text-[11px] uppercase tracking-[0.22em] text-white/35">Next Action</p>
                            <p className="mt-2 text-sm leading-7 text-white/80">{buildNextActionCopy(entry)}</p>
                          </div>
                        </div>

                        <div className="space-y-3 rounded-3xl border border-white/10 bg-black/30 p-4">
                          <div>
                            <p className="text-[11px] uppercase tracking-[0.22em] text-white/35">Sectional Edge</p>
                            <p className="mt-2 text-2xl font-syne font-bold text-emerald-300">{entry.metric.display}</p>
                          </div>
                          <div className="grid grid-cols-2 gap-3 text-sm">
                            <div>
                              <p className="text-[11px] uppercase tracking-[0.18em] text-white/35">Readiness</p>
                              <p className="mt-1 font-semibold text-white">{entry.readinessScore}/10</p>
                            </div>
                            <div>
                              <p className="text-[11px] uppercase tracking-[0.18em] text-white/35">Confidence</p>
                              <p className="mt-1 font-semibold text-white">{(entry.intakeConfidence * 100).toFixed(0)}%</p>
                            </div>
                          </div>
                          <p className="text-sm leading-6 text-white/55">{formatReadinessSummary(entry)}</p>
                        </div>
                      </div>

                      <div className="flex flex-wrap gap-2">
                        <Badge variant="outline" className="border-white/10 text-white/60">{entry.region}</Badge>
                        {entry.sourceRun.position !== null ? (
                          <Badge variant="outline" className="border-white/10 text-white/60">Finished {entry.sourceRun.position}</Badge>
                        ) : null}
                        {entry.sourceRun.distanceM ? (
                          <Badge variant="outline" className="border-white/10 text-white/60">{entry.sourceRun.distanceM}m</Badge>
                        ) : null}
                        {typeof entry.frankingScore === "number" ? (
                          <Badge variant="outline" className="border-white/10 text-white/60">Franking {entry.frankingScore.toFixed(0)}</Badge>
                        ) : null}
                        <Badge variant="outline" className="border-white/10 text-white/60">{entry.alertCount} alerts</Badge>
                      </div>
                    </CardContent>
                  </Card>
                ))
              ) : (
                <Card className="bg-white/[0.03] border-white/10">
                  <CardContent className="py-16 text-center">
                    <Search className="h-10 w-10 mx-auto mb-4 text-white/25" />
                    <p className="text-white/70">No Blackbook entries match those filters.</p>
                  </CardContent>
                </Card>
              )}
            </div>

            <div className="space-y-4 xl:sticky xl:top-6">
              <Card className="bg-white/[0.03] border-white/10">
                <CardHeader className="pb-3">
                  <div className="space-y-1">
                    <div className="flex items-center gap-2 text-racing-gold">
                      <Sparkles className="h-4 w-4" />
                      <span className="text-xs uppercase tracking-[0.24em]">Stride Blackbook Read</span>
                    </div>
                    <p className="text-sm text-white/50">Latest-start AI read, stewards note, and next-start setup.</p>
                  </div>
                </CardHeader>
                <CardContent className="space-y-4">
                  {detailLoading ? (
                    <div className="space-y-3">
                      <Skeleton className="h-20 w-full" />
                      <Skeleton className="h-28 w-full" />
                      <Skeleton className="h-24 w-full" />
                    </div>
                  ) : detail ? (
                    <>
                      <div className="rounded-2xl border border-racing-gold/15 bg-black/30 p-4 space-y-3">
                        <div className="flex items-start justify-between gap-3">
                          <div className="space-y-1">
                            <p className="text-xl font-semibold text-white">{detail.entry.horseName}</p>
                            <p className="text-sm text-white/45">
                              Latest start • {detail.entry.sourceRun.track} R{detail.entry.sourceRun.raceNumber} • {formatRaceDate(detail.entry.sourceRun.raceDate)}
                            </p>
                          </div>
                          <div className="flex flex-col items-end gap-2">
                            <Badge className={formatBandColor(detail.entry.readinessBand)}>{detail.entry.readinessBand}</Badge>
                            <Badge variant="outline" className="border-white/10 text-white/60">
                              {detail.entry.readinessScore}/10
                            </Badge>
                          </div>
                        </div>
                        <div className="flex flex-wrap gap-2">
                          <Badge className="bg-black/50 border-white/10 text-white/70">{detail.entry.reasonLabel}</Badge>
                          <Badge variant="outline" className="border-emerald-500/25 text-emerald-300">{detail.entry.metric.display}</Badge>
                          {detail.entry.sourceRun.position !== null && (
                            <Badge variant="outline" className="border-white/10 text-white/60">
                              Finished {detail.entry.sourceRun.position}
                            </Badge>
                          )}
                        </div>
                      </div>

                      <div className="rounded-xl border border-white/10 bg-black/25 p-4">
                        <p className="text-xs uppercase tracking-[0.24em] text-white/35 mb-2">STRIDE Read</p>
                        <p className="text-sm text-white/75 leading-7">
                          {detail.detail.entryAnalysis?.summary
                            || [detail.detail.entryAnalysis?.mechanism, detail.detail.entryAnalysis?.validation].filter(Boolean).join(" ")
                            || "No AI read stored for this latest-start profile yet."}
                        </p>
                      </div>

                      <div className="rounded-xl border border-white/10 bg-black/25 p-4">
                        <p className="text-xs uppercase tracking-[0.24em] text-white/35 mb-2">Most Recent Stewards Report</p>
                        <p className="text-sm text-white/75 leading-7">
                          {detail.detail.sourceComment || "No stewards line is stored for this latest start."}
                        </p>
                      </div>

                      <div className="rounded-xl border border-white/10 bg-black/25 p-4 space-y-3">
                        <p className="text-xs uppercase tracking-[0.24em] text-white/35">Next Start Setup</p>
                        {detail.entry.nextRun ? (
                          <>
                            <p className="text-sm text-white/75 leading-7">
                              {detail.detail.conditionAlignment?.summary || detail.detail.entryAnalysis?.conditionsNeeded || detail.detail.idealConditions?.summary || "Waiting for the setup to sharpen."}
                            </p>
                            <div className="grid grid-cols-2 gap-3 text-sm">
                              <div className="rounded-lg border border-white/10 bg-black/20 p-3">
                                <p className="text-xs uppercase tracking-[0.2em] text-white/35">Next Run</p>
                                <p className="text-white/80 mt-2">{detail.entry.nextRun.track} R{detail.entry.nextRun.raceNumber}</p>
                                <p className="text-white/45 mt-1">{formatOffTime(detail.entry.nextRun.offTime)} • {detail.entry.nextRun.going || "Track TBA"}</p>
                              </div>
                              <div className="rounded-lg border border-white/10 bg-black/20 p-3">
                                <p className="text-xs uppercase tracking-[0.2em] text-white/35">Setup</p>
                                <p className="text-white/80 mt-2">Barrier {detail.entry.nextRun.barrier ?? "TBA"}</p>
                                <p className="text-white/45 mt-1">{detail.entry.nextRun.tempoSummary} tempo • {formatValueEdge(detail.entry.nextRun.valueEdgePct)}</p>
                              </div>
                            </div>
                          </>
                        ) : (
                          <>
                            <p className="text-sm text-white/75 leading-7">
                              {detail.detail.entryAnalysis?.conditionsNeeded || detail.detail.idealConditions?.summary || "Waiting for a next nomination before STRIDE can reassess the setup."}
                            </p>
                            <p className="text-sm text-white/45">No matched next run yet, so this horse stays on watch rather than becoming an active play.</p>
                          </>
                        )}
                      </div>

                      {detail.entry.nextRun && (
                        <div className="rounded-xl border border-white/10 bg-black/25 p-4 space-y-3">
                          <div className="flex items-center justify-between gap-3">
                            <p className="text-xs uppercase tracking-[0.24em] text-white/35">Race-Day Call</p>
                            <Badge className={formatBandColor(detail.entry.readinessBand)}>
                              {detail.detail.raceDayBrief?.verdict || detail.entry.nextRun.verdict}
                            </Badge>
                          </div>
                          <p className="text-sm text-emerald-300 leading-7">
                            {detail.detail.raceDayBrief?.winCondition || "Waiting for a race-day update before STRIDE turns this into a betting brief."}
                          </p>
                          {detail.detail.raceDayBrief?.primaryRisk && (
                            <p className="text-sm text-rose-300 leading-7">{detail.detail.raceDayBrief.primaryRisk}</p>
                          )}
                        </div>
                      )}
                    </>
                  ) : (
                    <div className="rounded-xl border border-white/10 bg-black/20 p-8 text-center text-white/45">
                      Select a Blackbook runner to open the latest-start brief.
                    </div>
                  )}
                </CardContent>
              </Card>
            </div>
          </div>
        </>
      )}
    </div>
  );
}
