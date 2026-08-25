import pytest
from fastapi import HTTPException

import api.reports as reports_api
import api.resume as resume_api
from services.auth_service import hash_password


@pytest.mark.asyncio
async def test_report_detail_returns_404_for_other_user(tmp_storage, monkeypatch):
    storage = await tmp_storage()
    alice = await storage.user_create("alice", hash_password("password-123"))
    bob = await storage.user_create("bob", hash_password("password-123"))
    report = await storage.report_create(alice["id"], {
        "session_id": None, "scores_json": "{}", "feedback_json": "{}",
        "suggestions_json": "[]",
    })
    monkeypatch.setattr(reports_api, "storage", storage)

    assert (await reports_api.get_report(report["id"], alice))["id"] == report["id"]
    with pytest.raises(HTTPException) as exc_info:
        await reports_api.get_report(report["id"], bob)
    assert exc_info.value.status_code == 404
    await storage.close()


@pytest.mark.asyncio
async def test_report_list_uses_page_contract_and_rejects_bad_cursor(tmp_storage, monkeypatch):
    storage = await tmp_storage()
    alice = await storage.user_create("page-alice", hash_password("password-123"))
    for index in range(3):
        await storage.report_create(
            alice["id"],
            {
                "scores_json": "{}",
                "feedback_json": "{}",
                "suggestions_json": "[]",
                "created_at": f"2026-08-25T10:00:0{index}+00:00",
            },
        )
    monkeypatch.setattr(reports_api, "storage", storage)

    first = await reports_api.list_reports(alice, limit=2)
    assert len(first["items"]) == 2
    assert first["next_cursor"]
    assert all("user_id" not in row for row in first["items"])
    legacy = await reports_api.list_reports(alice, legacy=True)
    assert isinstance(legacy, list) and len(legacy) == 3
    with pytest.raises(HTTPException) as exc_info:
        await reports_api.list_reports(alice, cursor="invalid")
    assert exc_info.value.status_code == 400
    await storage.close()


@pytest.mark.asyncio
async def test_latest_resume_is_scoped_and_hides_user_id(tmp_storage, monkeypatch):
    storage = await tmp_storage()
    alice = await storage.user_create("alice", hash_password("password-123"))
    bob = await storage.user_create("bob", hash_password("password-123"))
    await storage.resume_create(alice["id"], {
        "content": "alice resume", "source": "md", "status": "ready",
    })
    monkeypatch.setattr(resume_api, "storage", storage)

    result = await resume_api.get_latest_resume(alice)
    assert result["content"] == "alice resume"
    assert "user_id" not in result
    with pytest.raises(HTTPException) as exc_info:
        await resume_api.get_latest_resume(bob)
    assert exc_info.value.status_code == 404
    await storage.close()
