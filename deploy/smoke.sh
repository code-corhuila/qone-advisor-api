#!/usr/bin/env sh
# Smoke checks of the advisor image by HTTP (norm 5.3.12, without a token): health, the
# envelope on 401 and on an unknown route, the correlation header. Usage:
#   deploy/smoke.sh http://localhost:8015
set -eu
BASE="${1:-http://localhost:8015}"
fail() { echo "FAIL: $1" >&2; exit 1; }
status() { curl -s -o /dev/null -w '%{http_code}' "$@"; }

[ "$(status "$BASE/api/v1/advisor/health")" = "200" ] || fail "/health is not 200"
curl -s "$BASE/api/v1/advisor/health" | grep -q '"service":"qone-advisor-api"' || fail "health does not name the service"
[ "$(status "$BASE/api/v1/advisor/conversations/me")" = "401" ] || fail "a request without a token is not 401"
curl -s "$BASE/api/v1/advisor/conversations/me" | grep -q '"error":"UNAUTHORIZED"' || fail "401 has no envelope"
[ "$(status "$BASE/api/v1/advisor/nothing")" = "404" ] || fail "an unknown route is not 404"
curl -s "$BASE/api/v1/advisor/nothing" | grep -q '"error":"NOT_FOUND"' || fail "404 has no envelope"
corr=$(curl -s -D - -o /dev/null -H 'X-Correlation-Id: smoke-1' "$BASE/api/v1/advisor/health" | tr -d '\r' | grep -i '^x-correlation-id:' | cut -d' ' -f2)
[ "$corr" = "smoke-1" ] || fail "X-Correlation-Id is not returned"
echo "OK: advisor smoke checks passed against $BASE"
