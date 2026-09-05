"""RacingMLModel must construct where the booster libraries are absent.

The CI runner installs numpy/scipy/pandas/scikit-learn/lightgbm and none of
xgboost, catboost or optuna. Until 2026-09-05 ml_model bound its sklearn
imports inside the same try-block as those three, so on that runner (or any
machine without the full stack) RacingMLModel() raised NameError on
StandardScaler in __init__ rather than degrading to is_trained=False. Run in
a subprocess so the import guard is exercised from a clean interpreter.
"""

import subprocess
import sys
from pathlib import Path

SERVER_PYTHON = Path(__file__).resolve().parents[1]

PROGRAM = r"""
import sys
for name in ("xgboost", "catboost", "optuna"):
    sys.modules[name] = None          # import -> ImportError, as on the CI runner
sys.path.insert(0, {sp!r})
import ml_model
assert ml_model.ML_AVAILABLE is False, "the simulation did not take"
m = ml_model.RacingMLModel(model_path={mp!r})
assert m.is_trained is False
assert m.scaler is not None
out = m.predict_components(__import__("pandas").DataFrame({{"a": [1.0, 2.0]}}))
assert out["method"] == "untrained" and list(out["ensemble"]) == [0.0, 0.0]
print("OK")
"""


def test_wrapper_constructs_and_reports_untrained_without_boosters(tmp_path):
    prog = PROGRAM.format(sp=str(SERVER_PYTHON), mp=str(tmp_path / "absent.pkl"))
    res = subprocess.run([sys.executable, "-c", prog], capture_output=True, text=True, timeout=120)
    assert res.returncode == 0, res.stdout + res.stderr
    assert res.stdout.strip().endswith("OK"), res.stdout + res.stderr
    assert "ML libraries not available" in res.stdout + res.stderr
