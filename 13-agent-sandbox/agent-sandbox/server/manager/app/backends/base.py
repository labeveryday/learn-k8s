"""Backend interface for sandbox isolation providers."""

from abc import ABC, abstractmethod


class Backend(ABC):
    @abstractmethod
    def create(
        self,
        session_id: str,
        image: str,
        env: dict,
        cpus: float,
        memory_mb: int,
        network: bool,
        ttl_seconds: int,
    ) -> dict:
        """Start an isolated sandbox. Returns {"endpoint": url, "ref": opaque_id}."""

    @abstractmethod
    def destroy(self, ref: str) -> None:
        """Tear down a sandbox by its backend ref."""

    @abstractmethod
    def cleanup_orphans(self) -> int:
        """Remove any sandboxes left over from a previous manager run."""
