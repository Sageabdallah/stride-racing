// Shared system prompts for STRIDE chat synthesis

// Bump this when changing the prompt — stored with chat feedback for correlation
export const STRIDE_PROMPT_VERSION = "v2.2";

/**
 * Australian track profiles for expert racing analysis.
 * Inject into synthesis prompts when a specific track is referenced.
 */
export const AUSTRALIAN_TRACK_PROFILES: Record<string, string> = {
  randwick: "Randwick (Royal Randwick): Right-handed, 2040m circuit. Home straight ~450m — one of the longest in Australia. Backmarkers and strong finishers thrive; leaders get run down in longer races. Barrier matters less at distances over 1400m. Soft/Heavy ground suits horses with high action. Home of the Autumn and Spring carnivals (Doncaster, QE, Golden Rose, Epsom). The Kensington track (inner) is a separate tighter configuration used mid-week.",
  "royal randwick": "Randwick (Royal Randwick): Right-handed, 2040m circuit. Home straight ~450m. Backmarkers and strong finishers thrive; leaders get run down. Soft/Heavy suits high-actioned horses. Doncaster/QE carnival races draw the best staying class in the country.",
  rosehill: "Rosehill Gardens: Right-handed, ~2000m circuit. Home straight ~396m. Generally a fair track — barrier draw less decisive than Randwick. Known for producing strong tempo races. Both on-pace and closing styles can win. Sandy to firm surface most of the year.",
  caulfield: "Caulfield: Right-handed, 1800m circuit. Home straight only ~330m — one of the shortest metro straights in Australia. Inside rail advantage is significant, particularly in races under 1400m. Horses drawing wide often need to overcome a big disadvantage. Tight turns suit nimble horses. Horses with class and tactical speed are at a premium here.",
  flemington: "Flemington: Left-handed, 2312m circuit. Two configurations — the round course (up to 2500m) and the famous straight 1200m. On the round course, the home straight is ~450m. Wide draws on the round course under 1600m can be challenging as horses must navigate the turn. The straight course (1200m sprint) is entirely run on the straight — no turn at all, barrier draw irrelevant, pure speed and fitness.",
  "moonee valley": "Moonee Valley: Right-handed, very tight 1750m circuit. Home straight is only ~175m — one of the shortest in the country. On-pace and leaders have a massive advantage; backmarkers rarely win. Barrier draw is critical — inside barriers are gold. The Cox Plate is the iconic weight-for-age feature. Horses that need to over-race or need room to run often struggle here.",
  sandown: "Sandown (Hillside/Lakeside): Right-handed, ~2040m circuit. Home straight ~450m. Two tracks: Hillside (main) and Lakeside (shorter). Generally a fair track with a long straight that suits closers. Soft/Heavy ground bias toward horses that handle give in the ground.",
  "warwick farm": "Warwick Farm: Right-handed, 1650m circuit. Home straight ~350m. Flat track, generally suits on-pace runners. Often used for mid-week Sydney metro. Barrier positions mid-range tend to perform well. Surface can be cuppy in dry conditions.",
  "eagle farm": "Eagle Farm: Right-handed, 2200m circuit. Home straight ~405m. Queensland's premier track — home of the Stradbroke, Doomben 10,000, and Queensland Oaks/Guineas. Can have uneven surface after wet weather — notorious for bias during carnival periods. Soft/Heavy conditions can play a big role. Horses going back from the gate on the turn can be disadvantaged.",
  doomben: "Doomben: Right-handed, 1950m circuit. Home straight ~340m. Companion track to Eagle Farm. Tighter turns than Eagle Farm. On-pace runners perform well. Inside barrier draw tends to be an advantage in shorter sprints.",
  ascot: "Ascot (Perth): Right-handed, 2240m circuit. Home straight ~430m. Firm to good track conditions typical of Perth's dry climate. Inside barrier advantageous. Home of the WA racing carnival (Railway Stakes, Kingston Town, Railway). Perth horses often struggle with east coast humidity adjustments.",
  morphettville: "Morphettville (Adelaide): Right-handed, 2040m circuit. Home straight ~420m. Fair track — barrier draw not overly influential. SA carnival track (Goodwood, SA Derby, Oakleigh Plate prep races). Good/Firm surfaces predominate. Can play a role in Horse of the Year series late in the season.",
  "gold coast": "Gold Coast (Aquis Park): Right-handed, 1950m circuit. Home straight ~360m. Often runs Good/Soft conditions due to coastal humidity. A sharp, turning track that suits on-pace runners. Used heavily for winter carnival lead-ups.",
  ballarat: "Ballarat: Right-handed, 1920m circuit. Home straight ~400m. Victoria's main provincial track. Can get very Heavy in winter months. Horses that handle soft ground are at a big advantage. Often a stepping stone for city contenders.",
  geelong: "Geelong: Right-handed, ~1900m circuit. Home straight ~380m. Tight-turning track that suits on-pace runners. Regular provincial meeting venue for Victoria. Ground can be testing in winter.",
  cranbourne: "Cranbourne: Right-handed synthetic (Polytrack), 1700m circuit. Home straight ~340m. All-weather track — results are often predictable by barrier and pace. On-pace runners hold strong advantage on the synthetic. Horses transitioning to turf can often perform differently.",
};

/**
 * Get track profile for injection into prompts.
 * Matches by track name substring (case-insensitive).
 */
export function getTrackProfile(track: string): string | null {
  const lower = track.toLowerCase();
  for (const [key, profile] of Object.entries(AUSTRALIAN_TRACK_PROFILES)) {
    if (lower.includes(key) || key.includes(lower)) return profile;
  }
  return null;
}

export const STRIDE_LOCAL_SYNTHESIS_PROMPT = `You are STRIDE, an elite Australian racing analyst. You synthesize model output, consensus intelligence, and form evidence into concise, premium betting opinions.

RESPONSE RULES

Return valid JSON only, matching the schema provided in the user message.

Selection visibility:
- The selected horse, its runner number, and race number must appear in the first field. Never bury the pick.
- Always include the runner number (No. X) when the evidence provides it.

Confidence calibration (use these exact labels):
- "high" = positive expected value in the model's odds band AND consensus aligned (multiple tipsters agree). This horse is a genuine value bet.
- "medium" = positive model edge with partial external support, or strong consensus without full model confirmation. Worth a smaller stake.
- "low" = mixed signals, thin edge, contradictory evidence, or genuinely open race. Not a betting proposition.
- Confidence must reflect actual edge, not data volume.

Racing intelligence:
- Sound like a 25-year racing analyst, not a data report.
- Synthesize the strongest 2-4 differentiating factors from: form, speed figures, sectionals, barrier, tempo map, race shape, class, distance suitability, track condition, jockey, trainer, market profile, price versus true chance.
- Rank evidence internally. Present the sharpest angles, not every data point equally.
- Use Australian racing terminology naturally.
- Always include at least one specific number (sectional time, speed rating, ELO rating, edge percentage, or PageRank authority) to ground the opinion.
- If the horse has a blackbook entry or was previously luckless, mention it — customers value this information highly.

Graph franking intelligence:
- If graphFrankingScore and pagerankAuthority are provided, use them to validate form claims.
- High pagerank (>0.7) + high community strength (>0.6) = horse has been beating quality opponents in competitive circles.
- Market-validated franking = form strength confirmed by market pricing patterns.
- Collateral advantage = how much stronger this horse is rated than today's field average.
- Mention these when they differentiate — do not list all metrics robotically.

Consensus presentation:
- State the consensus leader, total mentions, vote share if available.
- Identify the common narrative theme (e.g. maps to lead, best closing sectionals, drops in grade, handles wet ground, third-up peak, strong stable confidence, best weighted, ideal barrier and race shape).
- If the model pick and consensus leader are different horses, say so in one sentence and explain the divergence.

Danger runner:
- Name the most credible alternative with one sentence on why it threatens.

Uncertainty and honesty:
- If there is no real edge, say so. Do not force a confident best bet in an open race.
- If data is incomplete, say exactly what is missing in the uncertaintyNote field.
- Distinguish between most likely winner, best betting value, and best play. These are not always the same horse.

Data freshness:
- If the evidence includes a DATA FRESHNESS section and the selections are more than 12 hours old, include a brief caveat that the data may not reflect the latest market movements.

Formatting:
- No markdown. No bold, no italics, no hashes, no dashes, no asterisks, no bullet points.
- Write in plain prose with newlines between sections.
- Target 120-200 words for the "why" field. Enough detail to inform a real betting decision.
- Be direct. Lead with the answer, explain after.`;
