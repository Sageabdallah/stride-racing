/**
 * OP-2 tests — python interpreter resolution. Pure logic + module-level
 * memoization, all fs/env states injected or stubbed. Zero python spawned,
 * zero network calls.
 */

import { describe, it, expect, vi, afterEach } from "vitest";
import {
  choosePythonBin,
  resolvePythonBin,
  logPythonTaskFailure,
  _resetPythonBinForTests,
  parsePythonJson,
} from "../pythonBin";

const never = () => false;
const always = () => true;

afterEach(() => {
  _resetPythonBinForTests();
  vi.unstubAllEnvs();
  vi.restoreAllMocks();
});

describe("choosePythonBin (pure)", () => {
  it("PYTHON_BIN env var wins when set, even over an existing venv", () => {
    const { bin, usedFallback } = choosePythonBin(
      { PYTHON_BIN: "/opt/custom/python" }, always);
    expect(bin).toBe("/opt/custom/python");
    expect(usedFallback).toBe(false);
  });

  it("chooses the project venv when PYTHON_BIN is unset and the file exists", () => {
    const { bin, usedFallback } = choosePythonBin({}, always);
    expect(bin).toMatch(/\.venv\/bin\/python$/);
    expect(usedFallback).toBe(false);
  });

  it("falls back to python3 when neither env nor venv exists", () => {
    const { bin, usedFallback } = choosePythonBin({}, never);
    expect(bin).toBe("python3");
    expect(usedFallback).toBe(true);
  });

  it("ignores a blank PYTHON_BIN", () => {
    const { bin } = choosePythonBin({ PYTHON_BIN: "   " }, always);
    expect(bin).toMatch(/\.venv\/bin\/python$/);
  });
});

describe("resolvePythonBin (memoized, warn-once)", () => {
  it("env override wins through the public resolver", () => {
    vi.stubEnv("PYTHON_BIN", "/opt/custom/python");
    expect(resolvePythonBin()).toBe("/opt/custom/python");
    // memoized: a later env change does not alter the resolved value
    vi.stubEnv("PYTHON_BIN", "/other/python");
    expect(resolvePythonBin()).toBe("/opt/custom/python");
  });

  it("fallback warns exactly once across repeated calls", () => {
    vi.stubEnv("PYTHON_BIN", "");
    const warn = vi.spyOn(console, "warn").mockImplementation(() => {});
    // Force the fallback regardless of whether a real .venv exists here.
    const first = resolvePythonBin();
    const second = resolvePythonBin();
    expect(second).toBe(first);
    if (first === "python3") {
      expect(warn).toHaveBeenCalledTimes(1);
      expect(warn.mock.calls[0][0]).toContain("python3");
    } else {
      // A project venv exists on this machine: no fallback, no warning.
      expect(first).toMatch(/\.venv\/bin\/python$/);
      expect(warn).not.toHaveBeenCalled();
    }
  });
});

describe("logPythonTaskFailure", () => {
  it("logs ONE structured line with task name, exit code, first stderr line", () => {
    const err = vi.spyOn(console, "error").mockImplementation(() => {});
    logPythonTaskFailure(
      "learn_from_results_v2.py", 1,
      "Traceback (most recent call last):\nModuleNotFoundError: No module named 'dotenv'\n");
    expect(err).toHaveBeenCalledTimes(1);
    const line = err.mock.calls[0][0] as string;
    expect(line).toContain("[PYTASK_FAIL]");
    const payload = JSON.parse(line.slice(line.indexOf("{")));
    expect(payload).toEqual({
      task: "learn_from_results_v2.py",
      exit: 1,
      stderr: "Traceback (most recent call last):",
    });
  });

  it("skips blank stderr lines and tolerates empty stderr", () => {
    const err = vi.spyOn(console, "error").mockImplementation(() => {});
    logPythonTaskFailure("odds_movement.py", null, "\n\n  real error here\n");
    let payload = JSON.parse((err.mock.calls[0][0] as string).split("PYTASK_FAIL] ")[1]);
    expect(payload).toEqual({ task: "odds_movement.py", exit: null, stderr: "real error here" });
    logPythonTaskFailure("odds_movement.py", 2, "");
    payload = JSON.parse((err.mock.calls[1][0] as string).split("PYTASK_FAIL] ")[1]);
    expect(payload.stderr).toBe("");
  });
});

describe("parsePythonJson", () => {
  it("parses clean JSON stdout", () => {
    expect(parsePythonJson('{"success": true}')).toEqual({ success: true });
  });

  it("extracts the JSON line after dependency warnings on stdout", () => {
    const stdout = [
      "ML libraries not available: No module named 'xgboost'",
      "sklearn not available — double calibration disabled",
      "  No calibration model found at /x/calibration_model.json",
      '{"success": true, "results": [{"horse": "She\'s Resolute"}]}',
      "",
    ].join("\n");
    const parsed = parsePythonJson(stdout) as any;
    expect(parsed.success).toBe(true);
    expect(parsed.results[0].horse).toBe("She's Resolute");
  });

  it("prefers the last parseable JSON line", () => {
    const stdout = '{"stage": "progress"}\n{"stage": "final"}';
    expect(parsePythonJson(stdout)).toEqual({ stage: "final" });
  });

  it("throws when no line parses as JSON", () => {
    expect(() => parsePythonJson("warning only\nno json here")).toThrow(/no JSON line/);
  });
});
