"""Rate limiting and progressive cooldown management for identity email verification.

Enforces:
- Maximum 3 verification emails within any 24-hour window.
- Progressive cooldown gap between requests:
  - 1st request -> Gap of 15 minutes before 2nd request allowed.
  - 2nd request -> Gap of 30 minutes before 3rd request allowed.
  - 3rd request -> Cooldown of 1 hour (and locked until 24-hour window expires).
- Dual-tier persistence: Redis caching for fast distributed checks with PostgreSQL durability.
"""

import json
import logging
import math
from datetime import datetime, timedelta

from redis import Redis
from redis.exceptions import RedisError

from ledgerpilot.config import Settings
from ledgerpilot.identity.models import User
from ledgerpilot.shared.errors import DomainError

logger = logging.getLogger("ledgerpilot.identity.verification_limiter")

_REDIS_CLIENT: Redis | None = None


def get_redis_client(settings: Settings) -> Redis | None:
    global _REDIS_CLIENT
    if _REDIS_CLIENT is not None:
        return _REDIS_CLIENT
    if settings.rate_redis_url:
        try:
            _REDIS_CLIENT = Redis.from_url(
                settings.rate_redis_url.get_secret_value(),
                decode_responses=True,
                socket_timeout=settings.rate_redis_timeout_seconds,
            )
            return _REDIS_CLIENT
        except Exception as exc:
            logger.warning("Failed to initialize Redis client for verification limiter: %s", exc)
    return None


def evaluate_verification_cooldown(
    settings: Settings,
    user: User,
    email: str,
    instant: datetime,
) -> None:
    """Evaluate and update rate-limiting and cooldown gaps for verification email requests.

    Raises DomainError with HTTP 429 and retry_after if in cooldown or over limit.
    """
    redis_client = get_redis_client(settings)
    redis_key = f"identity:verify_gap:{email.lower().strip()}"

    # 1. Attempt reading from Redis cache first
    cached_state: dict[str, float] | None = None
    if redis_client:
        try:
            raw = redis_client.get(redis_key)
            if raw and isinstance(raw, str):
                cached_state = json.loads(raw)
        except (RedisError, json.JSONDecodeError) as exc:
            logger.debug("Redis verification cache read failed (%s); using database state", exc)

    # 2. Reconcile Redis and database state
    attempts = user.verification_attempts or 0
    started_at = user.verification_started_at
    blocked_until = user.verification_blocked_until

    if cached_state:
        c_attempts = int(cached_state.get("attempts", 0))
        c_started = datetime.fromtimestamp(cached_state["started_at"], tz=instant.tzinfo)
        c_blocked = (
            datetime.fromtimestamp(cached_state["blocked_until"], tz=instant.tzinfo)
            if cached_state.get("blocked_until")
            else None
        )
        if c_started > (started_at or c_started):
            attempts = c_attempts
            started_at = c_started
            blocked_until = c_blocked

    # 3. Check 24-hour daily window expiration
    daily_window = timedelta(hours=24)
    if not started_at or started_at <= instant - daily_window:
        attempts = 0
        started_at = instant
        blocked_until = None

    # 4. Enforce daily limit (Max 3 emails per 24 hours)
    if attempts >= settings.verification_daily_limit:
        window_end = started_at + daily_window
        wait = max(1, math.ceil((window_end - instant).total_seconds()))
        raise DomainError(
            "ERR_VERIFICATION_LIMIT",
            "Maximum 3 verification emails allowed within 24 hours. "
            "Please try again later, or use the newest code already sent.",
            429,
            retry_after=wait,
        )

    # 5. Enforce progressive cooldown gap
    if blocked_until and blocked_until > instant:
        wait = max(1, math.ceil((blocked_until - instant).total_seconds()))
        wait_mins = max(1, math.ceil(wait / 60))
        raise DomainError(
            "ERR_VERIFICATION_COOLDOWN",
            f"Please wait {wait_mins} minute{'s' if wait_mins > 1 else ''} "
            "before requesting another verification email.",
            429,
            retry_after=wait,
        )

    # 6. Request is permitted: advance attempt counter and calculate next cooldown gap
    new_attempts = attempts + 1
    if new_attempts == 1:
        gap_seconds = settings.verification_gap_1_seconds
    elif new_attempts == 2:
        gap_seconds = settings.verification_gap_2_seconds
    else:
        gap_seconds = settings.verification_gap_3_seconds

    next_blocked = instant + timedelta(seconds=gap_seconds)
    if new_attempts >= settings.verification_daily_limit:
        # Cap at end of 24-hour window
        next_blocked = max(next_blocked, started_at + daily_window)

    # 7. Update database model
    user.verification_attempts = new_attempts
    user.verification_started_at = started_at
    user.verification_blocked_until = next_blocked

    # 8. Sync state to Redis with 24-hour TTL
    if redis_client:
        try:
            remaining_window = max(60, int((started_at + daily_window - instant).total_seconds()))
            payload = json.dumps(
                {
                    "attempts": new_attempts,
                    "started_at": started_at.timestamp(),
                    "blocked_until": next_blocked.timestamp(),
                }
            )
            redis_client.set(redis_key, payload, ex=remaining_window)
        except RedisError as exc:
            logger.debug("Redis verification cache write failed (%s)", exc)
