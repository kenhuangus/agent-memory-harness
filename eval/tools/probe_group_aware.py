"""Verify the group-aware draw picks memory-carrying groups on the real datasets."""
from collections import Counter

from memeval.agent import _select_group_aware
from memeval.loaders import get_loader
from memeval.schema import Benchmark
from memeval.claudecode.run_bench import DEFAULT_FLOORS

for name in ("swe_contextbench", "swe_bench_cl"):
    tasks = get_loader(Benchmark.from_str(name)).load(None, limit=None)
    limit = DEFAULT_FLOORS[name]
    flat = tasks[:limit]
    grp = _select_group_aware(tasks, limit)

    def stats(sel):
        gc = Counter(t.group_id for t in sel)
        singles = sum(1 for g in gc.values() if g == 1)
        with_priors = sum(1 for t in sel if t.sessions)  # has retrievable context
        return f"groups={len(gc)} singleton_groups={singles} entries_with_priors={with_priors}/{len(sel)}"

    print(f"\n## {name} (limit={limit})")
    print(f"  flat : {stats(flat)}")
    print(f"  group: {stats(grp)}")
