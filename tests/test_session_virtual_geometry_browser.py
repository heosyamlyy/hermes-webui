"""Exercise grouped active anchoring and preview heights in a real browser."""
import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def test_grouped_active_anchor_and_offscreen_preview_heights(tmp_path):
    pytest.importorskip('playwright.sync_api')
    result = subprocess.run(
        [sys.executable, str(ROOT / 'tests/browser_session_virtual_geometry.py'),
         '--output', str(tmp_path / 'geometry')],
        cwd=ROOT, capture_output=True, text=True, timeout=90,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    report = json.loads((tmp_path / 'geometry/report.json').read_text())
    assert len(report['results']) == 32
    assert sum(scene['scene'] == 'selection' for scene in report['results']) == 4
    assert not report['errors']
    assert all(not scene['failures'] for scene in report['results'])
