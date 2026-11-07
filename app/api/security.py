"""API-key auth + per-client sliding-window rate limiting.

Auth is disabled when API_KEYS is empty (local dev). Rate limiting is in-memory
(single replica); put an API gateway / Redis limiter in front for multi-replica.
"""

from __future__ import annotations

import threading
import time
from collections import defaultdict, deque

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import APIKeyHeader

from app.config import get_settings

_api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)


def require_api_key(api_key: str | None = Depends(_api_key_header)) -> str:
    keys = get_settings().api_key_set
    if not keys:
        return "anonymous"
    if not api_key or api_key not in keys:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid or missing X-API-Key")
    return api_key


class RateLimiter:
    def __init__(self) -> None:
        self._hits: dict[str, deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()

    def check(self, client: str, limit: int, window_s: float = 60.0) -> None:
        now = time.monotonic()
        with self._lock:
            q = self._hits[client]
            while q and now - q[0] > window_s:
                q.popleft()
            if len(q) >= limit:
                retry = int(window_s - (now - q[0])) + 1
                raise HTTPException(
                    status.HTTP_429_TOO_MANY_REQUESTS,
                    "Rate limit exceeded",
                    headers={"Retry-After": str(retry)},
                )
            q.append(now)


limiter = RateLimiter()


def rate_limited(request: Request, principal: str = Depends(require_api_key)) -> str:
    client = principal if principal != "anonymous" else (request.client.host if request.client else "unknown")
    limiter.check(client, get_settings().rate_limit_per_minute)
    return principal
