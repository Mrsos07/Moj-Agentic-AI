FROM node:24-bookworm-slim
RUN apt-get update && apt-get install -y --no-install-recommends python3 python3-venv && rm -rf /var/lib/apt/lists/*
RUN python3 -m venv /opt/mcp && /opt/mcp/bin/pip install --no-cache-dir mcp==2.2.0
COPY runner.py /opt/runner.py
ENTRYPOINT ["python3", "-I", "/opt/runner.py"]
