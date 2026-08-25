import pytest

from services.storage.pagination import CursorError, decode_cursor, encode_cursor


def test_cursor_roundtrip_and_tamper_rejection():
    cursor = encode_cursor("2026-08-25T10:00:00+00:00", "00000000-0000-0000-0000-000000000001")
    decoded = decode_cursor(cursor)
    assert decoded.created_at == "2026-08-25T10:00:00+00:00"
    assert decoded.id == "00000000-0000-0000-0000-000000000001"

    with pytest.raises(CursorError):
        decode_cursor("not-base64")
    with pytest.raises(CursorError):
        decode_cursor(cursor + "tampered")


async def test_report_keyset_page_has_no_duplicates(repository_scope):
    prefix, scope = repository_scope
    async with scope() as repository:
        user = await repository.user_create(f"{prefix}_user", "hash")
        for index in range(5):
            await repository.report_create(
                user["id"],
                {
                    "scores_json": "{}",
                    "feedback_json": "{}",
                    "suggestions_json": "[]",
                    "position": f"position-{index}",
                    "created_at": f"2026-08-25T10:00:0{index}+00:00",
                },
            )

    async with scope() as repository:
        first = await repository.report_page(user["id"], limit=2, cursor=None)
        second = await repository.report_page(
            user["id"], limit=2, cursor=decode_cursor(first.next_cursor)
        )
        third = await repository.report_page(
            user["id"], limit=2, cursor=decode_cursor(second.next_cursor)
        )

    ids = [row["id"] for page in (first, second, third) for row in page.items]
    assert len(ids) == len(set(ids)) == 5
    assert first.next_cursor and second.next_cursor and third.next_cursor is None


async def test_unknown_report_cursor_is_rejected(repository_scope):
    prefix, scope = repository_scope
    async with scope() as repository:
        user = await repository.user_create(f"{prefix}_user", "hash")
    missing = decode_cursor(
        encode_cursor("2026-08-25T10:00:00+00:00", "00000000-0000-0000-0000-000000000099")
    )
    async with scope() as repository:
        with pytest.raises(CursorError):
            await repository.report_page(user["id"], limit=20, cursor=missing)
