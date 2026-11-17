"""Atomic distributed rate admission with bounded connections and expiring keys."""

from redis import Redis
from redis.backoff import NoBackoff
from redis.exceptions import RedisError
from redis.retry import Retry

from ledgerpilot.shared.errors import DomainError
from ledgerpilot.shared.telemetry import emit

# Count every dimension in one atomic operation. Rejected attempts do not extend
# an existing block. Keys expire automatically after their window and cooldown.
_SCRIPT = """
local window = tonumber(ARGV[1])
local cooldown = tonumber(ARGV[2])
local delay = 0
for i, key in ipairs(KEYS) do
  local blocked = redis.call('PTTL', key .. ':blocked')
  if blocked > 0 then
    delay = math.max(delay, blocked)
  else
    local count = redis.call('INCR', key)
    if count == 1 then redis.call('PEXPIRE', key, window) end
    local budget = tonumber(ARGV[i + 2])
    if count > budget then
      local blocked_for = math.max(redis.call('PTTL', key),
        math.min(cooldown, 1000 * 2 ^ math.min(count - budget, 12)))
      redis.call('SET', key .. ':blocked', '1', 'PX', blocked_for)
      delay = math.max(delay, blocked_for)
    end
  end
end
return math.ceil(delay / 1000)
"""


class RedisAdmission:
    def __init__(self, url: str, connections: int, timeout: float) -> None:
        self._redis = Redis.from_url(
            url,
            max_connections=connections,
            socket_connect_timeout=timeout,
            socket_timeout=timeout,
            retry=Retry(NoBackoff(), 0),
            retry_on_timeout=False,
        )
        self._script = self._redis.register_script(_SCRIPT)

    def check(self, keys: list[tuple[str, int]], window: int, cooldown: int) -> int:
        try:
            res = int(
                self._script(
                    keys=[f"ledgerpilot:v1:admission:{{rate}}:{key}" for key, _ in keys],
                    args=[window * 1000, cooldown * 1000, *[limit for _, limit in keys]],
                )
            )
            from ledgerpilot.observability.metrics import record_redis_status
            record_redis_status("admission", True)
            return res
        except RedisError:
            from ledgerpilot.observability.metrics import record_redis_error, record_redis_status
            record_redis_status("admission", False)
            record_redis_error("admission")
            emit("rate_limiter.unavailable", code="ERR_DEPENDENCY")
            raise DomainError(
                "ERR_DEPENDENCY", "Request protection is temporarily unavailable.", 503
            ) from None

    @property
    def client(self) -> Redis:
        return self._redis

    def close(self) -> None:
        self._redis.close()
