"""Tool Approval HTTP 路由只读预览并改变审批状态，不调用 MCP。"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from fastapi.testclient import TestClient

from agent_platform.api.app import create_app
from agent_platform.domain.mcp import ToolApprovalRequest, ToolApprovalState


class _FakeToolApprovalService:
    def __init__(self) -> None:
        self.request = ToolApprovalRequest(
            platform_id="plt-local",
            tenant_id="tenant-a",
            approval_id="approval-1",
            run_id="run-1",
            session_id="session-1",
            conversation_id="conversation-1",
            requested_by="admin-local",
            tool_invocation_id="span-1",
            tool_id="tool-1",
            tool_version=2,
            mcp_server_profile_id="profile-1",
            mcp_server_profile_version=3,
            arguments_ref="object:arguments",
            arguments_sha256="a" * 64,
            preview_ref="object:preview",
            preview_sha256="b" * 64,
            expires_at=datetime.now(UTC) + timedelta(minutes=5),
        )
        self.calls: list[tuple[object, ...]] = []

    def get_preview(self, identity, approval_id):
        self.calls.append(("get", identity, approval_id))
        return type(
            "Preview",
            (),
            {"request": self.request, "preview_text": '{"state":"OFFLINE"}'},
        )()

    def approve(self, identity, approval_id, *, preview_sha256, idempotency_key):
        self.calls.append(("approve", identity, approval_id, preview_sha256, idempotency_key))
        self.request = self.request.approve(
            identity,
            preview_sha256,
            idempotency_key=idempotency_key,
            now=datetime.now(UTC),
        )
        return self.request

    def reject(self, identity, approval_id):
        self.calls.append(("reject", identity, approval_id))
        self.request = self.request.reject(identity, now=datetime.now(UTC))
        return self.request


def test_tool_approval_routes_read_preview_and_forward_requester_identity() -> None:
    app = create_app(mode="fake")
    service = _FakeToolApprovalService()
    app.state.tool_approval_service = service
    client = TestClient(app)
    headers = {"X-Fake-User": "admin-local"}

    preview = client.get("/api/v1/tool-approvals/approval-1", headers=headers)
    assert preview.status_code == 200
    assert preview.json()["preview"] == '{"state":"OFFLINE"}'
    assert preview.json()["tool_version"] == 2
    assert service.calls[0][0] == "get"
    assert service.calls[0][1].user_id == "admin-local"

    approved = client.post(
        "/api/v1/tool-approvals/approval-1/approve",
        headers=headers,
        json={"preview_sha256": "b" * 64, "idempotency_key": "approve-1"},
    )
    assert approved.status_code == 200
    assert approved.json()["state"] == ToolApprovalState.APPROVED.value
    assert service.calls[-1][0] == "approve"
    assert service.calls[-1][-1] == "approve-1"


def test_tool_approval_reject_route_forwards_requester_identity() -> None:
    app = create_app(mode="fake")
    service = _FakeToolApprovalService()
    app.state.tool_approval_service = service
    client = TestClient(app)

    rejected = client.post(
        "/api/v1/tool-approvals/approval-1/reject",
        headers={"X-Fake-User": "admin-local"},
        json={},
    )

    assert rejected.status_code == 200
    assert rejected.json()["state"] == ToolApprovalState.REJECTED.value
    assert service.calls[0][0] == "reject"
    assert service.calls[0][1].user_id == "admin-local"
