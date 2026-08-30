import "dotenv/config";
import * as Sentry from "@sentry/node";
import express, { type Request, Response, NextFunction } from "express";
import rateLimit from "express-rate-limit";
import { registerRoutes } from "./routes";
import { appPasswordGate } from "./authGate";
import { serveStatic } from "./static";
import { createServer } from "http";

// Sentry must be initialized before any other imports that might throw
if (process.env.SENTRY_DSN) {
  Sentry.init({
    dsn: process.env.SENTRY_DSN,
    environment: process.env.NODE_ENV || "development",
    tracesSampleRate: 0.1,
  });
}

const app = express();
const httpServer = createServer(app);

declare module "http" {
  interface IncomingMessage {
    rawBody: unknown;
  }
}

app.use(
  express.json({
    verify: (req, _res, buf) => {
      req.rawBody = buf;
    },
  }),
);

app.use(express.urlencoded({ extended: false }));

// Rate limiting — protect Claude API spend from runaway clients
const chatLimiter = rateLimit({
  windowMs: 60 * 1000, // 1 minute
  max: 20,
  standardHeaders: true,
  legacyHeaders: false,
  message: { error: "Too many requests. Please wait a moment before sending another message." },
});

const globalLimiter = rateLimit({
  windowMs: 60 * 1000,
  max: 120,
  standardHeaders: true,
  legacyHeaders: false,
  message: { error: "Too many requests. Please try again shortly." },
});

app.use("/api/chat", chatLimiter);
app.use("/api/ask-stride", chatLimiter);
app.use("/api/stride-analyst", chatLimiter);
app.use("/api", globalLimiter);

// Shared-password gate — active only when APP_PASSWORD is set. Never
// deploy this server on a public URL without it (see server/authGate.ts).
app.use("/api", appPasswordGate);

export function log(message: string, source = "express") {
  const formattedTime = new Date().toLocaleTimeString("en-US", {
    hour: "numeric",
    minute: "2-digit",
    second: "2-digit",
    hour12: true,
  });

  console.log(`${formattedTime} [${source}] ${message}`);
}

app.use((req, res, next) => {
  const start = Date.now();
  const path = req.path;
  let capturedJsonResponse: Record<string, any> | undefined = undefined;

  const originalResJson = res.json;
  res.json = function (bodyJson, ...args) {
    capturedJsonResponse = bodyJson;
    return originalResJson.apply(res, [bodyJson, ...args]);
  };

  res.on("finish", () => {
    const duration = Date.now() - start;
    if (path.startsWith("/api")) {
      let logLine = `${req.method} ${path} ${res.statusCode} in ${duration}ms`;
      if (capturedJsonResponse) {
        try {
          const serialized = JSON.stringify(capturedJsonResponse);
          const preview = serialized.length > 1200
            ? `${serialized.slice(0, 1200)}… [truncated ${serialized.length - 1200} chars]`
            : serialized;
          logLine += ` :: ${preview}`;
        } catch {
          logLine += " :: [unserializable response body]";
        }
      }

      log(logLine);
    }
  });

  next();
});

(async () => {
  await registerRoutes(httpServer, app);

  app.use((err: any, _req: Request, res: Response, next: NextFunction) => {
    const status = err.status || err.statusCode || 500;
    const message = err.message || "Internal Server Error";

    console.error("Internal Server Error:", err);
    Sentry.captureException(err);

    if (res.headersSent) {
      return next(err);
    }

    return res.status(status).json({ message });
  });

  // importantly only setup vite in development and after
  // setting up all the other routes so the catch-all route
  // doesn't interfere with the other routes
  if (process.env.NODE_ENV === "production") {
    serveStatic(app);
  } else {
    const { setupVite } = await import("./vite");
    await setupVite(httpServer, app);
  }

  // ALWAYS serve the app on the port specified in the environment variable PORT
  // Other ports are firewalled. Default to 5000 if not specified.
  // this serves both the API and the client.
  // It is the only port that is not firewalled.
  const port = parseInt(process.env.PORT || "5000", 10);
  httpServer.listen(
    {
      port,
      host: "0.0.0.0",
    },
    async () => {
      log(`serving on port ${port}`);
      
      // Opt-in, and default off because the AWS EventBridge schedules now own
      // these jobs. The tasks fire at 06:00, 07:00, 08:00 and 10:00 and shell
      // the same Python (stride_build, consensus_agent, odds_movement) against
      // the same Neon database as stride-intelligence-0600, stride-consensus-0700,
      // stride-morning-odds-0800 and stride-tips-1000.
      //
      // The 08:00 collision is the one that matters: job_morning_odds proves
      // itself by counting MORNING_CHECK rows written after its own start time,
      // so a second copy of odds_movement.py writing those rows can satisfy that
      // assertion on a morning the Fargate capture wrote nothing — turning the
      // estate's best self-check into a false green.
      //
      // Default off rather than on: forgetting the flag costs a dashboard with
      // no scheduler, forgetting the guard costs a silent double-write.
      if (process.env.STRIDE_APP_SCHEDULER === "true") {
        try {
          const { initializeScheduler } = await import("./scheduler");
          initializeScheduler();
          log("Scheduler initialized (STRIDE_APP_SCHEDULER=true)", "scheduler");
        } catch (err) {
          console.error("Failed to initialize scheduler:", err);
        }
      } else {
        log(
          "Scheduler NOT started — the AWS EventBridge schedules own these jobs. " +
            "Set STRIDE_APP_SCHEDULER=true to run them from this process instead.",
          "scheduler",
        );
      }
    },
  );
})();
