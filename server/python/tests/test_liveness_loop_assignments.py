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
        '        feat[k] = 1\n'
    )
    ev = fla._loop_assignment_evidence(src, "x.py")
    assert set(ev) == {"alpha", "beta", "gamma", "delta"}
    assert ev["gamma"] == ["x.py:5: for k in MORE: feat[k] = ... (loop assignment)"]


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
