from __future__ import annotations

import json
import os
import shlex
import shutil
import stat
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).parents[2]
ENV_KEYS = {
    "OPENCLAW_WEB_STATE_DB",
    "OPENCLAW_WEB_ARTIFACT_ROOT",
    "OPENCLAW_WEB_SCHEMA_ROOT",
    "OPENCLAW_WEB_MARKET_CONFIG",
    "OPENCLAW_WEB_SCORING_CONFIG",
    "OPENCLAW_WORKFLOW_ROOT",
    "OPENCLAW_WEB_DISCORD_GUILD_ID",
    "OPENCLAW_WEB_REVIEW_CHANNEL_ID",
    "PATH",
}


def _bash_path(path: Path) -> str:
    absolute = path.resolve()
    drive = absolute.drive.rstrip(":").lower()
    return f"/mnt/{drive}/{absolute.as_posix().split(':', 1)[1].lstrip('/')}"


def _write_executable(path: Path, body: str) -> None:
    path.write_text(body, encoding="utf-8", newline="\n")
    path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


def _copy_source(tmp_path: Path) -> tuple[Path, Path]:
    source = tmp_path / "source"
    for directory in ("agents", "config", "schemas", "deploy"):
        shutil.copytree(ROOT / directory, source / directory)
    wheel = source / "dist" / "package.whl"
    wheel.parent.mkdir(parents=True)
    wheel.write_bytes(b"wheel-v1")
    return source, wheel


def _fake_commands(tmp_path: Path) -> tuple[Path, Path]:
    fake_bin = tmp_path / "fake-bin"
    fake_bin.mkdir()
    log = tmp_path / "commands.log"
    _write_executable(
        fake_bin / "systemctl",
        """#!/usr/bin/env bash
printf 'systemctl %s\\n' "$*" >> "$HARNESS_LOG"
case "$*" in
  *"disable --now"*)
    printf 'disabled\\n' > "$HARNESS_LOG.timer-enabled-state"
    printf 'inactive\\n' > "$HARNESS_LOG.timer-active-state"
    ;;
  *"stop openclaw-web-discovery.service"*) ;;
  *"enable --runtime openclaw-web-discovery.timer"*)
    [[ "${HARNESS_FORCE_RESTORE_MISMATCH:-0}" == 1 ]] || printf 'enabled-runtime\\n' > "$HARNESS_LOG.timer-enabled-state"
    ;;
  *"enable openclaw-web-discovery.timer"*)
    [[ "${HARNESS_FORCE_RESTORE_MISMATCH:-0}" == 1 ]] || printf 'enabled\\n' > "$HARNESS_LOG.timer-enabled-state"
    ;;
  *"mask --runtime openclaw-web-discovery.timer"*) printf 'masked-runtime\\n' > "$HARNESS_LOG.timer-enabled-state" ;;
  *"mask openclaw-web-discovery.timer"*) printf 'masked\\n' > "$HARNESS_LOG.timer-enabled-state" ;;
  *"start openclaw-web-discovery.timer"*)
    [[ "${HARNESS_FORCE_RESTORE_MISMATCH:-0}" == 1 ]] || printf 'active\\n' > "$HARNESS_LOG.timer-active-state"
    ;;
  *"is-enabled"*) cat "$HARNESS_LOG.timer-enabled-state" ;;
  *"is-active"*) cat "$HARNESS_LOG.timer-active-state" ;;
esac
exit 0
""",
    )
    _write_executable(
        fake_bin / "systemd-analyze",
        """#!/usr/bin/env bash
printf 'systemd-analyze %s\\n' "$*" >> "$HARNESS_LOG"
count_file="$HARNESS_LOG.systemd-count"
count=0
[[ ! -f "$count_file" ]] || count=$(<"$count_file")
count=$((count + 1))
printf '%s\\n' "$count" > "$count_file"
[[ "${HARNESS_SYSTEMD_FAIL:-0}" == 1 ]] && exit 1
if [[ -n "${HARNESS_SYSTEMD_FAIL_AFTER:-}" && "$count" -gt "$HARNESS_SYSTEMD_FAIL_AFTER" ]]; then
  exit 1
fi
exit 0
""",
    )
    _write_executable(
        fake_bin / "openclaw",
        """#!/usr/bin/env bash
printf 'openclaw %s\\n' "$*" >> "$HARNESS_LOG"
if [[ "$1 $2 $3" == "config get plugins.allow" ]]; then
  "${HARNESS_REAL_PYTHON:?}" - "${HARNESS_CONFIG_PATH:?}" <<'PY'
import json
import sys
from pathlib import Path
path = Path(sys.argv[1])
if not path.exists():
    raise SystemExit(1)
config = json.loads(path.read_text(encoding="utf-8"))
try:
    value = config["plugins"]["allow"]
except (KeyError, TypeError):
    raise SystemExit(1)
print(json.dumps(value))
PY
  exit $?
elif [[ "$1 $2 $3" == "config set plugins.allow" ]]; then
  mkdir -p "$HOME/.openclaw"
  "${HARNESS_REAL_PYTHON:?}" - "${HARNESS_CONFIG_PATH:?}" "$4" <<'PY'
import json
import os
import sys
from pathlib import Path
path = Path(sys.argv[1])
config = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
config.setdefault("plugins", {})["allow"] = json.loads(sys.argv[2])
data = json.dumps(config)
if os.environ.get("HARNESS_CONFIG_REPLACE") == "1":
    replacement = path.with_name(path.name + ".replacement")
    replacement.write_text(data, encoding="utf-8")
    replacement.chmod(0o600)
    replacement.replace(path)
else:
    path.write_text(data, encoding="utf-8")
PY
elif [[ "$1 $2 $3" == "config set plugins.entries.openclaw-web-components.enabled" ]]; then
  mkdir -p "$HOME/.openclaw"
  "${HARNESS_REAL_PYTHON:?}" - "${HARNESS_CONFIG_PATH:?}" <<'PY'
import json
import os
import sys
from pathlib import Path
path = Path(sys.argv[1])
config = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
config.setdefault("plugins", {}).setdefault("entries", {}).setdefault(
    "openclaw-web-components", {}
)["enabled"] = True
data = json.dumps(config)
if os.environ.get("HARNESS_CONFIG_REPLACE") == "1":
    replacement = path.with_name(path.name + ".replacement")
    replacement.write_text(data, encoding="utf-8")
    replacement.chmod(0o600)
    replacement.replace(path)
else:
    path.write_text(data, encoding="utf-8")
PY
elif [[ "$1 $2 $3" == "config set channels.discord.agentComponents.ttlMs" ]]; then
  mkdir -p "$HOME/.openclaw"
  "${HARNESS_REAL_PYTHON:?}" - "${HARNESS_CONFIG_PATH:?}" "$4" <<'PY'
import json
import os
import sys
from pathlib import Path
path = Path(sys.argv[1])
config = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
config.setdefault("channels", {}).setdefault("discord", {}).setdefault(
    "agentComponents", {}
)["ttlMs"] = json.loads(sys.argv[2])
data = json.dumps(config)
if os.environ.get("HARNESS_CONFIG_REPLACE") == "1":
    replacement = path.with_name(path.name + ".replacement")
    replacement.write_text(data, encoding="utf-8")
    replacement.chmod(0o600)
    replacement.replace(path)
else:
    path.write_text(data, encoding="utf-8")
PY
fi
exit 0
""",
    )
    _write_executable(
        fake_bin / "python3",
        f"""#!/usr/bin/env bash
if [[ "$1 $2" == "-m venv" ]]; then
  venv=$3
  mkdir -p "$venv/bin"
  cat > "$venv/bin/python" <<'PY'
#!/usr/bin/env bash
if [[ "$1 $2" == "-m pip" ]]; then exit 0; fi
if [[ "$1" == "-" ]]; then
  root=$2
  [[ -s "$root/config/markets/hanoi-80km.yaml" ]] || exit 1
  [[ -s "$root/config/scoring/base-v1.yaml" ]] || exit 1
  exit 0
fi
exec "{_bash_path(Path(sys.executable))}" "$@"
PY
  chmod +x "$venv/bin/python"
  cat > "$venv/bin/openclaw-web" <<'CLI'
#!/usr/bin/env bash
printf 'openclaw-web %s\\n' "$*" >> "$HARNESS_LOG"
exit 0
CLI
  chmod +x "$venv/bin/openclaw-web"
  exit 0
fi
exec "{_bash_path(Path(sys.executable))}" "$@"
""",
    )
    _write_executable(fake_bin / "lighthouse", "#!/usr/bin/env bash\nexit 0\n")
    _write_executable(fake_bin / "node", "#!/usr/bin/env bash\n[[ \"$1\" == \"--check\" ]]\n")
    _write_executable(
        fake_bin / "stat",
        """#!/usr/bin/env bash
if [[ "$1 $2" == "-c %a" && "$3" == */.config/openclaw-web/env ]]; then
  printf '600\\n'
  exit 0
fi
if [[ "$1 $2" == "-c %a" && "$3" == */.openclaw/openclaw.json ]]; then
  if [[ -f "$HARNESS_LOG.config-mode" ]]; then
    cat "$HARNESS_LOG.config-mode"
  else
    printf '%s\\n' "${HARNESS_CONFIG_ORIGINAL_MODE:-600}"
  fi
  exit 0
fi
if [[ "$1 $2" == "-c %a" && -n "${HARNESS_MODE_DRIFT_SUFFIX:-}" && "$3" == *"$HARNESS_MODE_DRIFT_SUFFIX" ]]; then
  printf '600\\n'
  exit 0
fi
exec /usr/bin/stat "$@"
""",
    )
    _write_executable(
        fake_bin / "chmod",
        """#!/usr/bin/env bash
if [[ "$#" == 2 && "$2" == */.openclaw/openclaw.json ]]; then
  printf '%s\\n' "$1" > "$HARNESS_LOG.config-mode"
  exit 0
fi
exec /usr/bin/chmod "$@"
""",
    )
    return fake_bin, log


def _run(
    script: str,
    args: list[Path],
    *,
    home: Path,
    fake_bin: Path,
    log: Path,
    extra_env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env.update(extra_env or {})
    runner = home.parent / f"run-{Path(script).stem}.sh"
    extra_exports = [
        f"export {key}={shlex.quote(value)}" for key, value in (extra_env or {}).items()
    ]
    _write_executable(
        runner,
        "\n".join(
            [
                "#!/usr/bin/env bash",
                f"export HOME={shlex.quote(_bash_path(home))}",
                f"export PATH={shlex.quote(_bash_path(fake_bin))}:$PATH",
                f"export HARNESS_LOG={shlex.quote(_bash_path(log))}",
                f"export HARNESS_REAL_PYTHON={shlex.quote(_bash_path(Path(sys.executable)))}",
                f"export HARNESS_CONFIG_PATH={shlex.quote(str(home / '.openclaw' / 'openclaw.json'))}",
                *extra_exports,
                f"printf '%s\\n' {shlex.quote((extra_env or {}).get('HARNESS_TIMER_ENABLED', 'disabled'))} > {shlex.quote(_bash_path(log))}.timer-enabled-state",
                f"printf '%s\\n' {shlex.quote((extra_env or {}).get('HARNESS_TIMER_ACTIVE', 'inactive'))} > {shlex.quote(_bash_path(log))}.timer-active-state",
                f"rm -f {shlex.quote(_bash_path(log))}.systemd-count",
                "exec "
                + " ".join(
                    [
                        shlex.quote(_bash_path(ROOT / script)),
                        *(shlex.quote(_bash_path(arg)) for arg in args),
                    ]
                ),
                "",
            ]
        ),
    )
    return subprocess.run(
        ["bash", _bash_path(runner)],
        check=False,
        capture_output=True,
        text=True,
        env=env,
    )


def _readlink(path: Path) -> str:
    result = subprocess.run(
        ["bash", "-c", f"readlink {shlex.quote(_bash_path(path))}"],
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def _glob_count(path: Path, pattern: str) -> int:
    result = subprocess.run(
        [
            "bash",
            "-c",
            f"shopt -s nullglob; matches=({shlex.quote(_bash_path(path))}/{pattern}); printf '%s\\n' \"${{#matches[@]}}\"",
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    return int(result.stdout.strip())


@pytest.fixture
def harness(tmp_path: Path) -> tuple[Path, Path, Path, Path, Path]:
    home = tmp_path / "home"
    home.mkdir()
    source, wheel = _copy_source(tmp_path)
    fake_bin, log = _fake_commands(tmp_path)
    return home, source, wheel, fake_bin, log


@pytest.mark.skipif(shutil.which("bash") is None, reason="bash is unavailable")
def test_fresh_install_has_exact_env_hash_bundle_and_keeps_timer_off(
    harness: tuple[Path, Path, Path, Path, Path],
) -> None:
    home, source, wheel, fake_bin, log = harness
    backup = home / ".openclaw" / "backups" / "fresh"

    result = _run(
        "deploy/install-remote.sh",
        [wheel, source, backup],
        home=home,
        fake_bin=fake_bin,
        log=log,
    )

    assert result.returncode == 0, result.stderr
    wheel_sha = __import__("hashlib").sha256(wheel.read_bytes()).hexdigest()
    current = home / ".local" / "share" / "openclaw-web" / "current"
    assert Path(_readlink(current)).name == wheel_sha
    env_file = home / ".config" / "openclaw-web" / "env"
    env_lines = env_file.read_text(encoding="utf-8").splitlines()
    assert {line.split("=", 1)[0] for line in env_lines} == ENV_KEYS
    assert "chmod 0600" in (ROOT / "deploy/install-remote.sh").read_text(encoding="utf-8")
    assert all("SECRET" not in line and "TOKEN" not in line for line in env_lines)
    bundle = (backup / "release-bundle.tsv").read_text(encoding="utf-8")
    assert f"wheel\tdist/package.whl\t{wheel_sha}" in bundle
    assert "discovery-prompt" not in bundle
    commands = log.read_text(encoding="utf-8")
    assert "enable openclaw-web-discovery.timer" not in commands
    assert "start openclaw-web-discovery.timer" not in commands
    assert "cron-run" not in commands
    assert "systemd-analyze --user verify" in commands
    plugin_root = home / ".openclaw" / "extensions" / "openclaw-web-components"
    assert _readlink(plugin_root).endswith(
        f"/releases/{wheel_sha}/openclaw-web-plugin"
    )
    release_plugin = (
        home
        / ".local"
        / "share"
        / "openclaw-web"
        / "releases"
        / wheel_sha
        / "openclaw-web-plugin"
    )
    assert (release_plugin / "index.js").is_file()
    assert (release_plugin / "openclaw.plugin.json").is_file()
    assert (release_plugin / "package.json").is_file()
    assert "openclaw plugins inspect openclaw-web-components --runtime --json" in commands


@pytest.mark.skipif(shutil.which("bash") is None, reason="bash is unavailable")
def test_install_preserves_existing_plugin_policy_and_unrelated_config(
    harness: tuple[Path, Path, Path, Path, Path],
) -> None:
    home, source, wheel, fake_bin, log = harness
    config = home / ".openclaw" / "openclaw.json"
    config.parent.mkdir(parents=True)
    config.write_text(
        json.dumps(
            {
                "plugins": {
                    "allow": ["discord", "browser"],
                    "entries": {
                        "discord": {
                            "enabled": True,
                            "config": {"marker": "keep"},
                        },
                        "openclaw-web-components": {
                            "enabled": False,
                            "config": {"marker": "keep"},
                        },
                    },
                },
                "channels": {
                    "discord": {
                        "agentComponents": {
                            "enabled": True,
                            "marker": "keep",
                        }
                    }
                },
                "unrelated": {"marker": "keep"},
            }
        ),
        encoding="utf-8",
    )
    config.chmod(0o640)

    result = _run(
        "deploy/install-remote.sh",
        [wheel, source, home / ".openclaw" / "backups" / "plugin-merge"],
        home=home,
        fake_bin=fake_bin,
        log=log,
        extra_env={
            "HARNESS_CONFIG_REPLACE": "1",
            "HARNESS_CONFIG_ORIGINAL_MODE": "640",
        },
    )

    assert result.returncode == 0, result.stderr
    merged = json.loads(config.read_text(encoding="utf-8"))
    assert merged["plugins"]["allow"] == [
        "discord",
        "browser",
        "openclaw-web-components",
    ]
    assert merged["plugins"]["entries"]["discord"] == {
        "enabled": True,
        "config": {"marker": "keep"},
    }
    assert merged["plugins"]["entries"]["openclaw-web-components"] == {
        "enabled": True,
        "config": {"marker": "keep"},
    }
    assert merged["channels"]["discord"]["agentComponents"] == {
        "enabled": True,
        "marker": "keep",
        "ttlMs": 86_400_000,
    }
    assert merged["unrelated"] == {"marker": "keep"}
    assert (Path(f"{log}.config-mode")).read_text(encoding="utf-8").strip() == "640"


@pytest.mark.skipif(shutil.which("bash") is None, reason="bash is unavailable")
def test_install_does_not_create_a_restrictive_allowlist_when_none_existed(
    harness: tuple[Path, Path, Path, Path, Path],
) -> None:
    home, source, wheel, fake_bin, log = harness
    config = home / ".openclaw" / "openclaw.json"

    result = _run(
        "deploy/install-remote.sh",
        [wheel, source, home / ".openclaw" / "backups" / "plugin-no-allow"],
        home=home,
        fake_bin=fake_bin,
        log=log,
    )

    assert result.returncode == 0, result.stderr
    installed = json.loads(config.read_text(encoding="utf-8"))
    assert "allow" not in installed["plugins"]
    assert installed["plugins"]["entries"]["openclaw-web-components"] == {
        "enabled": True
    }


@pytest.mark.skipif(shutil.which("bash") is None, reason="bash is unavailable")
def test_plugin_config_and_files_are_restored_by_rollback(
    harness: tuple[Path, Path, Path, Path, Path],
) -> None:
    home, source, wheel, fake_bin, log = harness
    config = home / ".openclaw" / "openclaw.json"
    config.parent.mkdir(parents=True)
    original = {
        "plugins": {"allow": ["discord"]},
        "unrelated": {"marker": "keep"},
    }
    config.write_text(json.dumps(original), encoding="utf-8")
    plugin_root = home / ".openclaw" / "extensions" / "openclaw-web-components"
    backup = home / ".openclaw" / "backups" / "plugin-rollback"

    installed = _run(
        "deploy/install-remote.sh",
        [wheel, source, backup],
        home=home,
        fake_bin=fake_bin,
        log=log,
    )
    assert installed.returncode == 0, installed.stderr
    assert _readlink(plugin_root).endswith("/openclaw-web-plugin")

    rolled_back = _run(
        "deploy/rollback-remote.sh",
        [backup],
        home=home,
        fake_bin=fake_bin,
        log=log,
    )

    assert rolled_back.returncode == 0, rolled_back.stderr
    assert json.loads(config.read_text(encoding="utf-8")) == original
    assert not (plugin_root / "index.js").exists()
    assert not (plugin_root / "openclaw.plugin.json").exists()
    assert not (plugin_root / "package.json").exists()


@pytest.mark.skipif(shutil.which("bash") is None, reason="bash is unavailable")
def test_install_and_rollback_preserve_a_preexisting_plugin_directory_exactly(
    harness: tuple[Path, Path, Path, Path, Path],
) -> None:
    home, source, wheel, fake_bin, log = harness
    plugin_root = home / ".openclaw" / "extensions" / "openclaw-web-components"
    plugin_root.mkdir(parents=True)
    original_index = plugin_root / "index.js"
    original_index.write_text("operator plugin", encoding="utf-8")
    original_index.chmod(0o640)
    marker = plugin_root / "operator-owned.txt"
    marker.write_text("keep", encoding="utf-8")
    plugin_root.chmod(0o750)
    backup = home / ".openclaw" / "backups" / "plugin-directory"

    installed = _run(
        "deploy/install-remote.sh",
        [wheel, source, backup],
        home=home,
        fake_bin=fake_bin,
        log=log,
    )
    assert installed.returncode == 0, installed.stderr
    assert _readlink(plugin_root).endswith("/openclaw-web-plugin")

    rolled_back = _run(
        "deploy/rollback-remote.sh",
        [backup],
        home=home,
        fake_bin=fake_bin,
        log=log,
    )

    assert rolled_back.returncode == 0, rolled_back.stderr
    assert plugin_root.is_dir() and not plugin_root.is_symlink()
    assert original_index.read_text(encoding="utf-8") == "operator plugin"
    assert marker.read_text(encoding="utf-8") == "keep"


@pytest.mark.skipif(shutil.which("bash") is None, reason="bash is unavailable")
def test_upgrade_and_rollback_restore_pointer_files_and_previous_timer_state(
    harness: tuple[Path, Path, Path, Path, Path],
) -> None:
    home, source, wheel, fake_bin, log = harness
    first_backup = home / ".openclaw" / "backups" / "first"
    first = _run("deploy/install-remote.sh", [wheel, source, first_backup], home=home, fake_bin=fake_bin, log=log)
    assert first.returncode == 0, first.stderr
    current = home / ".local" / "share" / "openclaw-web" / "current"
    previous = _readlink(current)
    wheel.write_bytes(b"wheel-v2")
    second_backup = home / ".openclaw" / "backups" / "second"
    second = _run(
        "deploy/install-remote.sh",
        [wheel, source, second_backup],
        home=home,
        fake_bin=fake_bin,
        log=log,
        extra_env={"HARNESS_TIMER_ENABLED": "enabled", "HARNESS_TIMER_ACTIVE": "active"},
    )
    assert second.returncode == 0, second.stderr
    assert _readlink(current) != previous

    rolled_back = _run(
        "deploy/rollback-remote.sh",
        [second_backup],
        home=home,
        fake_bin=fake_bin,
        log=log,
    )

    assert rolled_back.returncode == 0, rolled_back.stderr
    assert _readlink(current) == previous
    commands = log.read_text(encoding="utf-8")
    verify = commands.rindex("openclaw channels status --channel discord --probe")
    assert commands.rindex("systemctl --user enable openclaw-web-discovery.timer") > verify
    assert commands.rindex("systemctl --user start openclaw-web-discovery.timer") > verify


@pytest.mark.skipif(shutil.which("bash") is None, reason="bash is unavailable")
def test_rollback_restores_runtime_only_timer_enablement_after_validation(
    harness: tuple[Path, Path, Path, Path, Path],
) -> None:
    home, source, wheel, fake_bin, log = harness
    first = _run(
        "deploy/install-remote.sh",
        [wheel, source, home / ".openclaw" / "backups" / "runtime-first"],
        home=home,
        fake_bin=fake_bin,
        log=log,
    )
    assert first.returncode == 0, first.stderr
    wheel.write_bytes(b"wheel-runtime")
    backup = home / ".openclaw" / "backups" / "runtime-second"
    second = _run(
        "deploy/install-remote.sh",
        [wheel, source, backup],
        home=home,
        fake_bin=fake_bin,
        log=log,
        extra_env={
            "HARNESS_TIMER_ENABLED": "enabled-runtime",
            "HARNESS_TIMER_ACTIVE": "active",
        },
    )
    assert second.returncode == 0, second.stderr

    rolled_back = _run(
        "deploy/rollback-remote.sh", [backup], home=home, fake_bin=fake_bin, log=log
    )

    assert rolled_back.returncode == 0, rolled_back.stderr
    commands = log.read_text(encoding="utf-8")
    verify = commands.rindex("openclaw channels status --channel discord --probe")
    assert commands.rindex(
        "systemctl --user enable --runtime openclaw-web-discovery.timer"
    ) > verify


@pytest.mark.skipif(shutil.which("bash") is None, reason="bash is unavailable")
def test_rollback_fails_when_systemd_does_not_restore_the_saved_timer_state(
    harness: tuple[Path, Path, Path, Path, Path],
) -> None:
    home, source, wheel, fake_bin, log = harness
    first = _run(
        "deploy/install-remote.sh",
        [wheel, source, home / ".openclaw" / "backups" / "mismatch-first"],
        home=home,
        fake_bin=fake_bin,
        log=log,
    )
    assert first.returncode == 0, first.stderr
    wheel.write_bytes(b"wheel-mismatch")
    backup = home / ".openclaw" / "backups" / "mismatch-second"
    second = _run(
        "deploy/install-remote.sh",
        [wheel, source, backup],
        home=home,
        fake_bin=fake_bin,
        log=log,
        extra_env={"HARNESS_TIMER_ENABLED": "enabled", "HARNESS_TIMER_ACTIVE": "active"},
    )
    assert second.returncode == 0, second.stderr

    rolled_back = _run(
        "deploy/rollback-remote.sh",
        [backup],
        home=home,
        fake_bin=fake_bin,
        log=log,
        extra_env={"HARNESS_FORCE_RESTORE_MISMATCH": "1"},
    )

    assert rolled_back.returncode == 1
    assert "restored timer state does not match backup" in rolled_back.stderr


@pytest.mark.skipif(shutil.which("bash") is None, reason="bash is unavailable")
def test_rollback_restores_preexisting_symlink_without_overwriting_its_target(
    harness: tuple[Path, Path, Path, Path, Path],
) -> None:
    home, source, wheel, fake_bin, log = harness
    protected = home / "operator-owned.md"
    protected.write_text("operator content", encoding="utf-8")
    target = home / ".openclaw" / "workflow" / "COORDINATOR.md"
    target.parent.mkdir(parents=True)
    subprocess.run(
        [
            "bash",
            "-c",
            f"ln -s {shlex.quote(_bash_path(protected))} {shlex.quote(_bash_path(target))}",
        ],
        check=True,
    )
    backup = home / ".openclaw" / "backups" / "prior-symlink"

    installed = _run("deploy/install-remote.sh", [wheel, source, backup], home=home, fake_bin=fake_bin, log=log)
    assert installed.returncode == 0, installed.stderr
    assert protected.read_text(encoding="utf-8") == "operator content"

    rolled_back = _run("deploy/rollback-remote.sh", [backup], home=home, fake_bin=fake_bin, log=log)

    assert rolled_back.returncode == 0, rolled_back.stderr
    assert _readlink(target) == _bash_path(protected)
    assert protected.read_text(encoding="utf-8") == "operator content"


@pytest.mark.skipif(shutil.which("bash") is None, reason="bash is unavailable")
def test_same_wheel_refuses_changed_bundle_before_mutation(
    harness: tuple[Path, Path, Path, Path, Path],
) -> None:
    home, source, wheel, fake_bin, log = harness
    first_backup = home / ".openclaw" / "backups" / "bundle-first"
    first = _run("deploy/install-remote.sh", [wheel, source, first_backup], home=home, fake_bin=fake_bin, log=log)
    assert first.returncode == 0, first.stderr
    current = home / ".local" / "share" / "openclaw-web" / "current"
    pointer = _readlink(current)
    coordinator = source / "agents" / "shared" / "COORDINATOR.md"
    coordinator.write_text(coordinator.read_text(encoding="utf-8") + "\nasset drift\n", encoding="utf-8")

    second = _run(
        "deploy/install-remote.sh",
        [wheel, source, home / ".openclaw" / "backups" / "bundle-second"],
        home=home,
        fake_bin=fake_bin,
        log=log,
    )

    assert second.returncode == 2
    assert "release bundle hash mismatch" in second.stderr
    assert _readlink(current) == pointer


@pytest.mark.skipif(shutil.which("bash") is None, reason="bash is unavailable")
def test_existing_release_rechecks_offline_dependencies_before_mutation(
    harness: tuple[Path, Path, Path, Path, Path],
) -> None:
    home, source, wheel, fake_bin, log = harness
    first = _run(
        "deploy/install-remote.sh",
        [wheel, source, home / ".openclaw" / "backups" / "ready-first"],
        home=home,
        fake_bin=fake_bin,
        log=log,
    )
    assert first.returncode == 0, first.stderr
    (fake_bin / "lighthouse").unlink()

    retry = _run(
        "deploy/install-remote.sh",
        [wheel, source, home / ".openclaw" / "backups" / "ready-retry"],
        home=home,
        fake_bin=fake_bin,
        log=log,
    )

    assert retry.returncode == 2
    assert "lighthouse is required" in retry.stderr
    commands = log.read_text(encoding="utf-8")
    assert commands.count("systemctl --user disable --now") == 1


@pytest.mark.skipif(shutil.which("bash") is None, reason="bash is unavailable")
def test_existing_unknown_env_key_is_not_copied_or_printed(
    harness: tuple[Path, Path, Path, Path, Path],
) -> None:
    home, source, wheel, fake_bin, log = harness
    env_file = home / ".config" / "openclaw-web" / "env"
    env_file.parent.mkdir(parents=True)
    marker = "UNKNOWN_CREDENTIAL_SENTINEL"
    env_file.write_text(f"UNOWNED_SECRET={marker}\n", encoding="utf-8")

    result = _run(
        "deploy/install-remote.sh",
        [wheel, source, home / ".openclaw" / "backups" / "secret-env"],
        home=home,
        fake_bin=fake_bin,
        log=log,
    )

    assert result.returncode == 0, result.stderr
    assert marker not in result.stdout + result.stderr
    installed = env_file.read_text(encoding="utf-8")
    assert marker not in installed
    assert {line.split("=", 1)[0] for line in installed.splitlines()} == ENV_KEYS


@pytest.mark.skipif(shutil.which("bash") is None, reason="bash is unavailable")
def test_failed_offline_verification_rolls_back_fresh_install(
    harness: tuple[Path, Path, Path, Path, Path],
) -> None:
    home, source, wheel, fake_bin, log = harness
    backup = home / ".openclaw" / "backups" / "failed"

    result = _run(
        "deploy/install-remote.sh",
        [wheel, source, backup],
        home=home,
        fake_bin=fake_bin,
        log=log,
        extra_env={"HARNESS_SYSTEMD_FAIL_AFTER": "1"},
    )

    assert result.returncode != 0
    assert not (home / ".local" / "share" / "openclaw-web" / "current").exists()
    assert not (home / ".config" / "systemd" / "user" / "openclaw-web-discovery.service").exists()
    assert not (home / ".config" / "openclaw-web" / "env").exists()
    assert "SECRET" not in result.stdout + result.stderr
    commands = log.read_text(encoding="utf-8")
    assert "openclaw health" not in commands
    assert "channels status" not in commands
    assert "enable openclaw-web-discovery.timer" not in commands
    assert "start openclaw-web-discovery.timer" not in commands


@pytest.mark.skipif(shutil.which("bash") is None, reason="bash is unavailable")
def test_failed_preflight_removes_incomplete_release_staging(
    harness: tuple[Path, Path, Path, Path, Path],
) -> None:
    home, source, wheel, fake_bin, log = harness
    (source / "config" / "markets" / "hanoi-80km.yaml").write_text("", encoding="utf-8")

    result = _run(
        "deploy/install-remote.sh",
        [wheel, source, home / ".openclaw" / "backups" / "bad-config"],
        home=home,
        fake_bin=fake_bin,
        log=log,
    )

    assert result.returncode != 0
    releases = home / ".local" / "share" / "openclaw-web" / "releases"
    assert _glob_count(releases, ".*.new") == 0
    assert not (home / ".openclaw" / "backups" / "bad-config").exists()
    assert not log.exists()


@pytest.mark.skipif(shutil.which("bash") is None, reason="bash is unavailable")
def test_rollback_refuses_drift_without_restoring_timer(
    harness: tuple[Path, Path, Path, Path, Path],
) -> None:
    home, source, wheel, fake_bin, log = harness
    backup = home / ".openclaw" / "backups" / "drift"
    installed = _run("deploy/install-remote.sh", [wheel, source, backup], home=home, fake_bin=fake_bin, log=log)
    assert installed.returncode == 0, installed.stderr
    unit = home / ".config" / "systemd" / "user" / "openclaw-web-discovery.service"
    unit.write_text("operator edit", encoding="utf-8")

    rolled_back = _run("deploy/rollback-remote.sh", [backup], home=home, fake_bin=fake_bin, log=log)

    assert rolled_back.returncode == 1
    assert "deployment drift detected" in rolled_back.stderr
    assert unit.read_text(encoding="utf-8") == "operator edit"
    commands = log.read_text(encoding="utf-8")
    assert "enable openclaw-web-discovery.timer" not in commands
    assert "start openclaw-web-discovery.timer" not in commands


@pytest.mark.skipif(shutil.which("bash") is None, reason="bash is unavailable")
def test_rollback_refuses_installed_mode_drift_before_mutation(
    harness: tuple[Path, Path, Path, Path, Path],
) -> None:
    home, source, wheel, fake_bin, log = harness
    backup = home / ".openclaw" / "backups" / "mode-drift"
    installed = _run(
        "deploy/install-remote.sh", [wheel, source, backup], home=home, fake_bin=fake_bin, log=log
    )
    assert installed.returncode == 0, installed.stderr
    unit = home / ".config" / "systemd" / "user" / "openclaw-web-discovery.service"
    before = log.read_text(encoding="utf-8")

    rolled_back = _run(
        "deploy/rollback-remote.sh",
        [backup],
        home=home,
        fake_bin=fake_bin,
        log=log,
        extra_env={"HARNESS_MODE_DRIFT_SUFFIX": "openclaw-web-discovery.service"},
    )

    assert rolled_back.returncode == 1
    assert "deployment drift detected" in rolled_back.stderr
    assert unit.is_file()
    assert "systemctl --user disable --now" not in log.read_text(encoding="utf-8")[
        len(before) :
    ]


@pytest.mark.skipif(shutil.which("bash") is None, reason="bash is unavailable")
def test_rollback_refuses_immutable_plugin_asset_drift_before_mutation(
    harness: tuple[Path, Path, Path, Path, Path],
) -> None:
    home, source, wheel, fake_bin, log = harness
    backup = home / ".openclaw" / "backups" / "plugin-drift"
    installed = _run(
        "deploy/install-remote.sh", [wheel, source, backup], home=home, fake_bin=fake_bin, log=log
    )
    assert installed.returncode == 0, installed.stderr
    wheel_sha = __import__("hashlib").sha256(wheel.read_bytes()).hexdigest()
    plugin_asset = (
        home
        / ".local"
        / "share"
        / "openclaw-web"
        / "releases"
        / wheel_sha
        / "openclaw-web-plugin"
        / "index.js"
    )
    subprocess.run(
        [
            "bash",
            "-c",
            f"chmod u+w {shlex.quote(_bash_path(plugin_asset))} && printf drift > {shlex.quote(_bash_path(plugin_asset))}",
        ],
        check=True,
    )
    before = log.read_text(encoding="utf-8")

    rolled_back = _run(
        "deploy/rollback-remote.sh", [backup], home=home, fake_bin=fake_bin, log=log
    )

    assert rolled_back.returncode == 1
    assert "deployment drift detected" in rolled_back.stderr
    assert "systemctl --user disable --now" not in log.read_text(encoding="utf-8")[
        len(before) :
    ]


@pytest.mark.skipif(shutil.which("bash") is None, reason="bash is unavailable")
def test_rollback_refuses_timer_drift_before_mutation(
    harness: tuple[Path, Path, Path, Path, Path],
) -> None:
    home, source, wheel, fake_bin, log = harness
    backup = home / ".openclaw" / "backups" / "timer-drift"
    installed = _run(
        "deploy/install-remote.sh", [wheel, source, backup], home=home, fake_bin=fake_bin, log=log
    )
    assert installed.returncode == 0, installed.stderr
    before = log.read_text(encoding="utf-8")

    rolled_back = _run(
        "deploy/rollback-remote.sh",
        [backup],
        home=home,
        fake_bin=fake_bin,
        log=log,
        extra_env={"HARNESS_TIMER_ENABLED": "enabled", "HARNESS_TIMER_ACTIVE": "active"},
    )

    assert rolled_back.returncode == 1
    assert "deployment drift detected" in rolled_back.stderr
    rollback_commands = log.read_text(encoding="utf-8")[len(before) :]
    assert "systemctl --user disable --now" not in rollback_commands


@pytest.mark.skipif(shutil.which("bash") is None, reason="bash is unavailable")
def test_rollback_prevalidates_every_backup_before_mutation(
    harness: tuple[Path, Path, Path, Path, Path],
) -> None:
    home, source, wheel, fake_bin, log = harness
    config = home / ".openclaw" / "openclaw.json"
    config.parent.mkdir(parents=True)
    config.write_text(json.dumps({"unrelated": "keep"}), encoding="utf-8")
    backup = home / ".openclaw" / "backups" / "corrupt-backup"
    installed = _run(
        "deploy/install-remote.sh", [wheel, source, backup], home=home, fake_bin=fake_bin, log=log
    )
    assert installed.returncode == 0, installed.stderr
    service = home / ".config" / "systemd" / "user" / "openclaw-web-discovery.service"
    installed_service = service.read_bytes()
    (backup / "files" / ".openclaw" / "openclaw.json").write_text(
        "corrupt", encoding="utf-8"
    )
    before = log.read_text(encoding="utf-8")

    rolled_back = _run(
        "deploy/rollback-remote.sh", [backup], home=home, fake_bin=fake_bin, log=log
    )

    assert rolled_back.returncode == 2
    assert "backup file hash mismatch" in rolled_back.stderr
    assert service.read_bytes() == installed_service
    assert "systemctl --user disable --now" not in log.read_text(encoding="utf-8")[
        len(before) :
    ]


@pytest.mark.skipif(shutil.which("bash") is None, reason="bash is unavailable")
def test_rollback_rejects_an_invalid_saved_timer_state_before_mutation(
    harness: tuple[Path, Path, Path, Path, Path],
) -> None:
    home, source, wheel, fake_bin, log = harness
    backup = home / ".openclaw" / "backups" / "invalid-timer"
    installed = _run(
        "deploy/install-remote.sh", [wheel, source, backup], home=home, fake_bin=fake_bin, log=log
    )
    assert installed.returncode == 0, installed.stderr
    (backup / "timer-enabled").write_text("unexpected-state\n", encoding="utf-8")
    service = home / ".config" / "systemd" / "user" / "openclaw-web-discovery.service"
    before = log.read_text(encoding="utf-8")

    rolled_back = _run(
        "deploy/rollback-remote.sh", [backup], home=home, fake_bin=fake_bin, log=log
    )

    assert rolled_back.returncode == 2
    assert "unsupported saved timer state" in rolled_back.stderr
    assert service.is_file()
    assert "systemctl --user disable --now" not in log.read_text(encoding="utf-8")[
        len(before) :
    ]


@pytest.mark.skipif(shutil.which("bash") is None, reason="bash is unavailable")
def test_installer_refuses_reused_backup_before_any_mutation(tmp_path: Path) -> None:
    home = tmp_path / "home"
    source, wheel = _copy_source(tmp_path)
    fake_bin, log = _fake_commands(tmp_path)
    backup = home / ".openclaw" / "backups" / "existing"
    backup.mkdir(parents=True)
    marker = backup / "keep"
    marker.write_text("owned", encoding="utf-8")

    result = _run("deploy/install-remote.sh", [wheel, source, backup], home=home, fake_bin=fake_bin, log=log)

    assert result.returncode == 2
    assert result.stdout == ""
    assert result.stderr == "backup root already exists\n"
    assert marker.read_text(encoding="utf-8") == "owned"
