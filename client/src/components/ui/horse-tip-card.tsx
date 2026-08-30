import React from "react";
import { motion, useReducedMotion } from "framer-motion";
import { Badge } from "@/components/ui/badge";
import { Avatar, AvatarImage, AvatarFallback } from "@/components/ui/avatar";
import { Separator } from "@/components/ui/separator";
import { Button } from "@/components/ui/button";
import { Progress } from "@/components/ui/progress";
import {
  Star,
  Clock,
  TrendingUp,
  Activity,
  Brain,
  BarChart3,
  CheckCircle,
  ChevronDown,
  ChevronUp,
  Plus,
  Check,
  MessageSquare,
  Sparkles,
  Layers,
  Loader2,
  Zap,
} from "lucide-react";

// ── Types ────────────────────────────────────────────────────────────────────

export interface HorseTipBet {
  id: string;
  horseName: string;
  horseNumber?: string | number;
  track: string;
  raceNumber: number;
  raceName: string;
  raceDate: string;
  offTime: string;
  distance: string;
  barrier: string;
  jockey: string;
  trainer: string;
  form?: string | null;
  runningStyle?: string;
  marketOdds: number;
  winPercentage: number;
  edge: number;
  betType: string;
  confidenceScore: number;
  rating: number;
  reasons: string[];
  riskLevel: "low" | "medium" | "high";
  suggestedStake: string;
  formAnalystInsights?: string[];
  modelSummary?: string[];
  aiInsight?: string;
  aiScore?: number;
  llmProvider?: string;
  frankingScore?: number;
  frankingElo?: number;
  isAntiFranked?: boolean;
  fitnessRunLabel?: string;
  fitnessIsAtPeakRun?: boolean;
  fitnessPrepTrajectory?: string;
  lucklessFlag?: boolean;
  lucklessScore?: number;
  lucklessExplanation?: string;
  isBestRated?: boolean;
  soleQualifier?: boolean;
  pipelineSignals?: string;
  sectionalEdgeMultiplier?: number;
  sectionalEdgeInsight?: string;
  marketFavourite?: { horseName: string; odds: number; impliedProb: number };
  selectionType?: "bestBet" | "winTip" | "exoticTip";
  exoticType?: "quinella" | "exacta" | "trifecta" | "boxedTrifecta";
  exoticRationale?: string;
  exoticPicks?: {
    banker: { horseName: string; number: string; odds: number; winProb: number };
    fill: { horseName: string; number: string; odds: number; winProb: number }[];
    structure: string;
    estimatedCombinations: number;
    suggestedOutlay: string;
  };
  fieldSize?: number;
  bankerScore?: number;
  bankerStaking?: string;
  shouldBet?: boolean;
  selectionOrigin?: string;
  crowdScore?: number;
  crowdClassification?: string;
  stakeRecommendation?: string;
  independentMentions?: number;
  commercialMentions?: number;
  marketAlignment?: boolean;
  recalibrationApplied?: boolean;
  recalibrationShift?: number;
  sectionalMcEnhanced?: boolean;
  mlModelActive?: boolean;
}

export interface HorseTipCardProps {
  bet: HorseTipBet;
  silkUrl?: string | null;
  isExpanded: boolean;
  onToggleExpand: () => void;
  onAddToSlip: (e: React.MouseEvent) => void;
  onAskStride: () => void;
  isInSlip: boolean;
  slipLoading: boolean;
  strideLoading: boolean;
  strideExplanation?: string;
}

// ── Pure helpers (no hooks) ───────────────────────────────────────────────────

function getPickIdentityInfo(bet: HorseTipBet) {
  if (bet.selectionOrigin === "raw_model_leader")
    return { label: "RAW MODEL LEADER", color: "bg-sky-500/20 text-sky-300 border-sky-500/40" };
  if (bet.selectionOrigin === "filtered_substitute")
    return { label: "NO BET • FILTERED SUB", color: "bg-amber-500/20 text-amber-300 border-amber-500/40" };
  if (bet.selectionOrigin === "market_unavailable")
    return { label: "NO BET • MARKET UNAVAILABLE", color: "bg-slate-500/20 text-slate-300 border-slate-400/40" };
  if (bet.shouldBet === false)
    return { label: "NO BET • GUIDE ONLY", color: "bg-amber-500/20 text-amber-400 border-amber-500/30" };
  return { label: "BET PICK", color: "bg-emerald-500/20 text-emerald-300 border-emerald-500/40" };
}

function getBetTypeInfo(betType: string, edge?: number) {
  const hasEdge = edge != null && edge > 0;
  switch (betType) {
    case "banker":   return { label: "BANKER",  color: "bg-green-500/30 text-green-300 border-green-400/60" };
    case "value":    return { label: "VALUE",   color: "bg-emerald-500/20 text-emerald-400 border-emerald-500/50" };
    case "model_pick": return { label: "MODEL", color: "bg-cyan-500/20 text-cyan-400 border-cyan-500/50" };
    case "roughie":  return { label: "ROUGHIE", color: "bg-purple-500/20 text-purple-400 border-purple-500/50" };
    case "form_pick":  return { label: "FORM",  color: "bg-orange-500/20 text-orange-400 border-orange-500/50" };
    case "tip_only": return { label: "GUIDE",   color: "bg-amber-500/20 text-amber-400 border-amber-500/30" };
    default: return {
      label: hasEdge ? "LIVE" : "WATCH",
      color: hasEdge ? "bg-green-500/20 text-green-400 border-green-500/30" : "bg-amber-500/20 text-amber-400 border-amber-500/30",
    };
  }
}

function getRiskColor(risk: string) {
  if (risk === "low") return "text-green-400";
  if (risk === "high") return "text-red-400";
  return "text-yellow-400";
}

// ── Sub-components ────────────────────────────────────────────────────────────

function AiScoreBadge({ score }: { score?: number }) {
  if (score == null) return null;
  const color =
    score >= 75 ? "bg-green-500/20 text-green-400 border-green-500/30" :
    score >= 50 ? "bg-yellow-500/20 text-yellow-400 border-yellow-500/30" :
    "bg-red-500/20 text-red-400 border-red-500/30";
  return (
    <span className={`text-[10px] font-bold px-1.5 py-0.5 rounded border ${color}`}>
      AI {score}
    </span>
  );
}

function LucklessBadge({ flag, score }: { flag?: boolean; score?: number }) {
  if (!flag || !score) return null;
  return (
    <span className="text-[10px] font-bold px-1.5 py-0.5 rounded border bg-amber-500/20 text-amber-400 border-amber-500/30">
      Luckless {score}
    </span>
  );
}

const AI_SECTIONS = ["THE FORM", "THE RUN", "THE EDGE", "THE VERDICT", "THE RISK"];

function AiInsightDisplay({ insight, aiScore, llmProvider }: {
  insight: string; aiScore?: number; llmProvider?: string;
}) {
  const sections: { header: string; body: string }[] = [];
  let currentSection = "";
  let currentBody: string[] = [];
  const lines = insight.replace(/\*+/g, "").split("\n");
  for (const line of lines) {
    const trimmed = line.trim();
    if (!trimmed) continue;
    const matchedHeader = AI_SECTIONS.find(
      (h) => trimmed.toUpperCase().startsWith(h + ":") || trimmed.toUpperCase().startsWith(h + " ")
    );
    if (matchedHeader) {
      if (currentSection && currentBody.length > 0)
        sections.push({ header: currentSection, body: currentBody.join(" ") });
      currentSection = matchedHeader;
      const rest = trimmed.substring(matchedHeader.length).replace(/^[:\s]+/, "").trim();
      currentBody = rest ? [rest] : [];
    } else {
      currentBody.push(trimmed);
    }
  }
  if (currentSection && currentBody.length > 0)
    sections.push({ header: currentSection, body: currentBody.join(" ") });

  const hasSections = sections.length > 0;
  const plainText = hasSections ? null : insight.replace(/\*+/g, "");
  const scoreBg =
    aiScore != null
      ? aiScore >= 75 ? "bg-green-500/20 border-green-500/30" :
        aiScore >= 50 ? "bg-yellow-500/20 border-yellow-500/30" :
        "bg-red-500/20 border-red-500/30"
      : "";
  const scoreColor =
    aiScore != null
      ? aiScore >= 75 ? "text-green-400" : aiScore >= 50 ? "text-yellow-400" : "text-red-400"
      : "";

  return (
    <div className="bg-racing-gold/5 border border-racing-gold/20 rounded-lg overflow-hidden">
      <div className="flex items-center justify-between px-3 py-2 border-b border-racing-gold/10">
        <div className="flex items-center gap-2">
          <Sparkles className="h-3.5 w-3.5 text-racing-gold" />
          <span className="text-xs font-semibold text-racing-gold uppercase tracking-wide">Form Analysis</span>
          {llmProvider && <span className="text-[10px] text-white/30 font-mono">AI</span>}
        </div>
        {aiScore != null && (
          <span className={`text-xs font-bold px-2 py-0.5 rounded border ${scoreBg} ${scoreColor}`}>
            {aiScore}/100
          </span>
        )}
      </div>
      <div className="px-3 py-2 space-y-2">
        {hasSections
          ? sections.map((s, i) => (
              <div key={i}>
                <p className="text-[10px] font-bold text-racing-gold/70 uppercase tracking-wider mb-0.5">{s.header}</p>
                <p className="text-xs text-foreground/85 leading-relaxed">{s.body}</p>
              </div>
            ))
          : <p className="text-xs text-foreground/85 leading-relaxed">{plainText}</p>}
      </div>
    </div>
  );
}

// ── Main component ────────────────────────────────────────────────────────────

export function HorseTipCard({
  bet,
  silkUrl,
  isExpanded,
  onToggleExpand,
  onAddToSlip,
  onAskStride,
  isInSlip,
  slipLoading,
  strideLoading,
  strideExplanation,
}: HorseTipCardProps) {
  const shouldReduceMotion = useReducedMotion();
  const shouldAnimate = !shouldReduceMotion;

  const pickIdentity = getPickIdentityInfo(bet);
  const betTypeInfo = getBetTypeInfo(bet.betType, bet.edge);
  const isExotic = bet.selectionType === "exoticTip";

  const raceTime = new Date(bet.offTime).toLocaleTimeString("en-AU", {
    hour: "2-digit",
    minute: "2-digit",
  });

  // ── Animation variants (same language as PredictionMarketCard) ──
  const containerVariants = {
    hidden: { opacity: 0, y: 20, scale: 0.96, filter: "blur(4px)" },
    visible: {
      opacity: 1, y: 0, scale: 1, filter: "blur(0px)",
      transition: { type: "spring", stiffness: 300, damping: 30, mass: 0.8, staggerChildren: 0.06, delayChildren: 0.05 },
    },
  };
  const itemVariants = {
    hidden: { opacity: 0, x: -12, scale: 0.96, filter: "blur(2px)" },
    visible: {
      opacity: 1, x: 0, scale: 1, filter: "blur(0px)",
      transition: { type: "spring", stiffness: 400, damping: 28, mass: 0.6 },
    },
  };
  const badgeVariants = {
    hidden: { scale: 0, opacity: 0, filter: "blur(2px)" },
    visible: {
      scale: 1, opacity: 1, filter: "blur(0px)",
      transition: { type: "spring", stiffness: 500, damping: 25, mass: 0.5 },
    },
  };
  const progressVariants = {
    hidden: { scaleX: 0, opacity: 0 },
    visible: {
      scaleX: 1, opacity: 1,
      transition: { type: "spring", stiffness: 300, damping: 30, delay: 0.35 },
    },
  };

  return (
    <motion.div
      className={`w-full rounded-lg overflow-hidden cursor-pointer ${
        isExotic
          ? "glass-card border-0 border-l-2 border-l-purple-500/50"
          : bet.betType === "banker"
          ? "glass-card border-0 border-l-2 border-l-green-500/70 shadow-[0_0_12px_rgba(34,197,94,0.15)]"
          : "glass-card border-0"
      }`}
      initial={shouldAnimate ? "hidden" : "visible"}
      animate="visible"
      variants={shouldAnimate ? containerVariants : {}}
      onClick={onToggleExpand}
      data-testid={`card-best-bet-${bet.id}`}
    >
      <div className="p-3 space-y-3">

        {/* ── Row 1: Badges + Odds ── */}
        <motion.div
          className="flex items-center justify-between gap-2 flex-wrap"
          variants={shouldAnimate ? itemVariants : {}}
        >
          <div className="flex items-center gap-1.5 flex-wrap">
            {isExotic ? (
              <motion.div variants={shouldAnimate ? badgeVariants : {}}>
                <Badge className="bg-purple-500/20 text-purple-400 border-purple-500/50 border text-xs gap-1">
                  <Layers className="h-3 w-3" />
                  {bet.exoticType === "quinella" ? "QUINELLA" :
                   bet.exoticType === "exacta" ? "EXACTA" :
                   bet.exoticType === "boxedTrifecta" ? "BOX TRIFECTA" : "TRIFECTA"}
                </Badge>
              </motion.div>
            ) : (
              <>
                <motion.div variants={shouldAnimate ? badgeVariants : {}}>
                  <Badge className={`${pickIdentity.color} border text-xs`}>
                    {pickIdentity.label}
                  </Badge>
                </motion.div>
                {bet.shouldBet !== false && (
                  <motion.div variants={shouldAnimate ? badgeVariants : {}}>
                    <Badge className={`${betTypeInfo.color} border text-xs`}>
                      {betTypeInfo.label}
                    </Badge>
                  </motion.div>
                )}
                {bet.isBestRated && (
                  <motion.div variants={shouldAnimate ? badgeVariants : {}}>
                    <Badge className="bg-blue-500/20 text-blue-400 border-blue-500/30 border text-xs">
                      BEST RATED
                    </Badge>
                  </motion.div>
                )}
              </>
            )}
            {bet.betType === "banker" && bet.bankerScore != null && (
              <motion.div variants={shouldAnimate ? badgeVariants : {}}>
                <Badge variant="outline" className="text-[10px] border-green-500/30 text-green-400 no-default-hover-elevate no-default-active-elevate">
                  Score: {bet.bankerScore}
                </Badge>
              </motion.div>
            )}
            {bet.soleQualifier && (
              <motion.div variants={shouldAnimate ? badgeVariants : {}}>
                <Badge className="bg-amber-500/20 text-amber-400 border-amber-500/30 border text-xs">
                  ONLY VALUE PICK
                </Badge>
              </motion.div>
            )}
            <div className="flex gap-0.5">
              {Array.from({ length: 5 }).map((_, i) => (
                <Star key={i} className={`h-3 w-3 ${i < bet.rating ? "text-yellow-400 fill-yellow-400" : "text-white/20"}`} />
              ))}
            </div>
            <AiScoreBadge score={bet.aiScore} />
            <LucklessBadge flag={bet.lucklessFlag} score={bet.lucklessScore} />
          </div>

          {/* Odds */}
          <motion.div variants={shouldAnimate ? badgeVariants : {}} className="text-right shrink-0">
            {isExotic ? (
              <>
                <p className="text-lg font-bold text-purple-400">{bet.fieldSize || "?"} runners</p>
                <p className="text-xs text-white/40">Win {(bet.winPercentage ?? 0).toFixed(0)}%</p>
              </>
            ) : (
              <>
                <p className={`text-lg font-bold ${!bet.marketOdds ? "text-white/30" : bet.isBestRated ? "text-blue-400" : "text-green-400"}`}>
                  {bet.marketOdds ? `$${bet.marketOdds.toFixed(2)}` : "TBC"}
                </p>
                <p className="text-xs text-white/40">Win {(bet.winPercentage ?? 0).toFixed(0)}%</p>
              </>
            )}
          </motion.div>
        </motion.div>

        {/* ── Row 2: Silk + Horse Name ── */}
        <motion.div
          className="flex items-start gap-3 pt-1"
          variants={shouldAnimate ? itemVariants : {}}
        >
          {/* Horse silk — mimics the team logo Avatar in PredictionMarketCard */}
          <motion.div variants={shouldAnimate ? badgeVariants : {}} className="shrink-0">
            <Avatar className="w-12 h-12 rounded-xl border border-white/10 bg-black/30">
              {silkUrl ? (
                <AvatarImage
                  src={silkUrl}
                  alt={bet.horseName}
                  className="object-contain p-0.5"
                  referrerPolicy="no-referrer"
                />
              ) : null}
              <AvatarFallback className="rounded-xl bg-white/5">
                {/* Fallback jockey-silks SVG */}
                <svg viewBox="0 0 64 72" className="h-8 w-7 text-white/50">
                  <path
                    d="M24 7c0-4 3.1-7 8-7s8 3 8 7v3l7 7v12l-6-2v31c0 2.7-2.3 5-5 5H28c-2.7 0-5-2.3-5-5V27l-6 2V17l7-7V7Z"
                    fill="currentColor"
                  />
                </svg>
              </AvatarFallback>
            </Avatar>
          </motion.div>

          <motion.div className="flex-1 min-w-0" variants={shouldAnimate ? itemVariants : {}}>
            <h3 className="font-bold text-white truncate leading-tight">{bet.horseName}</h3>
            <p className="text-sm text-white/40">{bet.track} R{bet.raceNumber}</p>
          </motion.div>
        </motion.div>

        {/* ── Gradient separator ── */}
        <motion.div className="relative" variants={shouldAnimate ? itemVariants : {}}>
          <Separator className="bg-gradient-to-r from-transparent via-white/15 to-transparent" />
        </motion.div>

        {/* ── Row 3: Confidence / Win% / Edge ── */}
        <motion.div
          className="flex justify-between items-center"
          variants={shouldAnimate ? itemVariants : {}}
        >
          <div className="text-left">
            <p className="text-xs text-white/40 uppercase tracking-wide">Confidence</p>
            <p className="text-xl font-bold text-yellow-400">{bet.confidenceScore.toFixed(0)}/100</p>
          </div>
          <div className="text-left">
            <p className="text-xs text-white/40 uppercase tracking-wide">Win%</p>
            <p className="text-xl font-bold text-emerald-400">{(bet.winPercentage ?? 0).toFixed(0)}%</p>
          </div>
          <div className="text-left">
            <p className="text-xs text-white/40 uppercase tracking-wide">Edge</p>
            <p className={`text-xl font-bold ${(bet.edge ?? 0) > 0 ? "text-emerald-400" : "text-rose-400"}`}>
              {(bet.edge ?? 0) > 0 ? "+" : ""}{(bet.edge ?? 0).toFixed(1)}%
            </p>
          </div>
        </motion.div>

        {/* ── Gradient separator ── */}
        <motion.div className="relative" variants={shouldAnimate ? itemVariants : {}}>
          <Separator className="bg-gradient-to-r from-transparent via-white/15 to-transparent" />
        </motion.div>

        {/* ── Row 4: Crowd signal or model-only warning ── */}
        <motion.div className="space-y-1.5" variants={shouldAnimate ? itemVariants : {}}>
          {bet.crowdClassification &&
           bet.crowdClassification !== "MODEL_ONLY" &&
           bet.crowdClassification !== "REJECTED" ? (
            <div className={`flex items-center gap-2 px-3 py-1.5 rounded-lg text-xs font-medium ${
              bet.crowdClassification === "CONFIRMED"
                ? "bg-emerald-500/20 border border-emerald-500/30 text-emerald-300"
                : bet.crowdClassification === "CROWD_ONLY"
                ? "bg-blue-500/20 border border-blue-500/30 text-blue-300"
                : "bg-amber-500/20 border border-amber-500/30 text-amber-300"
            }`}>
              <span className="font-semibold">
                {bet.crowdClassification === "CONFIRMED" ? "CROWD + MODEL" : "CROWD PICK"}
              </span>
              <span className="opacity-70">{bet.crowdScore?.toFixed(0)}% of sources</span>
              {((bet.independentMentions ?? 0) > 0 || (bet.commercialMentions ?? 0) > 0) && (
                <span className="opacity-50 ml-auto">
                  {bet.independentMentions || 0} ind · {bet.commercialMentions || 0} com
                </span>
              )}
              {bet.marketAlignment && (
                <span className="text-emerald-400 ml-1" title="Market also backing this horse">+mkt</span>
              )}
            </div>
          ) : (
            <div className="text-xs text-amber-400/70 flex items-center gap-1">
              <span>&#9888;</span>
              <span>Model only — no crowd confirmation</span>
            </div>
          )}

          {/* Stake recommendation */}
          {bet.stakeRecommendation && bet.stakeRecommendation !== "NONE" && (
            <div className={`inline-flex items-center px-2 py-0.5 rounded text-xs font-medium ${
              bet.stakeRecommendation === "FULL"
                ? "bg-emerald-500/20 text-emerald-300"
                : bet.stakeRecommendation === "STANDARD"
                ? "bg-blue-500/20 text-blue-300"
                : "bg-amber-500/20 text-amber-300"
            }`}>
              {bet.stakeRecommendation} STAKE
            </div>
          )}

          {/* Market fav */}
          {!isExotic && bet.marketFavourite && (
            <p className="text-xs text-white/30 flex items-center gap-1">
              <TrendingUp className="h-3 w-3 text-white/20" />
              Market fav: {bet.marketFavourite.horseName} ${bet.marketFavourite.odds.toFixed(2)}
            </p>
          )}
        </motion.div>

        {/* ── Row 5: Key info row ── */}
        <motion.div
          className="flex items-center gap-3 text-xs text-white/40"
          variants={shouldAnimate ? itemVariants : {}}
        >
          <span className="flex items-center gap-1">
            <Clock className="h-3 w-3" />
            {raceTime}
          </span>
          <span>{bet.distance}</span>
          <span className={getRiskColor(bet.riskLevel)}>{bet.riskLevel.toUpperCase()} RISK</span>
        </motion.div>

        {/* ── Pipeline signals ── */}
        {bet.pipelineSignals && (
          <motion.p
            className="text-xs text-white/30 font-mono tracking-wide"
            variants={shouldAnimate ? itemVariants : {}}
            data-testid={`pipeline-signals-${bet.id}`}
          >
            {bet.pipelineSignals}
          </motion.p>
        )}

        {/* ── Sectional edge insight ── */}
        {bet.sectionalEdgeInsight && (
          <motion.p
            className={`text-xs flex items-center gap-1 ${
              bet.sectionalEdgeMultiplier && bet.sectionalEdgeMultiplier > 1
                ? "text-emerald-400/70"
                : "text-amber-400/60"
            }`}
            variants={shouldAnimate ? itemVariants : {}}
            data-testid={`sectional-insight-${bet.id}`}
          >
            <Activity className="h-3 w-3" />
            {bet.sectionalEdgeInsight}
          </motion.p>
        )}

        {/* ── Confidence bar (replaces the timer-progress bar) ── */}
        <motion.div
          className="space-y-1"
          variants={shouldAnimate ? progressVariants : {}}
          style={{ transformOrigin: "left" }}
        >
          <Progress value={bet.confidenceScore} className="h-2" />
        </motion.div>

        {/* ── Expanded analysis ── */}
        {isExpanded && (
          <div
            className="pt-3 border-t border-white/[0.06] space-y-3 animate-in slide-in-from-top-2"
            onClick={(e) => e.stopPropagation()}
          >
            {/* Exotic rationale */}
            {isExotic && bet.exoticRationale && (
              <div className="p-2 rounded bg-purple-500/10 border border-purple-500/20">
                <p className="text-xs text-purple-300">{bet.exoticRationale}</p>
                {bet.exoticPicks && (
                  <p className="text-xs text-white/40 mt-1 font-mono">{bet.exoticPicks.structure}</p>
                )}
              </div>
            )}

            {/* Franking */}
            {bet.frankingScore != null && bet.frankingScore > 0 && (
              <div className="flex items-center gap-1.5">
                <div className={`w-2 h-2 rounded-full ${
                  bet.isAntiFranked ? "bg-red-500" :
                  bet.frankingScore >= 60 ? "bg-green-500" :
                  bet.frankingScore >= 50 ? "bg-yellow-500" : "bg-gray-500"
                }`} />
                <span className="text-xs text-white/60">
                  {bet.isAntiFranked ? "Anti-Franked" :
                   bet.frankingScore >= 60 ? `Franked ${bet.frankingScore.toFixed(0)}` :
                   bet.frankingScore >= 50 ? `Form OK ${bet.frankingScore.toFixed(0)}` :
                   `Weak Form ${bet.frankingScore.toFixed(0)}`}
                </span>
                {bet.frankingElo != null && bet.frankingElo >= 75 && (
                  <span className="text-xs text-orange-400/80">ELO {bet.frankingElo.toFixed(0)}</span>
                )}
              </div>
            )}

            {/* Fitness */}
            {bet.fitnessRunLabel && (
              <div className="flex items-center gap-1.5">
                <Activity className="h-3 w-3 text-cyan-400" />
                <span className="text-xs text-white/60">{bet.fitnessRunLabel}</span>
                {bet.fitnessIsAtPeakRun && (
                  <Badge variant="outline" className="text-[10px] px-1.5 py-0 h-4 border-green-500/40 text-green-400 no-default-hover-elevate no-default-active-elevate">
                    Peak
                  </Badge>
                )}
                {bet.fitnessPrepTrajectory && (
                  <span className={`text-xs ${
                    ["improving","peaking"].includes((bet.fitnessPrepTrajectory).toLowerCase()) ? "text-green-400" :
                    bet.fitnessPrepTrajectory.toLowerCase() === "stable" ? "text-yellow-400" :
                    bet.fitnessPrepTrajectory.toLowerCase() === "declining" ? "text-red-400" :
                    "text-white/40"
                  }`}>
                    {["improving","peaking"].includes(bet.fitnessPrepTrajectory.toLowerCase()) ? (
                      <span className="flex items-center gap-0.5"><TrendingUp className="h-3 w-3" />{bet.fitnessPrepTrajectory}</span>
                    ) : bet.fitnessPrepTrajectory.toLowerCase() === "declining" ? (
                      <span className="flex items-center gap-0.5"><ChevronDown className="h-3 w-3" />{bet.fitnessPrepTrajectory}</span>
                    ) : (
                      <span>{bet.fitnessPrepTrajectory}</span>
                    )}
                  </span>
                )}
              </div>
            )}

            {/* Data quality badges */}
            {(bet.recalibrationApplied || bet.sectionalMcEnhanced || bet.mlModelActive) && (
              <div className="flex flex-wrap items-center gap-1.5">
                {bet.recalibrationApplied && (
                  <Badge variant="outline" className="text-[10px] border-blue-500/30 text-blue-400 gap-1">
                    <Activity className="h-2.5 w-2.5" />
                    RECALIBRATED{" "}
                    {bet.recalibrationShift != null && bet.recalibrationShift !== 0 && (
                      <span className={bet.recalibrationShift > 0 ? "text-green-400" : "text-red-400"}>
                        {bet.recalibrationShift > 0 ? "+" : ""}{bet.recalibrationShift.toFixed(1)}pp
                      </span>
                    )}
                  </Badge>
                )}
                {bet.sectionalMcEnhanced && (
                  <Badge variant="outline" className="text-[10px] border-purple-500/30 text-purple-400 gap-1">
                    <Zap className="h-2.5 w-2.5" />SECTIONAL MC
                  </Badge>
                )}
                {bet.mlModelActive && (
                  <Badge variant="outline" className="text-[10px] border-orange-500/30 text-orange-400 gap-1">
                    <Brain className="h-2.5 w-2.5" />ML ENSEMBLE
                  </Badge>
                )}
              </div>
            )}

            {/* Model summary */}
            {bet.modelSummary && bet.modelSummary.length > 0 && (
              <div className="bg-white/[0.03] rounded p-2">
                <p className="text-xs text-white/40 mb-1 flex items-center gap-1">
                  <BarChart3 className="h-3 w-3 text-blue-400" />MODEL ANALYSIS
                </p>
                <div className="space-y-0.5">
                  {bet.modelSummary.slice(0, 3).map((point, i) => (
                    <p key={i} className="text-xs text-blue-400/90 flex items-start gap-2">
                      <span className="text-blue-400 mt-0.5">•</span>{point}
                    </p>
                  ))}
                </div>
              </div>
            )}

            {/* Form analyst */}
            <div>
              <p className="text-xs text-white/40 mb-2 flex items-center gap-1">
                <CheckCircle className="h-3 w-3 text-green-400" />FORM ANALYST VIEW
              </p>
              <div className="space-y-1">
                {(bet.formAnalystInsights || bet.reasons).map((insight, i) => (
                  <p key={i} className="text-xs text-green-400/90 flex items-start gap-2">
                    <span className="text-green-400 mt-0.5">•</span>{insight}
                  </p>
                ))}
              </div>
            </div>

            {/* AI insight */}
            {bet.aiInsight && (
              <AiInsightDisplay
                insight={bet.aiInsight}
                aiScore={bet.aiScore}
                llmProvider={bet.llmProvider}
              />
            )}

            {/* Luckless excuse */}
            {bet.lucklessFlag && bet.lucklessExplanation && (
              <div className="p-3 bg-amber-500/10 border border-amber-500/30 rounded-lg">
                <p className="text-amber-400 font-semibold text-sm mb-1">Last Start Excuse</p>
                <p className="text-sm text-amber-200/80">{bet.lucklessExplanation}</p>
              </div>
            )}

            {/* Key details grid */}
            <div className="grid grid-cols-2 gap-2 text-xs">
              <div><p className="text-white/40">Form</p><p className="font-mono">{bet.form || "N/A"}</p></div>
              <div><p className="text-white/40">Barrier</p><p>{bet.barrier}</p></div>
              <div><p className="text-white/40">Jockey</p><p className="truncate">{bet.jockey}</p></div>
              <div><p className="text-white/40">Trainer</p><p className="truncate">{bet.trainer}</p></div>
            </div>

            <div className="bg-white/[0.03] rounded p-2">
              <p className="text-xs text-white/40">Suggested Stake</p>
              <p className="text-sm font-medium">{bet.suggestedStake}</p>
            </div>

            {bet.betType === "banker" && bet.bankerStaking && (
              <div className="bg-green-500/10 border border-green-500/20 rounded p-2">
                <p className="text-xs text-green-400 font-medium">Banker Staking</p>
                <p className="text-sm text-green-300">{bet.bankerStaking}</p>
              </div>
            )}

            {/* Ask Stride */}
            <Button
              size="sm"
              variant="outline"
              className="w-full gap-2 border-racing-gold/50 text-racing-gold hover:bg-racing-gold/10"
              onClick={(e) => { e.stopPropagation(); onAskStride(); }}
              disabled={strideLoading}
              data-testid={`button-ask-stride-best-${bet.id}`}
            >
              {strideLoading ? (
                <><Loader2 className="h-4 w-4 animate-spin" />Asking Stride...</>
              ) : strideExplanation ? (
                <><Sparkles className="h-4 w-4" />Stride Says</>
              ) : (
                <><MessageSquare className="h-4 w-4" />Ask Stride</>
              )}
            </Button>

            {strideExplanation && (
              <div className="bg-racing-gold/10 border border-racing-gold/30 rounded-md p-3">
                <div className="flex items-start gap-2">
                  <Sparkles className="h-4 w-4 text-racing-gold mt-0.5 shrink-0" />
                  <p className="text-sm text-foreground leading-relaxed">{strideExplanation}</p>
                </div>
              </div>
            )}

            {/* Add to slip */}
            <Button
              size="sm"
              className={`w-full gap-2 ${isInSlip ? "bg-green-600" : "bg-racing-orange text-black"}`}
              disabled={slipLoading || isInSlip}
              onClick={(e) => { e.stopPropagation(); onAddToSlip(e); }}
              data-testid={`button-add-slip-best-${bet.id}`}
            >
              {isInSlip ? (
                <><Check className="h-4 w-4" />In Slip</>
              ) : isExotic ? (
                <><Layers className="h-4 w-4" />Add Exotic</>
              ) : (
                <><Plus className="h-4 w-4" />Add to Slip</>
              )}
            </Button>
          </div>
        )}

        {/* ── Expand / collapse toggle ── */}
        <div className="flex justify-center pt-1" data-testid={`button-expand-${bet.id}`}>
          {isExpanded ? (
            <ChevronUp className="h-4 w-4 text-white/20" />
          ) : (
            <ChevronDown className="h-4 w-4 text-white/20" />
          )}
        </div>
      </div>

      {/* ── Bottom confidence bar (same position as PredictionMarketCard timer bar) ── */}
      <motion.div
        className="px-0"
        variants={shouldAnimate ? progressVariants : {}}
        style={{ transformOrigin: "left" }}
      >
        <div className="h-1 bg-white/5 overflow-hidden">
          <div
            className="h-full bg-gradient-to-r from-yellow-500 to-orange-500 transition-all duration-700 ease-out"
            style={{ width: `${bet.confidenceScore}%` }}
          />
        </div>
      </motion.div>
    </motion.div>
  );
}
