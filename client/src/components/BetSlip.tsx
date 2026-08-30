import { useState, useEffect } from "react";
import { createPortal } from "react-dom";
import { motion, AnimatePresence } from "framer-motion";
import { useBetSlip } from "@/lib/betSlipContext";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Progress } from "@/components/ui/progress";
import { 
  X, 
  Trash2, 
  DollarSign, 
  Calculator, 
  ChevronDown, 
  ChevronUp,
  TrendingUp,
  AlertTriangle,
  Zap,
  Target,
  Trophy
} from "lucide-react";
import type { StakingMethod } from "@shared/schema";

export function BetSlip() {
  const {
    selections,
    selectionCount,
    bankroll,
    stakingMethod,
    isOpen,
    isLoading,
    flyingItem,
    summary,
    removeSelection,
    clearSlip,
    setBankroll,
    setStakingMethod,
    calculateStakes,
    toggleSlip,
    closeSlip
  } = useBetSlip();

  const [bankrollInput, setBankrollInput] = useState(bankroll.toString());
  const [showMultis, setShowMultis] = useState(false);

  useEffect(() => {
    setBankrollInput(bankroll.toString());
  }, [bankroll]);

  const handleBankrollChange = () => {
    const amount = parseFloat(bankrollInput);
    if (amount > 0) {
      setBankroll(amount);
    }
  };

  const handleCalculate = () => {
    const amount = parseFloat(bankrollInput);
    if (amount > 0 && amount !== bankroll) {
      setBankroll(amount);
    }
    calculateStakes();
  };

  const getRiskColor = (risk: string) => {
    switch (risk) {
      case "low": return "text-green-400";
      case "medium": return "text-yellow-400";
      case "high": return "text-red-400";
      default: return "text-muted-foreground";
    }
  };

  const getRiskBg = (risk: string) => {
    switch (risk) {
      case "low": return "bg-green-500/20 border-green-500/50";
      case "medium": return "bg-yellow-500/20 border-yellow-500/50";
      case "high": return "bg-red-500/20 border-red-500/50";
      default: return "bg-muted/20 border-muted";
    }
  };

  const stakingMethods: { value: StakingMethod; label: string; description: string }[] = [
    { value: "fractional_kelly", label: "Quarter Kelly", description: "Conservative (25% Kelly)" },
    { value: "kelly", label: "Full Kelly", description: "Aggressive" },
    { value: "proportional", label: "Proportional", description: "Based on confidence" },
    { value: "level_stakes", label: "Level Stakes", description: "Equal 2% stakes" }
  ];

  return (
    <>
      {/* Flying Animation Element */}
      <AnimatePresence>
        {flyingItem && (
          <motion.div
            key={flyingItem.id}
            initial={{ 
              x: flyingItem.startX - 60, 
              y: flyingItem.startY - 20, 
              scale: 1, 
              opacity: 1 
            }}
            animate={{ 
              x: window.innerWidth - 100, 
              y: 24, 
              scale: 0.3, 
              opacity: 0 
            }}
            transition={{ duration: 0.5, ease: "easeInOut" }}
            className="fixed z-[100] pointer-events-none"
          >
            <div className="bg-racing-gold text-black px-4 py-2 rounded-lg shadow-xl font-bold whitespace-nowrap">
              {flyingItem.horseName}
            </div>
          </motion.div>
        )}
      </AnimatePresence>

      {/* Bet Slip Button - Inline positioned */}
      <motion.button
        onClick={toggleSlip}
        className="group inline-flex h-11 items-center gap-2 rounded-[18px] border border-racing-gold/35 bg-[linear-gradient(135deg,rgba(245,158,11,0.20),rgba(249,115,22,0.30))] px-4 text-racing-gold shadow-[0_14px_30px_rgba(249,115,22,0.18)] backdrop-blur-md"
        whileHover={{ scale: 1.02 }}
        whileTap={{ scale: 0.98 }}
        data-testid="button-toggle-betslip"
      >
        <span className="text-xs font-semibold uppercase tracking-[0.14em] text-racing-gold">Bet Slip</span>
        <span className="inline-flex min-w-[26px] items-center justify-center rounded-full bg-black/75 px-2 py-0.5 text-xs font-black text-white shadow-[inset_0_1px_0_rgba(255,255,255,0.08)]">
          {selectionCount}
        </span>
      </motion.button>

      {/* Slide-out Panel - Rendered via Portal to escape parent constraints */}
      {createPortal(
        <AnimatePresence>
          {isOpen && (
            <>
              {/* Backdrop */}
              <motion.div
                initial={{ opacity: 0 }}
                animate={{ opacity: 1 }}
                exit={{ opacity: 0 }}
                className="fixed inset-0 bg-black/50 z-[9998]"
                onClick={closeSlip}
              />

              {/* Panel */}
              <motion.div
                initial={{ x: "100%" }}
                animate={{ x: 0 }}
                exit={{ x: "100%" }}
                transition={{ type: "spring", damping: 25, stiffness: 200 }}
                className="fixed top-0 right-0 bottom-0 w-full sm:w-[480px] md:w-[520px] lg:w-[560px] max-w-[50vw] bg-background z-[9999] overflow-hidden flex flex-col shadow-2xl border-l border-border"
                data-testid="panel-betslip"
              >
              {/* Header */}
              <div className="p-4 border-b border-border bg-racing-gold flex items-center justify-between">
                <div className="flex items-center gap-2">
                  <h2 className="font-bold text-xl text-black">BET SLIP</h2>
                  {selectionCount > 0 && (
                    <Badge className="bg-black/20 text-black border-black/30 text-sm px-2">
                      {selectionCount}
                    </Badge>
                  )}
                </div>
                <Button size="icon" variant="ghost" onClick={closeSlip} className="text-black hover:bg-black/10" data-testid="button-close-betslip">
                  <X className="h-5 w-5" />
                </Button>
              </div>

              {/* Content - Sidebar layout */}
              <div className="flex-1 overflow-y-auto p-4 md:p-6">
                {selectionCount === 0 ? (
                  <div className="text-center py-12 text-muted-foreground">
                    <Trophy className="h-12 w-12 mx-auto mb-4 opacity-30" />
                    <p className="text-lg font-medium mb-2">Your bet slip is empty</p>
                    <p className="text-sm">Add selections from The Track Board or Race Day</p>
                  </div>
                ) : (
                  <div className="space-y-6">
                    {/* Selections List */}
                    <div>
                      <h3 className="text-lg font-bold mb-3 text-white">Your Selections</h3>
                      <div className="space-y-3">
                        {selections.map((sel) => {
                          const recommendation = summary?.recommendations.find(r => r.selectionId === sel.id);
                          
                          return (
                            <Card key={sel.id} className="bg-card border-border" data-testid={`card-slip-selection-${sel.id}`}>
                              <CardContent className="p-3">
                                <div className="flex items-start justify-between gap-2 mb-3">
                                  <div className="flex-1 min-w-0">
                                    <p className="font-bold text-white text-base">{sel.horseName}</p>
                                    <p className="text-sm text-muted-foreground">
                                      {sel.track} • R{sel.raceNumber}
                                    </p>
                                  </div>
                                  <Button 
                                    size="icon" 
                                    variant="ghost" 
                                    className="h-8 w-8 text-muted-foreground hover:text-red-400"
                                    onClick={() => removeSelection(sel.id)}
                                    data-testid={`button-remove-${sel.id}`}
                                  >
                                    <X className="h-4 w-4" />
                                  </Button>
                                </div>
                                
                                <div className="flex items-center gap-3">
                                  <div className="text-center">
                                    <p className={`text-lg font-bold ${sel.odds ? 'text-green-400' : 'text-white/30'}`}>{sel.odds ? `$${sel.odds.toFixed(2)}` : 'TBC'}</p>
                                    <p className="text-xs text-muted-foreground">Odds</p>
                                  </div>
                                  <div className="text-center">
                                    <p className="text-lg font-bold text-white">{(sel.predictedWinProb * 100).toFixed(0)}%</p>
                                    <p className="text-xs text-muted-foreground">Win Prob</p>
                                  </div>
                                </div>

                              {/* Stake Recommendation */}
                              {recommendation && recommendation.recommendedStake > 0 && (
                                <div className="mt-3 pt-3 border-t border-border/50 space-y-2">
                                  <div className="flex items-center justify-between">
                                    <span className="text-xs text-muted-foreground">Stake</span>
                                    <span className="font-bold text-base text-racing-orange">
                                      ${recommendation.recommendedStake.toFixed(2)}
                                    </span>
                                  </div>
                                  <div className="flex items-center justify-between">
                                    <span className="text-xs text-muted-foreground">Return</span>
                                    <span className="text-green-400 font-semibold text-sm">
                                      ${recommendation.potentialReturn.toFixed(2)}
                                    </span>
                                  </div>
                                  <div className="flex items-center justify-between">
                                    <span className="text-xs text-muted-foreground">EV</span>
                                    <span className={`font-semibold text-sm ${recommendation.expectedValue >= 0 ? "text-green-400" : "text-red-400"}`}>
                                      {recommendation.expectedValue >= 0 ? "+" : ""}${recommendation.expectedValue.toFixed(2)}
                                    </span>
                                  </div>
                                  <Badge className={`${getRiskBg(recommendation.riskLevel)} text-xs`}>
                                    {recommendation.riskLevel.toUpperCase()} RISK
                                  </Badge>
                                </div>
                              )}
                              </CardContent>
                            </Card>
                          );
                        })}
                      </div>
                    </div>

                    {/* Clear All */}
                    <Button 
                      variant="ghost" 
                      size="sm" 
                      className="w-full text-muted-foreground gap-2"
                      onClick={clearSlip}
                      data-testid="button-clear-slip"
                    >
                      <Trash2 className="h-4 w-4" />
                      Clear All
                    </Button>

                    {/* Bankroll & Settings */}
                    <Card className="bg-card border-border">
                      <CardContent className="p-4 space-y-4">
                        <div>
                          <Label className="text-xs text-muted-foreground">Your Bankroll</Label>
                          <div className="flex gap-2 mt-1">
                            <div className="relative flex-1">
                              <DollarSign className="absolute left-3 top-1/2 -translate-y-1/2 h-4 w-4 text-muted-foreground" />
                              <Input
                                type="number"
                                value={bankrollInput}
                                onChange={(e) => setBankrollInput(e.target.value)}
                                onBlur={handleBankrollChange}
                                className="pl-8"
                                placeholder="1000"
                                data-testid="input-bankroll"
                              />
                            </div>
                          </div>
                        </div>

                        <div>
                          <Label className="text-xs text-muted-foreground">Staking Method</Label>
                          <Select value={stakingMethod} onValueChange={(v) => setStakingMethod(v as StakingMethod)}>
                            <SelectTrigger className="mt-1" data-testid="select-staking-method">
                              <SelectValue />
                            </SelectTrigger>
                            <SelectContent>
                              {stakingMethods.map((method) => (
                                <SelectItem key={method.value} value={method.value}>
                                  <div className="flex flex-col items-start">
                                    <span>{method.label}</span>
                                    <span className="text-xs text-muted-foreground">{method.description}</span>
                                  </div>
                                </SelectItem>
                              ))}
                            </SelectContent>
                          </Select>
                        </div>

                        <Button 
                          className="w-full gap-2 bg-racing-orange text-black"
                          onClick={handleCalculate}
                          disabled={isLoading}
                          data-testid="button-calculate-stakes"
                        >
                          <Calculator className="h-4 w-4" />
                          {isLoading ? "Calculating..." : "Calculate Optimal Stakes"}
                        </Button>
                      </CardContent>
                    </Card>

                    {/* Summary */}
                    {summary && summary.totalStake > 0 && (
                      <Card className={`bg-card border ${getRiskBg(summary.riskAssessment)}`}>
                        <CardHeader className="pb-2">
                          <CardTitle className="text-sm flex items-center gap-2">
                            <Target className="h-4 w-4 text-racing-orange" />
                            STAKE SUMMARY
                          </CardTitle>
                        </CardHeader>
                        <CardContent className="space-y-3">
                          <div className="grid grid-cols-2 gap-3 text-sm">
                            <div>
                              <p className="text-muted-foreground text-xs">Total Stake</p>
                              <p className="font-bold text-white">${summary.totalStake.toFixed(2)}</p>
                            </div>
                            <div>
                              <p className="text-muted-foreground text-xs">Potential Return</p>
                              <p className="font-bold text-green-400">${summary.totalPotentialReturn.toFixed(2)}</p>
                            </div>
                            <div>
                              <p className="text-muted-foreground text-xs">Expected Value</p>
                              <p className={`font-bold ${summary.expectedValue >= 0 ? "text-green-400" : "text-red-400"}`}>
                                {summary.expectedValue >= 0 ? "+" : ""}${summary.expectedValue.toFixed(2)}
                              </p>
                            </div>
                            <div>
                              <p className="text-muted-foreground text-xs">Bank Used</p>
                              <p className="font-bold text-white">{summary.bankrollPercentageUsed.toFixed(1)}%</p>
                            </div>
                          </div>

                          <div className="flex items-center gap-2">
                            <AlertTriangle className={`h-4 w-4 ${getRiskColor(summary.riskAssessment)}`} />
                            <span className={`text-sm font-medium ${getRiskColor(summary.riskAssessment)}`}>
                              {summary.riskAssessment.toUpperCase()} RISK
                            </span>
                          </div>
                        </CardContent>
                      </Card>
                    )}

                    {/* Multi-Bet Options */}
                    {summary && summary.multiBetOptions.length > 0 && (
                      <Card className="bg-card border-border">
                        <CardHeader className="pb-2">
                          <button
                            className="flex items-center justify-between w-full text-left"
                            onClick={() => setShowMultis(!showMultis)}
                            data-testid="button-toggle-multis"
                          >
                            <CardTitle className="text-sm flex items-center gap-2">
                              <Zap className="h-4 w-4 text-yellow-400" />
                              MULTI-BET OPTIONS
                            </CardTitle>
                            {showMultis ? (
                              <ChevronUp className="h-4 w-4 text-muted-foreground" />
                            ) : (
                              <ChevronDown className="h-4 w-4 text-muted-foreground" />
                            )}
                          </button>
                        </CardHeader>
                        <AnimatePresence>
                          {showMultis && (
                            <motion.div
                              initial={{ height: 0, opacity: 0 }}
                              animate={{ height: "auto", opacity: 1 }}
                              exit={{ height: 0, opacity: 0 }}
                            >
                              <CardContent className="space-y-2">
                                {summary.multiBetOptions.map((multi, i) => (
                                  <div 
                                    key={i}
                                    className="p-3 bg-muted/20 rounded-lg border border-border/50"
                                    data-testid={`card-multi-${multi.type}`}
                                  >
                                    <div className="flex items-center justify-between mb-2">
                                      <Badge className="bg-yellow-500/20 text-yellow-400 border-yellow-500/50 text-xs">
                                        {multi.name}
                                      </Badge>
                                      <span className="text-lg font-bold text-green-400">
                                        ${multi.combinedOdds.toFixed(2)}
                                      </span>
                                    </div>
                                    <p className="text-xs text-muted-foreground mb-1">
                                      {multi.selections.join(" + ")}
                                    </p>
                                    <div className="flex items-center justify-between text-xs">
                                      <span className="text-muted-foreground">
                                        Combined Prob: {(multi.combinedProb * 100).toFixed(2)}%
                                      </span>
                                      <span className="text-muted-foreground">
                                        Stake: {multi.recommendedStakePct}% bank
                                      </span>
                                    </div>
                                  </div>
                                ))}
                              </CardContent>
                            </motion.div>
                          )}
                        </AnimatePresence>
                      </Card>
                    )}
                  </div>
                )}
              </div>
            </motion.div>
          </>
        )}
      </AnimatePresence>,
        document.body
      )}
    </>
  );
}
