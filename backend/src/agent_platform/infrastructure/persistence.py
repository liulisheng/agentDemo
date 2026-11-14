"""控制面 Session/Run 持久化端口的内存桥接实现，模拟 fencing token 语义。"""

from __future__ import annotations

from dataclasses import dataclass
from uuid import uuid4

from agent_platform.domain.common import DomainError
from agent_platform.domain.identity import IdentityContext


@dataclass(frozen=True, slots=True)
class SessionPersistenceRecord:
    session_id: str
    tenant_id: str
    deployment_id: str
    trace_id: str
    fencing_token: int = 0


@dataclass(frozen=True, slots=True)
class Lease:
    session_id: str
    fencing_token: int


class InMemorySessionRunPersistence:
    """为后续 MySQL Repository 固化租户键、Run 快照和旧 Worker 拒绝规则。"""

    def __init__(self) -> None:
        self._sessions: dict[tuple[str, str, str], SessionPersistenceRecord] = {}
        self._runs: dict[tuple[str, str, str], dict[str, object]] = {}

    def create_session(self, identity: IdentityContext, deployment_id: str, trace_id: str) -> SessionPersistenceRecord:
        session = SessionPersistenceRecord(f"session-{uuid4().hex}", identity.tenant_id, deployment_id, trace_id)
        self._sessions[(identity.platform_id, identity.tenant_id, session.session_id)] = session
        return session

    def register_session(self, identity: IdentityContext, session_id: str, deployment_id: str, trace_id: str) -> SessionPersistenceRecord:
        session = SessionPersistenceRecord(session_id, identity.tenant_id, deployment_id, trace_id)
        self._sessions[(identity.platform_id, identity.tenant_id, session_id)] = session
        return session

    def acquire_lease(self, identity: IdentityContext, session_id: str) -> Lease:
        key = (identity.platform_id, identity.tenant_id, session_id)
        current = self._sessions.get(key)
        if current is None:
            raise DomainError("RESOURCE_NOT_FOUND", "Session 不存在")
        updated = SessionPersistenceRecord(current.session_id, current.tenant_id, current.deployment_id, current.trace_id, current.fencing_token + 1)
        self._sessions[key] = updated
        return Lease(session_id, updated.fencing_token)

    def save_run(
        self,
        identity: IdentityContext,
        session_id: str,
        run_id: str,
        fencing_token: int,
        snapshot: dict[str, object],
        runtime_refs: dict[str, object],
    ) -> None:
        session = self._sessions.get((identity.platform_id, identity.tenant_id, session_id))
        if session is None:
            raise DomainError("RESOURCE_NOT_FOUND", "Session 不存在")
        if fencing_token != session.fencing_token:
            raise DomainError("FENCING_TOKEN_STALE", "Worker fencing token 已过期")
        self._runs[(identity.platform_id, identity.tenant_id, run_id)] = {
            "session_id": session_id,
            "snapshot": dict(snapshot),
            "runtime_refs": dict(runtime_refs),
        }

    def get_run(self, identity: IdentityContext, run_id: str) -> dict[str, object]:
        return dict(self._runs.get((identity.platform_id, identity.tenant_id, run_id), {}))
