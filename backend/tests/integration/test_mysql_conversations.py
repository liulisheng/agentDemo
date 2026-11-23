"""Conversation 与消息正文引用的 MySQL/LocalObjectStore 重启契约。"""

from __future__ import annotations

import os
import secrets
from pathlib import Path

import pytest

from tests._runtime_config_helpers import configure_in_memory_deployment

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
    deployment = configure_in_memory_deployment(catalog, identity, deployment)
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


def test_employee_conversation_list_paginates_run_and_message_activity(
    tmp_path: Path, mysql_conversation_database
) -> None:
    from datetime import UTC, datetime

    from agent_platform.adapters.identity.fake import FakeIdentityProvider
    from agent_platform.adapters.runtime.fake import FakeRuntimeAdapter
    from agent_platform.application.catalog_service import CatalogService
    from agent_platform.application.control_plane import ControlPlaneService
    from agent_platform.application.conversations import ConversationService
    from agent_platform.domain.catalog import SkillType
    from agent_platform.infrastructure.mysql_catalog import MySqlCatalogRepository
    from agent_platform.infrastructure.mysql_conversations import MySqlConversationRepository
    from agent_platform.infrastructure.mysql_execution import MySqlExecutionRepository
    from agent_platform.infrastructure.mysql_storage import MySqlMediaAssetRepository
    from agent_platform.ports.storage import LocalObjectStore

    database, config = mysql_conversation_database
    connection_factory = lambda: mysql.connect(**config, database=database)
    identity = FakeIdentityProvider().resolve_identity("admin-local")
    catalog = CatalogService(MySqlCatalogRepository(connection_factory))
    skill = catalog.create_skill(identity, "列表技能", SkillType.INSTRUCTION, "回答问题", [])
    catalog.publish_skill(identity, skill.skill_id)
    employee = catalog.create_employee(identity, "列表员工", "支持", "Conversation 分页")
    version = catalog.create_employee_version(
        identity, employee.employee_id, "固定系统指令", [skill.skill_id], []
    )
    published = catalog.publish_employee_version(identity, employee.employee_id, version.version)
    deployment = catalog.deploy(identity, employee.employee_id, published.version)
    deployment = configure_in_memory_deployment(catalog, identity, deployment)
    execution = MySqlExecutionRepository(connection_factory)
    control_plane = ControlPlaneService(catalog, FakeRuntimeAdapter(), persistence=execution)
    first_session = control_plane.create_session(identity, deployment.deployment_id)
    second_session = control_plane.create_session(identity, deployment.deployment_id)
    object_root = tmp_path / "conversation-list-objects"

    def object_store():
        return LocalObjectStore(
            object_root,
            metadata_repository=MySqlMediaAssetRepository(connection_factory),
        )

    service = ConversationService(MySqlConversationRepository(connection_factory), object_store())
    first_conversation = service.create_conversation(identity, "运行中的对话", is_group=False)
    second_conversation = service.create_conversation(identity, "等待中的对话", is_group=False)
    service.bind_agent(
        identity,
        first_conversation.conversation_id,
        employee.employee_id,
        employee.name,
        first_session.session_id,
    )
    service.bind_agent(
        identity,
        second_conversation.conversation_id,
        employee.employee_id,
        employee.name,
        second_session.session_id,
    )

    baseline = datetime(2000, 1, 1, tzinfo=UTC)
    connection = connection_factory()
    cursor = connection.cursor()
    try:
        cursor.execute(
            "UPDATE conversations SET updated_at=%s WHERE platform_id=%s AND tenant_id=%s "
            "AND conversation_id IN (%s,%s)",
            (
                baseline.replace(tzinfo=None),
                identity.platform_id,
                identity.tenant_id,
                first_conversation.conversation_id,
                second_conversation.conversation_id,
            ),
        )
        connection.commit()
    finally:
        cursor.close()
        connection.close()

    first_message = service.ingest(
        identity,
        first_conversation.conversation_id,
        identity.user_id,
        "请创建 ODS 到 DWD 的 OFFLINE 工作流。",
    )
    service.ingest(
        identity,
        second_conversation.conversation_id,
        identity.user_id,
        "先检查项目列表。",
    )
    connection = connection_factory()
    cursor = connection.cursor()
    try:
        cursor.execute(
            "SELECT updated_at FROM conversations WHERE platform_id=%s AND tenant_id=%s "
            "AND conversation_id=%s",
            (identity.platform_id, identity.tenant_id, first_conversation.conversation_id),
        )
        message_activity_at = cursor.fetchone()[0]
        assert message_activity_at.replace(tzinfo=UTC) > baseline
        cursor.execute(
            "UPDATE conversations SET updated_at=%s WHERE platform_id=%s AND tenant_id=%s "
            "AND conversation_id IN (%s,%s)",
            (
                baseline.replace(tzinfo=None),
                identity.platform_id,
                identity.tenant_id,
                first_conversation.conversation_id,
                second_conversation.conversation_id,
            ),
        )
        connection.commit()
    finally:
        cursor.close()
        connection.close()

    run = control_plane.create_run(
        identity, first_session.session_id, "fake run to advance Conversation activity"
    )
    page = service.list_for_employee(identity, employee.employee_id, limit=1)
    assert len(page.items) == 1
    assert page.items[0].conversation_id == first_conversation.conversation_id
    assert page.items[0].last_message is not None
    assert page.items[0].last_message.message_id == first_message.message_id
    assert page.items[0].last_message.preview == "请创建 ODS 到 DWD 的 OFFLINE 工作流。"
    assert page.items[0].last_run_id == run.run_id
    assert page.items[0].last_run_status == "SUCCEEDED"
    assert page.items[0].pending_approval_count == 0
    assert page.next_cursor is not None

    next_page = service.list_for_employee(
        identity, employee.employee_id, cursor=page.next_cursor, limit=1
    )
    assert [item.conversation_id for item in next_page.items] == [
        second_conversation.conversation_id
    ]
    assert next_page.next_cursor is None

    other = FakeIdentityProvider().resolve_identity("admin-other")
    assert service.list_for_employee(other, employee.employee_id).items == ()


def test_conversation_list_sql_scopes_run_and_approval_aggregates(mysql_conversation_database) -> None:
    from agent_platform.adapters.identity.fake import FakeIdentityProvider
    from agent_platform.domain.common import generate_database_id
    from agent_platform.infrastructure.mysql_conversations import MySqlConversationRepository

    database, config = mysql_conversation_database
    identity = FakeIdentityProvider().resolve_identity("admin-local")
    ids = [generate_database_id() for _ in range(8)]
    (
        conversation_id,
        employee_id,
        deployment_id,
        session_id,
        run_id,
        mcp_profile_id,
        tool_id,
        invocation_id,
    ) = ids
    connection = mysql.connect(**config, database=database)
    cursor = connection.cursor()
    try:
        cursor.execute(
            "INSERT INTO digital_employees (platform_id,tenant_id,employee_id,name,role_name,description,status) "
            "VALUES (%s,%s,%s,'列表员工','支持','列表 SQL 测试','ACTIVE')",
            (identity.platform_id, identity.tenant_id, employee_id),
        )
        cursor.execute(
            "INSERT INTO execution_profiles "
            "(platform_id,tenant_id,profile_id,version,engine_name,model_alias,tool_policy_json,"
            "approval_policy_json,context_policy_json,status) "
            "VALUES (%s,%s,'profile-local',1,'fake','fake-model','[]','{}','{}','PUBLISHED')",
            (identity.platform_id, identity.tenant_id),
        )
        cursor.execute(
            "INSERT INTO digital_employee_versions "
            "(platform_id,tenant_id,employee_id,version,system_prompt,execution_profile_id,"
            "execution_profile_version,approval_policy_json,status) "
            "VALUES (%s,%s,%s,1,'系统指令','profile-local',1,'{}','PUBLISHED')",
            (identity.platform_id, identity.tenant_id, employee_id),
        )
        cursor.execute(
            "INSERT INTO deployments "
            "(platform_id,tenant_id,deployment_id,employee_id,employee_version,status) "
            "VALUES (%s,%s,%s,%s,1,'ACTIVE')",
            (identity.platform_id, identity.tenant_id, deployment_id, employee_id),
        )
        cursor.execute(
            "INSERT INTO conversations "
            "(platform_id,tenant_id,conversation_id,title,is_group,status,created_by) "
            "VALUES (%s,%s,%s,'审批列表',FALSE,'ACTIVE',%s)",
            (identity.platform_id, identity.tenant_id, conversation_id, identity.user_id),
        )
        cursor.execute(
            "INSERT INTO conversation_participants "
            "(platform_id,tenant_id,conversation_id,user_id,role) VALUES (%s,%s,%s,%s,'owner')",
            (identity.platform_id, identity.tenant_id, conversation_id, identity.user_id),
        )
        cursor.execute(
            "INSERT INTO sessions "
            "(platform_id,tenant_id,session_id,user_id,engine_name,engine_session_id,status,"
            "deployment_id,employee_id,employee_version,trace_id) "
            "VALUES (%s,%s,%s,%s,'fake','list-session','ACTIVE',%s,%s,1,'trace-list')",
            (
                identity.platform_id,
                identity.tenant_id,
                session_id,
                identity.user_id,
                deployment_id,
                employee_id,
            ),
        )
        cursor.execute(
            "INSERT INTO agent_bindings "
            "(platform_id,tenant_id,binding_id,conversation_id,employee_id,employee_version,"
            "deployment_id,session_id,status) VALUES (%s,%s,%s,%s,%s,1,%s,%s,'ACTIVE')",
            (
                identity.platform_id,
                identity.tenant_id,
                generate_database_id(),
                conversation_id,
                employee_id,
                deployment_id,
                session_id,
            ),
        )
        cursor.execute(
            "INSERT INTO runs "
            "(platform_id,tenant_id,run_id,session_id,idempotency_key,request_digest,state,"
            "employee_id,employee_version,execution_profile_id,execution_profile_version,trace_id) "
            "VALUES (%s,%s,%s,%s,'list-run',%s,'AWAITING_APPROVAL',%s,1,'profile-local',1,'trace-list')",
            (
                identity.platform_id,
                identity.tenant_id,
                run_id,
                session_id,
                "a" * 64,
                employee_id,
            ),
        )
        cursor.execute(
            "INSERT INTO mcp_server_profiles "
            "(platform_id,tenant_id,profile_id,version,name,transport,command,args_json,"
            "working_directory_ref,environment_secret_refs_json,enabled_tool_names_json,"
            "tool_catalog_sha256,status) "
            "VALUES (%s,%s,%s,1,'测试 MCP','stdio','/usr/bin/python','[]','/tmp','{}',"
            "'[\"workflow_create\"]',%s,'ACTIVE')",
            (identity.platform_id, identity.tenant_id, mcp_profile_id, "d" * 64),
        )
        cursor.execute(
            "INSERT INTO tool_versions "
            "(platform_id,tenant_id,tool_id,version,name,protocol,description,input_schema_json,"
            "output_schema_json,timeout_ms,retry_policy_json,risk_policy,handler_name,status,"
            "mcp_server_profile_id,mcp_server_profile_version,remote_tool_name,input_schema_sha256,"
            "preview_tool_name,mcp_risk_level,idempotency_support) "
            "VALUES (%s,%s,%s,1,'workflow_create','mcp','创建离线工作流','{\"type\":\"object\"}',"
            "'{\"type\":\"object\"}',30000,'{\"max_retries\":0}','low','mcp-call','PUBLISHED',"
            "%s,1,'workflow_create',%s,'workflow_preview','WRITE',FALSE)",
            (identity.platform_id, identity.tenant_id, tool_id, mcp_profile_id, "e" * 64),
        )
        cursor.execute(
            "INSERT INTO platform_trace_spans "
            "(platform_id,tenant_id,run_id,span_id,trace_id,span_type,status,sequence_no,attributes_json) "
            "VALUES (%s,%s,%s,%s,'trace-list','tool','awaiting_approval',1,'{}')",
            (identity.platform_id, identity.tenant_id, run_id, invocation_id),
        )
        cursor.execute(
            "INSERT INTO tool_approval_requests "
            "(platform_id,tenant_id,approval_id,run_id,session_id,conversation_id,requested_by,"
            "tool_invocation_id,tool_id,tool_version,mcp_server_profile_id,mcp_server_profile_version,"
            "arguments_ref,arguments_sha256,preview_ref,preview_sha256,state,expires_at) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,1,%s,1,'object:args',%s,'object:preview',%s,"
            "'PENDING',DATE_ADD(CURRENT_TIMESTAMP(6),INTERVAL 1 HOUR))",
            (
                identity.platform_id,
                identity.tenant_id,
                generate_database_id(),
                run_id,
                session_id,
                conversation_id,
                identity.user_id,
                invocation_id,
                tool_id,
                mcp_profile_id,
                "b" * 64,
                "c" * 64,
            ),
        )
        connection.commit()
    finally:
        cursor.close()
        connection.close()

    rows = MySqlConversationRepository(
        lambda: mysql.connect(**config, database=database)
    ).list_conversation_summaries(identity, employee_id, "ACTIVE", None, 10)

    assert len(rows) == 1
    assert rows[0].conversation.conversation_id == conversation_id
    assert rows[0].last_run_id == run_id
    assert rows[0].last_run_status == "AWAITING_APPROVAL"
    assert rows[0].pending_approval_count == 1


def test_conversation_turn_submission_is_atomic_idempotent_and_rolls_back(
    tmp_path: Path, mysql_conversation_database
) -> None:
    from agent_platform.adapters.identity.fake import FakeIdentityProvider
    from agent_platform.adapters.runtime.fake import FakeRuntimeAdapter
    from agent_platform.application.catalog_service import CatalogService
    from agent_platform.application.control_plane import ControlPlaneService
    from agent_platform.application.conversation_turns import ConversationTurnService
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
    skill = catalog.create_skill(identity, "Turn 技能", SkillType.INSTRUCTION, "检查项目", [])
    catalog.publish_skill(identity, skill.skill_id)
    employee = catalog.create_employee(identity, "Turn 员工", "数仓开发", "异步提交")
    version = catalog.create_employee_version(
        identity, employee.employee_id, "数仓员工系统指令", [skill.skill_id], []
    )
    catalog.publish_employee_version(identity, employee.employee_id, version.version)
    deployment = catalog.deploy(identity, employee.employee_id, version.version)
    deployment = configure_in_memory_deployment(catalog, identity, deployment)
    execution = MySqlExecutionRepository(connection_factory)
    control_plane = ControlPlaneService(catalog, FakeRuntimeAdapter(), persistence=execution)
    session = control_plane.create_session(identity, deployment.deployment_id)
    object_root = tmp_path / "conversation-turn-objects"

    def object_store():
        return LocalObjectStore(
            object_root,
            metadata_repository=MySqlMediaAssetRepository(connection_factory),
        )

    conversations = ConversationService(
        MySqlConversationRepository(connection_factory), object_store()
    )
    conversation = conversations.create_conversation(identity, "异步 Turn", is_group=False)
    conversations.bind_agent(
        identity,
        conversation.conversation_id,
        employee.employee_id,
        employee.name,
        session.session_id,
    )
    turns = ConversationTurnService(conversations, control_plane)

    first = turns.submit(
        identity,
        conversation.conversation_id,
        "创建 ODS 到 DWD 的 OFFLINE 工作流",
        "turn-idem-1",
        employee_id=employee.employee_id,
    )
    replay = turns.submit(
        identity,
        conversation.conversation_id,
        "创建 ODS 到 DWD 的 OFFLINE 工作流",
        "turn-idem-1",
        employee_id=employee.employee_id,
    )
    assert first.created
    assert first.run.status.value == "queued"
    assert replay.created is False
    assert replay.run.run_id == first.run.run_id
    assert replay.message.message_id == first.message.message_id

    with pytest.raises(DomainError) as conflict:
        turns.submit(
            identity,
            conversation.conversation_id,
            "不同任务内容",
            "turn-idem-1",
            employee_id=employee.employee_id,
        )
    assert conflict.value.code == "IDEMPOTENCY_CONFLICT"
    assert len(conversations.list_messages(identity, conversation.conversation_id)) == 1

    def fail_after_message(stage: str) -> None:
        if stage == "after_turn_message_insert":
            raise RuntimeError("injected turn transaction failure")

    failing_control_plane = ControlPlaneService(
        catalog,
        FakeRuntimeAdapter(),
        persistence=MySqlExecutionRepository(
            connection_factory, failure_injector=fail_after_message
        ),
    )
    failing_turns = ConversationTurnService(conversations, failing_control_plane)
    with pytest.raises(RuntimeError, match="injected turn transaction failure"):
        failing_turns.submit(
            identity,
            conversation.conversation_id,
            "写入失败时不应留下 Run 或 Message",
            "turn-idem-rollback",
            employee_id=employee.employee_id,
        )

    connection = connection_factory()
    cursor = connection.cursor()
    try:
        cursor.execute(
            "SELECT COUNT(*) FROM runs WHERE platform_id=%s AND tenant_id=%s "
            "AND session_id=%s AND idempotency_key=%s",
            (
                identity.platform_id,
                identity.tenant_id,
                session.session_id,
                "turn-idem-rollback",
            ),
        )
        assert cursor.fetchone()[0] == 0
        cursor.execute(
            "SELECT last_message_sequence FROM sessions WHERE platform_id=%s "
            "AND tenant_id=%s AND session_id=%s",
            (identity.platform_id, identity.tenant_id, session.session_id),
        )
        assert cursor.fetchone()[0] == 1
    finally:
        cursor.close()
        connection.close()


def test_conversation_turn_api_service_uses_mysql_union_transaction(
    tmp_path: Path, mysql_conversation_database
) -> None:
    from agent_platform.adapters.identity.fake import FakeIdentityProvider
    from agent_platform.adapters.runtime.fake import FakeRuntimeAdapter
    from agent_platform.application.catalog_service import CatalogService
    from agent_platform.application.control_plane import ControlPlaneService
    from agent_platform.application.conversation_turns import ConversationTurnService
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
    skill = catalog.create_skill(identity, "Turn 原子技能", SkillType.INSTRUCTION, "检查项目", [])
    catalog.publish_skill(identity, skill.skill_id)
    employee = catalog.create_employee(identity, "Turn 原子员工", "数仓开发", "atomic turn")
    employee_version = catalog.create_employee_version(
        identity, employee.employee_id, "固定员工指令", [skill.skill_id], []
    )
    catalog.publish_employee_version(identity, employee.employee_id, employee_version.version)
    deployment = catalog.deploy(identity, employee.employee_id, employee_version.version)
    deployment = configure_in_memory_deployment(catalog, identity, deployment)
    session = ControlPlaneService(
        catalog,
        FakeRuntimeAdapter(),
        persistence=MySqlExecutionRepository(connection_factory),
    ).create_session(identity, deployment.deployment_id)
    object_root = tmp_path / "conversation-turn-atomic-objects"

    def object_store():
        return LocalObjectStore(
            object_root,
            metadata_repository=MySqlMediaAssetRepository(connection_factory),
        )

    conversations = ConversationService(
        MySqlConversationRepository(connection_factory), object_store()
    )
    conversation = conversations.create_conversation(identity, "Atomic turn", is_group=False)
    conversations.bind_agent(
        identity,
        conversation.conversation_id,
        employee.employee_id,
        employee.name,
        session.session_id,
    )
    turns = ConversationTurnService(
        conversations,
        ControlPlaneService(
            catalog, FakeRuntimeAdapter(), persistence=MySqlExecutionRepository(connection_factory)
        ),
    )

    first = turns.submit(
        identity,
        conversation.conversation_id,
        "生成 ODS 到 DWD 预览",
        "mysql-turn-1",
        employee_id=employee.employee_id,
    )
    replay = turns.submit(
        identity,
        conversation.conversation_id,
        "生成 ODS 到 DWD 预览",
        "mysql-turn-1",
        employee_id=employee.employee_id,
    )
    assert first.created and first.run.status.value == "queued"
    assert not replay.created
    assert (replay.run.run_id, replay.message.message_id) == (
        first.run.run_id,
        first.message.message_id,
    )
    with pytest.raises(DomainError) as conflict:
        turns.submit(
            identity,
            conversation.conversation_id,
            "修改后的工作流任务",
            "mysql-turn-1",
            employee_id=employee.employee_id,
        )
    assert conflict.value.code == "IDEMPOTENCY_CONFLICT"

    page = conversations.list_for_employee(identity, employee.employee_id, limit=10)
    assert len(page.items) == 1
    assert page.items[0].last_run_id == first.run.run_id
    assert page.items[0].last_run_status == "QUEUED"
    assert page.items[0].last_message is not None
    assert page.items[0].last_message.message_id == first.message.message_id

    def fail_after_message(stage: str) -> None:
        if stage == "after_turn_message_insert":
            raise RuntimeError("injected rollback after message insertion")

    failing_turns = ConversationTurnService(
        conversations,
        ControlPlaneService(
            catalog,
            FakeRuntimeAdapter(),
            persistence=MySqlExecutionRepository(
                connection_factory, failure_injector=fail_after_message
            ),
        ),
    )
    with pytest.raises(RuntimeError, match="injected rollback"):
        failing_turns.submit(
            identity,
            conversation.conversation_id,
            "事务失败的工作流任务",
            "mysql-turn-rollback",
            employee_id=employee.employee_id,
        )

    connection = connection_factory()
    cursor = connection.cursor()
    try:
        cursor.execute(
            "SELECT COUNT(*) FROM runs WHERE platform_id=%s AND tenant_id=%s "
            "AND session_id=%s AND idempotency_key='mysql-turn-rollback'",
            (identity.platform_id, identity.tenant_id, session.session_id),
        )
        assert cursor.fetchone()[0] == 0
        cursor.execute(
            "SELECT COUNT(*) FROM messages WHERE platform_id=%s AND tenant_id=%s "
            "AND conversation_id=%s AND source='user'",
            (identity.platform_id, identity.tenant_id, conversation.conversation_id),
        )
        assert cursor.fetchone()[0] == 1
    finally:
        cursor.close()
        connection.close()


def test_mysql_run_worker_processes_a_persisted_conversation_turn_and_fences_stale_result(
    tmp_path: Path, mysql_conversation_database
) -> None:
    import asyncio
    from dataclasses import replace

    from agent_platform.adapters.identity.fake import FakeIdentityProvider
    from agent_platform.adapters.runtime.fake import FakeRuntimeAdapter
    from agent_platform.api.app import create_app
    from agent_platform.application.catalog_service import CatalogService
    from agent_platform.application.control_plane import ControlPlaneService
    from agent_platform.domain.catalog import SkillType
    from agent_platform.domain.execution import RuntimeStatus
    from agent_platform.infrastructure.mysql_catalog import MySqlCatalogRepository
    from agent_platform.infrastructure.mysql_conversations import MySqlConversationRepository
    from agent_platform.infrastructure.mysql_execution import MySqlExecutionRepository
    from agent_platform.infrastructure.mysql_mcp import MySqlMcpRepository
    from agent_platform.infrastructure.mysql_resource_center import MySqlResourceCenterRepository
    from agent_platform.infrastructure.mysql_storage import MySqlMediaAssetRepository
    from agent_platform.ports.storage import LocalObjectStore
    from agent_platform.workers.run_worker import RunWorker

    database, config = mysql_conversation_database
    connection_factory = lambda: mysql.connect(**config, database=database)
    identity = FakeIdentityProvider().resolve_identity("admin-local")
    catalog = CatalogService(MySqlCatalogRepository(connection_factory))
    skill = catalog.create_skill(identity, "Worker Skill", SkillType.INSTRUCTION, "生成安全预览", [])
    catalog.publish_skill(identity, skill.skill_id)
    employee = catalog.create_employee(identity, "Worker 员工", "数仓开发", "独立 Run Worker")
    version = catalog.create_employee_version(
        identity, employee.employee_id, "保持 RunSnapshot 固定", [skill.skill_id], []
    )
    catalog.publish_employee_version(identity, employee.employee_id, version.version)
    deployment = catalog.deploy(identity, employee.employee_id, version.version)
    deployment = configure_in_memory_deployment(catalog, identity, deployment)
    object_root = tmp_path / "run-worker-objects"

    def object_store():
        return LocalObjectStore(
            object_root,
            metadata_repository=MySqlMediaAssetRepository(connection_factory),
        )

    store = object_store()

    class CapturingExecutionRepository(MySqlExecutionRepository):
        claim = None

        def claim_next_queued_run(self, platform_id, worker_id, *, lease_seconds=30):
            self.claim = super().claim_next_queued_run(
                platform_id, worker_id, lease_seconds=lease_seconds
            )
            return self.claim

    execution = CapturingExecutionRepository(connection_factory)
    resource_center = MySqlResourceCenterRepository(connection_factory)
    catalog_repository = MySqlCatalogRepository(connection_factory)
    conversation_repository = MySqlConversationRepository(connection_factory)
    app = create_app(
        mode="mysql",
        catalog_repository=catalog_repository,
        execution_repository=execution,
        conversation_repository=conversation_repository,
        mcp_repository=MySqlMcpRepository(connection_factory),
        resource_center_repository=resource_center,
        object_store=store,
    )
    from fastapi.testclient import TestClient

    client = TestClient(app)
    headers = {"X-Fake-User": identity.user_id}
    session_response = client.post(
        "/api/v1/sessions",
        headers=headers,
        json={"deployment_id": deployment.deployment_id},
    )
    assert session_response.status_code == 201
    conversation_response = client.post(
        "/api/v1/conversations",
        headers=headers,
        json={"title": "Worker Run", "is_group": False},
    )
    assert conversation_response.status_code == 201
    conversation_id = conversation_response.json()["id"]
    binding_response = client.post(
        f"/api/v1/conversations/{conversation_id}/bindings",
        headers=headers,
        json={
            "employee_id": employee.employee_id,
            "employee_name": employee.name,
            "session_id": session_response.json()["id"],
        },
    )
    assert binding_response.status_code == 201
    turn_response = client.post(
        f"/api/v1/conversations/{conversation_id}/turns",
        headers=headers,
        json={
            "employee_id": employee.employee_id,
            "text": "生成 ODS 到 DWD 的 OFFLINE 预览",
            "idempotency_key": "mysql-worker-turn-1",
        },
    )
    assert turn_response.status_code == 202
    submission = turn_response.json()
    assert submission["status"] == "QUEUED"

    worker_control_plane = ControlPlaneService(
        app.state.catalog_service,
        FakeRuntimeAdapter(),
        persistence=execution,
        object_store=object_store(),
        resource_center_repository=resource_center,
    )
    worker = RunWorker(
        identity.platform_id,
        "mysql-run-worker-1",
        execution,
        worker_control_plane,
        object_store(),
        lease_seconds=30,
        heartbeat_interval_seconds=1,
    )

    assert asyncio.run(worker.run_once())
    assert execution.claim is not None
    completed = worker_control_plane.get_run(identity, submission["run_id"])
    assert completed.status is RuntimeStatus.SUCCEEDED
    assert completed.result_text.startswith("fake: 生成 ODS 到 DWD")
    assert client.get(f"/api/v1/runs/{submission['run_id']}", headers=headers).json()["status"] == "succeeded"
    timeline = worker_control_plane.get_trace(identity, submission["run_id"])
    assert any(span.span_type.value == "session" for span in timeline.spans)
    assert any(span.span_type.value == "run" for span in timeline.spans)

    connection = connection_factory()
    cursor = connection.cursor()
    try:
        cursor.execute(
            "UPDATE runs SET fencing_token=fencing_token+1 WHERE platform_id=%s "
            "AND tenant_id=%s AND run_id=%s",
            (identity.platform_id, identity.tenant_id, submission["run_id"]),
        )
        connection.commit()
    finally:
        cursor.close()
        connection.close()
    assert not execution.complete_run(
        identity,
        replace(completed, status=RuntimeStatus.FAILED),
        execution.claim.fencing_token,
        (),
        None,
    )
