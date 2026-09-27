import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import Text, cast, delete, func, or_
from sqlalchemy.future import select

from api.db.base_client import BaseDBClient
from api.db.models import (
    CallScoreModel,
    SakinahRunModel,
    SakinahScenarioModel,
    UserModel,
    WorkflowModel,
    WorkflowRunModel,
)

SCENARIO_FIELDS = (
    "title",
    "category",
    "tags",
    "mode",
    "persona",
    "age",
    "gender",
    "language",
    "other_language",
    "emotion",
    "communication_style",
    "initial_information",
    "hidden_information",
    "disclosure",
    "behaviour",
    "background",
    "additional_factors",
    "notes",
    "freestyle_prompt",
)

SAKINAH_ROLE = "sakinah"
SERVICE_USER_ROLE = "service_user"


def _structured_call_scores(
    calm_turns: list[dict[str, Any]] | None,
    role: str | None = None,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    """Project CALM turns into the authenticated, organization-scoped store.

    ``engineered_prompt`` is an explicit release diagnostic: it is retained for
    an authorized Run Details view and history export, never public artifacts.
    """
    calm: list[dict[str, Any]] = []
    safety: list[dict[str, Any]] = []
    clinical: list[dict[str, Any]] = []
    for turn in calm_turns or []:
        if not isinstance(turn, dict):
            continue
        turn_role = turn.get("role") or "service_user"
        if role is not None and turn_role != role:
            continue
        turn_id = turn.get("turn_id")
        projected = {
            "turn_id": turn_id,
            "role": turn_role,
            "scoring_method": turn.get("scoring_method") or "unknown",
            "scored_at": turn.get("scored_at"),
            "scores": turn.get("calm_scores") or {},
            "confidence": turn.get("calm_confidence") or {},
            "trend": turn.get("trend") or {},
            "significant_changes": turn.get("significant_changes") or {},
        }
        if isinstance(turn.get("engineered_prompt"), str):
            projected["engineered_prompt"] = turn["engineered_prompt"]
        calm.append(projected)
        safety.append(
            {
                "turn_id": turn_id,
                "role": turn_role,
                "safety_state": turn.get("safety_scores") or turn.get("safety_state") or {},
            }
        )
        clinical.append(
            {
                "turn_id": turn_id,
                "role": turn_role,
                "clinical_evaluation": turn.get("clinical_evaluation") or {},
            }
        )
    return {"turns": calm}, {"turns": safety}, {"turns": clinical}


def _timeline_turns(call_score: CallScoreModel | None) -> list[dict[str, Any]]:
    """Return a chronologically stable authorized CALM turn timeline.

    The caller must have already passed the Run Details organization check.
    Engineered evaluator prompts are included solely for that authenticated
    dashboard/export path; public artifact links never expose them.
    """
    if call_score is None or not isinstance(call_score.calm_score, dict):
        return []
    raw_turns = call_score.calm_score.get("turns") or []
    if not isinstance(raw_turns, list):
        return []

    turns: list[dict[str, Any]] = []
    for position, raw_turn in enumerate(raw_turns):
        if not isinstance(raw_turn, dict):
            continue
        scores = raw_turn.get("scores") or raw_turn.get("calm_scores") or {}
        confidence = raw_turn.get("confidence") or raw_turn.get("calm_confidence") or {}
        if not isinstance(scores, dict) or not isinstance(confidence, dict):
            continue
        turn = {
            "turn_id": raw_turn.get("turn_id"),
            "role": raw_turn.get("role"),
            "scoring_method": raw_turn.get("scoring_method") or "unknown",
            "scored_at": raw_turn.get("scored_at"),
            "scores": scores,
            "confidence": confidence,
            "trend": raw_turn.get("trend") or {},
            "significant_changes": raw_turn.get("significant_changes") or {},
            "_position": position,
        }
        if isinstance(raw_turn.get("engineered_prompt"), str):
            turn["engineered_prompt"] = raw_turn["engineered_prompt"]
        turns.append(turn)

    # ISO-8601 strings sort chronologically.  Preserve write order for older
    # snapshots that predate scored_at, and number only after ordering.
    turns.sort(key=lambda item: (item.get("scored_at") or "", item["_position"]))
    for index, turn in enumerate(turns, start=1):
        turn["turn_index"] = index
        turn.pop("_position", None)
    return turns


async def _upsert_role_call_scores(
    session,
    workflow_run_id: int | None,
    calm_turns: list[dict[str, Any]],
    role: str,
) -> None:
    if workflow_run_id is None:
        return
    calm_score, safety_score, clinical_evaluation = _structured_call_scores(
        calm_turns, role=role
    )
    result = await session.execute(
        select(CallScoreModel).where(CallScoreModel.agent_run_id == workflow_run_id)
    )
    call_score = result.scalars().first()
    if call_score is None:
        session.add(
            CallScoreModel(
                agent_run_id=workflow_run_id,
                calm_score=calm_score,
                safety_score=safety_score,
                clinical_evaluation=clinical_evaluation,
            )
        )
    else:
        call_score.calm_score = calm_score
        call_score.safety_score = safety_score
        call_score.clinical_evaluation = clinical_evaluation


def _scenario_dict(scenario: SakinahScenarioModel) -> dict[str, Any]:
    return {
        "id": scenario.id,
        "sequence": scenario.sequence,
        "title": scenario.title,
        "category": scenario.category,
        "tags": scenario.tags or [],
        "mode": scenario.mode,
        "persona": scenario.persona,
        "age": scenario.age,
        "gender": scenario.gender,
        "language": scenario.language,
        "otherLanguage": scenario.other_language,
        "emotion": scenario.emotion,
        "communicationStyle": scenario.communication_style,
        "initialInformation": scenario.initial_information,
        "hiddenInformation": scenario.hidden_information,
        "disclosure": scenario.disclosure,
        "behaviour": scenario.behaviour,
        "background": scenario.background,
        "additionalFactors": scenario.additional_factors,
        "notes": scenario.notes,
        "freestylePrompt": scenario.freestyle_prompt,
        "createdAt": scenario.created_at.isoformat(),
        "updatedAt": scenario.updated_at.isoformat(),
    }


class SakinahPersistenceClient(BaseDBClient):
    async def update_call_calm_score_progress(
        self,
        *,
        workflow_run_id: int,
        organization_id: int,
        calm_turns: list[dict[str, Any]],
    ) -> bool:
        """Upsert ongoing score-only history for an ordinary Sakinah call.

        This is deliberately organization-scoped even though it is invoked by
        the call worker: a stale/background task must never write a score row
        for a run belonging to another tenant.
        """
        async with self.async_session() as session:
            authorized = (
                await session.execute(
                    select(WorkflowRunModel.id)
                    .join(WorkflowModel)
                    .where(
                        WorkflowRunModel.id == workflow_run_id,
                        WorkflowModel.organization_id == organization_id,
                    )
                )
            ).scalar_one_or_none()
            if authorized is None:
                return False
            calm_score, safety_score, clinical_evaluation = _structured_call_scores(
                calm_turns
            )
            result = await session.execute(
                select(CallScoreModel).where(
                    CallScoreModel.agent_run_id == workflow_run_id
                )
            )
            call_score = result.scalars().first()
            if call_score is None:
                session.add(
                    CallScoreModel(
                        agent_run_id=workflow_run_id,
                        calm_score=calm_score,
                        safety_score=safety_score,
                        clinical_evaluation=clinical_evaluation,
                    )
                )
            else:
                call_score.calm_score = calm_score
                call_score.safety_score = safety_score
                call_score.clinical_evaluation = clinical_evaluation
            await session.commit()
            return True

    async def get_calm_score_timeline(
        self,
        *,
        workflow_run_id: int,
        organization_id: int,
    ) -> dict[str, Any] | None:
        """Fetch authorized score tracks for a simulation or ordinary call."""
        paired = await self.get_paired_calm_score_timeline(
            workflow_run_id=workflow_run_id,
            organization_id=organization_id,
        )
        if paired is not None:
            return paired

        async with self.async_session() as session:
            run = (
                await session.execute(
                    select(WorkflowRunModel)
                    .join(WorkflowModel)
                    .where(
                        WorkflowRunModel.id == workflow_run_id,
                        WorkflowModel.organization_id == organization_id,
                    )
                )
            ).scalars().first()
            if run is None:
                return None
            score = (
                await session.execute(
                    select(CallScoreModel).where(
                        CallScoreModel.agent_run_id == workflow_run_id
                    )
                )
            ).scalars().first()
            turns = _timeline_turns(score)
            if not turns:
                return None
            role_turns: dict[str, list[dict[str, Any]]] = {}
            for turn in turns:
                role = turn.get("role") or SERVICE_USER_ROLE
                role_turns.setdefault(role, []).append(turn)
            return {
                "session_id": str(workflow_run_id),
                "status": run.call_status or ("completed" if run.is_completed else "running"),
                "roles": [
                    {
                        "role": role,
                        "run_id": workflow_run_id,
                        "workflow_id": run.workflow_id,
                        "turns": role_turns[role],
                    }
                    for role in (SAKINAH_ROLE, SERVICE_USER_ROLE)
                    if role in role_turns
                ],
            }

    async def get_paired_calm_score_timeline(
        self,
        *,
        workflow_run_id: int,
        organization_id: int,
    ) -> dict[str, Any] | None:
        """Fetch both simulation roles' score-only timelines for Run Details.

        The requested run has already been organization-authorized by the
        caller.  We scope the paired run again here so a malformed historical
        Sakinah row can never bridge an organization boundary.
        """
        async with self.async_session() as session:
            simulation = (
                await session.execute(
                    select(SakinahRunModel).where(
                        or_(
                            SakinahRunModel.run_id == workflow_run_id,
                            SakinahRunModel.service_user_run_id == workflow_run_id,
                        )
                    )
                )
            ).scalars().first()
            if simulation is None:
                return None

            role_run_ids = {
                SAKINAH_ROLE: simulation.run_id,
                SERVICE_USER_ROLE: simulation.service_user_run_id,
            }
            requested_ids = [run_id for run_id in role_run_ids.values() if run_id]
            if not requested_ids:
                return None
            authorized_rows = (
                await session.execute(
                    select(WorkflowRunModel.id, WorkflowRunModel.workflow_id)
                    .join(WorkflowModel)
                    .where(
                        WorkflowRunModel.id.in_(requested_ids),
                        WorkflowModel.organization_id == organization_id,
                    )
                )
            ).all()
            authorized_workflows = {
                row.id: row.workflow_id for row in authorized_rows
            }
            if workflow_run_id not in authorized_workflows:
                return None

            score_rows = (
                await session.execute(
                    select(CallScoreModel).where(
                        CallScoreModel.agent_run_id.in_(authorized_workflows.keys())
                    )
                )
            ).scalars().all()
            scores_by_run = {score.agent_run_id: score for score in score_rows}

            roles = []
            for role in (SAKINAH_ROLE, SERVICE_USER_ROLE):
                run_id = role_run_ids[role]
                if run_id not in authorized_workflows:
                    continue
                roles.append(
                    {
                        "role": role,
                        "run_id": run_id,
                        "workflow_id": authorized_workflows[run_id],
                        "turns": _timeline_turns(scores_by_run.get(run_id)),
                    }
                )
            return {
                "session_id": simulation.session_id,
                "status": simulation.status,
                "roles": roles,
            }

    async def update_sakinah_run_progress(
        self,
        *,
        user_id: int,
        session_id: str,
        calm_turns: list[dict[str, Any]],
        preview_data: dict[str, Any],
    ) -> SakinahRunModel | None:
        """Durably expose in-progress CALM state to the owning user only.

        The relational row owns queryable current/final score summaries.  Raw
        transcripts and recordings remain in the configured object store.
        """
        async with self.async_session() as session:
            item = (
                await session.execute(
                    select(SakinahRunModel)
                    .where(
                        SakinahRunModel.session_id == session_id,
                        SakinahRunModel.user_id == user_id,
                    )
                    .with_for_update()
                )
            ).scalars().first()
            if item is None:
                return None
            item.calm_turns = calm_turns
            item.preview_data = {**(item.preview_data or {}), **preview_data}
            # Keep each speaker's timeline queryable on its native workflow
            # run. Do not merge both roles into the Sakinah row: callers and
            # responses have different score dimensions and run details are
            # authorized independently.
            await _upsert_role_call_scores(
                session, item.run_id, calm_turns, SAKINAH_ROLE
            )
            await _upsert_role_call_scores(
                session, item.service_user_run_id, calm_turns, SERVICE_USER_ROLE
            )
            await session.commit()
            await session.refresh(item)
            return item

    async def get_sakinah_scenarios(
        self, user_id: int, search: str | None = None
    ) -> list[dict[str, Any]]:
        async with self.async_session() as session:
            query = select(SakinahScenarioModel).where(
                SakinahScenarioModel.user_id == user_id
            )
            if search and search.strip():
                pattern = f"%{search.strip()}%"
                search_text = func.concat_ws(
                    " ",
                    SakinahScenarioModel.id,
                    SakinahScenarioModel.title,
                    SakinahScenarioModel.category,
                    cast(SakinahScenarioModel.tags, Text),
                    SakinahScenarioModel.persona,
                    SakinahScenarioModel.language,
                    SakinahScenarioModel.other_language,
                    SakinahScenarioModel.communication_style,
                    SakinahScenarioModel.initial_information,
                    SakinahScenarioModel.hidden_information,
                    SakinahScenarioModel.disclosure,
                    SakinahScenarioModel.behaviour,
                    SakinahScenarioModel.background,
                    SakinahScenarioModel.additional_factors,
                    SakinahScenarioModel.notes,
                    SakinahScenarioModel.freestyle_prompt,
                )
                query = query.where(search_text.ilike(pattern))
            result = await session.execute(
                query.order_by(
                    SakinahScenarioModel.sequence.asc(),
                    SakinahScenarioModel.created_at.asc(),
                )
            )
            return [_scenario_dict(item) for item in result.scalars().all()]

    async def create_sakinah_scenario(
        self,
        user_id: int,
        scenario: dict[str, Any],
        *,
        scenario_id: str | None = None,
        sequence: int | None = None,
        created_at: datetime | None = None,
        updated_at: datetime | None = None,
    ) -> dict[str, Any]:
        async with self.async_session() as session:
            # Lock the user row so two simultaneous creates cannot receive the
            # same display sequence for one user.
            await session.execute(
                select(UserModel.id).where(UserModel.id == user_id).with_for_update()
            )
            if sequence is None:
                sequence = (
                    await session.execute(
                        select(
                            func.coalesce(func.max(SakinahScenarioModel.sequence), 0)
                        ).where(SakinahScenarioModel.user_id == user_id)
                    )
                ).scalar_one() + 1
            now = datetime.now(UTC)
            item = SakinahScenarioModel(
                id=scenario_id or str(uuid.uuid4()),
                user_id=user_id,
                sequence=sequence,
                created_at=created_at or now,
                updated_at=updated_at or now,
                **{
                    field: scenario.get(field, [] if field == "tags" else "")
                    for field in SCENARIO_FIELDS
                },
            )
            session.add(item)
            await session.commit()
            await session.refresh(item)
            return _scenario_dict(item)

    async def update_sakinah_scenario(
        self, user_id: int, scenario_id: str, scenario: dict[str, Any]
    ) -> dict[str, Any] | None:
        async with self.async_session() as session:
            result = await session.execute(
                select(SakinahScenarioModel).where(
                    SakinahScenarioModel.id == scenario_id,
                    SakinahScenarioModel.user_id == user_id,
                )
            )
            item = result.scalars().first()
            if item is None:
                return None
            for field in SCENARIO_FIELDS:
                setattr(item, field, scenario.get(field, [] if field == "tags" else ""))
            item.updated_at = datetime.now(UTC)
            await session.commit()
            await session.refresh(item)
            return _scenario_dict(item)

    async def delete_sakinah_scenario(self, user_id: int, scenario_id: str) -> bool:
        async with self.async_session() as session:
            result = await session.execute(
                delete(SakinahScenarioModel).where(
                    SakinahScenarioModel.id == scenario_id,
                    SakinahScenarioModel.user_id == user_id,
                )
            )
            await session.commit()
            return result.rowcount > 0

    async def create_sakinah_run(
        self,
        *,
        session_id: str,
        user_id: int,
        agent_id: int,
        run_id: int,
        scenario: str,
        started_at: datetime,
        service_user_agent_id: int | None = None,
        service_user_run_id: int | None = None,
        experiment_mode: str | None = None,
    ) -> SakinahRunModel:
        async with self.async_session() as session:
            item = SakinahRunModel(
                session_id=session_id,
                user_id=user_id,
                agent_id=agent_id,
                run_id=run_id,
                service_user_agent_id=service_user_agent_id,
                service_user_run_id=service_user_run_id,
                scenario=scenario,
                started_at=started_at,
                experiment_mode=experiment_mode,
            )
            session.add(item)
            await session.commit()
            await session.refresh(item)
            return item

    async def get_sakinah_runs(
        self, user_id: int, limit: int = 50
    ) -> list[SakinahRunModel]:
        async with self.async_session() as session:
            result = await session.execute(
                select(SakinahRunModel)
                .where(SakinahRunModel.user_id == user_id)
                .order_by(SakinahRunModel.started_at.desc())
                .limit(limit)
            )
            return list(result.scalars().all())

    async def get_sakinah_run(
        self, user_id: int, session_id: str
    ) -> SakinahRunModel | None:
        async with self.async_session() as session:
            result = await session.execute(
                select(SakinahRunModel).where(
                    SakinahRunModel.session_id == session_id,
                    SakinahRunModel.user_id == user_id,
                )
            )
            return result.scalars().first()

    async def complete_sakinah_run(
        self,
        *,
        user_id: int,
        session_id: str,
        status: str,
        ended_at: datetime,
        transcript: str,
        conversation: list[dict[str, Any]],
        preview_data: dict[str, Any],
        recording_url: str | None = None,
        transcript_url: str | None = None,
        recording_file_reference: dict[str, Any] | None = None,
        calm_turns: list[dict[str, Any]] | None = None,
        timings: dict[str, Any] | None = None,
    ) -> SakinahRunModel | None:
        async with self.async_session() as session:
            result = await session.execute(
                select(SakinahRunModel)
                .where(
                    SakinahRunModel.session_id == session_id,
                    SakinahRunModel.user_id == user_id,
                )
                .with_for_update()
            )
            item = result.scalars().first()
            if item is None:
                return None
            item.status = status
            item.ended_at = ended_at
            item.transcript = transcript
            item.conversation = conversation
            item.preview_data = preview_data
            item.recording_url = recording_url
            item.transcript_url = transcript_url
            item.recording_file_reference = recording_file_reference or {}
            item.calm_turns = calm_turns or []
            item.timings = timings or {}

            workflow_run = await session.get(WorkflowRunModel, item.run_id)
            if workflow_run is not None:
                workflow_run.latency_metrics = {
                    **(workflow_run.latency_metrics or {}),
                    "simulation": timings or {},
                }
            await _upsert_role_call_scores(
                session, item.run_id, calm_turns or [], SAKINAH_ROLE
            )
            await _upsert_role_call_scores(
                session,
                item.service_user_run_id,
                calm_turns or [],
                SERVICE_USER_ROLE,
            )
            await session.commit()
            await session.refresh(item)
            return item

    async def get_workflow_run_artifacts_for_user(
        self, user_id: int, run_id: int
    ) -> dict[str, Any] | None:
        async with self.async_session() as session:
            result = await session.execute(
                select(WorkflowRunModel)
                .join(WorkflowModel, WorkflowRunModel.workflow_id == WorkflowModel.id)
                .where(
                    WorkflowRunModel.id == run_id,
                    WorkflowModel.user_id == user_id,
                )
            )
            run = result.scalars().first()
            if run is None:
                return None
            return {
                "recording_url": run.recording_url,
                "transcript_url": run.transcript_url,
                "recording_file_reference": (run.extra or {}).get("recordings", {}),
            }

    async def sync_sakinah_run_artifacts(self, workflow_run_id: int) -> None:
        """Copy native workflow artifact references into the white-label run."""
        async with self.async_session() as session:
            run_result = await session.execute(
                select(WorkflowRunModel, WorkflowModel.user_id)
                .join(WorkflowModel, WorkflowRunModel.workflow_id == WorkflowModel.id)
                .where(WorkflowRunModel.id == workflow_run_id)
            )
            workflow_row = run_result.first()
            if workflow_row is None:
                return
            workflow_run, workflow_owner_id = workflow_row
            white_label_result = await session.execute(
                select(SakinahRunModel).where(
                    SakinahRunModel.user_id == workflow_owner_id,
                    or_(
                        SakinahRunModel.run_id == workflow_run_id,
                        SakinahRunModel.service_user_run_id == workflow_run_id,
                    ),
                )
            )
            white_label_run = white_label_result.scalars().first()
            if white_label_run is None:
                return
            role = (
                "service_user"
                if white_label_run.service_user_run_id == workflow_run_id
                else "sakinah"
            )
            references = dict(white_label_run.recording_file_reference or {})
            role_recordings = (workflow_run.extra or {}).get("recordings", {})
            # Artifact fields are written independently. Do not replace a
            # previously saved reference with an empty object if reconciliation
            # runs between the recording URL update and the recordings metadata
            # update.
            if role_recordings:
                existing_role_recordings = references.get(role)
                if isinstance(existing_role_recordings, dict):
                    references[role] = {
                        **existing_role_recordings,
                        **role_recordings,
                    }
                else:
                    references[role] = role_recordings
            white_label_run.recording_file_reference = references
            if role == "sakinah":
                if workflow_run.recording_url:
                    white_label_run.recording_url = workflow_run.recording_url
                if workflow_run.transcript_url:
                    white_label_run.transcript_url = workflow_run.transcript_url
            await session.commit()
