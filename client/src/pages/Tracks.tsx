import { MapPin, Calendar, Clock, Loader2, ChevronRight } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Navbar } from "@/components/Navbar";
import { AmbientEffects } from "@/components/AmbientEffects";
import { useQuery } from "@tanstack/react-query";
import { useState } from "react";

interface Meet {
  course: string;
  date: string;
  meet_id: string;
  races: any[];
}

interface RacecardData {
  filename: string;
  date: string;
  meets: Meet[];
  meetCount: number;
  raceCount: number;
}

interface LocalRacecardsResponse {
  total: number;
  racecards: RacecardData[];
}

const FEATURED_TRACKS = ["flemington", "randwick", "moonee valley", "caulfield", "eagle farm"];

export default function Tracks() {
  const [selectedDate, setSelectedDate] = useState<string | null>(null);
  
  const { data, isLoading, error } = useQuery<LocalRacecardsResponse>({
    queryKey: ["/api/local/racecards"],
  });

  const aggregatedTracks = data?.racecards.reduce((acc: Record<string, { 
    track: string; 
    totalRaces: number; 
    dates: string[];
    meets: Meet[];
  }>, racecard) => {
    racecard.meets.forEach(meet => {
      const trackKey = meet.course.toLowerCase();
      if (!acc[trackKey]) {
        acc[trackKey] = {
          track: meet.course,
          totalRaces: 0,
          dates: [],
          meets: []
        };
      }
      acc[trackKey].totalRaces += meet.races?.length || 0;
      if (!acc[trackKey].dates.includes(racecard.date)) {
        acc[trackKey].dates.push(racecard.date);
      }
      acc[trackKey].meets.push(meet);
    });
    return acc;
  }, {}) || {};

  const tracksList = Object.values(aggregatedTracks).sort((a, b) => b.totalRaces - a.totalRaces);

  return (
    <div className="min-h-screen bg-black font-dm" data-testid="page-tracks">
      <AmbientEffects />
      <Navbar />
      
      <main className="relative z-[1] max-w-7xl mx-auto px-4 sm:px-6 pt-24 pb-12">
        <div className="mb-8">
          <h1 className="text-3xl sm:text-4xl page-title mb-2" data-testid="text-title">RACETRACKS</h1>
          <p className="text-white/40">Australian premier racing venues with live data</p>
          {data && (
            <p className="text-sm text-racing-orange mt-2">
              {data.total} days of racecards loaded with {tracksList.length} tracks
            </p>
          )}
        </div>

        {isLoading ? (
          <div className="flex items-center justify-center py-20">
            <Loader2 className="h-8 w-8 animate-spin text-racing-orange" />
            <span className="ml-3 text-white/40">Loading track data...</span>
          </div>
        ) : error ? (
          <Card className="glass-card border-0">
            <CardContent className="py-12 text-center">
              <p className="text-danger">Failed to load track data</p>
            </CardContent>
          </Card>
        ) : (
          <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-6">
            {tracksList.map((track, index) => {
              const isFeatured = FEATURED_TRACKS.some(ft => 
                track.track.toLowerCase().includes(ft)
              );
              const nextDate = track.dates.sort()[0];
              
              return (
                <Card 
                  key={track.track} 
                  className="glass-card border-0 hover-elevate transition-all" 
                  data-testid={`card-track-${index}`}
                >
                  <CardContent className="p-6 space-y-4">
                    <div className="flex items-start justify-between gap-2">
                      <div>
                        <h3 className="text-xl font-bold mb-1 text-white">{track.track}</h3>
                        <div className="flex items-center gap-1 text-white/35 text-sm">
                          <MapPin className="h-3 w-3" />
                          <span>Australia</span>
                        </div>
                      </div>
                      {isFeatured && (
                        <Badge className="bg-racing-gold/20 text-racing-gold border-racing-gold/30 shrink-0">
                          FEATURED
                        </Badge>
                      )}
                    </div>

                    <div className="flex items-center gap-4 text-sm flex-wrap">
                      <div className="flex items-center gap-1.5">
                        <Calendar className="h-4 w-4 text-racing-orange" />
                        <span>{track.dates.length} meeting{track.dates.length > 1 ? 's' : ''}</span>
                      </div>
                      <div className="flex items-center gap-1.5">
                        <Clock className="h-4 w-4 text-white/35" />
                        <span>{track.totalRaces} races</span>
                      </div>
                    </div>

                    <div className="pt-4 border-t border-white/[0.06] flex items-center justify-between gap-2">
                      <div>
                        <p className="text-xs text-white/35 uppercase tracking-wide mb-1">Next Meeting</p>
                        <p className="font-medium text-sm">{nextDate}</p>
                      </div>
                      <Button 
                        variant="outline" 
                        size="sm" 
                        className="tracking-wide shrink-0" 
                        data-testid={`button-view-races-${index}`}
                        onClick={() => setSelectedDate(nextDate)}
                      >
                        VIEW
                        <ChevronRight className="h-4 w-4 ml-1" />
                      </Button>
                    </div>
                  </CardContent>
                </Card>
              );
            })}
          </div>
        )}

        {selectedDate && (
          <RacecardModal 
            date={selectedDate} 
            onClose={() => setSelectedDate(null)} 
          />
        )}
      </main>
    </div>
  );
}

function RacecardModal({ date, onClose }: { date: string; onClose: () => void }) {
  const { data, isLoading } = useQuery<{
    date: string;
    meets: Meet[];
    meetCount: number;
    raceCount: number;
  }>({
    queryKey: ["/api/local/racecards", date],
  });

  return (
    <div className="fixed inset-0 bg-black/80 backdrop-blur-sm z-50 flex items-center justify-center p-4" onClick={onClose}>
      <Card className="glass-card border-0 max-w-4xl w-full max-h-[80vh] overflow-hidden" onClick={e => e.stopPropagation()}>
        <CardContent className="p-6">
          <div className="flex items-center justify-between mb-6">
            <h2 className="text-2xl font-bold tracking-wide">Racecard - {date}</h2>
            <Button variant="ghost" size="sm" onClick={onClose}>Close</Button>
          </div>
          
          {isLoading ? (
            <div className="flex items-center justify-center py-12">
              <Loader2 className="h-6 w-6 animate-spin text-racing-orange" />
            </div>
          ) : data ? (
            <div className="space-y-6 max-h-[60vh] overflow-y-auto">
              {data.meets.map((meet, idx) => (
                <div key={idx} className="border border-white/[0.06] rounded-lg p-4">
                  <h3 className="text-lg font-bold text-racing-orange mb-3">{meet.course}</h3>
                  <div className="space-y-2">
                    {meet.races?.slice(0, 5).map((race: any, rIdx: number) => (
                      <div key={rIdx} className="flex items-center justify-between p-3 bg-white/[0.04] rounded-md">
                        <div>
                          <span className="font-medium">Race {race.race_number || rIdx + 1}</span>
                          <span className="text-white/40 ml-2 text-sm">{race.race_name}</span>
                        </div>
                        <div className="text-sm text-white/40">
                          {race.distance}m - {race.runners?.length || 0} runners
                        </div>
                      </div>
                    ))}
                    {meet.races?.length > 5 && (
                      <p className="text-sm text-white/40 text-center py-2">
                        +{meet.races.length - 5} more races
                      </p>
                    )}
                  </div>
                </div>
              ))}
            </div>
          ) : (
            <p className="text-white/40 text-center py-8">No data available</p>
          )}
        </CardContent>
      </Card>
    </div>
  );
}
