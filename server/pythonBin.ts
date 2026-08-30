/**
 * Central python-interpreter resolution (OP-2).
 *
 * Every python spawn in the server must go through resolvePythonBin():
 *   1. PYTHON_BIN env var wins when set.
 *   2. Else the project virtualenv (.venv/bin/python at the repo root) when
 *      that file exists.
 *   3. Else "python3" — with a single console.warn naming the fallback.
 *
 * Resolution is memoized so the fallback warning fires exactly once per
 * process, no matter how many tasks spawn python.
 */

import fs from "fs";
import path from "path";
import { fileURLToPath } from "url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const REPO_ROOT = path.resolve(__dirname, "..");
const VENV_PYTHON = path.join(REPO_ROOT, ".venv", "bin", "python");

let resolvedBin: string | null = null;
let fallbackWarned = false;

/** Pure resolution logic, separated so tests can inject env + fs state. */
export function choosePythonBin(
  env: NodeJS.ProcessEnv,
  fileExists: (p: string) => boolean,
): { bin: string; usedFallback: boolean } {
  const fromEnv = env.PYTHON_BIN?.trim();
  if (fromEnv) {
    return { bin: fromEnv, usedFallback: false };
  }
  if (fileExists(VENV_PYTHON)) {
    return { bin: VENV_PYTHON, usedFallback: false };
  }
  return { bin: "python3", usedFallback: true };
}

/** The interpreter every server-side python spawn must use. Memoized. */
export function resolvePythonBin(): string {
  if (resolvedBin !== null) {
    return resolvedBin;
  }
  const { bin, usedFallback } = choosePythonBin(process.env, defaultFileExists);
  resolvedBin = bin;
  if (usedFallback && !fallbackWarned) {
    fallbackWarned = true;
    console.warn(
      `[PythonBin] PYTHON_BIN not set and no project venv at ${VENV_PYTHON} — ` +
      `falling back to "python3" on PATH. Scheduled python tasks will use the ` +
      `system interpreter; set PYTHON_BIN or create .venv to silence this.`,
    );
  }
  return resolvedBin;
}

/**
 * Directory holding the racing python system (mc_api.py and friends).
 *   1. STRIDE_PYTHON_DIR env var wins when set — point it at the
 *      stride-racing checkout's server/python when the two repos live
 *      side by side instead of merged.
 *   2. Else server/python inside this repo (the merged layout).
 */
export function resolvePythonScriptDir(): string {
  const fromEnv = process.env.STRIDE_PYTHON_DIR?.trim();
  if (fromEnv) {
    return fromEnv;
  }
  return path.join(__dirname, "python");
}

/**
 * Parse the JSON payload from a python task's stdout. The racing engine
 * prints dependency/artifact warnings to stdout before its JSON line
 * (e.g. "ML libraries not available: No module named 'xgboost'"), so a
 * bare JSON.parse of the whole stream fails in any environment missing
 * an optional library. The payload is the last line that parses as JSON.
 */
export function parsePythonJson(stdout: string): unknown {
  try {
    return JSON.parse(stdout);
  } catch {
    const candidates = stdout
      .split("\n")
      .map((line) => line.trim())
      .filter((line) => line.startsWith("{") || line.startsWith("["));
    for (let i = candidates.length - 1; i >= 0; i--) {
      try {
        return JSON.parse(candidates[i]);
      } catch {
        // keep scanning earlier candidates
      }
    }
    throw new Error(`no JSON line found in python output (${stdout.length} bytes)`);
  }
}

/** ONE structured line for a failed python task: task name, exit code,
 *  first stderr line. exitCode is null when the process never started. */
export function logPythonTaskFailure(
  taskName: string,
  exitCode: number | null,
  stderr: string,
): void {
  const firstLine = (stderr || "").split("\n").map((l) => l.trim()).find((l) => l.length > 0) ?? "";
  console.error(`[PYTASK_FAIL] ${JSON.stringify({ task: taskName, exit: exitCode, stderr: firstLine })}`);
}

// Indirection so tests can stub without monkey-patching globals.
function defaultFileExists(p: string): boolean {
  return fs.existsSync(p);
}

/** Test-only: clear the memoized resolution and the warn-once latch. */
export function _resetPythonBinForTests(): void {
  resolvedBin = null;
  fallbackWarned = false;
}
