def test_image_model_tight_fit_branch(monkeypatch):
    from services.hwfit import image_models

    monkeypatch.setattr(
        image_models,
        "IMAGE_MODEL_REGISTRY",
        [
            {
                "id": "tight/model",
                "name": "Tight Model",
                "provider": "Local",
                "params_b": 1,
                "vram_bf16": 9,
                "vram_fp8": None,
                "vram_q4": None,
                "default_quant": "BF16",
                "quant_repos": {},
                "capabilities": ["text-to-image"],
                "description": "fits with minimal headroom",
                "quality": 80,
                "speed": 60,
                "released": "2026",
            }
        ],
    )

    result = image_models.rank_image_models({"has_gpu": True, "gpu_vram_gb": 10})[0]

    assert result["fits"] is True
    assert result["fit"] == "tight"
    assert result["fit_label"] == "Tight"
    assert result["score"] == 65.0
