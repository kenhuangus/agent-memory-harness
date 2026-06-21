"""Rubric §K (anti-slop AST/grep scans) + §P (out-of-scope docstring +
README) for the redaction module.

These tests scan the source files of the redaction package and the rendered
README to catch slop patterns the unit tests would miss: stub plugin
classes, missing docstrings, lingering TODO markers, hidden print()
statements, unjustified pragmas, missing out-of-scope policy.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

def _find_repo_root(start: Path) -> Path:
    for parent in [start, *start.parents]:
        if (parent / ".git").exists():
            return parent
    raise RuntimeError("could not find repo root (no .git directory)")


REDACTION_DIR = Path(__file__).resolve().parent.parent / "redaction"
REPO_ROOT = _find_repo_root(Path(__file__).resolve())


def _python_files() -> list[Path]:
    return sorted(REDACTION_DIR.rglob("*.py"))


def _parse(path: Path) -> ast.Module:
    return ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


# --- §K anti-slop scans ------------------------------------------------- #
def test_no_todo_markers() -> None:
    """§53: zero TODO/FIXME/XXX/HACK markers in redaction source."""
    marker_re = re.compile(r"\b(TODO|FIXME|XXX|HACK)\b")
    offenders: list[str] = []
    for path in _python_files():
        for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if marker_re.search(line):
                offenders.append(f"{path.relative_to(REPO_ROOT)}:{lineno}: {line.strip()}")
    assert not offenders, "TODO-class markers found:\n" + "\n".join(offenders)


def test_no_stub_plugin_classes() -> None:
    """§54: every custom plugin class assigns BOTH secret_type and denylist
    at class level. No pass-only stubs."""
    plugins_dir = REDACTION_DIR / "plugins"
    offenders: list[str] = []
    for path in sorted(plugins_dir.glob("*.py")):
        if path.name == "__init__.py":
            continue
        tree = _parse(path)
        for node in ast.walk(tree):
            if not isinstance(node, ast.ClassDef):
                continue
            # Pass-only body = stub.
            if len(node.body) == 1 and isinstance(node.body[0], ast.Pass):
                offenders.append(f"{path.name}:{node.lineno} class {node.name} is pass-only")
                continue
            # Must assign secret_type and denylist at class level.
            assigned: set[str] = set()
            for stmt in node.body:
                if isinstance(stmt, ast.Assign):
                    for t in stmt.targets:
                        if isinstance(t, ast.Name):
                            assigned.add(t.id)
                elif isinstance(stmt, ast.AnnAssign) and isinstance(stmt.target, ast.Name):
                    assigned.add(stmt.target.id)
            for required in ("secret_type", "denylist"):
                if required not in assigned:
                    offenders.append(
                        f"{path.name}:{node.lineno} class {node.name} missing "
                        f"class-level {required!r}"
                    )
    assert not offenders, "Stub-plugin patterns found:\n" + "\n".join(offenders)


def test_pragmas_are_justified() -> None:
    """§55: every pragma (no cover / type: ignore / noqa) carries a
    `# REASON: <text>` justification on the same line."""
    pragma_re = re.compile(r"#\s*(pragma:\s*no\s*cover|type:\s*ignore|noqa)")
    reason_re = re.compile(r"#\s*REASON:\s*\S")
    offenders: list[str] = []
    for path in _python_files():
        for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if pragma_re.search(line) and not reason_re.search(line):
                offenders.append(
                    f"{path.relative_to(REPO_ROOT)}:{lineno}: pragma without "
                    f"# REASON: justification — {line.strip()!r}"
                )
    assert not offenders, "Unjustified pragmas:\n" + "\n".join(offenders)


def test_no_print_calls_in_source() -> None:
    """§56: zero print() calls in redaction source — use logging."""
    offenders: list[str] = []
    for path in _python_files():
        tree = _parse(path)
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id == "print"
            ):
                offenders.append(f"{path.relative_to(REPO_ROOT)}:{node.lineno}: print() call")
    assert not offenders, "print() calls found:\n" + "\n".join(offenders)


def test_public_symbols_have_real_docstrings() -> None:
    """§57: every public function/class has a non-empty docstring."""
    offenders: list[str] = []
    for path in _python_files():
        tree = _parse(path)
        for node in ast.walk(tree):
            if not isinstance(
                node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)
            ):
                continue
            # Private symbols (leading underscore) are exempt — except dunders
            # we don't want to silently miss.
            if node.name.startswith("_") and not (
                node.name.startswith("__") and node.name.endswith("__")
            ):
                continue
            doc = ast.get_docstring(node)
            if not doc or not doc.strip() or doc.strip().upper() in {"TODO", "FIXME"}:
                offenders.append(
                    f"{path.relative_to(REPO_ROOT)}:{node.lineno}: "
                    f"{node.name} has no real docstring"
                )
    assert not offenders, "Missing docstrings:\n" + "\n".join(offenders)


def test_active_plugins_use_analyze_line_not_scan_line() -> None:
    """§45-46: redact() drives plugins via analyze_line, never scan_line or
    transient_settings. Use AST so the policy mentions in the docstring
    ("no scan_line") don't trigger false positives.
    """
    tree = _parse(REDACTION_DIR / "__init__.py")
    call_names: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            if isinstance(node.func, ast.Attribute):
                call_names.append(node.func.attr)
            elif isinstance(node.func, ast.Name):
                call_names.append(node.func.id)
    assert "analyze_line" in call_names, "analyze_line() call missing from redaction core"
    assert "scan_line" not in call_names, "scan_line CALL forbidden per ADR-005"
    assert "transient_settings" not in call_names, (
        "transient_settings CALL forbidden per ADR-005"
    )


def test_no_yaml_or_scan_line_imports() -> None:
    """§47 (YAML) and §45 (scan_line) reinforced by import scan."""
    for path in _python_files():
        tree = _parse(path)
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                module = node.module or ""
                names = [n.name for n in node.names]
                if "yaml" in module or "yaml" in names:
                    raise AssertionError(f"{path.name} imports YAML: {ast.dump(node)}")
                if "scan" in module and any("scan_line" in n for n in names):
                    raise AssertionError(f"{path.name} imports scan_line")


def test_no_yaml_config_loaded() -> None:
    """§47: redaction module does not read any YAML config."""
    for path in _python_files():
        src = path.read_text(encoding="utf-8")
        assert "yaml.load" not in src, f"{path.name} loads YAML"
        assert ".yaml" not in src.lower(), f"{path.name} references .yaml"
        assert ".yml" not in src.lower(), f"{path.name} references .yml"


# --- §P out-of-scope policy is discoverable --------------------------- #
def test_module_docstring_lists_freeform_english_oos() -> None:
    """§92: module docstring names free-form English credentials as OOS."""
    from memeval.dreaming import redaction

    doc = redaction.__doc__ or ""
    assert "Free-form English credentials" in doc, (
        "module docstring missing 'Free-form English credentials' out-of-scope entry"
    )


def test_module_docstring_lists_novel_formats_oos() -> None:
    """§93: module docstring names novel/custom token formats as OOS."""
    from memeval.dreaming import redaction

    doc = redaction.__doc__ or ""
    assert "Novel" in doc and "token formats" in doc, (
        "module docstring missing 'Novel/custom token formats' out-of-scope entry"
    )


def test_module_docstring_lists_pii_oos() -> None:
    """§94: module docstring names PII as OOS."""
    from memeval.dreaming import redaction

    doc = redaction.__doc__ or ""
    assert "PII" in doc, "module docstring missing 'PII' out-of-scope entry"


def test_readme_lists_out_of_scope_categories() -> None:
    """§95: README also documents the three out-of-scope categories
    (discoverable both ways per ADR-011 §Consequences)."""
    readme = REDACTION_DIR / "README.md"
    assert readme.exists(), f"{readme} does not exist"
    content = readme.read_text(encoding="utf-8")
    assert "Free-form English credentials" in content
    assert ("Novel/custom token formats" in content) or ("Novel token formats" in content)
    assert "PII" in content
