import { Switch, Route } from "wouter";
import { queryClient } from "./lib/queryClient";
import { QueryClientProvider } from "@tanstack/react-query";
import { Toaster } from "@/components/ui/toaster";
import { TooltipProvider } from "@/components/ui/tooltip";
import { BetSlipProvider } from "@/lib/betSlipContext";
import Home from "@/pages/Home";
import ChatInterface from "@/pages/ChatInterface";
import SimulationDashboard from "@/pages/SimulationDashboard";
import BettingDashboard from "@/pages/BettingDashboard";
import CalibrationDashboard from "@/pages/CalibrationDashboard";
import Tracks from "@/pages/Tracks";
import FormGuide from "@/pages/FormGuide";
import About from "@/pages/About";
import BacktestResults from "@/pages/BacktestResults";
import GapAnalysis from "@/pages/GapAnalysis";
import ProofHub from "@/pages/ProofHub";
import NotFound from "@/pages/not-found";

function Router() {
  return (
    <Switch>
      <Route path="/" component={Home} />
      <Route path="/chat" component={ChatInterface} />
      <Route path="/ask-stride" component={ChatInterface} />
      <Route path="/simulations" component={SimulationDashboard} />
      <Route path="/bets" component={BettingDashboard} />
      <Route path="/blackbook" component={BettingDashboard} />
      <Route path="/best-bets" component={BettingDashboard} />
      <Route path="/race-day" component={BettingDashboard} />
      <Route path="/proof" component={ProofHub} />
      <Route path="/calibration" component={CalibrationDashboard} />
      <Route path="/tracks" component={Tracks} />
      <Route path="/form-guide" component={FormGuide} />
      <Route path="/about" component={About} />
      <Route path="/backtest" component={BacktestResults} />
      <Route path="/gap-analysis" component={GapAnalysis} />
      <Route component={NotFound} />
    </Switch>
  );
}

function App() {
  return (
    <QueryClientProvider client={queryClient}>
      <TooltipProvider>
        <BetSlipProvider>
          <Toaster />
          <Router />
        </BetSlipProvider>
      </TooltipProvider>
    </QueryClientProvider>
  );
}

export default App;
