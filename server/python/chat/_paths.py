"""Make the flat server/python modules importable from inside this package.

pf_client, identity_normalization and result_margins live beside this package
as top-level modules, not in a package. Every test file in server/python does
`sys.path.insert(0, dirname(__file__))` for the same reason; doing it once here
means `import chat` works from the repo root, from server/python, from a
Lambda task root and from pytest, without each caller repeating the dance.
"""

from __future__ import annotations

import os
import sys

SERVER_PYTHON_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REPO_ROOT = os.path.dirname(os.path.dirname(SERVER_PYTHON_DIR))

if SERVER_PYTHON_DIR not in sys.path:
    sys.path.insert(0, SERVER_PYTHON_DIR)
