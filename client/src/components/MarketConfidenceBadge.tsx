import { Badge } from "@/components/ui/badge";
import { useState } from "react";

interface MarketConfidenceProps {
  confidenceScore: number | null;
  confidenceLabel: "GREEN" | "AMBER" | "RED" | null;
  convergenceTier: "LOCK" | "CONFIRM" | "FLAG" | "CROWD_OVERRIDE" | "SKIP" | null;
  consensusScore?: number | null;
  marketSignalScore?: number | null;
  strideScore?: number | null;
  votePct?: number | null;
  tipsterCount?: number | null;
  consensusInjection?: number | null;
  marketInjection?: number | null;
  compact?: boolean;
}

const tierConfig: Record<string, { label: string; className: string }> = {
  LOCK: {
    label: "LOCK",
    className: "border-emerald-500/30 bg-emerald-500/10 text-emerald-300",
  },
  CONFIRM: {
    label: "CONFIRM",
    className:
      "border-racing-orange/30 bg-racing-orange/10 text-racing-orange",
  },
  FLAG: {
    label: "FLAG",
    className: "border-amber-500/30 bg-amber-500/10 text-amber-300",
  },
  CROWD_OVERRIDE: {
    label: "CROWD",
    className: "border-violet-500/30 bg-violet-500/10 text-violet-300",
  },
  SKIP: {
    label: "SKIP",
    className: "border-white/10 bg-white/[0.04] text-white/60",
  },
};

const confidenceConfig: Record<string, string> = {
  GREEN: "border-emerald-500/30 bg-emerald-500/10 text-emerald-300",
  AMBER: "border-amber-500/30 bg-amber-500/10 text-amber-300",
  RED: "border-red-500/30 bg-red-500/10 text-red-300",
};

export function MarketConfidenceBadge({
  confidenceScore,
  confidenceLabel,
  convergenceTier,
  consensusScore,
  marketSignalScore,
  strideScore,
  votePct,
  tipsterCount,
  consensusInjection,
  marketInjection,
  compact = false,
}: MarketConfidenceProps) {
  const [expanded, setExpanded] = useState(false);

  if (!confidenceLabel && !convergenceTier) return null;

  const tier = convergenceTier
    ? tierConfig[convergenceTier] || tierConfig.SKIP
    : null;
  const confClass = confidenceLabel
    ? confidenceConfig[confidenceLabel] || confidenceConfig.RED
    : null;

  return (
    <div className="inline-flex items-center gap-1.5">
      {tier && (
        <Badge variant="outline" className={`text-[10px] px-1.5 py-0 ${tier.className}`}>
          {tier.label}
        </Badge>
      )}
      {confClass && confidenceScore != null && (
        <Badge
          variant="outline"
          className={`text-[10px] px-1.5 py-0 cursor-pointer ${confClass}`}
          onClick={() => !compact && setExpanded(!expanded)}
        >
          MCS {Math.round(confidenceScore)}
        </Badge>
      )}
      {expanded && !compact && (
        <div className="absolute z-50 mt-16 ml-0 rounded-lg border border-white/10 bg-[#1a1a2e] p-3 shadow-xl text-xs text-white/80 min-w-[200px]">
          <div className="space-y-1">
            {strideScore != null && (
              <div className="flex justify-between">
                <span className="text-white/50">STRIDE</span>
                <span>{strideScore.toFixed(1)}</span>
              </div>
            )}
            {consensusScore != null && (
              <div className="flex justify-between">
                <span className="text-white/50">Consensus</span>
                <span>{consensusScore.toFixed(1)}</span>
              </div>
            )}
            {marketSignalScore != null && (
              <div className="flex justify-between">
                <span className="text-white/50">Market</span>
                <span>{marketSignalScore.toFixed(1)}</span>
              </div>
            )}
            {votePct != null && tipsterCount != null && (
              <div className="flex justify-between">
                <span className="text-white/50">Vote</span>
                <span>
                  {votePct.toFixed(0)}% ({tipsterCount} polled)
                </span>
              </div>
            )}
            {(consensusInjection != null || marketInjection != null) && (
              <div className="flex justify-between border-t border-white/10 pt-1 mt-1">
                <span className="text-white/50">Injection</span>
                <span>
                  {consensusInjection != null && (
                    <span className={consensusInjection > 0 ? "text-emerald-400" : consensusInjection < 0 ? "text-red-400" : ""}>
                      C:{consensusInjection > 0 ? "+" : ""}{consensusInjection.toFixed(1)}
                    </span>
                  )}
                  {marketInjection != null && (
                    <span className={`ml-1 ${marketInjection > 0 ? "text-emerald-400" : marketInjection < 0 ? "text-red-400" : ""}`}>
                      M:{marketInjection > 0 ? "+" : ""}{marketInjection.toFixed(1)}
                    </span>
                  )}
                </span>
              </div>
            )}
          </div>
        </div>
      )}
    </div>
  );
}
