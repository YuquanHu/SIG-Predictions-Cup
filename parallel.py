"""Run independent calls at the same time (the work is waiting on the network)."""
from concurrent.futures import ThreadPoolExecutor


def run_all(calls, workers=8):
    """Run zero-argument callables concurrently and return their results in order. If any raises, the others
    still finish, then the first exception is re-raised."""
    calls = list(calls)
    if not calls:
        return []
    with ThreadPoolExecutor(max_workers=min(workers, len(calls))) as pool:
        futures = [pool.submit(call) for call in calls]
    return [f.result() for f in futures]
