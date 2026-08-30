import type { ChatModeState } from "@shared/schema";

export type ChatModeKey = "default" | "brain" | "search" | "brain-search";

export interface ChatModeTheme {
  key: ChatModeKey;
  label: string;
  accentTextClass: string;
  accentBorderClass: string;
  accentSoftBgClass: string;
  accentStrongBgClass: string;
  glowClass: string;
  assistantBubbleClass: string;
  userBubbleClass: string;
  tracePanelClass: string;
  traceCardClass: string;
  resultSelectedClass: string;
  resultCardClass: string;
  inputClass: string;
  linkClass: string;
  badgeClass: string;
  buttonClass: string;
  loadingBarClass: string;
}

const MODE_THEMES: Record<ChatModeKey, ChatModeTheme> = {
  default: {
    key: "default",
    label: "Stride",
    accentTextClass: "text-racing-orange",
    accentBorderClass: "border-racing-orange/30",
    accentSoftBgClass: "bg-racing-orange/10",
    accentStrongBgClass: "bg-racing-orange/[0.14]",
    glowClass: "bg-[radial-gradient(circle_at_top,rgba(249,115,22,0.09),transparent_26%)]",
    assistantBubbleClass: "border-racing-orange/[0.18] bg-black/40",
    userBubbleClass: "border-racing-orange/25 bg-racing-orange/[0.12]",
    tracePanelClass: "border-racing-orange/25 bg-[linear-gradient(180deg,rgba(249,115,22,0.12),rgba(0,0,0,0.24))]",
    traceCardClass: "border-racing-orange/15 bg-black/35",
    resultSelectedClass: "border-racing-orange/35 bg-racing-orange/[0.08]",
    resultCardClass: "border-white/[0.07] bg-white/[0.03]",
    inputClass: "border-racing-orange/30 bg-black/70",
    linkClass: "text-racing-orange underline decoration-racing-orange/70 underline-offset-2 hover:text-white",
    badgeClass: "border-racing-orange/30 bg-racing-orange/[0.10] text-racing-orange",
    buttonClass: "border-racing-orange/30 bg-racing-orange/[0.06] text-white/85 hover:border-racing-orange/55 hover:bg-racing-orange/[0.12]",
    loadingBarClass: "bg-racing-orange",
  },
  brain: {
    key: "brain",
    label: "Deep Thought",
    accentTextClass: "text-[#8B5CF6]",
    accentBorderClass: "border-[#8B5CF6]/35",
    accentSoftBgClass: "bg-[#8B5CF6]/12",
    accentStrongBgClass: "bg-[#8B5CF6]/16",
    glowClass: "bg-[radial-gradient(circle_at_top,rgba(139,92,246,0.14),transparent_28%)]",
    assistantBubbleClass: "border-[#8B5CF6]/25 bg-[linear-gradient(180deg,rgba(139,92,246,0.10),rgba(0,0,0,0.28))]",
    userBubbleClass: "border-[#8B5CF6]/35 bg-[#8B5CF6]/14",
    tracePanelClass: "border-[#8B5CF6]/28 bg-[linear-gradient(180deg,rgba(139,92,246,0.18),rgba(17,10,26,0.84))]",
    traceCardClass: "border-[#8B5CF6]/18 bg-[rgba(22,14,34,0.72)]",
    resultSelectedClass: "border-[#8B5CF6]/35 bg-[#8B5CF6]/10",
    resultCardClass: "border-white/[0.07] bg-white/[0.03]",
    inputClass: "border-[#8B5CF6]/35 bg-[rgba(12,10,19,0.86)]",
    linkClass: "text-[#c3b0ff] underline decoration-[#8B5CF6]/70 underline-offset-2 hover:text-white",
    badgeClass: "border-[#8B5CF6]/35 bg-[#8B5CF6]/14 text-[#c3b0ff]",
    buttonClass: "border-[#8B5CF6]/35 bg-[#8B5CF6]/10 text-white/90 hover:border-[#8B5CF6]/60 hover:bg-[#8B5CF6]/16",
    loadingBarClass: "bg-[#8B5CF6]",
  },
  search: {
    key: "search",
    label: "Search",
    accentTextClass: "text-[#1EAEDB]",
    accentBorderClass: "border-[#1EAEDB]/35",
    accentSoftBgClass: "bg-[#1EAEDB]/12",
    accentStrongBgClass: "bg-[#1EAEDB]/16",
    glowClass: "bg-[radial-gradient(circle_at_top,rgba(30,174,219,0.16),transparent_28%)]",
    assistantBubbleClass: "border-[#1EAEDB]/26 bg-[linear-gradient(180deg,rgba(30,174,219,0.10),rgba(0,0,0,0.28))]",
    userBubbleClass: "border-[#1EAEDB]/35 bg-[#1EAEDB]/14",
    tracePanelClass: "border-[#1EAEDB]/28 bg-[linear-gradient(180deg,rgba(30,174,219,0.18),rgba(7,18,24,0.84))]",
    traceCardClass: "border-[#1EAEDB]/18 bg-[rgba(8,20,26,0.72)]",
    resultSelectedClass: "border-[#1EAEDB]/35 bg-[#1EAEDB]/10",
    resultCardClass: "border-white/[0.07] bg-white/[0.03]",
    inputClass: "border-[#1EAEDB]/35 bg-[rgba(8,16,21,0.86)]",
    linkClass: "text-[#77dfff] underline decoration-[#1EAEDB]/70 underline-offset-2 hover:text-white",
    badgeClass: "border-[#1EAEDB]/35 bg-[#1EAEDB]/14 text-[#77dfff]",
    buttonClass: "border-[#1EAEDB]/35 bg-[#1EAEDB]/10 text-white/90 hover:border-[#1EAEDB]/60 hover:bg-[#1EAEDB]/16",
    loadingBarClass: "bg-[#1EAEDB]",
  },
  "brain-search": {
    key: "brain-search",
    label: "Deep Thought + Search",
    accentTextClass: "text-white",
    accentBorderClass: "border-white/15",
    accentSoftBgClass: "bg-white/[0.06]",
    accentStrongBgClass: "bg-white/[0.08]",
    glowClass:
      "bg-[radial-gradient(circle_at_top_left,rgba(139,92,246,0.16),transparent_26%),radial-gradient(circle_at_top_right,rgba(30,174,219,0.16),transparent_26%)]",
    assistantBubbleClass:
      "border-white/12 bg-[linear-gradient(135deg,rgba(139,92,246,0.14),rgba(30,174,219,0.12)_52%,rgba(0,0,0,0.36))]",
    userBubbleClass:
      "border-white/15 bg-[linear-gradient(135deg,rgba(139,92,246,0.18),rgba(30,174,219,0.16))]",
    tracePanelClass:
      "border-white/15 bg-[linear-gradient(135deg,rgba(139,92,246,0.24),rgba(30,174,219,0.18)_54%,rgba(0,0,0,0.32))]",
    traceCardClass: "border-white/12 bg-black/30",
    resultSelectedClass:
      "border-white/18 bg-[linear-gradient(135deg,rgba(139,92,246,0.12),rgba(30,174,219,0.12))]",
    resultCardClass: "border-white/[0.07] bg-white/[0.03]",
    inputClass:
      "border-white/15 bg-[linear-gradient(135deg,rgba(139,92,246,0.12),rgba(30,174,219,0.12)_60%,rgba(0,0,0,0.58))]",
    linkClass: "text-[#d9d4ff] underline decoration-white/60 underline-offset-2 hover:text-white",
    badgeClass:
      "border-white/15 bg-[linear-gradient(135deg,rgba(139,92,246,0.18),rgba(30,174,219,0.18))] text-white",
    buttonClass:
      "border-white/15 bg-[linear-gradient(135deg,rgba(139,92,246,0.12),rgba(30,174,219,0.12))] text-white hover:bg-[linear-gradient(135deg,rgba(139,92,246,0.18),rgba(30,174,219,0.18))]",
    loadingBarClass: "bg-[linear-gradient(90deg,#8B5CF6,#1EAEDB)]",
  },
};

export function getChatModeKey(mode?: Partial<ChatModeState>): ChatModeKey {
  if (mode?.brain && mode?.search) return "brain-search";
  if (mode?.search) return "search";
  if (mode?.brain) return "brain";
  return "default";
}

export function getChatModeTheme(mode?: Partial<ChatModeState>): ChatModeTheme {
  return MODE_THEMES[getChatModeKey(mode)];
}

export function getModeLoadingSteps(mode?: Partial<ChatModeState>): string[] {
  const key = getChatModeKey(mode);
  switch (key) {
    case "brain":
      return [
        "Breaking down the request",
        "Testing the strongest reasoning path",
        "Pressure-checking the answer",
        "Writing the final response",
      ];
    case "search":
      return [
        "Planning the live search",
        "Searching the web with STRIDE",
        "Selecting the strongest results",
        "Building the cited answer",
      ];
    case "brain-search":
      return [
        "Planning the reasoning and search pass",
        "Searching the web with STRIDE",
        "Combining local and web evidence",
        "Writing the cited answer",
      ];
    default:
      return [
        "Checking the saved Stride context",
        "Reviewing the strongest local evidence",
        "Writing the answer",
      ];
  }
}

function normalizePromptText(question?: string | null) {
  return question?.toLowerCase().replace(/\s+/g, " ").trim() ?? "";
}

export function getModeLoadingPanelLabel(mode?: Partial<ChatModeState>): string {
  const key = getChatModeKey(mode);
  switch (key) {
    case "brain":
      return "Live Thinking";
    case "search":
    case "brain-search":
      return "Live Trace";
    default:
      return "Answer Flow";
  }
}

export function getModeExperienceCopy(mode?: Partial<ChatModeState>): string {
  const key = getChatModeKey(mode);
  switch (key) {
    case "brain":
      return "Deep Thought will think visibly through strategy, analysis, comparisons, and multi-step reasoning before answering.";
    case "search":
      return "Search will retrieve live web sources, select the strongest evidence, and return a cited answer.";
    case "brain-search":
      return "Deep Thought + Search will reason visibly, search live, and return a cited answer.";
    default:
      return "Ask about form, strategy, track conditions, or pick a race from the sidebar.";
  }
}

export function getDeepThoughtLoadingStream(question?: string | null): string[] {
  const text = normalizePromptText(question);
  const stream = [
    "Reading the exact ask — identifying what's really being asked versus what's literally written",
    "Mapping the key assumptions that will most affect whether the answer is actually useful",
  ];

  if (/(compare|comparison|versus|\bvs\b|better than|or should i)/.test(text)) {
    stream.push("Setting up a direct comparison — identifying what criteria actually matter for this decision, not just listing surface-level differences");
  }

  if (/(plan|strategy|approach|roadmap|next step|should i|how should)/.test(text)) {
    stream.push("Working through the trade-offs — what's the upside, what are the realistic failure points, and what would need to be true for this to go wrong");
  }

  if (/(why|how does|because|reason|affect)/.test(text)) {
    stream.push("Tracing the cause-and-effect chain — not just what happened but why, and whether that causal link is actually reliable");
  }

  if (/(best bet|best chance|who wins|what should i back|value)/.test(text)) {
    stream.push("Stress-testing the call — checking edge, EV, and whether the confidence level actually justifies a bet recommendation here");
  }

  if (/(model|algorithm|edge|ev|expected value|calibration|pricing)/.test(text)) {
    stream.push("Checking the model logic against pricing discipline — where calibration matters and where raw edge is more reliable than the implied probability");
  }

  if (/(form|last start|recent run|fitness|prep)/.test(text)) {
    stream.push("Evaluating form in context — last start alone doesn't tell the story, need to look at class, distance suitability, and how the run was run");
  }

  if (/(track|going|surface|wet|heavy|soft|firm|good)/.test(text)) {
    stream.push("Factoring in track and surface — conditions can completely change the pecking order, especially for horses with strong going preferences");
  }

  stream.push("Pulling the reasoning together into the clearest, most direct answer the evidence supports");
  return Array.from(new Set(stream));
}

function pick<T>(arr: T[]): T {
  return arr[Math.floor(Math.random() * arr.length)];
}

export function getSearchThoughtStream(question?: string | null): string[] {
  const text = normalizePromptText(question);

  // Extract racing context
  const TRACKS: Record<string, { detail: string; character: string }> = {
    randwick: { detail: "right-handed", character: "long home straight, premium Sydney metro — the big races are won here" },
    flemington: { detail: "left-handed", character: "the straight course option and wide open fields make barrier draws critical for the turn races" },
    caulfield: { detail: "tight turns, short straight", character: "premium metro — a track that rewards fitness and tactical speed over raw class" },
    "eagle farm": { detail: "Queensland carnival", character: "the premier Queensland track — soft tracks are common and it rewards staying types" },
    rosehill: { detail: "right-handed", character: "a leader's track — the straight suits on-pace runners and the going is often consistent" },
    "moonee valley": { detail: "tight city circuit", character: "the Valley's short straight and tight bends demand tactical speed — big fields are a lottery here" },
    moonee: { detail: "tight city circuit", character: "the Valley's short straight and tight bends demand tactical speed — big fields are a lottery here" },
    sandown: { detail: "long straight", character: "a genuine test — the long home straight brings out the best in stayers and is honest on form" },
    doomben: { detail: "backmarker-friendly Queensland track", character: "the design suits back-half runners with a decent straight — leaders can struggle in big fields" },
    morphettville: { detail: "South Australian track", character: "a good straight that suits galloping types — honest track with no significant bias" },
    "gold coast": { detail: "tight Queensland track", character: "tight turns and short straight suit on-pace runners — backmarkers need a good run to figure" },
    hawkesbury: { detail: "tight turns, NSW provincial", character: "a provincial tight-track that catches out horses that like to settle midfield" },
    kembla: { detail: "NSW provincial, Kembla Grange", character: "an honest provincial track — form transfers reasonably well from here to Sydney metro" },
    warwick: { detail: "SA provincial", character: "South Australian provincial — form can be hard to assess against metro benchmarks" },
    "warwick farm": { detail: "right-handed Sydney track", character: "a fair track with a long home straight — leaders hold up well and the form is reliable" },
  };

  const detectedTrack = Object.keys(TRACKS).find((t) => text.includes(t));
  const trackInfo = detectedTrack ? TRACKS[detectedTrack] : null;
  const trackName = detectedTrack
    ? detectedTrack.charAt(0).toUpperCase() + detectedTrack.slice(1)
    : null;

  const dateMatch = text.match(/\b(jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|jun(?:e)?|jul(?:y)?|aug(?:ust)?|sep(?:tember)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)\s+(\d{1,2})\b/);
  const dayMatch  = text.match(/\b(monday|tuesday|wednesday|thursday|friday|saturday|sunday)\b/);
  const dateStr   = dateMatch ? `${dateMatch[1]} ${dateMatch[2]}` : dayMatch ? dayMatch[1] : null;
  const isWeekend = dateStr ? /saturday|sunday/.test(dateStr) : false;

  const raceMatch = text.match(/\b(?:race\s*(\d+)|r(\d+))\b/);
  const raceNum   = raceMatch ? (raceMatch[1] ?? raceMatch[2]) : null;

  // Extract horse name (capitalised sequence after "for" or standalone proper nouns)
  const horseMatch = text.match(/\b(?:for|on|about|how (?:has|is))\s+([a-z][a-z' ]{2,24})\b/);
  const horseName  = horseMatch ? horseMatch[1].trim() : null;

  const isBestBets   = /(best bet|best bets|tips|who wins|what should i back|recommend|pick|selection|back)/.test(text);
  const isFormQuery  = /(form|recent runs|last start|history|record|how has|how is)/.test(text);
  const isMarketQuery = /(odds|price|market|favourite|overbet|value|each.way|sp)/.test(text);
  const isRaceSpecific = raceNum !== null;

  const openers = [
    "Okay,", "Right,", "Alright,", "Let me think through this —", "Hmm,",
    "So,", "Right so,", "Okay let's work through this —",
  ];

  const thoughts: string[] = [];

  // ── Phase 0: Parse and scope the ask ──────────────────────────────────────
  const phase0Pool = [
    `${pick(openers)} the ask is ${isBestBets ? "a best bets request" : isFormQuery ? "a form query" : isMarketQuery ? "a market/value question" : "a racing information request"}${trackName ? ` for ${trackName}` : ""}${dateStr ? ` on ${dateStr}` : ""}${horseName ? ` — specifically about ${horseName}` : ""}. First thing I need to figure out is whether published tips exist yet or if I'm working from entries and early market`,
    `${pick(openers)} ${trackName ? `${trackName}` : "this meeting"}${dateStr ? ` on ${dateStr}` : ""}${isWeekend ? " — that's a weekend card, so likely a full metropolitan program" : ""}. The key question is timing — if this meeting is more than 48 hours out, day-of tips won't be published yet and I'll need to dig into entries, barrier draws, and any early Betfair pre-post movement`,
    `Scoping the question — ${trackName ? `${trackName}${trackInfo ? `, which is ${trackInfo.detail}` : ""}` : "need to identify the track"}${raceNum ? `, specifically race ${raceNum}` : ""}${dateStr ? `, date is ${dateStr}` : ""}${horseName ? `, focusing on ${horseName}` : ""}. Let me figure out what information is actually going to be available at this point in time`,
    `${pick(openers)} ${dateStr ? `${dateStr}` : "this date"}${trackName ? ` at ${trackName}` : ""}${trackInfo ? ` — ${trackInfo.character}` : ""}. I need to check whether we're pre-barrier draw, post-barrier draw, or race-day — that determines which sources are going to actually have useful information`,
    `Working through this — ${trackName ? `${trackName}` : "the track"}${dateStr ? ` on ${dateStr}` : ""}${horseName ? ` with ${horseName} as the focus` : ""}. If tips aren't live yet, the best sources are usually race entries, stable quotes, trial reports, and early TAB or Betfair pricing — that's where the real signal is`,
  ];
  thoughts.push(pick(phase0Pool));

  // ── Phase 1: Search strategy ───────────────────────────────────────────────
  const phase1Pool = [
    `${dateStr ? `${dateStr}${isWeekend ? " is a weekend — bigger fields, more media coverage, more tips to find" : " is a midweek card — less coverage but form guides and race previews are usually still up"}` : "This meeting"} — I'm going to look for the race card first to confirm the field, then check form guides and any trainer or stable comments${trackName ? ` specific to ${trackName}` : ""}`,
    `Planning the search — I shouldn't just fire a generic "tips" query because that'll return everything and nothing. I want${trackName ? ` ${trackName}` : " this track"} specifically${dateStr ? ` on ${dateStr}` : ""}${isFormQuery && horseName ? `, and I need form data specifically on ${horseName} — recent runs, class level, distance record` : isBestBets ? ", plus any expert selections or market moves that signal where the money is going" : ""}`,
    `${pick(openers)} the smart approach here is multiple targeted searches — race card${trackName ? ` at ${trackName}` : ""}, then form guide, then early market. Barriers are usually out 2-3 days before — if they're available${trackInfo ? `, ${trackName}'s ${trackInfo.detail} character means barrier position can really matter` : ", that's a key piece of the puzzle"}`,
    `Right — I'll check racenet and racing.com first for the card${dateStr ? ` on ${dateStr}` : ""}${trackName ? ` at ${trackName}` : ""}, then punters.com.au and racing publications for previews and selections. If day-of tips aren't live yet, jockey bookings and trainer quotes in the lead-up are usually the most reliable early signal`,
    `If this race is a few days out, the most useful information will be: barrier draw (if available), the recent form of the main contenders, any stable quotes or gear changes, and where the early TAB market is. That's what an experienced punter would look at${trackName ? ` for ${trackName}` : ""}${horseName ? ` — specifically checking ${horseName}'s record and recent work` : ""}`,
    `${pick(openers)} multiple sources here — race card, form guide, early market${trackName && trackInfo ? `. At ${trackName}, ${trackInfo.character} — so I'm paying particular attention to barrier and sectional profile, not just class` : ""}. Let me check what's actually available`,
  ];
  thoughts.push(pick(phase1Pool));

  // ── Phase 2: Quality check and cross-referencing ──────────────────────────
  const phase2Pool = [
    `Wait — I need to verify these results are actually for ${dateStr ? dateStr : "the right date"}${trackName ? ` at ${trackName}` : ""} and not a nearby meeting. Search results for racing can easily pull in the wrong card — last week's${trackName ? ` ${trackName}` : ""} result or a different state entirely. Let me filter carefully`,
    `Checking source reliability — ${trackName ? `${trackName}` : "this track"} should have form data even if official tips haven't been published. I'm looking at: is this a credible racing publication? Is the date explicitly stated? Is this the same meeting or a different one with similar name?`,
    `${pick(openers)} I've got some results — need to assess them properly. ${isMarketQuery ? "For a market question, I need sources that cite actual odds or SP, not just opinions about value." : isBestBets ? "For best bets, I'm looking for sources that give specific horse selections with reasoning — not just 'watch out for the favourite'." : isFormQuery && horseName ? `For ${horseName}'s form, I need actual run-by-run data, not just a summary mention.` : "I need actual race or form data, not just race previews that recycle the same narrative."}`,
    `Cross-referencing what I've found${dateStr ? ` for ${dateStr}` : ""}${trackName ? ` at ${trackName}` : ""} — ${trackName && trackInfo ? `this is ${trackInfo.character}, so I'm weighting sources that understand the track character over generic tip sites` : "prioritising sources with actual form data over generic preview copy"}. Checking if there's enough here to give a proper answer or if the data is too thin`,
    `${pick(openers)} some of these results are from nearby meetings or adjacent dates — I have to be precise here${trackName ? ` about ${trackName}` : ""}${dateStr ? ` on ${dateStr}` : ""}. A result from the week before or a different state will completely mislead the answer. Filtering to what's actually relevant`,
    `Got results — now quality-scoring them. ${isFormQuery && horseName ? `For ${horseName}, I want to see actual run positions and margins, not just "finished midfield". Going deeper on the form.` : isBestBets ? "For best bets I want convergence — a selection that shows up in multiple credible sources is much more reliable than a single tipster's outlier." : "Looking for specific, data-backed information rather than opinion pieces."} ${trackName && trackInfo ? `Also considering the ${trackName} track character — ${trackInfo.detail} — when assessing how reliable this information is.` : ""}`,
    `Hang on — I want to confirm I'm not pulling in ${trackName === "randwick" ? "Rosehill" : trackName === "flemington" ? "Caulfield" : trackName === "eagle farm" ? "Doomben" : "the wrong track"} results by mistake. The source needs to explicitly name${trackName ? ` ${trackName}` : " the correct track"}${dateStr ? ` and ${dateStr}` : ""} — I'll discard anything that doesn't match`,
  ];
  thoughts.push(pick(phase2Pool));

  // ── Phase 3: Building the answer ──────────────────────────────────────────
  const phase3Pool = [
    `Alright, I have what I need${trackName ? ` for ${trackName}` : ""}${dateStr ? ` on ${dateStr}` : ""}${raceNum ? ` race ${raceNum}` : ""}. ${isBestBets ? "I'll lead with the strongest selection, explain the reasoning, and be clear about any gaps in the available information." : isFormQuery && horseName ? `I have enough form data on ${horseName} to give a proper assessment — going to focus on what the form actually says, not just recite it.` : "I'll pull the strongest evidence together and be upfront about what I could and couldn't find."}`,
    `${pick(openers)} even if day-of tips haven't been published yet, I've got${trackName ? ` ${trackName}` : ""} form lines and early market data${dateStr ? ` for ${dateStr}` : ""}. That's enough to give a well-reasoned call — I'll flag where I'm working from form rather than published selections, so the answer is transparent about its confidence level`,
    `Good — I've got enough to work with. ${trackName && trackInfo ? `${trackName} — ${trackInfo.character} — that context matters for interpreting what I've found.` : ""} ${isBestBets ? "Leading with my strongest call and explaining what's driving it." : isMarketQuery ? "Drawing out the market information and what it implies about where the value actually sits." : "Pulling together the key information and answering directly."} Going to be honest about any gaps rather than padding with speculation`,
    `Okay, putting the answer together now${raceNum ? ` for race ${raceNum}` : ""}${trackName ? ` at ${trackName}` : ""}${dateStr ? ` on ${dateStr}` : ""}. ${horseName ? `The question was specifically about ${horseName} — going to lead with what the evidence says about them, not just general race context.` : isBestBets ? "Going to give my best call with a clear rationale — and I'll flag where the data is thinner than I'd like." : "Answering as directly as the available information allows."}`,
    `Drawing the strongest evidence into a direct answer${trackName ? ` for ${trackName}` : ""}${dateStr ? ` on ${dateStr}` : ""}. ${isBestBets ? "Best bets need a real reason — I won't just name a horse, I'll explain the edge." : isFormQuery ? "Form analysis needs to go beyond last start — looking at the progression, the company they've been keeping, and whether the conditions suit." : "The answer needs to be specific and useful, not a list of caveats."} Writing it up now`,
    `Compiling the final answer — ${trackName && trackInfo ? `at ${trackName} (${trackInfo.detail}), ` : ""}${dateStr ? `for ${dateStr}, ` : ""}I'll note where information was limited and give the clearest, most direct call the evidence actually supports. ${horseName ? `${horseName} is the focus — leading with that.` : ""}`,
  ];
  thoughts.push(pick(phase3Pool));

  return thoughts;
}
