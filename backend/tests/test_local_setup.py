"""Local credential automation must preserve secrets and reject unsafe targets."""

from pathlib import Path

import pytest
from pydantic import ValidationError

from ledgerpilot.local_setup import (
    ROOT,
    SetupError,
    prepare_environment,
    read_values,
    runtime_settings,
    validate_values,
)


def test_file_validation_does_not_inherit_ambient_settings(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / ".env"
    prepare_environment(path, ROOT / ".env.example")
    values = read_values(path)
    values.pop("DB_POOL_SIZE", None)
    monkeypatch.setenv("DB_POOL_SIZE", "999")
    assert runtime_settings(values).db_pool_size == 5
    values.pop("SESSION_SIGNING_KEY")
    monkeypatch.setenv("SESSION_SIGNING_KEY", "ambient-secret-must-not-fill-a-missing-key")
    with pytest.raises(ValidationError):
        runtime_settings(values)


def test_prepare_is_idempotent_and_preserves_unrelated_settings(tmp_path: Path) -> None:
    path = tmp_path / ".env"
    path.write_text(
        "APP_ENV=local\nUNRELATED_SETTING='preserve me'\n"
        "POSTGRES_PASSWORD=existing-administrator-password\n",
        encoding="utf-8",
    )
    changed = prepare_environment(path, ROOT / ".env.example")
    values = read_values(path)
    assert values["UNRELATED_SETTING"] == "preserve me"
    assert values["POSTGRES_PASSWORD"] == "existing-administrator-password"
    assert "APP_DB_PASSWORD" in changed
    assert values["APP_DB_PASSWORD"] != values["MIGRATION_DB_PASSWORD"]
    validate_values(values)
    original = path.read_bytes()
    assert prepare_environment(path, ROOT / ".env.example") == []
    assert path.read_bytes() == original


def test_new_environment_replaces_all_template_secret_values(tmp_path: Path) -> None:
    path = tmp_path / ".env"
    prepare_environment(path, ROOT / ".env.example")
    values = read_values(path)
    validate_values(values)
    assert "replace-" not in path.read_text("utf-8")


def test_alternate_port_updates_both_urls_atomically(tmp_path: Path) -> None:
    path = tmp_path / ".env"
    prepare_environment(path, ROOT / ".env.example", port=55432)
    values = read_values(path)
    validate_values(values)
    assert values["POSTGRES_PORT"] == "55432"
    assert ":55432/" in values["DATABASE_URL"]
    assert ":55432/" in values["MIGRATION_DATABASE_URL"]


def test_weak_existing_secret_needs_explicit_replacement(tmp_path: Path) -> None:
    path = tmp_path / ".env"
    path.write_text("APP_ENV=local\nPOSTGRES_PASSWORD=short\n", encoding="utf-8")
    with pytest.raises(SetupError, match="passwords"):
        prepare_environment(path, ROOT / ".env.example")
    assert "POSTGRES_PASSWORD=short" in path.read_text("utf-8")
    prepare_environment(path, ROOT / ".env.example", replace_weak=True)
    validate_values(read_values(path))


@pytest.mark.parametrize(
    "content",
    [
        "APP_ENV=production\n",
        "APP_ENV=local\nDATABASE_URL=postgresql+psycopg://user:secret@remote.example.com/db\n",
    ],
)
def test_prepare_refuses_hosted_configuration_without_changing_it(
    tmp_path: Path, content: str
) -> None:
    path = tmp_path / ".env"
    path.write_text(content, encoding="utf-8")
    with pytest.raises(SetupError):
        prepare_environment(path, ROOT / ".env.example")
    assert path.read_text("utf-8") == content


def test_rejects_shared_roles_and_passwords(tmp_path: Path) -> None:
    path = tmp_path / ".env"
    prepare_environment(path, ROOT / ".env.example")
    values = read_values(path)
    values["MIGRATION_DB_USER"] = values["APP_DB_USER"]
    with pytest.raises(SetupError, match="distinct"):
        validate_values(values)
    values = read_values(path)
    values["MIGRATION_DB_PASSWORD"] = values["APP_DB_PASSWORD"]
    with pytest.raises(SetupError, match="distinct"):
        validate_values(values)


def test_prepare_refuses_to_replace_a_lost_mfa_key(tmp_path: Path) -> None:
    path = tmp_path / ".env"
    prepare_environment(path, ROOT / ".env.example")
    key = read_values(path)["MFA_ENCRYPTION_KEY"]
    path.write_text(path.read_text(encoding="utf-8").replace(key, ""), encoding="utf-8")
    before = path.read_bytes()
    with pytest.raises(SetupError, match="Restore"):
        prepare_environment(path, ROOT / ".env.example")
    assert path.read_bytes() == before
