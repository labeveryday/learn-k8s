FROM python:3.12-slim

RUN useradd -m -u 1000 sandbox

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt \
    && python -m ipykernel install --sys-prefix

COPY app ./app

# Runtime dirs live in /workspace and /tmp so the rootfs can be read-only.
ENV HOME=/workspace \
    SANDBOX_WORKSPACE=/workspace \
    JUPYTER_RUNTIME_DIR=/tmp/jupyter/runtime \
    JUPYTER_DATA_DIR=/tmp/jupyter/data \
    IPYTHONDIR=/tmp/ipython \
    MPLCONFIGDIR=/tmp/mpl \
    MPLBACKEND=Agg \
    XDG_CACHE_HOME=/tmp/cache \
    PIP_CACHE_DIR=/tmp/cache/pip \
    PYTHONUNBUFFERED=1

RUN mkdir -p /workspace && chown 1000:1000 /workspace

USER sandbox
EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
