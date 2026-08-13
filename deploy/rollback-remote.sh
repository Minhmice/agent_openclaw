#!/usr/bin/env bash
set -euo pipefail
umask 077

if [[ $# -ne 1 ]]; then
  echo "usage: rollback-remote.sh <backup-root>" >&2
  exit 2
fi

backup_root=$(realpath "$1")
home=$(realpath "$HOME")
case "$backup_root" in "$home/.openclaw/backups"/*) ;; *) echo "backup root must be under ~/.openclaw/backups" >&2; exit 2;; esac
manifest="$backup_root/manifest.tsv"
installed_manifest="$backup_root/installed.tsv"
immutable_manifest="$backup_root/immutable.tsv"
previous_current="$backup_root/previous-current"
[[ -f "$manifest" ]] || { echo "backup manifest is missing" >&2; exit 2; }
[[ -f "$installed_manifest" ]] || { echo "installed manifest is missing" >&2; exit 2; }
[[ -f "$immutable_manifest" ]] || { echo "immutable manifest is missing" >&2; exit 2; }
[[ -f "$backup_root/timer-enabled" && -f "$backup_root/timer-active" ]] || {
  echo "timer state backup is missing" >&2
  exit 2
}

valid_relative() {
  local relative=$1
  [[ -n "$relative" && "$relative" != /* && "/$relative/" != *"/../"* && "$relative" != ".." ]]
}

saved_enabled=$(<"$backup_root/timer-enabled")
saved_active=$(<"$backup_root/timer-active")
case "$saved_enabled" in
  enabled|enabled-runtime|disabled|not-found) ;;
  *) echo "unsupported saved timer state" >&2; exit 2;;
esac
case "$saved_active" in
  active|inactive) ;;
  *) echo "unsupported saved timer state" >&2; exit 2;;
esac

remove_target() {
  local target=$1
  if [[ -d "$target" && ! -L "$target" ]]; then
    rm -rf -- "$target"
  else
    rm -f -- "$target"
  fi
}

# Validate every backup payload before touching the timer or any installed
# target. A corrupt later manifest entry must not leave a partial rollback.
while IFS=$'\t' read -r disposition relative metadata mode; do
  valid_relative "$relative" || { echo "invalid manifest target" >&2; exit 2; }
  case "$disposition" in
    absent)
      [[ "$metadata" == "-" && "$mode" == "-" ]] || {
        echo "invalid absent manifest entry" >&2
        exit 2
      }
      ;;
    file)
      source="$backup_root/files/$relative"
      [[ -f "$source" && ! -L "$source" ]] || { echo "backup file is missing" >&2; exit 2; }
      [[ "$(sha256sum "$source" | cut -d' ' -f1)" == "$metadata" ]] || {
        echo "backup file hash mismatch" >&2
        exit 2
      }
      [[ "$(stat -c '%a' "$source")" == "$mode" ]] || {
        echo "backup file mode mismatch" >&2
        exit 2
      }
      ;;
    symlink)
      [[ -n "$metadata" && "$mode" == "-" ]] || {
        echo "invalid backup symlink entry" >&2
        exit 2
      }
      ;;
    directory)
      archive="$backup_root/trees/$relative.tar"
      [[ -f "$archive" && ! -L "$archive" ]] || { echo "backup tree is missing" >&2; exit 2; }
      [[ "$(sha256sum "$archive" | cut -d' ' -f1)" == "$metadata" ]] || {
        echo "backup tree hash mismatch" >&2
        exit 2
      }
      tar -tf "$archive" >/dev/null || { echo "backup tree is invalid" >&2; exit 2; }
      archive_root=$(basename "$relative")
      while IFS= read -r member; do
        [[ "$member" == "$archive_root" || "$member" == "$archive_root/" ||
            "$member" == "$archive_root/"* ]] || {
          echo "backup tree contains an unexpected path" >&2
          exit 2
        }
        [[ "/$member/" != *"/../"* ]] || {
          echo "backup tree contains an unsafe path" >&2
          exit 2
        }
      done < <(tar -tf "$archive")
      ;;
    *) echo "invalid manifest disposition" >&2; exit 2;;
  esac
done < "$manifest"

# Refuse before mutation if any deployed file or pointer was changed by an
# operator. This protects edits made after installation.
while IFS=$'\t' read -r kind relative metadata mode; do
  valid_relative "$relative" || { echo "invalid installed manifest target" >&2; exit 2; }
  target="$HOME/$relative"
  case "$kind" in
    file)
      [[ -f "$target" && ! -L "$target" ]] || { echo "deployment drift detected" >&2; exit 1; }
      [[ "$(sha256sum "$target" | cut -d' ' -f1)" == "$metadata" ]] || {
        echo "deployment drift detected" >&2
        exit 1
      }
      [[ "$(stat -c '%a' "$target")" == "$mode" ]] || {
        echo "deployment drift detected" >&2
        exit 1
      }
      ;;
    symlink)
      [[ -L "$target" && "$(readlink "$target")" == "$metadata" ]] || {
        echo "deployment drift detected" >&2
        exit 1
      }
      ;;
    *) echo "invalid installed manifest disposition" >&2; exit 2;;
  esac
done < "$installed_manifest"

while IFS=$'\t' read -r kind relative metadata mode; do
  valid_relative "$relative" || { echo "invalid immutable manifest target" >&2; exit 2; }
  target="$HOME/$relative"
  [[ "$kind" == "file" && -f "$target" && ! -L "$target" &&
      "$(sha256sum "$target" | cut -d' ' -f1)" == "$metadata" &&
      "$(stat -c '%a' "$target")" == "$mode" ]] || {
    echo "deployment drift detected" >&2
    exit 1
  }
done < "$immutable_manifest"

current_enabled=$(systemctl --user is-enabled openclaw-web-discovery.timer 2>/dev/null || true)
current_active=$(systemctl --user is-active openclaw-web-discovery.timer 2>/dev/null || true)
[[ "$current_enabled" == "disabled" && "$current_active" == "inactive" ]] || {
  echo "deployment drift detected" >&2
  exit 1
}

systemctl --user disable --now openclaw-web-discovery.timer >/dev/null 2>&1 || true
systemctl --user stop openclaw-web-discovery.service >/dev/null 2>&1 || true

while IFS=$'\t' read -r disposition relative metadata mode; do
  valid_relative "$relative" || { echo "invalid manifest target" >&2; exit 2; }
  target="$HOME/$relative"
  case "$disposition" in
    absent)
      [[ ! -e "$target" && ! -L "$target" ]] || remove_target "$target"
      ;;
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
    *) echo "invalid manifest disposition" >&2; exit 2;;
  esac
done < "$manifest"

# The separately recorded value makes current-pointer restoration auditable.
current="$HOME/.local/share/openclaw-web/current"
if [[ -s "$previous_current" && -L "$current" ]]; then
  [[ "$(readlink "$current")" == "$(<"$previous_current")" ]] || {
    echo "restored current pointer does not match backup" >&2
    exit 1
  }
fi

systemctl --user daemon-reload
service="$HOME/.config/systemd/user/openclaw-web-discovery.service"
timer="$HOME/.config/systemd/user/openclaw-web-discovery.timer"
if [[ -f "$service" || -f "$timer" ]]; then
  [[ -f "$service" && -f "$timer" ]] || { echo "restored unit set is incomplete" >&2; exit 1; }
  systemd-analyze --user verify "$service" "$timer"
fi
openclaw config validate >/dev/null
openclaw health >/dev/null
openclaw channels status --channel discord --probe >/dev/null

# Restore the exact prior enablement/activity only after all restore checks.
case "$saved_enabled" in
  enabled) systemctl --user enable openclaw-web-discovery.timer >/dev/null ;;
  enabled-runtime) systemctl --user enable --runtime openclaw-web-discovery.timer >/dev/null ;;
  disabled|not-found) ;;
esac
if [[ "$saved_active" == "active" ]]; then
  systemctl --user start openclaw-web-discovery.timer
fi
restored_enabled=$(systemctl --user is-enabled openclaw-web-discovery.timer 2>/dev/null || true)
restored_active=$(systemctl --user is-active openclaw-web-discovery.timer 2>/dev/null || true)
[[ "$restored_enabled" == "$saved_enabled" && "$restored_active" == "$saved_active" ]] || {
  echo "restored timer state does not match backup" >&2
  exit 1
}
printf 'rollback_timestamp=%s\n' "$(basename "$backup_root")"
