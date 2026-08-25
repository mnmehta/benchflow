from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from typing import Any

import yaml

from ..assets import asset_text, render_jinja_text, render_jinja_yaml_document
from ..models import ResolvedRunPlan, ValidationError, model_storage_relative_path
from ..rhoai_mooncake import (
    mooncake_configmap_name,
    rhoai_mooncake_model_env,
    rhoai_mooncake_model_volume,
    rhoai_mooncake_model_volume_mount,
    rhoai_mooncake_spec,
    rhoai_mooncake_store_sidecar,
    render_rhoai_mooncake_manifests,
)
from ..rhoai_gateway import rhoai_release_gateway_reference

RHOAI_PROFILER_CONFIGMAP_SUFFIX = "vllm-profiler"
RHOAI_PROFILER_MOUNT_PATH = "/home/vllm/profiler"
RHOAI_PROFILER_OUTPUT_DIR = "/tmp/benchflow-profiler"
RAHIIS_PROGRESS_DEADLINE_SECONDS = 1800


def _deep_merge(base: dict[str, Any], overlay: dict[str, Any]) -> dict[str, Any]:
    merged = deepcopy(base)
    for key, value in overlay.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _deep_merge(merged[key], value)
        else:
            merged[key] = value
    return merged


def _base_labels(plan: ResolvedRunPlan) -> dict[str, str]:
    labels = {
        "app.kubernetes.io/name": "benchflow",
        "benchflow.io/experiment": plan.metadata.name,
        "benchflow.io/platform": plan.deployment.platform,
        "benchflow.io/mode": plan.deployment.mode,
    }
    placement = plan.deployment.runtime.placement
    if placement.mode == "same-node":
        labels["benchflow.io/placement-pool"] = placement.spread_pool
        labels["benchflow.io/placement-group"] = plan.deployment.release_name
    return labels


def _append_affinity_terms(
    affinity: dict[str, Any], section: str, key: str, terms: list[dict[str, Any]]
) -> None:
    pod_affinity = affinity.setdefault(section, {})
    existing = pod_affinity.get(key)
    if existing is None:
        pod_affinity[key] = terms
        return
    if isinstance(existing, list):
        for term in terms:
            if term not in existing:
                existing.append(term)
        return
    raise ValidationError(f"runtime.affinity.{section}.{key} must be a list")


def _runtime_affinity(plan: ResolvedRunPlan) -> dict[str, Any]:
    affinity = deepcopy(plan.deployment.runtime.affinity)
    placement = plan.deployment.runtime.placement
    # Sequential controls matrix submission order in the orchestration layer.
    # It must not constrain unrelated matrices at pod scheduling time.
    if placement.mode != "same-node":
        return affinity

    # LLMInferenceService exposes only a PodSpec template, not PodTemplate metadata,
    # so custom BenchFlow placement labels cannot be propagated to model pods.
    # Use stable RHOAI/KServe-generated workload labels instead.
    workload_selector = {
        "app.kubernetes.io/part-of": "llminferenceservice",
        "kserve.io/component": "workload",
    }
    workload_identity_label = "app.kubernetes.io/name"
    _append_affinity_terms(
        affinity,
        "podAffinity",
        "requiredDuringSchedulingIgnoredDuringExecution",
        [
            {
                "topologyKey": "kubernetes.io/hostname",
                "labelSelector": {
                    "matchLabels": workload_selector,
                },
                "matchLabelKeys": [workload_identity_label],
            }
        ],
    )
    _append_affinity_terms(
        affinity,
        "podAntiAffinity",
        "requiredDuringSchedulingIgnoredDuringExecution",
        [
            {
                "topologyKey": "kubernetes.io/hostname",
                "labelSelector": {
                    "matchLabels": workload_selector,
                },
                "mismatchLabelKeys": [workload_identity_label],
            }
        ],
    )
    return affinity


def _validate_rhoai_profiling(plan: ResolvedRunPlan) -> None:
    if not plan.execution.profiling.enabled:
        return
    if plan.deployment.platform != "rhoai":
        raise ValidationError(
            "execution.profiling is currently supported only for rhoai deployments"
        )


def _model_path(plan: ResolvedRunPlan) -> str:
    return f"/{model_storage_relative_path(plan.deployment.model_storage, plan.model)}"


def render_llmd_values(plan: ResolvedRunPlan) -> dict[str, Any]:
    return {
        "releaseName": plan.deployment.release_name,
        "platform": plan.deployment.platform,
        "mode": plan.deployment.mode,
        "namespace": plan.deployment.namespace,
        "repoRef": plan.deployment.repo_ref,
        "platformChannel": plan.deployment.platform_channel,
        "gateway": plan.deployment.gateway,
        "schedulerProfile": plan.deployment.scheduler_profile,
        "schedulerImage": plan.deployment.scheduler_image,
        "modelArtifacts": {
            "name": plan.model.name,
            "uri": f"pvc://{plan.deployment.model_storage.pvc_name}{_model_path(plan)}",
        },
        "runtime": {
            "image": plan.deployment.runtime.image,
            "replicas": plan.deployment.runtime.replicas,
            "tensorParallelism": plan.deployment.runtime.tensor_parallelism,
            "vllmArgs": plan.deployment.runtime.vllm_args,
            "env": plan.deployment.runtime.env,
            "sharedMemorySize": plan.deployment.runtime.shared_memory_size,
            "serviceAccountName": plan.deployment.runtime.service_account_name,
            "nodeSelector": plan.deployment.runtime.node_selector,
            "affinity": plan.deployment.runtime.affinity,
            "tolerations": plan.deployment.runtime.tolerations,
            "imagePullSecrets": plan.deployment.runtime.image_pull_secrets,
            "hostPaths": [
                {
                    "name": host_path.name,
                    "hostPath": host_path.host_path,
                    "mountPath": host_path.mount_path,
                    "type": host_path.type,
                    "readOnly": host_path.read_only,
                }
                for host_path in plan.deployment.runtime.host_paths
            ],
            "pvcMounts": [
                {
                    "name": pvc_mount.name,
                    "claimName": pvc_mount.claim_name,
                    "mountPath": pvc_mount.mount_path,
                    "readOnly": pvc_mount.read_only,
                    "create": pvc_mount.create,
                    "storageClassName": pvc_mount.storage_class_name,
                    "size": pvc_mount.size,
                    "accessModes": list(pvc_mount.access_modes),
                }
                for pvc_mount in plan.deployment.runtime.pvc_mounts
            ],
            "resources": {
                "limits": dict(plan.deployment.runtime.resources.limits),
                "requests": dict(plan.deployment.runtime.resources.requests),
                "removeLimits": list(plan.deployment.runtime.resources.remove_limits),
                "removeRequests": list(
                    plan.deployment.runtime.resources.remove_requests
                ),
            },
        },
        "options": plan.deployment.options,
    }


def _runtime_resource_requirements(
    plan: ResolvedRunPlan, *, include_gpu: bool
) -> dict[str, dict[str, str]]:
    resources = {
        "limits": dict(plan.deployment.runtime.resources.limits),
        "requests": dict(plan.deployment.runtime.resources.requests),
    }
    if include_gpu:
        gpu_count = str(plan.deployment.runtime.tensor_parallelism)
        resources["limits"]["nvidia.com/gpu"] = gpu_count
        resources["requests"]["nvidia.com/gpu"] = gpu_count
    return resources


def _runtime_host_path_volume_mounts(plan: ResolvedRunPlan) -> list[dict[str, Any]]:
    mounts: list[dict[str, Any]] = []
    for host_path in plan.deployment.runtime.host_paths:
        mounts.append(
            {
                "name": host_path.name,
                "mountPath": host_path.mount_path,
                "readOnly": host_path.read_only,
            }
        )
    return mounts


def _runtime_pvc_volume_mounts(plan: ResolvedRunPlan) -> list[dict[str, Any]]:
    mounts: list[dict[str, Any]] = []
    for pvc_mount in plan.deployment.runtime.pvc_mounts:
        mounts.append(
            {
                "name": pvc_mount.name,
                "mountPath": pvc_mount.mount_path,
                "readOnly": pvc_mount.read_only,
            }
        )
    return mounts


def _runtime_host_path_volumes(plan: ResolvedRunPlan) -> list[dict[str, Any]]:
    volumes: list[dict[str, Any]] = []
    for host_path in plan.deployment.runtime.host_paths:
        host_path_spec = {"path": host_path.host_path, "type": host_path.type}
        volumes.append({"name": host_path.name, "hostPath": host_path_spec})
    return volumes


def _runtime_pvc_volumes(plan: ResolvedRunPlan) -> list[dict[str, Any]]:
    volumes: list[dict[str, Any]] = []
    for pvc_mount in plan.deployment.runtime.pvc_mounts:
        volumes.append(
            {
                "name": pvc_mount.name,
                "persistentVolumeClaim": {"claimName": pvc_mount.claim_name},
            }
        )
    return volumes


def render_runtime_pvc_manifests(plan: ResolvedRunPlan) -> list[dict[str, Any]]:
    manifests: list[dict[str, Any]] = []
    for pvc_mount in plan.deployment.runtime.pvc_mounts:
        if not pvc_mount.create:
            continue
        spec: dict[str, Any] = {
            "accessModes": list(pvc_mount.access_modes),
            "resources": {"requests": {"storage": pvc_mount.size}},
        }
        if pvc_mount.storage_class_name:
            spec["storageClassName"] = pvc_mount.storage_class_name
        manifests.append(
            {
                "apiVersion": "v1",
                "kind": "PersistentVolumeClaim",
                "metadata": {
                    "name": pvc_mount.claim_name,
                    "namespace": plan.deployment.namespace,
                    "labels": {
                        **_base_labels(plan),
                        "benchflow.io/purpose": "runtime-pvc-mount",
                    },
                },
                "spec": spec,
            }
        )
    return manifests


def _rhoai_uses_isvc(plan: ResolvedRunPlan) -> bool:
    return plan.deployment.mode == "isvc"


def _rhoai_runtime_env(plan: ResolvedRunPlan) -> list[dict[str, Any]]:
    env = {key: value for key, value in plan.deployment.runtime.env.items()}
    env.update(
        {entry["name"]: entry["value"] for entry in rhoai_mooncake_model_env(plan)}
    )
    return [{"name": key, "value": value} for key, value in sorted(env.items())]


def _rhoai_basic_model_path(plan: ResolvedRunPlan) -> str:
    mount_root = plan.deployment.model_storage.mount_path.rstrip("/")
    return f"{mount_root}/{model_storage_relative_path(plan.deployment.model_storage, plan.model)}"


def _rhoai_basic_runtime_env(plan: ResolvedRunPlan) -> list[dict[str, Any]]:
    mount_root = plan.deployment.model_storage.mount_path.rstrip("/")
    cache_dir = f"{mount_root}{plan.deployment.model_storage.cache_dir.rstrip('/')}"
    env = {
        "HOME": "/tmp/vllm-home",
        "HF_HOME": cache_dir,
        "TRANSFORMERS_CACHE": f"{cache_dir}/hub",
        "HF_HUB_CACHE": f"{cache_dir}/hub",
        **plan.deployment.runtime.env,
    }
    return [{"name": key, "value": value} for key, value in sorted(env.items())]


def _rhoai_vllm_args(plan: ResolvedRunPlan) -> list[str]:
    model_path = f"/mnt/models{_model_path(plan)}"
    return [
        "--port=8000",
        "--host=0.0.0.0",
        f"--model={model_path}",
        f"--served-model-name={plan.model.name}",
        f"--tensor-parallel-size={plan.deployment.runtime.tensor_parallelism}",
        "--enable-ssl-refresh",
        "--ssl-certfile=/var/run/kserve/tls/tls.crt",
        "--ssl-keyfile=/var/run/kserve/tls/tls.key",
    ] + plan.deployment.runtime.vllm_args


def _rhoai_basic_vllm_args(plan: ResolvedRunPlan) -> list[str]:
    return [
        "--port=8080",
        "--host=0.0.0.0",
        f"--model={_rhoai_basic_model_path(plan)}",
        f"--served-model-name={plan.model.name}",
        f"--tensor-parallel-size={plan.deployment.runtime.tensor_parallelism}",
    ] + plan.deployment.runtime.vllm_args


def _rhoai_precise_tokenizer_model_path(plan: ResolvedRunPlan) -> str:
    return f"/mnt/models/base{_model_path(plan)}"


def _rhoai_custom_epp_config_lines(
    plan: ResolvedRunPlan, context: dict[str, Any]
) -> list[str]:
    raw_config = plan.deployment.options.get("epp_config")
    if raw_config is None or str(raw_config).strip() == "":
        return []
    if not isinstance(raw_config, str):
        raise ValidationError(
            "deployment profile options.epp_config must be a YAML string"
        )

    rendered = render_jinja_text(raw_config, context).strip()
    parsed = yaml.safe_load(rendered)
    if not isinstance(parsed, dict):
        raise ValidationError(
            "deployment profile options.epp_config must render to a YAML mapping"
        )
    if parsed.get("kind") != "EndpointPickerConfig":
        raise ValidationError(
            "deployment profile options.epp_config must render an EndpointPickerConfig"
        )
    return rendered.splitlines()


def _rhoai_epp_verbosity(plan: ResolvedRunPlan) -> int | None:
    raw_value = plan.deployment.options.get("epp_verbosity")
    if raw_value is None or str(raw_value).strip() == "":
        return None
    if isinstance(raw_value, bool):
        raise ValidationError(
            "deployment profile options.epp_verbosity must be an integer"
        )
    try:
        verbosity = int(str(raw_value).strip())
    except ValueError as exc:
        raise ValidationError(
            "deployment profile options.epp_verbosity must be an integer"
        ) from exc
    if verbosity < 0:
        raise ValidationError(
            "deployment profile options.epp_verbosity must be greater than or "
            "equal to 0"
        )
    return verbosity


def _rhoai_startup_probe(plan: ResolvedRunPlan) -> dict[str, Any] | None:
    default_probe = {
        "httpGet": {
            "path": "/health",
            "port": 8000,
            "scheme": "HTTPS",
        },
        "failureThreshold": 120,
        "periodSeconds": 10,
        "timeoutSeconds": 1,
    }
    raw_probe = plan.deployment.options.get("startup_probe")
    if raw_probe is None:
        return default_probe
    if raw_probe is False:
        return None
    if isinstance(raw_probe, str):
        raw_probe = yaml.safe_load(raw_probe)
    if not isinstance(raw_probe, dict):
        raise ValidationError(
            "deployment profile options.startup_probe must be a mapping"
        )
    return _deep_merge(default_probe, raw_probe)


def _yaml_lines(value: dict[str, Any] | None) -> list[str]:
    if value is None:
        return []
    return yaml.safe_dump(value, sort_keys=False).rstrip().splitlines()


def _rhoai_validate_isvc(plan: ResolvedRunPlan) -> None:
    if not _rhoai_uses_isvc(plan):
        return
    if not plan.deployment.runtime.image:
        raise ValidationError("rhoai isvc deployments require deployment.runtime.image")
    if plan.deployment.scheduler_image:
        raise ValidationError("rhoai isvc deployments do not support scheduler_image")
    if str(plan.deployment.options.get("epp_config") or "").strip():
        raise ValidationError(
            "rhoai isvc deployments do not support options.epp_config"
        )
    if _rhoai_epp_verbosity(plan) is not None:
        raise ValidationError(
            "rhoai isvc deployments do not support options.epp_verbosity"
        )


def _rhoai_llminferenceservice_template_context(
    plan: ResolvedRunPlan,
) -> dict[str, Any]:
    _validate_rhoai_profiling(plan)
    has_custom_epp_config = bool(
        str(plan.deployment.options.get("epp_config") or "").strip()
    )
    epp_verbosity = _rhoai_epp_verbosity(plan)
    custom_scheduler_enabled = (
        plan.deployment.mode
        in {
            "approximate-prefix-cache",
            "precise-prefix-cache",
        }
        or has_custom_epp_config
        or epp_verbosity is not None
    )
    scheduler_config_enabled = (
        plan.deployment.mode
        in {
            "approximate-prefix-cache",
            "precise-prefix-cache",
        }
        or has_custom_epp_config
    )
    context: dict[str, Any] = {
        "release_name": plan.deployment.release_name,
        "namespace": plan.deployment.namespace,
        "rhoai_gateway_ref": rhoai_release_gateway_reference(plan),
        "labels": _base_labels(plan),
        "enable_auth": str(plan.deployment.options.get("enable_auth", False)).lower(),
        "model_name": plan.model.name,
        "model_uri": f"pvc://{plan.deployment.model_storage.pvc_name}",
        "replicas": plan.deployment.runtime.replicas,
        "runtime_image": plan.deployment.runtime.image,
        "scheduler_image": plan.deployment.scheduler_image,
        "runtime_args": _rhoai_vllm_args(plan),
        "runtime_env": _rhoai_runtime_env(plan),
        "runtime_node_selector": plan.deployment.runtime.node_selector,
        "runtime_affinity": _runtime_affinity(plan),
        "runtime_tolerations": plan.deployment.runtime.tolerations,
        "runtime_image_pull_secrets": plan.deployment.runtime.image_pull_secrets,
        "runtime_resources": _runtime_resource_requirements(plan, include_gpu=True),
        "runtime_service_account_name": plan.deployment.runtime.service_account_name,
        "runtime_shared_memory_size": plan.deployment.runtime.shared_memory_size,
        "runtime_host_path_mounts": _runtime_host_path_volume_mounts(plan),
        "runtime_host_path_volumes": _runtime_host_path_volumes(plan),
        "runtime_pvc_mounts": _runtime_pvc_volume_mounts(plan),
        "runtime_pvc_volumes": _runtime_pvc_volumes(plan),
        "mooncake_enabled": rhoai_mooncake_spec(plan) is not None,
        "mooncake_configmap_name": mooncake_configmap_name(plan),
        "mooncake_model_volume_mount": rhoai_mooncake_model_volume_mount(plan),
        "mooncake_model_volume": rhoai_mooncake_model_volume(plan),
        "mooncake_store_sidecar": rhoai_mooncake_store_sidecar(plan),
        "startup_probe_lines": _yaml_lines(_rhoai_startup_probe(plan)),
        "gpu_count": str(plan.deployment.runtime.tensor_parallelism),
        "custom_scheduler_enabled": custom_scheduler_enabled,
        "scheduler_config_enabled": scheduler_config_enabled,
        "epp_verbosity": epp_verbosity,
        "approximate_prefix_cache_enabled": (
            plan.deployment.mode == "approximate-prefix-cache"
        ),
        "precise_prefix_cache_enabled": plan.deployment.mode == "precise-prefix-cache",
        "precise_prefix_cache_tokenizer_model_path": (
            _rhoai_precise_tokenizer_model_path(plan)
        ),
        "profiling_enabled": plan.execution.profiling.enabled,
        "profiler_call_ranges": plan.execution.profiling.call_ranges,
        "profiler_idle_seconds": plan.execution.profiling.idle_seconds,
        "profiler_configmap_name": rhoai_profiler_configmap_name(plan),
        "profiler_mount_path": RHOAI_PROFILER_MOUNT_PATH,
    }
    context["custom_epp_config_lines"] = _rhoai_custom_epp_config_lines(plan, context)
    return context


def _rhoai_inferenceservice_template_context(plan: ResolvedRunPlan) -> dict[str, Any]:
    _validate_rhoai_profiling(plan)
    _rhoai_validate_isvc(plan)
    return {
        "release_name": plan.deployment.release_name,
        "namespace": plan.deployment.namespace,
        "labels": _base_labels(plan),
        "enable_auth": str(plan.deployment.options.get("enable_auth", False)).lower(),
        "replicas": plan.deployment.runtime.replicas,
        "runtime_image": plan.deployment.runtime.image,
        "runtime_args": _rhoai_basic_vllm_args(plan),
        "runtime_env": _rhoai_basic_runtime_env(plan),
        "runtime_node_selector": plan.deployment.runtime.node_selector,
        "runtime_affinity": _runtime_affinity(plan),
        "runtime_tolerations": plan.deployment.runtime.tolerations,
        "runtime_image_pull_secrets": plan.deployment.runtime.image_pull_secrets,
        "runtime_resources": _runtime_resource_requirements(plan, include_gpu=True),
        "runtime_service_account_name": plan.deployment.runtime.service_account_name,
        "runtime_shared_memory_size": plan.deployment.runtime.shared_memory_size,
        "runtime_host_path_mounts": _runtime_host_path_volume_mounts(plan),
        "runtime_host_path_volumes": _runtime_host_path_volumes(plan),
        "runtime_pvc_mounts": _runtime_pvc_volume_mounts(plan),
        "runtime_pvc_volumes": _runtime_pvc_volumes(plan),
        "model_storage_pvc_name": plan.deployment.model_storage.pvc_name,
        "model_storage_mount_path": plan.deployment.model_storage.mount_path,
        "profiling_enabled": plan.execution.profiling.enabled,
        "profiler_call_ranges": plan.execution.profiling.call_ranges,
        "profiler_idle_seconds": plan.execution.profiling.idle_seconds,
        "profiler_configmap_name": rhoai_profiler_configmap_name(plan),
        "profiler_mount_path": RHOAI_PROFILER_MOUNT_PATH,
    }


def render_rhoai_manifest(plan: ResolvedRunPlan) -> dict[str, Any]:
    if _rhoai_uses_isvc(plan):
        return render_jinja_yaml_document(
            "deployment/rhoai/inferenceservice.yaml.j2",
            _rhoai_inferenceservice_template_context(plan),
        )
    if plan.deployment.mode not in {
        "distributed-default",
        "approximate-prefix-cache",
        "precise-prefix-cache",
    }:
        raise ValueError(f"unsupported RHOAI deployment mode: {plan.deployment.mode}")
    return render_jinja_yaml_document(
        "deployment/rhoai/llminferenceservice.yaml.j2",
        _rhoai_llminferenceservice_template_context(plan),
    )


def rhoai_profiler_configmap_name(plan: ResolvedRunPlan) -> str:
    return f"{plan.deployment.release_name}-{RHOAI_PROFILER_CONFIGMAP_SUFFIX}"


def render_rhoai_profiler_configmap(plan: ResolvedRunPlan) -> dict[str, Any]:
    _validate_rhoai_profiling(plan)
    return {
        "apiVersion": "v1",
        "kind": "ConfigMap",
        "metadata": {
            "name": rhoai_profiler_configmap_name(plan),
            "namespace": plan.deployment.namespace,
            "labels": {
                **_base_labels(plan),
                "app.kubernetes.io/component": "vllm-profiler",
            },
        },
        "data": {
            "sitecustomize.py": asset_text("deployment/rhoai/profiler/sitecustomize.py")
        },
    }


def rhaiis_raw_vllm_deployment_name(plan: ResolvedRunPlan) -> str:
    return f"{plan.deployment.release_name}-vllm"


def rhaiis_raw_vllm_is_distributed(plan: ResolvedRunPlan) -> bool:
    distributed = plan.deployment.options.get("distributed") or {}
    return isinstance(distributed, dict) and distributed.get("enabled") is True


def rhaiis_raw_vllm_workload_kind(plan: ResolvedRunPlan) -> str:
    return "statefulset" if rhaiis_raw_vllm_is_distributed(plan) else "deployment"


def rhaiis_raw_vllm_headless_service_name(plan: ResolvedRunPlan) -> str:
    return f"{rhaiis_raw_vllm_deployment_name(plan)}-headless"


def rhaiis_raw_vllm_service_name(plan: ResolvedRunPlan) -> str:
    return plan.deployment.release_name


def rhaiis_raw_vllm_servicemonitor_name(plan: ResolvedRunPlan) -> str:
    return f"{plan.deployment.release_name}-vllm"


def _rhaiis_raw_vllm_labels(plan: ResolvedRunPlan) -> dict[str, str]:
    return {
        **_base_labels(plan),
        "app.kubernetes.io/component": "raw-vllm",
        "app.kubernetes.io/instance": plan.deployment.release_name,
        "benchflow.io/release": plan.deployment.release_name,
    }


def _rhaiis_raw_vllm_selector_labels(plan: ResolvedRunPlan) -> dict[str, str]:
    return {
        "app.kubernetes.io/component": "raw-vllm",
        "app.kubernetes.io/instance": plan.deployment.release_name,
        "benchflow.io/release": plan.deployment.release_name,
    }


def _rhaiis_raw_vllm_model_path(plan: ResolvedRunPlan) -> str:
    explicit = str(plan.deployment.options.get("model_path") or "").strip()
    if explicit:
        return explicit
    mount_root = plan.deployment.model_storage.mount_path.rstrip("/")
    return f"{mount_root}/{model_storage_relative_path(plan.deployment.model_storage, plan.model)}"


def _rhaiis_raw_vllm_uses_model_pvc(plan: ResolvedRunPlan) -> bool:
    return not str(plan.deployment.options.get("model_path") or "").strip()


def _rhaiis_raw_vllm_runtime_env(
    plan: ResolvedRunPlan, *, home: str = "/tmp/vllm-home"
) -> list[dict[str, Any]]:
    if _rhaiis_raw_vllm_uses_model_pvc(plan):
        mount_root = plan.deployment.model_storage.mount_path.rstrip("/")
        cache_dir = f"{mount_root}{plan.deployment.model_storage.cache_dir.rstrip('/')}"
    else:
        model_path = Path(_rhaiis_raw_vllm_model_path(plan))
        containing_mounts = [
            Path(item.mount_path)
            for item in plan.deployment.runtime.host_paths
            if model_path == Path(item.mount_path)
            or model_path.is_relative_to(Path(item.mount_path))
        ]
        cache_root = max(
            containing_mounts,
            key=lambda item: len(item.parts),
            default=model_path.parent,
        )
        cache_dir = str(cache_root / "hf")
    env = {
        "HOME": home,
        "HF_HOME": cache_dir,
        "TRANSFORMERS_CACHE": f"{cache_dir}/hub",
        "HF_HUB_CACHE": f"{cache_dir}/hub",
        **plan.deployment.runtime.env,
    }
    return [{"name": key, "value": value} for key, value in sorted(env.items())]


def _rhaiis_raw_vllm_storage(
    plan: ResolvedRunPlan,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    mounts: list[dict[str, Any]] = []
    volumes: list[dict[str, Any]] = []
    if _rhaiis_raw_vllm_uses_model_pvc(plan):
        mounts.append(
            {
                "name": "model-storage",
                "mountPath": plan.deployment.model_storage.mount_path,
            }
        )
        volumes.append(
            {
                "name": "model-storage",
                "persistentVolumeClaim": {
                    "claimName": plan.deployment.model_storage.pvc_name
                },
            }
        )
    if plan.deployment.runtime.shared_memory_size:
        mounts.append({"name": "dshm", "mountPath": "/dev/shm"})
        volumes.append(
            {
                "name": "dshm",
                "emptyDir": {
                    "medium": "Memory",
                    "sizeLimit": plan.deployment.runtime.shared_memory_size,
                },
            }
        )
    mounts.extend(_runtime_host_path_volume_mounts(plan))
    mounts.extend(_runtime_pvc_volume_mounts(plan))
    volumes.extend(_runtime_host_path_volumes(plan))
    volumes.extend(_runtime_pvc_volumes(plan))
    return mounts, volumes


def _rhaiis_raw_vllm_container(plan: ResolvedRunPlan) -> dict[str, Any]:
    volume_mounts, _ = _rhaiis_raw_vllm_storage(plan)
    return {
        "name": "vllm",
        "image": plan.deployment.runtime.image,
        "command": ["python3", "-m", "vllm.entrypoints.openai.api_server"],
        "args": [
            f"--model={_rhaiis_raw_vllm_model_path(plan)}",
            f"--served-model-name={plan.model.name}",
            f"--tensor-parallel-size={plan.deployment.runtime.tensor_parallelism}",
            "--port=8000",
            "--host=0.0.0.0",
            *plan.deployment.runtime.vllm_args,
        ],
        "env": _rhaiis_raw_vllm_runtime_env(plan),
        "ports": [{"containerPort": 8000, "name": "http", "protocol": "TCP"}],
        "readinessProbe": {
            "httpGet": {"path": "/health", "port": "http"},
            "periodSeconds": 10,
            "timeoutSeconds": 5,
            "failureThreshold": 3,
        },
        "resources": _runtime_resource_requirements(plan, include_gpu=True),
        "volumeMounts": volume_mounts,
    }


def _rhaiis_vllm_serve_argv(command: list[Any], args: list[Any]) -> list[str]:
    """Rewrite api_server ``--model=`` argv into ``vllm serve <model> …``.

    The kimi-k3 / InferenceX multi-node path uses ``vllm serve`` (serve.py), which
    sets local MQ bind addresses correctly under hostNetwork. Launching via
    ``python -m vllm.entrypoints.openai.api_server`` makes workers try to bind
    ZMQ to ``--master-addr`` and fail with ``Cannot assign requested address``.
    """
    model: str | None = None
    rest: list[str] = []
    for arg in args:
        text = str(arg)
        if text.startswith("--model="):
            model = text.split("=", 1)[1]
        else:
            rest.append(text)
    if model:
        return ["vllm", "serve", model, *rest]
    if command and str(command[0]) == "vllm":
        return [str(item) for item in (*command, *args)]
    raise ValidationError(
        "rhaiis distributed raw-vllm requires --model=… in container args "
        "(or an existing vllm serve command)"
    )


def _rhaiis_needs_humming_situ_patch(vllm_args: list[Any]) -> bool:
    """True when serve argv selects humming MoE (needs SITU allowlist)."""
    return any(
        "moe-backend=humming" in str(arg) or str(arg) == "humming"
        for arg in vllm_args
    )


def _rhaiis_humming_situ_preamble() -> str:
    """In-pod patch for MoEActivation.SITU (vLLM PR #50510 allowlist only).

    The kimi-k3 / InferenceX ``vllm/vllm-openai:kimi-k3`` image ships humming
    without SITU on the fused_humming_moe allowlist; Kimi-K3 activation fails
    unless this line is present before ``vllm serve``.
    """
    return (
        'echo "applying humming MoEActivation.SITU allowlist patch" >&2\n'
        "python3 - <<'PY'\n"
        "from pathlib import Path\n"
        "path = Path(\n"
        '    "/usr/local/lib/python3.12/dist-packages/vllm/model_executor/'
        'layers/fused_moe/experts/fused_humming_moe.py"\n'
        ")\n"
        "if not path.is_file():\n"
        '    raise SystemExit(f"humming experts file not found: {path}")\n'
        "text = path.read_text()\n"
        'if "MoEActivation.SITU" in text:\n'
        '    print("already patched (SITU present)")\n'
        "else:\n"
        '    needle = "            MoEActivation.SWIGLUOAI,\\n"\n'
        "    insert = needle + "
        '"            MoEActivation.SITU,\\n"\n'
        "    if needle not in text:\n"
        '        raise SystemExit("SWIGLUOAI allowlist line not found")\n'
        "    bak = path.with_suffix(path.suffix + '.bak-pre-situ')\n"
        "    if not bak.exists():\n"
        "        bak.write_text(text)\n"
        "    path.write_text(text.replace(needle, insert, 1))\n"
        '    print("inserted MoEActivation.SITU")\n'
        "cache = path.parent / '__pycache__'\n"
        "if cache.is_dir():\n"
        "    for pyc in cache.glob('fused_humming_moe*.pyc'):\n"
        "        pyc.unlink(missing_ok=True)\n"
        "PY\n"
    )


def _rhaiis_mamba_hybrid_preamble() -> str:
    """In-pod patch for int32 idx_mapping fill (vLLM PR #50327).

    Model Runner V2 ``idx_mapping`` is int32 (PP may use -1 sentinels). The
    scalar branch of ``MambaHybridModelState.postprocess_state`` used
    ``index_fill_``, which requires int64 and does not skip sentinels — see
    issue #50947. kimi-k3's recipe applies the same Triton ``_fill_num_accepted_kernel``
    replacement at serve start when the image predates the merge.
    """
    # Keep needle/patch text identical to scripts/run-vllm-kimi-k3-recipe.sh.
    return r'''echo "applying mamba_hybrid PR #50327 int32 idx_mapping patch" >&2
python3 - <<'PY'
from pathlib import Path

path = Path(
    "/usr/local/lib/python3.12/dist-packages/vllm/v1/worker/gpu/model_states/mamba_hybrid.py"
)
if not path.is_file():
    print(f"mamba_hybrid.py not found ({path}); skipping")
    raise SystemExit(0)
src = path.read_text()
if "_fill_num_accepted_kernel" in src:
    print("already patched (_fill_num_accepted_kernel present)")
    raise SystemExit(0)
needle = """        # Chunked prefill does not sample a token, so num_sampled can be 0.
        # Mamba treats num_accepted_tokens=1 as the neutral non-spec value.
        if not isinstance(num_sampled, int):
            # idx_mapping may contain -1 sentinels (filtered rows) under PP; the
            # kernel skips them rather than scattering with a host-side gather.
            n = idx_mapping.shape[0]
            if n:
                _scatter_num_accepted_kernel[(n,)](
                    idx_mapping, num_sampled, self.num_accepted_tokens_gpu
                )
        else:
            # Fill with single value.
            self.num_accepted_tokens_gpu.index_fill_(
                0, idx_mapping, max(num_sampled, 1)
            )

        # Align: save the running state to the block-aligned position when
        # spec-decode acceptance leaves the sequence non-block-aligned (mirrors
        # the V1 align postprocess). num_computed_tokens already holds the
        # post-step advanced count.
        if (
            self._align_mode
            and num_computed_tokens is not None
            and self._mamba_ctx is not None
        ):
            num_reqs = idx_mapping.shape[0]
            if num_reqs:
                self._mamba_ctx.run_fused_postprocess_align(
                    num_reqs,
                    self.num_accepted_tokens_gpu,
                    self._mamba_state_idx_gpu,
                    num_computed_tokens,
                    idx_mapping,
                )


@triton.jit
def _scatter_num_accepted_kernel(
    idx_mapping_ptr,  # [num_reqs] batch_idx -> req_state_idx (-1 to skip)
    num_sampled_ptr,  # [num_reqs]
    num_accepted_ptr,  # [max_num_reqs]
):
    row = tl.program_id(0)
    req_state_idx = tl.load(idx_mapping_ptr + row)
    if req_state_idx < 0:
        return
    num_sampled = tl.load(num_sampled_ptr + row)
    tl.store(num_accepted_ptr + req_state_idx, tl.maximum(num_sampled, 1))
"""
patch = """        # Chunked prefill does not sample a token, so num_sampled can be 0.
        # Mamba treats num_accepted_tokens=1 as the neutral non-spec value.
        # Kimi-K3 harness: PR #50327 — int32 idx_mapping + PP -1 sentinels cannot
        # use index_fill_ (needs int64; negatives corrupt state). Use Triton fill.
        num_reqs = idx_mapping.shape[0]
        if not num_reqs:
            return

        if not isinstance(num_sampled, int):
            # idx_mapping may contain -1 sentinels (filtered rows) under PP; the
            # kernel skips them rather than scattering with a host-side gather.
            _scatter_num_accepted_kernel[(num_reqs,)](
                idx_mapping, num_sampled, self.num_accepted_tokens_gpu
            )
        else:
            # Fill with single value.
            _fill_num_accepted_kernel[(num_reqs,)](
                idx_mapping, self.num_accepted_tokens_gpu, max(num_sampled, 1)
            )

        # Align: save the running state to the block-aligned position when
        # spec-decode acceptance leaves the sequence non-block-aligned (mirrors
        # the V1 align postprocess). num_computed_tokens already holds the
        # post-step advanced count.
        if (
            self._align_mode
            and num_computed_tokens is not None
            and self._mamba_ctx is not None
        ):
            self._mamba_ctx.run_fused_postprocess_align(
                num_reqs,
                self.num_accepted_tokens_gpu,
                self._mamba_state_idx_gpu,
                num_computed_tokens,
                idx_mapping,
            )


@triton.jit
def _scatter_num_accepted_kernel(
    idx_mapping_ptr,  # [num_reqs] batch_idx -> req_state_idx (-1 to skip)
    num_sampled_ptr,  # [num_reqs]
    num_accepted_ptr,  # [max_num_reqs]
):
    row = tl.program_id(0)
    req_state_idx = tl.load(idx_mapping_ptr + row)
    if req_state_idx < 0:
        return
    num_sampled = tl.load(num_sampled_ptr + row)
    tl.store(num_accepted_ptr + req_state_idx, tl.maximum(num_sampled, 1))


@triton.jit
def _fill_num_accepted_kernel(
    idx_mapping_ptr,  # [num_reqs] batch_idx -> req_state_idx (-1 to skip)
    num_accepted_ptr,  # [max_num_reqs]
    num_sampled,
):
    row = tl.program_id(0)
    req_state_idx = tl.load(idx_mapping_ptr + row)
    if req_state_idx < 0:
        return
    tl.store(num_accepted_ptr + req_state_idx, num_sampled)
"""
if needle not in src:
    raise SystemExit(f"mamba_hybrid PR#50327 patch needle missing in {path}")
bak = path.with_suffix(path.suffix + ".bak-pre-50327")
if not bak.exists():
    bak.write_text(src)
path.write_text(src.replace(needle, patch, 1))
cache = path.parent / "__pycache__"
if cache.is_dir():
    for pyc in cache.glob("mamba_hybrid*.pyc"):
        pyc.unlink(missing_ok=True)
print(f"patched {path} (PR #50327 int32 idx_mapping fill)")
PY
'''


def _rhaiis_host_network_socket_iface_preamble() -> str:
    return (
        'if [ -z "${GLOO_SOCKET_IFNAME:-}" ] || '
        '[ -z "${NCCL_SOCKET_IFNAME:-}" ]; then\n'
        '  _iface=""\n'
        '  for _cand in $(ls /sys/class/net 2>/dev/null '
        "| grep -E '^enp' | sort); do _iface=\"$_cand\"; break; done\n"
        '  if [ -z "${_iface}" ]; then\n'
        '    for _cand in $(ls /sys/class/net 2>/dev/null '
        "| grep -E '^(eth|bond)'); do _iface=\"$_cand\"; break; done\n"
        "  fi\n"
        '  if [ -z "${_iface}" ]; then\n'
        '    for _cand in $(ls /sys/class/net 2>/dev/null '
        "| grep -vE '^(lo|docker|cni|flannel|veth|cali|tunl|lxc|"
        "cilium|ibs|ib|mlx)'); do _iface=\"$_cand\"; break; done\n"
        "  fi\n"
        '  _iface="${_iface:-eth0}"\n'
        '  export GLOO_SOCKET_IFNAME="${GLOO_SOCKET_IFNAME:-${_iface}}"\n'
        '  export NCCL_SOCKET_IFNAME="${NCCL_SOCKET_IFNAME:-${_iface}}"\n'
        '  echo "hostNetwork socket iface '
        'GLOO=${GLOO_SOCKET_IFNAME} NCCL=${NCCL_SOCKET_IFNAME}" >&2\n'
        "fi\n"
    )


def _rhaiis_raw_vllm_pod_spec(
    plan: ResolvedRunPlan, container_spec: dict[str, Any]
) -> dict[str, Any]:
    _, volumes = _rhaiis_raw_vllm_storage(plan)
    pod_spec: dict[str, Any] = {
        "containers": [container_spec],
        "volumes": volumes,
    }
    runtime = plan.deployment.runtime
    if runtime.node_selector:
        pod_spec["nodeSelector"] = dict(runtime.node_selector)
    if runtime.affinity:
        pod_spec["affinity"] = deepcopy(runtime.affinity)
    if runtime.tolerations:
        pod_spec["tolerations"] = list(runtime.tolerations)
    if runtime.service_account_name:
        pod_spec["serviceAccountName"] = runtime.service_account_name
    if runtime.image_pull_secrets:
        pod_spec["imagePullSecrets"] = list(runtime.image_pull_secrets)
    return pod_spec


def _render_rhaiis_single_raw_vllm_manifests(
    plan: ResolvedRunPlan,
) -> list[dict[str, Any]]:
    labels = _rhaiis_raw_vllm_labels(plan)
    selector_labels = _rhaiis_raw_vllm_selector_labels(plan)
    container_spec = _rhaiis_raw_vllm_container(plan)
    pod_spec = _rhaiis_raw_vllm_pod_spec(plan, container_spec)
    deployment = {
        "apiVersion": "apps/v1",
        "kind": "Deployment",
        "metadata": {
            "name": rhaiis_raw_vllm_deployment_name(plan),
            "namespace": plan.deployment.namespace,
            "labels": labels,
        },
        "spec": {
            "progressDeadlineSeconds": RAHIIS_PROGRESS_DEADLINE_SECONDS,
            "replicas": plan.deployment.runtime.replicas,
            "selector": {"matchLabels": selector_labels},
            "template": {
                "metadata": {"labels": {**labels, **selector_labels}},
                "spec": pod_spec,
            },
        },
    }
    service = {
        "apiVersion": "v1",
        "kind": "Service",
        "metadata": {
            "name": rhaiis_raw_vllm_service_name(plan),
            "namespace": plan.deployment.namespace,
            "labels": labels,
        },
        "spec": {
            "type": "ClusterIP",
            "selector": selector_labels,
            "ports": [
                {
                    "name": "http",
                    "port": 8000,
                    "protocol": "TCP",
                    "targetPort": "http",
                }
            ],
        },
    }
    servicemonitor = {
        "apiVersion": "monitoring.coreos.com/v1",
        "kind": "ServiceMonitor",
        "metadata": {
            "name": rhaiis_raw_vllm_servicemonitor_name(plan),
            "namespace": plan.deployment.namespace,
            "labels": labels,
        },
        "spec": {
            "selector": {"matchLabels": selector_labels},
            "namespaceSelector": {"matchNames": [plan.deployment.namespace]},
            "endpoints": [
                {
                    "path": "/metrics",
                    "port": "http",
                    "scheme": "http",
                }
            ],
        },
    }
    return [deployment, service, servicemonitor]


def _render_rhaiis_distributed_raw_vllm_manifests(
    plan: ResolvedRunPlan,
) -> list[dict[str, Any]]:
    runtime = plan.deployment.runtime
    if runtime.replicas < 2:
        raise ValidationError(
            "rhaiis distributed raw-vllm requires runtime.replicas >= 2"
        )
    model_path = Path(_rhaiis_raw_vllm_model_path(plan))
    if not model_path.is_absolute():
        raise ValidationError("rhaiis raw-vllm options.model_path must be absolute")
    if not any(
        model_path == Path(item.mount_path)
        or model_path.is_relative_to(Path(item.mount_path))
        for item in runtime.host_paths
    ):
        raise ValidationError(
            "rhaiis distributed raw-vllm options.model_path must be inside a runtime.host_paths mount"
        )

    distributed = plan.deployment.options.get("distributed") or {}
    master_port = int(distributed.get("master_port", 29500))
    launch_style = str(distributed.get("launch_style") or "ix-agg")
    head_start_delay_seconds = int(distributed.get("head_start_delay_seconds", 45))
    workload_name = rhaiis_raw_vllm_deployment_name(plan)
    headless_name = rhaiis_raw_vllm_headless_service_name(plan)
    leader_host = f"{workload_name}-0.{headless_name}.{plan.deployment.namespace}.svc.cluster.local"
    labels = _rhaiis_raw_vllm_labels(plan)
    selector_labels = _rhaiis_raw_vllm_selector_labels(plan)
    metrics_target = {"benchflow.io/metrics-target": plan.deployment.release_name}

    container_spec = _rhaiis_raw_vllm_container(plan)
    # Under hostNetwork HOSTNAME is the node name (e.g. gf2a612), so do not
    # derive the StatefulSet ordinal from it. metadata.name stays
    # <sts>-<ordinal> either way.
    container_env = list(container_spec.get("env") or [])
    container_env.extend(
        [
            {
                "name": "POD_NAME",
                "valueFrom": {"fieldRef": {"fieldPath": "metadata.name"}},
            },
            {
                "name": "POD_IP",
                "valueFrom": {"fieldRef": {"fieldPath": "status.podIP"}},
            },
            {"name": "DP_LEADER_HOST", "value": leader_host},
            {
                "name": "HEAD_START_DELAY_SECONDS",
                "value": str(head_start_delay_seconds),
            },
        ]
    )
    container_spec["env"] = container_env

    # Under hostNetwork, Gloo/NCCL must bind the node Ethernet iface — otherwise
    # Gloo advertises 127.0.0.1 and DP mesh init fails (same as kimi-k3 recipe).
    host_network = bool(distributed.get("host_network", False))
    socket_iface_preamble = ""
    if host_network:
        socket_iface_preamble = _rhaiis_host_network_socket_iface_preamble()

    humming_situ_preamble = ""
    if _rhaiis_needs_humming_situ_patch(runtime.vllm_args):
        humming_situ_preamble = _rhaiis_humming_situ_preamble()

    # Always attempt on distributed Kimi launches: PP/DP hybrid paths hit the
    # int32 idx_mapping bug; no-op when the image already has PR #50327.
    mamba_preamble = _rhaiis_mamba_hybrid_preamble()

    launch_preamble = (
        socket_iface_preamble + humming_situ_preamble + mamba_preamble
    )

    if launch_style == "ix-agg":
        # InferenceX / kimi-k3 image: vllm serve + --nnodes/--node-rank/
        # --master-addr with --data-parallel-size (profile-owned). Rank 0 delays
        # so workers start first (deploy.sh order). Do NOT pass
        # --data-parallel-address here (IX_AGG skips it); serve.py sets local
        # mq_connect_ip while --master-addr remains the head rendezvous.
        serve_argv = _rhaiis_vllm_serve_argv(
            container_spec.pop("command"),
            container_spec.pop("args"),
        )
        base_argv = [
            *serve_argv,
            f"--nnodes={runtime.replicas}",
        ]
        launch_script = (
            launch_preamble
            + 'node_rank=${POD_NAME##*-}\n'
            'if [ -z "${POD_IP}" ]; then\n'
            '  echo "POD_IP is empty; cannot set --master-addr" >&2\n'
            "  exit 1\n"
            "fi\n"
            'if [ "${node_rank}" = "0" ]; then\n'
            '  master_addr="${POD_IP}"\n'
            '  delay="${HEAD_START_DELAY_SECONDS:-0}"\n'
            '  if [ "${delay}" -gt 0 ] 2>/dev/null; then\n'
            '    echo "rank0 waiting ${delay}s so DP workers start first" >&2\n'
            '    sleep "${delay}"\n'
            "  fi\n"
            '  exec "$@" --node-rank="${node_rank}" '
            '--master-addr="${master_addr}"\n'
            "fi\n"
            'master_addr=$(getent ahostsv4 "${DP_LEADER_HOST}" 2>/dev/null '
            "| awk '{print $1; exit}')\n"
            'if [ -z "${master_addr}" ]; then\n'
            '  master_addr=$(getent hosts "${DP_LEADER_HOST}" 2>/dev/null '
            "| awk '{print $1; exit}')\n"
            "fi\n"
            'if [ -z "${master_addr}" ]; then\n'
            '  echo "failed to resolve DP leader ${DP_LEADER_HOST}" >&2\n'
            "  exit 1\n"
            "fi\n"
            'exec "$@" --node-rank="${node_rank}" '
            '--master-addr="${master_addr}" --headless\n'
        )
        distributed_port = master_port
    else:
        # Stock vLLM 0.27+ external / one-pod-per-rank MoE DP.
        serve_argv = _rhaiis_vllm_serve_argv(
            container_spec.pop("command"),
            container_spec.pop("args"),
        )
        base_argv = [
            *serve_argv,
            f"--data-parallel-rpc-port={master_port}",
        ]
        launch_script = (
            launch_preamble
            + 'node_rank=${POD_NAME##*-}\n'
            'if [ -z "${POD_IP}" ]; then\n'
            '  echo "POD_IP is empty; cannot set --data-parallel-address" >&2\n'
            "  exit 1\n"
            "fi\n"
            'if [ "${node_rank}" = "0" ]; then\n'
            '  dp_addr="${POD_IP}"\n'
            "else\n"
            '  dp_addr=$(getent ahostsv4 "${DP_LEADER_HOST}" 2>/dev/null '
            "| awk '{print $1; exit}')\n"
            '  if [ -z "${dp_addr}" ]; then\n'
            '    dp_addr=$(getent hosts "${DP_LEADER_HOST}" 2>/dev/null '
            "| awk '{print $1; exit}')\n"
            "  fi\n"
            '  if [ -z "${dp_addr}" ]; then\n'
            '    echo "failed to resolve DP leader ${DP_LEADER_HOST}" >&2\n'
            "    exit 1\n"
            "  fi\n"
            "fi\n"
            'if [ "${node_rank}" = "0" ]; then\n'
            '  exec "$@" --data-parallel-rank="${node_rank}" '
            '--data-parallel-address="${dp_addr}"\n'
            "fi\n"
            'exec "$@" --data-parallel-rank="${node_rank}" '
            '--data-parallel-address="${dp_addr}" --headless\n'
        )
        distributed_port = master_port

    container_spec["command"] = ["/bin/sh", "-c"]
    container_spec["args"] = [
        launch_script,
        "benchflow-vllm",
        *base_argv,
    ]
    container_spec["ports"].append(
        {
            "containerPort": distributed_port,
            "name": "distributed",
            "protocol": "TCP",
        }
    )
    # Kimi weight load is many minutes; keep workers "ready" via process liveness
    # and give rank0 a long HTTP readiness budget (matches IX health polls).
    container_spec["readinessProbe"] = {
        "exec": {
            "command": [
                "/bin/sh",
                "-c",
                (
                    'node_rank=${POD_NAME##*-}; '
                    'if [ "${node_rank}" != "0" ]; then kill -0 1; else '
                    'python3 -c "import urllib.request; '
                    "urllib.request.urlopen('http://127.0.0.1:8000/health', "
                    'timeout=3)"; fi'
                ),
            ]
        },
        "initialDelaySeconds": 30,
        "periodSeconds": 10,
        "timeoutSeconds": 5,
        "failureThreshold": 720,
    }
    pod_spec = _rhaiis_raw_vllm_pod_spec(plan, container_spec)
    pod_spec["hostNetwork"] = bool(distributed.get("host_network", False))
    pod_spec["hostIPC"] = bool(distributed.get("host_ipc", False))
    if pod_spec["hostNetwork"]:
        pod_spec["dnsPolicy"] = "ClusterFirstWithHostNet"

    affinity = pod_spec.setdefault("affinity", {})
    required_anti_affinity = affinity.setdefault("podAntiAffinity", {}).setdefault(
        "requiredDuringSchedulingIgnoredDuringExecution", []
    )
    required_anti_affinity.append(
        {
            "labelSelector": {"matchLabels": selector_labels},
            "topologyKey": "kubernetes.io/hostname",
        }
    )

    statefulset = {
        "apiVersion": "apps/v1",
        "kind": "StatefulSet",
        "metadata": {
            "name": workload_name,
            "namespace": plan.deployment.namespace,
            "labels": labels,
        },
        "spec": {
            "serviceName": headless_name,
            "podManagementPolicy": "Parallel",
            "replicas": runtime.replicas,
            "selector": {"matchLabels": selector_labels},
            "template": {
                "metadata": {"labels": {**labels, **selector_labels}},
                "spec": pod_spec,
            },
        },
    }
    headless_service = {
        "apiVersion": "v1",
        "kind": "Service",
        "metadata": {
            "name": headless_name,
            "namespace": plan.deployment.namespace,
            "labels": {**labels, **metrics_target},
        },
        "spec": {
            "clusterIP": "None",
            "publishNotReadyAddresses": True,
            "selector": selector_labels,
            "ports": [
                {
                    "name": "http",
                    "port": 8000,
                    "protocol": "TCP",
                    "targetPort": "http",
                },
                {
                    "name": "distributed",
                    "port": distributed_port,
                    "protocol": "TCP",
                    "targetPort": "distributed",
                },
            ],
        },
    }
    api_service = {
        "apiVersion": "v1",
        "kind": "Service",
        "metadata": {
            "name": rhaiis_raw_vllm_service_name(plan),
            "namespace": plan.deployment.namespace,
            "labels": labels,
        },
        "spec": {
            "type": "ExternalName",
            "externalName": leader_host,
            "ports": [
                {
                    "name": "http",
                    "port": 8000,
                    "protocol": "TCP",
                    "targetPort": "http",
                }
            ],
        },
    }
    servicemonitor = {
        "apiVersion": "monitoring.coreos.com/v1",
        "kind": "ServiceMonitor",
        "metadata": {
            "name": rhaiis_raw_vllm_servicemonitor_name(plan),
            "namespace": plan.deployment.namespace,
            "labels": labels,
        },
        "spec": {
            "selector": {"matchLabels": metrics_target},
            "namespaceSelector": {"matchNames": [plan.deployment.namespace]},
            "endpoints": [
                {
                    "path": "/metrics",
                    "port": "http",
                    "scheme": "http",
                    "relabelings": [
                        {
                            "sourceLabels": ["__meta_kubernetes_pod_name"],
                            "regex": f"{workload_name}-0",
                            "action": "keep",
                        }
                    ],
                }
            ],
        },
    }
    return [statefulset, headless_service, api_service, servicemonitor]


def render_rhaiis_raw_vllm_manifests(plan: ResolvedRunPlan) -> list[dict[str, Any]]:
    if not plan.deployment.runtime.image:
        raise ValidationError(
            "rhaiis raw-vllm deployments require deployment.runtime.image"
        )
    if rhaiis_raw_vllm_is_distributed(plan):
        return _render_rhaiis_distributed_raw_vllm_manifests(plan)
    return _render_rhaiis_single_raw_vllm_manifests(plan)


RHAIIS_RAW_SGLANG_HTTP_PORT = 30000


def rhaiis_raw_sglang_deployment_name(plan: ResolvedRunPlan) -> str:
    return f"{plan.deployment.release_name}-sglang"


def rhaiis_raw_sglang_is_distributed(plan: ResolvedRunPlan) -> bool:
    return rhaiis_raw_vllm_is_distributed(plan)


def rhaiis_raw_sglang_workload_kind(plan: ResolvedRunPlan) -> str:
    return "statefulset" if rhaiis_raw_sglang_is_distributed(plan) else "deployment"


def rhaiis_raw_sglang_headless_service_name(plan: ResolvedRunPlan) -> str:
    return f"{rhaiis_raw_sglang_deployment_name(plan)}-headless"


def rhaiis_raw_sglang_service_name(plan: ResolvedRunPlan) -> str:
    return plan.deployment.release_name


def rhaiis_raw_sglang_servicemonitor_name(plan: ResolvedRunPlan) -> str:
    return f"{plan.deployment.release_name}-sglang"


def _rhaiis_raw_sglang_labels(plan: ResolvedRunPlan) -> dict[str, str]:
    return {
        **_base_labels(plan),
        "app.kubernetes.io/component": "raw-sglang",
        "app.kubernetes.io/instance": plan.deployment.release_name,
        "benchflow.io/release": plan.deployment.release_name,
    }


def _rhaiis_raw_sglang_selector_labels(plan: ResolvedRunPlan) -> dict[str, str]:
    return {
        "app.kubernetes.io/component": "raw-sglang",
        "app.kubernetes.io/instance": plan.deployment.release_name,
        "benchflow.io/release": plan.deployment.release_name,
    }


def _rhaiis_raw_sglang_container(plan: ResolvedRunPlan) -> dict[str, Any]:
    volume_mounts, _ = _rhaiis_raw_vllm_storage(plan)
    return {
        "name": "sglang",
        "image": plan.deployment.runtime.image,
        "command": ["sglang", "serve"],
        "args": [
            "--trust-remote-code",
            f"--model-path={_rhaiis_raw_vllm_model_path(plan)}",
            f"--tp-size={plan.deployment.runtime.tensor_parallelism}",
            "--host=0.0.0.0",
            f"--port={RHAIIS_RAW_SGLANG_HTTP_PORT}",
            *plan.deployment.runtime.sglang_args,
        ],
        "env": _rhaiis_raw_vllm_runtime_env(plan, home="/tmp/sglang-home"),
        "ports": [
            {
                "containerPort": RHAIIS_RAW_SGLANG_HTTP_PORT,
                "name": "http",
                "protocol": "TCP",
            }
        ],
        "readinessProbe": {
            "httpGet": {"path": "/health", "port": "http"},
            "periodSeconds": 10,
            "timeoutSeconds": 5,
            "failureThreshold": 3,
        },
        "resources": _runtime_resource_requirements(plan, include_gpu=True),
        "volumeMounts": volume_mounts,
    }


def _render_rhaiis_single_raw_sglang_manifests(
    plan: ResolvedRunPlan,
) -> list[dict[str, Any]]:
    labels = _rhaiis_raw_sglang_labels(plan)
    selector_labels = _rhaiis_raw_sglang_selector_labels(plan)
    container_spec = _rhaiis_raw_sglang_container(plan)
    pod_spec = _rhaiis_raw_vllm_pod_spec(plan, container_spec)
    deployment = {
        "apiVersion": "apps/v1",
        "kind": "Deployment",
        "metadata": {
            "name": rhaiis_raw_sglang_deployment_name(plan),
            "namespace": plan.deployment.namespace,
            "labels": labels,
        },
        "spec": {
            "progressDeadlineSeconds": RAHIIS_PROGRESS_DEADLINE_SECONDS,
            "replicas": plan.deployment.runtime.replicas,
            "selector": {"matchLabels": selector_labels},
            "template": {
                "metadata": {"labels": {**labels, **selector_labels}},
                "spec": pod_spec,
            },
        },
    }
    service = {
        "apiVersion": "v1",
        "kind": "Service",
        "metadata": {
            "name": rhaiis_raw_sglang_service_name(plan),
            "namespace": plan.deployment.namespace,
            "labels": labels,
        },
        "spec": {
            "type": "ClusterIP",
            "selector": selector_labels,
            "ports": [
                {
                    "name": "http",
                    "port": RHAIIS_RAW_SGLANG_HTTP_PORT,
                    "protocol": "TCP",
                    "targetPort": "http",
                }
            ],
        },
    }
    servicemonitor = {
        "apiVersion": "monitoring.coreos.com/v1",
        "kind": "ServiceMonitor",
        "metadata": {
            "name": rhaiis_raw_sglang_servicemonitor_name(plan),
            "namespace": plan.deployment.namespace,
            "labels": labels,
        },
        "spec": {
            "selector": {"matchLabels": selector_labels},
            "namespaceSelector": {"matchNames": [plan.deployment.namespace]},
            "endpoints": [
                {
                    "path": "/metrics",
                    "port": "http",
                    "scheme": "http",
                }
            ],
        },
    }
    return [deployment, service, servicemonitor]


def _render_rhaiis_distributed_raw_sglang_manifests(
    plan: ResolvedRunPlan,
) -> list[dict[str, Any]]:
    runtime = plan.deployment.runtime
    if runtime.replicas < 2:
        raise ValidationError(
            "rhaiis distributed raw-sglang requires runtime.replicas >= 2"
        )
    model_path = Path(_rhaiis_raw_vllm_model_path(plan))
    if not model_path.is_absolute():
        raise ValidationError("rhaiis raw-sglang options.model_path must be absolute")
    if not any(
        model_path == Path(item.mount_path)
        or model_path.is_relative_to(Path(item.mount_path))
        for item in runtime.host_paths
    ):
        raise ValidationError(
            "rhaiis distributed raw-sglang options.model_path must be inside a runtime.host_paths mount"
        )

    distributed = plan.deployment.options.get("distributed") or {}
    master_port = int(distributed.get("master_port", 20000))
    launch_style = str(distributed.get("launch_style") or "sglang-nnodes")
    if launch_style != "sglang-nnodes":
        raise ValidationError(
            "rhaiis distributed raw-sglang requires launch_style 'sglang-nnodes'"
        )
    head_start_delay_seconds = int(distributed.get("head_start_delay_seconds", 45))
    workload_name = rhaiis_raw_sglang_deployment_name(plan)
    headless_name = rhaiis_raw_sglang_headless_service_name(plan)
    leader_host = (
        f"{workload_name}-0.{headless_name}."
        f"{plan.deployment.namespace}.svc.cluster.local"
    )
    labels = _rhaiis_raw_sglang_labels(plan)
    selector_labels = _rhaiis_raw_sglang_selector_labels(plan)
    metrics_target = {"benchflow.io/metrics-target": plan.deployment.release_name}

    container_spec = _rhaiis_raw_sglang_container(plan)
    container_env = list(container_spec.get("env") or [])
    container_env.extend(
        [
            {
                "name": "POD_NAME",
                "valueFrom": {"fieldRef": {"fieldPath": "metadata.name"}},
            },
            {
                "name": "POD_IP",
                "valueFrom": {"fieldRef": {"fieldPath": "status.podIP"}},
            },
            {"name": "DP_LEADER_HOST", "value": leader_host},
            {
                "name": "HEAD_START_DELAY_SECONDS",
                "value": str(head_start_delay_seconds),
            },
            {"name": "DIST_PORT", "value": str(master_port)},
        ]
    )
    container_spec["env"] = container_env

    host_network = bool(distributed.get("host_network", False))
    socket_iface_preamble = ""
    if host_network:
        socket_iface_preamble = _rhaiis_host_network_socket_iface_preamble()

    serve_argv = [
        str(item)
        for item in (
            *(container_spec.pop("command") or []),
            *(container_spec.pop("args") or []),
        )
    ]
    base_argv = [*serve_argv, f"--nnodes={runtime.replicas}"]
    launch_script = (
        socket_iface_preamble
        + 'export SGLANG_HOST_IP="${POD_IP}"\n'
        'node_rank=${POD_NAME##*-}\n'
        'if [ -z "${POD_IP}" ]; then\n'
        '  echo "POD_IP is empty; cannot set --dist-init-addr" >&2\n'
        "  exit 1\n"
        "fi\n"
        'if [ "${node_rank}" = "0" ]; then\n'
        '  dist_init_addr="${POD_IP}"\n'
        '  delay="${HEAD_START_DELAY_SECONDS:-0}"\n'
        '  if [ "${delay}" -gt 0 ] 2>/dev/null; then\n'
        '    echo "rank0 waiting ${delay}s so workers start first" >&2\n'
        '    sleep "${delay}"\n'
        "  fi\n"
        '  exec "$@" --node-rank="${node_rank}" '
        '--dist-init-addr="${dist_init_addr}:${DIST_PORT}"\n'
        "fi\n"
        'dist_init_addr=$(getent ahostsv4 "${DP_LEADER_HOST}" 2>/dev/null '
        "| awk '{print $1; exit}')\n"
        'if [ -z "${dist_init_addr}" ]; then\n'
        '  dist_init_addr=$(getent hosts "${DP_LEADER_HOST}" 2>/dev/null '
        "| awk '{print $1; exit}')\n"
        "fi\n"
        'if [ -z "${dist_init_addr}" ]; then\n'
        '  echo "failed to resolve DP leader ${DP_LEADER_HOST}" >&2\n'
        "  exit 1\n"
        "fi\n"
        'exec "$@" --node-rank="${node_rank}" '
        '--dist-init-addr="${dist_init_addr}:${DIST_PORT}"\n'
    )
    container_spec["command"] = ["/bin/sh", "-c"]
    container_spec["args"] = [
        launch_script,
        "benchflow-sglang",
        *base_argv,
    ]
    container_spec["ports"].append(
        {
            "containerPort": master_port,
            "name": "distributed",
            "protocol": "TCP",
        }
    )
    container_spec["readinessProbe"] = {
        "exec": {
            "command": [
                "/bin/sh",
                "-c",
                (
                    'node_rank=${POD_NAME##*-}; '
                    'if [ "${node_rank}" != "0" ]; then kill -0 1; else '
                    'python3 -c "import urllib.request; '
                    "urllib.request.urlopen("
                    f"'http://127.0.0.1:{RHAIIS_RAW_SGLANG_HTTP_PORT}/health', "
                    'timeout=3)"; fi'
                ),
            ]
        },
        "initialDelaySeconds": 30,
        "periodSeconds": 10,
        "timeoutSeconds": 5,
        "failureThreshold": 720,
    }
    pod_spec = _rhaiis_raw_vllm_pod_spec(plan, container_spec)
    pod_spec["hostNetwork"] = bool(distributed.get("host_network", False))
    pod_spec["hostIPC"] = bool(distributed.get("host_ipc", False))
    if pod_spec["hostNetwork"]:
        pod_spec["dnsPolicy"] = "ClusterFirstWithHostNet"

    affinity = pod_spec.setdefault("affinity", {})
    required_anti_affinity = affinity.setdefault("podAntiAffinity", {}).setdefault(
        "requiredDuringSchedulingIgnoredDuringExecution", []
    )
    required_anti_affinity.append(
        {
            "labelSelector": {"matchLabels": selector_labels},
            "topologyKey": "kubernetes.io/hostname",
        }
    )

    statefulset = {
        "apiVersion": "apps/v1",
        "kind": "StatefulSet",
        "metadata": {
            "name": workload_name,
            "namespace": plan.deployment.namespace,
            "labels": labels,
        },
        "spec": {
            "serviceName": headless_name,
            "podManagementPolicy": "Parallel",
            "replicas": runtime.replicas,
            "selector": {"matchLabels": selector_labels},
            "template": {
                "metadata": {"labels": {**labels, **selector_labels}},
                "spec": pod_spec,
            },
        },
    }
    headless_service = {
        "apiVersion": "v1",
        "kind": "Service",
        "metadata": {
            "name": headless_name,
            "namespace": plan.deployment.namespace,
            "labels": {**labels, **metrics_target},
        },
        "spec": {
            "clusterIP": "None",
            "publishNotReadyAddresses": True,
            "selector": selector_labels,
            "ports": [
                {
                    "name": "http",
                    "port": RHAIIS_RAW_SGLANG_HTTP_PORT,
                    "protocol": "TCP",
                    "targetPort": "http",
                },
                {
                    "name": "distributed",
                    "port": master_port,
                    "protocol": "TCP",
                    "targetPort": "distributed",
                },
            ],
        },
    }
    api_service = {
        "apiVersion": "v1",
        "kind": "Service",
        "metadata": {
            "name": rhaiis_raw_sglang_service_name(plan),
            "namespace": plan.deployment.namespace,
            "labels": labels,
        },
        "spec": {
            "type": "ExternalName",
            "externalName": leader_host,
            "ports": [
                {
                    "name": "http",
                    "port": RHAIIS_RAW_SGLANG_HTTP_PORT,
                    "protocol": "TCP",
                    "targetPort": "http",
                }
            ],
        },
    }
    servicemonitor = {
        "apiVersion": "monitoring.coreos.com/v1",
        "kind": "ServiceMonitor",
        "metadata": {
            "name": rhaiis_raw_sglang_servicemonitor_name(plan),
            "namespace": plan.deployment.namespace,
            "labels": labels,
        },
        "spec": {
            "selector": {"matchLabels": metrics_target},
            "namespaceSelector": {"matchNames": [plan.deployment.namespace]},
            "endpoints": [
                {
                    "path": "/metrics",
                    "port": "http",
                    "scheme": "http",
                    "relabelings": [
                        {
                            "sourceLabels": ["__meta_kubernetes_pod_name"],
                            "regex": f"{workload_name}-0",
                            "action": "keep",
                        }
                    ],
                }
            ],
        },
    }
    return [statefulset, headless_service, api_service, servicemonitor]


def render_rhaiis_raw_sglang_manifests(plan: ResolvedRunPlan) -> list[dict[str, Any]]:
    if not plan.deployment.runtime.image:
        raise ValidationError(
            "rhaiis raw-sglang deployments require deployment.runtime.image"
        )
    if rhaiis_raw_sglang_is_distributed(plan):
        return _render_rhaiis_distributed_raw_sglang_manifests(plan)
    return _render_rhaiis_single_raw_sglang_manifests(plan)


def rhaiis_raw_deployment_name(plan: ResolvedRunPlan) -> str:
    if plan.deployment.mode == "raw-sglang":
        return rhaiis_raw_sglang_deployment_name(plan)
    return rhaiis_raw_vllm_deployment_name(plan)


def rhaiis_raw_workload_kind(plan: ResolvedRunPlan) -> str:
    if plan.deployment.mode == "raw-sglang":
        return rhaiis_raw_sglang_workload_kind(plan)
    return rhaiis_raw_vllm_workload_kind(plan)


def rhaiis_raw_headless_service_name(plan: ResolvedRunPlan) -> str:
    if plan.deployment.mode == "raw-sglang":
        return rhaiis_raw_sglang_headless_service_name(plan)
    return rhaiis_raw_vllm_headless_service_name(plan)


def rhaiis_raw_service_name(plan: ResolvedRunPlan) -> str:
    if plan.deployment.mode == "raw-sglang":
        return rhaiis_raw_sglang_service_name(plan)
    return rhaiis_raw_vllm_service_name(plan)


def rhaiis_raw_servicemonitor_name(plan: ResolvedRunPlan) -> str:
    if plan.deployment.mode == "raw-sglang":
        return rhaiis_raw_sglang_servicemonitor_name(plan)
    return rhaiis_raw_vllm_servicemonitor_name(plan)


def rhaiis_raw_is_distributed(plan: ResolvedRunPlan) -> bool:
    return rhaiis_raw_vllm_is_distributed(plan)


def render_rhaiis_raw_manifests(plan: ResolvedRunPlan) -> list[dict[str, Any]]:
    if plan.deployment.mode == "raw-sglang":
        return render_rhaiis_raw_sglang_manifests(plan)
    if plan.deployment.mode == "raw-vllm":
        return render_rhaiis_raw_vllm_manifests(plan)
    raise ValidationError(
        f"unsupported RHAIIS deployment mode: {plan.deployment.mode}"
    )


def write_deployment_assets(
    plan: ResolvedRunPlan,
    output_dir: Path,
    *,
    rhoai_release_gateway: dict[str, Any] | None = None,
) -> list[Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []

    if plan.deployment.platform == "llm-d":
        for pvc_manifest in render_runtime_pvc_manifests(plan):
            pvc_name = str(pvc_manifest.get("metadata", {}).get("name") or "runtime")
            target = output_dir / f"pvc-{pvc_name}.yaml"
            target.write_text(
                yaml.safe_dump(pvc_manifest, sort_keys=False), encoding="utf-8"
            )
            written.append(target)
        target = output_dir / "llm-d-values.yaml"
        target.write_text(
            yaml.safe_dump(render_llmd_values(plan), sort_keys=False), encoding="utf-8"
        )
        written.append(target)
        return written

    if plan.deployment.platform == "rhoai":
        if (
            plan.deployment.target.resource_kind != "InferenceService"
            and rhoai_release_gateway is None
        ):
            raise ValidationError(
                "RHOAI LLMInferenceService rendering requires its release-scoped "
                "Gateway manifest"
            )
        for pvc_manifest in render_runtime_pvc_manifests(plan):
            pvc_name = str(pvc_manifest.get("metadata", {}).get("name") or "runtime")
            target = output_dir / f"pvc-{pvc_name}.yaml"
            target.write_text(
                yaml.safe_dump(pvc_manifest, sort_keys=False), encoding="utf-8"
            )
            written.append(target)
        mooncake_manifests = render_rhoai_mooncake_manifests(plan)
        if mooncake_manifests:
            for manifest, filename in zip(
                mooncake_manifests,
                (
                    "mooncake-configmap.yaml",
                    "mooncake-master-service.yaml",
                    "mooncake-master-deployment.yaml",
                ),
                strict=True,
            ):
                target = output_dir / filename
                target.write_text(
                    yaml.safe_dump(manifest, sort_keys=False), encoding="utf-8"
                )
                written.append(target)
        if plan.execution.profiling.enabled:
            profiler_target = output_dir / "vllm-profiler-configmap.yaml"
            profiler_target.write_text(
                yaml.safe_dump(render_rhoai_profiler_configmap(plan), sort_keys=False),
                encoding="utf-8",
            )
            written.append(profiler_target)
        if rhoai_release_gateway is not None:
            gateway_target = output_dir / "rhoai-release-gateway.yaml"
            gateway_target.write_text(
                yaml.safe_dump(rhoai_release_gateway, sort_keys=False),
                encoding="utf-8",
            )
            written.append(gateway_target)
        target = output_dir / "llminferenceservice.yaml"
        target.write_text(
            yaml.safe_dump(render_rhoai_manifest(plan), sort_keys=False),
            encoding="utf-8",
        )
        written.append(target)
        return written

    if plan.deployment.platform == "rhaiis":
        for pvc_manifest in render_runtime_pvc_manifests(plan):
            pvc_name = str(pvc_manifest.get("metadata", {}).get("name") or "runtime")
            target = output_dir / f"pvc-{pvc_name}.yaml"
            target.write_text(
                yaml.safe_dump(pvc_manifest, sort_keys=False), encoding="utf-8"
            )
            written.append(target)
        manifests = render_rhaiis_raw_manifests(plan)
        for manifest in manifests:
            kind = str(manifest.get("kind") or "manifest").lower()
            manifest_name = str((manifest.get("metadata") or {}).get("name") or "")
            name = (
                "headless-service.yaml"
                if kind == "service" and manifest_name != plan.deployment.release_name
                else f"{kind}.yaml"
            )
            target = output_dir / name
            target.write_text(
                yaml.safe_dump(manifest, sort_keys=False), encoding="utf-8"
            )
            written.append(target)
        return written

    raise ValidationError(
        f"unsupported deployment platform for rendered assets: {plan.deployment.platform}"
    )
