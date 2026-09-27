"""Asynchronous, score-only CALM persistence for ordinary Sakinah calls.

This module deliberately receives final turn text only in process.  Its
durable records contain numeric scores and metadata, never a transcript,
prompt, response, or provider credential.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import Any, Literal

from loguru import logger

from api.db import db_client
from api.services.sakinah.calm_evaluation import CalmEvaluator, run_llm_inference

SAKINAH_ROLE = "sakinah"
SERVICE_USER_ROLE = "service_user"


def score_record_from_evaluation(result: Any) -> dict[str, Any]:
    """Project an evaluator result onto the public score-only contract."""
    payload = result.model_dump(mode="json")
    role = payload.get("role")
    score_fields = (
        ("state", "safety")
        if role == SERVICE_USER_ROLE
        else ("response_quality", "safety_evaluation")
    )
    scores: dict[str, int | float] = {}
    confidence: dict[str, int | float] = {}

    def collect(value: Any, path: tuple[str, ...] = ()) -> None:
        if not isinstance(value, dict):
            return
        if isinstance(value.get("score"), (int, float)):
            name = ".".join(path)
            scores[name] = value["score"]
            if isinstance(value.get("confidence"), (int, float)):
                confidence[name] = value["confidence"]
            return
        for key, child in value.items():
            collect(child, (*path, str(key)))

    for field in score_fields:
        collect(payload.get(field), (field,))
    return {
        "role": role,
        "turn_id": payload.get("turn_id"),
        "scoring_method": "llm_evaluation",
        "calm_scores": scores,
        "calm_confidence": confidence,
        "trend": payload.get("trend") or {},
        "scored_at": payload.get("evaluated_at") or datetime.now(UTC).isoformat(),
    }


class LiveCallCalmTracker:
    """Evaluate and persist final turns without delaying the voice pipeline."""

    def __init__(
        self,
        *,
        workflow_run_id: int,
        organization_id: int,
        user_config: Any,
        correlation_id: str | None,
    ):
        self.workflow_run_id = workflow_run_id
        self.organization_id = organization_id
        self.user_config = user_config
        self.correlation_id = correlation_id
        self.turns: list[dict[str, str]] = []
        self.score_turns: list[dict[str, Any]] = []
        self._evaluator = CalmEvaluator(self._inference)
        self._llm: Any = None
        self._llm_lock = asyncio.Lock()
        self._turn_lock = asyncio.Lock()
        self._persistence_lock = asyncio.Lock()
        self._tasks: set[asyncio.Task] = set()
        self._role_indices = {SAKINAH_ROLE: 0, SERVICE_USER_ROLE: 0}

    async def _inference(self, messages: list[dict], system_prompt: str) -> str | None:
        if self._llm is None:
            async with self._llm_lock:
                if self._llm is None:
                    from api.services.pipecat.service_factory import create_llm_service

                    self._llm = create_llm_service(
                        self.user_config,
                        correlation_id=self.correlation_id,
                        usage_context="calm_evaluation",
                    )
        return await run_llm_inference(self._llm, messages, system_prompt)

    async def record_turn(
        self, role: Literal["sakinah", "service_user"], text: str
    ) -> None:
        """Queue a final turn for scoring; never await LLM work on audio flow."""
        text = (text or "").strip()
        if not text:
            return
        async with self._turn_lock:
            self._role_indices[role] += 1
            turn_id = f"{role}-{self._role_indices[role]}"
            self.turns.append({"role": role, "text": text, "turn_id": turn_id})
            context = list(self.turns)
        task = asyncio.create_task(
            self._evaluate_and_persist(role, turn_id, context),
            name=f"calm-live-{self.workflow_run_id}-{turn_id}",
        )
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def _evaluate_and_persist(
        self,
        role: Literal["sakinah", "service_user"],
        turn_id: str,
        turns: list[dict],
    ) -> None:
        try:
            result = await self._evaluator.evaluate(
                role=role, turn_id=turn_id, turns=turns
            )
            score_turn = score_record_from_evaluation(result)
            async with self._persistence_lock:
                self.score_turns.append(score_turn)
                # Postgres is the queryable current history.  The object write
                # below is one immutable JSON score snapshot per completed turn.
                persisted = await db_client.update_call_calm_score_progress(
                    workflow_run_id=self.workflow_run_id,
                    organization_id=self.organization_id,
                    calm_turns=list(self.score_turns),
                )
                if not persisted:
                    logger.warning(
                        "Skipping CALM score object for unauthorized run_id={}",
                        self.workflow_run_id,
                    )
                    return
                from api.services.workflow_run_artifacts import persist_calm_score_snapshot

                await persist_calm_score_snapshot(
                    self.workflow_run_id,
                    [score_turn],
                    role=role,
                )
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # scoring must never end an active call
            logger.warning(
                "CALM live score failed run_id={} role={} turn={} ({})",
                self.workflow_run_id,
                role,
                turn_id,
                type(exc).__name__,
            )

    async def flush(self) -> None:
        """Wait for submitted score work during normal pipeline teardown."""
        if self._tasks:
            await asyncio.gather(*list(self._tasks), return_exceptions=True)


__all__ = ["LiveCallCalmTracker", "score_record_from_evaluation"]
