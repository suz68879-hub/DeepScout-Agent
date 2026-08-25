import sqlite3
from contextlib import closing

import pytest

from scripts.generate_migration_fixture import generate_fixture


def test_generated_fixture_has_expected_synthetic_rows(tmp_path):
    path = tmp_path / "generated-no-pii.db"

    counts = generate_fixture(path, users=2)

    assert sum(counts.values()) == 34
    with closing(sqlite3.connect(path)) as connection:
        usernames = [row[0] for row in connection.execute("SELECT username FROM app_user")]
        assert usernames == ["synthetic_user_0001", "synthetic_user_0002"]
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []


def test_generated_fixture_refuses_to_overwrite(tmp_path):
    path = tmp_path / "generated-no-pii.db"
    generate_fixture(path, users=1)

    with pytest.raises(FileExistsError, match="refusing to overwrite"):
        generate_fixture(path, users=1)
