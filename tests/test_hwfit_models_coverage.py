import builtins
import json


def test_hwfit_params_parses_units_and_bare_counts():
    from services.hwfit import models

    assert models.params_b({"parameters_raw": 7_000_000_000}) == 7.0
    assert models.params_b({"parameter_count": "7B"}) == 7.0
    assert models.params_b({"parameter_count": "750M"}) == 0.75
    assert models.params_b({"parameter_count": "500K"}) == 0.0005
    assert models.params_b({"parameter_count": "1.5T"}) == 1500.0
    assert models.params_b({"parameter_count": "355"}) == 0.355
    assert models.params_b({"parameter_count": "7000"}) == 7.0
    assert models.params_b({"parameter_count": "7000000000"}) == 7.0
    assert models.params_b({"parameter_count": "not-a-count"}) == 0.0
    assert models.params_b({}) == 0.0


def test_hwfit_best_quant_for_prequantized_models(monkeypatch):
    from services.hwfit import models

    prequant = {"quantization": "AWQ-4bit", "parameter_count": "7B"}

    monkeypatch.setattr(models, "estimate_memory_gb", lambda _model, _quant, _ctx: 4.0)
    assert models.best_quant_for_budget(prequant, 5.0, 8192) == ("AWQ-4bit", 8192, 4.0)

    def halves_to_fit(_model, _quant, ctx):
        return 6.0 if ctx > 4096 else 4.0

    monkeypatch.setattr(models, "estimate_memory_gb", halves_to_fit)
    assert models.best_quant_for_budget(prequant, 5.0, 8192) == ("AWQ-4bit", 4096, 4.0)

    monkeypatch.setattr(models, "estimate_memory_gb", lambda _model, _quant, _ctx: 99.0)
    assert models.best_quant_for_budget(prequant, 5.0, 8192) == (None, None, None)


def test_hwfit_best_quant_for_gguf_models(monkeypatch):
    from services.hwfit import models

    dense = {"quantization": "GGUF", "parameter_count": "70B"}

    def q6_fits(_model, quant, ctx):
        if quant == "Q6_K" and ctx == 8192:
            return 23.0
        return 99.0

    monkeypatch.setattr(models, "estimate_memory_gb", q6_fits)
    assert models.best_quant_for_budget(dense, 24.0, 8192) == ("Q6_K", 8192, 23.0)

    def lower_context_fits(_model, quant, ctx):
        if quant == "Q4_K_M" and ctx == 4096:
            return 18.0
        return 99.0

    monkeypatch.setattr(models, "estimate_memory_gb", lower_context_fits)
    assert models.best_quant_for_budget(dense, 20.0, 8192) == ("Q4_K_M", 4096, 18.0)

    monkeypatch.setattr(models, "estimate_memory_gb", lambda _model, _quant, _ctx: 99.0)
    assert models.best_quant_for_budget(dense, 20.0, 8192) == (None, None, None)


def test_hwfit_memory_active_params_and_use_case_inference():
    from services.hwfit import models

    moe = {
        "name": "mixtral",
        "is_moe": True,
        "active_parameters": 12_000_000_000,
        "parameters_raw": 48_000_000_000,
    }
    dense = {"name": "dense", "parameters_raw": 7_000_000_000}

    assert models._active_params_b(moe) == 12.0
    assert models._active_params_b(dense) == 7.0
    assert models.estimate_memory_gb(moe, "Q4_K_M", 4096) == 48.0 * 0.58 + 0.000008 * 12.0 * 4096 + 0.5
    assert models.estimate_memory_gb(dense, "unknown-quant", 2048) == 7.0 * 0.58 + 0.000008 * 7.0 * 2048 + 0.5

    cases = [
        ({"name": "bge embedding"}, "embedding"),
        ({"name": "CosyVoice TTS"}, "tts"),
        ({"name": "Whisper ASR"}, "stt"),
        ({"name": "CodeLlama"}, "coding"),
        ({"name": "Qwen vision"}, "multimodal"),
        ({"name": "DeepSeek-R1 Reasoner"}, "reasoning"),
        ({"use_case": "instruction chat"}, "chat"),
        ({"name": "plain model"}, "general"),
    ]

    for model, expected in cases:
        assert models.infer_use_case(model) == expected


def test_hwfit_get_models_error_paths_and_catalog_path(monkeypatch, tmp_path):
    from services.hwfit import models

    real_open = builtins.open
    models._models_cache = None

    def missing_open(*_args, **_kwargs):
        raise FileNotFoundError("missing")

    monkeypatch.setattr(builtins, "open", missing_open)
    assert models.get_models() == []

    models._models_cache = None

    def bad_json_open(*_args, **_kwargs):
        class BadJsonFile:
            def __enter__(self):
                return self

            def __exit__(self, *_exc):
                return False

            def read(self):
                return "{bad"

        return BadJsonFile()

    monkeypatch.setattr(builtins, "open", bad_json_open)
    assert models.get_models() == []

    models._models_cache = None
    catalog = tmp_path / "hf_models.json"
    catalog.write_text(json.dumps([{"id": "local/model"}]), encoding="utf-8")

    monkeypatch.setattr(models.os.path, "dirname", lambda _path: str(tmp_path.parent))
    monkeypatch.setattr(models.os.path, "join", lambda *_parts: str(catalog))
    monkeypatch.setattr(builtins, "open", real_open)
    assert models.get_models() == [{"id": "local/model"}]
    assert models.get_models() == [{"id": "local/model"}]
    assert models.model_catalog_path() == str(catalog)
