# Review agent image.
#
# Layer order matters (Phase 02 lab-02): requirements.txt changes rarely,
# source changes often. Copying requirements first means a code edit reuses
# the cached dependency layer instead of reinstalling everything.

FROM python:3.12-slim

# git: the agent's tools shell out to it (ls-files, log, show)
RUN apt-get update \
    && apt-get install -y --no-install-recommends git curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY agent.py prompts.py server.py tools.py .

# Run as a named non-root user. The Deployment enforces this again with
# runAsNonRoot; defense in both layers.
RUN useradd --create-home agent
USER agent

EXPOSE 8080
CMD ["python", "server.py"]
