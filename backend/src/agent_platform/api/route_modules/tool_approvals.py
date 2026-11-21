"""Tool Approval 预览、确认和拒绝路由。"""

from __future__ import annotations

from fastapi import APIRouter

from agent_platform.api.schemas import ToolApprovalApproveRequest, ToolApprovalRejectRequest

from .common import Identity, ToolApprovals, _check_tenant

router = APIRouter()


@router.get("/tool-approvals/{approval_id}")
def get_tool_approval(
    approval_id: str, identity: Identity, service: ToolApprovals
) -> dict[str, object]:
    """只向 Run 提交者返回解密后且摘要校验通过的预览正文。"""

    details = service.get_preview(identity, approval_id)
    request = details.request
    return {
        "approval_id": request.approval_id,
        "run_id": request.run_id,
        "session_id": request.session_id,
        "conversation_id": request.conversation_id,
        "requested_by": request.requested_by,
        "tool_id": request.tool_id,
        "tool_version": request.tool_version,
        "mcp_server_profile_id": request.mcp_server_profile_id,
        "mcp_server_profile_version": request.mcp_server_profile_version,
        "preview": details.preview_text,
        "preview_sha256": request.preview_sha256,
        "expires_at": request.expires_at,
        "state": request.state.value,
    }


@router.post("/tool-approvals/{approval_id}/approve")
def approve_tool_approval(
    approval_id: str,
    request: ToolApprovalApproveRequest,
    identity: Identity,
    service: ToolApprovals,
) -> dict[str, object]:
    """确认精确 preview digest；仅排队一次性动作，不执行 MCP 写调用。"""

    _check_tenant(identity, request)
    result = service.approve(
        identity,
        approval_id,
        preview_sha256=request.preview_sha256,
        idempotency_key=request.idempotency_key,
    )
    return {
        "approval_id": result.approval_id,
        "run_id": result.run_id,
        "state": result.state.value,
        "approved_by": result.approved_by,
        "approved_at": result.approved_at,
    }


@router.post("/tool-approvals/{approval_id}/reject")
def reject_tool_approval(
    approval_id: str,
    request: ToolApprovalRejectRequest,
    identity: Identity,
    service: ToolApprovals,
) -> dict[str, object]:
    """由 Run 提交者拒绝预览，并关闭绑定的单次写操作。"""

    _check_tenant(identity, request)
    result = service.reject(identity, approval_id)
    return {
        "approval_id": result.approval_id,
        "run_id": result.run_id,
        "state": result.state.value,
        "rejected_by": result.rejected_by,
        "finished_at": result.finished_at,
    }
