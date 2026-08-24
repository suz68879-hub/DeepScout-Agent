"""Tenant-bound RTC scene, token and Start/Stop OpenAPI orchestration."""
import asyncio
import time
from typing import Any

import httpx

from config import settings
from services.interview_service import _restore_state, build_welcome_message
from services.storage import storage
from services.token_build import AccessToken, PRIVILEGES
from services.utils import Signer

AGENT_USER_ID = "AiAgent"
_session_locks: dict[str, asyncio.Lock] = {}


class RTCConfigurationError(RuntimeError):
    pass


def get_scenes_payload(session: dict) -> dict[str, Any]:
    token_builder = AccessToken(
        settings.RTC_APP_ID,
        settings.RTC_APP_KEY,
        session["rtc_room_id"],
        session["rtc_user_id"],
    )
    token_builder.add_privilege(PRIVILEGES["PrivSubscribeStream"], 0)
    token_builder.add_privilege(PRIVILEGES["PrivPublishStream"], 0)
    token_builder.expire_time(int(time.time()) + 3600 * 24)
    return {
        "ResponseMetadata": {"Action": "getScenes"},
        "SessionId": session["id"],
        "Result": {"scenes": [{
            "scene": {
                "id": "Custom",
                "name": "自定义助手",
                "botName": AGENT_USER_ID,
                "icon": "https://lf3-rtc-demo.volccdn.com/obj/rtc-aigc-assets/DoubaoAvatar.png",
                "isInterruptMode": True,
                "isVision": False,
                "isScreenMode": False,
                "isAvatarScene": None,
                "avatarBgUrl": None,
            },
            "rtc": {
                "AppId": settings.RTC_APP_ID,
                "RoomId": session["rtc_room_id"],
                "UserId": session["rtc_user_id"],
                "Token": token_builder.serialize(),
                "SessionId": session["id"],
            },
            "VoiceChat": {},
        }]},
    }


async def build_voice_chat_body(
    action: str | None,
    session: dict,
    incoming_body: Any,
) -> Any:
    if action == "StartVoiceChat":
        if not settings.SERVER_URL:
            raise RTCConfigurationError("SERVER_URL is required to start RTC callbacks")
        if not settings.RTC_CALLBACK_SECRET:
            raise RTCConfigurationError("RTC_CALLBACK_SECRET is required")
        interview_state = await _restore_state(session)
        callback_url = (
            f"{settings.SERVER_URL.rstrip('/')}/api/chat_callback"
            f"?rtc_callback_id={session['rtc_callback_id']}"
        )
        return {
            "AppId": settings.RTC_APP_ID,
            "RoomId": session["rtc_room_id"],
            "TaskId": session["rtc_task_id"],
            "AgentConfig": {
                "TargetUserId": [session["rtc_user_id"]],
                "WelcomeMessage": build_welcome_message(interview_state),
                "UserId": AGENT_USER_ID,
                "EnableConversationStateCallback": True,
                "ServerMessageURLForRTS": callback_url,
                "ServerMessageSignatureForRTS": settings.RTC_CALLBACK_SECRET,
            },
            "Config": {
                "ASRConfig": {"Provider": "volcano", "ProviderParams": {
                    "Mode": "smallmodel",
                    "AppId": settings.ASR_APP_ID,
                    "Cluster": "volcengine_streaming_common",
                }},
                "TTSConfig": {"Provider": "volcano", "ProviderParams": {
                    "app": {"appid": settings.TTS_APP_ID, "cluster": "volcano_tts"},
                    "audio": {
                        "voice_type": "BV001_streaming",
                        "speed_ratio": 1,
                        "pitch_ratio": 1,
                        "volume_ratio": 1,
                    },
                }},
                "LLMConfig": {
                    "Mode": "ArkV3",
                    "EndPointId": settings.ARK_ENDPOINT_ID,
                    "SystemMessages": [
                        "你是懂小智，AI 面试陪练的面试官。提问专业、语气友好，表达简明扼要。",
                    ],
                },
                "InterruptMode": 0,
            },
        }
    if action == "StopVoiceChat":
        return {
            "AppId": settings.RTC_APP_ID,
            "RoomId": session["rtc_room_id"],
            "TaskId": session["rtc_task_id"],
        }
    return incoming_body


def _idempotent_response(action: str) -> dict[str, Any]:
    return {
        "ResponseMetadata": {"Action": action},
        "Result": {"Idempotent": True},
    }


async def call_voice_chat_openapi(
    action: str | None,
    version: str,
    session: dict,
    incoming_body: Any,
) -> dict[str, Any]:
    if action == "StartVoiceChat" and session.get("rtc_status") == "running":
        return _idempotent_response("StartVoiceChat")
    if action == "StopVoiceChat" and session.get("rtc_status") in {"created", "stopped"}:
        return _idempotent_response("StopVoiceChat")
    lock = _session_locks.setdefault(session["id"], asyncio.Lock())
    async with lock:
        current = await storage.session_get(session["user_id"], session["id"])
        current = current or session
        rtc_status = current.get("rtc_status", "created")
        if action == "StartVoiceChat" and rtc_status == "running":
            return _idempotent_response("StartVoiceChat")
        if action == "StopVoiceChat" and rtc_status in {"created", "stopped"}:
            return _idempotent_response("StopVoiceChat")

        request_body = await build_voice_chat_body(action, current, incoming_body)
        host = "rtc.volcengineapi.com"
        request_data = {
            "method": "POST",
            "path": "/",
            "params": {"Action": action, "Version": version},
            "headers": {"Host": host, "Content-Type": "application/json"},
            "body": request_body,
        }
        signer = Signer(request_data, "rtc")
        signer.add_authorization({
            "accessKeyId": settings.VOLC_AK,
            "secretKey": settings.VOLC_SK,
        })
        url = f"https://{host}?Action={action}&Version={version}"
        async with httpx.AsyncClient() as client:
            response = await client.post(
                url,
                headers=request_data["headers"],
                json=request_body,
                timeout=30.0,
            )
        payload = response.json()
        if not payload.get("ResponseMetadata", {}).get("Error"):
            target = "running" if action == "StartVoiceChat" else "stopped"
            if action in {"StartVoiceChat", "StopVoiceChat"}:
                await storage.session_update(
                    current["user_id"], current["id"], {"rtc_status": target},
                )
        return payload


def clear_rtc_locks() -> None:
    _session_locks.clear()
