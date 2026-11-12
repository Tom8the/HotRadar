"""Verify retained manual evidence and the current automatic content read paths."""
import runpy
from pathlib import Path

runpy.run_path(str(Path(__file__).with_name("verify-session-data.py")), run_name="__main__")
