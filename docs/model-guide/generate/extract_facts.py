"""Derive every figure used in the model-weights document straight from source.
Nothing here is typed by hand: constants come from the AST, importances from
the committed liveness report. Output is the single source of truth for the doc.
"""
import ast, json, os, sys

import os
ROOT = os.environ.get("STRIDE_REPO", os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..")))
PY_DIR = os.path.join(ROOT, "server", "python")
REPORT = os.path.join(ROOT, "docs", "research", "feature_liveness_report.json")

facts = {}

def parse(path):
    with open(path) as fh:
        return ast.parse(fh.read()), fh

def module_consts(path, names):
    tree, _ = parse(path)
    out = {}
    for node in tree.body:
        if isinstance(node, ast.Assign):
            for t in node.targets:
                if isinstance(t, ast.Name) and t.id in names:
                    try:
                        out[t.id] = ast.literal_eval(node.value)
                    except ValueError:
                        out[t.id] = ast.unparse(node.value)
    return out

def class_attr(path, cls, attr):
    tree, _ = parse(path)
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and node.name == cls:
            for item in node.body:
                if isinstance(item, ast.Assign):
                    for t in item.targets:
                        if isinstance(t, ast.Name) and t.id == attr:
                            return ast.literal_eval(item.value)
    raise KeyError(f"{cls}.{attr} not found in {path}")

# ---- 1. Feature importances from the committed liveness report -------------
rep = json.load(open(REPORT))
pkl = rep["pkl"]
imp = pkl["importances"]
facts["artifact"] = {
    "version": pkl["version"],
    "trained_at": pkl["trained_at"],
    "n_trained_columns": pkl["n_trained_columns"],
    "importance_source": pkl["importance_source"],
    "n_zero_importance": len(pkl["zero_importance_features"]),
    "sum_importance": round(sum(imp.values()), 6),
}
ranked = sorted(imp.items(), key=lambda kv: -kv[1])
nonzero = [(k, v) for k, v in ranked if v > 0]
facts["n_nonzero"] = len(nonzero)
facts["ranked_nonzero"] = nonzero
cum, cums = 0.0, []
for k, v in nonzero:
    cum += v
    cums.append((k, v, cum))
facts["cumulative"] = cums
facts["top10_share"] = round(cums[9][2], 6)
facts["top15_share"] = round(cums[14][2], 6)

# ---- 2. Feature column declarations ---------------------------------------
ml_cols = class_attr(os.path.join(PY_DIR, "ml_model.py"), "RacingMLModel", "FEATURE_COLUMNS")
rt_cols = module_consts(os.path.join(PY_DIR, "retrain_v2.py"), {"FEATURE_COLUMNS"})["FEATURE_COLUMNS"]
facts["declared_columns"] = {
    "ml_model": len(ml_cols),
    "retrain_v2": len(rt_cols),
    "identical_set_and_order": list(ml_cols) == list(rt_cols),
}
facts["declared_not_in_artifact"] = sorted(set(ml_cols) - set(imp))
facts["artifact_not_declared"] = len(set(imp) - set(ml_cols))

# ---- 3. Base-learner blend weights ----------------------------------------
perf = class_attr(os.path.join(PY_DIR, "ml_model.py"), "RacingMLModel", "_model_performance")
blend = {}
for cat, models in perf.items():
    acc = {k: v["correct"] / max(v["total"], 1) for k, v in models.items()}
    tot = sum(acc.values())
    blend[cat] = {k: round(v / tot, 4) for k, v in acc.items()}
facts["seed_performance"] = perf
facts["blend_weights"] = blend

# ---- 4. Convergence constants ---------------------------------------------
cb = os.path.join(PY_DIR, "consensus_blender.py")
facts["convergence"] = module_consts(cb, {
    "STRIDE_THRESHOLD", "CONSENSUS_THRESHOLD", "MARKET_THRESHOLD",
    "MIN_MODEL_SCORE_FOR_BET", "MIN_CONFIRM_CONVERGENCE_SCORE",
})
src = open(cb).read()
facts["pillar_weight_defaults"] = {
    "stride": src.split('STRIDE_MODEL_WEIGHT", "')[1].split('"')[0],
    "consensus": src.split('CONSENSUS_WEIGHT", "')[1].split('"')[0],
    "market": src.split('MARKET_SIGNAL_WEIGHT", "')[1].split('"')[0],
}
print(json.dumps(facts, indent=2, default=str))
