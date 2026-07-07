"""Kubernetes backend: one hardened pod per session.

Pods are labeled app=strands-sandbox. Egress isolation is enforced by the
NetworkPolicies in k8s/network-policies.yaml: deny-all egress by default,
DNS allowed, full egress only for pods labeled egress=allowed (set when a
session is created with network=True). Delegated worker jobs need model
API access, so start those sessions with network=True or route through an
egress proxy you control.
"""

import logging
import os
import time

from kubernetes import client, config
from kubernetes.client import (
    V1Capabilities,
    V1Container,
    V1ContainerPort,
    V1EmptyDirVolumeSource,
    V1EnvVar,
    V1HTTPGetAction,
    V1ObjectMeta,
    V1Pod,
    V1PodSpec,
    V1Probe,
    V1ResourceRequirements,
    V1SeccompProfile,
    V1SecurityContext,
    V1Volume,
    V1VolumeMount,
)

from .base import Backend

log = logging.getLogger("sandbox.k8s")

APP_LABEL = "strands-sandbox"


class K8sBackend(Backend):
    def __init__(self):
        try:
            config.load_incluster_config()
        except config.ConfigException:
            config.load_kube_config()
        self.core = client.CoreV1Api()
        self.namespace = os.environ.get("SANDBOX_NAMESPACE", "strands-sandboxes")

    def create(self, session_id, image, env, cpus, memory_mb, network, ttl_seconds):
        name = f"sbx-{session_id}"
        workspace_mb = int(os.environ.get("SANDBOX_WORKSPACE_MB", "1024"))
        labels = {
            "app": APP_LABEL,
            "session": session_id,
            "egress": "allowed" if network else "none",
        }
        container = V1Container(
            name="sandbox",
            image=image,
            image_pull_policy=os.environ.get("SANDBOX_IMAGE_PULL_POLICY", "IfNotPresent"),
            ports=[V1ContainerPort(container_port=8000)],
            env=[V1EnvVar(name=k, value=str(v)) for k, v in env.items()],
            resources=V1ResourceRequirements(
                requests={"cpu": str(min(cpus, 0.25)), "memory": "256Mi"},
                limits={"cpu": str(cpus), "memory": f"{memory_mb}Mi"},
            ),
            security_context=V1SecurityContext(
                run_as_non_root=True,
                run_as_user=1000,
                allow_privilege_escalation=False,
                read_only_root_filesystem=True,
                capabilities=V1Capabilities(drop=["ALL"]),
                seccomp_profile=V1SeccompProfile(type="RuntimeDefault"),
            ),
            volume_mounts=[
                V1VolumeMount(name="workspace", mount_path="/workspace"),
                V1VolumeMount(name="tmp", mount_path="/tmp"),
            ],
            readiness_probe=V1Probe(
                http_get=V1HTTPGetAction(path="/health", port=8000),
                initial_delay_seconds=2,
                period_seconds=2,
                failure_threshold=45,
            ),
        )
        pod = V1Pod(
            metadata=V1ObjectMeta(name=name, labels=labels),
            spec=V1PodSpec(
                restart_policy="Never",
                automount_service_account_token=False,
                active_deadline_seconds=ttl_seconds + 3600,
                containers=[container],
                volumes=[
                    V1Volume(
                        name="workspace",
                        empty_dir=V1EmptyDirVolumeSource(size_limit=f"{workspace_mb}Mi"),
                    ),
                    V1Volume(
                        name="tmp",
                        empty_dir=V1EmptyDirVolumeSource(size_limit="256Mi"),
                    ),
                ],
            ),
        )
        self.core.create_namespaced_pod(namespace=self.namespace, body=pod)

        pod_ip = None
        deadline = time.time() + 120
        while time.time() < deadline:
            status = self.core.read_namespaced_pod(name=name, namespace=self.namespace).status
            if status.phase in ("Failed", "Succeeded"):
                raise RuntimeError(f"sandbox pod entered phase {status.phase}")
            if status.pod_ip:
                pod_ip = status.pod_ip
                break
            time.sleep(1)
        if not pod_ip:
            self.destroy(name)
            raise RuntimeError("timed out waiting for sandbox pod IP")

        log.info("created pod %s at %s (network=%s)", name, pod_ip, network)
        return {"endpoint": f"http://{pod_ip}:8000", "ref": name}

    def destroy(self, ref):
        try:
            self.core.delete_namespaced_pod(
                name=ref, namespace=self.namespace, grace_period_seconds=0
            )
        except client.exceptions.ApiException as exc:
            if exc.status != 404:
                raise

    def cleanup_orphans(self):
        pods = self.core.list_namespaced_pod(
            namespace=self.namespace, label_selector=f"app={APP_LABEL}"
        )
        for pod in pods.items:
            self.destroy(pod.metadata.name)
        return len(pods.items)
