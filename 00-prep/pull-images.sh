#!/usr/bin/env bash
# Pre-pull every container image used in the curriculum so you can work offline.
set -euo pipefail

images=(
  # Phase 1 (Linux via containers)
  "ubuntu:22.04"
  "alpine:3.19"
  "debian:bookworm-slim"

  # Phase 2 (Docker)
  "nginx:1.27-alpine"
  "redis:7-alpine"
  "postgres:16-alpine"
  "python:3.11-slim"
  "hello-world:latest"

  # Phase 3 (Kubernetes)
  "busybox:1.36"
  "curlimages/curl:latest"
  "bitnami/kubectl:latest"
  "traefik:v3.0"                       # ingress option
  "registry.k8s.io/ingress-nginx/controller:v1.10.1"
  "kindest/node:v1.30.0"               # kind cluster node image

  # Phase 4: skipped - using host's local ollama (already installed with models).
  # If you ever want the in-cluster path, uncomment one of these:
  # "ollama/ollama:latest"        # ~500 MB
  # "vllm/vllm-openai:latest"     # ~12 GB; GPU-oriented; Mac CPU mode is slow
)

for img in "${images[@]}"; do
  echo ">>> pulling $img"
  docker pull "$img" || echo "WARN: failed to pull $img (continuing)"
done

echo
echo "Done. Cached images:"
docker images --format 'table {{.Repository}}:{{.Tag}}\t{{.Size}}'
