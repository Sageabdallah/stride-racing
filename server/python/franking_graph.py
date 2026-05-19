#!/usr/bin/env python3
"""Graph-theoretic form franking engine: treats race results as noisy observations of hidden horse quality and uses network propagation to denoise them."""

import os
import json
import math
import logging
from datetime import datetime, timedelta
from collections import defaultdict, deque

import numpy as np
import psycopg2
import networkx as nx

try:
    import community as community_louvain
    LOUVAIN_AVAILABLE = True
except ImportError:
    LOUVAIN_AVAILABLE = False

logger = logging.getLogger(__name__)

CONFIG = {
    "gen_decay": [1.0, 0.45, 0.20, 0.08, 0.03],
    "prune_threshold": 0.01,
    "consistency_bonus_max": 1.3,
    "class_step_bonus": 1.15,
    "trainer_stability_bonus": 1.1,
    "temporal_half_life_days": 60,
    "excuse_wide_barrier_small": 10,
    "excuse_wide_barrier_any": 14,
    "excuse_spell_days": 60,
    "excuse_weight_increase_kg": 3.0,
    "excuse_market_drift_pct": 0.50,
    "excuse_cap": 0.85,
    "pagerank_damping": 0.85,
    "anti_frank_threshold": 0.6,
    "market_scepticism_discount": 0.7,
    "lru_cache_size": 2048,
    "graph_json_path": os.path.join(os.path.dirname(__file__), "data", "franking_graph.json"),
    "going_groups": {
        "firm": ["firm", "hard", "fast", "good to firm"],
        "good": ["good", "dead", "synthetic", "good 3", "good 4"],
        "soft": ["soft", "yielding", "good to soft", "soft 5", "soft 6"],
        "heavy": ["heavy", "soft to heavy", "heavy 8", "heavy 9", "heavy 10"],
    },
}


def _get_connection():
    """Return a new psycopg2 connection using DATABASE_URL."""
    url = os.environ.get("DATABASE_URL", "")
    if not url:
        raise RuntimeError("DATABASE_URL not set")
    return psycopg2.connect(url)


def _sf(val, default=0.0):
    if val is None:
        return default
    try:
        return float(val)
    except (ValueError, TypeError):
        return default


def _si(val, default=0):
    if val is None:
        return default
    try:
        return int(val)
    except (ValueError, TypeError):
        return default


def _parse_date(s):
    try:
        return datetime.strptime(str(s)[:10], "%Y-%m-%d")
    except (ValueError, TypeError):
        return None


def _days_between(a, b):
    da, db = _parse_date(a), _parse_date(b)
    if da and db:
        return abs((da - db).days)
    return 9999


def _normalise_going(g):
    if not g:
        return "good"
    gl = g.lower().strip()
    for group, terms in CONFIG["going_groups"].items():
        for t in terms:
            if t in gl:
                return group
    return "good"


def _parse_opponents(val):
    if val is None:
        return []
    if isinstance(val, list):
        return val
    if isinstance(val, str):
        try:
            parsed = json.loads(val)
            if isinstance(parsed, list):
                return parsed
        except (json.JSONDecodeError, TypeError):
            pass
    return []



class FormGraph:
    """NetworkX MultiDiGraph with 4 edge types for racing form analysis."""

    def __init__(self):
        self.G = nx.MultiDiGraph()
        self.horse_graph = nx.DiGraph()
        self.horse_supernode = nx.Graph()
        self._raw_data = {}
        self._horse_races = defaultdict(list)
        self._race_runners = defaultdict(list)
        self._jockey_rides = defaultdict(list)
        self._trainer_rides = defaultdict(list)
        self._pagerank_cache = None
        self._community_cache = None
        self._betweenness_cache = None
        self._franking_cache = {}
        self._graph_built = False


    def _load_all_results(self):
        """Load all race results from DB into memory."""
        conn = _get_connection()
        cur = conn.cursor()
        cur.execute("""
            SELECT horse_id, horse_name, race_id, track, race_date,
                   distance_m, race_class, class_level, going, position,
                   margin_lengths, weight_kg, jockey, jockey_id, barrier,
                   sp_odds, field_size, race_name, race_number, form_string,
                   opponents_json
            FROM race_results_history
            WHERE position IS NOT NULL
            ORDER BY race_date ASC, race_id ASC, position ASC
        """)
        rows = cur.fetchall()
        cur.close()
        conn.close()
        logger.info("Loaded %d race result rows from DB", len(rows))
        return rows


    def build_graph(self):
        """Construct the full multi-edge graph from the database."""
        try:
            rows = self._load_all_results()
        except Exception as e:
            logger.error("Failed to load results: %s", e)
            self._graph_built = True
            return

        self.G = nx.MultiDiGraph()
        self.horse_graph = nx.DiGraph()
        self.horse_supernode = nx.Graph()
        self._horse_races = defaultdict(list)
        self._race_runners = defaultdict(list)
        self._jockey_rides = defaultdict(list)
        self._trainer_rides = defaultdict(list)
        self._raw_data = {}

        for r in rows:
            (horse_id, horse_name, race_id, track, race_date,
             distance_m, race_class, class_level, going, position,
             margin_lengths, weight_kg, jockey, jockey_id, barrier,
             sp_odds, field_size, race_name, race_number, form_string,
             opponents_json) = r
            trainer = ""

            node = (horse_id, race_id)
            attrs = {
                "horse_id": horse_id,
                "horse_name": horse_name or "",
                "race_id": race_id,
                "position": _si(position, 99),
                "margin": _sf(margin_lengths),
                "sp_odds": _sf(sp_odds, 10.0),
                "class_level": _si(class_level, 1),
                "distance_m": _si(distance_m, 1400),
                "going": going or "",
                "track": track or "",
                "barrier": _si(barrier, 1),
                "weight_kg": _sf(weight_kg, 56.0),
                "jockey": jockey or "",
                "jockey_id": jockey_id or "",
                "trainer": trainer or "",
                "race_date": str(race_date)[:10] if race_date else "2020-01-01",
                "field_size": _si(field_size, 10),
                "opponents_json": opponents_json,
            }
            self.G.add_node(node, **attrs)
            self._raw_data[node] = attrs
            self._horse_races[horse_id].append(node)
            self._race_runners[race_id].append(node)
            jk = jockey or ""
            tr = trainer or ""
            if jk:
                self._jockey_rides[jk].append(node)
            if tr:
                self._trainer_rides[tr].append(node)

        logger.info("Graph nodes: %d", self.G.number_of_nodes())

        self._build_beaten_edges()
        self._build_shared_race_edges()
        self._build_identity_edges()
        self._build_jt_transfer_edges()
        self._build_horse_level_graph()

        self._pagerank_cache = None
        self._community_cache = None
        self._betweenness_cache = None
        self._franking_cache = {}
        self._graph_built = True

        logger.info("Graph edges: %d (beaten + shared_race + identity + jt_transfer)",
                     self.G.number_of_edges())

    def _build_beaten_edges(self):
        """Edge Type 1: A finished ahead of B → A→B with margin weight."""
        for race_id, nodes in self._race_runners.items():
            sorted_nodes = sorted(nodes, key=lambda n: self._raw_data[n]["position"])
            for i in range(len(sorted_nodes)):
                for j in range(i + 1, len(sorted_nodes)):
                    a = sorted_nodes[i]
                    b = sorted_nodes[j]
                    da = self._raw_data[a]
                    db = self._raw_data[b]
                    margin = abs(db["margin"] - da["margin"])
                    if margin < 0.01:
                        margin = 0.01
                    self.G.add_edge(a, b, edge_type="beaten", weight=margin,
                                    race_id=race_id, race_date=da["race_date"])

    def _build_shared_race_edges(self):
        """Edge Type 2: every pair in same race, weight = 1/(1+margin_diff)."""
        for race_id, nodes in self._race_runners.items():
            for i in range(len(nodes)):
                for j in range(i + 1, len(nodes)):
                    a = nodes[i]
                    b = nodes[j]
                    margin_diff = abs(self._raw_data[a]["margin"] - self._raw_data[b]["margin"])
                    w = 1.0 / (1.0 + margin_diff)
                    self.G.add_edge(a, b, edge_type="shared_race", weight=w, race_id=race_id)
                    self.G.add_edge(b, a, edge_type="shared_race", weight=w, race_id=race_id)

    def _build_identity_edges(self):
        """Edge Type 3: same horse's chronological performances."""
        for horse_id, nodes in self._horse_races.items():
            sorted_nodes = sorted(nodes, key=lambda n: self._raw_data[n]["race_date"])
            for i in range(len(sorted_nodes) - 1):
                a = sorted_nodes[i]
                b = sorted_nodes[i + 1]
                days = _days_between(self._raw_data[a]["race_date"],
                                     self._raw_data[b]["race_date"])
                w = math.exp(-0.693 * days / max(CONFIG["temporal_half_life_days"], 1))
                self.G.add_edge(a, b, edge_type="identity", weight=w, days=days)
                self.G.add_edge(b, a, edge_type="identity", weight=w, days=days)

    def _build_jt_transfer_edges(self):
        """Edge Type 4: jockey/trainer transfer at same track/distance band."""
        def _dist_band(d):
            if d < 1200:
                return "sprint"
            elif d < 1800:
                return "mile"
            else:
                return "staying"

        for person_rides in [self._jockey_rides, self._trainer_rides]:
            for person, nodes in person_rides.items():
                if not person or len(nodes) < 2:
                    continue
                sorted_nodes = sorted(nodes, key=lambda n: self._raw_data[n]["race_date"])
                for i in range(len(sorted_nodes) - 1):
                    a = sorted_nodes[i]
                    b = sorted_nodes[i + 1]
                    da = self._raw_data[a]
                    db = self._raw_data[b]
                    if da["horse_id"] == db["horse_id"]:
                        continue
                    if da["track"] != db["track"]:
                        continue
                    if _dist_band(da["distance_m"]) != _dist_band(db["distance_m"]):
                        continue
                    days = _days_between(da["race_date"], db["race_date"])
                    if days > 90:
                        continue
                    w = 0.2 * math.exp(-0.693 * days / 30.0)
                    self.G.add_edge(a, b, edge_type="jt_transfer", weight=w,
                                    person=person, days=days)

    def _build_horse_level_graph(self):
        """Build simplified horse-level DiGraph for PageRank & community."""
        self.horse_graph = nx.DiGraph()
        self.horse_supernode = nx.Graph()

        beaten_agg = defaultdict(float)
        for u, v, data in self.G.edges(data=True):
            if data.get("edge_type") != "beaten":
                continue
            h_a = u[0]
            h_b = v[0]
            if h_a == h_b:
                continue
            beaten_agg[(h_a, h_b)] += 1.0 / (1.0 + data.get("weight", 1.0))

        for (h_a, h_b), w in beaten_agg.items():
            self.horse_graph.add_edge(h_a, h_b, weight=w)

        for race_id, nodes in self._race_runners.items():
            horse_ids = list(set(n[0] for n in nodes))
            r_node = f"race:{race_id}"
            for hid in horse_ids:
                self.horse_supernode.add_edge(hid, r_node)


    def save_graph(self):
        """Serialise graph metadata to JSON on disk."""
        try:
            path = CONFIG["graph_json_path"]
            os.makedirs(os.path.dirname(path), exist_ok=True)
            meta = {
                "saved_at": datetime.now().isoformat(),
                "n_nodes": self.G.number_of_nodes(),
                "n_edges": self.G.number_of_edges(),
                "n_horses": len(self._horse_races),
                "n_races": len(self._race_runners),
                "horse_ids": list(self._horse_races.keys())[:100],
            }
            with open(path, "w") as f:
                json.dump(meta, f, indent=2)
            logger.info("Graph metadata saved to %s", path)
        except Exception as e:
            logger.error("Failed to save graph: %s", e)

    def load_graph(self):
        """Load graph from DB (graph is always rebuilt, metadata is informational)."""
        path = CONFIG["graph_json_path"]
        if os.path.exists(path):
            try:
                with open(path, "r") as f:
                    meta = json.load(f)
                logger.info("Graph metadata loaded: %d nodes, %d edges (from %s)",
                            meta.get("n_nodes", 0), meta.get("n_edges", 0),
                            meta.get("saved_at", "unknown"))
            except Exception as e:
                logger.warning("Could not load graph metadata: %s", e)

    def load_or_build(self):
        """Load metadata if available, then build graph from DB."""
        self.load_graph()
        self.build_graph()
        self.save_graph()

    def invalidate_cache(self):
        """Invalidate all caches when new results arrive."""
        self._pagerank_cache = None
        self._community_cache = None
        self._betweenness_cache = None
        self._franking_cache = {}


    def compute_deep_franking(self, horse_id):
        """
        BFS through beaten-by graph up to depth 5 with exponential decay.

        Returns dict with franking_score, franking_depth, franking_chain.
        """
        try:
            return self._deep_franking_impl(horse_id)
        except Exception as e:
            logger.error("Deep franking failed for %s: %s", horse_id, e)
            return {
                "franking_score": 0.0,
                "franking_depth": 0,
                "franking_independence": 0,
                "best_frank_chains": [],
            }

    def _deep_franking_impl(self, horse_id):
        if horse_id in self._franking_cache:
            return self._franking_cache[horse_id]

        target_nodes = self._horse_races.get(horse_id, [])
        if not target_nodes:
            return {"franking_score": 0.0, "franking_depth": 0,
                    "franking_independence": 0, "best_frank_chains": []}

        decay_table = CONFIG["gen_decay"]
        prune = CONFIG["prune_threshold"]

        beaten_by_nodes = set()
        for tn in target_nodes:
            for u, _, data in self.G.in_edges(tn, data=True):
                if data.get("edge_type") == "beaten":
                    beaten_by_nodes.add(u)

        if not beaten_by_nodes:
            result = {"franking_score": 0.0, "franking_depth": 0,
                      "franking_independence": 0, "best_frank_chains": []}
            self._franking_cache[horse_id] = result
            return result

        all_paths = []
        queue = deque()
        for bnode in beaten_by_nodes:
            d = self._raw_data.get(bnode, {})
            queue.append((bnode, 0, decay_table[0], [bnode], {bnode[0]}))

        max_depth = 0
        independent_roots = set()

        while queue:
            node, gen, strength, path, visited_horses = queue.popleft()
            if strength < prune:
                continue

            nd = self._raw_data.get(node, {})
            path_score = strength
            path_score *= self._path_quality_score(path)
            all_paths.append((path_score, gen + 1, path[:], node[0]))
            independent_roots.add(path[0][0])
            if gen + 1 > max_depth:
                max_depth = gen + 1

            if gen + 1 >= len(decay_table):
                continue

            next_gen = gen + 1
            next_decay = decay_table[next_gen]
            for u, _, data in self.G.in_edges(node, data=True):
                if data.get("edge_type") != "beaten":
                    continue
                if u[0] in visited_horses:
                    continue
                edge_w = data.get("weight", 1.0)
                contrib = next_decay * (1.0 / (1.0 + edge_w))
                if contrib < prune:
                    continue
                new_visited = visited_horses | {u[0]}
                queue.append((u, next_gen, contrib, path + [u], new_visited))

        if not all_paths:
            result = {"franking_score": 0.0, "franking_depth": 0,
                      "franking_independence": 0, "best_frank_chains": []}
            self._franking_cache[horse_id] = result
            return result

        individual_strengths = [p[0] for p in all_paths]
        capped = [min(s, 0.99) for s in individual_strengths]
        combined = 1.0 - math.prod(1.0 - s for s in capped[:50])
        franking_score = max(-1.0, min(1.0, combined * 2.0 - 1.0))

        all_paths.sort(key=lambda x: x[0], reverse=True)
        best_chains = []
        for score, depth, path, _ in all_paths[:5]:
            names = []
            for n in path:
                d = self._raw_data.get(n, {})
                names.append(f"{d.get('horse_name', n[0])}(R{d.get('race_date', '?')[-5:]},P{d.get('position', '?')})")
            chain_str = f"[{score:.3f}] " + " -> ".join(names)
            best_chains.append(chain_str)

        result = {
            "franking_score": round(franking_score, 4),
            "franking_depth": max_depth,
            "franking_independence": len(independent_roots),
            "best_frank_chains": best_chains,
        }
        self._franking_cache[horse_id] = result
        return result

    def _path_quality_score(self, path):
        """Score a franking path for consistency, class gradient, temporal coherence."""
        if not path:
            return 1.0

        multiplier = 1.0

        odds_values = []
        class_levels = []
        dates = []
        trainers = set()

        for node in path:
            d = self._raw_data.get(node, {})
            odds_values.append(d.get("sp_odds", 10.0))
            class_levels.append(d.get("class_level", 1))
            dates.append(d.get("race_date", "2020-01-01"))
            tr = d.get("trainer", "")
            if tr:
                trainers.add(tr)

        avg_odds = np.mean(odds_values) if odds_values else 10.0
        if avg_odds < 5.0:
            multiplier *= CONFIG["consistency_bonus_max"]
        elif avg_odds < 10.0:
            multiplier *= 1.0 + 0.3 * (10.0 - avg_odds) / 5.0

        for i in range(len(class_levels) - 1):
            if class_levels[i + 1] > class_levels[i]:
                step = class_levels[i + 1] - class_levels[i]
                multiplier *= CONFIG["class_step_bonus"] ** step

        if len(dates) >= 2:
            for i in range(len(dates) - 1):
                gap = _days_between(dates[i], dates[i + 1])
                if gap > 90:
                    multiplier *= math.exp(-0.693 * (gap - 90) / 90.0)

        if len(trainers) == 1 and len(path) > 1:
            multiplier *= CONFIG["trainer_stability_bonus"]

        return multiplier


    def _get_excuse_detector(self):
        """Return an ExcuseDetector bound to this graph's data."""
        return ExcuseDetector(self)


    def compute_form_stability(self, horse_id):
        """
        Rolling StdDev of position-weighted performance ratings.
        Returns float 0.0-1.0; consistent horses near 0.
        """
        try:
            nodes = self._horse_races.get(horse_id, [])
            if len(nodes) < 2:
                return 0.5

            sorted_nodes = sorted(nodes, key=lambda n: self._raw_data[n]["race_date"])
            recent = sorted_nodes[-10:]

            ratings = []
            for n in recent:
                d = self._raw_data[n]
                pos = d["position"]
                field = max(d["field_size"], 2)
                pct = 1.0 - (pos - 1) / (field - 1) if field > 1 else 0.5
                class_w = d["class_level"] / 10.0
                rating = pct * (0.7 + 0.3 * class_w)
                ratings.append(rating)

            stddev = float(np.std(ratings))
            stability = 1.0 / (1.0 + stddev * 2.0)
            return round(max(0.0, min(1.0, stability)), 4)
        except Exception as e:
            logger.error("Form stability error for %s: %s", horse_id, e)
            return 0.5

    def compute_race_quality(self, race_id):
        """
        Compute race quality: initial quality, retroactive quality, cohort divergence.

        Returns dict with quality metrics.
        """
        try:
            return self._race_quality_impl(race_id)
        except Exception as e:
            logger.error("Race quality error for %s: %s", race_id, e)
            return {"quality_score": 0.5, "reliability": 0.0, "cohort_divergence": 0.0}

    def _race_quality_impl(self, race_id):
        nodes = self._race_runners.get(race_id, [])
        if not nodes:
            return {"quality_score": 0.5, "reliability": 0.0, "cohort_divergence": 0.0}

        class_levels = []
        field_size = 0
        for n in nodes:
            d = self._raw_data[n]
            class_levels.append(d["class_level"])
            field_size = max(field_size, d["field_size"])

        avg_class = np.mean(class_levels) if class_levels else 3.0
        field_weight = math.log(max(field_size, 2)) / math.log(16)
        initial_quality = (avg_class / 10.0) * (0.5 + 0.5 * field_weight)

        subsequent_perfs = []
        finish_positions = []
        for n in nodes:
            d = self._raw_data[n]
            horse_id = d["horse_id"]
            race_date = d["race_date"]
            finish_positions.append(d["position"])

            later_nodes = [
                ln for ln in self._horse_races.get(horse_id, [])
                if self._raw_data[ln]["race_date"] > race_date
            ]
            later_nodes = sorted(later_nodes, key=lambda x: self._raw_data[x]["race_date"])[:3]

            if later_nodes:
                perfs = []
                for ln in later_nodes:
                    ld = self._raw_data[ln]
                    fs = max(ld["field_size"], 2)
                    pct = 1.0 - (ld["position"] - 1) / (fs - 1) if fs > 1 else 0.5
                    perfs.append(pct)
                subsequent_perfs.append(np.mean(perfs))
            else:
                subsequent_perfs.append(0.5)

        retro = float(np.mean(subsequent_perfs)) if subsequent_perfs else 0.5
        quality_score = initial_quality * 0.4 + retro * 0.6

        cohort_div = 0.0
        if len(finish_positions) >= 3 and len(subsequent_perfs) >= 3:
            try:
                corr = float(np.corrcoef(finish_positions, subsequent_perfs)[0, 1])
                if not np.isnan(corr):
                    cohort_div = corr
            except Exception:
                pass

        reliability = min(1.0, len(nodes) / 12.0) * min(1.0, len([s for s in subsequent_perfs if s != 0.5]) / max(len(nodes), 1))

        return {
            "quality_score": round(max(0.0, min(1.0, quality_score)), 4),
            "reliability": round(reliability, 4),
            "cohort_divergence": round(cohort_div, 4),
        }


    def compute_anti_franking(self, horse_id):
        """
        Enhanced anti-franking with excuse detection and magnitude scaling.

        Returns dict with anti_franked, ratio, excuse_probability.
        """
        try:
            return self._anti_franking_impl(horse_id)
        except Exception as e:
            logger.error("Anti-franking error for %s: %s", horse_id, e)
            return {"anti_franked": False, "raw_ratio": 0.5,
                    "excuse_adjusted_ratio": 0.5, "excuse_adjusted_count": 0}

    def _anti_franking_impl(self, horse_id):
        nodes = self._horse_races.get(horse_id, [])
        if not nodes:
            return {"anti_franked": False, "raw_ratio": 0.5,
                    "excuse_adjusted_ratio": 0.5, "excuse_adjusted_count": 0}

        detector = self._get_excuse_detector()
        sorted_nodes = sorted(nodes, key=lambda n: self._raw_data[n]["race_date"],
                              reverse=True)[:10]

        total_checked = 0
        raw_poor = 0
        excuse_adjusted_poor = 0

        for node in sorted_nodes:
            d = self._raw_data[node]
            race_id = d["race_id"]
            race_date = d["race_date"]
            horse_pos = d["position"]

            for v_node in self._race_runners.get(race_id, []):
                vd = self._raw_data[v_node]
                if vd["horse_id"] == horse_id:
                    continue
                if vd["position"] <= horse_pos:
                    continue

                opp_id = vd["horse_id"]
                later = [
                    ln for ln in self._horse_races.get(opp_id, [])
                    if self._raw_data[ln]["race_date"] > race_date
                ]
                later = sorted(later, key=lambda x: self._raw_data[x]["race_date"])[:1]
                if not later:
                    continue

                ld = self._raw_data[later[0]]
                next_pos = ld["position"]
                next_field = max(ld["field_size"], 3)
                total_checked += 1
                bottom_third = next_field * 2.0 / 3.0

                if next_pos > bottom_third:
                    raw_poor += 1
                    excuse_prob = detector.detect_excuse(opp_id, ld["race_id"])
                    if excuse_prob < 0.4:
                        sp = ld.get("sp_odds", 5.0)
                        implied_prob = 1.0 / max(sp, 1.01)
                        expected_prob = 1.0 / max(next_field, 2)
                        try:
                            magnitude = abs(math.log(max(implied_prob, 0.01) / max(expected_prob, 0.01)))
                        except (ValueError, ZeroDivisionError):
                            magnitude = 1.0
                        excuse_adjusted_poor += min(magnitude, 2.0)

        if total_checked == 0:
            return {"anti_franked": False, "raw_ratio": 0.5,
                    "excuse_adjusted_ratio": 0.5, "excuse_adjusted_count": 0}

        raw_ratio = raw_poor / total_checked
        excuse_ratio = excuse_adjusted_poor / total_checked
        anti_franked = raw_ratio > CONFIG["anti_frank_threshold"]

        return {
            "anti_franked": anti_franked,
            "raw_ratio": round(raw_ratio, 4),
            "excuse_adjusted_ratio": round(min(excuse_ratio, 1.0), 4),
            "excuse_adjusted_count": int(excuse_adjusted_poor),
        }


    def compute_pagerank(self):
        """
        Run NetworkX PageRank on beaten-by subgraph with damping=0.85.

        Returns dict of {horse_id: pagerank_authority_score}.
        """
        try:
            if self._pagerank_cache is not None:
                return self._pagerank_cache

            if self.horse_graph.number_of_nodes() == 0:
                return {}

            raw_pr = nx.pagerank(self.horse_graph, alpha=CONFIG["pagerank_damping"],
                                 weight="weight", max_iter=100, tol=1e-6)

            if not raw_pr:
                return {}

            vals = list(raw_pr.values())
            mn, mx = min(vals), max(vals)
            spread = mx - mn if mx > mn else 1.0

            normalised = {k: round((v - mn) / spread, 6) for k, v in raw_pr.items()}
            self._pagerank_cache = normalised
            logger.info("PageRank computed for %d horses", len(normalised))
            return normalised
        except Exception as e:
            logger.error("PageRank failed: %s", e)
            return {}

    def compute_personalised_pagerank(self, horse_id):
        """
        Personalised PageRank with restart concentrated on target horse's recent nodes.

        Returns horse-centric authority scores.
        """
        try:
            if self.horse_graph.number_of_nodes() == 0:
                return 0.5

            if horse_id not in self.horse_graph:
                return 0.5

            personalization = {n: 0.0 for n in self.horse_graph.nodes()}
            personalization[horse_id] = 1.0

            raw_pr = nx.pagerank(self.horse_graph, alpha=CONFIG["pagerank_damping"],
                                 personalization=personalization,
                                 weight="weight", max_iter=100, tol=1e-6)

            val = raw_pr.get(horse_id, 0.0)
            vals = list(raw_pr.values())
            mx = max(vals) if vals else 1.0
            return round(min(val / max(mx, 1e-10), 1.0), 6)
        except Exception as e:
            logger.error("Personalised PageRank failed for %s: %s", horse_id, e)
            return 0.5


    def detect_communities(self):
        """
        Louvain community detection on horse-level graph.

        Returns {horse_id: {community_id: int, community_strength: float}}.
        """
        try:
            if self._community_cache is not None:
                return self._community_cache

            if not LOUVAIN_AVAILABLE:
                logger.warning("python-louvain not available, skipping community detection")
                return {}

            undirected = self.horse_graph.to_undirected()
            if undirected.number_of_nodes() == 0:
                return {}

            partition = community_louvain.best_partition(undirected, weight="weight",
                                                         random_state=42)

            communities = defaultdict(list)
            for horse_id, comm_id in partition.items():
                communities[comm_id].append(horse_id)

            comm_quality = {}
            for comm_id, members in communities.items():
                if len(members) < 2:
                    comm_quality[comm_id] = 0.5
                    continue

                external_perfs = []
                for hid in members[:50]:
                    nodes = self._horse_races.get(hid, [])
                    for n in nodes[-5:]:
                        d = self._raw_data[n]
                        race_nodes = self._race_runners.get(d["race_id"], [])
                        external_in_race = [
                            rn for rn in race_nodes
                            if self._raw_data[rn]["horse_id"] not in set(members)
                        ]
                        if external_in_race:
                            fs = max(d["field_size"], 2)
                            pct = 1.0 - (d["position"] - 1) / (fs - 1) if fs > 1 else 0.5
                            external_perfs.append(pct)

                comm_quality[comm_id] = float(np.mean(external_perfs)) if external_perfs else 0.5

            result = {}
            for horse_id, comm_id in partition.items():
                result[horse_id] = {
                    "community_id": comm_id,
                    "community_strength": round(max(0.0, min(1.0, comm_quality.get(comm_id, 0.5))), 4),
                }

            self._community_cache = result
            logger.info("Community detection: %d communities found", len(communities))
            return result
        except Exception as e:
            logger.error("Community detection failed: %s", e)
            return {}

    def compute_bridge_score(self, horse_id):
        """
        Betweenness centrality of horse across communities.
        Returns normalised 0-1.
        """
        try:
            if self._betweenness_cache is None:
                undirected = self.horse_graph.to_undirected()
                if undirected.number_of_nodes() == 0:
                    return 0.0
                k = min(100, undirected.number_of_nodes())
                bc = nx.betweenness_centrality(undirected, k=k, weight="weight",
                                                normalized=True)
                self._betweenness_cache = bc

            val = self._betweenness_cache.get(horse_id, 0.0)
            vals = list(self._betweenness_cache.values())
            mx = max(vals) if vals else 1.0
            return round(min(val / max(mx, 1e-10), 1.0), 6)
        except Exception as e:
            logger.error("Bridge score failed for %s: %s", horse_id, e)
            return 0.0


    def check_market_validation(self, horse_id):
        """
        Check if franking events are validated by subsequent market movements.

        Returns {market_validated: bool, ...}.
        """
        try:
            return self._market_validation_impl(horse_id)
        except Exception as e:
            logger.error("Market validation failed for %s: %s", horse_id, e)
            return {"market_validated": False, "market_agreement_score": 0.5}

    def _market_validation_impl(self, horse_id):
        nodes = self._horse_races.get(horse_id, [])
        if len(nodes) < 2:
            return {"market_validated": False, "market_agreement_score": 0.5}

        sorted_nodes = sorted(nodes, key=lambda n: self._raw_data[n]["race_date"])
        agreements = 0
        total = 0

        for i in range(len(sorted_nodes) - 1):
            curr = self._raw_data[sorted_nodes[i]]
            nxt = self._raw_data[sorted_nodes[i + 1]]

            was_franked = curr["position"] <= 3

            if was_franked and total < 20:
                curr_odds = curr["sp_odds"]
                next_odds = nxt["sp_odds"]
                total += 1

                if next_odds < curr_odds * 0.9:
                    agreements += 1
                elif next_odds > curr_odds * 1.3:
                    pass

        if total == 0:
            return {"market_validated": False, "market_agreement_score": 0.5}

        agreement_score = agreements / total
        market_validated = agreement_score >= 0.5

        if not market_validated:
            agreement_score *= CONFIG["market_scepticism_discount"]

        return {
            "market_validated": market_validated,
            "market_agreement_score": round(agreement_score, 4),
        }


    def compute_full_franking_profile(self, horse_id):
        """
        Comprehensive franking profile combining all analysis layers.

        Returns dict with all franking metrics.
        """
        try:
            return self._full_profile_impl(horse_id)
        except Exception as e:
            logger.error("Full profile failed for %s: %s", horse_id, e)
            return self._default_profile()

    def _full_profile_impl(self, horse_id):
        deep = self.compute_deep_franking(horse_id)
        anti = self.compute_anti_franking(horse_id)
        stability = self.compute_form_stability(horse_id)
        pr = self.compute_pagerank()
        ppr = self.compute_personalised_pagerank(horse_id)
        communities = self.detect_communities()
        bridge = self.compute_bridge_score(horse_id)
        market = self.check_market_validation(horse_id)
        momentum = self._compute_franking_momentum(horse_id)
        confidence = self._compute_franking_confidence(horse_id, deep)
        collapsed = self._compute_collapsed_exposure(horse_id)

        comm_data = communities.get(horse_id, {"community_id": -1, "community_strength": 0.5})

        return {
            "franking_score": deep.get("franking_score", 0.0),
            "franking_momentum": momentum,
            "franking_confidence": confidence,
            "franking_depth": deep.get("franking_depth", 0),
            "franking_independence": deep.get("franking_independence", 0),
            "pagerank_authority": pr.get(horse_id, 0.5),
            "personalised_pagerank": ppr,
            "community_strength": comm_data.get("community_strength", 0.5),
            "community_id": comm_data.get("community_id", -1),
            "form_stability": stability,
            "market_validated": market.get("market_validated", False),
            "market_agreement_score": market.get("market_agreement_score", 0.5),
            "excuse_adjusted_anti_franks": anti.get("excuse_adjusted_count", 0),
            "collapsed_race_exposure": collapsed,
            "best_frank_chains": deep.get("best_frank_chains", []),
            "bridge_score": bridge,
            "anti_franked": anti.get("anti_franked", False),
            "anti_frank_ratio": anti.get("raw_ratio", 0.5),
        }

    def _compute_franking_momentum(self, horse_id):
        """Trend of franking quality over last 3-5 runs. Returns -1.0 to 1.0."""
        try:
            nodes = self._horse_races.get(horse_id, [])
            if len(nodes) < 3:
                return 0.0

            sorted_nodes = sorted(nodes, key=lambda n: self._raw_data[n]["race_date"])
            recent = sorted_nodes[-5:]

            scores = []
            for n in recent:
                d = self._raw_data[n]
                fs = max(d["field_size"], 2)
                pct = 1.0 - (d["position"] - 1) / (fs - 1) if fs > 1 else 0.5
                class_bonus = d["class_level"] / 10.0
                scores.append(pct * (0.6 + 0.4 * class_bonus))

            if len(scores) < 2:
                return 0.0

            x = np.arange(len(scores), dtype=float)
            y = np.array(scores, dtype=float)
            slope, _ = np.polyfit(x, y, 1)
            momentum = max(-1.0, min(1.0, slope * 5.0))
            return round(momentum, 4)
        except Exception:
            return 0.0

    def _compute_franking_confidence(self, horse_id, deep_result):
        """Confidence in franking score based on data depth and consistency."""
        try:
            depth = deep_result.get("franking_depth", 0)
            independence = deep_result.get("franking_independence", 0)
            n_races = len(self._horse_races.get(horse_id, []))

            data_factor = min(1.0, n_races / 10.0)
            depth_factor = min(1.0, depth / 3.0)
            independence_factor = min(1.0, independence / 5.0)

            confidence = data_factor * 0.4 + depth_factor * 0.3 + independence_factor * 0.3
            return round(max(0.0, min(1.0, confidence)), 4)
        except Exception:
            return 0.1

    def _compute_collapsed_exposure(self, horse_id):
        """Fraction of franking paths that share common race sources."""
        try:
            nodes = self._horse_races.get(horse_id, [])
            if not nodes:
                return 0.0

            race_ids = set()
            franking_race_ids = set()
            for n in nodes:
                d = self._raw_data[n]
                race_ids.add(d["race_id"])
                for v_node in self._race_runners.get(d["race_id"], []):
                    vd = self._raw_data[v_node]
                    if vd["horse_id"] != horse_id and vd["position"] < d["position"]:
                        for ln in self._horse_races.get(vd["horse_id"], []):
                            ld = self._raw_data[ln]
                            if ld["race_date"] > d["race_date"]:
                                franking_race_ids.add(ld["race_id"])

            if not franking_race_ids:
                return 0.0

            overlap = len(race_ids & franking_race_ids)
            return round(overlap / max(len(franking_race_ids), 1), 4)
        except Exception:
            return 0.0

    @staticmethod
    def _default_profile():
        return {
            "franking_score": 0.0,
            "franking_momentum": 0.0,
            "franking_confidence": 0.0,
            "franking_depth": 0,
            "franking_independence": 0,
            "pagerank_authority": 0.5,
            "personalised_pagerank": 0.5,
            "community_strength": 0.5,
            "community_id": -1,
            "form_stability": 0.5,
            "market_validated": False,
            "market_agreement_score": 0.5,
            "excuse_adjusted_anti_franks": 0,
            "collapsed_race_exposure": 0.0,
            "best_frank_chains": [],
            "bridge_score": 0.0,
            "anti_franked": False,
            "anti_frank_ratio": 0.5,
        }



class ExcuseDetector:
    """Detects legitimate excuses for poor performances."""

    def __init__(self, form_graph):
        self._fg = form_graph

    def detect_excuse(self, horse_id, race_id):
        """
        Detect excuse probability for a horse in a specific race.

        Returns float 0.0-1.0.
        """
        try:
            return self._detect_impl(horse_id, race_id)
        except Exception as e:
            logger.error("Excuse detection error %s/%s: %s", horse_id, race_id, e)
            return 0.0

    def _detect_impl(self, horse_id, race_id):
        node = (horse_id, race_id)
        d = self._fg._raw_data.get(node)
        if not d:
            return 0.0

        excuse_prob = 0.0

        barrier = d["barrier"]
        field_size = d["field_size"]
        if field_size < 14 and barrier >= CONFIG["excuse_wide_barrier_small"]:
            excuse_prob += 0.25
        elif barrier >= CONFIG["excuse_wide_barrier_any"]:
            excuse_prob += 0.25

        nodes = self._fg._horse_races.get(horse_id, [])
        sorted_nodes = sorted(nodes, key=lambda n: self._fg._raw_data[n]["race_date"])
        curr_idx = None
        for i, n in enumerate(sorted_nodes):
            if n == node:
                curr_idx = i
                break

        if curr_idx is not None and curr_idx > 0:
            prev = self._fg._raw_data[sorted_nodes[curr_idx - 1]]
            days_since = _days_between(prev["race_date"], d["race_date"])
            if days_since > CONFIG["excuse_spell_days"]:
                excuse_prob += 0.30

            weight_diff = d["weight_kg"] - prev["weight_kg"]
            if weight_diff >= CONFIG["excuse_weight_increase_kg"]:
                excuse_prob += 0.20
        elif curr_idx == 0:
            excuse_prob += 0.30

        curr_going = _normalise_going(d["going"])
        if curr_idx is not None:
            going_history = []
            for n in sorted_nodes[:curr_idx]:
                going_history.append(_normalise_going(self._fg._raw_data[n]["going"]))
            if going_history:
                from collections import Counter
                most_common = Counter(going_history).most_common(1)[0][0]
                mismatch = False
                if curr_going == "heavy" and most_common in ("good", "firm"):
                    mismatch = True
                if curr_going in ("good", "firm") and most_common == "heavy":
                    mismatch = True
                if mismatch:
                    excuse_prob += 0.20

        sp = d["sp_odds"]
        pos = d["position"]
        fs = max(field_size, 2)
        expected_odds = fs / max(pos, 1)
        if sp > expected_odds * (1.0 + CONFIG["excuse_market_drift_pct"]):
            excuse_prob += 0.15

        return min(excuse_prob, CONFIG["excuse_cap"])



_instance = None


def get_franking_graph():
    """Return the singleton FormGraph instance, building if necessary."""
    global _instance
    if _instance is None:
        _instance = FormGraph()
        _instance.load_or_build()
    return _instance


def get_franking_profile(horse_id):
    """Convenience: compute full franking profile for a horse."""
    try:
        fg = get_franking_graph()
        return fg.compute_full_franking_profile(horse_id)
    except Exception as e:
        logger.error("get_franking_profile failed for %s: %s", horse_id, e)
        return FormGraph._default_profile()


def get_franking_for_field(horse_ids):
    """Compute franking profiles for a list of horse IDs."""
    try:
        fg = get_franking_graph()
        results = {}
        for hid in horse_ids:
            results[hid] = fg.compute_full_franking_profile(hid)
        return results
    except Exception as e:
        logger.error("get_franking_for_field failed: %s", e)
        return {hid: FormGraph._default_profile() for hid in horse_ids}


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
    import sys

    fg = FormGraph()
    logger.info("Building graph from database...")
    fg.load_or_build()

    logger.info("Graph built: %d nodes, %d edges",
                fg.G.number_of_nodes(), fg.G.number_of_edges())
    logger.info("Horse-level graph: %d nodes, %d edges",
                fg.horse_graph.number_of_nodes(), fg.horse_graph.number_of_edges())

    if fg._horse_races:
        sample_id = list(fg._horse_races.keys())[0]
        logger.info("Sample profile for %s:", sample_id)
        profile = fg.compute_full_franking_profile(sample_id)
        print(json.dumps(profile, indent=2, default=str))
