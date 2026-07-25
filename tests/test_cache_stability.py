"""Cache-stability monitoring: collapse detection and run accounting."""

from pydantic_ai.usage import RequestUsage

from bot.core.cache_stability import (
    MIN_PREFIX_TOKENS,
    CacheMonitor,
)


def usage(*, input_tokens: int = 0, read: int = 0, write: int = 0) -> RequestUsage:
    return RequestUsage(
        input_tokens=input_tokens, cache_read_tokens=read, cache_write_tokens=write
    )


def monitor() -> CacheMonitor:
    m = CacheMonitor.__new__(CacheMonitor)  # skip _load(): no disk in tests
    m.runs = __import__("collections").deque(maxlen=60)
    m._current = None
    m._marks = {}
    m._latched = set()
    m._last_seen = {}
    return m


def observe(m: CacheMonitor, **kw) -> None:
    m.observe(usage(**kw), model="claude-opus-5", provider="anthropic")


def test_healthy_run_records_no_collapse():
    m = monitor()
    m.begin_run("batch processing")
    observe(m, input_tokens=800, write=12_000)
    observe(m, input_tokens=200, read=12_000)
    observe(m, input_tokens=150, read=12_200)
    m.end_run()

    (run,) = m.runs
    assert run.requests == 3
    assert run.collapses == 0
    assert run.cache_read == 24_200
    # the write is a real cost at a premium, so it stays in the denominator:
    # 24200 / (24200 read + 12000 written + 1150 uncached)
    assert round(run.hit_rate, 2) == 0.65


def test_collapse_detected_when_read_back_drops():
    m = monitor()
    m.begin_run("cycle")
    observe(m, input_tokens=500, write=20_000)
    observe(m, input_tokens=500, read=20_000)
    observe(m, input_tokens=20_000, read=0)  # prefix moved — nothing read back
    m.end_run()

    (run,) = m.runs
    assert run.collapses == 1
    assert run.samples[-1].collapsed


def test_collapse_warns_once_then_relatches_after_recovery():
    m = monitor()
    m.begin_run("cycle")
    observe(m, write=20_000)
    observe(m, read=20_000)
    observe(m, read=0)  # collapse
    observe(m, read=0)  # sustained — latched, not re-reported
    observe(m, read=20_000)  # healthy again, unlatches
    observe(m, read=0)  # a fresh collapse is reported
    m.end_run()

    (run,) = m.runs
    assert [s.collapsed for s in run.samples] == [
        False,
        False,
        True,
        False,
        False,
        True,
    ]


def test_small_prefix_never_judged_collapsed():
    """Below Anthropic's minimum cacheable size the read count is noise."""
    m = monitor()
    m.begin_run("bio rewrite")
    observe(m, write=MIN_PREFIX_TOKENS - 100)
    observe(m, read=0)
    m.end_run()

    (run,) = m.runs
    assert run.collapses == 0


def test_marks_are_per_model_so_a_switch_does_not_warn():
    m = monitor()
    m.begin_run("cycle")
    m.observe(usage(write=20_000), model="claude-opus-5", provider="anthropic")
    m.observe(usage(read=0), model="claude-haiku-4-5", provider="anthropic")
    m.end_run()

    (run,) = m.runs
    assert run.collapses == 0


def test_warm_start_reflects_first_request_read_back():
    """The 1h tool+instruction TTL bridging two runs is the thing it proves."""
    cold = monitor()
    cold.begin_run("cycle")
    observe(cold, input_tokens=14_000, write=14_000)
    cold.end_run()
    assert cold.runs[0].warm_start is False

    warm = monitor()
    warm.begin_run("cycle")
    observe(warm, input_tokens=300, read=14_000)
    warm.end_run()
    assert warm.runs[0].warm_start is True


def test_marks_reset_between_runs():
    """A new run must not be judged against the previous run's prefix."""
    m = monitor()
    m.begin_run("first")
    observe(m, write=20_000)
    observe(m, read=20_000)
    m.end_run()

    m.begin_run("second")
    observe(m, input_tokens=20_000, read=0)  # cold start, not a collapse
    m.end_run()

    assert m.runs[1].collapses == 0


def test_empty_run_is_not_recorded():
    m = monitor()
    m.begin_run("failed before any model request")
    m.end_run()
    assert not m.runs


def test_summary_aggregates_the_window():
    m = monitor()
    m.begin_run("a")
    observe(m, input_tokens=1_000, write=10_000)
    m.end_run()
    m.begin_run("b")
    observe(m, input_tokens=500, read=10_000)
    m.end_run()

    summary = m.summary()
    assert summary["window_runs"] == 2
    assert summary["cache_read"] == 10_000
    assert summary["cache_write"] == 10_000
    assert summary["uncached"] == 1_500
    assert summary["warm_starts"] == 1
    # newest run first, so the cockpit reads top-down
    assert [r["label"] for r in summary["runs"]] == ["b", "a"]


def test_observation_failure_does_not_break_the_run(monkeypatch):
    """The monitor is observational — it must never take a run down."""
    from bot.core.cache_stability import CacheObservingModel

    m = monitor()
    model = CacheObservingModel.__new__(CacheObservingModel)
    model.monitor = m

    def boom(*args, **kwargs):
        raise RuntimeError("telemetry exploded")

    monkeypatch.setattr(m, "observe", boom)
    monkeypatch.setattr(
        type(model), "wrapped", property(lambda self: _FakeModel()), raising=False
    )
    model._observe(usage(read=1))  # does not raise


class _FakeModel:
    model_name = "claude-opus-5"
    system = "anthropic"


def test_saving_is_measured_against_a_no_cache_bill():
    """The panel's headline: what caching removed from the input bill.

    Priced at the 1h write premium (2x) because the provider reports one
    write total without splitting 5m from 1h — the conservative read.
    """
    m = monitor()
    m.begin_run("batch processing")
    observe(m, input_tokens=10_000, read=80_000, write=10_000)
    m.end_run()

    (run,) = m.runs
    # no cache: 100k tokens at 1x. billed: 80k*0.1 + 10k*2 + 10k*1 = 38k
    assert round(run.saved, 3) == 0.62


def test_a_write_only_run_costs_more_than_no_cache_at_all():
    """Storing a prefix nothing reads back is a loss — the panel must be
    able to say so rather than always reporting a win."""
    m = monitor()
    m.begin_run("cold cycle")
    observe(m, input_tokens=1_000, write=40_000)
    m.end_run()

    (run,) = m.runs
    assert run.saved < 0


def test_summary_reports_the_live_strategy_not_a_copy():
    """The cockpit renders TTLs from this, so it can never describe a
    policy phi isn't running."""
    from bot.core.cache_stability import CACHE_TTLS

    m = monitor()
    m.begin_run("a")
    observe(m, input_tokens=100, read=5_000)
    m.end_run()

    summary = m.summary()
    assert summary["strategy"] == CACHE_TTLS
    assert summary["prices"]["read"] == 0.1


def test_run_carries_a_trace_link_when_a_span_is_active():
    from opentelemetry.sdk.trace import TracerProvider

    m = monitor()
    # a real recording span — logfire isn't configured under pytest, so its
    # spans have no valid context to read a trace id from
    with TracerProvider().get_tracer("test").start_as_current_span("agent run"):
        m.begin_run("cycle")
    observe(m, input_tokens=100, read=5_000)
    m.end_run()

    entry = m.summary()["runs"][0]
    assert entry["trace_id"] and len(entry["trace_id"]) == 32
    assert entry["trace_id"] in (entry["trace_url"] or "")


def test_no_trace_link_outside_a_span():
    m = monitor()
    m.begin_run("cycle")
    observe(m, input_tokens=100, read=5_000)
    m.end_run()

    assert m.summary()["runs"][0]["trace_url"] is None
