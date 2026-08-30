import type { ChatModeState, ChatTrace, ChatTraceSection } from "@shared/schema";

import { extractStrideEntities, type StrideRetrievalRequest } from "./strideChatRetrieval";

type ConceptualTopic =
  | "best_bet"
  | "edge"
  | "track_condition"
  | "track_profile"
  | "model_quality"
  | "pace_map"
  | "performance"
  | "sectionals"
  | "general_strategy"
  | "class_guide"
  | "exotic_strategy"
  | "jockey_booking_theory"
  | "weight_class"
  | "distance_theory"
  | "going_preferences"
  | "age_season_theory"
  | "value_betting_theory";

interface ConceptualStrideQuestion {
  topic: ConceptualTopic;
  track?: string;
}

function normalize(value: string): string {
  return value.toLowerCase().replace(/\s+/g, " ").trim();
}

function titleCase(value?: string): string | undefined {
  if (!value) return value;
  return value
    .split(/\s+/)
    .filter(Boolean)
    .map((part) => part.charAt(0).toUpperCase() + part.slice(1).toLowerCase())
    .join(" ");
}

function getTrackProfileResponse(track?: string): string {
  const normalizedTrack = normalize(track ?? "");

  if (normalizedTrack.includes("flemington")) {
    return "At Flemington, I would weight tempo honesty, where the horse lands in running, and whether it can build momentum without being cluttered up. The track rewards runners that travel smoothly and sustain their effort, so map, sectional strength, and lane access usually matter more than surface-level form comments.";
  }

  if (normalizedTrack.includes("randwick")) {
    return "At Randwick, I care most about whether the horse can absorb pressure and still finish off. The long straight gives strong closers their chance, but only if they are not giving away too much start, so map, balance through the bend, and late-race stamina matter a lot.";
  }

  if (normalizedTrack.includes("rosehill")) {
    return "At Rosehill, tactical position matters more than people think. It is a tighter, more pressure-sensitive track than Flemington, so barrier, early intent, and whether a runner can hold a spot without burning fuel are often the difference between a good bet and a bad one.";
  }

  if (normalizedTrack.includes("warwick farm")) {
    return "At Warwick Farm, I would focus on tactical speed, barrier usage, and whether the horse can hold rhythm around the turn. It is a track where race shape can punish runners that settle too far back or get cluttered up before they can build momentum.";
  }

  if (normalizedTrack.includes("ascot")) {
    return "At Ascot, I want to know whether the horse's run style actually fits the pattern of the day. Position in running, lane usage, and how much pressure the leaders absorb are critical there, so I would not trust a raw rating unless the map and track pattern line up with it.";
  }

  if (normalizedTrack.includes("caulfield")) {
    return "At Caulfield, barrier and early position are major pieces of the puzzle because the track can get tactical quickly. I care about whether the runner can land where it wants without spending too much energy, then still have enough left when the pressure goes on approaching the bend.";
  }

  if (normalizedTrack.includes("moonee valley")) {
    return "At Moonee Valley, I put a big premium on tactical speed, barrier, and the ability to hold a spot around the bends. It is one of the clearest examples of a track where map can outweigh a small edge in raw talent.";
  }

  if (normalizedTrack.includes("doomben")) {
    return "At Doomben, race shape and lane position matter a lot because runners can get exposed if they are wide without cover. I would focus on map, tactical speed, and whether the horse can travel kindly before asking for a final effort.";
  }

  if (normalizedTrack.includes("eagle farm")) {
    return "At Eagle Farm, I care about whether the runner can sustain pressure over a longer straight and bigger track. It generally gives horses more time to build into their run than Doomben, so genuine speed, balance, and strength late matter more than cheap tactical advantages.";
  }

  if (normalizedTrack.includes("hawkesbury")) {
    return "At Hawkesbury, I would focus on whether the horse can settle in the right lane and keep building rather than get stop-start through the race. The better betting angles usually come from run-style fit, barrier usage, and whether the market has overstated a horse whose setup is not as clean as it looks on paper.";
  }

  return "At any track, I care most about race shape, barrier pressure, where the horse lands in running, and whether the surface and layout let it actually run to its rating. The best bets usually come from horses whose setup fits the venue better than the market has priced.";
}

export function identifyConceptualStrideQuestion(
  message: string,
  context?: StrideRetrievalRequest["context"],
): ConceptualStrideQuestion | null {
  const lowerMessage = normalize(message);
  const entities = extractStrideEntities(message, context);
  const hasHorse = Boolean(entities.horseName);
  const hasRace = Boolean(entities.raceNumber || entities.raceName);
  const hasDatedRace = Boolean(entities.date && (entities.track || entities.raceNumber || entities.horseName));
  const hasSpecificAnchor = hasHorse || hasRace || hasDatedRace;
  const broadTrackOnly = Boolean(entities.track) && !hasHorse && !hasRace && !hasDatedRace;
  const mentionsTrackCondition =
    /\btrack condition\b/.test(lowerMessage) ||
    /\bgoing\b/.test(lowerMessage) ||
    /\brain-affected\b/.test(lowerMessage) ||
    ((/\bsoft(?:\s*[1-9])?\b/.test(lowerMessage) ||
      /\bheavy(?:\s*[1-9])?\b/.test(lowerMessage) ||
      /\bfirm\b/.test(lowerMessage) ||
      /\bgood(?:\s*[1-9])?\b/.test(lowerMessage)) &&
      /\b(track|ground|surface|deck|going|condition)\b/.test(lowerMessage));

  if (!hasSpecificAnchor && /(best bet|best chance|what should i back|who do you like|who wins|what is the bet)/.test(lowerMessage)) {
    return { topic: "best_bet", track: entities.track };
  }

  if (/(edge|expected value|fair odds|overlay|underlay|implied probability|price discipline|ev\b)/.test(lowerMessage)) {
    return { topic: "edge", track: entities.track };
  }

  if (!hasSpecificAnchor && mentionsTrackCondition) {
    return { topic: "track_condition", track: entities.track };
  }

  // Model quality: only conceptual for abstract "explain how the model works" questions
  // Skip when user is asking about actual model output data
  const isDataSeekingModel = /(how('s| is| are)?\s+(the model|stride|it)\s+(going|doing|performing)|show me|what('s| is| are) (my|the|our)|accuracy|win rate|strike rate.*\d|results)/i.test(lowerMessage);
  if (!isDataSeekingModel && /(algorithm|model|selection score|calibration|how does .*work|explain .*model|monte carlo|simulation)/.test(lowerMessage)) {
    return { topic: "model_quality", track: entities.track };
  }

  if ((broadTrackOnly && /(what factors|what matters|how should i bet|how do i bet|bias|barrier|tempo|track)/.test(lowerMessage)) || /what factors matter most at/.test(lowerMessage)) {
    return { topic: "track_profile", track: entities.track };
  }

  if (!hasSpecificAnchor && /(pace map|tempo|race shape|speed map|barrier map|barrier pressure)/.test(lowerMessage)) {
    return { topic: "pace_map", track: entities.track };
  }

  if (!hasSpecificAnchor && /(sectional|last 600|late speed|finishing burst)/.test(lowerMessage)) {
    return { topic: "sectionals", track: entities.track };
  }

  // Performance: only treat as conceptual if truly abstract ("explain what strike rate means")
  // Skip conceptual when user is asking for actual data ("how are we going", "show me results", "what's my ROI")
  const isDataSeekingPerformance = /(how('s| is| are)?\s+(stride|we|i|the model)\s+(going|doing|performing)|show me|what('s| is| are) my|results so far|profit|loss|p.?l\b|how much|total bets|recent results|last \d+ days)/i.test(lowerMessage);
  if (!hasSpecificAnchor && !isDataSeekingPerformance && /(strike rate|roi|backtest|performance|calibration curve|model quality)/.test(lowerMessage)) {
    return { topic: "performance", track: entities.track };
  }

  if (/(class level|benchmark race|bm\s*\d+|group 1|group 2|group 3|listed race|maiden race|open handicap|restricted handicap|what class|what grade|grade of race|class of race|class hierarchy|class drop|class rise|rising in class|dropping in class)/.test(lowerMessage)) {
    return { topic: "class_guide", track: entities.track };
  }

  if (/(quaddie|first four|trifecta|exacta|how to bet exotic|exotic betting|boxed trifecta|banker strategy|leg selection|exotic strategy|how do quaddies work|what is a quaddie|how does a trifecta work)/.test(lowerMessage)) {
    return { topic: "exotic_strategy", track: entities.track };
  }

  if (/(jockey change|jockey booking|jockey switch|what does.*jockey|apprentice claim|claiming apprentice|jockey signal|jockey significance|how to read jockey|why.*jockey|jockey dropped|new jockey)/.test(lowerMessage)) {
    return { topic: "jockey_booking_theory", track: entities.track };
  }

  if (/(weight for age|what does weight mean|how does weight work|handicap weight|top weight|set weight|penalty|mare allowance|gelding|weight carry|impost|how much does weight)/.test(lowerMessage)) {
    return { topic: "weight_class", track: entities.track };
  }

  if (/(first time at distance|does distance matter|staying horse|sprinting horse|miler|staying program|how far|trip up|step up in distance|drop back in distance|sprinter staying|marathon|staying race|distance specialist)/.test(lowerMessage)) {
    return { topic: "distance_theory", track: entities.track };
  }

  if (/(wet track horse|how to read going|good vs soft|heavy track specialist|all weather track|dry track|soft ground|heavy ground|firmish|track condition|going preference|handles wet|good in rain|rain affected|slow track)/.test(lowerMessage)) {
    return { topic: "going_preferences", track: entities.track };
  }

  if (/(2yo racing|3yo season|maiden season|spring carnival|autumn carnival|classic horse|golden rose|cox plate|derby|oaks|racing calendar|racing season|when does racing season|southern hemisphere|northern hemisphere|young horse|how old|peak age|racing age)/.test(lowerMessage)) {
    return { topic: "age_season_theory", track: entities.track };
  }

  if (/(how to find value|what is value|overlay|underbet|overbet|what makes a value bet|why back at those odds|betting value|market efficiency|find overlays|market weakness|how to profit|long term profit|positive expected value|beating the market)/.test(lowerMessage)) {
    return { topic: "value_betting_theory", track: entities.track };
  }

  if (!hasSpecificAnchor && /(explain|how does|what matters|why does|what is|how should)/.test(lowerMessage)) {
    return { topic: "general_strategy", track: entities.track };
  }

  return null;
}

export function buildConceptualStrideResponse(match: ConceptualStrideQuestion, message: string): string {
  const lowerMessage = normalize(message);

  switch (match.topic) {
    case "best_bet":
      return "I cannot name a genuine best bet from Deep Thought alone without a race, field, or market context, and I do not want to fake one. The right profile is the runner with a clean map edge, a positive gap between rated chance and market chance, and a price that still sits above your fair odds after allowing for uncertainty.";
    case "edge":
      if (/(algorithm|model|edge calculation|selection score)/.test(lowerMessage)) {
        return "Your edge is the gap between your assessed chance and the market's implied chance after normalising the market properly. In practice, Stride should turn probability into fair odds, compare that to the live quote, and only count a runner as a real edge when the rated chance is materially stronger than the market once you allow for uncertainty and overround.";
      }
      return "Your edge is the gap between your assessed chance and the market's implied chance. In practice, you turn your rated win probability into fair odds, compare that to the live quote, and only get involved when the market is still longer than your price by a margin big enough to survive noise, bad luck, and model error.";
    case "track_condition":
      if (/(model|algorithm|selection score|pricing|edge)/.test(lowerMessage)) {
        return "Track condition changes both the expected race shape and how much confidence you should place in your price. In your model, it should shift the weighting on map, action, stamina, and pressure tolerance, while also increasing uncertainty when the meeting is likely to play differently from the base form. The practical effect is that soft or heavy ground should not just move a horse up or down a line or two; it should also change how aggressively you trust the edge if the surface introduces more variance.";
      }
      return "Track condition matters because it changes how energy gets spent through the race. On soft or heavy ground I care more about action, resilience under pressure, and whether the horse can hold position without burning fuel early; on firmer decks, tactical speed and clean momentum usually matter more.";
    case "track_profile":
      return getTrackProfileResponse(match.track);
    case "model_quality":
      if (/(edge calculation|selection score)/.test(lowerMessage)) {
        return "Stride should be treated like a pricing engine rather than an oracle. The core idea is that it turns form, map, pace, stability, and market context into a rated probability, then the wagering decision comes from whether that rated chance is better than the price on offer. The sharp part is not just picking winners; it is finding runners the market has mispriced relative to their actual setup.";
      }
      return "Stride should be treated like a pricing engine rather than an oracle. It combines form, race shape, fitness, and market context into a rated probability, then the betting decision comes from whether that rated chance is materially better than the market's implied chance after allowing for uncertainty.";
    case "pace_map":
      return "Pace map matters because it tells you which runners get the race run on their terms and which ones are likely to be pressured, trapped, or forced to concede ground. The best betting edges often come from identifying when the market has rated a horse on raw ability but underweighted how the race shape helps or hurts that ability.";
    case "performance":
      return "For performance, I would care less about raw strike rate on its own and more about whether the model is calibrated across price bands. A useful review looks at strike, ROI, expected value capture, calibration by odds range, and whether the model is consistently strongest in the sections of the market where you actually want to bet.";
    case "sectionals":
      return "Sectionals matter because they tell you how the horse actually spent energy, not just where it finished. A strong late split is useful only if the horse can reproduce it in a race shape that gives it the same opportunity, so I use sectionals to understand fit and upside rather than treating them as an automatic betting signal.";
    case "class_guide":
      return "Australian race classes run from Maiden (never won) up through benchmark grades (BM58, BM64, BM70, BM78, BM84, BM90, BM96) and into Listed, Group 3, Group 2, and Group 1. Benchmark numbers represent the average rating of the field — a BM78 race has a field average around 78 in the ratings system. Weight-for-age (WFA) races, like the Cox Plate or WS Cox Plate, are set on a scale rather than the handicapper's pen. Horses with recent wins carry penalties in some grades. A class rise is when a horse steps up in grade, which is typically harder — a class drop is when it comes down, which usually means it faces a weaker field than it has beaten before. The biggest betting angle in class is identifying horses dropping in grade that are still improving and are mispriced as though the class drop signals a problem.";
    case "exotic_strategy":
      return "A quaddie requires you to pick the winner of four nominated legs — usually the last four races on the card. A trifecta requires the first three in correct order. A first four is first four in order. A boxed trifecta means you select three horses and back all six possible finishing orders, but it costs six times the unit stake. For quaddies, the sharp approach is to identify one or two bankers (races where you have a very strong opinion), and spread in the other legs. The single line (banker in every leg) is the lowest-cost play; a roving banker or leg spread adds coverage but multiplies cost quickly. Exotics offer pool-determined dividends — when you beat the favourite in a key leg, the pool size and holdout dramatically affect your return. Exotic betting rewards contrarian selections more than win betting does.";
    case "jockey_booking_theory":
      return "Jockey bookings carry information. When a top-tier jockey picks up a ride on a lesser-fancied horse, particularly if they have dropped a better-fancied ride to do it, that is worth noting. The reverse — a leading jockey being replaced — can signal a stable concern, a rule-4 deduction query, or a fitness doubt. Claiming apprentices (those with less than a set number of winners) can carry weight allowances of 1.5–3kg, which in close races can be significant. The critical question is whether the claim negates any inexperience disadvantage at the specific track or in the class of race. Trainer-jockey combinations that consistently show higher win rates than their individual rates separately are a structural edge most markets underweight.";
    case "weight_class":
      return "In Australian handicap racing, the handicapper assigns weights to normalise horses' chances — heavier weight means the horse is rated higher and needs to give weight to rivals. In set-weight races, all horses carry the same weight (with standard sex and age allowances). Weight-for-age (WFA) races use a scale that adjusts for age and distance, giving younger horses and mares a weight relief. Penalty weight is assigned when a horse wins after ratings are set — typically 3–5kg for a metropolitan win, less for provincial. The practical guide: each kilogram of extra weight is roughly equivalent to about 0.5 lengths over 1200m and about 1 length over 2400m. Top weight in a big field handicap is not inherently bad — good horses carry weight — but watch for a horse that wins its maiden and then jumps sharply in class with a heavy penalty before the handicapper properly resets its mark.";
    case "distance_theory":
      return "Australian racing runs from 900m sprints up to staying races at 3200m plus. Sprinters (up to 1200m) need speed and gate speed. Milers (1200–1600m) benefit from tactical speed and a reliable turn of foot. Middle-distance horses (1600–2000m) are where most of the top-class horses live — it rewards a balance of speed and stamina. Stayers (2000m+) need genuine stamina and efficient energy use. First-time stayers — horses trying a mile or more for the first time — are often underrated if their breeding suggests stamina, but overrated if they have only won sprinting. Step-ups in distance are easier to judge from sectional data than raw form: a horse consistently running strong last sections in shorter races has scope to stay.";
    case "going_preferences":
      return "The Australian going scale runs from Firm 1 (very fast) through Good 3/4 (ideal conditions) through Soft 5/6/7 to Heavy 8/9/10. Horses that handle wet ground tend to show high action (knees and hocks), stay relaxed under pressure, and have won on wet tracks previously. Wet-ground form from Queensland (Eagle Farm often gets wet) can be misleading at a southern venue because the surface behaves differently. Some horses can win on any ground (true class); others have a firm preference. Track upgrading can happen on race day — always check the morning going report. A track forecast to improve from Soft 7 to Soft 5 by late in the card is very different to one that holds at Heavy 10. The best wet-track punt is a horse that shows strong late sections on Heavy ground because those sections typically punish poor ground handlers most severely.";
    case "age_season_theory":
      return "The Australian racing season is built around two carnivals: the Spring Carnival (roughly September–November, Victoria and Queensland) and the Autumn Carnival (March–May, Sydney and Melbourne). Two-year-olds (2YOs) race from January through to the spring, peaking in races like the Golden Rose (1400m, Rosehill) and the Golden Slipper (1200m, Rosehill — world's richest race for 2YOs). Three-year-olds target the classics — Caulfield Guineas, Cox Plate, VRC Derby, Australian Derby — in the September–April window. Older horses (4+) are the core of handicap racing year-round. The peak performance age for a thoroughbred is typically 4–6 years, but there is wide individual variation. A 3YO weight-for-age allowance against older horses is significant in September/October and shrinks through the summer as they mature.";
    case "value_betting_theory":
      return "Value betting means backing a horse at odds higher than its true chance of winning. If a horse has a genuine 25% chance of winning (4 to 1 fair odds) but the market offers $5.50 (roughly 18% implied), that gap is your edge. Over time, consistently backing horses at positive expected value is how professional punters make money — not by picking the most winners, but by finding mispriced chances. Markets are generally efficient but they do have systematic biases: longshots are overbet (market overestimates them), short-priced favourites are often underbet (market underestimates them), and horses with a change in key conditions (jockey, barrier, distance step) are frequently mispriced before the market catches up. STRIDE specifically focuses on edge — the gap between model probability and market probability — as the primary signal for whether a bet has positive expected value.";
    case "general_strategy":
    default:
      return "The safest general betting approach is to anchor on three things: where the horse maps, whether your rated chance is better than the market's implied chance, and whether the race shape actually lets the horse run to that rating. Good betting is not about finding the prettiest form line; it is about finding the runners whose setup is stronger than the price suggests.";
  }
}

function buildConceptualThinkingSummary(match: ConceptualStrideQuestion): string {
  switch (match.topic) {
    case "best_bet":
      return "The question is broad and asks for a betting call without a loaded race, so the safe answer is to explain the right profile rather than invent a pick.";
    case "edge":
      return "This is a pricing and value question, so it should be answered from wagering logic rather than from random local race rows.";
    case "track_condition":
      return "This is a general racing-principles question about footing and energy distribution, not a request for a specific race lookup.";
    case "track_profile":
      return "This is a venue-specific strategy question, so the answer should focus on how that track shapes race setup and betting decisions.";
    case "model_quality":
      return "This is a methodology question about how Stride thinks, scores, and prices runners.";
    case "pace_map":
      return "This is a race-shape question, so the answer should explain how tempo and position create or remove betting edge.";
    case "performance":
      return "This is a model review question, so the answer should focus on calibration and quality of edge, not on one meeting's local evidence.";
    case "sectionals":
      return "This is a form-interpretation question about how to use sectionals properly.";
    case "class_guide":
      return "This is a racing-education question about Australian class structure and what class changes mean for betting.";
    case "exotic_strategy":
      return "This is an exotic betting strategy question — quaddies, trifectas, and first fours explained for Australian racing.";
    case "jockey_booking_theory":
      return "This is a jockey-signal question — how to read jockey bookings, changes, and apprentice claims as market intelligence.";
    case "weight_class":
      return "This is a weight and handicapping question — how penalties, WFA scales, and allowances affect AUS racing.";
    case "distance_theory":
      return "This is a distance-profiling question — how sprint, middle-distance, and staying horses are assessed in Australian racing.";
    case "going_preferences":
      return "This is a track condition question — how to identify wet/dry track preferences and read the AUS going scale.";
    case "age_season_theory":
      return "This is a racing calendar and age-cohort question — AUS season structure, 2YO/3YO classics, and peak ages.";
    case "value_betting_theory":
      return "This is a value betting question — how to find overlays, assess fair odds, and think about positive expected value.";
    default:
      return "This is a broad strategy question that can be answered directly from Stride's betting principles.";
  }
}

function buildConceptualSourceSummary(match: ConceptualStrideQuestion): string {
  switch (match.topic) {
    case "model_quality":
      return "Used Stride's model and pricing logic.";
    case "track_profile":
      return match.track ? `Used Stride's track-shape lens for ${titleCase(match.track)}.` : "Used Stride's track-shape principles.";
    default:
      return "Used Stride's general wagering and race-shape logic.";
  }
}

export function buildConceptualStrideReasoning(match: ConceptualStrideQuestion): string {
  return [
    `Intent: conceptual (${match.topic}).`,
    "This question does not need live web search or forced local retrieval.",
    buildConceptualThinkingSummary(match),
    buildConceptualSourceSummary(match),
  ].join("\n");
}

export function buildConceptualStrideTrace(
  match: ConceptualStrideQuestion,
  mode: ChatModeState,
  assumptions: string[] = [],
): ChatTrace {
  const sections: ChatTraceSection[] = [
    {
      id: "thinking",
      kind: "thinking",
      title: "What it focused on",
      summary: buildConceptualThinkingSummary(match),
      bullets: [
        ...assumptions,
        match.track ? `Track in scope: ${titleCase(match.track)}.` : "No single horse, race, or date anchor was required.",
      ].slice(0, 4),
    },
    {
      id: "steps",
      kind: "steps",
      title: "What it did",
      summary: "Deep Thought answered directly from the type of question being asked.",
      bullets: [
        "Classified the question as conceptual rather than race-specific.",
        "Skipped forced local evidence retrieval so the answer stayed relevant.",
        "Answered from Stride's racing and betting logic instead of inventing a fake race anchor.",
      ],
    },
    {
      id: "sources",
      kind: "sources",
      title: "Sources and tools",
      summary: buildConceptualSourceSummary(match),
      bullets:
        match.topic === "model_quality"
          ? ["Stride pricing logic", "selection and edge principles"]
          : match.topic === "track_profile"
            ? ["Stride track-shape principles", "Australian racing setup logic"]
            : ["Stride wagering principles", "race-shape and form logic"],
    },
    {
      id: "why",
      kind: "why",
      title: "Why it reached the answer",
      summary: "A clean conceptual answer is stronger here than dumping unrelated local race rows.",
      bullets: [
        "The question can be answered honestly without pretending a specific race was loaded.",
        "Broad strategy questions should stay broad unless the user asks for a track, horse, or race-specific view.",
      ],
    },
  ];

  return {
    label: mode.brain ? "Deep Thought Summary" : "Answer flow",
    headline: "Deep Thought recognized this as a conceptual question and answered it directly.",
    summary: "No live web search or forced local race lookup was needed for this question.",
    sections,
    searchQueries: [],
    searchResults: [],
    finalWhy: "This was a broad strategy or methodology question, so a direct conceptual answer was the most useful path.",
  };
}
