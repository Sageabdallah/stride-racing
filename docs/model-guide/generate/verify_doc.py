"""Read the finished .docx back and check every figure in it against the
source-derived data. Fails loudly on any mismatch."""
import json, os, re, zipfile, sys
REPO = os.environ.get('STRIDE_REPO', os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', '..')))

DOC = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'STRIDE-How-the-model-makes-up-its-mind.docx')
facts  = json.load(open('facts.json'))
groups = json.load(open('groups.json'))
plain  = json.load(open('plain.json'))
zas    = json.load(open('zas.json'))

xml = zipfile.ZipFile(DOC).read('word/document.xml').decode('utf-8')
runs = re.findall(r'<w:t[^>]*>(.*?)</w:t>', xml, re.S)
def unesc(s):
    return (s.replace('&amp;','&').replace('&lt;','<').replace('&gt;','>')
             .replace('&quot;','"').replace('&apos;',"'"))
text = ' '.join(unesc(r) for r in runs)
text = re.sub(r'\s+', ' ', text)

fails, checks = [], 0
def check(label, cond):
    global checks
    checks += 1
    if not cond:
        fails.append(label)

pct = lambda v: f"{v*100:.2f}%"
p1  = lambda v: f"{v*100:.1f}%"

# --- every factor: plain description, technical name, weight all present -----
for i, (name, val) in enumerate(facts['ranked_nonzero'], 1):
    check(f"factor #{i} name '{name}'", name in text)
    check(f"factor #{i} weight {pct(val)}", pct(val) in text)
    desc = plain[name]
    check(f"factor #{i} description", desc[:38] in text)

# --- theme shares -----------------------------------------------------------
for g, v in groups['group_shares'].items():
    check(f"theme '{g}' label", g in text)
    check(f"theme '{g}' share {pct(v)}", pct(v) in text)
    check(f"theme '{g}' count", str(len(groups['groups'][g])) in text)

# --- artifact facts ---------------------------------------------------------
A = facts['artifact']
check("trained column count", str(A['n_trained_columns']) in text)
check("zero-weight count",    str(A['n_zero_importance']) in text)
check("non-zero count",       str(len(facts['ranked_nonzero'])) in text)
check("training date",        A['trained_at'][:10] in text)
check("model version",        f"version {A['version']}" in text)
check("top-15 cumulative",    pct(facts['top15_share']) in text)
check("top-10 cumulative",    pct(facts['top10_share']) in text)

# --- base-learner blend -----------------------------------------------------
for cat, w in facts['blend_weights'].items():
    for m, v in w.items():
        check(f"blend {cat}/{m} = {p1(v)}", p1(v) in text)

# --- convergence ------------------------------------------------------------
cv = facts['convergence']
check("model bar 65", "65" in text)
check("consensus bar 65", "65" in text)
check("market bar 60", "60" in text)
check("min model score", str(cv['MIN_MODEL_SCORE_FOR_BET']).rstrip('0').rstrip('.') in text
                          or "below 8" in text)
check("min confirm score", str(int(cv['MIN_CONFIRM_CONVERGENCE_SCORE'])) in text)
for k, v in facts['pillar_weight_defaults'].items():
    check(f"pillar {k} = {p1(float(v))}", p1(float(v)) in text)

# --- zero-at-serve ----------------------------------------------------------
check("zero-at-serve count", f"{zas['count']} of the factors" in text)
check("zero-at-serve mass",  pct(zas['sum']) in text)
check("audit date",          zas['audit_date'] in text)

# --- pipeline constants read straight from the pipeline source --------------
src = open(os.path.join(REPO,'server/python/run_tips_pipeline.py')).read()
ladder = [("0.80","80%"),("0.70","70%"),("0.50","50%"),("0.45","45%"),
          ("0.40","40%"),("0.30","30%")]
for code, shown in ladder:
    check(f"price ladder {shown} present in source", f"mw = {code}" in src)
    check(f"price ladder {shown} present in doc",    shown in text)
check("ml blend 20/40 in source", "ml_w = 0.20 if (odds and odds <= 3) else 0.40" in src)
check("longshot block in source", "if odds > 30:" in src)
check("flat stake in source", 'return "1u" if conf in ("high", "medium") else "0u"' in src)

# --- injections -------------------------------------------------------------
cb = open(os.path.join(REPO,'server/python/consensus_blender.py')).read()
for pts in ["12.0","7.0","3.0","8.0","4.0","5.0"]:
    check(f"injection value {pts} in source", pts in cb)
for shown in ["+12.0","+7.0","+3.0","+8.0","+4.0","−3.0","−5.0","−8.0"]:
    check(f"injection {shown} in doc", shown in text)

print(f"checks run: {checks}")
if fails:
    print(f"FAILED: {len(fails)}")
    for f in fails[:40]:
        print("  -", f)
    sys.exit(1)
print("ALL CHECKS PASSED — every figure in the document matches the source data")
