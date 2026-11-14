from agent_platform.adapters.identity.fake import FakeIdentityProvider
from agent_platform.application.tool_audit import InMemoryToolAuditRepository
from agent_platform.domain.usage import ToolInvocationRecord


def test_tool_audit_preserves_protocol_version_and_error_category() -> None:
    identity = FakeIdentityProvider().resolve_identity("admin-local")
    repo = InMemoryToolAuditRepository()
    record = ToolInvocationRecord("invoke-1", "run-1", identity.tenant_id, "echo", "builtin", "1", "failed", "POLICY_DENIED")
    repo.record(identity, record)
    assert repo.list(identity, "run-1")[0].error_category == "POLICY_DENIED"
