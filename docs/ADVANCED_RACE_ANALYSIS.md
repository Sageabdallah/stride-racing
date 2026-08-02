# Advanced Race Logic Analysis
## Full Field "Train of Thought" Reasoning via Groq API

### Overview

This feature implements sophisticated 4-phase logical deduction for each horse in a race using the Groq API (Llama 3.3 70B). It simulates a professional race analyst's cognitive process, providing detailed handicapping logic that covers all horses in the field.

### The 4-Phase Framework

#### Phase 1: Profile & Positioning
Analyzes where each horse will settle in the run based on:
- Barrier draw (inside vs outside advantages)
- Early speed profile (leader/on-pace/stalker/midfield/backmarker)
- Jockey tactics and trainer patterns
- Track bias (rail position, surface conditions)

**Example Output:**
```
"Based on barrier 3, leader speed profile, and aggressive jockey tactics, this horse 
will likely settle 1st-2nd because it has natural gate speed and the inside draw favors 
controlling the pace. The rail is playing fairly, so leading from the fence is advantageous."
```

#### Phase 2: Pace Scenario Fit
Maps each horse to the likely pace scenario:
- Identifies speed horses who will push forward
- Identifies stalkers who benefit from genuine tempo
- Identifies closers who need a hot pace to pick up tiring leaders
- Assesses what happens if pace collapses

**Example Output:**
```
"In a race with Speed Demon and Fast Starter likely to push forward, this horse's 
sprinting stamina means it will benefit from a hot pace. If the pace collapses, 
this horse will be caught flat as it needs a genuine tempo to show its best finish."
```

#### Phase 3: Class & Condition Assessment
Evaluates class movement and fitness:
- Moving up/down in grade from last start
- Form cycle (improving/fresh/declining)
- Weight change impact
- Distance suitability
- Recent performance metrics

**Example Output:**
```
"Moving up in grade from Benchmark 70 to 78. Form cycle shows improving - evidence 
from tightening margins and faster sectionals in last two starts. Weight increase 
of 2kg hinders given 1600m distance where every kilogram counts in the finish."
```

#### Phase 4: Comparative Finishing Logic (The "Why")
Compares against specific rivals:
- Relative to named competitors, what advantages/disadvantages exist
- Specific finishing position prediction with confidence %
- Direct comparison: "Will finish ahead of X because... but behind Y because..."

**Example Output:**
```
"Relative to Speed Demon and Late Charger, this horse exceeds in sustained speed 
but lacks tactical versatility. Therefore, in this specific race dynamic: predicted 
2nd place with 70% confidence - will track the speed but can't quite reel in a 
genuine leader over this journey."
```

### Sophisticated Reasoning Categories

Instead of simple "can_win" boolean, each horse is classified as:

| Category | Definition | Typical Win % |
|----------|------------|---------------|
| **Contender** | Legitimate winning chance | >20% |
| **Place Hope** | Won't win but 2nd/3rd likely | 5-15% win, >40% place |
| **Lucky to Place** | Needs chaos to run top 3 | <5% win, <30% place |
| **Outclassed** | No place hope | <2% win |

### Winner Determination Logic

After all horses are analyzed through Phases 1-4, the system crowns ONE winner by comparing all Phase 4 analyses simultaneously:

```
"Analyzing the field collectively, Midfield Master presents the strongest win case 
because it maps to sit 3rd, 2L off a genuine tempo, with superior turn of foot to 
outfinish the speed. Speed Demon and Fast Flash are place chances due to their pace 
pressing roles - they'll set it up but lack the stamina to hold on. Late Charger 
cannot win due to insufficient tempo - there's no pace collapse scenario given the 
two confirmed leaders."
```

### Market Intelligence

Each horse is assessed against market odds:

| Assessment | Definition | Action |
|------------|------------|--------|
| **Value** | Market price longer than assessed chance | Consider for bet |
| **Overbet** | Market price shorter than assessed chance | Avoid or lay |
| **Correctly Priced** | Market aligns with assessment | No edge |

Value edge is calculated as: `(Assessed Prob - Market Implied Prob) / Market Implied Prob * 100`

### API Endpoints

#### Run Analysis
```http
POST /api/race-analysis/analyze
Content-Type: application/json

{
  "track": "Flemington",
  "raceNumber": 7,
  "raceDate": "2026-03-11",
  "raceData": { ... } // Optional - will load from tips file if not provided
}
```

**Response:**
```json
{
  "success": true,
  "analysis": {
    "track": "Flemington",
    "race_number": 7,
    "race_name": "Australian Cup",
    "pace_scenario": "honest_tempo",
    "pace_description": "Speed Demon and Fast Flash will ensure a genuine tempo...",
    "predicted_winner": {
      "horse_name": "Midfield Master",
      "number": 2,
      "assessed_win_prob": 28.5,
      "confidence_pct": 75
    },
    "predicted_places": [...],
    "horses": [...],
    "race_summary": "A truly run race where the best middle-distance horse should win...",
    "key_dynamics": "The tempo battle between the two leaders will be critical...",
    "betting_recommendation": "Back Midfield Master at $4+ or consider exacta 2 over 1,3"
  }
}
```

#### Fetch Stored Analysis
```http
GET /api/race-analysis/Flemington/7/2026-03-11
```

#### Fetch All Analyses for Date
```http
GET /api/race-analysis/2026-03-11
```

### Frontend Usage

```tsx
import AdvancedRaceAnalysis from '@/components/AdvancedRaceAnalysis';

function RacePage({ track, raceNumber, raceDate }) {
  return (
    <AdvancedRaceAnalysis
      track={track}
      raceNumber={raceNumber}
      raceDate={raceDate}
      onAnalysisComplete={(analysis) => {
        console.log('Winner:', analysis.predicted_winner?.horse_name);
      }}
    />
  );
}
```

### Database Schema

#### race_analyses table
Stores race-level analysis results:
- `track`, `raceNumber`, `raceDate` - Race identification
- `paceScenario`, `paceDescription` - Pace analysis
- `predictedWinner`, `predictedPlaces` - Winner prediction
- `horsesAnalysisJson` - Full field analysis (JSON array)
- `raceSummary`, `keyDynamics`, `bettingRecommendation` - Narrative analysis

#### horse_race_analyses table
Stores individual horse analyses:
- `phase1Profile` through `phase4Comparative` - 4-phase reasoning
- `trainOfThought` - Combined narrative
- `category` - Contender/Place Hope/Lucky to Place/Outclassed
- `assessedWinProb`, `assessedPlaceProb` - Probability assessments
- `marketAssessment`, `valueEdgePct` - Market intelligence
- `comparativeAdvantage`, `keyRivals` - Comparative analysis

### Example Complete Analysis

See `examples/sample_4phase_analysis.json` for a complete example showing the full output structure for a race with 8 runners.

### Performance Considerations

- **Groq API Rate Limiting**: 30 requests/minute on free tier
- **Analysis Time**: ~10-30 seconds per race depending on field size
- **Token Usage**: ~2000-4000 tokens per race
- **Cost**: ~$0.02-0.05 per race on Groq

### Configuration

Environment variables:
```bash
GROQ_API_KEY=gsk_...           # Required
LLM_PROVIDER=groq              # Use 'groq' or 'ollama'
LLM_MODEL=llama-3.3-70b-versatile  # Optional, has default
```

### Error Handling

The system handles various failure modes:
- LLM unavailable → Returns null, logs warning
- JSON parse failure → Retries with relaxed parsing
- Constraint violation → Enforces rules post-processing
- Rate limiting → Respects Groq's 30 req/min limit

### Files Added/Modified

| File | Description |
|------|-------------|
| `server/python/advanced_race_analysis.py` | Core 4-phase analysis engine |
| `shared/schema.ts` | Database schema for race_analyses and horse_race_analyses |
| `server/routes.ts` | API endpoints for running/fetching analysis |
| `client/src/hooks/useAdvancedRaceAnalysis.ts` | React hook for API integration |
| `client/src/components/AdvancedRaceAnalysis.tsx` | UI component for displaying analysis |
| `docs/ADVANCED_RACE_ANALYSIS.md` | This documentation |
