#!/usr/bin/env python3
"""Report the effective memory-store profile selected by build_store()."""

from __future__ import annotations

import argparse
import os
import shutil
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping, Optional


ENV_FLAGS = (
    "MEMORY_PROFILE",
    "MEMEVAL_LOCAL_ANN",
    "MEMEVAL_ALLOW_MODEL_DOWNLOAD",
    "VOYAGE_API_KEY",
)
HASHING_EMBEDDER = "_HashingEmbedder"
LOCAL_EMBEDDER = "SentenceTransformersEmbedder"
VOYAGE_EMBEDDER = "VoyageEmbedder"
SQLITE_VEC = "sqlite_vec"
FAILURE_EXIT = 3
UNKNOWN = "unknown"
LOCAL_ANN_PROFILES = frozenset({"accuracy-local", "fusion-local"})


@dataclass(frozen=True)
class EffectiveProfile:
    """Facts introspected from the RouterStore returned by build_store()."""

    profile_name: Optional[str]
    vector_backend_class: Optional[str]
    embedder_class: Optional[str]
    vector_index: Optional[str]
    requested_vector_index: Optional[str]
    vector_index_status: Optional[str]
    notes: tuple[str, ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class Verdict:
    """Human-readable classification plus the process exit code it implies."""

    message: str
    exit_code: int = 0


def capture_env(environ: Optional[Mapping[str, str]] = None) -> dict[str, Optional[str]]:
    source = os.environ if environ is None else environ
    return {name: source.get(name) for name in ENV_FLAGS}


def classify_effective_profile(
    facts: EffectiveProfile,
    env: Mapping[str, Optional[str]],
    *,
    require_local_ann: bool = False,
) -> Verdict:
    """Classify effective profile facts into the diagnostic's one-line verdict."""

    memory_profile = (env.get("MEMORY_PROFILE") or "").strip().lower()
    local_ann_requested = (
        memory_profile in LOCAL_ANN_PROFILES
        or (not memory_profile and env.get("MEMEVAL_LOCAL_ANN") == "1")
    )
    actual_local_ann = (
        facts.profile_name in LOCAL_ANN_PROFILES
        and facts.embedder_class == LOCAL_EMBEDDER
        and facts.vector_index == SQLITE_VEC
    )

    if local_ann_requested and facts.embedder_class == HASHING_EMBEDDER:
        return Verdict(
            "REQUESTED local-ANN but FELL BACK to hashing (deps/model unavailable).",
            FAILURE_EXIT,
        )

    if (
        facts.requested_vector_index == SQLITE_VEC
        and facts.vector_index not in (SQLITE_VEC, None)
    ):
        return Verdict(
            "REQUESTED sqlite-vec ANN but FELL BACK to brute-force vector search.",
            FAILURE_EXIT,
        )

    if require_local_ann and not actual_local_ann:
        return Verdict(
            "REQUIRED local-ANN but effective store is "
            f"profile={_display(facts.profile_name)}, "
            f"embedder={_display(facts.embedder_class)}, "
            f"index={_display(facts.vector_index)}.",
            FAILURE_EXIT,
        )

    if actual_local_ann:
        return Verdict("local-ANN active (MiniLM + sqlite-vec).")

    if facts.profile_name == "accuracy" and facts.embedder_class == VOYAGE_EMBEDDER:
        if not env.get("VOYAGE_API_KEY"):
            return Verdict(
                "accuracy profile active with VoyageEmbedder, but VOYAGE_API_KEY is absent.",
                FAILURE_EXIT,
            )
        return Verdict("accuracy profile active (VoyageEmbedder).")

    if facts.embedder_class == HASHING_EMBEDDER:
        if facts.profile_name == "fusion":
            return Verdict("offline hashing default (fusion profile).")
        if facts.profile_name == "speed":
            return Verdict("offline hashing profile (speed profile).")
        return Verdict(
            "hashing embedder active under "
            f"profile={_display(facts.profile_name)}.",
            FAILURE_EXIT,
        )

    return Verdict(
        "effective profile has no known fallback mismatch "
        f"(profile={_display(facts.profile_name)}, "
        f"embedder={_display(facts.embedder_class)}, "
        f"index={_display(facts.vector_index)})."
    )


def build_effective_profile() -> EffectiveProfile:
    """Build a fresh store under a temp dir and return its introspected facts."""

    _prefer_repo_sources()
    from cookbook_memory.core.contract import build_store

    tmpdir = tempfile.mkdtemp(prefix="memeval-effective-profile-")
    store: Any = None
    try:
        store = build_store(tmpdir)
        return introspect_store(store)
    finally:
        if store is not None:
            _close_registered_backends(store)
        shutil.rmtree(tmpdir, ignore_errors=True)


def introspect_store(store: Any) -> EffectiveProfile:
    notes: list[str] = []

    router = _get_attr(store, "_router", notes, "RouterStore._router")
    config = (
        _get_attr(router, "_config", notes, "Router._config")
        if router is not None
        else None
    )
    profile_name = (
        _get_attr(config, "profile_name", notes, "RouterConfig.profile_name")
        if config is not None
        else None
    )
    backends = (
        _get_attr(router, "backends", notes, "Router.backends")
        if router is not None
        else None
    )
    vectors = None
    if isinstance(backends, dict):
        vectors = backends.get("vectors")
        if vectors is None:
            notes.append("Router.backends did not contain a 'vectors' backend")
    elif backends is not None:
        notes.append(f"Router.backends was {type(backends).__name__}, not dict")

    embedder = (
        _get_attr(vectors, "_embed", notes, "SqliteVectorStore._embed")
        if vectors is not None
        else None
    )
    embedder_class = type(embedder).__name__ if embedder is not None else None
    vector_backend_class = type(vectors).__name__ if vectors is not None else None
    vector_index = (
        _get_attr(vectors, "vector_index", notes, "SqliteVectorStore.vector_index")
        if vectors is not None
        else None
    )
    requested_vector_index = (
        _get_attr(
            vectors,
            "requested_vector_index",
            notes,
            "SqliteVectorStore.requested_vector_index",
        )
        if vectors is not None
        else None
    )
    vector_index_status = (
        _get_attr(
            vectors,
            "vector_index_status",
            notes,
            "SqliteVectorStore.vector_index_status",
        )
        if vectors is not None
        else None
    )

    return EffectiveProfile(
        profile_name=_as_optional_str(profile_name),
        vector_backend_class=vector_backend_class,
        embedder_class=embedder_class,
        vector_index=_as_optional_str(vector_index),
        requested_vector_index=_as_optional_str(requested_vector_index),
        vector_index_status=_as_optional_str(vector_index_status),
        notes=tuple(notes),
    )


def format_report(
    facts: EffectiveProfile,
    env: Mapping[str, Optional[str]],
    verdict: Verdict,
) -> str:
    env_bits = "; ".join(
        f"{name}={'present' if env.get(name) is not None else 'absent'}"
        for name in ENV_FLAGS
    )
    lines = [
        "Effective memory profile preflight",
        f"profile: {_display(facts.profile_name)}",
        f"vector backend: {_display(facts.vector_backend_class)}",
        f"embedder: {_display(facts.embedder_class)}",
        "vector index: "
        f"{_display(facts.vector_index)} "
        f"(requested={_display(facts.requested_vector_index)}; "
        f"status={_display(facts.vector_index_status)})",
        f"env: {env_bits}",
        f"VERDICT: {verdict.message}",
    ]
    if facts.notes:
        lines.append("introspection notes:")
        lines.extend(f"- {note}" for note in facts.notes)
    return "\n".join(lines)


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Report the profile, embedder, and vector index actually built by build_store()."
    )
    parser.add_argument(
        "--require-local-ann",
        action="store_true",
        help="exit non-zero unless the effective store is MiniLM plus sqlite-vec",
    )
    args = parser.parse_args(argv)

    env = capture_env()
    facts = build_effective_profile()
    verdict = classify_effective_profile(
        facts,
        env,
        require_local_ann=args.require_local_ann,
    )
    print(format_report(facts, env, verdict))
    return verdict.exit_code


def _prefer_repo_sources() -> None:
    """Prefer this checkout's eval/ and plugin/ packages when run as a file."""

    here = Path(__file__).resolve()
    repo_root = here.parents[4]
    for path in (repo_root / "plugin", repo_root / "eval"):
        path_str = str(path)
        if path.exists() and path_str not in sys.path:
            sys.path.insert(0, path_str)


def _get_attr(obj: Any, attr: str, notes: list[str], label: str) -> Any:
    try:
        return getattr(obj, attr)
    except AttributeError:
        notes.append(f"could not introspect {label}: missing attribute")
    except Exception as exc:
        notes.append(
            f"could not introspect {label}: {type(exc).__name__}: {exc}"
        )
    return None


def _close_registered_backends(store: Any) -> None:
    router = getattr(store, "_router", None)
    backends = getattr(router, "backends", None)
    if not isinstance(backends, dict):
        return
    seen: set[int] = set()
    for backend in backends.values():
        marker = id(backend)
        if marker in seen:
            continue
        seen.add(marker)
        close = getattr(backend, "close", None)
        if callable(close):
            try:
                close()
            except Exception:
                pass


def _as_optional_str(value: Any) -> Optional[str]:
    return None if value is None else str(value)


def _display(value: Optional[str]) -> str:
    return value if value else UNKNOWN


if __name__ == "__main__":
    raise SystemExit(main())
