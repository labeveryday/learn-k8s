# Phase 0: Offline Prep

**Do this while you have internet.** Once done, the rest of the curriculum works offline.

**Platform: macOS (Apple Silicon or Intel).** Setup uses Homebrew + Colima below. **On Linux:** install Docker Engine natively (your distro's package manager) and `kubectl kind helm k9s jq yq` the same way — skip Colima entirely (it only exists to run a Docker daemon on Macs). The rest of the curriculum is platform-agnostic. **On Windows:** use WSL2 and follow the Linux path.

## 1. Install tools

Assuming Homebrew is installed (`brew -v`).

### Container runtime: Colima (recommended)

Docker Desktop requires a paid license for most companies (>250 employees or >$10M revenue). **Use Colima instead** — it's free, open source, and provides a standard `docker` CLI that the rest of the curriculum (including `kind`) uses unchanged.

```bash
brew install colima docker docker-compose
colima start --cpu 4 --memory 8 --disk 60
```

`--cpu 4 --memory 8` is what the labs need (see Colima notes below for why); `--disk 60` is comfortable headroom for the pre-pulled images plus cluster data — lower it if you're disk-constrained.

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
# kubectl (CLI), kind (runs K8s in Docker locally), helm (package manager, Phase 3), k9s (terminal UI for clusters)
brew install kubectl kind helm k9s

# Used throughout the curriculum
brew install jq yq tree watch htop

# For Python projects later (vLLM)
brew install python@3.11

# ollama (runs LLMs locally) — Phase 4 uses it as the no-image-pull fallback for vLLM
brew install ollama
```

### Verify

```bash
colima status                    # should be "Running"
docker version                   # prints Client AND Server version blocks; Server missing = daemon not up (re-run `colima start`)
docker run --rm hello-world      # smoke test; prints "Hello from Docker!"
kubectl version --client         # prints a Client Version line
kind version                     # prints "kind vX.Y.Z ..."
helm version                     # prints "version.BuildInfo{Version:...}"
```

Each line above should print a version (no "command not found", no connection error). If `docker version` shows only the Client block, the Colima VM isn't running.

## 2. Pre-pull Docker images

Run `./pull-images.sh` in this folder. It pulls every image used in the curriculum so you can work offline.

```bash
cd 00-prep
bash pull-images.sh
```

## 3. Cache documentation offline

Your primary offline aid is the `reference/` cheatsheets (kept open while you work) plus `man` pages and `kubectl explain` — that covers most of the curriculum. The downloads below are optional, handy only if you'll be *fully* offline:

- **Kubernetes docs:** `git clone https://github.com/kubernetes/website.git` for the markdown source. (You can also print kubernetes.io to PDF, but the whole site is huge — only bother if you really need it.)
- **Docker docs:** `git clone https://github.com/docker/docs.git`
- **Linux `man` pages:** already installed locally; use `man <cmd>`.
- **vLLM docs:** `git clone https://github.com/vllm-project/vllm.git` (includes `/docs`).

Optional for deep dives:

- "Kubernetes the Hard Way" by Kelsey Hightower: `git clone https://github.com/kelseyhightower/kubernetes-the-hard-way.git`
- Linux kernel namespace docs: `man 7 namespaces`, `man 7 cgroups`.

## 4. Pre-download a model for vLLM capstone

You installed ollama in Section 1 (it's the Phase 4 fallback). For the vLLM phase we'll also pre-fetch a small Hugging Face model. Pull it now:

```bash
# Small, CPU-friendly model for offline experimentation
python3.11 -m pip install --user huggingface_hub
python3.11 -c "from huggingface_hub import snapshot_download; snapshot_download('TinyLlama/TinyLlama-1.1B-Chat-v1.0', local_dir='$HOME/models/tinyllama')"
```

`python3.11 -m pip` ties pip to the Python you installed in Section 1. If pip complains about an "externally-managed-environment," use a venv: `python3.11 -m venv ~/.venvs/hf && source ~/.venvs/hf/bin/activate`, then re-run the two lines above.

If you can't install `huggingface_hub`, skip — phase 04 has a fallback using an ollama-served model.

## 5. Sanity check

```bash
docker run --rm hello-world          # should print the welcome message
kind create cluster --name sanity    # spins up a single-node K8s cluster
kubectl get nodes                    # should list one node
kind delete cluster --name sanity    # clean up
```

If all four succeed, you're ready for Phase 1. If `kind create cluster` hangs or fails, its node image (`kindest/node`) probably didn't pull — re-run `pull-images.sh` while online.

## Colima notes (read once)

- **Restart after reboot:** `colima start` — the VM doesn't persist across reboots by default.
- **Resources:** the labs in this curriculum run fine in `--cpu 4 --memory 8`. For Phase 4 (vLLM CPU mode) consider bumping memory to 12–16 GB: `colima stop && colima start --cpu 4 --memory 12`.
- **Where do containers live?** Inside Colima's Lima VM (Docker daemon runs there). The `docker` CLI on your Mac talks to it over a socket — same UX as Docker Desktop.
- **`kind` works unchanged.** It detects the Docker socket Colima exposes.
- **Privileged containers (Phase 1 Lab 04):** `docker run --privileged ...` works on Colima.
- **Bind mounts:** Colima auto-mounts `$HOME` into the VM, so `-v $HOME/foo:/foo` works. Paths outside `$HOME` may need explicit mount config (`colima start --mount /tmp:w`).
- **Save battery:** `colima stop` when you're done for the day.
