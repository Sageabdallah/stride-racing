/**
 * OP-2 tests — scheduler runPythonScript: interpreter routing + the single
 * structured failure line. child_process.spawn is fully mocked; zero real
 * python is spawned and no DB/pipeline module side effects occur.
 */

import { EventEmitter } from "events";
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";

const spawnMock = vi.fn();

vi.mock("child_process", () => ({
  spawn: (...args: unknown[]) => spawnMock(...args),
}));
vi.mock("../db", () => ({ db: {} }));
vi.mock("../pipeline", () => ({ racePipeline: {} }));

class FakeProcess extends EventEmitter {
  stdout = new EventEmitter();
  stderr = new EventEmitter();
}

async function importScheduler() {
  vi.resetModules();
  return await import("../scheduler");
}

beforeEach(() => {
  spawnMock.mockReset();
  vi.stubEnv("PYTHON_BIN", "/venv/bin/python");
});

afterEach(() => {
  vi.unstubAllEnvs();
  vi.restoreAllMocks();
});

describe("runPythonScript", () => {
  it("spawns the resolved interpreter, not a hardcoded python3", async () => {
    const proc = new FakeProcess();
    spawnMock.mockReturnValue(proc);
    const { runPythonScript } = await importScheduler();
    const promise = runPythonScript("learn_from_results_v2.py", ["--source", "scheduler"]);
    proc.emit("close", 0);
    await promise;
    expect(spawnMock).toHaveBeenCalledTimes(1);
    const [bin, argv] = spawnMock.mock.calls[0];
    expect(bin).toBe("/venv/bin/python");
    expect(String(argv[0])).toMatch(/learn_from_results_v2\.py$/);
    expect(argv[1]).toBe("--source");
  });

  it("logs ONE structured failure line with task name, exit code, first stderr line", async () => {
    const errSpy = vi.spyOn(console, "error").mockImplementation(() => {});
    vi.spyOn(console, "log").mockImplementation(() => {});
    const proc = new FakeProcess();
    spawnMock.mockReturnValue(proc);
    const { runPythonScript } = await importScheduler();
    const promise = runPythonScript("learn_from_results_v2.py", []);
    proc.stderr.emit("data", Buffer.from("Traceback (most recent call last):\nModuleNotFoundError: No module named 'dotenv'\n"));
    proc.emit("close", 1);
    const result = await promise;
    expect(result.success).toBe(false);
    const failLines = errSpy.mock.calls
      .map((c) => String(c[0]))
      .filter((l) => l.includes("[PYTASK_FAIL]"));
    expect(failLines).toHaveLength(1);
    const payload = JSON.parse(failLines[0].slice(failLines[0].indexOf("{")));
    expect(payload).toEqual({
      task: "learn_from_results_v2.py",
      exit: 1,
      stderr: "Traceback (most recent call last):",
    });
  });

  it("does not log a failure line on success", async () => {
    const errSpy = vi.spyOn(console, "error").mockImplementation(() => {});
    vi.spyOn(console, "log").mockImplementation(() => {});
    const proc = new FakeProcess();
    spawnMock.mockReturnValue(proc);
    const { runPythonScript } = await importScheduler();
    const promise = runPythonScript("odds_movement.py", ["2026-07-29", "--snapshot", "morning"]);
    proc.emit("close", 0);
    const result = await promise;
    expect(result.success).toBe(true);
    expect(errSpy.mock.calls.map((c) => String(c[0]))
      .filter((l) => l.includes("[PYTASK_FAIL]"))).toHaveLength(0);
  });

  it("logs the structured line with null exit when the process never starts", async () => {
    const errSpy = vi.spyOn(console, "error").mockImplementation(() => {});
    vi.spyOn(console, "log").mockImplementation(() => {});
    const proc = new FakeProcess();
    spawnMock.mockReturnValue(proc);
    const { runPythonScript } = await importScheduler();
    const promise = runPythonScript("stride_build.py", ["2026-07-29"]);
    proc.emit("error", new Error("spawn ENOENT"));
    const result = await promise;
    expect(result.success).toBe(false);
    const failLines = errSpy.mock.calls
      .map((c) => String(c[0]))
      .filter((l) => l.includes("[PYTASK_FAIL]"));
    expect(failLines).toHaveLength(1);
    const payload = JSON.parse(failLines[0].slice(failLines[0].indexOf("{")));
    expect(payload.task).toBe("stride_build.py");
    expect(payload.exit).toBeNull();
    expect(payload.stderr).toBe("spawn ENOENT");
  });
});

describe("consensusAgentDailyTask failure propagation (gate reachability)", () => {
  it("REJECTS when consensus_agent.py exits nonzero — a swallowed failure would make the startup catch-up gate unreachable and let tips run NO_BET", async () => {
    vi.spyOn(console, "error").mockImplementation(() => {});
    vi.spyOn(console, "log").mockImplementation(() => {});
    const proc = new FakeProcess();
    spawnMock.mockReturnValue(proc);
    const { consensusAgentDailyTask } = await importScheduler();
    const promise = consensusAgentDailyTask();
    proc.stderr.emit("data", Buffer.from("Traceback: consensus blew up\n"));
    proc.emit("close", 1);
    await expect(promise).rejects.toThrow(/consensus_agent_daily failed/);
  });

  it("resolves normally when consensus_agent.py exits 0", async () => {
    vi.spyOn(console, "log").mockImplementation(() => {});
    const proc = new FakeProcess();
    spawnMock.mockReturnValue(proc);
    const { consensusAgentDailyTask } = await importScheduler();
    const promise = consensusAgentDailyTask();
    proc.stdout.emit("data", Buffer.from("consensus ok\n"));
    proc.emit("close", 0);
    await expect(promise).resolves.toBeUndefined();
  });
});
