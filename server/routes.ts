import type { Express } from "express";
import { createServer, type Server } from "http";
import { storage } from "./storage";
import {
  chatCompletionRequestSchema,
  chatRequestSchema,
  simulationRequestSchema,
} from "@shared/schema";
import { z } from "zod";
import Anthropic from "@anthropic-ai/sdk";
import { spawn } from "child_process";
import path from "path";
import fs from "fs";
import { fileURLToPath } from "url";
import { db } from "./db";
import { resolvePythonBin, resolvePythonScriptDir, parsePythonJson } from "./pythonBin";
import { CLAUDE_MODEL } from "./claudeConfig";
import { fetchPfMeets, fetchPfRacecardMeets, fetchPfResults, fetchPfSpeedmaps, hasPuntingFormKey } from "./pfProvider";
import { desc, sql, eq } from "drizzle-orm";
import { generateRaceOverview, generateRunnerAnalysis } from "./runnerAnalysis";
import { loadAllForwardTestSnapshots, loadForwardTestSnapshot } from "./forwardTestSnapshot";
import { initializeBlackbook, registerBlackbookRoutes } from "./blackbook";
import { initializeStrideTipResults } from "./strideTipResults";
import {
  buildLocalStrideResponse,
  clearStrideChatState,
  completeStrideTurn,
  resolveStrideTurn,
  startStrideTurn,
} from "./strideChatService";
import { formatLocalSynthesisResponse, orchestrateChatTurn, synthesizeLocalWithClaude } from "./chatOrchestrator";
import {
  buildConceptualStrideReasoning,
  buildConceptualStrideResponse,
  identifyConceptualStrideQuestion,
} from "./strideConceptualChat";

// ESM compatibility for __dirname
const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);

// Helper function to run Python Monte Carlo simulation
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
        console.error('Python MC error:', stderr);
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

// Anthropic client for Stride AI. Model comes from claudeConfig; the
// placeholder key keeps the server booting when no key is configured —
// every Claude call site degrades to its local fallback on auth failure.
const anthropic = new Anthropic({
  apiKey: process.env.ANTHROPIC_API_KEY ?? "anthropic-key-not-configured",
});

const hasAnthropicApiKey = Boolean(process.env.ANTHROPIC_API_KEY?.trim());
const learningRunsDir = path.join(__dirname, "python", "research", "learning_runs");
const learningLatestPointerPath = path.join(learningRunsDir, "latest.json");

const learnFromResultsSchema = z.object({
  startDate: z.string().regex(/^\d{4}-\d{2}-\d{2}$/).optional(),
  endDate: z.string().regex(/^\d{4}-\d{2}-\d{2}$/).optional(),
  tracks: z.array(z.string().min(1)).optional(),
  dryRun: z.boolean().optional(),
  stageRetrain: z.boolean().optional(),
});

function readLatestLearningRunSummary() {
  if (!fs.existsSync(learningLatestPointerPath)) {
    return null;
  }

  const latestPointer = JSON.parse(fs.readFileSync(learningLatestPointerPath, "utf-8"));
  const summaryPath = latestPointer?.path;
  if (!summaryPath || !fs.existsSync(summaryPath)) {
    return null;
  }

  return JSON.parse(fs.readFileSync(summaryPath, "utf-8"));
}

function sanitizeStrideText(text: string): string {
  return text.replace(/\*/g, "").replace(/#/g, "");
}

function buildLocalChatReasoning(message: string): string {
  const lowerMessage = message.toLowerCase();
  const thoughts: string[] = [];

  if (/(best bet|best chance|what should i back|today)/.test(lowerMessage)) {
    thoughts.push(
      "This is a race-day decision question, so the answer should center on edge, map, and price discipline rather than generic tipping."
    );
  }

  if (/(edge|expected value|ev|fair odds|price)/.test(lowerMessage)) {
    thoughts.push(
      "The key concept is whether the model's assessed chance is stronger than the market's implied probability after allowing for uncertainty."
    );
  }

  if (/(track condition|soft|heavy|good|firm)/.test(lowerMessage)) {
    thoughts.push(
      "Track condition changes the pressure profile of the race, so the answer should mention how tempo, footing, and recovery under pressure interact."
    );
  }

  if (/(flemington|randwick|ascot|doomben|warwick farm|moonee valley|caulfield)/.test(lowerMessage)) {
    thoughts.push(
      "This also needs a track-shape lens because barrier pressure, bend timing, and lane usage can matter more than raw ratings at these venues."
    );
  }

  if (thoughts.length === 0) {
    thoughts.push(
      "The cleanest fallback is to answer from general wagering logic, form interpretation, and race-shape principles without pretending I have live model context loaded."
    );
  }

  thoughts.push(
    "The live Stride model key is unavailable on this machine, so this reply needs to stay honest about being a local fallback rather than a live card-specific model read."
  );

  return thoughts.join(" ");
}

function buildLocalChatResponse(message: string): string {
  const lowerMessage = message.toLowerCase();

  if (/(best bet|best chance|what should i back|today)/.test(lowerMessage)) {
    return "I can't name a genuine best bet from the standalone chat without the live card context, and I don't want to fake one. The right profile is the runner with a clear map edge, positive gap between rated chance and market chance, and a price that still sits above your fair odds. If you want a race-specific answer, open The Track Board or Race Day and ask from the loaded selections so Stride can rank the actual runners rather than speak in generalities.";
  }

  if (/(edge|expected value|ev|fair odds|price)/.test(lowerMessage)) {
    return "Your edge is the gap between the model's probability and the market's implied probability. Turn your rated win chance into fair odds, compare that to the live quote, and only get involved when the market is still longer than your price by a margin big enough to survive noise and model error. The best edges are the ones backed by map, stability, and track fit, not just a raw number floating on its own.";
  }

  if (/(flemington)/.test(lowerMessage)) {
    return "At Flemington, I'd weight tempo honesty, where the horse lands in running, and whether it can build momentum without being cluttered up. The track rewards runners that travel smoothly and sustain their effort, so map and sectional strength usually matter more than surface-level form comments. If the market misses how well a horse's run style fits the race shape there, that's where the real betting edge tends to appear.";
  }

  if (/(track condition|soft|heavy|good|firm)/.test(lowerMessage)) {
    return "Track condition matters because it changes how energy gets spent through the race. On rain-affected ground I care more about proven action, resilience under pressure, and whether the horse can hold its position without burning fuel early; on firmer decks, tactical speed and clean momentum often matter more. The best betting angle is when the market prices a horse off headline form but underweights how much the surface improves or hurts its actual setup.";
  }

  if (/(algorithm|model|how does .*work|explain)/.test(lowerMessage)) {
    return "The model should be treated like a pricing engine, not an oracle. It turns form, map, pace, stability, and market context into a rated probability, then the wagering decision comes from whether that rated chance is better than the price on offer. The sharp part is not just picking winners; it's finding the runners the market has mispriced relative to their true setup.";
  }

  return "Stride's live model service is unavailable on this machine right now, so I'll keep it straight. The safest general approach is to anchor on three things: where the horse maps, whether your rated chance beats the market, and whether the race shape actually lets the horse run to that rating. If you want, ask me about edge, map, track condition, or a specific track and I'll answer from that angle.";
}

// Stride AI System Prompt - Australian Thoroughbred Racing Intelligence
const STRIDE_SYSTEM_PROMPT = `# STRIDE — Australian Thoroughbred Racing AI

## CHAIN OF THOUGHT MODE

You may receive messages that include your own previous reasoning analysis. When you see a message starting with "[REASONING ANALYSIS]", this is your step-by-step thought process that you should use to craft a polished, refined final answer. Synthesize this reasoning into a clear, direct response for the user.

## IDENTITY

You are **STRIDE**, an AI specialist in Australian thoroughbred racing. You have comprehensive knowledge of form analysis, betting mathematics, breeding, track characteristics, and racing operations across all Australian states.

You're speaking with a sophisticated user who has built Monte Carlo simulation systems for racing. Match their technical level.

## PERSONALITY

- **Direct** — Lead with the answer, explain after
- **Analytical** — Think in probabilities and expected value
- **Australian-focused** — All examples, tracks, and references are Australian
- **Honest** — Admit uncertainty; never guarantee winners
- **Conversational** — Talk like a knowledgeable mate at the track

## KNOWLEDGE BASE

When asked what data you're trained on or where your knowledge comes from, explain:

"My knowledge comes from comprehensive training data covering Australian thoroughbred racing, including:
- Historical race results, sectionals, and ratings across all Australian tracks
- Form data, class structures, and benchmark systems used by Racing Victoria, Racing NSW, Racing Queensland, Racing SA, and Racing WA
- Breeding records, stallion statistics, and pedigree databases
- Track configurations, rail positions, and bias patterns
- Jockey and trainer statistics, combinations, and patterns
- Betting market data, odds movements, and wagering mathematics
- Racing rules, conditions, and industry regulations

This knowledge has a cutoff date and may not reflect the most recent race results or current odds."

## CORE EXPERTISE

### Form & Handicapping
- Form strings, cycles, and trajectory analysis
- Sectional times, par times, and speed maps
- Benchmark ratings and class assessment
- Track bias, rail positions, and conditions (Good/Soft/Heavy scale)
- Barrier statistics by track and distance
- Jockey/trainer patterns and combinations

### Betting Mathematics
- Value calculation: EV = (Probability × Odds) - 1
- Kelly Criterion and fractional Kelly staking
- All bet types: Win/Place, Exotics, Multis
- Odds conversion and implied probability
- Bankroll management and risk of ruin
- Fixed odds vs tote dynamics

### Australian Tracks
- VIC: Flemington, Caulfield, Moonee Valley, Sandown, Cranbourne
- NSW: Randwick, Rosehill, Warwick Farm, Canterbury, Newcastle
- QLD: Eagle Farm, Doomben, Sunshine Coast, Gold Coast
- SA: Morphettville, Murray Bridge
- WA: Ascot, Belmont
- Track-specific characteristics, configurations, and biases

### Racing Calendar
- Melbourne Cup Carnival (VRC)
- The Championships (ATC Sydney)
- Brisbane Winter Carnival
- Group 1 calendar and pathway races
- State-based racing calendars

### Breeding
- Australian stallion ranks and profiles
- Broodmare sire influence
- Distance and surface aptitude from pedigree
- Wet track breeding indicators

## RESPONSE RULES

### When Giving Tips
1. Explain the reasoning — never blind tips
2. Assess race shape and pace
3. Evaluate market odds vs your probability
4. Frame as probability, not certainty
5. Include bet type recommendation if appropriate

### When Analysing Races
1. Context: Class, distance, track, conditions
2. Speed Map: Leaders, midfield, backmarkers
3. Contenders: Top chances with reasoning
4. Oppositions: Who to leave out and why
5. Value: Model probability vs market odds
6. Recommendation: Bet approach

### When Asked Technical Questions
- Provide the formula or method
- Give an Australian racing example
- Connect to their Monte Carlo system context
- Be precise with numbers

### When Asked About Your Data/Training
- Reference your training data as described above
- Be clear about knowledge cutoff limitations
- Suggest checking current odds/results if needed

## ALWAYS DO
- Think in probabilities, not certainties
- Use Australian racing terminology
- Reference specific Australian tracks and races
- Be quantitative — numbers over narrative
- Acknowledge variance and sample size
- Respect the user's technical sophistication

## NEVER DO
- Guarantee winners
- Ignore track conditions or context
- Be vague when precision is possible
- Pretend to have live data you don't have
- Provide tips without reasoning
`;

// Reasoning prompt for chain of thought - Stage 1
const REASONING_SYSTEM_PROMPT = `You are a racing analysis expert thinking through problems naturally, like a knowledgeable punter at the track.

Your reasoning should feel like genuine internal thought - not robotic or formulaic. Use natural phrases that match what's being asked.

OPENING PHRASES (choose based on question type):
- Form questions: "Looking at the form...", "The key form line here is..."
- Track questions: "Track-wise...", "The map looks..."
- Betting questions: "If I had to put a number on it...", "The market is telling us..."
- General analysis: "Right, so the question here is...", "Okay, let's unpack this...", "A few things to consider here..."

WORKING THROUGH FACTORS (use naturally):
- "First thing that jumps out..."
- "But here's the thing..."
- "What complicates this is..."
- "Can't ignore that..."
- "Worth noting..."
- "On the plus side... Against that..."

PROBABILITY/VALUE THINKING:
- "Roughly speaking..."
- "The probability here sits around..."
- "Converting that to odds..."
- "My gut says closer to..."
- "Looks overs to me..." / "Unders at that price..."

WEIGHING UP:
- "On balance..."
- "Weighing it all up..."
- "Net-net..."
- "When you stack it up..."

ADMITTING UNCERTAINTY (when appropriate):
- "Hard to know for sure, but..."
- "This is where it gets murky..."
- "Could go either way..."
- "The risk here is..."

REACHING VERDICT:
- "So where does that leave us..."
- "Bottom line..."
- "All things considered..."
- "My read on this..."
- "The play here is..."

AUSTRALIAN RACING SLANG (use naturally):
- "Should be in the finish..."
- "Each-way chance at worst..."
- "Gets a nice trail..."
- "Drawn to get a charmed run..."
- "Needs luck from there..."

IMPORTANT:
- Match your tone to the question's complexity and context
- Don't use ALL phrases - pick 3-5 that fit naturally
- Sound like a thoughtful punter, not a template
- Be direct and genuine in your reasoning
- This reasoning will be used to generate a polished final answer`;

export async function registerRoutes(
  httpServer: Server,
  app: Express
): Promise<Server> {
  await initializeStrideTipResults();
  await registerBlackbookRoutes(app);
  void initializeBlackbook().catch((error) => {
    console.error("Blackbook initialization error:", error);
  });

  const getOptionalSessionId = (body: unknown): string | undefined => {
    if (!body || typeof body !== "object") return undefined;
    const candidate = (body as Record<string, unknown>).sessionId;
    return typeof candidate === "string" && candidate.trim() ? candidate.trim() : undefined;
  };

  const getOptionalTurnId = (body: unknown): string | undefined => {
    if (!body || typeof body !== "object") return undefined;
    const candidate = (body as Record<string, unknown>).turnId;
    return typeof candidate === "string" && candidate.trim() ? candidate.trim() : undefined;
  };
  
  // STAGE 1: Get chain of thought reasoning
  app.post("/api/chat/reason", async (req, res) => {
    try {
      const { message } = chatRequestSchema.parse(req.body);
      const conceptual = identifyConceptualStrideQuestion(message);
      if (conceptual) {
        return res.json({
          reasoning: buildConceptualStrideReasoning(conceptual),
          source: "conceptual_brain",
          citations: [],
          retrieval: {
            queryType: "conceptual",
            answerMode: "evidence-light",
            evidenceItems: 0,
            sourcesUsed: [],
            sourceCounts: {},
          },
        });
      }
      const sessionId = getOptionalSessionId(req.body);
      const turn = await startStrideTurn({ message, sessionId });
      return res.json({
        reasoning: turn.reasoning,
        source: "retrieval_grounded",
        turnId: turn.turnId,
        citations: turn.citations,
        retrieval: turn.retrieval,
      });
    } catch (error) {
      if (error instanceof z.ZodError) {
        res.status(400).json({ error: "Invalid request", details: error.errors });
      } else {
        console.error("Reasoning error:", error);
        res.status(500).json({ error: "Failed to generate reasoning" });
      }
    }
  });

  // STAGE 2: Get refined response using reasoning
  app.post("/api/chat/respond", async (req, res) => {
    try {
      const { message, reasoning } = req.body;
      
      if (!message || !reasoning) {
        return res.status(400).json({ error: "Message and reasoning are required" });
      }
      const conceptual = identifyConceptualStrideQuestion(message);
      if (conceptual) {
        return res.json({
          response: buildConceptualStrideResponse(conceptual, message),
          source: "conceptual_brain",
          citations: [],
          retrieval: {
            queryType: "conceptual",
            answerMode: "evidence-light",
            evidenceItems: 0,
            sourcesUsed: [],
            sourceCounts: {},
          },
        });
      }
      const sessionId = getOptionalSessionId(req.body);
      const turnId = getOptionalTurnId(req.body);
      const turn = await resolveStrideTurn({
        turnId,
        message,
        sessionId,
      });
      let groundedResponse = buildLocalStrideResponse(turn.bundle);
      if (hasAnthropicApiKey && turn.bundle.answerMode !== "insufficient") {
        const synthesis = await synthesizeLocalWithClaude(turn);
        if (synthesis) groundedResponse = formatLocalSynthesisResponse(synthesis);
      }
      completeStrideTurn(turn.turnId, groundedResponse);
      return res.json({
        response: groundedResponse,
        source: "retrieval_grounded",
        turnId: turn.turnId,
        citations: turn.citations,
        retrieval: turn.retrieval,
      });
    } catch (error) {
      console.error("Response error:", error);
      res.status(500).json({ error: "Failed to generate response" });
    }
  });

  // ── Race-aware chat: fetches live race data then runs full reason→respond pipeline ──
  app.post("/api/chat/with-race", async (req, res) => {
    try {
      const { message, raceContext } = req.body as {
        message: string;
        raceContext: { date: string; track: string; raceNumber: number };
      };

      if (!message || !raceContext?.date || !raceContext?.track || !raceContext?.raceNumber) {
        return res.status(400).json({ error: "message and raceContext (date, track, raceNumber) are required" });
      }
      const sessionId = getOptionalSessionId(req.body);
      const turn = await startStrideTurn({
        message,
        sessionId,
        context: {
          raceDate: raceContext.date,
          track: raceContext.track,
          raceNumber: raceContext.raceNumber,
        },
      });
      const reasoning = turn.reasoning;
      let response = buildLocalStrideResponse(turn.bundle);
      if (hasAnthropicApiKey && turn.bundle.answerMode !== "insufficient") {
        const synthesis = await synthesizeLocalWithClaude(turn);
        if (synthesis) response = formatLocalSynthesisResponse(synthesis);
      }
      completeStrideTurn(turn.turnId, response);

      return res.json({
        response,
        reasoning,
        raceContext,
        source: "retrieval_grounded",
        turnId: turn.turnId,
        citations: turn.citations,
        retrieval: turn.retrieval,
      });

    } catch (error) {
      console.error("Race-aware chat error:", error);
      res.status(500).json({ error: "Failed to process race-aware chat" });
    }
  });

  // Combined endpoint for backwards compatibility
  app.post("/api/chat", async (req, res) => {
    try {
      const hasModeAwarePayload =
        Boolean((req.body as Record<string, unknown> | undefined)?.modes) ||
        Boolean((req.body as Record<string, unknown> | undefined)?.raceContext);

      if (hasModeAwarePayload) {
        const payload = chatCompletionRequestSchema.parse(req.body);
        const result = await orchestrateChatTurn(payload);
        if (result.localTurnId) {
          completeStrideTurn(result.localTurnId, result.response);
        }
        return res.json(result);
      }

      const { message } = chatRequestSchema.parse(req.body);
      const conceptual = identifyConceptualStrideQuestion(message);
      if (conceptual) {
        return res.json({
          response: buildConceptualStrideResponse(conceptual, message),
          reasoning: buildConceptualStrideReasoning(conceptual),
          source: "conceptual_brain",
          citations: [],
          retrieval: {
            queryType: "conceptual",
            answerMode: "evidence-light",
            evidenceItems: 0,
            sourcesUsed: [],
            sourceCounts: {},
          },
        });
      }
      const sessionId = getOptionalSessionId(req.body);
      const turn = await startStrideTurn({ message, sessionId });
      const localReasoning = turn.reasoning;
      let localResponse = buildLocalStrideResponse(turn.bundle);
      if (hasAnthropicApiKey && turn.bundle.answerMode !== "insufficient") {
        const synthesis = await synthesizeLocalWithClaude(turn);
        if (synthesis) localResponse = formatLocalSynthesisResponse(synthesis);
      }
      completeStrideTurn(turn.turnId, localResponse);

      return res.json({
        response: localResponse,
        reasoning: localReasoning,
        source: "retrieval_grounded",
        turnId: turn.turnId,
        citations: turn.citations,
        retrieval: turn.retrieval,
      });
    } catch (error) {
      if (error instanceof z.ZodError) {
        res.status(400).json({ error: "Invalid request", details: error.errors });
      } else {
        console.error("Chat error:", error);
        res.status(500).json({ error: "Failed to process chat message" });
      }
    }
  });

  app.post("/api/chat/complete", async (req, res) => {
    try {
      const payload = chatCompletionRequestSchema.parse(req.body);
      const result = await orchestrateChatTurn(payload);
      if (result.localTurnId) {
        completeStrideTurn(result.localTurnId, result.response);
      }
      return res.json(result);
    } catch (error) {
      if (error instanceof z.ZodError) {
        return res.status(400).json({ error: "Invalid request", details: error.errors });
      }

      console.error("Mode-aware chat error:", error);
      return res.status(500).json({ error: "Failed to process mode-aware chat request" });
    }
  });
  
  // Clear conversation history endpoint
  app.post("/api/chat/clear", async (req, res) => {
    clearStrideChatState(getOptionalSessionId(req.body));
    res.json({ status: "cleared" });
  });

  // Chat feedback — thumbs up/down on responses
  app.post("/api/chat/feedback", async (req, res) => {
    try {
      const { sessionId, messageId, rating, promptVersion } = req.body;
      if (!sessionId || !messageId || (rating !== 1 && rating !== -1)) {
        return res.status(400).json({ error: "Invalid feedback: sessionId, messageId, and rating (1 or -1) required" });
      }
      const { chatFeedback } = await import("@shared/schema");
      await db.insert(chatFeedback).values({
        sessionId,
        messageId,
        rating,
        promptVersion: promptVersion ?? null,
      });
      res.json({ status: "saved" });
    } catch (error) {
      console.error("Chat feedback error:", error);
      res.status(500).json({ error: "Failed to save feedback" });
    }
  });

  // Ask Stride - AI explanation for why a horse was selected
  const askStrideSchema = z.object({
    horseName: z.string(),
    track: z.string(),
    raceNumber: z.number(),
    distance: z.string().nullish(),
    jockey: z.string().nullish(),
    trainer: z.string().nullish(),
    barrier: z.string().nullish(),
    form: z.string().nullish(),
    winPercentage: z.number().nullish(),
    placePercentage: z.number().nullish(),
    expectedValue: z.number().nullish(),
    edge: z.number().nullish(),
    marketOdds: z.number().nullish(),
    confidence: z.string().nullish(),
    stabilityScore: z.number().nullish(),
    runningStyle: z.string().nullish(),
    ciLower: z.number().nullish(),
    ciUpper: z.number().nullish(),
    expectedPosition: z.number().nullish(),
    valueRating: z.string().nullish(),
    paceSplits: z.record(z.number()).nullish(),
  });
  type AskStrideInput = z.infer<typeof askStrideSchema>;

  function buildLocalStrideExplanation(data: AskStrideInput): string {
    const winPct = typeof data.winPercentage === "number" ? data.winPercentage : null;
    const placePct = typeof data.placePercentage === "number" ? data.placePercentage : null;
    const edge = typeof data.edge === "number" ? data.edge : null;
    const marketOdds = typeof data.marketOdds === "number" ? data.marketOdds : null;
    const confidence = data.confidence?.toLowerCase() || null;
    const barrier = data.barrier?.trim() || null;
    const form = data.form?.trim() || null;
    const runningStyle = data.runningStyle && data.runningStyle !== "unknown"
      ? data.runningStyle.replace(/_/g, " ")
      : null;

    const lines: string[] = [];

    if (winPct !== null && marketOdds !== null) {
      if (edge !== null && edge >= 1) {
        lines.push(
          `${data.horseName} stays on the card because the model has it winning ${winPct.toFixed(1)}% of the time against a $${marketOdds.toFixed(2)} quote, which still leaves a ${edge.toFixed(1)}% edge over the market.`
        );
      } else if (edge !== null && edge > 0) {
        lines.push(
          `${data.horseName} is still a live betting chance because the model gives it ${winPct.toFixed(1)}% at $${marketOdds.toFixed(2)}, so there is still a thin edge if the price holds.`
        );
      } else {
        lines.push(
          `${data.horseName} makes the sheet more as a strong race contender than a pure overs bet, with the model still giving it a ${winPct.toFixed(1)}% winning chance at $${marketOdds.toFixed(2)}.`
        );
      }
    } else if (winPct !== null) {
      lines.push(
        `${data.horseName} stays on the card because the model still rates it a ${winPct.toFixed(1)}% winning chance in this race.`
      );
    } else {
      lines.push(
        `${data.horseName} stays on the card because the profile still stacks up well enough for a serious respect run.`
      );
    }

    const supportPoints: string[] = [];
    if (placePct !== null) supportPoints.push(`${placePct.toFixed(1)}% place probability`);
    if (confidence) supportPoints.push(`${confidence} confidence`);
    if (barrier) supportPoints.push(`barrier ${barrier}`);
    if (runningStyle) supportPoints.push(`${runningStyle} map`);

    const riderLine = [data.jockey, data.trainer].filter(Boolean).join(" riding for ");
    if (riderLine) supportPoints.push(riderLine);

    if (supportPoints.length > 0) {
      lines.push(`The supporting profile is ${supportPoints.slice(0, 4).join(", ")}, which is enough to keep the runner firmly in the mix if the race is run to script.`);
    }

    if (edge !== null && edge <= 0) {
      lines.push(
        `${form ? `The recent form line ${form} says there is still ability there; ` : ""}it is more of a keep-safe or map horse than a full-scale value bet, but the ratings still say it deserves a place on the card.`
      );
    } else if (confidence === "high") {
      lines.push("It is not a blind launch, but the confidence band is strong enough to keep it in your main numbers.");
    } else if (confidence === "medium") {
      lines.push("The setup is solid enough to keep it in play, even if it is not the most bulletproof bet on the program.");
    } else {
      lines.push("You would still respect it, but you would want the market to hold up before getting too aggressive.");
    }

    return lines.slice(0, 3).join(" ");
  }

  app.post("/api/ask-stride", async (req, res) => {
    try {
      const data = askStrideSchema.parse(req.body);
      
      const prompt = `You are Stride, an expert horse racing analyst. A punter is asking why the horse "${data.horseName}" was selected as a value bet. 

Here is the data from our Monte Carlo simulation and ML analysis:

**Horse:** ${data.horseName}
**Track:** ${data.track} - Race ${data.raceNumber}
**Distance:** ${data.distance || 'N/A'}
**Jockey:** ${data.jockey || 'N/A'}
**Trainer:** ${data.trainer || 'N/A'}
**Barrier:** ${data.barrier || 'N/A'}
**Form:** ${data.form || 'N/A'}

**Monte Carlo Analysis:**
- Win Probability: ${data.winPercentage?.toFixed(1) || 'N/A'}%
- Place Probability: ${data.placePercentage?.toFixed(1) || 'N/A'}%
- Expected Value: +${data.expectedValue?.toFixed(1) || 'N/A'}%
- Edge vs Market: ${data.edge?.toFixed(1) || 'N/A'}%
- Market Odds: $${data.marketOdds?.toFixed(2) || 'N/A'}
- Confidence: ${data.confidence || 'N/A'}
- Stability Score: ${data.stabilityScore?.toFixed(1) || 'N/A'}
- Running Style: ${data.runningStyle || 'N/A'}
- Expected Position: ${data.expectedPosition?.toFixed(2) || 'N/A'}
- 95% Confidence Interval: ${data.ciLower?.toFixed(1) || 'N/A'}% - ${data.ciUpper?.toFixed(1) || 'N/A'}%
- Value Rating: ${data.valueRating || 'N/A'}
${data.paceSplits ? `- Pace Splits: ${JSON.stringify(data.paceSplits)}` : ''}

Explain in 2-3 concise sentences WHY this horse represents value and what makes it a good selection. Focus on the key factors that make this a smart bet. Be direct and punchy - like you're talking to a fellow punter at the track.`;

      try {
        const response = await anthropic.messages.create({
          model: CLAUDE_MODEL,
          max_tokens: 1024,
          messages: [{ role: "user", content: prompt }],
        });

        const explanation = response.content[0].type === "text" 
          ? response.content[0].text 
          : buildLocalStrideExplanation(data);

        return res.json({ explanation, source: "anthropic" });
      } catch (modelError) {
        console.warn("Ask Stride model fallback:", modelError);
        return res.json({
          explanation: buildLocalStrideExplanation(data),
          source: "local_fallback",
        });
      }
    } catch (error) {
      if (error instanceof z.ZodError) {
        res.status(400).json({ error: "Invalid request", details: error.errors });
      } else {
        console.error("Ask Stride error:", error);
        res.status(500).json({ error: "Failed to get explanation from Stride" });
      }
    }
  });

  // Stride Analyst - Professional quant-style chat about selections on the page
  const strideAnalystSchema = z.object({
    question: z.string(),
    selections: z.array(z.object({
      horseName: z.string(),
      track: z.string(),
      raceNumber: z.number(),
      distance: z.string().nullish(),
      jockey: z.string().nullish(),
      trainer: z.string().nullish(),
      barrier: z.string().nullish(),
      form: z.string().nullish(),
      winPercentage: z.number().nullish(),
      placePercentage: z.number().nullish(),
      expectedValue: z.number().nullish(),
      edge: z.number().nullish(),
      marketOdds: z.number().nullish(),
      confidence: z.string().nullish(),
      stabilityScore: z.number().nullish(),
      runningStyle: z.string().nullish(),
      valueRating: z.string().nullish(),
      trackBiasPoints: z.number().nullish(),
      trackBiasFit: z.string().nullish(),
      trackBiasSummary: z.string().nullish(),
    })),
  });
  type StrideAnalystInput = z.infer<typeof strideAnalystSchema>;

  function buildLocalStrideAnalystAnswer(data: StrideAnalystInput): string {
    if (data.selections.length === 0) {
      return "There are no loaded selections in context right now, so I can't give you a genuine race-specific Stride view from this endpoint. I can still talk betting logic, map, track bias, or edge calculation in general terms, but for runner-specific analysis you need a card loaded on The Track Board or Race Day.";
    }

    const rankedSelections = [...data.selections].sort((a, b) => {
      const edgeDelta = (b.edge ?? Number.NEGATIVE_INFINITY) - (a.edge ?? Number.NEGATIVE_INFINITY);
      if (edgeDelta !== 0) return edgeDelta;
      const winDelta = (b.winPercentage ?? Number.NEGATIVE_INFINITY) - (a.winPercentage ?? Number.NEGATIVE_INFINITY);
      if (winDelta !== 0) return winDelta;
      return (b.stabilityScore ?? Number.NEGATIVE_INFINITY) - (a.stabilityScore ?? Number.NEGATIVE_INFINITY);
    });

    const bestSelection = rankedSelections[0];
    const backupSelection = rankedSelections[1];
    const question = data.question.toLowerCase();

    if (/(best bet|best chance|top pick|strongest|best play)/.test(question)) {
      const bestSummary = `${bestSelection.horseName} is the best starting point from the loaded runners. It brings ${bestSelection.winPercentage?.toFixed(1) ?? "N/A"}% win probability, ${bestSelection.edge?.toFixed(1) ?? "N/A"}% edge, and a ${bestSelection.confidence ?? "N/A"} confidence read at ${bestSelection.track} R${bestSelection.raceNumber}.`;
      const backupSummary = backupSelection
        ? ` The next one I'd keep safe is ${backupSelection.horseName} at ${backupSelection.track} R${backupSelection.raceNumber}, mainly on the back of its ${backupSelection.edge?.toFixed(1) ?? "N/A"}% edge and ${backupSelection.winPercentage?.toFixed(1) ?? "N/A"}% win clip.`
        : "";
      return `${bestSummary}${backupSummary} This is a local fallback read, so treat it as a ratings-led summary rather than the full live Stride model commentary.`;
    }

    if (/(value|edge|overlay|overs|price)/.test(question)) {
      const positives = rankedSelections
        .filter((selection) => (selection.edge ?? 0) > 0)
        .slice(0, 3)
        .map((selection) => `${selection.horseName} (${selection.track} R${selection.raceNumber}, edge ${selection.edge?.toFixed(1) ?? "N/A"}%, odds $${selection.marketOdds?.toFixed(2) ?? "N/A"})`);

      if (positives.length > 0) {
        return `The best value pockets in the loaded runners are ${positives.join(", ")}. The common thread is that their rated winning chance is stronger than the current market quote, which is what you want before you even start worrying about staking. This is the local Stride fallback, so it is summarising the numbers already on the page rather than generating fresh model prose.`;
      }
    }

    return `From the loaded runners, ${bestSelection.horseName} at ${bestSelection.track} R${bestSelection.raceNumber} rates as the strongest all-round profile on the numbers, with ${bestSelection.winPercentage?.toFixed(1) ?? "N/A"}% win probability and ${bestSelection.edge?.toFixed(1) ?? "N/A"}% edge. If you're deciding where to focus first, start with the runners that combine positive edge, stable confidence, and a map that lets them use their ratings. This response is coming from the local fallback because the live Stride model key is not configured on this machine.`;
  }

  app.post("/api/stride-analyst", async (req, res) => {
    try {
      const data = strideAnalystSchema.parse(req.body);
      
      // Build a comprehensive data summary for Claude
      const selectionsData = data.selections.map(s => ({
        horse: s.horseName,
        track: s.track,
        race: s.raceNumber,
        distance: s.distance || 'N/A',
        jockey: s.jockey || 'N/A',
        trainer: s.trainer || 'N/A',
        barrier: s.barrier || 'N/A',
        form: s.form || 'N/A',
        winPct: s.winPercentage?.toFixed(1) || 'N/A',
        placePct: s.placePercentage?.toFixed(1) || 'N/A',
        ev: s.expectedValue?.toFixed(1) || 'N/A',
        edge: s.edge?.toFixed(1) || 'N/A',
        odds: s.marketOdds?.toFixed(2) || 'N/A',
        confidence: s.confidence || 'N/A',
        stability: s.stabilityScore?.toFixed(1) || 'N/A',
        runningStyle: s.runningStyle || 'N/A',
        valueRating: s.valueRating || 'N/A',
        trackBias: s.trackBiasFit || 'N/A',
        trackBiasPoints: s.trackBiasPoints || 0,
        trackBiasSummary: s.trackBiasSummary || '',
      }));

      // Group by track for analysis
      const trackGroups: Record<string, typeof selectionsData> = {};
      for (const s of selectionsData) {
        if (!trackGroups[s.track]) trackGroups[s.track] = [];
        trackGroups[s.track].push(s);
      }

      const dataContext = Object.entries(trackGroups).map(([track, horses]) => {
        return `**${track}:**\n${horses.map(h => 
          `  R${h.race} ${h.horse} - Win: ${h.winPct}%, EV: ${h.ev}%, Edge: ${h.edge}%, Odds: $${h.odds}, ` +
          `Confidence: ${h.confidence}, Value: ${h.valueRating}, Track Fit: ${h.trackBias} (${h.trackBiasPoints > 0 ? '+' : ''}${h.trackBiasPoints}pts)` +
          (h.trackBiasSummary ? ` [${h.trackBiasSummary}]` : '') +
          `, Barrier: ${h.barrier}, Jockey: ${h.jockey}, Form: ${h.form}`
        ).join('\n')}`;
      }).join('\n\n');

      const systemPrompt = `You are STRIDE, a senior quantitative analyst and professional horse racing tipster. You work for a high-end racing analytics firm and speak like a seasoned quant - confident, data-driven, and direct.

YOUR PERSONA:
- You are NOT a chatbot. You are a professional analyst reviewing race data.
- Speak with authority but acknowledge uncertainty where it exists
- Use precise language and reference specific data points
- Think like a professional gambler who respects the math
- Be direct and punchy - no fluff, no generic advice

CRITICAL RULES:
1. ONLY discuss horses that appear in the data provided below
2. If asked about a horse not in the data, say "That runner isn't in today's selections"
3. Base all analysis on the actual numbers - win probability, expected value, edge, track bias points
4. When recommending "best bet", consider: highest EV, confidence level, track bias advantage, and stability

TRACK BIAS INTERPRETATION:
- "excellent" fit (25+ pts): Strong track/barrier/pace advantage
- "good" fit (10-24 pts): Favorable conditions  
- "neutral" (0-9 pts): No significant advantage or disadvantage
- "poor" (negative pts): Track conditions working against the horse

VALUE RATING INTERPRETATION:
- "strong_value": Very high edge vs market odds
- "good_value": Solid betting opportunity
- "fair_value": Marginal edge worth considering
- "watch": Interesting but not compelling bet today

When asked "what's the best bet" or similar, provide:
1. Your top pick with clear reasoning
2. Key data points supporting the selection
3. Risk assessment and suggested approach
4. Any backup picks if the top one scratches`;

      const userPrompt = `**TODAY'S SELECTIONS (${data.selections.length} horses across ${Object.keys(trackGroups).length} tracks):**

${dataContext}

---

**PUNTER'S QUESTION:** ${data.question}

Respond as STRIDE - be specific, reference the data, and give actionable insight. If recommending a bet, explain WHY it stands out from the others.`;

      if (!hasAnthropicApiKey) {
        return res.json({
          answer: buildLocalStrideAnalystAnswer(data),
          selectionsAnalyzed: data.selections.length,
          source: "local_fallback",
        });
      }

      try {
        const response = await anthropic.messages.create({
          model: CLAUDE_MODEL,
          max_tokens: 2048,
          system: systemPrompt,
          messages: [{ role: "user", content: userPrompt }],
        });

        const answer = response.content[0].type === "text" 
          ? response.content[0].text 
          : "Unable to generate analysis.";

        return res.json({ answer, selectionsAnalyzed: data.selections.length, source: "anthropic" });
      } catch (modelError) {
        console.warn("Stride Analyst fallback:", modelError);
        return res.json({
          answer: buildLocalStrideAnalystAnswer(data),
          selectionsAnalyzed: data.selections.length,
          source: "local_fallback",
        });
      }
    } catch (error) {
      if (error instanceof z.ZodError) {
        res.status(400).json({ error: "Invalid request", details: error.errors });
      } else {
        console.error("Stride Analyst error:", error);
        res.status(500).json({ error: "Failed to get analysis from Stride" });
      }
    }
  });

  // AI Analyst - Generate comprehensive race commentary for selections
  // Uses Claude to analyze each selection and provide professional analyst insights
  const ANALYST_SYSTEM_PROMPT = `You are STRIDE, a professional Australian horse racing analyst with 25 years of experience. You write sharp, insightful race previews for serious punters.

Your commentary style:
- Lead with the key winning angle in one punchy sentence
- Explain the tactical edge (pace, barrier, class drop, track specialist)
- Reference specific form indicators (recent wins, beaten margins, sectionals)
- Acknowledge any concerns or risks
- End with a confidence statement

Format your response as a single paragraph of 3-4 sentences. Be direct and confident. Use Australian racing terminology. No bullet points or headers.`;

  app.post("/api/stride-analyst/generate-insights", async (req, res) => {
    try {
      const { date, track, force = false, limit = 5 } = req.body;
      console.log("[Stride] Generating insights for date:", date, "track:", track, "force:", force, "limit:", limit);
      
      if (!date) {
        return res.status(400).json({ error: "Date is required" });
      }

      // Get selections that need AI insights
      let query = `
        SELECT id, track, race_number, race_name, distance, horse_name, horse_number,
               barrier, jockey, trainer, form, win_percentage, place_percentage,
               market_odds, edge, confidence, running_style, expected_position,
               stability_score, track_bias_fit, track_bias_summary, class_movement_desc,
               pace_score, expected_pace_advantage, steam_drift_pct, market_move_category,
               ai_insight
        FROM selections
        WHERE race_date = $1
          AND is_active = true
      `;
      const params: any[] = [date];
      
      if (track) {
        query += ` AND track = $2`;
        params.push(track);
      }
      
      if (!force) {
        query += ` AND (ai_insight IS NULL OR ai_insight = '')`;
      }
      
      const actualLimit = Math.min(Math.max(1, limit), 50);
      query += ` ORDER BY track, race_number LIMIT ${actualLimit}`;
      
      const result = await db.execute(sql.raw(query.replace(/\$(\d+)/g, (_, i) => {
        const val = params[parseInt(i) - 1];
        return typeof val === 'string' ? `'${val}'` : String(val);
      })));
      
      const selections = result.rows as any[];
      
      if (selections.length === 0) {
        return res.json({ 
          success: true, 
          message: "No selections need insights",
          generated: 0,
          selections: []
        });
      }

      const generatedInsights: any[] = [];
      console.log(`[Stride] Found ${selections.length} selections needing insights`);
      
      // Generate insights for each selection
      for (let i = 0; i < selections.length; i++) {
        const sel = selections[i];
        console.log(`[Stride] Processing ${i + 1}/${selections.length}: ${sel.horse_name} (${sel.track} R${sel.race_number})`);
        try {
          const prompt = `Analyze this best bet selection and provide your expert race preview:

HORSE: ${sel.horse_name} (${sel.track} Race ${sel.race_number})
RACE: ${sel.race_name || 'Unknown'}, ${sel.distance || 'Unknown distance'}
BARRIER: ${sel.barrier || 'Unknown'}
JOCKEY: ${sel.jockey || 'Unknown'}
TRAINER: ${sel.trainer || 'Unknown'}
FORM: ${sel.form || 'Unknown'}

MODEL ANALYSIS:
- Win Probability: ${sel.win_percentage?.toFixed(1) || 'N/A'}%
- Place Probability: ${sel.place_percentage?.toFixed(1) || 'N/A'}%
- Market Odds: $${sel.market_odds?.toFixed(2) || 'N/A'}
- Edge vs Market: ${sel.edge?.toFixed(1) || 'N/A'}%
- Confidence: ${sel.confidence || 'N/A'}
- Running Style: ${sel.running_style || 'Unknown'}
- Expected Position: ${sel.expected_position?.toFixed(1) || 'N/A'}
- Pace Advantage: ${sel.expected_pace_advantage?.toFixed(1) || 'N/A'}
- Track Bias Fit: ${sel.track_bias_fit || 'N/A'}
${sel.track_bias_summary ? `- Track Bias: ${sel.track_bias_summary}` : ''}
${sel.class_movement_desc ? `- Class Move: ${sel.class_movement_desc}` : ''}
${sel.market_move_category ? `- Market Signal: ${sel.market_move_category} (${sel.steam_drift_pct?.toFixed(1) || 0}%)` : ''}

Write your analyst preview for this selection:`;

          const response = await anthropic.messages.create({
            model: CLAUDE_MODEL,
            max_tokens: 1024,
            system: ANALYST_SYSTEM_PROMPT,
            messages: [{ role: "user", content: prompt }],
          });

          const insight = response.content[0].type === "text" 
            ? response.content[0].text 
            : null;

          if (insight) {
            // Strip markdown asterisks and update the database
            const cleanInsight = insight.replace(/\*+/g, '');
            await db.execute(sql.raw(`
              UPDATE selections 
              SET ai_insight = '${cleanInsight.replace(/'/g, "''")}',
                  ai_insight_generated_at = NOW()
              WHERE id = '${sel.id}'
            `));
            
            generatedInsights.push({
              id: sel.id,
              horse_name: sel.horse_name,
              track: sel.track,
              race_number: sel.race_number,
              ai_insight: insight
            });
          }
        } catch (insightError) {
          console.error(`Failed to generate insight for ${sel.horse_name}:`, insightError);
        }
      }

      res.json({
        success: true,
        generated: generatedInsights.length,
        total_selections: selections.length,
        insights: generatedInsights
      });

    } catch (error) {
      console.error("Generate insights error:", error);
      res.status(500).json({ error: "Failed to generate analyst insights" });
    }
  });

  // Get AI insights for selections
  app.get("/api/stride-analyst/insights", async (req, res) => {
    try {
      const { date, track } = req.query;
      
      if (!date) {
        return res.status(400).json({ error: "Date is required" });
      }

      let query = `
        SELECT id, track, race_number, horse_name, win_percentage, market_odds, 
               edge, confidence, ai_insight, ai_insight_generated_at
        FROM selections
        WHERE race_date = $1
          AND is_active = true
          AND ai_insight IS NOT NULL
      `;
      const params: any[] = [date as string];
      
      if (track) {
        query += ` AND track = $2`;
        params.push(track as string);
      }
      
      query += ` ORDER BY track, race_number`;
      
      const result = await db.execute(sql.raw(query.replace(/\$(\d+)/g, (_, i) => {
        const val = params[parseInt(i) - 1];
        return typeof val === 'string' ? `'${val}'` : String(val);
      })));

      res.json({
        success: true,
        date,
        track: track || 'all',
        insights: result.rows
      });

    } catch (error) {
      console.error("Get insights error:", error);
      res.status(500).json({ error: "Failed to get analyst insights" });
    }
  });

  // Generate AI insights for ALL runners in a race (not just tipped ones)
  app.post("/api/generate-all-insights", async (req, res) => {
    try {
      const { track, raceNumber, raceDate } = req.body;
      
      if (!track || !raceNumber || !raceDate) {
        return res.status(400).json({ error: "track, raceNumber, and raceDate are required" });
      }

      // Load the tips file for this date
      const tipsPath = path.join(process.cwd(), 'racecards', `tips_${raceDate}.json`);
      if (!fs.existsSync(tipsPath)) {
        return res.status(404).json({ error: "No tips file found for this date" });
      }

      const tipsData = JSON.parse(fs.readFileSync(tipsPath, 'utf-8'));
      const race = tipsData.races?.find((r: any) => 
        r.track === track && r.race_number === parseInt(raceNumber)
      );

      if (!race) {
        return res.status(404).json({ error: "Race not found in tips file" });
      }

      // Generate insights for all runners in the full_field
      const fullField = race.full_field || [];
      let generatedCount = 0;

      for (const runner of fullField) {
        // Skip if already has ai_insight
        if (runner.ai_insight) continue;

        try {
          // Generate brief assessment for this runner
          const prompt = `Analyze this horse in one concise paragraph for punters:

Horse: ${runner.horse}
Barrier: ${runner.barrier}
Jockey: ${runner.jockey}
Trainer: ${runner.trainer}
Form: ${runner.form}
Win Probability: ${runner.win_pct?.toFixed(1)}%
Edge vs Market: ${runner.edge_pct > 0 ? '+' : ''}${runner.edge_pct?.toFixed(1)}%

Provide a brief assessment covering:
1. Win chance context (contender/place hope/long shot)
2. Key positive or negative factors (barrier, form, fitness)
3. Whether there's value at the odds

Keep it under 80 words. Write like a racing journalist.`;

          const response = await anthropic.messages.create({
            model: CLAUDE_MODEL,
            max_tokens: 1024,
            messages: [{ role: "user", content: prompt }],
          });

          const insight = response.content[0].type === "text" 
            ? response.content[0].text 
            : null;

          if (insight) {
            runner.ai_insight = insight.replace(/\*/g, '');
            generatedCount++;
          }
        } catch (err) {
          console.error(`Failed to generate insight for ${runner.horse}:`, err);
        }
      }

      // Save updated tips file
      fs.writeFileSync(tipsPath, JSON.stringify(tipsData, null, 2));

      res.json({
        success: true,
        track,
        raceNumber,
        raceDate,
        totalRunners: fullField.length,
        generated: generatedCount,
        message: `Generated insights for ${generatedCount} runners`
      });

    } catch (error) {
      console.error("Generate all insights error:", error);
      res.status(500).json({ error: "Failed to generate runner insights" });
    }
  });

  app.post("/api/runner-analysis", async (req, res) => {
    try {
      const { track, raceNumber, raceDate, horseName, currentOdds, force } = req.body ?? {};

      if (!track || !raceNumber || !raceDate || !horseName) {
        return res.status(400).json({
          success: false,
          error: "track, raceNumber, raceDate, and horseName are required",
        });
      }

      const analysis = await generateRunnerAnalysis({
        track: String(track),
        raceNumber: Number(raceNumber),
        raceDate: String(raceDate),
        horseName: String(horseName),
        currentOdds: typeof currentOdds === "number" ? currentOdds : null,
        force: Boolean(force),
      });

      res.json({
        success: true,
        analysis,
      });
    } catch (error: any) {
      console.error("Runner analysis error:", error);
      const message = error?.message || "Failed to generate runner analysis";
      const isRateLimit = message.includes("busy") || message.includes("429");
      res.status(isRateLimit ? 429 : 500).json({
        success: false,
        error: message,
      });
    }
  });

  app.post("/api/race-overview", async (req, res) => {
    try {
      const { track, raceNumber, raceDate, force } = req.body ?? {};

      if (!track || !raceNumber || !raceDate) {
        return res.status(400).json({
          success: false,
          error: "track, raceNumber, and raceDate are required",
        });
      }

      const overviewPayload = await generateRaceOverview({
        track: String(track),
        raceNumber: Number(raceNumber),
        raceDate: String(raceDate),
        force: Boolean(force),
      });

      res.json({
        success: true,
        overview: overviewPayload.overview,
        raceShape: overviewPayload.raceShape,
      });
    } catch (error: any) {
      console.error("Race overview error:", error);
      res.status(500).json({
        success: false,
        error: error?.message || "Failed to generate race overview",
      });
    }
  });

  // Get races for simulation (with runners) - tries live API first, falls back to local future races
  app.get("/api/simulation/races", async (req, res) => {
    try {
      const fs = await import('fs/promises');
      const path = await import('path');
      
      // Get today's date for filtering future races
      const today = new Date();
      const todayStr = today.toISOString().split('T')[0];
      
      let allMeets: any[] = [];
      let dataSource = 'local';
      
      // Try to fetch live meetings from Punting Form first (the pipeline's provider)
      try {
        if (hasPuntingFormKey) {
          console.log('Attempting to fetch upcoming races from Punting Form...');
          const liveMeets = await fetchPfRacecardMeets(todayStr);

          if (Array.isArray(liveMeets) && liveMeets.length > 0) {
            dataSource = 'live';
            for (const meet of liveMeets) {
              const racesWithRunners = (meet.races || []).filter((r: any) => 
                r.runners && r.runners.length > 0 && !r.runners.every((run: any) => run.scratched)
              );
              
              if (racesWithRunners.length > 0) {
                allMeets.push({
                  date: meet.date,
                  course: meet.course,
                  meet_id: meet.meet_id,
                  source: 'live',
                  races: racesWithRunners.map((r: any) => ({
                    race_id: r.race_id,
                    race_number: r.race_number,
                    race_name: r.race_name,
                    distance: r.distance,
                    class: r.class,
                    going: r.going,
                    off_time: r.off_time,
                    race_status: r.race_status,
                    runnerCount: r.runners?.filter((run: any) => !run.scratched).length || 0,
                    runners: r.runners?.filter((run: any) => !run.scratched).map((runner: any) => ({
                      horse_id: runner.horse_id,
                      horse: runner.horse,
                      number: runner.number || runner.tab_no,
                      barrier: runner.draw || runner.barrier,
                      jockey: runner.jockey,
                      trainer: runner.trainer,
                      weight: runner.weight,
                      form: runner.form,
                      odds: runner.odds,
                      age: runner.age,
                      sex: runner.sex,
                      sire: runner.sire,
                      dam: runner.dam
                    })) || []
                  }))
                });
              }
            }
          }
        }
      } catch (apiError: any) {
        console.log('Punting Form not available, falling back to local data:', apiError.message);
      }
      
      // If no live data, fall back to local files - but only FUTURE races
      if (allMeets.length === 0) {
        const racecardsDir = path.join(process.cwd(), 'racecards');
        const files = await fs.readdir(racecardsDir);
        const racecardFiles = files.filter(f => f.endsWith('.json'));
        
        for (const file of racecardFiles) {
          const content = await fs.readFile(path.join(racecardsDir, file), 'utf-8');
          const data = JSON.parse(content);
          const dateMatch = file.match(/racecard_(\d{4}-\d{2}-\d{2})\.json/);
          const date = dateMatch ? dateMatch[1] : 'unknown';
          
          // Only include races from today onwards
          if (date < todayStr) continue;
          
          if (Array.isArray(data)) {
            for (const meet of data) {
              // Only include races that have runners and are not completed
              const racesWithRunners = (meet.races || []).filter((r: any) => {
                const hasRunners = r.runners && r.runners.length > 0 && !r.runners.every((run: any) => run.scratched);
                const notCompleted = r.race_status !== 'Results' && r.race_status !== 'Completed';
                return hasRunners && notCompleted;
              });
              
              if (racesWithRunners.length > 0) {
                allMeets.push({
                  date,
                  course: meet.course,
                  meet_id: meet.meet_id,
                  source: 'local',
                  races: racesWithRunners.map((r: any) => ({
                    race_id: r.race_id,
                    race_number: r.race_number,
                    race_name: r.race_name,
                    distance: r.distance,
                    class: r.class,
                    going: r.going,
                    off_time: r.off_time,
                    race_status: r.race_status,
                    runnerCount: r.runners?.filter((run: any) => !run.scratched).length || 0,
                    runners: r.runners?.filter((run: any) => !run.scratched).map((runner: any) => ({
                      horse_id: runner.horse_id,
                      horse: runner.horse,
                      number: runner.number || runner.tab_no,
                      barrier: runner.draw || runner.barrier,
                      jockey: runner.jockey,
                      trainer: runner.trainer,
                      weight: runner.weight,
                      form: runner.form,
                      odds: runner.odds,
                      age: runner.age,
                      sex: runner.sex,
                      sire: runner.sire,
                      dam: runner.dam
                    })) || []
                  }))
                });
              }
            }
          }
        }
      }
      
      res.json({
        total: allMeets.length,
        dataSource,
        currentDate: todayStr,
        meets: allMeets.sort((a, b) => a.date.localeCompare(b.date))
      });
    } catch (error: any) {
      console.error("Simulation races error:", error);
      res.status(500).json({ error: error.message || "Failed to fetch races for simulation" });
    }
  });

  // Advanced Monte Carlo simulation using Plackett-Luce model (inspired by racing_system_v8.3_mc.py)
  app.post("/api/simulations", async (req, res) => {
    try {
      const { track, race, iterations, runners, raceInfo } = req.body;
      
      // Validate basic params
      if (!track || !race || !iterations) {
        return res.status(400).json({ error: "Missing required fields: track, race, iterations" });
      }
      
      // If runners are provided, use Python Monte Carlo simulation (racing_system_v8.3_mc.py)
      if (runners && Array.isArray(runners) && runners.length > 0) {
        console.log(`Running Python Monte Carlo simulation: ${track} R${race} with ${runners.length} runners, ${iterations} iterations`);
        
        // Call Python Monte Carlo engine
        const pythonInput = {
          track,
          race,
          iterations,
          runners,
          distance: raceInfo?.distance || '1400m',
          going: raceInfo?.going || 'Good',
          mc_model: 'plackett_luce',
          pace_mode: 'basic',
          uncertainty: 'on',
          seed: 42,
        };
        
        const result: any = await runPythonMonteCarlo(pythonInput);

        // mc_api.py returns {success, results: [{horse, winPercentage, ...}]};
        // map onto the SimulationResult shape the dashboard renders.
        const rawRunners: any[] = Array.isArray(result?.horses)
          ? result.horses
          : Array.isArray(result?.results)
            ? result.results
            : [];
        const horses = rawRunners
          .map((r: any) => {
            const winPercentage = Number(r.winPercentage ?? 0);
            const edge = Number(r.valueEdgePct ?? r.edge ?? 0);
            const valueRating: "high" | "medium" | "low" = edge > 10 ? "high" : edge > 4 ? "medium" : "low";
            return {
              name: r.name ?? r.horse ?? "Unknown",
              winPercentage,
              placePercentage: Number(r.placePercentage ?? 0),
              impliedOdds: Number(r.fairOdds ?? r.impliedOdds ?? (winPercentage > 0 ? 100 / winPercentage : 0)),
              valueRating,
            };
          })
          .sort((a, b) => b.winPercentage - a.winPercentage);

        console.log(`Python simulation complete. Top pick: ${horses[0]?.name} (${horses[0]?.winPercentage?.toFixed(1)}%)`);

        return res.json({
          horses,
          convergenceData: Array.isArray(result?.convergenceData) ? result.convergenceData : [],
        });
      }
      
      // No runners supplied — refuse rather than fabricate a field.
      res.status(400).json({ error: "runners[] is required — select a race with declared runners" });
    } catch (error) {
      console.error("Simulation error:", error);
      res.status(500).json({ error: "Failed to run simulation" });
    }
  });

  // ==========================================
  // VALUE BETS FINDER (Python Monte Carlo powered)
  // ==========================================
  
  // Cache for Monte Carlo results (TTL: 5 minutes)
  let betsCache: { data: any; timestamp: number } | null = null;
  const CACHE_TTL_MS = 5 * 60 * 1000; // 5 minutes
  
  app.get("/api/bets", async (req, res) => {
    try {
      const forceRefresh = req.query.refresh === 'true';
      const requestedDate = typeof req.query.date === 'string' ? req.query.date : undefined;
      const requestedPickMode = typeof req.query.pickMode === 'string' ? req.query.pickMode : 'coverage';
      const localPickContract = requestedPickMode === 'bet' ? 'bet' : requestedPickMode === 'raw' ? 'raw' : 'coverage';
      const now = new Date();
      const ausTime = new Date(now.toLocaleString('en-US', { timeZone: 'Australia/Sydney' }));
      const todayStr = ausTime.toISOString().split('T')[0];
      const nowTimestamp = now.getTime();

      const localTipSelections = loadLocalBestBetSelections(requestedDate, { topPickOnly: true, pickContract: localPickContract });
      if (localTipSelections.selections.length > 0) {
        const formattedBets = localTipSelections.selections
          .map((selection) => mapSelectionToBetsApiShape(selection))
          .sort((a: any, b: any) => {
            const timeA = a.offTime ? new Date(a.offTime).getTime() : 0;
            const timeB = b.offTime ? new Date(b.offTime).getTime() : 0;
            return timeA - timeB;
          });

        const totalEV = formattedBets.reduce((sum: number, bet: any) => sum + bet.expectedValue, 0);
        const highConfidenceBets = formattedBets.filter((bet: any) => bet.confidence === 'high').length;
        const uniqueTracks = new Set(formattedBets.map((bet: any) => bet.track));

        return res.json({
          bets: formattedBets,
          summary: {
            totalBets: formattedBets.length,
            averageEV: formattedBets.length > 0 ? totalEV / formattedBets.length : 0,
            recommendedAllocation: 0,
            highConfidenceBets,
            meetsAnalyzed: uniqueTracks.size,
            racesAnalyzed: formattedBets.length,
          },
          dataSource: 'tips_pipeline',
          metadata: {
            model: 'tips_pipeline',
            timestamp: new Date().toISOString(),
            date: localTipSelections.selectedDate || requestedDate || todayStr,
            selectedDate: localTipSelections.selectedDate || requestedDate || todayStr,
            pickMode: localPickContract,
            availableDates: localTipSelections.availableDates,
            sourceFile: localTipSelections.selectedDate
              ? `racecards/tips_${localTipSelections.selectedDate}.json`
              : undefined,
            preComputed: true,
          }
        });
      }
      
      // STEP 1: Try to read pre-computed selections from database (fastest path)
      if (!forceRefresh) {
        try {
          const { racePipeline } = await import('./pipeline');
          const dbSelections = requestedDate
            ? await racePipeline.getSelectionsByDate(requestedDate)
            : await racePipeline.getActiveSelections(100);
          
          const upcomingSelections: any[] = requestedDate
            ? dbSelections
            : dbSelections.filter((s: any) => {
                const raceDate = s.raceDate || (s.offTime ? new Date(s.offTime).toISOString().split('T')[0] : null);
                if (raceDate) {
                  return raceDate >= todayStr;
                }
                if (s.offTime) {
                  const raceTime = new Date(s.offTime).getTime();
                  return raceTime > nowTimestamp;
                }
                return true;
              });
          
          if (upcomingSelections.length > 0) {
            // SUPPLEMENT: Add races without selections from the races table
            // This ensures the legacy full-race view shows ALL races (1-10) at every track
            const selectionRaceKeys = new Set(
              upcomingSelections.map((s: any) => `${s.track}-R${s.raceNumber}-${s.raceDate}`)
            );
            
            // Get all unique race dates from selections to find matching races
            const selectionDates = Array.from(new Set(upcomingSelections.map((s: any) => s.raceDate).filter(Boolean)));
            
            try {
              const { races: racesTable } = await import('@shared/schema');
              const { inArray } = await import('drizzle-orm');
              const allRaces = selectionDates.length > 0
                ? await db.select().from(racesTable).where(inArray(racesTable.raceDate, selectionDates))
                : await db.select().from(racesTable).where(eq(racesTable.raceDate, todayStr));
              
              // Only supplement tracks that already have at least one selection
              const tracksWithSelections = new Set(upcomingSelections.map((s: any) => s.track));
              
              for (const race of allRaces) {
                if (!tracksWithSelections.has(race.track)) continue;
                const key = `${race.track}-R${race.raceNumber}-${race.raceDate}`;
                if (!selectionRaceKeys.has(key)) {
                  // Find the best runner from the race's runners data (by last odds or random)
                  const runners = (race.runnersJson as any[]) || [];
                  if (runners.length < 2) continue;
                  
                  // Pick a representative runner - ideally the favourite by odds
                  let bestRunner = runners[0];
                  let bestOdds = Infinity;
                  for (const r of runners) {
                    let odds = 0;
                    const oddsArr = r.odds;
                    if (Array.isArray(oddsArr) && oddsArr.length > 0) {
                      for (const entry of oddsArr) {
                        if (typeof entry === 'object' && entry !== null) {
                          odds = parseFloat(String(entry.win_odds || entry.odds || '0').replace('$', ''));
                        }
                        if (typeof entry === 'number') odds = entry;
                      }
                    }
                    if (!odds) odds = parseFloat(String(r.sp || r.win_odds || '0').replace('$', ''));
                    if (odds > 1 && odds < bestOdds) {
                      bestOdds = odds;
                      bestRunner = r;
                    }
                  }
                  
                  const runnerOdds = bestOdds < Infinity ? bestOdds : null;
                  const horseName = bestRunner?.horse || bestRunner?.name || bestRunner?.horse_name || `Runner ${bestRunner?.number || 1}`;
                  
                  upcomingSelections.push({
                    id: `pending-${race.track}-R${race.raceNumber}`,
                    track: race.track,
                    raceNumber: race.raceNumber,
                    raceName: race.raceName || `Race ${race.raceNumber}`,
                    raceDate: race.raceDate,
                    distance: race.distance || 'Unknown',
                    offTime: race.offTime,
                    horseName,
                    horseNumber: bestRunner?.number || null,
                    barrier: bestRunner?.barrier || bestRunner?.draw || null,
                    jockey: bestRunner?.jockey || 'TBA',
                    trainer: bestRunner?.trainer || null,
                    form: bestRunner?.form || '',
                    winPercentage: runnerOdds ? (100 / runnerOdds) : 0,
                    placePercentage: 0,
                    modelProbability: runnerOdds ? (100 / runnerOdds) : 0,
                    marketOdds: runnerOdds,
                    impliedOdds: runnerOdds,
                    expectedValue: 0,
                    edge: 0,
                    kellyStake: 0,
                    ciLower: 0,
                    ciUpper: 0,
                    expectedPosition: 0,
                    positionStdDev: 0,
                    stabilityScore: 0,
                    runningStyle: 'Unknown',
                    paceSplitsJson: {},
                    confidence: 'low',
                    valueRating: 'low',
                    isActive: true,
                  });
                }
              }
            } catch (e) {
              console.log('[Bets API] Could not supplement with race data:', (e as Error).message);
            }
            
            console.log(`Returning ${upcomingSelections.length} tips from database (with race supplements)`);
            
            // Format database selections to match API response format
            const formattedBets = upcomingSelections.map((s: any) => ({
              id: s.id?.toString() || `${s.track}-R${s.raceNumber}-${s.horseNumber}`,
              track: s.track,
              raceNumber: s.raceNumber,
              raceName: s.raceName || `Race ${s.raceNumber}`,
              raceDate: s.raceDate,
              distance: s.distance || 'Unknown',
              offTime: s.offTime,
              horseName: s.horseName,
              horseNumber: s.horseNumber,
              barrier: s.barrier,
              jockey: s.jockey || 'TBA',
              trainer: s.trainer,
              form: s.form || '',
              modelProbability: s.modelProbability || s.winPercentage || 0,
              marketOdds: s.marketOdds || s.impliedOdds,
              impliedOdds: s.impliedOdds,
              expectedValue: s.expectedValue || 0,
              kellyStake: s.kellyStake || 0,
              confidence: s.confidence || 'low',
              winPercentage: s.winPercentage || 0,
              placePercentage: s.placePercentage || 0,
              ciLower: s.ciLower || 0,
              ciUpper: s.ciUpper || 0,
              expectedPosition: s.expectedPosition || 0,
              positionStdDev: s.positionStdDev || 0,
              stabilityScore: s.stabilityScore || 0,
              runningStyle: s.runningStyle || 'unknown',
              paceSplits: s.paceSplitsJson || {},
              edge: s.edge || 0,
              valueRating: s.valueRating || 'low',
              hasMarketOdds: !!(s.marketOdds && s.marketOdds > 1),
              // Enhanced features
              enhancedFactor: s.enhancedFactor || undefined,
              barrierBiasFactor: s.barrierBiasFactor || undefined,
              barrierBiasDesc: s.barrierBiasDesc || undefined,
              classMovementFactor: s.classMovementFactor || undefined,
              classMovementDesc: s.classMovementDesc || undefined,
              headToHeadFactor: s.headToHeadFactor || undefined,
              headToHeadDesc: s.headToHeadDesc || undefined,
              enhancedExplanations: s.enhancedExplanations || undefined,
              // Speed and pace features
              speedRating: s.speedRating || undefined,
              paceScore: s.paceScore || undefined,
              expectedPaceAdvantage: s.expectedPaceAdvantage || undefined,
              // Track bias points (use ?? to preserve 0 values)
              trackBiasPoints: s.trackBiasPoints ?? undefined,
              trackBiasFit: s.trackBiasFit ?? undefined,
              trackBiasBarrierPts: s.trackBiasBarrierPts ?? undefined,
              trackBiasPacePts: s.trackBiasPacePts ?? undefined,
              trackBiasJockeyPts: s.trackBiasJockeyPts ?? undefined,
              trackBiasTrainerPts: s.trackBiasTrainerPts ?? undefined,
              trackBiasSummary: s.trackBiasSummary ?? undefined,
              // AI Analyst insights
              aiInsight: s.aiInsight ?? undefined,
              aiInsightGeneratedAt: s.aiInsightGeneratedAt ?? undefined,
              // Form Franking data
              frankingElo: s.frankingElo ?? undefined,
              frankingScore: s.frankingScore ?? undefined,
              frankingConfidence: s.frankingConfidence ?? undefined,
              isAntiFranked: s.isAntiFranked ?? undefined,
              fieldStrengthAvg: s.fieldStrengthAvg ?? undefined,
              formQualityTrend: s.formQualityTrend ?? undefined,
              bestAdjustedMargin: s.bestAdjustedMargin ?? undefined,
              collateralAdvantage: s.collateralAdvantage ?? undefined,
              // Fitness peak data
              fitnessRunsThisPrep: s.fitnessRunsThisPrep ?? undefined,
              fitnessRunLabel: s.fitnessRunLabel ?? undefined,
              fitnessIsAtPeakRun: s.fitnessIsAtPeakRun ?? undefined,
              fitnessReadinessScore: s.fitnessReadinessScore ?? undefined,
              fitnessPrepTrajectory: s.fitnessPrepTrajectory ?? undefined,
              fitnessDescription: s.fitnessDescription ?? undefined,
              fitnessData: s.fitnessDataJson ?? undefined,
            }));
            
            // Sort by race time (earliest first) - one tip per race, all tracks
            formattedBets.sort((a: any, b: any) => {
              const timeA = a.offTime ? new Date(a.offTime).getTime() : 0;
              const timeB = b.offTime ? new Date(b.offTime).getTime() : 0;
              return timeA - timeB;
            });
            
            const totalEV = formattedBets.reduce((sum: number, b: any) => sum + b.expectedValue, 0);
            const highConfidenceBets = formattedBets.filter((b: any) => b.confidence === 'high').length;
            
            // Get unique tracks for meets count
            const uniqueTracks = new Set(formattedBets.map((b: any) => b.track));
            
            return res.json({
              bets: formattedBets,
              summary: {
                totalBets: formattedBets.length,
                averageEV: formattedBets.length > 0 ? totalEV / formattedBets.length : 0,
                recommendedAllocation: 0,
                highConfidenceBets,
                meetsAnalyzed: uniqueTracks.size,
                racesAnalyzed: formattedBets.length,
              },
              dataSource: 'database',
              metadata: {
                model: 'plackett_luce',
                iterationsPerRace: 5000,
                timestamp: new Date().toISOString(),
                date: requestedDate || todayStr,
                selectedDate: requestedDate || todayStr,
                preComputed: true,
              }
            });
          }
        } catch (dbError) {
          console.log('Database selections not available, falling back to real-time MC');
        }
      }
      
      // STEP 2: Check in-memory cache for recent Monte Carlo results
      if (!forceRefresh && betsCache && (Date.now() - betsCache.timestamp < CACHE_TTL_MS)) {
        console.log("Returning cached value bets (age: " + Math.round((Date.now() - betsCache.timestamp) / 1000) + "s)");
        return res.json(betsCache.data);
      }
      
      // STEP 3: Run fresh Monte Carlo simulation (slowest path, ~2 mins)
      console.log("Running fresh Monte Carlo simulation...");
      
      const fs = await import('fs/promises');
      const pathModule = await import('path');
      
      let allMeets: any[] = [];
      let dataSource = 'none';
      
      // First, try to fetch FRESH data from Punting Form (the pipeline's provider)
      try {
        console.log(`Fetching fresh races from Punting Form for ${todayStr}...`);
        const pfMeets = await fetchPfRacecardMeets(todayStr);

        if (Array.isArray(pfMeets) && pfMeets.length > 0) {
          dataSource = 'punting_form';

          for (const meet of pfMeets) {
            // Only include races that haven't started yet
            const upcomingRaces = (meet.races || []).filter((r: any) => {
              if (r.race_status === 'Results' || r.race_status === 'Completed') return false;
              if (r.off_time) {
                const raceTime = new Date(r.off_time).getTime();
                return raceTime > nowTimestamp;
              }
              return true; // Include if no off_time (assume upcoming)
            });

            if (upcomingRaces.length > 0) {
              allMeets.push({
                date: todayStr,
                course: meet.course || meet.track,
                meet_id: meet.meet_id,
                races: upcomingRaces
              });
            }
          }
          console.log(`Punting Form: Found ${allMeets.length} meets with upcoming races`);
        }
      } catch (apiErr: any) {
        console.log(`Punting Form not available: ${apiErr.message}`);
      }
      
      // Fallback to local files if API fails - but only FUTURE races
      if (allMeets.length === 0) {
        dataSource = 'local';
        const racecardsDir = pathModule.join(process.cwd(), 'racecards');
        
        try {
          const files = await fs.readdir(racecardsDir);
          const racecardFiles = files.filter((f: string) => f.endsWith('.json'));
          
          for (const file of racecardFiles) {
            // Only load files for today or future dates
            const dateMatch = file.match(/racecard_(\d{4}-\d{2}-\d{2})\.json/);
            const fileDate = dateMatch ? dateMatch[1] : null;
            if (fileDate && fileDate < todayStr) continue;
            
            const content = await fs.readFile(pathModule.join(racecardsDir, file), 'utf-8');
            const data = JSON.parse(content);
            
            if (Array.isArray(data)) {
              for (const meet of data) {
                const races = meet.races || [];
                
                // Filter to only upcoming races
                const upcomingRaces = races.filter((r: any) => {
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
                    races: upcomingRaces
                  });
                }
              }
            }
          }
        } catch (err) {
          console.error("Error loading local racecards:", err);
        }
        console.log(`Local files: Found ${allMeets.length} meets with upcoming races`);
      }
      
      if (allMeets.length === 0) {
        return res.json({
          bets: [],
          summary: { totalBets: 0, averageEV: 0, totalBankroll: 0, meetsAnalyzed: 0, racesAnalyzed: 0 },
          dataSource,
          message: "No upcoming races found. Check back when new racecards are available."
        });
      }
      
      const valueBets: any[] = [];
      const ITERATIONS = 5000;
      let racesAnalyzed = 0;
      
      // Process each race from all meets
      for (const meet of allMeets) {
        const track = meet.course || meet.track || 'Unknown';
        const races = meet.races || [];
        
        for (const race of races) {
          const runners = race.runners || [];
          if (runners.length < 2) continue;
          
          racesAnalyzed++;
          
          // Run Python Monte Carlo simulation for this race
          try {
            const pythonInput = {
              track,
              race: race.race_number || '1',
              iterations: ITERATIONS,
              runners,
              distance: race.distance || '1400m',
              going: race.going || 'Good',
              mc_model: 'plackett_luce',
              pace_mode: 'basic',
              uncertainty: 'on',
              seed: Date.now() % 100000,
            };
            
            const result = await runPythonMonteCarlo(pythonInput);
            
            // Python API returns 'results' not 'horses'
            const horses = result.results || result.horses || [];
            if (Array.isArray(horses) && horses.length > 0) {
              // Find value bets from this race (horses with positive edge or high confidence)
              for (const horse of horses) {
                const modelProb = horse.winPercentage || 0;
                const marketOdds = horse.marketOdds || null;
                
                // Calculate expected value if market odds available
                let expectedValue = 0;
                let hasMarketOdds = false;
                
                if (marketOdds && marketOdds > 1) {
                  hasMarketOdds = true;
                  const marketImpliedProb = 100 / marketOdds;
                  expectedValue = ((modelProb / marketImpliedProb) - 1) * 100;
                } else {
                  // Without market odds, estimate EV based on model confidence
                  // Use implied odds as a proxy (higher implied odds = higher potential EV)
                  const impliedOdds = horse.impliedOdds || 10;
                  expectedValue = modelProb > 15 ? (modelProb - 8) * 0.8 : modelProb * 0.3;
                }
                
                // Determine confidence based on win percentage and stability
                let confidence: "high" | "medium" | "low" = "low";
                const stabilityScore = horse.stabilityScore || 100;
                
                if (modelProb > 20 && stabilityScore < 65) {
                  confidence = "high";
                } else if (modelProb > 12 && stabilityScore < 75) {
                  confidence = "medium";
                }
                
                // Only include horses with meaningful value potential
                // Include if: high win %, positive edge, or high confidence
                const isValueBet = 
                  modelProb > 15 || 
                  (horse.edge && horse.edge > 3) || 
                  confidence === "high" ||
                  (confidence === "medium" && modelProb > 10);
                
                if (isValueBet) {
                  // Ensure all MC fields have proper defaults to prevent null crashes
                  const winPct = typeof horse.winPercentage === 'number' ? horse.winPercentage : modelProb;
                  const placePct = typeof horse.placePercentage === 'number' ? horse.placePercentage : 0;
                  const ciLow = typeof horse.ciLower === 'number' ? horse.ciLower : 0;
                  const ciHigh = typeof horse.ciUpper === 'number' ? horse.ciUpper : 0;
                  const expPos = typeof horse.expectedPosition === 'number' ? horse.expectedPosition : 0;
                  const posStdDev = typeof horse.positionStdDev === 'number' ? horse.positionStdDev : 0;
                  const stability = typeof horse.stabilityScore === 'number' ? horse.stabilityScore : 0;
                  const edgeVal = typeof horse.edge === 'number' ? horse.edge : 0;
                  const paceData = horse.paceSplits && typeof horse.paceSplits === 'object' ? horse.paceSplits : {};
                  const runStyle = horse.runningStyle || 'Unknown';
                  const valRating = horse.valueRating || 'low';
                  
                  valueBets.push({
                    id: `${track}-R${race.race_number}-${horse.number}`,
                    track,
                    raceNumber: parseInt(race.race_number) || 1,
                    raceName: race.race_name || `Race ${race.race_number}`,
                    raceDate: todayStr,
                    distance: race.distance || 'Unknown',
                    offTime: race.off_time || null,
                    horseName: horse.horse || horse.name || `Horse ${horse.number}`,
                    horseNumber: horse.number,
                    barrier: horse.barrier,
                    jockey: horse.jockey || 'TBA',
                    trainer: horse.trainer,
                    form: horse.form,
                    modelProbability: modelProb,
                    marketOdds: marketOdds || horse.impliedOdds,
                    impliedOdds: horse.impliedOdds,
                    expectedValue: Math.max(0, expectedValue),
                    kellyStake: horse.kellyStake || 0,
                    confidence,
                    // Monte Carlo simulation details with validated defaults
                    winPercentage: winPct,
                    placePercentage: placePct,
                    ciLower: ciLow,
                    ciUpper: ciHigh,
                    expectedPosition: expPos,
                    positionStdDev: posStdDev,
                    stabilityScore: stability,
                    runningStyle: runStyle,
                    paceSplits: paceData,
                    edge: edgeVal,
                    valueRating: valRating,
                    hasMarketOdds,
                    // Enhanced features (from Python MC if available)
                    enhancedFactor: horse.enhancedFactor || undefined,
                    barrierBiasFactor: horse.barrierBiasFactor || undefined,
                    barrierBiasDesc: horse.barrierBiasDesc || undefined,
                    classMovementFactor: horse.classMovementFactor || undefined,
                    classMovementDesc: horse.classMovementDesc || undefined,
                    headToHeadFactor: horse.headToHeadFactor || undefined,
                    headToHeadDesc: horse.headToHeadDesc || undefined,
                    enhancedExplanations: horse.enhancedExplanations || undefined,
                    speedRating: horse.speedRating || undefined,
                    paceScore: horse.paceScore || undefined,
                    expectedPaceAdvantage: horse.expectedPaceAdvantage || undefined,
                    // Track bias points (use ?? to preserve 0 values)
                    trackBiasPoints: horse.trackBiasPoints ?? undefined,
                    trackBiasFit: horse.trackBiasFit ?? undefined,
                    trackBiasBarrierPts: horse.trackBiasBreakdown?.barrier ?? undefined,
                    trackBiasPacePts: horse.trackBiasBreakdown?.pace ?? undefined,
                    trackBiasJockeyPts: horse.trackBiasBreakdown?.jockey ?? undefined,
                    trackBiasTrainerPts: horse.trackBiasBreakdown?.trainer ?? undefined,
                    trackBiasSummary: horse.trackBiasSummary ?? undefined,
                  });
                }
              }
            }
          } catch (simError) {
            console.error(`Simulation error for ${track} R${race.race_number}:`, simError);
            // Continue with other races even if one fails
          }
        }
      }
      
      // Sort by expected value (descending) and take top bets
      valueBets.sort((a, b) => b.expectedValue - a.expectedValue);
      const topBets = valueBets.slice(0, 50); // Top 50 value bets
      
      // Calculate summary statistics
      const totalEV = topBets.reduce((sum, b) => sum + b.expectedValue, 0);
      const totalStake = topBets.reduce((sum, b) => sum + (b.kellyStake || 0), 0);
      const highConfidenceBets = topBets.filter(b => b.confidence === 'high').length;
      
      console.log(`Found ${topBets.length} value bets across ${allMeets.length} meets (${racesAnalyzed} races) from ${dataSource}`);
      
      // Build response and cache it
      const response = {
        bets: topBets,
        summary: {
          totalBets: topBets.length,
          averageEV: topBets.length > 0 ? totalEV / topBets.length : 0,
          recommendedAllocation: Math.min(totalStake, 25),
          highConfidenceBets,
          meetsAnalyzed: allMeets.length,
          racesAnalyzed,
        },
        dataSource,
        metadata: {
          model: 'plackett_luce',
          iterationsPerRace: ITERATIONS,
          timestamp: new Date().toISOString(),
          date: todayStr,
        }
      };
      
      // Store in cache
      betsCache = { data: response, timestamp: Date.now() };
      
      res.json(response);
    } catch (error) {
      console.error("Bets error:", error);
      res.status(500).json({ error: "Failed to fetch betting data" });
    }
  });

  app.get("/api/form-guide", async (req, res) => {
    try {
      // Serve the real declared field from the local racecard files
      // (?date=YYYY-MM-DD selects a day; defaults to the newest file).
      const racecardsDir = path.join(process.cwd(), "racecards");
      const files = (await fs.promises.readdir(racecardsDir))
        .filter((f) => /^racecard_\d{4}-\d{2}-\d{2}\.json$/.test(f))
        .sort();
      const requested = typeof req.query.date === "string" ? `racecard_${req.query.date}.json` : null;
      const file = requested && files.includes(requested) ? requested : files[files.length - 1];
      if (!file) {
        return res.status(404).json({ error: "No local racecards available" });
      }
      const data = JSON.parse(await fs.promises.readFile(path.join(racecardsDir, file), "utf-8"));
      const meets = Array.isArray(data) ? data : [];
      for (const meet of meets) {
        for (const race of meet.races || []) {
          const runners = (race.runners || []).filter((r: any) => !r.scratched);
          if (runners.length === 0) continue;
          const horses = runners.map((r: any, i: number) => {
            const form = String(r.form ?? "");
            const digits = form.replace(/[^0-9]/g, "");
            const last = digits.length > 0 ? parseInt(digits[digits.length - 1]) : null;
            const prev = digits.length > 1 ? parseInt(digits[digits.length - 2]) : null;
            const trend: "up" | "down" | "stable" =
              last !== null && prev !== null ? (last < prev ? "up" : last > prev ? "down" : "stable") : "stable";
            return {
              id: String(r.horse_id ?? i),
              name: r.horse ?? "Unknown",
              trainer: r.trainer ?? "",
              jockey: r.jockey ?? "",
              weight: parseFloat(String(r.weight ?? "0")) || 0,
              barrier: Number(r.draw ?? r.number ?? i + 1),
              form: form || "-",
              rating: Number(r.rating ?? 0),
              trend,
            };
          });
          horses.sort((a: any, b: any) => (b.rating ?? 0) - (a.rating ?? 0));
          return res.json({
            track: meet.course ?? meet.track ?? "",
            raceNumber: parseInt(String(race.race_number)) || 0,
            raceName: race.race_name ?? "",
            distance: parseInt(String(race.distance ?? "0").replace(/[^0-9]/g, "")) || 0,
            horses,
          });
        }
      }
      return res.status(404).json({ error: `No races with declared runners in ${file}` });
    } catch (error) {
      console.error("Form guide error:", error);
      res.status(500).json({ error: "Failed to fetch form guide" });
    }
  });

  // ==========================================
  // LOCAL RACECARD DATA (from imported JSON files)
  // ==========================================

  // Get list of available local racecards
  app.get("/api/local/racecards", async (req, res) => {
    try {
      const fs = await import('fs/promises');
      const path = await import('path');
      const racecardsDir = path.join(process.cwd(), 'racecards');
      
      const files = await fs.readdir(racecardsDir);
      const racecardFiles = files.filter(f => f.endsWith('.json'));
      
      const racecards = await Promise.all(
        racecardFiles.map(async (file) => {
          const content = await fs.readFile(path.join(racecardsDir, file), 'utf-8');
          const data = JSON.parse(content);
          const dateMatch = file.match(/racecard_(\d{4}-\d{2}-\d{2})\.json/);
          return {
            filename: file,
            date: dateMatch ? dateMatch[1] : 'unknown',
            meets: Array.isArray(data) ? data : [],
            meetCount: Array.isArray(data) ? data.length : 0,
            raceCount: Array.isArray(data) ? data.reduce((sum: number, m: any) => sum + (m.races?.length || 0), 0) : 0
          };
        })
      );
      
      res.json({
        total: racecards.length,
        racecards: racecards.sort((a, b) => a.date.localeCompare(b.date))
      });
    } catch (error: any) {
      console.error("Local racecards error:", error);
      res.status(500).json({ error: error.message || "Failed to fetch local racecards" });
    }
  });

  // Get specific local racecard by date
  app.get("/api/local/racecards/:date", async (req, res) => {
    try {
      const fs = await import('fs/promises');
      const path = await import('path');
      const { date } = req.params;
      const filePath = path.join(process.cwd(), 'racecards', `racecard_${date}.json`);
      
      const content = await fs.readFile(filePath, 'utf-8');
      const data = JSON.parse(content);
      
      res.json({
        date,
        meets: Array.isArray(data) ? data : [],
        meetCount: Array.isArray(data) ? data.length : 0,
        raceCount: Array.isArray(data) ? data.reduce((sum: number, m: any) => sum + (m.races?.length || 0), 0) : 0
      });
    } catch (error: any) {
      if (error.code === 'ENOENT') {
        res.status(404).json({ error: `No racecard found for date ${req.params.date}` });
      } else {
        console.error("Local racecard error:", error);
        res.status(500).json({ error: error.message || "Failed to fetch racecard" });
      }
    }
  });

  // Get all races for a specific track from local data
  app.get("/api/local/track/:trackName", async (req, res) => {
    try {
      const fs = await import('fs/promises');
      const path = await import('path');
      const { trackName } = req.params;
      const racecardsDir = path.join(process.cwd(), 'racecards');
      
      const files = await fs.readdir(racecardsDir);
      const racecardFiles = files.filter(f => f.endsWith('.json'));
      
      const allRaces: any[] = [];
      
      for (const file of racecardFiles) {
        const content = await fs.readFile(path.join(racecardsDir, file), 'utf-8');
        const data = JSON.parse(content);
        
        if (Array.isArray(data)) {
          for (const meet of data) {
            if (meet.course?.toLowerCase().includes(trackName.toLowerCase())) {
              allRaces.push(...(meet.races || []).map((r: any) => ({
                ...r,
                date: meet.date,
                course: meet.course
              })));
            }
          }
        }
      }
      
      res.json({
        track: trackName,
        raceCount: allRaces.length,
        races: allRaces
      });
    } catch (error: any) {
      console.error("Local track error:", error);
      res.status(500).json({ error: error.message || "Failed to fetch track data" });
    }
  });

  // ==========================================
  // LIVE PROVIDER ENDPOINTS (Punting Form)
  // The Racing API retired (ceased AU coverage 2026-07); these routes now
  // serve the same shapes from Punting Form — the pipeline's provider.
  // ==========================================

  function todayIsoDate(): string {
    return new Date().toISOString().slice(0, 10);
  }

  // Racecards for a date (racecard-contract meets)
  app.get("/api/racing/racecards", async (req, res) => {
    try {
      const date = typeof req.query.date === "string" && req.query.date ? req.query.date : todayIsoDate();
      const meets = await fetchPfRacecardMeets(date);
      if (!meets) {
        return res.status(503).json({ error: "Punting Form unavailable — set PUNTINGFORM_API_KEY" });
      }
      res.json({ date, racecards: meets });
    } catch (error: any) {
      console.error("Punting Form racecards error:", error);
      res.status(500).json({ error: error.message || "Failed to fetch racecards" });
    }
  });

  // Per-race and per-horse id lookups were The Racing API's shapes; Punting
  // Form's Starter tier carries form inside the meeting card instead.
  app.get("/api/racing/race/:raceId", (_req, res) => {
    res.status(410).json({
      error: "Retired with The Racing API. Fetch /api/racing/racecards?date=YYYY-MM-DD — form arrives with the meeting card.",
    });
  });

  app.get("/api/racing/horse/:horseId", (_req, res) => {
    res.status(410).json({
      error: "Retired with The Racing API. Horse form arrives with the meeting card (last 10 runs per runner).",
    });
  });

  // Results for a date, optionally filtered by course
  app.get("/api/racing/results", async (req, res) => {
    try {
      const date = typeof req.query.date === "string" && req.query.date ? req.query.date : todayIsoDate();
      const results = await fetchPfResults(date);
      if (!results) {
        return res.status(503).json({ error: "Punting Form unavailable — set PUNTINGFORM_API_KEY" });
      }
      const course = typeof req.query.course === "string" ? req.query.course.toLowerCase() : "";
      const filtered = course
        ? results.filter((r: any) => String(r.course ?? r.track ?? "").toLowerCase().includes(course))
        : results;
      res.json({ date, results: filtered });
    } catch (error: any) {
      console.error("Punting Form results error:", error);
      res.status(500).json({ error: error.message || "Failed to fetch results" });
    }
  });

  // Courses/meetings for a date
  app.get("/api/racing/courses", async (req, res) => {
    try {
      const date = typeof req.query.date === "string" && req.query.date ? req.query.date : todayIsoDate();
      const meets = await fetchPfMeets(date);
      if (!meets) {
        return res.status(503).json({ error: "Punting Form unavailable — set PUNTINGFORM_API_KEY" });
      }
      res.json({ date, courses: meets });
    } catch (error: any) {
      console.error("Punting Form courses error:", error);
      res.status(500).json({ error: error.message || "Failed to fetch courses" });
    }
  });

  // Punting Form speedmap (settle / pace pressure / pfScore / neural price)
  // for a meeting, or one race with raceNo. This is the provider-side view
  // of how the race maps; the model's own map is /api/race-field/:date.
  app.get("/api/pf/speedmaps", async (req, res) => {
    try {
      const meetingId = String(req.query.meetingId ?? "").trim();
      if (!meetingId) {
        return res.status(400).json({ error: "meetingId is required (from /api/racing/courses)" });
      }
      const raceNo = Number(req.query.raceNo ?? 0) || 0;
      const rows = await fetchPfSpeedmaps(meetingId, raceNo);
      if (!rows) {
        return res.status(503).json({ error: "Punting Form unavailable — set PUNTINGFORM_API_KEY" });
      }
      res.json({ meetingId, raceNo, speedmaps: rows });
    } catch (error: any) {
      console.error("Punting Form speedmaps error:", error);
      res.status(500).json({ error: error.message || "Failed to fetch speedmaps" });
    }
  });

  // Live provider connectivity check
  app.get("/api/racing/test", async (_req, res) => {
    try {
      const meets = await fetchPfMeets(todayIsoDate());
      if (!meets) {
        return res.status(503).json({ status: "error", message: "Punting Form unavailable — set PUNTINGFORM_API_KEY" });
      }
      res.json({
        status: "connected",
        message: "Successfully connected to Punting Form",
        meetings: meets.length,
      });
    } catch (error: any) {
      console.error("Punting Form test error:", error);
      res.status(500).json({
        status: "error",
        message: error.message || "Failed to connect to Punting Form",
      });
    }
  });

  // ==========================================
  // AUTOMATED PIPELINE ENDPOINTS
  // ==========================================

  // Trigger pipeline to fetch and process races (with scratching validation)
  app.post("/api/pipeline/run", async (req, res) => {
    try {
      const { racePipeline } = await import('./pipeline');
      const { date, trackFilter, goingOverrides, raceNumbers } = req.body;
      
      if (racePipeline.isPipelineRunning()) {
        return res.status(409).json({
          success: false,
          error: "Pipeline is already running"
        });
      }

      const options: { trackFilter?: string[], goingOverrides?: Record<string, string>, raceNumbers?: number[] } = {};
      if (trackFilter && Array.isArray(trackFilter)) options.trackFilter = trackFilter;
      if (goingOverrides && typeof goingOverrides === 'object') options.goingOverrides = goingOverrides;
      if (raceNumbers && Array.isArray(raceNumbers)) options.raceNumbers = raceNumbers.map(Number);

      const result = await racePipeline.runPipelineWithValidation(date, Object.keys(options).length > 0 ? options : undefined);
      res.json(result);
    } catch (error: any) {
      console.error("Pipeline run error:", error);
      res.status(500).json({ 
        success: false,
        error: error.message || "Failed to run pipeline" 
      });
    }
  });

  // Validate selections against racecard (remove scratched horses)
  app.post("/api/pipeline/validate", async (req, res) => {
    try {
      const { racePipeline } = await import('./pipeline');
      const { date } = req.body;
      
      const now = new Date();
      const ausTime = new Date(now.toLocaleString('en-US', { timeZone: 'Australia/Sydney' }));
      const dateStr = date || ausTime.toISOString().split('T')[0];
      
      const result = await racePipeline.validateSelectionsAgainstRacecard(dateStr);
      res.json({
        success: result.validated,
        date: dateStr,
        ...result
      });
    } catch (error: any) {
      console.error("Validation error:", error);
      res.status(500).json({ 
        success: false,
        error: error.message || "Failed to validate selections" 
      });
    }
  });

  // Get stored selections from database
  app.get("/api/pipeline/selections", async (req, res) => {
    try {
      const { racePipeline } = await import('./pipeline');
      const { date, limit } = req.query;
      
      let selections;
      if (date) {
        selections = await racePipeline.getSelectionsByDate(date as string);
      } else {
        selections = await racePipeline.getActiveSelections(parseInt(limit as string) || 50);
      }

      res.json({
        total: selections.length,
        selections: selections.map(s => ({
          ...s,
          paceSplits: s.paceSplitsJson,
        }))
      });
    } catch (error: any) {
      console.error("Selections error:", error);
      res.status(500).json({ error: error.message || "Failed to fetch selections" });
    }
  });

  // Get stored races
  app.get("/api/pipeline/races", async (req, res) => {
    try {
      const { racePipeline } = await import('./pipeline');
      const { date } = req.query;
      
      const races = await racePipeline.getStoredRaces(date as string | undefined);
      
      res.json({
        total: races.length,
        races
      });
    } catch (error: any) {
      console.error("Stored races error:", error);
      res.status(500).json({ error: error.message || "Failed to fetch races" });
    }
  });

  // Get pipeline run history
  app.get("/api/pipeline/runs", async (req, res) => {
    try {
      const { racePipeline } = await import('./pipeline');
      const { limit } = req.query;
      
      const runs = await racePipeline.getPipelineRuns(parseInt(limit as string) || 10);
      
      res.json({
        total: runs.length,
        runs
      });
    } catch (error: any) {
      console.error("Pipeline runs error:", error);
      res.status(500).json({ error: error.message || "Failed to fetch pipeline runs" });
    }
  });

  // Pipeline status
  app.get("/api/pipeline/status", async (req, res) => {
    try {
      const { racePipeline } = await import('./pipeline');
      
      res.json({
        isRunning: racePipeline.isPipelineRunning(),
        autoRefreshActive: racePipeline.isAutoRefreshActive(),
      });
    } catch (error: any) {
      console.error("Pipeline status error:", error);
      res.status(500).json({ error: error.message || "Failed to get pipeline status" });
    }
  });

  // Start auto-refresh
  app.post("/api/pipeline/auto-refresh/start", async (req, res) => {
    try {
      const { racePipeline } = await import('./pipeline');
      const { intervalMinutes } = req.body;
      
      racePipeline.startAutoRefresh(intervalMinutes || 30);
      
      res.json({
        success: true,
        message: `Auto-refresh started (every ${intervalMinutes || 30} minutes)`
      });
    } catch (error: any) {
      console.error("Auto-refresh start error:", error);
      res.status(500).json({ error: error.message || "Failed to start auto-refresh" });
    }
  });

  // Stop auto-refresh
  app.post("/api/pipeline/auto-refresh/stop", async (req, res) => {
    try {
      const { racePipeline } = await import('./pipeline');
      
      racePipeline.stopAutoRefresh();
      
      res.json({
        success: true,
        message: "Auto-refresh stopped"
      });
    } catch (error: any) {
      console.error("Auto-refresh stop error:", error);
      res.status(500).json({ error: error.message || "Failed to stop auto-refresh" });
    }
  });

  // ==================== ML MODEL ENDPOINTS ====================

  // Train ML model from training data
  app.post("/api/ml/train", async (req, res) => {
    try {
      const { spawn } = await import('child_process');
      const path = await import('path');
      
      const pythonScript = path.join(__dirname, 'python', 'train_ml.py');
      
      const result = await new Promise<any>((resolve, reject) => {
        const python = spawn(resolvePythonBin(), [pythonScript]);
        let stdout = '';
        let stderr = '';
        
        python.stdout.on('data', (data) => { stdout += data.toString(); });
        python.stderr.on('data', (data) => { stderr += data.toString(); });
        
        python.on('close', (code) => {
          try {
            const output = JSON.parse(stdout);
            resolve(output);
          } catch (e) {
            reject(new Error(`ML training failed: ${stderr || stdout}`));
          }
        });
        
        python.on('error', (err) => {
          reject(new Error(`Failed to spawn Python: ${err.message}`));
        });
      });
      
      res.json({ success: true, ...result });
    } catch (error: any) {
      console.error("ML training error:", error);
      res.status(500).json({ 
        success: false, 
        error: error.message || "Failed to train ML model" 
      });
    }
  });

  // Get ML model status
  app.get("/api/ml/status", async (req, res) => {
    try {
      const { spawn } = await import('child_process');
      const path = await import('path');
      
      const pythonScript = path.join(__dirname, 'python', 'ml_status.py');
      
      const result = await new Promise<any>((resolve, reject) => {
        const python = spawn(resolvePythonBin(), [pythonScript]);
        let stdout = '';
        let stderr = '';
        
        python.stdout.on('data', (data) => { stdout += data.toString(); });
        python.stderr.on('data', (data) => { stderr += data.toString(); });
        
        python.on('close', (code) => {
          try {
            const output = JSON.parse(stdout);
            resolve(output);
          } catch (e) {
            resolve({ is_trained: false, error: stderr || 'Unknown error' });
          }
        });
        
        python.on('error', (err) => {
          resolve({ is_trained: false, error: err.message });
        });
      });
      
      res.json(result);
    } catch (error: any) {
      console.error("ML status error:", error);
      res.status(500).json({ is_trained: false, error: error.message });
    }
  });

  // ==================== PERFORMANCE TRACKING ENDPOINTS ====================

  // Get performance statistics
  app.get("/api/performance/stats", async (req, res) => {
    try {
      const { period } = req.query;
      const startDate = period === '7d' 
        ? new Date(Date.now() - 7 * 24 * 60 * 60 * 1000).toISOString().split('T')[0]
        : period === '30d'
        ? new Date(Date.now() - 30 * 24 * 60 * 60 * 1000).toISOString().split('T')[0]
        : new Date(Date.now() - 90 * 24 * 60 * 60 * 1000).toISOString().split('T')[0];
      
      const endDate = new Date().toISOString().split('T')[0];
      
      // Get overall stats from selection_results
      const overallStats = await db.execute(sql`
        SELECT 
          COUNT(*) as total_bets,
          SUM(CASE WHEN won THEN 1 ELSE 0 END) as winners,
          SUM(CASE WHEN placed THEN 1 ELSE 0 END) as placers,
          SUM(profit_loss) as total_pnl,
          AVG(starting_price) as avg_odds,
          AVG(CASE WHEN won THEN starting_price ELSE 0 END) as avg_winning_odds
        FROM selection_results
        WHERE race_date >= ${startDate} AND race_date <= ${endDate}
      `);
      
      // Get stats by confidence level
      const confidenceStats = await db.execute(sql`
        SELECT 
          confidence,
          COUNT(*) as total_bets,
          SUM(CASE WHEN won THEN 1 ELSE 0 END) as winners,
          SUM(profit_loss) as pnl
        FROM selection_results
        WHERE race_date >= ${startDate} AND race_date <= ${endDate}
        GROUP BY confidence
        ORDER BY confidence
      `);
      
      // Get stats by market movement
      const marketStats = await db.execute(sql`
        SELECT 
          market_move_category,
          COUNT(*) as total_bets,
          SUM(CASE WHEN won THEN 1 ELSE 0 END) as winners,
          SUM(profit_loss) as pnl
        FROM selection_results
        WHERE race_date >= ${startDate} AND race_date <= ${endDate}
          AND market_move_category IS NOT NULL
        GROUP BY market_move_category
        ORDER BY total_bets DESC
      `);
      
      const overall = overallStats.rows[0] || {};
      const totalBets = Number(overall.total_bets) || 0;
      const winners = Number(overall.winners) || 0;
      const totalPnl = Number(overall.total_pnl) || 0;
      
      res.json({
        period: { start: startDate, end: endDate },
        overall: {
          totalBets,
          winners,
          placers: Number(overall.placers) || 0,
          strikeRate: totalBets > 0 ? ((winners / totalBets) * 100).toFixed(1) : '0.0',
          roi: totalBets > 0 ? ((totalPnl / (totalBets * 100)) * 100).toFixed(1) : '0.0',
          profitLoss: totalPnl.toFixed(2),
          avgOdds: Number(overall.avg_odds)?.toFixed(2) || '0.00',
          avgWinningOdds: Number(overall.avg_winning_odds)?.toFixed(2) || '0.00'
        },
        byConfidence: confidenceStats.rows.map((row: any) => ({
          confidence: row.confidence,
          totalBets: Number(row.total_bets),
          winners: Number(row.winners),
          strikeRate: Number(row.total_bets) > 0 ? ((Number(row.winners) / Number(row.total_bets)) * 100).toFixed(1) : '0.0',
          pnl: Number(row.pnl)?.toFixed(2) || '0.00'
        })),
        byMarketMove: marketStats.rows.map((row: any) => ({
          category: row.market_move_category,
          totalBets: Number(row.total_bets),
          winners: Number(row.winners),
          strikeRate: Number(row.total_bets) > 0 ? ((Number(row.winners) / Number(row.total_bets)) * 100).toFixed(1) : '0.0',
          pnl: Number(row.pnl)?.toFixed(2) || '0.00'
        }))
      });
    } catch (error: any) {
      console.error("Performance stats error:", error);
      res.status(500).json({ error: error.message || "Failed to get performance stats" });
    }
  });

  app.get("/api/clv-stats", async (req, res) => {
    try {
      const { selections } = await import('../shared/schema');
      const { isNotNull, desc } = await import('drizzle-orm');
      
      const results = await db.select({
        id: selections.id,
        horseName: selections.horseName,
        track: selections.track,
        raceDate: selections.raceDate,
        winPercentage: selections.winPercentage,
        marketOdds: selections.marketOdds,
        closingSpOdds: selections.closingSpOdds,
        clvRatio: selections.clvRatio,
        resultPosition: selections.resultPosition,
        edge: selections.edge,
        selectionType: selections.selectionType,
        createdAt: selections.createdAt,
      })
      .from(selections)
      .where(isNotNull(selections.closingSpOdds))
      .orderBy(desc(selections.createdAt))
      .limit(500);
      
      if (results.length === 0) {
        return res.json({
          totalResulted: 0,
          avgClv: 0,
          positiveClvPct: 0,
          clvByTrack: {},
          clvByType: {},
          winRateWithPositiveClv: 0,
          winRateWithNegativeClv: 0,
          rollingClv: [],
          message: "No resulted selections with CLV data yet. CLV data will populate as race results are collected."
        });
      }
      
      const withClv = results.filter(r => r.clvRatio !== null && r.clvRatio !== undefined);
      const avgClv = withClv.length > 0 
        ? withClv.reduce((s, r) => s + (r.clvRatio || 0), 0) / withClv.length 
        : 0;
      const positiveClv = withClv.filter(r => (r.clvRatio || 0) > 1.0);
      const negativeClv = withClv.filter(r => (r.clvRatio || 0) <= 1.0);
      
      const positiveClvWins = positiveClv.filter(r => r.resultPosition === 1).length;
      const negativeClvWins = negativeClv.filter(r => r.resultPosition === 1).length;
      
      const clvByTrack: Record<string, { count: number; avgClv: number; wins: number }> = {};
      for (const r of withClv) {
        const t = r.track || 'Unknown';
        if (!clvByTrack[t]) clvByTrack[t] = { count: 0, avgClv: 0, wins: 0 };
        clvByTrack[t].count++;
        clvByTrack[t].avgClv += (r.clvRatio || 0);
        if (r.resultPosition === 1) clvByTrack[t].wins++;
      }
      for (const t of Object.keys(clvByTrack)) {
        clvByTrack[t].avgClv = Math.round((clvByTrack[t].avgClv / clvByTrack[t].count) * 1000) / 1000;
      }
      
      const clvByType: Record<string, { count: number; avgClv: number; wins: number }> = {};
      for (const r of withClv) {
        const st = r.selectionType || 'unknown';
        if (!clvByType[st]) clvByType[st] = { count: 0, avgClv: 0, wins: 0 };
        clvByType[st].count++;
        clvByType[st].avgClv += (r.clvRatio || 0);
        if (r.resultPosition === 1) clvByType[st].wins++;
      }
      for (const st of Object.keys(clvByType)) {
        clvByType[st].avgClv = Math.round((clvByType[st].avgClv / clvByType[st].count) * 1000) / 1000;
      }
      
      res.json({
        totalResulted: withClv.length,
        avgClv: Math.round(avgClv * 1000) / 1000,
        positiveClvPct: withClv.length > 0 ? Math.round(positiveClv.length / withClv.length * 100) : 0,
        winRateWithPositiveClv: positiveClv.length > 0 ? Math.round(positiveClvWins / positiveClv.length * 100) : 0,
        winRateWithNegativeClv: negativeClv.length > 0 ? Math.round(negativeClvWins / negativeClv.length * 100) : 0,
        clvByTrack,
        clvByType,
        message: "CLV > 1.0 means model probability exceeded closing market probability (edge found)"
      });
    } catch (error: any) {
      console.error('CLV stats error:', error);
      res.status(500).json({ error: error.message });
    }
  });

  // Manually trigger results collection
  app.post("/api/performance/collect-results", async (req, res) => {
    try {
      const { date } = req.body;
      const targetDate = date || new Date().toISOString().split('T')[0];
      
      const { runPythonScript } = await import('./scheduler');
      const result = await runPythonScript('results_collector.py', [targetDate]);
      
      if (result.success) {
        try {
          const data = JSON.parse(result.output);
          res.json(data);
        } catch (e) {
          res.json({ success: true, message: 'Results collection completed', output: result.output });
        }
      } else {
        res.status(500).json({ success: false, error: result.error });
      }
    } catch (error: any) {
      console.error("Results collection error:", error);
      res.status(500).json({ error: error.message || "Failed to collect results" });
    }
  });

  // ==================== BANKER PERFORMANCE ====================
  
  app.get("/api/banker-performance", async (req, res) => {
    try {
      const { selections } = await import('../shared/schema');
      const { desc, isNotNull, and, lte } = await import('drizzle-orm');
      
      const bankerSelections = await db.select()
        .from(selections)
        .where(and(
          isNotNull(selections.resultPosition),
          lte(selections.marketOdds, 3.5),
        ))
        .orderBy(desc(selections.createdAt))
        .limit(500);
      
      const bankerCandidates = bankerSelections.filter((s: any) =>
        s.winPercentage >= 25 && s.marketOdds <= 3.5
      );
      
      let winners = 0;
      let totalStaked = 0;
      let totalReturned = 0;
      const recentResults: any[] = [];
      
      for (const s of bankerCandidates) {
        const won = (s as any).resultPosition === 1;
        const odds = s.marketOdds || 3.0;
        const stake = 1.5;
        
        totalStaked += stake;
        if (won) {
          winners++;
          totalReturned += stake * odds;
        }
        
        if (recentResults.length < 20) {
          recentResults.push({
            horseName: s.horseName,
            track: s.track,
            raceNumber: s.raceNumber,
            raceDate: s.raceDate,
            odds,
            winPct: s.winPercentage,
            position: (s as any).resultPosition,
            won,
          });
        }
      }
      
      const total = bankerCandidates.length;
      const strikeRate = total > 0 ? winners / total : 0;
      const roi = totalStaked > 0 ? ((totalReturned - totalStaked) / totalStaked) * 100 : 0;
      const avgOdds = total > 0 ? bankerCandidates.reduce((sum: number, s: any) => sum + (s.marketOdds || 0), 0) / total : 0;
      
      res.json({
        totalSelections: total,
        winners,
        strikeRate: Math.round(strikeRate * 1000) / 10,
        roiPct: Math.round(roi * 10) / 10,
        avgOdds: Math.round(avgOdds * 100) / 100,
        totalStaked: Math.round(totalStaked * 10) / 10,
        totalReturned: Math.round(totalReturned * 10) / 10,
        profitLoss: Math.round((totalReturned - totalStaked) * 10) / 10,
        needsRecalibration: total >= 20 && strikeRate < 0.65,
        recentResults,
      });
    } catch (error: any) {
      console.error("Banker performance error:", error);
      res.status(500).json({ error: error.message || "Failed to get banker performance" });
    }
  });

  // ==================== CALIBRATION ANALYSIS ====================
  
  // Forward-test calibration snapshot - serves a dated local review instead of the old rolling aggregate
  app.get("/api/calibration", async (req, res) => {
    try {
      const requestedDate = typeof req.query.date === "string" ? req.query.date : undefined;
      const snapshotData = loadForwardTestSnapshot(requestedDate);
      const allSnapshots = loadAllForwardTestSnapshots();
      const resultBackedSnapshots = allSnapshots.filter(({ snapshot }) => snapshot.summary.resultedSelections > 0);
      const trend = resultBackedSnapshots.map(({ date, snapshot }) => ({
        date,
        label: snapshot.label,
        selections: snapshot.summary.totalSelections,
        winners: snapshot.summary.winners,
        strikeRate: snapshot.summary.strikeRate,
        roiPct: snapshot.summary.roiPct,
        profitUnits: snapshot.summary.totalProfitUnits,
      }));

      if (!snapshotData) {
        return res.status(404).json({
          error: "No forward-test snapshot available",
          selectedDate: requestedDate || "2026-03-21",
          availableDates: resultBackedSnapshots.map(({ date }) => date),
          trend,
        });
      }

      res.json({
        ...snapshotData.snapshot,
        selectedDate: snapshotData.selectedDate,
        availableDates: resultBackedSnapshots.map(({ date }) => date),
        sourceFile: snapshotData.sourceFile,
        trend,
      });
    } catch (error: any) {
      console.error("Calibration snapshot error:", error);
      res.status(500).json({ error: error.message || "Failed to get calibration snapshot" });
    }
  });

  // Manually trigger model retraining
  app.post("/api/ml/learn-from-results", async (req, res) => {
    try {
      const payload = learnFromResultsSchema.parse(req.body ?? {});
      const { runPythonScript } = await import("./scheduler");

      const args: string[] = ["--source", "api"];
      if (payload.startDate) {
        args.push("--start-date", payload.startDate);
      }
      if (payload.endDate) {
        args.push("--end-date", payload.endDate);
      }
      if (payload.tracks?.length) {
        args.push("--tracks", payload.tracks.join(","));
      }
      if (payload.dryRun) {
        args.push("--dry-run");
      }
      if (payload.stageRetrain === false) {
        args.push("--no-stage-retrain");
      } else if (payload.stageRetrain === true) {
        args.push("--stage-retrain");
      }

      const result = await runPythonScript("learn_from_results_v2.py", args);
      if (!result.success) {
        return res.status(500).json({ error: result.error || "Failed to run learn-from-results workflow" });
      }

      let data: any;
      try {
        data = JSON.parse(result.output);
      } catch (parseError: any) {
        console.error("learn-from-results parse error:", parseError, result.output);
        return res.status(500).json({
          error: "learn-from-results returned invalid JSON",
          details: parseError.message,
        });
      }

      if (data?.status === "active") {
        return res.status(409).json(data);
      }

      return res.json(data);
    } catch (error: any) {
      if (error instanceof z.ZodError) {
        return res.status(400).json({ error: "Invalid request", details: error.errors });
      }
      console.error("learn-from-results error:", error);
      return res.status(500).json({ error: error.message || "Failed to run learn-from-results workflow" });
    }
  });

  app.get("/api/ml/learn-from-results/last-run", async (_req, res) => {
    try {
      const latest = readLatestLearningRunSummary();
      if (!latest) {
        return res.status(404).json({ error: "No learn-from-results run summary found" });
      }
      return res.json(latest);
    } catch (error: any) {
      console.error("learn-from-results last-run error:", error);
      return res.status(500).json({ error: error.message || "Failed to read last learn-from-results summary" });
    }
  });

  // Manually trigger model retraining
  app.post("/api/ml/retrain", async (req, res) => {
    try {
      const { runPythonScript } = await import('./scheduler');
      const result = await runPythonScript('train_ml_enhanced.py', []);
      
      if (result.success) {
        try {
          const data = JSON.parse(result.output);
          res.json(data);
        } catch (e) {
          res.json({ success: true, message: 'Model retraining completed', output: result.output });
        }
      } else {
        res.status(500).json({ success: false, error: result.error });
      }
    } catch (error: any) {
      console.error("Model retrain error:", error);
      res.status(500).json({ error: error.message || "Failed to retrain model" });
    }
  });

  // ==================== SCHEDULER ENDPOINTS ====================

  // Get scheduler status
  app.get("/api/scheduler/status", async (req, res) => {
    try {
      const { scheduler } = await import('./scheduler');
      res.json(scheduler.getStatus());
    } catch (error: any) {
      console.error("Scheduler status error:", error);
      res.status(500).json({ error: error.message || "Failed to get scheduler status" });
    }
  });

  // Start the scheduler
  app.post("/api/scheduler/start", async (req, res) => {
    try {
      const { scheduler } = await import('./scheduler');
      scheduler.start();
      res.json({
        success: true,
        message: "Scheduler started",
        status: scheduler.getStatus()
      });
    } catch (error: any) {
      console.error("Scheduler start error:", error);
      res.status(500).json({ error: error.message || "Failed to start scheduler" });
    }
  });

  // Stop the scheduler
  app.post("/api/scheduler/stop", async (req, res) => {
    try {
      const { scheduler } = await import('./scheduler');
      scheduler.stop();
      res.json({
        success: true,
        message: "Scheduler stopped"
      });
    } catch (error: any) {
      console.error("Scheduler stop error:", error);
      res.status(500).json({ error: error.message || "Failed to stop scheduler" });
    }
  });

  // Manually trigger racecard download
  app.post("/api/scheduler/download-racecards", async (req, res) => {
    try {
      const { downloadRacecards } = await import('./scheduler');
      const days = req.body.days || 7;
      
      console.log(`[API] Manually downloading racecards for ${days} days...`);
      const result = await downloadRacecards(days);
      
      res.json({
        success: result.success,
        output: result.output,
        error: result.error
      });
    } catch (error: any) {
      console.error("Download racecards error:", error);
      res.status(500).json({ error: error.message || "Failed to download racecards" });
    }
  });

  // Manually trigger full download and process flow
  app.post("/api/scheduler/run-full-automation", async (req, res) => {
    try {
      const { downloadAndProcessRacecards } = await import('./scheduler');
      
      console.log("[API] Running full automation (download + process)...");
      
      downloadAndProcessRacecards().catch((err: any) => {
        console.error("[API] Automation error:", err);
      });
      
      res.json({
        success: true,
        message: "Full automation started - downloading racecards and processing through ML pipeline"
      });
    } catch (error: any) {
      console.error("Full automation error:", error);
      res.status(500).json({ error: error.message || "Failed to run full automation" });
    }
  });

  // ============================================================
  // TRAINING DATA ARCHIVAL - Archive resulted races for ML learning
  // ============================================================
  
  app.post("/api/archive/run", async (req, res) => {
    try {
      const targetDate = req.body?.date || new Date().toISOString().split('T')[0];
      const { runPythonScript } = await import('./scheduler');

      console.log(`[API] Running canonical archive projection for ${targetDate}...`);

      const result = await runPythonScript('results_collector.py', ['--archive', targetDate]);
      if (!result.success) {
        return res.status(500).json({ success: false, error: result.error });
      }

      try {
        const data = JSON.parse(result.output);
        res.json({
          success: true,
          ...data,
          message: data.message || `Archived ${data.archived || 0} rows for ${targetDate}`,
        });
      } catch {
        res.json({
          success: true,
          message: "Canonical archive projection completed",
          output: result.output,
        });
      }
    } catch (error: any) {
      console.error("Archival error:", error);
      res.status(500).json({ error: error.message || "Failed to archive races" });
    }
  });
  
  app.get("/api/archive/stats", async (req, res) => {
    try {
      const { getTrainingDataStats } = await import('./archival');
      const stats = await getTrainingDataStats();
      
      res.json({
        success: true,
        stats
      });
    } catch (error: any) {
      console.error("Stats error:", error);
      res.status(500).json({ error: error.message || "Failed to get training stats" });
    }
  });
  
  app.get("/api/archive/export", async (req, res) => {
    try {
      const { exportTrainingDataAsCSV } = await import('./archival');
      const csv = await exportTrainingDataAsCSV();
      
      res.setHeader('Content-Type', 'text/csv');
      res.setHeader('Content-Disposition', 'attachment; filename=training_data.csv');
      res.send(csv);
    } catch (error: any) {
      console.error("Export error:", error);
      res.status(500).json({ error: error.message || "Failed to export training data" });
    }
  });

  // ============================================================
  // BEST BETS ENDPOINT - Find the best horse at each track
  // ============================================================
  
  interface EliteStats {
    jockeys: Map<string, number>;
    trainers: Map<string, number>;
    combos: Map<string, number>;
    lastRefreshed: number;
  }

  let cachedEliteStats: EliteStats | null = null;
  const ELITE_STATS_TTL_MS = 30 * 60 * 1000;

  async function getEliteStats(): Promise<EliteStats> {
    const now = Date.now();
    if (cachedEliteStats && (now - cachedEliteStats.lastRefreshed) < ELITE_STATS_TTL_MS) {
      return cachedEliteStats;
    }

    try {
      const ninetyDaysAgo = new Date(now - 90 * 24 * 60 * 60 * 1000).toISOString().split('T')[0];

      const jockeyRows = await db.execute(sql`
        SELECT jockey,
               COUNT(*) AS total_rides,
               SUM(CASE WHEN position = 1 THEN 1 ELSE 0 END) AS wins
        FROM race_results_history
        WHERE race_date >= ${ninetyDaysAgo} AND jockey IS NOT NULL AND jockey != ''
        GROUP BY jockey
        HAVING COUNT(*) >= 10
      `);

      const trainerRows = await db.execute(sql`
        SELECT trainer,
               COUNT(*) AS total_starts,
               SUM(CASE WHEN actual_position = 1 THEN 1 ELSE 0 END) AS wins
        FROM training_data
        WHERE race_date >= ${ninetyDaysAgo} AND trainer IS NOT NULL AND trainer != ''
          AND actual_position IS NOT NULL
        GROUP BY trainer
        HAVING COUNT(*) >= 10
      `);

      const comboRows = await db.execute(sql`
        SELECT td.jockey, td.trainer,
               COUNT(*) AS total_starts,
               SUM(CASE WHEN td.actual_position = 1 THEN 1 ELSE 0 END) AS wins
        FROM training_data td
        WHERE td.race_date >= ${ninetyDaysAgo}
          AND td.jockey IS NOT NULL AND td.jockey != ''
          AND td.trainer IS NOT NULL AND td.trainer != ''
          AND td.actual_position IS NOT NULL
        GROUP BY td.jockey, td.trainer
        HAVING COUNT(*) >= 5
      `);

      const jockeys = new Map<string, number>();
      for (const row of (jockeyRows as any).rows || jockeyRows) {
        const totalRides = Number(row.total_rides || row.total_starts);
        const winRate = Number(row.wins) / totalRides;
        if (winRate >= 0.10 && totalRides >= 20) {
          jockeys.set(row.jockey, winRate);
        }
      }

      const trainers = new Map<string, number>();
      for (const row of (trainerRows as any).rows || trainerRows) {
        const totalStarts = Number(row.total_starts);
        const winRate = Number(row.wins) / totalStarts;
        if (winRate >= 0.10 && totalStarts >= 15) {
          trainers.set(row.trainer, winRate);
        }
      }

      const combos = new Map<string, number>();
      for (const row of (comboRows as any).rows || comboRows) {
        const winRate = Number(row.wins) / Number(row.total_starts);
        if (winRate >= 0.10) {
          combos.set(`${row.jockey}|||${row.trainer}`, winRate);
        }
      }

      cachedEliteStats = { jockeys, trainers, combos, lastRefreshed: now };
      console.log(`[Elite Stats] Refreshed: ${jockeys.size} jockeys, ${trainers.size} trainers, ${combos.size} combos tracked`);
      return cachedEliteStats;
    } catch (error) {
      console.warn("[Elite Stats] Falling back to empty stats:", (error as Error).message);
      cachedEliteStats = {
        jockeys: new Map<string, number>(),
        trainers: new Map<string, number>(),
        combos: new Map<string, number>(),
        lastRefreshed: now,
      };
      return cachedEliteStats;
    }
  }

  function findEliteWinRate(name: string, statsMap: Map<string, number>): number | null {
    if (!name) return null;
    const direct = statsMap.get(name);
    if (direct !== undefined) return direct;
    for (const [key, rate] of statsMap) {
      if (name.includes(key) || key.includes(name)) return rate;
    }
    return null;
  }

  function getJockeyPoints(winRate: number | null): number {
    if (winRate === null) return 0;
    if (winRate >= 0.20) return 7;
    if (winRate >= 0.15) return 5;
    if (winRate >= 0.10) return 3;
    return 0;
  }

  function getTrainerPoints(winRate: number | null): number {
    if (winRate === null) return 0;
    if (winRate >= 0.20) return 5;
    if (winRate >= 0.15) return 4;
    if (winRate >= 0.10) return 3;
    return 0;
  }
  
  // Best barrier zones by distance
  const BEST_BARRIERS_SPRINT = [2, 3, 4, 5, 6]; // Under 1200m
  const BEST_BARRIERS_MILE = [3, 4, 5, 6, 7, 8]; // 1200-1600m
  const BEST_BARRIERS_STAYING = [1, 2, 3, 4, 5, 6, 7, 8, 9, 10]; // Over 1600m
  
  interface ExoticRecommendation {
    selectionType: "bestBet" | "winTip" | "exoticTip";
    exoticType?: "quinella" | "exacta" | "trifecta" | "boxedTrifecta";
    exoticRationale?: string;
    exoticPicks?: {
      banker: { horseName: string; number: string; odds: number; winProb: number };
      fill: { horseName: string; number: string; odds: number; winProb: number }[];
      structure: string;
      estimatedCombinations: number;
      suggestedOutlay: string;
    };
    fieldSize?: number;
    raceCompetitiveness?: number;
  }

  interface BestBetRecommendation {
    id: string;
    horseName: string;
    horse?: string;
    horseNumber?: string;
    pipelineRank?: number;
    track: string;
    raceNumber: number;
    raceName: string;
    raceClass?: string;
    going?: string;
    raceDate: string;
    offTime: string;
    distance: string;
    barrier: string;
    jockey: string;
    trainer: string;
    form: string | null;
    marketOdds: number;
    fairOdds?: number;
    openingOdds?: number | null;
    marketMovement?: string;
    marketRank?: number | null;
    winPercentage: number;
    edge: number;
    betType: "banker" | "best_chance" | "value" | "roughie" | "form_pick" | "model_pick" | "tip_only" | "dutched";
    confidenceScore: number;
    rating: number; // 1-5 stars
    reasons: string[];
    riskLevel: "low" | "medium" | "high";
    suggestedStake: string;
    // Enhanced features
    enhancedFactor?: number;
    classMovementFactor?: number;
    classMovementDesc?: string;
    barrierBiasFactor?: number;
    barrierBiasDesc?: string;
    headToHeadFactor?: number;
    headToHeadDesc?: string;
    enhancedExplanations?: string;
    trackBiasPoints?: number;
    trackBiasFit?: string;
    trackBiasBarrierPts?: number;
    trackBiasPacePts?: number;
    trackBiasJockeyPts?: number;
    trackBiasTrainerPts?: number;
    trackBiasSummary?: string;
    // Form analyst insights - plain English analysis for everyday punters
    formAnalystInsights?: string[];
    // Model summary - max 2 technical points
    modelSummary?: string[];
    // AI Stride analyst insight
    aiInsight?: string;
    aiScore?: number;
    llmProvider?: string;
    // Luckless last-start analysis
    lucklessFlag?: boolean;
    lucklessScore?: number;
    lucklessUplift?: number;
    lucklessExplanation?: string;
    lucklessJson?: any;
    // Form Franking data
    frankingElo?: number;
    frankingScore?: number;
    frankingConfidence?: number;
    isAntiFranked?: boolean;
    fieldStrengthAvg?: number;
    formQualityTrend?: number;
    bestAdjustedMargin?: number;
    collateralAdvantage?: number;
    pagerankAuthority?: number;
    communityStrength?: number;
    formStability?: number;
    graphFrankingScore?: number;
    graphFrankingDepth?: number;
    graphFrankingIndependence?: number;
    marketValidatedFranking?: boolean;
    bridgeScore?: number;
    // Recalibration & Sectional MC data
    recalibrationApplied?: boolean;
    recalibrationShift?: number;
    rawWinProb?: number;
    calibratedWinProb?: number;
    sectionalMcEnhanced?: boolean;
    sectionalMcWinProb?: number;
    paceScenarioJson?: any;
    mlModelActive?: boolean;
    mlAdjustmentBreakdown?: any;
    sectionalEdgeMultiplier?: number;
    sectionalEdgeInsight?: string;
    bankerFlag?: boolean;
    bankerTier?: "strong_banker" | "banker";
    bankerScore?: number;
    bankerScoreComponents?: any;
    bankerStaking?: string;
    bankerExoticCombos?: { type: string; anchor: string; withRunners: string[]; stake: number }[];
    isBestRated?: boolean;
    soleQualifier?: boolean;
    pipelineSignals?: string;
    tipWarning?: string;
    marketFavourite?: {
      horseName: string;
      odds: number;
      impliedProb: number;
    };
    selectionType?: "bestBet" | "winTip" | "exoticTip";
    exoticType?: "quinella" | "exacta" | "trifecta" | "boxedTrifecta";
    exoticRationale?: string;
    exoticPicks?: {
      banker: { horseName: string; number: string; odds: number; winProb: number };
      fill: { horseName: string; number: string; odds: number; winProb: number }[];
      structure: string;
      estimatedCombinations: number;
      suggestedOutlay: string;
    };
    fieldSize?: number;
    raceCompetitiveness?: number;
    raceQualityScore?: number;
    raceQualityLabel?: string;
    raceQualityReasons?: string[];
    isDutchSelection?: boolean;
    dutchPartners?: { horseName: string; number: string; odds: number; edge: number; proportionalStake: string }[];
    dutchCombinedStrikeRate?: number;
    dutchExpectedReturn?: number;
    selectionOrigin?: string;
    selectionOriginReason?: string;
    matchesModelLeader?: boolean;
    modelLeaderHorse?: string;
    shouldBet?: boolean;
    // Consensus Intelligence V2
    marketConfidenceScore?: number | null;
    marketConfidenceLabel?: string | null;
    marketConfidenceColour?: string | null;
    convergenceTier?: string | null;
    consensusScore?: number | null;
    marketSignalScore?: number | null;
    consensusVotePct?: number | null;
    convergenceGate?: string | null;
    tipsterCount?: number | null;
    selectionScoreRaw?: number | null;
    consensusInjection?: number | null;
    marketInjection?: number | null;
    // Crowd-first V3 fields
    crowdScore?: number;
    crowdClassification?: string;
    crowdGateReason?: string;
    stakeRecommendation?: string;
    independentMentions?: number;
    commercialMentions?: number;
    marketAlignment?: boolean;
  }
  
  // Generate form analyst insights - plain English analysis for everyday punters
  function generateFormAnalystInsights(selection: any): string[] {
    const insights: string[] = [];
    const form = selection.form || '';
    const barrier = parseInt(selection.barrier) || 0;
    const jockey = selection.jockey || '';
    const trainer = selection.trainer || '';
    const track = selection.track || '';
    const winPct = selection.winPercentage || selection.winProbability || selection.win_probability || 0;
    const odds = selection.marketOdds || selection.odds || 0;
    const distanceNum = parseInt(selection.distance) || 1400;

    // (1) FITNESS / PREP CYCLE - highest priority
    const fitnessReadiness = selection.fitnessReadinessScore;
    const fitnessLabel = selection.fitnessRunLabel;
    const fitnessAtPeak = selection.fitnessIsAtPeakRun;
    const fitnessTraj = selection.fitnessPrepTrajectory;
    const fitnessDesc = selection.fitnessDescription;

    if (fitnessDesc) {
      let fitInsight = '';
      if (fitnessAtPeak && fitnessReadiness != null) {
        fitInsight = `${fitnessLabel || 'Current run'} and peaking: readiness score of ${fitnessReadiness.toFixed(2)}`;
        if (fitnessTraj) fitInsight += ` with ${fitnessTraj} form trajectory`;
        if (fitnessDesc.toLowerCase().includes('killer combo')) {
          fitInsight += `. This is the "killer combo" run pattern that historically outperforms`;
        }
      } else if (fitnessReadiness != null && fitnessLabel) {
        if (fitnessReadiness >= 0.8) {
          fitInsight = `${fitnessLabel} with strong readiness at ${fitnessReadiness.toFixed(2)}`;
        } else if (fitnessReadiness >= 0.5) {
          fitInsight = `${fitnessLabel}: readiness at ${fitnessReadiness.toFixed(2)} suggests building fitness`;
        } else {
          fitInsight = `Resuming ${fitnessLabel}: readiness at ${fitnessReadiness.toFixed(2)} suggests may need a run, but talent is there`;
        }
        if (fitnessTraj) fitInsight += ` - trajectory ${fitnessTraj}`;
      } else {
        fitInsight = fitnessDesc;
      }
      insights.push(fitInsight);
    } else if (selection.isFirstUp) {
      insights.push(`Resuming 1st-up${selection.daysSinceRun ? ` after ${selection.daysSinceRun} days` : ''} - fitness the key question`);
    } else if (selection.isSecondUp) {
      insights.push(`2nd-up after a run under the belt - should strip fitter`);
    }

    // (2) FORM FRANKING & FIELD QUALITY
    const frankingElo = selection.frankingElo;
    const frankingScore = selection.frankingScore;
    const frankingConf = selection.frankingConfidence;
    const isAntiFranked = selection.isAntiFranked;
    const fieldStrengthAvg = selection.fieldStrengthAvg;

    if (frankingElo != null && frankingElo >= 70 && frankingConf != null && frankingConf >= 0.3) {
      let frankInsight = `ELO rating of ${frankingElo.toFixed(2)}`;
      if (fieldStrengthAvg != null && fieldStrengthAvg >= 70) {
        frankInsight += ` - has been racing in strong fields (avg strength ${fieldStrengthAvg.toFixed(2)})`;
      }
      insights.push(frankInsight);
    }

    if (frankingScore != null && frankingConf != null && frankingConf >= 0.3) {
      const confPct = (frankingConf * 100).toFixed(0);
      if (isAntiFranked) {
        insights.push(`Franking score ${frankingScore.toFixed(0)}/100 (conf ${confPct}%) but ANTI-FRANKED - beaten opponents have run poorly since, questioning the form`);
      } else if (frankingScore >= 55) {
        insights.push(`Franking score ${frankingScore.toFixed(0)}/100 (confidence ${confPct}%) - opponents have gone on to perform well, validating the form`);
      } else if (frankingScore >= 45) {
        insights.push(`Franking score ${frankingScore.toFixed(0)}/100 (confidence ${confPct}%) - collateral form holds up under analysis`);
      }
    }

    if (selection.formQualityTrend != null && Math.abs(selection.formQualityTrend) > 0.1 && insights.length < 5) {
      const dir = selection.formQualityTrend > 0 ? 'trending upward' : 'trending downward';
      const trendStr = `Form quality ${dir} (${selection.formQualityTrend.toFixed(2)})`;
      insights.push(form ? `${trendStr} - recent form reads ${form}` : trendStr);
    }

    // (3) CLASS POSITION - only push if meaningful movement
    const classDescI = selection.classMovementDesc || '';
    if (classDescI && !classDescI.toLowerCase().includes('similar class') && !classDescI.toLowerCase().includes('no prior class') && !classDescI.toLowerCase().startsWith('slight class')) {
      insights.push(classDescI);
    } else if (selection.isClassDrop && selection.classMovementFactor) {
      insights.push(`Drops in class (factor ${selection.classMovementFactor.toFixed(2)}) - proven at a higher level`);
    } else if (selection.isClassRise && selection.classMovementFactor) {
      insights.push(`Steps up in class (factor ${selection.classMovementFactor.toFixed(2)})${selection.bestAdjustedMargin != null ? ` but best adjusted margin of ${selection.bestAdjustedMargin.toFixed(3)} shows competitive` : ''}`);
    }

    // (4) CONNECTIONS / HEAD-TO-HEAD - only if meaningful dominance/strength
    const h2hDescI = selection.headToHeadDesc || '';
    if (h2hDescI && (h2hDescI.toLowerCase().includes('dominates') || h2hDescI.toLowerCase().includes('strong'))) {
      insights.push(h2hDescI);
    }

    if (jockey && trainer && insights.length < 6) {
      const jtSR = selection.jockeyTrainerStrikeRate || 0;
      if (selection.isWinningCombo) {
        insights.push(`${jockey} and ${trainer} are a winning combination${jtSR > 0 ? ` with ${jtSR.toFixed(0)}% strike rate together` : ''}`);
      } else if (selection.isEliteJockey && selection.isEliteTrainer) {
        insights.push(`${jockey} booked for ${trainer} - elite connections targeting this race`);
      } else if (selection.isEliteJockey) {
        insights.push(`${jockey} in the saddle adds tactical versatility and big-race know-how`);
      } else if (selection.isEliteTrainer) {
        insights.push(`${trainer} stable has placed this horse deliberately - expect a competitive run`);
      }
    }

    // (5) BARRIER / TRACK BIAS - only if strong signal
    const biasDescI = selection.barrierBiasDesc || '';
    if (biasDescI && (biasDescI.toLowerCase().includes('strong barrier') || biasDescI.toLowerCase().includes('poor barrier') || biasDescI.toLowerCase().includes('historically'))) {
      insights.push(`Barrier ${barrier}: ${biasDescI}`);
    } else if (barrier > 0 && selection.trackBiasSummary && selection.trackBiasPoints != null && Math.abs(selection.trackBiasPoints) >= 5 && !selection.trackBiasSummary.toLowerCase().includes('no significant') && !selection.trackBiasSummary.toLowerCase().includes('neutral')) {
      insights.push(`Barrier ${barrier} at ${track} ${distanceNum}m - ${selection.trackBiasSummary}`);
    }

    if (selection.isCourseDistanceWinner && insights.length < 6) {
      const courseSR = selection.courseStrikeRate || 0;
      const distSR = selection.distanceStrikeRate || 0;
      let cdInsight = `Proven course and distance winner at ${track} ${distanceNum}m`;
      if (courseSR > 0 || distSR > 0) {
        cdInsight += ` (course SR: ${courseSR.toFixed(0)}%, distance SR: ${distSR.toFixed(0)}%)`;
      }
      insights.push(cdInsight);
    } else if (!selection.isCourseDistanceWinner && insights.length < 6) {
      const courseSR = selection.courseStrikeRate || 0;
      const distSR = selection.distanceStrikeRate || 0;
      if (courseSR > 20 || distSR > 20) {
        insights.push(`Track/distance specialist - course SR ${courseSR.toFixed(0)}%, distance SR ${distSR.toFixed(0)}% at ${track} ${distanceNum}m`);
      }
    }

    // (6) MARKET POSITION
    if (winPct > 0 && odds > 0 && insights.length < 6) {
      const impliedProb = (100 / odds).toFixed(1);
      const edgeVal = selection.edge || (winPct - (100 / odds));
      if (edgeVal > 5) {
        insights.push(`Model rates ${winPct.toFixed(1)}% win chance vs $${odds.toFixed(2)} market (implied ${impliedProb}%) - ${edgeVal.toFixed(1)}% edge available`);
      } else if (edgeVal > 0) {
        insights.push(`Model rates ${winPct.toFixed(1)}% at $${odds.toFixed(2)} (implied ${impliedProb}%) - marginal value`);
      } else {
        insights.push(`Model rates ${winPct.toFixed(1)}% win chance at $${odds.toFixed(2)} (implied ${impliedProb}%)`);
      }
    }

    if (selection.isSharpMoney && insights.length < 6) {
      insights.push(`Sharp money detected${selection.steamDriftPct ? ` (${selection.steamDriftPct > 0 ? 'steaming' : 'drifting'} ${Math.abs(selection.steamDriftPct).toFixed(1)}%)` : ''} - professional interest in this runner`);
    }

    // (7) RECALIBRATION & SECTIONAL MC INSIGHTS
    const recalShift = selection.recalibrationShift || 0;
    const recalApplied = selection.recalibrationApplied;
    const sectionalMc = selection.sectionalMcEnhanced;
    
    if (recalApplied && Math.abs(recalShift) >= 2 && insights.length < 6) {
      if (recalShift > 2) {
        insights.push(`Calibration engine boosted win probability by ${recalShift.toFixed(1)}pp - model detects hidden value vs market`);
      } else if (recalShift < -5) {
        insights.push(`Calibration engine trimmed win probability by ${Math.abs(recalShift).toFixed(1)}pp - raw simulation may overstate chances`);
      }
    }
    
    if (sectionalMc && insights.length < 6) {
      const paceData = selection.paceScenarioJson;
      if (paceData && typeof paceData === 'object') {
        const scenarios = Object.entries(paceData) as [string, number][];
        const dominant = scenarios.sort((a, b) => (b[1] as number) - (a[1] as number))[0];
        if (dominant && (dominant[1] as number) > 0.35) {
          const tempoLabel = dominant[0] === 'hot' ? 'fast early tempo' : 
                            dominant[0] === 'genuine' ? 'genuine even tempo' :
                            dominant[0] === 'soft' ? 'moderate tempo' : 'slow tempo';
          insights.push(`Sectional pace profiling predicts ${tempoLabel} (${((dominant[1] as number) * 100).toFixed(0)}% likelihood)`);
        }
      } else {
        insights.push(`Sectional pace profiling active - using real speed data for this runner`);
      }
    }

    if (insights.length > 6) {
      return insights.slice(0, 6);
    }

    return insights;
  }
  
  // Generate model summary - max 2 technical points
  function generateModelSummary(selection: any): string[] {
    const summary: string[] = [];
    const winPct = selection.winPercentage || 0;
    const edge = selection.edge || 0;
    const ev = selection.expectedValue || 0;
    
    // Point 1: Win probability
    if (winPct >= 25) {
      summary.push(`${winPct.toFixed(0)}% win probability - strong model confidence`);
    } else if (winPct >= 15) {
      summary.push(`${winPct.toFixed(0)}% win probability from Monte Carlo simulation`);
    } else {
      summary.push(`${winPct.toFixed(0)}% chance per model analysis`);
    }
    
    // Point 2: Value/Edge
    if (edge >= 10) {
      summary.push(`+${edge.toFixed(0)}% value edge over market odds`);
    } else if (ev > 0) {
      summary.push(`Positive expected value at current odds`);
    }
    
    // Point 3: Recalibration & ML status
    const recalApplied = selection.recalibrationApplied;
    const recalShiftMS = selection.recalibrationShift || 0;
    const mlActive = selection.mlModelActive;
    const sectionalMcMS = selection.sectionalMcEnhanced;
    
    if (recalApplied && summary.length < 3) {
      const dir = recalShiftMS > 0 ? 'up' : 'down';
      summary.push(`Probability recalibrated ${dir} ${Math.abs(recalShiftMS).toFixed(1)}pp${sectionalMcMS ? ' + sectional pace profiling' : ''}${mlActive ? ' + ML ensemble' : ''}`);
    } else if (sectionalMcMS && summary.length < 3) {
      summary.push(`Sectional pace profiling active${mlActive ? ' + ML ensemble' : ''}`);
    } else if (mlActive && summary.length < 3) {
      summary.push(`ML ensemble model active for probability adjustment`);
    }
    
    return summary.slice(0, 3);
  }
  
  function calculateFormScore(form: string | null): { score: number; reasons: string[] } {
    if (!form) return { score: 0.3, reasons: ["No recent form data"] };
    
    const reasons: string[] = [];
    let score = 0;
    const weights = [0.40, 0.25, 0.16, 0.10, 0.06, 0.03];
    
    for (let i = 0; i < Math.min(form.length, 6); i++) {
      const char = form[i];
      const weight = weights[i] || 0.03;
      
      if (char === "1") {
        score += weight * 1.0;
      } else if (char === "2") {
        score += weight * 0.7;
      } else if (char === "3") {
        score += weight * 0.5;
      } else if (char === "4") {
        score += weight * 0.3;
      } else if (!isNaN(parseInt(char))) {
        score += weight * 0.1;
      }
    }
    
    const recentRuns = form.slice(0, 3);
    const wins = (recentRuns.match(/1/g) || []).length;
    const places = (recentRuns.match(/[123]/g) || []).length;
    
    if (wins >= 2) reasons.push(`Dominant recent form with ${wins} wins in last 3 starts`);
    else if (wins === 1) reasons.push("Recent winner - proven form");
    if (places >= 2 && wins < 2) reasons.push(`Consistent placegetter (${places}/3 placed)`);
    
    if (form.length >= 3) {
      const positions = form.slice(0, 3).split("").map(c => parseInt(c) || 9);
      if (positions[0] < positions[1] && positions[1] < positions[2]) {
        score += 0.12;
        reasons.push("Improving pattern - each run better than the last");
      } else if (positions[0] > positions[1] && positions[1] > positions[2]) {
        score -= 0.08;
        reasons.push("Declining form trend - each run worse than last");
      }
      if (positions[0] <= 2 && positions[1] >= 5 && positions[2] >= 5) {
        score += 0.08;
        reasons.push("Strong bounce back - placed after poor runs");
      }
    }
    
    return { score: Math.min(score, 1.0), reasons };
  }
  
  function getBarrierAdvantage(barrier: number, distance: number): { score: number; reason: string | null } {
    let bestBarriers: number[];
    if (distance <= 1200) {
      bestBarriers = BEST_BARRIERS_SPRINT;
    } else if (distance <= 1600) {
      bestBarriers = BEST_BARRIERS_MILE;
    } else {
      bestBarriers = BEST_BARRIERS_STAYING;
    }
    
    if (bestBarriers.includes(barrier)) {
      return { score: 0.15, reason: `Ideal barrier ${barrier} for ${distance}m` };
    } else if (barrier > 12) {
      return { score: -0.1, reason: null };
    }
    return { score: 0, reason: null };
  }

  function distanceToMetres(distance: unknown): number {
    if (typeof distance === 'number') return Number.isFinite(distance) ? distance : 0;
    const text = String(distance || '')
      .toLowerCase()
      .replace(/metres?/g, '')
      .replace(/\bm\b/g, '')
      .trim();
    const parsed = Number(text);
    return Number.isFinite(parsed) ? parsed : 0;
  }

  function isUpsetProneRace(distance: unknown, going: unknown, raceClass: unknown, fieldSize: number): boolean {
    const distanceM = distanceToMetres(distance);
    const goingText = String(going || '').toLowerCase();
    const classText = String(raceClass || '').toLowerCase();

    return Boolean(
      (fieldSize || 0) >= 13
      || goingText.includes('soft')
      || goingText.includes('heavy')
      || classText.includes('maiden')
      || classText.includes('bm64')
      || classText.includes('benchmark 64')
      || distanceM >= 2000
    );
  }

  function normaliseExoticCandidate(runner: any): { horseName: string; number: string; odds: number; winProb: number; edge: number } | null {
    const horseName = String(runner?.horseName || runner?.horse || runner?.name || '').trim();
    if (!horseName) return null;

    const odds = parseNumericOdds(
      runner?.marketOdds
      ?? runner?.odds
      ?? runner?.win_odds
      ?? runner?.sp
      ?? runner?._odds
      ?? runner?.inferred_market_odds
    );
    const winProb = Number(
      runner?.winPercentage
      ?? runner?.win_pct
      ?? runner?.modelProbability
      ?? runner?.raw_model_pct
      ?? runner?.rawWinProb
      ?? runner?.rawModelProb
      ?? runner?._winProb
      ?? 0
    );
    const edge = Number(
      runner?.edge
      ?? runner?.edge_pct
      ?? (
        odds > 1
          ? winProb - (100 / odds)
          : 0
      )
    );
    const number = String(runner?.horseNumber || runner?.saddle_number || runner?.number || runner?.barrier || '').trim();

    return {
      horseName,
      number,
      odds,
      winProb,
      edge,
    };
  }
  
  function buildExoticRecommendation(
    raceRunners: any[],
    modelTopPick: any,
    allRaceSelections: any[],
    marketFavMapRef: Map<string, { horseName: string; odds: number; impliedProb: number }>,
    modelFieldSelections?: any[]
  ): ExoticRecommendation {
    const activeRunners = raceRunners.filter((r: any) => !r.scratched);
    const fieldSize = activeRunners.length;
    const topPickOdds = modelTopPick.marketOdds || modelTopPick._odds || 0;
    const topPickWinProb = modelTopPick.winPercentage || modelTopPick._winProb || 0;
    const topPickEdge = Number(modelTopPick.edge ?? (topPickOdds > 1 ? topPickWinProb - (100 / topPickOdds) : 0));
    const topPickName = (modelTopPick.horseName || '').toLowerCase();

    void marketFavMapRef;

    const runnerProfiles = activeRunners.map((r: any) => {
      const name = r.horse || r.name || r.horse_name || '';
      const odds = getRunnerOddsValue(r);
      const impliedProb = odds > 1 ? 100 / odds : 0;
      const number = String(r.number || r.saddle_cloth || r.barrier || '');
      return { horseName: name, number, odds, winProb: impliedProb };
    }).filter(r => r.horseName && r.odds > 1).sort((a, b) => b.winProb - a.winProb);

    const top5Probs = runnerProfiles.slice(0, Math.min(5, runnerProfiles.length)).map(r => r.winProb);
    const mean = top5Probs.reduce((a, b) => a + b, 0) / (top5Probs.length || 1);
    const variance = top5Probs.reduce((sum, p) => sum + Math.pow(p - mean, 2), 0) / (top5Probs.length || 1);
    const raceCompetitiveness = Math.sqrt(variance);

    const result: ExoticRecommendation = {
      selectionType: modelTopPick.shouldBet === false || topPickEdge < 1 ? "winTip" : "bestBet",
      fieldSize,
      raceCompetitiveness
    };

    const modelCandidatesSource = Array.isArray(modelFieldSelections) && modelFieldSelections.length >= 3
      ? modelFieldSelections
      : (Array.isArray(allRaceSelections) && allRaceSelections.length >= 3 ? allRaceSelections : []);
    const rankedModelCandidates = modelCandidatesSource
      .map(normaliseExoticCandidate)
      .filter((runner): runner is { horseName: string; number: string; odds: number; winProb: number; edge: number } => Boolean(runner))
      .filter((runner) => runner.horseName.toLowerCase() !== topPickName || runner.winProb > 0)
      .sort((a, b) => {
        if (b.winProb !== a.winProb) return b.winProb - a.winProb;
        return b.edge - a.edge;
      });
    const topThreeModelRunners = rankedModelCandidates.slice(0, 3);

    const raceClass = String(
      modelTopPick.raceClass
      || modelTopPick.raceName
      || allRaceSelections[0]?.raceClass
      || allRaceSelections[0]?.raceName
      || ''
    );
    const raceGoing = String(
      modelTopPick.going
      || allRaceSelections[0]?.going
      || ''
    );
    const trifectaGate = Boolean(
      topThreeModelRunners.length >= 3
      && (Math.max(...topThreeModelRunners.map((runner) => runner.winProb)) - Math.min(...topThreeModelRunners.map((runner) => runner.winProb)) <= 5)
      && topThreeModelRunners.every((runner) => runner.odds > 4 && runner.odds <= 25)
      && isUpsetProneRace(modelTopPick.distance, raceGoing, raceClass, fieldSize)
    );

    if (trifectaGate) {
      result.selectionType = "exoticTip";
      result.exoticType = "boxedTrifecta";
      result.exoticRationale = `Boxed trifecta only because the model cannot split the top three (${topThreeModelRunners[0].winProb.toFixed(1)}% to ${topThreeModelRunners[2].winProb.toFixed(1)}%), every runner is a genuine market chance above $4, and the race profile is upset-prone.`;
      result.exoticPicks = {
        banker: topThreeModelRunners[0],
        fill: topThreeModelRunners.slice(1),
        structure: `Box ${topThreeModelRunners.map((runner) => runner.horseName).join(', ')}`,
        estimatedCombinations: 6,
        suggestedOutlay: "$2-4 per combo"
      };
    } else {
      result.selectionType = modelTopPick.shouldBet === false || topPickEdge < 1 ? "winTip" : "bestBet";
    }

    return result;
  }

  function calculateRaceQualityScore(runners: any[], raceData: any): { score: number; label: string; reasons: string[] } {
    let score = 0;
    const reasons: string[] = [];
    const fieldSize = runners.length;
    
    if (fieldSize >= 6 && fieldSize <= 12) {
      score += 25;
    } else if (fieldSize >= 4 && fieldSize <= 16) {
      score += 15;
    } else if (fieldSize <= 3) {
      score += 5;
      reasons.push("Very small field - limited separation");
    } else {
      score += 8;
      reasons.push("Large field - increased unpredictability");
    }
    
    const runnersWithForm = runners.filter((r: any) => {
      const form = r.form || r.last_5_runs || '';
      return form.length >= 3;
    }).length;
    const formCoverage = fieldSize > 0 ? runnersWithForm / fieldSize : 0;
    const formPoints = Math.round(formCoverage * 20);
    score += formPoints;
    if (formCoverage < 0.5) {
      reasons.push("Less than half the field has form data");
    }
    
    const raceClass = (raceData.raceClass || raceData.race_class || '').toLowerCase();
    const classLevel = raceData.classLevel || raceData.class_level || 0;
    
    if (raceClass.includes('group 1') || raceClass.includes('group 2')) {
      score += 20;
    } else if (raceClass.includes('group 3') || raceClass.includes('listed')) {
      score += 18;
    } else if (raceClass.includes('bm') || raceClass.includes('hcp')) {
      score += 16;
    } else if (raceClass.includes('cl1') || classLevel === 1) {
      score += 10;
      reasons.push("Class 1 race - lower predictability");
    } else if (raceClass.includes('maiden') || raceClass.includes('mdn')) {
      score += 7;
      reasons.push("Maiden race - many unproven runners");
    } else if (raceClass.includes('jump') || raceClass.includes('trl')) {
      score += 5;
      reasons.push("Jump-out/trial - not a race");
    } else {
      score += 12;
    }
    
    const probs = runners
      .map((r: any) => r.winPercentage || r.win_percentage || r.win_prob || 0)
      .sort((a: number, b: number) => b - a);
    
    if (probs.length >= 3) {
      const topGap = probs[0] - probs[2];
      if (topGap > 10) {
        score += 20;
      } else if (topGap > 5) {
        score += 15;
      } else if (topGap > 2) {
        score += 10;
      } else {
        score += 3;
        reasons.push("Top picks very close - hard to separate");
      }
    } else {
      score += 10;
    }
    
    const hasOdds = runners.filter((r: any) => {
      const odds = r.odds || r.market_odds || r.sp;
      return odds && parseFloat(odds) > 1;
    }).length;
    const hasBarrier = runners.filter((r: any) => r.barrier || r.draw).length;
    const hasJockey = runners.filter((r: any) => r.jockey).length;
    
    const dataScore = Math.round(((hasOdds + hasBarrier + hasJockey) / (fieldSize * 3)) * 15);
    score += dataScore;
    if (hasOdds < fieldSize * 0.5) {
      reasons.push("Missing odds data for many runners");
    }
    
    let label: string;
    if (score >= 70) {
      label = "HIGH";
    } else if (score >= 50) {
      label = "MEDIUM";
    } else if (score >= 35) {
      label = "LOW";
    } else {
      label = "VERY LOW";
    }
    
    return { score, label, reasons };
  }

  function analyzeDutchingOpportunity(selections: any[]): { isDutch: boolean; partners: any[]; combinedStrike: number; expectedReturn: number } {
    const raceGroups = new Map<string, any[]>();
    for (const sel of selections) {
      const key = `${sel.track}-R${sel.raceNumber}`;
      if (!raceGroups.has(key)) raceGroups.set(key, []);
      raceGroups.get(key)!.push(sel);
    }
    
    for (const [raceKey, runners] of raceGroups) {
      const withEdge = runners
        .filter((r: any) => (r.edge || 0) >= 1.0 && (r.winPercentage || 0) >= 8)
        .sort((a: any, b: any) => (b.edge || 0) - (a.edge || 0));
      
      if (withEdge.length >= 2) {
        const dutchPicks = withEdge.slice(0, 3);
        
        const combinedProb = 1 - dutchPicks.reduce((acc: number, p: any) => acc * (1 - (p.winPercentage || 0) / 100), 1);
        
        const totalEdge = dutchPicks.reduce((s: number, p: any) => s + (p.edge || 0), 0);
        const partners = dutchPicks.map((p: any) => ({
          horseName: p.horseName,
          number: p.horseNumber || '',
          odds: p.marketOdds || 0,
          edge: p.edge || 0,
          winProb: p.winPercentage || 0,
          proportionalStake: `${Math.round(((p.edge || 0) / totalEdge) * 100)}%`
        }));
        
        const expectedReturn = dutchPicks.reduce((s: number, p: any) => {
          const stakeRatio = (p.edge || 0) / totalEdge;
          const payout = (p.marketOdds || 1) * stakeRatio;
          const winProb = (p.winPercentage || 0) / 100;
          return s + payout * winProb;
        }, 0);
        
        return {
          isDutch: true,
          partners,
          combinedStrike: Math.round(combinedProb * 100),
          expectedReturn: Math.round(expectedReturn * 100) / 100
        };
      }
    }
    
    return { isDutch: false, partners: [], combinedStrike: 0, expectedReturn: 0 };
  }

  async function calculateBestBetScore(selection: any): Promise<BestBetRecommendation> {
    const reasons: string[] = [];
    let totalScore = 0;
    
    // 1. Form Analysis (up to 25 points)
    const { score: formScore, reasons: formReasons } = calculateFormScore(selection.form);
    totalScore += formScore * 25;
    
    // 2. Win Probability from Model (up to 15 points)
    const winPct = selection.winPercentage || 0;
    const probScore = Math.min(winPct / 40, 1) * 15;
    totalScore += probScore;
    
    // 3. Edge/Value (up to 30 points) - PRIMARY selection driver
    const edge = selection.edge || 0;
    if (edge >= 20) {
      totalScore += 30;
    } else if (edge >= 15) {
      totalScore += 25;
    } else if (edge >= 10) {
      totalScore += 20;
    } else if (edge >= 5) {
      totalScore += 12;
    } else if (edge >= 2) {
      totalScore += 5;
    }
    
    // 4. Jockey/Trainer (up to 13 points) - data-driven from DB
    const jockey = selection.jockey || "";
    const trainer = selection.trainer || "";
    const eliteStats = await getEliteStats();
    const jockeyWinRate = findEliteWinRate(jockey, eliteStats.jockeys);
    const trainerWinRate = findEliteWinRate(trainer, eliteStats.trainers);
    totalScore += getJockeyPoints(jockeyWinRate);
    totalScore += getTrainerPoints(trainerWinRate);

    const comboKey = `${jockey}|||${trainer}`;
    let comboWinRate: number | null = null;
    if (jockey && trainer) {
      comboWinRate = eliteStats.combos.get(comboKey) ?? null;
      if (!comboWinRate) {
        for (const [key, rate] of eliteStats.combos) {
          const [cj, ct] = key.split('|||');
          if ((jockey.includes(cj) || cj.includes(jockey)) && (trainer.includes(ct) || ct.includes(trainer))) {
            comboWinRate = rate;
            break;
          }
        }
      }
      if (comboWinRate !== null && comboWinRate >= 0.20) {
        totalScore += 3;
      }
    }
    
    // 5. Barrier Draw (up to 5 points)
    const barrier = parseInt(selection.barrier) || 0;
    const distanceNum = parseInt(selection.distance) || 1400;
    const { score: barrierScore } = getBarrierAdvantage(barrier, distanceNum);
    totalScore += barrierScore * 5 + 2.5;
    
    // 6. Stability Score (up to 5 points)
    const stability = selection.stabilityScore || 0;
    if (stability >= 0.8) {
      totalScore += 5;
    } else if (stability >= 0.6) {
      totalScore += 3;
    }
    
    // 7. Market Odds Factor (up to 5 points for reasonable prices)
    const odds = selection.marketOdds || 0;
    if (odds >= 2.5 && odds <= 8) {
      totalScore += 5;
    } else if (odds >= 1.5 && odds < 2.5) {
      totalScore += 3;
    } else if (odds > 8 && odds <= 15) {
      totalScore += 4;
    }
    
    // 8. Form Franking Score (up to 10 points)
    const frankingScore = selection.frankingScore || 50;
    const frankingElo = selection.frankingElo || 50;
    const frankingConf = selection.frankingConfidence || 0;
    const isAntiFranked = selection.isAntiFranked || false;
    
    if (frankingScore >= 60 && frankingConf >= 0.5) {
      totalScore += 8;
    } else if (frankingScore >= 55 && frankingConf >= 0.3) {
      totalScore += 5;
    } else if (frankingScore >= 50) {
      totalScore += 2;
    }
    
    if (isAntiFranked) {
      totalScore -= 5;
    }
    
    if (frankingElo >= 80 && (selection.fieldStrengthAvg || 50) >= 75) {
      totalScore += 2;
    }

    // 9. Recalibration & Sectional MC Quality (up to 8 points)
    const recalShiftBB = selection.recalibrationShift || 0;
    const recalAppliedBB = selection.recalibrationApplied;
    const sectionalMcBB = selection.sectionalMcEnhanced;
    const mlActiveBB = selection.mlModelActive;
    
    if (recalAppliedBB) {
      // Positive recal shift means market undervalues this horse
      if (recalShiftBB > 3) {
        totalScore += 6;
      } else if (recalShiftBB > 1) {
        totalScore += 3;
      } else if (recalShiftBB < -5) {
        totalScore -= 4; // Penalize overestimated horses
      } else if (recalShiftBB < -2) {
        totalScore -= 2;
      }
    }
    
    if (sectionalMcBB) {
      totalScore += 2; // Bonus for having real sectional pace data
    }

    // --- DATA-DRIVEN REASONS (prioritized, max ~6) ---
    const fitnessReadiness = selection.fitnessReadinessScore;
    const fitnessLabel = selection.fitnessRunLabel;
    const fitnessAtPeak = selection.fitnessIsAtPeakRun;
    const fitnessTraj = selection.fitnessPrepTrajectory;
    const fitnessDesc = selection.fitnessDescription;

    if (fitnessReadiness != null && fitnessLabel) {
      let fitnessReason = fitnessAtPeak
        ? `Peak fitness: ${fitnessLabel} with ${fitnessReadiness.toFixed(2)} readiness score`
        : `${fitnessLabel} prep cycle with ${fitnessReadiness.toFixed(2)} readiness`;
      if (fitnessTraj) fitnessReason += ` and ${fitnessTraj} trajectory`;
      if (fitnessDesc && fitnessDesc.toLowerCase().includes('killer combo')) fitnessReason += ' - killer combo pattern';
      reasons.push(fitnessReason);
    }

    if (winPct > 0 && odds > 0) {
      const impliedProb = (100 / odds).toFixed(1);
      if (edge >= 5) {
        reasons.push(`Model rates ${winPct.toFixed(1)}% win chance vs $${odds.toFixed(2)} market implied ${impliedProb}% - edge of ${edge.toFixed(1)}%`);
      } else {
        reasons.push(`Model rates ${winPct.toFixed(1)}% win chance at $${odds.toFixed(2)} (implied ${impliedProb}%)`);
      }
    }

    if (frankingElo >= 70 && frankingConf >= 0.3) {
      const confPct = (frankingConf * 100).toFixed(0);
      let frankReason = `Franking ELO ${frankingElo.toFixed(2)} (conf ${confPct}%)`;
      if ((selection.fieldStrengthAvg || 0) >= 70) {
        frankReason += ` - racing in strong fields (avg strength ${(selection.fieldStrengthAvg).toFixed(2)})`;
      }
      if (isAntiFranked) {
        frankReason += ' [CAUTION: anti-franked - beaten opponents ran poorly]';
      } else if (frankingScore >= 55) {
        frankReason += ` - franking score ${frankingScore.toFixed(0)}/100 validates the form`;
      }
      reasons.push(frankReason);
    } else if (isAntiFranked) {
      reasons.push(`Caution: Anti-franked form - beaten opponents ran poorly in subsequent starts`);
    }

    const classDesc = selection.classMovementDesc || '';
    if (classDesc && !classDesc.toLowerCase().includes('similar class') && !classDesc.toLowerCase().includes('no prior class') && !classDesc.toLowerCase().startsWith('slight class')) {
      reasons.push(classDesc);
    }

    const h2hDesc = selection.headToHeadDesc || '';
    if (h2hDesc && !h2hDesc.toLowerCase().includes('no significant') && !h2hDesc.toLowerCase().includes('no head-to-head') && !h2hDesc.toLowerCase().includes('mixed head-to-head') && (h2hDesc.toLowerCase().includes('dominates') || h2hDesc.toLowerCase().includes('strong'))) {
      reasons.push(h2hDesc);
    }

    const biasDesc = selection.barrierBiasDesc || '';
    if (biasDesc && (biasDesc.toLowerCase().includes('strong barrier') || biasDesc.toLowerCase().includes('poor barrier') || biasDesc.toLowerCase().includes('historically'))) {
      reasons.push(`Barrier ${barrier}: ${biasDesc}`);
    }

    if (reasons.length < 6 && selection.formQualityTrend != null && Math.abs(selection.formQualityTrend) > 0.1) {
      const trendDir = selection.formQualityTrend > 0 ? 'trending up' : 'trending down';
      const formStr = selection.form ? ` - recent form reads ${selection.form}` : '';
      reasons.push(`Form quality ${trendDir} (${selection.formQualityTrend.toFixed(2)})${formStr}`);
    }

    if (reasons.length < 6 && recalAppliedBB && Math.abs(recalShiftBB) >= 2) {
      if (recalShiftBB > 2) {
        reasons.push(`Recalibration boosted win chance by ${recalShiftBB.toFixed(1)}pp - genuine value detected`);
      } else if (recalShiftBB < -5) {
        reasons.push(`Recalibration trimmed win chance by ${Math.abs(recalShiftBB).toFixed(1)}pp - exercise caution`);
      }
    }

    if (reasons.length < 6 && selection.isCourseDistanceWinner) {
      const courseSR = selection.courseStrikeRate || 0;
      const distSR = selection.distanceStrikeRate || 0;
      reasons.push(`Proven C&D winner at ${selection.track}${courseSR > 0 ? ` (course ${courseSR.toFixed(0)}%, distance ${distSR.toFixed(0)}%)` : ''}`);
    }

    if (reasons.length < 6 && jockey && trainer) {
      const isEliteJ = jockeyWinRate !== null && jockeyWinRate >= 0.15;
      const isEliteT = trainerWinRate !== null && trainerWinRate >= 0.15;
      const jtSR = selection.jockeyTrainerStrikeRate || 0;
      if (comboWinRate !== null && comboWinRate >= 0.20) {
        reasons.push(`${jockey} and ${trainer} are a lethal combo (${(comboWinRate * 100).toFixed(0)}% win rate together)`);
      } else if (selection.isWinningCombo) {
        reasons.push(`${jockey} and ${trainer} are a proven winning combo${jtSR > 0 ? ` (${jtSR.toFixed(0)}% strike rate together)` : ''}`);
      } else if (isEliteJ && isEliteT) {
        reasons.push(`Elite connections: ${jockey} (${(jockeyWinRate! * 100).toFixed(0)}% SR) riding for ${trainer} (${(trainerWinRate! * 100).toFixed(0)}% SR)`);
      } else if (isEliteJ) {
        reasons.push(`In-form jockey ${jockey} (${(jockeyWinRate! * 100).toFixed(0)}% win rate last 90 days)`);
      } else if (isEliteT) {
        reasons.push(`${trainer} stable in strong form (${(trainerWinRate! * 100).toFixed(0)}% win rate last 90 days)`);
      }
    }

    if (reasons.length > 6) {
      reasons.length = 6;
    }
    
    // Determine bet type from pipeline signals
    let betType: "banker" | "best_chance" | "value" | "roughie" | "form_pick" | "model_pick";
    
    // BANKER: Use MC-driven banker detection (banker_flag from BankerDetector)
    const bankerFlag = selection.banker_flag || selection.bankerFlag;
    const bankerTier = selection.banker_tier || selection.bankerTier;
    const bankerScore = selection.banker_score || selection.bankerScore || 0;
    if (bankerFlag) {
      betType = "banker";
      if (bankerTier === 'strong_banker') {
        reasons.unshift(`STRONG BANKER (score ${Math.round(bankerScore)}) — dominant across all model layers`);
      } else {
        reasons.unshift(`BANKER (score ${Math.round(bankerScore)}) — all model layers agree this horse is dominant`);
      }
    }
    // Legacy banker fallback: Short-priced, high win prob, strong form indicators, peak fitness
    else if (odds <= 3.5 && winPct >= 25 && frankingScore >= 52 && fitnessReadiness != null && fitnessReadiness >= 0.8) {
      betType = "banker";
    }
    // VALUE: Positive edge confirmed by recalibration, ML model active
    else if (edge >= 3 && recalAppliedBB && recalShiftBB > -3 && mlActiveBB) {
      betType = "value";
    }
    // FORM PICK: Strong franking + good fitness, regardless of edge
    else if (frankingScore >= 55 && frankingConf >= 0.3 && !isAntiFranked && fitnessReadiness != null && fitnessReadiness >= 0.7) {
      betType = "form_pick";
    }
    // MODEL PICK: Sectional MC enhanced or ML model with recalibration, moderate win prob
    else if ((sectionalMcBB || (mlActiveBB && recalAppliedBB)) && winPct >= 15) {
      betType = "model_pick";
    }
    // ROUGHIE: Longer odds but with some positive signal
    else if (odds >= 10 && (edge > 0 || frankingScore >= 52 || (fitnessReadiness != null && fitnessReadiness >= 0.85))) {
      betType = "roughie";
    }
    // BEST CHANCE: Default fallback
    else {
      betType = "best_chance";
    }
    
    // Build pipeline signals summary
    const signals: string[] = [];
    if (recalAppliedBB && Math.abs(recalShiftBB) >= 1) {
      signals.push(recalShiftBB > 0 ? `Recal +${recalShiftBB.toFixed(1)}pp` : `Recal ${recalShiftBB.toFixed(1)}pp`);
    }
    if (sectionalMcBB) signals.push('Sectional MC');
    if (mlActiveBB) signals.push('ML Ensemble');
    if (frankingScore >= 55 && frankingConf >= 0.3) signals.push(`Franking ${frankingScore.toFixed(0)}`);
    if (fitnessAtPeak) signals.push('Peak Fitness');
    else if (fitnessReadiness != null && fitnessReadiness >= 0.85) signals.push('Race Fit');
    if (edge >= 3) signals.push(`Edge +${edge.toFixed(1)}%`);
    else if (edge <= -10) signals.push(`No Edge ${edge.toFixed(1)}%`);
    if (bankerFlag) {
      signals.unshift(bankerTier === 'strong_banker' ? `Strong Banker ${Math.round(bankerScore)}` : `Banker ${Math.round(bankerScore)}`);
    }
    const pipelineSignals = signals.length > 0 ? signals.join(' | ') : undefined;
    
    // V3 CROWD CLASSIFICATION CAP
    // Applied after all other scoring — crowd gate overrides display confidence
    // A horse the crowd rejected should never display as High Conviction
    const crowdClassification = selection.crowdClassification ?? selection.crowd_classification ?? null;
    const crowdScore = selection.crowdScore ?? selection.crowd_score ?? 0;

    if (crowdClassification === 'MODEL_ONLY') {
      // Model liked it, crowd ignored it — archetype trap risk
      // Cap at 55 maximum (Watching tier, never High Conviction or Live Setup)
      totalScore = Math.min(totalScore, 55);
    } else if (crowdClassification === 'REJECTED') {
      // Nothing aligned — cap at 35 (below all conviction tiers)
      totalScore = Math.min(totalScore, 35);
    } else if (crowdClassification === 'CROWD_ONLY_WEAK') {
      // Crowd liked it but model weak — show moderate confidence only
      // Unless unanimous (crowd_score === 100) which is now a live bet
      if (crowdScore < 100) {
        totalScore = Math.min(totalScore, 60);
      }
    } else if (crowdClassification === 'CONFIRMED') {
      // Crowd + model both strong — allow full score, add small bonus
      totalScore = Math.min(totalScore + 5, 100);
    } else if (crowdClassification === 'CROWD_ONLY') {
      // Crowd strong, model lukewarm — allow up to 75, not full conviction
      totalScore = Math.min(totalScore, 75);
    }
    // If crowdClassification is null (no consensus data), apply no cap
    // — old behaviour preserved for backwards compatibility

    // Determine risk level
    let riskLevel: "low" | "medium" | "high";
    if (odds <= 4 && winPct >= 25 && formScore >= 0.5) {
      riskLevel = "low";
    } else if (odds >= 10 || formScore < 0.3) {
      riskLevel = "high";
    } else {
      riskLevel = "medium";
    }
    
    // Suggested stake (banker-specific staking overrides standard staking)
    let suggestedStake: string;
    if (bankerFlag && bankerTier === 'strong_banker') {
      suggestedStake = "2 units win + 1 unit exacta";
      riskLevel = "low";
    } else if (bankerFlag) {
      suggestedStake = "1.5 units win + 0.5 unit exacta";
      riskLevel = "low";
    } else if (riskLevel === "low" && totalScore >= 70) {
      suggestedStake = "2-3% of bank";
    } else if (riskLevel === "medium" || totalScore >= 50) {
      suggestedStake = "1-2% of bank";
    } else {
      suggestedStake = "0.5-1% of bank";
    }
    
    // Star rating (1-5)
    const rating = Math.min(5, Math.max(1, Math.round(totalScore / 20)));
    
    return {
      id: selection.id,
      horseName: selection.horseName,
      horse: selection.horseName,
      track: selection.track,
      raceNumber: selection.raceNumber,
      raceName: selection.raceName || "",
      raceClass: selection.raceClass || selection.raceName || "",
      going: selection.going || "",
      raceDate: selection.raceDate,
      offTime: selection.offTime,
      distance: selection.distance,
      barrier: selection.barrier,
      jockey: selection.jockey || "",
      trainer: selection.trainer || "",
      form: selection.form,
      marketOdds: selection.marketOdds || 0,
      fairOdds: selection.fairOdds ?? undefined,
      openingOdds: selection.openingOdds ?? undefined,
      marketMovement: selection.marketMovement ?? undefined,
      marketRank: selection.marketRank ?? undefined,
      winPercentage: selection.winPercentage || 0,
      edge: selection.edge || 0,
      betType,
      pipelineSignals,
      confidenceScore: totalScore,
      rating,
      reasons,
      riskLevel,
      suggestedStake,
      // Enhanced features
      enhancedFactor: selection.enhancedFactor || undefined,
      classMovementFactor: selection.classMovementFactor || undefined,
      classMovementDesc: selection.classMovementDesc || undefined,
      barrierBiasFactor: selection.barrierBiasFactor || undefined,
      barrierBiasDesc: selection.barrierBiasDesc || undefined,
      headToHeadFactor: selection.headToHeadFactor || undefined,
      headToHeadDesc: selection.headToHeadDesc || undefined,
      enhancedExplanations: selection.enhancedExplanations || undefined,
      // Track bias points (use ?? to preserve 0 values)
      trackBiasPoints: selection.trackBiasPoints ?? undefined,
      trackBiasFit: selection.trackBiasFit ?? undefined,
      trackBiasBarrierPts: selection.trackBiasBarrierPts ?? undefined,
      trackBiasPacePts: selection.trackBiasPacePts ?? undefined,
      trackBiasJockeyPts: selection.trackBiasJockeyPts ?? undefined,
      trackBiasTrainerPts: selection.trackBiasTrainerPts ?? undefined,
      trackBiasSummary: selection.trackBiasSummary ?? undefined,
      // Form analyst insights - plain English analysis
      formAnalystInsights: generateFormAnalystInsights(selection),
      // Model summary - max 2 technical points
      modelSummary: generateModelSummary(selection),
      // AI Stride analyst insight (strip markdown asterisks)
      aiInsight: selection.aiInsight ? selection.aiInsight.replace(/\*+/g, '') : undefined,
      aiScore: selection.aiScore ?? undefined,
      llmProvider: selection.llmProvider ?? undefined,
      horseNumber: selection.horseNumber ?? undefined,
      pipelineRank: selection.pipelineRank ?? undefined,
      selectionOrigin: selection.selectionOrigin ?? undefined,
      selectionOriginReason: selection.selectionOriginReason ?? undefined,
      matchesModelLeader: selection.matchesModelLeader ?? undefined,
      modelLeaderHorse: selection.modelLeaderHorse ?? undefined,
      shouldBet: selection.shouldBet ?? undefined,
      // Consensus Intelligence V2
      marketConfidenceScore: selection.marketConfidenceScore ?? undefined,
      marketConfidenceLabel: selection.marketConfidenceLabel ?? undefined,
      marketConfidenceColour: selection.marketConfidenceColour ?? undefined,
      convergenceTier: selection.convergenceTier ?? undefined,
      consensusScore: selection.consensusScore ?? undefined,
      marketSignalScore: selection.marketSignalScore ?? undefined,
      consensusVotePct: selection.consensusVotePct ?? undefined,
      convergenceGate: selection.convergenceGate ?? undefined,
      tipsterCount: selection.tipstersPolled ?? undefined,
      selectionScoreRaw: selection.selectionScoreRaw ?? undefined,
      consensusInjection: selection.consensusInjection ?? undefined,
      marketInjection: selection.marketInjection ?? undefined,
      // Crowd-first V3 fields
      crowdScore: selection.crowdScore ?? undefined,
      crowdClassification: selection.crowdClassification ?? undefined,
      crowdGateReason: selection.crowdGateReason ?? undefined,
      stakeRecommendation: selection.stakeRecommendation ?? undefined,
      independentMentions: selection.independentMentions ?? 0,
      commercialMentions: selection.commercialMentions ?? 0,
      marketAlignment: selection.marketAlignment ?? false,
      // Luckless last-start analysis
      lucklessFlag: selection.lucklessFlag ?? undefined,
      lucklessScore: selection.lucklessScore ?? undefined,
      lucklessUplift: selection.lucklessUplift ?? undefined,
      lucklessExplanation: selection.lucklessExplanation ?? undefined,
      lucklessJson: selection.lucklessJson ?? undefined,
      // Form Franking data
      frankingElo: selection.frankingElo ?? undefined,
      frankingScore: selection.frankingScore ?? undefined,
      frankingConfidence: selection.frankingConfidence ?? undefined,
      isAntiFranked: selection.isAntiFranked ?? undefined,
      fieldStrengthAvg: selection.fieldStrengthAvg ?? undefined,
      formQualityTrend: selection.formQualityTrend ?? undefined,
      bestAdjustedMargin: selection.bestAdjustedMargin ?? undefined,
      collateralAdvantage: selection.collateralAdvantage ?? undefined,
      // Advanced Graph Franking
      pagerankAuthority: selection.pagerankAuthority ?? undefined,
      communityStrength: selection.communityStrength ?? undefined,
      formStability: selection.formStability ?? undefined,
      graphFrankingScore: selection.graphFrankingScore ?? undefined,
      graphFrankingDepth: selection.graphFrankingDepth ?? undefined,
      graphFrankingIndependence: selection.graphFrankingIndependence ?? undefined,
      marketValidatedFranking: selection.marketValidatedFranking ?? undefined,
      bridgeScore: selection.bridgeScore ?? undefined,
      // Recalibration & Sectional MC data
      recalibrationApplied: selection.recalibrationApplied ?? undefined,
      recalibrationShift: selection.recalibrationShift ?? undefined,
      rawWinProb: selection.rawWinProb ?? undefined,
      calibratedWinProb: selection.calibratedWinProb ?? undefined,
      sectionalMcEnhanced: selection.sectionalMcEnhanced ?? undefined,
      sectionalMcWinProb: selection.sectionalMcWinProb ?? undefined,
      paceScenarioJson: selection.paceScenarioJson ?? undefined,
      mlModelActive: selection.mlModelActive ?? undefined,
      mlAdjustmentBreakdown: selection.mlAdjustmentBreakdown ?? undefined,
      sectionalEdgeMultiplier: selection.sectionalEdgeMultiplier || null,
      sectionalEdgeInsight: selection.sectionalEdgeInsight || null,
      bankerFlag: bankerFlag || false,
      bankerTier: bankerFlag ? bankerTier : undefined,
      bankerScore: bankerFlag ? Math.round(bankerScore) : undefined,
      bankerScoreComponents: bankerFlag ? (selection.bankerScoreComponents as any) : undefined,
      bankerStaking: bankerFlag ? (() => {
  const stk = selection.bankerStaking as any;
  if (!stk) return undefined;
  if (typeof stk === 'string') return stk;
  const parts: string[] = [];
  if (stk.win_stake) parts.push(`${stk.win_stake} units win`);
  if (stk.exacta_stake) parts.push(`${stk.exacta_stake} unit exacta`);
  return parts.join(' + ') || undefined;
})() : undefined,
      bankerExoticCombos: bankerFlag ? (() => {
  const combos = selection.bankerExoticCombos as any;
  if (!combos || !Array.isArray(combos)) return undefined;
  return combos.map((c: any) => ({
    type: c.type || c.bet_type || 'exacta',
    anchor: c.anchor || '',
    withRunners: c.with_runners || c.withRunners || [],
    stake: c.stake || 0,
  }));
})() : undefined,
    };
  }
  
  // Australian Metro Tracks - Best Bets only show metro meetings
  const METRO_TRACKS = [
    // NSW (Sydney + Provincial)
    'randwick', 'royal randwick', 'rosehill', 'rosehill gardens',
    'canterbury', 'canterbury park', 'warwick farm', 'kensington',
    'newcastle', 'kembla grange', 'gosford', 'wyong',
    // VIC (Melbourne)
    'flemington', 'caulfield', 'moonee valley', 'sandown',
    'sandown lakeside', 'sandown hillside', 'sportsbet sandown',
    // QLD (Brisbane)
    'eagle farm', 'doomben', 'gold coast', 'aquis park',
    // SA (Adelaide)
    'morphettville',
    // WA (Perth)
    'ascot', 'belmont'
  ];
  
  function isMetroTrack(trackName: string): boolean {
    const normalizedTrack = trackName.toLowerCase().trim();
    return METRO_TRACKS.some(metro => 
      normalizedTrack.includes(metro) || metro.includes(normalizedTrack)
    );
  }

  function getAustralianDateIso(date = new Date()): string {
    return date.toLocaleDateString('en-CA', { timeZone: 'Australia/Sydney' });
  }

  function parseNumericOdds(value: unknown): number {
    if (typeof value === 'number') return Number.isFinite(value) ? value : 0;
    if (typeof value === 'string') {
      const parsed = parseFloat(value.replace(/\$/g, '').trim());
      return Number.isFinite(parsed) ? parsed : 0;
    }
    return 0;
  }

  function getRunnerOddsValue(runner: any): number {
    if (!runner) return 0;

    if (typeof runner.odds === 'number' && runner.odds > 0) return runner.odds;
    if (typeof runner.odds === 'string') {
      const parsed = parseNumericOdds(runner.odds);
      if (parsed > 0) return parsed;
    }

    if (Array.isArray(runner.odds)) {
      for (const entry of runner.odds) {
        if (typeof entry === 'number' && entry > 0) return entry;
        if (entry && typeof entry === 'object') {
          const parsed = parseNumericOdds((entry as any).win_odds ?? (entry as any).odds);
          if (parsed > 0) return parsed;
        }
      }
    }

    return parseNumericOdds(runner.win_odds ?? runner.sp ?? runner.marketOdds);
  }

  function buildRaceKey(track: string, raceNumber: unknown): string {
    const parsedRaceNumber = parseInt(String(raceNumber ?? '').replace(/^R/i, ''), 10);
    return `${track}-R${parsedRaceNumber}`;
  }

  function listLocalTipDates(): string[] {
    const racecardsDir = path.join(process.cwd(), 'racecards');
    if (!fs.existsSync(racecardsDir)) return [];

    return fs.readdirSync(racecardsDir)
      .map((fileName) => fileName.match(/^tips_(\d{4}-\d{2}-\d{2})\.json$/)?.[1] ?? null)
      .filter((dateStr): dateStr is string => Boolean(dateStr))
      .sort();
  }

  function buildLocalRacecardContext(date: string): {
    runnersMap: Map<string, any[]>;
    marketFavMap: Map<string, { horseName: string; odds: number; impliedProb: number }>;
    offTimeMap: Map<string, string>;
  } {
    const runnersMap = new Map<string, any[]>();
    const marketFavMap = new Map<string, { horseName: string; odds: number; impliedProb: number }>();
    const offTimeMap = new Map<string, string>();
    const racecardPath = path.join(process.cwd(), 'racecards', `racecard_${date}.json`);

    if (!fs.existsSync(racecardPath)) {
      return { runnersMap, marketFavMap, offTimeMap };
    }

    try {
      const racecardRaw = JSON.parse(fs.readFileSync(racecardPath, 'utf-8'));
      const meetings = Array.isArray(racecardRaw) ? racecardRaw : [];

      for (const meeting of meetings) {
        for (const race of meeting.races || []) {
          const track = race.course || meeting.course || '';
          const raceKey = buildRaceKey(track, race.race_number);
          if (!track || raceKey.endsWith('NaN')) continue;

          const runners = Array.isArray(race.runners) ? race.runners : [];
          runnersMap.set(raceKey, runners);
          if (race.off_time) offTimeMap.set(raceKey, race.off_time);

          let favouriteName = '';
          let favouriteOdds = Infinity;

          for (const runner of runners) {
            if (runner?.scratched) continue;
            const odds = getRunnerOddsValue(runner);
            const horseName = runner?.horse || runner?.name || runner?.horse_name || '';
            if (horseName && odds > 1 && odds < favouriteOdds) {
              favouriteName = horseName;
              favouriteOdds = odds;
            }
          }

          if (favouriteName && favouriteOdds < Infinity) {
            marketFavMap.set(raceKey, {
              horseName: favouriteName,
              odds: favouriteOdds,
              impliedProb: parseFloat((100 / favouriteOdds).toFixed(1)),
            });
          }
        }
      }
    } catch (error) {
      console.warn(`[Best Bets] Failed to read local racecard ${date}:`, (error as Error).message);
    }

    return { runnersMap, marketFavMap, offTimeMap };
  }

  function buildFallbackOffTime(date: string, raceNumber: number): string {
    const hour = 1 + Math.max(0, Math.min(18, raceNumber));
    return `${date}T${String(hour).padStart(2, '0')}:00:00.000Z`;
  }

  function mapTipSelectionToBestBetShape(
    tip: any,
    race: any,
    raceDate: string,
    offTime: string
  ): any {
    const mc = tip?._mc_data || {};
    const horseName = String(tip?.horse || mc.horse || '').trim();
    const horseNumber = String(tip?.saddle_number ?? mc.number ?? tip?.rank ?? '').trim();
    const marketOdds = parseNumericOdds(tip?.odds ?? mc.marketOdds);
    const winPercentage = Number(tip?.win_pct ?? mc.winPercentage ?? 0);
    const impliedWinPct = marketOdds > 1 ? (100 / marketOdds) : 0;
    const edge = Number(tip?.edge_pct ?? mc.edge ?? mc.valueEdgePct ?? (winPercentage - impliedWinPct));
    const fitnessData = mc.fitnessData && typeof mc.fitnessData === 'object' ? mc.fitnessData : undefined;
    const paceScenario = mc.paceScenarioJson ?? mc.paceScenarioDistribution ?? mc.paceScenario ?? undefined;
    const frankingScore = mc.frankingScore ?? mc.graph_franking_score;
    const frankingConfidence = mc.frankingConfidence ?? mc.graph_franking_independence;

    return {
      id: `local-${raceDate}-${race.track}-${race.race_number}-${horseNumber || horseName}`,
      horseName,
      horse: horseName,
      horseNumber,
      pipelineRank: Number(tip?.rank ?? tip?.tip_rank ?? tip?._pipeline_rank ?? mc.rank ?? 0) || undefined,
      track: String(race.track || ''),
      raceNumber: Number(race.race_number || 0),
      raceName: String(race.race_name || ''),
      raceClass: String(race.race_class || race.race_name || ''),
      going: String(race.going || ''),
      raceDate,
      offTime,
      distance: String(race.distance || mc._distance_m || ''),
      barrier: String(tip?.barrier ?? mc.barrier ?? ''),
      jockey: String(tip?.jockey || mc.jockey || ''),
      trainer: String(tip?.trainer || mc.trainer || ''),
      form: (tip?.form ?? mc.form ?? '') || null,
      marketOdds,
      fairOdds: parseNumericOdds(tip?.fair_odds ?? mc.fairOdds),
      openingOdds: parseNumericOdds(tip?.opening_odds ?? mc.openingOdds),
      marketMovement: typeof tip?.market_movement === 'string'
        ? tip.market_movement
        : (typeof mc.marketMovement === 'string' ? mc.marketMovement : undefined),
      marketRank: Number(tip?.market_rank ?? mc.marketRank ?? 0) || undefined,
      winPercentage,
      placePercentage: Number(tip?.place_pct ?? mc.placePercentage ?? 0),
      modelProbability: Number(tip?.raw_model_pct ?? mc.rawModelProb ?? mc.rawWinProb ?? 0),
      expectedValue: Number(mc.expectedValue ?? 0),
      edge,
      kellyStake: Number(mc.kellyStake ?? 0),
      ciLower: Number(mc.ciLower ?? 0),
      ciUpper: Number(mc.ciUpper ?? 0),
      expectedPosition: Number(mc.expectedPosition ?? 0),
      stabilityScore: Number(mc.stabilityScore ?? 0),
      runningStyle: String(tip?.running_style || mc.runningStyle || 'unknown'),
      confidence: String(tip?.confidence || mc.confidence || 'low'),
      valueRating: String(tip?.value_rating || mc.valueRating || 'Fair'),
      aiInsight: tip?.ai_insight || tip?.brief_assessment || mc.ai_insight || mc.ai_analysis || undefined,
      aiScore: Number(tip?.ai_score ?? mc.ai_score ?? 0) || undefined,
      llmProvider: mc.ai_analysis ? 'local-tips' : undefined,
      enhancedFactor: Number(mc.enhancedFactor ?? 0) || undefined,
      classMovementFactor: typeof mc.classMovement === 'number' ? mc.classMovement : undefined,
      classMovementDesc: typeof mc.classMovement === 'string' ? mc.classMovement : undefined,
      barrierBiasFactor: typeof mc.barrierBias === 'number' ? mc.barrierBias : undefined,
      barrierBiasDesc: mc.trackBiasSummary || undefined,
      headToHeadFactor: typeof mc.headToHead === 'number' ? mc.headToHead : undefined,
      enhancedExplanations: mc.enhancedExplanations || undefined,
      trackBiasPoints: mc.trackBiasPoints ?? undefined,
      trackBiasFit: mc.trackBiasFit ?? undefined,
      trackBiasSummary: mc.trackBiasSummary ?? undefined,
      fieldSize: Number(race.field_size || 0),
      fieldStrengthAvg: mc.classAvg ?? undefined,
      isEliteJockey: mc.isEliteJockey ?? undefined,
      isEliteTrainer: mc.isEliteTrainer ?? undefined,
      isWinningCombo: mc.isWinningCombo ?? undefined,
      jockeyTrainerStrikeRate: mc.jockeyTrainerStrikeRate ?? undefined,
      isCourseDistanceWinner: mc.isCourseDistanceWinner ?? undefined,
      courseStrikeRate: mc.courseStrikeRate ?? mc.courseSR ?? undefined,
      distanceStrikeRate: mc.distanceStrikeRate ?? mc.distanceSR ?? undefined,
      isFirstUp: mc.isFirstUp ?? undefined,
      isSecondUp: mc.isSecondUp ?? undefined,
      daysSinceRun: mc.daysSinceRun ?? tip?.days_since_run ?? undefined,
      frankingElo: mc.franking_elo ?? undefined,
      frankingScore: frankingScore ?? undefined,
      frankingConfidence: frankingConfidence ?? undefined,
      isAntiFranked: false,
      pagerankAuthority: mc.pagerank_authority ?? undefined,
      communityStrength: mc.community_strength ?? undefined,
      formStability: mc.form_stability ?? undefined,
      graphFrankingScore: mc.graph_franking_score ?? undefined,
      graphFrankingDepth: mc.graph_franking_depth ?? undefined,
      graphFrankingIndependence: mc.graph_franking_independence ?? undefined,
      marketValidatedFranking: mc.market_validated_franking ?? undefined,
      bridgeScore: mc.bridge_score ?? undefined,
      recalibrationApplied: mc.recalibrationApplied ?? undefined,
      recalibrationShift: mc.recalibrationShift ?? undefined,
      rawWinProb: mc.rawWinProb ?? undefined,
      calibratedWinProb: mc.calibratedWinProb ?? undefined,
      sectionalMcEnhanced: mc.sectionalMcEnhanced ?? undefined,
      sectionalMcWinProb: mc.sectionalMcWinProb ?? undefined,
      paceScenarioJson: paceScenario,
      mlModelActive: mc.mlModelActive ?? mc.enhancedMlActive ?? undefined,
      mlAdjustmentBreakdown: mc.mlAdjustmentBreakdown ?? undefined,
      sectionalEdgeMultiplier: mc.sectionalEdgeMultiplier ?? undefined,
      sectionalEdgeInsight: mc.sectionalEdgeInsight ?? undefined,
      bankerFlag: mc.banker_flag ?? undefined,
      bankerTier: mc.banker_tier ?? undefined,
      bankerScore: mc.banker_score ?? undefined,
      bankerScoreComponents: mc.banker_score_components ?? undefined,
      bankerStaking: mc.banker_staking ?? undefined,
      bankerExoticCombos: mc.banker_exotic_combos ?? undefined,
      lucklessFlag: tip?.luckless_flag ?? mc._luckless_data?.flag ?? undefined,
      lucklessScore: tip?.luckless_score ?? mc._luckless_data?.score ?? undefined,
      lucklessUplift: tip?.luckless_uplift ?? mc._luckless_data?.uplift ?? undefined,
      lucklessExplanation: tip?.luckless_explanation ?? mc._luckless_data?.explanation ?? undefined,
      lucklessJson: tip?.luckless_json ?? mc._luckless_data ?? undefined,
      fitnessRunsThisPrep: fitnessData?.runsThisPrep ?? undefined,
      fitnessRunLabel: fitnessData?.runLabel ?? undefined,
      fitnessIsAtPeakRun: fitnessData?.isAtPeakRun ?? undefined,
      fitnessReadinessScore: fitnessData?.readinessScore ?? undefined,
      fitnessPrepTrajectory: fitnessData?.prepTrajectory ?? undefined,
      fitnessDescription: fitnessData?.description ?? undefined,
      fitnessData,
      selectionOrigin: tip?.selection_origin ?? undefined,
      selectionOriginReason: tip?.selection_origin_reason ?? undefined,
      matchesModelLeader: tip?.matches_model_leader ?? undefined,
      modelLeaderHorse: tip?.model_leader_horse ?? undefined,
      shouldBet: tip?.should_bet ?? undefined,
      // Consensus Intelligence V2
      marketConfidenceScore: tip?.market_confidence_score ?? undefined,
      marketConfidenceLabel: tip?.market_confidence_label ?? undefined,
      marketConfidenceColour: tip?.market_confidence_colour ?? undefined,
      convergenceTier: tip?.convergence_tier ?? undefined,
      consensusScore: tip?.consensus_score ?? undefined,
      marketSignalScore: tip?.market_signal_score ?? undefined,
      consensusVotePct: tip?.consensus_vote_pct ?? undefined,
      convergenceGate: tip?.convergence_gate ?? undefined,
      tipsterCount: tip?.tipsters_polled ?? undefined,
      selectionScoreRaw: tip?.selection_score_raw ?? undefined,
      consensusInjection: tip?.consensus_injection ?? undefined,
      marketInjection: tip?.market_injection ?? undefined,
      // Crowd-first V3 fields
      crowdScore: tip?.crowd_score ?? undefined,
      crowdClassification: tip?.crowd_classification ?? undefined,
      crowdGateReason: tip?.crowd_gate_reason ?? undefined,
      stakeRecommendation: tip?.stake_recommendation ?? undefined,
      independentMentions: tip?.independent_mentions ?? 0,
      commercialMentions: tip?.commercial_mentions ?? 0,
      marketAlignment: tip?.market_alignment ?? false,
    };
  }

  function getPromotedLocalRaceTips(race: any): any[] {
    const topPicks = Array.isArray(race?.top_picks) ? race.top_picks : [];
    const fullField = Array.isArray(race?.full_field) ? race.full_field : [];

    if (topPicks.length === 0) {
      return fullField.slice(0, 3).map((runner: any, index: number) => ({
        ...runner,
        rank: index + 1,
      }));
    }

    if (fullField.length === 0) {
      return topPicks;
    }

    const topPick = topPicks[0] ?? {};
    const fullFieldLeader = fullField[0] ?? {};
    const topPickHorse = String(topPick?.horse || "").trim().toLowerCase();
    const fullFieldHorse = String(fullFieldLeader?.horse || "").trim().toLowerCase();

    if (!topPickHorse || !fullFieldHorse || topPickHorse === fullFieldHorse) {
      return topPicks;
    }

    const topPickScore = Number(topPick?.selection_score ?? 0);
    const topPickEdge = Number(topPick?.edge_pct ?? 0);
    const fullFieldScore = Number(fullFieldLeader?.selection_score ?? 0);
    const fullFieldEdge = Number(fullFieldLeader?.edge_pct ?? 0);
    const fullFieldOdds = Number(fullFieldLeader?.odds ?? 0);

    const shouldPromoteFullFieldLeader =
      Number.isFinite(fullFieldScore) &&
      Number.isFinite(topPickScore) &&
      Number.isFinite(fullFieldEdge) &&
      Number.isFinite(topPickEdge) &&
      Number.isFinite(fullFieldOdds) &&
      fullFieldOdds > 1 &&
      fullFieldOdds <= 30 &&
      fullFieldScore >= topPickScore + 5 &&
      fullFieldEdge >= topPickEdge + 3;

    if (!shouldPromoteFullFieldLeader) {
      return topPicks;
    }

    return fullField.slice(0, 3).map((runner: any, index: number) => ({
      ...runner,
      rank: index + 1,
    }));
  }

  function buildRawModelLeaderLocalRaceTip(race: any): any | null {
    const explicitLeader = race?.raw_model_leader;
    if (explicitLeader && typeof explicitLeader === 'object') {
      return explicitLeader;
    }

    const fullField = Array.isArray(race?.full_field) ? race.full_field : [];
    const leader = fullField[0];
    if (!leader) return null;

    const odds = parseNumericOdds(leader?.odds);
    const edge = Number(leader?.edge_pct ?? 0);
    const horseName = leader?.horse;
    const evaluated = evaluateBetCandidateLocal(leader);

    return {
      ...leader,
      rank: 1,
      selection_origin: 'raw_model_leader',
      selection_origin_reason: 'Strongest pre-filter model signal in the race.',
      matches_model_leader: true,
      model_leader_horse: horseName,
      should_bet: evaluated.canBet,
    };
  }

  function evaluateBetCandidateLocal(pick: any): { canBet: boolean; reason: string } {
    if (!pick) {
      return { canBet: false, reason: "NO BET — no raw model leader was available." };
    }

    const odds = parseNumericOdds(
      pick?.odds
      ?? pick?.marketOdds
      ?? pick?.win_odds
      ?? pick?.sp
      ?? pick?.inferred_market_odds
    );
    const hasRealMarket = Boolean(pick?.has_real_market_odds ?? pick?.hasMarketOdds) && odds > 1;
    const edge = Number(pick?.edge_pct ?? pick?.edge ?? 0);
    const rawProb = Number(pick?.raw_model_pct ?? pick?.modelProbability ?? pick?.rawWinProb ?? 0);
    const calibratedProb = Number(pick?.win_pct ?? pick?.winPercentage ?? 0);
    const prob = Math.max(rawProb, calibratedProb);
    const confidence = String(pick?.confidence || '').trim().toLowerCase();

    if (!hasRealMarket) {
      return { canBet: false, reason: "NO BET — the raw model leader has no real market quote." };
    }
    if (!(edge > 0)) {
      return { canBet: false, reason: "NO BET — the raw model leader does not clear a positive edge." };
    }
    if (odds > 15) {
      return { canBet: false, reason: `NO BET — $${odds.toFixed(1)} sits outside the validated win-bet range, so this stays coverage only.` };
    }
    if (odds < 3) {
      if (edge < 4 || prob < 30) {
        return { canBet: false, reason: "NO BET — short-priced runners need a dominant overlay and clear top-strike signal." };
      }
      return { canBet: true, reason: "Raw model leader is a strong favourite with a genuine overlay, so it is still bettable." };
    }
    if (odds <= 5) {
      if (edge < 2.5 || prob < 15) {
        return { canBet: false, reason: "NO BET — this short-to-mid price runner does not clear the stronger favourite guardrails." };
      }
      return { canBet: true, reason: "Raw model leader sits in the stronger runner band with enough edge to be a live bet." };
    }
    if (edge < 3 || prob < 10) {
      return { canBet: false, reason: "NO BET — the runner sits in the value band but lacks the edge/probability needed for a live bet." };
    }
    if (confidence === 'low' && odds > 12) {
      return { canBet: false, reason: "NO BET — late-band value runner is still flagged low confidence, so it stays guide-only." };
    }
    return { canBet: true, reason: "Raw model leader sits in the validated value band and clears the edge threshold." };
  }

  function coverageProbLocal(pick: any): number {
    const rawProb = Number(pick?.raw_model_pct ?? pick?.modelProbability ?? pick?.rawWinProb ?? 0);
    const calibratedProb = Number(pick?.win_pct ?? pick?.winPercentage ?? 0);
    return Math.max(rawProb, calibratedProb);
  }

  function coverageSortKeyLocal(tip: any, modelLeaderHorse: string): [number, number, number, number, number, number] {
    const selectionScore = Number(tip?.selection_score ?? 0);
    const edge = Number(tip?.edge_pct ?? tip?.edge ?? 0);
    const odds = parseNumericOdds(tip?.odds ?? tip?._mc_data?.marketOdds);
    const prob = coverageProbLocal(tip);
    const matchesModelLeader =
      Boolean(modelLeaderHorse) &&
      String(tip?.horse || '').trim().toLowerCase() === modelLeaderHorse;
    const rank = Number(tip?.rank ?? tip?._pipeline_rank ?? 999);
    return [-prob, -edge, -selectionScore, odds || 999, matchesModelLeader ? 0 : 1, rank];
  }

  function isCoverageExceptionCandidateLocal(tip: any): boolean {
    const odds = parseNumericOdds(tip?.odds ?? tip?._mc_data?.marketOdds);
    if (!(Boolean(tip?.has_real_market_odds) && odds > 20 && odds <= 30)) {
      return false;
    }

    const edge = Number(tip?.edge_pct ?? tip?.edge ?? 0);
    const prob = coverageProbLocal(tip);
    const confidence = String(tip?.confidence || '').trim().toLowerCase();

    if (odds <= 25) {
      return prob >= 13 && edge >= 2 && confidence !== 'low';
    }
    return prob >= 16 && edge >= 4 && confidence === 'high';
  }

  function buildLegacyCoverageLocalRaceTip(race: any): any | null {
    const explicitPrimary = race?.primary_pick;
    if (explicitPrimary && typeof explicitPrimary === 'object') {
      return explicitPrimary;
    }

    const rankedTips = getPromotedLocalRaceTips(race);
    if (!rankedTips.length) return null;

    const fullField = Array.isArray(race?.full_field) ? race.full_field : [];
    const modelLeaderHorse = String((fullField[0] || {}).horse || '').trim().toLowerCase();
    const candidatePool = [...rankedTips];
    const seenHorses = new Set(candidatePool.map((tip: any) => String(tip?.horse || '').trim().toLowerCase()).filter(Boolean));
    for (const runner of fullField) {
      const horse = String(runner?.horse || '').trim().toLowerCase();
      if (!horse || seenHorses.has(horse)) continue;
      candidatePool.push(runner);
      seenHorses.add(horse);
    }

    const sortCandidates = (tips: any[]) =>
      [...tips].sort((a, b) => {
        const [probA, edgeA, scoreA, oddsA, leaderA, rankA] = coverageSortKeyLocal(a, modelLeaderHorse);
        const [probB, edgeB, scoreB, oddsB, leaderB, rankB] = coverageSortKeyLocal(b, modelLeaderHorse);
        if (probA !== probB) return probA - probB;
        if (edgeA !== edgeB) return edgeA - edgeB;
        if (scoreA !== scoreB) return scoreA - scoreB;
        if (oddsA !== oddsB) return oddsA - oddsB;
        if (leaderA !== leaderB) return leaderA - leaderB;
        return rankA - rankB;
      });

    const bettablePositive = sortCandidates(candidatePool.filter((tip: any) => evaluateBetCandidateLocal(tip).canBet));
    const probabilityFirst = sortCandidates(candidatePool.filter((tip: any) => {
      const odds = parseNumericOdds(tip?.odds ?? tip?._mc_data?.marketOdds);
      return Boolean(tip?.has_real_market_odds) && odds > 1 && odds <= 20 && coverageProbLocal(tip) >= 8;
    }));
    const exceptionCandidates = sortCandidates(candidatePool.filter((tip: any) => isCoverageExceptionCandidateLocal(tip)));
    const guidePositive = sortCandidates(candidatePool.filter((tip: any) => {
      const odds = parseNumericOdds(tip?.odds ?? tip?._mc_data?.marketOdds);
      return Boolean(tip?.has_real_market_odds) && odds > 1 && odds <= 20 && Number(tip?.edge_pct ?? 0) > 0;
    }));
    const reasonableMarket = sortCandidates(candidatePool.filter((tip: any) => {
      const odds = parseNumericOdds(tip?.odds ?? tip?._mc_data?.marketOdds);
      return Boolean(tip?.has_real_market_odds) && odds > 1 && odds <= 20;
    }));
    const realPositive = sortCandidates(candidatePool.filter((tip: any) => {
      const odds = parseNumericOdds(tip?.odds ?? tip?._mc_data?.marketOdds);
      return Boolean(tip?.has_real_market_odds) && odds > 1 && odds <= 25 && Number(tip?.edge_pct ?? 0) > 0;
    }));
    const realMarket = sortCandidates(candidatePool.filter((tip: any) => {
      const odds = parseNumericOdds(tip?.odds ?? tip?._mc_data?.marketOdds);
      return Boolean(tip?.has_real_market_odds) && odds > 1 && odds <= 25;
    }));
    const anyMarket = sortCandidates(candidatePool.filter((tip: any) => {
      const odds = parseNumericOdds(tip?.odds ?? tip?._mc_data?.marketOdds);
      return Boolean(tip?.has_real_market_odds) && odds > 1;
    }));

    const selected =
      bettablePositive[0]
      || probabilityFirst[0]
      || exceptionCandidates[0]
      || guidePositive[0]
      || reasonableMarket[0]
      || realPositive[0]
      || realMarket[0]
      || anyMarket[0]
      || candidatePool[0];
    const odds = parseNumericOdds(selected?.odds ?? selected?._mc_data?.marketOdds);
    const hasRealMarket = Boolean(selected?.has_real_market_odds) && odds > 1;
    const hasPositiveEdge = Number(selected?.edge_pct ?? 0) > 0;
    const matchesModelLeader =
      Boolean(modelLeaderHorse) &&
      String(selected?.horse || '').trim().toLowerCase() === modelLeaderHorse;
    const evaluated = evaluateBetCandidateLocal(selected);

    let selectionOrigin = 'tip_only';
    let selectionOriginReason = 'No positive-edge filtered contender survived; surfaced as the clearest race guide rather than a true bet.';
    let shouldBet = false;
    if (!hasRealMarket) {
      selectionOrigin = 'market_unavailable';
      selectionOriginReason = 'Guide horse has no real market quote; race should be treated as modelling context only.';
    } else if (evaluated.canBet && matchesModelLeader) {
      selectionOrigin = 'model_backed';
      selectionOriginReason = evaluated.reason;
      shouldBet = true;
    } else if (hasPositiveEdge && matchesModelLeader) {
      selectionOrigin = 'tip_only';
      selectionOriginReason = evaluated.reason;
    } else if (hasPositiveEdge) {
      selectionOrigin = 'filtered_substitute';
      selectionOriginReason = 'Guide horse is the safest filtered alternative, but it is not the raw model leader so this race remains a NO BET.';
    }

    return {
      ...selected,
      selection_origin: selectionOrigin,
      selection_origin_reason: selectionOriginReason,
      matches_model_leader: matchesModelLeader,
      model_leader_horse: fullField[0]?.horse,
      should_bet: shouldBet,
    };
  }

  function buildCoverageLocalRaceTip(race: any): any | null {
    const explicitCoverage = race?.coverage_pick;
    const base = (explicitCoverage && typeof explicitCoverage === 'object')
      ? explicitCoverage
      : buildLegacyCoverageLocalRaceTip(race);
    if (!base) return null;
    // Enrich with crowd-first V3 fields from matching top_pick
    const topPicks = Array.isArray(race?.top_picks) ? race.top_picks : [];
    const horseName = String(base.horse || '').trim().toLowerCase();
    const matchingPick = topPicks.find((p: any) => String(p.horse || '').trim().toLowerCase() === horseName);
    if (matchingPick) {
      base.crowd_score = matchingPick.crowd_score ?? base.crowd_score;
      base.crowd_classification = matchingPick.crowd_classification ?? base.crowd_classification;
      base.crowd_gate_reason = matchingPick.crowd_gate_reason ?? base.crowd_gate_reason;
      base.stake_recommendation = matchingPick.stake_recommendation ?? base.stake_recommendation;
      base.independent_mentions = matchingPick.independent_mentions ?? base.independent_mentions;
      base.commercial_mentions = matchingPick.commercial_mentions ?? base.commercial_mentions;
      base.market_alignment = matchingPick.market_alignment ?? base.market_alignment;
      if (matchingPick.should_bet != null) base.should_bet = matchingPick.should_bet;
    }
    return base;
  }

  function buildBetLocalRaceTip(race: any): any | null {
    const explicitBet = race?.bet_pick;
    let base: any = null;
    if (explicitBet && typeof explicitBet === 'object') {
      base = explicitBet;
    } else {
      const rawLeader = buildRawModelLeaderLocalRaceTip(race);
      if (!rawLeader) return null;
      const evaluated = evaluateBetCandidateLocal(rawLeader);
      if (!evaluated.canBet) return null;
      base = {
        ...rawLeader,
        selection_origin: 'model_backed',
        selection_origin_reason: evaluated.reason,
        matches_model_leader: true,
        model_leader_horse: rawLeader?.horse,
        should_bet: true,
      };
    }
    // Enrich with crowd-first V3 fields from matching top_pick
    const topPicks = Array.isArray(race?.top_picks) ? race.top_picks : [];
    const horseName = String(base.horse || '').trim().toLowerCase();
    const matchingPick = topPicks.find((p: any) => String(p.horse || '').trim().toLowerCase() === horseName);
    if (matchingPick) {
      base.crowd_score = matchingPick.crowd_score ?? base.crowd_score;
      base.crowd_classification = matchingPick.crowd_classification ?? base.crowd_classification;
      base.crowd_gate_reason = matchingPick.crowd_gate_reason ?? base.crowd_gate_reason;
      base.stake_recommendation = matchingPick.stake_recommendation ?? base.stake_recommendation;
      base.independent_mentions = matchingPick.independent_mentions ?? base.independent_mentions;
      base.commercial_mentions = matchingPick.commercial_mentions ?? base.commercial_mentions;
      base.market_alignment = matchingPick.market_alignment ?? base.market_alignment;
      if (matchingPick.should_bet != null) base.should_bet = matchingPick.should_bet;
    }
    return base;
  }

  function selectLocalTopRaceRepresentative(rankedTips: any[]): any[] {
    if (!rankedTips.length) return [];

    const firstWithRealMarket = rankedTips.find((tip: any) => {
      const odds = parseNumericOdds(tip?.odds ?? tip?._mc_data?.marketOdds);
      return Boolean(tip?.has_real_market_odds) && odds > 1;
    });
    if (firstWithRealMarket) return [firstWithRealMarket];

    const firstWithAnyMarket = rankedTips.find((tip: any) => {
      const odds = parseNumericOdds(tip?.odds ?? tip?._mc_data?.marketOdds ?? tip?.inferred_market_odds);
      return odds > 1;
    });
    if (firstWithAnyMarket) return [firstWithAnyMarket];

    return rankedTips.slice(0, 1);
  }

  function loadLocalBestBetSelections(
    requestedDate?: string,
    options?: { topPickOnly?: boolean; pickContract?: 'bet' | 'coverage' | 'raw' }
  ): {
    availableDates: string[];
    selectedDate?: string;
    selections: any[];
    runnersMap: Map<string, any[]>;
    modelFieldMap: Map<string, any[]>;
    marketFavMap: Map<string, { horseName: string; odds: number; impliedProb: number }>;
  } {
    const allDates = listLocalTipDates();
    if (allDates.length === 0) {
      return {
        availableDates: [],
        selections: [],
        runnersMap: new Map<string, any[]>(),
        modelFieldMap: new Map<string, any[]>(),
        marketFavMap: new Map<string, { horseName: string; odds: number; impliedProb: number }>(),
      };
    }

    const resolvedDate = requestedDate
      ? (allDates.includes(requestedDate)
          ? requestedDate
          : (allDates.find((dateStr) => dateStr >= requestedDate) || allDates[allDates.length - 1]))
      : allDates[allDates.length - 1];
    const targetDates = resolvedDate ? [resolvedDate] : [];

    const selections: any[] = [];
    const runnersMap = new Map<string, any[]>();
    const modelFieldMap = new Map<string, any[]>();
    const marketFavMap = new Map<string, { horseName: string; odds: number; impliedProb: number }>();

    for (const date of targetDates) {
      const tipsPath = path.join(process.cwd(), 'racecards', `tips_${date}.json`);
      if (!fs.existsSync(tipsPath)) continue;

      try {
        const tipsData = JSON.parse(fs.readFileSync(tipsPath, 'utf-8'));
        const racecardContext = buildLocalRacecardContext(date);

        racecardContext.runnersMap.forEach((value, key) => runnersMap.set(key, value));
        racecardContext.marketFavMap.forEach((value, key) => marketFavMap.set(key, value));

        for (const race of tipsData.races || []) {
          const raceKey = buildRaceKey(String(race.track || ''), race.race_number);
          const offTime = racecardContext.offTimeMap.get(raceKey)
            || buildFallbackOffTime(date, Number(race.race_number || 0));
          modelFieldMap.set(raceKey, Array.isArray(race.full_field) ? race.full_field : []);

          if (!runnersMap.has(raceKey) && Array.isArray(race.full_field)) {
            const fallbackRunners = race.full_field.map((runner: any) => ({
              ...runner,
              number: runner.saddle_number ?? runner.number,
              horse: runner.horse,
              win_odds: runner.odds,
            }));
            runnersMap.set(raceKey, fallbackRunners);

            let favouriteName = '';
            let favouriteOdds = Infinity;
            for (const runner of fallbackRunners) {
              const odds = getRunnerOddsValue(runner);
              if (runner.horse && odds > 1 && odds < favouriteOdds) {
                favouriteName = runner.horse;
                favouriteOdds = odds;
              }
            }

            if (favouriteName && favouriteOdds < Infinity) {
              marketFavMap.set(raceKey, {
                horseName: favouriteName,
                odds: favouriteOdds,
                impliedProb: parseFloat((100 / favouriteOdds).toFixed(1)),
              });
            }
          }

          const rankedTips = getPromotedLocalRaceTips(race);
          const coverageTip = buildCoverageLocalRaceTip(race);
          const betTip = buildBetLocalRaceTip(race);
          const rawModelLeaderTip = buildRawModelLeaderLocalRaceTip(race);
          const pickContract = options?.pickContract || 'coverage';
          const contractTip =
            pickContract === 'bet'
              ? betTip
              : pickContract === 'raw'
                ? rawModelLeaderTip
                : coverageTip;
          const sourceTips = options?.topPickOnly
            ? (contractTip ? [contractTip] : [])
            : (coverageTip ? [coverageTip] : rankedTips);

          for (const [tipIndex, tip] of sourceTips.entries()) {
            const rankedTip = tip && typeof tip === 'object'
              ? { ...tip, _pipeline_rank: tipIndex + 1 }
              : tip;
            const selection = mapTipSelectionToBestBetShape(rankedTip, race, date, offTime);
            if (selection.horseName) {
              selections.push(selection);
            }
          }
        }
      } catch (error) {
        console.warn(`[Best Bets] Failed to read local tips ${date}:`, (error as Error).message);
      }
    }

    selections.sort((a, b) => new Date(a.offTime).getTime() - new Date(b.offTime).getTime());
    return {
      availableDates: allDates,
      selectedDate: resolvedDate,
      selections,
      runnersMap,
      modelFieldMap,
      marketFavMap,
    };
  }

  function mapSelectionToBetsApiShape(selection: any): any {
    return {
      id: selection.id,
      track: selection.track,
      raceNumber: selection.raceNumber,
      raceName: selection.raceName || `Race ${selection.raceNumber}`,
      raceClass: selection.raceClass || selection.raceName || '',
      going: selection.going || '',
      raceDate: selection.raceDate,
      distance: selection.distance || 'Unknown',
      offTime: selection.offTime,
      horseName: selection.horseName,
      horse: selection.horseName,
      horseNumber: selection.horseNumber,
      barrier: selection.barrier,
      jockey: selection.jockey || 'TBA',
      trainer: selection.trainer || null,
      form: selection.form || '',
      modelProbability: selection.modelProbability || selection.winPercentage || 0,
      marketOdds: selection.marketOdds || selection.impliedOdds,
      impliedOdds: selection.impliedOdds || selection.marketOdds,
      expectedValue: selection.expectedValue || 0,
      kellyStake: selection.kellyStake || 0,
      confidence: selection.confidence || 'low',
      winPercentage: selection.winPercentage || 0,
      placePercentage: selection.placePercentage || 0,
      ciLower: selection.ciLower || 0,
      ciUpper: selection.ciUpper || 0,
      expectedPosition: selection.expectedPosition || 0,
      positionStdDev: selection.positionStdDev || 0,
      stabilityScore: selection.stabilityScore || 0,
      runningStyle: selection.runningStyle || 'unknown',
      paceSplits: selection.paceSplits || selection.paceScenarioJson || {},
      edge: selection.edge || 0,
      valueRating: selection.valueRating || 'Fair',
      hasMarketOdds: !!(selection.marketOdds && selection.marketOdds > 1),
      enhancedFactor: selection.enhancedFactor || undefined,
      barrierBiasFactor: selection.barrierBiasFactor || undefined,
      barrierBiasDesc: selection.barrierBiasDesc || undefined,
      classMovementFactor: selection.classMovementFactor || undefined,
      classMovementDesc: selection.classMovementDesc || undefined,
      headToHeadFactor: selection.headToHeadFactor || undefined,
      headToHeadDesc: selection.headToHeadDesc || undefined,
      enhancedExplanations: selection.enhancedExplanations || undefined,
      speedRating: selection.speedRating || undefined,
      paceScore: selection.paceScore || undefined,
      expectedPaceAdvantage: selection.expectedPaceAdvantage || undefined,
      trackBiasPoints: selection.trackBiasPoints ?? undefined,
      trackBiasFit: selection.trackBiasFit ?? undefined,
      trackBiasBarrierPts: selection.trackBiasBarrierPts ?? undefined,
      trackBiasPacePts: selection.trackBiasPacePts ?? undefined,
      trackBiasJockeyPts: selection.trackBiasJockeyPts ?? undefined,
      trackBiasTrainerPts: selection.trackBiasTrainerPts ?? undefined,
      trackBiasSummary: selection.trackBiasSummary ?? undefined,
      aiInsight: selection.aiInsight ?? undefined,
      aiInsightGeneratedAt: selection.aiInsightGeneratedAt ?? undefined,
      aiScore: selection.aiScore ?? undefined,
      aiReasoningJson: selection.aiReasoningJson ?? undefined,
      llmProvider: selection.llmProvider ?? undefined,
      frankingElo: selection.frankingElo ?? undefined,
      frankingScore: selection.frankingScore ?? undefined,
      frankingConfidence: selection.frankingConfidence ?? undefined,
      isAntiFranked: selection.isAntiFranked ?? undefined,
      fieldStrengthAvg: selection.fieldStrengthAvg ?? undefined,
      formQualityTrend: selection.formQualityTrend ?? undefined,
      bestAdjustedMargin: selection.bestAdjustedMargin ?? undefined,
      collateralAdvantage: selection.collateralAdvantage ?? undefined,
      fitnessRunsThisPrep: selection.fitnessRunsThisPrep ?? undefined,
      fitnessRunLabel: selection.fitnessRunLabel ?? undefined,
      fitnessIsAtPeakRun: selection.fitnessIsAtPeakRun ?? undefined,
      fitnessReadinessScore: selection.fitnessReadinessScore ?? undefined,
      fitnessPrepTrajectory: selection.fitnessPrepTrajectory ?? undefined,
      fitnessDescription: selection.fitnessDescription ?? undefined,
      fitnessData: selection.fitnessData ?? undefined,
      lucklessFlag: selection.lucklessFlag ?? undefined,
      lucklessScore: selection.lucklessScore ?? undefined,
      lucklessUplift: selection.lucklessUplift ?? undefined,
      lucklessExplanation: selection.lucklessExplanation ?? undefined,
      lucklessJson: selection.lucklessJson ?? undefined,
      selectionOrigin: selection.selectionOrigin ?? undefined,
      selectionOriginReason: selection.selectionOriginReason ?? undefined,
      matchesModelLeader: selection.matchesModelLeader ?? undefined,
      modelLeaderHorse: selection.modelLeaderHorse ?? undefined,
      shouldBet: selection.shouldBet ?? undefined,
      // Consensus Intelligence V2
      marketConfidenceScore: selection.marketConfidenceScore ?? undefined,
      marketConfidenceLabel: selection.marketConfidenceLabel ?? undefined,
      marketConfidenceColour: selection.marketConfidenceColour ?? undefined,
      convergenceTier: selection.convergenceTier ?? undefined,
      consensusScore: selection.consensusScore ?? undefined,
      marketSignalScore: selection.marketSignalScore ?? undefined,
      consensusVotePct: selection.consensusVotePct ?? undefined,
      convergenceGate: selection.convergenceGate ?? undefined,
      tipsterCount: selection.tipstersPolled ?? undefined,
      selectionScoreRaw: selection.selectionScoreRaw ?? undefined,
      consensusInjection: selection.consensusInjection ?? undefined,
      marketInjection: selection.marketInjection ?? undefined,
      // Crowd-first V3 fields
      crowdScore: selection.crowdScore ?? undefined,
      crowdClassification: selection.crowdClassification ?? undefined,
      crowdGateReason: selection.crowdGateReason ?? undefined,
      stakeRecommendation: selection.stakeRecommendation ?? undefined,
      independentMentions: selection.independentMentions ?? 0,
      commercialMentions: selection.commercialMentions ?? 0,
      marketAlignment: selection.marketAlignment ?? false,
      isActive: true,
    };
  }

  function getPipelineRankValue(selection: any): number {
    const rawRank = selection?.pipelineRank ?? selection?.tipRank ?? selection?.rank;
    const parsedRank = Number(rawRank);
    return Number.isFinite(parsedRank) && parsedRank > 0 ? parsedRank : 99;
  }

  // Full-field race data with MC scores + AI reasoning for all runners
  app.get("/api/race-field/:date", async (req, res) => {
    const { date } = req.params;
    if (!/^\d{4}-\d{2}-\d{2}$/.test(date)) {
      return res.status(400).json({ error: "Invalid date format" });
    }
    try {
      const { loadRaceFieldWithSpeedMaps } = await import("./raceSpeedMap");
      const races = await loadRaceFieldWithSpeedMaps(date);
      res.json({ success: true, date, races });
    } catch (e) {
      console.warn("[race-field] failed to load enriched race field:", (e as Error).message);
      res.json({ success: true, date, races: [] });
    }
  });

  app.get("/api/best-bets", async (req, res) => {
    try {
      const { racePipeline } = await import('./pipeline');
      const { date, track, limit: limitParam } = req.query;
      const limit = parseInt(limitParam as string) || 20;
      const requestedDate = typeof date === 'string' ? date : undefined;
      const todayStr = getAustralianDateIso();
      const localBetSelections = loadLocalBestBetSelections(requestedDate, { topPickOnly: true, pickContract: 'bet' });
      const localCoverageSelections = loadLocalBestBetSelections(requestedDate, { topPickOnly: true, pickContract: 'coverage' });
      const localCuratedBestBets = (() => {
        const selectedDate = localBetSelections.selectedDate || localCoverageSelections.selectedDate;
        if (!selectedDate) return [] as any[];

        const tipsPath = path.join(process.cwd(), 'racecards', `tips_${selectedDate}.json`);
        if (!fs.existsSync(tipsPath)) return [] as any[];

        try {
          const tipsData = JSON.parse(fs.readFileSync(tipsPath, 'utf-8'));
          const savedBestBets = Array.isArray(tipsData?.best_bets) ? tipsData.best_bets : [];
          if (savedBestBets.length === 0) return [] as any[];

          const bestBetOrder = new Map<string, number>();
          savedBestBets.forEach((tip: any, index: number) => {
            const raceKey = buildRaceKey(String(tip?.track || ''), tip?.race_number);
            const horseKey = String(tip?.horse || '').trim().toLowerCase();
            if (raceKey && horseKey) {
              bestBetOrder.set(`${raceKey}|${horseKey}`, index);
            }
          });

          return localBetSelections.selections
            .filter((selection: any) => {
              const raceKey = buildRaceKey(String(selection?.track || ''), selection?.raceNumber);
              const horseKey = String(selection?.horseName || '').trim().toLowerCase();
              return bestBetOrder.has(`${raceKey}|${horseKey}`);
            })
            .sort((a: any, b: any) => {
              const keyA = `${buildRaceKey(String(a?.track || ''), a?.raceNumber)}|${String(a?.horseName || '').trim().toLowerCase()}`;
              const keyB = `${buildRaceKey(String(b?.track || ''), b?.raceNumber)}|${String(b?.horseName || '').trim().toLowerCase()}`;
              return (bestBetOrder.get(keyA) ?? 999) - (bestBetOrder.get(keyB) ?? 999);
            });
        } catch (error) {
          console.warn(`[Best Bets] Failed to read saved best bets for ${selectedDate}:`, (error as Error).message);
          return [] as any[];
        }
      })();
      const localFallbackSelections: ReturnType<typeof loadLocalBestBetSelections> | null =
        localCoverageSelections.selections.length > 0
          ? localCoverageSelections
          : (localBetSelections.selections.length > 0 ? localBetSelections : null);

      if (localFallbackSelections) {
        let localBetPicks = [...localBetSelections.selections];
        let localCoveragePicks = [...localCoverageSelections.selections];
        let localSavedBestBetPicks = [...localCuratedBestBets];

        if (track) {
          const trackNeedle = (track as string).toLowerCase();
          localBetPicks = localBetPicks.filter((s: any) => s.track.toLowerCase().includes(trackNeedle));
          localCoveragePicks = localCoveragePicks.filter((s: any) => s.track.toLowerCase().includes(trackNeedle));
          localSavedBestBetPicks = localSavedBestBetPicks.filter((s: any) => s.track.toLowerCase().includes(trackNeedle));
        }

        const runnersMap = localFallbackSelections.runnersMap;
        const modelFieldMap = localFallbackSelections.modelFieldMap;
        const marketFavMap = localFallbackSelections.marketFavMap;

        const scoredBetPicks = await Promise.all(localBetPicks.map((selection: any) => calculateBestBetScore({
          ...selection,
          paceSplits: selection.paceSplitsJson,
        })));
        const scoredCoveragePicks = await Promise.all(localCoveragePicks.map((selection: any) => calculateBestBetScore({
          ...selection,
          paceSplits: selection.paceSplitsJson,
        })));
        const scoredSavedBestBets = await Promise.all(localSavedBestBetPicks.map((selection: any) => calculateBestBetScore({
          ...selection,
          paceSplits: selection.paceSplitsJson,
        })));

        const allBetPicks = scoredBetPicks
          .filter((bet: any) => bet.shouldBet !== false && bet.marketOdds > 1 && bet.edge > 0)
          .sort((a, b) => new Date(a.offTime).getTime() - new Date(b.offTime).getTime());

        const coveragePerRace = scoredCoveragePicks
          .map((bet: any) => {
            bet.selectionType = bet.shouldBet ? "bestBet" : "winTip";
            if (bet.shouldBet === false) {
              bet.betType = "tip_only";
              bet.tipWarning = bet.selectionOriginReason || "Guide only — no bet.";
            }
            return bet;
          })
          .sort((a, b) => new Date(a.offTime).getTime() - new Date(b.offTime).getTime());

        const tipsList = coveragePerRace.filter((bet: any) => bet.shouldBet === false);

        const applyExoticAnalysis = (bet: BestBetRecommendation) => {
          const raceKey = `${bet.track}-R${bet.raceNumber}`;
          const raceSelections = coveragePerRace.filter((selection: any) => `${selection.track}-R${selection.raceNumber}` === raceKey);
          const raceRunners = runnersMap.get(raceKey) || [];
          const raceModelField = modelFieldMap.get(raceKey) || [];
          const exoticRec = buildExoticRecommendation(raceRunners, bet, raceSelections, marketFavMap, raceModelField);
          bet.selectionType = exoticRec.selectionType;
          bet.exoticType = exoticRec.exoticType;
          bet.exoticRationale = exoticRec.exoticRationale;
          bet.exoticPicks = exoticRec.exoticPicks;
          bet.fieldSize = exoticRec.fieldSize;
          bet.raceCompetitiveness = exoticRec.raceCompetitiveness;
        };

        const attachMarketFav = (bet: BestBetRecommendation) => {
          const raceKey = `${bet.track}-R${bet.raceNumber}`;
          const fav = marketFavMap.get(raceKey);
          if (fav && fav.horseName.toLowerCase() !== bet.horseName.toLowerCase()) {
            bet.marketFavourite = fav;
          }
        };

        allBetPicks.forEach(applyExoticAnalysis);
        coveragePerRace.forEach(attachMarketFav);
        allBetPicks.forEach(attachMarketFav);
        scoredSavedBestBets.forEach(attachMarketFav);

        const trackBetsMap = new Map<string, BestBetRecommendation[]>();
        for (const bet of allBetPicks) {
          if (!trackBetsMap.has(bet.track)) trackBetsMap.set(bet.track, []);
          trackBetsMap.get(bet.track)!.push(bet);
        }

        const modelBacked: BestBetRecommendation[] = [];
        for (const [, bets] of trackBetsMap) {
          bets.sort((a: BestBetRecommendation, b: BestBetRecommendation) => {
            const edgeA = a.winPercentage - (a.marketOdds > 1 ? 100 / a.marketOdds : 0);
            const edgeB = b.winPercentage - (b.marketOdds > 1 ? 100 / b.marketOdds : 0);
            return edgeB - edgeA;
          });
          modelBacked.push(...bets.slice(0, 3));
        }
        modelBacked.sort((a, b) => new Date(a.offTime).getTime() - new Date(b.offTime).getTime());
        const displayedBestBets = scoredSavedBestBets.length > 0
          ? scoredSavedBestBets
          : modelBacked;

        const trackMap = new Map<string, BestBetRecommendation>();
        allBetPicks.forEach((bet: BestBetRecommendation) => {
          const betEdge = bet.winPercentage - (bet.marketOdds > 1 ? 100 / bet.marketOdds : 0);
          const existingBet = trackMap.get(bet.track);
          const existingEdge = existingBet ? existingBet.winPercentage - (existingBet.marketOdds > 1 ? 100 / existingBet.marketOdds : 0) : -999;
          if (!existingBet || betEdge > existingEdge) {
            trackMap.set(bet.track, bet);
          }
        });
        const bestPerTrack = Array.from(trackMap.values()).sort((a, b) =>
          new Date(a.offTime).getTime() - new Date(b.offTime).getTime()
        );

        const raceDates = new Set<string>();
        coveragePerRace.forEach((bet) => {
          const raceDate = new Date(bet.offTime);
          const ausDate = new Date(raceDate.getTime() + (11 * 60 * 60 * 1000));
          raceDates.add(ausDate.toISOString().split('T')[0]);
        });
        const availableDates = localFallbackSelections.availableDates?.length
          ? localFallbackSelections.availableDates
          : Array.from(raceDates).sort();
        const selectedDate = localFallbackSelections.selectedDate
          || requestedDate
          || availableDates[availableDates.length - 1];

        const topBets = [...displayedBestBets]
          .sort((a, b) => {
            if (b.confidenceScore !== a.confidenceScore) return b.confidenceScore - a.confidenceScore;
            const edgeA = a.winPercentage - (a.marketOdds > 1 ? 100 / a.marketOdds : 0);
            const edgeB = b.winPercentage - (b.marketOdds > 1 ? 100 / b.marketOdds : 0);
            return edgeB - edgeA;
          })
          .slice(0, limit);

        const summary = {
          totalAnalyzed: coveragePerRace.length,
          meetsAnalyzed: new Set(coveragePerRace.map((bet) => bet.track)).size,
          totalRaces: coveragePerRace.length,
          tracksWithBets: trackMap.size,
          tipCount: tipsList.length,
          avgConfidence: displayedBestBets.length > 0
            ? displayedBestBets.reduce((sum: number, b: BestBetRecommendation) => sum + b.confidenceScore, 0) / displayedBestBets.length
            : 0,
          betTypeBreakdown: {
            bankers: displayedBestBets.filter((b: BestBetRecommendation) => b.betType === "banker").length,
            value: displayedBestBets.filter((b: BestBetRecommendation) => b.betType === "value").length,
            formPicks: displayedBestBets.filter((b: BestBetRecommendation) => b.betType === "form_pick").length,
            roughies: displayedBestBets.filter((b: BestBetRecommendation) => b.betType === "roughie").length,
            bestChance: displayedBestBets.filter((b: BestBetRecommendation) => b.betType === "best_chance").length,
            modelPicks: displayedBestBets.filter((b: BestBetRecommendation) => b.betType === "model_pick").length,
            tipOnly: tipsList.length,
            exoticTips: displayedBestBets.filter((b: BestBetRecommendation) => b.selectionType === "exoticTip").length,
          }
        };

        const allSelections = [...modelBacked, ...tipsList];
        const dutchAnalysis = analyzeDutchingOpportunity(allSelections);
        let dutchBets: any[] = [];
        if (dutchAnalysis.isDutch && dutchAnalysis.partners.length >= 2) {
          const primaryPick = dutchAnalysis.partners[0];
          const baseBet = allSelections.find((s: any) => s.horseName === primaryPick.horseName);
          if (baseBet) {
            const dutchBet: any = {
              ...baseBet,
              id: `dutch-${baseBet.id}`,
              betType: 'dutched' as any,
              isDutchSelection: true,
              dutchPartners: dutchAnalysis.partners,
              dutchCombinedStrikeRate: dutchAnalysis.combinedStrike,
              dutchExpectedReturn: dutchAnalysis.expectedReturn,
              suggestedStake: `Dutch ${dutchAnalysis.partners.length} horses - ${dutchAnalysis.partners.map((p: any) => `${p.horseName} ${p.proportionalStake}`).join(', ')}`,
              reasons: [
                `Dutch bet: ${dutchAnalysis.partners.length} horses with positive edge in same race`,
                `Combined strike rate: ~${dutchAnalysis.combinedStrike}%`,
                ...dutchAnalysis.partners.map((p: any) => `${p.horseName} (${p.odds.toFixed(1)}) - ${p.edge.toFixed(1)}% edge, ${p.proportionalStake} stake`)
              ],
            };
            dutchBets.push(dutchBet);
          }
        }

        return res.json({
          success: true,
          bestPerTrack,
          bestBets: displayedBestBets,
          tips: tipsList,
          oneBetPerRace: allBetPicks,
          coveragePerRace,
          topBets,
          selectedDate,
          availableDates,
          dutchBets,
          summary,
          generatedAt: new Date().toISOString()
        });
      }

      // Use the local tips pipeline output first so the dashboard matches the saved racecard tips.
      let selections: any[] = [];
      let staleDbSelections: any[] = [];
      if (!localFallbackSelections) {
        
        try {
          if (requestedDate) {
            selections = await racePipeline.getSelectionsByDate(requestedDate);
          } else {
            const allActive = await racePipeline.getActiveSelections(500);
            const upcomingSelections = allActive.filter((s: any) => {
              const raceDate = s.raceDate || (s.offTime ? new Date(s.offTime).toISOString().split('T')[0] : null);
              return raceDate && raceDate >= todayStr;
            });

            if (upcomingSelections.length > 0) {
              selections = upcomingSelections;
            } else if (allActive.length > 0) {
              const mostRecentDate = allActive
                .map((s: any) => s.raceDate || (s.offTime ? new Date(s.offTime).toISOString().split('T')[0] : ''))
                .filter(Boolean)
                .sort()
                .pop();

              staleDbSelections = mostRecentDate
                ? allActive.filter((s: any) => {
                    const rd = s.raceDate || (s.offTime ? new Date(s.offTime).toISOString().split('T')[0] : null);
                    return rd === mostRecentDate;
                  })
                : [];
            }
          }
        } catch (error) {
          console.warn("[Best Bets] DB selection lookup failed after local tips miss:", (error as Error).message);
        }
      }

      if (selections.length === 0 && staleDbSelections.length > 0) {
        selections = staleDbSelections;
      }

      if (selections.length === 0) {
        return res.json({
          success: true,
          bestPerTrack: [],
          bestBets: [],
          tips: [],
          oneBetPerRace: [],
          coveragePerRace: [],
          topBets: [],
          selectedDate: requestedDate,
          availableDates: [],
          dutchBets: [],
          summary: {
            totalAnalyzed: 0,
            meetsAnalyzed: 0,
            totalRaces: 0,
            tracksWithBets: 0,
            tipCount: 0,
            avgConfidence: 0,
            betTypeBreakdown: {
              bankers: 0,
              value: 0,
              formPicks: 0,
              roughies: 0,
              bestChance: 0,
              modelPicks: 0,
              tipOnly: 0,
              exoticTips: 0,
            }
          },
          generatedAt: new Date().toISOString()
        });
      }
      
      // METRO TRACK FILTER: Best Bets only show metro meetings
      selections = selections.filter((s: any) => isMetroTrack(s.track));
      
      // MARKET FAVOURITE LOOKUP: Find the shortest-priced horse per race from runners data
      const marketFavMap = new Map<string, { horseName: string; odds: number; impliedProb: number }>();
      const runnersMap = new Map<string, any[]>();
      try {
        const selectionDates = Array.from(new Set(selections.map((s: any) => s.raceDate).filter(Boolean)));
        if (selectionDates.length > 0) {
          const { races: racesTable } = await import('@shared/schema');
          const { inArray } = await import('drizzle-orm');
          const allRacesForFav = await db.select().from(racesTable).where(inArray(racesTable.raceDate, selectionDates));
          for (const race of allRacesForFav) {
            if (!isMetroTrack(race.track)) continue;
            const runners = (race.runnersJson as any[]) || [];
            let favName = '';
            let favOdds = Infinity;
            for (const r of runners) {
              if (r.scratched) continue;
              let odds = 0;
              const oddsArr = r.odds;
              if (Array.isArray(oddsArr) && oddsArr.length > 0) {
                for (const entry of oddsArr) {
                  if (typeof entry === 'object' && entry !== null) {
                    odds = parseFloat(String(entry.win_odds || entry.odds || '0').replace('$', ''));
                  }
                  if (typeof entry === 'number') odds = entry;
                }
              }
              if (!odds) odds = parseFloat(String(r.sp || r.win_odds || '0').replace('$', ''));
              const name = r.horse || r.name || r.horse_name || '';
              if (odds > 1 && odds < favOdds && name) {
                favOdds = odds;
                favName = name;
              }
            }
            const raceKey = `${race.track}-R${race.raceNumber}`;
            runnersMap.set(raceKey, runners);
            if (favName && favOdds < Infinity) {
              marketFavMap.set(raceKey, { horseName: favName, odds: favOdds, impliedProb: parseFloat((100 / favOdds).toFixed(1)) });
            }
          }
        }
      } catch (e) {
        console.log('[Best Bets] Could not load market favourites:', (e as Error).message);
      }

      // QUALITY FILTER: Best Bets are hand-selected top picks
      // Require valid market odds and positive edge, then rank and select best per track
      selections = selections.map((s: any) => {
        const odds = s.marketOdds || 0;
        const winProb = s.winPercentage || s.modelProbability || 0;
        const marketImplied = odds > 1 ? 100 / odds : 0;
        const edge = winProb - marketImplied;
        return { ...s, _edge: edge, _winProb: winProb, _odds: odds };
      });

      // Defense-in-depth: filter out longshots that shouldn't be tips
      // But preserve at least the rank-1 pick per race so no race disappears entirely
      const rank1Keys = new Set<string>();
      for (const s of selections) {
        const rKey = `${s.track}-R${s.raceNumber}`;
        if (!rank1Keys.has(rKey)) rank1Keys.add(rKey); // first occurrence per race (rank 1)
      }
      const rank1Map = new Map<string, any>();
      for (const s of selections) {
        const rKey = `${s.track}-R${s.raceNumber}`;
        if (!rank1Map.has(rKey)) rank1Map.set(rKey, s);
      }
      selections = selections.filter((s: any) => {
        // Always keep rank-1 pick per race
        const rKey = `${s.track}-R${s.raceNumber}`;
        if (rank1Map.get(rKey) === s) return true;
        if (s._odds >= 30 && s._winProb < 10) return false;
        if (s._odds >= 20 && s._winProb < 8) return false;
        return true;
      });
      const allMetroSelections = [...selections];
      const allMetroTracks = new Set(allMetroSelections.map((s: any) => s.track));

      const selectionsByRace = new Map<string, any[]>();
      for (const s of allMetroSelections) {
        const raceKey = `${s.track}-R${s.raceNumber}`;
        if (!selectionsByRace.has(raceKey)) selectionsByRace.set(raceKey, []);
        selectionsByRace.get(raceKey)!.push(s);
      }

      const qualifiedSelections = selections.filter((s: any) => s._odds > 1 && s._edge >= 1.0);
      const tipCandidates = selections.filter((s: any) => s._odds > 1 && s._edge < 1.0);

      const valueCountPerRace = new Map<string, number>();
      for (const s of qualifiedSelections) {
        const raceKey = `${s.track}-R${s.raceNumber}`;
        valueCountPerRace.set(raceKey, (valueCountPerRace.get(raceKey) || 0) + 1);
      }

      let filteredQualified = qualifiedSelections;
      let filteredTipCandidates = tipCandidates;
      if (track) {
        filteredQualified = filteredQualified.filter((s: any) =>
          s.track.toLowerCase().includes((track as string).toLowerCase())
        );
        filteredTipCandidates = filteredTipCandidates.filter((s: any) =>
          s.track.toLowerCase().includes((track as string).toLowerCase())
        );
      }

      const scoredQualified = await Promise.all(filteredQualified.map((s: any) => calculateBestBetScore({
        ...s,
        paceSplits: s.paceSplitsJson
      })));

      scoredQualified.forEach((bet: any) => {
        const raceKey = `${bet.track}-R${bet.raceNumber}`;
        const qualifierCount = valueCountPerRace.get(raceKey) || 0;
        if (qualifierCount === 1) {
          bet.soleQualifier = true;
        }
      });

      const qualifiedRaceMap = new Map<string, BestBetRecommendation>();
      // Sort by pipeline rank first, then by blend of win% and edge
      scoredQualified.sort((a, b) => {
        const rankA = getPipelineRankValue(a);
        const rankB = getPipelineRankValue(b);
        if (rankA !== rankB) return rankA - rankB;
        const edgeA = a.winPercentage - (a.marketOdds > 1 ? 100 / a.marketOdds : 0);
        const edgeB = b.winPercentage - (b.marketOdds > 1 ? 100 / b.marketOdds : 0);
        const scoreA = a.winPercentage * 0.4 + Math.max(0, edgeA) * 0.6;
        const scoreB = b.winPercentage * 0.4 + Math.max(0, edgeB) * 0.6;
        return scoreB - scoreA;
      });
      scoredQualified.forEach(bet => {
        const raceKey = `${bet.track}-R${bet.raceNumber}`;
        if (!qualifiedRaceMap.has(raceKey)) qualifiedRaceMap.set(raceKey, bet);
      });

      const trackBetsMap = new Map<string, BestBetRecommendation[]>();
      for (const bet of qualifiedRaceMap.values()) {
        if (!trackBetsMap.has(bet.track)) trackBetsMap.set(bet.track, []);
        trackBetsMap.get(bet.track)!.push(bet);
      }
      const modelBacked: BestBetRecommendation[] = [];
      for (const [, bets] of trackBetsMap) {
        bets.sort((a: BestBetRecommendation, b: BestBetRecommendation) => {
          const edgeA = a.winPercentage - (a.marketOdds > 1 ? 100 / a.marketOdds : 0);
          const edgeB = b.winPercentage - (b.marketOdds > 1 ? 100 / b.marketOdds : 0);
          return edgeB - edgeA;
        });
        modelBacked.push(...bets.slice(0, 3));
      }

      const allQualifiedSelections = Array.from(qualifiedRaceMap.values());
      const coveredRaces = new Set(allQualifiedSelections.map(b => `${b.track}-R${b.raceNumber}`));

      const scoredTips = await Promise.all(filteredTipCandidates.map((s: any) => calculateBestBetScore({
        ...s,
        paceSplits: s.paceSplitsJson
      })));
      const tipRaceMap = new Map<string, BestBetRecommendation>();
      // Sort tips by pipeline rank first, then AI score * win%
      scoredTips.sort((a, b) => {
        const rankA = getPipelineRankValue(a);
        const rankB = getPipelineRankValue(b);
        if (rankA !== rankB) return rankA - rankB;
        const aScore = ((a as any).aiScore || 50) / 100 * a.winPercentage;
        const bScore = ((b as any).aiScore || 50) / 100 * b.winPercentage;
        return bScore - aScore;
      });
      scoredTips.forEach(bet => {
        const raceKey = `${bet.track}-R${bet.raceNumber}`;
        if (!coveredRaces.has(raceKey) && !tipRaceMap.has(raceKey)) {
          bet.betType = "tip_only";
          bet.tipWarning = "This selection does not align with the model \u2014 provided as a guide only";
          tipRaceMap.set(raceKey, bet);
        }
      });
      const tipsList = Array.from(tipRaceMap.values());

      if (tipsList.length > 0) {
        const tipScores = tipsList.map(t => t.confidenceScore || 0).sort((a, b) => a - b);
        const minScore = tipScores[0];
        const maxScore = tipScores[tipScores.length - 1];
        const range = maxScore - minScore || 1;

        for (const tip of tipsList) {
          const raw = tip.confidenceScore || 0;
          const normalised = ((raw - minScore) / range) * 70 + 20;
          tip.confidenceScore = Math.round(Math.min(100, normalised));

          if (normalised >= 72) tip.rating = 5;
          else if (normalised >= 56) tip.rating = 4;
          else if (normalised >= 42) tip.rating = 3;
          else if (normalised >= 30) tip.rating = 2;
          else tip.rating = 1;

          const tipFieldSize = tip.fieldSize || 0;
          let qualityScore = 50;
          const qualityReasons: string[] = [];

          if (tipFieldSize >= 6 && tipFieldSize <= 12) qualityScore += 15;
          else if (tipFieldSize <= 3) { qualityScore -= 15; qualityReasons.push("Very small field"); }
          else if (tipFieldSize >= 16) { qualityScore -= 10; qualityReasons.push("Large field"); }

          const raceNameLower = (tip.raceName || '').toLowerCase();
          if (raceNameLower.includes('maiden') || raceNameLower.includes('mdn')) { qualityScore -= 12; qualityReasons.push("Maiden race"); }
          else if (raceNameLower.includes('group') || raceNameLower.includes('listed')) qualityScore += 10;
          else if (raceNameLower.includes('jump') || raceNameLower.includes('trial')) { qualityScore -= 20; qualityReasons.push("Trial/jump-out"); }

          if (tip.winPercentage < 8) { qualityScore -= 10; qualityReasons.push("Low model confidence"); }

          tip.raceQualityScore = Math.max(0, Math.min(100, qualityScore));
          tip.raceQualityLabel = qualityScore >= 65 ? "HIGH" : qualityScore >= 45 ? "MEDIUM" : qualityScore >= 30 ? "LOW" : "WATCH ONLY";
          tip.raceQualityReasons = qualityReasons;

          if (qualityScore < 30) {
            tip.rating = 0;
            tip.tipWarning = "Low confidence race - watch only";
          }
        }
      }

      // Apply exotic recommendations
      const applyExoticAnalysis = (bet: BestBetRecommendation) => {
        const raceKey = `${bet.track}-R${bet.raceNumber}`;
        const raceSelections = selectionsByRace.get(raceKey) || [];
        const raceRunners = runnersMap.get(raceKey) || [];

        const exoticRec = buildExoticRecommendation(raceRunners, bet, raceSelections, marketFavMap);
        bet.selectionType = exoticRec.selectionType;
        bet.exoticType = exoticRec.exoticType;
        bet.exoticRationale = exoticRec.exoticRationale;
        bet.exoticPicks = exoticRec.exoticPicks;
        bet.fieldSize = exoticRec.fieldSize;
        bet.raceCompetitiveness = exoticRec.raceCompetitiveness;
      };

      allQualifiedSelections.forEach(applyExoticAnalysis);
      tipsList.forEach(bet => {
        bet.selectionType = "winTip";
      });

      // Attach market favourite to all selections where it differs from our pick
      const attachMarketFav = (bet: BestBetRecommendation) => {
        const raceKey = `${bet.track}-R${bet.raceNumber}`;
        const fav = marketFavMap.get(raceKey);
        if (fav && fav.horseName.toLowerCase() !== bet.horseName.toLowerCase()) {
          bet.marketFavourite = fav;
        }
      };
      allQualifiedSelections.forEach(attachMarketFav);
      tipsList.forEach(attachMarketFav);

      modelBacked.sort((a, b) => new Date(a.offTime).getTime() - new Date(b.offTime).getTime());
      allQualifiedSelections.sort((a, b) => new Date(a.offTime).getTime() - new Date(b.offTime).getTime());
      tipsList.sort((a, b) => new Date(a.offTime).getTime() - new Date(b.offTime).getTime());

      const oneBetPerRace = [...allQualifiedSelections, ...tipsList].sort((a, b) =>
        new Date(a.offTime).getTime() - new Date(b.offTime).getTime()
      );

      const trackMap = new Map<string, BestBetRecommendation>();
      scoredQualified.forEach((bet: any) => {
        const betEdge = bet.winPercentage - (bet.marketOdds > 1 ? 100 / bet.marketOdds : 0);
        const existingBet = trackMap.get(bet.track);
        const existingEdge = existingBet ? existingBet.winPercentage - (existingBet.marketOdds > 1 ? 100 / existingBet.marketOdds : 0) : -999;
        if (!existingBet || betEdge > existingEdge) {
          trackMap.set(bet.track, bet);
        }
      });
      const bestPerTrack = Array.from(trackMap.values()).sort((a, b) =>
        new Date(a.offTime).getTime() - new Date(b.offTime).getTime()
      );

      const raceDates = new Set<string>();
      oneBetPerRace.forEach((bet) => {
        const raceDate = new Date(bet.offTime);
        const ausDate = new Date(raceDate.getTime() + (11 * 60 * 60 * 1000));
        const dateStr = ausDate.toISOString().split('T')[0];
        raceDates.add(dateStr);
      });
      const derivedAvailableDates = Array.from(raceDates).sort();
      const availableDates = derivedAvailableDates;
      const selectedDate = requestedDate || availableDates[availableDates.length - 1];

      const topBets = [...modelBacked]
        .sort((a, b) => {
          if (b.confidenceScore !== a.confidenceScore) return b.confidenceScore - a.confidenceScore;
          const edgeA = a.winPercentage - (a.marketOdds > 1 ? 100 / a.marketOdds : 0);
          const edgeB = b.winPercentage - (b.marketOdds > 1 ? 100 / b.marketOdds : 0);
          return edgeB - edgeA;
        })
        .slice(0, limit);

      const summary = {
        totalAnalyzed: allMetroSelections.length,
        meetsAnalyzed: allMetroTracks.size,
        totalRaces: oneBetPerRace.length,
        tracksWithBets: trackMap.size,
        tipCount: tipsList.length,
        avgConfidence: modelBacked.length > 0
          ? modelBacked.reduce((sum: number, b: BestBetRecommendation) => sum + b.confidenceScore, 0) / modelBacked.length
          : 0,
        betTypeBreakdown: {
          bankers: modelBacked.filter((b: BestBetRecommendation) => b.betType === "banker").length,
          value: modelBacked.filter((b: BestBetRecommendation) => b.betType === "value").length,
          formPicks: modelBacked.filter((b: BestBetRecommendation) => b.betType === "form_pick").length,
          roughies: modelBacked.filter((b: BestBetRecommendation) => b.betType === "roughie").length,
          bestChance: modelBacked.filter((b: BestBetRecommendation) => b.betType === "best_chance").length,
          modelPicks: modelBacked.filter((b: BestBetRecommendation) => b.betType === "model_pick").length,
          tipOnly: tipsList.length,
          exoticTips: modelBacked.filter((b: BestBetRecommendation) => b.selectionType === "exoticTip").length,
        }
      };

      const allSelections = [...(modelBacked || []), ...(tipsList || [])];
      const dutchAnalysis = analyzeDutchingOpportunity(allSelections);

      let dutchBets: any[] = [];
      if (dutchAnalysis.isDutch && dutchAnalysis.partners.length >= 2) {
        const primaryPick = dutchAnalysis.partners[0];
        const baseBet = allSelections.find((s: any) => s.horseName === primaryPick.horseName);
        if (baseBet) {
          const dutchBet: any = {
            ...baseBet,
            id: `dutch-${baseBet.id}`,
            betType: 'dutched' as any,
            isDutchSelection: true,
            dutchPartners: dutchAnalysis.partners,
            dutchCombinedStrikeRate: dutchAnalysis.combinedStrike,
            dutchExpectedReturn: dutchAnalysis.expectedReturn,
            suggestedStake: `Dutch ${dutchAnalysis.partners.length} horses - ${dutchAnalysis.partners.map((p: any) => `${p.horseName} ${p.proportionalStake}`).join(', ')}`,
            reasons: [
              `Dutch bet: ${dutchAnalysis.partners.length} horses with positive edge in same race`,
              `Combined strike rate: ~${dutchAnalysis.combinedStrike}%`,
              ...dutchAnalysis.partners.map((p: any) => `${p.horseName} (${p.odds.toFixed(1)}) - ${p.edge.toFixed(1)}% edge, ${p.proportionalStake} stake`)
            ],
          };
          dutchBets.push(dutchBet);
        }
      }

      res.json({
        success: true,
        bestPerTrack,
        bestBets: modelBacked,
        tips: tipsList,
        oneBetPerRace,
        coveragePerRace: oneBetPerRace,
        topBets,
        selectedDate,
        availableDates,
        dutchBets,
        summary,
        generatedAt: new Date().toISOString()
      });
      
    } catch (error: any) {
      console.error("Best bets error:", error);
      res.status(500).json({ error: error.message || "Failed to generate best bets" });
    }
  });

  // ============================================
  // BET SLIP API - Kelly Criterion Staking
  // ============================================
  
  // In-memory bet slip storage (session-based)
  const betSlips: Map<string, {
    selections: Map<string, any>;
    bankroll: number;
    stakingMethod: string;
    kellyFraction: number;
    maxStakePercentage: number;
  }> = new Map();
  
  function getBetSlip(sessionId: string) {
    if (!betSlips.has(sessionId)) {
      betSlips.set(sessionId, {
        selections: new Map(),
        bankroll: 0,
        stakingMethod: "fractional_kelly",
        kellyFraction: 0.25, // Quarter Kelly (conservative)
        maxStakePercentage: 0.10 // Max 10% per bet
      });
    }
    return betSlips.get(sessionId)!;
  }
  
  // Helper: Calculate Kelly stake
  function calculateKellyStake(odds: number, probability: number, kellyFraction: number, maxStake: number): number {
    if (odds <= 1 || probability <= 0 || probability >= 1) return 0;
    
    const b = odds - 1; // Decimal odds to "b" value
    const p = probability;
    const q = 1 - p;
    
    let kelly = (b * p - q) / b;
    kelly *= kellyFraction; // Apply Kelly fraction
    kelly = Math.min(kelly, maxStake); // Cap at max
    
    return Math.max(kelly, 0); // No negative stakes
  }
  
  // Helper: Calculate expected value
  function calculateExpectedValue(odds: number, probability: number, stake: number): number {
    const winAmount = stake * (odds - 1);
    return (probability * winAmount) - ((1 - probability) * stake);
  }
  
  // Helper: Assess risk level
  function assessRisk(stakePct: number, odds: number, probability: number): "low" | "medium" | "high" {
    let riskScore = 0;
    
    if (stakePct > 0.05) riskScore += 2;
    else if (stakePct > 0.03) riskScore += 1;
    
    if (odds > 10) riskScore += 2;
    else if (odds > 5) riskScore += 1;
    
    if (probability < 0.15) riskScore += 2;
    else if (probability < 0.25) riskScore += 1;
    
    if (riskScore >= 4) return "high";
    if (riskScore >= 2) return "medium";
    return "low";
  }
  
  // Helper: Generate stake reasoning
  function generateStakeReasoning(selection: any, stakePct: number, ev: number): string {
    const reasons: string[] = [];
    
    if (selection.confidenceScore >= 70) {
      reasons.push(`High confidence (${selection.confidenceScore.toFixed(0)}%)`);
    } else if (selection.confidenceScore >= 50) {
      reasons.push(`Moderate confidence (${selection.confidenceScore.toFixed(0)}%)`);
    } else {
      reasons.push(`Lower confidence (${selection.confidenceScore.toFixed(0)}%)`);
    }
    
    const impliedProb = 1 / selection.odds;
    const valueOverlay = selection.predictedWinProb / impliedProb;
    
    if (valueOverlay >= 1.3) {
      reasons.push(`Strong value - ${((valueOverlay - 1) * 100).toFixed(0)}% overlay`);
    } else if (valueOverlay >= 1.15) {
      reasons.push("Good value at these odds");
    } else if (valueOverlay < 1.0) {
      reasons.push("Below value - reduced stake");
    }
    
    if (ev > 0) {
      reasons.push(`+EV $${ev.toFixed(2)}`);
    } else {
      reasons.push("Negative EV");
    }
    
    return reasons.join(" | ");
  }
  
  // Helper: Generate multi-bet options
  function getMultiBetOptions(selections: any[]): any[] {
    const options: any[] = [];
    const n = selections.length;
    
    if (n < 2) return options;
    
    // Helper to get combinations
    function combinations<T>(arr: T[], k: number): T[][] {
      if (k === 0) return [[]];
      if (arr.length === 0) return [];
      const [first, ...rest] = arr;
      const withFirst = combinations(rest, k - 1).map(c => [first, ...c]);
      const withoutFirst = combinations(rest, k);
      return [...withFirst, ...withoutFirst];
    }
    
    // Calculate combined odds and probability
    const calcCombinedOdds = (sels: any[]) => sels.reduce((acc, s) => acc * s.odds, 1);
    const calcCombinedProb = (sels: any[]) => sels.reduce((acc, s) => acc * s.predictedWinProb, 1);
    
    // Best Double
    if (n >= 2) {
      const doubles = combinations(selections, 2);
      const bestDouble = doubles.reduce((best, current) => 
        calcCombinedProb(current) > calcCombinedProb(best) ? current : best
      );
      
      options.push({
        type: "double",
        name: "Best Double",
        selections: bestDouble.map(s => s.horseName),
        combinedOdds: Math.round(calcCombinedOdds(bestDouble) * 100) / 100,
        combinedProb: Math.round(calcCombinedProb(bestDouble) * 10000) / 10000,
        recommendedStakePct: 0.5 // 0.5% for multis
      });
    }
    
    // Best Treble
    if (n >= 3) {
      const trebles = combinations(selections, 3);
      const bestTreble = trebles.reduce((best, current) => 
        calcCombinedProb(current) > calcCombinedProb(best) ? current : best
      );
      
      options.push({
        type: "treble",
        name: "Best Treble",
        selections: bestTreble.map(s => s.horseName),
        combinedOdds: Math.round(calcCombinedOdds(bestTreble) * 100) / 100,
        combinedProb: Math.round(calcCombinedProb(bestTreble) * 10000) / 10000,
        recommendedStakePct: 0.3
      });
    }
    
    // All-up accumulator
    if (n >= 2) {
      options.push({
        type: "accumulator",
        name: `${n}-Leg Multi`,
        selections: selections.map(s => s.horseName),
        combinedOdds: Math.round(calcCombinedOdds(selections) * 100) / 100,
        combinedProb: Math.round(calcCombinedProb(selections) * 10000) / 10000,
        recommendedStakePct: 0.2
      });
    }
    
    return options;
  }
  
  // GET /api/betslip - Get current bet slip
  app.get("/api/betslip", (req, res) => {
    const sessionId = (req.headers["x-session-id"] as string) || "default";
    const slip = getBetSlip(sessionId);
    
    res.json({
      success: true,
      selections: Array.from(slip.selections.values()),
      selectionCount: slip.selections.size,
      bankroll: slip.bankroll,
      stakingMethod: slip.stakingMethod,
      kellyFraction: slip.kellyFraction,
      maxStakePercentage: slip.maxStakePercentage
    });
  });
  
  // POST /api/betslip/add - Add selection to bet slip
  app.post("/api/betslip/add", (req, res) => {
    try {
      const sessionId = (req.headers["x-session-id"] as string) || "default";
      const slip = getBetSlip(sessionId);
      const data = req.body;
      
      // Required fields
      if (!data.horseName || !data.track || data.raceNumber === undefined || 
          data.odds === undefined || data.predictedWinProb === undefined) {
        return res.status(400).json({
          success: false,
          error: "Missing required fields: horseName, track, raceNumber, odds, predictedWinProb"
        });
      }
      
      // Generate ID if not provided
      const id = data.id || `${data.track}-R${data.raceNumber}-${data.horseName}`.replace(/\s+/g, '-');
      
      const selection = {
        id,
        horseName: data.horseName,
        track: data.track,
        raceNumber: data.raceNumber,
        raceDate: data.raceDate || new Date().toISOString().split('T')[0],
        raceTime: data.raceTime || "",
        odds: data.odds,
        predictedWinProb: data.predictedWinProb,
        confidenceScore: data.confidenceScore || data.predictedWinProb * 100,
        betType: data.betType || "win",
        placeOdds: data.placeOdds,
        reasons: data.reasons || [],
        addedAt: new Date().toISOString()
      };
      
      slip.selections.set(id, selection);
      
      res.json({
        success: true,
        message: `Added ${data.horseName} to bet slip`,
        selectionCount: slip.selections.size,
        selection
      });
    } catch (error: any) {
      res.status(500).json({ success: false, error: error.message });
    }
  });
  
  // DELETE /api/betslip/remove/:id - Remove selection
  app.delete("/api/betslip/remove/:id", (req, res) => {
    const sessionId = (req.headers["x-session-id"] as string) || "default";
    const slip = getBetSlip(sessionId);
    const id = req.params.id;
    
    if (slip.selections.has(id)) {
      const removed = slip.selections.get(id);
      slip.selections.delete(id);
      res.json({
        success: true,
        message: `Removed ${removed.horseName} from bet slip`,
        selectionCount: slip.selections.size
      });
    } else {
      res.json({
        success: false,
        message: "Selection not found",
        selectionCount: slip.selections.size
      });
    }
  });
  
  // POST /api/betslip/clear - Clear all selections
  app.post("/api/betslip/clear", (req, res) => {
    const sessionId = (req.headers["x-session-id"] as string) || "default";
    const slip = getBetSlip(sessionId);
    const count = slip.selections.size;
    slip.selections.clear();
    
    res.json({
      success: true,
      message: `Cleared ${count} selections`,
      selectionCount: 0
    });
  });
  
  // POST /api/betslip/bankroll - Set bankroll
  app.post("/api/betslip/bankroll", (req, res) => {
    const sessionId = (req.headers["x-session-id"] as string) || "default";
    const slip = getBetSlip(sessionId);
    const { amount } = req.body;
    
    if (!amount || amount <= 0) {
      return res.status(400).json({
        success: false,
        error: "Bankroll must be a positive number"
      });
    }
    
    slip.bankroll = amount;
    
    res.json({
      success: true,
      bankroll: slip.bankroll,
      message: `Bankroll set to $${amount.toFixed(2)}`
    });
  });
  
  // POST /api/betslip/staking-method - Set staking method
  app.post("/api/betslip/staking-method", (req, res) => {
    const sessionId = (req.headers["x-session-id"] as string) || "default";
    const slip = getBetSlip(sessionId);
    const { method, kellyFraction, maxStakePercentage } = req.body;
    
    const validMethods = ["kelly", "fractional_kelly", "level_stakes", "proportional", "percentage"];
    if (!validMethods.includes(method)) {
      return res.status(400).json({
        success: false,
        error: `Invalid method. Choose from: ${validMethods.join(", ")}`
      });
    }
    
    slip.stakingMethod = method;
    if (kellyFraction !== undefined) slip.kellyFraction = kellyFraction;
    if (maxStakePercentage !== undefined) slip.maxStakePercentage = maxStakePercentage;
    
    res.json({
      success: true,
      method: slip.stakingMethod,
      kellyFraction: slip.kellyFraction,
      maxStakePercentage: slip.maxStakePercentage
    });
  });
  
  // POST /api/betslip/calculate - Calculate optimal stakes
  app.post("/api/betslip/calculate", (req, res) => {
    try {
      const sessionId = (req.headers["x-session-id"] as string) || "default";
      const slip = getBetSlip(sessionId);
      const selections = Array.from(slip.selections.values());
      
      if (selections.length === 0) {
        return res.json({
          success: true,
          totalSelections: 0,
          totalStake: 0,
          totalPotentialReturn: 0,
          totalPotentialProfit: 0,
          combinedProbability: 0,
          expectedValue: 0,
          riskAssessment: "none",
          bankrollPercentageUsed: 0,
          recommendations: [],
          multiBetOptions: []
        });
      }
      
      if (slip.bankroll <= 0) {
        return res.json({
          success: true,
          totalSelections: selections.length,
          totalStake: 0,
          totalPotentialReturn: 0,
          totalPotentialProfit: 0,
          combinedProbability: 0,
          expectedValue: 0,
          riskAssessment: "unknown",
          bankrollPercentageUsed: 0,
          recommendations: selections.map(sel => ({
            selectionId: sel.id,
            horseName: sel.horseName,
            recommendedStake: 0,
            stakePercentage: 0,
            potentialReturn: 0,
            potentialProfit: 0,
            expectedValue: 0,
            riskLevel: "unknown",
            reasoning: "Set bankroll to get stake recommendations"
          })),
          multiBetOptions: []
        });
      }
      
      const recommendations: any[] = [];
      let totalStake = 0;
      let totalPotentialReturn = 0;
      let totalEV = 0;
      
      // Calculate total confidence for proportional staking
      const totalConfidence = selections.reduce((sum, s) => sum + s.confidenceScore, 0);
      
      for (const selection of selections) {
        let stakePct = 0;
        
        // Calculate stake based on method
        if (slip.stakingMethod === "kelly" || slip.stakingMethod === "fractional_kelly") {
          stakePct = calculateKellyStake(
            selection.odds,
            selection.predictedWinProb,
            slip.kellyFraction,
            slip.maxStakePercentage
          );
        } else if (slip.stakingMethod === "proportional") {
          const proportion = selection.confidenceScore / totalConfidence;
          stakePct = Math.min(proportion * 0.30, slip.maxStakePercentage);
        } else if (slip.stakingMethod === "level_stakes") {
          stakePct = Math.min(0.02, slip.maxStakePercentage);
        } else {
          stakePct = 0.02;
        }
        
        // Calculate actual stake
        let stake = slip.bankroll * stakePct;
        stake = Math.max(stake, stakePct > 0 ? 1 : 0); // Min $1
        stake = Math.min(stake, slip.bankroll * slip.maxStakePercentage);
        
        // Recalculate percentage
        stakePct = stake / slip.bankroll;
        
        // Calculate returns and EV
        const potentialReturn = stake * selection.odds;
        const potentialProfit = potentialReturn - stake;
        const ev = calculateExpectedValue(selection.odds, selection.predictedWinProb, stake);
        
        // Assess risk
        const risk = assessRisk(stakePct, selection.odds, selection.predictedWinProb);
        
        // Generate reasoning
        const reasoning = generateStakeReasoning(selection, stakePct, ev);
        
        recommendations.push({
          selectionId: selection.id,
          horseName: selection.horseName,
          recommendedStake: Math.round(stake * 100) / 100,
          stakePercentage: Math.round(stakePct * 10000) / 100,
          potentialReturn: Math.round(potentialReturn * 100) / 100,
          potentialProfit: Math.round(potentialProfit * 100) / 100,
          expectedValue: Math.round(ev * 100) / 100,
          riskLevel: risk,
          reasoning
        });
        
        totalStake += stake;
        totalPotentialReturn += potentialReturn;
        totalEV += ev;
      }
      
      // Sort by stake (highest first)
      recommendations.sort((a, b) => b.recommendedStake - a.recommendedStake);
      
      // Overall risk assessment
      const highRiskCount = recommendations.filter(r => r.riskLevel === "high").length;
      const bankrollPctUsed = (totalStake / slip.bankroll) * 100;
      
      let overallRisk: "low" | "medium" | "high" = "low";
      if (bankrollPctUsed > 20 || highRiskCount >= recommendations.length / 2) {
        overallRisk = "high";
      } else if (bankrollPctUsed > 10 || highRiskCount >= 2) {
        overallRisk = "medium";
      }
      
      // Combined probability
      const combinedProb = selections.reduce((acc, s) => acc * s.predictedWinProb, 1);
      
      // Multi-bet options
      const multiBetOptions = getMultiBetOptions(selections);
      
      res.json({
        success: true,
        totalSelections: selections.length,
        totalStake: Math.round(totalStake * 100) / 100,
        totalPotentialReturn: Math.round(totalPotentialReturn * 100) / 100,
        totalPotentialProfit: Math.round((totalPotentialReturn - totalStake) * 100) / 100,
        combinedProbability: Math.round(combinedProb * 1000000) / 1000000,
        expectedValue: Math.round(totalEV * 100) / 100,
        riskAssessment: overallRisk,
        bankrollPercentageUsed: Math.round(bankrollPctUsed * 100) / 100,
        recommendations,
        multiBetOptions
      });
      
    } catch (error: any) {
      res.status(500).json({ success: false, error: error.message });
    }
  });
  
  // POST /api/betslip/quick-calculate - Set bankroll and calculate in one call
  app.post("/api/betslip/quick-calculate", (req, res) => {
    try {
      const sessionId = (req.headers["x-session-id"] as string) || "default";
      const slip = getBetSlip(sessionId);
      const { bankroll, stakingMethod } = req.body;
      
      if (bankroll && bankroll > 0) {
        slip.bankroll = bankroll;
      }
      
      if (stakingMethod) {
        slip.stakingMethod = stakingMethod;
      }
      
      // Calculate stakes inline (same logic as /api/betslip/calculate)
      const selections = Array.from(slip.selections.values());
      
      if (selections.length === 0) {
        return res.json({
          success: true,
          totalSelections: 0,
          totalStake: 0,
          totalPotentialReturn: 0,
          totalPotentialProfit: 0,
          combinedProbability: 0,
          expectedValue: 0,
          riskAssessment: "none",
          bankrollPercentageUsed: 0,
          recommendations: [],
          multiBetOptions: []
        });
      }
      
      if (slip.bankroll <= 0) {
        return res.json({
          success: true,
          totalSelections: selections.length,
          totalStake: 0,
          message: "Set bankroll to get stake recommendations",
          recommendations: selections.map(sel => ({
            selectionId: sel.id,
            horseName: sel.horseName,
            recommendedStake: 0,
            stakePercentage: 0,
            potentialReturn: 0,
            potentialProfit: 0,
            expectedValue: 0,
            riskLevel: "unknown",
            reasoning: "Set bankroll to get stake recommendations"
          })),
          multiBetOptions: []
        });
      }
      
      const recommendations: any[] = [];
      let totalStake = 0;
      let totalPotentialReturn = 0;
      let totalEV = 0;
      const totalConfidence = selections.reduce((sum, s) => sum + s.confidenceScore, 0);
      
      for (const selection of selections) {
        let stakePct = 0;
        
        if (slip.stakingMethod === "kelly" || slip.stakingMethod === "fractional_kelly") {
          stakePct = calculateKellyStake(selection.odds, selection.predictedWinProb, slip.kellyFraction, slip.maxStakePercentage);
        } else if (slip.stakingMethod === "proportional") {
          const proportion = selection.confidenceScore / totalConfidence;
          stakePct = Math.min(proportion * 0.30, slip.maxStakePercentage);
        } else {
          stakePct = Math.min(0.02, slip.maxStakePercentage);
        }
        
        let stake = slip.bankroll * stakePct;
        stake = Math.max(stake, stakePct > 0 ? 1 : 0);
        stake = Math.min(stake, slip.bankroll * slip.maxStakePercentage);
        stakePct = stake / slip.bankroll;
        
        const potentialReturn = stake * selection.odds;
        const potentialProfit = potentialReturn - stake;
        const ev = calculateExpectedValue(selection.odds, selection.predictedWinProb, stake);
        const risk = assessRisk(stakePct, selection.odds, selection.predictedWinProb);
        const reasoning = generateStakeReasoning(selection, stakePct, ev);
        
        recommendations.push({
          selectionId: selection.id,
          horseName: selection.horseName,
          recommendedStake: Math.round(stake * 100) / 100,
          stakePercentage: Math.round(stakePct * 10000) / 100,
          potentialReturn: Math.round(potentialReturn * 100) / 100,
          potentialProfit: Math.round(potentialProfit * 100) / 100,
          expectedValue: Math.round(ev * 100) / 100,
          riskLevel: risk,
          reasoning
        });
        
        totalStake += stake;
        totalPotentialReturn += potentialReturn;
        totalEV += ev;
      }
      
      recommendations.sort((a, b) => b.recommendedStake - a.recommendedStake);
      
      const highRiskCount = recommendations.filter(r => r.riskLevel === "high").length;
      const bankrollPctUsed = (totalStake / slip.bankroll) * 100;
      
      let overallRisk: "low" | "medium" | "high" = "low";
      if (bankrollPctUsed > 20 || highRiskCount >= recommendations.length / 2) overallRisk = "high";
      else if (bankrollPctUsed > 10 || highRiskCount >= 2) overallRisk = "medium";
      
      const combinedProb = selections.reduce((acc, s) => acc * s.predictedWinProb, 1);
      const multiBetOptions = getMultiBetOptions(selections);
      
      res.json({
        success: true,
        totalSelections: selections.length,
        totalStake: Math.round(totalStake * 100) / 100,
        totalPotentialReturn: Math.round(totalPotentialReturn * 100) / 100,
        totalPotentialProfit: Math.round((totalPotentialReturn - totalStake) * 100) / 100,
        combinedProbability: Math.round(combinedProb * 1000000) / 1000000,
        expectedValue: Math.round(totalEV * 100) / 100,
        riskAssessment: overallRisk,
        bankrollPercentageUsed: Math.round(bankrollPctUsed * 100) / 100,
        recommendations,
        multiBetOptions
      });
      
    } catch (error: any) {
      res.status(500).json({ success: false, error: error.message });
    }
  });

  app.get("/api/download/:filename", async (req, res) => {
    const { filename } = req.params;
    const allowedFiles: Record<string, { output: string; sources: string[] }> = {
      "wizzards-core-backend.zip": {
        output: "/tmp/wizzards-core-backend.zip",
        sources: [
          "server/python", "server/routes.ts", "server/pipeline.ts",
          "server/storage.ts", "server/db.ts", "server/scheduler.ts",
          "server/archival.ts", "server/index.ts", "server/static.ts",
          "shared/schema.ts", "drizzle.config.ts", "README.md"
        ]
      },
      "wizzards-frontend.zip": {
        output: "/tmp/wizzards-frontend.zip",
        sources: ["client/src"]
      }
    };
    const config = allowedFiles[filename];
    if (!config) {
      return res.status(404).json({ error: "File not found" });
    }
    if (!fs.existsSync(config.output)) {
      try {
        const { execSync } = await import("child_process");
        execSync(`zip -r ${config.output} ${config.sources.join(" ")} -x '*/__pycache__/*' '*/models/*' '*.pkl' '*.joblib'`, { cwd: process.cwd() });
      } catch (e: any) {
        return res.status(500).json({ error: "Failed to generate zip" });
      }
    }
    res.setHeader("Content-Type", "application/zip");
    res.setHeader("Content-Disposition", `attachment; filename="${filename}"`);
    const stream = fs.createReadStream(config.output);
    stream.pipe(res);
  });

  app.get("/api/backtest-results", async (req, res) => {
    try {
      const resultsPath = path.join(process.cwd(), "backtest_results", "backtest_results.json");
      if (!fs.existsSync(resultsPath)) {
        return res.json({ available: false, message: "No backtest results available yet" });
      }
      const data = JSON.parse(fs.readFileSync(resultsPath, "utf-8"));
      return res.json({ available: true, ...data });
    } catch (e: any) {
      return res.status(500).json({ error: e.message });
    }
  });

  // ============================================================
  // SECTIONAL TIMES ENDPOINTS
  // ============================================================

  app.get("/api/sectional-times/horse/:horseName", async (req, res) => {
    try {
      const horseName = decodeURIComponent(req.params.horseName);
      const result = await db.execute(
        sql`SELECT race_date, track, race_number, race_name, distance_m,
               last_200m_speed, last_200m_time, last_400m_speed, last_400m_time,
               last_600m_speed, last_600m_time, last_800m_speed, last_800m_time,
               finishing_burst, avg_speed, splits_json, winning_time, track_config
        FROM sectional_times
        WHERE LOWER(horse_name) = LOWER(${horseName})
        ORDER BY race_date DESC
        LIMIT 10`
      );
      res.json({ success: true, sectionals: result.rows });
    } catch (error: any) {
      res.status(500).json({ success: false, error: error.message });
    }
  });

  app.get("/api/sectional-times/stats", async (req, res) => {
    try {
      const result = await db.execute(sql.raw(`
        SELECT COUNT(*) as total_records,
          COUNT(DISTINCT horse_name) as unique_horses,
          COUNT(DISTINCT track) as tracks,
          COUNT(DISTINCT race_date) as race_dates,
          MIN(race_date) as earliest_date,
          MAX(race_date) as latest_date,
          COUNT(race_results_history_id) as matched_records
        FROM sectional_times
      `));
      res.json({ success: true, stats: result.rows[0] });
    } catch (error: any) {
      res.status(500).json({ success: false, error: error.message });
    }
  });

  // ============================================================
  // GAP ANALYSIS ENDPOINTS
  // ============================================================

  app.get("/api/gap-analysis/snapshots-summary", async (req, res) => {
    try {
      const result = await db.execute(sql.raw(`
        SELECT COUNT(*) as total_snapshots,
          COUNT(DISTINCT race_date) as dates_covered,
          COUNT(DISTINCT track) as tracks_covered,
          MIN(created_at) as first_snapshot,
          MAX(created_at) as last_snapshot
        FROM feature_snapshots
      `));
      const row: any = result.rows[0] || {};
      res.json({
        totalSnapshots: Number(row.total_snapshots) || 0,
        datesCovered: Number(row.dates_covered) || 0,
        tracksCovered: Number(row.tracks_covered) || 0,
        firstSnapshot: row.first_snapshot,
        lastSnapshot: row.last_snapshot,
      });
    } catch (error: any) {
      console.error("Snapshots summary error:", error);
      res.status(500).json({ error: error.message || "Failed to fetch snapshots summary" });
    }
  });

  app.get("/api/gap-analysis/feature-drift", async (req, res) => {
    try {
      const { execSync } = await import("child_process");
      const output = execSync(`"${resolvePythonBin()}" server/python/compare_features.py`, {
        timeout: 30000,
        cwd: process.cwd(),
      }).toString();
      const report = JSON.parse(output);
      const parityScore = report.summary?.overall_parity_score ?? 0;
      const perFeature = report.per_feature_drift || [];
      const mapFeature = (f: any) => ({
        feature: f.feature,
        nan_ratio: f.nan_ratio ?? 0,
        zero_ratio: f.zero_ratio ?? 0,
        range_violation: (f.anomalies && f.anomalies.length > 0) || false,
        drift_score: f.zero_ratio + f.nan_ratio,
      });
      const anomalies = perFeature
        .filter((f: any) => (f.anomalies && f.anomalies.length > 0) || f.nan_ratio > 0.1 || f.zero_ratio > 0.5)
        .map(mapFeature);
      const allFeatures = perFeature.map(mapFeature);
      res.json({ parityScore, anomalies, allFeatures });
    } catch (error: any) {
      console.error("Feature drift error:", error);
      res.status(500).json({ error: error.message || "Failed to run feature drift analysis" });
    }
  });

  app.get("/api/gap-analysis/prediction-audit", async (req, res) => {
    try {
      const summaryResult = await db.execute(sql.raw(`
        SELECT 
          COUNT(*) as total_predictions,
          COUNT(CASE WHEN result_status = 'resulted' THEN 1 END) as resulted,
          COUNT(CASE WHEN result_status = 'pending' THEN 1 END) as pending,
          COUNT(CASE WHEN won = true THEN 1 END) as winners,
          COUNT(CASE WHEN placed = true THEN 1 END) as placers,
          ROUND(COUNT(CASE WHEN won = true THEN 1 END)::numeric / NULLIF(COUNT(CASE WHEN result_status = 'resulted' THEN 1 END), 0) * 100, 1) as strike_rate,
          ROUND(COALESCE(SUM(CASE WHEN result_status = 'resulted' THEN profit_loss END), 0)::numeric, 2) as total_profit_loss,
          ROUND(AVG(CASE WHEN result_status = 'resulted' THEN market_odds END)::numeric, 2) as avg_odds
        FROM prediction_audit
      `));
      const recentResult = await db.execute(sql.raw(`
        SELECT * FROM prediction_audit ORDER BY created_at DESC LIMIT 50
      `));
      const s: any = summaryResult.rows[0] || {};
      const summary = {
        totalPredictions: Number(s.total_predictions) || 0,
        totalResulted: Number(s.resulted) || 0,
        pendingResults: Number(s.pending) || 0,
        winners: Number(s.winners) || 0,
        placers: Number(s.placers) || 0,
        strikeRate: Number(s.strike_rate) || 0,
        totalPnl: Number(s.total_profit_loss) || 0,
        avgOdds: Number(s.avg_odds) || 0,
      };
      const recent = (recentResult.rows || []).map((r: any) => ({
        date: r.race_date,
        track: r.track,
        race: Number(r.race_number) || 0,
        horse: r.horse_name,
        predicted_pct: r.predicted_win_prob != null ? Number(r.predicted_win_prob) : null,
        market_odds: r.market_odds != null ? Number(r.market_odds) : null,
        result: r.won ? 'won' : r.placed ? 'placed' : r.result_status === 'resulted' ? 'lost' : '',
        pnl: Number(r.profit_loss) || 0,
        result_status: r.result_status || 'pending',
      }));
      res.json({ summary, recent });
    } catch (error: any) {
      console.error("Prediction audit error:", error);
      res.status(500).json({ error: error.message || "Failed to fetch prediction audit" });
    }
  });

  app.get("/api/gap-analysis/race-schedule", async (req, res) => {
    try {
      const statusResult = await db.execute(sql.raw(`
        SELECT result_status, COUNT(*) as count
        FROM race_schedule
        GROUP BY result_status
      `));
      const upcomingResult = await db.execute(sql.raw(`
        SELECT * FROM race_schedule 
        WHERE result_status = 'pending'
        ORDER BY off_time ASC LIMIT 20
      `));
      const statusMap: Record<string, number> = { pending: 0, collected: 0, failed: 0 };
      for (const row of statusResult.rows as any[]) {
        const status = (row.result_status || '').toLowerCase();
        if (status in statusMap) {
          statusMap[status] = Number(row.count) || 0;
        }
      }
      const pending = (upcomingResult.rows || []).map((r: any) => ({
        race_id: r.id,
        track: r.track,
        race_number: Number(r.race_number) || 0,
        date: r.race_date,
        due_time: r.off_time || r.result_due_at || '',
        status: r.result_status || 'pending',
      }));
      res.json({
        summary: statusMap,
        pending,
      });
    } catch (error: any) {
      console.error("Race schedule error:", error);
      res.status(500).json({ error: error.message || "Failed to fetch race schedule" });
    }
  });

  app.post("/api/gap-analysis/collect-results", async (req, res) => {
    try {
      const { exec } = await import("child_process");
      exec(`"${resolvePythonBin()}" server/python/auto_results_collector.py`, { cwd: process.cwd() }, (err, stdout, stderr) => {
        if (stdout) console.log(stdout);
        if (stderr) console.error(stderr);
      });
      res.json({ success: true, message: "Results collection triggered" });
    } catch (error: any) {
      console.error("Collect results error:", error);
      res.status(500).json({ error: error.message || "Failed to trigger results collection" });
    }
  });

  app.post("/api/sectionals/collect-weekly", async (req, res) => {
    try {
      const days = Math.max(1, Math.min(30, parseInt(req.body?.days) || 7));
      const { runPythonScript } = await import('./scheduler');
      runPythonScript('weekly_sectional_collector.py', ['--days', String(days)])
        .then(result => {
          if (result.success) {
            console.log("[API] Weekly sectional collection completed");
          } else {
            console.error("[API] Weekly sectional collection failed:", result.error);
          }
        });
      res.json({ success: true, message: `Weekly sectional collection triggered (last ${days} days)` });
    } catch (error: any) {
      console.error("Weekly sectional collection error:", error);
      res.status(500).json({ error: error.message || "Failed to trigger weekly sectional collection" });
    }
  });

  app.get("/api/gap-analysis/live-performance", async (req, res) => {
    try {
      const result = await db.execute(sql.raw(`
        SELECT 
          race_date,
          COUNT(*) as bets,
          COUNT(CASE WHEN won = true THEN 1 END) as winners,
          ROUND(SUM(profit_loss)::numeric, 2) as pnl
        FROM prediction_audit
        WHERE result_status = 'resulted'
        GROUP BY race_date
        ORDER BY race_date ASC
      `));
      let cumulativePnl = 0;
      let totalBets = 0;
      let totalWinners = 0;
      const daily = (result.rows || []).map((row: any) => {
        const bets = Number(row.bets) || 0;
        const winners = Number(row.winners) || 0;
        const pnl = Number(row.pnl) || 0;
        cumulativePnl += pnl;
        totalBets += bets;
        totalWinners += winners;
        return {
          date: row.race_date,
          bets,
          winners,
          strikeRate: bets > 0 ? Math.round((winners / bets) * 1000) / 10 : 0,
          pnl,
          cumulativePnl: Math.round(cumulativePnl * 100) / 100,
        };
      });
      const rollingStrikeRate = totalBets > 0 ? Math.round((totalWinners / totalBets) * 1000) / 10 : 0;
      res.json({ daily, rollingStrikeRate, totalPnl: Math.round(cumulativePnl * 100) / 100 });
    } catch (error: any) {
      console.error("Live performance error:", error);
      res.status(500).json({ error: error.message || "Failed to fetch live performance data" });
    }
  });

  // ============================================
  // ADVANCED RACE ANALYSIS - Full Field 4-Phase Groq Analysis
  // ============================================
  
  // Run 4-phase "train of thought" analysis on a race via Groq API
  app.post("/api/race-analysis/analyze", async (req, res) => {
    try {
      const { track, raceNumber, raceDate, raceData } = req.body;
      
      if (!track || !raceNumber || !raceDate) {
        return res.status(400).json({ 
          success: false, 
          error: "Missing required fields: track, raceNumber, raceDate" 
        });
      }
      
      // Try to get race data from request body or load from tips file
      let raceInput = raceData;
      if (!raceInput) {
        const tipsPath = path.join(process.cwd(), 'racecards', `tips_${raceDate}.json`);
        if (fs.existsSync(tipsPath)) {
          const tipsData = JSON.parse(fs.readFileSync(tipsPath, 'utf-8'));
          const race = tipsData.races?.find((r: any) => 
            r.track === track && r.race_number === parseInt(raceNumber)
          );
          if (race) {
            raceInput = race;
          }
        }
      }
      
      if (!raceInput) {
        return res.status(404).json({
          success: false,
          error: `Race not found: ${track} R${raceNumber} on ${raceDate}`
        });
      }
      
      // Call Python script for advanced analysis
      const { spawn } = await import('child_process');
      const pythonScript = path.join(__dirname, 'python', 'advanced_race_analysis.py');
      
      const result = await new Promise<any>((resolve, reject) => {
        const python = spawn(resolvePythonBin(), [pythonScript, '--analyze-race'], {
          env: { ...process.env, RACE_DATA: JSON.stringify(raceInput) }
        });
        
        let stdout = '';
        let stderr = '';
        
        python.stdout.on('data', (data) => { stdout += data.toString(); });
        python.stderr.on('data', (data) => { stderr += data.toString(); });
        
        python.on('close', (code) => {
          if (code !== 0) {
            reject(new Error(`Python analysis failed: ${stderr}`));
            return;
          }
          try {
            const output = JSON.parse(stdout);
            resolve(output);
          } catch (e) {
            reject(new Error(`Failed to parse Python output: ${stdout}`));
          }
        });
        
        python.on('error', (err) => reject(err));
      });
      
      // Store in database
      try {
        const { raceAnalyses, horseRaceAnalyses } = await import('@shared/schema');
        
        // Insert race-level analysis
        const [raceAnalysis] = await db.insert(raceAnalyses).values({
          track,
          raceNumber: parseInt(raceNumber),
          raceDate,
          raceName: result.race_name,
          distance: result.distance,
          going: result.going,
          raceClass: result.race_class,
          fieldSize: result.field_size,
          paceScenario: result.pace_scenario,
          paceDescription: result.pace_description,
          speedHorsesJson: result.speed_horses,
          stalkersJson: result.stalkers,
          closersJson: result.closers,
          predictedWinner: result.predicted_winner?.horse_name,
          predictedWinnerNumber: result.predicted_winner?.number,
          predictedWinnerProb: result.predicted_winner?.assessed_win_prob,
          predictedWinnerConfidence: result.predicted_winner?.confidence_pct,
          predictedPlacesJson: result.predicted_places,
          raceSummary: result.race_summary,
          keyDynamics: result.key_dynamics,
          bettingRecommendation: result.betting_recommendation,
          horsesAnalysisJson: result.horses,
          modelUsed: result.model_used || 'groq-llama-3.3-70b',
        }).returning();
        
        // Insert individual horse analyses
        if (result.horses && result.horses.length > 0) {
          const horseValues = result.horses.map((h: any) => ({
            raceAnalysisId: raceAnalysis.id,
            track,
            raceNumber: parseInt(raceNumber),
            raceDate,
            horseName: h.horse_name,
            horseNumber: h.number,
            barrier: h.barrier,
            phase1Profile: h.phase_1_profile?.reasoning,
            phase2Pace: h.phase_2_pace?.reasoning,
            phase3Class: h.phase_3_class?.reasoning,
            phase4Comparative: h.phase_4_comparative?.reasoning,
            trainOfThought: h.train_of_thought,
            category: h.category,
            assessedWinProb: h.assessed_win_prob,
            assessedPlaceProb: h.assessed_place_prob,
            predictedFinishPosition: h.predicted_finish_position,
            confidencePct: h.confidence_pct,
            marketOdds: h.market_odds,
            marketAssessment: h.market_assessment,
            valueEdgePct: h.value_edge_pct,
            canWin: h.can_win,
            placePotential: h.place_potential,
            cannotWinReason: h.cannot_win_reason,
            upsetScenario: h.upset_scenario,
            comparativeAdvantage: h.comparative_advantage,
            keyRivalsJson: h.key_rivals,
            beatsWhomJson: h.beats_whom,
            losesToWhomJson: h.loses_to_whom,
          }));
          
          await db.insert(horseRaceAnalyses).values(horseValues);
        }
        
        result.db_id = raceAnalysis.id;
      } catch (dbError) {
        console.log('[Race Analysis] DB storage failed (non-critical):', dbError);
      }
      
      res.json({
        success: true,
        analysis: result
      });
      
    } catch (error: any) {
      console.error("Advanced race analysis error:", error);
      res.status(500).json({ 
        success: false, 
        error: error.message || "Failed to run advanced race analysis" 
      });
    }
  });
  
  // Get stored race analysis
  app.get("/api/race-analysis/:track/:raceNumber/:raceDate", async (req, res) => {
    try {
      const { track, raceNumber, raceDate } = req.params;
      
      const { raceAnalyses, horseRaceAnalyses } = await import('@shared/schema');
      const { eq, and } = await import('drizzle-orm');
      
      // Get race-level analysis
      const [raceAnalysis] = await db
        .select()
        .from(raceAnalyses)
        .where(and(
          eq(raceAnalyses.track, track),
          eq(raceAnalyses.raceNumber, parseInt(raceNumber)),
          eq(raceAnalyses.raceDate, raceDate)
        ))
        .orderBy(desc(raceAnalyses.analysisTimestamp))
        .limit(1);
      
      if (!raceAnalysis) {
        return res.status(404).json({
          success: false,
          error: "Analysis not found. Run POST /api/race-analysis/analyze first."
        });
      }
      
      // Get individual horse analyses
      const horses = await db
        .select()
        .from(horseRaceAnalyses)
        .where(eq(horseRaceAnalyses.raceAnalysisId, raceAnalysis.id))
        .orderBy(horseRaceAnalyses.predictedFinishPosition);
      
      res.json({
        success: true,
        analysis: {
          ...raceAnalysis,
          horses
        }
      });
      
    } catch (error: any) {
      console.error("Get race analysis error:", error);
      res.status(500).json({ 
        success: false, 
        error: error.message || "Failed to fetch race analysis" 
      });
    }
  });
  
  // Get all analyses for a date
  app.get("/api/race-analysis/:raceDate", async (req, res) => {
    try {
      const { raceDate } = req.params;
      
      const { raceAnalyses } = await import('@shared/schema');
      const { eq } = await import('drizzle-orm');
      
      const analyses = await db
        .select()
        .from(raceAnalyses)
        .where(eq(raceAnalyses.raceDate, raceDate))
        .orderBy(raceAnalyses.track, raceAnalyses.raceNumber);
      
      res.json({
        success: true,
        date: raceDate,
        total: analyses.length,
        analyses
      });
      
    } catch (error: any) {
      console.error("Get race analyses error:", error);
      res.status(500).json({ 
        success: false, 
        error: error.message || "Failed to fetch race analyses" 
      });
    }
  });

  return httpServer;
}
