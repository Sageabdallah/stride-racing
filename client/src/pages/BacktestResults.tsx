import { useQuery } from "@tanstack/react-query";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Skeleton } from "@/components/ui/skeleton";
import { Navbar } from "@/components/Navbar";
import { AmbientEffects } from "@/components/AmbientEffects";
import { Link } from "wouter";
import {
  ArrowLeft,
  BarChart3,
  TrendingUp,
  TrendingDown,
  Shield,
  Target,
  Activity,
  AlertTriangle,
  DollarSign,
  MapPin,
  Calendar,
  Layers,
} from "lucide-react";
import {
  BarChart,
  Bar,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  ResponsiveContainer,
  Cell,
  LineChart,
  Line,
  ReferenceLine,
} from "recharts";

interface TrackBreakdown {
  bets: number;
  wins: number;
  staked: number;
  returned: number;
}

interface StrategyResult {
  label: string;
  roi: number;
  strike_rate: number;
  total_bets: number;
  pnl: number;
  avg_sp: number;
}

interface BacktestData {
  available: boolean;
  backtest_date: string;
  methodology: string;
  staking: string;
  test_period: string;
  best_strategy: {
    label: string;
    config: Record<string, number>;
    roi: number;
    strike_rate: number;
    place_rate: number;
    total_bets: number;
    total_wins: number;
    pnl: number;
    avg_sp: number;
    avg_win_sp: number;
  };
  all_strategies: StrategyResult[];
  best_by_track: Record<string, TrackBreakdown>;
  best_by_month: Record<string, TrackBreakdown>;
  best_by_odds: Record<string, TrackBreakdown>;
}

function calcRoi(s: TrackBreakdown) {
  return s.staked > 0 ? ((s.returned - s.staked) / s.staked) * 100 : 0;
}

function calcSr(s: TrackBreakdown) {
  return s.bets > 0 ? (s.wins / s.bets) * 100 : 0;
}

function RoiBadge({ roi }: { roi: number }) {
  if (roi > 0) {
    return (
      <Badge variant="default" className="bg-emerald-500/20 text-emerald-400 border-emerald-500/30">
        <TrendingUp className="w-3 h-3 mr-1" />
        {roi > 0 ? "+" : ""}
        {roi.toFixed(1)}%
      </Badge>
    );
  }
  return (
    <Badge variant="default" className="bg-red-500/20 text-red-400 border-red-500/30">
      <TrendingDown className="w-3 h-3 mr-1" />
      {roi.toFixed(1)}%
    </Badge>
  );
}

function StatCard({
  label,
  value,
  icon: Icon,
  sub,
}: {
  label: string;
  value: string;
  icon: typeof BarChart3;
  sub?: string;
}) {
  return (
    <Card className="glass-card border-white/5" data-testid={`stat-${label.toLowerCase().replace(/\s/g, "-")}`}>
      <CardContent className="p-4">
        <div className="flex items-center gap-3">
          <div className="w-10 h-10 rounded-lg bg-racing-orange/10 flex items-center justify-center shrink-0">
            <Icon className="w-5 h-5 text-racing-orange" />
          </div>
          <div>
            <p className="text-white/40 text-xs uppercase tracking-wider">{label}</p>
            <p className="text-white text-xl font-bold font-syne">{value}</p>
            {sub && <p className="text-white/30 text-xs mt-0.5">{sub}</p>}
          </div>
        </div>
      </CardContent>
    </Card>
  );
}

export default function BacktestResults() {
  const { data, isLoading, error } = useQuery<BacktestData>({
    queryKey: ["/api/backtest-results"],
  });

  if (isLoading) {
    return (
      <div className="min-h-screen bg-black font-dm">
        <AmbientEffects />
        <Navbar />
        <main className="max-w-7xl mx-auto px-4 sm:px-6 pt-24 pb-12 relative z-[1]">
          <Skeleton className="h-8 w-64 mb-8 bg-white/5" />
          <div className="grid grid-cols-1 md:grid-cols-4 gap-4">
            {[1, 2, 3, 4].map((i) => (
              <Skeleton key={i} className="h-24 bg-white/5 rounded-lg" />
            ))}
          </div>
        </main>
      </div>
    );
  }

  if (error || !data?.available) {
    return (
      <div className="min-h-screen bg-black font-dm">
        <AmbientEffects />
        <Navbar />
        <main className="max-w-7xl mx-auto px-4 sm:px-6 pt-24 pb-12 relative z-[1] text-center">
          <h1 className="text-2xl font-syne font-bold text-white mb-4">Backtest Results</h1>
          <p className="text-white/50">No backtest results available yet. Run the backtesting system to generate results.</p>
          <Link href="/">
            <span className="text-racing-orange hover:underline cursor-pointer mt-4 inline-block">Back to Home</span>
          </Link>
        </main>
      </div>
    );
  }

  const best = data.best_strategy;

  const trackData = Object.entries(data.best_by_track || {})
    .map(([track, s]) => ({
      track: track.replace("Sportsbet ", "").replace("Ladbrokes ", ""),
      bets: s.bets,
      wins: s.wins,
      roi: parseFloat(calcRoi(s).toFixed(1)),
      sr: parseFloat(calcSr(s).toFixed(1)),
    }))
    .sort((a, b) => b.bets - a.bets);

  const monthData = Object.entries(data.best_by_month || {})
    .map(([month, s]) => ({
      month,
      bets: s.bets,
      wins: s.wins,
      roi: parseFloat(calcRoi(s).toFixed(1)),
      pnl: s.returned - s.staked,
    }))
    .sort((a, b) => a.month.localeCompare(b.month));

  const oddsData = Object.entries(data.best_by_odds || {})
    .map(([range, s]) => ({
      range,
      bets: s.bets,
      wins: s.wins,
      roi: parseFloat(calcRoi(s).toFixed(1)),
      sr: parseFloat(calcSr(s).toFixed(1)),
    }));

  return (
    <div className="min-h-screen bg-black font-dm" data-testid="page-backtest">
      <AmbientEffects />
      <Navbar />

      <main className="max-w-7xl mx-auto px-4 sm:px-6 pt-24 pb-12 relative z-[1]">
        <Link href="/">
          <span className="inline-flex items-center gap-2 text-white/40 hover:text-white text-sm mb-6 cursor-pointer" data-testid="link-back">
            <ArrowLeft className="w-4 h-4" />
            Back to Home
          </span>
        </Link>

        <div className="mb-8">
          <h1 className="text-3xl sm:text-4xl page-title mb-2" data-testid="text-title">
            BACKTEST <span className="gradient-text-shimmer">RESULTS</span>
          </h1>
          <p className="text-white/40 text-lg max-w-2xl">
            Verified ML model performance using strict temporal train/test split with no data leakage.
          </p>
        </div>

        <Card className="glass-card border-racing-orange/20 mb-8" data-testid="card-methodology">
          <CardContent className="p-5">
            <div className="flex items-start gap-3">
              <Shield className="w-5 h-5 text-racing-orange mt-0.5 shrink-0" />
              <div>
                <h3 className="text-white font-semibold text-sm mb-1">Leak-Proof Methodology</h3>
                <p className="text-white/50 text-sm leading-relaxed">
                  {data.methodology}. {data.staking}. Test period: {data.test_period}.
                  Model trained exclusively on historical data prior to the test period.
                  All ROI calculated on Starting Price (SP) only.
                </p>
              </div>
            </div>
          </CardContent>
        </Card>

        <div className="grid grid-cols-2 md:grid-cols-4 gap-4 mb-8">
          <StatCard label="Total Bets" value={best.total_bets.toString()} icon={BarChart3} sub={`${best.label}`} />
          <StatCard label="Strike Rate" value={`${best.strike_rate}%`} icon={Target} sub={`${best.total_wins} winners`} />
          <StatCard label="ROI" value={`${best.roi > 0 ? "+" : ""}${best.roi}%`} icon={best.roi > 0 ? TrendingUp : TrendingDown} sub={`P&L: $${best.pnl > 0 ? "+" : ""}${best.pnl.toLocaleString()}`} />
          <StatCard label="Avg SP" value={`$${best.avg_sp}`} icon={DollarSign} sub={`Winners avg: $${best.avg_win_sp}`} />
        </div>

        <div className="grid grid-cols-1 lg:grid-cols-2 gap-6 mb-8">
          <Card className="glass-card border-white/5" data-testid="card-strategies">
            <CardHeader className="pb-3">
              <CardTitle className="text-white text-lg font-syne flex items-center gap-2">
                <Layers className="w-5 h-5 text-racing-orange" />
                Strategy Comparison
              </CardTitle>
            </CardHeader>
            <CardContent className="p-0">
              <div className="overflow-x-auto">
                <table className="w-full text-sm" data-testid="table-strategies">
                  <thead>
                    <tr className="border-b border-white/5">
                      <th className="text-left text-white/40 py-2 px-4 font-medium">Strategy</th>
                      <th className="text-right text-white/40 py-2 px-4 font-medium">Bets</th>
                      <th className="text-right text-white/40 py-2 px-4 font-medium">Win%</th>
                      <th className="text-right text-white/40 py-2 px-4 font-medium">ROI</th>
                    </tr>
                  </thead>
                  <tbody>
                    {data.all_strategies.map((s, i) => (
                      <tr key={i} className="border-b border-white/5 last:border-0">
                        <td className="py-2.5 px-4 text-white/70 text-xs">{s.label}</td>
                        <td className="py-2.5 px-4 text-right text-white/50">{s.total_bets}</td>
                        <td className="py-2.5 px-4 text-right text-white/50">{s.strike_rate}%</td>
                        <td className="py-2.5 px-4 text-right">
                          <RoiBadge roi={s.roi} />
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </CardContent>
          </Card>

          <Card className="glass-card border-white/5" data-testid="card-track-roi">
            <CardHeader className="pb-3">
              <CardTitle className="text-white text-lg font-syne flex items-center gap-2">
                <MapPin className="w-5 h-5 text-racing-orange" />
                ROI by Track
              </CardTitle>
            </CardHeader>
            <CardContent>
              <ResponsiveContainer width="100%" height={300}>
                <BarChart data={trackData} margin={{ top: 10, right: 10, left: -10, bottom: 5 }}>
                  <CartesianGrid strokeDasharray="3 3" stroke="rgba(255,255,255,0.05)" />
                  <XAxis
                    dataKey="track"
                    tick={{ fill: "rgba(255,255,255,0.4)", fontSize: 10 }}
                    angle={-35}
                    textAnchor="end"
                    height={80}
                  />
                  <YAxis tick={{ fill: "rgba(255,255,255,0.4)", fontSize: 11 }} />
                  <Tooltip
                    contentStyle={{
                      background: "rgba(0,0,0,0.9)",
                      border: "1px solid rgba(255,255,255,0.1)",
                      borderRadius: "8px",
                      color: "#fff",
                      fontSize: "12px",
                    }}
                    formatter={(value: number) => [`${value}%`, "ROI"]}
                  />
                  <ReferenceLine y={0} stroke="rgba(255,255,255,0.2)" />
                  <Bar dataKey="roi" radius={[4, 4, 0, 0]}>
                    {trackData.map((entry, index) => (
                      <Cell
                        key={`cell-${index}`}
                        fill={entry.roi > 0 ? "rgba(16,185,129,0.7)" : "rgba(239,68,68,0.5)"}
                      />
                    ))}
                  </Bar>
                </BarChart>
              </ResponsiveContainer>
            </CardContent>
          </Card>
        </div>

        <div className="grid grid-cols-1 lg:grid-cols-2 gap-6 mb-8">
          <Card className="glass-card border-white/5" data-testid="card-month-pnl">
            <CardHeader className="pb-3">
              <CardTitle className="text-white text-lg font-syne flex items-center gap-2">
                <Calendar className="w-5 h-5 text-racing-orange" />
                Monthly P&L
              </CardTitle>
            </CardHeader>
            <CardContent>
              <ResponsiveContainer width="100%" height={250}>
                <LineChart data={monthData} margin={{ top: 10, right: 10, left: -10, bottom: 5 }}>
                  <CartesianGrid strokeDasharray="3 3" stroke="rgba(255,255,255,0.05)" />
                  <XAxis dataKey="month" tick={{ fill: "rgba(255,255,255,0.4)", fontSize: 11 }} />
                  <YAxis tick={{ fill: "rgba(255,255,255,0.4)", fontSize: 11 }} />
                  <Tooltip
                    contentStyle={{
                      background: "rgba(0,0,0,0.9)",
                      border: "1px solid rgba(255,255,255,0.1)",
                      borderRadius: "8px",
                      color: "#fff",
                      fontSize: "12px",
                    }}
                    formatter={(value: number) => [`$${value.toLocaleString()}`, "P&L"]}
                  />
                  <ReferenceLine y={0} stroke="rgba(255,255,255,0.2)" />
                  <Line
                    type="monotone"
                    dataKey="pnl"
                    stroke="hsl(30,100%,50%)"
                    strokeWidth={2}
                    dot={{ fill: "hsl(30,100%,50%)", r: 4 }}
                  />
                </LineChart>
              </ResponsiveContainer>
            </CardContent>
          </Card>

          <Card className="glass-card border-white/5" data-testid="card-odds-breakdown">
            <CardHeader className="pb-3">
              <CardTitle className="text-white text-lg font-syne flex items-center gap-2">
                <Activity className="w-5 h-5 text-racing-orange" />
                Performance by Odds Range
              </CardTitle>
            </CardHeader>
            <CardContent className="p-0">
              <div className="overflow-x-auto">
                <table className="w-full text-sm" data-testid="table-odds">
                  <thead>
                    <tr className="border-b border-white/5">
                      <th className="text-left text-white/40 py-2 px-4 font-medium">Odds</th>
                      <th className="text-right text-white/40 py-2 px-4 font-medium">Bets</th>
                      <th className="text-right text-white/40 py-2 px-4 font-medium">Win%</th>
                      <th className="text-right text-white/40 py-2 px-4 font-medium">ROI</th>
                    </tr>
                  </thead>
                  <tbody>
                    {oddsData.map((s, i) => (
                      <tr key={i} className="border-b border-white/5 last:border-0">
                        <td className="py-2.5 px-4 text-white/70">{s.range}</td>
                        <td className="py-2.5 px-4 text-right text-white/50">{s.bets}</td>
                        <td className="py-2.5 px-4 text-right text-white/50">{s.sr}%</td>
                        <td className="py-2.5 px-4 text-right">
                          <RoiBadge roi={s.roi} />
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </CardContent>
          </Card>
        </div>

        <Card className="glass-card border-white/5 mb-8" data-testid="card-track-table">
          <CardHeader className="pb-3">
            <CardTitle className="text-white text-lg font-syne flex items-center gap-2">
              <MapPin className="w-5 h-5 text-racing-orange" />
              Track-by-Track Breakdown
            </CardTitle>
          </CardHeader>
          <CardContent className="p-0">
            <div className="overflow-x-auto">
              <table className="w-full text-sm" data-testid="table-tracks">
                <thead>
                  <tr className="border-b border-white/5">
                    <th className="text-left text-white/40 py-2 px-4 font-medium">Track</th>
                    <th className="text-right text-white/40 py-2 px-4 font-medium">Bets</th>
                    <th className="text-right text-white/40 py-2 px-4 font-medium">Winners</th>
                    <th className="text-right text-white/40 py-2 px-4 font-medium">Strike Rate</th>
                    <th className="text-right text-white/40 py-2 px-4 font-medium">ROI</th>
                  </tr>
                </thead>
                <tbody>
                  {trackData.map((s, i) => (
                    <tr key={i} className="border-b border-white/5 last:border-0">
                      <td className="py-2.5 px-4 text-white/70">{s.track}</td>
                      <td className="py-2.5 px-4 text-right text-white/50">{s.bets}</td>
                      <td className="py-2.5 px-4 text-right text-white/50">{s.wins}</td>
                      <td className="py-2.5 px-4 text-right text-white/50">{s.sr}%</td>
                      <td className="py-2.5 px-4 text-right">
                        <RoiBadge roi={s.roi} />
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </CardContent>
        </Card>

        <Card className="glass-card border-amber-500/20" data-testid="card-disclaimer">
          <CardContent className="p-5">
            <div className="flex items-start gap-3">
              <AlertTriangle className="w-5 h-5 text-amber-400 mt-0.5 shrink-0" />
              <div>
                <h3 className="text-amber-400 font-semibold text-sm mb-2">Important Disclaimers</h3>
                <ul className="text-white/40 text-xs space-y-1.5 leading-relaxed">
                  <li>Past performance does not guarantee future results. Racing outcomes are inherently unpredictable.</li>
                  <li>Backtest results use a strict temporal train/test split with no data leakage. The ML model was trained only on historical data prior to the test period.</li>
                  <li>All returns calculated on Starting Price (SP) with flat $100 stakes. Real-world results may differ due to market conditions, liquidity, and available odds.</li>
                  <li>This analysis is for informational purposes only and does not constitute financial or gambling advice.</li>
                  <li>Please gamble responsibly. If gambling is causing you problems, call 1800 858 858 for free counselling.</li>
                </ul>
              </div>
            </div>
          </CardContent>
        </Card>
      </main>
    </div>
  );
}
