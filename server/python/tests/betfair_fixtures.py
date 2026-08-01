"""Shared canned Betfair payloads and fakes — every test runs with zero
network and zero database."""

import json

DATE = "2026-08-01"

# listMarketCatalogue result: two mappable Randwick WIN markets, one market
# at a venue race_schedule has never heard of.
CATALOGUE = [
    {
        "marketId": "1.111", "marketName": "R1 1200m Hcap",
        "marketStartTime": "2026-08-01T03:15:00.000Z",
        "event": {"id": "e1", "name": "Randwick (AUS) 1st Aug",
                  "venue": "Randwick", "countryCode": "AU",
                  "timezone": "Australia/Sydney"},
        "runners": [
            {"selectionId": 111, "runnerName": "1. Fast Horse", "sortPriority": 1},
            {"selectionId": 222, "runnerName": "2. Mystery Guest (NZ)", "sortPriority": 2},
        ],
    },
    {
        "marketId": "1.222", "marketName": "R2 1400m Mdn",
        "marketStartTime": "2026-08-01T03:50:00.000Z",
        "event": {"id": "e1", "name": "Randwick (AUS) 1st Aug", "venue": "Randwick"},
        "runners": [
            {"selectionId": 333, "runnerName": "4. Second String", "sortPriority": 1},
            {"selectionId": 444, "runnerName": "5. Late Scratch", "sortPriority": 2},
            {"selectionId": 555, "runnerName": "6. No Market", "sortPriority": 3},
        ],
    },
    {
        "marketId": "1.999", "marketName": "R1 1000m 2yo",
        "marketStartTime": "2026-08-01T05:00:00.000Z",
        "event": {"id": "e9", "name": "Nowhere Park 1st Aug", "venue": "Nowhere Park"},
        "runners": [{"selectionId": 999, "runnerName": "1. Ghost Runner"}],
    },
]

# race_schedule rows as the cursor returns them: (track, race_number,
# race_date::text, off_time). off_time is naive local (AEST) — 13:15 AEST
# is the 03:15Z marketStartTime above.
SCHEDULE_ROWS = [
    ("Royal Randwick", 1, DATE, "2026-08-01T13:15:00"),
    ("Royal Randwick", 2, DATE, "2026-08-01T13:50:00"),
]

# Horse-id bridge rows as race_results_history stores them — ORIGINAL case,
# which is what BRIDGE_SQL now projects so norm_name can strip an uppercase
# country suffix. FakeCursor lowercases these iff the executed SQL projects
# LOWER(horse_name), faithfully reproducing the forking bug a regression would
# reintroduce. Mystery Guest is deliberately absent — the unknown-runner path.
BRIDGE_ROWS = [
    ("Fast Horse", "h_fast"),
    ("Second String", "h_second"),
    ("Late Scratch", "h_late"),
    ("No Market", "h_nomarket"),
]

# listMarketBook result for the two mapped markets. Late Scratch is REMOVED
# and No Market has no offers/trades — both must be skipped, not guessed.
BOOKS = [
    {
        "marketId": "1.111", "totalMatched": 54321.0, "runners": [
            {"selectionId": 111, "status": "ACTIVE", "lastPriceTraded": 3.55,
             "totalMatched": 1500.5,
             "ex": {"availableToBack": [{"price": 3.5, "size": 120.5},
                                        {"price": 3.45, "size": 80.0}],
                    "availableToLay": [{"price": 3.7, "size": 60.0}]}},
            {"selectionId": 222, "status": "ACTIVE", "lastPriceTraded": 8.0,
             "totalMatched": 400.0,
             "ex": {"availableToBack": [{"price": 8.2, "size": 30.0}],
                    "availableToLay": []}},
        ],
    },
    {
        "marketId": "1.222", "totalMatched": 1000.0, "runners": [
            {"selectionId": 333, "status": "ACTIVE", "lastPriceTraded": None,
             "totalMatched": 250.0,
             "ex": {"availableToBack": [{"price": 6.0, "size": 42.0}],
                    "availableToLay": [{"price": 6.4, "size": 18.0}]}},
            {"selectionId": 444, "status": "REMOVED", "lastPriceTraded": 12.0,
             "ex": {"availableToBack": [], "availableToLay": []}},
            {"selectionId": 555, "status": "ACTIVE", "lastPriceTraded": None,
             "ex": {"availableToBack": [], "availableToLay": []}},
        ],
    },
]

APP_KEYS_DELAYED = [{"appName": "Stride-Racing", "appVersions": [
    {"applicationKey": "test-app-key", "delayData": True, "active": True}]}]
APP_KEYS_LIVE = [{"appName": "Stride-Racing", "appVersions": [
    {"applicationKey": "test-app-key", "delayData": False, "active": True}]}]


def make_post(catalogue=CATALOGUE, books=BOOKS, appkeys=APP_KEYS_DELAYED, calls=None):
    """JSON-RPC transport fake routed by method name."""
    calls = calls if calls is not None else []

    def post(url, payload, headers, timeout=30):
        method = payload["method"]
        calls.append((url, method))
        if method.endswith("getDeveloperAppKeys"):
            result = appkeys
        elif method.endswith("listMarketCatalogue"):
            result = catalogue
        elif method.endswith("listMarketBook"):
            wanted = set(payload["params"]["marketIds"])
            result = [b for b in books if b["marketId"] in wanted]
        else:
            raise AssertionError(f"unexpected RPC method {method}")
        return 200, json.dumps({"jsonrpc": "2.0", "result": result, "id": 1})

    post.calls = calls
    return post


class FakeCursor:
    def __init__(self, conn):
        self.conn = conn
        self._result = []

    def execute(self, sql, params=None):
        self.conn.executed.append((sql, params))
        text = " ".join(sql.split()).lower()
        if "from race_schedule" in text:
            self._result = self.conn.schedule_rows
        elif "from race_results_history" in text:
            # Reproduce Postgres faithfully: the projected horse_name column is
            # lowercased only if the SELECT list (after the DISTINCT ON paren)
            # projects LOWER(horse_name). The buggy projection thus hands
            # norm_name a pre-lowercased "(nz)" it can no longer strip.
            projection = text.split(" from ")[0].split("distinct on (lower(horse_name))", 1)[-1]
            lowered = "lower(horse_name)" in projection
            self._result = [(name.lower() if lowered else name, hid)
                            for name, hid in self.conn.bridge_rows]
        else:
            self._result = []

    def executemany(self, sql, seq_of_params):
        rows = list(seq_of_params)
        self.conn.executed.append((sql, rows))
        text = " ".join(sql.split()).lower()
        dedup = "on conflict" in text and "do nothing" in text
        for row in rows:
            # PK per migrations/runner_odds_snapshots.sql:
            # (race_id, runner_id, snapshot_kind, bookmaker, captured_at)
            key = tuple(row[:5])
            if key in self.conn.keys:
                if dedup:
                    continue
                raise Exception(
                    'duplicate key value violates unique constraint '
                    '"runner_odds_snapshots_pkey"')
            self.conn.keys.add(key)
            self.conn.pending.append(row)

    def fetchall(self):
        return self._result

    def close(self):
        pass


class FakeConn:
    """Enough of a psycopg2 connection for read paths + append-only inserts,
    with real duplicate-key behaviour so the ON CONFLICT contract is tested."""

    def __init__(self, schedule_rows=SCHEDULE_ROWS, bridge_rows=BRIDGE_ROWS):
        self.schedule_rows = list(schedule_rows)
        self.bridge_rows = list(bridge_rows)
        self.executed = []
        self.rows = []
        self.pending = []
        self.keys = set()
        self.commits = 0
        self.rollbacks = 0

    def cursor(self):
        return FakeCursor(self)

    def commit(self):
        self.commits += 1
        self.rows.extend(self.pending)
        self.pending = []

    def rollback(self):
        self.rollbacks += 1
        for row in self.pending:
            self.keys.discard(tuple(row[:5]))
        self.pending = []

    def close(self):
        pass

    def insert_statements(self):
        return [sql for sql, _ in self.executed if sql.lstrip().lower().startswith("insert")]
