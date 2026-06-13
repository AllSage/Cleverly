"""Local Training Lab routes."""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from core.middleware import require_admin
from src.offline_finetune import finetune_status, read_job, start_lora_job
from src.local_training import (
    DEFAULT_ORDER,
    MAX_DATASET_CHARS,
    MAX_GENERATE_CHARS,
    LocalTrainingError,
    create_dataset,
    ensure_training_dirs,
    generate_text,
    list_artifacts,
    list_datasets,
    train_ngram,
)


logger = logging.getLogger(__name__)


class DatasetCreateRequest(BaseModel):
    name: str = Field(default="Dataset", max_length=80)
    text: str = Field(min_length=32, max_length=MAX_DATASET_CHARS)


class TrainRequest(BaseModel):
    dataset_id: str
    model_name: str = Field(default="", max_length=80)
    order: int = Field(default=DEFAULT_ORDER, ge=1, le=5)


class GenerateRequest(BaseModel):
    artifact_id: str
    prompt: str = Field(default="", max_length=512)
    max_chars: int = Field(default=240, ge=1, le=MAX_GENERATE_CHARS)
    temperature: float = Field(default=0.8, ge=0, le=2)
    seed: int | None = None


class FineTuneRequest(BaseModel):
    dataset_id: str
    model_id: str
    output_name: str = Field(default="local-lora", max_length=80)
    max_steps: int = Field(default=20, ge=1, le=1000)
    epochs: int = Field(default=1, ge=1, le=10)
    batch_size: int = Field(default=1, ge=1, le=16)
    learning_rate: float = Field(default=2e-4, ge=1e-7, le=1.0)
    max_length: int = Field(default=512, ge=64, le=2048)
    lora_rank: int = Field(default=8, ge=1, le=256)
    target_modules: str = Field(
        default="q_proj,k_proj,v_proj,o_proj,gate_proj,up_proj,down_proj",
        max_length=240,
    )


def _bad_request(exc: LocalTrainingError) -> HTTPException:
    return HTTPException(status_code=400, detail=str(exc))


def setup_training_routes() -> APIRouter:
    router = APIRouter(
        prefix="/api/training",
        tags=["training"],
        dependencies=[Depends(require_admin)],
    )

    @router.get("/status")
    def training_status():
        """Return local training datasets and artifacts."""
        root = ensure_training_dirs()
        return {
            "ok": True,
            "mode": "offline-local",
            "root": str(root),
            "default_order": DEFAULT_ORDER,
            "max_dataset_chars": MAX_DATASET_CHARS,
            "max_generate_chars": MAX_GENERATE_CHARS,
            "datasets": list_datasets(),
            "artifacts": list_artifacts(),
            "finetune": finetune_status(),
        }

    @router.post("/datasets")
    def training_create_dataset(body: DatasetCreateRequest):
        try:
            return {"ok": True, "dataset": create_dataset(body.name, body.text)}
        except LocalTrainingError as exc:
            raise _bad_request(exc)

    @router.post("/train")
    def training_train(body: TrainRequest):
        try:
            artifact = train_ngram(body.dataset_id, body.model_name, body.order)
            return {"ok": True, "artifact": artifact}
        except LocalTrainingError as exc:
            raise _bad_request(exc)

    @router.post("/generate")
    def training_generate(body: GenerateRequest):
        try:
            output = generate_text(
                body.artifact_id,
                prompt=body.prompt,
                max_chars=body.max_chars,
                temperature=body.temperature,
                seed=body.seed,
            )
            return {"ok": True, "output": output}
        except LocalTrainingError as exc:
            raise _bad_request(exc)

    @router.get("/finetune/status")
    def training_finetune_status():
        return {"ok": True, "finetune": finetune_status()}

    @router.post("/finetune/jobs")
    def training_start_finetune(body: FineTuneRequest):
        try:
            job = start_lora_job(
                dataset_id=body.dataset_id,
                model_id=body.model_id,
                output_name=body.output_name,
                max_steps=body.max_steps,
                epochs=body.epochs,
                batch_size=body.batch_size,
                learning_rate=body.learning_rate,
                max_length=body.max_length,
                lora_rank=body.lora_rank,
                target_modules=body.target_modules,
            )
            return {"ok": True, "job": job}
        except LocalTrainingError as exc:
            raise _bad_request(exc)

    @router.get("/finetune/jobs/{job_id}")
    def training_get_finetune_job(job_id: str):
        try:
            return {"ok": True, "job": read_job(job_id)}
        except LocalTrainingError as exc:
            raise _bad_request(exc)

    return router
