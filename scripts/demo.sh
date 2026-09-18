#!/usr/bin/env bash
# Demo script (PRD §92). Requires `make up` (or `docker compose up`) to
# already be running with api on :8000 and demo-webhook on :9000.
set -euo pipefail

API="http://localhost:8000"
WEBHOOK_CONTROL="http://localhost:9000/_control"

bold() { printf "\n\033[1m%s\033[0m\n" "$1"; }
jqp() { python3 -m json.tool 2>/dev/null || cat; }

bold "0. Health check"
curl -sf "$API/v1/health" | jqp

bold "This demo script assumes you have already created a tenant, API key,"
echo "and registered an endpoint pointing at http://demo-webhook:9000/events"
echo "(use the API directly — see docs/api or README 'Local Setup' for the"
echo "exact curl commands, since tenant bootstrap isn't exposed as a public"
echo "unauthenticated endpoint by design)."
echo
echo "Set these before running the rest of this script:"
echo "  export API_KEY=em_live_..."
echo

: "${API_KEY:?Set API_KEY first, see above}"

bold "1. Publish an event, expect it to be delivered"
curl -s -X POST "$WEBHOOK_CONTROL" -d '{"mode":"success"}' -H "Content-Type: application/json" > /dev/null
EVENT_ID=$(curl -s -X POST "$API/v1/events" \
  -H "Authorization: Bearer $API_KEY" -H "Content-Type: application/json" \
  -d '{"type":"payment.completed","data":{"payment_id":"pay_123","amount":499}}' \
  | python3 -c "import sys,json;print(json.load(sys.stdin)['event_id'])")
echo "event_id=$EVENT_ID"
sleep 2
curl -s "$API/v1/events/$EVENT_ID" -H "Authorization: Bearer $API_KEY" | jqp

bold "2. Webhook returns 500 — expect retries"
curl -s -X POST "$WEBHOOK_CONTROL" -d '{"mode":"500"}' -H "Content-Type: application/json" > /dev/null
EVENT_ID=$(curl -s -X POST "$API/v1/events" \
  -H "Authorization: Bearer $API_KEY" -H "Content-Type: application/json" \
  -d '{"type":"payment.completed","data":{"payment_id":"pay_124"}}' \
  | python3 -c "import sys,json;print(json.load(sys.stdin)['event_id'])")
echo "event_id=$EVENT_ID — watch worker logs for attempt 1/2/3 retrying"
sleep 8

bold "3. Webhook stays unavailable — expect eventual DLQ"
echo "(leave mode=500 and wait past 6 attempts / ~1 minute of backoff)"
sleep 60
curl -s "$API/v1/dlq" -H "Authorization: Bearer $API_KEY" | jqp

bold "4. Replay from DLQ, webhook fixed — expect delivered"
curl -s -X POST "$WEBHOOK_CONTROL" -d '{"mode":"success"}' -H "Content-Type: application/json" > /dev/null
DLQ_ID=$(curl -s "$API/v1/dlq" -H "Authorization: Bearer $API_KEY" | python3 -c "import sys,json;d=json.load(sys.stdin);print(d[0]['id'] if d else '')")
if [ -n "$DLQ_ID" ]; then
  curl -s -X POST "$API/v1/dlq/$DLQ_ID/replay" -H "Authorization: Bearer $API_KEY" | jqp
fi

bold "5. Duplicate idempotency key — expect same event_id returned twice"
KEY="demo-idem-$(date +%s)"
R1=$(curl -s -X POST "$API/v1/events" -H "Authorization: Bearer $API_KEY" -H "Content-Type: application/json" -H "Idempotency-Key: $KEY" -d '{"type":"order.created","data":{}}')
R2=$(curl -s -X POST "$API/v1/events" -H "Authorization: Bearer $API_KEY" -H "Content-Type: application/json" -H "Idempotency-Key: $KEY" -d '{"type":"order.created","data":{}}')
echo "$R1"
echo "$R2"

bold "6. Kill a worker mid-processing — expect recovery"
echo "Run in another terminal: docker compose kill -s SIGKILL \$(docker compose ps -q worker | head -1)"
echo "then watch the remaining worker's logs for 'reclaimed_stale_messages'."

bold "7. Traffic spike — expect 429s once the free-tier rate limit is hit"
for i in $(seq 1 120); do
  curl -s -o /dev/null -w "%{http_code} " -X POST "$API/v1/events" \
    -H "Authorization: Bearer $API_KEY" -H "Content-Type: application/json" \
    -d '{"type":"load.test","data":{}}'
done
echo
