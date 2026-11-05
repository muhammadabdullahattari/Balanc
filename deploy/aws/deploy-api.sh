#!/usr/bin/env bash
# Root-only EC2 deployment; TLS nginx uses the supplied upstream include.
set -Eeuo pipefail
image="${1:?Usage: deploy-api.sh IMAGE_URI AWS_REGION}"
region="${2:?Usage: deploy-api.sh IMAGE_URI AWS_REGION}"
env_file=/etc/ledgerpilot/api.env
upstream=/etc/nginx/ledgerpilot-upstream.conf
evidence=/var/lib/ledgerpilot/evidence
exec 9>/var/lock/ledgerpilot-deploy.lock
flock -n 9 || { echo "Another deployment is running" >&2; exit 1; }
[[ "$(stat -c '%u:%a' "$env_file")" == "0:600" ]] || { echo "Runtime env must be root-owned mode 600" >&2; exit 1; }
[[ -f "$upstream" ]] || { echo "Install the nginx upstream include first" >&2; exit 1; }
nginx -t
install -d -m 700 -o 10001 -g 10001 "$evidence"
aws ecr get-login-password --region "$region" | docker login --username AWS --password-stdin "${image%%/*}"
docker pull "$image"
previous_upstream="$(cat "$upstream")"
port=8001
if grep -q '127.0.0.1:8001' "$upstream"; then port=8000; fi
old_present=false
if docker container inspect ledgerpilot-api >/dev/null 2>&1; then old_present=true; fi
# Never destroy an unexplained leftover from an interrupted deployment.
for name in ledgerpilot-api-candidate ledgerpilot-api-previous; do
  if docker container inspect "$name" >/dev/null 2>&1; then
    echo "Inspect interrupted deployment container $name before retrying" >&2; exit 1
  fi
done
renamed=false
promoted=false
switched=false
ready() {
  for attempt in $(seq 1 60); do
    if docker exec "$1" python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health/ready', timeout=3)" >/dev/null 2>&1; then return 0; fi
    sleep 2
  done
  return 1
}
rollback() {
  status=$?
  trap - EXIT INT TERM
  if [[ $status -ne 0 ]]; then
    echo "Deployment failed; restoring previous traffic route" >&2
    printf '%s\n' "$previous_upstream" > "$upstream"
    if $switched; then nginx -t && nginx -s reload || echo "URGENT: proxy recovery failed" >&2; fi
    if $promoted; then docker rm -f ledgerpilot-api >/dev/null || true; fi
    if $renamed; then docker rename ledgerpilot-api-previous ledgerpilot-api; fi
    docker rm -f ledgerpilot-api-candidate >/dev/null 2>&1 || true
    if $old_present; then ready ledgerpilot-api || echo "URGENT: previous API readiness failed" >&2; fi
  fi
  exit "$status"
}
trap rollback EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
docker run --detach --name ledgerpilot-api-candidate --restart unless-stopped \
  --cap-drop ALL --security-opt no-new-privileges:true --pids-limit 128 \
  --memory 1g --cpus 2 --env-file "$env_file" \
  --env EVIDENCE_STORAGE_ROOT=/var/lib/ledgerpilot/evidence \
  --mount "type=bind,source=$evidence,target=/var/lib/ledgerpilot/evidence" \
  --publish "127.0.0.1:$port:8000" "$image" >/dev/null
ready ledgerpilot-api-candidate || { echo "Candidate readiness failed" >&2; exit 1; }
printf 'upstream ledgerpilot_api { server 127.0.0.1:%s; }\n' "$port" > "$upstream"
nginx -t
switched=true
nginx -s reload
# Keep the previous process available until traffic handoff and a second readiness check.
ready ledgerpilot-api-candidate
if $old_present; then docker rename ledgerpilot-api ledgerpilot-api-previous; renamed=true; fi
docker rename ledgerpilot-api-candidate ledgerpilot-api
promoted=true
trap - EXIT INT TERM
if $old_present; then docker stop --time 60 ledgerpilot-api-previous >/dev/null; docker rm ledgerpilot-api-previous >/dev/null; fi
echo "Deployed with readiness verified: $image"
