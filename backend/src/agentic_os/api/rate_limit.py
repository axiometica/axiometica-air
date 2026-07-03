"""
Dependency-based rate limiting (Fix 7).

Uses the `limits` library (a slowapi dependency) for Redis-backed counting so
limits are shared across all uvicorn workers.

Usage:
    from agentic_os.api.rate_limit import RateLimit

    @router.post("/auth/login")
    def login(request: Request, ..., _rl: None = Depends(RateLimit(10, 60))):
        ...
"""

import logging
import os

from fastapi import HTTPException, Request

logger = logging.getLogger(__name__)


class RateLimit:
    """
    FastAPI dependency that enforces a sliding-window rate limit per IP.

    Parameters
    ----------
    times:   max number of requests allowed in the window
    seconds: window size in seconds
    """

    def __init__(self, times: int, seconds: int):
        self.times = times
        self.seconds = seconds
        self._limiter = None
        self._limit = None

    def _get_limiter(self) -> tuple:
        """Lazy-init so tests that never hit Redis don't fail at import time."""
        if self._limiter is None:
            try:
                from limits import parse
                from limits.storage import storage_from_string
                from limits.strategies import FixedWindowRateLimiter

                redis_url = os.getenv("REDIS_URL", "redis://redis:6379")
                storage = storage_from_string(redis_url)
                self._limiter = FixedWindowRateLimiter(storage)
                self._limit = parse(f"{self.times}/{self.seconds}seconds")
            except Exception as exc:
                logger.warning("[RateLimit] Could not init Redis limiter: %s", exc)
                self._limiter = "unavailable"  # sentinel so we don't retry every request
        return self._limiter, self._limit

    async def __call__(self, request: Request) -> None:
        limiter, limit = self._get_limiter()
        if limiter == "unavailable" or limiter is None:
            return  # fail-open if Redis is unreachable

        try:
            client_ip = request.client.host if request.client else "unknown"
            route = request.url.path
            key = f"{route}:{client_ip}"
            if not limiter.hit(limit, key):
                raise HTTPException(
                    status_code=429,
                    detail=f"Too many requests — limit is {self.times} per {self.seconds}s. Please slow down.",
                    headers={"Retry-After": str(self.seconds)},
                )
        except HTTPException:
            raise
        except Exception as exc:
            # Fail-open: never block a legitimate request due to Redis issues
            logger.warning("[RateLimit] Check failed (fail-open): %s", exc)
