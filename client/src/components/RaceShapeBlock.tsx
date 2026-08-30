import { useMemo, useState } from "react";
import { ChevronDown, ChevronUp, Flag, RefreshCw, Timer } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { RaceSpeedMapGraphic } from "@/components/RaceSpeedMapGraphic";
import type { RaceOverviewState, RaceShapeRunner, RaceShapeZone } from "@/hooks/useRaceOverview";
import type { RaceSpeedMap } from "@/types/speedMap";

interface RaceShapeBlockProps {
  track?: string;
  raceNumber?: number;
  raceDate?: string;
  state: RaceOverviewState;
  retryOverview: () => Promise<void>;
  speedMap?: RaceSpeedMap | null;
}

const ZONE_STYLES: Record<RaceShapeZone["key"], { border: string; bg: string; pill: string; text: string }> = {
  leader: {
    border: "border-red-500/30",
    bg: "bg-red-500/10",
    pill: "bg-red-500 text-black",
    text: "text-red-300",
  },
  on_pace: {
    border: "border-orange-500/30",
    bg: "bg-orange-500/10",
    pill: "bg-orange-500 text-black",
    text: "text-orange-300",
  },
  on_pace_wide: {
    border: "border-amber-500/30",
    bg: "bg-amber-500/10",
    pill: "bg-amber-400 text-black",
    text: "text-amber-200",
  },
  midfield: {
    border: "border-green-500/30",
    bg: "bg-green-500/10",
    pill: "bg-green-500 text-black",
    text: "text-green-300",
  },
  back_half: {
    border: "border-blue-500/30",
    bg: "bg-blue-500/10",
    pill: "bg-blue-500 text-white",
    text: "text-blue-300",
  },
  backmarker: {
    border: "border-purple-500/30",
    bg: "bg-purple-500/10",
    pill: "bg-purple-500 text-white",
    text: "text-purple-300",
  },
};

const TEMPO_STYLES = {
  HOT: "border-red-500/30 bg-red-500/10 text-red-300",
  GENUINE: "border-orange-500/30 bg-orange-500/10 text-orange-300",
  SOFT: "border-amber-500/30 bg-amber-500/10 text-amber-200",
  UNKNOWN: "border-white/10 bg-white/[0.04] text-white/70",
} as const;

function sortZoneRunners(a: RaceShapeRunner, b: RaceShapeRunner) {
  if (a.band !== b.band) {
    return a.band - b.band;
  }
  if (a.barrier == null && b.barrier == null) {
    return a.horse.localeCompare(b.horse);
  }
  if (a.barrier == null) {
    return 1;
  }
  if (b.barrier == null) {
    return -1;
  }
  if (a.barrier !== b.barrier) {
    return a.barrier - b.barrier;
  }
  return a.horse.localeCompare(b.horse);
}

function RaceShapeLoading() {
  return (
    <div className="space-y-3">
      <div className="grid grid-cols-2 gap-3 md:grid-cols-6">
        {Array.from({ length: 6 }).map((_, index) => (
          <div key={index} className="rounded-xl border border-white/[0.06] bg-black/20 p-3">
            <Skeleton className="h-3 w-16 bg-white/10" />
            <div className="mt-3 space-y-2">
              <Skeleton className="h-8 w-8 rounded-full bg-white/10" />
              <Skeleton className="h-8 w-8 rounded-full bg-white/10" />
            </div>
          </div>
        ))}
      </div>
      <Skeleton className="h-11 w-full bg-white/10" />
      <Skeleton className="h-20 w-full bg-white/10" />
      <div className="grid grid-cols-2 gap-3 md:grid-cols-6">
        {Array.from({ length: 6 }).map((_, index) => (
          <Skeleton key={index} className="h-14 w-full bg-white/10" />
        ))}
      </div>
    </div>
  );
}

function descriptorForSpeedMapRunner(runner: NonNullable<RaceShapeBlockProps["speedMap"]>["runners"][number]) {
  return [runner.age, runner.colour, runner.sex].filter(Boolean).join(" ") || "Race-day runner";
}

export function RaceShapeBlock({
  track,
  raceNumber,
  raceDate,
  state,
  retryOverview,
  speedMap,
}: RaceShapeBlockProps) {
  const [collapsedMobile, setCollapsedMobile] = useState(false);
  const [activeHorse, setActiveHorse] = useState<string | null>(null);
  const raceShape = state.data?.raceShape || null;
  const tokenRunners = raceShape?.runners || [];
  const zoneGroups = useMemo(() => {
    if (!raceShape) {
      return [];
    }

    return raceShape.zones.map((zone) => ({
      zone,
      runners: raceShape.runners.filter((runner) => runner.zone === zone.key).slice().sort(sortZoneRunners),
    }));
  }, [raceShape]);

  const activeSpeedMapRunner = useMemo(() => {
    const runners = speedMap?.runners || [];
    if (!runners.length) {
      return null;
    }
    if (activeHorse) {
      return runners.find((runner) => runner.horse === activeHorse) || runners.find((runner) => runner.isHighlighted) || runners[0];
    }
    return runners.find((runner) => runner.isHighlighted) || runners[0];
  }, [activeHorse, speedMap?.runners]);

  const activeTokenRunner = useMemo(() => {
    if (!tokenRunners.length) {
      return null;
    }
    if (!activeHorse) {
      return tokenRunners[0];
    }
    return tokenRunners.find((runner) => runner.horse === activeHorse) || tokenRunners[0];
  }, [activeHorse, tokenRunners]);

  if (!track || !raceNumber || !raceDate) {
    return null;
  }

  const hasSpeedMap = Boolean(speedMap?.runners?.length);
  const hasAnyShapeData = hasSpeedMap || Boolean(raceShape);
  const tempoLabel = speedMap?.tempoLabel || raceShape?.tempoLabel;
  const tempoReason = speedMap?.tempoReason || raceShape?.tempoReason;
  const trackBias = raceShape?.trackBias || null;
  const narrative = raceShape?.narrative || speedMap?.narrative || null;
  const leaders = speedMap?.leaders ?? raceShape?.leaders ?? 0;
  const onPace = speedMap?.onPace ?? raceShape?.onPace ?? 0;
  const midfield = speedMap?.midfield ?? raceShape?.midfield ?? 0;
  const closers = speedMap?.closers ?? raceShape?.closers ?? 0;

  return (
    <div className="border-b border-white/[0.06] bg-[linear-gradient(180deg,rgba(255,255,255,0.04),rgba(255,255,255,0.015))]">
      <div className="px-4 py-4 sm:px-5">
        <div className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
          <div className="flex items-center gap-3">
            <div className="flex h-8 w-8 items-center justify-center rounded-lg bg-racing-gold/15 text-racing-gold">
              <Flag className="h-4 w-4" />
            </div>
            <div>
              <div className="flex items-center gap-2">
                <span className="text-sm font-semibold uppercase tracking-[0.16em] text-white/85">
                  Race Shape
                </span>
                <button
                  type="button"
                  onClick={() => setCollapsedMobile((value) => !value)}
                  className="inline-flex items-center gap-1 rounded-full border border-white/10 bg-white/[0.04] px-2 py-0.5 text-[10px] uppercase tracking-[0.18em] text-white/45 md:hidden"
                >
                  {collapsedMobile ? (
                    <>
                      <ChevronDown className="h-3 w-3" />
                      Expand
                    </>
                  ) : (
                    <>
                      <ChevronUp className="h-3 w-3" />
                      Collapse
                    </>
                  )}
                </button>
              </div>
              <p className="text-xs text-white/40">
                Real racecard-driven speed map, tempo call, and field-shape read for this race.
              </p>
            </div>
          </div>

          {tempoLabel ? (
            <div className="flex items-center gap-2 self-start">
              <Badge variant="outline" className={`gap-1 border ${TEMPO_STYLES[tempoLabel]}`}>
                <Timer className="h-3 w-3" />
                {tempoLabel} TEMPO
              </Badge>
              {trackBias ? (
                <Badge variant="outline" className="border-white/10 bg-white/[0.04] text-white/70">
                  {trackBias}
                </Badge>
              ) : null}
            </div>
          ) : null}
        </div>

        <div className={collapsedMobile ? "mt-4 hidden md:block" : "mt-4 block"}>
          {!hasAnyShapeData && (state.status === "loading" || state.status === "idle") ? (
            <RaceShapeLoading />
          ) : !hasAnyShapeData && state.status === "error" ? (
            <div className="rounded-xl border border-red-500/20 bg-red-500/5 px-4 py-4">
              <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
                <p className="text-sm text-red-300">Race shape unavailable.</p>
                <Button
                  variant="outline"
                  size="sm"
                  className="border-red-500/30 text-red-300 hover:bg-red-500/10"
                  onClick={() => void retryOverview()}
                >
                  Retry
                </Button>
              </div>
              {state.message ? <p className="mt-2 text-xs text-red-300/70">{state.message}</p> : null}
            </div>
          ) : !hasAnyShapeData ? (
            <div className="rounded-xl border border-white/[0.08] bg-black/20 px-4 py-4">
              <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
                <p className="text-sm text-white/60">Race shape data has not been generated for this field yet.</p>
                <Button
                  variant="outline"
                  size="sm"
                  className="gap-2 border-white/15 text-white/70 hover:bg-white/[0.06]"
                  onClick={() => void retryOverview()}
                >
                  <RefreshCw className="h-3.5 w-3.5" />
                  Refresh
                </Button>
              </div>
            </div>
          ) : (
            <div className="space-y-4">
              {hasSpeedMap ? (
                <div className="space-y-4">
                  <RaceSpeedMapGraphic
                    speedMap={speedMap!}
                    activeHorse={activeSpeedMapRunner?.horse || null}
                    onActiveHorseChange={setActiveHorse}
                  />

                  {activeSpeedMapRunner ? (
                    <div className="rounded-xl border border-white/[0.08] bg-black/30 p-4">
                      <div className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
                        <div className="min-w-0">
                          <div className="flex flex-wrap items-center gap-2">
                            <span className="text-sm font-semibold text-white">{activeSpeedMapRunner.horse}</span>
                            <Badge variant="outline" className="border-white/10 bg-white/[0.04] text-white/70">
                              Barrier {activeSpeedMapRunner.barrier ?? "?"}
                            </Badge>
                            <Badge variant="outline" className="border-white/10 bg-white/[0.04] text-white/70">
                              Settle {activeSpeedMapRunner.predictedSettlingPosition}/{speedMap?.fieldSize}
                            </Badge>
                            <Badge variant="outline" className="border-white/10 bg-white/[0.04] text-white/70">
                              {descriptorForSpeedMapRunner(activeSpeedMapRunner)}
                            </Badge>
                            {activeSpeedMapRunner.isHighlighted ? (
                              <Badge variant="outline" className="border-racing-gold/40 bg-racing-gold/10 text-racing-gold">
                                Top Pick
                              </Badge>
                            ) : activeSpeedMapRunner.isTipped ? (
                              <Badge variant="outline" className="border-racing-orange/40 bg-racing-orange/10 text-racing-orange">
                                Tipped
                              </Badge>
                            ) : null}
                          </div>
                          <p className="mt-2 text-xs text-white/45">
                            {activeSpeedMapRunner.jockey ? `${activeSpeedMapRunner.jockey} | ` : ""}
                            {activeSpeedMapRunner.trainer || "Stable not listed"}
                          </p>
                        </div>

                        {activeSpeedMapRunner.silkUrl ? (
                          <div className="h-12 w-12 shrink-0 overflow-hidden rounded-xl border border-white/10 bg-black/35">
                            <img
                              src={activeSpeedMapRunner.silkUrl}
                              alt=""
                              className="h-full w-full object-cover"
                              loading="lazy"
                              referrerPolicy="no-referrer"
                            />
                          </div>
                        ) : null}
                      </div>

                      <p className="mt-3 text-sm leading-relaxed text-white/72">
                        {activeSpeedMapRunner.placementReason}
                      </p>
                      <p className="mt-2 text-xs text-white/40">
                        Source: {activeSpeedMapRunner.placementSource}
                        {activeSpeedMapRunner.comment ? ` | Last-run note: ${activeSpeedMapRunner.comment}` : ""}
                      </p>
                    </div>
                  ) : null}
                </div>
              ) : (
                <div className="rounded-2xl border border-white/[0.06] bg-black/20 p-3 sm:p-4">
                  <div className="mb-3 flex items-center justify-between gap-3">
                    <div className="text-[10px] uppercase tracking-[0.22em] text-white/40">
                      Track Diagram
                    </div>
                    <div className="text-[10px] uppercase tracking-[0.18em] text-white/35">
                      Finish Left • Start Right
                    </div>
                  </div>

                  <div className="grid grid-cols-2 gap-3 md:grid-cols-6">
                    {zoneGroups.map(({ zone, runners: zoneRunners }) => {
                      const styles = ZONE_STYLES[zone.key];
                      return (
                        <div key={zone.key} className={`rounded-xl border p-3 ${styles.border} ${styles.bg}`}>
                          <div className="flex items-start justify-between gap-2">
                            <div>
                              <p className={`text-[11px] font-semibold uppercase tracking-[0.16em] ${styles.text}`}>
                                {zone.label}
                              </p>
                              <p className="text-[10px] text-white/45">Band {zone.bandRange}</p>
                            </div>
                            <Badge variant="outline" className="border-white/10 bg-black/20 text-[10px] text-white/65">
                              {zoneRunners.length}
                            </Badge>
                          </div>

                          <div className="mt-3 flex min-h-[96px] flex-wrap gap-2">
                            {zoneRunners.length > 0 ? (
                              zoneRunners.map((runner) => (
                                <button
                                  key={`${zone.key}-${runner.horse}`}
                                  type="button"
                                  onClick={() => setActiveHorse(runner.horse)}
                                  onMouseEnter={() => setActiveHorse(runner.horse)}
                                  className={`flex h-9 w-9 items-center justify-center rounded-full border text-sm font-semibold transition ${
                                    activeTokenRunner?.horse === runner.horse
                                      ? `${styles.pill} border-white/40 shadow-[0_0_0_1px_rgba(255,255,255,0.12)]`
                                      : "border-white/10 bg-black/25 text-white/80 hover:border-white/20 hover:bg-black/35"
                                  }`}
                                  aria-label={`${runner.horse} barrier ${runner.barrier ?? "?"}`}
                                >
                                  {runner.barrier ?? "?"}
                                </button>
                              ))
                            ) : (
                              <div className="flex items-center text-xs text-white/30">No runners mapped here.</div>
                            )}
                          </div>
                        </div>
                      );
                    })}
                  </div>

                  {activeTokenRunner ? (
                    <div className="mt-4 rounded-xl border border-white/[0.08] bg-black/30 p-3">
                      <div className="flex flex-col gap-2 sm:flex-row sm:items-start sm:justify-between">
                        <div>
                          <div className="flex flex-wrap items-center gap-2">
                            <span className="text-sm font-semibold text-white">{activeTokenRunner.horse}</span>
                            <Badge variant="outline" className="border-white/10 bg-white/[0.04] text-white/70">
                              Barrier {activeTokenRunner.barrier ?? "?"}
                            </Badge>
                            <Badge variant="outline" className="border-white/10 bg-white/[0.04] text-white/70">
                              Band {activeTokenRunner.band}
                            </Badge>
                            {activeTokenRunner.bandSource === "estimated" ? (
                              <Badge variant="outline" className="border-amber-500/30 bg-amber-500/10 text-amber-200">
                                Est.
                              </Badge>
                            ) : null}
                          </div>
                          <p className="mt-1 text-xs text-white/45">
                            {activeTokenRunner.jockey ? `${activeTokenRunner.jockey} | ` : ""}
                            {activeTokenRunner.paceRole}
                            {activeTokenRunner.sampleCount > 0 && activeTokenRunner.avg800mPct != null
                              ? ` | ${activeTokenRunner.avg800mPct}% avg 800m position`
                              : ""}
                          </p>
                        </div>
                        <div className="max-w-xl text-sm text-white/70">
                          {activeTokenRunner.reasonSummary}
                          {activeTokenRunner.positionConflictReason ? (
                            <span className="block pt-1 text-amber-200/85">{activeTokenRunner.positionConflictReason}</span>
                          ) : null}
                        </div>
                      </div>
                    </div>
                  ) : null}
                </div>
              )}

              {tempoLabel && tempoReason ? (
                <div className={`rounded-2xl border px-4 py-3 ${TEMPO_STYLES[tempoLabel]}`}>
                  <div className="flex flex-col gap-2 sm:flex-row sm:items-center sm:justify-between">
                    <div>
                      <p className="text-[10px] uppercase tracking-[0.22em] text-white/45">Tempo Banner</p>
                      <p className="text-base font-semibold">{tempoLabel} TEMPO</p>
                    </div>
                    <p className="max-w-3xl text-sm leading-relaxed text-white/80">
                      {tempoReason}
                    </p>
                  </div>
                </div>
              ) : null}

              {narrative ? (
                <div className="rounded-2xl border border-white/[0.06] bg-black/20 px-4 py-4">
                  <div className="mb-2 text-[10px] uppercase tracking-[0.22em] text-white/40">
                    Race Shape Narrative
                  </div>
                  <p className="text-sm leading-relaxed text-white/78">
                    {narrative}
                  </p>
                </div>
              ) : null}

              <div className="grid grid-cols-2 gap-3 md:grid-cols-6">
                <div className="rounded-xl border border-white/[0.06] bg-black/20 px-3 py-3">
                  <p className="text-[10px] uppercase tracking-[0.18em] text-white/35">Leaders</p>
                  <p className="mt-1 text-xl font-semibold text-white">{leaders}</p>
                  <p className="text-xs text-white/45">Front line</p>
                </div>
                <div className="rounded-xl border border-white/[0.06] bg-black/20 px-3 py-3">
                  <p className="text-[10px] uppercase tracking-[0.18em] text-white/35">On-Pace</p>
                  <p className="mt-1 text-xl font-semibold text-white">{onPace}</p>
                  <p className="text-xs text-white/45">Leader + pace</p>
                </div>
                <div className="rounded-xl border border-white/[0.06] bg-black/20 px-3 py-3">
                  <p className="text-[10px] uppercase tracking-[0.18em] text-white/35">Midfield</p>
                  <p className="mt-1 text-xl font-semibold text-white">{midfield}</p>
                  <p className="text-xs text-white/45">Off pace + midfield</p>
                </div>
                <div className="rounded-xl border border-white/[0.06] bg-black/20 px-3 py-3">
                  <p className="text-[10px] uppercase tracking-[0.18em] text-white/35">Closers</p>
                  <p className="mt-1 text-xl font-semibold text-white">{closers}</p>
                  <p className="text-xs text-white/45">Back-half runners</p>
                </div>
                <div className="rounded-xl border border-white/[0.06] bg-black/20 px-3 py-3">
                  <p className="text-[10px] uppercase tracking-[0.18em] text-white/35">Tempo</p>
                  <p className="mt-1 text-xl font-semibold text-white">{tempoLabel || "—"}</p>
                  <p className="text-xs text-white/45">Projected shape</p>
                </div>
                <div className="rounded-xl border border-white/[0.06] bg-black/20 px-3 py-3">
                  <p className="text-[10px] uppercase tracking-[0.18em] text-white/35">Track Bias</p>
                  <p className="mt-1 text-sm font-semibold text-white">
                    {trackBias || "Not flagged"}
                  </p>
                  <p className="text-xs text-white/45">
                    {trackBias ? "Passed through from race prep" : "No explicit bias signal"}
                  </p>
                </div>
              </div>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
