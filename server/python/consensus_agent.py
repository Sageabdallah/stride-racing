#!/usr/bin/env python3
"""
STRIDE Consensus Agent V3 — Crowd-First Architecture

Fixed panel of independent tipsters fetched via Tavily Extract,
plus Claude web search research (ALL sources, including bookmakers)
for maximum crowd signal coverage. crowd_score is the primary output.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime
from difflib import SequenceMatcher
from pathlib import Path

import requests as _requests

try:
    import anthropic as _anthropic_module
except ImportError:
    _anthropic_module = None

try:
    import psycopg2
except ImportError:
    psycopg2 = None

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent / "intelligence"))

from intelligence.common import (
    INTELLIGENCE_DIR,
    flatten_races,
    get_connection,
    load_database_url,
    load_racecard_meetings,
    normalize_name,
    normalize_track,
    safe_float,
)
from horse_names import horse_name_match, normalize_horse_name


BOOKMAKER_BLOCKED_DOMAINS = [
    "sportsbet.com.au",
    "tab.com.au",
    "ladbrokes.com.au",
    "neds.com.au",
    "betfair.com.au",
    "bluebet.com.au",
    "pointsbet.com.au",
    "bet365.com",
    "palmerbet.com.au",
    "betright.com.au",
    "topbetta.com.au",
    "elitebet.com.au",
    "odds.com.au",
    "oddschecker.com.au",
    "bettingodds.com.au",
    # punters.com.au — removed from blocklist: has independent editorial tips despite Flutter ownership
    # racenet.com.au — removed from blocklist: editorial tips are editorially independent
]

BOOKMAKER_BLOCKED_KEYWORDS = [
    "sponsored by",
    "brought to you by",
    "bet now with",
    "sign up bonus",
    "deposit bonus",
    "bonus bet",
    "money back",
    "price boost",
    "our partner",
]

MIN_INDEPENDENT_SOURCES_PER_RACE = 3

BUCKET_WEIGHTS = {
    "form_analyst": 1.0,
    "speed_analyst": 1.3,
    "ratings_analyst": 1.3,
    "stable_watcher": 1.5,
    "value_analyst": 1.4,
    "track_specialist": 1.2,
    "retail_punter": 0.7,
    "market_reader": 1.4,
}

DAILY_TAVILY_CAP = 50     # panel extract (up to 35+ active sources)
DAILY_CLAUDE_CAP = 200    # batched panel extraction (~40) + web search research (~40) + buffer


# ── Extraction model ──
# Read from env, never hardcoded. The previous hardcoded id
# (claude-sonnet-4-20250514) was retired 2026-06-15; every extraction call
# 404'd, the agent wrote all-zero-mention scores, printed "Complete" and exited
# 0. Nothing downstream could tell that day from a genuinely quiet one. An id is
# a deployment fact, so it lives in the environment and is checked at startup by
# preflight_extraction_model() before any race is processed.
#
# temperature is deliberately NOT passed: non-default sampling parameters are
# rejected on claude-sonnet-5 and later. thinking is explicitly disabled —
# these models run adaptive thinking when the parameter is omitted, which shares
# the max_tokens budget with the response and can truncate a JSON extraction.
DEFAULT_EXTRACTION_MODEL = "claude-sonnet-5"


def extraction_model() -> str:
    """ANTHROPIC_CONSENSUS_MODEL, or the current default."""
    return os.environ.get(
        "ANTHROPIC_CONSENSUS_MODEL", DEFAULT_EXTRACTION_MODEL
    ).strip() or DEFAULT_EXTRACTION_MODEL


NO_THINKING = {"type": "disabled"}


class ExtractionModelUnavailable(RuntimeError):
    """The configured extraction model cannot be called. Fatal by design."""


def preflight_extraction_model(client, model: str) -> None:
    """One tiny call before any race is processed.

    Uses the same parameter shape as the real extraction calls, so it catches a
    retired/misspelled id (404) *and* a rejected parameter (400) — the two ways
    this has actually broken. Between 2026-06-15 and 2026-08-02 every extraction
    404'd and the agent still exited 0; this makes that impossible.
    """
    try:
        client.messages.create(
            model=model,
            max_tokens=4,
            thinking=NO_THINKING,
            timeout=30,
            messages=[{"role": "user", "content": "ok"}],
        )
    except Exception as e:
        raise ExtractionModelUnavailable(
            f"extraction model {model!r} is not callable: {type(e).__name__}: {e}. "
            f"Set ANTHROPIC_CONSENSUS_MODEL to a live model id."
        ) from e


# Populated by run_consensus_agent, read by main() for the exit contract.
LAST_RUN_HEALTH: dict = {}

# ── Tipster-accuracy feedback (opt-in: STRIDE_ACCURACY_WEIGHTS=true) ──
# source_accuracy_tracker records every panel tip's outcome; these weights
# feed the measured hit rates back into panel quality multipliers. Leak-free
# by construction: only settled tips from strictly past race days are read.
# Sources below the sample floor stay neutral; the ratio to the panel-wide
# baseline is shrunk (pseudo-count prior) and hard-bounded so no tipster can
# be silenced or dominate on a lucky streak. Default off: multipliers are {}
# and scoring is byte-identical to before.
ACCURACY_MIN_TIPS = 20
ACCURACY_LOOKBACK_DAYS = 120
ACCURACY_SHRINK_TIPS = 20
ACCURACY_CLAMP = (0.75, 1.25)


def accuracy_multiplier(wins, tips, baseline_rate,
                        min_tips=ACCURACY_MIN_TIPS,
                        shrink=ACCURACY_SHRINK_TIPS,
                        clamp=ACCURACY_CLAMP):
    """Bounded, shrunk hit-rate ratio vs the panel baseline (1.0 = neutral)."""
    if tips < min_tips or baseline_rate <= 0:
        return 1.0
    shrunk_rate = (wins + shrink * baseline_rate) / (tips + shrink)
    return round(max(clamp[0], min(clamp[1], shrunk_rate / baseline_rate)), 3)


def load_accuracy_multipliers(lookback_days=ACCURACY_LOOKBACK_DAYS):
    """
    Per-tipster quality multipliers from the source_accuracy table.
    Opens its own read-only connection (the panel fetch runs before the
    agent's main connection exists). Returns {} unless
    STRIDE_ACCURACY_WEIGHTS=true and enough settled history exists.
    """
    if os.environ.get("STRIDE_ACCURACY_WEIGHTS", "false").strip().lower() not in ("true", "1", "yes"):
        return {}
    conn = None
    try:
        conn = get_connection()
        cur = conn.cursor()
        cur.execute("""
            SELECT tipster_id,
                   COUNT(*) FILTER (WHERE was_winner) AS wins,
                   COUNT(*) AS tips
            FROM source_accuracy
            WHERE tipster_id IS NOT NULL
              AND race_date >= (CURRENT_DATE - INTERVAL '%s days')
              AND race_date < CURRENT_DATE
            GROUP BY tipster_id
        """ % int(lookback_days))
        rows = cur.fetchall()
        cur.close()
        total_wins = sum(int(r[1] or 0) for r in rows)
        total_tips = sum(int(r[2] or 0) for r in rows)
        if total_tips < ACCURACY_MIN_TIPS:
            print(f"[ACCURACY] Only {total_tips} settled tips in {lookback_days}d — neutral weights", file=sys.stderr)
            return {}
        baseline = total_wins / total_tips
        mults = {r[0]: accuracy_multiplier(int(r[1] or 0), int(r[2] or 0), baseline) for r in rows}
        active = {k: v for k, v in mults.items() if v != 1.0}
        print(f"[ACCURACY] Weights active for {len(active)}/{len(mults)} tipsters "
              f"(baseline hit {baseline:.3f}, {total_tips} tips/{lookback_days}d)", file=sys.stderr)
        return mults
    except Exception as e:
        print(f"[ACCURACY] Unavailable ({e}) — neutral weights", file=sys.stderr)
        return {}
    finally:
        try:
            if conn:
                conn.close()
        except Exception:
            pass


def _usage_path(date_str: str) -> Path:
    return INTELLIGENCE_DIR / f".consensus_api_usage_{date_str}.json"


def _load_usage(date_str: str) -> dict:
    path = _usage_path(date_str)
    if path.exists():
        try:
            return json.loads(path.read_text())
        except (json.JSONDecodeError, OSError):
            pass
    return {"tavily": 0, "claude": 0}


def _save_usage(date_str: str, usage: dict) -> None:
    _usage_path(date_str).write_text(json.dumps(usage))


def _check_cap(usage: dict, key: str, cap: int) -> bool:
    return usage.get(key, 0) < cap


def is_content_usable(
    content: str, url: str, field_names: list[str]
) -> tuple[bool, str]:
    """
    Multi-layer quality gate.
    Returns (usable, reason).
    """
    url_lower = url.lower()
    for domain in BOOKMAKER_BLOCKED_DOMAINS:
        if domain in url_lower:
            return False, f"BLOCKED (domain: {domain})"

    if content:
        content_lower = content.lower()
        for kw in BOOKMAKER_BLOCKED_KEYWORDS:
            if kw in content_lower:
                return False, f"BLOCKED (keyword: '{kw}')"

    if not content or len(content.strip()) < 200:
        return False, "too short (stub/paywall)"

    content_lower = content.lower()
    if not any(normalize_horse_name(h) in content_lower for h in field_names):
        return False, "no field runners mentioned"

    return True, "ok"


def match_horse_to_field(extracted_name: str, field_names: list[str]) -> str | None:
    """
    Match an extracted horse name against the racecard field.
    Returns the canonical field name, or None if no confident match.
    """
    extracted_norm = normalize_horse_name(extracted_name)
    if not extracted_norm:
        return None

    for field_name in field_names:
        if normalize_horse_name(field_name) == extracted_norm:
            return field_name

    scores = []
    for field_name in field_names:
        ratio = SequenceMatcher(
            None, extracted_norm, normalize_horse_name(field_name)
        ).ratio()
        if ratio >= 0.85:
            scores.append((ratio, field_name))

    if not scores:
        return None

    scores.sort(key=lambda x: x[0], reverse=True)

    if len(scores) == 1:
        return scores[0][1]

    if scores[0][0] - scores[1][0] < 0.02:
        return None

    return scores[0][1]


PANEL_OPTIONAL_ENV = "STRIDE_PANEL_OPTIONAL"


class PanelUnavailable(RuntimeError):
    """The tipster panel is absent. Fatal unless explicitly made optional."""


def load_tipster_panel() -> dict:
    """Load tipster_panel.json from the same directory.

    Raises rather than returning an empty list. Returning `{"sources": []}`
    on a missing file is what let the panel be absent from every Fargate
    task since the cloud went live, with one stderr line as the only
    evidence: fetch_panel_pages then logged "No active+verified panel
    members" and consensus ran on Perplexity alone, scoring every race and
    reporting success. A degraded panel does not crash anything — it just
    makes worse picks, quietly, which is why it has to be the loud kind of
    failure instead.

    The guard lives HERE, in the consumer, not in whichever job happens to
    invoke it. Fargate stages the panel in dispatch(); the Mac scheduler
    has it on disk; a future Lambda would have neither. Putting the check
    at the read means every one of those paths is covered by construction,
    including paths that do not exist yet.

    STRIDE_PANEL_OPTIONAL=true is the deliberate escape for local dev and
    CI, which have no bucket credential. Documented in CLAUDE.md — the same
    treatment .claude/skills/ gets, and for the same reason: absent by
    design must be written down, not discovered.
    """
    panel_path = Path(__file__).resolve().parent / "tipster_panel.json"
    if panel_path.exists():
        return json.loads(panel_path.read_text(encoding="utf-8"))

    if os.environ.get(PANEL_OPTIONAL_ENV, "").strip().lower() in ("true", "1", "yes"):
        print(f"[PANEL] tipster_panel.json absent and {PANEL_OPTIONAL_ENV} is "
              f"set — running panel-less on purpose. Consensus scores will "
              f"come from Perplexity alone.", file=sys.stderr)
        return {"sources": [], "bucket_definitions": {}}

    raise PanelUnavailable(
        f"{panel_path} not found. The file is gitignored (the repo is "
        f"PUBLIC) and is staged from the private models bucket at task "
        f"start — see infra/09c_upload_panel.sh and _stage_panel() in "
        f"infra/jobs/handler.py. Set {PANEL_OPTIONAL_ENV}=true to run "
        f"without it on purpose.")


def fetch_panel_pages(
    tavily_client,
    panel: dict,
    date_str: str,
    usage: dict,
    dry_run: bool = False,
    accuracy_multipliers: dict | None = None,
) -> tuple[list[dict], list[dict]]:
    """
    Fetch all active+verified panel members via Tavily Extract.
    Returns (page_results, panel_log_rows).

    accuracy_multipliers: optional {tipster_id: multiplier} from
    load_accuracy_multipliers(); composes with the proofed-results boost
    into each page's quality_multiplier. None/{} = neutral (default).
    """
    active_sources = [
        s for s in panel.get("sources", [])
        if s.get("active") and s.get("verified")
    ]

    if not active_sources:
        print("[PANEL] WARNING: No active+verified panel members", file=sys.stderr)
        return [], []

    print(f"[PANEL] Fetching {len(active_sources)} active panel members...", file=sys.stderr)

    results = []
    panel_log_rows = []

    for source in active_sources:
        fetch_url = source.get("tip_page_url") or source.get("base_url", "")
        sid = source["id"]
        name = source["name"]
        bucket = source.get("type", "form_analyst")

        if dry_run:
            print(f"  [DRY-RUN] Would fetch: {name} ({fetch_url})", file=sys.stderr)
            panel_log_rows.append({
                "race_date": date_str, "tipster_id": sid, "tipster_name": name,
                "fetch_url": fetch_url, "fetch_status": "SKIPPED",
                "content_length": 0, "picks_extracted": 0, "fetch_duration_ms": 0,
            })
            continue

        if not _check_cap(usage, "tavily", DAILY_TAVILY_CAP):
            print(f"  [PANEL] Tavily cap reached. Skipping {name}.", file=sys.stderr)
            panel_log_rows.append({
                "race_date": date_str, "tipster_id": sid, "tipster_name": name,
                "fetch_url": fetch_url, "fetch_status": "SKIPPED",
                "content_length": 0, "picks_extracted": 0, "fetch_duration_ms": 0,
            })
            continue

        start_ms = time.time()
        try:
            usage["tavily"] += 1
            _save_usage(date_str, usage)

            extract_result = tavily_client.extract(
                urls=[fetch_url], extract_depth="basic"
            )

            elapsed_ms = int((time.time() - start_ms) * 1000)
            content = ""
            if extract_result and extract_result.get("results"):
                content = extract_result["results"][0].get("raw_content", "")

            if content and len(content.strip()) > 100:
                print(
                    f"  OK [{bucket:15}] {name} ({len(content):,} chars, {elapsed_ms}ms)",
                    file=sys.stderr,
                )
                # Proofed sources (publicly tracked P/L) get 15% quality boost
                q_mult = 1.15 if source.get("proofed_results") else 1.0
                # Measured-accuracy feedback (STRIDE_ACCURACY_WEIGHTS=true)
                if accuracy_multipliers:
                    q_mult = round(q_mult * accuracy_multipliers.get(sid, 1.0), 3)

                results.append({
                    "content": content,
                    "url": fetch_url,
                    "tipster_id": sid,
                    "tipster_name": name,
                    "bucket": bucket,
                    "is_panel_source": True,
                    "quality_multiplier": q_mult,
                })
                panel_log_rows.append({
                    "race_date": date_str, "tipster_id": sid, "tipster_name": name,
                    "fetch_url": fetch_url, "fetch_status": "SUCCESS",
                    "content_length": len(content), "picks_extracted": 0,
                    "fetch_duration_ms": elapsed_ms,
                })
            else:
                # Tavily reports WHY in failed_results, and this only ever
                # read results[0], so a moved URL and a site that blocks
                # extraction were both logged as the same bare "EMPTY".
                # Racenet had been a plain 404 since some time before
                # 2026-08-06 and nothing said so.
                failed = (extract_result or {}).get("failed_results") or []
                why = failed[0].get("error", "") if failed else "no content"
                print(
                    f"  EMPTY [{bucket:15}] {name} ({elapsed_ms}ms): {why}",
                    file=sys.stderr,
                )
                panel_log_rows.append({
                    "race_date": date_str, "tipster_id": sid, "tipster_name": name,
                    "fetch_url": fetch_url, "fetch_status": "FAILED",
                    "content_length": len(content) if content else 0,
                    "picks_extracted": 0, "fetch_duration_ms": elapsed_ms,
                })

            time.sleep(0.5)

        except Exception as e:
            elapsed_ms = int((time.time() - start_ms) * 1000)
            print(f"  FAIL [{bucket:15}] {name}: {e}", file=sys.stderr)
            panel_log_rows.append({
                "race_date": date_str, "tipster_id": sid, "tipster_name": name,
                "fetch_url": fetch_url, "fetch_status": "FAILED",
                "content_length": 0, "picks_extracted": 0,
                "fetch_duration_ms": elapsed_ms,
            })

    return results, panel_log_rows


def search_race_tips_perplexity(
    track: str,
    race_number: int,
    race_name: str,
    distance: int | None,
    race_class: str | None,
    field: list[dict],
    field_names: list[str],
    date_str: str,
    date_formatted: str,
) -> str:
    """
    Uses Perplexity Sonar Pro to search the web for racing tips.

    Completely separate from Anthropic quota — no rate limit competition.
    Perplexity handles all web searching and returns a synthesised summary
    with citations. Claude then extracts structured picks from this summary
    in a single cheap extraction call.

    Returns raw text summary from Perplexity, or empty string on failure.
    """

    perplexity_key = os.getenv("PERPLEXITY_API_KEY")
    if not perplexity_key:
        print("    [PERPLEXITY] No API key found — falling back to Claude web search", file=sys.stderr)
        return ""

    field_lines = []
    for r in field[:14]:
        if isinstance(r, dict):
            horse = r.get("horse", r.get("horse_name", "?"))
            barrier = r.get("draw", r.get("barrier", "?"))
            jockey = r.get("jockey", "?")
            trainer = r.get("trainer", "?")
            odds = r.get("marketOdds", r.get("odds", "?"))
        else:
            horse = r
            barrier = jockey = trainer = odds = "?"
        field_lines.append(f"  {horse} (barrier {barrier}, jockey: {jockey}, trainer: {trainer}, odds: ${odds})")

    query = f""""{race_name}" "{track}" tips {date_formatted}

Find horse racing tips, previews, and form analysis for this specific race:

Track: {track}
Race {race_number}: {race_name}
Distance: {distance or '?'}m | Class: {race_class or '?'}
Date: {date_formatted}

Runners:
{chr(10).join(field_lines)}

Search for tips from these types of sources:
1. Newspaper racing writers (Herald Sun, Daily Telegraph, Courier-Mail, The Advertiser, The West Australian)
2. Racing websites (Racenet, Punters.com.au, Racing.com, Just Horse Racing, The Races)
3. Racing TV/radio tipsters (Sky Racing, RSN, Racing.com TV)
4. Independent form analysts and speed mappers
5. Bookmaker best bets (TAB, Sportsbet, Ladbrokes)
6. Social media tipsters (X/Twitter racing accounts)

For each horse being tipped, note:
- The specific source/tipster name recommending them
- Whether they pick the horse to WIN, place (EACH WAY), or as a roughie
- The key reason given

Only include horses from the runners list above. List every source you find."""

    try:
        print(f"    [PERPLEXITY] Researching {track} R{race_number} ({race_name})...", file=sys.stderr, flush=True)
        response = _requests.post(
            "https://api.perplexity.ai/chat/completions",
            headers={
                "Authorization": f"Bearer {perplexity_key}",
                "Content-Type": "application/json",
            },
            json={
                "model": "sonar-pro",
                "messages": [
                    {
                        "role": "system",
                        "content": (
                            "You are an expert Australian horse racing research analyst. "
                            "Search thoroughly for tipster opinions, form analysis, previews, "
                            "and market information for the specific race asked about. "
                            "Include newspaper tipsters, racing website tips, TV/radio tipsters, "
                            "and bookmaker best bets. Name every source you find. "
                            "Be specific about which horses are being recommended and why."
                        ),
                    },
                    {"role": "user", "content": query},
                ],
                "return_citations": True,
                "search_recency_filter": "week",
                "temperature": 0.1,
            },
            timeout=60,
        )

        if response.status_code != 200:
            print(f"    [PERPLEXITY] API error {response.status_code}: {response.text[:200]}", file=sys.stderr)
            return ""

        data = response.json()
        content = data["choices"][0]["message"]["content"]
        citations = data.get("citations", [])
        print(
            f"    [PERPLEXITY] {track} R{race_number}: Got {len(content)} chars from {len(citations)} sources",
            file=sys.stderr,
        )
        return content

    except _requests.exceptions.Timeout:
        print(f"    [PERPLEXITY] Timeout for {track} R{race_number} — falling back", file=sys.stderr)
        return ""
    except Exception as e:
        print(f"    [PERPLEXITY] Failed for {track} R{race_number}: {e}", file=sys.stderr)
        return ""


def build_perplexity_queries(
    track: str,
    race_number: int,
    race_name: str,
    distance: int | None,
    race_class: str | None,
    field_lines: str,
    date_formatted: str,
) -> list[tuple[str, str, str]]:
    """
    Build 4 targeted Perplexity queries, each probing a different source ecosystem.
    Returns list of (query_text, system_prompt, label) tuples.
    """
    race_header = (
        f"Track: {track}\n"
        f"Race {race_number}: {race_name}\n"
        f"Distance: {distance or '?'}m | Class: {race_class or '?'}\n"
        f"Date: {date_formatted}\n\n"
        f"Runners:\n{field_lines}"
    )

    queries = [
        (
            f'"{race_name}" "{track}" tips best bets newspaper {date_formatted}\n\n'
            f"Find horse racing tips from NEWSPAPER RACING WRITERS for this race:\n\n"
            f"{race_header}\n\n"
            f"Search specifically for:\n"
            f"- Herald Sun racing tips (Matt Stewart, Shane Crawford)\n"
            f"- Daily Telegraph racing (Ray Thomas, Max Presnell)\n"
            f"- Courier-Mail racing (Nathan Exelby)\n"
            f"- The Advertiser racing tips\n"
            f"- The West Australian racing\n"
            f"- AAP racing preview\n"
            f"- Sydney Morning Herald racing\n"
            f"- The Age racing tips\n\n"
            f"For each horse tipped, name the specific journalist/writer and their publication.\n"
            f"Only include horses from the runners list above.",

            "You are a specialist in Australian newspaper racing coverage. "
            "Search thoroughly for tips from print media racing writers — "
            "Herald Sun, Daily Telegraph, Courier-Mail, The Advertiser, The West Australian, "
            "SMH, The Age. Name every journalist and publication you find.",

            "newspaper",
        ),

        (
            f'"{race_name}" "{track}" preview tips form guide {date_formatted}\n\n'
            f"Find horse racing tips from RACING WEBSITES AND FORM ANALYSTS for this race:\n\n"
            f"{race_header}\n\n"
            f"Search specifically for:\n"
            f"- Racenet race preview and tips\n"
            f"- Punters.com.au tips and form guide\n"
            f"- Racing.com preview and tips\n"
            f"- Just Horse Racing tips\n"
            f"- The Races preview\n"
            f"- Sportsbetting.com.au tips\n"
            f"- Tipping.com.au picks\n"
            f"- Back A Winner tips\n"
            f"- Best Bets racing tips\n\n"
            f"For each horse tipped, name the specific analyst/tipster and their website.\n"
            f"Only include horses from the runners list above.",

            "You are an expert on Australian racing website tipsters. "
            "Search thoroughly for tips from racing portals — "
            "Racenet, Punters.com.au, Racing.com, Just Horse Racing, The Races, "
            "Sportsbetting.com.au, Tipping.com.au. Name every analyst you find.",

            "portal",
        ),

        (
            f'"{race_name}" "{track}" ratings speed map sectionals {date_formatted}\n\n'
            f"Find RATINGS-BASED and SPEED/DATA tips for this race:\n\n"
            f"{race_header}\n\n"
            f"Search specifically for:\n"
            f"- Timeform Australia ratings and tips\n"
            f"- Racing Post ratings/predictions for Australian racing\n"
            f"- Proform Racing speed maps and sectional analysis\n"
            f"- Racing and Sports computer ratings\n"
            f"- Champion Picks data-driven selections\n"
            f"- Dynamic Odds model picks\n"
            f"- Any speed map analysis or sectional timing predictions\n\n"
            f"For each horse selected, note the data/rating methodology used.\n"
            f"Only include horses from the runners list above.",

            "You are a specialist in quantitative horse racing analysis in Australia. "
            "Search for ratings-based picks (Timeform, Racing Post, computer models), "
            "speed maps, sectional analysis, and data-driven selections. "
            "Name every analyst and their methodology.",

            "data",
        ),

        (
            f'"{race_name}" "{track}" tips twitter stable reports trackwork {date_formatted}\n\n'
            f"Find SOCIAL MEDIA, INSIDER, and COMMUNITY tips for this race:\n\n"
            f"{race_header}\n\n"
            f"Search specifically for:\n"
            f"- Twitter/X horse racing tipsters mentioning this race\n"
            f"- Stable reports and trackwork updates\n"
            f"- Racing forum discussions and NAPs\n"
            f"- Telegram racing tip channels\n"
            f"- Sky Racing TV tipster picks\n"
            f"- RSN radio racing tips\n"
            f"- Punter community consensus and polls\n"
            f"- Betting exchange (Betfair) market movers\n\n"
            f"For each horse mentioned, name the specific account/source.\n"
            f"Only include horses from the runners list above.",

            "You are an expert on Australian horse racing social media, insider tips, "
            "and community sentiment. Search for Twitter/X racing accounts, stable reports, "
            "trackwork intelligence, racing forum consensus, Sky Racing and RSN tipsters, "
            "and Betfair market movements. Name every source you find.",

            "social",
        ),
    ]

    return queries


def search_race_tips_perplexity_multi(
    track: str,
    race_number: int,
    race_name: str,
    distance: int | None,
    race_class: str | None,
    field: list[dict],
    field_names: list[str],
    date_str: str,
    date_formatted: str,
    usage: dict,
) -> tuple[str, int, dict[str, int]]:
    """
    Multi-query Perplexity search: 4 targeted queries per race.
    Returns (merged_text, total_citations, per_query_source_counts).
    """
    perplexity_key = os.getenv("PERPLEXITY_API_KEY")
    if not perplexity_key:
        print("    [PERPLEXITY_MULTI] No API key — falling back", file=sys.stderr)
        return "", 0, {}

    field_lines_list = []
    for r in field[:14]:
        if isinstance(r, dict):
            horse = r.get("horse", r.get("horse_name", "?"))
            barrier = r.get("draw", r.get("barrier", "?"))
            jockey = r.get("jockey", "?")
            trainer = r.get("trainer", "?")
            odds = r.get("marketOdds", r.get("odds", "?"))
        else:
            horse = r
            barrier = jockey = trainer = odds = "?"
        field_lines_list.append(f"  {horse} (barrier {barrier}, jockey: {jockey}, trainer: {trainer}, odds: ${odds})")
    field_lines = chr(10).join(field_lines_list)

    queries = build_perplexity_queries(
        track, race_number, race_name, distance, race_class,
        field_lines, date_formatted,
    )

    section_labels = {
        "newspaper": "=== NEWSPAPER & MEDIA TIPS ===",
        "portal": "=== RACING WEBSITE ANALYSTS ===",
        "data": "=== RATINGS / SPEED / DATA ===",
        "social": "=== SOCIAL / INSIDER / COMMUNITY ===",
    }

    merged_parts = []
    all_citations = set()
    per_query_counts: dict[str, int] = {}

    for query_text, system_prompt, label in queries:
        try:
            usage_key = f"perplexity_{label}"
            usage[usage_key] = usage.get(usage_key, 0) + 1
            total_pplx = sum(v for k, v in usage.items() if k.startswith("perplexity_"))
            if total_pplx > 200:
                print(f"    [PERPLEXITY_MULTI] Soft warning: {total_pplx} total Perplexity calls", file=sys.stderr)

            print(f"    [PERPLEXITY_MULTI] {label}: {track} R{race_number}...", file=sys.stderr, flush=True)

            response = _requests.post(
                "https://api.perplexity.ai/chat/completions",
                headers={
                    "Authorization": f"Bearer {perplexity_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": "sonar-pro",
                    "messages": [
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": query_text},
                    ],
                    "return_citations": True,
                    "search_recency_filter": "week",
                    "temperature": 0.1,
                },
                timeout=60,
            )

            if response.status_code != 200:
                print(f"    [PERPLEXITY_MULTI] {label} error {response.status_code}", file=sys.stderr)
                per_query_counts[label] = 0
                continue

            data = response.json()
            content = data["choices"][0]["message"]["content"]
            citations = data.get("citations", [])

            for c in citations:
                if isinstance(c, str):
                    all_citations.add(c)
                elif isinstance(c, dict):
                    all_citations.add(c.get("url", ""))

            per_query_counts[label] = len(citations)
            print(
                f"    [PERPLEXITY_MULTI] {label}: {len(content)} chars, {len(citations)} citations",
                file=sys.stderr,
            )

            merged_parts.append(f"{section_labels.get(label, label)}\n{content}")

            time.sleep(1)

        except _requests.exceptions.Timeout:
            print(f"    [PERPLEXITY_MULTI] {label} timeout — skipping", file=sys.stderr)
            per_query_counts[label] = 0
        except Exception as e:
            print(f"    [PERPLEXITY_MULTI] {label} failed: {e}", file=sys.stderr)
            per_query_counts[label] = 0

    merged_text = "\n\n".join(merged_parts)
    total_citations = len(all_citations)

    print(
        f"    [PERPLEXITY_MULTI] {track} R{race_number} TOTAL: "
        f"{total_citations} unique citations across {len(merged_parts)} queries",
        file=sys.stderr,
    )

    _save_usage(date_str, usage)

    return merged_text, total_citations, per_query_counts


def claude_research_race(
    anthropic_client,
    track: str,
    race_number: int,
    race_name: str,
    distance: int | None,
    race_class: str | None,
    field: list[dict],
    field_names: list[str],
    date_str: str,
    panel_sources_checked: list[str],
    usage: dict,
    dry_run: bool = False,
) -> dict:
    """
    V4: Uses Perplexity to search the web (separate quota, no rate limits),
    then Claude extracts structured picks from the summary.

    This completely eliminates the Anthropic rate limit bottleneck.
    Perplexity = web search. Claude = structured extraction only.
    """

    if dry_run:
        print(f"    [DRY-RUN] Would research {track} R{race_number} via Perplexity + Claude extraction", file=sys.stderr)
        return {"horses_found": [], "total_sources_checked": 0, "search_summary": "dry-run"}

    try:
        date_formatted = datetime.strptime(date_str, "%Y-%m-%d").strftime("%A %d %B %Y")
    except ValueError:
        date_formatted = date_str

    perplexity_summary, citation_count, query_counts = search_race_tips_perplexity_multi(
        track, race_number, race_name, distance, race_class,
        field, field_names, date_str, date_formatted, usage,
    )

    if not perplexity_summary:
        print(f"    [RESEARCH] {track} R{race_number}: Perplexity returned no data", file=sys.stderr)
        return {"horses_found": [], "total_sources_checked": 0, "search_summary": "no data from perplexity"}

    if not _check_cap(usage, "claude", DAILY_CLAUDE_CAP):
        print(f"    [RESEARCH] Claude cap reached — returning Perplexity raw without extraction", file=sys.stderr)
        return {"horses_found": [], "total_sources_checked": citation_count, "search_summary": perplexity_summary[:200]}

    extract_prompt = f"""Extract structured horse racing tip data from this multi-source research summary.
The research covers {citation_count} unique sources across newspaper media, racing websites, ratings/data analysts, and social/insider channels.

Runners in this race: {', '.join(field_names)}

Research summary:
{perplexity_summary}

For each horse from the field that is mentioned as a tip or recommendation, extract:
- horse_name: must exactly match one name from the runners list above
- total_mentions: how many sources/analysts mentioned this horse
- independent_mentions: mentions from non-bookmaker sources (blogs, analysts, form guides)
- commercial_mentions: mentions from bookmakers or their tip content (TAB, Sportsbet, etc)
- source_names: list of source names that tipped this horse
- source_buckets: list from [form_analyst, speed_analyst, ratings_analyst, stable_watcher, value_analyst, track_specialist, retail_punter, market_reader]
- source_ecosystem: which section the source was found in — one of [newspaper, portal, data, social]
- confidence: WIN | EACH_WAY | ROUGHIE
- tipster_reasoning: max 40 words on WHY sources like this horse
- reasoning_signals: list from [barrier_advantage, wet_track_specialist, class_drop, stable_confidence, jockey_booking, recent_form, fresh_horse, speed_map_advantage, market_support, market_drift, trainer_intent, trackwork_positive, distance_suited, track_specialist, weight_advantage]
- market_alignment: true if market odds are also shortening/supporting this horse
- crowd_score: integer 0-100. crowd_score = (total_mentions / total_sources_checked) x 100. Use the same total_sources_checked denominator for every horse.

Return ONLY valid JSON:
{{
  "horses_found": [...],
  "total_sources_checked": <integer>,
  "search_summary": "<one sentence summary>"
}}

If no horses from the field are mentioned: {{"horses_found": [], "total_sources_checked": 0, "search_summary": "no tips found"}}"""

    try:
        usage["claude"] += 1
        _save_usage(date_str, usage)

        response = anthropic_client.messages.create(
            model=extraction_model(),
            max_tokens=2500,
            thinking=NO_THINKING,
            timeout=90,
            system="You are a data extraction assistant. Extract structured data from racing research. Return ONLY valid JSON. No preamble.",
            messages=[{"role": "user", "content": extract_prompt}],
        )

        text_blocks = [b.text for b in response.content if hasattr(b, "type") and b.type == "text"]
        raw = text_blocks[-1] if text_blocks else ""
        raw = raw.strip()

        if "```json" in raw:
            raw = raw.split("```json", 1)[1]
            if "```" in raw:
                raw = raw.split("```", 1)[0]
            raw = raw.strip()
        elif "```" in raw:
            parts = raw.split("```")
            if len(parts) >= 3:
                raw = parts[1].strip()
            else:
                raw = raw.replace("```", "").strip()

        if raw and raw[0] != "{":
            brace_start = raw.find("{")
            if brace_start >= 0:
                brace_end = raw.rfind("}")
                if brace_end > brace_start:
                    raw = raw[brace_start:brace_end + 1]

        result = json.loads(raw)

        horses = result.get("horses_found", [])
        sources = result.get("total_sources_checked", 0)

        print(
            f"    [RESEARCH] {track} R{race_number}: "
            f"{sources} sources | "
            f"{len(horses)} horses found",
            file=sys.stderr,
        )

        for horse_data in horses:
            mentions = horse_data.get("total_mentions", 0)
            conf = horse_data.get("confidence", "?")
            cs = horse_data.get("crowd_score", 0)
            if mentions > 0:
                print(
                    f"      → {horse_data.get('horse_name', '?')}: "
                    f"{mentions} mentions (crowd_score={cs}), confidence {conf}",
                    file=sys.stderr,
                )

        return result

    except json.JSONDecodeError as e:
        print(f"    [RESEARCH] JSON parse failed {track} R{race_number}: {e}", file=sys.stderr)
        return {"horses_found": [], "total_sources_checked": 0, "search_summary": f"parse error: {e}"}
    except Exception as e:
        print(f"    [RESEARCH] Extraction failed {track} R{race_number}: {e}", file=sys.stderr)
        return {"horses_found": [], "total_sources_checked": 0, "search_summary": f"extraction error: {e}"}


def extract_picks_from_content(
    anthropic_client,
    content: str,
    field_names: list[str],
    track: str,
    race_number: int,
    date_str: str,
    usage: dict,
) -> list[dict]:
    """
    Send raw tipster content to Claude for structured extraction.
    V2: also extracts tipster_reasoning and reasoning_signals.
    """
    if not _check_cap(usage, "claude", DAILY_CLAUDE_CAP):
        return []

    horse_list = ", ".join(field_names)

    prompt = f"""Extract tips for {track} Race {race_number} on {date_str}.
Runners: {horse_list}

For each TIPPED horse extract:
- horse_name: exact name from the runner list above
- confidence: "WIN" if strong tip (best bet, top pick), "EACH_WAY" if mentioned positively, "ROUGHIE" if longshot/value mention
- tipster_reasoning: max 20 words explaining WHY the tipster likes this horse
- reasoning_signals: list from [barrier_advantage, wet_track_specialist, class_drop, stable_confidence, jockey_booking, recent_form, fresh_horse, speed_map_advantage, market_support, market_drift, trainer_intent, trackwork_positive, distance_suited, track_specialist, weight_advantage]

Return JSON: {{"picks": [{{"horse_name": "...", "confidence": "WIN|EACH_WAY|ROUGHIE", "tipster_reasoning": "...", "reasoning_signals": ["..."]}}]}}

If no horses from the field are mentioned as tips, return: {{"picks": []}}

TEXT TO ANALYSE:
{content[:3500]}"""

    try:
        usage["claude"] += 1
        _save_usage(date_str, usage)

        response = anthropic_client.messages.create(
            model=extraction_model(),
            max_tokens=600,
            thinking=NO_THINKING,
            system="You are a data extraction assistant for horse racing. Return ONLY valid JSON.",
            messages=[{"role": "user", "content": prompt}],
        )

        raw = response.content[0].text.strip()
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            cleaned = raw.replace("```json", "").replace("```", "").strip()
            data = json.loads(cleaned)

        return data.get("picks", [])

    except Exception as e:
        print(f"    [EXTRACT] Failed: {e}", file=sys.stderr)
        return []


def extract_picks_from_content_batch(
    anthropic_client,
    pages: list[dict],
    field_names: list[str],
    track: str,
    race_number: int,
    date_str: str,
    usage: dict,
) -> list[dict]:
    """
    Batch-extract tips from multiple panel pages in a single Claude call.
    Each page dict has: content, tipster_name, tipster_id, bucket, url.
    Returns picks tagged with source_name for attribution.
    """
    if not pages:
        return []
    if not _check_cap(usage, "claude", DAILY_CLAUDE_CAP):
        return []

    horse_list = ", ".join(field_names)

    source_blocks = []
    for p in pages:
        source_blocks.append(
            f"=== SOURCE: {p['tipster_name']} ({p['bucket']}) ===\n"
            f"{p['content'][:2000]}"
        )
    combined = "\n\n".join(source_blocks)

    prompt = f"""Extract tips for {track} Race {race_number} on {date_str}.
Runners: {horse_list}

Below are excerpts from {len(pages)} different tipster sources. For each horse that ANY source tips, extract:
- horse_name: exact name from the runner list above
- source_name: which source tipped this horse (must match one of the SOURCE headers)
- confidence: "WIN" if strong tip, "EACH_WAY" if mentioned positively, "ROUGHIE" if longshot
- tipster_reasoning: max 20 words explaining WHY
- reasoning_signals: list from [barrier_advantage, wet_track_specialist, class_drop, stable_confidence, jockey_booking, recent_form, fresh_horse, speed_map_advantage, market_support, market_drift, trainer_intent, trackwork_positive, distance_suited, track_specialist, weight_advantage]

Return JSON: {{"picks": [{{"horse_name": "...", "source_name": "...", "confidence": "WIN|EACH_WAY|ROUGHIE", "tipster_reasoning": "...", "reasoning_signals": ["..."]}}]}}

If a source tips multiple horses, create one entry per horse per source.
If no horses from the field are mentioned: {{"picks": []}}

SOURCES:
{combined}"""

    try:
        usage["claude"] += 1
        _save_usage(date_str, usage)

        response = anthropic_client.messages.create(
            model=extraction_model(),
            max_tokens=1200,
            thinking=NO_THINKING,
            system="You are a data extraction assistant for horse racing. Return ONLY valid JSON.",
            messages=[{"role": "user", "content": prompt}],
        )

        raw = response.content[0].text.strip()
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            cleaned = raw.replace("```json", "").replace("```", "").strip()
            if cleaned and cleaned[0] != "{":
                brace_start = cleaned.find("{")
                if brace_start >= 0:
                    brace_end = cleaned.rfind("}")
                    if brace_end > brace_start:
                        cleaned = cleaned[brace_start:brace_end + 1]
            data = json.loads(cleaned)

        picks = data.get("picks", [])
        print(
            f"    [BATCH_EXTRACT] {len(pages)} sources → {len(picks)} picks",
            file=sys.stderr,
        )
        return picks

    except Exception as e:
        print(f"    [BATCH_EXTRACT] Failed: {e}", file=sys.stderr)
        return []


def calculate_consensus_score(
    raw_mentions: int,
    tipsters_polled: int,
    bucket_count: int,
    high_conf_count: int,
    weighted_sum: float,
    independent_source_rate: float,
) -> float:
    """
    V2 scoring — vote percentage is the primary signal.
    3/5 (60%) is stronger than 3/20 (15%).
    Returns score 0-100. Default 35 for zero mentions.
    """
    if raw_mentions == 0:
        return 35.0

    vote_pct = (raw_mentions / tipsters_polled * 100) if tipsters_polled > 0 else 0

    if vote_pct >= 60:
        vote_score = 55.0
    elif vote_pct >= 40:
        vote_score = 40.0 + (vote_pct - 40) / 20 * 15
    elif vote_pct >= 25:
        vote_score = 25.0 + (vote_pct - 25) / 15 * 15
    elif vote_pct >= 10:
        vote_score = 10.0 + (vote_pct - 10) / 15 * 15
    else:
        vote_score = vote_pct

    diversity_bonus = {0: 0, 1: 5, 2: 12, 3: 20, 4: 23, 5: 25}.get(
        min(bucket_count, 5), 25
    )

    confidence_bonus = min(high_conf_count * 2, 10)

    if independent_source_rate >= 0.9:
        mult = 1.1
    elif independent_source_rate >= 0.7:
        mult = 1.0
    elif independent_source_rate >= 0.5:
        mult = 0.85
    else:
        mult = 0.65

    return min(round((vote_score + diversity_bonus + confidence_bonus) * mult, 2), 100.0)


def check_reasoning_alignment(
    horse: str,
    track: str,
    race_number: int,
    date_str: str,
    reasoning_signals: list[str],
) -> str:
    """
    Cross-reference extracted reasoning signals against STRIDE intelligence files.
    Returns: CONFIRMED | CONTRADICTED | UNVERIFIABLE
    """
    if not reasoning_signals:
        return "UNVERIFIABLE"

    intel_path = INTELLIGENCE_DIR / "output" / date_str
    if not intel_path.exists():
        return "UNVERIFIABLE"

    speed_file = intel_path / "pace_scenario.json"
    form_file = intel_path / "form_deep_dive.json"

    confirmed_count = 0
    contradicted_count = 0

    horse_norm = normalize_name(horse)
    track_key = normalize_track(track)

    if speed_file.exists() and any(s in reasoning_signals for s in [
        "speed_map_advantage", "barrier_advantage", "fresh_horse"
    ]):
        try:
            speed_data = json.loads(speed_file.read_text(encoding="utf-8"))
            for entry in speed_data if isinstance(speed_data, list) else []:
                if (normalize_name(entry.get("horse_name", "")) == horse_norm
                        and normalize_track(entry.get("track", "")) == track_key):
                    if entry.get("pace_advantage"):
                        confirmed_count += 1
                    break
        except (json.JSONDecodeError, OSError):
            pass

    if form_file.exists() and any(s in reasoning_signals for s in [
        "recent_form", "class_drop", "distance_suited", "wet_track_specialist"
    ]):
        try:
            form_data = json.loads(form_file.read_text(encoding="utf-8"))
            for entry in form_data if isinstance(form_data, list) else []:
                if (normalize_name(entry.get("horse_name", "")) == horse_norm
                        and normalize_track(entry.get("track", "")) == track_key):
                    if entry.get("form_trend") in ("improving", "peak"):
                        confirmed_count += 1
                    elif entry.get("form_trend") in ("declining", "poor"):
                        contradicted_count += 1
                    break
        except (json.JSONDecodeError, OSError):
            pass

    if contradicted_count > confirmed_count:
        return "CONTRADICTED"
    elif confirmed_count > 0:
        return "CONFIRMED"
    return "UNVERIFIABLE"


def store_panel_log(conn, log_rows: list[dict]) -> None:
    """Insert panel fetch log entries."""
    if not log_rows:
        return
    with conn.cursor() as cur:
        for row in log_rows:
            cur.execute(
                """INSERT INTO tipster_panel_log
                   (race_date, tipster_id, tipster_name, fetch_url,
                    fetch_status, content_length, picks_extracted, fetch_duration_ms)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s)""",
                (
                    row["race_date"], row["tipster_id"], row["tipster_name"],
                    row.get("fetch_url"), row["fetch_status"],
                    row.get("content_length", 0), row.get("picks_extracted", 0),
                    row.get("fetch_duration_ms", 0),
                ),
            )


def store_mentions_batch(conn, mentions: list[dict]) -> None:
    """Insert raw mentions into consensus_mentions table (V2 columns)."""
    if not mentions:
        return
    with conn.cursor() as cur:
        for m in mentions:
            cur.execute(
                """INSERT INTO consensus_mentions
                   (race_date, track, race_number, horse_name, horse_name_norm,
                    source_url, source_name, source_bucket, confidence_language, raw_snippet,
                    tipster_id, tipster_reasoning, reasoning_signals,
                    is_independent, is_panel_source, quality_multiplier)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
                (
                    m["race_date"], m["track"], m["race_number"],
                    m["horse_name"], normalize_name(m["horse_name"]),
                    m.get("source_url"), m.get("source_name", ""),
                    m.get("source_bucket", "form_analyst"),
                    m.get("confidence_language"),
                    m.get("raw_snippet", "")[:500],
                    m.get("tipster_id"),
                    m.get("tipster_reasoning"),
                    json.dumps(m.get("reasoning_signals")) if m.get("reasoning_signals") else None,
                    m.get("is_independent", True),
                    m.get("is_panel_source", False),
                    m.get("quality_multiplier", 1.0),
                ),
            )


def store_consensus_scores_batch(conn, date_str: str, scores: list[dict]) -> None:
    """Upsert aggregated consensus scores (V2 columns)."""
    if not scores:
        return
    with conn.cursor() as cur:
        for s in scores:
            cur.execute(
                """INSERT INTO consensus_scores
                   (race_date, track, race_number, horse_name, horse_name_norm,
                    total_mentions, bucket_spread, high_confidence_mentions,
                    consensus_score, sources,
                    field_size, tipsters_polled, vote_pct,
                    independent_source_rate, reasoning_alignment)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                   ON CONFLICT (race_date, track, race_number, horse_name_norm)
                   DO UPDATE SET
                    total_mentions = EXCLUDED.total_mentions,
                    bucket_spread = EXCLUDED.bucket_spread,
                    high_confidence_mentions = EXCLUDED.high_confidence_mentions,
                    consensus_score = EXCLUDED.consensus_score,
                    sources = EXCLUDED.sources,
                    field_size = EXCLUDED.field_size,
                    tipsters_polled = EXCLUDED.tipsters_polled,
                    vote_pct = EXCLUDED.vote_pct,
                    independent_source_rate = EXCLUDED.independent_source_rate,
                    reasoning_alignment = EXCLUDED.reasoning_alignment,
                    updated_at = NOW()""",
                (
                    date_str, s["track"], s["race_number"],
                    s["horse_name"], normalize_name(s["horse_name"]),
                    s["total_mentions"], s["bucket_spread"],
                    s["high_confidence_mentions"],
                    s["consensus_score"],
                    json.dumps(s.get("sources", [])),
                    s.get("field_size", 0),
                    s.get("tipsters_polled", 0),
                    s.get("vote_pct", 0),
                    s.get("independent_source_rate", 1.0),
                    s.get("reasoning_alignment"),
                ),
            )


def _write_output(date_str: str, data: dict) -> None:
    output_path = INTELLIGENCE_DIR / f"consensus_{date_str}.json"
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(data, indent=2, default=str))
    print(f"[CONSENSUS] Written to {output_path}", file=sys.stderr)


def build_health(date_str: str, results_by_race: dict, usage: dict,
                 panel_log: list, db_ok: bool, db_error: str | None,
                 dry_run: bool = False) -> dict:
    """Run health, so a broken day is distinguishable from a quiet one.

    The agent used to exit 0 whether it found 400 mentions or none at all, which
    is what let a retired model id corrupt six weeks of accrual unnoticed.
    """
    races = len(results_by_race)
    horses = sum(len(r) for r in results_by_race.values() if isinstance(r, dict))
    mentions = sum(
        d.get("total_mentions", 0)
        for r in results_by_race.values() if isinstance(r, dict)
        for d in r.values() if isinstance(d, dict)
    )
    scored = sum(
        1
        for r in results_by_race.values() if isinstance(r, dict)
        for d in r.values() if isinstance(d, dict) and d.get("total_mentions", 0) > 0
    )
    ok = sum(1 for e in panel_log if e.get("fetch_status") == "SUCCESS")
    return {
        "race_date": date_str,
        "dry_run": dry_run,
        "extraction_model": extraction_model(),
        "races": races,
        "horses_scored": horses,
        "horses_with_mentions": scored,
        "total_mentions": mentions,
        "panel_fetch_success": ok,
        "panel_fetch_attempted": len(panel_log),
        "api_calls": dict(usage),
        "db_mirror_ok": db_ok,
        "db_mirror_error": db_error,
        # The condition that must never again pass silently. A dry run makes no
        # extraction calls, so zero mentions is its expected result, not a fault.
        "zero_yield": (not dry_run) and races > 0 and mentions == 0,
    }


def _write_health(date_str: str, health: dict) -> None:
    """Sibling file, not a key inside consensus_<date>.json.

    consensus_blender.load_consensus_intelligence treats every top-level key as
    a race, so a health block inside the artifact would be counted as one. The
    consumed file's shape stays byte-identical.
    """
    path = INTELLIGENCE_DIR / f"consensus_{date_str}.health.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(health, indent=2, default=str))
    print(f"[CONSENSUS] Health written to {path}", file=sys.stderr)


# Connection-level failures worth redialling for. Empty when psycopg2 is
# absent (except () catches nothing, and every error lands in the generic
# branch instead).
_CONN_ERRORS = ((psycopg2.OperationalError, psycopg2.InterfaceError)
               if psycopg2 is not None else ())


def _store_with_retry(fn, conn_factory, *args, attempts: int = 3, base_delay: float = 2.0):
    """Retry a DB mirror write, redialling on connection-level failure.

    These were fire-and-forget: a transient Neon SSL drop lost the whole
    day's mirror while the JSON still said everything was fine. The first
    retry re-CALLED fn with the same connection object passed as an argument
    — a dropped connection is dead, so attempts 2 and 3 could only ever
    raise InterfaceError: connection already closed (2026-08-06). The retry
    now owns the connection: it opens one lazily from conn_factory, and any
    OperationalError/InterfaceError discards it so the next attempt gets a
    live socket. Raises the last error if every attempt fails."""
    conn, last = None, None
    try:
        for i in range(attempts):
            try:
                if conn is None or getattr(conn, "closed", 0):
                    conn = conn_factory()
                return fn(conn, *args)
            except _CONN_ERRORS as e:
                last = e
                try:
                    conn.close()
                except Exception:
                    pass
                conn = None      # the point: next attempt gets a live socket
            except Exception as e:
                last = e
            if i < attempts - 1:
                print(
                    f"[CONSENSUS] DB write attempt {i + 1}/{attempts} failed "
                    f"({type(last).__name__}); retrying",
                    file=sys.stderr,
                )
                time.sleep(base_delay * (2 ** i))
        raise last
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass


def run_consensus_agent(
    date_str: str,
    track_filter: str | None = None,
    dry_run: bool = False,
    max_races: int | None = None,
) -> dict:
    """
    V2 main entry point.
    Panel fetch → quality gate → Claude extraction → AI fallback → scoring.
    """
    from dotenv import load_dotenv
    load_dotenv()

    tavily_key = os.getenv("TAVILY_API_KEY", "").strip()
    anthropic_key = os.getenv("ANTHROPIC_API_KEY", "").strip()

    if not tavily_key:
        print("[CONSENSUS] ERROR: TAVILY_API_KEY not set in .env", file=sys.stderr)
        return {}
    if not anthropic_key:
        print("[CONSENSUS] ERROR: ANTHROPIC_API_KEY not set in .env", file=sys.stderr)
        return {}

    # Presence check only — no connection is opened here, or anywhere before
    # the write site. A missing DATABASE_URL used to crash at startup; the
    # late-connection change moved it to AFTER ~11 minutes of paid research.
    # Fail on the misconfiguration before spending a cent, not after.
    if not dry_run and not os.getenv("DATABASE_URL", "").strip():
        raise RuntimeError(
            "DATABASE_URL is not set — the DB mirror cannot run. Failing "
            "before any research spend: discovering this at the write site "
            "costs a full day's LLM budget.")

    from tavily import TavilyClient
    import anthropic

    tavily_client = TavilyClient(api_key=tavily_key)
    anthropic_client = anthropic.Anthropic(api_key=anthropic_key)

    # Preflight runs in --dry-run too. It is a 4-token call, and the whole point
    # of a dry run is to find out whether today's run would work; skipping the
    # one check that catches the failure mode this repair exists for would
    # defeat that.
    _model = extraction_model()
    preflight_extraction_model(anthropic_client, _model)
    print(f"[CONSENSUS] Extraction model OK: {_model}", file=sys.stderr)

    try:
        meetings = load_racecard_meetings(date_str)
    except FileNotFoundError:
        print(f"[CONSENSUS] No racecard found for {date_str}. Writing empty consensus file.", file=sys.stderr)
        _write_output(date_str, {})
        return {}

    races = flatten_races(meetings)
    if not races:
        print(f"[CONSENSUS] No runners for {date_str}. Writing empty consensus file.", file=sys.stderr)
        _write_output(date_str, {})
        return {}

    if track_filter:
        track_norm = normalize_track(track_filter)
        races = [r for r in races if r["track_key"] == track_norm]
        if not races:
            print(f"[CONSENSUS] No races for track filter '{track_filter}'", file=sys.stderr)
            _write_output(date_str, {})
            return {}

    if max_races and len(races) > max_races:
        races = races[:max_races]

    print(f"[CONSENSUS] Processing {len(races)} races for {date_str}", file=sys.stderr)

    usage = _load_usage(date_str)

    panel = load_tipster_panel()
    tipsters_polled = len([
        s for s in panel.get("sources", [])
        if s.get("active") and s.get("verified")
    ])
    print(f"[PANEL] {tipsters_polled} active+verified tipsters in panel", file=sys.stderr)

    accuracy_multipliers = load_accuracy_multipliers()
    panel_pages, panel_log_rows = fetch_panel_pages(
        tavily_client, panel, date_str, usage, dry_run=dry_run,
        accuracy_multipliers=accuracy_multipliers,
    )

    results_by_race = {}
    all_mentions = []
    all_scores = []

    # No DB connection is opened ahead of the research loop below: it runs
    # ~11 minutes of web research, and Neon reaps idle connections, so a
    # connection opened here is dead by the first write (2026-08-06). The
    # panel log opens and closes its own short-lived connection instead.
    if not dry_run and panel_log_rows:
        panel_conn = None
        try:
            panel_conn = get_connection()
            store_panel_log(panel_conn, panel_log_rows)
        except Exception as e:
            print(f"[PANEL] Panel log write error: {e}", file=sys.stderr)
        finally:
            if panel_conn is not None:
                try:
                    panel_conn.close()
                except Exception:
                    pass

    for race in races:
        track = race["track"]
        track_key = race["track_key"]
        race_number = race["race_number"]
        race_name = race.get("race_name") or f"Race {race_number}"
        distance = race.get("distance_m")
        race_class = race.get("race_class")
        runners = race["runners"]
        field_names = [r.get("horse", "") for r in runners if r.get("horse")]

        if not field_names:
            continue

        race_key = f"{track_key}_R{race_number}"
        field_size = len(field_names)
        print(f"  {track} R{race_number} ({field_size} runners)", file=sys.stderr)

        all_picks_this_race: list[dict] = []
        sources_for_race: set[str] = set()

        usable_pages = []
        for page in panel_pages:
            usable, reason = is_content_usable(page["content"], page["url"], field_names)
            if usable:
                usable_pages.append(page)

        BATCH_SIZE = 5
        for batch_start in range(0, len(usable_pages), BATCH_SIZE):
            batch = usable_pages[batch_start:batch_start + BATCH_SIZE]

            picks = extract_picks_from_content_batch(
                anthropic_client, batch, field_names,
                track, race_number, date_str, usage,
            )

            page_lookup = {p["tipster_name"]: p for p in batch}

            for pick in picks:
                extracted_name = pick.get("horse_name", "")
                matched_name = match_horse_to_field(extracted_name, field_names)
                if not matched_name:
                    continue

                source_name = pick.get("source_name", "")
                source_page = page_lookup.get(source_name)
                if not source_page:
                    best_ratio = 0
                    for pname, pdata in page_lookup.items():
                        ratio = SequenceMatcher(None, source_name.lower(), pname.lower()).ratio()
                        if ratio > best_ratio:
                            best_ratio = ratio
                            source_page = pdata
                    if best_ratio < 0.6:
                        source_page = batch[0]

                tipster_name = source_page["tipster_name"]
                tipster_id = source_page.get("tipster_id")
                bucket = source_page["bucket"]
                url = source_page["url"]
                q_mult = source_page.get("quality_multiplier", 1.0)

                confidence = pick.get("confidence", "EACH_WAY")
                conf_lang = "HIGH" if confidence == "WIN" else "MEDIUM" if confidence == "EACH_WAY" else "LOW"

                pick_data = {
                    "horse_name": matched_name,
                    "confidence_language": conf_lang,
                    "confidence": confidence,
                    "reason": pick.get("tipster_reasoning"),
                    "reasoning_signals": pick.get("reasoning_signals", []),
                    "bucket": bucket,
                    "source_url": url,
                    "source_name": tipster_name,
                    "tipster_id": tipster_id,
                    "is_panel_source": True,
                    "is_independent": True,
                    "quality_multiplier": q_mult,
                }
                all_picks_this_race.append(pick_data)
                sources_for_race.add(tipster_name)

                all_mentions.append({
                    "race_date": date_str,
                    "track": track,
                    "race_number": race_number,
                    "horse_name": matched_name,
                    "source_url": url,
                    "source_name": tipster_name,
                    "source_bucket": bucket,
                    "confidence_language": conf_lang,
                    "raw_snippet": pick.get("tipster_reasoning", ""),
                    "tipster_id": tipster_id,
                    "tipster_reasoning": pick.get("tipster_reasoning"),
                    "reasoning_signals": pick.get("reasoning_signals", []),
                    "is_independent": True,
                    "is_panel_source": True,
                    "quality_multiplier": q_mult,
                })

        panel_source_names = list(sources_for_race)
        research = claude_research_race(
            anthropic_client, track, race_number, race_name,
            distance, race_class, runners, field_names,
            date_str, panel_source_names, usage, dry_run=dry_run,
        )

        # Rate-limit pacing: wait between races to stay under 30K tokens/min
        if not dry_run:
            time.sleep(5)

        claude_source_count = 0
        research_crowd_scores = {}
        research_independent = {}
        research_commercial = {}
        research_market_align = {}

        for horse_data in research.get("horses_found", []):
            matched_name = match_horse_to_field(
                horse_data.get("horse_name", ""), field_names
            )
            if not matched_name:
                continue

            n_total = horse_data.get("total_mentions", 0)
            n_independent = horse_data.get("independent_mentions", 0)
            n_commercial = horse_data.get("commercial_mentions", 0)
            # Defensive cast — Claude may return string instead of int
            try:
                crowd_score_val = float(horse_data.get("crowd_score", 0))
            except (TypeError, ValueError):
                crowd_score_val = 0
            research_crowd_scores[matched_name] = crowd_score_val
            research_independent[matched_name] = n_independent
            research_commercial[matched_name] = n_commercial
            research_market_align[matched_name] = bool(horse_data.get("market_alignment", False))

            source_names = horse_data.get("source_names", [])
            source_buckets = horse_data.get("source_buckets", [])
            confidence = horse_data.get("confidence", "EACH_WAY")
            conf_lang = (
                "HIGH" if confidence == "WIN"
                else "MEDIUM" if confidence == "EACH_WAY"
                else "LOW"
            )

            for i in range(n_total):
                src_name = source_names[i] if i < len(source_names) else f"web_source_{i+1}"
                src_bucket = source_buckets[i] if i < len(source_buckets) else "form_analyst"
                is_ind = i < n_independent  # first N are independent, rest commercial
                q_mult = 1.0 if is_ind else 0.8

                pick_data = {
                    "horse_name": matched_name,
                    "confidence_language": conf_lang,
                    "confidence": confidence,
                    "reason": horse_data.get("tipster_reasoning"),
                    "reasoning_signals": horse_data.get("reasoning_signals", []),
                    "bucket": src_bucket,
                    "source_url": None,
                    "source_name": f"claude_research:{src_name}",
                    "tipster_id": None,
                    "is_panel_source": False,
                    "is_independent": is_ind,
                    "quality_multiplier": q_mult,
                }
                all_picks_this_race.append(pick_data)

                all_mentions.append({
                    "race_date": date_str,
                    "track": track,
                    "race_number": race_number,
                    "horse_name": matched_name,
                    "source_url": None,
                    "source_name": f"claude_research:{src_name}",
                    "source_bucket": src_bucket,
                    "confidence_language": conf_lang,
                    "raw_snippet": horse_data.get("tipster_reasoning", ""),
                    "tipster_id": None,
                    "tipster_reasoning": horse_data.get("tipster_reasoning"),
                    "reasoning_signals": horse_data.get("reasoning_signals", []),
                    "is_independent": is_ind,
                    "is_panel_source": False,
                    "quality_multiplier": q_mult,
                })
                claude_source_count += 1

        total_research_sources = research.get("total_sources_checked", 0)
        tipsters_polled_adjusted = tipsters_polled + total_research_sources

        race_results = {}
        for horse in field_names:
            horse_mentions = [p for p in all_picks_this_race if p["horse_name"] == horse]
            buckets_hit = set(m["bucket"] for m in horse_mentions)
            high_conf = sum(1 for m in horse_mentions if m["confidence_language"] == "HIGH")
            weighted_sum = sum(
                BUCKET_WEIGHTS.get(m["bucket"], 1.0) * m.get("quality_multiplier", 1.0)
                for m in horse_mentions
            )
            panel_count = sum(1 for m in horse_mentions if m.get("is_panel_source"))
            total = len(horse_mentions)
            independent_rate = (
                sum(1 for m in horse_mentions if m.get("is_independent", True)) / total
                if total > 0 else 1.0
            )

            all_signals = []
            for m in horse_mentions:
                all_signals.extend(m.get("reasoning_signals", []))

            reasoning_alignment = check_reasoning_alignment(
                horse, track, race_number, date_str, all_signals
            )

            _tp = tipsters_polled_adjusted if tipsters_polled_adjusted > 0 else tipsters_polled
            vote_pct = (total / _tp * 100) if _tp > 0 else 0

            score = calculate_consensus_score(
                total, _tp, len(buckets_hit),
                high_conf, weighted_sum, independent_rate,
            )

            horse_crowd_score = research_crowd_scores.get(horse, 0)
            if horse_crowd_score == 0 and panel_count > 0:
                horse_crowd_score = round(panel_count / _tp * 100, 1) if _tp > 0 else 0
            ind_mentions = (
                sum(1 for m in horse_mentions if m.get("is_independent", True) and not m.get("is_panel_source"))
            )
            com_mentions = (
                sum(1 for m in horse_mentions if not m.get("is_independent", True) and not m.get("is_panel_source"))
            )
            ind_mentions += panel_count
            market_aligned = research_market_align.get(horse, False)

            race_results[horse] = {
                "consensus_score": score,
                "crowd_score": horse_crowd_score,
                "vote_pct": round(vote_pct, 1),
                "total_mentions": total,
                "independent_mentions": ind_mentions,
                "commercial_mentions": com_mentions,
                "market_alignment": market_aligned,
                "tipsters_polled": _tp,
                "bucket_spread": len(buckets_hit),
                "high_confidence_mentions": high_conf,
                "buckets": list(buckets_hit),
                "independent_source_rate": round(independent_rate, 3),
                "reasoning_alignment": reasoning_alignment,
                "sources": list(set(m["source_name"] for m in horse_mentions)),
            }

            all_scores.append({
                "track": track,
                "race_number": race_number,
                "horse_name": horse,
                "total_mentions": total,
                "independent_mentions": ind_mentions,
                "commercial_mentions": com_mentions,
                "bucket_spread": len(buckets_hit),
                "high_confidence_mentions": high_conf,
                "consensus_score": score,
                "crowd_score": horse_crowd_score,
                "market_alignment": market_aligned,
                "sources": list(set(m["source_name"] for m in horse_mentions)),
                "field_size": field_size,
                "tipsters_polled": _tp,
                "vote_pct": round(vote_pct, 1),
                "independent_source_rate": round(independent_rate, 3),
                "reasoning_alignment": reasoning_alignment,
            })

        results_by_race[race_key] = race_results

        tipped = [h for h, d in race_results.items() if d["total_mentions"] > 0]
        if tipped:
            top = max(tipped, key=lambda h: race_results[h]["consensus_score"])
            top_data = race_results[top]
            print(
                f"    {len(tipped)} tipped | top: {top} "
                f"(score={top_data['consensus_score']}, vote={top_data['vote_pct']}%, "
                f"mentions={top_data['total_mentions']}, "
                f"buckets={top_data['bucket_spread']}, "
                f"alignment={top_data['reasoning_alignment']})",
                file=sys.stderr,
            )
        else:
            print(f"    No tips found for this race", file=sys.stderr)

    db_ok, db_error = True, None
    if not dry_run:
        # The connection is opened here, at first use, by the retry's
        # factory — and re-opened if a write kills it. Nothing has been
        # held open across the research phase.
        try:
            _store_with_retry(store_mentions_batch, get_connection, all_mentions)
            _store_with_retry(store_consensus_scores_batch, get_connection,
                              date_str, all_scores)
            print(
                f"[CONSENSUS] DB: {len(all_mentions)} mentions, {len(all_scores)} scores stored",
                file=sys.stderr,
            )
        except Exception as e:
            db_ok, db_error = False, f"{type(e).__name__}: {e}"
            print(f"[CONSENSUS] DB write error after retries: {e}", file=sys.stderr)

    # A dry run must not touch the live artifact. It used to: _write_output ran
    # unconditionally, so `consensus_agent.py <date> --dry-run` — the documented
    # /stride-full-dry step — silently overwrote a real consensus file with
    # all-zero scores. The file is gitignored and has no DB mirror on days the
    # mirror failed, so that was an unrecoverable clobber of the only copy.
    if dry_run:
        print(
            f"[CONSENSUS] DRY RUN: not writing consensus_{date_str}.json "
            f"({len(results_by_race)} races would be written)",
            file=sys.stderr,
        )
    else:
        _write_output(date_str, results_by_race)

    health = build_health(date_str, results_by_race, usage, panel_log_rows,
                          db_ok, db_error, dry_run=dry_run)
    if not dry_run:
        _write_health(date_str, health)
    LAST_RUN_HEALTH.clear()
    LAST_RUN_HEALTH.update(health)

    print(
        f"[CONSENSUS] Complete. API calls: Tavily={usage.get('tavily', 0)}, Claude={usage.get('claude', 0)} | "
        f"mentions={health['total_mentions']} across {health['races']} races "
        f"({health['horses_with_mentions']}/{health['horses_scored']} horses)",
        file=sys.stderr,
    )
    return results_by_race


def run_panel_only(date_str: str) -> int:
    """Fetch the tipster panel and report per source. Returns an exit code.

    The panel is one of consensus's two inputs and there was no way to
    exercise it on its own. --dry-run cannot: it returns before Tavily is
    called, so it proves the list parses and nothing else. A full run does
    fetch, but costs a day's Perplexity and Claude budget and writes to the
    database to find out, which is not a test anyone runs twice.

    So: real Tavily extracts, no races, no Perplexity, no Claude, no
    database connection, no consensus file. Two things can be wrong and
    they need different answers, so they exit differently — the panel file
    missing from the image is exit 6, and sources that no longer extract
    are exit 1. Not 2: argparse already exits 2 on a bad command line, and
    a reader debugging a red job should not have to tell those apart. The URLs were last edited 2026-04-10; tip pages move.
    """
    try:
        panel = load_tipster_panel()
    except PanelUnavailable as e:
        print(f"[PANEL-PROOF] FATAL: {e}", file=sys.stderr)
        return 6
    active = [s for s in panel.get("sources", [])
              if s.get("active") and s.get("verified")]
    print(f"[PANEL-PROOF] {len(panel.get('sources', []))} sources in file, "
          f"{len(active)} active+verified", file=sys.stderr)
    if not active:
        print("[PANEL-PROOF] FATAL: no active+verified sources. In the cloud "
              "this is almost certainly the file missing from the image — "
              "server/python/tipster_panel.json is in .gitignore, so it is "
              "not in the build context and load_tipster_panel() returns "
              "empty with only a stderr line to say so.", file=sys.stderr)
        return 6

    tavily_key = os.getenv("TAVILY_API_KEY")
    if not tavily_key:
        print("[PANEL-PROOF] FATAL: TAVILY_API_KEY not set", file=sys.stderr)
        return 6
    from tavily import TavilyClient

    usage = _load_usage(date_str)
    _, rows = fetch_panel_pages(
        TavilyClient(api_key=tavily_key), panel, date_str, usage,
        dry_run=False, accuracy_multipliers=None,
    )

    ok = [r for r in rows if r["fetch_status"] == "SUCCESS"]
    print(f"\n[PANEL-PROOF] {len(ok)}/{len(rows)} sources returned usable "
          f"content", file=sys.stderr)
    for r in sorted(rows, key=lambda x: (x["fetch_status"], x["tipster_name"])):
        print(f"  {r['fetch_status']:8} {r['tipster_name'][:32]:34} "
              f"{r['content_length']:>8,} chars  {r['fetch_url'][:60]}",
              file=sys.stderr)
    # Measured 2026-08-06 from a machine with the panel present: 3/16.
    # Racenet is a plain 404; racing.com, Racing and Sports and Timeform all
    # refuse the fetch, and extract_depth="advanced" changes none of it — the
    # URLs are stale or the sites now block extraction. The file was last
    # edited 2026-04-10.
    #
    # A floor rather than "all failed", because all-failed is the only case
    # the old shape could catch, and 3/16 is not a working panel. Half is a
    # starting line, not a measured optimum: move it when there is a number
    # worth moving it to.
    FLOOR = 0.5
    if len(ok) < FLOOR * len(rows):
        print(f"[PANEL-PROOF] FATAL: {len(ok)}/{len(rows)} usable is below "
              f"the {FLOOR:.0%} floor. Consensus still runs — the panel is a "
              f"quality input, not a hard dependency — so this will not stop "
              f"a race day. It means the tipster half of consensus is mostly "
              f"absent and the scores are Perplexity-only.", file=sys.stderr)
        return 1
    return 0


def main():
    parser = argparse.ArgumentParser(description="STRIDE Consensus Agent V2")
    parser.add_argument("date", help="Race date YYYY-MM-DD")
    parser.add_argument("--track", default=None, help="Filter to specific track")
    parser.add_argument("--dry-run", action="store_true", help="Print plan without executing API calls")
    parser.add_argument("--max-races", type=int, default=None, help="Limit number of races")
    parser.add_argument("--panel-only", action="store_true",
                        help="Fetch the tipster panel and report; no races, "
                             "no Perplexity/Claude, no DB writes")
    args = parser.parse_args()

    if args.panel_only:
        sys.exit(run_panel_only(args.date))

    try:
        run_consensus_agent(
            date_str=args.date,
            track_filter=args.track,
            dry_run=args.dry_run,
            max_races=args.max_races,
        )
    except ExtractionModelUnavailable as e:
        print(f"[CONSENSUS] FATAL: {e}", file=sys.stderr)
        sys.exit(3)
    except PanelUnavailable as e:
        # 6, the same code --panel-only uses for "panel absent", so one
        # number means one thing however consensus was invoked.
        print(f"[CONSENSUS] FATAL: {e}", file=sys.stderr)
        sys.exit(6)

    # Exit contract. A day that scored races but found nothing is a broken run,
    # not a quiet one, and the pipeline must be able to tell: without a non-zero
    # exit here, six weeks of 404s looked exactly like six weeks of quiet news.
    h = LAST_RUN_HEALTH
    if h.get("zero_yield"):
        print(
            f"[CONSENSUS] FATAL: {h['races']} races scored, zero mentions extracted. "
            f"Treating as a failed run, not a quiet day.",
            file=sys.stderr,
        )
        sys.exit(4)
    if h and not h.get("db_mirror_ok", True):
        print("[CONSENSUS] FATAL: DB mirror failed after retries.", file=sys.stderr)
        sys.exit(5)


if __name__ == "__main__":
    main()
