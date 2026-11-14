"""业务上下文组装端口。"""

from typing import Protocol

from agent_platform.domain.context import BusinessContextPolicy, ContextSnapshot


class BusinessContextAssemblerPort(Protocol):
    policy: BusinessContextPolicy

    def assemble(self, **kwargs: object) -> ContextSnapshot:
        ...
