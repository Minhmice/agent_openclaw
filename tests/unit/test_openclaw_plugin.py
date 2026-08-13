from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).parents[2]


def test_native_plugin_metadata_is_strict_and_runtime_entry_exists() -> None:
    plugin_root = ROOT / "deploy/openclaw-web-plugin"
    manifest = json.loads((plugin_root / "openclaw.plugin.json").read_text(encoding="utf-8"))
    package = json.loads((plugin_root / "package.json").read_text(encoding="utf-8"))

    assert manifest["id"] == "openclaw-web-components"
    assert manifest["activation"] == {
        "onStartup": True,
        "onChannels": ["discord"],
    }
    assert manifest["configSchema"] == {
        "type": "object",
        "additionalProperties": False,
    }
    assert package["type"] == "module"
    assert package["openclaw"]["extensions"] == ["./index.js"]
    assert (plugin_root / "index.js").is_file()


@pytest.mark.skipif(shutil.which("node") is None, reason="node is unavailable")
def test_openclaw_web_plugin_runtime_contract() -> None:
    result = subprocess.run(
        [
            "node",
            "--test",
            str(ROOT / "tests/plugin/openclaw-web-plugin.test.mjs"),
        ],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )

    assert result.returncode == 0, result.stdout + result.stderr
