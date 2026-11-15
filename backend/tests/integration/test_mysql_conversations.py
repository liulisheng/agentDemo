"""Conversation 与消息正文引用的 MySQL/LocalObjectStore 重启契约。"""

from __future__ import annotations

import os
import secrets
from pathlib import Path

import pytest

mysql = pytest.importorskip("mysql.connector")


@pytest.fixture
def mysql_conversation_database():
    if os.environ.get("AGENT_PLATFORM_MYSQL_TEST_ALLOW_CREATE_DATABASE") != "1":
        pytest.skip("set AGENT_PLATFORM_MYSQL_TEST_ALLOW_CREATE_DATABASE=1 for disposable MySQL integration")
    database = f"agent_platform_conversation_test_{secrets.token_hex(5)}"
    config = {
        "host": os.environ.get("MYSQL_HOST", "127.0.0.1"),
        "port": int(os.environ.get("MYSQL_PORT", "3306")),
        "user": os.environ.get("MYSQL_USER", "root"),
        "password": os.environ.get("MYSQL_PASSWORD", ""),
    }
    connection = mysql.connect(**config)
    cursor = connection.cursor()
    try:
        cursor.execute(f"CREATE DATABASE `{database}` CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci")
        connection.commit()
    finally:
        cursor.close()
        connection.close()
    try:
        from agent_platform.infrastructure.migrations import MigrationRunner

        MigrationRunner(
            lambda: mysql.connect(**config, database=database),
            Path(__file__).parents[3] / "docs" / "migrations",
        ).apply()
        connection = mysql.connect(**config, database=database)
        cursor = connection.cursor()
        try:
            cursor.execute("INSERT INTO platforms (platform_id,code,name,status) VALUES ('plt-local','local','本地','ACTIVE')")
            cursor.execute("INSERT INTO tenants (platform_id,tenant_id,code,name,status) VALUES ('plt-local','tenant-a','tenant-a','A','ACTIVE')")
            cursor.execute("INSERT INTO tenants (platform_id,tenant_id,code,name,status) VALUES ('plt-local','tenant-b','tenant-b','B','ACTIVE')")
            cursor.execute(
                "INSERT INTO users (platform_id,tenant_id,user_id,external_subject,username,display_name,status) "
                "VALUES ('plt-local','tenant-a','admin-local','subject-local','admin-local','管理员','ACTIVE')"
            )
            connection.commit()
        finally:
            cursor.close()
            connection.close()
        yield database, config
    finally:
        connection = mysql.connect(**config)
        cursor = connection.cursor()
        try:
            cursor.execute(f"DROP DATABASE IF EXISTS `{database}`")
            connection.commit()
        finally:
            cursor.close()
            connection.close()


def test_conversation_and_message_body_reference_survive_restart(
    tmp_path: Path, mysql_conversation_database
) -> None:
    from agent_platform.adapters.identity.fake import FakeIdentityProvider
    from agent_platform.adapters.runtime.fake import FakeRuntimeAdapter
    from agent_platform.application.catalog_service import CatalogService
    from agent_platform.application.control_plane import ControlPlaneService
    from agent_platform.application.conversations import ConversationService
    from agent_platform.domain.catalog import SkillType
    from agent_platform.domain.common import DomainError
    from agent_platform.infrastructure.mysql_catalog import MySqlCatalogRepository
    from agent_platform.infrastructure.mysql_conversations import MySqlConversationRepository
    from agent_platform.infrastructure.mysql_execution import MySqlExecutionRepository
    from agent_platform.infrastructure.mysql_storage import MySqlMediaAssetRepository
    from agent_platform.ports.storage import LocalObjectStore

    database, config = mysql_conversation_database
    connection_factory = lambda: mysql.connect(**config, database=database)
    identity = FakeIdentityProvider().resolve_identity("admin-local")
    catalog = CatalogService(MySqlCatalogRepository(connection_factory))
    skill = catalog.create_skill(identity, "助手技能", SkillType.INSTRUCTION, "回答用户", [])
    catalog.publish_skill(identity, skill.skill_id)
    employee = catalog.create_employee(identity, "助手", "支持", "消息闭环")
    version = catalog.create_employee_version(identity, employee.employee_id, "系统指令", [skill.skill_id], [])
    catalog.publish_employee_version(identity, employee.employee_id, version.version)
    deployment = catalog.deploy(identity, employee.employee_id, version.version)
    execution = MySqlExecutionRepository(connection_factory)
    session = ControlPlaneService(catalog, FakeRuntimeAdapter(), persistence=execution).create_session(
        identity, deployment.deployment_id
    )
    object_root = tmp_path / "conversation-objects"

    def object_store():
        return LocalObjectStore(
            object_root,
            metadata_repository=MySqlMediaAssetRepository(connection_factory),
        )

    service = ConversationService(MySqlConversationRepository(connection_factory), object_store())
    conversation = service.create_conversation(identity, "本地支持", is_group=False)
    binding = service.bind_agent(
        identity, conversation.conversation_id, employee.employee_id, "助手", session.session_id
    )
    message = service.ingest(
        identity,
        conversation.conversation_id,
        identity.user_id,
        "请帮我检查这条消息正文是否跨进程恢复。",
    )

    restarted = ConversationService(MySqlConversationRepository(connection_factory), object_store())
    assert restarted.get_conversation(identity, conversation.conversation_id).title == "本地支持"
    assert restarted.route(identity, message.message_id) == (session.session_id,)
    loaded = restarted.list_messages(identity, conversation.conversation_id)
    assert loaded[0].text == message.text
    assert loaded[0].message_id == message.message_id
    assert binding.session_id == session.session_id

    other = FakeIdentityProvider().resolve_identity("admin-other")
    with pytest.raises(DomainError) as cross_tenant:
        restarted.list_messages(other, conversation.conversation_id)
    assert cross_tenant.value.code == "RESOURCE_NOT_FOUND"

    connection = connection_factory()
    cursor = connection.cursor()
    try:
        cursor.execute(
            "SELECT content_ref, content_sha256, byte_length, content_type FROM messages "
            "WHERE platform_id=%s AND tenant_id=%s AND message_id=%s",
            (identity.platform_id, identity.tenant_id, message.message_id),
        )
        row = cursor.fetchone()
        assert row[0].startswith("object:plt-local:tenant-a:")
        assert message.text.encode("utf-8") not in repr(row).encode("utf-8")
    finally:
        cursor.close()
        connection.close()
