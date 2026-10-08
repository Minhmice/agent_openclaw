from __future__ import annotations

import shlex
import shutil
import stat
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).parents[2]


def _bash_path(path: Path) -> str:
    absolute = path.resolve()
    drive = absolute.drive.rstrip(":").lower()
    return f"/mnt/{drive}/{absolute.as_posix().split(':', 1)[1].lstrip('/')}"


def _write_executable(path: Path, body: str) -> None:
    path.write_text(body, encoding="utf-8", newline="\n")
    path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


@pytest.mark.skipif(shutil.which("bash") is None, reason="bash is unavailable")
def test_bash_wrapper_falls_back_to_interactive_auth_when_key_is_missing(tmp_path: Path) -> None:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    arguments = tmp_path / "ssh-arguments.txt"
    _write_executable(
        fake_bin / "ssh",
        """#!/usr/bin/env bash
printf '%s\\n' "$@" > "$SSH_ARGUMENTS_FILE"
""",
    )

    runner = tmp_path / "run-wrapper.sh"
    _write_executable(
        runner,
        "\n".join(
            [
                "#!/usr/bin/env bash",
                "export OPENCLAW_SSH_HOST=example.test",
                "export OPENCLAW_SSH_PORT=2200",
                "export OPENCLAW_SSH_USER=minhmice",
                f"export OPENCLAW_SSH_KEY={shlex.quote(_bash_path(tmp_path / 'missing-key'))}",
                f"export PATH={shlex.quote(_bash_path(fake_bin))}:$PATH",
                f"export SSH_ARGUMENTS_FILE={shlex.quote(_bash_path(arguments))}",
                f"exec {shlex.quote(_bash_path(ROOT / 'scripts/openclaw-ssh.sh'))} health",
                "",
            ]
        ),
    )

    result = subprocess.run(
        ["bash", _bash_path(runner)],
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    assert arguments.read_text(encoding="utf-8").splitlines() == [
        "-p",
        "2200",
        "minhmice@example.test",
        "health",
    ]
    assert "OPENCLAW_SSH_KEY" in result.stderr
    assert "continuing" in result.stderr.lower()


def test_powershell_wrapper_falls_back_when_optional_key_is_missing() -> None:
    wrapper = (ROOT / "scripts/openclaw-ssh.ps1").read_text(encoding="utf-8")

    assert "continuing without an explicit key" in wrapper
    assert "OPENCLAW_SSH_PASSWORD" not in wrapper
