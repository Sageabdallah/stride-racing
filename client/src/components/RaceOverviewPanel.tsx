import { useEffect, useMemo, useState } from "react";
import { Brain, ChevronDown, ChevronUp, Sparkles } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { type RaceOverviewState, useRaceOverview } from "@/hooks/useRaceOverview";

interface RaceOverviewPanelProps {
  track?: string;
  raceNumber?: number;
  raceDate?: string;
  state?: RaceOverviewState;
  retryOverview?: () => Promise<void>;
}

const COLLAPSE_PREFIX = "race-overview-collapse:";

const OVERVIEW_SECTIONS = [
  { label: "RACE MAP", key: "raceMap" as const },
  { label: "PACE ANALYSIS", key: "paceAnalysis" as const },
  { label: "TRACK & CONDITIONS", key: "trackConditions" as const },
  { label: "FORM CYCLE", key: "formCycle" as const },
  { label: "MARKET INTELLIGENCE", key: "marketIntelligence" as const },
  { label: "RACE PREDICTION", key: "racePrediction" as const, highlight: true },
];

function OverviewSection({
  label,
  text,
  highlight = false,
}: {
  label: string;
  text: string;
  highlight?: boolean;
}) {
  return (
    <div
      className={`rounded-xl border px-4 py-4 ${
        highlight
          ? "border-racing-orange/35 bg-racing-orange/10 shadow-[0_0_0_1px_rgba(249,115,22,0.08)]"
          : "border-white/[0.06] bg-black/20"
      }`}
    >
      <div className="mb-2 flex items-center gap-3">
        <span
          className={`text-[10px] uppercase tracking-[0.22em] ${
            highlight ? "font-semibold text-racing-orange/90" : "font-medium text-white/40"
          }`}
        >
          {label}
        </span>
        <div className={`h-px flex-1 ${highlight ? "bg-racing-orange/20" : "bg-white/10"}`} />
      </div>
      <p className={`leading-relaxed ${highlight ? "text-sm font-medium text-white/90" : "text-sm text-white/75"}`}>
        {text}
      </p>
    </div>
  );
}

function RaceOverviewLoading() {
  return (
    <div className="space-y-3">
      <Skeleton className="h-4 w-[88%] bg-white/10" />
      <Skeleton className="h-4 w-[62%] bg-white/10" />
      <Skeleton className="h-4 w-[94%] bg-white/10" />
    </div>
  );
}

function RaceOverviewPanelContent({
  track,
  raceNumber,
  raceDate,
  state,
  retryOverview,
}: {
  track?: string;
  raceNumber?: number;
  raceDate?: string;
  state: RaceOverviewState;
  retryOverview: () => Promise<void>;
}) {
  const [collapsed, setCollapsed] = useState(false);

  const collapseKey = useMemo(() => {
    if (!track || !raceNumber || !raceDate) {
      return null;
    }
    return `${COLLAPSE_PREFIX}${raceDate}|${raceNumber}|${track.toLowerCase()}`;
  }, [raceDate, raceNumber, track]);

  useEffect(() => {
    if (!collapseKey || typeof window === "undefined") {
      setCollapsed(false);
      return;
    }

    const stored = window.sessionStorage.getItem(collapseKey);
    setCollapsed(stored === "1");
  }, [collapseKey]);

  if (!track || !raceNumber || !raceDate) {
    return null;
  }

  const toggleCollapsed = () => {
    const next = !collapsed;
    setCollapsed(next);
    if (collapseKey && typeof window !== "undefined") {
      window.sessionStorage.setItem(collapseKey, next ? "1" : "0");
    }
  };

  return (
    <div className="border-b border-white/[0.06] bg-[linear-gradient(180deg,rgba(255,255,255,0.05),rgba(255,255,255,0.02))]">
      <div className="px-4 py-4 sm:px-5">
        <div className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
          <div className="flex items-center gap-3">
            <button
              type="button"
              onClick={toggleCollapsed}
              className="flex items-center gap-2 text-left"
            >
              <div className="flex h-8 w-8 items-center justify-center rounded-lg bg-racing-orange/15 text-racing-orange">
                <Brain className="h-4 w-4" />
              </div>
              <div>
                <div className="flex items-center gap-2">
                  <span className="text-sm font-semibold uppercase tracking-[0.16em] text-white/85">
                    AI Race Overview
                  </span>
                  {collapsed ? (
                    <ChevronDown className="h-4 w-4 text-white/40" />
                  ) : (
                    <ChevronUp className="h-4 w-4 text-white/40" />
                  )}
                </div>
                <p className="text-xs text-white/40">
                  Race-level pace, map, market, and finishing call before the runner rows.
                </p>
              </div>
            </button>
          </div>

          <div className="flex items-center gap-2 self-start">
            <div className="inline-flex items-center gap-1 rounded-full border border-white/10 bg-white/[0.04] px-2.5 py-1 text-[10px] uppercase tracking-[0.18em] text-white/45">
              <Sparkles className="h-3 w-3 text-racing-orange" />
              Powered by WizBet
            </div>
            <Button
              type="button"
              variant="ghost"
              size="sm"
              onClick={toggleCollapsed}
              className="h-8 px-2 text-xs text-white/55 hover:text-white"
            >
              {collapsed ? "Expand" : "Collapse"}
            </Button>
          </div>
        </div>

        {!collapsed && (
          <div className="mt-4 space-y-3">
            {state.status === "loading" || state.status === "idle" ? (
              <RaceOverviewLoading />
            ) : state.status === "error" ? (
              <div className="rounded-xl border border-red-500/20 bg-red-500/5 px-4 py-4">
                <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
                  <p className="text-sm text-red-300">Race overview unavailable.</p>
                  <Button
                    variant="outline"
                    size="sm"
                    className="border-red-500/30 text-red-300 hover:bg-red-500/10"
                    onClick={() => void retryOverview()}
                  >
                    Retry
                  </Button>
                </div>
                {state.message && <p className="mt-2 text-xs text-red-300/70">{state.message}</p>}
              </div>
            ) : (
              OVERVIEW_SECTIONS.map((section) => (
                <OverviewSection
                  key={section.label}
                  label={section.label}
                  text={state.data?.overview?.[section.key] || ""}
                  highlight={Boolean(section.highlight)}
                />
              ))
            )}
          </div>
        )}
      </div>
    </div>
  );
}

export function RaceOverviewPanel(props: RaceOverviewPanelProps) {
  if (props.state && props.retryOverview) {
    return (
      <RaceOverviewPanelContent
        track={props.track}
        raceNumber={props.raceNumber}
        raceDate={props.raceDate}
        state={props.state}
        retryOverview={props.retryOverview}
      />
    );
  }

  return <RaceOverviewPanelWithData track={props.track} raceNumber={props.raceNumber} raceDate={props.raceDate} />;
}

function RaceOverviewPanelWithData({
  track,
  raceNumber,
  raceDate,
}: {
  track?: string;
  raceNumber?: number;
  raceDate?: string;
}) {
  const { state, retryOverview } = useRaceOverview({ track, raceNumber, raceDate });

  return (
    <RaceOverviewPanelContent
      track={track}
      raceNumber={raceNumber}
      raceDate={raceDate}
      state={state}
      retryOverview={retryOverview}
    />
  );
}
