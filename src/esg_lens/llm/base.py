"""Provider-agnostic interface. Every agent talks to this, never to a vendor SDK directly.

Cross-cutting concerns live here once, for every provider:
- retries with exponential backoff on rate limits (429), server errors (5xx), timeouts and connection drops;
- thread-safe usage accounting (the orchestrator runs indicators in parallel threads);
- robust JSON parsing of model output.
"""

from __future__ import annotations

import json
import logging
import random
import re
import sys
import threading
import time
from abc import ABC, abstractmethod
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass
from typing import TypeVar

T = TypeVar("T")

_TRANSIENT_NAMES = (
    "ratelimit",
    "timeout",
    "connection",
    "internalserver",
    "serviceunavailable",
    "resourceexhausted",
    "overloaded",
    "deadlineexceeded",
    "unavailable",
)


def is_transient(exc: BaseException) -> bool:
    """True for errors worth retrying: 408/409/429/5xx, timeouts, dropped connections."""
    status = getattr(exc, "status_code", None) or getattr(exc, "code", None)
    if isinstance(status, int) and (status in {408, 409, 429} or status >= 500):
        return True
    name = type(exc).__name__.lower()
    return any(t in name for t in _TRANSIENT_NAMES)


logger = logging.getLogger(__name__)
MAX_WAIT_S = 120.0  # beyond this, the quota is daily (or the service is down): fail with a clear message

_RETRY_IN = re.compile(r"retry in ([\d.]+)\s*s|retrydelay['\"]?\s*[:=]\s*['\"]?([\d.]+)s", re.IGNORECASE)


def retry_after(exc: BaseException) -> float | None:
    """Seconds the server asked us to wait (Retry-After header, or Google's retryDelay / 'retry in Xs')."""
    response = getattr(exc, "response", None)
    headers = getattr(response, "headers", None) or {}
    try:
        value = headers.get("retry-after") or headers.get("Retry-After")
        if value:
            return float(value)
    except (TypeError, ValueError):
        pass
    m = _RETRY_IN.search(str(exc))
    if m:
        return float(m.group(1) or m.group(2))
    return None


class RateLimiter:
    """Sliding one-minute window, shared by the parallel agent threads. `units` = requests or texts."""

    def __init__(self, per_minute: int, clock=time.monotonic, sleep=time.sleep):
        self.per_minute = per_minute
        self._events: deque[tuple[float, int]] = deque()
        self._lock = threading.Lock()
        self._clock, self._sleep = clock, sleep

    def acquire(self, units: int = 1) -> None:
        if self.per_minute <= 0:
            return
        units = min(units, self.per_minute)
        while True:
            with self._lock:
                now = self._clock()
                while self._events and now - self._events[0][0] >= 60:
                    self._events.popleft()
                used = sum(n for _, n in self._events)
                if used + units <= self.per_minute:
                    self._events.append((now, units))
                    return
                wait = 60 - (now - self._events[0][0]) + 0.05
            self._sleep(wait)


def _reason(exc: BaseException) -> str:
    status = getattr(exc, "status_code", None) or getattr(exc, "code", None)
    if status == 429 or "ratelimit" in type(exc).__name__.lower():
        return "rate limited"
    if isinstance(status, int) and status >= 500:
        return f"server error {status}"
    return f"temporary error ({type(exc).__name__})"


@dataclass
class Usage:
    calls: int = 0
    input_chars: int = 0
    output_chars: int = 0


class LLMProvider(ABC):
    name: str = "base"
    max_attempts: int = 6
    base_delay_s: float = 1.0

    def __init__(self) -> None:
        self.usage = Usage()
        self._lock = threading.Lock()
        self.request_limiter = RateLimiter(0)
        self.embed_limiter = RateLimiter(0)

    def set_quotas(self, requests_per_minute: int = 0, embed_per_minute: int = 0) -> LLMProvider:
        self.request_limiter = RateLimiter(requests_per_minute)
        self.embed_limiter = RateLimiter(embed_per_minute)
        return self

    @abstractmethod
    def _complete(self, system: str, user: str) -> str:
        """Return the raw text answer of the model."""

    @abstractmethod
    def _embed(self, texts: list[str]) -> list[list[float]]:
        """Return one embedding vector per input text."""

    def _with_retry(self, fn: Callable[[], T]) -> T:
        for attempt in range(1, self.max_attempts + 1):
            try:
                return fn()
            except Exception as e:
                if attempt == self.max_attempts or not is_transient(e):
                    raise
                asked = retry_after(e)
                if asked is not None and asked > MAX_WAIT_S:
                    raise RuntimeError(
                        f"{self.name}: quota exhausted, the service asks to wait {asked:.0f} s "
                        "(daily limit?). Progress is saved: run the same command again later."
                    ) from e
                delay = self.base_delay_s * 2 ** (attempt - 1)
                if asked is not None:
                    delay = max(delay, asked + 1)
                delay += random.uniform(0, delay / 2)  # jitter, so parallel threads do not retry in sync
                print(
                    f"  {self.name}: {_reason(e)}, waiting {delay:.0f} s then retrying...",
                    file=sys.stderr,
                    flush=True,
                )
                time.sleep(delay)
        raise RuntimeError("unreachable")  # pragma: no cover

    def embed(self, texts: list[str]) -> list[list[float]]:
        self.embed_limiter.acquire(len(texts))
        vectors = self._with_retry(lambda: self._embed(texts))
        if len(vectors) != len(texts):
            raise ValueError(f"{self.name}: got {len(vectors)} embeddings for {len(texts)} texts")
        return vectors

    def complete(self, system: str, user: str) -> str:
        self.request_limiter.acquire()
        out = self._with_retry(lambda: self._complete(system, user))
        with self._lock:
            self.usage.calls += 1
            self.usage.input_chars += len(system) + len(user)
            self.usage.output_chars += len(out)
        return out

    def complete_json(self, system: str, user: str) -> dict:
        """Ask for JSON and parse it robustly (models sometimes wrap it in prose or code fences)."""
        raw = self.complete(system + "\nAnswer with a single JSON object and nothing else.", user)
        return parse_json_object(raw)


def rejects_temperature(exc: BaseException) -> bool:
    """Reasoning models (e.g. the GPT-5 family) refuse a custom temperature: detect it to retry without."""
    text = str(exc).lower()
    hints = ("unsupported", "not support", "does not", "only")
    return "temperature" in text and any(w in text for w in hints)


_FENCE = re.compile(r"```(?:json)?\s*(.*?)```", re.DOTALL)


def parse_json_object(raw: str) -> dict:
    m = _FENCE.search(raw)
    text = m.group(1) if m else raw
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end == -1:
        raise ValueError(f"no JSON object in model output: {raw[:200]!r}")
    return json.loads(text[start : end + 1])
