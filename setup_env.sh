#!/usr/bin/env bash
#
# setup_env.sh — dedicated, isolated environment for the Shift Handover
# Intelligence project. Nothing else lives in here.
#
# Creates a conda environment named `freshworks` containing exactly what this
# project needs and nothing more:
#
#   * Python 3.12   — the seed and purge scripts (standard library only)
#   * Node.js 24.x  — required by FDK 10.x for the AI Actions app
#   * ipykernel     — registered as a Jupyter kernel scoped to this project
#   * @freshworks/fdk — installed inside the env, not globally
#
# Run from the project root, in Claude Code or Terminal on the Mac:
#
#     bash setup_env.sh
#
# WHY A SEPARATE ENV
# ------------------
# FDK 10.x requires Node 24.x. Other projects on this machine pin Node 22,
# and FDK is very particular about the Node version it packs against. Sharing
# an environment is how you end up debugging a packaging failure at 3am that
# is really a Node version mismatch.
#
# The Python scripts deliberately use only the standard library — urllib,
# json, base64 — so there is nothing to pip install and nothing to drift.

set -euo pipefail

ENV_NAME="freshworks"
PY_VERSION="3.12"
NODE_VERSION="24"
KERNEL_DISPLAY="Freshworks Hackathon (freshworks)"

bold() { printf '\033[1m%s\033[0m\n' "$1"; }
ok()   { printf '  \033[32m✓\033[0m %s\n' "$1"; }
warn() { printf '  \033[33m!\033[0m %s\n' "$1"; }

bold "Shift Handover Intelligence — environment setup"
echo

# ---------------------------------------------------------------- conda check
if ! command -v conda >/dev/null 2>&1; then
  echo "conda not found on PATH."
  echo "Install Miniforge or Miniconda first, then re-run this script."
  exit 1
fi
ok "conda found: $(conda --version)"

# shellcheck disable=SC1091
source "$(conda info --base)/etc/profile.d/conda.sh"

# ---------------------------------------------------------------- create env
if conda env list | awk '{print $1}' | grep -qx "$ENV_NAME"; then
  ok "environment '$ENV_NAME' already exists — reusing it"
else
  bold "Creating environment '$ENV_NAME'"
  conda create -y -n "$ENV_NAME" -c conda-forge \
    "python=$PY_VERSION" "nodejs=$NODE_VERSION" ipykernel
  ok "created"
fi

conda activate "$ENV_NAME"
ok "activated '$ENV_NAME'"

# ---------------------------------------------------------------- verify node
NODE_ACTUAL="$(node --version 2>/dev/null || echo none)"
if [[ "$NODE_ACTUAL" == v24.* ]]; then
  ok "node $NODE_ACTUAL  (FDK 10.x requires 24.x)"
else
  warn "node is $NODE_ACTUAL but FDK 10.x needs 24.x — installing into the env"
  conda install -y -n "$ENV_NAME" -c conda-forge "nodejs=$NODE_VERSION"
  ok "node $(node --version)"
fi

ok "python $(python --version 2>&1 | cut -d' ' -f2)"

# ---------------------------------------------------------------- jupyter kernel
bold "Registering Jupyter kernel"
python -m ipykernel install --user \
  --name "$ENV_NAME" \
  --display-name "$KERNEL_DISPLAY" >/dev/null
ok "kernel registered as: $KERNEL_DISPLAY"
echo "    select this kernel in any notebook for this project — nothing else"

# ---------------------------------------------------------------- fdk
bold "Freshworks Developer Kit"
if command -v fdk >/dev/null 2>&1; then
  ok "fdk already available: $(fdk version 2>/dev/null | head -1)"
else
  warn "installing @freshworks/fdk into the env (not globally)"
  npm install -g @freshworks/fdk >/dev/null 2>&1 || {
    warn "npm install of fdk failed — install manually inside the env:"
    echo "        conda activate $ENV_NAME && npm install -g @freshworks/fdk"
  }
  command -v fdk >/dev/null 2>&1 && ok "fdk $(fdk version 2>/dev/null | head -1)"
fi

# ---------------------------------------------------------------- app deps
if [[ -f ai-actions-app/package.json ]]; then
  bold "AI Actions app dependencies"
  ( cd ai-actions-app && npm install --no-fund --no-audit >/dev/null 2>&1 ) \
    && ok "vitest and coverage installed" \
    || warn "npm install failed in ai-actions-app — run it manually"
fi

# ---------------------------------------------------------------- summary
echo
bold "Done."
cat <<EOF

  Environment    $ENV_NAME
  Python         $(python --version 2>&1 | cut -d' ' -f2)
  Node           $(node --version)
  Jupyter kernel $KERNEL_DISPLAY

  Activate it before doing anything in this project:

      conda activate $ENV_NAME

  Then:

      export FRESHSERVICE_DOMAIN=shobana.freshservice.com
      export FRESHSERVICE_API_KEY=...

      python3 seed/purge_freshservice.py --confirm
      python3 seed/seed_shift_handover.py

      cd ai-actions-app && npm test && fdk validate

  Nothing else belongs in this environment. Keep it scoped to this project.

EOF
