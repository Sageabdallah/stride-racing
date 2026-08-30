import { useState } from "react";
import { useQuery, useMutation } from "@tanstack/react-query";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Skeleton } from "@/components/ui/skeleton";
import { Link } from "wouter";
import {
  ArrowLeft,
  Activity,
  Target,
  TrendingUp,
  BarChart3,
  Clock,
  RefreshCw,
  AlertTriangle,
  CheckCircle,
  XCircle,
} from "lucide-react";
import {
  LineChart,
  Line,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  ResponsiveContainer,
} from "recharts";
import { apiRequest, queryClient } from "@/lib/queryClient";

interface SnapshotsSummary {
  totalSnapshots: number;
  datesCovered: number;
  tracksCovered: number;
  firstSnapshot: string;
  lastSnapshot: string;
}

interface PredictionAuditSummary {
  pendingResults: number;
  totalResulted: number;
  winners: number;
  strikeRate: number;
  totalPnl: number;
}

interface PredictionRecord {
  date: string;
  track: string;
  race: number;
  horse: string;
  predicted_pct: number;
  market_odds: number;
  result: string;
  pnl: number;
  result_status: "pending" | "resulted";
}

interface PredictionAuditData {
  summary: PredictionAuditSummary;
  recent: PredictionRecord[];
}

interface DailyPerformance {
  date: string;
  bets: number;
  winners: number;
  strikeRate: number;
  pnl: number;
  cumulativePnl: number;
}

interface LivePerformanceData {
  daily: DailyPerformance[];
  rollingStrikeRate: number;
  totalPnl: number;
}

interface RaceScheduleItem {
  race_id: string;
  track: string;
  race_number: number;
  date: string;
  due_time: string;
  status: "pending" | "collected" | "failed";
}

interface RaceScheduleData {
  summary: {
    pending: number;
    collected: number;
    failed: number;
  };
  pending: RaceScheduleItem[];
}

interface DriftFeature {
  feature: string;
  nan_ratio: number;
  zero_ratio: number;
  range_violation: boolean;
  drift_score: number;
}

interface FeatureDriftData {
  parityScore: number;
  anomalies: DriftFeature[];
  allFeatures: DriftFeature[];
}

function formatPnl(value: number): string {
  const abs = Math.abs(value);
  return value >= 0 ? `+${abs.toFixed(1)}u` : `-${abs.toFixed(1)}u`;
}

export default function GapAnalysis() {
  const [driftEnabled, setDriftEnabled] = useState(false);

  const { data: snapshotsData, isLoading: snapshotsLoading } = useQuery<SnapshotsSummary>({
    queryKey: ['/api/gap-analysis/snapshots-summary'],
  });

  const { data: auditData, isLoading: auditLoading } = useQuery<PredictionAuditData>({
    queryKey: ['/api/gap-analysis/prediction-audit'],
  });

  const { data: perfData, isLoading: perfLoading } = useQuery<LivePerformanceData>({
    queryKey: ['/api/gap-analysis/live-performance'],
  });

  const { data: scheduleData, isLoading: scheduleLoading } = useQuery<RaceScheduleData>({
    queryKey: ['/api/gap-analysis/race-schedule'],
  });

  const { data: driftData, isLoading: driftLoading, isFetching: driftFetching } = useQuery<FeatureDriftData>({
    queryKey: ['/api/gap-analysis/feature-drift'],
    enabled: driftEnabled,
  });

  const collectMutation = useMutation({
    mutationFn: () => apiRequest('POST', '/api/gap-analysis/collect-results'),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['/api/gap-analysis/race-schedule'] });
      queryClient.invalidateQueries({ queryKey: ['/api/gap-analysis/prediction-audit'] });
    },
  });

  const isMainLoading = snapshotsLoading || auditLoading;

  const chartData = perfData?.daily?.map(d => ({
    date: d.date,
    pnl: d.cumulativePnl,
  })) || [];

  return (
    <div className="min-h-screen bg-black font-dm text-white">
      <header className="page-header-liquid">
        <div className="container mx-auto px-4 py-4">
          <div className="flex items-center justify-between flex-wrap gap-3">
            <div className="flex items-center gap-4">
              <Link href="/">
                <Button variant="ghost" size="sm" data-testid="button-back">
                  <ArrowLeft className="h-4 w-4 mr-2" />
                  Back
                </Button>
              </Link>
              <div>
                <h1 className="text-xl font-syne font-extrabold tracking-tight text-white flex items-center gap-2">
                  <BarChart3 className="h-5 w-5 text-racing-orange" />
                  Gap Analysis
                </h1>
                <p className="text-sm text-white/40">
                  Training-inference gap, live performance & results collection
                </p>
              </div>
            </div>
          </div>
        </div>
      </header>

      <main className="container mx-auto px-4 py-6 space-y-6">
        {isMainLoading ? (
          <div className="space-y-4">
            <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
              {[1, 2, 3, 4].map(i => (
                <Card key={i} className="glass-card border-0">
                  <CardContent className="p-4">
                    <Skeleton className="h-4 w-24 mb-2" />
                    <Skeleton className="h-8 w-16" />
                  </CardContent>
                </Card>
              ))}
            </div>
            <Card className="glass-card border-0">
              <CardContent className="p-6">
                <Skeleton className="h-64 w-full" />
              </CardContent>
            </Card>
          </div>
        ) : (
          <>
            {/* Section 1: Overview Cards */}
            <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
              <Card className="glass-card border-0">
                <CardContent className="p-4">
                  <div className="flex items-center gap-2 mb-1">
                    <Target className="h-3.5 w-3.5 text-racing-orange" />
                    <p className="text-xs text-white/40 uppercase tracking-wider">Snapshots</p>
                  </div>
                  <p className="text-2xl font-bold text-white" data-testid="text-total-snapshots">
                    {snapshotsData?.totalSnapshots ?? 0}
                  </p>
                  <p className="text-xs text-white/30 mt-1">
                    Feature snapshots captured
                  </p>
                </CardContent>
              </Card>

              <Card className="glass-card border-0">
                <CardContent className="p-4">
                  <div className="flex items-center gap-2 mb-1">
                    <Activity className="h-3.5 w-3.5 text-racing-gold" />
                    <p className="text-xs text-white/40 uppercase tracking-wider">Coverage</p>
                  </div>
                  <p className="text-2xl font-bold text-white" data-testid="text-dates-covered">
                    {snapshotsData?.datesCovered ?? 0}
                  </p>
                  <p className="text-xs text-white/30 mt-1">
                    {snapshotsData?.tracksCovered ?? 0} tracks covered
                  </p>
                </CardContent>
              </Card>

              <Card className="glass-card border-0">
                <CardContent className="p-4">
                  <div className="flex items-center gap-2 mb-1">
                    <Clock className="h-3.5 w-3.5 text-amber-400" />
                    <p className="text-xs text-white/40 uppercase tracking-wider">Pending</p>
                  </div>
                  <p className="text-2xl font-bold text-white" data-testid="text-pending-results">
                    {auditData?.summary?.pendingResults ?? 0}
                  </p>
                  <p className="text-xs text-white/30 mt-1">
                    Awaiting results
                  </p>
                </CardContent>
              </Card>

              <Card className="glass-card border-0">
                <CardContent className="p-4">
                  <div className="flex items-center gap-2 mb-1">
                    <TrendingUp className="h-3.5 w-3.5 text-green-400" />
                    <p className="text-xs text-white/40 uppercase tracking-wider">Strike Rate</p>
                  </div>
                  <p className="text-2xl font-bold text-white" data-testid="text-live-strike-rate">
                    {auditData?.summary?.strikeRate != null
                      ? `${auditData.summary.strikeRate.toFixed(1)}%`
                      : "N/A"}
                  </p>
                  <p className="text-xs text-white/30 mt-1">
                    {auditData?.summary?.winners ?? 0} / {auditData?.summary?.totalResulted ?? 0} resulted
                  </p>
                </CardContent>
              </Card>
            </div>

            {/* Section 2: Live Performance */}
            <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
              <Card className="glass-card border-0">
                <CardHeader className="pb-2">
                  <CardTitle className="text-base flex items-center gap-2 font-syne text-white">
                    <TrendingUp className="h-4 w-4 text-racing-orange" />
                    Cumulative P&L
                  </CardTitle>
                  <p className="text-xs text-white/35">
                    Running profit/loss over time
                  </p>
                </CardHeader>
                <CardContent>
                  {perfLoading ? (
                    <Skeleton className="h-64 w-full" />
                  ) : chartData.length > 0 ? (
                    <ResponsiveContainer width="100%" height={280}>
                      <LineChart data={chartData} margin={{ top: 10, right: 20, left: -10, bottom: 5 }}>
                        <CartesianGrid strokeDasharray="3 3" stroke="rgba(255,255,255,0.04)" />
                        <XAxis
                          dataKey="date"
                          tick={{ fill: 'rgba(255,255,255,0.35)', fontSize: 10 }}
                          axisLine={{ stroke: 'rgba(255,255,255,0.06)' }}
                        />
                        <YAxis
                          tick={{ fill: 'rgba(255,255,255,0.35)', fontSize: 10 }}
                          axisLine={{ stroke: 'rgba(255,255,255,0.06)' }}
                          tickFormatter={(v) => `${v}u`}
                        />
                        <Tooltip
                          contentStyle={{
                            backgroundColor: 'rgba(0,0,0,0.9)',
                            border: '1px solid rgba(255,255,255,0.1)',
                            borderRadius: '8px',
                            fontSize: '12px',
                          }}
                          labelStyle={{ color: '#fff' }}
                          formatter={(value: number) => [`${formatPnl(value)}`, 'P&L']}
                        />
                        <Line
                          type="monotone"
                          dataKey="pnl"
                          stroke="hsl(var(--racing-orange))"
                          strokeWidth={2}
                          dot={{ fill: 'hsl(var(--racing-orange))', r: 3, strokeWidth: 0 }}
                        />
                      </LineChart>
                    </ResponsiveContainer>
                  ) : (
                    <div className="h-64 flex items-center justify-center text-white/30">
                      <div className="text-center">
                        <Activity className="h-8 w-8 mx-auto mb-2 text-white/20" />
                        <p>No data yet - run the pipeline to generate predictions</p>
                      </div>
                    </div>
                  )}
                </CardContent>
              </Card>

              <Card className="glass-card border-0">
                <CardHeader className="pb-2">
                  <CardTitle className="text-base flex items-center gap-2 font-syne text-white">
                    <BarChart3 className="h-4 w-4 text-racing-gold" />
                    Daily Performance
                  </CardTitle>
                  <p className="text-xs text-white/35">
                    Rolling strike rate: {perfData?.rollingStrikeRate != null ? `${perfData.rollingStrikeRate.toFixed(1)}%` : 'N/A'}
                    {' | '}Total P&L: {perfData?.totalPnl != null ? formatPnl(perfData.totalPnl) : 'N/A'}
                  </p>
                </CardHeader>
                <CardContent>
                  {perfLoading ? (
                    <Skeleton className="h-64 w-full" />
                  ) : perfData?.daily && perfData.daily.length > 0 ? (
                    <div className="overflow-x-auto max-h-[280px] overflow-y-auto">
                      <table className="w-full text-sm">
                        <thead className="sticky top-0 bg-black/80 backdrop-blur">
                          <tr className="border-b border-white/[0.06]">
                            <th className="text-left py-2.5 px-2 text-white/35 font-medium text-xs uppercase tracking-wider">Date</th>
                            <th className="text-right py-2.5 px-2 text-white/35 font-medium text-xs uppercase tracking-wider">Bets</th>
                            <th className="text-right py-2.5 px-2 text-white/35 font-medium text-xs uppercase tracking-wider">Won</th>
                            <th className="text-right py-2.5 px-2 text-white/35 font-medium text-xs uppercase tracking-wider">Strike</th>
                            <th className="text-right py-2.5 px-2 text-white/35 font-medium text-xs uppercase tracking-wider">P&L</th>
                          </tr>
                        </thead>
                        <tbody>
                          {perfData.daily.map((day, idx) => (
                            <tr key={day.date} className={`${idx % 2 === 0 ? 'bg-white/[0.02]' : ''} hover:bg-white/[0.04] transition-colors`} data-testid={`row-daily-${idx}`}>
                              <td className="py-2.5 px-2 font-medium text-white/80">{day.date}</td>
                              <td className="py-2.5 px-2 text-right text-white/60">{day.bets}</td>
                              <td className="py-2.5 px-2 text-right text-white/60">{day.winners}</td>
                              <td className="py-2.5 px-2 text-right text-white/60">{day.strikeRate.toFixed(1)}%</td>
                              <td className={`py-2.5 px-2 text-right font-medium ${day.pnl >= 0 ? 'text-green-400' : 'text-red-400'}`}>
                                {formatPnl(day.pnl)}
                              </td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    </div>
                  ) : (
                    <div className="h-64 flex items-center justify-center text-white/30">
                      <div className="text-center">
                        <Activity className="h-8 w-8 mx-auto mb-2 text-white/20" />
                        <p>No data yet - run the pipeline to generate predictions</p>
                      </div>
                    </div>
                  )}
                </CardContent>
              </Card>
            </div>

            {/* Section 3: Prediction Audit Log */}
            <Card className="glass-card border-0">
              <CardHeader className="pb-3">
                <CardTitle className="text-base font-syne text-white flex items-center gap-2">
                  <Target className="h-4 w-4 text-racing-orange" />
                  Prediction Audit Log
                </CardTitle>
                <p className="text-xs text-white/35">
                  Recent predictions with results tracking
                </p>
              </CardHeader>
              <CardContent>
                {auditLoading ? (
                  <Skeleton className="h-48 w-full" />
                ) : auditData?.recent && auditData.recent.length > 0 ? (
                  <div className="overflow-x-auto max-h-[400px] overflow-y-auto">
                    <table className="w-full text-sm">
                      <thead className="sticky top-0 bg-black/80 backdrop-blur">
                        <tr className="border-b border-white/[0.06]">
                          <th className="text-left py-2.5 px-2 text-white/35 font-medium text-xs uppercase tracking-wider">Date</th>
                          <th className="text-left py-2.5 px-2 text-white/35 font-medium text-xs uppercase tracking-wider">Track</th>
                          <th className="text-right py-2.5 px-2 text-white/35 font-medium text-xs uppercase tracking-wider">Race</th>
                          <th className="text-left py-2.5 px-2 text-white/35 font-medium text-xs uppercase tracking-wider">Horse</th>
                          <th className="text-right py-2.5 px-2 text-white/35 font-medium text-xs uppercase tracking-wider">Pred%</th>
                          <th className="text-right py-2.5 px-2 text-white/35 font-medium text-xs uppercase tracking-wider">Odds</th>
                          <th className="text-center py-2.5 px-2 text-white/35 font-medium text-xs uppercase tracking-wider">Result</th>
                          <th className="text-right py-2.5 px-2 text-white/35 font-medium text-xs uppercase tracking-wider">P&L</th>
                          <th className="text-center py-2.5 px-2 text-white/35 font-medium text-xs uppercase tracking-wider">Status</th>
                        </tr>
                      </thead>
                      <tbody>
                        {auditData.recent.map((pred, idx) => {
                          const isWinner = pred.result === 'won' || pred.result === '1st';
                          const isLoser = pred.result_status === 'resulted' && !isWinner;
                          const isPending = pred.result_status === 'pending';
                          const rowColor = isWinner
                            ? 'text-green-400'
                            : isLoser
                            ? 'text-red-400'
                            : 'text-white/40';

                          return (
                            <tr
                              key={`${pred.date}-${pred.track}-${pred.race}-${pred.horse}-${idx}`}
                              className={`${idx % 2 === 0 ? 'bg-white/[0.02]' : ''} hover:bg-white/[0.04] transition-colors`}
                              data-testid={`row-prediction-${idx}`}
                            >
                              <td className="py-2.5 px-2 text-white/60">{pred.date}</td>
                              <td className="py-2.5 px-2 text-white/80">{pred.track}</td>
                              <td className="py-2.5 px-2 text-right text-white/60">R{pred.race}</td>
                              <td className="py-2.5 px-2 font-medium text-white/80">{pred.horse}</td>
                              <td className="py-2.5 px-2 text-right text-white/60">
                                {pred.predicted_pct != null ? `${pred.predicted_pct.toFixed(1)}%` : '-'}
                              </td>
                              <td className="py-2.5 px-2 text-right text-white/60">
                                {pred.market_odds != null ? `$${pred.market_odds.toFixed(2)}` : '-'}
                              </td>
                              <td className={`py-2.5 px-2 text-center font-medium ${rowColor}`}>
                                {isPending ? '-' : pred.result || '-'}
                              </td>
                              <td className={`py-2.5 px-2 text-right font-medium ${isPending ? 'text-white/40' : pred.pnl >= 0 ? 'text-green-400' : 'text-red-400'}`}>
                                {isPending ? '-' : formatPnl(pred.pnl)}
                              </td>
                              <td className="py-2.5 px-2 text-center">
                                {pred.result_status === 'resulted' ? (
                                  <Badge className="bg-green-500/20 text-green-400 border-green-500/30 text-[10px]">
                                    Resulted
                                  </Badge>
                                ) : (
                                  <Badge className="bg-white/10 text-white/50 border-white/20 text-[10px]">
                                    Pending
                                  </Badge>
                                )}
                              </td>
                            </tr>
                          );
                        })}
                      </tbody>
                    </table>
                  </div>
                ) : (
                  <div className="py-12 text-center">
                    <Activity className="h-10 w-10 mx-auto mb-3 text-white/20" />
                    <p className="text-white/40">No data yet - run the pipeline to generate predictions</p>
                  </div>
                )}
              </CardContent>
            </Card>

            {/* Section 4: Race Schedule & Results Collection */}
            <Card className="glass-card border-0">
              <CardHeader className="pb-3">
                <div className="flex items-center justify-between flex-wrap gap-3">
                  <div>
                    <CardTitle className="text-base font-syne text-white flex items-center gap-2">
                      <Clock className="h-4 w-4 text-racing-orange" />
                      Race Schedule & Results Collection
                    </CardTitle>
                    <p className="text-xs text-white/35 mt-1">
                      Automated results collection status
                    </p>
                  </div>
                  <Button
                    variant="default"
                    size="sm"
                    onClick={() => collectMutation.mutate()}
                    disabled={collectMutation.isPending}
                    data-testid="button-collect-results"
                  >
                    <RefreshCw className={`h-4 w-4 mr-2 ${collectMutation.isPending ? 'animate-spin' : ''}`} />
                    {collectMutation.isPending ? 'Collecting...' : 'Collect Results'}
                  </Button>
                </div>
              </CardHeader>
              <CardContent>
                {scheduleLoading ? (
                  <Skeleton className="h-32 w-full" />
                ) : scheduleData ? (
                  <div className="space-y-4">
                    <div className="flex items-center gap-4 flex-wrap">
                      <div className="flex items-center gap-2">
                        <Clock className="h-4 w-4 text-amber-400" />
                        <span className="text-sm text-white/60">Pending:</span>
                        <span className="text-sm font-bold text-white" data-testid="text-schedule-pending">{scheduleData.summary.pending}</span>
                      </div>
                      <div className="flex items-center gap-2">
                        <CheckCircle className="h-4 w-4 text-green-400" />
                        <span className="text-sm text-white/60">Collected:</span>
                        <span className="text-sm font-bold text-white" data-testid="text-schedule-collected">{scheduleData.summary.collected}</span>
                      </div>
                      <div className="flex items-center gap-2">
                        <XCircle className="h-4 w-4 text-red-400" />
                        <span className="text-sm text-white/60">Failed:</span>
                        <span className="text-sm font-bold text-white" data-testid="text-schedule-failed">{scheduleData.summary.failed}</span>
                      </div>
                    </div>

                    {scheduleData.pending.length > 0 ? (
                      <div className="overflow-x-auto max-h-[250px] overflow-y-auto">
                        <table className="w-full text-sm">
                          <thead className="sticky top-0 bg-black/80 backdrop-blur">
                            <tr className="border-b border-white/[0.06]">
                              <th className="text-left py-2.5 px-2 text-white/35 font-medium text-xs uppercase tracking-wider">Track</th>
                              <th className="text-right py-2.5 px-2 text-white/35 font-medium text-xs uppercase tracking-wider">Race</th>
                              <th className="text-left py-2.5 px-2 text-white/35 font-medium text-xs uppercase tracking-wider">Date</th>
                              <th className="text-left py-2.5 px-2 text-white/35 font-medium text-xs uppercase tracking-wider">Due Time</th>
                            </tr>
                          </thead>
                          <tbody>
                            {scheduleData.pending.map((race, idx) => (
                              <tr key={`${race.race_id}-${idx}`} className={`${idx % 2 === 0 ? 'bg-white/[0.02]' : ''} hover:bg-white/[0.04] transition-colors`} data-testid={`row-schedule-${idx}`}>
                                <td className="py-2.5 px-2 font-medium text-white/80">{race.track}</td>
                                <td className="py-2.5 px-2 text-right text-white/60">R{race.race_number}</td>
                                <td className="py-2.5 px-2 text-white/60">{race.date}</td>
                                <td className="py-2.5 px-2 text-white/60">{race.due_time}</td>
                              </tr>
                            ))}
                          </tbody>
                        </table>
                      </div>
                    ) : (
                      <p className="text-sm text-white/30 py-4 text-center">No pending races</p>
                    )}
                  </div>
                ) : (
                  <div className="py-12 text-center">
                    <Activity className="h-10 w-10 mx-auto mb-3 text-white/20" />
                    <p className="text-white/40">No data yet - run the pipeline to generate predictions</p>
                  </div>
                )}
              </CardContent>
            </Card>

            {/* Section 5: Feature Drift Analysis */}
            <Card className="glass-card border-0">
              <CardHeader className="pb-3">
                <div className="flex items-center justify-between flex-wrap gap-3">
                  <div>
                    <CardTitle className="text-base font-syne text-white flex items-center gap-2">
                      <AlertTriangle className="h-4 w-4 text-racing-orange" />
                      Feature Drift Analysis
                    </CardTitle>
                    <p className="text-xs text-white/35 mt-1">
                      Compare training vs live inference feature distributions
                    </p>
                  </div>
                  <Button
                    variant="default"
                    size="sm"
                    onClick={() => setDriftEnabled(true)}
                    disabled={driftFetching}
                    data-testid="button-run-drift"
                  >
                    <RefreshCw className={`h-4 w-4 mr-2 ${driftFetching ? 'animate-spin' : ''}`} />
                    {driftFetching ? 'Analyzing...' : 'Run Drift Analysis'}
                  </Button>
                </div>
              </CardHeader>
              <CardContent>
                {driftFetching ? (
                  <div className="space-y-3">
                    <Skeleton className="h-8 w-48" />
                    <Skeleton className="h-48 w-full" />
                  </div>
                ) : driftData ? (
                  <div className="space-y-4">
                    <div className="flex items-center gap-4 flex-wrap">
                      <div className="flex items-center gap-2">
                        <Target className="h-4 w-4 text-racing-orange" />
                        <span className="text-sm text-white/60">Parity Score:</span>
                        <span className={`text-lg font-bold ${driftData.parityScore >= 0.8 ? 'text-green-400' : driftData.parityScore >= 0.5 ? 'text-amber-400' : 'text-red-400'}`} data-testid="text-parity-score">
                          {(driftData.parityScore * 100).toFixed(1)}%
                        </span>
                      </div>
                      <Badge className={
                        driftData.parityScore >= 0.8
                          ? 'bg-green-500/20 text-green-400 border-green-500/30'
                          : driftData.parityScore >= 0.5
                          ? 'bg-amber-500/20 text-amber-400 border-amber-500/30'
                          : 'bg-red-500/20 text-red-400 border-red-500/30'
                      }>
                        {driftData.parityScore >= 0.8 ? 'Healthy' : driftData.parityScore >= 0.5 ? 'Warning' : 'Critical'}
                      </Badge>
                    </div>

                    {driftData.anomalies && driftData.anomalies.length > 0 && (
                      <div>
                        <h4 className="text-sm font-semibold text-white/60 mb-2 flex items-center gap-2">
                          <AlertTriangle className="h-3.5 w-3.5 text-amber-400" />
                          Top Anomalies
                        </h4>
                        <div className="overflow-x-auto">
                          <table className="w-full text-sm">
                            <thead>
                              <tr className="border-b border-white/[0.06]">
                                <th className="text-left py-2.5 px-2 text-white/35 font-medium text-xs uppercase tracking-wider">Feature</th>
                                <th className="text-right py-2.5 px-2 text-white/35 font-medium text-xs uppercase tracking-wider">NaN Ratio</th>
                                <th className="text-right py-2.5 px-2 text-white/35 font-medium text-xs uppercase tracking-wider">Zero Ratio</th>
                                <th className="text-center py-2.5 px-2 text-white/35 font-medium text-xs uppercase tracking-wider">Range</th>
                                <th className="text-right py-2.5 px-2 text-white/35 font-medium text-xs uppercase tracking-wider">Drift Score</th>
                              </tr>
                            </thead>
                            <tbody>
                              {driftData.anomalies.map((feat, idx) => (
                                <tr key={feat.feature} className={`${idx % 2 === 0 ? 'bg-white/[0.02]' : ''} hover:bg-white/[0.04] transition-colors`} data-testid={`row-drift-${idx}`}>
                                  <td className="py-2.5 px-2 font-medium text-white/80 font-mono text-xs">{feat.feature}</td>
                                  <td className={`py-2.5 px-2 text-right ${feat.nan_ratio > 0.1 ? 'text-red-400' : 'text-white/60'}`}>
                                    {(feat.nan_ratio * 100).toFixed(1)}%
                                  </td>
                                  <td className={`py-2.5 px-2 text-right ${feat.zero_ratio > 0.5 ? 'text-amber-400' : 'text-white/60'}`}>
                                    {(feat.zero_ratio * 100).toFixed(1)}%
                                  </td>
                                  <td className="py-2.5 px-2 text-center">
                                    {feat.range_violation ? (
                                      <XCircle className="h-4 w-4 text-red-400 mx-auto" />
                                    ) : (
                                      <CheckCircle className="h-4 w-4 text-green-400 mx-auto" />
                                    )}
                                  </td>
                                  <td className={`py-2.5 px-2 text-right font-medium ${feat.drift_score > 0.5 ? 'text-red-400' : feat.drift_score > 0.2 ? 'text-amber-400' : 'text-green-400'}`}>
                                    {feat.drift_score.toFixed(3)}
                                  </td>
                                </tr>
                              ))}
                            </tbody>
                          </table>
                        </div>
                      </div>
                    )}

                    {driftData.anomalies && driftData.anomalies.length === 0 && (
                      <div className="py-6 text-center">
                        <CheckCircle className="h-8 w-8 mx-auto mb-2 text-green-400" />
                        <p className="text-white/40">No significant feature drift detected</p>
                      </div>
                    )}
                  </div>
                ) : !driftEnabled ? (
                  <div className="py-12 text-center">
                    <AlertTriangle className="h-10 w-10 mx-auto mb-3 text-white/20" />
                    <p className="text-white/40">Click "Run Drift Analysis" to compare training vs inference features</p>
                  </div>
                ) : (
                  <div className="py-12 text-center">
                    <Activity className="h-10 w-10 mx-auto mb-3 text-white/20" />
                    <p className="text-white/40">No data yet - run the pipeline to generate predictions</p>
                  </div>
                )}
              </CardContent>
            </Card>
          </>
        )}
      </main>
    </div>
  );
}
