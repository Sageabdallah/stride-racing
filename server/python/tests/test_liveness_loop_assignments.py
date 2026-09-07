"""The static liveness audit must see loop assignments.

serve_features.py plumbs the sectional set as `for k in SECTIONAL_LIVE_FEATURES:
feat[k] = ...`. The audit's line regexes look for the literal name as a
subscript or dict key, so every feature assigned that way read REFERENCED_ONLY
at serve — and the audit's own z_* tripwire (`_self_test`) fired, unnoticed,
because nothing ran it. The AST pass credits loop assignments over literal
collections only, and never over the declaration lists the regex pass masks.
"""

import sys
from pathlib import Path

SERVER_PYTHON = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SERVER_PYTHON))

import feature_liveness_audit as fla  # noqa: E402


def test_inline_literal_and_module_constant_loops_are_assignments():
    src = (
        'NAMES = ("alpha", "beta")\n'
        'MORE = NAMES + ("gamma",)\n'
        'def f(r):\n'
        '    feat = {}\n'
        '    for k in MORE:\n'
        '        feat[k] = r.get(k)\n'
        '    for k in ["delta"]:\n'
        '        feat[k] = r.get(k)\n'
    )
    ev = fla._loop_assignment_evidence(src, "x.py")
    assert set(ev) == {"alpha", "beta", "gamma", "delta"}
    assert ev["gamma"] == ["x.py:5: for k in MORE: feat[k] = ... (loop assignment)"]


def test_constant_fills_are_placeholders_not_liveness():
    """A loop that assigns a CONSTANT fills the column; it is not evidence
    that anything computes it. retrain_v2's except handler does exactly this
    for the Phase-5 market trio (`for _c in (...): out[_c] = 0.0`), and on
    that evidence alone all three read LIVE_BOTH — a fallback standing in for
    the feature."""
    src = (
        'def f(out, r):\n'
        '    for c in ("zero_filled", "nan_filled", "none_filled", "str_filled"):\n'
        '        out[c] = 0.0\n'
        '    for c in ["nan_filled"]:\n'
        '        out[c] = np.nan\n'
        '    for c in ["none_filled"]:\n'
        '        out[c] = None\n'
        '    for c in ["str_filled"]:\n'
        '        out[c] = float("nan")\n'
    )
    assert fla._loop_assignment_evidence(src, "x.py") == {}

    # a loop that defaults AND computes still counts: one real assignment is enough
    both = (
        'def f(out, r):\n'
        '    for c in ["computed"]:\n'
        '        out[c] = 0.0\n'
        '        out[c] = r.get(c)\n'
    )
    assert set(fla._loop_assignment_evidence(both, "x.py")) == {"computed"}


def test_declaration_lists_cannot_be_laundered_through_an_alias_or_a_sum():
    """The exclusion recognised only a bare `for col in FEATURE_COLUMNS`. An
    alias or a `+` walked past it and credited every declared feature from one
    placeholder loop — the exact evidence the exclusion exists to reject."""
    src = (
        'FEATURE_COLUMNS = ["a", "b"]\n'
        'ALIAS = FEATURE_COLUMNS\n'
        'ALIAS2 = ALIAS\n'
        'def f(out, r):\n'
        '    for c in ALIAS:\n'
        '        out[c] = r.get(c)\n'
        '    for c in ALIAS2:\n'
        '        out[c] = r.get(c)\n'
        '    for c in FEATURE_COLUMNS + ["appended"]:\n'
        '        out[c] = r.get(c)\n'
    )
    assert fla._loop_assignment_evidence(src, "x.py") == {}


def test_declaration_lists_never_count_as_liveness():
    src = (
        'FEATURE_COLUMNS = ["a", "b"]\n'
        'NAN_PRESERVE_FEATURES = ["b"]\n'
        'def f(out):\n'
        '    for col in FEATURE_COLUMNS:\n'
        '        out[col] = None\n'
        '    for col in NAN_PRESERVE_FEATURES:\n'
        '        out[col] = float("nan")\n'
    )
    assert fla._loop_assignment_evidence(src, "x.py") == {}


def test_computed_iterables_and_non_subscript_targets_contribute_nothing():
    src = (
        'SET_A = ("a", "b")\n'
        'from elsewhere import IMPORTED\n'
        'def f(out, mask):\n'
        '    for c in [x for x in SET_A]:\n'          # comprehension
        '        out[c] = 1\n'
        '    for c in IMPORTED:\n'                    # not resolvable in-file
        '        out[c] = 1\n'
        '    for c in SET_A:\n'
        '        out.loc[mask, c] = 2\n'              # not X[c]
        '    for c in SET_A:\n'
        '        value = c\n'                         # no subscript assignment
        '    for c in (1, 2):\n'                      # not strings
        '        out[c] = 3\n'
    )
    assert fla._loop_assignment_evidence(src, "x.py") == {}


def test_unparseable_source_yields_nothing_not_an_exception():
    assert fla._loop_assignment_evidence("def broken(:\n", "x.py") == {}


def test_real_tree_sectional_set_is_served_via_serve_features():
    """The tripwire in fla._self_test, as a pytest so CI runs it two ways."""
    real = fla.audit_static()
    by_name = {r["feature"]: r for r in real["features"]}
    for z in ("z_200m", "z_400m", "z_600m", "z_800m", "lambda_decay", "svi", "rsi",
              "trip_cost_seconds"):
        assert by_name[z]["serve_status"] == "ASSIGNED", by_name[z]
        assert any("serve_features.py" in e and "SECTIONAL_LIVE_FEATURES" in e
                   for e in by_name[z]["serve_evidence"]), by_name[z]["serve_evidence"]


def test_real_tree_placeholder_fill_is_never_cited_as_evidence():
    """`for col in FEATURE_COLUMNS: out[col] = np.nan` (retrain_v2) fills every
    declared column and must never appear as liveness evidence."""
    real = fla.audit_static()
    for r in real["features"]:
        for e in r["train_evidence"] + r["serve_evidence"]:
            assert "FEATURE_COLUMNS" not in e, (r["feature"], e)


def test_real_tree_market_trio_cites_the_module_that_computes_it():
    """fair_implied_prob / odds_rank / odds_rank_pct read LIVE_BOTH on the
    evidence of two fallbacks: retrain_v2's except-handler zero-fill at train,
    and run_tips_pipeline's default `_rel_mkt` list at serve. relative_market
    is imported lazily inside a function on both sides — the same shape as
    form_feature_builder — so the audit never scanned the file that actually
    computes them. The verdict was right; the evidence proved nothing."""
    real = fla.audit_static()
    by_name = {r["feature"]: r for r in real["features"]}
    for f in ("fair_implied_prob", "odds_rank", "odds_rank_pct"):
        row = by_name[f]
        assert row["verdict"] == "LIVE_BOTH", row
        # the computation, not relative_market's own `defaults = [{...0.0}]`
        assert any("relative_market.py" in e and "defaults" not in e
                   for e in row["train_evidence"]), row["train_evidence"]


def test_real_tree_verdict_counts_are_the_expected_board():
    """The board, pinned so it cannot drift silently in either direction.
    68/4 since mc_api.py joined SERVE_FILES (2026-09-07); before that the
    stricter evidence rules held it at 67/5, and neither change may move a
    column to DEAD unnoticed."""
    counts = fla.audit_static()["verdict_counts"]
    assert counts == {"LIVE_BOTH": 68, "ZERO_AT_SERVE": 4}, counts


def test_ground_suitability_is_served_by_mc_api_not_the_shared_builder():
    """Two serve paths reach RacingMLModel and the audit scanned only one, so
    ground_suitability read "trained but never served" — a RED gate-5 row for
    a column mc_api computes on a live inference path.

    Both halves are pinned here because the second is what makes the first
    safe: serve_features omits it DELIBERATELY (LIVE_FEATURES is 14, not 15)
    since its artifact importance is 0.0000 — "dead weight both ways",
    docs/research/FEATURE_PROVENANCE.md:39 — so the builder that skips it
    changes no published probability. If someone plumbs it into the shared
    builder, or mc_api stops assigning it, this test says which happened."""
    by_name = {r["feature"]: r for r in fla.audit_static()["features"]}
    row = by_name["ground_suitability"]
    assert row["verdict"] == "LIVE_BOTH", row
    assert any("mc_api.py" in e for e in row["serve_evidence"]), row["serve_evidence"]

    shared = (SERVER_PYTHON / "serve_features.py").read_text(encoding="utf-8")
    assert "ground_suitability" not in shared, \
        "the shared builder now plumbs it — update FEATURE_PROVENANCE and this test"
    # Unconditional on purpose. FEATURE_PROVENANCE.md is tracked in git, so it
    # is present in every checkout including CI, and an `if exists()` guard
    # here could only ever do one thing: silently skip the check when the file
    # moved — which is precisely when the 0.0000 figure stops being verifiable.
    # That figure is the whole justification for calling this column served, so
    # losing it must fail loudly, not quietly pass.
    provenance = SERVER_PYTHON.parents[1] / "docs" / "research" / "FEATURE_PROVENANCE.md"
    assert provenance.exists(), \
        f"{provenance} is gone — it carries the 0.0000 importance this verdict rests on"
    line = [l for l in provenance.read_text(encoding="utf-8").splitlines()
            if l.startswith("| ground_suitability ")]
    assert line and "0.0000" in line[0], line


def test_real_tree_winner_pattern_features_surface_as_zero_at_serve():
    """The old audit rated the four winner-pattern features DEAD_BOTH_SIDES
    because it could not see their training loop (`for _wp_col in (...):
    out[_wp_col] = ...`). They are trained and nothing serves them, which is
    the worse verdict: a real train/serve gap the fill loop was hiding."""
    real = fla.audit_static()
    by_name = {r["feature"]: r for r in real["features"]}
    for f in ("prior_pb_close_underreaction", "cohort_fast_close_prior",
              "pos400_win_prior", "jockey_wet_residual"):
        assert by_name[f]["verdict"] == "ZERO_AT_SERVE", by_name[f]
        assert any("_wp_col" in e for e in by_name[f]["train_evidence"]), by_name[f]["train_evidence"]


def test_self_test_passes():
    fla._self_test()
