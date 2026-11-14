FROM python:3.13-slim@sha256:e48f59cdba69a3cbd9ed2cf0f348d90d45a8d350d98134cf1070bc106ee9ed90

WORKDIR /opt/harness
COPY harness-engine/src /opt/harness/src
COPY harness-engine/requirements-deepseek-check.txt /opt/harness/requirements-deepseek-check.txt
RUN python -m pip install --no-cache-dir 'cryptography>=42' \
    && python -m pip install --no-cache-dir -r /opt/harness/requirements-deepseek-check.txt

ENV PYTHONPATH=/opt/harness/src
USER 65532:65532
ENTRYPOINT ["python", "-m", "harness_engine.cli"]
