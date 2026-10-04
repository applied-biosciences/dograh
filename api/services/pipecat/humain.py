"""Pipecat adapters for the HUMAIN Voice streaming SDK.

The SDK is imported lazily so configurations can still be inspected and
validated in installations that do not select HUMAIN as their provider.
"""

import asyncio
from collections.abc import AsyncGenerator
from typing import Any

from pipecat.audio.utils import create_stream_resampler
from pipecat.frames.frames import (
    CancelFrame,
    EndFrame,
    Frame,
    TranscriptionFrame,
    TTSAudioRawFrame,
)
from pipecat.services.settings import STTSettings, TTSSettings
from pipecat.services.stt_service import STTService
from pipecat.services.tts_service import TTSService
from pipecat.utils.time import time_now_iso8601
from pipecat.utils.tracing.service_decorators import traced_tts

HUMAIN_STT_SAMPLE_RATE = 16_000
HUMAIN_TTS_SAMPLE_RATE = 24_000


class HumainSTTService(STTService):
    """Realtime HUMAIN Voice STT over the official streaming client."""

    def __init__(
        self,
        *,
        api_key: str,
        base_url: str,
        api_path: str,
        model: str,
        language: str,
        sample_rate: int | None = None,
        settings: STTSettings | None = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(
            sample_rate=sample_rate,
            settings=settings or STTSettings(model=model, language=language),
            **kwargs,
        )
        self._api_key = api_key
        self._base_url = base_url
        self._api_path = api_path
        self._model = model
        self._language = language
        self._client: Any | None = None
        self._stream: Any | None = None
        self._resampler = create_stream_resampler(clear_after_secs=None)

    async def start(self, frame):
        await super().start(frame)
        try:
            from humain_voice import stt
        except ModuleNotFoundError as exc:  # pragma: no cover - deployment guidance
            raise RuntimeError(
                "HUMAIN Voice support requires the humain-voice package"
            ) from exc

        self._client = stt.RealtimeClient(
            api_url=self._base_url,
            api_key=self._api_key,
            api_path=self._api_path,
        )
        await self._client.connect()
        language = self._language
        if language == "auto":
            language = "ar-en"
        language_name = "".join(
            part.capitalize() for part in language.replace("_", "-").split("-")
        )
        language = getattr(stt.Language, language_name, language)
        self._stream = await self._client.start_stream(
            language=language,
            on_response=self._handle_response,
        )

    async def stop(self, frame: EndFrame):
        await super().stop(frame)
        await self._disconnect()

    async def cancel(self, frame: CancelFrame):
        await super().cancel(frame)
        await self._disconnect()

    async def cleanup(self):
        await super().cleanup()
        await self._disconnect()

    async def _disconnect(self) -> None:
        stream, client = self._stream, self._client
        self._stream = None
        self._client = None
        if stream is not None:
            await stream.close()
        if client is not None:
            await client.disconnect()

    @staticmethod
    def _response_value(response: Any, key: str, default: Any = None) -> Any:
        if isinstance(response, dict):
            return response.get(key, default)
        return getattr(response, key, default)

    def _handle_response(self, response: Any) -> None:
        asyncio.create_task(self._push_response(response))

    async def _push_response(self, response: Any) -> None:
        text = self._response_value(response, "transcription") or self._response_value(
            response, "text"
        ) or self._response_value(response, "transcript")
        if not text:
            return
        finalized = bool(
            self._response_value(response, "is_final", self._response_value(response, "final", True))
        )
        frame_type = TranscriptionFrame
        await self.push_frame(
            frame_type(
                str(text),
                self._user_id,
                time_now_iso8601(),
                result=response,
                finalized=finalized,
            )
        )
        if finalized:
            await self.emit_stt_usage_metrics()

    async def run_stt(self, audio: bytes) -> AsyncGenerator[Frame | None, None]:
        if self._stream is not None:
            pcm = audio
            if self.sample_rate != HUMAIN_STT_SAMPLE_RATE:
                pcm = await self._resampler.resample(
                    audio, self.sample_rate, HUMAIN_STT_SAMPLE_RATE
                )
            await self._stream.send(pcm)
        yield None


class HumainTTSService(TTSService):
    """Streaming HUMAIN Voice TTS that emits raw PCM16 audio frames."""

    def __init__(
        self,
        *,
        api_key: str,
        base_url: str,
        api_path: str,
        model: str,
        voice: str,
        language: str,
        speed: float = 1.0,
        sample_rate: int | None = HUMAIN_TTS_SAMPLE_RATE,
        settings: TTSSettings | None = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(
            push_text_frames=False,
            push_stop_frames=True,
            push_start_frame=True,
            sample_rate=sample_rate,
            settings=settings
            or TTSSettings(
                model=model,
                voice=voice,
                language=language,
            ),
            **kwargs,
        )
        self._api_key = api_key
        self._base_url = base_url
        self._api_path = api_path
        self._speed = speed

    @traced_tts
    async def run_tts(self, text: str, context_id: str) -> AsyncGenerator[Frame, None]:
        try:
            from humain_voice import tts
        except ModuleNotFoundError as exc:  # pragma: no cover - deployment guidance
            raise RuntimeError(
                "HUMAIN Voice support requires the humain-voice package"
            ) from exc

        settings = self._settings
        model_name = str(settings.model).replace("-", "_").split(".")[-1]
        model = getattr(
            tts.TtsModel,
            "".join(part.capitalize() for part in model_name.split("_")),
            settings.model,
        )
        await self.start_tts_usage_metrics(text)
        async with tts.TTSClient(
            api_url=self._base_url,
            api_key=self._api_key,
            api_path=self._api_path,
        ) as client:
            async for chunk in client.synthesize_stream(
                text,
                voice_id=settings.voice,
                model=model,
            ):
                audio = chunk.get("audio") if isinstance(chunk, dict) else getattr(chunk, "audio", chunk)
                if audio:
                    yield TTSAudioRawFrame(
                        bytes(audio), HUMAIN_TTS_SAMPLE_RATE, 1, context_id=context_id
                    )
