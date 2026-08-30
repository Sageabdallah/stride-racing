import { useEffect, useMemo, useRef, useState } from "react";
import { Badge } from "@/components/ui/badge";
import type { RaceSpeedMap, RaceSpeedMapRunner, RaceSpeedMapZone } from "@/types/speedMap";

const VISUAL_ZONES: Array<{ key: RaceSpeedMapZone; label: string; tint: string; text: string }> = [
  { key: "backmarker", label: "Backmarker", tint: "from-[#d7b55d]/30 to-[#8a651e]/10", text: "text-[#f3d37d]" },
  { key: "off_midfield", label: "Off Midfield", tint: "from-[#7d8798]/28 to-[#38404b]/10", text: "text-slate-200" },
  { key: "midfield", label: "Midfield", tint: "from-[#8998ac]/28 to-[#455264]/10", text: "text-slate-100" },
  { key: "off_pace", label: "Off Pace", tint: "from-[#96b082]/26 to-[#43553c]/10", text: "text-[#d5e5c6]" },
  { key: "pace", label: "Pace", tint: "from-[#2f6a33]/32 to-[#16351a]/10", text: "text-[#b9efb3]" },
  { key: "leader", label: "Leader", tint: "from-[#1f5a29]/36 to-[#102e15]/10", text: "text-[#98f08f]" },
];

const ZONE_COUNT = VISUAL_ZONES.length;
const DEFAULT_LAYOUT_WIDTH = 1200;
const SPRITE_WIDTH = 104;
const SPRITE_HEIGHT = 118;
const BASE_BOTTOM = 74;
const MAX_BOTTOM = 176;
const VERTICAL_STEP = 22;
const MIN_GAP_X = 10;
const MIN_GAP_Y = 8;
const X_FAN_SEQUENCE = [0, -0.08, 0.08, -0.16, 0.16];
const DEFAULT_RENDER_SCALE = 0.86;
const SCALE_FLOOR = 0.72;
const SCALE_STEP = 0.03;
const COLLISION_WIDTH_RATIO = 0.82;
const COLLISION_HEIGHT_RATIO = 0.82;

type ResolvedRunnerPosition = {
  runner: RaceSpeedMapRunner;
  left: number;
  bottom: number;
  scale: number;
  zIndex: number;
};

type PlacedRunner = ResolvedRunnerPosition & {
  leftPx: number;
  width: number;
  height: number;
  collisionWidth: number;
  collisionHeight: number;
};

function laneAnchor(lane: number) {
  if (lane <= 0) return 0.28;
  if (lane === 1) return 0.5;
  return 0.72;
}

function laneVerticalBias(lane: number) {
  if (lane <= 0) return 16;
  if (lane === 1) return 8;
  return 0;
}

function clamp(value: number, min: number, max: number) {
  return Math.max(min, Math.min(max, value));
}

function roundTo(value: number, digits = 3) {
  const scale = 10 ** digits;
  return Math.round(value * scale) / scale;
}

function descriptorForRunner(runner: RaceSpeedMapRunner) {
  return [runner.age, runner.colour, runner.sex].filter(Boolean).join(" ") || "Race-day runner";
}

function compareRunners(left: RaceSpeedMapRunner, right: RaceSpeedMapRunner) {
  if (left.predictedSettlingPosition !== right.predictedSettlingPosition) {
    return left.predictedSettlingPosition - right.predictedSettlingPosition;
  }
  if (left.lane !== right.lane) {
    return left.lane - right.lane;
  }
  if (left.barrier == null && right.barrier == null) {
    return left.horse.localeCompare(right.horse);
  }
  if (left.barrier == null) {
    return 1;
  }
  if (right.barrier == null) {
    return -1;
  }
  if (left.barrier !== right.barrier) {
    return left.barrier - right.barrier;
  }
  return left.horse.localeCompare(right.horse);
}

function buildScaleCandidates(baseScale: number) {
  const startingScale = roundTo(Math.max(SCALE_FLOOR, baseScale));
  const candidates: number[] = [];

  for (let scale = startingScale; scale > SCALE_FLOOR; scale = roundTo(scale - SCALE_STEP)) {
    candidates.push(scale);
  }

  if (!candidates.includes(SCALE_FLOOR)) {
    candidates.push(SCALE_FLOOR);
  }

  return candidates;
}

function buildBottomCandidates(baseBottom: number) {
  const candidates: number[] = [];

  for (let bottom = baseBottom; bottom <= MAX_BOTTOM; bottom += VERTICAL_STEP) {
    candidates.push(bottom);
  }

  if (candidates[candidates.length - 1] !== MAX_BOTTOM) {
    candidates.push(MAX_BOTTOM);
  }

  return candidates;
}

function runnerBaseScale() {
  return DEFAULT_RENDER_SCALE;
}

function boxesOverlap(left: PlacedRunner, right: PlacedRunner) {
  const horizontalDistance = Math.abs(left.leftPx - right.leftPx);
  const verticalCentreLeft = left.bottom + left.collisionHeight / 2;
  const verticalCentreRight = right.bottom + right.collisionHeight / 2;
  const verticalDistance = Math.abs(verticalCentreLeft - verticalCentreRight);

  return horizontalDistance < left.collisionWidth / 2 + right.collisionWidth / 2 + MIN_GAP_X
    && verticalDistance < left.collisionHeight / 2 + right.collisionHeight / 2 + MIN_GAP_Y;
}

function clampLeftToZone(rawLeftPx: number, zoneLeftPx: number, zoneWidthPx: number, width: number) {
  const halfWidth = width / 2;
  const minCenter = zoneLeftPx + halfWidth + MIN_GAP_X / 2;
  const maxCenter = zoneLeftPx + zoneWidthPx - halfWidth - MIN_GAP_X / 2;

  if (minCenter > maxCenter) {
    return zoneLeftPx + zoneWidthPx / 2;
  }

  return clamp(rawLeftPx, minCenter, maxCenter);
}

function buildLeftCandidates(baseLeftPx: number, zoneLeftPx: number, zoneWidthPx: number, width: number) {
  const candidates: number[] = [];
  const pushCandidate = (value: number) => {
    if (!candidates.some((candidate) => Math.abs(candidate - value) < 0.5)) {
      candidates.push(value);
    }
  };

  X_FAN_SEQUENCE.forEach((xFanOffset) => {
    pushCandidate(clampLeftToZone(baseLeftPx + xFanOffset * zoneWidthPx, zoneLeftPx, zoneWidthPx, width));
  });

  pushCandidate(clampLeftToZone(zoneLeftPx, zoneLeftPx, zoneWidthPx, width));
  pushCandidate(clampLeftToZone(zoneLeftPx + zoneWidthPx, zoneLeftPx, zoneWidthPx, width));

  return candidates;
}

function resolveRunnerLayout(runners: RaceSpeedMapRunner[], layoutWidth: number): ResolvedRunnerPosition[] {
  const zoneWidthPx = layoutWidth / ZONE_COUNT;
  const placements = new Map<string, PlacedRunner>();

  VISUAL_ZONES.forEach((zone, zoneIndex) => {
    const zoneRunners = runners
      .filter((runner) => runner.zone === zone.key)
      .slice()
      .sort(compareRunners);

    const placedInZone: PlacedRunner[] = [];
    const zoneLeftPx = zoneIndex * zoneWidthPx;

    zoneRunners.forEach((runner) => {
      const baseLeftPx = zoneLeftPx + laneAnchor(runner.lane) * zoneWidthPx;
      const baseBottom = BASE_BOTTOM + laneVerticalBias(runner.lane);
      const scaleCandidates = buildScaleCandidates(runnerBaseScale());
      const bottomCandidates = buildBottomCandidates(baseBottom);
      let chosen: PlacedRunner | null = null;

      for (const scale of scaleCandidates) {
        const width = SPRITE_WIDTH * scale;
        const height = SPRITE_HEIGHT * scale;
        const collisionWidth = width * COLLISION_WIDTH_RATIO;
        const collisionHeight = height * COLLISION_HEIGHT_RATIO;

        for (const leftPx of buildLeftCandidates(baseLeftPx, zoneLeftPx, zoneWidthPx, width)) {

          for (const bottom of bottomCandidates) {
            const candidate: PlacedRunner = {
              runner,
              left: (leftPx / layoutWidth) * 100,
              leftPx,
              bottom,
              scale,
              width,
              height,
              collisionWidth,
              collisionHeight,
              zIndex: 100 + Math.round(MAX_BOTTOM - bottom),
            };

            if (!placedInZone.some((placedRunner) => boxesOverlap(candidate, placedRunner))) {
              chosen = candidate;
              break;
            }
          }

          if (chosen) {
            break;
          }
        }

        if (chosen) {
          break;
        }
      }

      if (!chosen) {
        const scale = SCALE_FLOOR;
        const width = SPRITE_WIDTH * scale;
        const height = SPRITE_HEIGHT * scale;
        const collisionWidth = width * COLLISION_WIDTH_RATIO;
        const collisionHeight = height * COLLISION_HEIGHT_RATIO;
        const leftPx = clampLeftToZone(baseLeftPx, zoneLeftPx, zoneWidthPx, width);
        const bottom = clamp(baseBottom + runner.stackRow * VERTICAL_STEP, baseBottom, MAX_BOTTOM);

        chosen = {
          runner,
          left: (leftPx / layoutWidth) * 100,
          leftPx,
          bottom,
          scale,
          width,
          height,
          collisionWidth,
          collisionHeight,
          zIndex: 100 + Math.round(MAX_BOTTOM - bottom),
        };
      }

      placedInZone.push(chosen);
      placements.set(runner.horse, chosen);
    });
  });

  return runners.map((runner) => {
    const placedRunner = placements.get(runner.horse);
    if (placedRunner) {
      return placedRunner;
    }

    const zoneIndex = VISUAL_ZONES.findIndex((zone) => zone.key === runner.zone);
    const scale = runnerBaseScale();
    const zoneWidthPercent = 100 / ZONE_COUNT;
    const left = Math.max(zoneIndex, 0) * zoneWidthPercent + laneAnchor(runner.lane) * zoneWidthPercent;
    const bottom = BASE_BOTTOM + laneVerticalBias(runner.lane);

    return {
      runner,
      left,
      bottom,
      scale,
      zIndex: 100 + Math.round(MAX_BOTTOM - bottom),
    };
  });
}

function RunnerSprite({
  runner,
  left,
  bottom,
  scale,
  zIndex,
  active,
  compact,
  onActivate,
}: {
  runner: RaceSpeedMapRunner;
  left: number;
  bottom: number;
  scale: number;
  zIndex: number;
  active: boolean;
  compact: boolean;
  onActivate: (horse: string) => void;
}) {
  const showLabel = !compact || runner.isHighlighted || active;
  const badgeTone = runner.isHighlighted
    ? "border-racing-gold/70 bg-racing-gold/20 text-racing-gold"
    : runner.isTipped
      ? "border-racing-orange/60 bg-racing-orange/15 text-racing-orange"
      : "border-white/15 bg-black/55 text-white/70";
  const markerTone = runner.isHighlighted
    ? "border-racing-gold/55 bg-[linear-gradient(180deg,rgba(255,199,77,0.18),rgba(0,0,0,0.12))] shadow-[0_18px_40px_rgba(255,199,77,0.18)]"
    : active
      ? "border-white/25 bg-[linear-gradient(180deg,rgba(255,255,255,0.14),rgba(0,0,0,0.14))] shadow-[0_16px_32px_rgba(0,0,0,0.35)]"
      : "border-white/12 bg-[linear-gradient(180deg,rgba(255,255,255,0.10),rgba(0,0,0,0.12))] shadow-[0_14px_28px_rgba(0,0,0,0.30)]";
  const badgeRing = runner.isHighlighted ? "border-racing-gold/70" : "border-white/20";

  return (
    <button
      type="button"
      onMouseEnter={() => onActivate(runner.horse)}
      onFocus={() => onActivate(runner.horse)}
      onClick={() => onActivate(runner.horse)}
      className="absolute transition-transform"
      style={{
        left: `${left}%`,
        bottom: `${bottom}px`,
        transform: `translateX(-50%) scale(${scale})`,
        zIndex: runner.isHighlighted ? 320 : active ? 280 : zIndex,
      }}
      aria-label={`${runner.horse} settling ${runner.predictedSettlingPosition}`}
    >
      <div className="relative h-[118px] w-[104px]">
        {runner.isHighlighted ? (
          <div className="absolute inset-x-3 top-3 h-12 rounded-full bg-racing-gold/30 blur-xl" />
        ) : null}
        {active ? (
          <div className="absolute inset-x-4 top-4 h-10 rounded-full bg-white/16 blur-lg" />
        ) : null}

        <div className={`absolute left-1/2 top-3 flex h-[68px] w-[64px] -translate-x-1/2 items-center justify-center rounded-[18px] border ${markerTone} backdrop-blur-[2px]`}>
          {runner.silkUrl ? (
            <img
              src={runner.silkUrl}
              alt=""
              className="h-[58px] w-[52px] object-contain drop-shadow-[0_8px_14px_rgba(0,0,0,0.35)]"
              loading="lazy"
              referrerPolicy="no-referrer"
              draggable={false}
            />
          ) : (
            <svg viewBox="0 0 64 72" className="h-[56px] w-[48px] text-white/60 drop-shadow-[0_8px_14px_rgba(0,0,0,0.35)]">
              <path
                d="M24 7c0-4 3.1-7 8-7s8 3 8 7v3l7 7v12l-6-2v31c0 2.7-2.3 5-5 5H28c-2.7 0-5-2.3-5-5V27l-6 2V17l7-7V7Z"
                fill="currentColor"
              />
            </svg>
          )}
        </div>

        <div className={`absolute right-1 top-1 flex h-8 min-w-8 items-center justify-center rounded-full border-2 ${badgeRing} bg-white/95 px-1.5 text-center text-[12px] font-black text-black shadow-[0_8px_16px_rgba(0,0,0,0.35)]`}>
          {runner.saddleNumber ?? "?"}
        </div>

        {showLabel ? (
          <div className="absolute left-1/2 top-[76px] flex w-full -translate-x-1/2 flex-col items-center gap-1">
            <div className="max-w-[98px] rounded-md border border-white/12 bg-black/82 px-2 py-1 text-center text-[10px] font-semibold leading-tight text-white shadow-lg">
              {runner.horse}
            </div>
            {runner.marketOdds ? (
              <Badge variant="outline" className={`h-5 px-2 text-[10px] ${badgeTone}`}>
                ${runner.marketOdds.toFixed(2)}
              </Badge>
            ) : null}
          </div>
        ) : null}
      </div>
    </button>
  );
}

export function RaceSpeedMapGraphic({
  speedMap,
  activeHorse,
  onActiveHorseChange,
}: {
  speedMap: RaceSpeedMap;
  activeHorse: string | null;
  onActiveHorseChange: (horse: string) => void;
}) {
  const compactLabels = speedMap.fieldSize > 10;
  const mapRef = useRef<HTMLDivElement | null>(null);
  const [layoutWidth, setLayoutWidth] = useState(DEFAULT_LAYOUT_WIDTH);

  useEffect(() => {
    const mapNode = mapRef.current;
    if (!mapNode) {
      return;
    }

    const updateWidth = () => {
      setLayoutWidth(mapNode.clientWidth || DEFAULT_LAYOUT_WIDTH);
    };

    updateWidth();

    if (typeof ResizeObserver === "undefined") {
      return;
    }

    const observer = new ResizeObserver(() => {
      updateWidth();
    });

    observer.observe(mapNode);
    return () => observer.disconnect();
  }, []);

  const positionedRunners = useMemo(() => {
    return resolveRunnerLayout(speedMap.runners, layoutWidth);
  }, [layoutWidth, speedMap.runners]);

  return (
    <div className="rounded-[28px] border border-white/[0.08] bg-[#182718] p-3 shadow-[inset_0_1px_0_rgba(255,255,255,0.04)] sm:p-4">
      <div className="mb-3 flex items-center justify-between gap-3">
        <div className="flex items-center gap-2">
          <Badge variant="outline" className="border-white/12 bg-black/35 text-[10px] uppercase tracking-[0.18em] text-white/55">
            Live Speed Map
          </Badge>
          <Badge variant="outline" className="border-white/12 bg-black/35 text-[10px] uppercase tracking-[0.18em] text-white/55">
            {speedMap.fieldSize} runners
          </Badge>
        </div>
        <div className="text-[10px] uppercase tracking-[0.18em] text-white/40">
          Backmarkers Left • Leaders Right
        </div>
      </div>

      <div className="relative overflow-hidden rounded-[24px] border border-white/[0.08] bg-[linear-gradient(180deg,#6f8567_0%,#5f7d59_20%,#456744_58%,#24401f_100%)]">
        <div className="pointer-events-none absolute inset-0 bg-[radial-gradient(circle_at_top,rgba(255,255,255,0.15),transparent_45%),linear-gradient(180deg,rgba(255,255,255,0.08),rgba(0,0,0,0.08))]" />
        <div className="pointer-events-none absolute left-0 right-0 top-8 h-[2px] bg-white/55" />
        <div className="pointer-events-none absolute left-0 right-0 top-16 h-[2px] bg-white/32" />
        <div className="pointer-events-none absolute left-0 right-0 bottom-16 h-[2px] bg-white/18" />

        <div className="grid grid-cols-6">
          {VISUAL_ZONES.map((zone) => (
            <div key={zone.key} className="relative h-[320px] border-r border-white/10 last:border-r-0">
              <div className={`absolute inset-0 bg-gradient-to-b ${zone.tint}`} />
              <div className="pointer-events-none absolute inset-y-0 right-0 w-px bg-white/12" />
            </div>
          ))}
        </div>

        <div className="pointer-events-none absolute inset-x-0 top-0 flex items-center justify-between px-4 pt-3">
          <div className="rounded-full border border-black/35 bg-black/40 px-2 py-1 text-[10px] font-semibold uppercase tracking-[0.16em] text-white/75">
            Pace {speedMap.tempoLabel}
          </div>
        </div>

        <div ref={mapRef} className="relative h-[320px]">
          {positionedRunners.map(({ runner, left, bottom, scale, zIndex }) => (
            <RunnerSprite
              key={`${runner.horse}-${runner.barrier ?? "?"}`}
              runner={runner}
              left={left}
              bottom={bottom}
              scale={scale}
              zIndex={zIndex}
              active={activeHorse === runner.horse}
              compact={compactLabels}
              onActivate={onActiveHorseChange}
            />
          ))}
        </div>

        <div className="grid grid-cols-6 border-t border-white/10 bg-black/45">
          {VISUAL_ZONES.map((zone) => (
            <div key={zone.key} className="px-2 py-3 text-center">
              <p className={`text-[11px] font-semibold uppercase tracking-[0.16em] ${zone.text}`}>
                {zone.label}
              </p>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}
