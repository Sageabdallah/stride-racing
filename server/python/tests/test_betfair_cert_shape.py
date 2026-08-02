"""A Betfair client certificate must not be a CA certificate.

`openssl req -x509` defaults to basicConstraints CA:TRUE. The pair
generated 2026-07-31 inherited that default and had no nsCertType, so
Betfair refused it at certlogin — and the failure surfaced as
CERT_AUTH_REQUIRED, which reads exactly like "not uploaded yet". Two days
were spent looking at the upload for a defect that was in the file.

These pin the two halves of that lesson: the generated shape, and the
checker's ability to tell a malformed certificate from an unregistered
one. Certificates are minted in tmp with openssl; nothing touches the
real pair and no network call is made.
"""

import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
CNF = ROOT / "scripts" / "betfair-client-openssl.cnf"
CHECKER = ROOT / "scripts" / "betfair_cert_check.py"


@pytest.fixture(scope="module")
def checker():
    # Import without running main(); the module only reads .env at import.
    spec = importlib.util.spec_from_file_location("betfair_cert_check",
                                                  CHECKER)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["betfair_cert_check"] = mod
    spec.loader.exec_module(mod)
    return mod


def _mint(tmp_path, *extra_args, config=None):
    crt = tmp_path / "c.crt"
    key = tmp_path / "c.key"
    cmd = ["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes",
           "-days", "365", "-keyout", str(key), "-out", str(crt)]
    if config:
        cmd += ["-config", str(config), "-extensions", "client_cert"]
    else:
        cmd += ["-subj", "/C=AU/O=T/CN=t"]
    cmd += list(extra_args)
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    assert r.returncode == 0, r.stderr
    return str(crt)


def test_the_shipped_config_produces_a_client_certificate(tmp_path, checker):
    """The config in version control must yield a cert Betfair can accept.

    This is the regression that matters: certs/ is gitignored, so the
    config had to live somewhere tracked or a fresh clone would regenerate
    the CA-shaped certificate all over again.
    """
    assert CNF.exists(), f"{CNF} missing — the spec must stay in git"
    crt = _mint(tmp_path, config=CNF)
    text = checker._cert_text(crt)
    assert "CA:FALSE" in text
    assert "CA:TRUE" not in text
    assert "SSL Client" in text                      # nsCertType = client
    assert "Digital Signature" in text
    assert "Key Encipherment" in text
    assert "TLS Web Client Authentication" in text   # clientAuth
    assert checker._print_extension_check(crt) is True


def test_openssl_default_is_rejected(tmp_path, checker):
    """Plain `openssl req -x509` is exactly how the bad pair was made."""
    crt = _mint(tmp_path)
    assert "CA:TRUE" in checker._cert_text(crt)
    assert checker._print_extension_check(crt) is False


def test_ca_true_is_called_out_explicitly(tmp_path, checker, capsys):
    # Naming the cause is the whole point — a bare FAIL would have sent the
    # operator back to the upload page again.
    crt = _mint(tmp_path)
    checker._print_extension_check(crt)
    out = capsys.readouterr().out
    assert "CA:TRUE" in out
    assert "no matter how often it is uploaded" in out


def test_missing_nscerttype_alone_fails(tmp_path, checker):
    """CA:FALSE is necessary but not sufficient.

    A cert with correct basicConstraints but no nsCertType would pass a
    naive CA:TRUE-only check while Betfair still refused it.
    """
    cnf = tmp_path / "partial.cnf"
    cnf.write_text(
        "[req]\ndistinguished_name = dn\nx509_extensions = client_cert\n"
        "prompt = no\n[dn]\nCN = t\n"
        "[client_cert]\nbasicConstraints = CA:FALSE\n"
        "extendedKeyUsage = clientAuth\n")
    crt = _mint(tmp_path, config=cnf)
    text = checker._cert_text(crt)
    assert "CA:FALSE" in text and "SSL Client" not in text
    assert checker._print_extension_check(crt) is False


def test_checker_uses_the_certlogin_endpoint():
    """Cert login is a different host and path from interactive login.

    Posting a client certificate to /api/login proves nothing about the
    certificate, so the endpoint is worth pinning.
    """
    sys.path.insert(0, str(ROOT / "server" / "python"))
    from providers import betfair_auth as ba
    assert ba.CERT_LOGIN_URL == \
        "https://identitysso-cert.betfair.com.au/api/certlogin"
    assert ba.INTERACTIVE_LOGIN_URL != ba.CERT_LOGIN_URL
    # cert_login must post to the cert host, not the interactive one.
    import inspect
    src = inspect.getsource(ba.cert_login)
    assert "CERT_LOGIN_URL" in src
    assert "INTERACTIVE_LOGIN_URL" not in src
