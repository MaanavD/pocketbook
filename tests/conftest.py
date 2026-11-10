"""Load the repo root as the `pocketbook` package, the way Hermes imports the plugin."""
import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if "pocketbook" not in sys.modules:
    spec = importlib.util.spec_from_file_location(
        "pocketbook", ROOT / "__init__.py", submodule_search_locations=[str(ROOT)])
    mod = importlib.util.module_from_spec(spec)
    sys.modules["pocketbook"] = mod
    spec.loader.exec_module(mod)
