#!/usr/bin/env bash

set -Eeuo pipefail

APP_NAME="Queyntisen"
COMMAND_NAME="queyntisen"
INSTALL_DIR="${QUEYNTISEN_INSTALL_DIR:-$HOME/.queyntisen}"
LAUNCHER_DIR="${QUEYNTISEN_BIN_DIR:-$HOME/.local/bin}"
LAUNCHER="$LAUNCHER_DIR/$COMMAND_NAME"
CONFIG_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/queyntisen"
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
PYTHON_BIN="${PYTHON:-}"
BACKUP_DIR=""
INSTALL_COMPLETE=0

log() {
  printf '%s\n' "$*"
}

section() {
  printf '\n==> %s\n' "$*"
}

warn() {
  printf 'Warning: %s\n' "$*" >&2
}

fail() {
  printf 'Error: %s\n' "$*" >&2
  exit 1
}

on_error() {
  local line="$1"
  if [ "$INSTALL_COMPLETE" -eq 0 ] && [ -n "$BACKUP_DIR" ] && [ -d "$BACKUP_DIR" ]; then
    warn "Restoring previous install from $BACKUP_DIR"
    rm -rf "$INSTALL_DIR"
    mv "$BACKUP_DIR" "$INSTALL_DIR"
  fi
  fail "Installation failed near line $line. No user notes or AI config were removed."
}

trap 'on_error "$LINENO"' ERR

usage() {
  cat <<EOF
$APP_NAME installer

Usage:
  ./install.sh [options]

Options:
  --install-dir PATH   Install application files to PATH
                       Default: $INSTALL_DIR
  --bin-dir PATH       Install the '$COMMAND_NAME' launcher to PATH
                       Default: $LAUNCHER_DIR
  --python PATH        Python 3 executable to use
  --no-path-edit       Do not edit shell startup files
  --uninstall          Remove installed application files and launcher
                       Keeps $CONFIG_DIR by default
  --purge-config       With --uninstall, also remove saved AI setup
  -h, --help           Show this help

Environment overrides:
  QUEYNTISEN_INSTALL_DIR
  QUEYNTISEN_BIN_DIR
  PYTHON
EOF
}

NO_PATH_EDIT=0
UNINSTALL=0
PURGE_CONFIG=0

while [ "$#" -gt 0 ]; do
  case "$1" in
    --install-dir)
      [ "${2:-}" ] || fail "--install-dir requires a path"
      INSTALL_DIR="$2"
      shift 2
      ;;
    --bin-dir)
      [ "${2:-}" ] || fail "--bin-dir requires a path"
      LAUNCHER_DIR="$2"
      LAUNCHER="$LAUNCHER_DIR/$COMMAND_NAME"
      shift 2
      ;;
    --python)
      [ "${2:-}" ] || fail "--python requires a path"
      PYTHON_BIN="$2"
      shift 2
      ;;
    --no-path-edit)
      NO_PATH_EDIT=1
      shift
      ;;
    --uninstall)
      UNINSTALL=1
      shift
      ;;
    --purge-config)
      PURGE_CONFIG=1
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      fail "Unknown option: $1"
      ;;
  esac
done

detect_os() {
  case "$(uname -s)" in
    Linux) printf 'Linux' ;;
    Darwin) printf 'macOS' ;;
    *) fail "$APP_NAME supports Linux and macOS. Detected: $(uname -s)" ;;
  esac
}

command_exists() {
  command -v "$1" >/dev/null 2>&1
}

find_python() {
  if [ -n "$PYTHON_BIN" ]; then
    [ -x "$PYTHON_BIN" ] || fail "Python executable is not runnable: $PYTHON_BIN"
    printf '%s' "$PYTHON_BIN"
    return
  fi

  if command_exists python3; then
    command -v python3
  elif command_exists python; then
    command -v python
  else
    fail "Python 3 was not found. Install Python 3.8 or newer, then run this installer again."
  fi
}

check_python() {
  local python="$1"
  "$python" - <<'PY'
import sys
if sys.version_info < (3, 8):
    raise SystemExit(1)
print(f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}")
PY
}

python_has_module() {
  local python="$1"
  local module="$2"
  "$python" - "$module" <<'PY' >/dev/null 2>&1
import importlib.util
import sys
raise SystemExit(0 if importlib.util.find_spec(sys.argv[1]) else 1)
PY
}

shell_rc_files() {
  local files=()
  local shell_name
  shell_name="$(basename "${SHELL:-}")"

  case "$shell_name" in
    zsh)
      files+=("$HOME/.zshrc")
      ;;
    bash)
      files+=("$HOME/.bashrc")
      if [ "$(detect_os)" = "macOS" ]; then
        files+=("$HOME/.bash_profile")
      fi
      ;;
    fish)
      files+=("$HOME/.config/fish/config.fish")
      ;;
    *)
      files+=("$HOME/.profile")
      ;;
  esac

  printf '%s\n' "${files[@]}"
}

path_contains_launcher_dir() {
  case ":$PATH:" in
    *":$LAUNCHER_DIR:"*) return 0 ;;
    *) return 1 ;;
  esac
}

add_path_line() {
  local rc_file="$1"
  mkdir -p "$(dirname "$rc_file")"
  touch "$rc_file"

  if grep -Fqs "$LAUNCHER_DIR" "$rc_file"; then
    log "PATH entry already exists in $rc_file"
    return
  fi

  if [ "$(basename "$rc_file")" = "config.fish" ]; then
    {
      printf '\n# Queyntisen\n'
      printf 'fish_add_path "%s"\n' "$LAUNCHER_DIR"
    } >> "$rc_file"
  else
    {
      printf '\n# Queyntisen\n'
      printf 'export PATH="%s:$PATH"\n' "$LAUNCHER_DIR"
    } >> "$rc_file"
  fi
  log "Added $LAUNCHER_DIR to PATH in $rc_file"
}

uninstall() {
  section "Uninstalling $APP_NAME"
  if [ -e "$LAUNCHER" ]; then
    rm -f "$LAUNCHER"
    log "Removed launcher: $LAUNCHER"
  else
    log "Launcher not found: $LAUNCHER"
  fi

  if [ -d "$INSTALL_DIR" ]; then
    rm -rf "$INSTALL_DIR"
    log "Removed application directory: $INSTALL_DIR"
  else
    log "Application directory not found: $INSTALL_DIR"
  fi

  local backup
  for backup in "$INSTALL_DIR".backup.*; do
    [ -e "$backup" ] || continue
    rm -rf "$backup"
    log "Removed old install backup: $backup"
  done

  if [ "$PURGE_CONFIG" -eq 1 ]; then
    rm -rf "$CONFIG_DIR"
    log "Removed saved AI setup: $CONFIG_DIR"
  else
    log "Kept saved AI setup: $CONFIG_DIR"
  fi

  log ""
  log "Uninstall complete."
}

preflight() {
  section "Checking system"
  local os_name="$1"
  local python="$2"
  log "Operating system: $os_name"
  log "Project directory: $SCRIPT_DIR"
  log "Install directory: $INSTALL_DIR"
  log "Launcher path: $LAUNCHER"
  log "Config directory: $CONFIG_DIR"

  [ -f "$SCRIPT_DIR/editor.py" ] || fail "editor.py was not found in $SCRIPT_DIR"
  [ -f "$SCRIPT_DIR/requirements.txt" ] || fail "requirements.txt was not found in $SCRIPT_DIR"

  local version
  if ! version="$(check_python "$python")"; then
    fail "Python 3.8 or newer is required. Found: $("$python" --version 2>&1 || true)"
  fi
  log "Python: $python ($version)"

  if ! python_has_module "$python" venv; then
    if [ "$os_name" = "Linux" ]; then
      fail "Python venv support is missing. Install the python3-venv package for your distribution, then rerun this installer."
    fi
    fail "Python venv support is missing. Install a full Python 3 distribution, then rerun this installer."
  fi

  if ! python_has_module "$python" ensurepip; then
    if [ "$os_name" = "Linux" ]; then
      fail "Python ensurepip is missing. Install python3-venv for your distribution, then rerun this installer."
    fi
    fail "Python ensurepip is missing. Install a full Python 3 distribution, then rerun this installer."
  fi
}

backup_existing_install() {
  if [ -d "$INSTALL_DIR" ]; then
    BACKUP_DIR="${INSTALL_DIR}.backup.$(date +%Y%m%d%H%M%S)"
    section "Backing up existing install"
    mv "$INSTALL_DIR" "$BACKUP_DIR"
    log "Existing install moved to: $BACKUP_DIR"
  fi
}

copy_application_files() {
  section "Copying application files"
  mkdir -p "$INSTALL_DIR"
  cp "$SCRIPT_DIR/editor.py" "$INSTALL_DIR/editor.py"
  cp "$SCRIPT_DIR/requirements.txt" "$INSTALL_DIR/requirements.txt"
  [ -f "$SCRIPT_DIR/LICENSE" ] && cp "$SCRIPT_DIR/LICENSE" "$INSTALL_DIR/LICENSE"
  [ -f "$SCRIPT_DIR/README.md" ] && cp "$SCRIPT_DIR/README.md" "$INSTALL_DIR/README.md"
  log "Application files copied."
}

create_virtualenv() {
  local python="$1"
  section "Creating isolated Python environment"
  "$python" -m venv "$INSTALL_DIR/venv"
  "$INSTALL_DIR/venv/bin/python" -m pip install --upgrade pip setuptools wheel
  "$INSTALL_DIR/venv/bin/python" -m pip install -r "$INSTALL_DIR/requirements.txt"
  "$INSTALL_DIR/venv/bin/python" -m py_compile "$INSTALL_DIR/editor.py"
  log "Virtual environment is ready."
}

create_launcher() {
  section "Creating launcher"
  mkdir -p "$LAUNCHER_DIR"
  cat > "$LAUNCHER" <<EOF
#!/usr/bin/env bash
set -Eeuo pipefail
exec "$INSTALL_DIR/venv/bin/python" "$INSTALL_DIR/editor.py" "\$@"
EOF
  chmod 755 "$LAUNCHER"
  log "Launcher installed: $LAUNCHER"
}

configure_path() {
  section "Checking PATH"
  if path_contains_launcher_dir; then
    log "$LAUNCHER_DIR is already in PATH."
    return
  fi

  if [ "$NO_PATH_EDIT" -eq 1 ]; then
    warn "$LAUNCHER_DIR is not in PATH and --no-path-edit was used."
    return
  fi

  local updated=0
  while IFS= read -r rc_file; do
    [ -n "$rc_file" ] || continue
    add_path_line "$rc_file"
    updated=1
    break
  done < <(shell_rc_files)

  if [ "$updated" -eq 0 ]; then
    warn "Could not find a shell startup file to update."
  fi
}

post_install() {
  section "Installation complete"
  log "Run Queyntisen with:"
  log "  $COMMAND_NAME notes.md"
  log ""
  log "If your shell cannot find '$COMMAND_NAME' yet, run:"
  log "  export PATH=\"$LAUNCHER_DIR:\$PATH\""
  log ""
  log "Then try:"
  log "  $LAUNCHER notes.md"
  log ""
  log "AI setup is configured inside the editor with:"
  log "  :setup"
  log ""
  log "Saved AI setup will live in:"
  log "  $CONFIG_DIR"

  if [ -n "$BACKUP_DIR" ]; then
    log ""
    log "Previous install backup:"
    log "  $BACKUP_DIR"
    log "Remove it after confirming the new install works."
  fi
}

main() {
  if [ "$UNINSTALL" -eq 1 ]; then
    uninstall
    exit 0
  fi

  section "$APP_NAME installer"
  log "This installer supports Linux and macOS."
  log "It installs the app into your home directory and does not require sudo."

  local os_name
  local python
  os_name="$(detect_os)"
  python="$(find_python)"

  preflight "$os_name" "$python"
  backup_existing_install
  copy_application_files
  create_virtualenv "$python"
  create_launcher
  configure_path
  INSTALL_COMPLETE=1
  post_install
}

main "$@"
