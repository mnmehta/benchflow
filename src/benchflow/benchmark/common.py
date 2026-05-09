from __future__ import annotations

from ..accelerator import discover_plan_accelerator
from ..cluster import CommandError, require_any_command
from ..models import ResolvedRunPlan
from ..platform_state import load_cluster_platform_state
from ..setup.rhoai import (
    discover_rhoai_mlflow_version,
    normalize_rhoai_platform_version,
)
from ..ui import warning


class BenchmarkRunFailed(CommandError):
    def __init__(
        self,
        message: str,
        *,
        run_id: str = "",
        start_time: str = "",
        end_time: str = "",
    ) -> None:
        super().__init__(message)
        self.run_id = run_id
        self.start_time = start_time
        self.end_time = end_time


def benchmark_version_from_plan(plan: ResolvedRunPlan) -> str:
    explicit_version = str(plan.mlflow.version or "").strip()
    if explicit_version:
        return explicit_version
    if plan.deployment.platform == "llm-d":
        repo_ref = str(plan.deployment.repo_ref or "").strip()
        if repo_ref == "main":
            try:
                kubectl_cmd = require_any_command("oc", "kubectl")
                cluster_state = load_cluster_platform_state(
                    kubectl_cmd, plan.deployment.namespace
                )
                setup_state = dict(cluster_state.get("setup_state") or {})
                repo_head = str(setup_state.get("repo_head") or "").strip()
                if repo_head:
                    return f"llm-d-main-{repo_head[:7]}"
            except CommandError:
                pass
            return "llm-d-main"
        return f"llm-d-{repo_ref}"
    if plan.deployment.platform == "rhoai":
        kubeconfig = str(plan.target_cluster.kubeconfig or "").strip() or None
        try:
            return discover_rhoai_mlflow_version(kubeconfig=kubeconfig)
        except CommandError:
            pass
        return normalize_rhoai_platform_version(plan.deployment.platform_version)
    return f"{plan.deployment.platform}-{plan.deployment.mode}"


def resolved_accelerator(plan: ResolvedRunPlan) -> str:
    try:
        return discover_plan_accelerator(plan)
    except CommandError as exc:
        warning(
            "Could not auto-discover accelerator from the cluster; "
            f"falling back to unknown: {exc}"
        )
        return "unknown"
