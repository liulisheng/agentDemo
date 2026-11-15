"""Hub Package manifest 编码必须稳定，且不得泄漏租户凭据引用。"""

from agent_platform.domain.resource_center import build_hub_manifest


def test_manifest_digest_is_stable_across_mapping_key_order() -> None:
    left = build_hub_manifest(
        resource_type="SKILL",
        name="客服问答",
        version=3,
        content={"instruction": "回答用户", "input_schema": {"type": "object"}},
        dependencies=[
            {"sequence_no": 2, "type": "TOOL", "resource_id": "tool-b", "version": 1},
            {"sequence_no": 1, "type": "TOOL", "resource_id": "tool-a", "version": 2},
        ],
    )
    right = build_hub_manifest(
        resource_type="SKILL",
        name="客服问答",
        version=3,
        content={"input_schema": {"type": "object"}, "instruction": "回答用户"},
        dependencies=[
            {"version": 2, "resource_id": "tool-a", "type": "TOOL", "sequence_no": 1},
            {"version": 1, "resource_id": "tool-b", "type": "TOOL", "sequence_no": 2},
        ],
    )

    assert left.manifest_json == right.manifest_json
    assert left.sha256 == right.sha256
    assert [item["resource_id"] for item in left.data["dependencies"]] == ["tool-a", "tool-b"]


def test_tool_manifest_omits_secret_values_and_secret_refs_but_keeps_schema() -> None:
    secret_value = "do-not-publish-this-token"
    package = build_hub_manifest(
        resource_type="TOOL",
        name="工单查询",
        version=2,
        content={
            "protocol": "api",
            "auth_secret_ref": "tenant-a/secret/ticket-api",
            "api_key": secret_value,
            "input_schema": {
                "type": "object",
                "properties": {"password": {"type": "string"}},
            },
        },
    )

    assert "tenant-a/secret/ticket-api" not in package.manifest_json
    assert secret_value not in package.manifest_json
    assert "auth_secret_ref" not in package.manifest_json
    assert package.data["requires_recipient_credentials"] is True
    assert package.data["content"]["input_schema"]["properties"]["password"]["type"] == "string"
