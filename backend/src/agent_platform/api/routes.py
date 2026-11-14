from typing import Annotated

from fastapi import APIRouter, Depends

from agent_platform.api.dependencies import current_identity
from agent_platform.domain.agents import FakeDigitalEmployeeRepository
from agent_platform.domain.identity import IdentityContext

router = APIRouter(prefix="/api/v1")
employee_repository = FakeDigitalEmployeeRepository()


@router.get("/digital-employees")
def list_digital_employees(
    identity: Annotated[IdentityContext, Depends(current_identity)],
) -> dict[str, object]:
    """返回当前租户可在 Gallery 使用的数字员工摘要。"""

    return {
        "items": [
            {
                "id": employee.employee_id,
                "tenant_id": employee.tenant_id,
                "name": employee.name,
                "role_name": employee.role_name,
                "description": employee.description,
                "status": employee.status,
                "published_to_gallery": employee.published_to_gallery,
                "resource_counts": {
                    "knowledge_base": employee.knowledge_count,
                    "skill": employee.skill_count,
                    "sop": employee.sop_count,
                },
            }
            for employee in employee_repository.list_available(identity)
        ]
    }
