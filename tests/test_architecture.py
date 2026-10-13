"""The dependency rule of Annex C as a test (ADR-007)."""

import ast
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src" / "advisor"
FORBIDDEN = {
    "fastapi",
    "starlette",
    "pydantic",
    "psycopg",
    "psycopg_pool",
    "jwt",
    "uvicorn",
    "sqlalchemy",
    "pika",
    "langchain_core",
    "langchain_openai",
    "openai",
    "httpx",
}


def imports_of(module: Path) -> set[str]:
    tree = ast.parse(module.read_text(encoding="utf-8"))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module)
    return names


def modules_under(layer: str) -> list[Path]:
    folder = SRC / layer
    return sorted(folder.rglob("*.py")) if folder.exists() else []


def test_the_domain_depends_on_nothing_of_the_project_but_itself():
    for module in modules_under("domain"):
        for name in imports_of(module):
            if name.startswith("advisor."):
                assert name.startswith("advisor.domain"), f"{module.name} imports {name}"


def test_the_application_never_imports_an_adapter():
    for module in modules_under("application"):
        for name in imports_of(module):
            if name.startswith("advisor."):
                assert not name.startswith("advisor.adapter"), f"{module.name} imports {name}"
            assert not name.startswith("apps"), f"{module.name} imports {name}"


def test_neither_the_domain_nor_the_application_imports_a_framework_or_a_driver():
    for layer in ("domain", "application"):
        for module in modules_under(layer):
            hit = {name.split(".")[0] for name in imports_of(module)} & FORBIDDEN
            assert not hit, f"{module.relative_to(SRC)} imports {sorted(hit)}"
