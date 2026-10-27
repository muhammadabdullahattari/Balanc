#!/usr/bin/env bash
# Install once on the demo EC2 instance as /opt/ledgerpilot/deploy-api.sh.
# Runtime secrets stay in /etc/ledgerpilot/api.env with root-only permissions.
set -Eeuo pipefail

image="${1:?Usage: deploy-api.sh IMAGE_URI AWS_REGION}"
region="${2:?Usage: deploy-api.sh IMAGE_URI AWS_REGION}"
registry="${image%%/*}"
container="ledgerpilot-api"
env_file="/etc/ledgerpilot/api.env"

if [[ ! -r "$env_file" ]]; then
  echo "Missing readable runtime environment file: $env_file" >&2
  exit 1
fi

aws ecr get-login-password --region "$region" | docker login --username AWS --password-stdin "$registry"
docker pull "$image"

previous_image=""
if docker container inspect "$container" >/dev/null 2>&1; then
  previous_image="$(docker inspect --format '{{.Config.Image}}' "$container")"
  docker stop "$container" >/dev/null || true
  docker rm "$container" >/dev/null || true
fi

rollback() {
  status=$?
  if [[ $status -ne 0 ]]; then
    docker rm -f "$container" >/dev/null 2>&1 || true
    if [[ -n "$previous_image" ]]; then
      echo "New API did not become healthy; restoring $previous_image" >&2
      docker run --detach --name "$container" --restart unless-stopped \
        --env-file "$env_file" --publish 127.0.0.1:8000:8000 \
        "$previous_image" >/dev/null
    fi
  fi
  exit "$status"
}
trap rollback EXIT

id="$(docker run --detach --name "$container" --restart unless-stopped \
  --env-file "$env_file" --publish 127.0.0.1:8000:8000 \
  "$image")"
for attempt in $(seq 1 30); do
  state="$(docker inspect --format '{{.State.Health.Status}}' "$id" 2>/dev/null || true)"
  if [[ "$state" == "healthy" ]]; then
    trap - EXIT
    echo "Deployed and healthy: $image"
    exit 0
  fi
  if [[ "$state" == "unhealthy" || "$(docker inspect --format '{{.State.Status}}' "$id")" == "exited" ]]; then
    docker logs "$id" || true
    exit 1
  fi
  sleep 2
done
docker logs "$id" || true
echo "Timed out waiting for API health check" >&2
exit 1


