from agent_platform.application.context import BusinessContextAssembler
from agent_platform.application.memory import InMemoryMemoryStore
from agent_platform.domain.context import BusinessContextPolicy
from agent_platform.ports.context import BusinessContextAssemblerPort
from agent_platform.ports.memory import MemoryReader, MemoryWriter


def test_context_and_memory_ports_are_structural_contracts() -> None:
    assembler: BusinessContextAssemblerPort = BusinessContextAssembler(BusinessContextPolicy())
    store = InMemoryMemoryStore()
    reader: MemoryReader = store
    writer: MemoryWriter = store
    assert assembler.policy.max_tokens == 4096
    assert callable(reader.read)
    assert callable(writer.write)
