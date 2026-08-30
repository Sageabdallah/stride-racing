import { spawn } from "child_process";
import path from "path";
import { fileURLToPath } from "url";
import { db } from "./db";
import { resolvePythonBin, resolvePythonScriptDir, parsePythonJson } from "./pythonBin";
import { fetchPfRacecardMeets } from "./pfProvider";
import { races, selections, pipelineRuns } from "@shared/schema";
import { eq, and, gte, desc, sql } from "drizzle-orm";

function calculateOverround(runnersJson: any): number {
  try {
    const runners = Array.isArray(runnersJson) ? runnersJson : [];
    if (runners.length === 0) return 1.0;

    let totalImpliedProb = 0;
    let validCount = 0;

    for (const runner of runners) {
      const scratched = runner.scratched === true || runner.scratched === 'true';
      const status = (runner.status || '').toLowerCase();
      const isOut = scratched || ['scratched', 'non-runner', 'withdrawn', 'removed'].includes(status);
      if (isOut) continue;

      let decimalOdds = 0;

      if (Array.isArray(runner.odds) && runner.odds.length > 0) {
        for (const entry of runner.odds) {
          if (typeof entry === 'object' && entry !== null) {
            const dec = parseFloat(String(entry.decimal || entry.win_odds || entry.odds || '0'));
            if (dec > 1) { decimalOdds = dec; break; }
          }
          if (typeof entry === 'number' && entry > 1) { decimalOdds = entry; break; }
        }
      }

      if (decimalOdds <= 1) {
        const sp = parseFloat(String(runner.sp || '0').replace('$', ''));
        if (sp > 1) decimalOdds = sp;
      }
      if (decimalOdds <= 1) {
        const winOdds = parseFloat(String(runner.win_odds || runner.fixed_odds || '0').replace('$', ''));
        if (winOdds > 1) decimalOdds = winOdds;
      }

      if (decimalOdds > 1) {
        totalImpliedProb += 100 / decimalOdds;
        validCount++;
      }
    }

    if (validCount < 2 || totalImpliedProb <= 0) return 1.0;
    return totalImpliedProb / 100;
  } catch (e) {
    return 1.0;
  }
}

async function lookupFrankingByNames(horseNames: string[]): Promise<Record<string, any>> {
  if (!horseNames.length) return {};
  try {
    const lowerNames = horseNames.map(n => n.toLowerCase());
    const result = await db.execute(sql`
      SELECT horse_name, global_elo, franking_score, franking_confidence,
             anti_franked, field_strength_avg, form_quality_trend,
             best_adjusted_margin, collateral_advantage
      FROM franking_scores
      WHERE LOWER(horse_name) IN (${sql.join(lowerNames.map(n => sql`${n}`), sql`, `)})
    `);
    const map: Record<string, any> = {};
    for (const row of result.rows as any[]) {
      map[(row as any).horse_name.toLowerCase()] = row;
    }
    if (Object.keys(map).length > 0) {
      console.log(`[Pipeline] Franking: matched ${Object.keys(map).length}/${horseNames.length} horses`);
    }
    return map;
  } catch (e) {
    console.log('[Pipeline] Franking lookup skipped:', (e as Error).message);
    return {};
  }
}

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);

const BASE_ITERATIONS = 5000;
function getIterations(fieldSize: number): number {
  if (fieldSize <= 10) return BASE_ITERATIONS;
  if (fieldSize <= 14) return 3000;
  return 2000;
}

function runPythonMonteCarlo(input: any): Promise<any> {
  return new Promise((resolve, reject) => {
    const pythonScript = path.join(resolvePythonScriptDir(), 'mc_api.py');
    const python = spawn(resolvePythonBin(), [pythonScript]);
    
    let stdout = '';
    let stderr = '';
    
    python.stdout.on('data', (data) => {
      stdout += data.toString();
    });
    
    python.stderr.on('data', (data) => {
      stderr += data.toString();
    });
    
    python.on('close', (code) => {
      if (code !== 0) {
        reject(new Error(`Python simulation failed: ${stderr}`));
        return;
      }
      
      try {
        const result = parsePythonJson(stdout);
        resolve(result);
      } catch (e) {
        reject(new Error(`Failed to parse Python output: ${stdout}`));
      }
    });
    
    python.on('error', (err) => {
      reject(new Error(`Failed to spawn Python: ${err.message}`));
    });
    
    python.stdin.write(JSON.stringify(input));
    python.stdin.end();
  });
}

export interface PipelineResult {
  success: boolean;
  runId: string;
  meetsProcessed: number;
  racesProcessed: number;
  selectionsCreated: number;
  dataSource: string;
  error?: string;
}

export class RacePipeline {
  private isRunning = false;
  private autoRefreshInterval: NodeJS.Timeout | null = null;

  async runPipeline(targetDate?: string, options?: { trackFilter?: string[], goingOverrides?: Record<string, string>, raceNumbers?: number[] }): Promise<PipelineResult> {
    if (this.isRunning) {
      throw new Error("Pipeline is already running");
    }

    this.isRunning = true;
    let runId = '';
    let dataSource = 'racing_api';
    const trackFilter = options?.trackFilter?.map(t => t.toLowerCase()) || null;
    const goingOverrides = options?.goingOverrides || {};
    const raceNumberFilter = options?.raceNumbers || null;

    try {
      const [pipelineRun] = await db.insert(pipelineRuns).values({
        status: 'running',
        dataSource: 'racing_api',
      }).returning();
      
      runId = pipelineRun.id;
      console.log(`[Pipeline] Started run ${runId}`);
      if (trackFilter) {
        console.log(`[Pipeline] Track filter active: ${trackFilter.join(', ')}`);
      }
      if (Object.keys(goingOverrides).length > 0) {
        console.log(`[Pipeline] Going overrides: ${JSON.stringify(goingOverrides)}`);
      }

      const now = new Date();
      const ausTime = new Date(now.toLocaleString('en-US', { timeZone: 'Australia/Sydney' }));
      const todayStr = targetDate || ausTime.toISOString().split('T')[0];
      const nowTimestamp = now.getTime();

      let allMeets: any[] = [];

      try {
        console.log(`[Pipeline] Fetching races from Punting Form for ${todayStr}...`);
        const pfMeets = await fetchPfRacecardMeets(todayStr);
        if (Array.isArray(pfMeets) && pfMeets.length > 0) {
          console.log(`[Pipeline] Found ${pfMeets.length} meets from Punting Form: ${pfMeets.map((m: any) => m.course || m.track || 'Unknown').join(', ')}`);
          for (const meet of pfMeets) {
            const meetCourse = meet.course || meet.track || 'Unknown';

            const upcomingRaces = (meet.races || []).filter((r: any) => {
              if (r.race_status === 'Results' || r.race_status === 'Completed') return false;
              if (r.off_time) {
                const raceTime = new Date(r.off_time).getTime();
                return raceTime > nowTimestamp;
              }
              return true;
            });

            if (upcomingRaces.length > 0) {
              allMeets.push({
                date: todayStr,
                course: meetCourse,
                meet_id: meet.meet_id,
                races: upcomingRaces,
              });
              console.log(`[Pipeline] Successfully fetched ${meetCourse}: ${upcomingRaces.length} upcoming races`);
            } else {
              console.log(`[Pipeline] ${meetCourse}: No upcoming races (all completed or past)`);
            }
          }
        }
      } catch (apiErr: any) {
        console.log(`[Pipeline] Punting Form not available: ${apiErr.message}`);
      }

      if (allMeets.length === 0) {
        dataSource = 'local';
      }

      {
        const fs = await import('fs/promises');
        const pathModule = await import('path');
        const racecardsDir = pathModule.join(process.cwd(), 'racecards');
        const existingTrackDates = new Set(
          allMeets.map(m => `${(m.course || m.track || '').toLowerCase()}|${m.date || todayStr}`)
        );

        try {
          const files = await fs.readdir(racecardsDir);
          const racecardFiles = files.filter((f: string) => f.endsWith('.json'));

          for (const file of racecardFiles) {
            const dateMatch = file.match(/racecard_(\d{4}-\d{2}-\d{2})\.json/);
            const fileDate = dateMatch ? dateMatch[1] : null;
            if (fileDate && fileDate < todayStr) continue;

            const content = await fs.readFile(pathModule.join(racecardsDir, file), 'utf-8');
            const data = JSON.parse(content);

            if (Array.isArray(data)) {
              for (const meet of data) {
                const meetCourse = (meet.course || meet.track || '').toLowerCase();
                const meetDate = fileDate || todayStr;
                const key = `${meetCourse}|${meetDate}`;

                if (existingTrackDates.has(key)) {
                  const existingMeet = allMeets.find(m => 
                    (m.course || m.track || '').toLowerCase() === meetCourse && 
                    (m.date || todayStr) === meetDate
                  );
                  if (existingMeet) {
                    let oddsUpdated = 0;
                    for (const localRace of (meet.races || [])) {
                      const localRaceNum = parseInt(localRace.race_number);
                      const apiRace = (existingMeet.races || []).find((r: any) => parseInt(r.race_number) === localRaceNum);
                      if (!apiRace) {
                        existingMeet.races.push(localRace);
                        oddsUpdated++;
                        continue;
                      }
                      const apiRunners = apiRace.runners || [];
                      const apiHasOdds = apiRunners.some((r: any) => {
                        if (Array.isArray(r.odds) && r.odds.length > 0) return true;
                        if (r.sp || r.win_odds || r.fixed_odds) return true;
                        return false;
                      });
                      if (!apiHasOdds) {
                        const localRunners = localRace.runners || [];
                        const localHasOdds = localRunners.some((r: any) => {
                          if (Array.isArray(r.odds) && r.odds.length > 0) return true;
                          if (r.sp || r.win_odds || r.fixed_odds) return true;
                          return false;
                        });
                        if (localHasOdds) {
                          apiRace.runners = localRunners;
                          oddsUpdated++;
                        }
                      }
                    }
                    if (oddsUpdated > 0) {
                      console.log(`[Pipeline] Supplemented ${meet.course || meet.track} with ${oddsUpdated} races from local racecard (odds update)`);
                    }
                  }
                  continue;
                }

                const racesList = meet.races || [];
                const upcomingRaces = racesList.filter((r: any) => {
                  if (r.race_status === 'Results' || r.race_status === 'Completed') return false;
                  if (r.off_time) {
                    const raceTime = new Date(r.off_time).getTime();
                    return raceTime > nowTimestamp;
                  }
                  return true;
                });

                if (upcomingRaces.length > 0) {
                  allMeets.push({
                    ...meet,
                    date: meetDate,
                    races: upcomingRaces,
                  });
                  existingTrackDates.add(key);
                }
              }
            }
          }
        } catch (err) {
          console.error("[Pipeline] Error loading local racecards:", err);
        }
      }

      if (trackFilter && trackFilter.length > 0) {
        const beforeCount = allMeets.length;
        allMeets = allMeets.filter(m => {
          const courseName = (m.course || m.track || '').toLowerCase();
          return trackFilter.some(tf => courseName.includes(tf) || tf.includes(courseName));
        });
        console.log(`[Pipeline] Track filter: ${beforeCount} meets -> ${allMeets.length} (kept: ${allMeets.map(m => m.course || m.track).join(', ')})`);
      }

      // Check database for cached races that aren't in the API/local response
      const tracksFromAPI = new Set(allMeets.map(m => (m.course || m.track || '').toLowerCase()));
      
      try {
        const cachedRaces = await db.select()
          .from(races)
          .where(eq(races.raceDate, todayStr));
        
        const cachedTrackRaces: { [track: string]: any[] } = {};
        for (const race of cachedRaces) {
          const trackLower = (race.track || '').toLowerCase();
          if (!tracksFromAPI.has(trackLower)) {
            if (trackFilter && !trackFilter.some(tf => trackLower.includes(tf) || tf.includes(trackLower))) {
              continue;
            }
            if (!cachedTrackRaces[race.track]) {
              cachedTrackRaces[race.track] = [];
            }
            cachedTrackRaces[race.track].push(race);
          }
        }
        
        for (const [track, racesList] of Object.entries(cachedTrackRaces)) {
          console.log(`[Pipeline] Adding ${racesList.length} cached races from ${track} (not in API response)`);
          
          const cachedMeet = {
            course: track,
            track: track,
            meet_id: racesList[0]?.meetId || '',
            date: todayStr,
            races: racesList.map(r => ({
              race_number: r.raceNumber,
              race_name: r.raceName,
              distance: r.distance,
              off_time: r.offTime,
              going: r.going,
              class: r.raceClass,
              race_id: r.raceId,
              runners: r.runnersJson || [],
            })),
          };
          allMeets.push(cachedMeet);
        }
      } catch (err) {
        console.error("[Pipeline] Error loading cached races from database:", err);
      }

      // Supplement ALL meets (including cached DB races) with local racecard odds
      // This ensures races cached from API without odds get updated from local racecard files
      {
        const fs2 = await import('fs/promises');
        const pathModule2 = await import('path');
        const racecardsDir2 = pathModule2.join(process.cwd(), 'racecards');
        const racecardFile = pathModule2.join(racecardsDir2, `racecard_${todayStr}.json`);
        try {
          const content2 = await fs2.readFile(racecardFile, 'utf-8');
          const localData = JSON.parse(content2);
          if (Array.isArray(localData)) {
            for (const localMeet of localData) {
              const localCourse = (localMeet.course || localMeet.track || '').toLowerCase();
              const existingMeet = allMeets.find(m => 
                (m.course || m.track || '').toLowerCase() === localCourse
              );
              if (!existingMeet) continue;
              let oddsPatched = 0;
              for (const localRace of (localMeet.races || [])) {
                const localRaceNum = parseInt(localRace.race_number);
                const existingRace = (existingMeet.races || []).find((r: any) => parseInt(r.race_number) === localRaceNum);
                if (!existingRace) {
                  existingMeet.races.push(localRace);
                  oddsPatched++;
                  continue;
                }
                const existingRunners = existingRace.runners || [];
                const existingHasOdds = existingRunners.some((r: any) => {
                  if (Array.isArray(r.odds) && r.odds.length > 0) return true;
                  if (r.sp || r.win_odds || r.fixed_odds) return true;
                  return false;
                });
                if (!existingHasOdds) {
                  const localRunners = localRace.runners || [];
                  const localHasOdds = localRunners.some((r: any) => {
                    if (Array.isArray(r.odds) && r.odds.length > 0) return true;
                    if (r.sp || r.win_odds || r.fixed_odds) return true;
                    return false;
                  });
                  if (localHasOdds) {
                    existingRace.runners = localRunners;
                    oddsPatched++;
                  }
                }
              }
              if (oddsPatched > 0) {
                console.log(`[Pipeline] Patched ${existingMeet.course || existingMeet.track} with odds from local racecard for ${oddsPatched} races`);
              }
            }
          }
        } catch {
          // No local racecard for this date - that's fine
        }
      }
      
      const METRO_TRACKS = [
        'rosehill', 'randwick', 'royal randwick', 'flemington', 'caulfield',
        'moonee valley', 'eagle farm', 'doomben', 'morphettville',
        'ascot', 'belmont', 'sandown', 'cranbourne', 'pakenham',
        'newcastle', 'kensington', 'warwick farm', 'canterbury',
        'rosehill gardens', 'gold coast', 'sunshine coast'
      ];
      allMeets.sort((a: any, b: any) => {
        const aName = (a.course || a.track || '').toLowerCase();
        const bName = (b.course || b.track || '').toLowerCase();
        const aIsMetro = METRO_TRACKS.some(mt => aName.includes(mt));
        const bIsMetro = METRO_TRACKS.some(mt => bName.includes(mt));
        if (aIsMetro && !bIsMetro) return -1;
        if (!aIsMetro && bIsMetro) return 1;
        return 0;
      });

      const metroCount = allMeets.filter((m: any) => {
        const name = (m.course || m.track || '').toLowerCase();
        return METRO_TRACKS.some(mt => name.includes(mt));
      }).length;
      console.log(`[Pipeline] Found ${allMeets.length} meets to process (${metroCount} metro first): ${allMeets.map((m: any) => m.course || m.track).join(', ')}`);

      let meetsProcessed = 0;
      let racesProcessed = 0;
      let selectionsCreated = 0;

      for (const meet of allMeets) {
        const track = meet.course || meet.track || 'Unknown';
        const meetId = meet.meet_id || '';
        const raceDate = meet.date || todayStr;
        meetsProcessed++;

        for (const race of meet.races || []) {
          const allRunners = race.runners || [];
          const runners = allRunners.filter((r: any) => {
            const scratched = r.scratched === true || r.scratched === 'true';
            const status = (r.status || '').toLowerCase();
            const isOut = ['scratched', 'non-runner', 'withdrawn', 'removed'].includes(status);
            return !scratched && !isOut;
          });
          if (runners.length < 2) continue;

          const raceNumber = parseInt(race.race_number) || 1;
          
          if (raceNumberFilter && !raceNumberFilter.includes(raceNumber)) {
            continue;
          }
          
          const raceName = race.race_name || `Race ${raceNumber}`;
          const distance = race.distance || 'Unknown';
          const offTime = race.off_time ? new Date(race.off_time) : null;

          const runnersWithRealOdds = runners.filter((r: any) => {
            const oddsArr = r.odds;
            if (Array.isArray(oddsArr) && oddsArr.length > 0) {
              for (const entry of oddsArr) {
                if (typeof entry === 'object' && entry !== null) {
                  const winOdds = parseFloat(String(entry.win_odds || entry.odds || '0').replace('$', ''));
                  if (winOdds > 1) return true;
                }
                if (typeof entry === 'number' && entry > 1) return true;
              }
            }
            const sp = parseFloat(String(r.sp || '0').replace('$', ''));
            if (sp > 1) return true;
            const winOdds = parseFloat(String(r.win_odds || '0').replace('$', ''));
            if (winOdds > 1) return true;
            return false;
          });

          const hasOdds = runnersWithRealOdds.length > 0;
          console.log(`[Pipeline] ${track} R${raceNumber}: ${runnersWithRealOdds.length}/${runners.length} runners have real odds${!hasOdds ? ' (will process without odds)' : ''}`);

          // Check for existing race to avoid duplicates
          const existingRaces = await db.select()
            .from(races)
            .where(and(
              eq(races.track, track),
              eq(races.raceNumber, raceNumber),
              eq(races.raceDate, raceDate)
            ))
            .limit(1);

          const trackLowerForGoing = track.toLowerCase();
          const raceGoing = Object.entries(goingOverrides).find(([k]) => trackLowerForGoing.includes(k.toLowerCase()) || k.toLowerCase().includes(trackLowerForGoing))?.[1] || race.going || null;

          let storedRace;
          
          if (existingRaces.length > 0) {
            if (existingRaces[0].status === 'processed') {
              console.log(`[Pipeline] Skipping already-processed race: ${track} R${raceNumber}`);
              continue;
            }
            const [updated] = await db.update(races)
              .set({
                raceName,
                distance,
                raceClass: race.class || null,
                going: raceGoing,
                offTime,
                status: 'processing',
                runnersJson: runners,
                updatedAt: new Date(),
              })
              .where(eq(races.id, existingRaces[0].id))
              .returning();
            storedRace = updated;
            
            await db.update(selections)
              .set({ isActive: false })
              .where(eq(selections.raceId, storedRace.id));
              
            console.log(`[Pipeline] Updated existing race: ${track} R${raceNumber}`);
          } else {
            const [inserted] = await db.insert(races).values({
              raceId: race.race_id || null,
              meetId: meetId,
              track,
              raceNumber,
              raceName,
              distance,
              raceClass: race.class || null,
              going: raceGoing,
              offTime,
              raceDate,
              status: 'processing',
              runnersJson: runners,
            }).returning();
            storedRace = inserted;
          }

          racesProcessed++;

          try {
            // CRITICAL: Delete old selections for this race before creating new ones
            // This ensures scratched horses from previous runs are removed
            await db.delete(selections)
              .where(and(
                eq(selections.track, track),
                eq(selections.raceNumber, raceNumber),
                eq(selections.raceDate, raceDate)
              ));
            
            if (Object.keys(goingOverrides).length > 0 && raceGoing !== (race.going || null)) {
              console.log(`[Pipeline] Going override for ${track} R${raceNumber}: "${race.going}" -> "${raceGoing}"`);
            }

            const pythonInput = {
              track,
              race: String(raceNumber),
              iterations: getIterations(runners.length),
              runners,
              distance: distance,
              going: raceGoing || 'Good',
              mc_model: 'plackett_luce',
              pace_mode: 'basic',
              uncertainty: 'on',
              seed: Date.now() % 100000,
              // Enhanced features require race metadata
              raceClass: race.class || race.race_class || '',
              raceDate: raceDate,
              raceNumber: raceNumber,
            };

            const result = await runPythonMonteCarlo(pythonInput);
            console.log(`[Pipeline] MC simulation for ${track} R${raceNumber}: ${result.results?.length || 0} horses`);

            // Python API returns 'results' not 'horses'
            const horses = result.results || result.horses || [];
            if (Array.isArray(horses) && horses.length > 0) {
              // =================================================================
              // MARKET-ANCHORED PROBABILITY CALIBRATION
              // =================================================================
              // The MC model has a severe favourite-longshot bias: it distributes
              // probability too evenly, giving longshots (e.g. $81) inflated win%
              // (8%) while underestimating favourites ($2.60 = only 7%).
              //
              // Fix: Blend MC model probability with market-implied probability.
              // The market is highly efficient, especially for extreme prices.
              // We trust the model more for mid-range odds ($4-$15) where the
              // model might genuinely find value, and trust the market more for
              // extreme odds where model confidence is inherently low.
              // =================================================================
              const totalOverround = calculateOverround(storedRace.runnersJson);

              for (const h of horses) {
                const rawModelProb = h.winPercentage || 0;
                const odds = h.marketOdds || 0;
                h.rawModelProb = rawModelProb;

                if (odds > 1) {
                  const rawImplied = 100 / odds;
                  const trueMarketProb = rawImplied / totalOverround;

                  let modelWeight: number;
                  if (odds <= 3) {
                    modelWeight = 0.25;
                  } else if (odds <= 6) {
                    modelWeight = 0.40;
                  } else if (odds <= 15) {
                    modelWeight = 0.50;
                  } else if (odds <= 30) {
                    modelWeight = 0.35;
                  } else {
                    modelWeight = 0.20;
                  }

                  const calibratedProb = (modelWeight * rawModelProb) + ((1 - modelWeight) * trueMarketProb);
                  h.winPercentage = Math.round(calibratedProb * 100) / 100;
                }
              }

              // =================================================================
              // SMART SELECTION STRATEGY
              // =================================================================
              // Uses a composite score combining model view AND value/edge.
              // - If favourite is genuinely dominant in model, pick favourite
              // - If model sees better value elsewhere, pick the value horse
              // - Balances raw model probability, calibrated probability, and edge
              // =================================================================

              const scoredHorses = horses.map((h: any) => {
                const rawProb = h.rawModelProb || h.winPercentage || 0;
                const calibProb = h.winPercentage || 0;
                const odds = h.marketOdds || 0;
                let modelEdge = 0;
                if (odds > 1) {
                  const trueMarket = (100 / odds) / totalOverround;
                  modelEdge = rawProb - trueMarket;
                }

                // Selection score: blend of model strength + value
                // rawProb (40%): What the model actually thinks (before market anchoring)
                // calibProb (30%): Market-blended view (keeps things realistic)
                // modelEdge (30%): Model's edge over market (rewards value picks)
                // Edge is clamped to [-15, +15] to prevent extreme longshot distortion
                const clampedEdge = Math.max(-15, Math.min(15, modelEdge));
                const selectionScore = (rawProb * 0.40) + (calibProb * 0.30) + (clampedEdge * 0.30);

                h._selectionScore = selectionScore;
                h._modelEdge = modelEdge;
                return h;
              });

              // Sort by composite selection score
              const sortedHorses = [...scoredHorses].sort((a: any, b: any) => 
                (b._selectionScore || 0) - (a._selectionScore || 0)
              );

              // Favourite dominance check: if the market favourite is also the
              // model's top pick by raw probability AND has high calibrated prob,
              // ensure we don't overthink it — just back the favourite
              const marketFav = [...horses].sort((a: any, b: any) => 
                (a.marketOdds || 999) - (b.marketOdds || 999)
              )[0];
              const modelTop = [...horses].sort((a: any, b: any) => 
                (b.rawModelProb || b.winPercentage || 0) - (a.rawModelProb || a.winPercentage || 0)
              )[0];

              let bestHorse = sortedHorses[0];

              // If market favourite IS also the model's top pick, and the model
              // gives it strong support (rawProb >= 12%), always pick the favourite
              if (marketFav && modelTop) {
                const favName = (marketFav.horse || marketFav.name || '').toLowerCase();
                const modelTopName = (modelTop.horse || modelTop.name || '').toLowerCase();
                const favRawProb = marketFav.rawModelProb || marketFav.winPercentage || 0;
                if (favName === modelTopName && favRawProb >= 12) {
                  bestHorse = marketFav;
                }
              }

              // Safety: never select extreme longshots ($26+) unless model is very
              // confident (rawProb >= 15%) — this prevents noise-driven longshot picks
              if (bestHorse.marketOdds && bestHorse.marketOdds >= 26) {
                const rawP = bestHorse.rawModelProb || bestHorse.winPercentage || 0;
                if (rawP < 15) {
                  // Fall back to highest calibrated probability horse under $20
                  const safeHorses = sortedHorses.filter((h: any) => 
                    !h.marketOdds || h.marketOdds < 20
                  );
                  if (safeHorses.length > 0) {
                    bestHorse = safeHorses[0];
                  }
                }
              }

              const topCandidates = sortedHorses.slice(0, 5);
              const horseNames = topCandidates.map((h: any) => h.horse || h.name || '').filter(Boolean);
              const frankingMap = await lookupFrankingByNames(horseNames);
              for (const h of topCandidates) {
                const name = (h.horse || h.name || '').toLowerCase();
                if (frankingMap[name]) {
                  h._franking = frankingMap[name];
                }
              }
              // Also look up franking for bestHorse if not in top5
              const bestName = (bestHorse.horse || bestHorse.name || '').toLowerCase();
              if (!bestHorse._franking) {
                const bfMap = await lookupFrankingByNames([bestName]);
                if (bfMap[bestName]) bestHorse._franking = bfMap[bestName];
              }

              const selectedName = bestHorse.horse || bestHorse.name || 'Unknown';
              const selectedOdds = bestHorse.marketOdds || 0;
              const selectedRaw = bestHorse.rawModelProb || bestHorse.winPercentage || 0;
              const selectedScore = bestHorse._selectionScore || 0;
              console.log(`[Pipeline] ${track} R${raceNumber}: Selected ${selectedName} ($${selectedOdds}) | Raw: ${selectedRaw.toFixed(1)}% | Score: ${selectedScore.toFixed(1)}`);
              
              if (bestHorse) {
                const modelProb = bestHorse.winPercentage || 0;
                const rawModelP = bestHorse.rawModelProb || modelProb;
                const marketOdds = bestHorse.marketOdds || null;

                let expectedValue = 0;
                let computedEdge = 0;
                if (marketOdds && marketOdds > 1) {
                  const rawImpliedProb = 100 / marketOdds;
                  const trueMarketProb = rawImpliedProb / totalOverround;
                  // Use raw model probability for edge calculation — this reflects
                  // what the model actually thinks, not the market-blended view
                  expectedValue = ((rawModelP / trueMarketProb) - 1) * 100;
                  computedEdge = rawModelP - trueMarketProb;

                  let edgeConfidence = 1.0;
                  if (marketOdds > 30) {
                    edgeConfidence = 0.3;
                  } else if (marketOdds > 20) {
                    edgeConfidence = 0.5;
                  } else if (marketOdds > 15) {
                    edgeConfidence = 0.7;
                  }
                  computedEdge = computedEdge * edgeConfidence;
                  expectedValue = expectedValue * edgeConfidence;
                } else {
                  expectedValue = modelProb > 15 ? (modelProb - 8) * 0.8 : modelProb * 0.3;
                  computedEdge = 0;
                }

                let sectionalEdgeMultiplier = 1.0;
                let sectionalEdgeInsight: string | null = null;
                if (bestHorse.sectionalMcEnhanced && bestHorse.sectionalMcWinProb != null && modelProb > 0) {
                  const sectProb = bestHorse.sectionalMcWinProb;
                  const sectDelta = (sectProb - modelProb) / modelProb;
                  if (sectDelta >= 0) {
                    sectionalEdgeMultiplier = Math.min(1.15, 1.0 + sectDelta * 0.3);
                  } else if (sectDelta < -0.15) {
                    sectionalEdgeMultiplier = Math.max(0.85, 1.0 + sectDelta * 0.3);
                  }
                  computedEdge = computedEdge * sectionalEdgeMultiplier;

                  if (sectionalEdgeMultiplier > 1.05) {
                    sectionalEdgeInsight = "Sectional data confirms edge — strong finishing speed";
                  } else if (sectionalEdgeMultiplier > 1.0) {
                    sectionalEdgeInsight = "Sectional data supports edge";
                  } else if (sectionalEdgeMultiplier < 0.95) {
                    sectionalEdgeInsight = "Sectional data weakens edge — pace concern";
                  } else if (sectionalEdgeMultiplier < 1.0) {
                    sectionalEdgeInsight = "Sectional data slightly weakens edge";
                  }
                }

                // Confidence based on model conviction + edge quality
                let confidence: "high" | "medium" | "low" = "low";
                if (rawModelP >= 15 && computedEdge > 2 && marketOdds && marketOdds <= 10) {
                  confidence = "high";
                } else if (rawModelP >= 12 && computedEdge > 0 && marketOdds && marketOdds <= 15) {
                  confidence = "medium";
                } else if (modelProb >= 22 && marketOdds && marketOdds <= 5) {
                  confidence = "medium";
                }

                const horseName = bestHorse.horse || bestHorse.name || `Horse ${bestHorse.number}`;
                if (horseName) {
                  await db.insert(selections).values({
                    raceId: storedRace.id,
                    track,
                    raceNumber,
                    raceName,
                    raceDate,
                    offTime,
                    distance,
                    horseName,
                    horseNumber: bestHorse.number || null,
                    barrier: bestHorse.barrier || null,
                    jockey: bestHorse.jockey || null,
                    trainer: bestHorse.trainer || null,
                    form: bestHorse.form || null,
                    winPercentage: bestHorse.winPercentage || 0,
                    placePercentage: bestHorse.placePercentage || 0,
                    modelProbability: modelProb,
                    impliedOdds: bestHorse.impliedOdds || null,
                    marketOdds: marketOdds || null,
                    expectedValue: expectedValue,
                    edge: computedEdge,
                    kellyStake: bestHorse.kellyStake || 0,
                    ciLower: bestHorse.ciLower || 0,
                    ciUpper: bestHorse.ciUpper || 0,
                    expectedPosition: bestHorse.expectedPosition || 0,
                    positionStdDev: bestHorse.positionStdDev || 0,
                    stabilityScore: bestHorse.stabilityScore || 0,
                    runningStyle: bestHorse.runningStyle || 'Unknown',
                    paceSplitsJson: bestHorse.paceSplits || {},
                    confidence,
                    valueRating: computedEdge > 3 ? 'Excellent' : computedEdge > 1.5 ? 'Good' : computedEdge > 0 ? 'Fair' : 'Poor',
                    isActive: true,
                    mlAdjustment: bestHorse.mlAdjustment || null,
                    groundSuitability: bestHorse.groundSuitability || null,
                    distanceStrikeRate: bestHorse.distanceStrikeRate || null,
                    courseStrikeRate: bestHorse.courseStrikeRate || null,
                    weightedFormScore: bestHorse.weightedFormScore || null,
                    isFirstUp: bestHorse.isFirstUp === 1,
                    isSecondUp: bestHorse.isSecondUp === 1,
                    daysSinceRun: bestHorse.daysSinceRun || null,
                    isCourseDistanceWinner: bestHorse.isCourseDistanceWinner === 1,
                    isWinningCombo: bestHorse.isWinningCombo === 1,
                    jockeyTrainerStrikeRate: bestHorse.jockeyTrainerStrikeRate || null,
                    // Enhanced features (class movement, barrier bias, head-to-head)
                    enhancedFactor: bestHorse.enhancedFactor || null,
                    classMovementFactor: bestHorse.classMovement?.factor || null,
                    classMovementDesc: bestHorse.classMovement?.description || null,
                    barrierBiasFactor: bestHorse.barrierBias?.factor || null,
                    barrierBiasDesc: bestHorse.barrierBias?.description || null,
                    headToHeadFactor: bestHorse.headToHead?.factor || null,
                    headToHeadDesc: bestHorse.headToHead?.description || null,
                    enhancedExplanations: bestHorse.enhancedExplanations?.join(' | ') || null,
                    // Track bias points (use ?? to preserve 0 values)
                    trackBiasPoints: bestHorse.trackBiasPoints ?? null,
                    trackBiasFit: bestHorse.trackBiasFit ?? null,
                    trackBiasBarrierPts: bestHorse.trackBiasBreakdown?.barrier ?? null,
                    trackBiasPacePts: bestHorse.trackBiasBreakdown?.pace ?? null,
                    trackBiasJockeyPts: bestHorse.trackBiasBreakdown?.jockey ?? null,
                    trackBiasTrainerPts: bestHorse.trackBiasBreakdown?.trainer ?? null,
                    trackBiasSummary: bestHorse.trackBiasSummary ?? null,
                    // Form Franking data (looked up by horse name)
                    frankingElo: bestHorse._franking?.global_elo ?? null,
                    frankingScore: bestHorse._franking?.franking_score ?? null,
                    frankingConfidence: bestHorse._franking?.franking_confidence ?? null,
                    isAntiFranked: bestHorse._franking?.anti_franked ?? false,
                    fieldStrengthAvg: bestHorse._franking?.field_strength_avg ?? null,
                    formQualityTrend: bestHorse._franking?.form_quality_trend ?? null,
                    bestAdjustedMargin: bestHorse._franking?.best_adjusted_margin ?? null,
                    collateralAdvantage: bestHorse._franking?.collateral_advantage ?? null,
                    // Advanced Graph Franking features
                    pagerankAuthority: bestHorse.pagerank_authority ?? null,
                    communityStrength: bestHorse.community_strength ?? null,
                    formStability: bestHorse.form_stability ?? null,
                    graphFrankingScore: bestHorse.graph_franking_score ?? null,
                    graphFrankingDepth: bestHorse.graph_franking_depth ?? null,
                    graphFrankingIndependence: bestHorse.graph_franking_independence ?? null,
                    marketValidatedFranking: bestHorse.market_validated_franking ?? false,
                    bridgeScore: bestHorse.bridge_score ?? null,
                    // Fitness peak indicators
                    fitnessRunsThisPrep: bestHorse.fitnessData?.runsThisPrep ?? null,
                    fitnessRunLabel: bestHorse.fitnessData?.runLabel ?? null,
                    fitnessIsAtPeakRun: bestHorse.fitnessData?.isAtPeakRun ?? false,
                    fitnessReadinessScore: bestHorse.fitnessData?.fitnessReadinessScore ?? null,
                    fitnessPrepTrajectory: bestHorse.fitnessData?.prepFormTrajectory ?? null,
                    fitnessDescription: bestHorse.fitnessData?.description ?? null,
                    fitnessDataJson: bestHorse.fitnessData ?? null,
                    // Recalibration & Sectional MC metadata
                    recalibrationApplied: bestHorse.recalibrationApplied ?? false,
                    rawWinProb: bestHorse.rawWinProb ?? null,
                    calibratedWinProb: bestHorse.calibratedWinProb ?? null,
                    recalibrationShift: bestHorse.recalibrationShift ?? null,
                    sectionalMcEnhanced: bestHorse.sectionalMcEnhanced ?? false,
                    sectionalMcWinProb: bestHorse.sectionalMcWinProb ?? null,
                    blendedBaseProb: bestHorse.blendedBaseProb ?? null,
                    paceScenarioJson: bestHorse.paceScenario ?? null,
                    mlModelActive: bestHorse.mlModelActive ?? false,
                    mlAdjustmentBreakdown: bestHorse.mlAdjustmentBreakdown ?? null,
                    sectionalEdgeMultiplier: sectionalEdgeMultiplier !== 1.0 ? sectionalEdgeMultiplier : null,
                    sectionalEdgeInsight: sectionalEdgeInsight,
                    bankerFlag: bestHorse.banker_flag ?? false,
                    bankerTier: bestHorse.banker_tier ?? null,
                    bankerScore: bestHorse.banker_score ?? null,
                    bankerScoreComponents: bestHorse.banker_score_components ?? null,
                    bankerStaking: bestHorse.banker_staking ?? null,
                    bankerExoticCombos: bestHorse.banker_exotic_combos ?? null,
                    bankerQualificationLog: bestHorse.banker_qualification_log ?? null,
                  });
                  
                  selectionsCreated++;
                }
              }
            }

            await db.update(races)
              .set({ status: 'processed', updatedAt: new Date() })
              .where(eq(races.id, storedRace.id));

          } catch (simError) {
            console.error(`[Pipeline] Simulation error for ${track} R${raceNumber}:`, simError);
            await db.update(races)
              .set({ status: 'error', updatedAt: new Date() })
              .where(eq(races.id, storedRace.id));
          }
        }
      }

      await db.update(pipelineRuns)
        .set({
          status: 'completed',
          completedAt: new Date(),
          meetsProcessed,
          racesProcessed,
          selectionsCreated,
          dataSource,
        })
        .where(eq(pipelineRuns.id, runId));

      console.log(`[Pipeline] Completed: ${meetsProcessed} meets, ${racesProcessed} races, ${selectionsCreated} selections`);

      return {
        success: true,
        runId,
        meetsProcessed,
        racesProcessed,
        selectionsCreated,
        dataSource,
      };

    } catch (error: any) {
      console.error("[Pipeline] Error:", error);
      
      if (runId) {
        await db.update(pipelineRuns)
          .set({
            status: 'failed',
            completedAt: new Date(),
            errorMessage: error.message,
          })
          .where(eq(pipelineRuns.id, runId));
      }

      return {
        success: false,
        runId,
        meetsProcessed: 0,
        racesProcessed: 0,
        selectionsCreated: 0,
        dataSource: 'none',
        error: error.message,
      };
    } finally {
      this.isRunning = false;
    }
  }

  async getActiveSelections(limit = 50) {
    const now = new Date();
    
    // Sort by win percentage (highest first) to prioritize horses most likely to WIN
    return db.select()
      .from(selections)
      .where(eq(selections.isActive, true))
      .orderBy(desc(selections.winPercentage))
      .limit(limit);
  }

  async getSelectionsByDate(date: string) {
    // Sort by win percentage (highest first) to prioritize horses most likely to WIN
    return db.select()
      .from(selections)
      .where(and(
        eq(selections.raceDate, date),
        eq(selections.isActive, true)
      ))
      .orderBy(desc(selections.winPercentage));
  }

  async getStoredRaces(date?: string) {
    if (date) {
      return db.select()
        .from(races)
        .where(eq(races.raceDate, date))
        .orderBy(desc(races.createdAt));
    }
    return db.select()
      .from(races)
      .orderBy(desc(races.createdAt))
      .limit(100);
  }

  async getPipelineRuns(limit = 10) {
    return db.select()
      .from(pipelineRuns)
      .orderBy(desc(pipelineRuns.startedAt))
      .limit(limit);
  }

  async deactivateOldSelections() {
    const now = new Date();
    const ausTime = new Date(now.toLocaleString('en-US', { timeZone: 'Australia/Sydney' }));
    const todayStr = ausTime.toISOString().split('T')[0];

    const result = await db.update(selections)
      .set({ isActive: false })
      .where(and(
        eq(selections.isActive, true),
        eq(selections.raceDate, todayStr)
      ));
    
    return result;
  }

  startAutoRefresh(intervalMinutes = 30) {
    if (this.autoRefreshInterval) {
      clearInterval(this.autoRefreshInterval);
    }

    console.log(`[Pipeline] Auto-refresh started (every ${intervalMinutes} minutes)`);
    
    this.autoRefreshInterval = setInterval(async () => {
      console.log('[Pipeline] Auto-refresh triggered');
      try {
        await this.runPipeline();
      } catch (error) {
        console.error('[Pipeline] Auto-refresh error:', error);
      }
    }, intervalMinutes * 60 * 1000);

    this.runPipeline().catch(console.error);
  }

  stopAutoRefresh() {
    if (this.autoRefreshInterval) {
      clearInterval(this.autoRefreshInterval);
      this.autoRefreshInterval = null;
      console.log('[Pipeline] Auto-refresh stopped');
    }
  }

  isAutoRefreshActive() {
    return this.autoRefreshInterval !== null;
  }

  isPipelineRunning() {
    return this.isRunning;
  }

  async recoverStuckRuns() {
    try {
      const stuckRuns = await db.select()
        .from(pipelineRuns)
        .where(eq(pipelineRuns.status, 'running'));
      
      for (const run of stuckRuns) {
        await db.update(pipelineRuns)
          .set({
            status: 'failed',
            completedAt: new Date(),
            errorMessage: 'Server restarted during pipeline run',
          })
          .where(eq(pipelineRuns.id, run.id));
        console.log(`[Pipeline] Recovered stuck run: ${run.id}`);
      }
      
      if (stuckRuns.length > 0) {
        console.log(`[Pipeline] Recovered ${stuckRuns.length} stuck run(s)`);
      }
    } catch (error) {
      console.error("[Pipeline] Error recovering stuck runs:", error);
    }
  }

  /**
   * Validate selections against racecard data to remove scratched horses
   * This is the CRITICAL function that ensures no scratched horses appear in tips
   * 
   * @param dateStr - The date to validate (YYYY-MM-DD format)
   * @returns Object with validation stats
   */
  async validateSelectionsAgainstRacecard(dateStr: string): Promise<{
    validated: boolean;
    tracksInRacecard: string[];
    selectionsRemoved: number;
    scratchedHorsesRemoved: string[];
    staleTracksRemoved: string[];
  }> {
    const fs = await import('fs/promises');
    const pathModule = await import('path');
    
    const result = {
      validated: false,
      tracksInRacecard: [] as string[],
      selectionsRemoved: 0,
      scratchedHorsesRemoved: [] as string[],
      staleTracksRemoved: [] as string[],
    };

    try {
      const racecardsDir = pathModule.join(process.cwd(), 'racecards');
      const racecardPath = pathModule.join(racecardsDir, `racecard_${dateStr}.json`);

      // PRIMARY: Build valid horses map from races table (fresh API data)
      const allRaces = await db.select().from(races)
        .where(eq(races.raceDate, dateStr));

      const validHorsesMap = new Map<string, Map<number, Set<string>>>();

      for (const race of allRaces) {
        const track = race.track;
        if (!track) continue;

        if (!result.tracksInRacecard.includes(track)) {
          result.tracksInRacecard.push(track);
        }

        if (!validHorsesMap.has(track)) {
          validHorsesMap.set(track, new Map());
        }
        const trackMap = validHorsesMap.get(track)!;

        const raceNum = race.raceNumber;
        if (!raceNum) continue;

        if (!trackMap.has(raceNum)) {
          trackMap.set(raceNum, new Set());
        }
        const horseSet = trackMap.get(raceNum)!;

        const runners = race.runnersJson as any[];
        if (!runners) continue;

        for (const runner of runners) {
          const scratched = runner.scratched === true;
          const status = (runner.status || '').toLowerCase();
          const isOut = scratched || ['scratched', 'non-runner', 'withdrawn', 'removed'].includes(status);

          if (!isOut) {
            const horseName = runner.horse || runner.horse_name || runner.name || '';
            if (horseName) {
              horseSet.add(horseName.toLowerCase());
            }
          }
        }
      }

      // SECONDARY: Also check racecard file for additional scratching info
      // (the API sometimes marks horses as scratched via status field in racecard but not in the main runner data)
      try {
        const content = await fs.readFile(racecardPath, 'utf-8');
        const racecardData = JSON.parse(content);
        if (Array.isArray(racecardData)) {
          for (const meet of racecardData) {
            const track = meet.course || meet.track || '';
            if (!track || !validHorsesMap.has(track)) continue;
            const trackMap = validHorsesMap.get(track)!;

            for (const race of meet.races || []) {
              const raceNum = parseInt(race.race_number) || 0;
              if (!raceNum || !trackMap.has(raceNum)) continue;
              const horseSet = trackMap.get(raceNum)!;

              for (const runner of race.runners || []) {
                const status = (runner.status || '').toLowerCase();
                if (['scratched', 'non-runner', 'withdrawn', 'removed'].includes(status)) {
                  const horseName = (runner.horse || runner.horse_name || runner.name || '').toLowerCase();
                  if (horseName) {
                    horseSet.delete(horseName);
                  }
                }
              }
            }
          }
        }
      } catch (err) {
        // Racecard file not found is OK - we have the races table data
      }

      console.log(`[Validation] Loaded ${result.tracksInRacecard.length} tracks from races table: ${result.tracksInRacecard.join(', ')}`);

      // Get all selections for this date
      const allSelections = await db.select()
        .from(selections)
        .where(eq(selections.raceDate, dateStr));

      console.log(`[Validation] Found ${allSelections.length} selections for ${dateStr}`);

      const racesNeedingReselection = new Map<string, { track: string, raceNum: number, dateStr: string, scratchedNames: Set<string> }>();
      
      // Check each selection against the racecard
      for (const sel of allSelections) {
        const track = sel.track || '';
        const raceNum = sel.raceNumber || 0;
        const horseName = (sel.horseName || '').toLowerCase();
        
        let shouldRemove = false;
        let reason = '';
        
        // Check if track exists in racecard
        if (!validHorsesMap.has(track)) {
          shouldRemove = true;
          reason = `stale_track`;
          if (!result.staleTracksRemoved.includes(track)) {
            result.staleTracksRemoved.push(track);
          }
        } else {
          const trackMap = validHorsesMap.get(track)!;
          
          // Check if race exists
          if (!trackMap.has(raceNum)) {
            shouldRemove = true;
            reason = `stale_race`;
          } else {
            const validHorses = trackMap.get(raceNum)!;
            
            // Check if horse exists and is not scratched
            if (!validHorses.has(horseName)) {
              shouldRemove = true;
              reason = `scratched`;
              result.scratchedHorsesRemoved.push(`${sel.horseName} (${track} R${raceNum})`);
            }
          }
        }
        
        if (shouldRemove) {
          await db.delete(selections).where(eq(selections.id, sel.id));
          result.selectionsRemoved++;
          console.log(`[Validation] Removed ${sel.horseName} from ${track} R${raceNum} - Reason: ${reason}`);
          
          if (reason === 'scratched') {
            const raceKey = `${track}-R${raceNum}-${dateStr}`;
            if (!racesNeedingReselection.has(raceKey)) {
              racesNeedingReselection.set(raceKey, { track, raceNum, dateStr, scratchedNames: new Set<string>() });
            }
            racesNeedingReselection.get(raceKey)!.scratchedNames.add(horseName);
          }
        }
      }
      
      if (racesNeedingReselection.size > 0) {
        console.log(`[Validation] Re-selecting for ${racesNeedingReselection.size} races with scratched picks`);
      }
      for (const [raceKey, info] of Array.from(racesNeedingReselection.entries())) {
        try {
          const existingSels = await db.select().from(selections)
            .where(and(
              eq(selections.track, info.track),
              eq(selections.raceNumber, info.raceNum),
              eq(selections.raceDate, info.dateStr),
              eq(selections.isActive, true)
            ));
          if (existingSels.length > 0) continue;
          
          const raceRows = await db.select().from(races)
            .where(and(
              eq(races.track, info.track),
              eq(races.raceNumber, info.raceNum),
              eq(races.raceDate, info.dateStr)
            )).limit(1);
          if (raceRows.length === 0) continue;
          
          const storedRace = raceRows[0];
          const allRunners = storedRace.runnersJson as any[];
          if (!allRunners || allRunners.length < 2) continue;
          
          const scratchedNames = info.scratchedNames;
          const activeRunners = allRunners.filter((r: any) => {
            const name = (r.horse || r.horse_name || r.name || '').toLowerCase();
            return !scratchedNames.has(name) && !r.scratched;
          });
          
          if (activeRunners.length < 2) continue;
          
          console.log(`[Validation] Re-running MC for ${info.track} R${info.raceNum} with ${activeRunners.length} non-scratched runners`);
          
          const pythonInput = {
            track: info.track,
            race: String(info.raceNum),
            iterations: 5000,
            runners: activeRunners,
            distance: storedRace.distance || 'Unknown',
            going: storedRace.going || 'Good',
            mc_model: 'plackett_luce',
            pace_mode: 'basic',
            uncertainty: 'on',
            seed: Date.now() % 100000,
            raceClass: storedRace.raceClass || '',
            raceDate: info.dateStr,
            raceNumber: info.raceNum,
          };
          
          const mcResult = await runPythonMonteCarlo(pythonInput);
          const horses = mcResult.results || mcResult.horses || [];
          
          if (horses.length > 0) {
            const reselOverround = calculateOverround(storedRace.runnersJson);
            for (const h of horses) {
              const rawMP = h.winPercentage || 0;
              const hOdds = h.marketOdds || 0;
              h.rawModelProb = rawMP;
              if (hOdds > 1) {
                const ri = 100 / hOdds;
                const tmp = ri / reselOverround;
                let mw: number;
                if (hOdds <= 3) mw = 0.25;
                else if (hOdds <= 6) mw = 0.40;
                else if (hOdds <= 15) mw = 0.50;
                else if (hOdds <= 30) mw = 0.35;
                else mw = 0.20;
                h.winPercentage = Math.round(((mw * rawMP) + ((1 - mw) * tmp)) * 100) / 100;
              }
            }

            const sorted = [...horses].sort((a: any, b: any) => (b.winPercentage || 0) - (a.winPercentage || 0));
            const best = sorted[0];
            const horseName = best.horse || best.name || '';
            
            if (horseName && !scratchedNames.has(horseName.toLowerCase())) {
              const modelProb = best.winPercentage || 0;
              const marketOdds = best.marketOdds || null;
              const totalOverround = reselOverround;
              let expectedValue = 0, computedEdge = 0;
              if (marketOdds && marketOdds > 1) {
                const rawImpliedProb = 100 / marketOdds;
                const trueMarketProb = rawImpliedProb / totalOverround;
                expectedValue = ((modelProb / trueMarketProb) - 1) * 100;
                computedEdge = modelProb - trueMarketProb;

                let edgeConfidence = 1.0;
                if (marketOdds > 30) edgeConfidence = 0.3;
                else if (marketOdds > 20) edgeConfidence = 0.5;
                else if (marketOdds > 15) edgeConfidence = 0.7;
                computedEdge = computedEdge * edgeConfidence;
                expectedValue = expectedValue * edgeConfidence;
              }

              let sectionalEdgeMultiplier = 1.0;
              let sectionalEdgeInsight: string | null = null;
              if (best.sectionalMcEnhanced && best.sectionalMcWinProb != null && modelProb > 0) {
                const sectProb = best.sectionalMcWinProb;
                const sectDelta = (sectProb - modelProb) / modelProb;
                if (sectDelta >= 0) {
                  sectionalEdgeMultiplier = Math.min(1.15, 1.0 + sectDelta * 0.3);
                } else if (sectDelta < -0.15) {
                  sectionalEdgeMultiplier = Math.max(0.85, 1.0 + sectDelta * 0.3);
                }
                computedEdge = computedEdge * sectionalEdgeMultiplier;
                if (sectionalEdgeMultiplier > 1.05) {
                  sectionalEdgeInsight = "Sectional data confirms edge — strong finishing speed";
                } else if (sectionalEdgeMultiplier > 1.0) {
                  sectionalEdgeInsight = "Sectional data supports edge";
                } else if (sectionalEdgeMultiplier < 0.95) {
                  sectionalEdgeInsight = "Sectional data weakens edge — pace concern";
                } else if (sectionalEdgeMultiplier < 1.0) {
                  sectionalEdgeInsight = "Sectional data slightly weakens edge";
                }
              }

              let confidence: "high" | "medium" | "low" = "low";
              if (modelProb >= 25 && marketOdds && marketOdds <= 8) confidence = "high";
              else if (modelProb >= 18 && marketOdds && marketOdds <= 12) confidence = "medium";
              
              await db.insert(selections).values({
                raceId: storedRace.id,
                track: info.track,
                raceNumber: info.raceNum,
                raceName: storedRace.raceName || '',
                raceDate: info.dateStr,
                offTime: storedRace.offTime,
                distance: storedRace.distance || '',
                horseName,
                horseNumber: best.number || null,
                barrier: best.barrier || null,
                jockey: best.jockey || null,
                trainer: best.trainer || null,
                form: best.form || null,
                winPercentage: modelProb,
                placePercentage: best.placePercentage || 0,
                modelProbability: modelProb,
                marketOdds: marketOdds || null,
                expectedValue,
                edge: computedEdge,
                kellyStake: best.kellyStake || 0,
                ciLower: best.ciLower || 0,
                ciUpper: best.ciUpper || 0,
                confidence,
                valueRating: computedEdge > 3 ? 'Excellent' : computedEdge > 1.5 ? 'Good' : computedEdge > 0 ? 'Fair' : 'Poor',
                isActive: true,
                mlAdjustment: best.mlAdjustment || null,
                enhancedFactor: best.enhancedFactor || null,
                sectionalEdgeMultiplier: sectionalEdgeMultiplier !== 1.0 ? sectionalEdgeMultiplier : null,
                sectionalEdgeInsight,
              });
              console.log(`[Validation] Re-selected ${horseName} for ${info.track} R${info.raceNum} (win ${modelProb.toFixed(1)}%, odds $${marketOdds})`);
              result.selectionsRemoved--;
            }
          }
        } catch (e: any) {
          console.error(`[Validation] Re-selection failed for ${raceKey}: ${e.message}`);
        }
      }

      result.validated = true;
      console.log(`[Validation] Complete: Removed ${result.selectionsRemoved} invalid selections`);
      if (result.staleTracksRemoved.length > 0) {
        console.log(`[Validation] Stale tracks removed: ${result.staleTracksRemoved.join(', ')}`);
      }
      if (result.scratchedHorsesRemoved.length > 0) {
        console.log(`[Validation] Scratched horses removed: ${result.scratchedHorsesRemoved.join(', ')}`);
      }

      return result;

    } catch (error: any) {
      console.error(`[Validation] Error validating selections: ${error.message}`);
      return result;
    }
  }

  /**
   * Run the full pipeline with validation for ALL dates with selections
   * This ensures scratched horses are removed after processing
   */
  async runPipelineWithValidation(targetDate?: string, options?: { trackFilter?: string[], goingOverrides?: Record<string, string>, raceNumbers?: number[] }): Promise<PipelineResult & { validationResult?: any }> {
    const pipelineResult = await this.runPipeline(targetDate, options);
    
    if (pipelineResult.success) {
      // Get all distinct dates that have active selections
      const distinctDates = await db.selectDistinct({ raceDate: selections.raceDate })
        .from(selections)
        .where(eq(selections.isActive, true));
      
      let totalRemoved = 0;
      const allValidationResults: any[] = [];
      
      // Validate each date against its corresponding racecard
      for (const { raceDate } of distinctDates) {
        if (!raceDate) continue;
        const validationResult = await this.validateSelectionsAgainstRacecard(raceDate);
        totalRemoved += validationResult.selectionsRemoved;
        if (validationResult.selectionsRemoved > 0) {
          allValidationResults.push({ date: raceDate, ...validationResult });
        }
      }
      
      return {
        ...pipelineResult,
        selectionsCreated: pipelineResult.selectionsCreated - totalRemoved,
        validationResult: {
          datesValidated: distinctDates.length,
          totalSelectionsRemoved: totalRemoved,
          details: allValidationResults,
        },
      };
    }
    
    return pipelineResult;
  }
}

export const racePipeline = new RacePipeline();

racePipeline.recoverStuckRuns();
