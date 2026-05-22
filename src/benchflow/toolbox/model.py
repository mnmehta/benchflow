from __future__ import annotations

from pathlib import Path

from ..contracts import ExecutionContext, ResolvedRunPlan, ValidationError
from ..model import download_model
from ..remote_jobs import remote_run_plan_json, run_remote_job


def download_cached_model(
    plan: ResolvedRunPlan,
    *,
    context: ExecutionContext,
    skip_if_exists: bool = True,
    config_only: bool = False,
) -> Path:
    if plan.target_cluster.enabled():
        run_remote_job(
            plan,
            job_kind="model-download",
            args=[
                "model",
                "download",
                "--run-plan-json",
                remote_run_plan_json(plan),
                "--models-storage-path",
                "/models-storage",
                *([] if skip_if_exists else ["--no-skip-if-exists"]),
                *(["--config-only"] if config_only else []),
            ],
            volume_mounts=[
                {"name": "models-storage", "mountPath": "/models-storage"},
            ],
            volumes=[
                {
                    "name": "models-storage",
                    "persistentVolumeClaim": {
                        "claimName": plan.deployment.model_storage.pvc_name
                    },
                }
            ],
        )
        return (
            Path("/models-storage")
            / plan.deployment.model_storage.cache_dir.lstrip("/")
            / plan.model.pvc_directory_name
        )
    if context.models_storage_path is None:
        raise ValidationError("model download requires a models storage path")
    return download_model(
        plan,
        models_storage_path=context.models_storage_path,
        skip_if_exists=skip_if_exists,
        config_only=config_only,
    )
