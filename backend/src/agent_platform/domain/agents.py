from dataclasses import dataclass

from agent_platform.domain.identity import IdentityContext


@dataclass(frozen=True, slots=True)
class DigitalEmployeeSummary:
    """Gallery 页面使用的数字员工只读摘要。"""

    employee_id: str
    platform_id: str
    tenant_id: str
    name: str
    role_name: str
    description: str
    status: str
    published_to_gallery: bool
    knowledge_count: int
    skill_count: int
    sop_count: int


class FakeDigitalEmployeeRepository:
    """提供确定性的本地员工目录，便于前端和 API 契约测试。"""

    def __init__(self) -> None:
        self._employees = (
            DigitalEmployeeSummary(
                employee_id="employee-admin",
                platform_id="plt-local",
                tenant_id="tenant-a",
                name="行政助手",
                role_name="事务管家",
                description="处理办公事务和员工服务请求。",
                status="ACTIVE",
                published_to_gallery=True,
                knowledge_count=2,
                skill_count=1,
                sop_count=3,
            ),
            DigitalEmployeeSummary(
                employee_id="employee-ops",
                platform_id="plt-local",
                tenant_id="tenant-a",
                name="运维助手",
                role_name="运维分析师",
                description="分析日志、监控和运行故障。",
                status="ACTIVE",
                published_to_gallery=True,
                knowledge_count=1,
                skill_count=2,
                sop_count=2,
            ),
            DigitalEmployeeSummary(
                employee_id="employee-other",
                platform_id="plt-local",
                tenant_id="tenant-b",
                name="业务助手",
                role_name="业务顾问",
                description="服务另一个租户的业务咨询。",
                status="ACTIVE",
                published_to_gallery=True,
                knowledge_count=1,
                skill_count=1,
                sop_count=1,
            ),
        )

    def list_available(self, identity: IdentityContext) -> tuple[DigitalEmployeeSummary, ...]:
        """按已验证的平台和租户上下文过滤可用员工。"""

        return tuple(
            employee
            for employee in self._employees
            if employee.platform_id == identity.platform_id
            and employee.tenant_id == identity.tenant_id
            and employee.status == "ACTIVE"
            and employee.published_to_gallery
        )
