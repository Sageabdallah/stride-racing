import { Navbar } from "@/components/Navbar";
import { AmbientEffects } from "@/components/AmbientEffects";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { useQuery } from "@tanstack/react-query";
import { Skeleton } from "@/components/ui/skeleton";
import { TrendingUp, Award, Users, MapPin, Calendar, ChevronDown, ChevronUp } from "lucide-react";
import { useState } from "react";
import { Button } from "@/components/ui/button";

interface Meet {
  course: string;
  date: string;
  meet_id: string;
  races: Race[];
}

interface Runner {
  horse: string;
  horse_id?: string;
  number?: number;
  tab_no?: number;
  barrier?: number;
  draw?: number;
  jockey?: string;
  trainer?: string;
  weight?: number;
  form?: string;
  age?: string;
  sex?: string;
  sire?: string;
  dam_sire?: string;
}

interface Race {
  race_id?: string;
  race_number?: number;
  race_name?: string;
  distance?: number;
  class?: string;
  going?: string;
  prize?: number;
  off_time?: string;
  runners?: Runner[];
}

interface RacecardData {
  date: string;
  meets: Meet[];
  meetCount: number;
  raceCount: number;
}

interface LocalRacecardsResponse {
  total: number;
  racecards: { date: string; meets: Meet[]; meetCount: number; raceCount: number }[];
}

export default function FormGuide() {
  const [expandedRaces, setExpandedRaces] = useState<Set<string>>(new Set());
  const [selectedMeet, setSelectedMeet] = useState<number>(0);

  const { data, isLoading } = useQuery<LocalRacecardsResponse>({
    queryKey: ["/api/local/racecards"],
  });

  const toggleRace = (raceKey: string) => {
    setExpandedRaces(prev => {
      const next = new Set(prev);
      if (next.has(raceKey)) {
        next.delete(raceKey);
      } else {
        next.add(raceKey);
      }
      return next;
    });
  };

  const allMeets = data?.racecards.flatMap(rc => 
    rc.meets.map(m => ({ ...m, cardDate: rc.date }))
  ) || [];

  const currentMeet = allMeets[selectedMeet];

  return (
    <div className="min-h-screen bg-black font-dm" data-testid="page-form-guide">
      <AmbientEffects />
      <Navbar />
      
      <main className="relative z-[1] max-w-7xl mx-auto px-4 sm:px-6 pt-24 pb-12">
        <div className="mb-8">
          <h1 className="text-3xl sm:text-4xl page-title mb-2" data-testid="text-title">FORM GUIDE</h1>
          <p className="text-white/40">Comprehensive form analysis for upcoming races</p>
        </div>

        {isLoading ? (
          <Card className="glass-card border-0">
            <CardHeader>
              <Skeleton className="h-6 w-64" />
            </CardHeader>
            <CardContent className="space-y-4">
              {[1, 2, 3, 4, 5].map((i) => (
                <div key={i} className="flex items-center gap-4 p-4 bg-white/[0.03] rounded-md">
                  <Skeleton className="h-8 w-8 rounded-full" />
                  <div className="flex-1 space-y-2">
                    <Skeleton className="h-5 w-40" />
                    <Skeleton className="h-4 w-64" />
                  </div>
                  <Skeleton className="h-6 w-16" />
                </div>
              ))}
            </CardContent>
          </Card>
        ) : allMeets.length > 0 ? (
          <div className="space-y-6">
            <div className="flex flex-wrap gap-2">
              {allMeets.slice(0, 10).map((meet, idx) => (
                <Button
                  key={idx}
                  variant={selectedMeet === idx ? "default" : "outline"}
                  size="sm"
                  onClick={() => setSelectedMeet(idx)}
                  className="tracking-wide"
                  data-testid={`button-meet-${idx}`}
                >
                  {meet.course}
                </Button>
              ))}
              {allMeets.length > 10 && (
                <Badge variant="outline" className="px-3 py-1">
                  +{allMeets.length - 10} more
                </Badge>
              )}
            </div>

            {currentMeet && (
              <Card className="glass-card border-0">
                <CardHeader>
                  <div className="flex items-start justify-between flex-wrap gap-4">
                    <div>
                      <CardTitle className="text-xl font-bold tracking-wide mb-1 flex items-center gap-2 font-syne text-white">
                        <MapPin className="h-5 w-5 text-racing-orange" />
                        {currentMeet.course}
                      </CardTitle>
                      <div className="flex items-center gap-2 text-white/40">
                        <Calendar className="h-4 w-4" />
                        <span>{currentMeet.date}</span>
                        <span className="mx-2">-</span>
                        <span>{currentMeet.races?.length || 0} races</span>
                      </div>
                    </div>
                    <Badge variant="outline" className="bg-racing-orange/20 text-racing-orange border-racing-orange/30">
                      LIVE DATA
                    </Badge>
                  </div>
                </CardHeader>
                <CardContent>
                  <div className="space-y-4">
                    {currentMeet.races?.map((race, raceIdx) => {
                      const raceKey = `${currentMeet.meet_id}-${race.race_number || raceIdx}`;
                      const isExpanded = expandedRaces.has(raceKey);
                      
                      return (
                        <div key={raceKey} className="border border-white/[0.06] rounded-lg overflow-hidden">
                          <button
                            onClick={() => toggleRace(raceKey)}
                            className="w-full flex items-center justify-between p-4 bg-white/[0.03] hover:bg-white/[0.06] transition-colors text-left"
                            data-testid={`button-race-${raceIdx}`}
                          >
                            <div className="flex items-center gap-4">
                              <div className="flex items-center justify-center w-10 h-10 rounded-full bg-racing-orange text-background font-bold">
                                R{race.race_number || raceIdx + 1}
                              </div>
                              <div>
                                <h3 className="font-bold text-white">{race.race_name || `Race ${race.race_number || raceIdx + 1}`}</h3>
                                <div className="flex flex-wrap gap-x-4 gap-y-1 text-sm text-white/35">
                                  <span>{race.distance}m</span>
                                  {race.class && <span>{race.class}</span>}
                                  {race.going && <span>{race.going}</span>}
                                </div>
                              </div>
                            </div>
                            <div className="flex items-center gap-3">
                              <div className="flex items-center gap-1 text-sm text-white/35">
                                <Users className="h-4 w-4" />
                                <span>{race.runners?.length || 0}</span>
                              </div>
                              {isExpanded ? (
                                <ChevronUp className="h-5 w-5" />
                              ) : (
                                <ChevronDown className="h-5 w-5" />
                              )}
                            </div>
                          </button>
                          
                          {isExpanded && race.runners && (
                            <div className="p-4 space-y-2 bg-black/40">
                              {race.runners.map((runner, runnerIdx) => (
                                <div
                                  key={runner.horse_id || runnerIdx}
                                  className="flex items-center gap-4 p-3 bg-white/[0.03] rounded-md hover-elevate transition-all"
                                  data-testid={`row-runner-${runnerIdx}`}
                                >
                                  <div className="flex items-center justify-center w-8 h-8 rounded-full bg-white/[0.06] text-sm font-bold">
                                    {runner.barrier || runner.draw || runner.tab_no || runnerIdx + 1}
                                  </div>
                                  
                                  <div className="flex-1 min-w-0">
                                    <div className="flex items-center gap-2 mb-1">
                                      <h4 className="font-bold truncate text-white">{runner.horse}</h4>
                                      {runnerIdx === 0 && <Award className="h-4 w-4 text-racing-gold" />}
                                    </div>
                                    <div className="flex flex-wrap gap-x-4 gap-y-1 text-xs text-white/35">
                                      {runner.trainer && <span>T: {runner.trainer}</span>}
                                      {runner.jockey && <span>J: {runner.jockey}</span>}
                                      {runner.weight && <span>{runner.weight}kg</span>}
                                    </div>
                                  </div>

                                  <div className="text-right shrink-0">
                                    {runner.form && (
                                      <div className="font-mono text-sm bg-white/[0.06] px-2 py-1 rounded">
                                        {runner.form}
                                      </div>
                                    )}
                                  </div>
                                </div>
                              ))}
                            </div>
                          )}
                        </div>
                      );
                    })}
                  </div>
                </CardContent>
              </Card>
            )}
          </div>
        ) : (
          <Card className="glass-card border-0">
            <CardContent className="py-12 text-center">
              <p className="text-white/40">No form guide data available</p>
              <p className="text-sm text-white/40 mt-2">Racing data will appear here when racecards are loaded</p>
            </CardContent>
          </Card>
        )}
      </main>
    </div>
  );
}
