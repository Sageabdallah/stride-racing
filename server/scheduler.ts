import { spawn } from "child_process";
import fs from "fs";
import path from "path";
import { fileURLToPath } from "url";
import { sql } from "drizzle-orm";
import { db } from "./db";
import { racePipeline } from "./pipeline";
import { resolvePythonBin, logPythonTaskFailure } from "./pythonBin";

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);

// Resolve once at startup so a missing PYTHON_BIN/venv warns exactly once,
// at boot, not on the first failed task.
const PYTHON_BIN = resolvePythonBin();

interface ScheduledTask {
  name: string;
  dayOfWeek: number;
  hour: number;
  minute: number;
  callback: () => Promise<void>;
  lastRun?: Date;
}

class Scheduler {
  private tasks: ScheduledTask[] = [];
  private intervalId: NodeJS.Timeout | null = null;
  private isRunning = false;
  private checkIntervalMs = 60000;
  private statePath = path.join(__dirname, "python", "research", "scheduler_state.json");
  private persistedRuns: Record<string, string> = this.loadPersistedRuns();

  private loadPersistedRuns(): Record<string, string> {
    try {
      if (!fs.existsSync(this.statePath)) {
        return {};
      }
      const raw = JSON.parse(fs.readFileSync(this.statePath, "utf-8"));
      return raw && typeof raw === "object" ? raw as Record<string, string> : {};
    } catch (error) {
      console.error("[Scheduler] Failed to load persisted scheduler state:", error);
      return {};
    }
  }

  private savePersistedRuns() {
    try {
      fs.mkdirSync(path.dirname(this.statePath), { recursive: true });
      fs.writeFileSync(this.statePath, JSON.stringify(this.persistedRuns, null, 2));
    } catch (error) {
      console.error("[Scheduler] Failed to persist scheduler state:", error);
    }
  }

  private recordSuccessfulRun(taskName: string, when: Date) {
    this.persistedRuns[taskName] = when.toISOString();
    this.savePersistedRuns();
  }

  addTask(task: ScheduledTask) {
    if (!task.lastRun) {
      const persisted = this.persistedRuns[task.name];
      if (persisted) {
        const parsed = new Date(persisted);
        if (!Number.isNaN(parsed.getTime())) {
          task.lastRun = parsed;
        }
      }
    }
    this.tasks.push(task);
    console.log(`[Scheduler] Added task: ${task.name} (Day ${task.dayOfWeek}, ${task.hour}:${String(task.minute).padStart(2, '0')})`);
  }

  start() {
    if (this.isRunning) {
      console.log("[Scheduler] Already running");
      return;
    }

    this.isRunning = true;
    console.log("[Scheduler] Started - checking every minute");

    this.intervalId = setInterval(() => {
      this.checkTasks();
    }, this.checkIntervalMs);

    this.checkTasks();
  }

  stop() {
    if (this.intervalId) {
      clearInterval(this.intervalId);
      this.intervalId = null;
    }
    this.isRunning = false;
    console.log("[Scheduler] Stopped");
  }

  private async checkTasks() {
    const now = new Date();
    const currentDay = now.getDay();
    const currentHour = now.getHours();
    const currentMinute = now.getMinutes();

    for (const task of this.tasks) {
      // dayOfWeek = -1 means "run every day"
      const dayMatches = task.dayOfWeek === -1 || task.dayOfWeek === currentDay;
      if (
        dayMatches &&
        task.hour === currentHour &&
        currentMinute >= task.minute &&
        currentMinute < task.minute + 1
      ) {
        const today = now.toDateString();
        const lastRunDate = task.lastRun?.toDateString();

        if (lastRunDate !== today) {
          console.log(`[Scheduler] Running task: ${task.name}`);
          task.lastRun = now;
          
          try {
            await task.callback();
            this.recordSuccessfulRun(task.name, task.lastRun ?? now);
            console.log(`[Scheduler] Task completed: ${task.name}`);
          } catch (error) {
            console.error(`[Scheduler] Task failed: ${task.name}`, error);
          }
        }
      }
    }
  }

  getStatus() {
    return {
      isRunning: this.isRunning,
      tasks: this.tasks.map(t => ({
        name: t.name,
        dayOfWeek: t.dayOfWeek,
        hour: t.hour,
        minute: t.minute,
        lastRun: t.lastRun?.toISOString() || null,
      })),
    };
  }

  async runTaskManually(taskName: string) {
    const task = this.tasks.find(t => t.name === taskName);
    if (!task) {
      throw new Error(`Task not found: ${taskName}`);
    }

    console.log(`[Scheduler] Manually running task: ${taskName}`);
    task.lastRun = new Date();
    await task.callback();
    this.recordSuccessfulRun(task.name, task.lastRun);
    return { success: true, taskName };
  }

  getTask(taskName: string) {
    return this.tasks.find(t => t.name === taskName);
  }
}

export const scheduler = new Scheduler();

async function weeklySectionalCollectionTask(): Promise<void> {
  console.log("[Scheduler] Running weekly sectional times collection (NSW, QLD, VIC/SA)...");
  try {
    const result = await runPythonScript('weekly_sectional_collector.py', ['--days', '7']);
    if (result.success) {
      console.log("[Scheduler] Weekly sectional collection completed");
    } else {
      console.error("[Scheduler] Weekly sectional collection failed:", result.error);
    }
  } catch (error) {
    console.error("[Scheduler] Weekly sectional collection error:", error);
  }
}

function escapeSqlLiteral(value: string): string {
  return value.replace(/'/g, "''");
}

async function countRowsForDate(
  tableName: "race_results_history" | "sectional_times",
  dateStr: string,
): Promise<number> {
  const safeDate = escapeSqlLiteral(dateStr);
  const result = await db.execute(sql.raw(`
    SELECT COUNT(*)::int AS count
    FROM ${tableName}
    WHERE race_date::date = '${safeDate}'::date
  `));
  const row = (result.rows?.[0] ?? {}) as { count?: number | string };
  return Number(row.count || 0);
}

function parseImportedCount(output: string): number {
  const totalMatch = output.match(/Total imported:\s*(\d+)/i);
  if (totalMatch) {
    return Number(totalMatch[1]);
  }

  const savedMatch = output.match(/Backfill complete:\s*\d+\s*races processed,\s*(\d+)\s*records saved/i);
  if (savedMatch) {
    return Number(savedMatch[1]);
  }

  const patterns = [
    /Imported\s+(\d+)\s+sectional records/gi,
    /:\s+\d+\s+runners,\s+\d+\s+matched,\s+(\d+)\s+imported/gi,
    /:\s+(\d+)\s+imported\s+\(\d+\s+matched\)/gi,
  ];

  let total = 0;
  for (const pattern of patterns) {
    for (const match of output.matchAll(pattern)) {
      total += Number(match[1] || 0);
    }
  }
  return total;
}

function writeNightlyAlert(kind: string, dateStr: string, payload: Record<string, unknown>) {
  const buildLogPath = path.join(__dirname, "python", "intelligence", "build_log.txt");
  const researchDir = path.join(__dirname, "python", "research");
  const alertLogPath = path.join(researchDir, "stride_results_health_alerts.jsonl");
  const latestAlertPath = path.join(researchDir, "stride_results_health_latest.json");

  fs.mkdirSync(path.dirname(buildLogPath), { recursive: true });
  fs.mkdirSync(researchDir, { recursive: true });

  const timestamp = new Date().toISOString();
  const warningLine = `${timestamp} | STRIDE nightly ${kind} WARNING | date=${dateStr}`;
  fs.appendFileSync(buildLogPath, `${warningLine}\n`);

  const alertPayload = {
    timestamp,
    date: dateStr,
    kind,
    ...payload,
  };
  fs.appendFileSync(alertLogPath, `${JSON.stringify(alertPayload)}\n`);
  fs.writeFileSync(latestAlertPath, JSON.stringify(alertPayload, null, 2));
}

// STRIDE nightly results collection — runs auto_results_collector + RRH backfill for yesterday
async function strideResultsNightlyTask(): Promise<void> {
  console.log("[Scheduler] Running canonical learn-from-results workflow...");
  try {
    const result = await runPythonScript('learn_from_results_v2.py', ['--source', 'scheduler']);
    if (result.success) {
      try {
        const data = JSON.parse(result.output);
        if (data.status === "active") {
          console.log(`[Scheduler] learn-from-results already active: ${data.runId}`);
          return;
        }
        console.log(
          `[Scheduler] learn-from-results completed: status=${data.status}, ` +
          `resultsDelta=${data.gates?.resultsDelta ?? 0}, ` +
          `sectionalsDelta=${data.gates?.sectionalsDelta ?? 0}, ` +
          `retrain=${data.retrain?.status ?? "unknown"}`
        );
      } catch {
        console.log("[Scheduler] learn-from-results completed");
      }
    } else {
      console.error("[Scheduler] learn-from-results failed:", result.error);
    }
  } catch (error) {
    console.error("[Scheduler] learn-from-results error:", error);
  }
}

// STRIDE daily intelligence build — runs stride_build.py for today
async function strideIntelligenceDailyTask(): Promise<void> {
  console.log("[Scheduler] Running STRIDE intelligence build...");
  try {
    const todayStr = new Date().toISOString().split('T')[0];
    console.log(`[Scheduler] Building intelligence for ${todayStr}...`);
    const result = await runPythonScript('stride_build.py', [todayStr]);
    if (result.success) {
      try {
        const buildLogPath = path.join(__dirname, 'python', 'intelligence', 'build_log.txt');
        const logContent = fs.readFileSync(buildLogPath, 'utf-8');
        const fileLines = logContent.split('\n').filter((l: string) => l.includes('KB)'));
        const totalKB = fileLines.reduce((sum: number, line: string) => {
          const match = line.match(/\(([\d.]+)\s*KB\)/);
          return sum + (match ? parseFloat(match[1]) : 0);
        }, 0);
        console.log(`[Scheduler] Intelligence build complete: ${fileLines.length} files, ${totalKB.toFixed(1)} KB total`);
      } catch {
        console.log("[Scheduler] Intelligence build completed");
      }
    } else {
      console.error("[Scheduler] Intelligence build failed:", result.error);
    }
  } catch (error) {
    console.error("[Scheduler] Intelligence build error:", error);
  }
}

function getTodayScheduledTime(hour: number, minute: number, now = new Date()): Date {
  const scheduled = new Date(now);
  scheduled.setHours(hour, minute, 0, 0);
  return scheduled;
}

function getMostRecentDailyOccurrence(hour: number, minute: number, now = new Date()): Date {
  const scheduled = getTodayScheduledTime(hour, minute, now);
  if (scheduled.getTime() > now.getTime()) {
    scheduled.setDate(scheduled.getDate() - 1);
  }
  return scheduled;
}

// ---------------------------------------------------------------------------
// Startup catch-up planner (OP-5) — data-driven, ordered, dependency-gated.
//
// WHY: the previous runStartupCatchUps hard-coded exactly two catch-up-eligible
// tasks; process_bets_daily (10:00, creates the day's selections) was not one
// of them, so any boot after 10:00 silently skipped the day's tips — a primary
// cause of the dead-clock incident.
//
// CONSTRAINT: the consensus agent MUST run before tips or every pick gates
// NO_BET. Catch-up is therefore ORDERED (plan below) and GATED (a failed
// consensus catch-up skips the tips catch-up).
// ---------------------------------------------------------------------------

export interface CatchUpSpec {
  name: string;
  hour: number;
  minute: number;
  // "same-day": due when now is past today's HH:MM occurrence and lastRun
  //   predates it (the pre-existing stride_intelligence_daily rule).
  // "24h": due against the most recent HH:MM occurrence within a 24h window
  //   (the pre-existing stride_results_nightly rule).
  window: "same-day" | "24h";
}

// Execution order is table order, NOT chronological order: intelligence first
// (pre-existing position), then the consensus chain, then tips, then results.
export const STARTUP_CATCHUP_PLAN: CatchUpSpec[] = [
  { name: "stride_intelligence_daily", hour: 6, minute: 0, window: "same-day" },
  { name: "consensus_baseline_odds", hour: 0, minute: 30, window: "same-day" },
  { name: "consensus_agent_daily", hour: 7, minute: 0, window: "same-day" },
  { name: "consensus_morning_odds", hour: 8, minute: 0, window: "same-day" },
  { name: "process_bets_daily", hour: 10, minute: 0, window: "same-day" },
  { name: "stride_results_nightly", hour: 23, minute: 0, window: "24h" },
];

export interface CatchUpTaskView {
  name: string;
  lastRun?: Date | null;
}

function catchUpOccurrence(spec: CatchUpSpec, now: Date): Date {
  return spec.window === "24h"
    ? getMostRecentDailyOccurrence(spec.hour, spec.minute, now)
    : getTodayScheduledTime(spec.hour, spec.minute, now);
}

/** Pure planner: the ORDERED list of due catch-up task names. A task is due
 *  when now is past its scheduled occurrence within its window AND lastRun
 *  predates that occurrence — identical rules to the two pre-existing
 *  catch-ups (same-day = old intelligence rule, 24h = old nightly rule). */
export function planStartupCatchUps(now: Date, tasks: CatchUpTaskView[]): string[] {
  const byName = new Map(tasks.map((t) => [t.name, t]));
  const due: string[] = [];
  for (const spec of STARTUP_CATCHUP_PLAN) {
    const task = byName.get(spec.name);
    if (!task) continue;
    const occurrence = catchUpOccurrence(spec, now);
    const withinWindow = spec.window === "24h"
      ? now.getTime() - occurrence.getTime() <= 24 * 60 * 60 * 1000
      : now.getTime() >= occurrence.getTime();
    if (withinWindow && (!task.lastRun || task.lastRun.getTime() < occurrence.getTime())) {
      due.push(spec.name);
    }
  }
  return due;
}

// Exported for tests; production caller is initializeScheduler().
export async function runStartupCatchUps(): Promise<void> {
  const now = new Date();
  const views = STARTUP_CATCHUP_PLAN
    .map((spec) => scheduler.getTask(spec.name))
    .filter((t): t is NonNullable<typeof t> => Boolean(t));
  const plan = planStartupCatchUps(now, views);
  if (plan.length === 0) {
    console.log("[Scheduler] Startup catch-up: nothing due");
    return;
  }
  console.log(`[Scheduler] Startup catch-up plan: ${plan.join(" -> ")}`);

  const specByName = new Map(STARTUP_CATCHUP_PLAN.map((s) => [s.name, s]));
  let consensusFailed = false;
  for (const name of plan) {
    // Dependency gate: a failed consensus catch-up skips tips — running tips
    // now would gate every pick NO_BET. Odds-snapshot failures do NOT gate
    // tips (snapshots are additive); nothing gates the results task.
    if (name === "process_bets_daily" && consensusFailed) {
      console.error(
        "[Scheduler] Startup catch-up SKIPPED: process_bets_daily — " +
        "skipping tips catch-up: consensus failed — running tips now would gate every pick NO_BET");
      continue;
    }
    const occurrence = catchUpOccurrence(specByName.get(name)!, now);
    console.log(`[Scheduler] Startup catch-up: ${name} missed ${occurrence.toISOString()}, running now`);
    try {
      await scheduler.runTaskManually(name);
      console.log(`[Scheduler] Startup catch-up completed: ${name}`);
    } catch (error) {
      console.error(`[Scheduler] Startup catch-up failed: ${name}`, error);
      if (name === "consensus_agent_daily") {
        consensusFailed = true;
      }
    }
  }
}

// Consensus Intelligence Layer tasks
async function consensusBaselineOddsTask(): Promise<void> {
  console.log("[Scheduler] Capturing baseline odds snapshot...");
  try {
    const todayStr = new Date().toISOString().split('T')[0];
    const result = await runPythonScript('odds_movement.py', [todayStr, '--snapshot', 'baseline_night']);
    if (result.success) {
      console.log(`[Scheduler] Baseline odds snapshot captured for ${todayStr}`);
    } else {
      console.error("[Scheduler] Baseline odds snapshot failed:", result.error);
    }
  } catch (error) {
    console.error("[Scheduler] Baseline odds snapshot error:", error);
  }
}

async function consensusAgentDailyTask(): Promise<void> {
  console.log("[Scheduler] Running consensus agent (Tavily + Claude extraction)...");
  const todayStr = new Date().toISOString().split('T')[0];
  const result = await runPythonScript('consensus_agent.py', [todayStr]);
  if (!result.success) {
    console.error("[Scheduler] Consensus agent failed:", result.error);
    // Must propagate: the startup catch-up gate skips the tips catch-up on a
    // failed consensus — a swallowed failure here would let tips run and gate
    // every pick NO_BET.
    throw new Error("consensus_agent_daily failed (see error log above)");
  }
  console.log(`[Scheduler] Consensus agent completed for ${todayStr}`);
}

async function consensusMorningOddsTask(): Promise<void> {
  console.log("[Scheduler] Capturing morning odds and computing market signals...");
  try {
    const todayStr = new Date().toISOString().split('T')[0];
    const result = await runPythonScript('odds_movement.py', [todayStr, '--snapshot', 'morning']);
    if (result.success) {
      console.log(`[Scheduler] Morning odds + market signals completed for ${todayStr}`);
    } else {
      console.error("[Scheduler] Morning odds snapshot failed:", result.error);
    }
  } catch (error) {
    console.error("[Scheduler] Morning odds snapshot error:", error);
  }
}

async function sourceAccuracyNightlyTask(): Promise<void> {
  console.log("[Scheduler] Running source accuracy tracker...");
  try {
    const todayStr = new Date().toISOString().split('T')[0];
    const result = await runPythonScript('source_accuracy_tracker.py', [todayStr]);
    if (result.success) {
      console.log(`[Scheduler] Source accuracy tracking completed for ${todayStr}`);
    } else {
      console.error("[Scheduler] Source accuracy tracking failed:", result.error);
    }
  } catch (error) {
    console.error("[Scheduler] Source accuracy tracking error:", error);
  }
}

// Export task functions for manual triggering via API
export { collectResultsTask, weeklyRetrainTask, weeklySectionalCollectionTask, strideResultsNightlyTask, strideIntelligenceDailyTask, consensusBaselineOddsTask, consensusAgentDailyTask, consensusMorningOddsTask, sourceAccuracyNightlyTask };

export function runPythonScript(scriptName: string, args: string[] = []): Promise<{ success: boolean; output: string; error?: string }> {
  return new Promise((resolve) => {
    const scriptPath = path.join(__dirname, 'python', scriptName);
    const python = spawn(PYTHON_BIN, [scriptPath, ...args], {
      cwd: path.resolve(__dirname, ".."),
      env: process.env,
    });
    
    let stdout = '';
    let stderr = '';
    
    python.stdout.on('data', (data) => {
      stdout += data.toString();
      console.log(`[Python] ${data.toString().trim()}`);
    });
    
    python.stderr.on('data', (data) => {
      stderr += data.toString();
      console.error(`[Python Error] ${data.toString().trim()}`);
    });
    
    python.on('close', (code) => {
      if (code === 0) {
        resolve({ success: true, output: stdout });
      } else {
        logPythonTaskFailure(scriptName, code, stderr);
        resolve({ success: false, output: stdout, error: stderr });
      }
    });
    
    python.on('error', (err) => {
      logPythonTaskFailure(scriptName, null, err.message);
      resolve({ success: false, output: '', error: err.message });
    });
  });
}

export async function downloadRacecards(days: number = 7): Promise<{ success: boolean; output: string; error?: string }> {
  console.log(`[Scheduler] Downloading racecards for next ${days} days...`);
  return runPythonScript('download_racecards.py', ['--days', String(days)]);
}

export async function downloadAndProcessRacecards(): Promise<void> {
  console.log("[Scheduler] Starting automated racecard download and processing...");
  
  const downloadResult = await downloadRacecards(7);
  
  if (!downloadResult.success) {
    console.error("[Scheduler] Racecard download failed:", downloadResult.error);
    // Continue anyway - pipeline will use local racecard files as fallback
  } else {
    console.log("[Scheduler] Racecards downloaded successfully");
  }
  
  console.log("[Scheduler] Running ML pipeline...");
  
  const pipelineResult = await racePipeline.runPipeline();
  
  if (pipelineResult.success) {
    console.log(`[Scheduler] Pipeline completed: ${pipelineResult.racesProcessed} races, ${pipelineResult.selectionsCreated} selections`);
    try {
      const { refreshBlackbook } = await import("./blackbook");
      await refreshBlackbook();
      console.log("[Scheduler] Blackbook refreshed after racecard processing");
    } catch (error) {
      console.error("[Scheduler] Blackbook refresh failed after racecard processing:", error);
    }
  } else {
    console.error("[Scheduler] Pipeline failed:", pipelineResult.error);
  }
}

// Process local racecards only (no download attempt) - for daily 10 AM run
export async function processLocalRacecards(): Promise<void> {
  console.log("[Scheduler] Processing local racecards with Monte Carlo...");
  
  const pipelineResult = await racePipeline.runPipeline();
  
  if (pipelineResult.success) {
    console.log(`[Scheduler] Pipeline completed: ${pipelineResult.racesProcessed} races, ${pipelineResult.selectionsCreated} selections stored in database`);
    try {
      const { refreshBlackbook } = await import("./blackbook");
      await refreshBlackbook();
      console.log("[Scheduler] Blackbook refreshed after local racecard processing");
    } catch (error) {
      console.error("[Scheduler] Blackbook refresh failed after local racecard processing:", error);
    }
  } else {
    console.error("[Scheduler] Pipeline failed:", pipelineResult.error);
  }
}

async function archiveResultedRacesTask() {
  console.log("[Scheduler] Projecting canonical results into training_data...");
  try {
    const today = new Date().toISOString().split('T')[0];
    const result = await runPythonScript('results_collector.py', ['--archive', today]);
    if (!result.success) {
      console.error("[Scheduler] Canonical archive projection failed:", result.error);
      return;
    }
    try {
      const data = JSON.parse(result.output);
      console.log(`[Scheduler] Canonical archive complete: ${data.archived || 0} rows projected`);
    } catch {
      console.log("[Scheduler] Canonical archive projection completed");
    }
  } catch (error) {
    console.error("[Scheduler] Archival error:", error);
  }
}

// Collect race results and match to selections for performance tracking
async function collectResultsTask() {
  console.log("[Scheduler] Collecting race results and matching to selections...");
  try {
    const today = new Date().toISOString().split('T')[0];
    const result = await runPythonScript('results_collector.py', [today]);
    if (result.success) {
      try {
        const data = JSON.parse(result.output);
        console.log(`[Scheduler] Results collected: ${data.results_matched || 0} matched, ${data.winners || 0} winners, ROI: ${data.roi || 0}%`);
        try {
          const { refreshBlackbook } = await import("./blackbook");
          await refreshBlackbook();
          console.log("[Scheduler] Blackbook refreshed after results collection");
        } catch (refreshError) {
          console.error("[Scheduler] Blackbook refresh failed after results collection:", refreshError);
        }
      } catch (e) {
        console.log("[Scheduler] Results collection completed");
      }
    } else {
      console.error("[Scheduler] Results collection failed:", result.error);
    }
  } catch (error) {
    console.error("[Scheduler] Results collection error:", error);
  }
}

// Weekly model retraining with fresh results
async function weeklyRetrainTask() {
  console.log("[Scheduler] Starting weekly ML model retraining...");
  try {
    const result = await runPythonScript('train_ml_enhanced.py', []);
    if (result.success) {
      try {
        const data = JSON.parse(result.output);
        if (data.success) {
          console.log(`[Scheduler] Model retrained successfully. AUC: ${data.main_model?.metrics?.auc_roc?.toFixed(3) || 'N/A'}`);
        } else {
          console.error("[Scheduler] Model training failed:", data.error);
        }
      } catch (e) {
        console.log("[Scheduler] Model training completed");
      }
    } else {
      console.error("[Scheduler] Model training failed:", result.error);
    }
  } catch (error) {
    console.error("[Scheduler] Model training error:", error);
  }
}

export function initializeScheduler() {
  // Wednesday 4 PM - Download racecards for upcoming week
  scheduler.addTask({
    name: 'download_racecards_wednesday',
    dayOfWeek: 3,
    hour: 16,
    minute: 0,
    callback: downloadAndProcessRacecards,
  });
  
  // Daily 10 AM - Run Monte Carlo pipeline and store value bets in database
  // Uses local racecards only (no download attempt) for reliability
  scheduler.addTask({
    name: 'process_bets_daily',
    dayOfWeek: -1,  // Every day
    hour: 10,
    minute: 0,
    callback: processLocalRacecards,  // Process existing local files only
  });
  
  // Daily 6 AM - Build STRIDE intelligence files for today's racing
  scheduler.addTask({
    name: 'stride_intelligence_daily',
    dayOfWeek: -1,
    hour: 6,
    minute: 0,
    callback: strideIntelligenceDailyTask,
  });

  // Daily 11 PM - Run the canonical learning workflow for yesterday + 7-day recovery sweep
  scheduler.addTask({
    name: 'stride_results_nightly',
    dayOfWeek: -1,
    hour: 23,
    minute: 0,
    callback: strideResultsNightlyTask,
  });

  // Daily 12:30 AM - Capture baseline odds after markets settle
  scheduler.addTask({
    name: 'consensus_baseline_odds',
    dayOfWeek: -1,
    hour: 0,
    minute: 30,
    callback: consensusBaselineOddsTask,
  });

  // Daily 7 AM - Run consensus agent (Tavily search + Claude extraction)
  scheduler.addTask({
    name: 'consensus_agent_daily',
    dayOfWeek: -1,
    hour: 7,
    minute: 0,
    callback: consensusAgentDailyTask,
  });

  // Daily 8 AM - Capture morning odds and compute market signal scores
  scheduler.addTask({
    name: 'consensus_morning_odds',
    dayOfWeek: -1,
    hour: 8,
    minute: 0,
    callback: consensusMorningOddsTask,
  });

  // Daily 11 PM - Track tipster accuracy against actual results
  scheduler.addTask({
    name: 'source_accuracy_nightly',
    dayOfWeek: -1,
    hour: 23,
    minute: 0,
    callback: sourceAccuracyNightlyTask,
  });

  scheduler.start();
  void runStartupCatchUps();
}
