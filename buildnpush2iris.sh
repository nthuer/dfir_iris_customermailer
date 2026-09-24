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
# This module's hooks run synchronously inside the web app, so the app
# container is the one that matters - which the IRIS convention only
# covers with -a. Always run it with -a (app AND worker).
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

  -a    Install into the worker AND the app container (required for this
        module: its hooks run synchronously inside the app container).
  -h    Show this help.

  No Python is required on the host: if it cannot build the wheel, the
  build runs inside an IRIS container.

  Without a flag the module is installed into the worker container only.

Environment overrides:
  IRIS_APP_CONTAINER      default: iriswebapp_app
  IRIS_WORKER_CONTAINER   default: iriswebapp_worker
  PYTHON                  default: python3 (optional - falls back to a container build)
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
#
# No build tooling is required on the host: if the host cannot build the
# wheel, it is built inside an IRIS container, which ships Python, pip
# and setuptools. Production hosts stay clean.

SRC_TARBALL="./.ccm_build_src.tar.gz"     # relative on purpose, see below
trap 'rm -f "$SRC_TARBALL"' EXIT

clean_build_dirs() { rm -rf dist build ./*.egg-info; }

build_on_host() {
    command -v "$PYTHON" >/dev/null 2>&1 || return 1

    # In order of preference. setuptools >= 70.1 builds wheels without the
    # separate 'wheel' package, and `pip wheel` brings its own build
    # backend, so a bare pip is enough.
    clean_build_dirs
    "$PYTHON" -m build --wheel >/dev/null 2>&1 && return 0
    clean_build_dirs
    "$PYTHON" -m pip wheel --no-deps --wheel-dir dist . >/dev/null 2>&1 && return 0
    clean_build_dirs
    "$PYTHON" setup.py bdist_wheel >/dev/null 2>&1 && return 0
    clean_build_dirs
    return 1
}

build_in_container() {
    local container="$APP_CONTAINER"
    container_running "$container" || container="$WORKER_CONTAINER"
    command -v tar >/dev/null 2>&1 || die "Building needs either Python on the host or 'tar' to build inside $container."

    log "Building inside $container ..."
    # Host paths stay relative and container paths absolute, so this also
    # works in Git Bash on Windows (with MSYS_NO_PATHCONV=1).
    # The archive is written into the directory being archived, so tar
    # exits 1 with "file changed as we read it" even though the archive
    # is fine. Verify the archive itself instead of trusting the code.
    tar --exclude=.git --exclude=.venv --exclude=dist --exclude=build \
        --exclude='*.egg-info' --exclude=__pycache__ --exclude="$(basename "$SRC_TARBALL")" \
        -czf "$SRC_TARBALL" . 2>/dev/null || true
    tar -tzf "$SRC_TARBALL" >/dev/null 2>&1 || die "Could not pack the sources."

    docker exec "$container" sh -c 'rm -rf /tmp/ccm_build && mkdir -p /tmp/ccm_build' \
        || die "Could not prepare the build directory in $container."
    docker cp "$SRC_TARBALL" "$container:/tmp/ccm_build/src.tar.gz" \
        || die "Could not copy the sources into $container."
    docker exec "$container" sh -c '
        cd /tmp/ccm_build && tar xzf src.tar.gz && rm -rf dist build ./*.egg-info
        python3 setup.py bdist_wheel >/dev/null 2>&1 \
            || pip3 wheel --no-deps --wheel-dir dist . >/dev/null 2>&1' \
        || die "Building inside $container failed."

    local produced
    produced="$(docker exec "$container" sh -c 'ls -1 /tmp/ccm_build/dist/*.whl 2>/dev/null | head -n 1')"
    [ -n "$produced" ] || die "No wheel was produced inside $container."
    clean_build_dirs
    mkdir -p dist
    docker cp "$container:$produced" "dist/$(basename "$produced")" \
        || die "Could not copy the wheel out of $container."
    docker exec "$container" rm -rf /tmp/ccm_build >/dev/null 2>&1 || true
}

log "Building wheel ..."
if ! build_on_host; then
    if command -v "$PYTHON" >/dev/null 2>&1; then
        log "Host build failed ($PYTHON lacks a usable build backend) - using a container instead."
    else
        log "$PYTHON not found on the host - building in a container instead."
    fi
    build_in_container
fi

WHEEL_PATH="$(ls -1t dist/*.whl 2>/dev/null | head -n 1 || true)"
[ -n "$WHEEL_PATH" ] || die "No wheel was produced in ./dist."
WHEEL_NAME="$(basename "$WHEEL_PATH")"
ok "Built $WHEEL_NAME"

# Fail fast if the mail templates did not make it into the wheel - the
# module would install cleanly but be unable to render any mail.
# The listing is captured once and matched with a here-string on purpose:
# piping into `grep -q` under `set -o pipefail` reports a failed pipeline
# even on a match, because grep exits at the first hit and unzip then
# dies on SIGPIPE - which would reject a perfectly valid wheel.
if command -v unzip >/dev/null 2>&1; then
    wheel_listing="$(unzip -l "$WHEEL_PATH")"
    if ! grep -q "mail_templates/.*\.html" <<< "$wheel_listing"; then
        die "Wheel is missing the mail templates - check MANIFEST.in / package_data."
    fi
    ok "Wheel contains the mail templates"
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

Next steps in the IRIS web interface (first installation only):
  1. Advanced -> Modules -> Add module
  2. Module name: iris_customer_case_mailer_module
  3. Configure at least smtp_host, smtp_from_address and
     default_report_template (the module is active right away)

In a case, the Processors menu (bolt icon) then offers:
  Preview customer report / Send customer report / Test send customer report
EOF
