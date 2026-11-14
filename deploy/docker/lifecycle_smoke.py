from harness_engine.core import EngineRequest, CancelStatus
from harness_engine.engines.fake import FakeEngine


engine = FakeEngine()
engine.start()
session = engine.create_session()
result = engine.run(
    EngineRequest(prompt="docker-lifecycle-ok", session_id=session.session_id)
)
resumed = engine.resume_session(session)
cancel = engine.cancel(session.session_id)
engine.close()

assert result.text == "fake: docker-lifecycle-ok"
assert resumed.session_id == session.session_id
assert cancel.status is CancelStatus.NOT_RUNNING
print("session=passed resume=passed cancel=passed")
