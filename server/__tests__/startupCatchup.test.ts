/**
 * OP-5 tests — startup catch-up planner + executor.
 *
 * PINNING: the first suite restates the PRE-REFACTOR rules (copied verbatim
 * from the old runStartupCatchUps) as local reference functions and sweeps a
 * matrix of (now, lastRun) inputs, asserting the new pure planner makes
 * identical decisions for stride_intelligence_daily (same-day rule) and
 * stride_results_nightly (24h window rule).
 *
 * Executor tests use fake system dates, mocked task callbacks, a mocked fs
 * (the scheduler persists run state — kept side-effect free), and mocked
 * db/pipeline imports. Zero python spawned, zero network calls.
 */

import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import {
  planStartupCatchUps,
  STARTUP_CATCHUP_PLAN,
  runStartupCatchUps,
  scheduler,
} from "../scheduler";

vi.mock("../db", () => ({ db: {} }));
vi.mock("../pipeline", () => ({ racePipeline: {} }));
vi.mock("fs", () => ({
  default: {
    existsSync: () => false,
    readFileSync: () => { throw new Error("no state file in tests"); },
    writeFileSync: () => {},
    mkdirSync: () => {},
  },
}));

// --- pre-refactor reference rules (pinned, do not "fix" to match the planner)
function oldIntelligenceDue(now: Date, lastRun: Date | null): boolean {
  const scheduledToday = new Date(now);
  scheduledToday.setHours(6, 0, 0, 0);
  return now.getTime() >= scheduledToday.getTime() &&
    (!lastRun || lastRun.getTime() < scheduledToday.getTime());
}

function oldNightlyDue(now: Date, lastRun: Date | null): boolean {
  const lastScheduledNightly = new Date(now);
  lastScheduledNightly.setHours(23, 0, 0, 0);
  if (lastScheduledNightly.getTime() > now.getTime()) {
    lastScheduledNightly.setDate(lastScheduledNightly.getDate() - 1);
  }
  const withinWindow = now.getTime() - lastScheduledNightly.getTime() <= 24 * 60 * 60 * 1000;
  return withinWindow &&
    (!lastRun || lastRun.getTime() < lastScheduledNightly.getTime());
}

const at = (iso: string) => new Date(iso);

describe("pinning: planner decisions identical to pre-refactor rules", () => {
  const nows = [
    at("2026-07-29T00:15:00"), at("2026-07-29T05:59:59"), at("2026-07-29T06:00:00"),
    at("2026-07-29T06:00:01"), at("2026-07-29T12:56:00"), at("2026-07-29T22:59:59"),
    at("2026-07-29T23:00:00"), at("2026-07-29T23:30:00"),
  ];
  const lastRuns: (Date | null)[] = [
    null,
    at("2026-07-28T05:59:00"), at("2026-07-28T06:00:00"), at("2026-07-28T22:59:00"),
    at("2026-07-28T23:00:00"), at("2026-07-29T00:30:00"), at("2026-07-29T06:00:00"),
    at("2026-07-29T06:00:01"), at("2026-07-29T23:00:00"),
  ];

  it("stride_intelligence_daily: same-day rule identical across the matrix", () => {
    for (const now of nows) {
      for (const lastRun of lastRuns) {
        const plan = planStartupCatchUps(now, [
          { name: "stride_intelligence_daily", lastRun },
        ]);
        expect(plan.includes("stride_intelligence_daily"),
          `now=${now.toISOString()} lastRun=${lastRun?.toISOString()}`)
          .toBe(oldIntelligenceDue(now, lastRun));
      }
    }
  });

  it("stride_results_nightly: 24h window rule identical across the matrix", () => {
    for (const now of nows) {
      for (const lastRun of lastRuns) {
        const plan = planStartupCatchUps(now, [
          { name: "stride_results_nightly", lastRun },
        ]);
        expect(plan.includes("stride_results_nightly"),
          `now=${now.toISOString()} lastRun=${lastRun?.toISOString()}`)
          .toBe(oldNightlyDue(now, lastRun));
      }
    }
  });

  it("unregistered tasks are skipped, never invented", () => {
    const plan = planStartupCatchUps(at("2026-07-29T12:56:00"), []);
    expect(plan).toEqual([]);
  });
});

describe("planStartupCatchUps (pure planner)", () => {
  it("boot 12:56, nothing run yet → tasks 1–5 in table order, plus nightly (due)", () => {
    const plan = planStartupCatchUps(at("2026-07-29T12:56:00"),
      STARTUP_CATCHUP_PLAN.map((s) => ({ name: s.name, lastRun: null })));
    expect(plan).toEqual([
      "stride_intelligence_daily",
      "consensus_baseline_odds",
      "consensus_agent_daily",
      "consensus_morning_odds",
      "process_bets_daily",
      "stride_results_nightly",
    ]);
  });

  it("boot 12:56 with nightly already recovered → exactly tasks 1–5", () => {
    // mirrors the observed incident: boot after 10:00 must not skip tips
    const plan = planStartupCatchUps(at("2026-07-29T12:56:00"), [
      { name: "stride_intelligence_daily", lastRun: null },
      { name: "consensus_baseline_odds", lastRun: null },
      { name: "consensus_agent_daily", lastRun: null },
      { name: "consensus_morning_odds", lastRun: null },
      { name: "process_bets_daily", lastRun: null },
      { name: "stride_results_nightly", lastRun: at("2026-07-29T00:00:00") },
    ]);
    expect(plan).toEqual([
      "stride_intelligence_daily",
      "consensus_baseline_odds",
      "consensus_agent_daily",
      "consensus_morning_odds",
      "process_bets_daily",
    ]);
  });

  it("boot 06:30 → baseline-odds due; consensus/morning/bets not yet due", () => {
    const plan = planStartupCatchUps(at("2026-07-29T06:30:00"), [
      // intelligence (06:00) already ran this morning
      { name: "stride_intelligence_daily", lastRun: at("2026-07-29T06:05:00") },
      { name: "consensus_baseline_odds", lastRun: null },
      { name: "consensus_agent_daily", lastRun: null },
      { name: "consensus_morning_odds", lastRun: null },
      { name: "process_bets_daily", lastRun: null },
      { name: "stride_results_nightly", lastRun: at("2026-07-28T23:05:00") },
    ]);
    expect(plan).toEqual(["consensus_baseline_odds"]);
  });

  it("already-run tasks are not re-planned", () => {
    const plan = planStartupCatchUps(at("2026-07-29T12:56:00"), [
      { name: "stride_intelligence_daily", lastRun: at("2026-07-29T06:00:00") },
      { name: "consensus_baseline_odds", lastRun: at("2026-07-29T00:30:00") },
      { name: "consensus_agent_daily", lastRun: at("2026-07-29T07:00:00") },
      { name: "consensus_morning_odds", lastRun: at("2026-07-29T08:00:00") },
      { name: "process_bets_daily", lastRun: at("2026-07-29T10:00:00") },
      { name: "stride_results_nightly", lastRun: at("2026-07-28T23:00:00") },
    ]);
    expect(plan).toEqual([]);
  });
});

// --- executor ---------------------------------------------------------------

const BOOT_1256 = at("2026-07-29T12:56:00");

function registerPlanTasks(callbacks: Record<string, () => Promise<void>>) {
  for (const spec of STARTUP_CATCHUP_PLAN) {
    scheduler.addTask({
      name: spec.name,
      dayOfWeek: -1,
      hour: spec.hour,
      minute: spec.minute,
      callback: callbacks[spec.name] ?? (() => Promise.resolve()),
    });
  }
  // nightly was recovered at 00:05, keeping plans to tasks 1–5 unless a test
  // clears it explicitly
  scheduler.getTask("stride_results_nightly")!.lastRun = at("2026-07-29T00:05:00");
}

function clearScheduler() {
  const s = scheduler as unknown as { tasks: unknown[]; persistedRuns: Record<string, string> };
  s.tasks.length = 0;
  s.persistedRuns = {}; // runTaskManually records runs here — must not leak across tests
}

describe("runStartupCatchUps (executor)", () => {
  beforeEach(() => {
    vi.useFakeTimers();
    vi.setSystemTime(BOOT_1256);
    clearScheduler();
    vi.spyOn(console, "log").mockImplementation(() => {});
    vi.spyOn(console, "error").mockImplementation(() => {});
  });

  afterEach(() => {
    vi.useRealTimers();
    vi.restoreAllMocks();
    clearScheduler();
  });

  it("runs the plan SEQUENTIALLY in table order, one log line per start/completion", async () => {
    const order: string[] = [];
    const callbacks: Record<string, () => Promise<void>> = {};
    for (const spec of STARTUP_CATCHUP_PLAN) {
      callbacks[spec.name] = async () => {
        order.push(`start:${spec.name}`);
        await Promise.resolve(); // yield a microtask: a non-awaiting executor would interleave
        order.push(`end:${spec.name}`);
      };
    }
    registerPlanTasks(callbacks);
    await runStartupCatchUps();
    // strict sequential interleaving: each task fully ends before the next starts
    expect(order).toEqual([
      "start:stride_intelligence_daily", "end:stride_intelligence_daily",
      "start:consensus_baseline_odds", "end:consensus_baseline_odds",
      "start:consensus_agent_daily", "end:consensus_agent_daily",
      "start:consensus_morning_odds", "end:consensus_morning_odds",
      "start:process_bets_daily", "end:process_bets_daily",
    ]);
    const logs = (console.log as ReturnType<typeof vi.fn>).mock.calls.map((c) => String(c[0]));
    for (const name of order.filter((l) => l.startsWith("start:")).map((l) => l.slice(6))) {
      expect(logs.some((l) => l.includes(`Startup catch-up: ${name} missed`))).toBe(true);
      expect(logs.some((l) => l.includes(`Startup catch-up completed: ${name}`))).toBe(true);
    }
  });

  it("consensus failure gates tips: bets skipped with one loud log naming why", async () => {
    const ran: string[] = [];
    registerPlanTasks({
      consensus_agent_daily: () => Promise.reject(new Error("consensus agent exploded")),
      stride_intelligence_daily: async () => { ran.push("stride_intelligence_daily"); },
      consensus_baseline_odds: async () => { ran.push("consensus_baseline_odds"); },
      consensus_morning_odds: async () => { ran.push("consensus_morning_odds"); },
      process_bets_daily: async () => { ran.push("process_bets_daily"); },
    });
    await runStartupCatchUps();
    expect(ran).not.toContain("process_bets_daily");
    const errLogs = (console.error as ReturnType<typeof vi.fn>).mock.calls.map((c) => String(c[0]));
    const skipLines = errLogs.filter((l) =>
      l.includes("skipping tips catch-up: consensus failed") &&
      l.includes("gate every pick NO_BET"));
    expect(skipLines).toHaveLength(1);
  });

  it("odds-snapshot failures do NOT gate tips", async () => {
    const ran: string[] = [];
    registerPlanTasks({
      consensus_baseline_odds: () => Promise.reject(new Error("baseline odds failed")),
      consensus_morning_odds: () => Promise.reject(new Error("morning odds failed")),
      stride_intelligence_daily: async () => { ran.push("stride_intelligence_daily"); },
      consensus_agent_daily: async () => { ran.push("consensus_agent_daily"); },
      process_bets_daily: async () => { ran.push("process_bets_daily"); },
    });
    await runStartupCatchUps();
    expect(ran).toContain("process_bets_daily");
  });

  it("nothing gates the results task, even a consensus failure", async () => {
    const ran: string[] = [];
    registerPlanTasks({
      consensus_agent_daily: () => Promise.reject(new Error("consensus exploded")),
      stride_results_nightly: async () => { ran.push("stride_results_nightly"); },
    });
    scheduler.getTask("stride_results_nightly")!.lastRun = null; // make nightly due
    await runStartupCatchUps();
    expect(ran).toContain("stride_results_nightly");
  });

  it("nothing due → one line, no tasks run", async () => {
    vi.setSystemTime(at("2026-07-29T00:15:00")); // before every same-day occurrence
    registerPlanTasks({});
    await runStartupCatchUps();
    const logs = (console.log as ReturnType<typeof vi.fn>).mock.calls.map((c) => String(c[0]));
    expect(logs.filter((l) => l.includes("Startup catch-up: nothing due"))).toHaveLength(1);
  });
});
