import json
import os
import sys
import types
from pathlib import Path

import numpy as np
import pytest

from src import embeddings as emb


EMBED_ENV_KEYS = [
    "EMBEDDING_URL",
    "EMBEDDING_MODEL",
    "FASTEMBED_MODEL",
    "FASTEMBED_CACHE_PATH",
    "LLM_HOST",
    "CLEVERLY_OFFLINE",
    "CLEVERLY_OFFLINE_EMBEDDINGS",
]


@pytest.fixture(autouse=True)
def restore_embedding_state():
    original = {key: os.environ.get(key) for key in EMBED_ENV_KEYS}
    original_down = emb._http_embed_down
    yield
    for key, value in original.items():
        if value is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = value
    emb._http_embed_down = original_down


class FakeResponse:
    def __init__(self, payload):
        self.payload = payload
        self.raised = False

    def raise_for_status(self):
        self.raised = True

    def json(self):
        return self.payload


class FakeHttpClient:
    def __init__(self, timeout=None):
        self.timeout = timeout
        self.posts = []

    def post(self, url, json):
        self.posts.append((url, json))
        base = len(self.posts) * 10
        payload = {
            "data": [
                {"index": 1, "embedding": [0.0, 0.0]},
                {"index": 0, "embedding": [float(base), 0.0]},
            ]
        }
        return FakeResponse(payload)


def test_http_embedding_client_batches_sorts_normalizes_and_caches_dimension(monkeypatch):
    created = []

    def make_client(timeout=None):
        client = FakeHttpClient(timeout)
        created.append(client)
        return client

    monkeypatch.setenv("LLM_HOST", "llm.local")
    monkeypatch.setattr(emb.httpx, "Client", make_client)

    client = emb.EmbeddingClient(model="embed-model")
    empty = client.encode([])
    assert empty.dtype == np.float32
    assert empty.size == 0

    vecs = client.encode([f"text-{idx}" for idx in range(65)])

    assert client.url == "http://llm.local:11434/v1/embeddings"
    assert client.model == "embed-model"
    assert len(created[0].posts) == 2
    assert created[0].posts[0][1]["input"][0] == "text-0"
    assert created[0].posts[1][1]["input"] == ["text-64"]
    assert vecs.shape == (4, 2)
    assert np.allclose(vecs[0], [1.0, 0.0])
    assert np.allclose(vecs[1], [0.0, 0.0])
    assert client.get_sentence_embedding_dimension() == 2
    assert client.get_sentence_embedding_dimension() == 2

    fresh = emb.EmbeddingClient(url="http://probe", model="probe-model")
    assert fresh.get_sentence_embedding_dimension() == 2
    assert fresh.get_sentence_embedding_dimension() == 2


def test_http_embedding_client_uses_explicit_url_and_can_skip_normalization(monkeypatch):
    created = []
    monkeypatch.setattr(
        emb.httpx,
        "Client",
        lambda timeout=None: created.append(FakeHttpClient(timeout)) or created[-1],
    )

    client = emb.EmbeddingClient(url="http://embed", model=None)
    vecs = client.encode(["a"], normalize_embeddings=False)

    assert client.url == "http://embed"
    assert vecs.tolist() == [[10.0, 0.0], [0.0, 0.0]]


def install_fastembed(monkeypatch, vectors=None):
    class FakeTextEmbedding:
        created = []

        def __init__(self, **kwargs):
            self.kwargs = kwargs
            self.__class__.created.append(kwargs)

        def embed(self, texts):
            source = vectors if vectors is not None else [[3.0, 4.0], [0.0, 0.0]]
            for idx, _text in enumerate(texts):
                yield source[idx % len(source)]

    fastembed = types.ModuleType("fastembed")
    fastembed.TextEmbedding = FakeTextEmbedding
    monkeypatch.setitem(sys.modules, "fastembed", fastembed)
    return FakeTextEmbedding


def test_fastembed_client_success_empty_normalized_cached_dimension(tmp_path, monkeypatch):
    fake_text_embedding = install_fastembed(monkeypatch)
    monkeypatch.setenv("FASTEMBED_CACHE_PATH", str(tmp_path / "cache"))
    monkeypatch.setenv("FASTEMBED_MODEL", "local-model")

    client = emb.FastEmbedClient()

    assert client.model == "local-model"
    assert client.url == "local://fastembed"
    assert fake_text_embedding.created[0]["cache_dir"] == str(tmp_path / "cache")
    assert client.encode([]).size == 0
    vecs = client.encode(["one", "two"])
    assert np.allclose(vecs[0], [0.6, 0.8])
    assert np.allclose(vecs[1], [0.0, 0.0])
    assert client.get_sentence_embedding_dimension() == 2
    assert client.get_sentence_embedding_dimension() == 2

    fresh = emb.FastEmbedClient(model="fresh-model")
    assert fresh.get_sentence_embedding_dimension() == 2
    assert fresh.get_sentence_embedding_dimension() == 2


def test_fastembed_client_import_error_message(monkeypatch):
    monkeypatch.setitem(sys.modules, "fastembed", None)

    with pytest.raises(RuntimeError, match="Local fastembed is not installed"):
        emb.FastEmbedClient()


def test_fastembed_windows_broken_symlink_heal_and_skip_on_error(tmp_path, monkeypatch):
    fake_text_embedding = install_fastembed(monkeypatch)
    cache_dir = tmp_path / "cache"
    onnx = str(cache_dir / "models--org--embed" / "snapshots" / "rev" / "model.onnx")
    removed = []

    fake_glob = types.ModuleType("glob")
    fake_glob.glob = lambda *args, **kwargs: [onnx]
    fake_shutil = types.ModuleType("shutil")
    fake_shutil.rmtree = lambda path, ignore_errors=False: removed.append((path, ignore_errors))
    monkeypatch.setitem(sys.modules, "glob", fake_glob)
    monkeypatch.setitem(sys.modules, "shutil", fake_shutil)
    monkeypatch.setenv("FASTEMBED_CACHE_PATH", str(cache_dir))
    monkeypatch.setattr(emb.os, "name", "nt", raising=False)
    monkeypatch.setattr(emb.os.path, "islink", lambda path: path == onnx)
    real_exists = os.path.exists
    monkeypatch.setattr(emb.os.path, "exists", lambda path: False if path == onnx else real_exists(path))

    emb.FastEmbedClient(model="heal-model")

    assert removed == [(str(cache_dir / "models--org--embed"), True)]
    assert fake_text_embedding.created[-1]["model_name"] == "heal-model"

    fake_glob.glob = lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("glob failed"))
    emb.FastEmbedClient(model="skip-error")
    assert fake_text_embedding.created[-1]["model_name"] == "skip-error"


def test_fastembed_windows_symlink_heal_loop_break(monkeypatch):
    fake_text_embedding = install_fastembed(monkeypatch)
    fake_glob = types.ModuleType("glob")
    fake_glob.glob = lambda *args, **kwargs: ["stuck.onnx"]
    fake_shutil = types.ModuleType("shutil")
    fake_shutil.rmtree = lambda *args, **kwargs: None
    monkeypatch.setitem(sys.modules, "glob", fake_glob)
    monkeypatch.setitem(sys.modules, "shutil", fake_shutil)
    monkeypatch.setattr(emb.os, "name", "nt", raising=False)
    monkeypatch.setattr(emb.os, "makedirs", lambda *args, **kwargs: None)
    monkeypatch.setattr(emb.os.path, "islink", lambda path: True)
    monkeypatch.setattr(emb.os.path, "exists", lambda path: False)
    monkeypatch.setattr(emb.os.path, "basename", lambda path: path)
    monkeypatch.setattr(emb.os.path, "dirname", lambda path: path)

    emb.FastEmbedClient(model="break-model")

    assert fake_text_embedding.created[-1]["model_name"] == "break-model"


def test_load_persisted_endpoint_success_missing_no_url_and_invalid_json(tmp_path, monkeypatch):
    fake_file = tmp_path / "src" / "embeddings.py"
    endpoint_file = tmp_path / "data" / "embedding_endpoint.json"
    endpoint_file.parent.mkdir()
    fake_file.parent.mkdir()
    fake_file.write_text("", encoding="utf-8")
    monkeypatch.setattr(emb, "__file__", str(fake_file))

    assert emb._load_persisted_endpoint() == {}

    endpoint_file.write_text(json.dumps({"model": "missing-url"}), encoding="utf-8")
    assert emb._load_persisted_endpoint() == {}

    endpoint_file.write_text(json.dumps({"url": "http://saved", "model": "saved-model"}), encoding="utf-8")
    assert emb._load_persisted_endpoint() == {"url": "http://saved", "model": "saved-model"}

    endpoint_file.write_text("{", encoding="utf-8")
    assert emb._load_persisted_endpoint() == {}


def test_truthy_and_reset_http_embed_state():
    emb._http_embed_down = True

    assert emb._truthy(" yes ") is True
    assert emb._truthy("0") is False
    assert emb._truthy(None) is False
    emb.reset_http_embed_state()
    assert emb._http_embed_down is False


def test_get_embedding_client_offline_disabled(monkeypatch):
    monkeypatch.setenv("CLEVERLY_OFFLINE", "1")
    monkeypatch.delenv("CLEVERLY_OFFLINE_EMBEDDINGS", raising=False)

    assert emb.get_embedding_client() is None


def test_get_embedding_client_http_success_with_persisted_endpoint(monkeypatch):
    class GoodHttpClient:
        def __init__(self):
            self.url = "http://client"
            self.model = "client-model"
            self.probed = False

        def get_sentence_embedding_dimension(self):
            self.probed = True
            return 2

    client = GoodHttpClient()
    monkeypatch.setattr(emb, "_load_persisted_endpoint", lambda: {"url": "http://saved", "model": "saved-model"})
    monkeypatch.setattr(emb, "EmbeddingClient", lambda: client)

    assert emb.get_embedding_client() is client
    assert client.probed is True
    assert os.environ["EMBEDDING_URL"] == "http://saved"
    assert os.environ["EMBEDDING_MODEL"] == "saved-model"


def test_get_embedding_client_http_failure_falls_back_and_latches(monkeypatch):
    class BadHttpClient:
        def get_sentence_embedding_dimension(self):
            raise RuntimeError("http down")

    class GoodFastEmbed:
        model = "fast"

        def __init__(self):
            self.probed = False

        def get_sentence_embedding_dimension(self):
            self.probed = True
            return 2

    monkeypatch.setattr(emb, "_load_persisted_endpoint", lambda: {})
    monkeypatch.setattr(emb, "EmbeddingClient", lambda: BadHttpClient())
    monkeypatch.setattr(emb, "FastEmbedClient", GoodFastEmbed)

    client = emb.get_embedding_client()

    assert isinstance(client, GoodFastEmbed)
    assert client.probed is True
    assert emb._http_embed_down is True


def test_get_embedding_client_offline_embeddings_skip_http_and_fastembed_errors(monkeypatch):
    class GoodFastEmbed:
        model = "fast"

        def get_sentence_embedding_dimension(self):
            return 3

    monkeypatch.setenv("CLEVERLY_OFFLINE", "1")
    monkeypatch.setenv("CLEVERLY_OFFLINE_EMBEDDINGS", "1")
    monkeypatch.setattr(emb, "_load_persisted_endpoint", lambda: {"url": "http://saved"})
    monkeypatch.setattr(emb, "EmbeddingClient", lambda: (_ for _ in ()).throw(AssertionError("should skip http")))
    monkeypatch.setattr(emb, "FastEmbedClient", lambda: GoodFastEmbed())
    assert isinstance(emb.get_embedding_client(), GoodFastEmbed)

    monkeypatch.delenv("CLEVERLY_OFFLINE", raising=False)
    emb._http_embed_down = True
    monkeypatch.setattr(emb, "FastEmbedClient", lambda: (_ for _ in ()).throw(ImportError("missing")))
    assert emb.get_embedding_client() is None

    monkeypatch.setattr(emb, "FastEmbedClient", lambda: (_ for _ in ()).throw(RuntimeError("broken")))
    assert emb.get_embedding_client() is None
