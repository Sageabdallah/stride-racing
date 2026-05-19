#!/usr/bin/env python3
"""
NSW Racing XML results collector.

Collects race results and sectional data from the free Racing NSW XML export endpoint.
"""

import os
import sys
import re
import json
import argparse
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta
from urllib.parse import quote, urlencode

import requests
import psycopg2

CALENDAR_URL = "https://racing.racingnsw.com.au/FreeFields/Calendar_Results.aspx"
XML_BASE_URL = "https://racing.racingnsw.com.au/FreeFields/XML.aspx"
RESULTS_BASE_URL = "https://racing.racingnsw.com.au/FreeFields/Results.aspx"

REQUEST_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-AU,en;q=0.9",
    "Referer": "https://racing.racingnsw.com.au/",
}

NSW_TRACK_ALIASES = {
    "randwick": "Royal Randwick",
    "royal randwick": "Royal Randwick",
    "rosehill": "Rosehill Gardens",
    "rosehill gardens": "Rosehill Gardens",
    "warwick farm": "Warwick Farm",
    "canterbury": "Canterbury Park",
    "canterbury park": "Canterbury Park",
    "newcastle": "Newcastle",
    "kembla grange": "Kembla Grange",
    "gosford": "Gosford",
    "wyong": "Wyong",
    "hawkesbury": "Hawkesbury",
    "scone": "Scone",
    "tamworth": "Tamworth",
    "muswellbrook": "Muswellbrook",
    "mudgee": "Mudgee",
    "dubbo": "Dubbo",
    "bathurst": "Bathurst",
    "orange": "Orange",
    "moree": "Moree",
    "grafton": "Grafton",
    "coffs harbour": "Coffs Harbour",
    "port macquarie": "Port Macquarie",
    "taree": "Taree",
    "nowra": "Nowra",
    "queanbeyan": "Queanbeyan",
    "wagga": "Wagga",
    "wagga wagga": "Wagga",
    "albury": "Albury",
    "goulburn": "Goulburn",
}


def normalize_track_name(raw_name):
    if not raw_name:
        return raw_name
    name = raw_name.strip()
    lower = name.lower()
    return NSW_TRACK_ALIASES.get(lower, name)


def parse_meeting_key(key_str):
    parts = key_str.split(",")
    if len(parts) < 3:
        return None
    date_part = parts[0].strip()
    state = parts[1].strip()
    venue = ",".join(parts[2:]).strip()

    date_match = re.match(r'(\d{4})([A-Za-z]+)(\d{1,2})', date_part)
    if not date_match:
        return None
    year = int(date_match.group(1))
    month_str = date_match.group(2)
    day = int(date_match.group(3))

    month_map = {
        "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
        "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
    }
    month = month_map.get(month_str.lower()[:3])
    if not month:
        return None

    try:
        race_date = datetime(year, month, day).strftime("%Y-%m-%d")
    except ValueError:
        return None

    return {
        "key": key_str,
        "date_part": date_part,
        "state": state,
        "venue": venue,
        "race_date": race_date,
        "track": normalize_track_name(venue),
    }


def discover_meetings(days_back=14, track_filter=None, verbose=False):
    if verbose:
        print(f"[NSW] Fetching calendar from {CALENDAR_URL}")

    try:
        resp = requests.get(CALENDAR_URL, headers=REQUEST_HEADERS, timeout=30)
        resp.raise_for_status()
    except requests.RequestException as e:
        print(f"[NSW] Error fetching calendar: {e}")
        return []

    html = resp.text

    pattern = r"Results\.aspx\?Key=([^'\"<>]+)"
    raw_keys = re.findall(pattern, html)

    if not raw_keys:
        pattern2 = r"Key=([^'\"<>\s]+)"
        raw_keys = re.findall(pattern2, html)

    if verbose:
        print(f"[NSW] Found {len(raw_keys)} result links in calendar")

    cutoff_date = (datetime.now() - timedelta(days=days_back)).strftime("%Y-%m-%d")
    meetings = []
    seen_keys = set()

    for raw_key in raw_keys:
        raw_key = raw_key.strip()
        if raw_key in seen_keys:
            continue
        seen_keys.add(raw_key)

        parsed = parse_meeting_key(raw_key)
        if not parsed:
            if verbose:
                print(f"[NSW]   Skipping unparseable key: {raw_key}")
            continue

        if parsed["race_date"] < cutoff_date:
            continue

        if track_filter:
            filter_lower = track_filter.lower()
            if filter_lower not in parsed["venue"].lower() and filter_lower not in parsed["track"].lower():
                continue

        meetings.append(parsed)

    meetings.sort(key=lambda m: m["race_date"], reverse=True)

    if verbose:
        print(f"[NSW] {len(meetings)} meetings within last {days_back} days" +
              (f" (track filter: {track_filter})" if track_filter else ""))

    return meetings


def fetch_xml(meeting_key, verbose=False):
    params = {"Key": meeting_key, "stage": "Results"}
    url = f"{XML_BASE_URL}?{urlencode(params)}"

    if verbose:
        print(f"[NSW]   Fetching XML: {url}")

    try:
        resp = requests.get(url, headers=REQUEST_HEADERS, timeout=45)
        resp.raise_for_status()
        content = resp.text.strip()

        if not content or "<" not in content:
            if verbose:
                print(f"[NSW]   Empty or non-XML response")
            return None

        if "error" in content.lower()[:200] and "<race" not in content.lower()[:500]:
            if verbose:
                print(f"[NSW]   Error response from server")
            return None

        return content

    except requests.RequestException as e:
        if verbose:
            print(f"[NSW]   HTTP error: {e}")
        return None


def parse_sectional_600(raw_str):
    if not raw_str:
        return None
    match = re.search(r'600[/\\](\d+\.?\d*)', str(raw_str))
    if match:
        try:
            return float(match.group(1))
        except ValueError:
            pass
    match2 = re.search(r'(\d+\.?\d+)', str(raw_str))
    if match2:
        try:
            val = float(match2.group(1))
            if 20 < val < 50:
                return val
        except ValueError:
            pass
    return None


def parse_distance(dist_str):
    if not dist_str:
        return None
    if isinstance(dist_str, (int, float)):
        return int(dist_str)
    m = re.search(r'(\d+)', str(dist_str))
    if m:
        return int(m.group(1))
    return None


def parse_weight(weight_str):
    if not weight_str:
        return None
    try:
        return float(weight_str)
    except (ValueError, TypeError):
        m = re.search(r'(\d+\.?\d*)', str(weight_str))
        if m:
            try:
                return float(m.group(1))
            except ValueError:
                pass
    return None


def parse_margin(margin_str):
    if not margin_str:
        return None
    margin_str = str(margin_str).strip().lower()
    if margin_str in ("", "0", "dh", "dead heat", "nose", "ns"):
        if margin_str in ("nose", "ns"):
            return 0.05
        if margin_str in ("dh", "dead heat"):
            return 0.0
        return 0.0
    if margin_str == "shd" or margin_str == "short head":
        return 0.1
    if margin_str == "shk" or margin_str == "short neck":
        return 0.15
    if margin_str == "hd" or margin_str == "head":
        return 0.2
    if margin_str in ("nk", "neck"):
        return 0.3
    if margin_str == "long head":
        return 0.25
    if margin_str == "long neck":
        return 0.4

    try:
        return float(margin_str)
    except ValueError:
        pass

    m = re.search(r'(\d+\.?\d*)', margin_str)
    if m:
        try:
            return float(m.group(1))
        except ValueError:
            pass
    return None


def parse_sp(sp_str):
    if not sp_str:
        return None
    sp_str = str(sp_str).strip().replace("$", "")
    try:
        val = float(sp_str)
        if val > 0:
            return val
    except ValueError:
        pass

    frac_match = re.match(r'(\d+)/(\d+)', sp_str)
    if frac_match:
        num = int(frac_match.group(1))
        den = int(frac_match.group(2))
        if den > 0:
            return round(num / den + 1.0, 2)
    return None


def parse_position(pos_str):
    if not pos_str:
        return None
    pos_str = str(pos_str).strip()
    m = re.search(r'(\d+)', pos_str)
    if m:
        return int(m.group(1))
    return None


def get_xml_text(element, tag, default=None):
    child = element.find(tag)
    if child is not None and child.text:
        return child.text.strip()
    return default


def parse_xml_results(xml_text, meeting_info, verbose=False):
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as e:
        if verbose:
            print(f"[NSW]   XML parse error: {e}")
        return [], []

    results_records = []
    sectional_records = []

    race_date = meeting_info["race_date"]
    track = meeting_info["track"]
    venue = meeting_info["venue"]

    meeting_el = root if root.tag.lower() == "meeting" else root.find(".//meeting")
    if meeting_el is None:
        meeting_el = root

    weather = get_xml_text(meeting_el, "Weather") or get_xml_text(meeting_el, "weather")

    race_tags = ["race", "Race", "RACE"]
    races = []
    for tag in race_tags:
        races = meeting_el.findall(tag)
        if races:
            break
    if not races:
        races = list(meeting_el)
        races = [r for r in races if r.tag.lower() == "race"]

    if verbose:
        print(f"[NSW]   Found {len(races)} races in XML")

    for race_el in races:
        race_number = None
        for attr in ["Number", "number", "RaceNumber", "racenumber", "No", "no"]:
            val = race_el.get(attr)
            if val:
                race_number = parse_position(val)
                break
        if race_number is None:
            rn_text = get_xml_text(race_el, "RaceNumber") or get_xml_text(race_el, "Number")
            if rn_text:
                race_number = parse_position(rn_text)
        if race_number is None:
            continue

        race_name = (race_el.get("name") or race_el.get("Name") or race_el.get("RaceName") or
                     get_xml_text(race_el, "RaceName") or get_xml_text(race_el, "Name") or "")
        distance_raw = (race_el.get("distance") or race_el.get("Distance") or
                        get_xml_text(race_el, "Distance") or "")
        distance_m = parse_distance(distance_raw)

        race_class = (race_el.get("class") or race_el.get("Class") or
                      get_xml_text(race_el, "Class") or get_xml_text(race_el, "RaceClass") or "")

        track_condition = (race_el.get("trackcondition") or race_el.get("TrackCondition") or
                           race_el.get("Going") or race_el.get("going") or
                           get_xml_text(race_el, "TrackCondition") or "")
        if not track_condition:
            track_condition = meeting_el.get("trackcondition") or meeting_el.get("TrackCondition") or ""

        sectional_raw = (race_el.get("sectionaltime") or race_el.get("SectionalTime") or
                         race_el.get("Sectional") or race_el.get("sectional") or
                         get_xml_text(race_el, "SectionalTime") or get_xml_text(race_el, "Sectional"))

        last_600m_time = parse_sectional_600(sectional_raw)

        fastest_time_raw = (race_el.get("fastesttime") or race_el.get("FastestTime") or
                            race_el.get("winningtime") or race_el.get("WinningTime") or
                            get_xml_text(race_el, "FastestTime") or get_xml_text(race_el, "WinningTime") or "")

        nom_tags = ["nomination", "Nomination", "NOMINATION", "runner", "Runner", "RUNNER",
                    "starter", "Starter", "STARTER"]
        nominations = []
        for tag in nom_tags:
            nominations = race_el.findall(tag)
            if nominations:
                break
        if not nominations:
            nominations = [child for child in race_el
                           if child.tag.lower() in ("nomination", "runner", "starter")]

        field_size = len(nominations) if nominations else 0

        for nom in nominations:
            horse_name = (nom.get("horse") or nom.get("Horse") or nom.get("HorseName") or
                          nom.get("Name") or
                          get_xml_text(nom, "HorseName") or get_xml_text(nom, "Horse") or "")
            if not horse_name:
                continue
            horse_name = horse_name.strip()

            horse_id_raw = (nom.get("id") or nom.get("HorseCode") or nom.get("HorseId") or "")
            horse_id = horse_id_raw.strip() if horse_id_raw else f"nsw_{horse_name.lower().replace(' ', '_')}"

            position = parse_position(
                nom.get("finished") or nom.get("Finished") or
                nom.get("FinishingPosition") or nom.get("Position") or nom.get("Placing") or
                get_xml_text(nom, "FinishingPosition") or get_xml_text(nom, "Position")
            )

            margin = parse_margin(
                nom.get("decimalmargin") or nom.get("DecimalMargin") or
                nom.get("Margin") or nom.get("BeatenMargin") or
                get_xml_text(nom, "Margin") or get_xml_text(nom, "BeatenMargin")
            )

            sp = parse_sp(
                nom.get("pricestarting") or nom.get("PriceStarting") or
                nom.get("StartingPrice") or nom.get("SP") or
                get_xml_text(nom, "StartingPrice") or get_xml_text(nom, "SP")
            )

            barrier = parse_position(
                nom.get("barrier") or nom.get("Barrier") or nom.get("Gate") or
                get_xml_text(nom, "Barrier")
            )

            weight = parse_weight(
                nom.get("weight") or nom.get("Weight") or nom.get("variedweight") or
                nom.get("WeightCarried") or get_xml_text(nom, "Weight")
            )

            jockey_first = nom.get("jockeyfirstname") or ""
            jockey_last = nom.get("jockeysurname") or ""
            if jockey_first or jockey_last:
                jockey = f"{jockey_first} {jockey_last}".strip()
            else:
                jockey = (nom.get("Jockey") or nom.get("JockeyName") or
                          get_xml_text(nom, "Jockey") or get_xml_text(nom, "JockeyName") or "")
            jockey = jockey.strip()

            trainer_first = nom.get("trainerfirstname") or ""
            trainer_last = nom.get("trainersurname") or ""
            if trainer_first or trainer_last:
                trainer = f"{trainer_first} {trainer_last}".strip()
            else:
                trainer = (nom.get("rsbtrainername") or nom.get("Trainer") or nom.get("TrainerName") or
                           get_xml_text(nom, "Trainer") or get_xml_text(nom, "TrainerName") or "")
            trainer = trainer.strip()

            saddle_number = parse_position(
                nom.get("saddlecloth") or nom.get("TabNo") or nom.get("number") or
                nom.get("Number") or nom.get("SaddleNumber") or
                get_xml_text(nom, "TabNo")
            )

            jockey_id = nom.get("jockeynumber") or nom.get("JockeyNumber") or None

            race_id = f"nsw_{race_date}_{track.lower().replace(' ', '_')}_r{race_number}"

            result_record = {
                "horse_id": horse_id,
                "horse_name": horse_name,
                "race_id": race_id,
                "track": track,
                "race_date": race_date,
                "distance_m": distance_m,
                "race_class": race_class or None,
                "class_level": None,
                "going": track_condition or None,
                "position": position,
                "margin_lengths": margin,
                "weight_kg": weight,
                "jockey": jockey or None,
                "jockey_id": jockey_id,
                "barrier": barrier,
                "sp_odds": sp,
                "field_size": field_size,
                "race_name": race_name or None,
                "race_number": race_number,
                "form_string": None,
                "opponents_json": None,
            }
            results_records.append(result_record)

            if last_600m_time:
                sec_record = {
                    "track": track,
                    "race_date": race_date,
                    "race_number": race_number,
                    "race_name": race_name or None,
                    "horse_name": horse_name,
                    "saddle_number": saddle_number,
                    "distance_m": distance_m,
                    "winning_time": fastest_time_raw or None,
                    "track_config": None,
                    "splits_json": [],
                    "last_200m_speed": None,
                    "last_200m_time": None,
                    "last_400m_speed": None,
                    "last_400m_time": None,
                    "last_600m_speed": round(600.0 / last_600m_time, 3) if last_600m_time and last_600m_time > 0 else None,
                    "last_600m_time": last_600m_time,
                    "last_800m_speed": None,
                    "last_800m_time": None,
                    "finishing_burst": None,
                    "avg_speed": None,
                    "race_results_history_id": None,
                }
                sectional_records.append(sec_record)

        if verbose and nominations:
            dist_str = f"{distance_m}m" if distance_m else "?m"
            sec_str = f" | 600m={last_600m_time:.2f}s" if last_600m_time else ""
            print(f"[NSW]   R{race_number}: {race_name} ({dist_str}, {field_size} runners){sec_str}")

    return results_records, sectional_records


def get_db_connection():
    db_url = os.environ.get("DATABASE_URL")
    if not db_url:
        print("[NSW] Error: DATABASE_URL not set")
        sys.exit(1)
    return psycopg2.connect(db_url)


def check_existing_results(conn, race_date, track, race_number):
    cur = conn.cursor()
    cur.execute("""
        SELECT COUNT(*) FROM race_results_history
        WHERE race_date = %s AND track = %s AND race_number = %s
    """, (race_date, track, race_number))
    count = cur.fetchone()[0]
    cur.close()
    return count > 0


def check_existing_sectional(conn, race_date, track, race_number, horse_name):
    cur = conn.cursor()
    cur.execute("""
        SELECT COUNT(*) FROM sectional_times
        WHERE race_date = %s AND track = %s AND race_number = %s AND horse_name = %s
    """, (race_date, track, race_number, horse_name))
    count = cur.fetchone()[0]
    cur.close()
    return count > 0


def import_results(results_records, conn, verbose=False):
    if not results_records:
        return 0, 0

    cur = conn.cursor()
    imported = 0
    skipped = 0

    races_checked = set()

    for record in results_records:
        race_key = (record["race_date"], record["track"], record["race_number"])

        if race_key not in races_checked:
            if check_existing_results(conn, *race_key):
                races_checked.add(race_key)

        if race_key in races_checked:
            cur.execute("""
                SELECT COUNT(*) FROM race_results_history
                WHERE race_date = %s AND track = %s AND race_number = %s AND horse_name = %s
            """, (record["race_date"], record["track"], record["race_number"], record["horse_name"]))
            if cur.fetchone()[0] > 0:
                skipped += 1
                continue

        cur.execute("""
            INSERT INTO race_results_history (
                horse_id, horse_name, race_id, track, race_date,
                distance_m, race_class, class_level, going,
                position, margin_lengths, weight_kg,
                jockey, jockey_id, barrier, sp_odds,
                field_size, race_name, race_number,
                form_string, opponents_json
            ) VALUES (
                %s, %s, %s, %s, %s,
                %s, %s, %s, %s,
                %s, %s, %s,
                %s, %s, %s, %s,
                %s, %s, %s,
                %s, %s
            )
        """, (
            record["horse_id"], record["horse_name"], record["race_id"],
            record["track"], record["race_date"],
            record["distance_m"], record["race_class"], record["class_level"],
            record["going"],
            record["position"], record["margin_lengths"], record["weight_kg"],
            record["jockey"], record["jockey_id"], record["barrier"], record["sp_odds"],
            record["field_size"], record["race_name"], record["race_number"],
            record["form_string"],
            json.dumps(record["opponents_json"]) if record["opponents_json"] else None,
        ))
        imported += 1

    conn.commit()
    cur.close()
    return imported, skipped


def import_sectionals(sectional_records, conn, verbose=False):
    if not sectional_records:
        return 0, 0

    cur = conn.cursor()
    imported = 0
    skipped = 0

    for record in sectional_records:
        if check_existing_sectional(conn, record["race_date"], record["track"],
                                    record["race_number"], record["horse_name"]):
            skipped += 1
            continue

        cur.execute("""
            SELECT id FROM race_results_history
            WHERE race_date = %s AND track = %s AND race_number = %s AND horse_name = %s
            LIMIT 1
        """, (record["race_date"], record["track"], record["race_number"], record["horse_name"]))
        row = cur.fetchone()
        race_results_history_id = row[0] if row else None

        cur.execute("""
            INSERT INTO sectional_times (
                race_results_history_id, track, race_date, race_number, race_name,
                horse_name, saddle_number, distance_m, winning_time, track_config,
                splits_json, last_200m_speed, last_200m_time,
                last_400m_speed, last_400m_time,
                last_600m_speed, last_600m_time,
                last_800m_speed, last_800m_time,
                finishing_burst, avg_speed, source
            ) VALUES (
                %s, %s, %s, %s, %s,
                %s, %s, %s, %s, %s,
                %s, %s, %s,
                %s, %s,
                %s, %s,
                %s, %s,
                %s, %s, 'racing_nsw'
            )
            ON CONFLICT (race_date, track, race_number, horse_name) DO NOTHING
        """, (
            race_results_history_id,
            record["track"], record["race_date"], record["race_number"], record["race_name"],
            record["horse_name"], record["saddle_number"], record["distance_m"],
            record["winning_time"], record["track_config"],
            json.dumps(record.get("splits_json", [])),
            record["last_200m_speed"], record["last_200m_time"],
            record["last_400m_speed"], record["last_400m_time"],
            record["last_600m_speed"], record["last_600m_time"],
            record["last_800m_speed"], record["last_800m_time"],
            record["finishing_burst"], record["avg_speed"],
        ))
        imported += 1

    conn.commit()
    cur.close()
    return imported, skipped


def process_meeting(meeting, conn, verbose=False):
    xml_text = fetch_xml(meeting["key"], verbose)
    if not xml_text:
        return 0, 0, 0, 0

    results_records, sectional_records = parse_xml_results(xml_text, meeting, verbose)

    if not results_records:
        if verbose:
            print(f"[NSW]   No results parsed from XML for {meeting['venue']}")
        return 0, 0, 0, 0

    results_imported, results_skipped = import_results(results_records, conn, verbose)
    sectionals_imported, sectionals_skipped = import_sectionals(sectional_records, conn, verbose)

    return results_imported, results_skipped, sectionals_imported, sectionals_skipped


def list_meetings(days_back=14, track_filter=None, verbose=False):
    meetings = discover_meetings(days_back, track_filter, verbose)
    if not meetings:
        print("[NSW] No meetings found")
        return

    print(f"\n{'='*70}")
    print(f"  NSW RACING MEETINGS (last {days_back} days)")
    if track_filter:
        print(f"  Track filter: {track_filter}")
    print(f"{'='*70}")
    print(f"  {'Date':<12} {'Track':<25} {'State':<6} Key")
    print(f"  {'-'*12} {'-'*25} {'-'*6} {'-'*30}")

    for m in meetings:
        print(f"  {m['race_date']:<12} {m['track']:<25} {m['state']:<6} {m['key']}")

    print(f"\n  Total: {len(meetings)} meeting(s)\n")


def run_collection(days_back=14, track_filter=None, verbose=False):
    meetings = discover_meetings(days_back, track_filter, verbose)
    if not meetings:
        print("[NSW] No meetings found to process")
        return

    print(f"\n{'='*70}")
    print(f"  NSW RACING XML RESULTS COLLECTOR")
    print(f"{'='*70}")
    print(f"  Meetings to process: {len(meetings)}")
    print(f"  Days back: {days_back}")
    if track_filter:
        print(f"  Track filter: {track_filter}")
    print()

    conn = get_db_connection()

    total_results_imported = 0
    total_results_skipped = 0
    total_sectionals_imported = 0
    total_sectionals_skipped = 0
    meetings_processed = 0
    meetings_with_data = 0

    for i, meeting in enumerate(meetings):
        label = f"[{meeting['race_date']}] {meeting['track']}"
        if verbose:
            print(f"\n[NSW] Processing {label}...")

        try:
            ri, rs, si, ss = process_meeting(meeting, conn, verbose)
            meetings_processed += 1

            if ri > 0 or si > 0:
                meetings_with_data += 1
                print(f"  {label}: {ri} results imported ({rs} skipped), {si} sectionals imported ({ss} skipped)")
            elif verbose:
                print(f"  {label}: No new data (results skipped={rs}, sectionals skipped={ss})")

            total_results_imported += ri
            total_results_skipped += rs
            total_sectionals_imported += si
            total_sectionals_skipped += ss

        except Exception as e:
            print(f"  {label}: ERROR - {e}")

        if (i + 1) % 5 == 0 and len(meetings) > 5:
            print(f"  Progress: {i+1}/{len(meetings)} meetings")

        time.sleep(1.0)

    conn.close()

    print(f"\n{'='*70}")
    print(f"  COLLECTION COMPLETE")
    print(f"{'='*70}")
    print(f"  Meetings processed: {meetings_processed}")
    print(f"  Meetings with new data: {meetings_with_data}")
    print(f"  Results imported: {total_results_imported} (skipped: {total_results_skipped})")
    print(f"  Sectionals imported: {total_sectionals_imported} (skipped: {total_sectionals_skipped})")
    print()


def main():
    parser = argparse.ArgumentParser(
        description="Collect NSW race results and sectional data from Racing NSW XML exports"
    )
    parser.add_argument("--list", action="store_true",
                        help="List available meetings without importing")
    parser.add_argument("--days", type=int, default=14,
                        help="Number of days back to look (default: 14)")
    parser.add_argument("--track", type=str, default=None,
                        help="Filter by track name (e.g., 'Royal Randwick')")
    parser.add_argument("--verbose", action="store_true",
                        help="Enable detailed logging")
    args = parser.parse_args()

    if args.list:
        list_meetings(args.days, args.track, args.verbose)
    else:
        if not os.environ.get("DATABASE_URL"):
            print("[NSW] Error: DATABASE_URL environment variable not set")
            sys.exit(1)
        run_collection(args.days, args.track, args.verbose)


if __name__ == "__main__":
    main()
