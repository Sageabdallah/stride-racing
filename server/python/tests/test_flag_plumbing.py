"""A flag with no production path is a flag that cannot be flipped.

Fargate tasks read the stride/prod Secrets Manager blob and nothing else
(infra/jobs/handler.py `_load_secrets`). deploy-infra.yml assembles that blob
from GitHub Actions secrets through infra/01_secrets.sh --from-env, which
builds it from a fixed KEYS allow-list, and put-secret-value replaces the
whole blob on every deploy. So a flag reaches production only if it is named
in BOTH the allow-list and the workflow's env block. Until 2026-09-05 the
three flags gate 3 of docs/project_retrain_gate.md requires flipped
(STRIDE_SERVE_LIVE_FEATURES, STRIDE_SERVE_NAN_CONTRACT,
STRIDE_RENORMALISE_FIELD) were in neither: the registered flip was
undeliverable, and a value set by hand in the console would have been erased
by the next deploy.

These tests read both files as text — PyYAML is not in the CI dependency set
(see test_preview_isolation._verify_jobs_run_block for the precedent).
"""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
SECRETS_SH = ROOT / "infra" / "01_secrets.sh"
DEPLOY_YML = ROOT / ".github" / "workflows" / "deploy-infra.yml"
ENV_EXAMPLE = ROOT / ".env.example"

# The flags governance intends to flip in production. Sources: gate 3 in
# docs/project_retrain_gate.md (the two flips), shadow-flip-criteria.md
# (deploy STRIDE_SERVE_LIVE_FEATURES together with STRIDE_SERVE_NAN_CONTRACT),
# and the shadow flag that already had a path and produces gate 3's evidence.
GATE3_PRODUCTION_FLAGS = (
    "STRIDE_SERVE_LIVE_FEATURES",
    "STRIDE_SERVE_NAN_CONTRACT",
    "STRIDE_RENORMALISE_FIELD",
    "STRIDE_SERVE_LIVE_FEATURES_SHADOW",
)


def secrets_allow_list():
    """The KEYS list inside the --from-env Python heredoc of 01_secrets.sh."""
    text = SECRETS_SH.read_text()
    start = text.index("KEYS = [")
    end = text.index("]", start)
    return re.findall(r'"([A-Z_][A-Z0-9_]*)"', text[start:end])


def deploy_env_block():
    """{env name: secret name} for the '01 secrets' step of deploy-infra.yml."""
    text = DEPLOY_YML.read_text()
    start = text.index("- name: 01 secrets")
    tail = text.find("\n      - name:", start + 10)
    block = text[start:] if tail == -1 else text[start:tail]
    env_at = block.index("\n        env:")
    run_at = block.index("\n        run:")
    pairs = re.findall(
        r"^\s+([A-Z_][A-Z0-9_]*):\s*\$\{\{\s*secrets\.([A-Z_][A-Z0-9_]*)\s*\}\}",
        block[env_at:run_at], re.M)
    return dict(pairs)


def test_allow_list_parses_to_a_real_list():
    keys = secrets_allow_list()
    assert len(keys) >= 25, keys
    assert "DATABASE_URL" in keys and "PUNTINGFORM_API_KEY" in keys
    assert len(keys) == len(set(keys)), "duplicate key in KEYS"


def test_every_env_line_maps_a_secret_of_the_same_name():
    env = deploy_env_block()
    assert env, "no `NAME: ${{ secrets.NAME }}` lines found under the 01 step"
    mismatched = {k: v for k, v in env.items() if k != v}
    assert not mismatched, f"env name != secret name: {mismatched}"


def test_allow_list_and_workflow_env_are_in_lockstep():
    """A key present in only one place is a value that is either shipped
    from nowhere (workflow only) or silently never shipped (KEYS only)."""
    keys = set(secrets_allow_list())
    env = set(deploy_env_block())
    assert keys == env, (
        f"only in 01_secrets.sh KEYS: {sorted(keys - env)}; "
        f"only in deploy-infra.yml env: {sorted(env - keys)}")


def test_gate3_flags_have_a_production_path():
    keys = set(secrets_allow_list())
    env = set(deploy_env_block())
    for flag in GATE3_PRODUCTION_FLAGS:
        assert flag in keys, f"{flag} missing from 01_secrets.sh KEYS"
        assert flag in env, f"{flag} missing from deploy-infra.yml step 01 env"


def test_gate3_flags_are_documented_in_env_example():
    """IMPLEMENTATION_PLAN §6: every flag lands in .env.example, or it is
    invisible. STRIDE_SERVE_LIVE_FEATURES had never been added."""
    text = ENV_EXAMPLE.read_text()
    for flag in GATE3_PRODUCTION_FLAGS:
        assert re.search(rf"^{flag}=", text, re.M), f"{flag} not in .env.example"
    assert "PRODUCTION PATH" in text, \
        ".env.example must say that Fargate reads the secret blob, not this file"


def test_absent_secrets_are_skipped_not_shipped_empty():
    """Listing a flag before the operator creates its GitHub secret must be
    safe: 01_secrets.sh keeps only keys with a value, so an unset secret is
    reported absent rather than written as an empty string that a reader
    could mistake for a deliberate blank."""
    text = SECRETS_SH.read_text()
    assert "for k in KEYS if os.environ.get(k)" in text
    assert "absent:" in text, "absent keys must be listed loudly"
