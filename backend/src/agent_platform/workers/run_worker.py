"""从 MySQL 租户队列领取 Run，并在有效 fencing lease 内驱动 Runtime。"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import logging
from collections.abc import Callable

from agent_platform.domain.common import DomainError
from agent_platform.domain.execution import RuntimeStatus
from agent_platform.domain.identity import IdentityContext
from agent_platform.domain.model_routes import ModelTestRecord, ModelTestResult
from agent_platform.ports.catalog_repository import CatalogRepository
from agent_platform.ports.execution_repository import ExecutionRepository, RunClaim
from agent_platform.ports.storage import ObjectStore

logger = logging.getLogger(__name__)

_TERMINAL_STATES = {
    RuntimeStatus.SUCCEEDED,
    RuntimeStatus.FAILED,
    RuntimeStatus.CANCELLED,
    RuntimeStatus.UNKNOWN,
}


class RunWorker:
    """运行受租约保护的异步 Run；无法确定结果时只写 UNKNOWN，不自动重放。"""

    def __init__(
        self,
        platform_id: str,
        worker_id: str,
        repository: ExecutionRepository,
        control_plane: object,
        object_store: ObjectStore,
        *,
        lease_seconds: int = 30,
        heartbeat_interval_seconds: float | None = None,
        poll_interval_seconds: float = 1.0,
        cancellation_grace_seconds: float = 10.0,
        model_test_repository: CatalogRepository | None = None,
        model_test_executor: Callable[[ModelTestRecord], ModelTestResult] | None = None,
    ) -> None:
        if not platform_id.strip() or not worker_id.strip():
            raise ValueError("platform_id 和 worker_id 必须非空")
        if lease_seconds < 3:
            raise ValueError("lease_seconds 必须至少为 3")
        heartbeat_interval = (
            heartbeat_interval_seconds
            if heartbeat_interval_seconds is not None
            else lease_seconds / 3
        )
        if not 0 < heartbeat_interval < lease_seconds:
            raise ValueError("heartbeat_interval_seconds 必须大于零且小于 lease_seconds")
        if poll_interval_seconds <= 0 or cancellation_grace_seconds <= 0:
            raise ValueError("Worker 轮询间隔和取消宽限时间必须大于零")
        self.platform_id = platform_id
        self.worker_id = worker_id
        self.repository = repository
        self.control_plane = control_plane
        self.object_store = object_store
        self.lease_seconds = lease_seconds
        self.heartbeat_interval_seconds = heartbeat_interval
        self.poll_interval_seconds = poll_interval_seconds
        self.cancellation_grace_seconds = cancellation_grace_seconds
        self.model_test_repository = model_test_repository
        self.model_test_executor = model_test_executor
        self._prefer_model_test = False

    async def run_once(self) -> bool:
        """在 Run 与模型测试之间轮换领取，避免持续 Run 队列饿死测试任务。"""

        if self._prefer_model_test:
            self._prefer_model_test = False
            if await self._run_next_model_test():
                return True

        claim = self.repository.claim_next_queued_run(
            self.platform_id,
            self.worker_id,
            lease_seconds=self.lease_seconds,
        )
        if claim is None:
            return await self._run_next_model_test()
        self._prefer_model_test = (
            self.model_test_repository is not None and self.model_test_executor is not None
        )
        identity = self._worker_identity(claim)
        run = self.repository.get_run(identity, claim.run_id)
        session = self.repository.get_session(identity, run.session_id)

        prompt: str | None = None
        input_error: str | None = None
        try:
            prompt = self._read_prompt(identity, claim.run_id)
        except DomainError as error:
            input_error = error.code
        except Exception:  # noqa: BLE001 — 对象仓储故障发生在 Runtime 调用前，可确定未执行
            input_error = "RUN_INPUT_UNAVAILABLE"

        if not self.repository.start_claimed_run(
            identity, claim.run_id, claim.worker_id, claim.fencing_token
        ):
            current = self.repository.get_run(identity, claim.run_id)
            if current.status is RuntimeStatus.CANCELLING:
                current.status = RuntimeStatus.CANCELLED
                current.error_category = "SUBMITTER_CANCELLED"
                self.repository.complete_run(
                    identity, current, claim.fencing_token, (), None
                )
            return True
        run.status = RuntimeStatus.RUNNING

        if input_error is not None or prompt is None:
            run.status = RuntimeStatus.FAILED
            run.error_category = input_error or "RUN_INPUT_MISSING"
            self.repository.complete_run(identity, run, claim.fencing_token, (), None)
            return True

        stop_heartbeat = asyncio.Event()
        lease_lost = asyncio.Event()
        execution_task = asyncio.create_task(
            asyncio.to_thread(
                self.control_plane.execute_claimed_run,
                identity,
                run,
                session,
                prompt,
                worker_id=claim.worker_id,
                fencing_token=claim.fencing_token,
            )
        )
        heartbeat_task = asyncio.create_task(
            self._maintain_lease(
                identity,
                claim,
                session,
                execution_task,
                stop_heartbeat,
                lease_lost,
            )
        )
        try:
            await execution_task
        except asyncio.CancelledError:
            if not lease_lost.is_set():
                raise
        except Exception:  # noqa: BLE001 — 失败的出站结果需要围栏为 UNKNOWN
            current = self.repository.get_run(identity, claim.run_id)
            if current.status not in _TERMINAL_STATES:
                self.repository.mark_unknown(identity, claim.run_id, claim.fencing_token)
            # Provider 异常可能包含 URL、响应片段或认证信息；只记录任务引用，不记录异常正文。
            logger.error(
                "Run Worker 执行失败并已收敛租户 Run 状态 run_id=%s",
                claim.run_id,
            )
        finally:
            stop_heartbeat.set()
            heartbeat_task.cancel()
            try:
                await heartbeat_task
            except asyncio.CancelledError:
                pass
        return True

    async def _run_next_model_test(self) -> bool:
        repository = self.model_test_repository
        executor = self.model_test_executor
        if repository is None or executor is None:
            return False
        claimed = repository.claim_next_model_test(
            self.platform_id,
            self.worker_id,
            lease_seconds=self.lease_seconds,
        )
        if claimed is None:
            return False
        identity = IdentityContext(
            platform_id=self.platform_id,
            tenant_id=claimed.tenant_id,
            user_id=self.worker_id,
            roles=frozenset(),
            auth_source="worker",
        )
        stop_heartbeat = asyncio.Event()
        result_task = asyncio.create_task(asyncio.to_thread(executor, claimed))
        heartbeat_task = asyncio.create_task(
            self._maintain_model_test_lease(
                identity,
                claimed,
                result_task,
                stop_heartbeat,
            )
        )
        try:
            try:
                result = await result_task
                if not isinstance(result, ModelTestResult):
                    raise TypeError("model test executor must return a ModelTestResult")
            except Exception:  # noqa: BLE001 — 出站后异常无法判断供应商是否已接收请求
                # Unknown outcomes are terminal: replaying could send a second paid request.
                result = ModelTestResult(
                    state="UNKNOWN",
                    error_category="MODEL_TEST_OUTCOME_UNKNOWN",
                )
            if not repository.finish_model_test(identity, claimed, result):
                logger.warning(
                    "模型测试结果未写入，租约已失效 test_id=%s",
                    claimed.test_id,
                )
            return True
        finally:
            stop_heartbeat.set()
            heartbeat_task.cancel()
            try:
                await heartbeat_task
            except asyncio.CancelledError:
                pass

    async def _maintain_model_test_lease(
        self,
        identity: IdentityContext,
        claimed: ModelTestRecord,
        result_task: asyncio.Task[ModelTestResult],
        stop: asyncio.Event,
    ) -> None:
        repository = self.model_test_repository
        if repository is None or claimed.worker_id is None:
            return
        while not stop.is_set() and not result_task.done():
            try:
                await asyncio.wait_for(
                    stop.wait(), timeout=self.heartbeat_interval_seconds
                )
                return
            except TimeoutError:
                pass
            renewed = await asyncio.to_thread(
                repository.heartbeat_model_test,
                identity,
                claimed.test_id,
                claimed.worker_id,
                claimed.fencing_token,
                lease_seconds=self.lease_seconds,
            )
            if not renewed:
                return

    async def run_forever(self, stop: asyncio.Event) -> None:
        """持续 claim；收到进程停止信号后不再领取新 Run。"""

        while not stop.is_set():
            try:
                worked = await self.run_once()
            except Exception:  # 单轮数据库/依赖异常不能伪造 Run 终态
                logger.exception("Run Worker 轮询失败，将在下一轮重试")
                worked = False
            if worked or stop.is_set():
                continue
            try:
                await asyncio.wait_for(stop.wait(), timeout=self.poll_interval_seconds)
            except TimeoutError:
                pass

    async def _maintain_lease(
        self,
        identity: IdentityContext,
        claim: RunClaim,
        session: object,
        execution_task: asyncio.Task[object],
        stop: asyncio.Event,
        lease_lost: asyncio.Event,
    ) -> None:
        while not stop.is_set():
            try:
                await asyncio.wait_for(
                    stop.wait(), timeout=self.heartbeat_interval_seconds
                )
                return
            except TimeoutError:
                pass

            renewed = self.repository.heartbeat(
                identity,
                claim.run_id,
                claim.fencing_token,
                worker_id=claim.worker_id,
                lease_seconds=self.lease_seconds,
            )
            current = self.repository.get_run(identity, claim.run_id)
            if current.status in _TERMINAL_STATES:
                return
            if not renewed:
                lease_lost.set()
                await self._lose_lease(identity, claim, session, execution_task)
                return
            if current.status is RuntimeStatus.CANCELLING:
                await self._cancel_run(identity, claim, session, execution_task, lease_lost)
                return

    async def _lose_lease(
        self,
        identity: IdentityContext,
        claim: RunClaim,
        session: object,
        execution_task: asyncio.Task[object],
    ) -> None:
        """先围栏并持久化 UNKNOWN，再尽力取消 Runtime，避免迟到结果覆盖状态。"""

        self.repository.mark_unknown(identity, claim.run_id, claim.fencing_token)
        await self._request_runtime_cancel(session)
        if not execution_task.done():
            execution_task.cancel()

    async def _cancel_run(
        self,
        identity: IdentityContext,
        claim: RunClaim,
        session: object,
        execution_task: asyncio.Task[object],
        lease_lost: asyncio.Event,
    ) -> None:
        """Runtime 确认取消后等待调用收敛；未能确认停止则围栏为 UNKNOWN。"""

        if getattr(session, "runtime_session_ref", None) is None:
            try:
                await asyncio.wait_for(
                    asyncio.shield(execution_task),
                    timeout=self.cancellation_grace_seconds,
                )
                return
            except TimeoutError:
                pass
            except Exception:  # noqa: BLE001 — 执行入口负责归档错误状态
                return

        cancelled = await self._request_runtime_cancel(session)
        if not cancelled:
            lease_lost.set()
            self.repository.mark_unknown(identity, claim.run_id, claim.fencing_token)
            if not execution_task.done():
                execution_task.cancel()
            return
        try:
            await asyncio.wait_for(
                asyncio.shield(execution_task),
                timeout=self.cancellation_grace_seconds,
            )
        except TimeoutError:
            lease_lost.set()
            self.repository.mark_unknown(identity, claim.run_id, claim.fencing_token)
            if not execution_task.done():
                execution_task.cancel()
        except Exception:  # noqa: BLE001 — ControlPlane 已将不确定结果记入 UNKNOWN
            return

    async def _request_runtime_cancel(self, session: object) -> bool:
        runtime = getattr(self.control_plane, "runtime", None)
        capabilities = getattr(runtime, "capabilities", None)
        if runtime is None or not getattr(capabilities, "cancellation", False):
            return False
        session_ref = getattr(session, "runtime_session_ref", None)
        if not session_ref:
            return False
        try:
            return bool(await asyncio.to_thread(runtime.cancel, session_ref))
        except Exception:  # noqa: BLE001 — 供应商取消异常不能证明 Runtime 已停止
            return False

    def _read_prompt(self, identity: IdentityContext, run_id: str) -> str:
        """按租户读取 USER Message 对象，并在 Runtime 出站前校验 SHA-256 和字节数。"""

        reference = self.repository.get_run_input_ref(identity, run_id)
        content = self.object_store.read(
            reference.content_ref,
            identity.platform_id,
            identity.tenant_id,
        )
        if len(content) != reference.byte_length:
            raise DomainError("RUN_INPUT_LENGTH_MISMATCH", "Run 输入消息字节数与索引不匹配")
        digest = hashlib.sha256(content).hexdigest()
        if not hmac.compare_digest(digest, reference.content_sha256):
            raise DomainError("RUN_INPUT_DIGEST_MISMATCH", "Run 输入消息摘要与索引不匹配")
        try:
            prompt = content.decode("utf-8")
        except UnicodeDecodeError as error:
            raise DomainError("RUN_INPUT_INVALID_UTF8", "Run 输入消息不是合法 UTF-8") from error
        if not prompt.strip():
            raise DomainError("RUN_INPUT_MISSING", "Run 输入消息不能为空")
        return prompt

    def _worker_identity(self, claim: RunClaim) -> IdentityContext:
        return IdentityContext(
            platform_id=claim.platform_id,
            tenant_id=claim.tenant_id,
            user_id=claim.worker_id,
            roles=frozenset(),
            auth_source="worker",
        )


def main() -> int:
    """以独立进程运行 MySQL Run Worker，并在 SIGTERM 时停止领取。"""

    import os
    import signal
    import socket

    from agent_platform.api.app import create_app_from_environment

    logging.basicConfig(
        level=os.environ.get("AGENT_PLATFORM_LOG_LEVEL", "INFO").upper(),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    if os.environ.get("AGENT_PLATFORM_MODE") != "mysql":
        raise RuntimeError("Run Worker 必须使用 MySQL 模式（AGENT_PLATFORM_MODE=mysql）")
    platform_id = os.environ.get("AGENT_PLATFORM_ID", "").strip()
    if not platform_id:
        raise RuntimeError("Run Worker 缺少 AGENT_PLATFORM_ID")
    worker_id = os.environ.get("AGENT_PLATFORM_WORKER_ID", socket.gethostname()).strip()
    if not worker_id:
        raise RuntimeError("Run Worker 缺少 AGENT_PLATFORM_WORKER_ID")
    lease_seconds = int(os.environ.get("AGENT_PLATFORM_WORKER_LEASE_SECONDS", "30"))
    heartbeat_interval = float(
        os.environ.get(
            "AGENT_PLATFORM_WORKER_HEARTBEAT_SECONDS", str(lease_seconds / 3)
        )
    )

    app = create_app_from_environment()
    if app.state.mode != "mysql":
        raise RuntimeError("Run Worker 必须使用 MySQL 持久化模式")
    control_plane = app.state.control_plane_service
    if control_plane.persistence is None or control_plane.object_store is None:
        raise RuntimeError("Run Worker 缺少 MySQL ExecutionRepository 或 ObjectStore")

    worker = RunWorker(
        platform_id,
        worker_id,
        control_plane.persistence,
        control_plane,
        control_plane.object_store,
        lease_seconds=lease_seconds,
        heartbeat_interval_seconds=heartbeat_interval,
        poll_interval_seconds=float(
            os.environ.get("AGENT_PLATFORM_WORKER_POLL_SECONDS", "1")
        ),
        cancellation_grace_seconds=float(
            os.environ.get("AGENT_PLATFORM_WORKER_CANCEL_GRACE_SECONDS", "10")
        ),
        model_test_repository=control_plane.catalog.repository,
        model_test_executor=getattr(app.state, "model_test_executor", None),
    )

    async def _run() -> None:
        stop = asyncio.Event()
        loop = asyncio.get_running_loop()
        for termination_signal in (signal.SIGTERM, signal.SIGINT):
            try:
                loop.add_signal_handler(termination_signal, stop.set)
            except NotImplementedError:
                pass
        async with app.router.lifespan_context(app):
            await worker.run_forever(stop)

    asyncio.run(_run())
    return 0


__all__ = ["RunWorker", "main"]
