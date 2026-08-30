export type RaceSpeedMapTempo = "HOT" | "GENUINE" | "SOFT" | "UNKNOWN";
export type RaceSpeedMapZone =
  | "leader"
  | "pace"
  | "off_pace"
  | "midfield"
  | "off_midfield"
  | "backmarker";
export type RaceSpeedMapPlacementSource = "historical" | "comment" | "fallback";

export interface RaceSpeedMapRunner {
  horse: string;
  horseId: string | null;
  saddleNumber: number | null;
  barrier: number | null;
  jockey: string | null;
  trainer: string | null;
  silkUrl: string | null;
  colour: string | null;
  sex: string | null;
  age: string | null;
  comment: string | null;
  marketOdds: number | null;
  isTipped: boolean;
  tipRank: number | null;
  isHighlighted: boolean;
  predictedSettlingPosition: number;
  settlingPercentile: number;
  zone: RaceSpeedMapZone;
  lane: number;
  stackRow: number;
  placementSource: RaceSpeedMapPlacementSource;
  placementReason: string;
}

export interface RaceSpeedMap {
  track: string;
  raceNumber: number;
  raceName: string;
  distance: string;
  going: string;
  fieldSize: number;
  tempoLabel: RaceSpeedMapTempo;
  tempoReason: string;
  highlightedHorse: string | null;
  leaders: number;
  onPace: number;
  midfield: number;
  closers: number;
  narrative: string;
  runners: RaceSpeedMapRunner[];
}
