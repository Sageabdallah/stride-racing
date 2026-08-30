import { db } from "./db";
import { selections, trainingData, races, type InsertTrainingData } from "@shared/schema";
import { eq, lt, and, isNull } from "drizzle-orm";

const RACING_API_BASE = 'https://api.theracingapi.com';
const RACING_API_USERNAME = process.env.RACING_API_USERNAME;
const RACING_API_PASSWORD = process.env.RACING_API_PASSWORD || '';

function getRacingAPIHeaders(): HeadersInit {
  const apiKey = process.env.THE_RACING_API_KEY;
  if (apiKey) {
    return {
      'Authorization': `Bearer ${apiKey}`,
      'Content-Type': 'application/json',
      'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    };
  }

  if (RACING_API_USERNAME) {
    const credentials = Buffer.from(`${RACING_API_USERNAME}:${RACING_API_PASSWORD}`).toString('base64');
    return {
      'Authorization': `Basic ${credentials}`,
      'Content-Type': 'application/json',
      'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    };
  }

  throw new Error('Racing API credentials not configured');
}

async function fetchRacingAPI(endpoint: string): Promise<any> {
  const response = await fetch(`${RACING_API_BASE}${endpoint}`, {
    headers: getRacingAPIHeaders(),
  });
  
  if (!response.ok) {
    throw new Error(`Racing API error: ${response.status}`);
  }
  
  return response.json();
}

async function fetchRaceResults(track: string, raceDate: string, raceNumber: number): Promise<any> {
  try {
    const endpoint = `/v1/australia/results?date=${raceDate}&course=${encodeURIComponent(track)}`;
    const data = await fetchRacingAPI(endpoint);
    
    const results = data?.results || data || [];
    if (Array.isArray(results)) {
      return results.find((r: any) => 
        r.race_number === raceNumber || 
        r.raceNumber === raceNumber
      );
    }
    return null;
  } catch (error) {
    console.log(`Could not fetch results for ${track} R${raceNumber} on ${raceDate}`);
    return null;
  }
}

export async function archiveResultedRaces(): Promise<{
  processed: number;
  archived: number;
  errors: number;
}> {
  console.log("[Archival] Starting archive of resulted races...");
  
  const now = new Date();
  let processed = 0;
  let archived = 0;
  let errors = 0;
  
  try {
    const pastSelections = await db
      .select()
      .from(selections)
      .where(
        and(
          lt(selections.offTime, now),
          eq(selections.isActive, true)
        )
      );
    
    console.log(`[Archival] Found ${pastSelections.length} past selections to process`);
    
    const raceGroups = new Map<string, typeof pastSelections>();
    for (const sel of pastSelections) {
      const key = `${sel.track}-${sel.raceDate}-${sel.raceNumber}`;
      if (!raceGroups.has(key)) {
        raceGroups.set(key, []);
      }
      raceGroups.get(key)!.push(sel);
    }
    
    for (const [raceKey, raceSelections] of Array.from(raceGroups.entries())) {
      processed++;
      const firstSel = raceSelections[0];
      
      try {
        const results = await fetchRaceResults(
          firstSel.track,
          firstSel.raceDate,
          firstSel.raceNumber
        );
        
        for (const sel of raceSelections) {
          let actualPosition: number | null = null;
          let won = 0;
          let placed = 0;
          let startingPrice: number | null = null;
          
          if (results) {
            const runners = results.runners || results.results || [];
            const horseResult = runners.find((r: any) => 
              r.horse?.toLowerCase() === sel.horseName.toLowerCase() ||
              r.horseName?.toLowerCase() === sel.horseName.toLowerCase() ||
              r.name?.toLowerCase() === sel.horseName.toLowerCase()
            );
            
            if (horseResult) {
              actualPosition = horseResult.position || horseResult.finishing_position;
              won = actualPosition === 1 ? 1 : 0;
              placed = actualPosition && actualPosition <= 3 ? 1 : 0;
              startingPrice = horseResult.sp || horseResult.starting_price || horseResult.odds;
            }
          }
          
          const trainingRecord: InsertTrainingData = {
            raceId: sel.raceId || undefined,
            track: sel.track,
            raceNumber: sel.raceNumber,
            raceDate: sel.raceDate,
            distance: sel.distance || undefined,
            going: undefined,
            raceClass: undefined,
            horseName: sel.horseName,
            horseNumber: sel.horseNumber || undefined,
            barrier: sel.barrier ? parseInt(sel.barrier) : undefined,
            jockey: sel.jockey || undefined,
            trainer: sel.trainer || undefined,
            weight: undefined,
            predictedWinProb: sel.winPercentage,
            predictedPlaceProb: sel.placePercentage,
            predictedPosition: sel.expectedPosition,
            confidence: sel.confidence,
            expectedValue: sel.expectedValue,
            marketOdds: sel.marketOdds,
            actualPosition,
            won,
            placed,
            startingPrice,
            selectionId: sel.id,
          };
          
          await db.insert(trainingData).values(trainingRecord);
          archived++;
        }
        
        for (const sel of raceSelections) {
          const matchedResult = results ? (() => {
            const runners = results.runners || results.results || [];
            return runners.find((r: any) => 
              r.horse?.toLowerCase() === sel.horseName.toLowerCase() ||
              r.horseName?.toLowerCase() === sel.horseName.toLowerCase() ||
              r.name?.toLowerCase() === sel.horseName.toLowerCase()
            );
          })() : null;
          
          const sp = matchedResult?.sp || matchedResult?.starting_price || matchedResult?.odds;
          const spNum = sp ? parseFloat(String(sp)) : null;
          const position = matchedResult?.position || matchedResult?.finishing_position || null;
          
          let clvUpdate: Record<string, any> = { isActive: false };
          if (position !== null) {
            clvUpdate.resultPosition = position;
            clvUpdate.resultedAt = new Date();
          }
          if (spNum && spNum > 1 && sel.winPercentage > 0) {
            const modelProb = sel.winPercentage / 100.0;
            const closingMarketProb = 1.0 / spNum;
            clvUpdate.closingSpOdds = spNum;
            clvUpdate.clvRatio = Math.round((modelProb / closingMarketProb) * 10000) / 10000;
          }
          
          await db
            .update(selections)
            .set(clvUpdate)
            .where(eq(selections.id, sel.id));
        }
        
      } catch (raceError) {
        console.error(`[Archival] Error processing ${raceKey}:`, raceError);
        errors++;
      }
    }
    
    console.log(`[Archival] Complete: ${processed} races processed, ${archived} records archived, ${errors} errors`);
    
  } catch (error) {
    console.error("[Archival] Fatal error:", error);
    throw error;
  }
  
  return { processed, archived, errors };
}

export async function getTrainingDataStats(): Promise<{
  totalRecords: number;
  winRate: number;
  placeRate: number;
  averagePredictedWinProb: number;
  calibrationError: number;
}> {
  const allData = await db.select().from(trainingData);
  
  if (allData.length === 0) {
    return {
      totalRecords: 0,
      winRate: 0,
      placeRate: 0,
      averagePredictedWinProb: 0,
      calibrationError: 0,
    };
  }
  
  const totalRecords = allData.length;
  const wins = allData.filter(d => d.won === 1).length;
  const places = allData.filter(d => d.placed === 1).length;
  const winRate = (wins / totalRecords) * 100;
  const placeRate = (places / totalRecords) * 100;
  
  const avgPredicted = allData.reduce((sum, d) => sum + (d.predictedWinProb || 0), 0) / totalRecords;
  const calibrationError = Math.abs(winRate - avgPredicted);
  
  return {
    totalRecords,
    winRate,
    placeRate,
    averagePredictedWinProb: avgPredicted,
    calibrationError,
  };
}

export async function exportTrainingDataAsCSV(): Promise<string> {
  const allData = await db.select().from(trainingData);
  
  const headers = [
    'track', 'race_number', 'race_date', 'distance', 'horse_name',
    'barrier', 'jockey', 'trainer', 'weight',
    'predicted_win_prob', 'predicted_place_prob', 'predicted_position',
    'confidence', 'expected_value', 'market_odds',
    'actual_position', 'won', 'placed', 'starting_price'
  ];
  
  const rows = allData.map(d => [
    d.track,
    d.raceNumber,
    d.raceDate,
    d.distance || '',
    d.horseName,
    d.barrier || '',
    d.jockey || '',
    d.trainer || '',
    d.weight || '',
    d.predictedWinProb || '',
    d.predictedPlaceProb || '',
    d.predictedPosition || '',
    d.confidence || '',
    d.expectedValue || '',
    d.marketOdds || '',
    d.actualPosition || '',
    d.won || 0,
    d.placed || 0,
    d.startingPrice || '',
  ].join(','));
  
  return [headers.join(','), ...rows].join('\n');
}
