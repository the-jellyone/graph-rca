FROM python:3.12-slim

WORKDIR /workspace

RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    docker.io \
    && rm -rf /var/lib/apt/lists/*

COPY rca_control_ui/requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt
RUN pip install --no-cache-dir numpy pyyaml gymnasium

# The control plane imports the existing investigation and lab drivers from src/.
COPY src /workspace/src
COPY lab /workspace/lab
COPY configs /workspace/configs
COPY rca_control_ui /workspace/rca_control_ui
COPY scripts /workspace/scripts

ENV PYTHONPATH=/workspace/src
WORKDIR /workspace/rca_control_ui

EXPOSE 8000

CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8000"]
