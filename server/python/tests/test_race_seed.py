"""The MC seed must be a function of race identity, not of the wall clock.

With `int(time.time())` every re-run produced a different tips file: a bad
tip could never be reproduced, and an A/B of two code versions on one card
compared two different sets of draws. `_race_seed` pins draws to the race.
"""

from run_tips_pipeline import _race_seed


def test_same_race_same_seed(monkeypatch):
    monkeypatch.delenv("STRIDE_MC_SEED_SALT", raising=False)
    a = _race_seed("2026-08-08", "Randwick", 5)
    b = _race_seed("2026-08-08", "Randwick", 5)
    assert a == b


def test_races_on_a_card_are_decorrelated(monkeypatch):
    monkeypatch.delenv("STRIDE_MC_SEED_SALT", raising=False)
    tracks = ["Randwick", "Eagle Farm", "Morphettville", "Belmont Park",
              "Caulfield Heath"]
    seeds = {_race_seed("2026-08-08", t, r) for t in tracks
             for r in range(1, 11)}
    # 50 races -> 50 distinct seeds barring a 1-in-2000 birthday collision;
    # allow one, refuse systematic collapse.
    assert len(seeds) >= 49


def test_seed_is_in_the_legacy_range(monkeypatch):
    monkeypatch.delenv("STRIDE_MC_SEED_SALT", raising=False)
    for r in range(1, 13):
        s = _race_seed("2026-08-08", "Randwick", r)
        assert 0 <= s < 100000


def test_salt_gives_independent_draws_on_purpose(monkeypatch):
    monkeypatch.delenv("STRIDE_MC_SEED_SALT", raising=False)
    base = _race_seed("2026-08-08", "Randwick", 5)
    monkeypatch.setenv("STRIDE_MC_SEED_SALT", "resample-1")
    assert _race_seed("2026-08-08", "Randwick", 5) != base


def test_date_and_race_number_both_matter(monkeypatch):
    monkeypatch.delenv("STRIDE_MC_SEED_SALT", raising=False)
    assert (_race_seed("2026-08-08", "Randwick", 5)
            != _race_seed("2026-08-09", "Randwick", 5))
    assert (_race_seed("2026-08-08", "Randwick", 5)
            != _race_seed("2026-08-08", "Randwick", 6))
