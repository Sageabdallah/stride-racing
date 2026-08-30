import { useState, useMemo, useEffect } from "react";
import { Link, useLocation } from "wouter";
import { ArrowLeft, Search, RefreshCw, TrendingUp, DollarSign, Target, Gauge, Activity, ChevronDown, ChevronUp, Filter, X, Calendar, MapPin, Zap, BarChart3, Clock, SlidersHorizontal, Trophy, Star, CheckCircle, AlertTriangle, Loader2, Plus, Check, MessageSquare, Sparkles, Crown, Timer, Flame, ChevronRight, Brain, Layers, Flag, BookMarked, Eye } from "lucide-react";
import { apiRequest } from "@/lib/queryClient";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader } from "@/components/ui/card";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Badge } from "@/components/ui/badge";
import { MarketConfidenceBadge } from "@/components/MarketConfidenceBadge";
import { Skeleton } from "@/components/ui/skeleton";
import { Progress } from "@/components/ui/progress";
import { useQuery, useMutation } from "@tanstack/react-query";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import { Input } from "@/components/ui/input";
import { Slider } from "@/components/ui/slider";
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { useBetSlip } from "@/lib/betSlipContext";
import { BetSlip } from "@/components/BetSlip";
import { RaceShapeBlock } from "@/components/RaceShapeBlock";
import { useRunnerAnalyses } from "@/hooks/useRunnerAnalyses";
import { RaceOverviewPanel } from "@/components/RaceOverviewPanel";
import { useRaceOverview } from "@/hooks/useRaceOverview";
import { BlackbookView } from "@/components/BlackbookView";
import type { RaceSpeedMap } from "@/types/speedMap";
import { HorseTipCard } from "@/components/ui/horse-tip-card";

interface Selection {
  id: string;
  track: string;
  raceNumber: number;
  raceName: string;
  raceDate: string;
  offTime: string;
  distance: string;
  horseName: string;
  horseNumber: string;
  barrier: string;
  jockey: string;
  trainer: string;
  form: string;
  winPercentage: number;
  placePercentage: number;
  modelProbability: number;
  impliedOdds: number;
  marketOdds: number;
  expectedValue: number;
  edge: number;
  kellyStake: number;
  ciLower: number;
  ciUpper: number;
  expectedPosition: number;
  positionStdDev: number;
  stabilityScore: number;
  runningStyle: string;
  paceSplits: Record<string, number>;
  confidence: string;
  valueRating: string;
  isActive: boolean;
  // Enhanced features
  enhancedFactor?: number;
  barrierBiasFactor?: number;
  barrierBiasDesc?: string;
  classMovementFactor?: number;
  classMovementDesc?: string;
  headToHeadFactor?: number;
  headToHeadDesc?: string;
  enhancedExplanations?: string;
  speedRating?: number;
  paceScore?: number;
  expectedPaceAdvantage?: number;
  // Track bias points
  trackBiasPoints?: number;
  trackBiasFit?: string;
  trackBiasBarrierPts?: number;
  trackBiasPacePts?: number;
  trackBiasJockeyPts?: number;
  trackBiasTrainerPts?: number;
  trackBiasSummary?: string;
  // AI Analyst insights
  aiInsight?: string;
  aiInsightGeneratedAt?: string;
  aiScore?: number;
  aiReasoningJson?: any;
  llmProvider?: string;
  frankingElo?: number;
  frankingScore?: number;
  frankingConfidence?: number;
  isAntiFranked?: boolean;
  fieldStrengthAvg?: number;
  formQualityTrend?: number;
  bestAdjustedMargin?: number;
  collateralAdvantage?: number;
  fitnessRunsThisPrep?: number;
  fitnessRunLabel?: string;
  fitnessIsAtPeakRun?: boolean;
  fitnessReadinessScore?: number;
  fitnessPrepTrajectory?: string;
  fitnessDescription?: string;
  fitnessData?: any;
  // Luckless last-start analysis
  lucklessFlag?: boolean;
  lucklessScore?: number;
  lucklessUplift?: number;
  lucklessExplanation?: string;
  lucklessJson?: any;
}

interface SelectionsResponse {
  total: number;
  selections: Selection[];
}

interface BestBet {
  id: string;
  horseName: string;
  horseNumber?: string | number;
  track: string;
  raceNumber: number;
  raceId?: string;
  raceName: string;
  raceDate: string;
  offTime: string;
  distance: string;
  barrier: string;
  jockey: string;
  trainer: string;
  form: string | null;
  runningStyle?: string;
  speedMapping?: any;
  marketOdds: number;
  winPercentage: number;
  edge: number;
  betType: "banker" | "best_chance" | "value" | "roughie" | "form_pick" | "model_pick" | "tip_only";
  tipWarning?: string;
  confidenceScore: number;
  rating: number;
  reasons: string[];
  riskLevel: "low" | "medium" | "high";
  suggestedStake: string;
  formAnalystInsights?: string[];
  modelSummary?: string[];
  aiInsight?: string;
  aiScore?: number;
  aiReasoningJson?: {
    ai_analysis?: string;
    ai_key_edge?: string;
    ai_risk_factors?: string[];
    ai_vs_field?: string;
  };
  llmProvider?: string;
  frankingElo?: number;
  frankingScore?: number;
  frankingConfidence?: number;
  isAntiFranked?: boolean;
  fieldStrengthAvg?: number;
  formQualityTrend?: number;
  bestAdjustedMargin?: number;
  collateralAdvantage?: number;
  fitnessRunsThisPrep?: number;
  fitnessRunLabel?: string;
  fitnessIsAtPeakRun?: boolean;
  fitnessReadinessScore?: number;
  fitnessPrepTrajectory?: string;
  fitnessDescription?: string;
  fitnessData?: any;
  fairOdds?: number;
  openingOdds?: number | null;
  marketMovement?: string;
  marketRank?: number | null;
  recalibrationApplied?: boolean;
  recalibrationShift?: number;
  rawWinProb?: number;
  calibratedWinProb?: number;
  sectionalMcEnhanced?: boolean;
  sectionalMcWinProb?: number;
  paceScenarioJson?: any;
  mlModelActive?: boolean;
  mlAdjustmentBreakdown?: any;
  lucklessFlag?: boolean;
  lucklessScore?: number;
  lucklessUplift?: number;
  lucklessExplanation?: string;
  lucklessJson?: any;
  isBestRated?: boolean;
  soleQualifier?: boolean;
  pipelineSignals?: string;
  sectionalEdgeMultiplier?: number;
  sectionalEdgeInsight?: string;
  marketFavourite?: {
    horseName: string;
    odds: number;
    impliedProb: number;
  };
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
  raceCompetitiveness?: number;
  bankerFlag?: boolean;
  bankerTier?: 'strong_banker' | 'banker';
  bankerScore?: number;
  bankerScoreComponents?: any;
  bankerStaking?: string;
  bankerExoticCombos?: { type: string; anchor: string; withRunners: string[]; stake: number }[];
  selectionOrigin?: string;
  selectionOriginReason?: string;
  matchesModelLeader?: boolean;
  modelLeaderHorse?: string;
  shouldBet?: boolean;
  // Crowd-first V3 fields
  crowdScore?: number;
  crowdClassification?: string;
  crowdGateReason?: string;
  stakeRecommendation?: string;
  independentMentions?: number;
  commercialMentions?: number;
  marketAlignment?: boolean;
}

interface BestBetsResponse {
  success: boolean;
  bestPerTrack: BestBet[];
  bestBets: BestBet[];
  tips: BestBet[];
  oneBetPerRace: BestBet[];
  coveragePerRace: BestBet[];
  topBets: BestBet[];
  selectedDate?: string;
  availableDates: string[];
  summary: {
    totalAnalyzed: number;
    totalRaces: number;
    tracksWithBets: number;
    meetsAnalyzed?: number;
    avgConfidence: number;
    tipCount?: number;
    betTypeBreakdown: Record<string, number>;
  };
  generatedAt: string;
}

interface SimulationRaceRunner {
  horse_id?: string;
  horse: string;
  number?: string;
  barrier?: string;
  draw?: string;
  jockey?: string;
  trainer?: string;
  weight?: string;
  form?: string;
  odds?: { bookmaker?: string; win_odds?: string; place_odds?: string }[];
}

interface SimulationRace {
  race_id?: string;
  race_number: string;
  race_name?: string;
  distance?: string;
  class?: string;
  going?: string;
  off_time?: string;
  runnerCount?: number;
  runners: SimulationRaceRunner[];
}

interface SimulationMeet {
  date?: string;
  course: string;
  meet_id?: string;
  races: SimulationRace[];
}

interface FullFieldRunner {
  horse: string;
  saddle_number: number;
  barrier: number;
  jockey: string;
  trainer: string;
  odds: number;
  win_pct: number;
  place_pct: number;
  raw_model_pct: number;
  edge_pct: number;
  selection_score: number;
  form: string;
  running_style: string;
  weighted_form_score: number;
  days_since_run: number;
  is_first_up: boolean;
  is_improving: boolean;
  distance_strike_rate: number;
  course_strike_rate: number;
  is_tipped: boolean;
  tip_rank: number | null;
  confidence: string;
  horse_id?: string | null;
  silk_url?: string | null;
  colour?: string | null;
  sex?: string | null;
  age?: string | null;
  comment?: string | null;
  ai_insight?: string;
  ai_score?: number;
  brief_assessment?: string;
  // Crowd-first V3
  crowd_score?: number;
  crowd_classification?: string;
  independent_mentions?: number;
  commercial_mentions?: number;
  total_mentions?: number;
}

interface RaceFieldData {
  track: string;
  race_number: number;
  race_name: string;
  distance: string;
  going: string;
  race_class: string;
  field_size: number;
  full_field: FullFieldRunner[];
  speedMap?: RaceSpeedMap | null;
}

interface RaceFieldResponse {
  success: boolean;
  date: string;
  races: RaceFieldData[];
}

interface SimulationRacesResponse {
  total?: number;
  dataSource?: string;
  currentDate?: string;
  meets: SimulationMeet[];
}

interface BetsResponse {
  bets: Selection[];
  dataSource: string;
  metadata?: {
    preComputed?: boolean;
    date?: string;
    selectedDate?: string;
    availableDates?: string[];
    sourceFile?: string;
  };
}

interface FeaturedStrideSelection {
  bet: BestBet;
  labels: string[];
}

type BestBetIdentityFilter =
  | "bet_pick"
  | "guide_only"
  | "filtered_substitute"
  | "market_unavailable"
  | "raw_model_leader"
  | "exotic";

type BestBetStrategyFilter =
  | "banker"
  | "value"
  | "model_pick"
  | "form_pick"
  | "roughie"
  | "best_chance";

const BEST_BET_IDENTITY_FILTER_OPTIONS: Array<{
  value: BestBetIdentityFilter;
  label: string;
  activeClassName: string;
}> = [
  {
    value: "bet_pick",
    label: "BET PICK",
    activeClassName: "border-emerald-500/40 bg-emerald-500/15 text-emerald-300",
  },
  {
    value: "guide_only",
    label: "NO BET • GUIDE ONLY",
    activeClassName: "border-amber-500/40 bg-amber-500/15 text-amber-300",
  },
  {
    value: "filtered_substitute",
    label: "NO BET • FILTERED SUB",
    activeClassName: "border-amber-400/40 bg-amber-400/12 text-amber-200",
  },
  {
    value: "market_unavailable",
    label: "NO BET • MARKET UNAVAILABLE",
    activeClassName: "border-slate-400/40 bg-slate-400/12 text-slate-200",
  },
  {
    value: "raw_model_leader",
    label: "RAW MODEL LEADER",
    activeClassName: "border-sky-500/40 bg-sky-500/15 text-sky-300",
  },
  {
    value: "exotic",
    label: "EXOTIC",
    activeClassName: "border-purple-500/40 bg-purple-500/15 text-purple-300",
  },
];

const BEST_BET_STRATEGY_FILTER_OPTIONS: Array<{
  value: BestBetStrategyFilter;
  label: string;
  activeClassName: string;
}> = [
  {
    value: "banker",
    label: "BANKER",
    activeClassName: "border-green-500/40 bg-green-500/15 text-green-300",
  },
  {
    value: "value",
    label: "VALUE",
    activeClassName: "border-emerald-500/40 bg-emerald-500/15 text-emerald-300",
  },
  {
    value: "model_pick",
    label: "MODEL",
    activeClassName: "border-cyan-500/40 bg-cyan-500/15 text-cyan-300",
  },
  {
    value: "form_pick",
    label: "FORM",
    activeClassName: "border-orange-500/40 bg-orange-500/15 text-orange-300",
  },
  {
    value: "roughie",
    label: "ROUGHIE",
    activeClassName: "border-purple-500/40 bg-purple-500/15 text-purple-300",
  },
  {
    value: "best_chance",
    label: "BEST CHANCE",
    activeClassName: "border-yellow-500/40 bg-yellow-500/15 text-yellow-300",
  },
];

const getBestBetIdentity = (
  bet?: Pick<BestBet, "track" | "raceNumber" | "horseName"> | null,
) => {
  if (!bet) return "";
  return `${bet.track}|${bet.raceNumber}|${bet.horseName}`.toLowerCase().trim();
};

const pickDistinctBestBet = (candidates: BestBet[], usedKeys: Set<string>) => {
  const next = candidates.find((bet) => !usedKeys.has(getBestBetIdentity(bet)));
  if (!next) return null;
  usedKeys.add(getBestBetIdentity(next));
  return next;
};

const sortByModelStrength = (left: BestBet, right: BestBet) => {
  if ((right.confidenceScore || 0) !== (left.confidenceScore || 0)) {
    return (right.confidenceScore || 0) - (left.confidenceScore || 0);
  }
  if ((right.winPercentage || 0) !== (left.winPercentage || 0)) {
    return (right.winPercentage || 0) - (left.winPercentage || 0);
  }
  return (right.edge || 0) - (left.edge || 0);
};

const sortByValueEdge = (left: BestBet, right: BestBet) => {
  if ((right.edge || 0) !== (left.edge || 0)) {
    return (right.edge || 0) - (left.edge || 0);
  }
  return (right.confidenceScore || 0) - (left.confidenceScore || 0);
};

const sortByJumpTime = (left: BestBet, right: BestBet) =>
  new Date(left.offTime).getTime() - new Date(right.offTime).getTime();

const getBestBetIdentityFilterValue = (bet: BestBet): BestBetIdentityFilter => {
  if (bet.selectionType === "exoticTip") {
    return "exotic";
  }
  if (bet.selectionOrigin === "raw_model_leader") {
    return "raw_model_leader";
  }
  if (bet.selectionOrigin === "filtered_substitute") {
    return "filtered_substitute";
  }
  if (bet.selectionOrigin === "market_unavailable") {
    return "market_unavailable";
  }
  if (bet.shouldBet === false) {
    return "guide_only";
  }
  return "bet_pick";
};

const getBestBetStrategyFilterValue = (bet: BestBet): BestBetStrategyFilter | null => {
  switch (bet.betType) {
    case "banker":
    case "value":
    case "model_pick":
    case "form_pick":
    case "roughie":
    case "best_chance":
      return bet.betType;
    default:
      return null;
  }
};

const matchesBestBetTypeFilters = (
  bet: BestBet,
  identityFilters: ReadonlySet<BestBetIdentityFilter>,
  strategyFilters: ReadonlySet<BestBetStrategyFilter>,
) => {
  const identityMatch =
    identityFilters.size === 0 || identityFilters.has(getBestBetIdentityFilterValue(bet));
  const strategyValue = getBestBetStrategyFilterValue(bet);
  const strategyMatch =
    strategyFilters.size === 0 || (strategyValue ? strategyFilters.has(strategyValue) : false);

  return identityMatch && strategyMatch;
};

const compareBestBetTrackAnchors = (left: BestBet, right: BestBet) => {
  const leftIsLive = left.shouldBet !== false ? 1 : 0;
  const rightIsLive = right.shouldBet !== false ? 1 : 0;
  if (rightIsLive !== leftIsLive) {
    return rightIsLive - leftIsLive;
  }

  const leftIsExotic = left.selectionType === "exoticTip" ? 1 : 0;
  const rightIsExotic = right.selectionType === "exoticTip" ? 1 : 0;
  if (leftIsExotic !== rightIsExotic) {
    return leftIsExotic - rightIsExotic;
  }

  if ((right.confidenceScore || 0) !== (left.confidenceScore || 0)) {
    return (right.confidenceScore || 0) - (left.confidenceScore || 0);
  }
  if ((right.edge || 0) !== (left.edge || 0)) {
    return (right.edge || 0) - (left.edge || 0);
  }
  if ((right.winPercentage || 0) !== (left.winPercentage || 0)) {
    return (right.winPercentage || 0) - (left.winPercentage || 0);
  }

  return sortByJumpTime(left, right);
};

const compareBestBetRaceSelections = (left: BestBet, right: BestBet) => {
  const leftIsBanker = left.betType === "banker" ? 1 : 0;
  const rightIsBanker = right.betType === "banker" ? 1 : 0;
  if (rightIsBanker !== leftIsBanker) {
    return rightIsBanker - leftIsBanker;
  }

  return compareBestBetTrackAnchors(left, right);
};

const buildBestBetsDeskHighlights = (bestBets: BestBet[], tips: BestBet[]) => {
  const allSelections = [...bestBets, ...tips];
  const modelCandidates = [...bestBets].sort(sortByModelStrength);
  const valueCandidates = [...allSelections].sort(sortByValueEdge);
  const earlyCandidates = [...allSelections].sort(sortByJumpTime);
  const usedKeys = new Set<string>();

  const bestBet =
    pickDistinctBestBet(modelCandidates.length ? modelCandidates : valueCandidates, usedKeys)
    || null;
  const bestValue =
    pickDistinctBestBet(valueCandidates.filter((bet) => (bet.edge || 0) > 0), usedKeys)
    || pickDistinctBestBet(valueCandidates, usedKeys)
    || null;
  const earlyPlay =
    pickDistinctBestBet(earlyCandidates, usedKeys)
    || pickDistinctBestBet(valueCandidates, usedKeys)
    || null;

  return { allSelections, bestBet, bestValue, earlyPlay };
};

const summarizeDeskStrideExplanation = (explanation?: string) => {
  if (!explanation) return null;

  const normalized = explanation.replace(/\s+/g, " ").trim();
  if (!normalized) return null;

  const sentences = normalized.match(/[^.!?]+[.!?]?/g)?.map((sentence) => sentence.trim()).filter(Boolean) || [];
  const summary = sentences.length >= 2 ? sentences.slice(0, 2).join(" ") : normalized;

  return summary.length > 220 ? `${summary.slice(0, 217).trimEnd()}...` : summary;
};

const getPickIdentityInfo = (bet: BestBet) => {
  if (bet.selectionOrigin === "raw_model_leader") {
    return {
      shortLabel: "Raw Model",
      label: "RAW MODEL LEADER",
      color: "bg-sky-500/20 text-sky-300 border-sky-500/40",
    };
  }

  if (bet.selectionOrigin === "filtered_substitute") {
    return {
      shortLabel: "No Bet",
      label: "NO BET • FILTERED SUB",
      color: "bg-amber-500/20 text-amber-300 border-amber-500/40",
    };
  }

  if (bet.selectionOrigin === "market_unavailable") {
    return {
      shortLabel: "No Bet",
      label: "NO BET • MARKET UNAVAILABLE",
      color: "bg-slate-500/20 text-slate-300 border-slate-400/40",
    };
  }

  if (bet.shouldBet === false) {
    return {
      shortLabel: "No Bet",
      label: "NO BET • GUIDE ONLY",
      color: "bg-amber-500/20 text-amber-400 border-amber-500/30",
    };
  }

  return {
    shortLabel: "Bet Pick",
    label: "BET PICK",
    color: "bg-emerald-500/20 text-emerald-300 border-emerald-500/40",
  };
};

const getDeskCallLabel = (bet: BestBet) => getPickIdentityInfo(bet).shortLabel;

const getBestBetFairOdds = (bet: BestBet) => {
  if (typeof bet.fairOdds === "number" && Number.isFinite(bet.fairOdds) && bet.fairOdds > 0) {
    return bet.fairOdds;
  }
  if (typeof bet.winPercentage === "number" && bet.winPercentage > 0) {
    return 100 / bet.winPercentage;
  }
  return null;
};

const getBestBetOverlayPct = (bet: BestBet) => {
  const fairOdds = getBestBetFairOdds(bet);
  if (!fairOdds || !bet.marketOdds || bet.marketOdds <= 0) {
    return null;
  }
  return ((bet.marketOdds / fairOdds) - 1) * 100;
};

const getBestBetConfidenceBand = (score: number, crowdClassification?: string | null) => {
  // CONFIRMED picks get special label regardless of score
  if (crowdClassification === 'CONFIRMED') {
    return {
      label: "Crowd + Model",
      className: "border-emerald-500/30 bg-emerald-500/10 text-emerald-300",
    };
  }
  if (score >= 80) {
    return {
      label: "High conviction",
      className: "border-emerald-500/30 bg-emerald-500/10 text-emerald-300",
    };
  }
  if (score >= 60) {
    return {
      label: "Live setup",
      className: "border-racing-orange/30 bg-racing-orange/10 text-racing-orange",
    };
  }
  if (score >= 40) {
    return {
      label: "Watching",
      className: "border-amber-500/30 bg-amber-500/10 text-amber-300",
    };
  }
  if (crowdClassification === 'MODEL_ONLY' || crowdClassification === 'REJECTED') {
    return {
      label: "Model only",
      className: "border-red-500/20 bg-red-500/[0.06] text-red-400/80",
    };
  }
  return {
    label: "Thin edge",
    className: "border-white/10 bg-white/[0.04] text-white/60",
  };
};

const getBestBetExecutionReason = (bet?: BestBet | null) => {
  if (!bet) return "No runner has separated clearly enough yet.";
  return bet.selectionOriginReason
    || bet.reasons?.[0]
    || bet.modelSummary?.[0]
    || bet.formAnalystInsights?.[0]
    || bet.pipelineSignals
    || "Model signals are still too mixed for a stronger headline.";
};

const getMeetingStrengthClassName = (score: number) => {
  if (score >= 320) {
    return "border-emerald-500/35 bg-[linear-gradient(135deg,rgba(16,185,129,0.14),rgba(255,255,255,0.03))]";
  }
  if (score >= 230) {
    return "border-racing-orange/35 bg-[linear-gradient(135deg,rgba(249,115,22,0.14),rgba(255,255,255,0.03))]";
  }
  if (score >= 150) {
    return "border-amber-500/30 bg-[linear-gradient(135deg,rgba(245,158,11,0.12),rgba(255,255,255,0.03))]";
  }
  return "border-white/10 bg-white/[0.03]";
};

type ViewMode = "blackbook" | "best";
type SortOption = "ev" | "edge" | "winPct" | "time" | "odds";
type GroupBy = "none" | "track" | "date";

// AI Insight section headers for the rich form analysis format
const AI_SECTIONS = ["THE FORM", "THE RUN", "THE CLASS", "THE DRAW", "THE QUERY", "WHY HIM"] as const;

function AiInsightDisplay({ insight, aiScore, llmProvider, compact = false }: {
  insight: string;
  aiScore?: number;
  llmProvider?: string;
  compact?: boolean;
}) {
  // Parse sectioned format (THE FORM:, THE RUN:, etc.)
  const sections: { header: string; body: string }[] = [];
  let currentSection = "";
  let currentBody: string[] = [];

  const lines = insight.replace(/\*+/g, '').split('\n');
  for (const line of lines) {
    const trimmed = line.trim();
    if (!trimmed) continue;

    const matchedHeader = AI_SECTIONS.find(h =>
      trimmed.toUpperCase().startsWith(h + ':') || trimmed.toUpperCase().startsWith(h + ' ')
    );

    if (matchedHeader) {
      if (currentSection && currentBody.length > 0) {
        sections.push({ header: currentSection, body: currentBody.join(' ') });
      }
      currentSection = matchedHeader;
      const rest = trimmed.substring(matchedHeader.length).replace(/^[:\s]+/, '').trim();
      currentBody = rest ? [rest] : [];
    } else {
      currentBody.push(trimmed);
    }
  }
  if (currentSection && currentBody.length > 0) {
    sections.push({ header: currentSection, body: currentBody.join(' ') });
  }

  // If no sections detected, fall back to plain text display
  const hasSections = sections.length > 0;
  const plainText = hasSections ? null : insight.replace(/\*+/g, '');

  const scoreColor = aiScore != null
    ? aiScore >= 75 ? 'text-green-400' : aiScore >= 50 ? 'text-yellow-400' : 'text-red-400'
    : '';
  const scoreBg = aiScore != null
    ? aiScore >= 75 ? 'bg-green-500/20 border-green-500/30' : aiScore >= 50 ? 'bg-yellow-500/20 border-yellow-500/30' : 'bg-red-500/20 border-red-500/30'
    : '';

  return (
    <div className="bg-racing-gold/5 border border-racing-gold/20 rounded-lg overflow-hidden">
      {/* Header */}
      <div className="flex items-center justify-between px-3 py-2 border-b border-racing-gold/10">
        <div className="flex items-center gap-2">
          <Sparkles className="h-3.5 w-3.5 text-racing-gold" />
          <span className="text-xs font-semibold text-racing-gold uppercase tracking-wide">Form Analysis</span>
          {llmProvider && (
            <span className="text-[10px] text-white/30 font-mono">AI</span>
          )}
        </div>
        {aiScore != null && (
          <span className={`text-xs font-bold px-2 py-0.5 rounded border ${scoreBg} ${scoreColor}`}>
            {aiScore}/100
          </span>
        )}
      </div>

      {/* Body */}
      <div className={`px-3 py-2 space-y-2 ${compact ? 'max-h-48 overflow-y-auto' : ''}`}>
        {hasSections ? sections.map((section, i) => (
          <div key={i}>
            <p className="text-[10px] font-bold text-racing-gold/70 uppercase tracking-wider mb-0.5">
              {section.header}
            </p>
            <p className="text-xs text-foreground/85 leading-relaxed">
              {section.body}
            </p>
          </div>
        )) : (
          <p className="text-xs text-foreground/85 leading-relaxed">
            {plainText}
          </p>
        )}
      </div>
    </div>
  );
}

// AI Score badge for card headers (visible without expanding)
function AiScoreBadge({ score }: { score?: number }) {
  if (score == null) return null;
  const color = score >= 75 ? 'bg-green-500/20 text-green-400 border-green-500/30'
    : score >= 50 ? 'bg-yellow-500/20 text-yellow-400 border-yellow-500/30'
    : 'bg-red-500/20 text-red-400 border-red-500/30';
  return (
    <span className={`text-[10px] font-bold px-1.5 py-0.5 rounded border ${color}`}>
      AI {score}
    </span>
  );
}

// Luckless last-start badge for card headers
function LucklessBadge({ flag, score }: { flag?: boolean; score?: number }) {
  if (!flag || !score) return null;
  return (
    <span className="text-[10px] font-bold px-1.5 py-0.5 rounded border bg-amber-500/20 text-amber-400 border-amber-500/30">
      Luckless {score}
    </span>
  );
}

const POSITIVE_VERDICT_KEYWORDS = [
  "back",
  "value watch",
  "value",
  "genuine",
  "strong case",
  "main danger",
  "best chance",
];
const NEGATIVE_VERDICT_KEYWORDS = [
  "against",
  "unlikely",
  "out of depth",
  "barrier ruins",
  "can't win",
  "no case",
  "eliminate",
  "no win bet",
];

function normalizeRunnerName(value: string): string {
  return value.toLowerCase().replace(/[^a-z0-9]/g, "");
}

function getMarketOdds(runner: any, isFullField: boolean): number | null {
  if (isFullField) {
    return typeof runner.odds === "number" && Number.isFinite(runner.odds) ? runner.odds : null;
  }

  const raw = runner?.odds?.find?.((entry: any) => entry.bookmaker?.toLowerCase() === "sportsbet")?.win_odds
    ?? runner?.odds?.[0]?.win_odds;
  const parsed = Number(raw);
  return Number.isFinite(parsed) ? parsed : null;
}

function getVerdictAccentClass(verdict: string): string {
  const normalized = verdict.toLowerCase();
  if (POSITIVE_VERDICT_KEYWORDS.some((keyword) => normalized.includes(keyword))) {
    return "border-l-green-500";
  }
  if (NEGATIVE_VERDICT_KEYWORDS.some((keyword) => normalized.includes(keyword))) {
    return "border-l-red-500";
  }
  return "border-l-white/15";
}

function RunnerAnalysisSection({
  label,
  text,
  isVerdict = false,
}: {
  label: string;
  text: string;
  isVerdict?: boolean;
}) {
  return (
    <div className={`rounded-lg border border-white/[0.06] bg-black/20 px-3 py-3 ${isVerdict ? `border-l-2 ${getVerdictAccentClass(text)}` : ""}`}>
      <div className="mb-2 flex items-center gap-3">
        <span className={`text-[10px] uppercase tracking-[0.22em] ${isVerdict ? "font-semibold text-white/60" : "font-medium text-white/40"}`}>
          {label}
        </span>
        <div className="h-px flex-1 bg-white/10" />
      </div>
      <p className={`leading-relaxed ${isVerdict ? "text-sm font-medium text-white/85" : "text-sm text-white/75"}`}>
        {text}
      </p>
    </div>
  );
}

function RunnerAnalysisLoading() {
  return (
    <div className="space-y-3">
      {["PROFILE", "PACE", "CONDITION", "VERDICT"].map((label) => (
        <div key={label} className="rounded-lg border border-white/[0.06] bg-black/20 px-3 py-3">
          <div className="mb-2 flex items-center gap-3">
            <span className="text-[10px] font-medium uppercase tracking-[0.22em] text-white/40">{label}</span>
            <div className="h-px flex-1 bg-white/10" />
          </div>
          <div className="space-y-2">
            <Skeleton className="h-3.5 w-[88%] bg-white/10" />
            <Skeleton className="h-3.5 w-full bg-white/10" />
            <Skeleton className="h-3.5 w-[72%] bg-white/10" />
          </div>
        </div>
      ))}
    </div>
  );
}

const AI_LOAD_PREFIX = "best-bets-ai:";
const BEST_BETS_DESK_NOTES_COLLAPSE_STORAGE_KEY = "best-bets-desk-notes-collapsed";

function RaceAiLoadPrompt({
  onLoad,
  label = "Load AI Analysis",
}: {
  onLoad: () => void;
  label?: string;
}) {
  return (
    <div className="border-b border-white/[0.06] bg-[linear-gradient(180deg,rgba(255,255,255,0.04),rgba(255,255,255,0.015))]">
      <div className="px-4 py-4 sm:px-5">
        <div className="rounded-2xl border border-racing-orange/20 bg-[linear-gradient(135deg,rgba(249,115,22,0.12),rgba(255,255,255,0.03))] px-4 py-4 sm:px-5">
          <div className="flex flex-col gap-4 sm:flex-row sm:items-center sm:justify-between">
            <div className="min-w-0">
              <div className="flex items-center gap-2">
                <div className="flex h-9 w-9 items-center justify-center rounded-lg bg-racing-orange/15 text-racing-orange">
                  <Brain className="h-4 w-4" />
                </div>
                <span className="text-sm font-semibold uppercase tracking-[0.16em] text-white/88">
                  AI Features On Demand
                </span>
              </div>
              <p className="mt-2 max-w-2xl text-sm leading-relaxed text-white/68">
                Load the AI race overview and runner-by-runner reads only when you want them.
                The live speed map stays available by default, so the page stays readable without burning tokens on every race open.
              </p>
            </div>

            <Button
              type="button"
              onClick={onLoad}
              className="h-10 shrink-0 rounded-xl bg-racing-orange px-4 text-sm font-semibold text-black hover:bg-racing-orange/90"
            >
              <Brain className="mr-2 h-4 w-4" />
              {label}
            </Button>
          </div>
        </div>
      </div>
    </div>
  );
}

function RaceRunnerPanel({
  selectedRace,
  fullFieldRunners,
  speedMap,
  highlightedSelections,
  raceDate,
  isLoadingSimulationRaces,
}: {
  selectedRace: any;
  fullFieldRunners?: FullFieldRunner[];
  speedMap?: RaceSpeedMap | null;
  highlightedSelections: Map<string, BestBet>;
  raceDate: string;
  isLoadingSimulationRaces: boolean;
}) {
  const hasFullField = Boolean(fullFieldRunners && fullFieldRunners.length > 0);
  const runners: any[] = hasFullField ? fullFieldRunners || [] : selectedRace?.runners || [];
  const raceTrack = selectedRace?.bets?.[0]?.track || "";
  const raceNumber = Number(selectedRace?.raceNumber || 0);
  const highlightedRaceSelections = useMemo(
    () => Array.from(highlightedSelections.values()).sort(compareBestBetRaceSelections),
    [highlightedSelections],
  );
  const fallbackPrimaryRunnerKey = useMemo(() => {
    if (!hasFullField || highlightedRaceSelections.length > 0) {
      return null;
    }

    const fallbackRunner = [...runners]
      .filter((runner) => Boolean(runner?.is_tipped))
      .sort((left, right) => {
        const leftEdge = typeof left?.edge_pct === "number" ? left.edge_pct : Number.NEGATIVE_INFINITY;
        const rightEdge = typeof right?.edge_pct === "number" ? right.edge_pct : Number.NEGATIVE_INFINITY;
        if (rightEdge !== leftEdge) {
          return rightEdge - leftEdge;
        }

        const leftWinPct = typeof left?.win_pct === "number" ? left.win_pct : Number.NEGATIVE_INFINITY;
        const rightWinPct = typeof right?.win_pct === "number" ? right.win_pct : Number.NEGATIVE_INFINITY;
        return rightWinPct - leftWinPct;
      })[0];

    return fallbackRunner?.horse ? normalizeRunnerName(fallbackRunner.horse) : null;
  }, [hasFullField, highlightedRaceSelections, runners]);
  const primarySelectionKey = highlightedRaceSelections[0]
    ? normalizeRunnerName(highlightedRaceSelections[0].horseName)
    : fallbackPrimaryRunnerKey;
  const aiStorageKey = useMemo(() => {
    if (!raceTrack || !raceNumber || !raceDate) {
      return null;
    }

    return `${AI_LOAD_PREFIX}${raceDate}|${raceNumber}|${raceTrack.toLowerCase()}`;
  }, [raceDate, raceNumber, raceTrack]);
  const [aiEnabled, setAiEnabled] = useState(false);

  useEffect(() => {
    if (!aiStorageKey || typeof window === "undefined") {
      setAiEnabled(false);
      return;
    }

    setAiEnabled(window.sessionStorage.getItem(aiStorageKey) === "1");
  }, [aiStorageKey]);

  const enableAi = () => {
    setAiEnabled(true);
    if (aiStorageKey && typeof window !== "undefined") {
      window.sessionStorage.setItem(aiStorageKey, "1");
    }
  };

  const analysisInputs = useMemo(
    () =>
      runners.map((runner) => ({
        horseName: runner.horse || "Unknown Runner",
        currentOdds: getMarketOdds(runner, hasFullField),
      })),
    [hasFullField, runners],
  );

  const { states, retryRunner } = useRunnerAnalyses({
    track: raceTrack,
    raceNumber,
    raceDate,
    runners: analysisInputs,
    enabled: aiEnabled,
  });
  const { state: overviewState, retryOverview } = useRaceOverview({
    track: raceTrack,
    raceNumber,
    raceDate,
    enabled: aiEnabled,
  });

  if (runners.length === 0) {
    return (
      <div>
        {aiEnabled ? (
          <>
            <RaceOverviewPanel
              track={raceTrack}
              raceNumber={raceNumber}
              raceDate={raceDate}
              state={overviewState}
              retryOverview={retryOverview}
            />
            <RaceShapeBlock
              track={raceTrack}
              raceNumber={raceNumber}
              raceDate={raceDate}
              state={overviewState}
              retryOverview={retryOverview}
              speedMap={speedMap}
            />
          </>
        ) : null}
        <div className="text-center py-12">
          {isLoadingSimulationRaces ? (
            <div className="flex flex-col items-center gap-3">
              <Loader2 className="h-8 w-8 animate-spin text-racing-orange" />
              <p className="text-white/40">Loading runners...</p>
            </div>
          ) : (
            <div className="flex flex-col items-center gap-2">
              <AlertTriangle className="h-10 w-10 text-white/20" />
              <p className="text-white/40">No runner data available for this race yet.</p>
              <p className="text-xs text-white/30">Runner information will appear once racecards are synced.</p>
            </div>
          )}
        </div>
      </div>
    );
  }

  const lowestOdds = hasFullField
    ? Math.min(...(fullFieldRunners || []).map((runner) => (runner.odds > 0 ? runner.odds : Number.POSITIVE_INFINITY)))
    : Number.POSITIVE_INFINITY;

  return (
    <div className="divide-y divide-white/[0.04]">
      {aiEnabled ? (
        <>
          <RaceOverviewPanel
            track={raceTrack}
            raceNumber={raceNumber}
            raceDate={raceDate}
            state={overviewState}
            retryOverview={retryOverview}
          />
          <RaceShapeBlock
            track={raceTrack}
            raceNumber={raceNumber}
            raceDate={raceDate}
            state={overviewState}
            retryOverview={retryOverview}
            speedMap={speedMap}
          />
        </>
      ) : (
        <>
          <RaceAiLoadPrompt onLoad={enableAi} />
          <RaceShapeBlock
            track={raceTrack}
            raceNumber={raceNumber}
            raceDate={raceDate}
            state={overviewState}
            retryOverview={retryOverview}
            speedMap={speedMap}
          />
        </>
      )}
      <div className="px-4 py-2 bg-white/[0.02]">
        <div className={`grid ${hasFullField ? "grid-cols-[auto_1fr_auto_auto_auto_auto_auto]" : "grid-cols-[auto_1fr_auto_auto_auto]"} gap-3 text-[10px] uppercase tracking-wider text-white/30 font-medium items-center`}>
          <div className="text-center w-10">No</div>
          <div>Runner</div>
          <div className="text-center w-20 hidden sm:block">Jockey</div>
          <div className="text-center w-12">Bar</div>
          {hasFullField && <div className="text-center w-14">Win%</div>}
          {hasFullField && <div className="text-center w-14">Edge</div>}
          <div className="text-right w-20">Odds</div>
        </div>
      </div>

      {runners.map((runner: any, index: number) => {
        const runnerName = runner.horse || `Runner ${index + 1}`;
        const runnerKey = normalizeRunnerName(runnerName);
        const selection = highlightedSelections.get(runnerKey);
        const isSelectedRunner = Boolean(selection) || (highlightedRaceSelections.length === 0 && hasFullField ? Boolean(runner.is_tipped) : false);
        const isPrimaryTip = isSelectedRunner && primarySelectionKey === runnerKey;
        const isSecondaryPick = isSelectedRunner && !isPrimaryTip;
        const odds = getMarketOdds(runner, hasFullField);
        const barrier = hasFullField ? runner.barrier : (runner.barrier || runner.draw || "-");
        const saddle = hasFullField ? (runner.saddle_number || index + 1) : (runner.number || `${index + 1}`);
        const isEven = index % 2 === 0;
        const hasPositiveEdge = hasFullField && runner.edge_pct > 0 && !isSelectedRunner;
        const isFav = hasFullField && odds != null && odds === lowestOdds;
        const analysisState = states[runnerKey] || { status: aiEnabled ? ("loading" as const) : ("idle" as const) };
        const daysSinceRun = hasFullField ? runner.days_since_run : null;
        const courseStrike = hasFullField ? runner.course_strike_rate : null;
        const distanceStrike = hasFullField ? runner.distance_strike_rate : null;

        return (
          <div
            key={`${runnerName}-${index}`}
            className={`grid grid-cols-[minmax(0,1fr)_auto] gap-x-12 px-4 py-4 transition-colors ${
              isPrimaryTip
                ? "bg-green-500/10 border-l-3 border-green-500"
                : isSecondaryPick
                  ? "bg-amber-500/10 border-l-3 border-amber-500"
                  : hasPositiveEdge
                    ? "border-l-2 border-green-500/30 bg-white/[0.01]"
                    : isEven
                      ? "bg-white/[0.01]"
                      : ""
            }`}
          >
            <div className="min-w-0 space-y-4">
              <div className={`grid ${hasFullField ? "grid-cols-[auto_minmax(0,1fr)_auto_auto_auto_auto_auto]" : "grid-cols-[auto_minmax(0,1fr)_auto_auto_auto]"} gap-3 items-center`}>
                <div className="flex justify-center w-10">
                  <div className={`w-8 h-8 rounded-full flex items-center justify-center text-sm font-bold ${
                    isPrimaryTip ? "bg-green-500 text-black" : isSecondaryPick ? "bg-amber-500 text-black" : "bg-white/[0.08] text-white/70"
                  }`}>
                    {saddle}
                  </div>
                </div>

                <div className="min-w-0">
                  <div className="flex items-center gap-1.5 flex-wrap">
                    <p className={`font-semibold truncate text-sm ${isSelectedRunner ? "text-white" : "text-white/90"}`}>
                      {runnerName}
                    </p>
                    {isSelectedRunner && (
                      <Badge className={`text-[10px] px-1.5 py-0 h-4 font-bold ${
                        selection?.betType === "banker" && isPrimaryTip
                          ? "bg-green-500/20 text-green-400 border-green-500/30"
                          : isPrimaryTip
                            ? "bg-green-500/20 text-green-400 border-green-500/30"
                            : "bg-amber-500/20 text-amber-400 border-amber-500/30"
                      }`}>
                        {selection?.betType === "banker" && isPrimaryTip ? "BANKER" : isPrimaryTip ? "TIP" : "PICK"}
                      </Badge>
                    )}
                    {isFav && !isSelectedRunner && (
                      <Badge className="text-[10px] px-1 py-0 h-4 bg-white/10 text-white/50 border-white/20">FAV</Badge>
                    )}
                    {hasFullField && runner.crowd_score != null && runner.crowd_score > 0 && (
                      <Badge className={`text-[10px] px-1.5 py-0 h-4 ${
                        runner.crowd_score >= 70
                          ? "bg-blue-500/20 text-blue-400 border-blue-500/30"
                          : "bg-blue-500/10 text-blue-400/60 border-blue-500/20"
                      }`}>
                        {runner.crowd_score.toFixed(0)}% crowd
                      </Badge>
                    )}
                  </div>
                  <p className="text-[11px] text-white/40 font-mono mt-0.5">
                    {hasFullField ? runner.form : runner.form}
                    {hasFullField && runner.is_first_up && <span className="ml-1 text-amber-400">1st-up</span>}
                    {hasFullField && runner.is_improving && <span className="ml-1 text-green-400">improving</span>}
                  </p>
                </div>

                <div className="text-center w-20 hidden sm:block">
                  <p className="text-xs text-white/50 truncate">{hasFullField ? runner.jockey : runner.jockey || "-"}</p>
                  <p className="text-[10px] text-white/30 truncate">{hasFullField ? runner.trainer : runner.trainer || ""}</p>
                </div>

                <div className="text-center w-12">
                  <div className={`inline-flex items-center justify-center w-6 h-6 rounded text-xs font-medium ${
                    Number(barrier) <= 4
                      ? "bg-green-500/10 text-green-400"
                      : Number(barrier) >= 10
                        ? "bg-red-500/10 text-red-400"
                        : "bg-white/[0.06] text-white/50"
                  }`}>
                    {barrier}
                  </div>
                </div>

                {hasFullField && (
                  <div className="text-center w-14">
                    <p className={`text-xs font-semibold ${runner.win_pct >= 20 ? "text-green-400" : runner.win_pct >= 10 ? "text-white/70" : "text-white/40"}`}>
                      {runner.win_pct.toFixed(1)}%
                    </p>
                  </div>
                )}

                {hasFullField && (
                  <div className="text-center w-14">
                    <p className={`text-xs font-semibold ${runner.edge_pct > 2 ? "text-green-400" : runner.edge_pct > 0 ? "text-green-400/60" : runner.edge_pct > -3 ? "text-white/40" : "text-red-400/60"}`}>
                      {runner.edge_pct > 0 ? "+" : ""}{runner.edge_pct.toFixed(1)}%
                    </p>
                  </div>
                )}

                <div className="text-right w-20">
                  <p className={`text-sm font-bold ${isSelectedRunner ? "text-racing-orange" : "text-green-400"}`}>
                    {odds != null ? `$${odds.toFixed(2)}` : "Market suspended"}
                  </p>
                </div>
              </div>

              {hasFullField && (
                <div className="grid grid-cols-2 gap-2 text-[11px] text-white/45 sm:grid-cols-4">
                  <div><span className="text-white/30">Place</span> {(runner.place_pct ?? 0).toFixed(1)}%</div>
                  <div><span className="text-white/30">Days</span> {daysSinceRun && daysSinceRun < 999 ? daysSinceRun : "Spell"}</div>
                  <div><span className="text-white/30">Track</span> {courseStrike ? `${(courseStrike * 100).toFixed(0)}%` : "-"}</div>
                  <div><span className="text-white/30">Distance</span> {distanceStrike ? `${(distanceStrike * 100).toFixed(0)}%` : "-"}</div>
                </div>
              )}

              {!aiEnabled ? null : analysisState.status === "loading" || analysisState.status === "idle" ? (
                <RunnerAnalysisLoading />
              ) : analysisState.status === "error" ? (
                <div className="rounded-lg border border-red-500/20 bg-red-500/5 px-4 py-3">
                  <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
                    <p className="text-sm text-red-300">Analysis currently unavailable.</p>
                    <Button
                      variant="outline"
                      size="sm"
                      className="border-red-500/30 text-red-300 hover:bg-red-500/10"
                      onClick={() => retryRunner({ horseName: runnerName, currentOdds: odds })}
                    >
                      Retry
                    </Button>
                  </div>
                  {analysisState.message && (
                    <p className="mt-2 text-xs text-red-300/70">{analysisState.message}</p>
                  )}
                </div>
              ) : (
                <div className="space-y-3">
                  <RunnerAnalysisSection label="PROFILE" text={analysisState.data?.profile || ""} />
                  <RunnerAnalysisSection label="PACE" text={analysisState.data?.pace || ""} />
                  <RunnerAnalysisSection label="CONDITION" text={analysisState.data?.condition || ""} />
                  <RunnerAnalysisSection label="VERDICT" text={analysisState.data?.verdict || ""} isVerdict />
                </div>
              )}
            </div>

            <div className="self-center">
              <Button
                type="button"
                variant="outline"
                className="h-8 w-8 min-h-8 min-w-8 rounded-md border-white/20 bg-white/[0.02] p-0 text-white/70 hover:bg-white/10 hover:text-white"
                onClick={() => console.log("+ clicked:", runnerName)}
              >
                <Plus className="h-3.5 w-3.5" />
              </Button>
            </div>
          </div>
        );
      })}
    </div>
  );
}

export default function BettingDashboard() {
  const [location, navigate] = useLocation();
  const [viewMode, setViewMode] = useState<ViewMode>("blackbook");
  const [searchQuery, setSearchQuery] = useState("");
  const [trackFilter, setTrackFilter] = useState("all");
  const [dateFilter, setDateFilter] = useState("all");
  const [confidenceFilter, setConfidenceFilter] = useState("all");
  const [valueRatingFilter, setValueRatingFilter] = useState("all");
  const [runningStyleFilter, setRunningStyleFilter] = useState("all");
  const [minEdge, setMinEdge] = useState(0);
  const [sortBy, setSortBy] = useState<SortOption>("time");
  const [sortOrder, setSortOrder] = useState<"asc" | "desc">("asc");
  const [groupBy, setGroupBy] = useState<GroupBy>("none");
  const [showFilters, setShowFilters] = useState(true);
  const [expandedCards, setExpandedCards] = useState<Set<string>>(new Set());
  const [expandedBestBets, setExpandedBestBets] = useState<Set<string>>(new Set());
  const [strideExplanations, setStrideExplanations] = useState<Record<string, string>>({});
  const [strideLoading, setStrideLoading] = useState<Set<string>>(new Set());
  const [todayOnly, setTodayOnly] = useState(false);
  const [collapsedGroups, setCollapsedGroups] = useState<Set<string>>(new Set());
  const [now, setNow] = useState(new Date());
  const [bestBetsDateFilter, setBestBetsDateFilter] = useState<string>("today");
  const [bestBetIdentityFilters, setBestBetIdentityFilters] = useState<Set<BestBetIdentityFilter>>(new Set());
  const [bestBetStrategyFilters, setBestBetStrategyFilters] = useState<Set<BestBetStrategyFilter>>(new Set());
  const [selectedBestTrack, setSelectedBestTrack] = useState<string>("");
  const [selectedBestRaceKey, setSelectedBestRaceKey] = useState<string>("");
  const [topSelectionsView, setTopSelectionsView] = useState<"trackBest" | "racecardTips">("trackBest");
  const [racecardLaneTrackFilter, setRacecardLaneTrackFilter] = useState<string>("all");
  const [racecardLaneConfidenceSort, setRacecardLaneConfidenceSort] = useState<string>("time");
  const [bestBetsDeskNotesCollapsed, setBestBetsDeskNotesCollapsed] = useState(true);

  // Stride Analyst Chat state
  const [strideOpen, setStrideOpen] = useState(false);
  const [strideQuestion, setStrideQuestion] = useState("");
  const [strideMessages, setStrideMessages] = useState<Array<{ role: 'user' | 'analyst', content: string }>>([]);
  const [strideAnalyzing, setStrideAnalyzing] = useState(false);

  const { addSelection, isInSlip, isLoading: slipLoading } = useBetSlip();

  useEffect(() => {
    if (location === "/best-bets") {
      setViewMode("best");
      setTopSelectionsView("trackBest");
      return;
    }

    if (location === "/race-day") {
      setViewMode("best");
      setTopSelectionsView("racecardTips");
      return;
    }

    setViewMode("blackbook");
  }, [location]);

  // Update clock every second for countdown timers
  useEffect(() => {
    const interval = setInterval(() => setNow(new Date()), 1000);
    return () => clearInterval(interval);
  }, []);

  useEffect(() => {
    if (typeof window === "undefined") return;
    const storedValue = window.sessionStorage.getItem(BEST_BETS_DESK_NOTES_COLLAPSE_STORAGE_KEY);
    if (storedValue !== null) {
      setBestBetsDeskNotesCollapsed(storedValue === "1");
    }
  }, []);

  useEffect(() => {
    if (typeof window === "undefined") return;
    window.sessionStorage.setItem(
      BEST_BETS_DESK_NOTES_COLLAPSE_STORAGE_KEY,
      bestBetsDeskNotesCollapsed ? "1" : "0",
    );
  }, [bestBetsDeskNotesCollapsed]);

  const getAustralianDateStr = (date: Date): string => {
    return date.toLocaleDateString('en-CA', { timeZone: 'Australia/Sydney' });
  };

  const normalizeTrackName = (trackName: string): string => {
    return trackName.toLowerCase().replace(/[^a-z0-9]/g, '');
  };

  const requestedTipsDate = bestBetsDateFilter === "today" ? "" : bestBetsDateFilter;
  const betsApiPath = requestedTipsDate
    ? `/api/bets?date=${encodeURIComponent(requestedTipsDate)}`
    : "/api/bets";
  const bestBetsApiPath = requestedTipsDate
    ? `/api/best-bets?date=${encodeURIComponent(requestedTipsDate)}`
    : "/api/best-bets";

  const requestStrideExplanation = async (bet: Selection | BestBet, options?: { force?: boolean }) => {
    if (strideLoading.has(bet.id)) return;
    if (!options?.force && strideExplanations[bet.id]) return;

    setStrideLoading(prev => new Set(prev).add(bet.id));

    try {
      const response = await apiRequest("POST", "/api/ask-stride", {
        horseName: bet.horseName,
        track: bet.track,
        raceNumber: bet.raceNumber,
        distance: bet.distance ?? undefined,
        jockey: bet.jockey ?? undefined,
        trainer: bet.trainer ?? undefined,
        barrier: bet.barrier ?? undefined,
        form: bet.form ?? undefined,
        winPercentage: bet.winPercentage,
        placePercentage: 'placePercentage' in bet ? bet.placePercentage : undefined,
        expectedValue: 'expectedValue' in bet ? bet.expectedValue : undefined,
        edge: bet.edge,
        marketOdds: bet.marketOdds,
        confidence: 'confidence' in bet ? bet.confidence : undefined,
        stabilityScore: 'stabilityScore' in bet ? bet.stabilityScore : undefined,
        runningStyle: 'runningStyle' in bet ? bet.runningStyle : undefined,
        ciLower: 'ciLower' in bet ? bet.ciLower : undefined,
        ciUpper: 'ciUpper' in bet ? bet.ciUpper : undefined,
        expectedPosition: 'expectedPosition' in bet ? bet.expectedPosition : undefined,
        valueRating: 'valueRating' in bet ? bet.valueRating : undefined,
        paceSplits: 'paceSplits' in bet ? bet.paceSplits : undefined,
      });

      const data = await response.json();
      setStrideExplanations(prev => ({ ...prev, [bet.id]: data.explanation }));
    } catch (error) {
      console.error("Ask Stride error:", error);
      setStrideExplanations(prev => ({ ...prev, [bet.id]: "Unable to get explanation. Please try again." }));
    } finally {
      setStrideLoading(prev => {
        const next = new Set(prev);
        next.delete(bet.id);
        return next;
      });
    }
  };

  const askStride = async (bet: Selection | BestBet) => {
    await requestStrideExplanation(bet);
  };

  // Ask Stride Analyst - comprehensive analysis about all selections
  const askStrideAnalyst = async () => {
    if (!strideQuestion.trim() || strideAnalyzing) return;

    const question = strideQuestion.trim();
    setStrideMessages(prev => [...prev, { role: 'user', content: question }]);
    setStrideQuestion("");
    setStrideAnalyzing(true);

    try {
      const response = await apiRequest("POST", "/api/stride-analyst", {
        question,
        selections: filteredSelections.map(s => ({
          horseName: s.horseName,
          track: s.track,
          raceNumber: s.raceNumber,
          distance: s.distance,
          jockey: s.jockey,
          trainer: s.trainer,
          barrier: s.barrier,
          form: s.form,
          winPercentage: s.winPercentage,
          placePercentage: s.placePercentage,
          expectedValue: s.expectedValue,
          edge: s.edge,
          marketOdds: s.marketOdds,
          confidence: s.confidence,
          stabilityScore: s.stabilityScore,
          runningStyle: s.runningStyle,
          valueRating: s.valueRating,
          trackBiasPoints: s.trackBiasPoints,
          trackBiasFit: s.trackBiasFit,
          trackBiasSummary: s.trackBiasSummary,
        })),
      });

      const data = await response.json();
      setStrideMessages(prev => [...prev, { role: 'analyst', content: data.answer }]);
    } catch (error) {
      console.error("Stride Analyst error:", error);
      setStrideMessages(prev => [...prev, { role: 'analyst', content: "Unable to analyze. Please try again." }]);
    } finally {
      setStrideAnalyzing(false);
    }
  };

  // Fetch value bets - reads from database first (instant), falls back to Monte Carlo
  const {
    data: betsData,
    isLoading,
    refetch,
    isRefetching,
    isFetching: isFetchingBets,
  } = useQuery<BetsResponse>({
    queryKey: [betsApiPath],
    staleTime: 60 * 1000,
    refetchInterval: 5 * 60 * 1000, // Refresh every 5 minutes
    refetchOnMount: "always",
    refetchOnWindowFocus: true,
    enabled: viewMode === "best",
  });

  // Mutation to run full Monte Carlo pipeline (stores results in database)
  const runPipelineMutation = useMutation({
    mutationFn: async () => {
      const response = await fetch('/api/pipeline/run', { method: 'POST' });
      if (!response.ok) throw new Error('Pipeline failed');
      return response.json();
    },
    onSuccess: () => {
      // Refresh bets from database after pipeline completes
      refetch();
      refetchBestBets();
    },
  });

  // Mutation to generate AI analyst insights for selections
  const { data: bestBetsData, isLoading: isLoadingBestBets, refetch: refetchBestBets, isFetching: isFetchingBestBets } = useQuery<BestBetsResponse>({
    queryKey: [bestBetsApiPath],
    staleTime: 60 * 1000,
    refetchInterval: 60 * 1000,
    refetchOnMount: "always",
    refetchOnWindowFocus: true,
    enabled: viewMode === "best",
  });

  const latestAvailableBestBetsDate = bestBetsData?.availableDates?.[bestBetsData.availableDates.length - 1] || "";
  const resolvedBestBetsDate = bestBetsData?.selectedDate
    || betsData?.metadata?.selectedDate
    || (bestBetsDateFilter === "today" ? latestAvailableBestBetsDate : bestBetsDateFilter)
    || latestAvailableBestBetsDate;
  const selectedBestBetsDate = useMemo(() => {
    const availableDates = bestBetsData?.availableDates || [];
    if (availableDates.includes(resolvedBestBetsDate)) {
      return resolvedBestBetsDate;
    }
    return availableDates[availableDates.length - 1] || resolvedBestBetsDate;
  }, [bestBetsData, resolvedBestBetsDate]);

  const activeBestBetFilterCount = bestBetIdentityFilters.size + bestBetStrategyFilters.size;

  const toggleBestBetIdentityFilter = (filterValue: BestBetIdentityFilter) => {
    setBestBetIdentityFilters((prev) => {
      const next = new Set(prev);
      if (next.has(filterValue)) {
        next.delete(filterValue);
      } else {
        next.add(filterValue);
      }
      return next;
    });
  };

  const toggleBestBetStrategyFilter = (filterValue: BestBetStrategyFilter) => {
    setBestBetStrategyFilters((prev) => {
      const next = new Set(prev);
      if (next.has(filterValue)) {
        next.delete(filterValue);
      } else {
        next.add(filterValue);
      }
      return next;
    });
  };

  const clearBestBetTypeFilters = () => {
    setBestBetIdentityFilters(new Set());
    setBestBetStrategyFilters(new Set());
  };

  const filteredBestBetsView = useMemo(() => {
    const safeBestBetsData = bestBetsData ?? {
      availableDates: [] as string[],
      bestPerTrack: [] as BestBet[],
      bestBets: [] as BestBet[],
      tips: [] as BestBet[],
      oneBetPerRace: [] as BestBet[],
      coveragePerRace: [] as BestBet[],
    };

    const availableDates = safeBestBetsData.availableDates || [];
    const selectedDate = availableDates.includes(selectedBestBetsDate)
      ? selectedBestBetsDate
      : (availableDates[availableDates.length - 1] || selectedBestBetsDate);

    const filterByDate = (bets: BestBet[]) => {
      return bets.filter((bet) => getAustralianDateStr(new Date(bet.offTime)) === selectedDate);
    };

    const dateFilteredCoveragePerRace = filterByDate(
      safeBestBetsData.coveragePerRace || safeBestBetsData.oneBetPerRace || [],
    );
    const filteredPool = dateFilteredCoveragePerRace.filter((bet) =>
      matchesBestBetTypeFilters(bet, bestBetIdentityFilters, bestBetStrategyFilters),
    );
    const filteredLiveBets = filteredPool.filter((bet) => bet.shouldBet !== false);
    const filteredTips = filteredPool.filter((bet) => bet.shouldBet === false);
    const crowdOverrides = filteredPool.filter(
      (bet) => bet.crowdClassification === 'CROWD_ONLY_WEAK' && (bet.crowdScore ?? 0) >= 70
    );

    const groupedByTrack = filteredPool.reduce((acc: Record<string, BestBet[]>, bet) => {
      if (!acc[bet.track]) acc[bet.track] = [];
      acc[bet.track].push(bet);
      return acc;
    }, {});

    const filteredBestPerTrack = Object.values(groupedByTrack)
      .map((trackBets) => [...trackBets].sort(compareBestBetTrackAnchors)[0])
      .filter((bet): bet is BestBet => Boolean(bet))
      .sort(sortByJumpTime);

    const sortedTracks = Object.keys(groupedByTrack).sort((a, b) => {
      const minTimeA = Math.min(...groupedByTrack[a].map((bet) => new Date(bet.offTime).getTime()));
      const minTimeB = Math.min(...groupedByTrack[b].map((bet) => new Date(bet.offTime).getTime()));
      return minTimeA - minTimeB;
    });

    return {
      safeBestBetsData,
      selectedDate,
      dateFilteredCoveragePerRace,
      filteredPool,
      filteredLiveBets,
      filteredTips,
      crowdOverrides,
      groupedByTrack,
      filteredBestPerTrack,
      sortedTracks,
    };
  }, [bestBetsData, selectedBestBetsDate, bestBetIdentityFilters, bestBetStrategyFilters]);

  const featuredStrideSelections = useMemo<FeaturedStrideSelection[]>(() => {
    const { filteredLiveBets, filteredTips } = filteredBestBetsView;

    if (filteredLiveBets.length === 0 && filteredTips.length === 0) {
      return [];
    }

    const { allSelections, bestBet, bestValue, earlyPlay } = buildBestBetsDeskHighlights(
      filteredLiveBets,
      filteredTips,
    );

    if (allSelections.length === 0) {
      return [];
    }

    const selectionMap = new Map<string, FeaturedStrideSelection>();
    [
      bestBet ? { label: "Best Bet", bet: bestBet } : null,
      bestValue ? { label: "Best Value", bet: bestValue } : null,
      earlyPlay ? { label: "Early Play", bet: earlyPlay } : null,
    ]
      .filter((entry): entry is { label: string; bet: BestBet } => Boolean(entry))
      .forEach(({ label, bet }) => {
        const existing = selectionMap.get(bet.id);
        if (existing) {
          existing.labels.push(label);
          return;
        }
        selectionMap.set(bet.id, { bet, labels: [label] });
      });

    return Array.from(selectionMap.values());
  }, [filteredBestBetsView]);

  const allFeaturedStrideLoaded = featuredStrideSelections.length > 0
    && featuredStrideSelections.every(({ bet }) => Boolean(strideExplanations[bet.id]));
  const anyFeaturedStrideLoading = featuredStrideSelections.some(({ bet }) => strideLoading.has(bet.id));

  const loadStrideForCard = async () => {
    for (const { bet } of featuredStrideSelections) {
      if (!strideExplanations[bet.id]) {
        await requestStrideExplanation(bet);
      }
    }
  };

  useEffect(() => {
    if (viewMode !== "best" || featuredStrideSelections.length === 0) {
      return;
    }

    const missingSelections = featuredStrideSelections.filter(({ bet }) => (
      !strideExplanations[bet.id] && !strideLoading.has(bet.id)
    ));

    if (missingSelections.length === 0) {
      return;
    }

    let cancelled = false;

    const loadMissingStride = async () => {
      for (const { bet } of missingSelections) {
        if (cancelled) {
          return;
        }
        await requestStrideExplanation(bet);
      }
    };

    void loadMissingStride();

    return () => {
      cancelled = true;
    };
  }, [featuredStrideSelections, strideExplanations, strideLoading, viewMode]);

  const generateInsightsMutation = useMutation({
    mutationFn: async () => {
      const response = await fetch('/api/stride-analyst/generate-insights', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          date: resolvedBestBetsDate || getAustralianDateStr(new Date()),
          force: false
        })
      });
      if (!response.ok) throw new Error('Failed to generate insights');
      return response.json();
    },
    onSuccess: (data) => {
      // Refresh bets to show new AI insights
      refetch();
      refetchBestBets();
      console.log(`Generated ${data.generated} AI insights`);
    },
  });

  const { data: simulationRacesData, isLoading: isLoadingSimulationRaces } = useQuery<SimulationRacesResponse>({
    queryKey: ["/api/simulation/races"],
    staleTime: 60 * 1000,
    enabled: viewMode === "best",
  });

  // Full-field race data with MC scores + AI reasoning for all runners
  const raceFieldDateParam = resolvedBestBetsDate;
  const { data: raceFieldData, refetch: refetchRaceField, isFetching: isFetchingRaceField } = useQuery<RaceFieldResponse>({
    queryKey: ["/api/race-field", raceFieldDateParam],
    queryFn: async () => {
      const res = await fetch(`/api/race-field/${raceFieldDateParam}`);
      return res.json();
    },
    staleTime: 5 * 60 * 1000,
    refetchInterval: 60 * 1000,
    refetchOnMount: "always",
    refetchOnWindowFocus: true,
    enabled: viewMode === "best" && !!raceFieldDateParam,
  });

  // Generate AI analysis for all runners in a race
  const generateAllRunnerInsightsMutation = useMutation({
    mutationFn: async ({ track, raceNumber, raceDate }: { track: string; raceNumber: number; raceDate: string }) => {
      const response = await fetch('/api/generate-all-insights', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ track, raceNumber, raceDate })
      });
      if (!response.ok) throw new Error('Failed to generate insights');
      return response.json();
    },
    onSuccess: () => {
      refetchRaceField();
    },
  });

  // Build lookup: "track-R{number}" -> enriched race field payload
  const raceFieldLookup = useMemo(() => {
    const map = new Map<string, RaceFieldData>();
    if (raceFieldData?.races) {
      for (const race of raceFieldData.races) {
        const key = `${race.track}-R${race.race_number}`;
        map.set(key, race);
      }
    }
    return map;
  }, [raceFieldData]);

  const refreshRacecardViews = async () => {
    await Promise.all([
      refetch(),
      refetchBestBets(),
      refetchRaceField(),
    ]);
  };

  // Only show upcoming races (filter out past races)
  const selections = useMemo(() => {
    if (betsData?.dataSource === "tips_pipeline") {
      return betsData?.bets || [];
    }

    const nowMs = Date.now();
    return (betsData?.bets || []).filter(s => {
      if (!s.offTime) return true; // Include if no off_time
      const raceTime = new Date(s.offTime).getTime();
      return raceTime > nowMs; // Only future races
    });
  }, [betsData]);

  // Get today's date in Sydney timezone
  const todayStr = useMemo(() => {
    return new Date().toLocaleDateString('en-AU', { timeZone: 'Australia/Sydney' });
  }, []);

  const simulationRaceLookup = useMemo(() => {
    const lookup = new Map<string, SimulationRace>();
    (simulationRacesData?.meets || []).forEach((meet) => {
      const meetTrack = normalizeTrackName(meet.course || "");
      const meetDate = meet.date || "";
      (meet.races || []).forEach((race) => {
        const raceNumber = String(race.race_number || "").replace(/^R/i, "");
        if (!meetTrack || !raceNumber || !meetDate) return;
        lookup.set(`${meetTrack}|${raceNumber}|${meetDate}`, race);
      });
    });
    return lookup;
  }, [simulationRacesData]);

  // Format countdown timer
  const formatCountdown = (offTime: string) => {
    const raceTime = new Date(offTime);
    const diff = raceTime.getTime() - now.getTime();
    if (diff <= 0) return "STARTED";

    const hours = Math.floor(diff / (1000 * 60 * 60));
    const minutes = Math.floor((diff % (1000 * 60 * 60)) / (1000 * 60));
    const seconds = Math.floor((diff % (1000 * 60)) / 1000);

    if (hours > 24) {
      const days = Math.floor(hours / 24);
      return `${days}d ${hours % 24}h`;
    }
    if (hours > 0) {
      return `${hours}h ${minutes}m`;
    }
    if (minutes > 0) {
      return `${minutes}m ${seconds}s`;
    }
    return `${seconds}s`;
  };

  // Check if race is starting soon (within 30 minutes)
  const isStartingSoon = (offTime: string) => {
    const raceTime = new Date(offTime);
    const diff = raceTime.getTime() - now.getTime();
    return diff > 0 && diff <= 30 * 60 * 1000;
  };

  // Get card accent color based on value rating (for left bar)
  const getCardAccentColor = (bet: Selection) => {
    switch (bet.valueRating) {
      case "Excellent": return "bg-green-500";
      case "Good": return "bg-blue-500";
      case "Fair": return "bg-yellow-500";
      default: return "bg-gray-500";
    }
  };

  // Get card wrapper style for top pick
  const getCardBorderStyle = (bet: Selection, isTopPick: boolean) => {
    if (isTopPick) return "border-2 border-yellow-500/70 shadow-[0_0_15px_rgba(234,179,8,0.3)]";
    return "";
  };

  // Toggle group collapsed state
  const toggleGroupCollapsed = (group: string) => {
    setCollapsedGroups(prev => {
      const next = new Set(prev);
      if (next.has(group)) next.delete(group);
      else next.add(group);
      return next;
    });
  };

  const uniqueTracks = useMemo(() => {
    const trackSet = new Set(
      selections
        .filter(s => !s.track.toLowerCase().includes('trial'))
        .map(s => s.track)
    );
    return Array.from(trackSet).sort();
  }, [selections]);

  // Extract unique dates from offTime with friendly labels
  const uniqueDates = useMemo(() => {
    const today = new Date();
    today.setHours(0, 0, 0, 0);
    const tomorrow = new Date(today);
    tomorrow.setDate(tomorrow.getDate() + 1);

    const dateMap = new Map<string, { date: Date; label: string }>();

    selections.forEach(s => {
      if (!s.offTime) return;
      const raceDate = new Date(s.offTime);
      const dateKey = raceDate.toISOString().split('T')[0]; // YYYY-MM-DD

      if (!dateMap.has(dateKey)) {
        const d = new Date(dateKey + 'T00:00:00');
        let label: string;

        if (d.getTime() === today.getTime()) {
          label = "Today";
        } else if (d.getTime() === tomorrow.getTime()) {
          label = "Tomorrow";
        } else {
          label = d.toLocaleDateString('en-AU', {
            weekday: 'short',
            day: 'numeric',
            month: 'short'
          });
        }

        dateMap.set(dateKey, { date: d, label });
      }
    });

    return Array.from(dateMap.entries())
      .sort((a, b) => a[1].date.getTime() - b[1].date.getTime())
      .map(([key, val]) => ({ value: key, label: val.label }));
  }, [selections]);

  const uniqueRunningStyles = useMemo(() => {
    const styleSet = new Set(selections.map(s => s.runningStyle).filter(Boolean));
    return Array.from(styleSet);
  }, [selections]);

  const uniqueValueRatings = useMemo(() => {
    const ratingOrder = ["Excellent", "Good", "Fair", "Poor", "high", "medium", "low"];
    const ratingSet = new Set(selections.map(s => s.valueRating).filter(Boolean));
    return Array.from(ratingSet).sort((a, b) => {
      const indexA = ratingOrder.indexOf(a);
      const indexB = ratingOrder.indexOf(b);
      if (indexA === -1 && indexB === -1) return a.localeCompare(b);
      if (indexA === -1) return 1;
      if (indexB === -1) return -1;
      return indexA - indexB;
    });
  }, [selections]);

  const filteredSelections = useMemo(() => {
    let result = [...selections];

    // Filter out trial races
    result = result.filter(s => {
      const trackLower = s.track.toLowerCase();
      const raceName = (s.raceName || '').toLowerCase();
      return !trackLower.includes('trial') && !raceName.includes('trial');
    });

    // Today only filter
    if (todayOnly) {
      result = result.filter(s => {
        const raceDate = new Date(s.offTime).toLocaleDateString('en-AU', { timeZone: 'Australia/Sydney' });
        return raceDate === todayStr;
      });
    }

    if (searchQuery) {
      const query = searchQuery.toLowerCase();
      result = result.filter(s =>
        s.horseName.toLowerCase().includes(query) ||
        s.jockey?.toLowerCase().includes(query) ||
        s.trainer?.toLowerCase().includes(query) ||
        s.track.toLowerCase().includes(query)
      );
    }

    if (trackFilter !== "all") {
      result = result.filter(s => s.track === trackFilter);
    }

    if (dateFilter !== "all") {
      result = result.filter(s => {
        if (!s.offTime) return false;
        const raceDate = new Date(s.offTime).toISOString().split('T')[0];
        return raceDate === dateFilter;
      });
    }

    if (confidenceFilter !== "all") {
      result = result.filter(s => s.confidence === confidenceFilter);
    }

    if (valueRatingFilter !== "all") {
      result = result.filter(s => s.valueRating === valueRatingFilter);
    }

    if (runningStyleFilter !== "all") {
      result = result.filter(s => s.runningStyle === runningStyleFilter);
    }

    if (minEdge > 0) {
      result = result.filter(s => s.edge >= minEdge);
    }

    result.sort((a, b) => {
      let comparison = 0;
      switch (sortBy) {
        case "ev":
          comparison = a.expectedValue - b.expectedValue;
          break;
        case "edge":
          comparison = a.edge - b.edge;
          break;
        case "winPct":
          comparison = a.winPercentage - b.winPercentage;
          break;
        case "time":
          comparison = new Date(a.offTime).getTime() - new Date(b.offTime).getTime();
          break;
        case "odds":
          comparison = a.marketOdds - b.marketOdds;
          break;
      }
      return sortOrder === "desc" ? -comparison : comparison;
    });

    return result;
  }, [selections, searchQuery, trackFilter, dateFilter, confidenceFilter, valueRatingFilter, runningStyleFilter, minEdge, sortBy, sortOrder, todayOnly, todayStr]);

  const groupedSelections = useMemo(() => {
    if (groupBy === "none") {
      return { "All Selections": filteredSelections };
    }

    const grouped: Record<string, Selection[]> = {};
    filteredSelections.forEach(s => {
      let key: string;
      if (groupBy === "track") {
        key = s.track;
      } else {
        // Group by date using offTime
        key = s.offTime ? formatDate(new Date(s.offTime).toISOString().split('T')[0]) : "Unknown";
      }
      if (!grouped[key]) grouped[key] = [];
      grouped[key].push(s);
    });

    return grouped;
  }, [filteredSelections, groupBy]);

  const stats = useMemo(() => {
    const totalBets = filteredSelections.length;
    const avgEV = totalBets > 0
      ? filteredSelections.reduce((sum, s) => sum + s.expectedValue, 0) / totalBets
      : 0;
    const avgEdge = totalBets > 0
      ? filteredSelections.reduce((sum, s) => sum + s.edge, 0) / totalBets
      : 0;
    const highConfidence = filteredSelections.filter(s => s.confidence === "high").length;
    const avgWinPct = totalBets > 0
      ? filteredSelections.reduce((sum, s) => sum + s.winPercentage, 0) / totalBets
      : 0;

    // Find the best pick (highest EV)
    const bestPick = filteredSelections.length > 0
      ? filteredSelections.reduce((best, s) => s.expectedValue > best.expectedValue ? s : best, filteredSelections[0])
      : null;

    // Find the next race to jump
    const nextRace = [...filteredSelections]
      .filter((selection) => selection.offTime && new Date(selection.offTime).getTime() > now.getTime())
      .sort((a, b) => new Date(a.offTime).getTime() - new Date(b.offTime).getTime())[0] || null;

    // Get best bet per track for grouping
    const bestPerTrack: Record<string, Selection> = {};
    filteredSelections.forEach(s => {
      if (!bestPerTrack[s.track] || s.expectedValue > bestPerTrack[s.track].expectedValue) {
        bestPerTrack[s.track] = s;
      }
    });

    return { totalBets, avgEV, avgEdge, highConfidence, avgWinPct, bestPick, nextRace, bestPerTrack };
  }, [filteredSelections, now]);

  const activeFiltersCount = [
    trackFilter !== "all",
    dateFilter !== "all",
    confidenceFilter !== "all",
    valueRatingFilter !== "all",
    runningStyleFilter !== "all",
    minEdge > 0,
    searchQuery.length > 0,
    todayOnly
  ].filter(Boolean).length;

  const clearAllFilters = () => {
    setSearchQuery("");
    setTrackFilter("all");
    setDateFilter("all");
    setConfidenceFilter("all");
    setValueRatingFilter("all");
    setRunningStyleFilter("all");
    setMinEdge(0);
    setTodayOnly(false);
  };

  const toggleCardExpanded = (id: string) => {
    setExpandedCards(prev => {
      const newSet = new Set(prev);
      if (newSet.has(id)) {
        newSet.delete(id);
      } else {
        newSet.add(id);
      }
      return newSet;
    });
  };

  function formatDate(dateStr: string) {
    const date = new Date(dateStr);
    return date.toLocaleDateString('en-AU', {
      weekday: 'short',
      day: 'numeric',
      month: 'short'
    });
  }

  function formatRaceTime(offTime: string | undefined) {
    if (!offTime) return null;
    try {
      const date = new Date(offTime);
      return date.toLocaleTimeString('en-AU', {
        hour: 'numeric',
        minute: '2-digit',
        hour12: true,
        timeZone: 'Australia/Sydney'
      });
    } catch {
      return null;
    }
  }

  const getConfidenceColor = (confidence: string) => {
    switch (confidence) {
      case "high": return "bg-success/20 text-success border-success/30";
      case "medium": return "bg-warning/20 text-warning border-warning/30";
      case "low": return "bg-danger/20 text-danger border-danger/30";
      default: return "bg-white/[0.06] text-white/40";
    }
  };

  const getValueRatingColor = (rating: string) => {
    switch (rating) {
      case "Excellent":
      case "high": return "text-success";
      case "Good":
      case "medium": return "text-warning";
      case "Fair": return "text-yellow-400";
      case "Poor":
      case "low": return "text-white/40";
      default: return "text-white/40";
    }
  };

  const getEVColor = (ev: number) => {
    if (ev >= 15) return "text-success";
    if (ev >= 8) return "text-warning";
    return "text-danger";
  };

  const getStabilityColor = (score: number) => {
    if (score < 50) return "text-success";
    if (score < 70) return "text-warning";
    return "text-danger";
  };

  const getRunningStyleLabel = (style: string) => {
    const labels: Record<string, string> = {
      'leader': 'Leader',
      'on_pace': 'On Pace',
      'stalker': 'Stalker',
      'midfield': 'Midfield',
      'backmarker': 'Backmarker',
      'closer': 'Closer'
    };
    return labels[style] || style;
  };

  const toggleBestBetCard = (id: string) => {
    setExpandedBestBets(prev => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  };

  const getBetTypeInfo = (betType: string, edge?: number) => {
    const hasBettingEdge = edge != null && edge > 0;
    switch (betType) {
      case "banker":
        return { label: "BANKER", color: "bg-green-500/30 text-green-300 border-green-400/60", desc: "Dominant short-price pick — multi-layer consensus confirmed", isTip: true };
      case "value":
        return { label: "VALUE", color: "bg-emerald-500/20 text-emerald-400 border-emerald-500/50", desc: hasBettingEdge ? "Positive edge confirmed — worth a bet" : "Value signal but edge not confirmed — keep an eye on", isTip: hasBettingEdge };
      case "model_pick":
        return { label: "MODEL", color: "bg-cyan-500/20 text-cyan-400 border-cyan-500/50", desc: hasBettingEdge ? "MC + ML model says bet — positive edge" : "Model selected but no clear edge — watch this one", isTip: hasBettingEdge };
      case "roughie":
        return { label: "ROUGHIE", color: "bg-purple-500/20 text-purple-400 border-purple-500/50", desc: hasBettingEdge ? "Longer odds with genuine edge — worth a bet" : "Longer odds with some positive signals — keep an eye on", isTip: hasBettingEdge };
      case "form_pick":
        return { label: "FORM", color: "bg-orange-500/20 text-orange-400 border-orange-500/50", desc: hasBettingEdge ? "Strong form with betting edge — back this one" : "Strong form but no clear edge — watch closely", isTip: hasBettingEdge };
      case "tip_only":
        return { label: "GUIDE", color: "bg-amber-500/20 text-amber-400 border-amber-500/30", desc: "Keep an eye on — does not have a clear betting edge", isTip: false };
      default:
        return { label: hasBettingEdge ? "LIVE" : "WATCH", color: hasBettingEdge ? "bg-green-500/20 text-green-400 border-green-500/30" : "bg-amber-500/20 text-amber-400 border-amber-500/30", desc: hasBettingEdge ? "Model says this horse has value — worth a bet" : "Selected but no confirmed edge — keep an eye on", isTip: hasBettingEdge };
    }
  };

  const getRiskColor = (risk: string) => {
    switch (risk) {
      case "low": return "text-green-400";
      case "medium": return "text-yellow-400";
      case "high": return "text-red-400";
      default: return "text-white/40";
    }
  };

  const isRaceDayRoute = location === "/race-day";
  const pageTitle = viewMode === "blackbook" ? "BLACKBOOK" : isRaceDayRoute ? "RACE DAY" : "THE TRACK BOARD";
  const pageSubtitle = viewMode === "blackbook"
    ? "Structured market memory and readiness alerts"
    : isRaceDayRoute
      ? "Full card lane and runner intelligence across every race"
      : "Daily execution across metro cards";
  const commandDeckSummary = useMemo(() => {
    if (viewMode !== "best") {
      return null;
    }

    const { selectedDate, filteredPool, filteredLiveBets, filteredTips, groupedByTrack, sortedTracks } = filteredBestBetsView;
    const todayStr = getAustralianDateStr(new Date());
    const tomorrow = new Date();
    tomorrow.setDate(tomorrow.getDate() + 1);
    const tomorrowStr = getAustralianDateStr(tomorrow);
    const summaryDateLabel = selectedDate === todayStr
      ? "Today"
      : selectedDate === tomorrowStr
        ? "Tomorrow"
        : new Date(selectedDate + "T00:00:00").toLocaleDateString("en-AU", {
            weekday: "short",
            day: "numeric",
            month: "short",
          });
    const nowMs = now.getTime();
    const compareExecutionPriority = (left: BestBet, right: BestBet) => {
      const leftIsLive = left.shouldBet !== false ? 1 : 0;
      const rightIsLive = right.shouldBet !== false ? 1 : 0;
      if (rightIsLive !== leftIsLive) {
        return rightIsLive - leftIsLive;
      }

      const leftJumpMs = new Date(left.offTime).getTime();
      const rightJumpMs = new Date(right.offTime).getTime();
      const leftIsUpcoming = leftJumpMs > nowMs ? 1 : 0;
      const rightIsUpcoming = rightJumpMs > nowMs ? 1 : 0;
      if (rightIsUpcoming !== leftIsUpcoming) {
        return rightIsUpcoming - leftIsUpcoming;
      }

      if (leftIsUpcoming && rightIsUpcoming && leftJumpMs !== rightJumpMs) {
        return leftJumpMs - rightJumpMs;
      }

      if ((right.edge || 0) !== (left.edge || 0)) {
        return (right.edge || 0) - (left.edge || 0);
      }

      if ((right.confidenceScore || 0) !== (left.confidenceScore || 0)) {
        return (right.confidenceScore || 0) - (left.confidenceScore || 0);
      }

      return sortByJumpTime(left, right);
    };

    const liveActionableBets = filteredLiveBets.filter((bet) => bet.shouldBet !== false);
    const upcomingActionableBets = [...liveActionableBets]
      .filter((bet) => new Date(bet.offTime).getTime() > nowMs)
      .sort(compareExecutionPriority);
    const nextLiveBet = upcomingActionableBets[0] || null;
    const bestEdgeSelection = [...liveActionableBets]
      .filter((bet) => (bet.edge || 0) > 0)
      .sort((a, b) => {
        if ((b.edge || 0) !== (a.edge || 0)) {
          return (b.edge || 0) - (a.edge || 0);
        }
        return compareBestBetTrackAnchors(a, b);
      })[0] || null;
    const meetingSummaries = sortedTracks
      .map((trackName) => {
        const trackBets = groupedByTrack[trackName] || [];
        const liveTrackBets = trackBets.filter((bet) => bet.shouldBet !== false);
        const bestTrackEdge = liveTrackBets.reduce((best, bet) => Math.max(best, bet.edge || 0), Number.NEGATIVE_INFINITY);
        const meetingConfidence = liveTrackBets.length > 0
          ? liveTrackBets.reduce((sum, bet) => sum + (bet.confidenceScore || 0), 0) / liveTrackBets.length
          : 0;
        const meetingStrengthScore = (liveTrackBets.length * 90)
          + (Number.isFinite(bestTrackEdge) ? Math.max(bestTrackEdge, 0) * 8 : 0)
          + meetingConfidence;

        return {
          trackName,
          liveBetCount: liveTrackBets.length,
          bestEdge: Number.isFinite(bestTrackEdge) ? bestTrackEdge : 0,
          meetingStrengthScore,
        };
      })
      .sort((a, b) => {
        if (b.meetingStrengthScore !== a.meetingStrengthScore) {
          return b.meetingStrengthScore - a.meetingStrengthScore;
        }
        return a.trackName.localeCompare(b.trackName);
      });

    return {
      summaryDateLabel,
      nextLiveBet,
      bestEdgeSelection,
      strongestMeeting: meetingSummaries[0] || null,
      liveBetsLeft: upcomingActionableBets.length,
      blockedMarkets: filteredPool.filter((bet) => bet.selectionOrigin === "market_unavailable").length,
      watchOnlyCount: filteredTips.length,
      trackCount: sortedTracks.length,
    };
  }, [filteredBestBetsView, now, viewMode]);
  const commandDeckMetrics = viewMode === "best" && commandDeckSummary ? [
    {
      key: "next-live",
      label: "Next live bet",
      value: commandDeckSummary.nextLiveBet ? commandDeckSummary.nextLiveBet.horseName : "No live bet",
      detail: commandDeckSummary.nextLiveBet
        ? `${commandDeckSummary.nextLiveBet.track} R${commandDeckSummary.nextLiveBet.raceNumber} • ${formatCountdown(commandDeckSummary.nextLiveBet.offTime)}`
        : "No upcoming actionable runner on this board.",
      toneClass: "border-racing-orange/20 bg-racing-orange/8 text-racing-orange",
      icon: Timer,
    },
    {
      key: "best-edge",
      label: "Best edge",
      value: commandDeckSummary.bestEdgeSelection
        ? `${commandDeckSummary.bestEdgeSelection.edge >= 0 ? "+" : ""}${commandDeckSummary.bestEdgeSelection.edge.toFixed(1)}%`
        : "No edge",
      detail: commandDeckSummary.bestEdgeSelection
        ? `${commandDeckSummary.bestEdgeSelection.horseName} • ${commandDeckSummary.bestEdgeSelection.track} R${commandDeckSummary.bestEdgeSelection.raceNumber}`
        : "No live runner is beating the market right now.",
      toneClass: "border-emerald-500/20 bg-emerald-500/8 text-emerald-300",
      icon: TrendingUp,
    },
    {
      key: "strongest-meeting",
      label: "Strongest meeting",
      value: commandDeckSummary.strongestMeeting ? commandDeckSummary.strongestMeeting.trackName : "No meeting",
      detail: commandDeckSummary.strongestMeeting
        ? `${commandDeckSummary.strongestMeeting.liveBetCount} live • top edge ${commandDeckSummary.strongestMeeting.bestEdge >= 0 ? "+" : ""}${commandDeckSummary.strongestMeeting.bestEdge.toFixed(1)}%`
        : "Meeting strength appears once the board is loaded.",
      toneClass: "border-cyan-500/20 bg-cyan-500/8 text-cyan-300",
      icon: MapPin,
    },
    {
      key: "board-status",
      label: "Board status",
      value: `${commandDeckSummary.liveBetsLeft} live / ${commandDeckSummary.watchOnlyCount} watch`,
      detail: `${commandDeckSummary.trackCount} tracks • ${commandDeckSummary.blockedMarkets} blocked markets`,
      toneClass: "border-white/10 bg-white/[0.04] text-white",
      icon: Activity,
    },
  ] : [];

  return (
    <div className="min-h-screen bg-black font-dm" data-testid="page-bets">
      <header
        className="page-header-liquid px-4 py-3 backdrop-blur-xl"
        style={{ position: "relative", top: "auto", zIndex: 20 }}
      >
        <div className="max-w-7xl mx-auto">
          <div className="overflow-hidden rounded-[30px] border border-white/[0.08] bg-[radial-gradient(circle_at_top_left,rgba(249,115,22,0.18),transparent_34%),radial-gradient(circle_at_top_right,rgba(234,179,8,0.12),transparent_32%),linear-gradient(180deg,rgba(255,255,255,0.05),rgba(255,255,255,0.015))] shadow-[0_24px_80px_rgba(0,0,0,0.38)]">
            <div className="px-4 py-4 sm:px-6 sm:py-5">
              <div className="flex flex-col gap-4 xl:flex-row xl:items-start xl:justify-between">
                <div className="min-w-0 space-y-4">
                  <div className="flex items-start gap-3">
                    <Link href="/">
                      <Button
                        variant="ghost"
                        size="icon"
                        className="h-11 w-11 rounded-2xl border border-white/10 bg-black/25 text-white/80 hover:bg-white/[0.06] hover:text-white"
                        data-testid="button-back"
                      >
                        <ArrowLeft className="h-5 w-5" />
                      </Button>
                    </Link>

                    <div className="min-w-0">
                      <div className="flex flex-wrap items-center gap-2">
                        <Badge className="border-racing-orange/25 bg-racing-orange/10 text-racing-orange">
                          {viewMode === "blackbook" ? "Memory Desk" : isRaceDayRoute ? "Full Card Lane" : "Metro Execution"}
                        </Badge>
                        {viewMode === "best" && commandDeckSummary && (
                          <Badge className="border-white/10 bg-white/[0.04] text-white/65">
                            {commandDeckSummary.summaryDateLabel}
                          </Badge>
                        )}
                      </div>

                      <div className="mt-3 flex items-center gap-3">
                        {viewMode === "blackbook" ? (
                          <BookMarked className="h-6 w-6 text-racing-gold" />
                        ) : isRaceDayRoute ? (
                          <Flag className="h-6 w-6 text-racing-gold" />
                        ) : (
                          <Trophy className="h-6 w-6 text-racing-gold" />
                        )}
                        <div className="min-w-0">
                          <h1 className="text-xl sm:text-2xl font-syne font-extrabold tracking-tight text-white">
                            {pageTitle}
                          </h1>
                          <p className="text-sm text-white/50">
                            {pageSubtitle}
                          </p>
                        </div>
                      </div>
                    </div>
                  </div>

                  {viewMode === "best" && commandDeckSummary && (
                    <div className="flex flex-wrap items-center gap-2">
                      <div className="inline-flex items-center gap-2 rounded-full border border-emerald-500/20 bg-emerald-500/10 px-3 py-1.5 text-xs text-emerald-300">
                        <span className="h-2 w-2 rounded-full bg-emerald-300 animate-pulse" />
                        {commandDeckSummary.liveBetsLeft} live bets left
                      </div>
                      <div className="inline-flex items-center gap-2 rounded-full border border-white/10 bg-black/20 px-3 py-1.5 text-xs text-white/65">
                        <Calendar className="h-3.5 w-3.5 text-racing-orange" />
                        {commandDeckSummary.trackCount} tracks loaded
                      </div>
                      {commandDeckSummary.nextLiveBet && (
                        <div className="inline-flex items-center gap-2 rounded-full border border-racing-orange/20 bg-racing-orange/10 px-3 py-1.5 text-xs text-racing-orange">
                          <Clock className="h-3.5 w-3.5" />
                          {commandDeckSummary.nextLiveBet.track} R{commandDeckSummary.nextLiveBet.raceNumber} • {formatCountdown(commandDeckSummary.nextLiveBet.offTime)}
                        </div>
                      )}
                    </div>
                  )}
                </div>

                <div className="flex flex-wrap items-center gap-2 xl:justify-end">
                  <Button
                    variant="outline"
                    size="sm"
                    onClick={async () => {
                      if (viewMode === "blackbook") {
                        window.dispatchEvent(new Event("stride:blackbook-refresh"));
                        return;
                      }
                      await refreshRacecardViews();
                    }}
                    disabled={isRefetching || isFetchingBets || isFetchingBestBets || isFetchingRaceField}
                    className="h-11 rounded-2xl border-white/10 bg-black/20 px-4 text-white/70 hover:bg-white/[0.06] hover:text-white"
                    data-testid="button-refresh"
                  >
                    <RefreshCw className={`h-4 w-4 ${(isRefetching || isFetchingBets || isFetchingBestBets || isFetchingRaceField) ? "animate-spin" : ""}`} />
                    <span className="hidden sm:inline">REFRESH</span>
                  </Button>
                  <Button
                    variant="default"
                    size="sm"
                    onClick={() => runPipelineMutation.mutate()}
                    disabled={runPipelineMutation.isPending}
                    className="h-11 rounded-2xl bg-[linear-gradient(135deg,#f59e0b,#f97316)] px-5 text-black shadow-[0_16px_34px_rgba(249,115,22,0.28)]"
                    data-testid="button-run-pipeline"
                  >
                    {runPipelineMutation.isPending ? (
                      <>
                        <Loader2 className="h-4 w-4 animate-spin" />
                        <span className="hidden sm:inline">ANALYSING...</span>
                      </>
                    ) : (
                      <>
                        <Zap className="h-4 w-4" />
                        <span className="hidden sm:inline">RUN ANALYSIS</span>
                      </>
                    )}
                  </Button>
                  {viewMode === "best" && (
                    <Button
                      variant="outline"
                      size="sm"
                      onClick={() => generateInsightsMutation.mutate()}
                      disabled={generateInsightsMutation.isPending}
                      className="h-11 rounded-2xl border-racing-gold/30 bg-racing-gold/10 px-4 text-racing-gold hover:bg-racing-gold/15"
                      data-testid="button-generate-insights"
                    >
                      {generateInsightsMutation.isPending ? (
                        <>
                          <Loader2 className="h-4 w-4 animate-spin" />
                          <span className="hidden sm:inline">GENERATING...</span>
                        </>
                      ) : (
                        <>
                          <Brain className="h-4 w-4" />
                          <span className="hidden sm:inline">DESK AI</span>
                        </>
                      )}
                    </Button>
                  )}
                </div>
              </div>

              <div className="mt-5 flex flex-col gap-4 xl:flex-row xl:items-center xl:justify-between">
                <div className="inline-flex flex-wrap items-center gap-1.5 rounded-[24px] border border-white/[0.08] bg-black/35 p-1.5 shadow-[inset_0_1px_0_rgba(255,255,255,0.04)]">
                  <Button
                    variant="ghost"
                    size="sm"
                    onClick={() => {
                      setViewMode("blackbook");
                      navigate("/blackbook");
                    }}
                    className={`h-11 rounded-[18px] px-4 text-xs font-semibold uppercase tracking-[0.14em] ${viewMode === "blackbook" ? "bg-white text-black shadow-sm" : "text-white/60 hover:bg-white/[0.06] hover:text-white"}`}
                    data-testid="button-view-value"
                  >
                    <BookMarked className="h-4 w-4" />
                    BLACKBOOK
                  </Button>
                  <Button
                    variant="ghost"
                    size="sm"
                    onClick={() => {
                      setViewMode("best");
                      setTopSelectionsView("trackBest");
                      navigate("/best-bets");
                    }}
                    className={`h-11 rounded-[18px] px-4 text-xs font-semibold uppercase tracking-[0.14em] ${viewMode === "best" && !isRaceDayRoute ? "bg-[linear-gradient(135deg,rgba(249,115,22,0.95),rgba(250,204,21,0.92))] text-black shadow-[0_12px_30px_rgba(249,115,22,0.26)]" : "text-white/60 hover:bg-white/[0.06] hover:text-white"}`}
                    data-testid="button-view-best"
                  >
                    <Trophy className="h-4 w-4" />
                    THE TRACK BOARD
                  </Button>
                  <Button
                    variant="ghost"
                    size="sm"
                    onClick={() => {
                      setViewMode("best");
                      setTopSelectionsView("racecardTips");
                      navigate("/race-day");
                    }}
                    className={`h-11 rounded-[18px] px-4 text-xs font-semibold uppercase tracking-[0.14em] ${isRaceDayRoute ? "bg-white text-black shadow-sm" : "text-white/60 hover:bg-white/[0.06] hover:text-white"}`}
                    data-testid="button-view-race-day"
                  >
                    <Flag className="h-4 w-4" />
                    RACE DAY
                  </Button>

                  <BetSlip />

                  <Link href="/calibration">
                    <Button
                      variant="ghost"
                      size="sm"
                      className="h-11 rounded-[18px] px-4 text-xs font-semibold uppercase tracking-[0.14em] text-white/50 hover:bg-white/[0.06] hover:text-white"
                      data-testid="button-calibration"
                    >
                      <BarChart3 className="h-4 w-4" />
                      <span className="hidden sm:inline">CALIBRATION</span>
                    </Button>
                  </Link>
                </div>

                <div className="flex flex-wrap items-center gap-2 text-xs text-white/45">
                  <div className="inline-flex items-center gap-2 rounded-full border border-white/10 bg-black/20 px-3 py-1.5">
                    <Layers className="h-3.5 w-3.5 text-white/55" />
                    One deck for navigation, action, and race-day context
                  </div>
                </div>
              </div>

              {viewMode === "best" && commandDeckMetrics.length > 0 && (
                <div className="mt-5 grid grid-cols-1 gap-3 md:grid-cols-2 xl:grid-cols-4">
                  {commandDeckMetrics.map(({ key, label, value, detail, toneClass, icon: Icon }) => (
                    <div
                      key={key}
                      className={`rounded-2xl border px-4 py-4 ${toneClass}`}
                      data-testid={`card-command-deck-${key}`}
                    >
                      <div className="flex items-center gap-2 text-[11px] uppercase tracking-[0.18em] opacity-80">
                        <Icon className="h-3.5 w-3.5" />
                        {label}
                      </div>
                      <p className="mt-2 text-lg font-semibold text-white">{value}</p>
                      <p className="mt-1 text-sm leading-6 text-white/55">{detail}</p>
                    </div>
                  ))}
                </div>
              )}
            </div>
          </div>
        </div>
      </header>
      <main className="max-w-7xl mx-auto p-4 sm:p-6 space-y-4">
        {/* BEST BETS VIEW */}
        {viewMode === "best" && (
          <>
            {/* Best Bets Content */}
            {isLoadingBestBets ? (
              <div className="flex items-center justify-center py-20">
                <Loader2 className="h-8 w-8 animate-spin text-yellow-500" />
                <span className="ml-3 text-white/40">Analyzing all tracks...</span>
              </div>
            ) : !(bestBetsData?.coveragePerRace?.length || bestBetsData?.oneBetPerRace?.length) ? (
              <Card className="bg-white/[0.03]">
                <CardContent className="py-12 text-center">
                  <AlertTriangle className="h-12 w-12 mx-auto mb-4 text-yellow-500/50" />
                  <h3 className="text-lg font-semibold mb-2">No Track Board Available</h3>
                  <p className="text-sm text-white/40 max-w-md mx-auto">
                    No races have been processed yet. Try refreshing or run the pipeline to analyze upcoming races.
                  </p>
                </CardContent>
              </Card>
            ) : (
              <>
                {/* RACE DAY FILTER */}
                {(() => {
                  const {
                    safeBestBetsData,
                    selectedDate,
                    filteredPool: filteredBetsPerRace,
                    filteredLiveBets: filteredBestBets,
                    filteredTips,
                    filteredBestPerTrack,
                    groupedByTrack,
                    sortedTracks,
                  } = filteredBestBetsView;
                  const hasActiveBestBetFilters = activeBestBetFilterCount > 0;
                  const todayStr = getAustralianDateStr(new Date());
                  const tomorrow = new Date();
                  tomorrow.setDate(tomorrow.getDate() + 1);
                  const tomorrowStr = getAustralianDateStr(tomorrow);

                  const formatDateLabel = (dateStr: string) => {
                    if (dateStr === todayStr) return "TODAY";
                    if (dateStr === tomorrowStr) return "TOMORROW";
                    const date = new Date(dateStr + 'T00:00:00');
                    return date.toLocaleDateString('en-AU', { weekday: 'short', day: 'numeric', month: 'short' }).toUpperCase();
                  };

                  const deskHighlights = buildBestBetsDeskHighlights(filteredBestBets, filteredTips);
                  const {
                    bestBet: bestBetSelection,
                    bestValue: bestValueSelection,
                    earlyPlay: earlyPlaySelection,
                  } = deskHighlights;
                  const resolvedRacecardLaneConfidenceSort = ["time", "desc", "asc"].includes(racecardLaneConfidenceSort)
                    ? racecardLaneConfidenceSort
                    : "time";
                  const summaryDateLabel = selectedDate === todayStr
                    ? "Today"
                    : selectedDate === tomorrowStr
                      ? "Tomorrow"
                      : new Date(selectedDate + 'T00:00:00').toLocaleDateString('en-AU', {
                          weekday: 'long',
                          day: 'numeric',
                          month: 'long',
                        });
                  const bankerCount = filteredBetsPerRace.filter((bet) => bet.betType === "banker").length;
                  const exoticCount = filteredBetsPerRace.filter((bet) => bet.selectionType === "exoticTip").length;
                  const racecardLaneCount = filteredBetsPerRace.length;
                  const allTracksForDate = sortedTracks;
                  const averageConfidence = filteredBetsPerRace.length > 0
                    ? filteredBetsPerRace.reduce((sum, bet) => sum + (bet.confidenceScore || 0), 0) / filteredBetsPerRace.length
                    : 0;
                  const nowMs = now.getTime();
                  const compareExecutionPriority = (left: BestBet, right: BestBet) => {
                    const leftIsLive = left.shouldBet !== false ? 1 : 0;
                    const rightIsLive = right.shouldBet !== false ? 1 : 0;
                    if (rightIsLive !== leftIsLive) {
                      return rightIsLive - leftIsLive;
                    }

                    const leftJumpMs = new Date(left.offTime).getTime();
                    const rightJumpMs = new Date(right.offTime).getTime();
                    const leftIsUpcoming = leftJumpMs > nowMs ? 1 : 0;
                    const rightIsUpcoming = rightJumpMs > nowMs ? 1 : 0;
                    if (rightIsUpcoming !== leftIsUpcoming) {
                      return rightIsUpcoming - leftIsUpcoming;
                    }

                    if (leftIsUpcoming && rightIsUpcoming && leftJumpMs !== rightJumpMs) {
                      return leftJumpMs - rightJumpMs;
                    }

                    const leftPositiveEdge = left.edge > 0 ? 1 : 0;
                    const rightPositiveEdge = right.edge > 0 ? 1 : 0;
                    if (rightPositiveEdge !== leftPositiveEdge) {
                      return rightPositiveEdge - leftPositiveEdge;
                    }

                    if ((right.edge || 0) !== (left.edge || 0)) {
                      return (right.edge || 0) - (left.edge || 0);
                    }

                    if ((right.confidenceScore || 0) !== (left.confidenceScore || 0)) {
                      return (right.confidenceScore || 0) - (left.confidenceScore || 0);
                    }

                    return sortByJumpTime(left, right);
                  };
                  const liveActionableBets = filteredBestBets.filter((bet) => bet.shouldBet !== false);
                  const upcomingActionableBets = [...liveActionableBets]
                    .filter((bet) => new Date(bet.offTime).getTime() > nowMs)
                    .sort(compareExecutionPriority);
                  const nextLiveBet = upcomingActionableBets[0] || null;
                  const bestEdgeSelection = [...liveActionableBets]
                    .filter((bet) => (bet.edge || 0) > 0)
                    .sort((a, b) => {
                      if ((b.edge || 0) !== (a.edge || 0)) {
                        return (b.edge || 0) - (a.edge || 0);
                      }
                      return compareBestBetTrackAnchors(a, b);
                    })[0] || null;
                  const bestBankerSelection = [...liveActionableBets]
                    .filter((bet) => bet.betType === "banker")
                    .sort(compareBestBetRaceSelections)[0] || null;
                  const actNowSelection = [...liveActionableBets]
                    .sort(compareExecutionPriority)[0] || null;
                  const liveBetsLeft = upcomingActionableBets.length;
                  const marketUnavailableCount = filteredBetsPerRace.filter((bet) => bet.selectionOrigin === "market_unavailable").length;
                  const guideOnlyNoEdgeCount = filteredTips.filter((bet) => (bet.edge || 0) <= 0).length;
                  const nextToJump = upcomingActionableBets.slice(0, 6);
                  const shortestFairOverlay = [...liveActionableBets]
                    .filter((bet) => (bet.edge || 0) > 0 && Boolean(getBestBetFairOdds(bet)))
                    .sort((a, b) => {
                      const fairA = getBestBetFairOdds(a) ?? Number.POSITIVE_INFINITY;
                      const fairB = getBestBetFairOdds(b) ?? Number.POSITIVE_INFINITY;
                      if (fairA !== fairB) return fairA - fairB;
                      return (b.edge || 0) - (a.edge || 0);
                    })[0] || null;
                  const longestLiveValuePrice = [...liveActionableBets]
                    .filter((bet) => (bet.edge || 0) > 0 && (bet.marketOdds || 0) > 0)
                    .sort((a, b) => {
                      if ((b.marketOdds || 0) !== (a.marketOdds || 0)) {
                        return (b.marketOdds || 0) - (a.marketOdds || 0);
                      }
                      return (b.edge || 0) - (a.edge || 0);
                    })[0] || null;
                  const shortestNoBetFavourite = [...filteredTips]
                    .filter((bet) => (bet.marketOdds || 0) > 0)
                    .sort((a, b) => {
                      if ((a.marketOdds || 0) !== (b.marketOdds || 0)) {
                        return (a.marketOdds || 0) - (b.marketOdds || 0);
                      }
                      return (b.winPercentage || 0) - (a.winPercentage || 0);
                    })[0] || null;
                  const meetingOpportunityCards = sortedTracks
                    .map((trackName) => {
                      const trackBets = groupedByTrack[trackName] || [];
                      const liveTrackBets = trackBets.filter((bet) => bet.shouldBet !== false);
                      const positiveEdgeCount = liveTrackBets.filter((bet) => (bet.edge || 0) > 0).length;
                      const bestTrackEdge = liveTrackBets.reduce((best, bet) => Math.max(best, bet.edge || 0), Number.NEGATIVE_INFINITY);
                      const bestTrackSelection = [...(liveTrackBets.length > 0 ? liveTrackBets : trackBets)]
                        .sort(compareBestBetRaceSelections)[0] || null;
                      const firstJumpBet = [...trackBets].sort(sortByJumpTime)[0] || null;
                      const meetingConfidence = liveTrackBets.length > 0
                        ? liveTrackBets.reduce((sum, bet) => sum + (bet.confidenceScore || 0), 0) / liveTrackBets.length
                        : (trackBets.reduce((sum, bet) => sum + (bet.confidenceScore || 0), 0) / Math.max(trackBets.length, 1));
                      const meetingStrengthScore = (liveTrackBets.length * 90)
                        + (positiveEdgeCount * 28)
                        + (Number.isFinite(bestTrackEdge) ? Math.max(bestTrackEdge, 0) * 8 : 0)
                        + meetingConfidence;

                      return {
                        trackName,
                        liveBetCount: liveTrackBets.length,
                        bestEdge: Number.isFinite(bestTrackEdge) ? bestTrackEdge : 0,
                        meetingConfidence,
                        meetingStrengthScore,
                        bestTrackSelection,
                        firstJumpBet,
                        positiveEdgeCount,
                      };
                    })
                    .sort((a, b) => {
                      if (b.meetingStrengthScore !== a.meetingStrengthScore) {
                        return b.meetingStrengthScore - a.meetingStrengthScore;
                      }
                      return a.trackName.localeCompare(b.trackName);
                    });
                  const strongestMeeting = meetingOpportunityCards[0] || null;
                  const executionBoardHeadline = actNowSelection
                    ? `${actNowSelection.horseName} is the first live call on ${summaryDateLabel}'s board.`
                    : bestBetSelection
                      ? `${bestBetSelection.horseName} owns the strongest overall case on this card.`
                      : `${summaryDateLabel}'s card is leaning more on guide-only coverage than hard live bets.`;
                  const executionBoardSummary = actNowSelection
                    ? `${actNowSelection.track} R${actNowSelection.raceNumber} is the immediate decision point${bestValueSelection ? `, while ${bestValueSelection.horseName} still carries the best outright price edge.` : "."}`
                    : bestValueSelection
                      ? `${bestValueSelection.horseName} is the best overlay available, but there is no urgent near-term live bet on the board.`
                      : `${filteredBetsPerRace.length} races across ${allTracksForDate.length} tracks are loaded, with ${filteredTips.length} guide-only runners currently carrying the card.`;
                  const resolvedRacecardLaneTrackFilter = allTracksForDate.includes(racecardLaneTrackFilter)
                    ? racecardLaneTrackFilter
                    : "all";
                  const racecardLaneSource = filteredBetsPerRace;
                  const filteredRacecardTips = resolvedRacecardLaneTrackFilter === "all"
                    ? racecardLaneSource
                    : racecardLaneSource.filter((bet) => bet.track === resolvedRacecardLaneTrackFilter);
                  const topSectionBets = topSelectionsView === "trackBest" ? filteredBestPerTrack : filteredRacecardTips;
                  const sortedTopSectionBets = [...topSectionBets].sort((a, b) => {
                    if (topSelectionsView === "racecardTips") {
                      const confidenceA = a.confidenceScore ?? 0;
                      const confidenceB = b.confidenceScore ?? 0;
                      if (resolvedRacecardLaneConfidenceSort === "desc" && confidenceA !== confidenceB) {
                        return confidenceB - confidenceA;
                      }
                      if (resolvedRacecardLaneConfidenceSort === "asc" && confidenceA !== confidenceB) {
                        return confidenceA - confidenceB;
                      }
                    }
                    return new Date(a.offTime).getTime() - new Date(b.offTime).getTime();
                  });
                  const topSectionTitle = topSelectionsView === "trackBest"
                    ? "BEST BET AT EACH TRACK"
                    : resolvedRacecardLaneTrackFilter === "all"
                      ? "FINAL RACE PREDICTIONS ACROSS ALL TRACKS"
                      : `FINAL PREDICTIONS • ${resolvedRacecardLaneTrackFilter.toUpperCase()}`;
                  const topSectionDescription = topSelectionsView === "trackBest"
                    ? "One anchor per venue, built from the strongest daily model or analyst position."
                    : resolvedRacecardLaneTrackFilter === "all"
                      ? "One final predicted horse for every race, with true bets still highlighted separately."
                      : `Showing every final predicted horse at ${resolvedRacecardLaneTrackFilter} for the selected day.`;

                  const selectedTrack = allTracksForDate.includes(selectedBestTrack)
                    ? selectedBestTrack
                    : (allTracksForDate[0] || "");

                  const racesAtSelectedTrack = selectedTrack
                    ? filteredBetsPerRace
                        .filter((bet) => bet.track === selectedTrack)
                        .reduce((acc: Record<string, BestBet[]>, bet) => {
                          const raceDate = bet.raceDate || getAustralianDateStr(new Date(bet.offTime));
                          const raceKey = `${bet.track}-R${bet.raceNumber}-${raceDate}`;
                          if (!acc[raceKey]) acc[raceKey] = [];
                          acc[raceKey].push(bet);
                          return acc;
                        }, {})
                    : {};

                  const raceOptions = Object.entries(racesAtSelectedTrack)
                    .map(([raceKey, bets]) => {
                      const raceBet = bets[0];
                      const raceDate = raceBet.raceDate || getAustralianDateStr(new Date(raceBet.offTime));
                      const lookupKey = `${normalizeTrackName(raceBet.track)}|${raceBet.raceNumber}|${raceDate}`;
                      const simulationRace = simulationRaceLookup.get(lookupKey);

                      const runnerCountFromTips = Math.max(...bets.map((b) => b.fieldSize || 0), 0);
                      const runnerCount = simulationRace?.runnerCount
                        ?? simulationRace?.runners?.length
                        ?? runnerCountFromTips;

                      return {
                        key: raceKey,
                        raceNumber: raceBet.raceNumber,
                        raceName: raceBet.raceName,
                        offTime: raceBet.offTime,
                        distance: raceBet.distance,
                        bets,
                        runners: simulationRace?.runners || [],
                        runnerCount,
                        going: simulationRace?.going,
                        className: simulationRace?.class,
                      };
                    })
                    .sort((a, b) => {
                      const raceNumberDiff = (a.raceNumber ?? 0) - (b.raceNumber ?? 0);
                      if (raceNumberDiff !== 0) return raceNumberDiff;
                      return new Date(a.offTime).getTime() - new Date(b.offTime).getTime();
                    });

                  const selectedRaceKey = raceOptions.some((race) => race.key === selectedBestRaceKey)
                    ? selectedBestRaceKey
                    : (raceOptions[0]?.key || "");

                  const selectedRace = raceOptions.find((race) => race.key === selectedRaceKey);
                  const selectedFieldRace = selectedRace
                    ? raceFieldLookup.get(`${selectedRace.bets?.[0]?.track || ""}-R${selectedRace.raceNumber}`)
                    : undefined;

                  const highlightedSelections = new Map<string, BestBet>();
                  (selectedRace?.bets || []).forEach((bet) => {
                    const key = bet.horseName.toLowerCase().trim();
                    const existing = highlightedSelections.get(key);
                    if (!existing || (existing.betType === "tip_only" && bet.betType !== "tip_only")) {
                      highlightedSelections.set(key, bet);
                    }
                  });

                  const executionBoardCards = [
                    {
                      key: "best-bet",
                      title: "Best Bet",
                      icon: Trophy,
                      accentClass: "text-racing-orange",
                      borderClass: "border-racing-orange/20 bg-[linear-gradient(180deg,rgba(249,115,22,0.14),rgba(255,255,255,0.02))]",
                      description: "Clearest overall case on the card",
                      bet: bestBetSelection,
                    },
                    {
                      key: "best-value",
                      title: "Best Value",
                      icon: TrendingUp,
                      accentClass: "text-emerald-400",
                      borderClass: "border-emerald-500/20 bg-[linear-gradient(180deg,rgba(16,185,129,0.14),rgba(255,255,255,0.02))]",
                      description: "Best price disagreement versus fair odds",
                      bet: bestValueSelection,
                    },
                    {
                      key: "best-banker",
                      title: "Best Banker",
                      icon: Crown,
                      accentClass: "text-green-400",
                      borderClass: "border-green-500/20 bg-[linear-gradient(180deg,rgba(34,197,94,0.14),rgba(255,255,255,0.02))]",
                      description: "Strongest short-price anchor on the board",
                      bet: bestBankerSelection,
                    },
                    {
                      key: "act-now",
                      title: "Act Now",
                      icon: Flame,
                      accentClass: "text-amber-300",
                      borderClass: "border-amber-500/20 bg-[linear-gradient(180deg,rgba(245,158,11,0.14),rgba(255,255,255,0.02))]",
                      description: "Highest-priority near-term live runner",
                      bet: actNowSelection,
                    },
                  ];
                  const actionRibbonCards = [
                    {
                      key: "next-live-bet",
                      title: "Next live bet",
                      value: nextLiveBet ? nextLiveBet.horseName : "No live bet",
                      detail: nextLiveBet
                        ? `${nextLiveBet.track} R${nextLiveBet.raceNumber} • ${formatCountdown(nextLiveBet.offTime)}`
                        : "No upcoming actionable runner on this filtered card.",
                      toneClass: "text-racing-orange",
                    },
                    {
                      key: "best-edge",
                      title: "Best edge",
                      value: bestEdgeSelection ? `${bestEdgeSelection.edge >= 0 ? "+" : ""}${bestEdgeSelection.edge.toFixed(1)}%` : "No edge",
                      detail: bestEdgeSelection
                        ? `${bestEdgeSelection.horseName} • ${bestEdgeSelection.track} R${bestEdgeSelection.raceNumber}`
                        : "No live runner is currently beating the market.",
                      toneClass: "text-emerald-300",
                    },
                    {
                      key: "best-banker",
                      title: "Best banker",
                      value: bestBankerSelection ? bestBankerSelection.horseName : "No banker",
                      detail: bestBankerSelection
                        ? `${bestBankerSelection.track} R${bestBankerSelection.raceNumber} • ${bestBankerSelection.confidenceScore.toFixed(0)} conf`
                        : "No race has separated into banker territory.",
                      toneClass: "text-green-300",
                    },
                    {
                      key: "strongest-meeting",
                      title: "Strongest meeting",
                      value: strongestMeeting ? strongestMeeting.trackName : "No meeting",
                      detail: strongestMeeting
                        ? `${strongestMeeting.liveBetCount} live • top edge ${strongestMeeting.bestEdge >= 0 ? "+" : ""}${strongestMeeting.bestEdge.toFixed(1)}%`
                        : "Meeting strength will appear once races are loaded.",
                      toneClass: "text-cyan-300",
                    },
                    {
                      key: "markets-unavailable",
                      title: "Markets unavailable",
                      value: `${marketUnavailableCount}`,
                      detail: marketUnavailableCount > 0
                        ? "Guide-only runners waiting on a live market."
                        : "Every surfaced runner has a usable market price.",
                      toneClass: marketUnavailableCount > 0 ? "text-amber-300" : "text-white",
                    },
                    {
                      key: "live-bets-left",
                      title: "Live bets left today",
                      value: `${liveBetsLeft}`,
                      detail: `${filteredTips.length} guide-only • ${bankerCount} bankers • ${exoticCount} exotics`,
                      toneClass: "text-white",
                    },
                  ];
                  const marketPressureAvailable = filteredBetsPerRace.some((bet) => Boolean(bet.marketMovement) || typeof bet.openingOdds === "number");
                  const strongestFirmer = marketPressureAvailable
                    ? [...filteredBetsPerRace]
                        .filter((bet) => bet.marketMovement?.toLowerCase().includes("firm"))
                        .sort(compareExecutionPriority)[0] || null
                    : null;
                  const strongestDrifter = marketPressureAvailable
                    ? [...filteredBetsPerRace]
                        .filter((bet) => bet.marketMovement?.toLowerCase().includes("drift"))
                        .sort((a, b) => {
                          if ((a.marketOdds || 0) !== (b.marketOdds || 0)) {
                            return (b.marketOdds || 0) - (a.marketOdds || 0);
                          }
                          return compareExecutionPriority(a, b);
                        })[0] || null
                    : null;
                  const staleMarketCount = filteredBetsPerRace.filter((bet) => !bet.marketOdds || bet.marketOdds <= 1).length;
                  const suspendedMarketCount = filteredBetsPerRace.filter((bet) => bet.selectionOrigin === "market_unavailable").length;
                  const deskNotesHeadline = featuredStrideSelections.length > 0
                    ? `Stride has ${featuredStrideSelections.length} headline read${featuredStrideSelections.length > 1 ? "s" : ""} queued for ${featuredStrideSelections.map(({ bet }) => bet.horseName).join(", ")}.`
                    : "Stride desk notes are ready when you want a deeper read on the headline runners.";

                  return (
                    <>
                      <div className="mb-6 space-y-4">
                        <div className="rounded-2xl border border-white/[0.06] bg-[linear-gradient(180deg,rgba(255,255,255,0.04),rgba(255,255,255,0.02))] p-4 sm:p-5">
                          <div className="flex flex-col gap-4 xl:flex-row xl:items-center xl:justify-between">
                            <div>
                              <div className="flex items-center gap-2 text-[11px] uppercase tracking-[0.22em] text-racing-orange/80">
                                <Gauge className="h-3.5 w-3.5" />
                                Execution Board
                              </div>
                              <h2 className="mt-2 text-2xl sm:text-[2rem] sm:leading-[1.05] font-syne font-bold text-white">
                                What matters first on {summaryDateLabel}'s card
                              </h2>
                              <p className="mt-2 max-w-3xl text-sm sm:text-base leading-7 text-white/65">
                                The top half is now tuned for action first: where the edge is, what jumps next, which meeting is strongest, and how much of the board is real betting versus watch-only coverage.
                              </p>
                            </div>

                            <div className="inline-flex items-center gap-2 rounded-full border border-racing-orange/20 bg-black/25 px-3 py-1.5 text-sm text-white/60">
                              <Calendar className="h-4 w-4 text-racing-orange" />
                              {summaryDateLabel}
                            </div>
                          </div>

                          <div className="mt-5 grid grid-cols-1 gap-3 md:grid-cols-2 xl:grid-cols-6">
                            {actionRibbonCards.map(({ key, title, value, detail, toneClass }) => (
                              <div
                                key={key}
                                className="rounded-2xl border border-white/[0.06] bg-black/20 px-4 py-4"
                                data-testid={`card-best-bets-ribbon-${key}`}
                              >
                                <p className="text-[11px] uppercase tracking-[0.18em] text-white/35">{title}</p>
                                <p className={`mt-2 text-lg font-semibold ${toneClass}`}>{value}</p>
                                <p className="mt-1 text-sm leading-6 text-white/45">{detail}</p>
                              </div>
                            ))}
                          </div>
                        </div>

                        {meetingOpportunityCards.length > 0 && (
                          <div className="rounded-2xl border border-white/[0.06] bg-black/20 p-4 sm:p-5">
                            <div className="flex flex-col gap-2 lg:flex-row lg:items-center lg:justify-between">
                              <div>
                                <div className="flex items-center gap-2 text-[11px] uppercase tracking-[0.18em] text-cyan-300/80">
                                  <MapPin className="h-3.5 w-3.5" />
                                  Meeting Opportunity
                                </div>
                                <p className="mt-1 text-sm text-white/50">
                                  Where the live card is concentrated, with the strongest venue surfaced first.
                                </p>
                              </div>
                              <div className="text-xs text-white/35">
                                {strongestMeeting ? `${strongestMeeting.trackName} is currently the strongest meeting.` : "Meeting strength updates as filters change."}
                              </div>
                            </div>

                            <div className="mt-4 flex gap-3 overflow-x-auto pb-2">
                              {meetingOpportunityCards.map((meeting) => (
                                <div
                                  key={`meeting-opportunity-${meeting.trackName}`}
                                  className={`min-w-[240px] rounded-2xl border px-4 py-4 ${getMeetingStrengthClassName(meeting.meetingStrengthScore)}`}
                                  data-testid={`card-meeting-opportunity-${meeting.trackName.replace(/\s+/g, '-').toLowerCase()}`}
                                >
                                  <div className="flex items-start justify-between gap-3">
                                    <div>
                                      <p className="text-sm font-semibold text-white">{meeting.trackName}</p>
                                      <p className="text-xs text-white/40">
                                        {meeting.liveBetCount} live • {meeting.positiveEdgeCount} with edge
                                      </p>
                                    </div>
                                    {strongestMeeting?.trackName === meeting.trackName && (
                                      <Badge className="border-emerald-500/30 bg-emerald-500/10 text-emerald-300">
                                        Strongest
                                      </Badge>
                                    )}
                                  </div>

                                  <div className="mt-4 grid grid-cols-2 gap-3 text-sm">
                                    <div>
                                      <p className="text-[11px] uppercase tracking-[0.18em] text-white/35">Best runner</p>
                                      <p className="mt-1 font-medium text-white">
                                        {meeting.bestTrackSelection?.horseName || "No live angle"}
                                      </p>
                                    </div>
                                    <div>
                                      <p className="text-[11px] uppercase tracking-[0.18em] text-white/35">Top edge</p>
                                      <p className={`mt-1 font-medium ${meeting.bestEdge > 0 ? "text-emerald-300" : "text-white/55"}`}>
                                        {meeting.bestEdge > 0 ? `+${meeting.bestEdge.toFixed(1)}%` : "No edge"}
                                      </p>
                                    </div>
                                    <div>
                                      <p className="text-[11px] uppercase tracking-[0.18em] text-white/35">First jump</p>
                                      <p className="mt-1 font-medium text-white/80">
                                        {meeting.firstJumpBet ? formatRaceTime(meeting.firstJumpBet.offTime) || "TBC" : "TBC"}
                                      </p>
                                    </div>
                                    <div>
                                      <p className="text-[11px] uppercase tracking-[0.18em] text-white/35">Meeting tone</p>
                                      <p className="mt-1 font-medium text-white/80">
                                        {meeting.meetingConfidence >= 80 ? "Aggressive" : meeting.meetingConfidence >= 60 ? "Live" : meeting.meetingConfidence >= 40 ? "Watching" : "Thin"}
                                      </p>
                                    </div>
                                  </div>
                                </div>
                              ))}
                            </div>
                          </div>
                        )}

                        <div className="rounded-2xl border border-white/[0.06] bg-black/20 p-4 sm:p-5">
                          <div className="flex flex-col gap-2 lg:flex-row lg:items-center lg:justify-between">
                            <div>
                              <div className="flex items-center gap-2 text-[11px] uppercase tracking-[0.18em] text-white/50">
                                <BarChart3 className="h-3.5 w-3.5" />
                                {marketPressureAvailable ? "Market Pressure" : "Price Discipline"}
                              </div>
                              <p className="mt-1 text-sm text-white/45">
                                {marketPressureAvailable
                                  ? "Where the market is firming, drifting, or withholding usable prices."
                                  : "Where the best fair-price overlays sit, and which short favourites still fail the betting test."}
                              </p>
                            </div>
                            <div className="text-xs text-white/35">
                              {marketPressureAvailable ? "Market-led strip" : "Model-led strip"}
                            </div>
                          </div>

                          <div className="mt-4 grid grid-cols-1 gap-3 md:grid-cols-2 xl:grid-cols-4">
                            {marketPressureAvailable ? (
                              <>
                                <div className="rounded-2xl border border-emerald-500/20 bg-emerald-500/8 px-4 py-4">
                                  <p className="text-[11px] uppercase tracking-[0.18em] text-emerald-300/70">Biggest firmer</p>
                                  <p className="mt-2 text-lg font-semibold text-emerald-300">
                                    {strongestFirmer?.horseName || "No firmer"}
                                  </p>
                                  <p className="mt-1 text-sm text-white/45">
                                    {strongestFirmer ? `${strongestFirmer.track} R${strongestFirmer.raceNumber} • ${strongestFirmer.marketMovement}` : "No firming signal is available on this card."}
                                  </p>
                                </div>
                                <div className="rounded-2xl border border-red-500/20 bg-red-500/8 px-4 py-4">
                                  <p className="text-[11px] uppercase tracking-[0.18em] text-red-300/70">Biggest drifter</p>
                                  <p className="mt-2 text-lg font-semibold text-red-300">
                                    {strongestDrifter?.horseName || "No drifter"}
                                  </p>
                                  <p className="mt-1 text-sm text-white/45">
                                    {strongestDrifter ? `${strongestDrifter.track} R${strongestDrifter.raceNumber} • ${strongestDrifter.marketMovement}` : "No drifting signal is available on this card."}
                                  </p>
                                </div>
                                <div className="rounded-2xl border border-white/10 bg-white/[0.03] px-4 py-4">
                                  <p className="text-[11px] uppercase tracking-[0.18em] text-white/35">Stale / no odds</p>
                                  <p className="mt-2 text-lg font-semibold text-white">{staleMarketCount}</p>
                                  <p className="mt-1 text-sm text-white/45">Runners missing a live, usable market price.</p>
                                </div>
                                <div className="rounded-2xl border border-amber-500/20 bg-amber-500/8 px-4 py-4">
                                  <p className="text-[11px] uppercase tracking-[0.18em] text-amber-300/70">Suspended / unavailable</p>
                                  <p className="mt-2 text-lg font-semibold text-amber-300">{suspendedMarketCount}</p>
                                  <p className="mt-1 text-sm text-white/45">Selections still blocked by market availability.</p>
                                </div>
                              </>
                            ) : (
                              <>
                                <div className="rounded-2xl border border-racing-orange/20 bg-racing-orange/8 px-4 py-4">
                                  <p className="text-[11px] uppercase tracking-[0.18em] text-racing-orange/80">Shortest fair overlay</p>
                                  <p className="mt-2 text-lg font-semibold text-white">
                                    {shortestFairOverlay?.horseName || "No overlay"}
                                  </p>
                                  <p className="mt-1 text-sm text-white/45">
                                    {shortestFairOverlay
                                      ? `${shortestFairOverlay.track} R${shortestFairOverlay.raceNumber} • fair ${getBestBetFairOdds(shortestFairOverlay) ? `$${getBestBetFairOdds(shortestFairOverlay)!.toFixed(2)}` : "TBC"}`
                                      : "No live runner currently qualifies as an overlay."}
                                  </p>
                                </div>
                                <div className="rounded-2xl border border-emerald-500/20 bg-emerald-500/8 px-4 py-4">
                                  <p className="text-[11px] uppercase tracking-[0.18em] text-emerald-300/70">Longest live price</p>
                                  <p className="mt-2 text-lg font-semibold text-white">
                                    {longestLiveValuePrice?.horseName || "No long play"}
                                  </p>
                                  <p className="mt-1 text-sm text-white/45">
                                    {longestLiveValuePrice
                                      ? `${longestLiveValuePrice.track} R${longestLiveValuePrice.raceNumber} • ${longestLiveValuePrice.marketOdds ? `$${longestLiveValuePrice.marketOdds.toFixed(2)}` : "TBC"}`
                                      : "No live value runner is carrying a bigger price right now."}
                                  </p>
                                </div>
                                <div className="rounded-2xl border border-amber-500/20 bg-amber-500/8 px-4 py-4">
                                  <p className="text-[11px] uppercase tracking-[0.18em] text-amber-300/70">Shortest no-bet favourite</p>
                                  <p className="mt-2 text-lg font-semibold text-white">
                                    {shortestNoBetFavourite?.horseName || "No favourite"}
                                  </p>
                                  <p className="mt-1 text-sm text-white/45">
                                    {shortestNoBetFavourite
                                      ? `${shortestNoBetFavourite.track} R${shortestNoBetFavourite.raceNumber} • ${shortestNoBetFavourite.marketOdds ? `$${shortestNoBetFavourite.marketOdds.toFixed(2)}` : "TBC"}`
                                      : "No short-priced guide-only favourite is in view."}
                                  </p>
                                </div>
                                <div className="rounded-2xl border border-white/10 bg-white/[0.03] px-4 py-4">
                                  <p className="text-[11px] uppercase tracking-[0.18em] text-white/35">Guide-only, no edge</p>
                                  <p className="mt-2 text-lg font-semibold text-white">{guideOnlyNoEdgeCount}</p>
                                  <p className="mt-1 text-sm text-white/45">Coverage runners that remain useful context, not live action.</p>
                                </div>
                              </>
                            )}
                          </div>
                        </div>

                        <div className="rounded-2xl border border-white/[0.06] bg-[linear-gradient(180deg,rgba(255,255,255,0.03),rgba(255,255,255,0.015))] p-4 sm:p-5">
                          <div className="flex flex-col gap-3 lg:flex-row lg:items-center lg:justify-between">
                            <div>
                              <div className="flex items-center gap-2 text-[11px] uppercase tracking-[0.18em] text-white/40">
                                <Filter className="h-3.5 w-3.5" />
                                Execution Controls
                              </div>
                              <p className="mt-1 text-sm text-white/45">
                                Filter the action board without changing the lower race browser.
                              </p>
                            </div>

                            <div className="flex flex-wrap items-center gap-2">
                              <Badge className="border-white/10 bg-white/5 text-white/60">
                                {activeBestBetFilterCount} active
                              </Badge>
                              <Badge className="border-white/10 bg-white/5 text-white/45">
                                Avg confidence {averageConfidence.toFixed(0)}
                              </Badge>
                              <Badge className="border-white/10 bg-white/5 text-white/45">
                                {racecardLaneCount} cards • {sortedTracks.length} tracks
                              </Badge>
                              {hasActiveBestBetFilters && (
                                <Button
                                  size="sm"
                                  variant="outline"
                                  className="border-racing-orange/30 text-racing-orange hover:bg-racing-orange/10"
                                  onClick={clearBestBetTypeFilters}
                                  data-testid="button-best-bet-clear-filters"
                                >
                                  Clear Filters
                                </Button>
                              )}
                            </div>
                          </div>

                          <div className="mt-4 flex flex-wrap items-center gap-2">
                            <span className="text-sm font-medium text-white/35 mr-2">
                              <Calendar className="h-4 w-4 inline mr-1" />
                              Race Day
                            </span>
                            {(safeBestBetsData.availableDates || []).slice(-5).map((dateStr) => (
                              <Button
                                key={dateStr}
                                size="sm"
                                variant={selectedDate === dateStr ? "default" : "outline"}
                                className={selectedDate === dateStr
                                  ? "bg-racing-orange text-black"
                                  : "border-racing-orange/40 text-racing-orange hover:bg-racing-orange/10"}
                                onClick={() => {
                                  setBestBetsDateFilter(dateStr);
                                  setSelectedBestTrack("");
                                  setSelectedBestRaceKey("");
                                  setRacecardLaneTrackFilter("all");
                                }}
                                data-testid={`button-date-filter-${dateStr}`}
                              >
                                {formatDateLabel(dateStr)}
                              </Button>
                            ))}
                          </div>

                          <div className="mt-4 rounded-2xl border border-white/[0.06] bg-black/20 p-4">
                            <div className="flex flex-col gap-3 lg:flex-row lg:items-center lg:justify-between">
                              <div>
                                <div className="flex items-center gap-2 text-[11px] uppercase tracking-[0.18em] text-white/40">
                                  <Filter className="h-3.5 w-3.5" />
                                  Advanced Track Board Filters
                                </div>
                                <p className="mt-1 text-sm text-white/45">
                                  Narrow the execution board by pick identity or strategy without affecting the older selections filters.
                                </p>
                              </div>
                            </div>

                            <div className="mt-4 space-y-4">
                              <div>
                                <p className="mb-2 text-[11px] uppercase tracking-[0.18em] text-white/35">
                                  Pick Identity
                                </p>
                                <div className="flex flex-wrap gap-2">
                                  {BEST_BET_IDENTITY_FILTER_OPTIONS.map((option) => {
                                    const isActive = bestBetIdentityFilters.has(option.value);
                                    return (
                                      <Button
                                        key={option.value}
                                        type="button"
                                        size="sm"
                                        variant="outline"
                                        onClick={() => toggleBestBetIdentityFilter(option.value)}
                                        className={`text-xs ${
                                          isActive
                                            ? option.activeClassName
                                            : "border-white/[0.08] bg-white/[0.03] text-white/70 hover:bg-white/[0.06] hover:text-white"
                                        }`}
                                        data-testid={`button-best-bet-identity-filter-${option.value}`}
                                      >
                                        {option.label}
                                      </Button>
                                    );
                                  })}
                                </div>
                              </div>

                              <div>
                                <p className="mb-2 text-[11px] uppercase tracking-[0.18em] text-white/35">
                                  Strategy
                                </p>
                                <div className="flex flex-wrap gap-2">
                                  {BEST_BET_STRATEGY_FILTER_OPTIONS.map((option) => {
                                    const isActive = bestBetStrategyFilters.has(option.value);
                                    return (
                                      <Button
                                        key={option.value}
                                        type="button"
                                        size="sm"
                                        variant="outline"
                                        onClick={() => toggleBestBetStrategyFilter(option.value)}
                                        className={`text-xs ${
                                          isActive
                                            ? option.activeClassName
                                            : "border-white/[0.08] bg-white/[0.03] text-white/70 hover:bg-white/[0.06] hover:text-white"
                                        }`}
                                        data-testid={`button-best-bet-strategy-filter-${option.value}`}
                                      >
                                        {option.label}
                                      </Button>
                                    );
                                  })}
                                </div>
                              </div>
                            </div>
                          </div>
                        </div>
                      </div>

                      {filteredBetsPerRace.length > 0 && (
                        <Card className="glass-card border-0 mb-6 overflow-hidden">
                          <CardContent className="p-0">
                            <div className="p-5 sm:p-6 bg-[radial-gradient(circle_at_top_left,rgba(249,115,22,0.20),transparent_52%),linear-gradient(145deg,rgba(255,255,255,0.05),rgba(255,255,255,0.01))]">
                              <div className="flex items-center gap-2 text-[11px] uppercase tracking-[0.22em] text-racing-orange/80 mb-3">
                                <Flag className="h-3.5 w-3.5" />
                                Execution Board
                              </div>
                              <h2 className="text-2xl sm:text-[2rem] sm:leading-[1.05] font-syne font-bold text-white mb-3">
                                {executionBoardHeadline}
                              </h2>
                              <p className="max-w-3xl text-sm sm:text-base leading-7 text-white/70">
                                {executionBoardSummary}
                              </p>
                            </div>

                            <div className="grid grid-cols-1 xl:grid-cols-4 gap-3 p-5 sm:p-6 pt-0">
                              {executionBoardCards.map(({ key, title, icon: Icon, accentClass, borderClass, description, bet }) => {
                                const fairOdds = bet ? getBestBetFairOdds(bet) : null;
                                const overlay = bet ? getBestBetOverlayPct(bet) : null;
                                const confidenceBand = bet ? getBestBetConfidenceBand(bet.confidenceScore || 0, (bet as any).crowdClassification) : null;
                                const countdownLabel = bet
                                  ? (new Date(bet.offTime).getTime() > nowMs ? formatCountdown(bet.offTime) : "Jumped")
                                  : null;

                                return (
                                  <div
                                    key={key}
                                    className={`rounded-2xl border ${borderClass} p-4 sm:p-5`}
                                    data-testid={`card-betting-desk-${key}`}
                                  >
                                    <div className="flex items-center justify-between gap-3 mb-4">
                                      <div className="space-y-1">
                                        <div className={`flex items-center gap-2 text-[11px] uppercase tracking-[0.18em] ${accentClass}`}>
                                          <Icon className="h-3.5 w-3.5" />
                                          {title}
                                        </div>
                                        <p className="text-xs text-white/35">{description}</p>
                                      </div>
                                      {bet && (
                                        <Badge className={`${confidenceBand?.className || "border-white/10 bg-black/25 text-white/70"} border`}>
                                          {key === "act-now" ? "Priority" : getDeskCallLabel(bet)}
                                        </Badge>
                                      )}
                                    </div>

                                    {bet ? (
                                      <div className="space-y-4">
                                        <div>
                                          <p className="text-xl font-semibold text-white">{bet.horseName}</p>
                                          <p className="text-sm text-white/45">{bet.track} R{bet.raceNumber}</p>
                                          {/* Crowd Signal Banner */}
                                          {bet.crowdClassification && bet.crowdClassification !== 'MODEL_ONLY' && bet.crowdClassification !== 'REJECTED' && (
                                            <div className={`flex items-center gap-2 px-3 py-1.5 rounded-lg text-xs font-medium mt-1.5 ${
                                              bet.crowdClassification === 'CONFIRMED'
                                                ? 'bg-emerald-500/20 border border-emerald-500/30 text-emerald-300'
                                                : bet.crowdClassification === 'CROWD_ONLY'
                                                ? 'bg-blue-500/20 border border-blue-500/30 text-blue-300'
                                                : 'bg-amber-500/20 border border-amber-500/30 text-amber-300'
                                            }`}>
                                              <span className="font-semibold">
                                                {bet.crowdClassification === 'CONFIRMED' ? 'CROWD + MODEL' : 'CROWD PICK'}
                                              </span>
                                              <span className="opacity-70">
                                                {bet.crowdScore?.toFixed(0)}% of sources
                                              </span>
                                            </div>
                                          )}
                                          {(bet.crowdClassification === 'MODEL_ONLY' || bet.crowdClassification === 'REJECTED') && (
                                            <div className="text-xs text-amber-400/70 flex items-center gap-1 mt-1.5">
                                              <span>&#9888;</span>
                                              <span>Model only — no crowd confirmation</span>
                                            </div>
                                          )}
                                          {bet.stakeRecommendation && bet.stakeRecommendation !== 'NONE' && (
                                            <div className={`inline-flex items-center px-2 py-0.5 rounded text-xs font-medium mt-1 ${
                                              bet.stakeRecommendation === 'FULL'
                                                ? 'bg-emerald-500/20 text-emerald-300'
                                                : bet.stakeRecommendation === 'STANDARD'
                                                ? 'bg-blue-500/20 text-blue-300'
                                                : 'bg-amber-500/20 text-amber-300'
                                            }`}>
                                              {bet.stakeRecommendation} STAKE
                                            </div>
                                          )}
                                        </div>

                                        <div className="grid grid-cols-2 gap-3 text-sm">
                                          <div>
                                            <p className="text-[11px] uppercase tracking-[0.18em] text-white/35">Market</p>
                                            <p className={`mt-1 font-semibold ${accentClass}`}>
                                              {bet.marketOdds ? `$${bet.marketOdds.toFixed(2)}` : "TBC"}
                                            </p>
                                          </div>
                                          <div>
                                            <p className="text-[11px] uppercase tracking-[0.18em] text-white/35">Fair</p>
                                            <p className="mt-1 font-semibold text-white/85">
                                              {fairOdds ? `$${fairOdds.toFixed(2)}` : "TBC"}
                                            </p>
                                          </div>
                                          <div>
                                            <p className="text-[11px] uppercase tracking-[0.18em] text-white/35">Edge</p>
                                            <p className={`mt-1 font-semibold ${bet.edge >= 0 ? "text-emerald-300" : "text-white/60"}`}>
                                              {bet.edge >= 0 ? "+" : ""}{bet.edge.toFixed(1)}%
                                            </p>
                                          </div>
                                          <div>
                                            <p className="text-[11px] uppercase tracking-[0.18em] text-white/35">Win</p>
                                            <p className="mt-1 font-semibold text-white/85">{bet.winPercentage.toFixed(0)}%</p>
                                          </div>
                                        </div>

                                        <div className="flex flex-wrap gap-2">
                                          <Badge className={`${confidenceBand?.className || "border-white/10 bg-white/[0.04] text-white/60"} border`}>
                                            {confidenceBand?.label || "Watching"}
                                          </Badge>
                                          <Badge className="border-white/10 bg-black/25 text-white/70">
                                            {formatRaceTime(bet.offTime) || "TBC"}
                                          </Badge>
                                          {countdownLabel && (
                                            <Badge className={`${new Date(bet.offTime).getTime() > nowMs ? "border-racing-orange/30 bg-racing-orange/10 text-racing-orange" : "border-white/10 bg-black/25 text-white/45"} border`}>
                                              {countdownLabel}
                                            </Badge>
                                          )}
                                          {overlay != null && overlay > 0 && (
                                            <Badge className="border-emerald-500/30 bg-emerald-500/10 text-emerald-300">
                                              Overlay +{overlay.toFixed(1)}%
                                            </Badge>
                                          )}
                                          {(bet as any).convergenceTier && (
                                            <MarketConfidenceBadge
                                              confidenceScore={(bet as any).marketConfidenceScore}
                                              confidenceLabel={(bet as any).marketConfidenceLabel}
                                              convergenceTier={(bet as any).convergenceTier}
                                              consensusScore={(bet as any).consensusScore}
                                              marketSignalScore={(bet as any).marketSignalScore}
                                              strideScore={(bet as any).selectionScoreRaw}
                                              votePct={(bet as any).consensusVotePct}
                                              tipsterCount={(bet as any).tipsterCount}
                                              consensusInjection={(bet as any).consensusInjection}
                                              marketInjection={(bet as any).marketInjection}
                                              compact
                                            />
                                          )}
                                        </div>

                                        <div className="rounded-xl border border-white/10 bg-black/20 px-3 py-4">
                                          <p className="text-[11px] uppercase tracking-[0.18em] text-white/35 mb-2">Why this is here</p>
                                          <p className="text-sm leading-6 text-white/75">
                                            {getBestBetExecutionReason(bet)}
                                          </p>
                                        </div>

                                        <Button
                                          size="sm"
                                          className={`w-full gap-2 ${isInSlip(bet.horseName, bet.track, bet.raceNumber) ? "bg-green-600" : "bg-racing-orange text-black"}`}
                                          disabled={slipLoading || isInSlip(bet.horseName, bet.track, bet.raceNumber)}
                                          onClick={(e) => {
                                            addSelection({
                                              horseName: bet.horseName,
                                              track: bet.track,
                                              raceNumber: bet.raceNumber,
                                              raceDate: bet.raceDate,
                                              raceTime: bet.offTime,
                                              odds: bet.marketOdds ?? 0,
                                              predictedWinProb: (bet.winPercentage ?? 0) / 100,
                                              confidenceScore: bet.confidenceScore ?? 0,
                                              betType: "win",
                                              reasons: bet.reasons || []
                                            }, e);
                                          }}
                                          data-testid={`button-add-slip-execution-${key}`}
                                        >
                                          {isInSlip(bet.horseName, bet.track, bet.raceNumber) ? (
                                            <>
                                              <Check className="h-4 w-4" />
                                              In Slip
                                            </>
                                          ) : (
                                            <>
                                              <Plus className="h-4 w-4" />
                                              Add to Slip
                                            </>
                                          )}
                                        </Button>
                                      </div>
                                    ) : (
                                      <div className="rounded-xl border border-white/10 bg-black/20 px-3 py-4">
                                        <p className="text-sm text-white/45 leading-6">
                                          {key === "best-banker"
                                            ? "No banker has separated itself on this filtered card."
                                            : "No runner has separated clearly enough to own this slot yet."}
                                        </p>
                                      </div>
                                    )}
                                  </div>
                                );
                              })}
                            </div>

                            <div className="border-t border-white/[0.06] px-5 sm:px-6 py-4">
                              <div className="flex items-center gap-2 text-[11px] uppercase tracking-[0.18em] text-racing-gold/80">
                                <Timer className="h-3.5 w-3.5" />
                                Next To Jump
                              </div>
                              {nextToJump.length === 0 ? (
                                <p className="mt-3 text-sm text-white/45">
                                  No upcoming live bets remain on this filtered card.
                                </p>
                              ) : (
                                <div className="mt-3 flex gap-3 overflow-x-auto pb-1">
                                  {nextToJump.map((bet) => (
                                    <div
                                      key={`desk-next-${bet.id}`}
                                      className={`min-w-[260px] rounded-2xl border px-4 py-4 ${
                                        isStartingSoon(bet.offTime)
                                          ? "border-racing-orange/30 bg-racing-orange/10"
                                          : "border-white/[0.08] bg-white/[0.03]"
                                      }`}
                                      data-testid={`card-next-to-jump-${bet.id}`}
                                    >
                                      <div className="flex items-start justify-between gap-3">
                                        <div>
                                          <p className="text-sm font-semibold text-white">{bet.horseName}</p>
                                          <p className="text-xs text-white/45">{bet.track} R{bet.raceNumber}</p>
                                        </div>
                                        <Badge className={`${isStartingSoon(bet.offTime) ? "border-racing-orange/30 bg-racing-orange/10 text-racing-orange" : "border-white/10 bg-black/25 text-white/60"} border`}>
                                          {formatCountdown(bet.offTime)}
                                        </Badge>
                                      </div>

                                      <div className="mt-3 flex items-center justify-between text-sm">
                                        <span className="text-white/70">{bet.marketOdds ? `$${bet.marketOdds.toFixed(2)}` : "TBC"}</span>
                                        <span className={`${bet.edge >= 0 ? "text-emerald-300" : "text-white/45"}`}>
                                          {bet.edge >= 0 ? "+" : ""}{bet.edge.toFixed(1)}%
                                        </span>
                                      </div>

                                      <Button
                                        size="sm"
                                        className={`mt-3 w-full gap-2 ${isInSlip(bet.horseName, bet.track, bet.raceNumber) ? "bg-green-600" : "bg-racing-orange text-black"}`}
                                        disabled={slipLoading || isInSlip(bet.horseName, bet.track, bet.raceNumber)}
                                        onClick={(e) => {
                                          addSelection({
                                            horseName: bet.horseName,
                                            track: bet.track,
                                            raceNumber: bet.raceNumber,
                                            raceDate: bet.raceDate,
                                            raceTime: bet.offTime,
                                            odds: bet.marketOdds ?? 0,
                                            predictedWinProb: (bet.winPercentage ?? 0) / 100,
                                            confidenceScore: bet.confidenceScore ?? 0,
                                            betType: "win",
                                            reasons: bet.reasons || []
                                          }, e);
                                        }}
                                        data-testid={`button-add-slip-next-${bet.id}`}
                                      >
                                        {isInSlip(bet.horseName, bet.track, bet.raceNumber) ? (
                                          <>
                                            <Check className="h-4 w-4" />
                                            In Slip
                                          </>
                                        ) : (
                                          <>
                                            <Plus className="h-4 w-4" />
                                            Add to Slip
                                          </>
                                        )}
                                      </Button>
                                    </div>
                                  ))}
                                </div>
                              )}
                            </div>
                          </CardContent>
                        </Card>
                      )}

                      {filteredBetsPerRace.length > 0 && featuredStrideSelections.length > 0 && (
                        <Card className="glass-card border-0 mb-6 overflow-hidden">
                          <CardContent className="p-5 space-y-4">
                            <div className="flex flex-col gap-3 lg:flex-row lg:items-center lg:justify-between">
                              <div>
                                <div className="flex items-center gap-2 text-[11px] uppercase tracking-[0.18em] text-racing-gold/80 mb-2">
                                  <Sparkles className="h-3.5 w-3.5" />
                                  Desk Notes
                                </div>
                                <p className="text-sm text-white/60 max-w-3xl">
                                  {deskNotesHeadline}
                                </p>
                              </div>
                              <div className="flex flex-wrap items-center gap-2">
                                <Button
                                  size="sm"
                                  variant="outline"
                                  className="gap-2 border-racing-gold/40 text-racing-gold hover:bg-racing-gold/10"
                                  onClick={() => {
                                    void loadStrideForCard();
                                  }}
                                  disabled={anyFeaturedStrideLoading || allFeaturedStrideLoaded}
                                  data-testid="button-load-card-stride"
                                >
                                  {anyFeaturedStrideLoading ? (
                                    <>
                                      <Loader2 className="h-4 w-4 animate-spin" />
                                      Loading Desk Notes...
                                    </>
                                  ) : allFeaturedStrideLoaded ? (
                                    <>
                                      <Sparkles className="h-4 w-4" />
                                      Desk Notes Loaded
                                    </>
                                  ) : (
                                    <>
                                      <MessageSquare className="h-4 w-4" />
                                      Load Desk Notes
                                    </>
                                  )}
                                </Button>
                                <Button
                                  size="sm"
                                  variant="ghost"
                                  className="gap-2 text-white/55 hover:text-white"
                                  onClick={() => setBestBetsDeskNotesCollapsed((value) => !value)}
                                  data-testid="button-toggle-best-bet-desk-notes"
                                >
                                  {bestBetsDeskNotesCollapsed ? (
                                    <>
                                      <ChevronDown className="h-4 w-4" />
                                      Expand
                                    </>
                                  ) : (
                                    <>
                                      <ChevronUp className="h-4 w-4" />
                                      Collapse
                                    </>
                                  )}
                                </Button>
                              </div>
                            </div>

                            {!bestBetsDeskNotesCollapsed && (
                              <div className="grid grid-cols-1 xl:grid-cols-3 gap-3">
                                {featuredStrideSelections.map(({ bet, labels }) => {
                                  const deskNote = summarizeDeskStrideExplanation(strideExplanations[bet.id]);

                                  return (
                                    <div
                                      key={`stride-card-${bet.id}`}
                                      className="rounded-2xl border border-racing-gold/20 bg-[linear-gradient(180deg,rgba(234,179,8,0.08),rgba(255,255,255,0.02))] p-4 space-y-3"
                                      data-testid={`card-stride-summary-${bet.id}`}
                                    >
                                      <div className="space-y-1">
                                        <div className="flex items-center gap-2 flex-wrap">
                                          <Badge className="bg-racing-gold/15 text-racing-gold border-racing-gold/30 border text-[10px]">
                                            {labels.join(" • ")}
                                          </Badge>
                                          <span className="text-xs text-white/35">
                                            {bet.track} R{bet.raceNumber}
                                          </span>
                                        </div>
                                        <div className="flex items-start justify-between gap-3">
                                          <div>
                                            <p className="text-lg font-semibold text-white">{bet.horseName}</p>
                                            <p className="text-sm text-white/45">
                                              {bet.winPercentage.toFixed(0)}% win chance at {bet.marketOdds ? `$${bet.marketOdds.toFixed(2)}` : 'TBC'}
                                            </p>
                                          </div>
                                          <span className={`text-sm font-semibold ${bet.edge >= 0 ? "text-emerald-400" : "text-white/45"}`}>
                                            {bet.edge >= 0 ? "+" : ""}{bet.edge.toFixed(1)}%
                                          </span>
                                        </div>
                                      </div>

                                      {strideLoading.has(bet.id) ? (
                                        <div className="rounded-xl border border-white/10 bg-black/20 px-3 py-4 text-sm text-white/50 flex items-center gap-2">
                                          <Loader2 className="h-4 w-4 animate-spin text-racing-gold" />
                                          Writing Stride desk note...
                                        </div>
                                      ) : deskNote ? (
                                        <div className="rounded-xl border border-racing-gold/25 bg-black/20 px-3 py-4">
                                          <p className="text-[11px] uppercase tracking-[0.18em] text-racing-gold/70 mb-2">
                                            Stride Verdict
                                          </p>
                                          <p className="text-sm leading-7 text-white/80">
                                            {deskNote}
                                          </p>
                                        </div>
                                      ) : (
                                        <div className="rounded-xl border border-white/10 bg-black/20 px-3 py-4">
                                          <p className="text-sm text-white/45 leading-6">
                                            Stride is still building the desk note for this runner.
                                          </p>
                                        </div>
                                      )}
                                    </div>
                                  );
                                })}
                              </div>
                            )}
                          </CardContent>
                        </Card>
                      )}

                      {filteredBetsPerRace.length === 0 ? (
                        <Card className="bg-white/[0.03] mb-6">
                          <CardContent className="py-8 text-center">
                            {hasActiveBestBetFilters ? (
                              <>
                                <Filter className="h-8 w-8 mx-auto mb-3 text-racing-orange/60" />
                                <h3 className="text-lg font-semibold text-white mb-2">No Track Board Selections Match Those Filters</h3>
                                <p className="text-sm text-white/40 max-w-md mx-auto">
                                  The selected date has races, but none of the cards match your current identity and strategy filters.
                                </p>
                                <Button
                                  size="sm"
                                  variant="outline"
                                  className="mt-4 border-racing-orange/30 text-racing-orange hover:bg-racing-orange/10"
                                  onClick={clearBestBetTypeFilters}
                                  data-testid="button-best-bet-empty-clear-filters"
                                >
                                  Clear Filters
                                </Button>
                              </>
                            ) : (
                              <>
                                <Calendar className="h-8 w-8 mx-auto mb-3 text-white/40" />
                                <p className="text-white/40">No races for this date. Select another day.</p>
                              </>
                            )}
                          </CardContent>
                        </Card>
                      ) : (
                        <>
                          {/* Best Pick Per Track Section - filtered by date */}
                          <div>
                            <div className="mb-4 grid grid-cols-1 gap-4 xl:grid-cols-[1.2fr_0.9fr]">
                              <div>
                                <h2 className="text-lg font-bold flex items-center gap-2">
                                  <Star className="h-5 w-5 text-yellow-500 fill-yellow-500" />
                                  {topSectionTitle}
                                </h2>
                                <p className="mt-1 text-sm text-white/45">
                                  {topSectionDescription}
                                </p>
                                {topSelectionsView === "racecardTips" && (
                                  <div className="mt-3 grid w-full max-w-2xl grid-cols-1 gap-3 sm:grid-cols-2">
                                    <div>
                                      <p className="mb-2 text-[11px] uppercase tracking-[0.18em] text-white/35">
                                        Racecard Lane Track
                                      </p>
                                      <Select
                                        value={resolvedRacecardLaneTrackFilter}
                                        onValueChange={setRacecardLaneTrackFilter}
                                      >
                                        <SelectTrigger
                                          className="border-white/[0.08] bg-white/[0.03] text-white"
                                          data-testid="select-racecard-lane-track-filter"
                                        >
                                          <SelectValue placeholder="All tracks" />
                                        </SelectTrigger>
                                        <SelectContent>
                                          <SelectItem value="all">All Tracks</SelectItem>
                                          {allTracksForDate.map((trackName) => (
                                            <SelectItem key={trackName} value={trackName}>
                                              {trackName}
                                            </SelectItem>
                                          ))}
                                        </SelectContent>
                                      </Select>
                                    </div>

                                    <div>
                                      <p className="mb-2 text-[11px] uppercase tracking-[0.18em] text-white/35">
                                        Racecard Confidence
                                      </p>
                                      <Select
                                        value={resolvedRacecardLaneConfidenceSort}
                                        onValueChange={setRacecardLaneConfidenceSort}
                                      >
                                        <SelectTrigger
                                          className="border-white/[0.08] bg-white/[0.03] text-white"
                                          data-testid="select-racecard-lane-confidence-sort"
                                        >
                                          <SelectValue placeholder="Jump time" />
                                        </SelectTrigger>
                                        <SelectContent>
                                          <SelectItem value="time">Jump time</SelectItem>
                                          <SelectItem value="desc">Confidence high → low</SelectItem>
                                          <SelectItem value="asc">Confidence low → high</SelectItem>
                                        </SelectContent>
                                      </Select>
                                    </div>
                                  </div>
                                )}
                              </div>

                              <Card className="glass-card border-0 overflow-hidden">
                                <CardContent className="p-2">
                                  <div className="grid grid-cols-2 gap-2">
                                    <button
                                      type="button"
                                      onClick={() => setTopSelectionsView("trackBest")}
                                      className={`rounded-xl border px-4 py-3 text-left transition-all ${
                                        topSelectionsView === "trackBest"
                                          ? "border-racing-orange bg-[linear-gradient(135deg,rgba(249,115,22,0.22),rgba(249,115,22,0.05))] shadow-[0_0_18px_rgba(249,115,22,0.16)]"
                                          : "border-white/[0.08] bg-white/[0.03] hover:bg-white/[0.06]"
                                      }`}
                                      data-testid="button-top-section-track-best"
                                    >
                                      <div className="flex items-center justify-between gap-3">
                                        <div>
                                          <p className="text-[11px] uppercase tracking-[0.18em] text-white/40">
                                            Track Bests
                                          </p>
                                          <p className="mt-1 text-sm font-semibold text-white">
                                            Venue anchors
                                          </p>
                                        </div>
                                        <div className="text-right">
                                          <p className="text-xl font-bold text-racing-orange">{filteredBestPerTrack.length}</p>
                                          <p className="text-[11px] text-white/35">cards</p>
                                        </div>
                                      </div>
                                    </button>

                                    <button
                                      type="button"
                                      onClick={() => setTopSelectionsView("racecardTips")}
                                      className={`rounded-xl border px-4 py-3 text-left transition-all ${
                                        topSelectionsView === "racecardTips"
                                          ? "border-cyan-400/50 bg-[linear-gradient(135deg,rgba(34,211,238,0.18),rgba(34,211,238,0.05))] shadow-[0_0_18px_rgba(34,211,238,0.14)]"
                                          : "border-white/[0.08] bg-white/[0.03] hover:bg-white/[0.06]"
                                      }`}
                                      data-testid="button-top-section-racecard-tips"
                                    >
                                      <div className="flex items-center justify-between gap-3">
                                        <div>
                                          <p className="text-[11px] uppercase tracking-[0.18em] text-white/40">
                                            Day Tips
                                          </p>
                                          <p className="mt-1 text-sm font-semibold text-white">
                                            Racecard lane
                                          </p>
                                        </div>
                                        <div className="text-right">
                                          <p className="text-xl font-bold text-cyan-400">{filteredRacecardTips.length}</p>
                                          <p className="text-[11px] text-white/35">tips</p>
                                        </div>
                                      </div>
                                    </button>
                                  </div>
                                </CardContent>
                              </Card>
                            </div>

                            {topSectionBets.length === 0 ? (
                              <Card className="glass-card border-0">
                                <CardContent className="py-8 text-center">
                                  <p className="text-white/45">
                                    {topSelectionsView === "trackBest"
                                      ? "No track-best cards are available for this date."
                                      : "No racecard tips are available for this date."}
                                  </p>
                                </CardContent>
                              </Card>
                            ) : (
                            <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
                              {sortedTopSectionBets.map((bet) => {
                      const isExpanded = expandedBestBets.has(bet.id);
                      // Look up horse silk from raceFieldLookup
                      const raceKey = `${bet.track}-R${bet.raceNumber}`;
                      const raceData = raceFieldLookup.get(raceKey);
                      const silkUrl = raceData?.full_field.find(
                        (r) => r.horse.toLowerCase() === bet.horseName.toLowerCase()
                      )?.silk_url;

                      return (
                        <HorseTipCard
                          key={bet.id}
                          bet={bet}
                          silkUrl={silkUrl}
                          isExpanded={isExpanded}
                          onToggleExpand={() => toggleBestBetCard(bet.id)}
                          onAddToSlip={(e) => {
                            addSelection({
                              horseName: bet.horseName,
                              track: bet.track,
                              raceNumber: bet.raceNumber,
                              raceDate: bet.raceDate,
                              raceTime: bet.offTime,
                              odds: bet.marketOdds ?? 0,
                              predictedWinProb: (bet.winPercentage ?? 0) / 100,
                              confidenceScore: bet.confidenceScore ?? 0,
                              betType: "win",
                              reasons: bet.reasons || [],
                            }, e);
                          }}
                          onAskStride={() => askStride(bet)}
                          isInSlip={isInSlip(bet.horseName, bet.track, bet.raceNumber)}
                          slipLoading={slipLoading}
                          strideLoading={strideLoading.has(bet.id)}
                          strideExplanation={strideExplanations[bet.id]}
                        />
                      );
                    })}
                              </div>
                            )}
                          </div>

                          {/* CROWD WATCHLIST — strong crowd support but model unconfirmed */}
                          {filteredBestBetsView.crowdOverrides.length > 0 && (
                            <div className="mt-4 border border-violet-500/20 rounded-lg p-3">
                              <div className="text-xs font-semibold text-violet-300 mb-2 flex items-center gap-1.5">
                                <Eye className="h-3.5 w-3.5" />
                                CROWD WATCHLIST — Strong public support, model unconfirmed
                              </div>
                              {filteredBestBetsView.crowdOverrides.map((horse) => (
                                <div key={horse.id} className="flex items-center justify-between py-1.5 border-t border-white/5">
                                  <span className="text-sm text-white/80">{horse.horseName}</span>
                                  <div className="flex items-center gap-3 text-xs text-white/50">
                                    <span>{horse.track} R{horse.raceNumber}</span>
                                    <span className="text-violet-300">{horse.crowdScore?.toFixed(0)}% crowd</span>
                                    <span>${horse.marketOdds?.toFixed(2)}</span>
                                  </div>
                                </div>
                              ))}
                              <div className="text-xs text-white/30 mt-2">
                                These horses have strong crowd support but model confidence is below threshold. Tracked for backtest analysis.
                              </div>
                            </div>
                          )}

                          {/* SPORTSBET-STYLE: Track -> Race -> Runners Flow */}
                          <div className="mt-8 space-y-6">
                            {/* Section Header */}
                            <div className="flex items-center justify-between">
                              <h2 className="text-lg font-bold flex items-center gap-2">
                                <MapPin className="h-5 w-5 text-racing-orange" />
                                RACE MEETS
                              </h2>
                              <span className="text-xs text-white/40">{allTracksForDate.length} tracks available</span>
                            </div>

                            {/* STEP 1: Track Selection - Visual Track Cards */}
                            <div className="grid grid-cols-2 sm:grid-cols-3 md:grid-cols-4 lg:grid-cols-5 gap-3">
                              {allTracksForDate.map((trackName) => {
                                const trackBets = groupedByTrack[trackName] || [];
                                const firstRaceTime = trackBets.length > 0
                                  ? new Date(trackBets[0].offTime).toLocaleTimeString('en-AU', { hour: '2-digit', minute: '2-digit' })
                                  : null;
                                const isSelected = selectedTrack === trackName;

                                return (
                                  <button
                                    key={trackName}
                                    type="button"
                                    onClick={() => {
                                      setSelectedBestTrack(trackName);
                                      setSelectedBestRaceKey("");
                                    }}
                                    className={`relative group text-left rounded-xl border transition-all duration-300 overflow-hidden ${
                                      isSelected
                                        ? "border-racing-orange bg-gradient-to-br from-racing-orange/20 to-racing-orange/5 shadow-[0_0_20px_rgba(249,115,22,0.2)]"
                                        : "border-white/[0.08] bg-white/[0.03] hover:bg-white/[0.06] hover:border-white/[0.15]"
                                    }`}
                                    data-testid={`button-best-track-${trackName.replace(/\s+/g, '-').toLowerCase()}`}
                                  >
                                    {/* Track Status Indicator */}
                                    <div className={`absolute top-0 left-0 right-0 h-1 ${
                                      isSelected ? "bg-racing-orange" : "bg-white/[0.06] group-hover:bg-white/[0.12]"
                                    }`} />

                                    <div className="p-4">
                                      {/* Track Name */}
                                      <h3 className={`font-bold text-sm truncate ${isSelected ? "text-white" : "text-white/90"}`}>
                                        {trackName}
                                      </h3>

                                      {/* Race Count & Time */}
                                      <div className="mt-2 flex items-center gap-2">
                                        <Badge variant="outline" className="text-[10px] px-1.5 py-0 h-4 border-white/10">
                                          {trackBets.length} RACES
                                        </Badge>
                                      </div>

                                      {/* First Race Time */}
                                      {firstRaceTime && (
                                        <div className="mt-2 flex items-center gap-1.5 text-xs text-white/40">
                                          <Clock className="h-3 w-3" />
                                          <span>First: {firstRaceTime}</span>
                                        </div>
                                      )}
                                    </div>

                                    {/* Selected Indicator */}
                                    {isSelected && (
                                      <div className="absolute bottom-2 right-2">
                                        <div className="w-5 h-5 rounded-full bg-racing-orange flex items-center justify-center">
                                          <ChevronRight className="h-3 w-3 text-black" />
                                        </div>
                                      </div>
                                    )}
                                  </button>
                                );
                              })}
                            </div>

                            {/* STEP 2: Race Selection - Horizontal Race Bar (Sportsbet Style) */}
                            {selectedTrack && (
                              <div className="space-y-3 animate-in slide-in-from-top-2">
                                <div className="flex items-center justify-between">
                                  <h3 className="text-sm font-semibold text-white/60 flex items-center gap-2">
                                    <Flag className="h-4 w-4 text-racing-orange" />
                                    {selectedTrack} - SELECT RACE
                                  </h3>
                                  <span className="text-xs text-white/40">{raceOptions.length} races</span>
                                </div>

                                {/* Race Number Strip */}
                                <div className="flex gap-2 overflow-x-auto pb-2 scrollbar-thin scrollbar-thumb-racing-orange/30 scrollbar-track-transparent">
                                  {raceOptions.map((race) => {
                                    const isActiveRace = selectedRaceKey === race.key;
                                    const modelCount = race.bets.filter((b) => b.betType !== "tip_only").length;
                                    const tipCount = race.bets.filter((b) => b.betType === "tip_only").length;
                                    const raceTime = new Date(race.offTime);
                                    const isJumped = raceTime < now;
                                    const isSoon = !isJumped && (raceTime.getTime() - now.getTime()) < 30 * 60 * 1000;

                                    return (
                                      <button
                                        key={race.key}
                                        type="button"
                                        onClick={() => setSelectedBestRaceKey(race.key)}
                                        className={`relative flex-shrink-0 w-20 rounded-lg border transition-all duration-200 ${
                                          isActiveRace
                                            ? "border-racing-orange bg-racing-orange/15 shadow-[0_0_15px_rgba(249,115,22,0.25)]"
                                            : isJumped
                                              ? "border-white/[0.05] bg-white/[0.02] opacity-50"
                                              : isSoon
                                                ? "border-yellow-500/30 bg-yellow-500/5 hover:bg-yellow-500/10"
                                                : "border-white/[0.08] bg-white/[0.03] hover:bg-white/[0.06]"
                                        }`}
                                        data-testid={`button-best-race-${race.raceNumber}`}
                                      >
                                        {/* Race Number */}
                                        <div className={`py-2 text-center border-b ${
                                          isActiveRace
                                            ? "border-racing-orange/30 bg-racing-orange/10"
                                            : isJumped
                                              ? "border-white/[0.05]"
                                              : isSoon
                                                ? "border-yellow-500/20 bg-yellow-500/5"
                                                : "border-white/[0.06] bg-white/[0.02]"
                                        }`}>
                                          <span className={`text-lg font-bold ${
                                            isActiveRace ? "text-racing-orange" : isJumped ? "text-white/30" : "text-white"
                                          }`}>
                                            R{race.raceNumber}
                                          </span>
                                        </div>

                                        {/* Race Info */}
                                        <div className="p-2 text-center space-y-1">
                                          <p className="text-xs text-white/50">
                                            {raceTime.toLocaleTimeString('en-AU', { hour: '2-digit', minute: '2-digit' })}
                                          </p>
                                          <p className="text-[10px] text-white/30">{race.distance}</p>

                                          {/* Status Indicator */}
                                          {isJumped ? (
                                            <span className="text-[9px] text-white/20 uppercase">Jumped</span>
                                          ) : isSoon ? (
                                            <span className="text-[9px] text-yellow-400 uppercase">Soon</span>
                                          ) : modelCount > 0 ? (
                                            <span className="text-[9px] text-racing-orange">{modelCount} pick{modelCount > 1 ? 's' : ''}</span>
                                          ) : tipCount > 0 ? (
                                            <span className="text-[9px] text-amber-400">Tip</span>
                                          ) : null}
                                        </div>
                                      </button>
                                    );
                                  })}
                                </div>
                              </div>
                            )}

                            {/* STEP 3: Selected Race Runners Display */}
                            {selectedRace && (
                              <div className="animate-in slide-in-from-bottom-4 duration-300">
                                {/* Race Header Card */}
                                <Card className="glass-card border-0 overflow-hidden">
                                  {/* Race Header */}
                                  <div className="relative bg-gradient-to-r from-racing-orange/20 via-racing-orange/10 to-transparent border-b border-racing-orange/20">
                                    <div className="p-4 sm:p-5">
                                      <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-3">
                                        <div>
                                          <div className="flex items-center gap-3">
                                            <h3 className="text-xl font-bold text-white">
                                              {selectedTrack} <span className="text-racing-orange">Race {selectedRace.raceNumber}</span>
                                            </h3>
                                            {selectedRace.className && (
                                              <Badge className="bg-white/10 text-white/70 border-0 text-xs">
                                                {selectedRace.className}
                                              </Badge>
                                            )}
                                          </div>
                                          <p className="text-sm text-white/50 mt-1">{selectedRace.raceName}</p>
                                        </div>

                                        <div className="flex items-center gap-4 text-sm">
                                          <div className="flex items-center gap-1.5 text-white/40">
                                            <Clock className="h-4 w-4 text-racing-orange" />
                                            <span>{new Date(selectedRace.offTime).toLocaleTimeString('en-AU', { hour: '2-digit', minute: '2-digit' })}</span>
                                          </div>
                                          <div className="flex items-center gap-1.5 text-white/40">
                                            <ArrowLeft className="h-4 w-4 rotate-90 text-racing-orange" />
                                            <span>{selectedRace.distance}</span>
                                          </div>
                                          {selectedRace.going && (
                                            <Badge variant="outline" className="text-xs border-white/10">
                                              {selectedRace.going}
                                            </Badge>
                                          )}
                                        </div>
                                      </div>
                                    </div>

                                    {/* Model Picks Summary */}
                                    {selectedRace.bets.length > 0 && (
                                      <div className="px-4 pb-3 flex flex-wrap gap-2">
                                        {selectedRace.bets.filter(b => b.betType !== "tip_only").map((bet) => (
                                          <Badge
                                            key={bet.id}
                                            className={`text-xs ${
                                              bet.betType === "banker"
                                                ? "bg-green-500/20 text-green-400 border-green-500/30"
                                                : "bg-racing-orange/20 text-racing-orange border-racing-orange/30"
                                            }`}
                                          >
                                            <Target className="h-3 w-3 mr-1" />
                                            {bet.horseName}
                                          </Badge>
                                        ))}
                                      </div>
                                    )}
                                  </div>

                                  {/* Runners List - Full Field with Groq Analysis */}
                                  <CardContent className="p-0">
                                    <RaceRunnerPanel
                                      selectedRace={selectedRace}
                                      fullFieldRunners={selectedFieldRace?.full_field}
                                      speedMap={selectedFieldRace?.speedMap || null}
                                      highlightedSelections={highlightedSelections}
                                      raceDate={selectedRace.bets?.[0]?.raceDate || raceFieldDateParam}
                                      isLoadingSimulationRaces={isLoadingSimulationRaces}
                                    />
                                  </CardContent>
                                </Card>
                              </div>
                            )}
                          </div>

                          {/* Legacy race cards retained below but hidden */}
                          {false && (
                            <div className="mt-8">
                            <h2 className="text-lg font-bold mb-4 flex items-center gap-2">
                              <TrendingUp className="h-5 w-5 text-racing-orange" />
                              RACE BY RACE SELECTIONS
                              <span className="text-sm font-normal text-white/40 ml-2">
                                ({filteredBestBets.length} model picks {filteredTips.length > 0 ? `\u2022 ${filteredTips.length} tips` : ''}{filteredBetsPerRace.filter(b => b.selectionType === 'exoticTip').length > 0 ? ` \u2022 ${filteredBetsPerRace.filter(b => b.selectionType === 'exoticTip').length} exotics` : ''})
                              </span>
                            </h2>

                            {sortedTracks.map((trackName) => {
                              const trackBets = [...groupedByTrack[trackName]].sort((a, b) => {
                                const raceNumberDiff = (a.raceNumber ?? 0) - (b.raceNumber ?? 0);
                                if (raceNumberDiff !== 0) return raceNumberDiff;

                                const aIsTip = a.betType === 'tip_only' ? 1 : 0;
                                const bIsTip = b.betType === 'tip_only' ? 1 : 0;
                                if (aIsTip !== bIsTip) return aIsTip - bIsTip;

                                return new Date(a.offTime).getTime() - new Date(b.offTime).getTime();
                              });
                              const trackModelCount = trackBets.filter(b => b.betType !== 'tip_only').length;
                              const trackTipCount = trackBets.filter(b => b.betType === 'tip_only').length;

                              return (
                                <div key={trackName} className="mb-6">
                                  <div className="flex items-center gap-2 mb-3 pb-2 border-b border-white/[0.06]">
                                    <MapPin className="h-4 w-4 text-racing-orange" />
                                    <h3 className="font-bold text-white">{trackName}</h3>
                                    <Badge variant="outline" className="text-xs">{trackBets.length} races</Badge>
                                    {trackTipCount > 0 && (
                                      <span className="text-xs text-white/30">{trackModelCount} model {trackTipCount > 0 ? `\u2022 ${trackTipCount} tips` : ''}</span>
                                    )}
                                  </div>

                                  <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4 gap-3">
                                    {trackBets.map((bet) => {
                                      const pickIdentity = getPickIdentityInfo(bet);
                                      const betTypeInfo = getBetTypeInfo(bet.betType, bet.edge);
                                      const isExpanded = expandedBestBets.has(`race-${bet.id}`);
                                      const isTipOnly = bet.betType === 'tip_only';
                                      const isExotic = bet.selectionType === 'exoticTip';
                                      return (
                                        <Card
                                          key={bet.id}
                                          className={isTipOnly
                                            ? "glass-card border-0 border-l-2 border-l-amber-500/30 opacity-80 hover:opacity-100 transition-all cursor-pointer"
                                            : isExotic
                                              ? "glass-card border-0 border-l-2 border-l-purple-500/50 hover:border-purple-500/30 transition-colors cursor-pointer"
                                              : bet.betType === "banker"
                                                ? "glass-card border-0 border-l-2 border-l-green-500/70 shadow-[0_0_12px_rgba(34,197,94,0.15)] hover:border-green-500/30 transition-colors cursor-pointer"
                                                : "glass-card border-0 hover:border-racing-orange/30 transition-colors cursor-pointer"
                                          }
                                          onClick={() => toggleBestBetCard(`race-${bet.id}`)}
                                          data-testid={`card-race-bet-${bet.id}`}
                                        >
                                          <CardContent className="p-3 space-y-2">
                                            <div className="flex items-center justify-between gap-2">
                                              <div className="flex items-center gap-2">
                                                <Badge variant="outline" className="text-xs font-mono">R{bet.raceNumber}</Badge>
                                                <span className="text-xs text-white/40">
                                                  {new Date(bet.offTime).toLocaleTimeString('en-AU', { hour: '2-digit', minute: '2-digit' })}
                                                </span>
                                              </div>
                                              <div className="flex items-center gap-1 flex-wrap">
                                                {isTipOnly ? (
                                                  <Badge className={`${pickIdentity.color} border text-xs gap-1`}>
                                                    <AlertTriangle className="h-3 w-3" />
                                                    {pickIdentity.label}
                                                  </Badge>
                                                ) : isExotic ? (
                                                  <Badge className="bg-purple-500/20 text-purple-400 border-purple-500/50 border text-xs gap-1" data-testid={`badge-exotic-${bet.id}`}>
                                                    <Layers className="h-3 w-3" />
                                                    {bet.exoticType === 'quinella' ? 'QUINELLA' :
                                                     bet.exoticType === 'exacta' ? 'EXACTA' :
                                                     bet.exoticType === 'boxedTrifecta' ? 'BOX TRIFECTA' : 'TRIFECTA'}
                                                  </Badge>
                                                ) : (
                                                  <>
                                                    <Badge className={`${pickIdentity.color} border text-xs`}>
                                                      {pickIdentity.label}
                                                    </Badge>
                                                    {bet.shouldBet !== false && (
                                                      <Badge className={`${betTypeInfo.color} border text-xs`}>
                                                        {betTypeInfo.label}
                                                      </Badge>
                                                    )}
                                                    {bet.isBestRated && (
                                                      <Badge className="bg-blue-500/20 text-blue-400 border-blue-500/30 border text-xs">
                                                        BEST RATED
                                                      </Badge>
                                                    )}
                                                  </>
                                                )}
                                                {bet.betType === "banker" && bet.bankerScore != null && (
                                                  <Badge variant="outline" className="text-[10px] border-green-500/30 text-green-400 no-default-hover-elevate no-default-active-elevate" data-testid={`badge-banker-score-race-${bet.id}`}>
                                                    Score: {bet.bankerScore}
                                                  </Badge>
                                                )}
                                                {!isTipOnly && bet.soleQualifier && (
                                                  <Badge className="bg-amber-500/20 text-amber-400 border-amber-500/30 border text-xs">
                                                    ONLY VALUE PICK
                                                  </Badge>
                                                )}
                                                {isExpanded ? (
                                                  <ChevronUp className="h-4 w-4 text-white/40" />
                                                ) : (
                                                  <ChevronDown className="h-4 w-4 text-white/40" />
                                                )}
                                              </div>
                                            </div>

                                            <div className="flex items-center justify-between">
                                              <div className="flex-1 min-w-0">
                                                <p className="font-bold text-white truncate">{bet.horseName}</p>
                                                {isExotic && bet.exoticPicks ? (
                                                  <div className="space-y-1" data-testid={`exotic-picks-${bet.id}`}>
                                                    <p className="text-xs text-purple-400 font-medium">
                                                      Banker: {bet.exoticPicks.banker.horseName} ${bet.exoticPicks.banker.odds.toFixed(2)}
                                                    </p>
                                                    <div className="flex flex-wrap gap-1">
                                                      {bet.exoticPicks.fill.map((f, i) => (
                                                        <span key={i} className="text-xs text-white/50">
                                                          {f.horseName} ${f.odds.toFixed(2)}{i < bet.exoticPicks!.fill.length - 1 ? ',' : ''}
                                                        </span>
                                                      ))}
                                                    </div>
                                                    <p className="text-xs text-white/30">
                                                      {bet.exoticPicks.estimatedCombinations} combos | {bet.exoticPicks.suggestedOutlay}
                                                    </p>
                                                  </div>
                                                ) : (
                                                  <>
                                                    <p className="text-xs text-white/40 truncate">{bet.jockey}</p>
                                                    {isTipOnly && (
                                                      <p className="text-xs text-amber-400/70 flex items-center gap-1">
                                                        <AlertTriangle className="h-3 w-3" />
                                                        Not model aligned — tip only
                                                      </p>
                                                    )}
                                                    {!isTipOnly && bet.isBestRated && (
                                                      <p className="text-xs text-blue-400/60">Top model pick — no value edge</p>
                                                    )}
                                                    {bet.marketFavourite && (
                                                      <p className="text-xs text-white/30 flex items-center gap-1" data-testid={`market-fav-${bet.track}-R${bet.raceNumber}`}>
                                                        <TrendingUp className="h-3 w-3 text-white/20" />
                                                        Market fav: {bet.marketFavourite.horseName} ${bet.marketFavourite.odds.toFixed(2)}
                                                      </p>
                                                    )}
                                                  </>
                                                )}
                                              </div>
                                              <div className="text-right">
                                                {isExotic ? (
                                                  <>
                                                    <p className="text-sm font-bold text-purple-400" data-testid={`exotic-field-${bet.id}`}>{bet.fieldSize || '?'} runners</p>
                                                    <p className="text-xs text-white/40">Win {(bet.winPercentage ?? 0).toFixed(0)}%</p>
                                                  </>
                                                ) : (
                                                  <>
                                                    <p className={`font-bold ${!bet.marketOdds ? 'text-white/30' : isTipOnly ? 'text-gray-400' : bet.isBestRated ? 'text-blue-400' : 'text-green-400'}`}>{bet.marketOdds ? `$${bet.marketOdds.toFixed(2)}` : 'TBC'}</p>
                                                    <p className="text-xs text-white/40">{(bet.winPercentage ?? 0).toFixed(0)}%</p>
                                                  </>
                                                )}
                                              </div>
                                            </div>

                                            {bet.pipelineSignals && (
                                              <p className="text-xs text-white/30 font-mono tracking-wide truncate" data-testid={`pipeline-signals-race-${bet.id}`}>
                                                {bet.pipelineSignals}
                                              </p>
                                            )}

                                            {bet.sectionalEdgeInsight && (
                                              <p className={`text-xs flex items-center gap-1 ${bet.sectionalEdgeMultiplier && bet.sectionalEdgeMultiplier > 1 ? 'text-emerald-400/70' : 'text-amber-400/60'}`} data-testid={`sectional-insight-race-${bet.id}`}>
                                                <Activity className="h-3 w-3" />
                                                {bet.sectionalEdgeInsight}
                                              </p>
                                            )}

                                            <div className="flex items-center justify-between pt-1">
                                              <div className="flex items-center gap-2">
                                                <div className="flex gap-0.5">
                                                  {Array.from({ length: 5 }).map((_, i) => (
                                                    <Star
                                                      key={i}
                                                      className={`h-3 w-3 ${i < bet.rating ? (isTipOnly ? "text-yellow-400/70 fill-yellow-400/70" : "text-yellow-400 fill-yellow-400") : "text-white/20"}`}
                                                    />
                                                  ))}
                                                </div>
                                                {isTipOnly && <span className="text-xs text-white/20">{bet.distance}</span>}
                                                <AiScoreBadge score={bet.aiScore} />
                                                <LucklessBadge flag={bet.lucklessFlag} score={bet.lucklessScore} />
                                              </div>
                                              <Button
                                                size="sm"
                                                className={isInSlip(bet.horseName, bet.track, bet.raceNumber) ? "bg-green-600" : isExotic ? "bg-purple-600 text-white" : "bg-racing-orange text-black"}
                                                disabled={slipLoading || isInSlip(bet.horseName, bet.track, bet.raceNumber)}
                                                onClick={(e) => {
                                                  e.stopPropagation();
                                                  addSelection({
                                                    horseName: bet.horseName,
                                                    track: bet.track,
                                                    raceNumber: bet.raceNumber,
                                                    raceDate: bet.raceDate,
                                                    raceTime: bet.offTime,
                                                    odds: bet.marketOdds ?? 0,
                                                    predictedWinProb: (bet.winPercentage ?? 0) / 100,
                                                    confidenceScore: bet.confidenceScore ?? 0,
                                                    betType: "win",
                                                    reasons: bet.formAnalystInsights || bet.reasons || []
                                                  }, e);
                                                }}
                                                data-testid={`button-add-slip-${bet.track}-R${bet.raceNumber}`}
                                              >
                                                {isInSlip(bet.horseName, bet.track, bet.raceNumber) ? <Check className="h-3 w-3 mr-1" /> : <Plus className="h-3 w-3 mr-1" />}
                                                {isInSlip(bet.horseName, bet.track, bet.raceNumber) ? "Added" : isExotic ? "Exotic" : "Add"}
                                              </Button>
                                            </div>

                                            <div className="space-y-1">
                                              <div className="flex justify-between text-xs">
                                                <span className="text-white/40">Confidence</span>
                                                <div className="flex items-center gap-2">
                                                  {bet.riskLevel && (
                                                    <span className={`text-[10px] font-semibold uppercase ${
                                                      bet.riskLevel === 'low' ? 'text-green-400' :
                                                      bet.riskLevel === 'medium' ? 'text-yellow-400' :
                                                      'text-red-400'
                                                    }`}>{bet.riskLevel}</span>
                                                  )}
                                                  <span className="font-medium text-yellow-400">{(bet.confidenceScore ?? 0).toFixed(0)}/100</span>
                                                </div>
                                              </div>
                                              <Progress value={bet.confidenceScore ?? 0} className="h-1.5" />
                                            </div>

                                            {isExpanded && (
                                              <div className="pt-2 border-t border-white/[0.06] space-y-2 animate-in slide-in-from-top-2">
                                                {isExotic && bet.exoticRationale && (
                                                  <div className="p-2 rounded bg-purple-500/10 border border-purple-500/20" data-testid={`exotic-rationale-${bet.id}`}>
                                                    <p className="text-xs text-purple-300">{bet.exoticRationale}</p>
                                                    {bet.exoticPicks && (
                                                      <p className="text-xs text-white/40 mt-1 font-mono">{bet.exoticPicks.structure}</p>
                                                    )}
                                                  </div>
                                                )}
                                                {bet.frankingScore != null && bet.frankingScore > 0 && (
                                                  <div className="flex items-center gap-1.5 mt-1" data-testid={`franking-indicator-${bet.id}`}>
                                                    <div className={`w-2 h-2 rounded-full ${
                                                      bet.isAntiFranked ? 'bg-red-500' :
                                                      bet.frankingScore >= 60 ? 'bg-green-500' :
                                                      bet.frankingScore >= 50 ? 'bg-yellow-500' :
                                                      'bg-gray-500'
                                                    }`} />
                                                    <span className="text-xs text-white/60">
                                                      {bet.isAntiFranked ? 'Anti-Franked' :
                                                       bet.frankingScore >= 60 ? `Franked ${bet.frankingScore.toFixed(0)}` :
                                                       bet.frankingScore >= 50 ? `Form OK ${bet.frankingScore.toFixed(0)}` :
                                                       `Weak Form ${bet.frankingScore.toFixed(0)}`}
                                                    </span>
                                                    {bet.frankingElo != null && bet.frankingElo >= 75 && (
                                                      <span className="text-xs text-orange-400/80">ELO {bet.frankingElo.toFixed(0)}</span>
                                                    )}
                                                  </div>
                                                )}

                                                {bet.fitnessRunLabel && (
                                                  <div className="flex items-center gap-1.5 mt-1" data-testid={`fitness-indicator-${bet.id}`}>
                                                    <Activity className="h-3 w-3 text-cyan-400" />
                                                    <span className="text-xs text-white/60">{bet.fitnessRunLabel}</span>
                                                    {bet.fitnessIsAtPeakRun && (
                                                      <Badge variant="outline" className="text-[10px] px-1.5 py-0 h-4 border-green-500/40 text-green-400 no-default-hover-elevate no-default-active-elevate">
                                                        Peak
                                                      </Badge>
                                                    )}
                                                    {bet.fitnessPrepTrajectory && (
                                                      <span className={`text-xs ${
                                                        bet.fitnessPrepTrajectory.toLowerCase() === 'improving' || bet.fitnessPrepTrajectory.toLowerCase() === 'peaking' ? 'text-green-400' :
                                                        bet.fitnessPrepTrajectory.toLowerCase() === 'stable' ? 'text-yellow-400' :
                                                        bet.fitnessPrepTrajectory.toLowerCase() === 'declining' ? 'text-red-400' :
                                                        'text-white/40'
                                                      }`}>
                                                        {bet.fitnessPrepTrajectory.toLowerCase() === 'improving' || bet.fitnessPrepTrajectory.toLowerCase() === 'peaking' ? (
                                                          <span className="flex items-center gap-0.5"><TrendingUp className="h-3 w-3" />{bet.fitnessPrepTrajectory}</span>
                                                        ) : bet.fitnessPrepTrajectory.toLowerCase() === 'declining' ? (
                                                          <span className="flex items-center gap-0.5"><ChevronDown className="h-3 w-3" />{bet.fitnessPrepTrajectory}</span>
                                                        ) : (
                                                          <span>{bet.fitnessPrepTrajectory}</span>
                                                        )}
                                                      </span>
                                                    )}
                                                  </div>
                                                )}

                                                {!isTipOnly && (bet.recalibrationApplied || bet.sectionalMcEnhanced || bet.mlModelActive) && (
                                                  <div className="flex flex-wrap items-center gap-1.5">
                                                    {bet.recalibrationApplied && (
                                                      <Badge variant="outline" className="text-[10px] border-blue-500/30 text-blue-400 gap-1">
                                                        <Activity className="h-2.5 w-2.5" />
                                                        RECALIBRATED {bet.recalibrationShift != null && bet.recalibrationShift !== 0 && (
                                                          <span className={bet.recalibrationShift > 0 ? "text-green-400" : "text-red-400"}>
                                                            {bet.recalibrationShift > 0 ? "+" : ""}{bet.recalibrationShift.toFixed(1)}pp
                                                          </span>
                                                        )}
                                                      </Badge>
                                                    )}
                                                    {bet.sectionalMcEnhanced && (
                                                      <Badge variant="outline" className="text-[10px] border-purple-500/30 text-purple-400 gap-1">
                                                        <Zap className="h-2.5 w-2.5" />
                                                        SECTIONAL MC
                                                      </Badge>
                                                    )}
                                                    {bet.mlModelActive && (
                                                      <Badge variant="outline" className="text-[10px] border-orange-500/30 text-orange-400 gap-1">
                                                        <Brain className="h-2.5 w-2.5" />
                                                        ML ENSEMBLE
                                                      </Badge>
                                                    )}
                                                  </div>
                                                )}

                                                {bet.modelSummary && bet.modelSummary.length > 0 && (
                                                  <div className="bg-white/[0.03] rounded p-2">
                                                    <p className="text-xs text-white/40 mb-1 flex items-center gap-1">
                                                      <BarChart3 className="h-3 w-3 text-blue-400" />
                                                      MODEL
                                                    </p>
                                                    <div className="space-y-0.5">
                                                      {bet.modelSummary.slice(0, 3).map((point, i) => (
                                                        <p key={i} className="text-xs text-blue-400/90">{'\u2022'} {point}</p>
                                                      ))}
                                                    </div>
                                                  </div>
                                                )}

                                                {((bet.reasons && bet.reasons.length > 0) || (bet.formAnalystInsights && bet.formAnalystInsights.length > 0)) && (
                                                  <div className="bg-white/[0.03] rounded p-2" data-testid={`confidence-reasons-${bet.id}`}>
                                                    <p className="text-xs text-white/40 mb-1.5 flex items-center gap-1">
                                                      <Sparkles className="h-3 w-3 text-yellow-400" />
                                                      WHY THIS RATING
                                                    </p>
                                                    <div className="space-y-1">
                                                      {(bet.formAnalystInsights || bet.reasons || []).slice(0, 5).map((reason, i) => (
                                                        <div key={i} className="flex items-start gap-1.5">
                                                          <div className={`w-1.5 h-1.5 rounded-full mt-1 flex-shrink-0 ${
                                                            i === 0 ? 'bg-yellow-400' : i === 1 ? 'bg-orange-400' : 'bg-white/30'
                                                          }`} />
                                                          <p className="text-xs text-white/60 leading-relaxed">{reason}</p>
                                                        </div>
                                                      ))}
                                                    </div>
                                                  </div>
                                                )}

                                                <div>
                                                  <p className="text-xs text-white/40 mb-1 flex items-center gap-1">
                                                    <CheckCircle className="h-3 w-3 text-green-400" />
                                                    FORM ANALYSIS
                                                  </p>
                                                  <div className="space-y-0.5">
                                                    {(bet.formAnalystInsights || bet.reasons || []).slice(0, 4).map((insight, i) => (
                                                      <p key={i} className="text-xs text-green-400/90">{'\u2022'} {insight}</p>
                                                    ))}
                                                  </div>
                                                </div>

                                                {/* AI Form Analysis */}
                                                {bet.aiInsight && (
                                                  <AiInsightDisplay
                                                    insight={bet.aiInsight}
                                                    aiScore={bet.aiScore}
                                                    llmProvider={bet.llmProvider}
                                                  />
                                                )}

                                                {/* Luckless Last Start Excuse */}
                                                {bet.lucklessFlag && bet.lucklessExplanation && (
                                                  <div className="mt-2 p-3 bg-amber-500/10 border border-amber-500/30 rounded-lg">
                                                    <div className="flex items-center gap-2 mb-1">
                                                      <span className="text-amber-400 font-semibold text-sm">Last Start Excuse</span>
                                                    </div>
                                                    <p className="text-sm text-amber-200/80">{bet.lucklessExplanation}</p>
                                                  </div>
                                                )}

                                                {bet.betType === "banker" && bet.bankerStaking && (
                                                  <div className="bg-green-500/10 border border-green-500/20 rounded p-2" data-testid={`banker-staking-race-${bet.id}`}>
                                                    <p className="text-xs text-green-400 font-medium">Banker Staking</p>
                                                    <p className="text-sm text-green-300">{bet.bankerStaking}</p>
                                                  </div>
                                                )}

                                                <div className="flex gap-3 text-xs text-white/40 pt-1">
                                                  <span>Form: <span className="font-mono text-white">{bet.form || "N/A"}</span></span>
                                                  <span>Barrier: <span className="text-white">{bet.barrier}</span></span>
                                                  <span>Distance: <span className="text-white">{bet.distance}</span></span>
                                                  <span className={getRiskColor(bet.riskLevel)}>{bet.riskLevel.toUpperCase()}</span>
                                                </div>
                                              </div>
                                            )}
                                          </CardContent>
                                        </Card>
                                      );
                                    })}
                                  </div>
                                </div>
                              );
                            })}
                          </div>
                          )}
                        </>
                      )}
                    </>
                  );
                })()}

                {/* Legend */}
                <div className="p-4 bg-white/[0.03] rounded-lg">
                  <h3 className="text-sm font-semibold text-white mb-3">Bet Types Explained</h3>
                  <div className="grid sm:grid-cols-6 gap-3 text-xs text-white/40">
                    <div>
                      <Badge className="bg-green-500/30 text-green-300 border-green-400/60 border mb-1">BANKER</Badge>
                      <p>Dominant short-price pick — multi-layer consensus confirmed</p>
                    </div>
                    <div>
                      <Badge className="bg-blue-500/20 text-blue-400 border-blue-500/50 border mb-1">VALUE</Badge>
                      <p>Price exceeds model's predicted probability</p>
                    </div>
                    <div>
                      <Badge className="bg-orange-500/20 text-orange-400 border-orange-500/50 border mb-1">FORM</Badge>
                      <p>Outstanding recent form warrants selection</p>
                    </div>
                    <div>
                      <Badge className="bg-purple-500/20 text-purple-400 border-purple-500/50 border mb-1">ROUGHIE</Badge>
                      <p>Longshot with genuine winning credentials</p>
                    </div>
                    <div>
                      <Badge className="bg-gray-500/20 text-gray-400 border-gray-500/50 border mb-1">TIP ONLY</Badge>
                      <p>Guide only — selection does not align with the model</p>
                    </div>
                    <div data-testid="legend-exotic">
                      <Badge className="bg-purple-500/20 text-purple-400 border-purple-500/50 border mb-1 gap-1"><Layers className="h-3 w-3" />EXOTIC</Badge>
                      <p>Multi-horse exotic bet (quinella, exacta, trifecta)</p>
                    </div>
                  </div>
                </div>
              </>
            )}
          </>
        )}

        {/* BLACKBOOK VIEW */}
        {viewMode === "blackbook" && (
          <BlackbookView />
        )}

        {/* LEGACY VALUE BETS VIEW - intentionally removed from the product surface */}
        {false && viewMode === "blackbook" && (
          <>
        {/* Enhanced Quick Stats Dashboard */}
        <div className="grid grid-cols-2 lg:grid-cols-4 gap-3">
          {/* Total Bets */}
          <Card className="glass-card border-0">
            <CardContent className="p-3 flex items-center gap-3">
              <div className="p-2 rounded-md bg-primary/20">
                <Target className="h-5 w-5 text-racing-orange" />
              </div>
              <div>
                <p className="text-xs text-white/35 uppercase tracking-wide">Total Bets</p>
                <p className="text-xl font-bold" data-testid="text-total-bets">{stats.totalBets}</p>
              </div>
            </CardContent>
          </Card>

          {/* Avg EV */}
          <Card className="glass-card border-0">
            <CardContent className="p-3 flex items-center gap-3">
              <div className="p-2 rounded-md bg-racing-gold/20">
                <TrendingUp className="h-5 w-5 text-racing-gold" />
              </div>
              <div>
                <p className="text-xs text-white/35 uppercase tracking-wide">Avg EV</p>
                <p className="text-xl font-bold" data-testid="text-average-ev">+{stats.avgEV.toFixed(1)}%</p>
              </div>
            </CardContent>
          </Card>

          {/* Best Pick Card - Highlighted */}
          <Card className="bg-gradient-to-br from-yellow-500/20 to-orange-500/10 border-yellow-500/40" data-testid="card-best-pick">
            <CardContent className="p-3">
              <div className="flex items-center gap-2 mb-1">
                <Crown className="h-4 w-4 text-yellow-500" />
                <p className="text-xs text-yellow-500 uppercase tracking-wide font-semibold">Best Pick</p>
              </div>
              {stats.bestPick ? (
                <div>
                  <p className="font-bold text-white truncate">{stats.bestPick?.horseName}</p>
                  <p className="text-xs text-white/40">{stats.bestPick?.track} R{stats.bestPick?.raceNumber}</p>
                  <p className="text-sm font-semibold text-green-400">+{stats.bestPick?.expectedValue?.toFixed(0)}% EV</p>
                </div>
              ) : (
                <p className="text-sm text-white/40">No bets found</p>
              )}
            </CardContent>
          </Card>

          {/* Next Race Countdown */}
          <Card className={`border-white/[0.06] ${stats.nextRace && isStartingSoon(stats.nextRace.offTime) ? 'bg-red-500/10 border-red-500/40 animate-pulse' : 'glass-card'}`} data-testid="card-next-race">
            <CardContent className="p-3">
              <div className="flex items-center gap-2 mb-1">
                <Timer className={`h-4 w-4 ${stats.nextRace && isStartingSoon(stats.nextRace.offTime) ? 'text-red-500' : 'text-blue-500'}`} />
                <p className={`text-xs uppercase tracking-wide font-semibold ${stats.nextRace && isStartingSoon(stats.nextRace.offTime) ? 'text-red-500' : 'text-blue-500'}`}>Next Race</p>
              </div>
              {stats.nextRace ? (
                <div>
                  <p className={`text-lg font-bold ${stats.nextRace && isStartingSoon(stats.nextRace.offTime) ? 'text-red-400' : 'text-white'}`} data-testid="text-countdown">
                    {formatCountdown(stats.nextRace.offTime)}
                  </p>
                  <p className="text-xs text-white/40 truncate">{stats.nextRace.track} R{stats.nextRace.raceNumber}</p>
                </div>
              ) : (
                <p className="text-sm text-white/40">No upcoming races</p>
              )}
            </CardContent>
          </Card>
        </div>

        {/* Secondary Stats Row */}
        <div className="grid grid-cols-3 gap-3">
          <Card className="glass-card border-0">
            <CardContent className="p-3 flex items-center gap-3">
              <div className="p-2 rounded-md bg-success/20">
                <Zap className="h-5 w-5 text-success" />
              </div>
              <div>
                <p className="text-xs text-white/35 uppercase tracking-wide">Avg Edge</p>
                <p className="text-xl font-bold">{stats.avgEdge.toFixed(1)}%</p>
              </div>
            </CardContent>
          </Card>

          <Card className="glass-card border-0">
            <CardContent className="p-3 flex items-center gap-3">
              <div className="p-2 rounded-md bg-blue-500/20">
                <BarChart3 className="h-5 w-5 text-blue-500" />
              </div>
              <div>
                <p className="text-xs text-white/35 uppercase tracking-wide">Avg Win %</p>
                <p className="text-xl font-bold">{stats.avgWinPct.toFixed(1)}%</p>
              </div>
            </CardContent>
          </Card>

          <Card className="glass-card border-0">
            <CardContent className="p-3 flex items-center gap-3">
              <div className="p-2 rounded-md bg-purple-500/20">
                <Gauge className="h-5 w-5 text-purple-500" />
              </div>
              <div>
                <p className="text-xs text-white/35 uppercase tracking-wide">High Conf</p>
                <p className="text-xl font-bold">{stats.highConfidence}</p>
              </div>
            </CardContent>
          </Card>
        </div>

        {/* Advanced Filters Panel */}
        {showFilters && (
          <Card className="glass-card border-0">
            <CardContent className="p-4 space-y-4">
              {/* Search */}
              <div className="relative">
                <Search className="absolute left-3 top-1/2 -translate-y-1/2 h-4 w-4 text-white/40" />
                <Input
                  placeholder="Search horse, jockey, trainer, or track..."
                  value={searchQuery}
                  onChange={(e) => setSearchQuery(e.target.value)}
                  className="pl-10 glass-input"
                  data-testid="input-search"
                />
                {searchQuery && (
                  <Button
                    variant="ghost"
                    size="icon"
                    className="absolute right-1 top-1/2 -translate-y-1/2 h-7 w-7"
                    onClick={() => setSearchQuery("")}
                  >
                    <X className="h-4 w-4" />
                  </Button>
                )}
              </div>

              {/* Filter Row 1: Main Filters */}
              <div className="grid grid-cols-2 sm:grid-cols-4 lg:grid-cols-6 gap-3">
                <Select value={trackFilter} onValueChange={setTrackFilter}>
                  <SelectTrigger className="glass-input" data-testid="select-track-filter">
                    <MapPin className="h-4 w-4 mr-2 text-white/40" />
                    <SelectValue placeholder="Track" />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="all">All Tracks</SelectItem>
                    {uniqueTracks.map((track) => (
                      <SelectItem key={track} value={track}>{track}</SelectItem>
                    ))}
                  </SelectContent>
                </Select>

                <Select value={dateFilter} onValueChange={setDateFilter}>
                  <SelectTrigger className="glass-input" data-testid="select-date-filter">
                    <Calendar className="h-4 w-4 mr-2 text-white/40" />
                    <SelectValue placeholder="Date" />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="all">All Dates</SelectItem>
                    {uniqueDates.map((dateObj) => (
                      <SelectItem key={dateObj.value} value={dateObj.value}>{dateObj.label}</SelectItem>
                    ))}
                  </SelectContent>
                </Select>

                <Select value={confidenceFilter} onValueChange={setConfidenceFilter}>
                  <SelectTrigger className="glass-input" data-testid="select-confidence-filter">
                    <Gauge className="h-4 w-4 mr-2 text-white/40" />
                    <SelectValue placeholder="Confidence" />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="all">All Confidence</SelectItem>
                    <SelectItem value="high">High</SelectItem>
                    <SelectItem value="medium">Medium</SelectItem>
                    <SelectItem value="low">Low</SelectItem>
                  </SelectContent>
                </Select>

                <Select value={valueRatingFilter} onValueChange={setValueRatingFilter}>
                  <SelectTrigger className="glass-input" data-testid="select-value-filter">
                    <TrendingUp className="h-4 w-4 mr-2 text-white/40" />
                    <SelectValue placeholder="Value" />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="all">All Value</SelectItem>
                    {uniqueValueRatings.map((rating) => (
                      <SelectItem key={rating} value={rating}>{rating}</SelectItem>
                    ))}
                  </SelectContent>
                </Select>

                <Select value={runningStyleFilter} onValueChange={setRunningStyleFilter}>
                  <SelectTrigger className="glass-input" data-testid="select-style-filter">
                    <Activity className="h-4 w-4 mr-2 text-white/40" />
                    <SelectValue placeholder="Style" />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="all">All Styles</SelectItem>
                    {uniqueRunningStyles.map((style) => (
                      <SelectItem key={style} value={style}>{getRunningStyleLabel(style)}</SelectItem>
                    ))}
                  </SelectContent>
                </Select>

                <Select value={sortBy} onValueChange={(v) => setSortBy(v as SortOption)}>
                  <SelectTrigger className="glass-input" data-testid="select-sort">
                    <BarChart3 className="h-4 w-4 mr-2 text-white/40" />
                    <SelectValue placeholder="Sort" />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="ev">Expected Value</SelectItem>
                    <SelectItem value="edge">Edge %</SelectItem>
                    <SelectItem value="winPct">Win %</SelectItem>
                    <SelectItem value="time">Race Time</SelectItem>
                    <SelectItem value="odds">Market Odds</SelectItem>
                  </SelectContent>
                </Select>
              </div>

              {/* Filter Row 2: Edge Slider & Actions */}
              <div className="flex flex-col sm:flex-row gap-4 items-start sm:items-center">
                <div className="flex-1 space-y-2">
                  <div className="flex items-center justify-between">
                    <span className="text-sm text-white/40">Min Edge: {minEdge}%</span>
                    <Button
                      variant="ghost"
                      size="sm"
                      onClick={() => setMinEdge(0)}
                      className="h-6 px-2 text-xs"
                    >
                      Reset
                    </Button>
                  </div>
                  <Slider
                    value={[minEdge]}
                    onValueChange={([v]) => setMinEdge(v)}
                    min={0}
                    max={50}
                    step={5}
                    className="w-full"
                    data-testid="slider-min-edge"
                  />
                </div>

                <div className="flex items-center gap-2">
                  <Tabs value={groupBy} onValueChange={(v) => setGroupBy(v as GroupBy)}>
                    <TabsList className="bg-white/[0.06]">
                      <TabsTrigger value="none" className="text-xs">List</TabsTrigger>
                      <TabsTrigger value="track" className="text-xs">By Track</TabsTrigger>
                      <TabsTrigger value="date" className="text-xs">By Date</TabsTrigger>
                    </TabsList>
                  </Tabs>

                  <Button
                    variant="ghost"
                    size="sm"
                    onClick={() => setSortOrder(sortOrder === "desc" ? "asc" : "desc")}
                    className="gap-1"
                    data-testid="button-sort-order"
                  >
                    {sortOrder === "desc" ? "Desc" : "Asc"}
                    {sortOrder === "desc" ? <ChevronDown className="h-4 w-4" /> : <ChevronUp className="h-4 w-4" />}
                  </Button>

                  {activeFiltersCount > 0 && (
                    <Button
                      variant="outline"
                      size="sm"
                      onClick={clearAllFilters}
                      className="gap-1 text-destructive hover:text-destructive"
                      data-testid="button-clear-filters"
                    >
                      <X className="h-4 w-4" />
                      Clear All
                    </Button>
                  )}
                </div>
              </div>

              {/* Quick Filter Chips */}
              <div className="flex flex-wrap items-center gap-2">
                <span className="text-xs text-white/40 mr-1">Quick Filters:</span>

                {/* Today Only Toggle - Prominent */}
                <Button
                  variant={todayOnly ? "default" : "outline"}
                  size="sm"
                  onClick={() => setTodayOnly(!todayOnly)}
                  className={`text-xs gap-1 ${todayOnly ? 'bg-racing-orange text-black' : ''}`}
                  data-testid="button-today-only"
                >
                  <Calendar className="h-3 w-3" />
                  Today Only
                </Button>

                <Button
                  variant={confidenceFilter === "high" && valueRatingFilter === "Excellent" ? "default" : "outline"}
                  size="sm"
                  onClick={() => {
                    if (confidenceFilter === "high" && valueRatingFilter === "Excellent") {
                      setConfidenceFilter("all");
                      setValueRatingFilter("all");
                    } else {
                      setConfidenceFilter("high");
                      setValueRatingFilter("Excellent");
                    }
                  }}
                  className="text-xs gap-1"
                  data-testid="button-filter-best"
                >
                  <Flame className="h-3 w-3" />
                  Hot Picks
                </Button>

                <Button
                  variant={minEdge >= 10 ? "default" : "outline"}
                  size="sm"
                  onClick={() => setMinEdge(minEdge >= 10 ? 0 : 10)}
                  className="text-xs gap-1"
                  data-testid="button-filter-edge"
                >
                  <TrendingUp className="h-3 w-3" />
                  10%+ Edge
                </Button>

                <Button
                  variant={sortBy === "time" && sortOrder === "asc" ? "default" : "outline"}
                  size="sm"
                  onClick={() => {
                    setSortBy("time");
                    setSortOrder("asc");
                  }}
                  className="text-xs gap-1"
                  data-testid="button-filter-next"
                >
                  <Clock className="h-3 w-3" />
                  Next to Jump
                </Button>

                <Button
                  variant={runningStyleFilter === "leader" ? "default" : "outline"}
                  size="sm"
                  onClick={() => setRunningStyleFilter(runningStyleFilter === "leader" ? "all" : "leader")}
                  className="text-xs gap-1"
                  data-testid="button-filter-leaders"
                >
                  <Zap className="h-3 w-3" />
                  Leaders
                </Button>

                <Button
                  variant={sortBy === "ev" && sortOrder === "desc" ? "default" : "outline"}
                  size="sm"
                  onClick={() => {
                    setSortBy("ev");
                    setSortOrder("desc");
                  }}
                  className="text-xs gap-1"
                  data-testid="button-filter-value"
                >
                  <DollarSign className="h-3 w-3" />
                  Best Value
                </Button>
              </div>
            </CardContent>
          </Card>
        )}

        {/* Results */}
        {isLoading ? (
          <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
            {[1, 2, 3, 4, 5, 6].map((i) => (
              <Card key={i} className="glass-card border-0">
                <CardContent className="p-4 space-y-4">
                  <Skeleton className="h-4 w-32" />
                  <Skeleton className="h-6 w-40" />
                  <div className="grid grid-cols-2 gap-4">
                    <Skeleton className="h-12 w-full" />
                    <Skeleton className="h-12 w-full" />
                  </div>
                </CardContent>
              </Card>
            ))}
          </div>
        ) : filteredSelections.length === 0 ? (
          <Card className="glass-card border-0">
            <CardContent className="p-12 flex flex-col items-center justify-center text-center">
              <Search className="h-12 w-12 text-white/40 mb-4" />
              <h3 className="text-lg font-semibold mb-2">No Opportunities Found</h3>
              <p className="text-white/40 mb-4">
                {activeFiltersCount > 0
                  ? "Try adjusting your filters to see more results"
                  : "Check back later for new opportunities"}
              </p>
              {activeFiltersCount > 0 && (
                <Button variant="outline" onClick={clearAllFilters}>
                  Clear All Filters
                </Button>
              )}
            </CardContent>
          </Card>
        ) : (
          Object.entries(groupedSelections).map(([group, bets]) => (
            <div key={group} className="space-y-3">
              {/* Collapsible Track/Date Group Header */}
              {groupBy !== "none" && (
                <div
                  className="flex items-center justify-between px-3 py-2 bg-white/[0.03] rounded-lg cursor-pointer hover-elevate"
                  onClick={() => toggleGroupCollapsed(group)}
                  data-testid={`group-header-${group}`}
                >
                  <div className="flex items-center gap-2">
                    {groupBy === "track" ? (
                      <MapPin className="h-4 w-4 text-racing-orange" />
                    ) : (
                      <Calendar className="h-4 w-4 text-racing-orange" />
                    )}
                    <h2 className="font-semibold text-lg">{group}</h2>
                    <Badge variant="secondary" className="ml-2">{bets.length}</Badge>
                    {/* Show best bet indicator for track grouping */}
                    {groupBy === "track" && stats.bestPerTrack[group] && (
                      <Badge className="bg-yellow-500/20 text-yellow-500 border-yellow-500/50 border text-xs ml-2">
                        <Crown className="h-3 w-3 mr-1" />
                        Best: {stats.bestPerTrack[group].horseName}
                      </Badge>
                    )}
                  </div>
                  <ChevronRight className={`h-5 w-5 text-white/40 transition-transform ${collapsedGroups.has(group) ? '' : 'rotate-90'}`} />
                </div>
              )}

              {/* Bet Cards Grid - collapsible when grouped */}
              {!collapsedGroups.has(group) && (
              <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
                {bets.map((bet) => {
                  const isTopPick = stats.bestPick?.id === bet.id;
                  const isBestInTrack = groupBy === "track" && stats.bestPerTrack[bet.track]?.id === bet.id;
                  const cardStyle = getCardBorderStyle(bet, isTopPick);
                  const hasValueEdge = (bet.edge ?? 0) >= 1 && bet.marketOdds && bet.marketOdds > 1;
                  const hasNoOdds = !bet.marketOdds || bet.marketOdds <= 1;

                  return (
                  <Card
                    key={bet.id}
                    className={`glass-card hover-elevate cursor-pointer transition-all relative overflow-hidden ${cardStyle} ${isStartingSoon(bet.offTime) ? 'ring-1 ring-red-500/50' : ''} ${!hasValueEdge && !isTopPick ? 'opacity-75' : ''}`}
                    onClick={() => toggleCardExpanded(bet.id)}
                    data-testid={`card-bet-${bet.id}`}
                  >
                    {/* Left accent bar for value rating */}
                    {!isTopPick && (
                      <div
                        className={`absolute left-0 top-0 bottom-0 w-1 ${hasValueEdge ? getCardAccentColor(bet) : 'bg-white/10'}`}
                        data-testid={`accent-bar-${bet.id}`}
                      />
                    )}
                    <CardContent className="p-5 pl-6 space-y-4">
                      {/* Top Pick / Best in Track / Value Badge */}
                      {(isTopPick || isBestInTrack || hasValueEdge || hasNoOdds) && (
                        <div className="flex items-center gap-2 -mt-1 mb-2 flex-wrap">
                          {isTopPick && (
                            <Badge className="bg-gradient-to-r from-yellow-500 to-orange-500 text-black border-0 text-xs font-bold gap-1">
                              <Crown className="h-3 w-3" />
                              TOP PICK
                            </Badge>
                          )}
                          {isBestInTrack && !isTopPick && (
                            <Badge className="bg-yellow-500/20 text-yellow-500 border-yellow-500/50 border text-xs gap-1">
                              <Star className="h-3 w-3" />
                              BEST AT TRACK
                            </Badge>
                          )}
                          {hasValueEdge && !isTopPick && (
                            <Badge className="bg-green-500/20 text-green-400 border-green-500/50 border text-xs gap-1" data-testid={`badge-value-${bet.id}`}>
                              <TrendingUp className="h-3 w-3" />
                              VALUE +{(bet.edge ?? 0).toFixed(0)}%
                            </Badge>
                          )}
                          {hasNoOdds && (
                            <Badge variant="secondary" className="text-xs gap-1 opacity-60" data-testid={`badge-no-odds-${bet.id}`}>
                              NO ODDS
                            </Badge>
                          )}
                        </div>
                      )}

                      {/* Header with Countdown */}
                      <div className="flex items-start justify-between gap-2">
                        <div className="flex-1 min-w-0">
                          <div className="flex items-center gap-2 text-xs text-white/40 flex-wrap">
                            <span className="flex items-center gap-1">
                              <MapPin className="h-3 w-3" />
                              <span className="truncate">{bet.track}</span>
                            </span>
                            <span>R{bet.raceNumber}</span>
                            {/* Live Countdown Timer */}
                            <span className={`flex items-center gap-1 font-medium ${isStartingSoon(bet.offTime) ? 'text-red-500' : 'text-blue-400'}`}>
                              <Timer className="h-3 w-3" />
                              {formatCountdown(bet.offTime)}
                            </span>
                          </div>
                          <h3 className="font-bold text-lg mt-1 truncate">{bet.horseName}</h3>
                          <p className="text-xs text-white/40 truncate">
                            {bet.jockey} / {bet.trainer}
                          </p>
                        </div>
                        <div className="flex flex-col items-end gap-1">
                          <Badge className={`${getConfidenceColor(bet.confidence)} text-xs`}>
                            {bet.confidence}
                          </Badge>
                          <ChevronDown className={`h-4 w-4 text-white/40 transition-transform ${expandedCards.has(bet.id) ? "rotate-180" : ""}`} />
                        </div>
                      </div>

                      {/* Win Probability Gauge + Edge + Odds */}
                      <div className="grid grid-cols-3 gap-3 text-center">
                        {/* Win % with visual gauge */}
                        <div className="bg-white/[0.03] rounded-lg p-3 relative overflow-hidden">
                          <div
                            className="absolute bottom-0 left-0 right-0 bg-green-500/20"
                            style={{ height: `${Math.min((bet.winPercentage ?? 0), 100)}%` }}
                          />
                          <div className="relative">
                            <p className="text-xs text-white/40 mb-1">Win %</p>
                            <p className="font-bold text-base text-green-400">{(bet.winPercentage ?? 0).toFixed(1)}%</p>
                          </div>
                        </div>
                        {/* Prominent Edge Indicator */}
                        <div className={`rounded-lg p-3 ${(bet.edge ?? 0) >= 10 ? 'bg-green-500/20' : 'bg-white/[0.03]'}`}>
                          <p className="text-xs text-white/40 mb-1">Edge</p>
                          <p className={`font-bold text-base ${getEVColor(bet.edge ?? 0)}`}>
                            {(bet.edge ?? 0) >= 10 && <Flame className="h-3 w-3 inline mr-1" />}
                            {(bet.edge ?? 0).toFixed(1)}%
                          </p>
                        </div>
                        <div className="bg-white/[0.03] rounded-lg p-3">
                          <p className="text-xs text-white/40 mb-1">Odds</p>
                          <p className={`font-bold text-base ${!bet.marketOdds ? 'text-white/30' : ''}`}>{bet.marketOdds ? `$${bet.marketOdds.toFixed(2)}` : 'TBC'}</p>
                        </div>
                      </div>

                      {/* EV Bar with better styling */}
                      <div className="space-y-1">
                        <div className="flex justify-between text-xs">
                          <span className="text-white/40">Expected Value</span>
                          <span className={`font-semibold ${getEVColor(bet.expectedValue ?? 0)}`}>+{(bet.expectedValue ?? 0).toFixed(1)}%</span>
                        </div>
                        <Progress value={Math.min(bet.expectedValue ?? 0, 100)} className="h-2" />
                      </div>

                      {/* Quick Add to Slip Button (always visible) */}
                      <Button
                        size="sm"
                        className={`w-full gap-2 ${isInSlip(bet.horseName, bet.track, bet.raceNumber) ? "bg-green-600" : "bg-racing-orange text-black"}`}
                        disabled={slipLoading || isInSlip(bet.horseName, bet.track, bet.raceNumber)}
                        onClick={(e) => {
                          e.stopPropagation();
                          addSelection({
                            horseName: bet.horseName,
                            track: bet.track,
                            raceNumber: bet.raceNumber,
                            raceDate: bet.raceDate,
                            raceTime: bet.offTime,
                            odds: bet.marketOdds ?? 0,
                            predictedWinProb: (bet.winPercentage ?? 0) / 100,
                            confidenceScore: bet.winPercentage ?? 0,
                            betType: "win",
                            reasons: [`${(bet.edge ?? 0).toFixed(1)}% edge`, `${(bet.winPercentage ?? 0).toFixed(0)}% win probability`]
                          }, e);
                        }}
                        data-testid={`button-quick-add-${bet.id}`}
                      >
                        {isInSlip(bet.horseName, bet.track, bet.raceNumber) ? (
                          <>
                            <Check className="h-4 w-4" />
                            In Slip
                          </>
                        ) : (
                          <>
                            <Plus className="h-4 w-4" />
                            Add to Slip
                          </>
                        )}
                      </Button>

                      {/* Expanded Details */}
                      {expandedCards.has(bet.id) && (
                        <div className="pt-3 border-t border-white/[0.06] space-y-3 animate-in slide-in-from-top-2">
                          <div className="grid grid-cols-2 gap-3 text-sm">
                            <div>
                              <p className="text-xs text-white/40">Place %</p>
                              <p className="font-medium">{(bet.placePercentage ?? 0).toFixed(1)}%</p>
                            </div>
                            <div>
                              <p className="text-xs text-white/40">Stability</p>
                              <p className={`font-medium ${getStabilityColor(bet.stabilityScore ?? 0)}`}>{(bet.stabilityScore ?? 0).toFixed(1)}</p>
                            </div>
                            <div>
                              <p className="text-xs text-white/40">Running Style</p>
                              <p className="font-medium">{getRunningStyleLabel(bet.runningStyle || "unknown")}</p>
                            </div>
                            <div>
                              <p className="text-xs text-white/40">Exp. Position</p>
                              <p className="font-medium">{(bet.expectedPosition ?? 0).toFixed(2)}</p>
                            </div>
                            <div>
                              <p className="text-xs text-white/40">CI Range</p>
                              <p className="font-medium">{(bet.ciLower ?? 0).toFixed(1)}-{(bet.ciUpper ?? 0).toFixed(1)}%</p>
                            </div>
                            <div>
                              <p className="text-xs text-white/40">Value Rating</p>
                              <p className={`font-medium ${getValueRatingColor(bet.valueRating || "low")}`}>{bet.valueRating || "N/A"}</p>
                            </div>
                          </div>

                          {/* Franking & Fitness Indicators */}
                          {(bet.frankingScore != null || bet.fitnessRunLabel) && (
                            <div className="space-y-1">
                              {bet.frankingScore != null && bet.frankingScore > 0 && (
                                <div className="flex items-center gap-1.5 mt-1" data-testid={`franking-indicator-${bet.id}`}>
                                  <div className={`w-2 h-2 rounded-full ${
                                    bet.frankingScore >= 60 ? 'bg-green-500' :
                                    bet.frankingScore >= 50 ? 'bg-yellow-500' :
                                    'bg-red-500'
                                  }`} />
                                  <span className="text-xs text-white/60">
                                    {bet.frankingScore >= 60 ? `Franked ${bet.frankingScore.toFixed(0)}` :
                                      bet.frankingScore >= 50 ? `Form OK ${bet.frankingScore.toFixed(0)}` :
                                      `Weak Form ${bet.frankingScore.toFixed(0)}`}
                                  </span>
                                  {bet.frankingElo != null && bet.frankingElo >= 75 && (
                                    <span className="text-xs text-orange-400/80">ELO {bet.frankingElo.toFixed(0)}</span>
                                  )}
                                </div>
                              )}

                              {bet.fitnessRunLabel && (
                                <div className="flex items-center gap-1.5 mt-1" data-testid={`fitness-indicator-${bet.id}`}>
                                  <Activity className="h-3 w-3 text-white/40" />
                                  <span className="text-xs text-white/60">{bet.fitnessRunLabel}</span>
                                  {bet.fitnessIsAtPeakRun && (
                                    <Badge className="bg-green-500/20 text-green-400 border-green-500/50 text-[10px] px-1 py-0">PEAK</Badge>
                                  )}
                                  {bet.fitnessPrepTrajectory && (
                                    <span className={`text-xs ${
                                      bet.fitnessPrepTrajectory.toLowerCase() === 'improving' || bet.fitnessPrepTrajectory.toLowerCase() === 'peaking' ? 'text-green-400' :
                                      bet.fitnessPrepTrajectory.toLowerCase() === 'stable' ? 'text-yellow-400' :
                                      bet.fitnessPrepTrajectory.toLowerCase() === 'declining' ? 'text-red-400' :
                                      'text-white/40'
                                    }`}>
                                      {bet.fitnessPrepTrajectory.toLowerCase() === 'improving' || bet.fitnessPrepTrajectory.toLowerCase() === 'peaking' ? (
                                        <span className="flex items-center gap-0.5"><TrendingUp className="h-3 w-3" />{bet.fitnessPrepTrajectory}</span>
                                      ) : bet.fitnessPrepTrajectory.toLowerCase() === 'declining' ? (
                                        <span className="flex items-center gap-0.5"><ChevronDown className="h-3 w-3" />{bet.fitnessPrepTrajectory}</span>
                                      ) : (
                                        <span>{bet.fitnessPrepTrajectory}</span>
                                      )}
                                    </span>
                                  )}
                                </div>
                              )}
                            </div>
                          )}

                          {/* Pace Analysis */}
                          {bet.paceSplits && Object.keys(bet.paceSplits).length > 0 && (
                            <div>
                              <p className="text-xs text-white/40 mb-2">Pace Scenario Analysis</p>
                              <div className="grid grid-cols-4 gap-1">
                                {Object.entries(bet.paceSplits).map(([pace, prob]) => (
                                  <div key={pace} className="bg-white/[0.03] rounded p-1 text-center">
                                    <p className="text-xs text-white/40 capitalize">{pace}</p>
                                    <p className="text-xs font-medium">{(prob as number).toFixed(1)}%</p>
                                  </div>
                                ))}
                              </div>
                            </div>
                          )}

                          {/* Enhanced Analysis - Barrier, Class, H2H */}
                          {(bet.barrierBiasDesc || bet.classMovementDesc || bet.headToHeadDesc || bet.enhancedExplanations) && (
                            <div className="space-y-2">
                              <p className="text-xs text-white/40 mb-1 flex items-center gap-1">
                                <Sparkles className="h-3 w-3 text-racing-gold" />
                                Enhanced Analysis
                              </p>
                              <div className="grid grid-cols-1 gap-2">
                                {bet.barrierBiasDesc && bet.barrierBiasDesc !== 'Mid barrier (neutral) (using default model)' && (
                                  <div className={`text-xs px-2 py-1 rounded ${
                                    (bet.barrierBiasFactor || 1) > 1.1 ? 'bg-green-500/20 text-green-400' :
                                    (bet.barrierBiasFactor || 1) < 0.9 ? 'bg-red-500/20 text-red-400' :
                                    'bg-white/[0.03] text-white/40'
                                  }`} data-testid="text-barrier-bias">
                                    <span className="font-medium">Barrier:</span> {bet.barrierBiasDesc}
                                  </div>
                                )}
                                {bet.classMovementDesc && bet.classMovementDesc !== 'No prior class history' && (
                                  <div className={`text-xs px-2 py-1 rounded ${
                                    (bet.classMovementFactor || 1) > 1.05 ? 'bg-green-500/20 text-green-400' :
                                    (bet.classMovementFactor || 1) < 0.95 ? 'bg-red-500/20 text-red-400' :
                                    'bg-white/[0.03] text-white/40'
                                  }`} data-testid="text-class-movement">
                                    <span className="font-medium">Class:</span> {bet.classMovementDesc}
                                  </div>
                                )}
                                {bet.headToHeadDesc && bet.headToHeadDesc !== 'No head-to-head history with rivals' && (
                                  <div className={`text-xs px-2 py-1 rounded ${
                                    (bet.headToHeadFactor || 1) > 1.05 ? 'bg-green-500/20 text-green-400' :
                                    (bet.headToHeadFactor || 1) < 0.95 ? 'bg-red-500/20 text-red-400' :
                                    'bg-white/[0.03] text-white/40'
                                  }`} data-testid="text-h2h">
                                    <span className="font-medium">H2H:</span> {bet.headToHeadDesc}
                                  </div>
                                )}
                              </div>
                            </div>
                          )}

                          {/* Track Bias Points - show for all values including 0 (neutral) */}
                          {bet.trackBiasPoints !== undefined && (
                            <div className="space-y-2">
                              <p className="text-xs text-white/40 mb-1 flex items-center gap-1">
                                <Target className="h-3 w-3 text-racing-orange" />
                                Track Bias Points
                              </p>
                              <div className={`text-sm font-medium px-3 py-2 rounded-lg ${
                                bet.trackBiasFit === 'excellent' ? 'bg-green-500/20 text-green-400 border border-green-500/30' :
                                bet.trackBiasFit === 'good' ? 'bg-emerald-500/15 text-emerald-400 border border-emerald-500/25' :
                                bet.trackBiasFit === 'poor' ? 'bg-red-500/20 text-red-400 border border-red-500/30' :
                                'bg-white/[0.03] text-white/40 border border-white/[0.06]'
                              }`} data-testid="text-track-bias-total">
                                <div className="flex items-center justify-between">
                                  <span className="uppercase tracking-wide text-xs">
                                    {bet.trackBiasFit?.toUpperCase()} FIT
                                  </span>
                                  <span className={`text-lg font-bold ${
                                    bet.trackBiasPoints > 0 ? 'text-green-400' :
                                    bet.trackBiasPoints < 0 ? 'text-red-400' :
                                    'text-white/40'
                                  }`}>
                                    {bet.trackBiasPoints > 0 ? '+' : ''}{bet.trackBiasPoints} pts
                                  </span>
                                </div>
                              </div>
                              <div className="grid grid-cols-2 gap-1">
                                {bet.trackBiasBarrierPts !== undefined && bet.trackBiasBarrierPts !== 0 && (
                                  <div className={`text-xs px-2 py-1 rounded ${
                                    bet.trackBiasBarrierPts > 0 ? 'bg-green-500/10 text-green-400' : 'bg-red-500/10 text-red-400'
                                  }`} data-testid="text-track-barrier-pts">
                                    Barrier: {bet.trackBiasBarrierPts >= 0 ? '+' : ''}{bet.trackBiasBarrierPts}
                                  </div>
                                )}
                                {bet.trackBiasPacePts !== undefined && bet.trackBiasPacePts !== 0 && (
                                  <div className={`text-xs px-2 py-1 rounded ${
                                    bet.trackBiasPacePts > 0 ? 'bg-green-500/10 text-green-400' : 'bg-red-500/10 text-red-400'
                                  }`} data-testid="text-track-pace-pts">
                                    Pace: {bet.trackBiasPacePts >= 0 ? '+' : ''}{bet.trackBiasPacePts}
                                  </div>
                                )}
                                {bet.trackBiasJockeyPts !== undefined && bet.trackBiasJockeyPts !== 0 && (
                                  <div className={`text-xs px-2 py-1 rounded ${
                                    bet.trackBiasJockeyPts > 0 ? 'bg-green-500/10 text-green-400' : 'bg-red-500/10 text-red-400'
                                  }`} data-testid="text-track-jockey-pts">
                                    Jockey: {bet.trackBiasJockeyPts >= 0 ? '+' : ''}{bet.trackBiasJockeyPts}
                                  </div>
                                )}
                                {bet.trackBiasTrainerPts !== undefined && bet.trackBiasTrainerPts !== 0 && (
                                  <div className={`text-xs px-2 py-1 rounded ${
                                    bet.trackBiasTrainerPts > 0 ? 'bg-green-500/10 text-green-400' : 'bg-red-500/10 text-red-400'
                                  }`} data-testid="text-track-trainer-pts">
                                    Trainer: {bet.trackBiasTrainerPts >= 0 ? '+' : ''}{bet.trackBiasTrainerPts}
                                  </div>
                                )}
                              </div>
                              {bet.trackBiasSummary && (
                                <p className="text-xs text-white/40" data-testid="text-track-bias-summary">
                                  {bet.trackBiasSummary}
                                </p>
                              )}
                            </div>
                          )}

                          {/* AI Form Analysis */}
                          {bet.aiInsight && (
                            <AiInsightDisplay
                              insight={bet.aiInsight}
                              aiScore={bet.aiScore}
                              llmProvider={bet.llmProvider}
                            />
                          )}

                          {/* Luckless Last Start Excuse */}
                          {bet.lucklessFlag && bet.lucklessExplanation && (
                            <div className="mt-2 p-3 bg-amber-500/10 border border-amber-500/30 rounded-lg">
                              <div className="flex items-center gap-2 mb-1">
                                <span className="text-amber-400 font-semibold text-sm">Last Start Excuse</span>
                              </div>
                              <p className="text-sm text-amber-200/80">{bet.lucklessExplanation}</p>
                            </div>
                          )}

                          {/* Form & Details */}
                          <div className="text-xs text-white/40">
                            <p>Form: <span className="font-mono">{bet.form || "N/A"}</span></p>
                            <p>Barrier: {bet.barrier} | Distance: {bet.distance}</p>
                          </div>

                          {/* Ask Stride Button */}
                          <Button
                            size="sm"
                            variant="outline"
                            className="w-full gap-2 border-racing-gold/50 text-racing-gold hover:bg-racing-gold/10"
                            onClick={(e) => {
                              e.stopPropagation();
                              askStride(bet);
                            }}
                            disabled={strideLoading.has(bet.id)}
                            data-testid={`button-ask-stride-${bet.id}`}
                          >
                            {strideLoading.has(bet.id) ? (
                              <>
                                <Loader2 className="h-4 w-4 animate-spin" />
                                Asking Stride...
                              </>
                            ) : strideExplanations[bet.id] ? (
                              <>
                                <Sparkles className="h-4 w-4" />
                                Stride Says
                              </>
                            ) : (
                              <>
                                <MessageSquare className="h-4 w-4" />
                                Ask Stride
                              </>
                            )}
                          </Button>

                          {/* Stride Explanation */}
                          {strideExplanations[bet.id] && (
                            <div className="bg-racing-gold/10 border border-racing-gold/30 rounded-md p-3">
                              <div className="flex items-start gap-2">
                                <Sparkles className="h-4 w-4 text-racing-gold mt-0.5 flex-shrink-0" />
                                <p className="text-sm text-foreground leading-relaxed">
                                  {strideExplanations[bet.id]}
                                </p>
                              </div>
                            </div>
                          )}

                          {/* Add to Slip Button */}
                          <Button
                            size="sm"
                            className={`w-full gap-2 ${isInSlip(bet.horseName, bet.track, bet.raceNumber) ? "bg-green-600" : "bg-racing-orange text-black"}`}
                            disabled={slipLoading || isInSlip(bet.horseName, bet.track, bet.raceNumber)}
                            onClick={(e) => {
                              e.stopPropagation();
                              addSelection({
                                horseName: bet.horseName,
                                track: bet.track,
                                raceNumber: bet.raceNumber,
                                raceDate: bet.raceDate,
                                raceTime: bet.offTime,
                                odds: bet.marketOdds ?? 0,
                                predictedWinProb: (bet.winPercentage ?? 0) / 100,
                                confidenceScore: bet.winPercentage ?? 0,
                                betType: "win",
                                reasons: [`${(bet.edge ?? 0).toFixed(1)}% edge`, `${(bet.winPercentage ?? 0).toFixed(0)}% win probability`]
                              }, e);
                            }}
                            data-testid={`button-add-slip-${bet.id}`}
                          >
                            {isInSlip(bet.horseName, bet.track, bet.raceNumber) ? (
                              <>
                                <Check className="h-4 w-4" />
                                In Slip
                              </>
                            ) : (
                              <>
                                <Plus className="h-4 w-4" />
                                Add to Slip
                              </>
                            )}
                          </Button>
                        </div>
                      )}
                    </CardContent>
                  </Card>
                  );
                })}
              </div>
              )}
            </div>
          ))
        )}

        {/* Footer */}
        {!isLoading && filteredSelections.length > 0 && (
          <div className="text-center text-sm text-white/40 pt-4">
            Showing {filteredSelections.length} of {selections.length} selections
            {isRefetching && " • Updating..."}
          </div>
        )}
          </>
        )}
      </main>

      {/* Stride Analyst Chat Panel - Tucked Away */}
      <div className={`fixed bottom-0 right-4 z-50 transition-all duration-300 ${strideOpen ? 'w-96' : 'w-auto'}`}>
        {!strideOpen ? (
          <Button
            onClick={() => setStrideOpen(true)}
            className="mb-4 bg-racing-orange hover:bg-racing-orange/90 text-white shadow-lg"
            data-testid="button-open-stride"
          >
            <Sparkles className="h-4 w-4 mr-2" />
            Ask Stride
          </Button>
        ) : (
          <Card className="border-racing-orange/30 shadow-2xl bg-black/95 backdrop-blur">
            <CardHeader className="flex flex-row items-center justify-between gap-2 pb-2 border-b border-racing-orange/20">
              <div className="flex items-center gap-2">
                <Sparkles className="h-5 w-5 text-racing-orange" />
                <span className="font-semibold text-sm uppercase tracking-wider">Stride Analyst</span>
              </div>
              <div className="flex items-center gap-1">
                <Button
                  size="sm"
                  variant="ghost"
                  onClick={() => setStrideMessages([])}
                  data-testid="button-clear-stride"
                >
                  <RefreshCw className="h-3.5 w-3.5" />
                </Button>
                <Button
                  size="sm"
                  variant="ghost"
                  onClick={() => setStrideOpen(false)}
                  data-testid="button-close-stride"
                >
                  <X className="h-4 w-4" />
                </Button>
              </div>
            </CardHeader>
            <CardContent className="p-0">
              <div className="h-64 overflow-y-auto p-3 space-y-3">
                {strideMessages.length === 0 ? (
                  <div className="text-center text-white/40 text-sm py-8">
                    <Sparkles className="h-8 w-8 mx-auto mb-2 text-racing-orange/50" />
                    <p className="font-medium">Ask Stride anything about today's selections</p>
                    <p className="text-xs mt-1">Try: "What's the best bet and why?"</p>
                    <div className="flex flex-wrap gap-1 justify-center mt-3">
                      {["Best bet today?", "Any track bias edges?", "Top value pick?"].map(q => (
                        <Button
                          key={q}
                          size="sm"
                          variant="outline"
                          className="text-xs h-6 px-2"
                          onClick={() => {
                            setStrideQuestion(q);
                          }}
                          data-testid={`button-stride-suggestion-${q.replace(/\s+/g, '-').toLowerCase()}`}
                        >
                          {q}
                        </Button>
                      ))}
                    </div>
                  </div>
                ) : (
                  strideMessages.map((msg, idx) => (
                    <div
                      key={idx}
                      className={`text-sm ${msg.role === 'user' ? 'text-right' : 'text-left'}`}
                    >
                      <div className={`inline-block max-w-[90%] p-2.5 rounded-lg ${
                        msg.role === 'user'
                          ? 'bg-racing-orange text-white'
                          : 'bg-white/[0.03] border border-white/[0.06] text-foreground'
                      }`}>
                        {msg.role === 'analyst' && (
                          <div className="flex items-center gap-1 mb-1 text-xs text-white/40">
                            <Sparkles className="h-3 w-3 text-racing-orange" />
                            <span className="font-medium uppercase tracking-wide">Stride</span>
                          </div>
                        )}
                        <div className="whitespace-pre-wrap">{msg.content}</div>
                      </div>
                    </div>
                  ))
                )}
                {strideAnalyzing && (
                  <div className="text-left">
                    <div className="inline-block bg-white/[0.03] border border-white/[0.06] p-2.5 rounded-lg">
                      <div className="flex items-center gap-2 text-sm text-white/40">
                        <Loader2 className="h-4 w-4 animate-spin text-racing-orange" />
                        <span>Analyzing {filteredSelections.length} selections...</span>
                      </div>
                    </div>
                  </div>
                )}
              </div>
              <div className="p-2 border-t border-white/[0.06]">
                <form
                  onSubmit={(e) => {
                    e.preventDefault();
                    askStrideAnalyst();
                  }}
                  className="flex gap-2"
                >
                  <Input
                    value={strideQuestion}
                    onChange={(e) => setStrideQuestion(e.target.value)}
                    placeholder="Ask about today's selections..."
                    className="flex-1 text-sm"
                    disabled={strideAnalyzing || filteredSelections.length === 0}
                    data-testid="input-stride-question"
                  />
                  <Button
                    type="submit"
                    size="icon"
                    disabled={!strideQuestion.trim() || strideAnalyzing || filteredSelections.length === 0}
                    className="bg-racing-orange hover:bg-racing-orange/90"
                    data-testid="button-send-stride"
                  >
                    <ChevronRight className="h-4 w-4" />
                  </Button>
                </form>
              </div>
            </CardContent>
          </Card>
        )}
      </div>
    </div>
  );
}
