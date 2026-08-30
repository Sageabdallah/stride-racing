import { createContext, useContext, useState, useCallback, useEffect, type ReactNode } from "react";
import { apiRequest } from "./queryClient";
import type { BetSlipSelection, StakeRecommendation, MultiBetOption, StakingMethod } from "@shared/schema";

interface FlyingItem {
  id: string;
  horseName: string;
  startX: number;
  startY: number;
}

interface BetSlipContextType {
  selections: BetSlipSelection[];
  selectionCount: number;
  bankroll: number;
  stakingMethod: StakingMethod;
  isOpen: boolean;
  isLoading: boolean;
  flyingItem: FlyingItem | null;
  summary: {
    totalSelections: number;
    totalStake: number;
    totalPotentialReturn: number;
    totalPotentialProfit: number;
    expectedValue: number;
    riskAssessment: string;
    bankrollPercentageUsed: number;
    recommendations: StakeRecommendation[];
    multiBetOptions: MultiBetOption[];
  } | null;
  addSelection: (selection: Omit<BetSlipSelection, "id" | "addedAt">, event?: React.MouseEvent) => Promise<void>;
  removeSelection: (id: string) => Promise<void>;
  clearSlip: () => Promise<void>;
  setBankroll: (amount: number) => Promise<void>;
  setStakingMethod: (method: StakingMethod) => Promise<void>;
  calculateStakes: () => Promise<void>;
  toggleSlip: () => void;
  openSlip: () => void;
  closeSlip: () => void;
  isInSlip: (horseName: string, track: string, raceNumber: number) => boolean;
}

const BetSlipContext = createContext<BetSlipContextType | null>(null);

export function useBetSlip() {
  const context = useContext(BetSlipContext);
  if (!context) {
    throw new Error("useBetSlip must be used within a BetSlipProvider");
  }
  return context;
}

export function BetSlipProvider({ children }: { children: ReactNode }) {
  const [selections, setSelections] = useState<BetSlipSelection[]>([]);
  const [bankroll, setBankrollState] = useState(1000);
  const [stakingMethod, setStakingMethodState] = useState<StakingMethod>("fractional_kelly");
  const [isOpen, setIsOpen] = useState(false);
  const [isLoading, setIsLoading] = useState(false);
  const [summary, setSummary] = useState<BetSlipContextType["summary"]>(null);
  const [flyingItem, setFlyingItem] = useState<FlyingItem | null>(null);

  const fetchSlip = useCallback(async () => {
    try {
      const response = await fetch("/api/betslip");
      const data = await response.json();
      if (data.success) {
        setSelections(data.selections || []);
        setBankrollState(data.bankroll || 1000);
        setStakingMethodState(data.stakingMethod || "fractional_kelly");
      }
    } catch (error) {
      console.error("Failed to fetch bet slip:", error);
    }
  }, []);

  useEffect(() => {
    fetchSlip();
  }, [fetchSlip]);

  const addSelection = useCallback(async (selection: Omit<BetSlipSelection, "id" | "addedAt">, event?: React.MouseEvent) => {
    setIsLoading(true);
    
    // Trigger flying animation if event is provided
    if (event) {
      const rect = (event.currentTarget as HTMLElement).getBoundingClientRect();
      const flyId = `flying-${Date.now()}`;
      setFlyingItem({
        id: flyId,
        horseName: selection.horseName,
        startX: rect.left + rect.width / 2,
        startY: rect.top + rect.height / 2
      });
      
      // Clear the flying item after animation completes
      setTimeout(() => {
        setFlyingItem(null);
      }, 600);
    }
    
    try {
      const response = await apiRequest("POST", "/api/betslip/add", selection);
      const data = await response.json();
      if (data.success) {
        await fetchSlip();
        // Don't auto-open the slip - let user click to view
      }
    } catch (error) {
      console.error("Failed to add selection:", error);
    } finally {
      setIsLoading(false);
    }
  }, [fetchSlip]);

  const removeSelection = useCallback(async (id: string) => {
    setIsLoading(true);
    try {
      const response = await fetch(`/api/betslip/remove/${encodeURIComponent(id)}`, { method: "DELETE" });
      const data = await response.json();
      if (data.success) {
        await fetchSlip();
      }
    } catch (error) {
      console.error("Failed to remove selection:", error);
    } finally {
      setIsLoading(false);
    }
  }, [fetchSlip]);

  const clearSlip = useCallback(async () => {
    setIsLoading(true);
    try {
      await apiRequest("POST", "/api/betslip/clear");
      await fetchSlip();
      setSummary(null);
    } catch (error) {
      console.error("Failed to clear slip:", error);
    } finally {
      setIsLoading(false);
    }
  }, [fetchSlip]);

  const setBankroll = useCallback(async (amount: number) => {
    setIsLoading(true);
    try {
      await apiRequest("POST", "/api/betslip/bankroll", { amount });
      setBankrollState(amount);
    } catch (error) {
      console.error("Failed to set bankroll:", error);
    } finally {
      setIsLoading(false);
    }
  }, []);

  const setStakingMethod = useCallback(async (method: StakingMethod) => {
    setIsLoading(true);
    try {
      await apiRequest("POST", "/api/betslip/staking-method", { method });
      setStakingMethodState(method);
    } catch (error) {
      console.error("Failed to set staking method:", error);
    } finally {
      setIsLoading(false);
    }
  }, []);

  const calculateStakes = useCallback(async () => {
    if (selections.length === 0) return;
    setIsLoading(true);
    try {
      const response = await apiRequest("POST", "/api/betslip/quick-calculate", {
        bankroll,
        stakingMethod
      });
      const data = await response.json();
      if (data.success) {
        setSummary({
          totalSelections: data.totalSelections,
          totalStake: data.totalStake,
          totalPotentialReturn: data.totalPotentialReturn,
          totalPotentialProfit: data.totalPotentialProfit,
          expectedValue: data.expectedValue,
          riskAssessment: data.riskAssessment,
          bankrollPercentageUsed: data.bankrollPercentageUsed,
          recommendations: data.recommendations || [],
          multiBetOptions: data.multiBetOptions || []
        });
      }
    } catch (error) {
      console.error("Failed to calculate stakes:", error);
    } finally {
      setIsLoading(false);
    }
  }, [selections.length, bankroll, stakingMethod]);

  const toggleSlip = useCallback(() => setIsOpen((prev) => !prev), []);
  const openSlip = useCallback(() => setIsOpen(true), []);
  const closeSlip = useCallback(() => setIsOpen(false), []);

  const isInSlip = useCallback((horseName: string, track: string, raceNumber: number) => {
    return selections.some(
      (s) => s.horseName === horseName && s.track === track && s.raceNumber === raceNumber
    );
  }, [selections]);

  return (
    <BetSlipContext.Provider
      value={{
        selections,
        selectionCount: selections.length,
        bankroll,
        stakingMethod,
        isOpen,
        isLoading,
        flyingItem,
        summary,
        addSelection,
        removeSelection,
        clearSlip,
        setBankroll,
        setStakingMethod,
        calculateStakes,
        toggleSlip,
        openSlip,
        closeSlip,
        isInSlip
      }}
    >
      {children}
    </BetSlipContext.Provider>
  );
}
