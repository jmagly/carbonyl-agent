# carbonyl-agent — Performance Benchmarks

Tracks performance baselines for key operations so we can detect regressions across releases. Per #22.

## Running

```bash
pip install -e ".[dev,bench]"
pytest tests/benchmarks/ --benchmark-only
```

For a stable baseline run:

```bash
pytest tests/benchmarks/ --benchmark-only \
    --benchmark-min-rounds=20 \
    --benchmark-warmup=on \
    --benchmark-save=baseline-<version>
```

## Scope

| Layer | Bench | Needs binary? |
|---|---|---|
| Text extraction | `extract_text` over synthetic pyte screen (50 / 200 rows) | No |
| Render-settle | `_render_settle_loop` immediate-settle case | No |
| Daemon RPC | `page_text` / `url` round-trip over Unix socket loopback | No |
| PTY throughput | Real Carbonyl bytes/sec read | **Yes** (#15) |
| `drain()` wall-clock | Real-page render-settle latency | **Yes** (#15) |
| `page_text()` on real screen | End-to-end pyte feed + extract | **Yes** (#15) |

The first three run on every CI; the last three land alongside #15 (E2E real-binary tests).

## Baseline (commit `c874797`, 2026-05-03)

Measured on a Linux 6.17 / Python 3.12 development host (no warmup, default 5-round minimum). Numbers are wall-clock per-call median.

| Bench | Median | Notes |
|---|---|---|
| `daemon_page_text_rpc` | ~37 µs | Unix socket round-trip + JSON encode/decode |
| `daemon_url_rpc` | ~35 µs | Smaller payload — overhead floor |
| `extract_text[50]` | ~1.8 ms | 50-row screen, 220 cols |
| `extract_text[200]` | ~5.9 ms | 200-row screen — scales ~linearly |
| `render_settle_loop_immediate` | ~10 ms | Dominated by 10× 1 ms poll intervals before settle window closes |

These are **not** the production environment — re-baseline on the CI runner once the bench job lands in `.gitea/workflows/`. Use the host-stamped JSON output (`.benchmarks/Linux-CPython-3.12-64bit/`) as the source of truth, not this table.

## Regression Policy

- **Advisory** (per #22 acceptance): a >20% degradation against the most recent committed baseline emits a CI warning, not a failure.
- Hard regressions (>50%) should block a release until investigated — typically a sign that a hot path lost a fast-path optimization or grew a sync I/O call.
- The `_render_settle_loop` and `extract_text` benches are the most stable signals; daemon RPC fluctuates with kernel scheduler noise.

## Adding a Bench

1. Drop a new `test_<name>` function into `tests/benchmarks/test_micro.py` (or a new file under `tests/benchmarks/`).
2. Take a `benchmark` fixture argument (provided by `pytest-benchmark`).
3. Call `benchmark(callable_under_test)` once — pytest-benchmark handles round count, warmup, and statistics.
4. If the bench needs a real binary, gate it with `@pytest.mark.requires_binary` and document in the table above.

## References

- Issue: `roctinam/carbonyl-agent#22`
- NFR: `.aiwg/requirements/nfr-register.md` — NFR-PERF
- pytest-benchmark docs: https://pytest-benchmark.readthedocs.io/
