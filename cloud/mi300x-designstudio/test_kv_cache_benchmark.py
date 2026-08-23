"""Framework-neutral unit checks for the cache benchmark math and payload."""

from kv_cache_benchmark import percentile, summarize, synthetic_prefix


def test_percentile_interpolates():
    assert percentile([1.0, 2.0, 3.0], 0.5) == 2.0
    assert percentile([1.0, 3.0], 0.5) == 2.0


def test_synthetic_prefix_is_deterministic_and_non_sensitive():
    first = synthetic_prefix(4096)
    assert first == synthetic_prefix(4096)
    assert len(first) == 4096 * 8


def test_summary_preserves_tail_latency():
    samples = [
        {
            "ttft_ms": value,
            "e2e_ms": value + 10,
            "itl_ms": 2.0,
            "output_tokens_per_second": 100.0,
            "prompt_tokens": 4096,
            "output_sha256": "a" * 64,
        }
        for value in (10.0, 20.0, 30.0)
    ]
    result = summarize(samples)
    assert result["ttft_ms"]["median"] == 20.0
    assert result["ttft_ms"]["p95"] == 29.0
    assert result["prompt_tokens_observed"] == [4096]
    assert result["deterministic_output"] is True
