#!/bin/bash
# Preflight environment checks for the deploy scripts.
#
# The deploy scripts (build-and-deploy-backend.sh, build-and-deploy-frontend.sh)
# used to fail deep inside Step 1 with a cryptic 'npx: command not found' when
# Node was missing. This helper runs the same checks up front and, on failure,
# offers a confirmation-gated auto-installer so the fix is one keypress away.
#
# Behaviour of preflight_check_node:
#   1. ALWAYS prints the detected environment (glibc version, node/npm/npx
#      locations, node --version). Users see what was checked either way.
#   2. If everything meets the requirement (Node 18+): prints "Status: OK"
#      and returns 0.
#   3. If Node is missing or too old:
#      - Prompts "Install Node 20 now? [y/N]:" on an interactive shell.
#      - Or auto-approves when PREFLIGHT_AUTO_INSTALL=1 is set (useful for
#        non-interactive CI-like usage).
#      - Or falls back to printing copy-paste install commands and exits 1
#        when neither applies (non-interactive with no opt-in env var).
#   4. If the user confirms, runs the install path auto-selected by glibc:
#         glibc >= 2.28  ->  nvm install of Node 20
#         glibc  < 2.28  ->  unofficial glibc-2.17 Node 20 build
#      Updates PATH for the current shell so the deploy can continue in the
#      same run. Persists the PATH change to the appropriate rcfile
#      (~/.bashrc or ~/.zshrc, detected from $SHELL).
#
# Usage from a deploy script:
#   . "${SCRIPT_DIR}/preflight.sh"
#   preflight_check_node

# ---------------------------------------------------------------------------
# glibc detection
# ---------------------------------------------------------------------------

# Print the host's glibc version as "MAJOR.MINOR" (e.g. "2.26"). Empty when
# ldd is unavailable (e.g. macOS, minimal containers) — the caller treats
# empty as "unknown, assume modern".
_preflight_glibc_version() {
  # Best-effort glibc version detection. Never fails; prints empty on
  # unknown (non-glibc host like macOS, or ldd unavailable).
  #
  # Uses bash parameter expansion + regex instead of `head | grep | head`
  # because the caller runs under `set -euo pipefail`: with pipefail, the
  # first `head -1` closes the pipe → `ldd` gets SIGPIPE → exit 141 → the
  # pipeline returns non-zero → `set -e` on the caller aborts the script
  # with no error message.
  command -v ldd >/dev/null 2>&1 || return 0
  local out first
  out=$(ldd --version 2>&1) || return 0
  first="${out%%$'\n'*}"
  if [[ "$first" =~ ([0-9]+\.[0-9]+) ]]; then
    echo "${BASH_REMATCH[1]}"
  fi
  return 0
}

# Node 18/20 official binaries need glibc >= 2.28. Returns 0 (true) when the
# host is new enough (or when the version is unknown, so we don't over-refuse
# on macOS or non-glibc systems), 1 (false) when we can prove it's too old.
_preflight_glibc_supports_modern_node() {
  local ver major minor
  ver=$(_preflight_glibc_version)
  [ -z "$ver" ] && return 0
  major="${ver%.*}"
  minor="${ver#*.}"
  if [ "$major" -gt 2 ]; then
    return 0
  fi
  if [ "$major" -eq 2 ] && [ "$minor" -ge 28 ]; then
    return 0
  fi
  return 1
}

# ---------------------------------------------------------------------------
# Shell rcfile detection (where auto-install persists PATH)
# ---------------------------------------------------------------------------

_preflight_shell_rcfile() {
  local shell_name
  shell_name=$(basename "${SHELL:-/bin/bash}")
  case "$shell_name" in
    zsh)  echo "$HOME/.zshrc" ;;
    *)    echo "$HOME/.bashrc" ;;
  esac
}

# ---------------------------------------------------------------------------
# Manual install instructions (fallback when user declines auto-install or
# non-interactive with no opt-in)
# ---------------------------------------------------------------------------

_preflight_install_instructions() {
  echo ""
  echo "Manual install (copy-paste one of the blocks below):"
  if _preflight_glibc_supports_modern_node; then
    cat <<'EOF'

  # nvm install of Node 20:
  curl -o- https://raw.githubusercontent.com/nvm-sh/nvm/v0.39.7/install.sh | bash
  export NVM_DIR="$HOME/.nvm" && . "$NVM_DIR/nvm.sh"
  nvm install 20
  nvm use 20

  # If you use zsh, also add:
  echo '. "$HOME/.nvm/nvm.sh"' >> ~/.zshrc
EOF
  else
    cat <<'EOF'

  # Unofficial glibc-2.17 Node 20 build (for Amazon Linux 2 / older glibc):
  NODE_VER=v20.18.0
  mkdir -p ~/node-glibc217 && cd ~/node-glibc217
  curl -LO "https://unofficial-builds.nodejs.org/download/release/${NODE_VER}/node-${NODE_VER}-linux-x64-glibc-217.tar.xz"
  tar xf "node-${NODE_VER}-linux-x64-glibc-217.tar.xz"
  export PATH="$HOME/node-glibc217/node-${NODE_VER}-linux-x64-glibc-217/bin:$PATH"

  # Persist for future shells (pick whichever your login shell uses):
  echo 'export PATH="$HOME/node-glibc217/node-v20.18.0-linux-x64-glibc-217/bin:$PATH"' >> ~/.bashrc
  echo 'export PATH="$HOME/node-glibc217/node-v20.18.0-linux-x64-glibc-217/bin:$PATH"' >> ~/.zshrc
EOF
  fi
  echo ""
  echo "Then re-run the deploy script."
  echo ""
}

# ---------------------------------------------------------------------------
# Auto-install (confirmation-gated in preflight_check_node)
# ---------------------------------------------------------------------------

# Verify the tools the auto-installer itself needs (curl always; tar for the
# glibc-2.17 path). Returns non-zero with an actionable message if missing.
_preflight_check_installer_prereqs() {
  local missing=()
  command -v curl >/dev/null 2>&1 || missing+=("curl")
  if ! _preflight_glibc_supports_modern_node; then
    command -v tar >/dev/null 2>&1 || missing+=("tar")
  fi
  if [ "${#missing[@]}" -gt 0 ]; then
    echo "  Cannot auto-install Node — required tool(s) missing: ${missing[*]}"
    echo "  Install these first via your system package manager (yum/apt/brew)."
    return 1
  fi
}

_preflight_install_nvm_node() {
  echo "  Installing nvm (v0.39.7)..."
  if ! curl -o- https://raw.githubusercontent.com/nvm-sh/nvm/v0.39.7/install.sh 2>/dev/null | bash > /tmp/nvm-install.log 2>&1; then
    echo "  nvm installer failed. Log:"
    tail -20 /tmp/nvm-install.log || true
    return 1
  fi
  export NVM_DIR="$HOME/.nvm"
  # shellcheck disable=SC1091
  . "$NVM_DIR/nvm.sh"

  echo "  Installing Node 20 via nvm (this takes ~30-60s)..."
  if ! nvm install 20 > /tmp/nvm-node-install.log 2>&1; then
    echo "  'nvm install 20' failed. Log:"
    tail -20 /tmp/nvm-node-install.log || true
    return 1
  fi
  nvm use 20 > /dev/null 2>&1 || true

  # nvm's installer already appends sourcing to ~/.bashrc. If the user's
  # login shell is zsh, add the same to ~/.zshrc.
  local rcfile
  rcfile=$(_preflight_shell_rcfile)
  if [ "$rcfile" = "$HOME/.zshrc" ] && ! grep -q 'NVM_DIR' "$rcfile" 2>/dev/null; then
    {
      echo ''
      echo '# Added by bedrock-activity-dashboard preflight'
      echo 'export NVM_DIR="$HOME/.nvm"'
      echo '[ -s "$NVM_DIR/nvm.sh" ] && \. "$NVM_DIR/nvm.sh"'
    } >> "$rcfile"
    echo "  Persisted nvm sourcing to $rcfile"
  fi
}

_preflight_install_glibc217_node() {
  local node_ver="v20.18.0"
  local tarball="node-${node_ver}-linux-x64-glibc-217.tar.xz"
  local install_root="$HOME/node-glibc217"
  local node_bin_dir="${install_root}/node-${node_ver}-linux-x64-glibc-217/bin"

  echo "  Downloading Node ${node_ver} unofficial glibc-2.17 build (~25 MB)..."
  mkdir -p "$install_root"
  if ! ( cd "$install_root" && curl -LOsSf "https://unofficial-builds.nodejs.org/download/release/${node_ver}/${tarball}" ); then
    echo "  Download failed. Check network connectivity to unofficial-builds.nodejs.org."
    return 1
  fi
  echo "  Extracting..."
  if ! ( cd "$install_root" && tar xf "$tarball" ); then
    echo "  Extract failed."
    return 1
  fi

  # Update this shell so the deploy can continue in the same run.
  export PATH="${node_bin_dir}:$PATH"

  # Persist to the appropriate rcfile.
  local rcfile path_line
  rcfile=$(_preflight_shell_rcfile)
  path_line="export PATH=\"${node_bin_dir}:\$PATH\""
  if ! grep -qF "$path_line" "$rcfile" 2>/dev/null; then
    {
      echo ''
      echo '# Added by bedrock-activity-dashboard preflight'
      echo "$path_line"
    } >> "$rcfile"
    echo "  Persisted PATH to $rcfile"
  fi
}

_preflight_install_node() {
  _preflight_check_installer_prereqs || return 1
  if _preflight_glibc_supports_modern_node; then
    _preflight_install_nvm_node
  else
    _preflight_install_glibc217_node
  fi
}

# ---------------------------------------------------------------------------
# Public: preflight_check_node
# ---------------------------------------------------------------------------

preflight_check_node() {
  echo ""
  echo "=== Preflight: Node.js environment ==="

  # Always print the detected state — users see what was checked either way.
  local glibc_ver
  glibc_ver=$(_preflight_glibc_version)
  echo "  glibc:            ${glibc_ver:-unknown (non-glibc host, e.g. macOS)}"

  local missing=()
  local cmd path
  for cmd in node npm npx; do
    if path=$(command -v "$cmd" 2>/dev/null); then
      printf "  %-16s %s\n" "${cmd}:" "$path"
    else
      missing+=("$cmd")
      printf "  %-16s %s\n" "${cmd}:" "NOT FOUND"
    fi
  done

  local node_ver="" major=""
  if [ "${#missing[@]}" -eq 0 ]; then
    # `|| node_ver=""` protects the assignment from `set -e` if the
    # pipeline exits non-zero for any reason (paranoia — same pattern
    # bit us in _preflight_glibc_version, so guard defensively here too).
    node_ver=$(node --version 2>/dev/null | sed 's/^v//') || node_ver=""
    major="${node_ver%%.*}"
    echo "  node --version:  v${node_ver}"
  fi

  # OK path
  if [ "${#missing[@]}" -eq 0 ] \
      && [ -n "$major" ] \
      && [[ "$major" =~ ^[0-9]+$ ]] \
      && [ "$major" -ge 18 ]; then
    echo "  Status:          OK — Node ${major} meets the 18+ requirement."
    echo ""
    return 0
  fi

  # Failure path — describe what's wrong.
  echo ""
  if [ "${#missing[@]}" -gt 0 ]; then
    echo "  Status:          FAILED — Node.js is not installed (missing: ${missing[*]})."
  else
    echo "  Status:          FAILED — Node version too old (need 18+, have v${node_ver})."
  fi

  # Decide whether to offer / run auto-install.
  local auto_env="${PREFLIGHT_AUTO_INSTALL:-}"
  local should_install=0
  echo ""
  if [ "$auto_env" = "1" ] || [ "$auto_env" = "yes" ]; then
    echo "  PREFLIGHT_AUTO_INSTALL=${auto_env} set — proceeding with install without prompt."
    should_install=1
  elif [ -t 0 ]; then
    if _preflight_glibc_supports_modern_node; then
      echo "  Proposed install:"
      echo "    1. Install nvm v0.39.7 to ~/.nvm/"
      echo "    2. nvm install 20 && nvm use 20"
      echo "    3. Append nvm sourcing to $(_preflight_shell_rcfile) if not already present"
    else
      echo "  Proposed install:"
      echo "    1. Download unofficial glibc-2.17 Node 20 build (~25 MB)"
      echo "    2. Extract to ~/node-glibc217/"
      echo "    3. Append PATH to $(_preflight_shell_rcfile)"
    fi
    echo ""
    local reply=""
    read -r -p "  Install Node 20 now? [y/N]: " reply || reply=""
    case "$reply" in
      y|Y|yes|YES) should_install=1 ;;
      *)           should_install=0 ;;
    esac
  else
    echo "  Non-interactive shell and PREFLIGHT_AUTO_INSTALL not set — skipping auto-install."
  fi

  if [ "$should_install" != "1" ]; then
    _preflight_install_instructions
    return 1
  fi

  echo ""
  echo "--- Installing Node.js ---"
  if ! _preflight_install_node; then
    echo ""
    echo "Auto-install failed. Falling back to manual instructions:"
    _preflight_install_instructions
    return 1
  fi

  # Re-check post-install so we don't hand a broken environment to the deploy.
  local new_ver new_major
  new_ver=$(node --version 2>/dev/null | sed 's/^v//') || new_ver=""
  new_major="${new_ver%%.*}"
  if [ -z "$new_ver" ] || ! [[ "$new_major" =~ ^[0-9]+$ ]] || [ "$new_major" -lt 18 ]; then
    echo ""
    echo "  Post-install re-check FAILED — 'node --version' returned '${new_ver}'."
    echo "  Falling back to manual instructions:"
    _preflight_install_instructions
    return 1
  fi

  echo ""
  echo "  Installed: node v${new_ver} at $(command -v node)"
  echo "  Continuing with deploy..."
  echo ""
}


# ===========================================================================
# Shared helpers for the additional preflight checks below
# (AWS CLI, Python 3, zip, AWS credentials)
# ===========================================================================

# Detect the system package manager. Prints one of: dnf, yum, apt-get, brew, unknown.
_preflight_detect_pkg_manager() {
  if command -v dnf     >/dev/null 2>&1; then echo "dnf";     return; fi
  if command -v yum     >/dev/null 2>&1; then echo "yum";     return; fi
  if command -v apt-get >/dev/null 2>&1; then echo "apt-get"; return; fi
  if command -v brew    >/dev/null 2>&1; then echo "brew";    return; fi
  echo "unknown"
}

# Returns 0 (true) if passwordless sudo is available, 1 (false) otherwise.
# We only run sudo commands automatically when passwordless — otherwise the
# script would hang waiting for a password prompt inside an unattended block.
_preflight_can_sudo() {
  command -v sudo >/dev/null 2>&1 || return 1
  sudo -n true 2>/dev/null
}

# Confirmation gate shared by every install-capable preflight check.
# Args: $1 = short "what will be installed" description shown in the prompt.
# Prints the "Proposed install:" heading + the description, then asks Y/N.
# Honours PREFLIGHT_AUTO_INSTALL=1 for non-interactive use.
# Returns 0 = "yes, install", 1 = "no, don't install".
_preflight_confirm() {
  local description="$1"
  local auto="${PREFLIGHT_AUTO_INSTALL:-}"
  if [ "$auto" = "1" ] || [ "$auto" = "yes" ]; then
    echo "  PREFLIGHT_AUTO_INSTALL=${auto} — proceeding with install without prompt."
    return 0
  fi
  if [ ! -t 0 ]; then
    echo "  Non-interactive shell and PREFLIGHT_AUTO_INSTALL not set — skipping auto-install."
    return 1
  fi
  echo ""
  echo "  Proposed install:"
  printf '%s\n' "$description" | sed 's/^/    /'
  echo ""
  local reply=""
  read -r -p "  Proceed? [y/N]: " reply || reply=""
  case "$reply" in
    y|Y|yes|YES) return 0 ;;
    *)           return 1 ;;
  esac
}

# Install a package using whichever package manager was detected. Uses
# passwordless sudo for yum/dnf/apt-get; runs brew without sudo.
# Args: $1 = display name  $2 = pkg name (linux)  $3 = pkg name (brew)
_preflight_pkg_install() {
  local display="$1"
  local pkg_linux="$2"
  local pkg_brew="$3"
  local mgr
  mgr=$(_preflight_detect_pkg_manager)
  case "$mgr" in
    dnf|yum|apt-get)
      if ! _preflight_can_sudo; then
        echo "  Passwordless sudo not available. Run manually then re-run this script:"
        echo "    sudo $mgr install -y $pkg_linux"
        return 1
      fi
      echo "  Installing ${display} via sudo ${mgr}..."
      if [ "$mgr" = "apt-get" ]; then
        sudo -n apt-get update -qq >/dev/null 2>&1 || true
      fi
      sudo -n "$mgr" install -y "$pkg_linux"
      ;;
    brew)
      echo "  Installing ${display} via brew..."
      brew install "$pkg_brew"
      ;;
    *)
      echo "  Unknown package manager. Install ${display} manually via your OS's package tools."
      return 1
      ;;
  esac
}

# ===========================================================================
# preflight_check_aws_cli — AWS CLI v2 installed (>=2.15 recommended)
# ===========================================================================
#
# v1 CLIs work for most calls but omit `rolloutState` from `ecs describe-
# services`, degrading Step 9 rollout polling to the /health fallback.
# v2 is a hard requirement below 2.x altogether; 2.0-2.14 works but is
# older than what the README lists, so we warn without failing.

_preflight_install_aws_cli() {
  local uname_s
  uname_s=$(uname -s 2>/dev/null || echo "unknown")

  if [ "$uname_s" = "Darwin" ]; then
    if command -v brew >/dev/null 2>&1; then
      echo "  Installing AWS CLI v2 via brew..."
      brew install awscli
      return
    fi
    echo "  On macOS without brew — download and install manually from:"
    echo "    https://awscli.amazonaws.com/AWSCLIV2.pkg"
    return 1
  fi

  # Linux x86_64 install into ~/.local (no sudo required — mirrors the
  # Node.js glibc-217 approach).
  local arch
  arch=$(uname -m 2>/dev/null || echo "x86_64")
  local zip_url="https://awscli.amazonaws.com/awscli-exe-linux-${arch}.zip"
  local tmpzip="/tmp/awscliv2-$$.zip"
  local tmpdir="/tmp/awscliv2-$$"

  if ! command -v unzip >/dev/null 2>&1; then
    echo "  unzip is required to install the AWS CLI. Install it first:"
    echo "    sudo $(_preflight_detect_pkg_manager) install -y unzip"
    return 1
  fi

  echo "  Downloading AWS CLI v2 for ${arch}..."
  if ! curl -sSfL "$zip_url" -o "$tmpzip"; then
    echo "  Download failed. Check network connectivity to awscli.amazonaws.com."
    return 1
  fi

  echo "  Extracting and installing to ~/.local/aws-cli..."
  mkdir -p "$tmpdir" "$HOME/.local/bin"
  unzip -q "$tmpzip" -d "$tmpdir"
  "$tmpdir/aws/install" \
    --install-dir "$HOME/.local/aws-cli" \
    --bin-dir "$HOME/.local/bin" \
    --update > /tmp/aws-cli-install.log 2>&1 || {
      echo "  AWS CLI install failed. Log:"
      tail -20 /tmp/aws-cli-install.log || true
      rm -rf "$tmpzip" "$tmpdir"
      return 1
  }
  rm -rf "$tmpzip" "$tmpdir"

  # Prepend ~/.local/bin so the newly-installed aws wins over any /usr/bin/aws v1.
  export PATH="$HOME/.local/bin:$PATH"
  local rcfile path_line
  rcfile=$(_preflight_shell_rcfile)
  path_line='export PATH="$HOME/.local/bin:$PATH"'
  if ! grep -qF "$path_line" "$rcfile" 2>/dev/null; then
    {
      echo ''
      echo '# Added by bedrock-activity-dashboard preflight (AWS CLI v2)'
      echo "$path_line"
    } >> "$rcfile"
    echo "  Persisted PATH to $rcfile"
  fi
  hash -r 2>/dev/null || true
}

preflight_check_aws_cli() {
  echo ""
  echo "=== Preflight: AWS CLI ==="
  local aws_path aws_ver aws_major aws_minor
  if aws_path=$(command -v aws 2>/dev/null); then
    printf "  %-16s %s\n" "aws:" "$aws_path"
    # `aws --version` prints e.g. "aws-cli/2.15.5 Python/3.11.6 ..."
    aws_ver=$(aws --version 2>&1 | head -1 || true)
    printf "  %-16s %s\n" "version:" "$aws_ver"
    if [[ "$aws_ver" =~ aws-cli/([0-9]+)\.([0-9]+) ]]; then
      aws_major="${BASH_REMATCH[1]}"
      aws_minor="${BASH_REMATCH[2]}"
      if [ "$aws_major" -ge 2 ]; then
        if [ "$aws_major" -eq 2 ] && [ "$aws_minor" -lt 15 ]; then
          echo "  Status:          OK (WARN — v2.${aws_minor} works but README recommends v2.15+ for reliable ECS rolloutState field)"
        else
          echo "  Status:          OK"
        fi
        return 0
      fi
    fi
  else
    printf "  %-16s %s\n" "aws:" "NOT FOUND"
  fi

  echo ""
  echo "  Status:          FAILED — AWS CLI v2 is required (README §Deployment Step 2)."

  if _preflight_confirm "1. Download awscli-exe-linux-x86_64.zip from awscli.amazonaws.com
2. Extract to /tmp and run the installer with --install-dir ~/.local/aws-cli
3. Prepend ~/.local/bin to PATH and persist to $(_preflight_shell_rcfile)"; then
    echo ""
    echo "--- Installing AWS CLI v2 ---"
    if ! _preflight_install_aws_cli; then
      echo ""
      echo "Auto-install failed. Manual install:"
      echo "  https://docs.aws.amazon.com/cli/latest/userguide/getting-started-install.html"
      return 1
    fi
    # Re-check
    local new_ver new_major
    new_ver=$(aws --version 2>&1 | head -1 || true)
    if [[ "$new_ver" =~ aws-cli/([0-9]+) ]]; then
      new_major="${BASH_REMATCH[1]}"
      if [ "$new_major" -ge 2 ]; then
        echo "  Installed: $new_ver at $(command -v aws)"
        return 0
      fi
    fi
    echo "  Post-install re-check FAILED — 'aws --version' returned: $new_ver"
    return 1
  fi

  echo ""
  echo "Manual install:"
  echo "  curl \"https://awscli.amazonaws.com/awscli-exe-linux-x86_64.zip\" -o /tmp/awscliv2.zip"
  echo "  unzip /tmp/awscliv2.zip -d /tmp && /tmp/aws/install --install-dir ~/.local/aws-cli --bin-dir ~/.local/bin"
  echo "  export PATH=\"\$HOME/.local/bin:\$PATH\""
  echo ""
  return 1
}

# ===========================================================================
# preflight_check_python3 — Python 3 installed
# ===========================================================================

preflight_check_python3() {
  echo ""
  echo "=== Preflight: Python 3 ==="
  local py_path py_ver
  if py_path=$(command -v python3 2>/dev/null); then
    printf "  %-16s %s\n" "python3:" "$py_path"
    py_ver=$(python3 --version 2>&1 || true)
    printf "  %-16s %s\n" "version:" "$py_ver"
    echo "  Status:          OK"
    return 0
  fi
  printf "  %-16s %s\n" "python3:" "NOT FOUND"
  echo ""
  echo "  Status:          FAILED — python3 is required (deploy scripts use it for version bumps and Cognito bootstrap)."

  local mgr
  mgr=$(_preflight_detect_pkg_manager)
  if _preflight_confirm "Install python3 via detected package manager: ${mgr}"; then
    echo ""
    echo "--- Installing Python 3 ---"
    local pkg_linux="python3"
    local pkg_brew="python@3.12"
    if _preflight_pkg_install "python3" "$pkg_linux" "$pkg_brew"; then
      if command -v python3 >/dev/null 2>&1; then
        echo "  Installed: $(python3 --version 2>&1) at $(command -v python3)"
        return 0
      fi
    fi
    echo "  Post-install re-check FAILED — python3 still not on PATH."
    return 1
  fi

  echo ""
  echo "Manual install:"
  case "$mgr" in
    dnf|yum)  echo "  sudo $mgr install -y python3" ;;
    apt-get)  echo "  sudo apt-get update && sudo apt-get install -y python3" ;;
    brew)     echo "  brew install python@3.12" ;;
    *)        echo "  Install python3 via your OS's package tools." ;;
  esac
  echo ""
  return 1
}

# ===========================================================================
# preflight_check_zip — zip binary (used in Step 5 backend source packaging)
# ===========================================================================

preflight_check_zip() {
  echo ""
  echo "=== Preflight: zip ==="
  local zip_path
  if zip_path=$(command -v zip 2>/dev/null); then
    printf "  %-16s %s\n" "zip:" "$zip_path"
    echo "  Status:          OK"
    return 0
  fi
  printf "  %-16s %s\n" "zip:" "NOT FOUND"
  echo ""
  echo "  Status:          FAILED — zip is required (Step 5 packages backend source with 'zip -r')."

  local mgr
  mgr=$(_preflight_detect_pkg_manager)
  if _preflight_confirm "Install zip via detected package manager: ${mgr}"; then
    echo ""
    echo "--- Installing zip ---"
    if _preflight_pkg_install "zip" "zip" "zip"; then
      if command -v zip >/dev/null 2>&1; then
        echo "  Installed: zip at $(command -v zip)"
        return 0
      fi
    fi
    echo "  Post-install re-check FAILED — zip still not on PATH."
    return 1
  fi

  echo ""
  echo "Manual install:"
  case "$mgr" in
    dnf|yum)  echo "  sudo $mgr install -y zip" ;;
    apt-get)  echo "  sudo apt-get update && sudo apt-get install -y zip" ;;
    brew)     echo "  brew install zip  # (usually preinstalled on macOS)" ;;
    *)        echo "  Install zip via your OS's package tools." ;;
  esac
  echo ""
  return 1
}

# ===========================================================================
# preflight_check_aws_credentials — sts:GetCallerIdentity smoke test
# ===========================================================================
#
# Credentials are user-specific — cannot be "auto-installed". Instead, when
# the check fails, print AWS-native refresh commands (SSO, named profile,
# environment variables) so the user can pick whichever credential mechanism
# their organization uses.
#
# MUST be called AFTER the deploy script's profile-resolution block, since
# it consumes ${PROFILE} to route the call to the same profile the rest of
# the deploy will use.

preflight_check_aws_credentials() {
  echo ""
  echo "=== Preflight: AWS credentials ==="

  local prof_display="${PROFILE:-<env credentials>}"
  printf "  %-16s %s\n" "profile:" "$prof_display"

  local identity
  if identity=$(aws sts get-caller-identity \
      ${PROFILE:+--profile $PROFILE} 2>&1); then
    local account arn
    account=$(echo "$identity" | grep -oE '"Account":[[:space:]]*"[0-9]+"' | grep -oE '[0-9]+')
    arn=$(echo "$identity"     | grep -oE '"Arn":[[:space:]]*"[^"]+"' | sed 's/.*"Arn":[[:space:]]*"\([^"]*\)".*/\1/')
    printf "  %-16s %s\n" "account:" "${account:-unknown}"
    printf "  %-16s %s\n" "arn:"     "${arn:-unknown}"
    echo "  Status:          OK"
    return 0
  fi

  # Diagnose failure — surface the raw error, then prescribe a fix using
  # only AWS-native commands (no organization-specific tooling).
  printf "  %-16s %s\n" "error:" "$(echo "$identity" | head -1)"
  echo ""
  echo "  Status:          FAILED — AWS credentials are missing or expired."
  echo ""
  echo "Refresh your credentials, then re-run this script. Common options:"
  echo ""
  echo "  # AWS IAM Identity Center (SSO):"
  echo "    aws sso login${PROFILE:+ --profile $PROFILE}"
  echo ""
  echo "  # Named profile (long-lived or STS temporary credentials):"
  echo "    aws configure${PROFILE:+ --profile $PROFILE}"
  echo ""
  echo "  # Environment variables (paste from wherever you obtain them):"
  echo "    export AWS_ACCESS_KEY_ID=<key>"
  echo "    export AWS_SECRET_ACCESS_KEY=<secret>"
  echo "    export AWS_SESSION_TOKEN=<token>   # only for STS temporary credentials"
  echo ""
  echo "Reference: https://docs.aws.amazon.com/cli/latest/userguide/cli-configure-files.html"
  echo ""
  return 1
}
