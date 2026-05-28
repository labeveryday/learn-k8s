# Phase 0: Offline Prep

**Do this while you have internet.** Once done, the rest of the curriculum works offline.

## 1. Install tools

Assuming Homebrew is installed (`brew -v`).

### Container runtime: Colima (recommended)

Docker Desktop requires a paid license for most companies (>250 employees or >$10M revenue). **Use Colima instead** — it's free, open source, and provides a standard `docker` CLI that the rest of the curriculum (including `kind`) uses unchanged.

```bash
brew install colima docker docker-compose
colima start --cpu 4 --memory 8 --disk 60
```

Manage the VM:

```bash
colima status        # is it running?
colima stop          # shut down (saves laptop battery)
colima start         # bring it back
colima delete        # wipe and recreate
```

If you *do* have a Docker Desktop license, it works too — just install it instead of Colima.

**Other free alternatives** (only if Colima misbehaves): Rancher Desktop (GUI), Podman Desktop (`alias docker=podman`).

### Kubernetes + the rest

```bash
# Kubernetes local cluster + CLI
brew install kubectl kind helm k9s

# Used throughout the curriculum
brew install jq yq tree watch htop

# For Python projects later (vLLM)
brew install python@3.11
```

### Verify

```bash
colima status                    # should be "Running"
docker version                   # client + server present
docker run --rm hello-world      # smoke test
kubectl version --client
kind version
helm version
```

## 2. Pre-pull Docker images

Run `./pull-images.sh` in this folder. It pulls every image used in the curriculum so you can work offline.

```bash
cd 00-prep
bash pull-images.sh
```

## 3. Cache documentation offline

Bookmark or download:

- **Kubernetes docs** (single-file): https://kubernetes.io/docs/home/  → *Download the site as PDF with your browser's print function, or `git clone https://github.com/kubernetes/website.git` for markdown.*
- **Docker docs:** `git clone https://github.com/docker/docs.git`
- **Linux `man` pages:** already installed locally; use `man <cmd>`.
- **vLLM docs:** `git clone https://github.com/vllm-project/vllm.git` (includes `/docs`).

Optional for deep dives:

- "Kubernetes the Hard Way" by Kelsey Hightower: `git clone https://github.com/kelseyhightower/kubernetes-the-hard-way.git`
- Linux kernel namespace docs: `man 7 namespaces`, `man 7 cgroups`.

## 4. Pre-download a model for vLLM capstone

You already have ollama. For the vLLM phase we'll use a small Hugging Face model. Pull it now:

```bash
# Small, CPU-friendly model for offline experimentation
pip install --user huggingface_hub
python -c "from huggingface_hub import snapshot_download; snapshot_download('TinyLlama/TinyLlama-1.1B-Chat-v1.0', local_dir='$HOME/models/tinyllama')"
```

If you can't install `huggingface_hub`, skip — phase 04 has a fallback using an ollama-served model.

## 5. Sanity check

```bash
docker run --rm hello-world          # should print the welcome message
kind create cluster --name sanity    # spins up a single-node K8s cluster
kubectl get nodes                    # should list one node
kind delete cluster --name sanity    # clean up
```

If all four succeed, you're ready for Phase 1.

## Colima notes (read once)

- **Restart after reboot:** `colima start` — the VM doesn't persist across reboots by default.
- **Resources:** the labs in this curriculum run fine in `--cpu 4 --memory 8`. For Phase 4 (vLLM CPU mode) consider bumping memory to 12–16 GB: `colima stop && colima start --cpu 4 --memory 12`.
- **Where do containers live?** Inside Colima's Lima VM (Docker daemon runs there). The `docker` CLI on your Mac talks to it over a socket — same UX as Docker Desktop.
- **`kind` works unchanged.** It detects the Docker socket Colima exposes.
- **Privileged containers (Phase 1 Lab 04):** `docker run --privileged ...` works on Colima.
- **Bind mounts:** Colima auto-mounts `$HOME` into the VM, so `-v $HOME/foo:/foo` works. Paths outside `$HOME` may need explicit mount config (`colima start --mount /tmp:w`).
- **Save battery:** `colima stop` when you're done for the day.
