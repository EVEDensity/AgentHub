"""Atomic workspace selectors; MissionRepository retains persistence authority."""
from __future__ import annotations

import sqlite3
from app.domain import Mission, WorkUnit


class WorkspaceClaimSelectionMixin:
    async def get_workspace_bound_work_unit_for_claim(
        self,
        workspace_id: str,
        *,
        agent_id: str,
        adapter_type: str,
        supported_work_unit_kinds: tuple[str, ...],
        runner_id: str | None = None,
        resume_mission_id: str | None = None,
        existing_lease_only: bool = False,
    ) -> tuple[Mission, WorkUnit] | None:
        """Lock one ready unit, optionally reopening this runner's lease.

        Normal polls prefer ready work over reopening an owned live lease.
        Explicit recovery considers only the selected Mission's owned live
        LEASED/RUNNING units, preserving their lease and attempt.
        """
        include_leased = bool(runner_id and runner_id.strip())
        if (resume_mission_id is not None and not resume_mission_id.strip()) or (
            (resume_mission_id is not None or existing_lease_only) and not include_leased
        ):
            raise ValueError("resume_mission_id requires an explicit Mission and Runner")

        if self._is_sqlite_connection():
            selector = self._select_workspace_sqlite
        else:
            selector = self._select_workspace_postgres
        return await selector(
            workspace_id, agent_id=agent_id, adapter_type=adapter_type,
            supported_work_unit_kinds=supported_work_unit_kinds, runner_id=runner_id,
            include_leased=include_leased, resume_mission_id=resume_mission_id,
            existing_lease_only=existing_lease_only,
        )

    async def _select_workspace_sqlite(
        self, workspace_id: str, *, agent_id: str, adapter_type: str,
        supported_work_unit_kinds: tuple[str, ...], runner_id: str | None,
        include_leased: bool, resume_mission_id: str | None,
        existing_lease_only: bool,
    ) -> tuple[Mission, WorkUnit] | None:
        kinds = list(supported_work_unit_kinds)
        kind_offset = 5 if include_leased else 4
        kind_placeholders = ", ".join(
            f"${kind_offset + index}" for index in range(len(kinds))
        )
        status_clause = (
            "AND (candidate.status IN ('PENDING', 'RETRYING') OR ("
            "candidate.status IN ('LEASED', 'RUNNING') AND candidate.lease IS NOT NULL "
            "AND json_extract(candidate.lease, '$.runnerId')=$4 "
            "AND julianday(json_extract(candidate.lease, '$.expiresAt')) > julianday('now')))"
            if include_leased
            else "AND candidate.status IN ('PENDING', 'RETRYING')"
        )
        query_args = (
            [workspace_id, agent_id, adapter_type, runner_id, *kinds]
            if include_leased
            else [workspace_id, agent_id, adapter_type, *kinds]
        )
        mission_clause = ""
        if resume_mission_id is not None:
            query_args.append(resume_mission_id)
            mission_clause = f"AND mission.id=${len(query_args)}"
        if resume_mission_id is not None or existing_lease_only:
            status_clause = (
                "AND candidate.status IN ('LEASED','RUNNING') "
                "AND candidate.lease IS NOT NULL "
                "AND json_extract(candidate.lease, '$.runnerId')=$4 "
                "AND julianday(json_extract(candidate.lease, '$.expiresAt')) > julianday('now')"
            )
        try:
            candidate_rows = await self._fetch_all(
                f"""SELECT
                          mission.id AS selected_mission_id,
                          mission.workspace_id AS selected_workspace_id,
                          mission.title AS selected_title,
                          mission.objective AS selected_objective,
                          mission.source AS selected_source,
                          mission.contract_id AS selected_contract_id,
                          mission.contract_version AS selected_contract_version,
                          mission.status AS selected_status,
                          mission.plan_version AS selected_plan_version,
                          mission.created_by AS selected_created_by,
                          mission.created_at AS selected_created_at,
                          mission.updated_at AS selected_updated_at,
                          candidate.id, candidate.mission_id,
                          candidate.parent_work_unit_id, candidate.assigned_agent_id,
                          candidate.kind, candidate.dependencies, candidate.input_refs,
                          candidate.expected_outputs, candidate.required_capabilities,
                          candidate.assigned_adapter, candidate.status,
                          candidate.attempt, candidate.lease
                   FROM missions AS mission
                   JOIN work_units AS candidate
                     ON candidate.mission_id=mission.id
                   WHERE mission.workspace_id=$1
                     AND mission.status='RUNNING'
                     {mission_clause}
                     AND (
                         candidate.parent_work_unit_id IS NOT NULL
                         OR (
                             mission.source->>'type' = 'a2a.inbound'
                             AND candidate.parent_work_unit_id IS NULL
                             AND candidate.kind = 'a2a.inbound'
                             AND candidate.assigned_adapter <> 'a2a.outbound'
                         )
                         OR (
                             mission.source->>'type' = 'a2a'
                             AND candidate.parent_work_unit_id IS NULL
                             AND candidate.kind = 'a2a.delegate'
                             AND candidate.assigned_adapter = 'a2a.outbound'
                         )
                         OR (
                             mission.source->>'type' = 'mission.fork'
                             AND candidate.parent_work_unit_id IS NULL
                             AND candidate.kind = 'mission.fork'
                             AND candidate.assigned_adapter <> 'a2a.outbound'
                         )
                         OR (
                             (mission.source->>'type' = 'manual' OR (mission.source->>'type' = 'chat' AND candidate.assigned_adapter = 'function-calling'))
                             AND candidate.parent_work_unit_id IS NULL
                             AND candidate.kind = 'desktop.task'
                             AND candidate.assigned_adapter <> 'a2a.outbound'
                         )
                     )
                     AND candidate.assigned_agent_id=$2
                     AND candidate.assigned_adapter=$3
                     AND candidate.kind IN ({kind_placeholders})
                     {status_clause}
                     AND NOT EXISTS (
                         SELECT 1
                         FROM json_each(candidate.dependencies) AS dep
                         LEFT JOIN work_units AS dependency_unit
                           ON dependency_unit.id = dep.value
                         WHERE dependency_unit.id IS NULL
                            OR dependency_unit.mission_id <> candidate.mission_id
                            OR dependency_unit.status <> 'SUCCEEDED'
                     )
                   ORDER BY CASE WHEN candidate.status IN ('PENDING','RETRYING') THEN 0 ELSE 1 END ASC, (
                       SELECT COUNT(*)
                       FROM work_units AS active_unit
                       WHERE active_unit.mission_id=mission.id
                         AND active_unit.status IN ('LEASED', 'RUNNING')
                   ) ASC,
                   mission.created_at ASC,
                   mission.id ASC,
                   candidate.id ASC
                   LIMIT 32""",
                *query_args,
            )
        except sqlite3.OperationalError as exc:
            # SQLite's json_extract raises on corrupt lease JSON. Such a
            # row is ambiguous and must be treated as unavailable rather
            # than allowing a claim path to crash or replay work.
            if "malformed JSON" in str(exc):
                return None
            raise
        row = self._first_claimable_row(candidate_rows)
        if row is None:
            return None
        return self._mission_from_claim_row(row), self._work_unit_from_row(row)

    async def _select_workspace_postgres(
        self, workspace_id: str, *, agent_id: str, adapter_type: str,
        supported_work_unit_kinds: tuple[str, ...], runner_id: str | None,
        include_leased: bool, resume_mission_id: str | None,
        existing_lease_only: bool,
    ) -> tuple[Mission, WorkUnit] | None:
        status_clause = (
            "AND (candidate.status IN ('PENDING', 'RETRYING') OR ("
            "candidate.status IN ('LEASED', 'RUNNING') AND candidate.lease IS NOT NULL "
            "AND candidate.lease->>'runnerId'=$4 "
            "AND (candidate.lease->>'expiresAt')::timestamptz > CURRENT_TIMESTAMP))"
            if include_leased
            else "AND candidate.status IN ('PENDING', 'RETRYING')"
        )
        kind_array_placeholder = "$5" if include_leased else "$4"
        query_args = (
            [workspace_id, agent_id, adapter_type, runner_id, list(supported_work_unit_kinds)]
            if include_leased
            else [workspace_id, agent_id, adapter_type, list(supported_work_unit_kinds)]
        )
        mission_clause = ""
        if resume_mission_id is not None:
            query_args.append(resume_mission_id)
            mission_clause = f"AND mission.id=${len(query_args)}"
        if resume_mission_id is not None or existing_lease_only:
            status_clause = (
                "AND candidate.status IN ('LEASED','RUNNING') AND candidate.lease IS NOT NULL "
                "AND candidate.lease->>'runnerId'=$4 "
                "AND (candidate.lease->>'expiresAt')::timestamptz > CURRENT_TIMESTAMP"
            )
        row = await self._fetch_one(
            f"""SELECT
                      mission.id AS selected_mission_id,
                      mission.workspace_id AS selected_workspace_id,
                      mission.title AS selected_title,
                      mission.objective AS selected_objective,
                      mission.source AS selected_source,
                      mission.contract_id AS selected_contract_id,
                      mission.contract_version AS selected_contract_version,
                      mission.status AS selected_status,
                      mission.plan_version AS selected_plan_version,
                      mission.created_by AS selected_created_by,
                      mission.created_at AS selected_created_at,
                      mission.updated_at AS selected_updated_at,
                      candidate.id, candidate.mission_id,
                      candidate.parent_work_unit_id, candidate.assigned_agent_id,
                      candidate.kind, candidate.dependencies, candidate.input_refs,
                      candidate.expected_outputs, candidate.required_capabilities,
                      candidate.assigned_adapter, candidate.status,
                      candidate.attempt, candidate.lease
               FROM missions AS mission
               JOIN work_units AS candidate
                 ON candidate.mission_id=mission.id
               WHERE mission.workspace_id=$1
                 AND mission.status='RUNNING'
                 {mission_clause}
                 AND (
                     candidate.parent_work_unit_id IS NOT NULL
                     OR (
                         mission.source->>'type' = 'a2a.inbound'
                         AND candidate.parent_work_unit_id IS NULL
                         AND candidate.kind = 'a2a.inbound'
                         AND candidate.assigned_adapter <> 'a2a.outbound'
                     )
                     OR (
                         mission.source->>'type' = 'a2a'
                         AND candidate.parent_work_unit_id IS NULL
                         AND candidate.kind = 'a2a.delegate'
                         AND candidate.assigned_adapter = 'a2a.outbound'
                     )
                     OR (
                         mission.source->>'type' = 'mission.fork'
                         AND candidate.parent_work_unit_id IS NULL
                         AND candidate.kind = 'mission.fork'
                         AND candidate.assigned_adapter <> 'a2a.outbound'
                     )
                     OR (
                         (mission.source->>'type' = 'manual' OR (mission.source->>'type' = 'chat' AND candidate.assigned_adapter = 'function-calling'))
                         AND candidate.parent_work_unit_id IS NULL
                         AND candidate.kind = 'desktop.task'
                         AND candidate.assigned_adapter <> 'a2a.outbound'
                     )
                 )
                 AND candidate.assigned_agent_id=$2
                 AND candidate.assigned_adapter=$3
                 AND candidate.kind = ANY({kind_array_placeholder}::text[])
               {status_clause}
                 AND NOT EXISTS (
                     SELECT 1
                     FROM jsonb_array_elements_text(candidate.dependencies) AS dep(id)
                     LEFT JOIN work_units AS dependency_unit
                       ON dependency_unit.id=dep.id
                     WHERE dependency_unit.id IS NULL
                        OR dependency_unit.mission_id <> candidate.mission_id
                        OR dependency_unit.status <> 'SUCCEEDED'
                 )
               ORDER BY CASE WHEN candidate.status IN ('PENDING','RETRYING') THEN 0 ELSE 1 END ASC, (
                   SELECT COUNT(*)
                   FROM work_units AS active_unit
                   WHERE active_unit.mission_id=mission.id
                     AND active_unit.status IN ('LEASED', 'RUNNING', 'VERIFYING')
               ) ASC,
               mission.created_at ASC,
               mission.id ASC,
               candidate.id ASC
               LIMIT 1
               FOR UPDATE OF mission, candidate SKIP LOCKED""",
            *query_args,
        )
        if row is None:
            return None
        mission = self._mission_from_claim_row(row)
        return mission, self._work_unit_from_row(row)
