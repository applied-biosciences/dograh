"""HUMAIN Voice TTS service.

The SDK uses an authenticated Socket.IO stream.  Credentials live only on the
service instance; this adapter deliberately never logs the key or request URL.
"""

from dataclasses import dataclass
from typing import Any, AsyncGenerator

from humain_voice import tts as humain_tts
from humain_voice.stt.types import ErrorResponse
from loguru import logger
from pipecat.frames.frames import CancelFrame, EndFrame, ErrorFrame, Frame, StartFrame, TTSAudioRawFrame
from pipecat.services.settings import TTSSettings
from pipecat.services.tts_service import TTSService
from pipecat.utils.tracing.service_decorators import traced_tts

HUMAIN_API_URL = "https://api.voice.humain.com"
HUMAIN_TTS_SAMPLE_RATE = 24000


@dataclass
class HumainTTSSettings(TTSSettings):
    """Configuration stored in the V2 TTS section."""


class HumainTTSService(TTSService):
    Settings = HumainTTSSettings

    def __init__(
        self,
        *,
        api_key: str,
        settings: HumainTTSSettings | None = None,
        api_url: str = HUMAIN_API_URL,
        **kwargs: Any,
    ) -> None:
        resolved = HumainTTSSettings(model="nebula", voice=None)
        if settings is not None:
            resolved.apply_update(settings)
        super().__init__(sample_rate=HUMAIN_TTS_SAMPLE_RATE, settings=resolved, **kwargs)
        self._api_key = api_key
        self._api_url = api_url
        self._client: humain_tts.TTSClient | None = None

    def can_generate_metrics(self) -> bool:
        return True

    async def start(self, frame: StartFrame) -> None:
        await super().start(frame)
        await self._connect()

    async def stop(self, frame: EndFrame) -> None:
        await super().stop(frame)
        await self._disconnect()

    async def cancel(self, frame: CancelFrame) -> None:
        await super().cancel(frame)
        await self._disconnect()

    async def cleanup(self) -> None:
        await super().cleanup()
        await self._disconnect()

    @traced_tts
    async def run_tts(self, text: str, context_id: str) -> AsyncGenerator[Frame, None]:
        voice = self._settings.voice
        if not isinstance(voice, str) or not voice.strip():
            yield ErrorFrame(error="HUMAIN TTS requires a configured voice profile ID")
            return
        try:
            await self._connect()
            if self._client is None:
                raise RuntimeError("HUMAIN TTS client is unavailable")
            await self.start_tts_usage_metrics(text)
            async for response in self._client.synthesize_stream(
                text,
                voice_id=voice,
                model=self._settings.model,
                timeout_seconds=30.0,
                on_error=self._on_error,
            ):
                if response.audio:
                    await self.stop_ttfb_metrics()
                    yield TTSAudioRawFrame(
                        response.audio, HUMAIN_TTS_SAMPLE_RATE, 1, context_id=context_id
                    )
        except Exception as exc:  # provider failures belong on the pipeline
            yield ErrorFrame(error=f"HUMAIN TTS error: {exc}")

    async def _connect(self) -> None:
        if self._client is None:
            self._client = humain_tts.TTSClient(api_url=self._api_url, api_key=self._api_key)
            await self._client.connect()

    async def _disconnect(self) -> None:
        client, self._client = self._client, None
        if client is not None:
            try:
                await client.close()
            except Exception as exc:  # cleanup must not hide the original error
                logger.debug(f"{self} HUMAIN TTS cleanup failed: {exc}")

    def _on_error(self, error: ErrorResponse | None) -> None:
        message = "HUMAIN TTS request failed"
        if error is not None:
            message = error.message or error.code or message
        self.create_task(self.push_error(error_msg=message), "humain-tts-error")
