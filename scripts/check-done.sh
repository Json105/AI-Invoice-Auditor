#!/usr/bin/env bash
# Automated verification gate for AI-Invoice-Auditor.
# Supports both direct CLI execution and Antigravity Stop hook JSON I/O (--hook).

set -u
set -o pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$PROJECT_ROOT"
export PYTHONPATH="$PROJECT_ROOT${PYTHONPATH:+:$PYTHONPATH}"

MODE="cli"
if [[ "${1:-}" == "--hook" ]]; then
  MODE="hook"
elif [[ $# -gt 0 ]]; then
  echo "Usage: $0 [--hook]" >&2
  exit 2
fi

CHECK_NAMES=(
  "Python syntax check (compileall)"
  "Frontend JS syntax check (node --check)"
  "Offline unit & integration tests (pytest)"
)

run_single_check() {
  local idx="$1"
  case "$idx" in
    0) python3 -m compileall -q src tests ;;
    1) node --check src/web/static/app.js ;;
    2) pytest -q tests/ ;;
    *) return 1 ;;
  esac
}

run_cli_mode() {
  local total="${#CHECK_NAMES[@]}"
  local i
  for ((i = 0; i < total; i++)); do
    echo "[$((i + 1))/${total}] ${CHECK_NAMES[$i]}..."
    if ! run_single_check "$i"; then
      echo "FAILED: ${CHECK_NAMES[$i]}" >&2
      return 1
    fi
  done
  echo "ALL CHECKS PASSED."
  return 0
}

should_skip_hook_gate() {
  local payload="$1"
  python3 -c '
import json, sys

raw = sys.argv[1].strip()
if not raw:
    sys.exit(1)

try:
    data = json.loads(raw)
except Exception:
    sys.exit(1)

# Prevent infinite retry loops when a Stop hook already triggered continuation,
# and avoid blocking explicit cancellations, errors, or budget/invocation limits.
if data.get("continuationActive"):
    sys.exit(0)

reason = data.get("terminationReason", "")
if reason and reason != "NO_TOOL_CALL":
    sys.exit(0)

sys.exit(1)
' "$payload"
}

emit_hook_continue() {
  local failed_step="$1"
  local output="$2"
  python3 -c '
import json, sys

step = sys.argv[1]
out = sys.argv[2].strip()
max_len = 1500
if len(out) > max_len:
    out = "...(truncated)...\n" + out[-max_len:]

reason = (
    f"Stop hook verification failed at [{step}]. "
    "Please fix the issues and ensure ./scripts/check-done.sh passes before finishing."
)
if out:
    reason += f"\n\nOutput:\n{out}"

print(json.dumps({"decision": "continue", "reason": reason}, ensure_ascii=False))
' "$failed_step" "$output"
}

run_hook_mode() {
  local stdin_payload=""
  if [[ ! -t 0 ]]; then
    stdin_payload="$(cat)"
  fi

  if should_skip_hook_gate "$stdin_payload"; then
    echo "{}"
    return 0
  fi

  local total="${#CHECK_NAMES[@]}"
  local i output status
  for ((i = 0; i < total; i++)); do
    # Capture stdout/stderr so hook stdout remains strictly valid JSON.
    output="$(run_single_check "$i" 2>&1)"
    status=$?
    if [[ "$status" -ne 0 ]]; then
      emit_hook_continue "${CHECK_NAMES[$i]}" "$output"
      return 0
    fi
  done

  echo "{}"
  return 0
}

if [[ "$MODE" == "hook" ]]; then
  run_hook_mode
else
  run_cli_mode
fi
