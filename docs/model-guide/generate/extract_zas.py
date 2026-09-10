"""Factors the model was trained on but which are not calculated at tip time."""
import json
REPORT = "../../docs/research/feature_liveness_report.json"
import sys, os
REPORT = sys.argv[1] if len(sys.argv) > 1 else "docs/research/feature_liveness_report.json"
rep = json.load(open(REPORT))
imp = rep["pkl"]["importances"]
zas = [f["feature"] for f in rep["static"]["features"] if f["verdict"] == "ZERO_AT_SERVE"]
rows = sorted(((f, imp.get(f, 0.0)) for f in zas), key=lambda r: -r[1])
json.dump({"rows": rows, "count": len(zas),
           "sum": round(sum(v for _, v in rows), 4),
           "audit_date": "2026-07-28"}, open("zas.json", "w"), indent=1)
print(f"zero-at-serve: {len(zas)} factors, {sum(v for _,v in rows)*100:.2f}% of model weight")
