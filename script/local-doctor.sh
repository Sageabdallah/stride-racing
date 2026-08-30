#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

required_node_major="20"
required_python_minor="3.11"

pass() {
  printf '[PASS] %s\n' "$1"
}

warn() {
  printf '[WARN] %s\n' "$1"
}

fail() {
  printf '[FAIL] %s\n' "$1"
}

section() {
  printf '\n%s\n' "$1"
}

have_cmd() {
  command -v "$1" >/dev/null 2>&1
}

section "Project"
printf 'Root: %s\n' "$ROOT_DIR"

section "Runtime Versions"
if have_cmd node; then
  node_version="$(node -p 'process.versions.node')"
  node_major="${node_version%%.*}"
  if [[ "$node_major" == "$required_node_major" ]]; then
    pass "Node version is $node_version"
  else
    warn "Node version is $node_version, expected major $required_node_major"
  fi
else
  fail "Node is not installed"
fi

if have_cmd python3; then
  python_version="$(python3 -c 'import sys; print(".".join(map(str, sys.version_info[:3])))')"
  if [[ "$python_version" == "$required_python_minor"* ]]; then
    pass "python3 version is $python_version"
  else
    warn "python3 version is $python_version, expected $required_python_minor.x"
  fi
else
  fail "python3 is not installed"
fi

if have_cmd python3.11; then
  pass "python3.11 is available"
else
  warn "python3.11 is not available"
fi

section "Node Install"
if [[ -d node_modules ]]; then
  pass "node_modules directory exists"
else
  warn "node_modules is missing"
fi

if [[ -x node_modules/.bin/tsx ]]; then
  pass "node_modules/.bin/tsx is executable"
else
  warn "node_modules/.bin/tsx is missing or not executable; run npm ci"
fi

section "Environment File"
if [[ -f .env ]]; then
  pass ".env exists"
else
  fail ".env is missing"
fi

required_env_keys=(
  DATABASE_URL
  RACING_API_USERNAME
  RACING_API_PASSWORD
  GROQ_API_KEY
  LLM_PROVIDER
  LLM_ENABLED
)

optional_env_keys=(
  ANTHROPIC_API_KEY
  THE_RACING_API_KEY
)

for key in "${required_env_keys[@]}"; do
  if [[ -f .env ]] && rg -q "^${key}=" .env; then
    pass ".env contains $key"
  else
    fail ".env is missing $key"
  fi
done

for key in "${optional_env_keys[@]}"; do
  if [[ -f .env ]] && rg -q "^${key}=" .env; then
    pass ".env contains optional $key"
  else
    warn ".env does not contain optional $key"
  fi
done

section "Port Check"
if lsof -nP -iTCP:5000 -sTCP:LISTEN >/tmp/local-doctor-port-5000.txt 2>/dev/null; then
  warn "Port 5000 is in use:"
  cat /tmp/local-doctor-port-5000.txt
else
  pass "Port 5000 is free"
fi
rm -f /tmp/local-doctor-port-5000.txt

section "Python Packages"
python3 - <<'PY'
import importlib
import sys

packages = {
    "requests": "requests",
    "bs4": "beautifulsoup4",
    "pandas": "pandas",
    "numpy": "numpy",
    "tabulate": "tabulate",
    "colorama": "colorama",
    "sklearn": "scikit-learn",
    "catboost": "catboost",
    "imblearn": "imbalanced-learn",
    "lightgbm": "lightgbm",
    "optuna": "optuna",
    "shap": "shap",
    "xgboost": "xgboost",
    "psycopg2": "psycopg2-binary",
    "category_encoders": "category_encoders",
    "feature_engine": "feature-engine",
    "hyperopt": "hyperopt",
    "pytorch_tabnet": "pytorch-tabnet",
    "scipy": "scipy",
    "playwright": "playwright",
    "dnfile": "dnfile",
    "networkx": "networkx",
    "community": "python-louvain",
    "groq": "groq",
    "sqlalchemy": "sqlalchemy",
    "dateutil": "python-dateutil",
    "dotenv": "python-dotenv",
}

missing = []
for module_name, package_name in packages.items():
    try:
        importlib.import_module(module_name)
        print(f"[PASS] Python module available: {module_name} ({package_name})")
    except Exception as exc:
        print(f"[WARN] Missing Python module: {module_name} ({package_name}) :: {exc}")
        missing.append(package_name)

if missing:
    unique_missing = []
    for package_name in missing:
        if package_name not in unique_missing:
            unique_missing.append(package_name)
    print("\nSuggested fix:")
    print("pip install " + " ".join(unique_missing))
else:
    print("\n[PASS] Required Python packages are installed")
PY

section "Recommended Commands"
cat <<'EOF'
brew install node@20 python@3.11 postgresql@16
export PATH="/opt/homebrew/opt/node@20/bin:/opt/homebrew/opt/python@3.11/bin:$PATH"
python3.11 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
pip install -r requirements.txt
python -m playwright install chromium
npm ci
PORT=5001 npm run dev
EOF
