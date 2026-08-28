# Build context is this client/ directory (docker build -t wdc-client ./client).
# The agent normally runs directly on the device — see deploy/client/install.sh —
# but this image is handy for testing and for devices that already run a
# container runtime.
FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY wdc_client ./wdc_client

RUN useradd -m -u 1000 appuser && chown -R appuser:appuser /app
USER appuser

# PID 1 in a container receives SIGTERM on `docker stop`; the agent installs a
# handler for it and closes its sessions instead of being killed outright.
STOPSIGNAL SIGTERM

CMD ["python", "-m", "wdc_client"]
