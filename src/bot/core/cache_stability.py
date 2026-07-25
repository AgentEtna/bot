"""Observe whether phi's prompt cache actually pays off.

phi's caching design (agent.py) is deliberate: tool definitions and the static
instruction base cache for 1h, message history for 5m, and `memoize_per_run`
exists so a network-fetched context block can't shift the cached prefix
mid-run. None of that was measured — a regression in any of it is invisible
from the outside, because the run still succeeds, just at full price.

This reads the provider's own verdict (`cache_read_tokens` /
`cache_write_tokens` on each response) rather than guessing from the request.
Two things it can see that nothing else can:

- **mid-run collapse** — history is append-only within a run, so each request
  should read back at least what the previous one cached. A large drop means
  the cacheable prefix moved (or the provider cache expired under it).
- **cross-run carry** — whether a run's *first* request reads back the 1h
  tool+instruction prefix left by the previous run, which is the whole
  premise of the 1h TTL.

Approach borrowed from pydantic-ai-harness's CacheStabilityMonitor, which
needs pydantic-ai 2.x capabilities; on 1.x the equivalent seam is a
`WrapperModel`.
"""

import json
import logging
from collections import deque
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from opentelemetry import trace
from pydantic_ai._run_context import RunContext
from pydantic_ai.messages import ModelMessage, ModelResponse
from pydantic_ai.models import (
    Model,
    ModelRequestParameters,
    StreamedResponse,
)
from pydantic_ai.models.wrapper import WrapperModel
from pydantic_ai.settings import ModelSettings
from pydantic_ai.usage import RequestUsage

from bot.config import settings

logger = logging.getLogger("bot.cache")

CACHE_FILE = Path("/data/cache_stability.json")

CACHE_TTLS = {
    "tool_definitions": "1h",
    "instructions": "1h",
    "messages": "5m",
}
"""The caching strategy itself — one source, read by both the agent (which
turns it into `AnthropicModelSettings`) and the cockpit (which reports on
it). Changing a TTL here changes what phi does *and* what the panel says."""

PRICE = {
    "uncached": 1.0,
    "read": 0.1,
    "5m": 1.25,
    "1h": 2.0,
}
"""Anthropic input-token prices as multiples of the base input rate."""

WRITE_PRICE = PRICE[CACHE_TTLS["tool_definitions"]]
"""What a cache write costs. The provider reports one `cache_write_tokens`
total without splitting 5m from 1h (the split lives in a nested
`cache_creation` object pydantic-ai drops), so cost is priced at the
longest TTL in use — the conservative read. If caching wins at this price
it wins at any."""

MAX_RUNS = 60
"""Rolling window of runs kept in memory and on disk."""

COLLAPSE_RATIO = 0.5
"""Warn when a request reads back less than this fraction of the run's
established prefix. Conservative so a partial miss doesn't fire."""

MIN_PREFIX_TOKENS = 1024
"""Anthropic's minimum cacheable size — below it `cache_read_tokens` is noise."""

CACHE_TTL_SECONDS = 300
"""Assumed message-cache TTL. Message-only: a gap longer than this makes a
collapse ambiguous between "prefix moved" and "provider cache expired"."""


@dataclass
class RequestSample:
    """One model request's cache verdict."""

    at: datetime
    model: str
    input_tokens: int
    cache_read: int
    cache_write: int
    gap_seconds: float | None
    collapsed: bool = False
    maybe_expiry: bool = False

    @property
    def billed_prefix(self) -> int:
        """Every input token this request paid for, cached or not."""
        return self.input_tokens + self.cache_read + self.cache_write


def cost_with_cache(read: int, write: int, uncached: int) -> float:
    """Input cost in base-rate token equivalents, as billed."""
    return read * PRICE["read"] + write * WRITE_PRICE + uncached * PRICE["uncached"]


def cost_without_cache(read: int, write: int, uncached: int) -> float:
    """What the same tokens would have cost with caching switched off."""
    return float(read + write + uncached)


@dataclass
class RunRecord:
    """Cache behavior across one `agent.run()`."""

    label: str
    started_at: datetime
    trace_id: str | None = None
    samples: list[RequestSample] = field(default_factory=list)

    @property
    def requests(self) -> int:
        return len(self.samples)

    @property
    def cache_read(self) -> int:
        return sum(s.cache_read for s in self.samples)

    @property
    def cache_write(self) -> int:
        return sum(s.cache_write for s in self.samples)

    @property
    def uncached(self) -> int:
        return sum(s.input_tokens for s in self.samples)

    @property
    def collapses(self) -> int:
        return sum(1 for s in self.samples if s.collapsed)

    @property
    def hit_rate(self) -> float:
        """Share of input tokens served from cache."""
        total = self.cache_read + self.cache_write + self.uncached
        return self.cache_read / total if total else 0.0

    @property
    def saved(self) -> float:
        """Fraction of the input bill caching removed. Negative means the
        write premium cost more than the reads saved."""
        full = cost_without_cache(self.cache_read, self.cache_write, self.uncached)
        if not full:
            return 0.0
        billed = cost_with_cache(self.cache_read, self.cache_write, self.uncached)
        return (full - billed) / full

    @property
    def warm_start(self) -> bool:
        """Did this run's first request reuse a prefix an earlier run left?

        This is the 1h tool+instruction TTL doing its job across runs — the
        entire reason for choosing 1h over 5m. False on a cold start or when
        runs are spaced further apart than the TTL, which is expected; false
        on runs minutes apart is not.
        """
        return bool(self.samples) and self.samples[0].cache_read >= MIN_PREFIX_TOKENS

    def as_dict(self) -> dict[str, Any]:
        return {
            "label": self.label,
            "started_at": self.started_at.isoformat(),
            "trace_id": self.trace_id,
            "trace_url": (
                f"{settings.logfire.ui_url}/?q=trace_id%3D%27{self.trace_id}%27"
                if self.trace_id and settings.logfire.ui_url
                else None
            ),
            "requests": self.requests,
            "cache_read": self.cache_read,
            "cache_write": self.cache_write,
            "uncached": self.uncached,
            "hit_rate": round(self.hit_rate, 4),
            "saved": round(self.saved, 4),
            "collapses": self.collapses,
            "warm_start": self.warm_start,
            "samples": [
                {
                    "at": s.at.isoformat(),
                    "model": s.model,
                    "input_tokens": s.input_tokens,
                    "cache_read": s.cache_read,
                    "cache_write": s.cache_write,
                    "gap_seconds": round(s.gap_seconds, 1)
                    if s.gap_seconds is not None
                    else None,
                    "collapsed": s.collapsed,
                    "maybe_expiry": s.maybe_expiry,
                }
                for s in self.samples
            ],
        }


class CacheMonitor:
    """Tracks cache read-back across requests, per run.

    A monitor is a process-global recorder; `begin_run` / `end_run` bracket
    one `agent.run()` so a collapse is judged against that run's own
    established prefix rather than across unrelated runs.
    """

    def __init__(self) -> None:
        self.runs: deque[RunRecord] = deque(maxlen=MAX_RUNS)
        self._current: RunRecord | None = None
        # high-water prefix per (provider, model) within the current run
        self._marks: dict[str, int] = {}
        # latched keys: already warned about this collapse, stay quiet until
        # a healthy read-back re-stabilizes
        self._latched: set[str] = set()
        # last request time per key, for the expiry-vs-moved-prefix hint
        self._last_seen: dict[str, datetime] = {}
        self._load()

    def begin_run(self, label: str) -> None:
        self._current = RunRecord(label=label, started_at=datetime.now(UTC))
        self._marks.clear()
        self._latched.clear()

    def end_run(self) -> None:
        if self._current is None:
            return
        record = self._current
        self._current = None
        if not record.samples:
            return
        self.runs.append(record)
        logger.info(
            f"cache [{record.label}]: {record.saved:.0%} off the input bill "
            f"({record.hit_rate:.0%} of "
            f"{record.cache_read + record.cache_write + record.uncached} tokens reused) "
            f"over {record.requests} requests"
            f"{', warm start' if record.warm_start else ', cold start'}"
            f"{f', {record.collapses} collapse(s)' if record.collapses else ''}"
        )
        self._save()

    def observe(self, usage: RequestUsage, model: str, provider: str) -> None:
        """Record one response's cache verdict and judge collapse."""
        now = datetime.now(UTC)
        key = f"{provider}:{model}"
        gap = None
        if last := self._last_seen.get(key):
            gap = (now - last).total_seconds()
        self._last_seen[key] = now

        read = usage.cache_read_tokens
        write = usage.cache_write_tokens
        established = self._marks.get(key, 0)

        collapsed = (
            established >= MIN_PREFIX_TOKENS
            and read < established * COLLAPSE_RATIO
            and key not in self._latched
        )
        maybe_expiry = bool(collapsed and gap is not None and gap > CACHE_TTL_SECONDS)

        if collapsed:
            self._latched.add(key)
            gap_note = (
                f" after a {gap:.0f}s gap (may be cache expiry rather than a moved prefix)"
                if maybe_expiry
                else ""
            )
            logger.warning(
                f"prompt cache collapsed on {key}: read back {read} tokens against an "
                f"established prefix of {established}{gap_note}"
            )
        elif read >= established * COLLAPSE_RATIO:
            self._latched.discard(key)

        self._marks[key] = max(established, read + write)

        if self._current is not None:
            # capture the trace id HERE, not in begin_run — begin_run happens
            # before agent.run(), where no span is active yet and the context
            # is invalid. this call sits inside the model request, so the
            # agent-run trace is guaranteed live.
            if self._current.trace_id is None:
                ctx = trace.get_current_span().get_span_context()
                if ctx.is_valid:
                    self._current.trace_id = format(ctx.trace_id, "032x")
            self._current.samples.append(
                RequestSample(
                    at=now,
                    model=model,
                    input_tokens=usage.input_tokens,
                    cache_read=read,
                    cache_write=write,
                    gap_seconds=gap,
                    collapsed=collapsed,
                    maybe_expiry=maybe_expiry,
                )
            )

    def summary(self) -> dict[str, Any]:
        """Rolling view for the operator cockpit."""
        runs = list(self.runs)
        read = sum(r.cache_read for r in runs)
        write = sum(r.cache_write for r in runs)
        uncached = sum(r.uncached for r in runs)
        total = read + write + uncached
        billed = cost_with_cache(read, write, uncached)
        return {
            # the strategy, read from the same dict the agent configures
            # from — so this can never describe a policy phi isn't running
            "strategy": CACHE_TTLS,
            "prices": {"read": PRICE["read"], "write": WRITE_PRICE, "uncached": 1.0},
            "window_runs": len(runs),
            "cache_read": read,
            "cache_write": write,
            "uncached": uncached,
            "hit_rate": round(read / total, 4) if total else 0.0,
            # the verdict: what caching actually removed from the input bill
            "billed_tokens": round(billed),
            "uncached_cost_tokens": total,
            "saved": round((total - billed) / total, 4) if total else 0.0,
            "collapses": sum(r.collapses for r in runs),
            "warm_starts": sum(1 for r in runs if r.warm_start),
            "runs": [r.as_dict() for r in reversed(runs)],
        }

    def _save(self) -> None:
        if not CACHE_FILE.parent.exists():
            return
        try:
            CACHE_FILE.write_text(
                json.dumps({"runs": [r.as_dict() for r in self.runs]})
            )
        except Exception as e:
            logger.warning(f"failed to save cache stability: {e}")

    def _load(self) -> None:
        if not CACHE_FILE.exists():
            return
        try:
            data = json.loads(CACHE_FILE.read_text())
            for entry in data.get("runs", [])[-MAX_RUNS:]:
                record = RunRecord(
                    label=entry["label"],
                    started_at=datetime.fromisoformat(entry["started_at"]),
                    trace_id=entry.get("trace_id"),
                )
                for s in entry.get("samples", []):
                    record.samples.append(
                        RequestSample(
                            at=datetime.fromisoformat(s["at"]),
                            model=s["model"],
                            input_tokens=s["input_tokens"],
                            cache_read=s["cache_read"],
                            cache_write=s["cache_write"],
                            gap_seconds=s.get("gap_seconds"),
                            collapsed=s.get("collapsed", False),
                            maybe_expiry=s.get("maybe_expiry", False),
                        )
                    )
                self.runs.append(record)
        except Exception as e:
            logger.warning(f"failed to load cache stability: {e}")


cache_monitor = CacheMonitor()


class CacheObservingModel(WrapperModel):
    """Reports every response's cache verdict to a `CacheMonitor`.

    pydantic-ai 1.x has no after-model-request hook, so the model wrapper is
    the seam. It is observational — it never alters the request or response.
    """

    def __init__(self, wrapped: Model | str, monitor: CacheMonitor | None = None):
        super().__init__(wrapped)
        self.monitor = monitor or cache_monitor

    def _observe(self, usage: RequestUsage) -> None:
        try:
            self.monitor.observe(
                usage, model=self.wrapped.model_name, provider=self.wrapped.system
            )
        except Exception as e:
            logger.warning(f"cache observation failed: {e}")

    async def request(
        self,
        messages: list[ModelMessage],
        model_settings: ModelSettings | None,
        model_request_parameters: ModelRequestParameters,
    ) -> ModelResponse:
        response = await super().request(
            messages, model_settings, model_request_parameters
        )
        self._observe(response.usage)
        return response

    @asynccontextmanager
    async def request_stream(
        self,
        messages: list[ModelMessage],
        model_settings: ModelSettings | None,
        model_request_parameters: ModelRequestParameters,
        run_context: RunContext[Any] | None = None,
    ) -> AsyncIterator[StreamedResponse]:
        async with super().request_stream(
            messages, model_settings, model_request_parameters, run_context
        ) as stream:
            yield stream
            self._observe(stream.usage())
