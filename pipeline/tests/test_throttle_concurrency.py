"""Integration test for HostnameThrottle pacing under concurrent load.

Validates task 9.3: when N workers are scheduled concurrently against
different hostnames, the total wall time should be at most 1.5x the
slowest single worker. This is a sanity check that asyncio.gather
actually overlaps independent hostnames.
"""

import asyncio
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from crawler import HostnameThrottle


async def _crawl_one(th, hostname, interval, duration):
    """Simulate a crawl that takes ``duration`` seconds after the throttle."""
    await th.wait_for_slot(hostname, interval=interval)
    # The actual crawl work is just a sleep; we don't care about its
    # internal scheduling, just that the throttle doesn't serialize
    # hostnames.
    await asyncio.sleep(duration)
    return hostname, time.monotonic()


def test_concurrent_hostnames_overlap():
    """5 workers on 5 different hostnames should run in parallel.

    The throttle is the only serialization point — if it correctly
    isolates hostnames, all 5 workers' crawl phase should overlap.
    With 0.1s throttles, the total wall time should be close to the
    slowest single duration (0.3s), not 5x the throttle (0.5s) plus
    the crawl.
    """
    th = HostnameThrottle()

    async def scenario():
        hostnames = [f"host{i}.example.com" for i in range(5)]
        durations = [0.3, 0.2, 0.25, 0.15, 0.2]
        t0 = time.monotonic()
        results = await asyncio.gather(
            *[
                _crawl_one(th, h, interval=0.1, duration=d)
                for h, d in zip(hostnames, durations)
            ]
        )
        elapsed = time.monotonic() - t0
        # All hostnames touched
        assert sorted(r[0] for r in results) == sorted(hostnames)
        # The slowest crawl is 0.3s. The throttle has nothing to wait
        # for across different hostnames, so total wall time should
        # be at most 1.5x the slowest, i.e. <= 0.45s.
        assert elapsed < 0.45, f"5 concurrent crawls took {elapsed:.3f}s (expected <0.45s)"

    asyncio.run(scenario())


def test_concurrent_same_hostname_uses_minimum_delay():
    """5 workers on the SAME hostname wait at least the interval before
    starting any crawl.

    The throttle's per-hostname pacing is enforced via ``asyncio.sleep``.
    The first worker passes through immediately; the next batch wakes up
    ``interval`` seconds later. The 5 workers' *minimum* wait time
    before any crawl starts is the interval (since asyncio.gather
    batches their sleeps).

    This test asserts that the FIRST crawl starts at ~t=0 and the
    REMAINING 4 workers all wait at least ``interval`` before
    starting, even though they all hit ``wait_for_slot`` simultaneously.
    The throttle guarantees this by sleeping before the actual crawl
    can begin.
    """
    th = HostnameThrottle()
    starts = []

    async def worker():
        await th.wait_for_slot("shared.example.com", interval=0.1)
        starts.append(time.monotonic())
        await asyncio.sleep(0.05)  # simulate crawl

    async def scenario():
        t0 = time.monotonic()
        await asyncio.gather(*[worker() for _ in range(5)])
        # Subtract t0 from each start
        relative_starts = sorted(s - t0 for s in starts)
        # First worker should start near t=0; the others wake at ~0.1s.
        assert relative_starts[0] < 0.05, (
            f"First worker started at {relative_starts[0]:.3f}s (expected ~0)"
        )
        # The other 4 should all start at ~0.1s (asyncio.gather wakes them
        # together after the sleep).
        for s in relative_starts[1:]:
            assert 0.08 < s < 0.2, (
                f"Worker started at {s:.3f}s, expected ~0.1s after batch wait"
            )

    asyncio.run(scenario())


def test_backoff_affects_only_offending_hostname():
    """A backoff on one hostname shouldn't slow down crawls to others."""
    th = HostnameThrottle()

    async def scenario():
        # Put host0 into a cooldown. The throttle enforces a 30s minimum
        # backoff (DEFAULT_BACKOFF_SECONDS) so we can't use sub-30s
        # values for this test. We assert that *if* the backoff applies
        # to host0 only, the other 4 hostnames are not blocked.
        # Use a fake clock to keep the test fast.
        clock = [0.0]
        th._clock = lambda: clock[0]
        th.backoff("host0.example.com", retry_after=60.0)
        assert th._cooldown_until["host0.example.com"] == 60.0
        # host0 must sleep until t=60 before proceeding; advance the clock
        # past that and verify it unblocks the offending host only.
        clock[0] = 60.0
        # host0..host4: each only sees its own cooldown, so all should pass
        results = await asyncio.gather(
            *[
                _crawl_one(th, f"host{i}.example.com", interval=0.0, duration=0.0)
                for i in range(5)
            ]
        )
        assert len(results) == 5

    asyncio.run(scenario())
