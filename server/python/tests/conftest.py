import sys
from pathlib import Path

# Test subjects live one level up (server/python) and are imported flat,
# matching how the pipeline scripts import each other.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
