def get_backend(name: str):
    name = (name or "docker").lower()
    if name == "docker":
        from .docker_backend import DockerBackend
        return DockerBackend()
    if name in ("k8s", "kubernetes"):
        from .k8s_backend import K8sBackend
        return K8sBackend()
    raise ValueError(f"Unknown SANDBOX_BACKEND: {name}")
