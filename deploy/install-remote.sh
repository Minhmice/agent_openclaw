#!/usr/bin/env bash
set -euo pipefail
umask 077

if [[ $# -ne 3 ]]; then
  echo "usage: install-remote.sh <wheel> <source-root> <backup-root>" >&2
  exit 2
fi

wheel=$(realpath "$1")
source_root=$(realpath "$2")
backup_root=$(realpath -m "$3")
home=$(realpath "$HOME")

case "$wheel" in "$source_root"/*) ;; *) echo "wheel must be under source root" >&2; exit 2;; esac
case "$backup_root" in "$home/.openclaw/backups"/*) ;; *) echo "backup root must be timestamped under ~/.openclaw/backups" >&2; exit 2;; esac
[[ -f "$wheel" ]] || { echo "wheel does not exist" >&2; exit 2; }
[[ ! -e "$backup_root" ]] || { echo "backup root already exists" >&2; exit 2; }

app_root="$HOME/.local/share/openclaw-web"
releases="$app_root/releases"
current="$app_root/current"
workflow="$HOME/.openclaw/workflow"
workspace_workflow="$HOME/.openclaw/workspace/workflow"
workflow_roots=("$workflow" "$workspace_workflow")
plugin_root="$HOME/.openclaw/extensions/openclaw-web-components"
openclaw_config="$HOME/.openclaw/openclaw.json"
units="$HOME/.config/systemd/user"
config_dir="$HOME/.config/openclaw-web"
state_root="$HOME/.local/state/openclaw-web"
artifact_root="$workflow/projects"
schemas="$app_root/schemas/generated"
manifest="$backup_root/manifest.tsv"
installed_manifest="$backup_root/installed.tsv"
bundle_manifest="$backup_root/release-bundle.tsv"
immutable_manifest="$backup_root/immutable.tsv"
previous_current="$backup_root/previous-current"

workflow_assets=(
  agents/shared/workflow-coordinator.py
  agents/shared/COORDINATOR.md
  agents/shared/QUALITY-GATES.md
  agents/shared/VIETNAMESE-LANGUAGE-POLICY.md
  agents/shared/README.md
  agents/shared/website-redesign-agent-spec.md
  agents/shared/contracts/curie-handoff.md
  agents/shared/contracts/curie-report.md
  agents/shared/contracts/curie-to-website.yaml
  agents/shared/contracts/discuss-intents.md
  agents/shared/contracts/website-to-pm.yaml
  agents/shared/contracts/workflow-commands.md
  config/markets/hanoi-80km.yaml
  config/scoring/base-v1.yaml
  config/scoring/cohorts/ecommerce.yaml
  config/scoring/cohorts/education.yaml
  config/scoring/cohorts/healthcare.yaml
  config/scoring/cohorts/hospitality.yaml
  config/scoring/cohorts/local-service.yaml
  config/scoring/cohorts/manufacturer.yaml
  config/scoring/cohorts/other.yaml
  config/scoring/cohorts/professional-services.yaml
  config/scoring/cohorts/real-estate.yaml
  config/scoring/cohorts/showroom-retail.yaml
)
schema_assets=(
  schemas/generated/artifact_envelope.json
  schemas/generated/audit_record.json
  schemas/generated/candidate.json
  schemas/generated/candidate_seed.json
  schemas/generated/component_set.json
  schemas/generated/delivery_record.json
  schemas/generated/evidence.json
  schemas/generated/feedback_event.json
  schemas/generated/issue_record.json
  schemas/generated/page_record.json
  schemas/generated/run_record.json
  schemas/generated/score_record.json
  schemas/generated/stage_record.json
)
unit_assets=(
  deploy/openclaw-web-discovery.service
  deploy/openclaw-web-discovery.timer
)
plugin_assets=(
  deploy/openclaw-web-plugin/index.js
  deploy/openclaw-web-plugin/openclaw.plugin.json
  deploy/openclaw-web-plugin/package.json
)
bundle_assets=("${workflow_assets[@]}" "${schema_assets[@]}" "${unit_assets[@]}" "${plugin_assets[@]}")

for relative in "${bundle_assets[@]}"; do
  [[ -s "$source_root/$relative" ]] || {
    echo "required release asset is missing or empty: $relative" >&2
    exit 2
  }
done
[[ ! -e "$current" || -L "$current" || -f "$current" ]] || {
  echo "current pointer has unsupported type" >&2
  exit 2
}
[[ ! -e "$openclaw_config" || (-f "$openclaw_config" && ! -L "$openclaw_config") ]] || {
  echo "OpenClaw config must be a regular file or absent" >&2
  exit 2
}
[[ ! -e "$plugin_root" || -L "$plugin_root" || -d "$plugin_root" ]] || {
  echo "plugin root has unsupported type" >&2
  exit 2
}

# Validate staged units before touching the timer or any installed target. The
# service points at the immutable `current` release, which may not exist on a
# first install, so verify an equivalent temporary unit with safe placeholder
# runtime paths. The installed unit is verified again after its pointer exists.
verify_staged_units() {
  local verify_dir
  verify_dir=$(mktemp -d)
  sed \
    -e '/^EnvironmentFile=/d' \
    -e 's#^WorkingDirectory=.*$#WorkingDirectory=/tmp#' \
    -e 's#^ExecStart=.*$#ExecStart=/bin/true#' \
    "$source_root/deploy/openclaw-web-discovery.service" \
    > "$verify_dir/openclaw-web-discovery.service"
  cp "$source_root/deploy/openclaw-web-discovery.timer" \
    "$verify_dir/openclaw-web-discovery.timer"
  if ! systemd-analyze --user verify \
    "$verify_dir/openclaw-web-discovery.service" \
    "$verify_dir/openclaw-web-discovery.timer"; then
    rm -rf -- "$verify_dir"
    return 1
  fi
  rm -rf -- "$verify_dir"
}
verify_staged_units
node --check "$source_root/deploy/openclaw-web-plugin/index.js"
openclaw config validate >/dev/null

wheel_sha=$(sha256sum "$wheel" | cut -d' ' -f1)
release="$releases/$wheel_sha"
staged_asset_manifest=$(mktemp)
release_tmp=""
cleanup_staging() {
  rm -f -- "$staged_asset_manifest"
  if [[ -n "$release_tmp" && -e "$release_tmp" ]]; then
    rm -rf -- "$release_tmp"
  fi
}
trap cleanup_staging EXIT
{
  printf 'wheel\t%s\t%s\n' "${wheel#"$source_root/"}" "$wheel_sha"
  for relative in "${bundle_assets[@]}"; do
    printf 'asset\t%s\t%s\n' "$relative" "$(sha256sum "$source_root/$relative" | cut -d' ' -f1)"
  done
} > "$staged_asset_manifest"
staged_asset_sha=$(sha256sum "$staged_asset_manifest" | cut -d' ' -f1)
offline_readiness() {
  local python=$1
  "$python" - "$source_root" <<'PY'
import json
import sys
from pathlib import Path

import yaml
from openclaw_web.runtime import run_daily_discovery  # noqa: F401
from playwright.sync_api import sync_playwright

root = Path(sys.argv[1])
for path in [
    root / "config/markets/hanoi-80km.yaml",
    root / "config/scoring/base-v1.yaml",
    *sorted((root / "config/scoring/cohorts").glob("*.yaml")),
]:
    value = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict) or not value:
        raise SystemExit(f"invalid required YAML: {path.relative_to(root)}")
for path in sorted((root / "schemas/generated").glob("*.json")):
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict) or not value:
        raise SystemExit(f"invalid required schema: {path.relative_to(root)}")
with sync_playwright() as playwright:
    if not Path(playwright.chromium.executable_path).is_file():
        raise SystemExit("Playwright Chromium is not installed")
PY
  command -v lighthouse >/dev/null || { echo "lighthouse is required" >&2; return 2; }
}

mkdir -p "$releases"
release_is_usable() {
  local candidate=$1
  [[ -x "$candidate/venv/bin/openclaw-web" &&
     -s "$candidate/release-manifest" &&
     -d "$candidate/openclaw-web-plugin" ]] || return 1
  "$candidate/venv/bin/openclaw-web" --help >/dev/null 2>&1
}
if [[ -e "$release" ]] && ! release_is_usable "$release"; then
  if [[ -L "$current" && "$(readlink "$current")" == "$release" ]]; then
    echo "existing current release is incomplete" >&2
    exit 2
  fi
  rm -rf -- "$release"
fi
if [[ ! -e "$release" ]]; then
  release_tmp="$release"
  mkdir -m 0755 "$release_tmp"
  python3 -m venv "$release_tmp/venv"
  "$release_tmp/venv/bin/python" -m pip install --disable-pip-version-check "$wheel" >/dev/null
  "$release_tmp/venv/bin/python" -m pip check >/dev/null
  mkdir -m 0755 "$release_tmp/openclaw-web-plugin"
  for relative in "${plugin_assets[@]}"; do
    install -m 0444 "$source_root/$relative" \
      "$release_tmp/openclaw-web-plugin/${relative#deploy/openclaw-web-plugin/}"
  done
  chmod 0555 "$release_tmp/openclaw-web-plugin"
  cp "$staged_asset_manifest" "$release_tmp/asset-manifest.tsv"
  printf 'wheel_sha=%s\nasset_manifest_sha=%s\n' "$wheel_sha" "$staged_asset_sha" > "$release_tmp/release-manifest"

  # Offline readiness: imports, non-empty YAML mappings, parseable schemas, and
  # local executables. No discovery, crawl, gateway call, or send.
  offline_readiness "$release_tmp/venv/bin/python"
  release_tmp=""
else
  release_is_usable "$release" || {
    echo "existing immutable release is incomplete" >&2
    exit 2
  }
  grep -Fxq "wheel_sha=$wheel_sha" "$release/release-manifest" || {
    echo "existing immutable release hash mismatch" >&2
    exit 2
  }
  grep -Fxq "asset_manifest_sha=$staged_asset_sha" "$release/release-manifest" || {
    echo "release bundle hash mismatch" >&2
    exit 2
  }
  [[ "$(sha256sum "$release/asset-manifest.tsv" | cut -d' ' -f1)" == "$staged_asset_sha" ]] || {
    echo "immutable release asset manifest drift" >&2
    exit 2
  }
  for relative in "${plugin_assets[@]}"; do
    release_asset="$release/openclaw-web-plugin/${relative#deploy/openclaw-web-plugin/}"
    release_asset_sha=$(sha256sum "$release_asset" | cut -d' ' -f1)
    source_asset_sha=$(sha256sum "$source_root/$relative" | cut -d' ' -f1)
    [[ -f "$release_asset" && ! -L "$release_asset" &&
        "$release_asset_sha" == "$source_asset_sha" ]] || {
      echo "immutable release plugin drift" >&2
      exit 2
    }
  done
  offline_readiness "$release/venv/bin/python"
fi
release_plugin="$release/openclaw-web-plugin"
node --check "$release_plugin/index.js"

mkdir -m 0700 -p "$backup_root"
chmod 0700 "$backup_root"
: > "$manifest"
: > "$installed_manifest"
: > "$immutable_manifest"
cp "$release/asset-manifest.tsv" "$bundle_manifest"
for relative in "${plugin_assets[@]}"; do
  release_asset="$release_plugin/${relative#deploy/openclaw-web-plugin/}"
  printf 'file\t%s\t%s\t%s\n' "${release_asset#"$HOME/"}" \
    "$(sha256sum "$release_asset" | cut -d' ' -f1)" "$(stat -c '%a' "$release_asset")" \
    >> "$immutable_manifest"
done
timer_enabled=$(systemctl --user is-enabled openclaw-web-discovery.timer 2>/dev/null || true)
timer_active=$(systemctl --user is-active openclaw-web-discovery.timer 2>/dev/null || true)
case "$timer_enabled" in
  enabled|enabled-runtime|disabled|not-found) ;;
  *) echo "unsupported current timer enablement state" >&2; exit 2;;
esac
case "$timer_active" in
  active|inactive) ;;
  *) echo "unsupported current timer activity state" >&2; exit 2;;
esac
printf '%s\n' "$timer_enabled" > "$backup_root/timer-enabled"
printf '%s\n' "$timer_active" > "$backup_root/timer-active"
if [[ -L "$current" ]]; then readlink "$current" > "$previous_current"; else : > "$previous_current"; fi

mutation_started=1
remove_target() {
  local target=$1
  if [[ -d "$target" && ! -L "$target" ]]; then
    rm -rf -- "$target"
  else
    rm -f -- "$target"
  fi
}

restore_partial_install() {
  local disposition relative metadata mode target source archive
  while IFS=$'\t' read -r disposition relative metadata mode; do
    target="$HOME/$relative"
    case "$disposition" in
      absent) remove_target "$target" ;;
      file)
        source="$backup_root/files/$relative"
        mkdir -p "$(dirname "$target")"
        remove_target "$target"
        cp -a "$source" "$target"
        ;;
      symlink)
        mkdir -p "$(dirname "$target")"
        remove_target "$target"
        ln -s "$metadata" "$target"
        ;;
      directory)
        archive="$backup_root/trees/$relative.tar"
        mkdir -p "$(dirname "$target")"
        remove_target "$target"
        tar -xpf "$archive" -C "$(dirname "$target")"
        ;;
    esac
  done < <(tac "$manifest")
  systemctl --user daemon-reload >/dev/null 2>&1 || true
}

on_error() {
  local status=$?
  trap - ERR
  if [[ "${mutation_started:-0}" == 1 ]]; then
    echo "installation failed; attempting manifest rollback" >&2
    restore_partial_install || \
      echo "automatic file rollback failed; use deploy/rollback-remote.sh with this backup" >&2
  fi
  exit "$status"
}
trap on_error ERR

systemctl --user disable --now openclaw-web-discovery.timer >/dev/null 2>&1 || true
systemctl --user stop openclaw-web-discovery.service >/dev/null 2>&1 || true

record_target() {
  local target=$1 relative archive
  case "$target" in "$HOME"/*) ;; *) echo "target outside HOME" >&2; return 2;; esac
  relative=${target#"$HOME/"}
  if [[ -L "$target" ]]; then
    printf 'symlink\t%s\t%s\t-\n' "$relative" "$(readlink "$target")" >> "$manifest"
  elif [[ -f "$target" ]]; then
    mkdir -p "$backup_root/files/$(dirname "$relative")"
    cp -a "$target" "$backup_root/files/$relative"
    printf 'file\t%s\t%s\t%s\n' "$relative" \
      "$(sha256sum "$target" | cut -d' ' -f1)" "$(stat -c '%a' "$target")" >> "$manifest"
  elif [[ -d "$target" ]]; then
    archive="$backup_root/trees/$relative.tar"
    mkdir -p "$(dirname "$archive")"
    tar --format=posix -cpf "$archive" -C "$(dirname "$target")" "$(basename "$target")"
    printf 'directory\t%s\t%s\t%s\n' "$relative" \
      "$(sha256sum "$archive" | cut -d' ' -f1)" "$(stat -c '%a' "$target")" >> "$manifest"
  elif [[ -e "$target" ]]; then
    echo "unsupported existing target type" >&2
    return 2
  else
    printf 'absent\t%s\t-\t-\n' "$relative" >> "$manifest"
  fi
}

record_installed_file() {
  local target=$1
  printf 'file\t%s\t%s\t%s\n' "${target#"$HOME/"}" \
    "$(sha256sum "$target" | cut -d' ' -f1)" "$(stat -c '%a' "$target")" >> "$installed_manifest"
}

record_installed_symlink() {
  local target=$1
  printf 'symlink\t%s\t%s\t-\n' "${target#"$HOME/"}" "$(readlink "$target")" >> "$installed_manifest"
}

install_file() {
  local mode=$1 source=$2 target=$3
  record_target "$target"
  mkdir -p "$(dirname "$target")"
  install -m "$mode" "$source" "$target"
  record_installed_file "$target"
}

workflow_target() {
  local root=$1 relative=$2
  case "$relative" in
    agents/shared/*) printf '%s/%s\n' "$root" "${relative#agents/shared/}" ;;
    config/*|schemas/*) printf '%s/%s\n' "$root" "$relative" ;;
    *) return 2 ;;
  esac
}

for root in "${workflow_roots[@]}"; do
  for relative in "${workflow_assets[@]}" "${schema_assets[@]}"; do
    mode=0644
    [[ "$relative" == "agents/shared/workflow-coordinator.py" ]] && mode=0755
    install_file "$mode" "$source_root/$relative" "$(workflow_target "$root" "$relative")"
  done
done
for relative in "${schema_assets[@]}"; do
  install_file 0644 "$source_root/$relative" "$schemas/${relative#schemas/generated/}"
done
install_file 0644 "$source_root/deploy/openclaw-web-discovery.service" "$units/openclaw-web-discovery.service"
install_file 0644 "$source_root/deploy/openclaw-web-discovery.timer" "$units/openclaw-web-discovery.timer"
record_target "$plugin_root"
mkdir -p "$(dirname "$plugin_root")"
plugin_link_tmp="$plugin_root.new"
[[ ! -e "$plugin_link_tmp" && ! -L "$plugin_link_tmp" ]] || {
  echo "plugin staging pointer already exists" >&2
  false
}
ln -s "$release_plugin" "$plugin_link_tmp"
remove_target "$plugin_root"
mv -Tf "$plugin_link_tmp" "$plugin_root"
record_installed_symlink "$plugin_root"

# Install the source before referencing its id in plugin policy. OpenClaw's
# validated config writer preserves unrelated entries. An existing restrictive
# allowlist is merged in memory; an absent allowlist stays absent.
config_mode=""
if [[ -f "$openclaw_config" ]]; then
  config_mode=$(stat -c '%a' "$openclaw_config")
fi
record_target "$openclaw_config"
if plugin_allow=$(openclaw config get plugins.allow --json 2>/dev/null); then
  merged_allow=$(printf '%s' "$plugin_allow" | python3 -c '
import json
import sys

value = json.load(sys.stdin)
if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
    raise SystemExit("plugins.allow must be an array of strings")
if "openclaw-web-components" not in value:
    value.append("openclaw-web-components")
print(json.dumps(value, separators=(",", ":")))
')
  openclaw config set plugins.allow "$merged_allow" --strict-json >/dev/null
fi
openclaw config set plugins.entries.openclaw-web-components.enabled true --strict-json >/dev/null
openclaw config set channels.discord.agentComponents.ttlMs 86400000 --strict-json >/dev/null
if [[ -n "$config_mode" ]]; then
  chmod "$config_mode" "$openclaw_config"
else
  chmod 0600 "$openclaw_config"
fi
openclaw config validate >/dev/null
record_installed_file "$openclaw_config"

env_file="$config_dir/env"
record_target "$env_file"
mkdir -m 0700 -p "$config_dir" "$state_root" "$artifact_root"
chmod 0700 "$config_dir" "$state_root"
env_tmp=$(mktemp "$config_dir/env.XXXXXX")
printf '%s\n' \
  "OPENCLAW_WEB_STATE_DB=$state_root/state.sqlite" \
  "OPENCLAW_WEB_ARTIFACT_ROOT=$artifact_root" \
  "OPENCLAW_WEB_SCHEMA_ROOT=$schemas" \
  "OPENCLAW_WEB_MARKET_CONFIG=$workflow/config/markets/hanoi-80km.yaml" \
  "OPENCLAW_WEB_SCORING_CONFIG=$workflow/config/scoring/base-v1.yaml" \
  "OPENCLAW_WORKFLOW_ROOT=$workflow" \
  "OPENCLAW_WEB_DISCORD_GUILD_ID=1446612692910739637" \
  "OPENCLAW_WEB_REVIEW_CHANNEL_ID=1536658476288450630" \
  "PATH=$HOME/.local/share/openclaw-web/tools/node_modules/.bin:/usr/local/bin:/usr/bin:/bin" > "$env_tmp"
chmod 0600 "$env_tmp"
mv -f "$env_tmp" "$env_file"
[[ "$(stat -c '%a' "$env_file")" == "600" ]] || {
  echo "environment file mode is not 0600" >&2
  false
}
record_installed_file "$env_file"

record_target "$current"
ln -s "$release" "$app_root/current.new"
mv -Tf "$app_root/current.new" "$current"
record_installed_symlink "$current"

systemctl --user daemon-reload
systemd-analyze --user verify \
  "$units/openclaw-web-discovery.service" \
  "$units/openclaw-web-discovery.timer"
[[ "$(systemctl --user is-enabled openclaw-web-discovery.timer 2>/dev/null || true)" == "disabled" ]]
[[ "$(systemctl --user is-active openclaw-web-discovery.timer 2>/dev/null || true)" == "inactive" ]]
"$current/venv/bin/openclaw-web" --help >/dev/null
openclaw plugins inspect openclaw-web-components --runtime --json >/dev/null
openclaw config validate >/dev/null

mutation_started=0
trap - ERR
rm -f "$staged_asset_manifest"
trap - EXIT
printf 'release_sha=%s\nbackup_timestamp=%s\ntimer_enabled=false\ninstall_verified=offline\n' \
  "$wheel_sha" "$(basename "$backup_root")"
