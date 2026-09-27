"""Asynchronous CALM turn-history persistence for ordinary Sakinah calls.

This module deliberately receives final turn text only in process.  Its
durable history holds numeric scores plus the exact evaluator prompt for the
authorized owner; it never stores a provider credential.
"""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime
from typing import Any, Literal

from loguru import logger

from api.db import db_client
from api.services.sakinah.calm_evaluation import (
    CalmEvaluator,
    build_evaluation_input,
    run_llm_inference,
)
from api.services.sakinah.calm.runtime import CalmSimulationRuntime

SAKINAH_ROLE = "sakinah"
SERVICE_USER_ROLE = "service_user"


def score_record_from_evaluation(
    result: Any, *, engineered_prompt: str | None = None
) -> dict[str, Any]:
    """Project an evaluator result onto the authorized turn-history contract."""
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
    record = {
        "role": role,
        "turn_id": payload.get("turn_id"),
        "scoring_method": "llm_evaluation",
        "calm_scores": scores,
        "calm_confidence": confidence,
        "trend": payload.get("trend") or {},
        "scored_at": payload.get("evaluated_at") or datetime.now(UTC).isoformat(),
    }
    if engineered_prompt:
        record["engineered_prompt"] = engineered_prompt
    return record


def score_record_from_generation_turn(turn: dict[str, Any]) -> dict[str, Any]:
    """Project the prompt-time CALM analysis onto the durable live contract.

    This is deliberately available before the independent LLM evaluator has
    responded.  It gives an active WebRTC or phone call a numeric, auditable
    score and the *actual next-turn prompt* rather than making the dashboard
    wait up to the evaluator timeout for its first stripe.
    """
    raw_trend = turn.get("trend") or {}
    parameters = raw_trend.get("parameters") or {}
    trend = {
        "parameters": {
            name: {
                key: value.get(key)
                for key in (
                    "current_score",
                    "previous_score",
                    "delta_previous",
                    "direction",
                )
                if key in value
            }
            for name, value in parameters.items()
            if isinstance(value, dict)
        }
    }
    record = {
        "role": SERVICE_USER_ROLE,
        "turn_id": turn.get("turn_id"),
        "scoring_method": "rule_based_calm",
        "calm_scores": turn.get("calm_scores") or {},
        "calm_confidence": turn.get("calm_confidence") or {},
        "trend": trend,
        "significant_changes": turn.get("significant_changes") or {},
        "scored_at": datetime.now(UTC).isoformat(),
    }
    if isinstance(turn.get("prompt_sent_to_llm"), str):
        record["engineered_prompt"] = turn["prompt_sent_to_llm"]
    return record


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
        # The same deterministic CALM runtime used by simulation prepares the
        # live Sakinah prompt.  It must live at call scope so prior-turn trends
        # remain continuous across agent visits and transport reconnects.
        self._generation_runtime = CalmSimulationRuntime()
        self._last_generation_turn_id: str | None = None

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

    async def prepare_generation_prompt(self, engine: Any, context: Any) -> None:
        """Inject and durably snapshot the CALM prompt before live generation.

        Pipecat invokes this immediately before the Sakinah LLM receives a
        context frame.  The database/object-store work is scheduled rather
        than awaited on the audio path, while ``flush`` still drains it before
        normal call teardown.
        """
        messages = getattr(context, "messages", []) or []
        latest_utterance: str | None = None
        for message in reversed(messages):
            if not isinstance(message, dict) or message.get("role") != "user":
                continue
            content = message.get("content")
            if isinstance(content, str) and content.strip():
                latest_utterance = content.strip()
                break
        if not latest_utterance:
            return

        async with self._turn_lock:
            # ``on_user_turn_message_added`` is emitted before this context is
            # forwarded.  Keep a defensive fallback for provider-specific
            # realtime aggregators that only expose the context message.
            matching_turn = next(
                (
                    turn
                    for turn in reversed(self.turns)
                    if turn["role"] == SERVICE_USER_ROLE
                    and turn["text"] == latest_utterance
                ),
                None,
            )
            if matching_turn is None:
                self._role_indices[SERVICE_USER_ROLE] += 1
                matching_turn = {
                    "role": SERVICE_USER_ROLE,
                    "text": latest_utterance,
                    "turn_id": f"{SERVICE_USER_ROLE}-{self._role_indices[SERVICE_USER_ROLE]}",
                }
                self.turns.append(matching_turn)
            if matching_turn["turn_id"] == self._last_generation_turn_id:
                return
            conversation_context = [
                {"role": turn["role"], "text": turn["text"]}
                for turn in self.turns[-6:]
            ]
            generation_turn = self._generation_runtime.analyze_turn(
                latest_utterance, conversation_context=conversation_context
            )
            generation_turn["turn_id"] = matching_turn["turn_id"]
            self._last_generation_turn_id = matching_turn["turn_id"]

        # This is the exact prompt sent to Sakinah, not an evaluator trace.
        # It is owner-authorized through the existing Run Details endpoint.
        prompt = generation_turn["prompt_sent_to_llm"]
        await engine._update_llm_context(prompt, [])
        task = asyncio.create_task(
            self._persist_score_turn(score_record_from_generation_turn(generation_turn)),
            name=f"calm-live-prompt-{self.workflow_run_id}-{generation_turn['turn_id']}",
        )
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    def _upsert_score_turn(self, score_turn: dict[str, Any]) -> dict[str, Any]:
        """Replace a prompt-time score with its later evaluator refinement."""
        key = (score_turn.get("role"), score_turn.get("turn_id"))
        for index, existing in enumerate(self.score_turns):
            if (existing.get("role"), existing.get("turn_id")) == key:
                # Preserve the generation prompt: it is the Sakinah prompt
                # history requested by the UI, whereas the later evaluator
                # prompt is an implementation diagnostic for turns without it.
                prompt = existing.get("engineered_prompt") or score_turn.get(
                    "engineered_prompt"
                )
                # Scheduling deliberately keeps storage off the audio path,
                # so an evaluator can finish before its prompt-time snapshot.
                # Never regress an LLM refinement back to rule-based scores in
                # that race; add the newly available generation prompt instead.
                if (
                    existing.get("scoring_method") == "llm_evaluation"
                    and score_turn.get("scoring_method") == "rule_based_calm"
                ):
                    if score_turn.get("engineered_prompt"):
                        existing["engineered_prompt"] = score_turn[
                            "engineered_prompt"
                        ]
                    return existing
                self.score_turns[index] = {**existing, **score_turn}
                if prompt:
                    self.score_turns[index]["engineered_prompt"] = prompt
                return self.score_turns[index]
        self.score_turns.append(score_turn)
        return score_turn

    async def _persist_score_turn(self, score_turn: dict[str, Any]) -> None:
        """Write one authorized score revision to PostgreSQL and object storage."""
        async with self._persistence_lock:
            stored_turn = self._upsert_score_turn(score_turn)
            history = list(self.score_turns)
            persisted = await db_client.update_call_calm_score_progress(
                workflow_run_id=self.workflow_run_id,
                organization_id=self.organization_id,
                calm_turns=history,
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
                [stored_turn],
                role=stored_turn.get("role"),
            )

    async def _evaluate_and_persist(
        self,
        role: Literal["sakinah", "service_user"],
        turn_id: str,
        turns: list[dict],
    ) -> None:
        try:
            messages, system_prompt = build_evaluation_input(role, turns)
            # This is the exact per-turn evaluator input, retained only in the
            # authenticated organization-scoped history.  Keeping both pieces
            # makes an active-test stripe auditable without exposing it through
            # public recording or transcript links.
            engineered_prompt = json.dumps(
                {"messages": messages, "system_instruction": system_prompt},
                separators=(",", ":"),
            )
            result = await self._evaluator.evaluate(
                role=role, turn_id=turn_id, turns=turns
            )
            score_turn = score_record_from_evaluation(
                result, engineered_prompt=engineered_prompt
            )
            await self._persist_score_turn(score_turn)
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


__all__ = [
    "LiveCallCalmTracker",
    "score_record_from_evaluation",
    "score_record_from_generation_turn",
]
