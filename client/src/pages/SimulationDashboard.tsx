import { useState, useMemo } from "react";
import { Link } from "wouter";
import { ArrowLeft, Play, Loader2, TrendingUp, BarChart3, Users, MapPin } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Slider } from "@/components/ui/slider";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { Progress } from "@/components/ui/progress";
import { Badge } from "@/components/ui/badge";
import { useMutation, useQuery } from "@tanstack/react-query";
import { apiRequest } from "@/lib/queryClient";
import { BarChart, Bar, XAxis, YAxis, Tooltip, ResponsiveContainer, Cell } from "recharts";
import { Skeleton } from "@/components/ui/skeleton";

interface Runner {
  horse_id: string;
  horse: string;
  number: string;
  barrier: string;
  jockey: string;
  trainer: string;
  weight: string;
  form: string;
  odds?: { bookmaker: string; win_odds: string; place_odds: string }[];
}

interface Race {
  race_id: string;
  race_number: string;
  race_name: string;
  distance: string;
  class: string;
  going: string;
  runnerCount: number;
  runners: Runner[];
}

interface Meet {
  date: string;
  course: string;
  meet_id: string;
  races: Race[];
}

interface SimulationRacesResponse {
  total: number;
  meets: Meet[];
  dataSource?: string;
  currentDate?: string;
}

interface SimulationHorse {
  name: string;
  number?: string;
  barrier?: string;
  jockey?: string;
  trainer?: string;
  form?: string;
  winPercentage: number;
  placePercentage: number;
  impliedOdds: number;
  marketOdds?: number;
  edge?: number;
  kellyStake?: number;
  confidence?: string;
  valueRating: "high" | "medium" | "low";
}

interface SimulationResult {
  horses: SimulationHorse[];
  convergenceData: Array<{ iteration: number; variance: number }>;
  metadata?: {
    track: string;
    race: string;
    iterations: number;
    runnerCount: number;
    model: string;
    timestamp: string;
  };
}

export default function SimulationDashboard() {
  const [selectedMeet, setSelectedMeet] = useState("");
  const [selectedRace, setSelectedRace] = useState("");
  const [simulations, setSimulations] = useState([10000]);
  const [results, setResults] = useState<SimulationResult | null>(null);

  const { data: racesData, isLoading: isLoadingRaces } = useQuery<SimulationRacesResponse>({
    queryKey: ["/api/simulation/races"],
  });

  const currentMeet = useMemo(() => {
    if (!racesData || !selectedMeet) return null;
    return racesData.meets.find(m => m.meet_id === selectedMeet);
  }, [racesData, selectedMeet]);

  const currentRace = useMemo(() => {
    if (!currentMeet || !selectedRace) return null;
    return currentMeet.races.find(r => r.race_number === selectedRace);
  }, [currentMeet, selectedRace]);

  const runSimulation = useMutation({
    mutationFn: async () => {
      if (!currentMeet || !currentRace) {
        throw new Error("Please select a meet and race");
      }
      
      const response = await apiRequest("POST", "/api/simulations", {
        track: currentMeet.course,
        race: currentRace.race_number,
        iterations: simulations[0],
        runners: currentRace.runners,
      });
      return response.json();
    },
    onSuccess: (data) => {
      setResults(data);
    },
  });

  const getValueColor = (rating: string) => {
    switch (rating) {
      case "high":
        return "text-success";
      case "medium":
        return "text-warning";
      case "low":
        return "text-danger";
      default:
        return "text-white/40";
    }
  };

  const getBarColor = (percentage: number) => {
    if (percentage >= 20) return "hsl(30, 100%, 50%)";
    if (percentage >= 10) return "hsl(43, 74%, 49%)";
    return "hsl(var(--muted-foreground))";
  };

  const handleMeetChange = (meetId: string) => {
    setSelectedMeet(meetId);
    setSelectedRace("");
    setResults(null);
  };

  const handleRaceChange = (raceNum: string) => {
    setSelectedRace(raceNum);
    setResults(null);
  };

  return (
    <div className="min-h-screen bg-black font-dm" data-testid="page-simulations">
      <header className="page-header-liquid px-4">
        <div className="max-w-7xl mx-auto flex items-center gap-4">
          <Link href="/">
            <Button variant="ghost" size="icon" data-testid="button-back">
              <ArrowLeft className="h-5 w-5" />
            </Button>
          </Link>
          <div className="flex items-center gap-2">
            <BarChart3 className="h-5 w-5 text-racing-orange" />
            <h1 className="text-lg font-syne font-extrabold tracking-tight text-white">MONTE CARLO SIMULATIONS</h1>
          </div>
        </div>
      </header>

      <main className="max-w-7xl mx-auto p-4 sm:p-6 space-y-6">
        <Card className="glass-card border-0">
          <CardHeader>
            <CardTitle className="font-syne text-lg font-bold tracking-wide">SIMULATION PARAMETERS</CardTitle>
          </CardHeader>
          <CardContent className="space-y-6">
            {isLoadingRaces ? (
              <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-4">
                <Skeleton className="h-10 w-full" />
                <Skeleton className="h-10 w-full" />
                <Skeleton className="h-10 w-full" />
              </div>
            ) : racesData && racesData.meets.length > 0 ? (
              <>
                {racesData.dataSource && (
                  <div className="flex items-center gap-2 mb-4">
                    <Badge 
                      variant="outline" 
                      className={racesData.dataSource === 'live' 
                        ? "bg-success/20 text-success border-success/30" 
                        : "bg-racing-orange/20 text-racing-orange border-racing-orange/30"
                      }
                    >
                      {racesData.dataSource === 'live' ? 'LIVE API DATA' : 'UPCOMING RACES'}
                    </Badge>
                    <span className="text-sm text-white/40">
                      {racesData.total} meetings available from {racesData.currentDate}
                    </span>
                  </div>
                )}
                <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-4">
                  <div className="space-y-2">
                    <label className="text-sm text-white/35 uppercase tracking-wide">Meeting</label>
                    <Select value={selectedMeet} onValueChange={handleMeetChange}>
                      <SelectTrigger className="glass-input" data-testid="select-track">
                        <SelectValue placeholder="Select Meeting" />
                      </SelectTrigger>
                      <SelectContent>
                        {racesData.meets.map((meet) => (
                          <SelectItem key={meet.meet_id} value={meet.meet_id}>
                            <span className="flex items-center gap-2">
                              <MapPin className="h-3 w-3" />
                              {meet.course} - {meet.date}
                            </span>
                          </SelectItem>
                        ))}
                      </SelectContent>
                    </Select>
                  </div>

                  <div className="space-y-2">
                    <label className="text-sm text-white/35 uppercase tracking-wide">Race</label>
                    <Select 
                      value={selectedRace} 
                      onValueChange={handleRaceChange}
                      disabled={!currentMeet}
                    >
                      <SelectTrigger className="glass-input" data-testid="select-race">
                        <SelectValue placeholder={currentMeet ? "Select Race" : "Select meeting first"} />
                      </SelectTrigger>
                      <SelectContent>
                        {currentMeet?.races.map((race) => (
                          <SelectItem key={race.race_number} value={race.race_number}>
                            <span className="flex items-center gap-2">
                              R{race.race_number} - {race.race_name} ({race.distance})
                              <Badge variant="outline" className="ml-2 text-xs">
                                {race.runnerCount} runners
                              </Badge>
                            </span>
                          </SelectItem>
                        ))}
                      </SelectContent>
                    </Select>
                  </div>

                  <div className="space-y-2 sm:col-span-2 lg:col-span-1">
                    <label className="text-sm text-white/35 uppercase tracking-wide">
                      Simulations: {simulations[0].toLocaleString()}
                    </label>
                    <Slider
                      value={simulations}
                      onValueChange={setSimulations}
                      min={1000}
                      max={100000}
                      step={1000}
                      className="py-2"
                      data-testid="slider-simulations"
                    />
                  </div>
                </div>

                {currentRace && (
                  <Card className="glass-card-sm border-0">
                    <CardContent className="py-4">
                      <div className="flex items-center justify-between flex-wrap gap-4">
                        <div>
                          <h3 className="font-bold text-lg">{currentRace.race_name}</h3>
                          <div className="flex items-center gap-4 text-sm text-white/40 mt-1">
                            <span>{currentRace.distance}</span>
                            {currentRace.class && <span>{currentRace.class}</span>}
                            {currentRace.going && <span>Going: {currentRace.going}</span>}
                          </div>
                        </div>
                        <div className="flex items-center gap-2">
                          <Users className="h-4 w-4 text-racing-orange" />
                          <span className="font-bold">{currentRace.runnerCount} Runners</span>
                        </div>
                      </div>
                    </CardContent>
                  </Card>
                )}
              </>
            ) : (
              <div className="text-center py-8">
                <p className="text-white/40">No races with runners available</p>
                <p className="text-sm text-white/40 mt-2">Races need declared runners to run simulations</p>
              </div>
            )}

            <Button
              size="lg"
              onClick={() => runSimulation.mutate()}
              disabled={!currentRace || runSimulation.isPending}
              className="w-full sm:w-auto font-bold tracking-widest"
              data-testid="button-run-simulation"
            >
              {runSimulation.isPending ? (
                <>
                  <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                  RUNNING...
                </>
              ) : (
                <>
                  <Play className="mr-2 h-4 w-4" />
                  RUN SIMULATION
                </>
              )}
            </Button>
          </CardContent>
        </Card>

        {runSimulation.isPending && (
          <Card className="glass-card border-0">
            <CardContent className="py-8">
              <div className="flex flex-col items-center gap-4">
                <Loader2 className="h-8 w-8 animate-spin text-racing-orange" />
                <p className="text-white/40">Running {simulations[0].toLocaleString()} simulations...</p>
                <Progress value={65} className="w-full max-w-md h-2" />
              </div>
            </CardContent>
          </Card>
        )}

        {results && !runSimulation.isPending && (
          <>
            <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
              <Card className="glass-card border-0">
                <CardHeader>
                  <CardTitle className="font-syne text-lg font-bold tracking-wide flex items-center gap-2">
                    <TrendingUp className="h-5 w-5 text-racing-orange" />
                    WIN PROBABILITY DISTRIBUTION
                  </CardTitle>
                </CardHeader>
                <CardContent>
                  <ResponsiveContainer width="100%" height={300}>
                    <BarChart data={results.horses.slice(0, 10)} layout="vertical">
                      <XAxis type="number" domain={[0, 'auto']} tickFormatter={(v) => `${v.toFixed(0)}%`} />
                      <YAxis 
                        type="category" 
                        dataKey="name" 
                        width={120} 
                        tick={{ fill: "hsl(var(--foreground))", fontSize: 11 }} 
                        tickFormatter={(v) => v.length > 14 ? v.slice(0, 14) + '...' : v}
                      />
                      <Tooltip
                        contentStyle={{
                          backgroundColor: "rgba(0,0,0,0.8)",
                          border: "1px solid rgba(255,255,255,0.08)",
                          borderRadius: "6px",
                        }}
                        formatter={(value: number) => [`${value.toFixed(2)}%`, "Win %"]}
                      />
                      <Bar dataKey="winPercentage" radius={[0, 4, 4, 0]}>
                        {results.horses.slice(0, 10).map((entry, index) => (
                          <Cell key={`cell-${index}`} fill={getBarColor(entry.winPercentage)} />
                        ))}
                      </Bar>
                    </BarChart>
                  </ResponsiveContainer>
                </CardContent>
              </Card>

              <Card className="glass-card border-0">
                <CardHeader>
                  <CardTitle className="font-syne text-lg font-bold tracking-wide flex items-center gap-2">
                    <BarChart3 className="h-5 w-5 text-racing-gold" />
                    MONTE CARLO CONVERGENCE
                  </CardTitle>
                </CardHeader>
                <CardContent>
                  <ResponsiveContainer width="100%" height={300}>
                    <BarChart data={results.convergenceData}>
                      <XAxis dataKey="iteration" tickFormatter={(v) => `${(v / 1000).toFixed(0)}k`} />
                      <YAxis tickFormatter={(v) => `${v.toFixed(1)}`} />
                      <Tooltip
                        contentStyle={{
                          backgroundColor: "rgba(0,0,0,0.8)",
                          border: "1px solid rgba(255,255,255,0.08)",
                          borderRadius: "6px",
                        }}
                      />
                      <Bar dataKey="variance" fill="hsl(30, 100%, 50%)" radius={[4, 4, 0, 0]} />
                    </BarChart>
                  </ResponsiveContainer>
                </CardContent>
              </Card>
            </div>

            <Card className="glass-card border-0">
              <CardHeader>
                <div className="flex items-start justify-between flex-wrap gap-4">
                  <CardTitle className="font-syne text-lg font-bold tracking-wide">
                    SIMULATION RESULTS - {currentMeet?.course} R{currentRace?.race_number}
                  </CardTitle>
                  {results.metadata && (
                    <div className="flex items-center gap-2">
                      <Badge variant="outline" className="text-xs">
                        {results.metadata.model.toUpperCase()} MODEL
                      </Badge>
                      <Badge variant="outline" className="text-xs">
                        {results.metadata.iterations.toLocaleString()} ITERATIONS
                      </Badge>
                    </div>
                  )}
                </div>
              </CardHeader>
              <CardContent>
                <div className="overflow-x-auto">
                  <Table>
                    <TableHeader>
                      <TableRow className="border-border hover:bg-transparent">
                        <TableHead className="text-white/35 uppercase tracking-wide text-xs">#</TableHead>
                        <TableHead className="text-white/35 uppercase tracking-wide text-xs">Horse</TableHead>
                        <TableHead className="text-white/35 uppercase tracking-wide text-xs">Jockey</TableHead>
                        <TableHead className="text-white/35 uppercase tracking-wide text-xs">Form</TableHead>
                        <TableHead className="text-white/35 uppercase tracking-wide text-xs text-right">Win %</TableHead>
                        <TableHead className="text-white/35 uppercase tracking-wide text-xs text-right">Place %</TableHead>
                        <TableHead className="text-white/35 uppercase tracking-wide text-xs text-right">Implied</TableHead>
                        <TableHead className="text-white/35 uppercase tracking-wide text-xs text-right">Edge</TableHead>
                        <TableHead className="text-white/35 uppercase tracking-wide text-xs text-right">Kelly</TableHead>
                        <TableHead className="text-white/35 uppercase tracking-wide text-xs text-right">Value</TableHead>
                      </TableRow>
                    </TableHeader>
                    <TableBody>
                      {results.horses.map((horse, index) => (
                        <TableRow key={index} className="border-border" data-testid={`row-horse-${index}`}>
                          <TableCell className="font-mono text-white/40">{horse.barrier || index + 1}</TableCell>
                          <TableCell className="font-medium">{horse.name}</TableCell>
                          <TableCell className="text-sm text-white/40">{horse.jockey || '-'}</TableCell>
                          <TableCell className="font-mono text-sm">{horse.form || '-'}</TableCell>
                          <TableCell className="text-right font-mono font-bold">{horse.winPercentage.toFixed(2)}%</TableCell>
                          <TableCell className="text-right font-mono">{horse.placePercentage.toFixed(1)}%</TableCell>
                          <TableCell className="text-right font-mono">${horse.impliedOdds.toFixed(2)}</TableCell>
                          <TableCell className={`text-right font-mono ${(horse.edge || 0) > 0 ? 'text-success' : 'text-white/40'}`}>
                            {horse.edge ? `${horse.edge > 0 ? '+' : ''}${horse.edge.toFixed(1)}%` : '-'}
                          </TableCell>
                          <TableCell className="text-right font-mono text-racing-orange">
                            {horse.kellyStake ? `${horse.kellyStake.toFixed(2)}%` : '-'}
                          </TableCell>
                          <TableCell className={`text-right font-bold uppercase ${getValueColor(horse.valueRating)}`}>
                            {horse.valueRating}
                          </TableCell>
                        </TableRow>
                      ))}
                    </TableBody>
                  </Table>
                </div>
              </CardContent>
            </Card>
          </>
        )}
      </main>
    </div>
  );
}
