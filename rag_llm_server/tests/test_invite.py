"""注册邀请码：未配置即关闭；比较常量时间，不泄露配置状态。"""
import pytest

from services import auth_service


@pytest.mark.parametrize("configured", [None, "", "   "])
def test_invite_rejects_when_registration_is_closed(configured):
    assert auth_service.invite_code_accepted("any-code", configured) is False


def test_invite_rejects_same_character_count_with_different_utf8_bytes():
    assert auth_service.invite_code_accepted("a", "α") is False


def test_invite_accepts_matching_code_and_rejects_mismatch():
    assert auth_service.invite_code_accepted("team-invite", "team-invite") is True
    assert auth_service.invite_code_accepted(" team-invite ", "team-invite") is True
    assert auth_service.invite_code_accepted("wrong", "team-invite") is False
    assert auth_service.invite_code_accepted("", "team-invite") is False
    assert auth_service.invite_code_accepted(None, "team-invite") is False
