"""
chroma_client.py

Singleton ChromaDB HTTP client.
Connects to a ChromaDB instance running as a standalone service.
"""

import os
import socket
import logging

from src.offline_policy import is_local_model_url
from src.settings import load_features, offline_mode

logger = logging.getLogger(__name__)

_client = None

# A short connect probe so an unreachable ChromaDB fails fast instead of
# blocking on the OS connection timeout (~30-60s, WinError 10060 on Windows),
# which otherwise stalls app startup. Tunable via CHROMADB_CONNECT_TIMEOUT.
_CONNECT_TIMEOUT = float(os.getenv("CHROMADB_CONNECT_TIMEOUT", "2.0"))


def _port_open(host: str, port: int, timeout: float = None) -> bool:
    """Return True if a TCP connection to host:port succeeds within timeout."""
    try:
        with socket.create_connection((host, port), timeout=timeout or _CONNECT_TIMEOUT):
            return True
    except OSError:
        return False


def _chroma_url(host: str, port: int) -> str:
    return f"http://{host}:{port}"


def _external_chroma_allowed(url: str) -> bool:
    if is_local_model_url(url):
        return True
    if offline_mode():
        return False
    try:
        return (load_features() or {}).get("network_integrations") is not False
    except Exception as exc:
        logger.warning("ChromaDB network integration feature check failed; blocking external host: %s", exc)
        return False


def get_chroma_client():
    """Get or create the singleton ChromaDB HTTP client.

    Raises RuntimeError with a clear install hint if the `chromadb` package
    is not installed — it's an optional dependency (RAG + memory vectors).
    """
    global _client
    if _client is not None:
        return _client

    host = os.getenv("CHROMADB_HOST", "localhost")
    port = int(os.getenv("CHROMADB_PORT", "8100"))
    if not _external_chroma_allowed(_chroma_url(host, port)):
        raise RuntimeError("External ChromaDB hosts are disabled")

    try:
        import chromadb
    except ImportError as e:
        raise RuntimeError(
            "ChromaDB integration is not installed. Install the optional "
            "dependency with: pip install chromadb-client"
        ) from e

    if not _port_open(host, port):
        raise RuntimeError(
            f"ChromaDB is not reachable at {host}:{port}. Start the ChromaDB "
            f"service (e.g. `docker compose up chromadb`) or set CHROMADB_HOST / "
            f"CHROMADB_PORT to point at a running instance."
        )

    client = chromadb.HttpClient(host=host, port=port)

    # Health check before caching — if the port is open but the service isn't
    # healthy yet (e.g. still starting), don't poison the singleton with a dead
    # client; leave _client unset so the next call retries.
    client.heartbeat()
    _client = client
    logger.info(f"ChromaDB connected: {host}:{port}")
    return _client


def reset_client():
    """Reset the singleton (e.g. after config change)."""
    global _client
    _client = None
