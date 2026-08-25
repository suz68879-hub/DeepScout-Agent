from pathlib import Path


def test_backend_ci_initializes_checkpointer_before_pytest():
    workflow = (
        Path(__file__).resolve().parents[2] / ".github" / "workflows" / "ci.yml"
    ).read_text(encoding="utf-8")

    alembic = workflow.index("uv run alembic upgrade head")
    checkpointer = workflow.index(
        "uv run python scripts/setup_postgres_checkpointer.py"
    )
    pytest = workflow.index("uv run pytest --cov=.")

    assert alembic < checkpointer < pytest
