import pytest

from agent_platform.adapters.identity.fake import FakeIdentityProvider
from agent_platform.domain.common import DomainError
from agent_platform.infrastructure.persistence import InMemorySessionRunPersistence


def test_persistence_bridge_enforces_fencing_and_snapshots() -> None:
    identity = FakeIdentityProvider().resolve_identity("admin-local")
    persistence = InMemorySessionRunPersistence()
    session = persistence.create_session(identity, "deployment-1", "trace-1")
    lease = persistence.acquire_lease(identity, session.session_id)
    persistence.save_run(
        identity,
        session.session_id,
        "run-1",
        lease.fencing_token,
        {"employee_version": 1, "execution_profile_id": "profile-fake", "trace_id": "trace-1"},
        {"runtime_session_ref": "fake-session-1", "result_ref": "fake-result-1"},
    )
    assert persistence.get_run(identity, "run-1")["snapshot"]["employee_version"] == 1
    with pytest.raises(DomainError) as error:
        persistence.save_run(identity, session.session_id, "run-2", lease.fencing_token - 1, {}, {})
    assert error.value.code == "FENCING_TOKEN_STALE"
