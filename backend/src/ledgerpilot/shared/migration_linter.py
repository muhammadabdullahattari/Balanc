"""G4: Online schema migration linter preventing table-locking DDL on high-volume ledger tables."""

from __future__ import annotations

import re
from pathlib import Path

FORBIDDEN_UPGRADE_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    (
        "ACCESS_EXCLUSIVE_TABLE_LOCK",
        re.compile(r"LOCK\s+TABLE\s+app\.\w+\s+IN\s+ACCESS\s+EXCLUSIVE\s+MODE", re.IGNORECASE),
    ),
    (
        "DESTRUCTIVE_DROP_LEDGER_TABLE",
        re.compile(
            r"DROP\s+TABLE\s+(?:IF\s+EXISTS\s+)?app\.(?:journals|journal_lines|bank_rows|evidence)\b",
            re.IGNORECASE,
        ),
    ),
    (
        "VACUUM_FULL_TABLE_REWRITE",
        re.compile(r"VACUUM\s+FULL\b", re.IGNORECASE),
    ),
]


def extract_upgrade_source(migration_text: str) -> str:
    """Return only the body of `def upgrade()` before `def downgrade()`."""
    up_idx = migration_text.find("def upgrade")
    if up_idx == -1:
        return migration_text
    down_idx = migration_text.find("def downgrade", up_idx)
    return migration_text[up_idx:down_idx] if down_idx != -1 else migration_text[up_idx:]


def lint_migrations_directory(migrations_dir: Path) -> list[str]:
    """Scan Alembic migration files and return any online-DDL safety violations in `upgrade()`."""
    violations: list[str] = []
    for path in sorted(migrations_dir.glob("*.py")):
        if path.name.startswith("__"):
            continue
        source = path.read_text(encoding="utf-8")
        upgrade_body = extract_upgrade_source(source)
        for rule_code, pattern in FORBIDDEN_UPGRADE_PATTERNS:
            if pattern.search(upgrade_body):
                violations.append(f"{path.name}: {rule_code}")
    return violations
