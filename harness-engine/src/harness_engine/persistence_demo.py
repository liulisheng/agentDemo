"""Runnable Fake + MySQL persistence flow for local development."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Any

from .core import EngineRequest, HarnessEngine, TenantContext
from .engines.deepseek import DeepSeekHarnessEngine
from .engines.fake import FakeEngine
from .persistence.ids import generate_id
from .persistence.mysql import (
    MySqlConfig,
    MySqlDatabase,
    MySqlRunRepository,
    MySqlSessionRepository,
)
from .session_service import PersistentSessionService


def build_demo_engine(
    engine_name: str,
    *,
    local_config: Path | None = None,
    dsh_home: Path | None = None,
    workspace: Path | None = None,
    profile: str | None = None,
    provider: str | None = None,
    model: str | None = None,
    max_tokens: int | None = None,
) -> HarnessEngine:
    """Construct one supported demo engine without changing persistence wiring."""
    if engine_name == "fake":
        return FakeEngine()
    if engine_name != "deepseek":
        raise ValueError(f"unsupported demo engine: {engine_name}")

    candidate = Path(".artifacts/local-openai.json")
    resolved_local_config = local_config
    if resolved_local_config is None and candidate.exists():
        resolved_local_config = candidate
    resolved_home = dsh_home or Path(
        os.environ.get("DSH_HOME") or ".artifacts/dsh-home"
    )
    return DeepSeekHarnessEngine(
        dsh_home=resolved_home,
        cwd=workspace or Path.cwd(),
        profile=profile,
        provider=provider,
        model=model,
        max_tokens=max_tokens,
        local_config=resolved_local_config,
    )


def run_persistent_demo(
    service: PersistentSessionService,
    engine: HarnessEngine,
    tenant: TenantContext,
    worker: TenantContext,
    *,
    prompt: str,
    idempotency_key: str,
    worker_id: str,
) -> dict[str, Any]:
    """Run a complete session, idempotent submit, worker execution, and readback."""
    session = service.create_session(tenant, engine, metadata={"source": "persistence-demo"})
    request = EngineRequest(prompt=prompt)
    submitted = service.submit_run(
        tenant, session.control_session_id, request, idempotency_key
    )
    duplicate = service.submit_run(
        tenant, session.control_session_id, request, idempotency_key
    )
    result = service.execute_run(
        worker, submitted.run_id, request, engine, worker_id
    )
    persisted_session = service.sessions.get(tenant, session.control_session_id)
    persisted_run = service.runs.get(tenant, submitted.run_id)

    return {
        "engine": persisted_session.engine_name,
        "control_session_id": persisted_session.control_session_id,
        "engine_session_id": persisted_session.engine_session_id,
        "persisted_session_status": persisted_session.status.value,
        "run_id": persisted_run.run_id,
        "idempotency_key": persisted_run.idempotency_key,
        "idempotent_submission": duplicate.run_id == submitted.run_id,
        "result_text": result.text,
        "result_status": result.status.value,
        "persisted_run_state": persisted_run.state.value,
        "result_ref": persisted_run.result_ref,
    }


def _seed_demo_identity(database: MySqlDatabase) -> tuple[TenantContext, TenantContext]:
    """Create isolated synthetic directory rows needed by the V001 foreign keys."""
    platform_id, tenant_id, user_id, worker_id = (generate_id() for _ in range(4))
    with database.transaction() as connection:
        cursor = connection.cursor()
        try:
            cursor.execute(
                "INSERT INTO platforms (platform_id, code, name, status) VALUES (%s, %s, %s, 'ACTIVE')",
                (platform_id, f"demo-{platform_id}", "Persistence demo"),
            )
            cursor.execute(
                "INSERT INTO tenants (platform_id, tenant_id, code, name, status) VALUES (%s, %s, %s, %s, 'ACTIVE')",
                (platform_id, tenant_id, f"demo-{tenant_id}", "Demo tenant"),
            )
            cursor.execute(
                "INSERT INTO users (platform_id, tenant_id, user_id, external_subject, username, display_name, status) VALUES (%s, %s, %s, %s, %s, %s, 'ACTIVE')",
                (
                    platform_id,
                    tenant_id,
                    user_id,
                    f"demo:{user_id}",
                    f"demo-{user_id}",
                    "Persistence demo user",
                ),
            )
        finally:
            cursor.close()

    owner = TenantContext(
        platform_id, tenant_id, user_id, roles=("owner",), auth_source="demo"
    )
    worker = TenantContext(
        platform_id, tenant_id, worker_id, roles=("worker",), auth_source="worker"
    )
    return owner, worker


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="harness persistence-demo",
        description="Run an engine session and persist its run lifecycle in MySQL.",
    )
    parser.add_argument("--engine", choices=("fake", "deepseek"), default="fake")
    parser.add_argument("--prompt", default="验证引擎 + MySQL 持久化流程")
    parser.add_argument("--local-config", type=Path)
    parser.add_argument("--dsh-home", type=Path)
    parser.add_argument("--workspace", type=Path, default=Path.cwd())
    parser.add_argument("--profile")
    parser.add_argument("--provider")
    parser.add_argument("--model")
    parser.add_argument("--max-tokens", type=int)
    args = parser.parse_args(argv)

    database = MySqlDatabase.from_config(MySqlConfig.from_env())
    schema_status = database.health()
    engine = build_demo_engine(
        args.engine,
        local_config=args.local_config,
        dsh_home=args.dsh_home,
        workspace=args.workspace,
        profile=args.profile,
        provider=args.provider,
        model=args.model,
        max_tokens=args.max_tokens,
    )
    tenant, worker = _seed_demo_identity(database)
    service = PersistentSessionService(
        MySqlSessionRepository(database), MySqlRunRepository(database)
    )
    try:
        report = run_persistent_demo(
            service,
            engine,
            tenant,
            worker,
            prompt=args.prompt,
            idempotency_key=f"demo-{generate_id()}",
            worker_id=f"demo-worker-{worker.user_id}",
        )
        report["database_schema"] = schema_status
        print(json.dumps(report, ensure_ascii=False, indent=2))
    finally:
        engine.close()
    return 0


__all__ = ["build_demo_engine", "main", "run_persistent_demo"]


if __name__ == "__main__":
    raise SystemExit(main())
