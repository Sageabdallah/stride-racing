/**
 * Optional shared-password gate for deployed instances.
 *
 * Off by default: when APP_PASSWORD is unset (local development) every
 * request passes through untouched. When APP_PASSWORD is set, every /api
 * request must present the password in the `x-app-password` header or as
 * `Authorization: Bearer <password>`, otherwise it gets a 401.
 *
 * This exists because the API has no per-user auth and several endpoints
 * spend real money (Claude/Groq/Perplexity calls) or trigger work
 * (/api/pipeline/run, /api/ml/train). Never deploy this server on a
 * public URL without setting APP_PASSWORD.
 */
import { timingSafeEqual } from "crypto";
import type { NextFunction, Request, Response } from "express";

const APP_PASSWORD = process.env.APP_PASSWORD?.trim() || "";

function matches(candidate: string): boolean {
  const a = Buffer.from(candidate);
  const b = Buffer.from(APP_PASSWORD);
  return a.length === b.length && timingSafeEqual(a, b);
}

export function appPasswordGate(req: Request, res: Response, next: NextFunction): void {
  if (!APP_PASSWORD) {
    next();
    return;
  }

  const header = req.header("x-app-password") ?? "";
  const bearer = req.header("authorization")?.replace(/^Bearer\s+/i, "") ?? "";
  if (matches(header) || matches(bearer)) {
    next();
    return;
  }

  res.status(401).json({ error: "This instance requires the app password (x-app-password header)." });
}
