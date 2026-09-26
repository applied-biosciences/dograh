"""Server-side HUMAIN profile catalogue; keys never leave this process."""

from collections.abc import Mapping, Sequence
from typing import Any

from humain_voice import tts as humain_tts

HUMAIN_API_URL = "https://api.voice.humain.com"


class HumainVoiceCatalogError(Exception):
    pass


def _value(item: Any, key: str) -> Any:
    return item.get(key) if isinstance(item, Mapping) else getattr(item, key, None)


def _text(value: Any) -> str | None:
    return value.strip() if isinstance(value, str) and value.strip() else None


def _normalize(voice: Any) -> dict[str, str | None] | None:
    voice_id = _text(_value(voice, "id"))
    if voice_id is None:
        return None
    profile = _value(voice, "profile")
    speaker = _value(profile, "speaker")
    gender, accent = _text(_value(speaker, "gender")), _text(_value(speaker, "dialect"))
    languages = _value(profile, "languages")
    language = ", ".join(x.strip() for x in languages if isinstance(x, str) and x.strip()) if isinstance(languages, Sequence) and not isinstance(languages, str) else None
    return {
        "voice_id": voice_id,
        "name": _text(_value(voice, "label")) or voice_id,
        "description": " · ".join(x for x in (accent, language) if x) or None,
        "gender": gender,
        "accent": accent,
        "language": language,
    }


async def list_humain_voices(api_key: str) -> list[dict[str, str | None]]:
    client = humain_tts.TTSClient(api_url=HUMAIN_API_URL, api_key=api_key)
    try:
        await client.connect()
        return [item for voice in await client.list_voices(timeout_seconds=5.0) if (item := _normalize(voice))]
    except Exception as exc:
        raise HumainVoiceCatalogError("HUMAIN voice discovery failed") from exc
    finally:
        try:
            await client.close()
        except Exception:
            pass
