#!/usr/bin/env bash
# Recreate GitHub Actions metrics CSVs from the live API.
#
# Config-driven usage:
#   bin/recreate.sh --config ../analysis.toml [--force]
#
# Direct usage:
#   bin/recreate.sh --org ORG [--period PERIOD] [--cache-dir DIR] [--reports-dir DIR] [--force]
#
# Environment overrides for direct usage:
#   ORG, PERIOD, CACHE_DIR, REPORTS_DIR, FORCE

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SUBMODULE_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"

ORG="${ORG:-}"
PERIOD="${PERIOD:-last-year}"
CACHE_DIR="${CACHE_DIR:-${SUBMODULE_DIR}/cache}"
REPORTS_DIR="${REPORTS_DIR:-${SUBMODULE_DIR}/reports}"
CONFIG=""
FORCE="${FORCE:-}"

usage() {
    awk 'NR > 1 && /^#/ { sub(/^# ?/, ""); print; next } NR > 1 { exit }' "$0"
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --config)
            CONFIG="$2"
            shift 2
            ;;
        --org)
            ORG="$2"
            shift 2
            ;;
        --period)
            PERIOD="$2"
            shift 2
            ;;
        --cache-dir)
            CACHE_DIR="$2"
            shift 2
            ;;
        --reports-dir|--output-dir)
            REPORTS_DIR="$2"
            shift 2
            ;;
        --force)
            FORCE="1"
            shift
            ;;
        -h|--help)
            usage
            exit 0
            ;;
        *)
            echo "Unknown argument: $1" >&2
            usage >&2
            exit 2
            ;;
    esac
done

UV="uv --project ${SUBMODULE_DIR} run"

if [[ -n "${CONFIG}" ]]; then
    ${UV} ghinsight actions recreate \
        --config "${CONFIG}" \
        ${FORCE:+--force}
    exit 0
fi

if [[ -z "${ORG}" ]]; then
    echo "Error: --org or ORG is required unless --config is used." >&2
    usage >&2
    exit 2
fi

echo "=== ghinsight: recreate metrics ==="
echo "  org:          ${ORG}"
echo "  period:       ${PERIOD}"
echo "  cache:        ${CACHE_DIR}"
echo "  reports:      ${REPORTS_DIR}"
echo ""

echo "--- Step 1: fetch raw data for ${ORG} ---"
${UV} ghinsight actions fetch \
    --org "${ORG}" \
    --period "${PERIOD}" \
    --cache-dir "${CACHE_DIR}" \
    ${FORCE:+--force}

echo ""
echo "--- Step 2: generate reports ---"
${UV} ghinsight actions report \
    --org "${ORG}" \
    --cache-dir "${CACHE_DIR}" \
    --output-dir "${REPORTS_DIR}"

echo ""
echo "=== Done. Reports in ${REPORTS_DIR} ==="
