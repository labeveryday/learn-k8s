"""Docker backend: one hardened container per session.

Networking model:
- "internal" network (internal=true): manager <-> sandbox control traffic
  only, no route to the outside world. Every sandbox joins this.
- "egress" network: normal bridge with internet. Only attached when the
  session was created with network=True.
"""

import logging
import os

import docker

from .base import Backend

log = logging.getLogger("sandbox.docker")

LABEL = "strands-sandbox"
INTERNAL_NET = os.environ.get("SANDBOX_DOCKER_INTERNAL_NET", "strands-sbx-internal")
EGRESS_NET = os.environ.get("SANDBOX_DOCKER_EGRESS_NET", "strands-sbx-egress")


class DockerBackend(Backend):
    def __init__(self):
        self.client = docker.from_env()
        self.internal = self._ensure_network(INTERNAL_NET, internal=True)
        self.egress = self._ensure_network(EGRESS_NET, internal=False)

    def _ensure_network(self, name: str, internal: bool):
        nets = self.client.networks.list(names=[name])
        for net in nets:
            if net.name == name:
                return net
        return self.client.networks.create(name, driver="bridge", internal=internal)

    def create(self, session_id, image, env, cpus, memory_mb, network, ttl_seconds):
        workspace_mb = int(os.environ.get("SANDBOX_WORKSPACE_MB", "512"))
        container = self.client.containers.run(
            image,
            detach=True,
            name=f"sbx-{session_id}",
            hostname=f"sbx-{session_id}",
            network=self.internal.name,
            environment=env,
            mem_limit=f"{memory_mb}m",
            memswap_limit=f"{memory_mb}m",
            nano_cpus=int(cpus * 1e9),
            pids_limit=256,
            cap_drop=["ALL"],
            security_opt=["no-new-privileges:true"],
            read_only=True,
            tmpfs={
                "/tmp": "size=256m,mode=1777",
                "/workspace": f"size={workspace_mb}m,uid=1000,gid=1000",
            },
            labels={LABEL: "1", "session": session_id},
        )
        if network:
            self.egress.connect(container)
        container.reload()
        ip = container.attrs["NetworkSettings"]["Networks"][self.internal.name]["IPAddress"]
        log.info("created container %s at %s (network=%s)", container.short_id, ip, network)
        return {"endpoint": f"http://{ip}:8000", "ref": container.id}

    def destroy(self, ref):
        try:
            container = self.client.containers.get(ref)
            container.remove(force=True)
        except docker.errors.NotFound:
            pass

    def cleanup_orphans(self):
        count = 0
        for container in self.client.containers.list(all=True, filters={"label": f"{LABEL}=1"}):
            try:
                container.remove(force=True)
                count += 1
            except Exception as exc:  # noqa: BLE001
                log.warning("failed to remove orphan %s: %s", container.short_id, exc)
        return count
