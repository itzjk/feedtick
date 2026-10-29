import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest


@pytest.mark.parametrize("old,new,target", [
    ("store.create_batch(payload)", "store.create_batch(payload)\n            store.db.execute('UPDATE entries SET confirmed=1')", "test_check_never_confirms"),
    ('"partial" if any(c["status"] == "error" for c in coverage) else "complete"', '"complete"', "test_partial_coverage_is_visible")])
def test_contract_mutations_are_caught(tmp_path, old, new, target):
    root = Path(__file__).resolve().parents[1]
    shutil.copytree(root / "src", tmp_path / "src")
    path = tmp_path / "src" / "feedtick" / "core.py"
    text = path.read_text()
    assert old in text
    path.write_text(text.replace(old, new))
    env = {**os.environ, "PYTHONPATH": str(tmp_path / "src")}
    result = subprocess.run([sys.executable, "-m", "pytest", "-q", f"{root}/tests/test_flow.py::{target}"], env=env, capture_output=True, text=True, timeout=30)
    assert result.returncode == 1 and "1 failed" in result.stdout, result.stdout + result.stderr
