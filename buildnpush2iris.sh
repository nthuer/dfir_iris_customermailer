#!/usr/bin/env bash
#
# Build the module wheel and install it into the DFIR-IRIS containers.
#
# Follows the convention used by the DFIR-IRIS community modules:
#
#   ./buildnpush2iris.sh        install into the worker container only
#   ./buildnpush2iris.sh -a     install into the worker AND app container
#   ./buildnpush2iris.sh -h     show this help
#
# This module registers a manual case hook (handled by the worker) and a
# send dialog served by the web app, so it has to be installed into BOTH
# containers: always run it with -a.
#
# Container names can be overridden for non-standard deployments:
#   IRIS_APP_CONTAINER=my_app IRIS_WORKER_CONTAINER=my_worker ./buildnpush2iris.sh -a
#
# Copyright 2026 Niklas Thürnau - Apache License 2.0

set -euo pipefail

APP_CONTAINER="${IRIS_APP_CONTAINER:-iriswebapp_app}"
WORKER_CONTAINER="${IRIS_WORKER_CONTAINER:-iriswebapp_worker}"
DEPENDENCIES_DIR="/iriswebapp/dependencies"
PYTHON="${PYTHON:-python3}"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

usage() {
    cat <<'USAGE'
Usage: ./buildnpush2iris.sh [-a|-h]

  -a    Install into the worker AND the app container (recommended, and
        required for this module: the hook runs in the worker, the send
        dialog is served by the app).
  -h    Show this help.

  Without a flag the module is installed into the worker container only.

Environment overrides:
  IRIS_APP_CONTAINER      default: iriswebapp_app
  IRIS_WORKER_CONTAINER   default: iriswebapp_worker
  PYTHON                  default: python3
USAGE
}

log()  { printf '\033[0;34m[*]\033[0m %s\n' "$1"; }
ok()   { printf '\033[0;32m[+]\033[0m %s\n' "$1"; }
die()  { printf '\033[0;31m[!]\033[0m %s\n' "$1" >&2; exit 1; }

install_app=0
while getopts ":ah" opt; do
    case "$opt" in
        a) install_app=1 ;;
        h) usage; exit 0 ;;
        \?) die "Unknown option: -$OPTARG (use -h for help)" ;;
    esac
done

# --------------------------------------------------------------- checks

command -v docker >/dev/null 2>&1 || die "docker not found in PATH."
command -v "$PYTHON" >/dev/null 2>&1 || die "$PYTHON not found in PATH (override with PYTHON=...)."

container_running() {
    docker ps --format '{{.Names}}' | grep -Fxq "$1"
}

container_running "$WORKER_CONTAINER" \
    || die "Container '$WORKER_CONTAINER' is not running. Start IRIS first, or set IRIS_WORKER_CONTAINER."
if [ "$install_app" -eq 1 ]; then
    container_running "$APP_CONTAINER" \
        || die "Container '$APP_CONTAINER' is not running. Start IRIS first, or set IRIS_APP_CONTAINER."
fi

# ---------------------------------------------------------------- build

log "Building wheel ..."
rm -rf dist build ./*.egg-info

if "$PYTHON" -c "import build" >/dev/null 2>&1; then
    "$PYTHON" -m build --wheel >/dev/null
else
    "$PYTHON" -c "import wheel" >/dev/null 2>&1 \
        || die "Neither 'build' nor 'wheel' is installed. Run: $PYTHON -m pip install wheel"
    "$PYTHON" setup.py bdist_wheel >/dev/null
fi

WHEEL_PATH="$(ls -1t dist/*.whl 2>/dev/null | head -n 1 || true)"
[ -n "$WHEEL_PATH" ] || die "No wheel was produced in ./dist."
WHEEL_NAME="$(basename "$WHEEL_PATH")"
ok "Built $WHEEL_NAME"

# Fail fast if the templates did not make it into the wheel - the module
# would install cleanly but be unable to render mails or the dialog.
# The listing is captured once and matched with here-strings on purpose:
# piping into `grep -q` under `set -o pipefail` reports a failed pipeline
# even on a match, because grep exits at the first hit and unzip then
# dies on SIGPIPE - which would reject a perfectly valid wheel.
if command -v unzip >/dev/null 2>&1; then
    wheel_listing="$(unzip -l "$WHEEL_PATH")"
    if ! grep -q "mail_templates/.*\.html" <<< "$wheel_listing"; then
        die "Wheel is missing the mail templates - check MANIFEST.in / package_data."
    fi
    if ! grep -q "ui/templates/dialog\.html" <<< "$wheel_listing"; then
        die "Wheel is missing the send dialog - check MANIFEST.in / package_data."
    fi
    ok "Wheel contains dialog and mail templates"
fi

# --------------------------------------------------------------- deploy

deploy_to() {
    local container="$1"

    log "Copying $WHEEL_NAME into $container:$DEPENDENCIES_DIR ..."
    docker exec "$container" mkdir -p "$DEPENDENCIES_DIR"
    docker cp "$WHEEL_PATH" "$container:$DEPENDENCIES_DIR/$WHEEL_NAME"

    log "Installing into $container ..."
    docker exec "$container" pip3 install --force-reinstall \
        "$DEPENDENCIES_DIR/$WHEEL_NAME"

    log "Restarting $container ..."
    docker restart "$container" >/dev/null
    ok "$container updated"
}

deploy_to "$WORKER_CONTAINER"
if [ "$install_app" -eq 1 ]; then
    deploy_to "$APP_CONTAINER"
else
    printf '\033[0;33m[!]\033[0m %s\n' \
        "Installed into the worker only. This module also needs the app container - rerun with -a."
fi

cat <<EOF

$(ok "Done.")

Next steps in the IRIS web interface:
  1. Advanced -> Modules -> Add module
  2. Module name: iris_customer_case_mailer_module
  3. Fill in the configuration (SMTP host/port/sender are mandatory)
  4. Enable the module

The hook then appears in a case under: Actions -> Send customer report
EOF
