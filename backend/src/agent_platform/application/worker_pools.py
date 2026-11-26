"""按 EngineProvider 隔离队列、Worker 认领和能力匹配。"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from agent_platform.domain.common import DomainError
from agent_platform.domain.teams import TeamMember


@dataclass(frozen=True, slots=True)
class WorkerLease:
    """一个引擎 Worker 的租约快照。"""

    engine_name: str
    run_id: str
    worker_id: str
    fencing_token: int
    claimed_at: datetime


@dataclass(slots=True)
class _Pool:
    engine_name: str
    max_concurrency: int
    healthy: bool = True
    active_workers: int = 0
    fencing_token: int = 0
    queue: list[str] | None = None

    def __post_init__(self) -> None:
        self.queue = [] if self.queue is None else self.queue


class WorkerPoolRegistry:
    """Fake 模式的进程内引擎队列注册；MySQL 模式健康和负载改从持久化 Run/心跳查询。"""

    def __init__(self) -> None:
        self._pools: dict[str, _Pool] = {}

    def register(self, engine_name: str, *, max_concurrency: int) -> None:
        if engine_name in self._pools:
            raise ValueError(f"Worker Pool {engine_name!r} already registered")
        if max_concurrency < 1:
            raise ValueError("max_concurrency must be positive")
        self._pools[engine_name] = _Pool(engine_name, max_concurrency)

    def set_health(self, engine_name: str, healthy: bool) -> None:
        self._get(engine_name).healthy = healthy

    def enqueue(self, engine_name: str, run_id: str) -> None:
        pool = self._get(engine_name)
        if not run_id.strip():
            raise ValueError("run_id must not be blank")
        if run_id not in pool.queue:
            pool.queue.append(run_id)

    def claim(self, engine_name: str, worker_id: str) -> WorkerLease:
        pool = self._get(engine_name)
        if not pool.healthy:
            raise DomainError("ENGINE_POOL_UNAVAILABLE", "Worker Pool 当前不可用")
        if pool.active_workers >= pool.max_concurrency:
            raise DomainError("ENGINE_POOL_BUSY", "Worker Pool 已达到并发上限")
        if not pool.queue:
            raise DomainError("QUEUE_EMPTY", "Worker Pool 没有可领取任务")
        run_id = pool.queue.pop(0)
        pool.active_workers += 1
        pool.fencing_token += 1
        return WorkerLease(engine_name, run_id, worker_id, pool.fencing_token, datetime.now(UTC))

    def release(self, lease: WorkerLease) -> None:
        pool = self._get(lease.engine_name)
        if pool.active_workers > 0:
            pool.active_workers -= 1

    def queue_depth(self, engine_name: str) -> int:
        return len(self._get(engine_name).queue)

    def health(self, engine_name: str) -> dict[str, object]:
        pool = self._get(engine_name)
        return {
            "engine_name": pool.engine_name,
            "healthy": pool.healthy,
            "queue_depth": len(pool.queue),
            "active_workers": pool.active_workers,
            "max_concurrency": pool.max_concurrency,
        }

    @staticmethod
    def member_supports(member: TeamMember, required_capabilities: tuple[str, ...]) -> bool:
        return set(required_capabilities).issubset(set(member.capabilities))

    def _get(self, engine_name: str) -> _Pool:
        try:
            return self._pools[engine_name]
        except KeyError as error:
            raise DomainError("ENGINE_PROVIDER_UNAVAILABLE", "EngineProvider 未注册") from error
