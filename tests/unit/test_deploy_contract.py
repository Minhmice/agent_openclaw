from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).parents[2]


def _read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_service_uses_immutable_current_release_and_required_env() -> None:
    service = _read("deploy/openclaw-web-discovery.service")

    assert "EnvironmentFile=%h/.config/openclaw-web/env" in service
    assert "EnvironmentFile=-" not in service
    assert "%h/.local/share/openclaw-web/current/venv/bin/openclaw-web" in service
    assert "cron-run --json" in service


def test_installer_owns_exact_nonsecret_environment_contract() -> None:
    installer = _read("deploy/install-remote.sh")
    required = {
        "OPENCLAW_WEB_STATE_DB",
        "OPENCLAW_WEB_ARTIFACT_ROOT",
        "OPENCLAW_WEB_SCHEMA_ROOT",
        "OPENCLAW_WEB_MARKET_CONFIG",
        "OPENCLAW_WEB_SCORING_CONFIG",
        "OPENCLAW_WORKFLOW_ROOT",
        "OPENCLAW_WEB_REVIEW_CHANNEL_ID",
    }

    for key in required:
        assert f'"{key}=' in installer
    assert "OPENCLAW_WEB_RUBRIC_CONFIG" not in installer
    assert "1536658476288450630" in installer
    assert "chmod 0600" in installer
    assert "source \"$env_file\"" not in installer
    assert "cat \"$env_file\"" not in installer


def test_installer_uses_hash_named_release_atomic_pointer_and_frozen_timer() -> None:
    installer = _read("deploy/install-remote.sh")

    assert 'release="$releases/$wheel_sha"' in installer
    assert '"$release/venv/bin/openclaw-web"' in installer
    assert "current.new" in installer
    assert 'mv -Tf "$app_root/current.new" "$current"' in installer
    assert "systemctl --user disable --now openclaw-web-discovery.timer" in installer
    assert "systemctl --user stop openclaw-web-discovery.service" in installer
    assert "enable --now openclaw-web-discovery.timer" not in installer
    assert "systemctl --user restart openclaw-gateway.service" not in installer


def test_installer_bundle_is_explicit_hashed_and_has_no_legacy_prompt() -> None:
    installer = _read("deploy/install-remote.sh")

    assert "release-bundle.tsv" in installer
    assert "wheel_sha" in installer
    assert "sha256sum" in installer
    assert "bundle_assets=(" in installer
    assert "find " not in installer
    assert "discovery-prompt" not in installer
    assert not (ROOT / "deploy/discovery-prompt.vi.txt").exists()


def test_installer_hashes_and_installs_the_native_components_plugin() -> None:
    installer = _read("deploy/install-remote.sh")

    for relative in (
        "deploy/openclaw-web-plugin/index.js",
        "deploy/openclaw-web-plugin/openclaw.plugin.json",
        "deploy/openclaw-web-plugin/package.json",
    ):
        assert relative in installer
    assert 'plugin_root="$HOME/.openclaw/extensions/openclaw-web-components"' in installer
    assert 'release_plugin="$release/openclaw-web-plugin"' in installer
    assert 'ln -s "$release_plugin" "$plugin_link_tmp"' in installer
    assert 'node --check "$source_root/deploy/openclaw-web-plugin/index.js"' in installer
    assert 'node --check "$release_plugin/index.js"' in installer
    assert "openclaw plugins inspect openclaw-web-components --runtime --json" in installer
    assert "openclaw plugins install" not in installer
    assert "openclaw plugins enable" not in installer
    assert "openclaw gateway restart" not in installer


def test_installer_freezes_plugin_directory_only_after_populating_it() -> None:
    installer = _read("deploy/install-remote.sh")

    assert 'mkdir -m 0755 "$release_tmp/openclaw-web-plugin"' in installer
    freeze = 'chmod 0555 "$release_tmp/openclaw-web-plugin"'
    assert freeze in installer
    assert installer.index(freeze) > installer.index('install -m 0444 "$source_root/$relative"')


def test_installer_sets_component_ttl_with_a_targeted_validated_write() -> None:
    installer = _read("deploy/install-remote.sh")

    setting = "channels.discord.agentComponents.ttlMs"
    assert f"openclaw config set {setting} 86400000 --strict-json" in installer
    assert installer.index(f"openclaw config set {setting}") < installer.rindex(
        "openclaw config validate"
    )
    assert 'cat "$openclaw_config"' not in installer


def test_installer_syncs_complete_contract_to_both_workflow_roots() -> None:
    installer = _read("deploy/install-remote.sh")

    for required in (
        "QUALITY-GATES.md",
        "VIETNAMESE-LANGUAGE-POLICY.md",
        "website-redesign-agent-spec.md",
        "contracts/curie-to-website.yaml",
        "config/markets/hanoi-80km.yaml",
        "config/scoring/base-v1.yaml",
        "schemas/generated/candidate.json",
    ):
        assert required in installer
    assert 'workflow_roots=("$workflow" "$workspace_workflow")' in installer


def test_installer_verifies_staged_and_installed_units_without_live_run() -> None:
    installer = _read("deploy/install-remote.sh")

    assert installer.count("systemd-analyze --user verify") >= 2
    assert "systemctl --user daemon-reload" in installer
    assert "is-enabled openclaw-web-discovery.timer" in installer
    assert "is-active openclaw-web-discovery.timer" in installer
    assert "cron-run --dry-run" not in installer
    assert "cron-run --json" not in installer
    assert "openclaw-web health" not in installer
    assert "openclaw config validate" in installer
    assert "install_verified=offline" in installer
    assert "healthy=true" not in installer


def test_rollback_is_drift_safe_preserves_data_and_restores_timer_last() -> None:
    rollback = _read("deploy/rollback-remote.sh")

    assert "manifest.tsv" in rollback
    assert "installed.tsv" in rollback
    assert "previous-current" in rollback
    assert "deployment drift detected" in rollback
    assert "systemd-analyze --user verify" in rollback
    assert "openclaw config validate" in rollback
    assert "openclaw health" in rollback
    assert "openclaw channels status --channel discord --probe" in rollback
    assert ".local/state/openclaw-web" not in rollback
    assert ".openclaw/workflow/projects" not in rollback
    verify_position = rollback.index("openclaw channels status --channel discord --probe")
    assert rollback.index("systemctl --user enable openclaw-web-discovery.timer") > verify_position
    assert rollback.index("systemctl --user start openclaw-web-discovery.timer") > verify_position
    assert 'restored_enabled=$(systemctl --user is-enabled' in rollback
    assert 'restored_active=$(systemctl --user is-active' in rollback


def test_deploy_backs_up_and_narrowly_merges_plugin_config() -> None:
    combined = _read("deploy/install-remote.sh") + _read("deploy/rollback-remote.sh")

    installer = _read("deploy/install-remote.sh")
    assert 'openclaw_config="$HOME/.openclaw/openclaw.json"' in installer
    assert 'record_target "$openclaw_config"' in installer
    assert "plugins.allow" in installer
    assert "openclaw-web-components" in installer
    assert "plugins.entries.openclaw-web-components.enabled" in installer
    assert 'record_installed_file "$openclaw_config"' in installer
    assert 'cat "$openclaw_config"' not in combined
    assert "openclaw config get --json" not in combined


def test_live_operational_docs_use_lead_approve_as_the_typed_fallback() -> None:
    for relative in (
        "docs/runbooks/web-audit-discovery.md",
        "agents/website-brief/README.md",
    ):
        document = _read(relative)
        assert "/lead-approve <project_id>" in document
        assert "/approve <project_id>" not in document
