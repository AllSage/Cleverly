import builtins
import sys
import types
from types import SimpleNamespace

from services.tts import tts_service


def test_tts_load_settings_lazy_kokoro_and_unknown_provider(monkeypatch, tmp_path):
    settings_module = types.ModuleType("src.settings")
    settings_module.load_settings = lambda: {"tts_provider": "browser"}
    monkeypatch.setitem(sys.modules, "src.settings", settings_module)

    service = tts_service.TTSService(cache_dir=str(tmp_path / "tts"))
    loaded = service._load_settings()
    assert loaded == {
        "tts_enabled": True,
        "tts_provider": "browser",
        "tts_model": "tts-1",
        "tts_voice": "alloy",
        "tts_speed": "1",
    }

    created = []

    class FakeKokoro:
        available = True

        def __init__(self):
            created.append("created")

    monkeypatch.setattr(tts_service, "_KokoroPipeline", FakeKokoro)
    assert service._get_kokoro() is service._get_kokoro()
    assert created == ["created"]

    monkeypatch.setattr(
        service,
        "_load_settings",
        lambda: {"tts_provider": "unknown", "tts_model": "m", "tts_voice": "v", "tts_speed": "1"},
    )
    assert service.synthesize("hello") is None


def test_tts_api_adds_authorization_header(monkeypatch, tmp_path):
    service = tts_service.TTSService(cache_dir=str(tmp_path / "tts"))
    monkeypatch.setattr(tts_service, "offline_mode", lambda: False)
    monkeypatch.setattr(tts_service, "load_features", lambda: {"external_model_endpoints": True})

    class Endpoint:
        base_url = "http://voice.example/"
        api_key = "secret"

    class Query:
        def filter(self, _condition):
            return self

        def first(self):
            return Endpoint()

    class DB:
        def query(self, _model):
            return Query()

        def close(self):
            self.closed = True

    database = types.ModuleType("src.database")
    database.SessionLocal = lambda: DB()
    database.ModelEndpoint = SimpleNamespace(id=object())
    monkeypatch.setitem(sys.modules, "src.database", database)

    class Response:
        content = b"ID3ok"

        def raise_for_status(self):
            return None

    calls = []
    monkeypatch.setattr(
        tts_service.httpx,
        "post",
        lambda url, json=None, headers=None, timeout=None: calls.append((url, json, headers, timeout)) or Response(),
    )

    assert service._synthesize_api("hello", "ep1", "tts-1", "nova", 0.75) == b"ID3ok"
    assert calls[-1][2]["Authorization"] == "Bearer secret"
    assert calls[-1][1]["voice"] == "nova"


def test_tts_local_and_browser_stats(monkeypatch, tmp_path):
    service = tts_service.TTSService(cache_dir=str(tmp_path / "tts"))
    (service.cache_dir / "a.wav").write_bytes(b"a" * 10)

    class AvailableKokoro:
        available = True

    monkeypatch.setattr(service, "_get_kokoro", lambda: AvailableKokoro())
    monkeypatch.setattr(
        service,
        "_load_settings",
        lambda: {"tts_enabled": True, "tts_provider": "local", "tts_model": "m", "tts_voice": "v", "tts_speed": "1"},
    )
    local = service.get_stats()
    assert local["model"] == "Kokoro-82M (GPU)"
    assert local["cache_entries"] == 1

    class MissingKokoro:
        available = False

    monkeypatch.setattr(service, "_get_kokoro", lambda: MissingKokoro())
    assert service.get_stats()["model"] == "Kokoro (not loaded)"

    monkeypatch.setattr(
        service,
        "_load_settings",
        lambda: {"tts_enabled": True, "tts_provider": "browser", "tts_model": "m", "tts_voice": "v", "tts_speed": "2"},
    )
    assert service.get_stats()["model"] == "Browser (Web Speech API)"


def test_kokoro_init_import_cuda_success_and_failure_branches(monkeypatch):
    original_import = builtins.__import__

    def blocked_import(name, *args, **kwargs):
        if name == "torch":
            raise ImportError("no torch")
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", blocked_import)
    missing = tts_service._KokoroPipeline()
    assert missing.available is False

    class CudaUnavailable:
        @staticmethod
        def is_available():
            return False

    monkeypatch.setattr(builtins, "__import__", original_import)
    monkeypatch.setitem(sys.modules, "torch", SimpleNamespace(cuda=CudaUnavailable()))
    monkeypatch.setitem(sys.modules, "kokoro", SimpleNamespace(KPipeline=lambda lang_code: object()))
    no_cuda = tts_service._KokoroPipeline()
    assert no_cuda.available is False

    class DeviceContext:
        def __enter__(self):
            return None

        def __exit__(self, *_args):
            return False

    class CudaAvailable:
        @staticmethod
        def is_available():
            return True

        @staticmethod
        def device(_index):
            return DeviceContext()

    class Model:
        def to(self, device):
            self.device = device
            return self

    class Pipeline:
        def __init__(self, lang_code):
            self.lang_code = lang_code
            self.model = Model()

    monkeypatch.setitem(
        sys.modules,
        "torch",
        SimpleNamespace(cuda=CudaAvailable(), device=lambda name: name),
    )
    monkeypatch.setitem(sys.modules, "kokoro", SimpleNamespace(KPipeline=Pipeline))
    loaded = tts_service._KokoroPipeline()
    assert loaded.available is True
    assert loaded.device == "cuda:0"

    def broken_pipeline(_lang_code):
        raise RuntimeError("boom")

    monkeypatch.setitem(sys.modules, "kokoro", SimpleNamespace(KPipeline=broken_pipeline))
    failed = tts_service._KokoroPipeline()
    assert failed.available is False


def test_kokoro_synthesize_raw_empty_success_and_exception(monkeypatch):
    class DeviceContext:
        def __enter__(self):
            return None

        def __exit__(self, *_args):
            return False

    fake_torch = SimpleNamespace(cuda=SimpleNamespace(device=lambda _device: DeviceContext()))

    class FakeArray:
        def __mul__(self, _value):
            return self

        def astype(self, _dtype):
            return self

        def tobytes(self):
            return b"\x00\x00" * 4

    fake_numpy = SimpleNamespace(concatenate=lambda chunks: FakeArray(), int16=object())
    monkeypatch.setitem(sys.modules, "torch", fake_torch)
    monkeypatch.setitem(sys.modules, "numpy", fake_numpy)

    kokoro = tts_service._KokoroPipeline.__new__(tts_service._KokoroPipeline)
    kokoro.available = True
    kokoro.device = "cuda:0"
    kokoro.pipeline = lambda text, voice=None: []
    assert kokoro.synthesize_raw("hello") is None

    kokoro.pipeline = lambda text, voice=None: [(None, None, object())]
    wav = kokoro.synthesize_raw("hello", voice="af_heart")
    assert wav.startswith(b"RIFF")

    kokoro.pipeline = lambda text, voice=None: (_ for _ in ()).throw(RuntimeError("bad synth"))
    assert kokoro.synthesize_raw("hello") is None
