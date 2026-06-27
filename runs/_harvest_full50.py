#!/usr/bin/env python3
"""Final 6-arm harvest for the FULL sympy50 V5 run (claude + grok).

Run AFTER both overnight drivers finish:
  python runs/_harvest_full50.py

For each (solver, arm) prints: resolved/graded, cost. For the plugin arms it also
reports cookbook memories stored (count + a sample). Reads:
  - claude:  results/v<results-version>/SUMMARY-*.json   (latest by mtime)
  - grok:    runs/sympy50v5-grok/<arm>/results.json
Tolerant of missing/unfinished arms (prints PENDING).
"""
from __future__ import annotations
import glob, json, os, sqlite3
from pathlib import Path

R = Path("/mnt/c/Users/kenhu/agent-memory-harness")

# (label, solver, arm, results-version dir under results/ as "v"+rv) for claude
CLAUDE = [
    ("claude/base",    "sympy50v5-claude-base"),
    ("claude/builtin", "sympy50v5-claude-builtin"),
    ("claude/plugin",  "sympy50v5-claude-plugin"),
]
GROK = [
    ("grok/base",    "base"),
    ("grok/builtin", "builtin"),
    ("grok/plugin",  "plugin"),
]


def latest_summary(rv: str):
    d = R / "results" / ("v" + rv)
    files = sorted(d.glob("SUMMARY-*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
    return files[0] if files else None


def claude_row(label: str, rv: str):
    f = latest_summary(rv)
    if not f:
        return (label, "PENDING", "-", "-", None)
    d = json.loads(f.read_text())
    st = (d.get("stages") or [{}])[0]
    resolved = st.get("resolved")
    graded = st.get("graded_n")
    cost = st.get("cost_usd")
    mem_store = R / "results" / ("v" + rv) / "_memory" / ".cookbook-memory"
    return (label, f"{resolved}/{graded}", f"${cost:.4f}" if cost is not None else "-",
            str(f), mem_store)


def grok_row(label: str, arm: str):
    f = R / "runs" / "sympy50v5-grok" / arm / "results.json"
    if not f.exists():
        return (label, "PENDING", "-", "-", None)
    d = json.loads(f.read_text())
    resolved = d.get("resolved")
    graded = d.get("graded_so_far")
    # grok results carry no cost (OpenRouter only bills the daydream side); show n/a.
    mem_store = R / "runs" / "sympy50v5-grok" / arm / ".cookbook-memory"
    return (label, f"{resolved}/{graded}", "n/a", str(f), mem_store)


def cookbook_stats(store_dir):
    """Count memories + return one sample. Store is the cookbook RouterStore basedir;
    look for a sqlite db or jsonl under it. Tolerant — returns (count, sample|None)."""
    if not store_dir or not Path(store_dir).exists():
        return (None, None)
    p = Path(store_dir)
    # sqlite path(s)
    for db in p.rglob("*.db"):
        try:
            con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
            for (tbl,) in con.execute("SELECT name FROM sqlite_master WHERE type='table'"):
                try:
                    n = con.execute(f"SELECT COUNT(*) FROM {tbl}").fetchone()[0]
                    if n:
                        row = con.execute(f"SELECT * FROM {tbl} LIMIT 1").fetchone()
                        con.close()
                        return (n, str(row)[:200])
                except sqlite3.Error:
                    continue
            con.close()
        except sqlite3.Error:
            continue
    # jsonl fallback
    cnt, sample = 0, None
    for jf in p.rglob("*.jsonl"):
        for line in jf.read_text(errors="ignore").splitlines():
            line = line.strip()
            if line:
                cnt += 1
                if sample is None:
                    sample = line[:200]
    return (cnt or None, sample)


def main():
    rows = []
    plugin_stores = []
    for label, rv in CLAUDE:
        r = claude_row(label, rv)
        rows.append(r)
        if "plugin" in label:
            plugin_stores.append((label, r[4]))
    for label, arm in GROK:
        r = grok_row(label, arm)
        rows.append(r)
        if "plugin" in label:
            plugin_stores.append((label, r[4]))

    print("=" * 64)
    print("FULL sympy50 V5 — 6-arm comparison")
    print("=" * 64)
    print(f"{'arm':<16}{'resolved/graded':<18}{'cost':<12}source")
    print("-" * 64)
    for label, rg, cost, src, _ in rows:
        print(f"{label:<16}{rg:<18}{cost:<12}{src}")
    print()
    print("Plugin cookbook memories stored")
    print("-" * 64)
    for label, store in plugin_stores:
        n, sample = cookbook_stats(store)
        print(f"{label}: count={n}  store={store}")
        if sample:
            print(f"    sample: {sample}")


if __name__ == "__main__":
    main()
