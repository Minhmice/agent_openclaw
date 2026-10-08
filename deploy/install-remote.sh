#!/usr/bin/env bash
set -euo pipefail
umask 077
[[ "${OPENCLAW_INSTALL_DEBUG:-0}" == "1" ]] && set -x

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
export PATH="$HOME/.local/share/openclaw-web/tools/node_modules/.bin:$PATH"
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
release_manifest="$source_root/deploy/release-manifest.tsv"
hash_helper="$source_root/deploy/installer-hash-helper.sh"

profile_enabled=0
[[ "${OPENCLAW_INSTALL_PROFILE:-0}" == "1" ]] && profile_enabled=1
install_start=$SECONDS
profile_log=""
profile_phase_start=$SECONDS
profile_counter_dir=""
if [[ "$profile_enabled" == 1 ]]; then
  profile_counter_dir=$(mktemp -d "$HOME/.openclaw-installer-profile.XXXXXX")
  chmod 0700 "$profile_counter_dir"
fi

cleanup_profile_counters() {
  [[ -z "$profile_counter_dir" ]] || rm -rf -- "$profile_counter_dir"
}

profile_increment() {
  local name=$1 path current=0
  [[ -n "$profile_counter_dir" ]] || return 0
  path="$profile_counter_dir/$name"
  [[ ! -f "$path" ]] || current=$(<"$path")
  printf '%s\n' "$((current + 1))" > "$path"
}

profile_counter_value() {
  local name=$1 path="$profile_counter_dir/$1"
  if [[ -f "$path" ]]; then
    cat "$path"
  else
    printf '0\n'
  fi
}

trap cleanup_profile_counters EXIT

sha256sum() {
  profile_increment sha256sum
  command sha256sum "$@"
}

cut() {
  profile_increment cut
  command cut "$@"
}

stat() {
  profile_increment stat
  command stat "$@"
}

profile_phase() {
  local name=$1 now
  [[ "$profile_enabled" == 1 && -n "$profile_log" ]] || return 0
  now=$SECONDS
  printf 'phase\t%s\t%s\n' "$name" "$((now - profile_phase_start))" >> "$profile_log"
  profile_phase_start=$now
}

workflow_sources=()
workflow_destinations=()
workflow_modes=()
schema_sources=()
schema_destinations=()
schema_modes=()
unit_sources=()
unit_destinations=()
unit_modes=()
plugin_sources=()
plugin_destinations=()
plugin_modes=()
dashboard_sources=()
dashboard_destinations=()
dashboard_modes=()
bundle_sources=()
service_unit=""
dashboard_service_unit=""
timer_unit=""
plugin_index_source=""
plugin_index_destination=""

valid_manifest_relative() {
  local value=$1
  [[ -n "$value" && "$value" != /* && "$value" != */ && "/$value/" != *"/../"* && "$value" != ".." ]]
}

[[ -s "$release_manifest" ]] || {
  echo "release manifest is missing or empty: deploy/release-manifest.tsv" >&2
  exit 2
}

declare -A seen_manifest_sources=()
declare -A seen_manifest_destinations=()
manifest_rows=0
while IFS=$'\t' read -r kind source destination mode extra; do
  [[ -z "$kind" || "$kind" == \#* ]] && continue
  [[ -z "${extra:-}" ]] || {
    echo "release manifest row has too many fields" >&2
    exit 2
  }
  case "$kind" in
    workflow|schema|unit|dashboard|plugin) ;;
    *) echo "release manifest has an unknown asset kind: $kind" >&2; exit 2 ;;
  esac
  valid_manifest_relative "$source" && valid_manifest_relative "$destination" || {
    echo "release manifest contains an unsafe path" >&2
    exit 2
  }
  [[ "$mode" =~ ^[0-7]{4}$ ]] || {
    echo "release manifest contains an invalid mode" >&2
    exit 2
  }
  source_key="$kind:$source"
  destination_key="$kind:$destination"
  [[ -z "${seen_manifest_sources[$source_key]+x}" ]] || {
    echo "release manifest repeats a source: $source" >&2
    exit 2
  }
  [[ -z "${seen_manifest_destinations[$destination_key]+x}" ]] || {
    echo "release manifest repeats a destination: $destination" >&2
    exit 2
  }
  [[ -s "$source_root/$source" ]] || {
    echo "required release asset is missing or empty: $source" >&2
    exit 2
  }
  seen_manifest_sources[$source_key]=1
  seen_manifest_destinations[$destination_key]=1
  bundle_sources+=("$source")
  manifest_rows=$((manifest_rows + 1))
  case "$kind" in
    workflow)
      workflow_sources+=("$source")
      workflow_destinations+=("$destination")
      workflow_modes+=("$mode")
      ;;
    schema)
      schema_sources+=("$source")
      schema_destinations+=("$destination")
      schema_modes+=("$mode")
      ;;
    unit)
      unit_sources+=("$source")
      unit_destinations+=("$destination")
      unit_modes+=("$mode")
      case "$destination" in
        *.service)
          if [[ -z "$service_unit" ]]; then
            service_unit="$destination"
          elif [[ -z "$dashboard_service_unit" ]]; then
            dashboard_service_unit="$destination"
          else
            echo "release manifest defines too many service units" >&2
            exit 2
          fi
          ;;
        *.timer)
          [[ -z "$timer_unit" ]] || { echo "release manifest defines multiple timer units" >&2; exit 2; }
          timer_unit="$destination"
          ;;
        *) echo "release manifest unit must end in .service or .timer" >&2; exit 2 ;;
      esac
      ;;
    dashboard)
      dashboard_sources+=("$source")
      dashboard_destinations+=("$destination")
      dashboard_modes+=("$mode")
      ;;
    plugin)
      plugin_sources+=("$source")
      plugin_destinations+=("$destination")
      plugin_modes+=("$mode")
      [[ "$destination" != "index.js" || -z "$plugin_index_source" ]] || {
        echo "release manifest defines multiple plugin entrypoints" >&2
        exit 2
      }
      if [[ "$destination" == "index.js" ]]; then
        plugin_index_source="$source"
        plugin_index_destination="$destination"
      fi
      ;;
  esac
done < "$release_manifest"

[[ "$manifest_rows" -gt 0 && "${#workflow_sources[@]}" -gt 0 && "${#schema_sources[@]}" -gt 0 &&
   "${#unit_sources[@]}" -gt 0 && "${#plugin_sources[@]}" -gt 0 &&
   "${#dashboard_sources[@]}" -gt 0 ]] || {
  echo "release manifest does not define every required asset group" >&2
  exit 2
}
[[ -n "$service_unit" && -n "$dashboard_service_unit" && -n "$timer_unit" &&
   -n "$plugin_index_source" ]] || {
  echo "release manifest is missing a service, dashboard service, timer, or plugin entrypoint" >&2
  exit 2
}
[[ -s "$hash_helper" ]] || {
  echo "installer hash helper is missing or empty" >&2
  exit 2
}
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
  local verify_dir index source destination
  local -a verify_units=()
  verify_dir=$(mktemp -d)
  for index in "${!unit_sources[@]}"; do
    source="${unit_sources[$index]}"
    destination="${unit_destinations[$index]}"
    mkdir -p "$(dirname "$verify_dir/$destination")"
    case "$destination" in
      *.service)
        sed \
          -e '/^EnvironmentFile=/d' \
          -e 's#^WorkingDirectory=.*$#WorkingDirectory=/tmp#' \
          -e 's#^ExecStart=.*$#ExecStart=/bin/true#' \
          "$source_root/$source" \
          > "$verify_dir/$destination"
        ;;
      "$timer_unit") cp "$source_root/$source" "$verify_dir/$destination" ;;
    esac
    verify_units+=("$verify_dir/$destination")
  done
  if ! systemd-analyze --user verify "${verify_units[@]}"; then
    rm -rf -- "$verify_dir"
    return 1
  fi
  rm -rf -- "$verify_dir"
}
verify_staged_units
node --check "$source_root/$plugin_index_source"
openclaw config validate >/dev/null

wheel_relative=${wheel#"$source_root/"}
# Keep the helper output under HOME so the Windows-backed harness can resolve
# the same absolute path from its Python process. The remote target also owns
# HOME, so this does not require the source bundle to be writable.
hash_output=$(mktemp "$HOME/.openclaw-installer-hash.XXXXXX")
hash_helper_processes=0
cleanup_hash_output() {
  rm -f -- "$hash_output"
}
trap 'cleanup_hash_output; cleanup_profile_counters' EXIT
bash "$hash_helper" "$source_root" "$hash_output" "$wheel_relative" "${bundle_sources[@]}"
hash_helper_processes=1
staged_asset_manifest=$(mktemp)
release_tmp=""
cleanup_staging() {
  cleanup_hash_output
  rm -f -- "$staged_asset_manifest"
  if [[ -n "$release_tmp" && -e "$release_tmp" ]]; then
    rm -rf -- "$release_tmp"
  fi
}
trap 'cleanup_staging; cleanup_profile_counters' EXIT
{
  IFS=$'\t' read -r hash_header
  [[ "$hash_header" == $'path\tsha256\tmode' ]] || {
    echo "installer hash helper returned a malformed header" >&2
    false
  }
  IFS=$'\t' read -r hashed_wheel wheel_sha wheel_mode
  [[ "$hashed_wheel" == "$wheel_relative" && "$wheel_sha" =~ ^[0-9a-f]{64}$ && "$wheel_mode" =~ ^[0-7]{4}$ ]] || {
    echo "installer hash helper returned a malformed wheel row" >&2
    false
  }
  printf 'wheel\t%s\t%s\n' "$hashed_wheel" "$wheel_sha"
  while IFS=$'\t' read -r source source_sha source_mode; do
    [[ -n "$source" && "$source_sha" =~ ^[0-9a-f]{64}$ && "$source_mode" =~ ^[0-7]{4}$ ]] || {
      echo "installer hash helper returned a malformed asset row" >&2
      false
    }
    printf 'asset\t%s\t%s\n' "$source" "$source_sha"
  done
} < "$hash_output" > "$staged_asset_manifest"
staged_asset_sha=$(sha256sum "$staged_asset_manifest" | cut -d' ' -f1)
profile_phase "bundle-hash"
release="$releases/$wheel_sha"
offline_readiness() {
  local python=$1
  "$python" - "$source_root" "${workflow_sources[@]}" "${schema_sources[@]}" <<'PY'
import json
import sys
from pathlib import Path

import yaml
from openclaw_web.health import _playwright_ready
from openclaw_web.runtime import run_daily_discovery  # noqa: F401

root = Path(sys.argv[1])
assets = [root / relative for relative in sys.argv[2:]]
yaml_assets = [path for path in assets if path.suffix in {".yaml", ".yml"}]
schema_assets = [path for path in assets if path.is_relative_to(root / "schemas")]
for path in yaml_assets:
    value = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict) or not value:
        raise SystemExit(f"invalid required YAML: {path.relative_to(root)}")
for path in schema_assets:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict) or not value:
        raise SystemExit(f"invalid required schema: {path.relative_to(root)}")
if not _playwright_ready():
    raise SystemExit("Playwright Chromium is not installed")
PY
  command -v lighthouse >/dev/null || { echo "lighthouse is required" >&2; return 2; }
}

mkdir -p "$releases"
release_is_usable() {
  local candidate=$1
  [[ -x "$candidate/venv/bin/openclaw-web" &&
     -s "$candidate/release-manifest" &&
     -d "$candidate/openclaw-web-plugin" &&
     -s "$candidate/dashboard/index.html" &&
     -s "$candidate/dashboard/app.js" &&
     -s "$candidate/dashboard/styles.css" ]] || return 1
  "$candidate/venv/bin/openclaw-web" --help >/dev/null 2>&1
}
if [[ -e "$release" ]] && ! release_is_usable "$release"; then
  if [[ -L "$current" && "$(readlink "$current")" == "$release" ]]; then
    echo "existing current release is incomplete" >&2
    exit 2
  fi
  # A failed install may have frozen its plugin directory before a later
  # validation error. It is safe to make only this inactive stale release
  # writable before removing it; active/current releases fail closed above.
  chmod -R u+w "$release" 2>/dev/null || true
  rm -rf -- "$release"
fi
if [[ ! -e "$release" ]]; then
  release_tmp="$release"
  mkdir -m 0755 "$release_tmp"
  python3 -m venv "$release_tmp/venv"
  "$release_tmp/venv/bin/python" -m pip install --disable-pip-version-check "$wheel" >/dev/null
  "$release_tmp/venv/bin/python" -m pip check >/dev/null
  mkdir -m 0755 "$release_tmp/openclaw-web-plugin"
  for index in "${!plugin_sources[@]}"; do
    source="${plugin_sources[$index]}"
    destination="${plugin_destinations[$index]}"
    mode="${plugin_modes[$index]}"
    mkdir -p "$(dirname "$release_tmp/openclaw-web-plugin/$destination")"
    install -m "$mode" "$source_root/$source" \
      "$release_tmp/openclaw-web-plugin/$destination"
  done
  for index in "${!dashboard_sources[@]}"; do
    source="${dashboard_sources[$index]}"
    destination="${dashboard_destinations[$index]}"
    mode="${dashboard_modes[$index]}"
    mkdir -p "$(dirname "$release_tmp/$destination")"
    install -m "$mode" "$source_root/$source" "$release_tmp/$destination"
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
  for index in "${!plugin_sources[@]}"; do
    source="${plugin_sources[$index]}"
    destination="${plugin_destinations[$index]}"
    release_asset="$release/openclaw-web-plugin/$destination"
    release_asset_sha=$(sha256sum "$release_asset" | cut -d' ' -f1)
    source_asset_sha=$(sha256sum "$source_root/$source" | cut -d' ' -f1)
    [[ -f "$release_asset" && ! -L "$release_asset" &&
        "$release_asset_sha" == "$source_asset_sha" ]] || {
      echo "immutable release plugin drift" >&2
      exit 2
    }
  done
  offline_readiness "$release/venv/bin/python"
fi
profile_phase "release-ready"
release_plugin="$release/openclaw-web-plugin"
node --check "$release_plugin/$plugin_index_destination"

mkdir -m 0700 -p "$backup_root"
chmod 0700 "$backup_root"
if [[ "$profile_enabled" == 1 ]]; then
  profile_log="$backup_root/install-profile.tsv"
  printf 'metric\tvalue\n' > "$profile_log"
  profile_phase "preflight"
fi
: > "$manifest"
: > "$installed_manifest"
: > "$immutable_manifest"
cp "$release/asset-manifest.tsv" "$bundle_manifest"
  for index in "${!plugin_sources[@]}"; do
    release_asset="$release_plugin/${plugin_destinations[$index]}"
    printf 'file\t%s\t%s\t%s\n' "${release_asset#"$HOME/"}" \
      "$(sha256sum "$release_asset" | cut -d' ' -f1)" "$(stat -c '%a' "$release_asset")" \
    >> "$immutable_manifest"
done
timer_enabled=$(systemctl --user is-enabled "$timer_unit" 2>/dev/null || true)
timer_active=$(systemctl --user is-active "$timer_unit" 2>/dev/null || true)
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

systemctl --user disable --now "$timer_unit" >/dev/null 2>&1 || true
systemctl --user stop "$service_unit" >/dev/null 2>&1 || true
systemctl --user stop "$dashboard_service_unit" >/dev/null 2>&1 || true
profile_phase "service-stopped"

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

for root in "${workflow_roots[@]}"; do
  for index in "${!workflow_sources[@]}"; do
    install_file "${workflow_modes[$index]}" "$source_root/${workflow_sources[$index]}" \
      "$root/${workflow_destinations[$index]}"
  done
  for index in "${!schema_sources[@]}"; do
    install_file "${schema_modes[$index]}" "$source_root/${schema_sources[$index]}" \
      "$root/${schema_destinations[$index]}"
  done
done
for index in "${!schema_sources[@]}"; do
  install_file "${schema_modes[$index]}" "$source_root/${schema_sources[$index]}" \
    "$schemas/${schema_destinations[$index]#schemas/generated/}"
done
for index in "${!dashboard_sources[@]}"; do
  destination="${dashboard_destinations[$index]}"
  release_asset="$release/$destination"
  [[ -f "$release_asset" && ! -L "$release_asset" ]] || {
    echo "immutable dashboard asset is missing" >&2
    false
  }
done
for index in "${!unit_sources[@]}"; do
  install_file "${unit_modes[$index]}" "$source_root/${unit_sources[$index]}" \
    "$units/${unit_destinations[$index]}"
done
profile_phase "backup-ready"
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
# Playwright's managed Chrome is not placed on PATH. Lighthouse also needs the
# browser sandbox flag disabled on this user-level host because the cached
# browser has no setuid sandbox helper. Keep both values explicit and
# non-secret so a future immutable release preserves the real Chromium path.
chrome_path=""
for candidate in "$HOME"/.cache/ms-playwright/*/chrome-linux64/chrome; do
  if [[ -f "$candidate" && -x "$candidate" ]]; then
    chrome_path="$candidate"
    break
  fi
done
chrome_no_sandbox=0
[[ -n "$chrome_path" ]] && chrome_no_sandbox=1
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
  "OPENCLAW_WEB_DASHBOARD_HOST=127.0.0.1" \
  "OPENCLAW_WEB_DASHBOARD_PORT=18080" \
  "OPENCLAW_WEB_DASHBOARD_ROOT=$current/dashboard" \
  "CHROME_PATH=$chrome_path" \
  "OPENCLAW_WEB_CHROME_NO_SANDBOX=$chrome_no_sandbox" \
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
  "$units/$service_unit" \
  "$units/$timer_unit"
[[ "$(systemctl --user is-enabled "$timer_unit" 2>/dev/null || true)" == "disabled" ]]
[[ "$(systemctl --user is-active "$timer_unit" 2>/dev/null || true)" == "inactive" ]]
"$current/venv/bin/openclaw-web" --help >/dev/null
openclaw plugins inspect openclaw-web-components --runtime --json >/dev/null
openclaw config validate >/dev/null
profile_phase "installed"

mutation_started=0
trap - ERR
if [[ "$profile_enabled" == 1 ]]; then
  profile_phase "complete"
  printf 'metric\t%s\n' "wall_seconds=$((SECONDS - install_start))" >> "$profile_log"
  printf 'metric\t%s\n' "sha256sum_calls=$(profile_counter_value sha256sum)" >> "$profile_log"
  printf 'metric\t%s\n' "cut_calls=$(profile_counter_value cut)" >> "$profile_log"
  printf 'metric\t%s\n' "stat_calls=$(profile_counter_value stat)" >> "$profile_log"
  printf 'metric\t%s\n' "hash_helper_processes=$hash_helper_processes" >> "$profile_log"
  printf 'metric\t%s\n' "hash_stat_pass_processes=$hash_helper_processes" >> "$profile_log"
  printf 'metric\t%s\n' "manifest_assets=${#bundle_sources[@]}" >> "$profile_log"
fi
rm -f "$staged_asset_manifest"
trap - EXIT
cleanup_profile_counters
printf 'release_sha=%s\nbackup_timestamp=%s\ntimer_enabled=false\ninstall_verified=offline\n' \
  "$wheel_sha" "$(basename "$backup_root")"
