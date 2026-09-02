#!/usr/bin/env python3
"""Download one MLflow run (metadata, metric history, artifacts) to disk.

Auth (first match wins per field):
  1. MLFLOW_TRACKING_URI / USERNAME / PASSWORD / WORKSPACE
  2. Kubernetes secret mlflow-ui-auth (namespace benchflow by default)

Examples:
  PYTHONPATH=src python3 scripts/pull-mlflow-run.py 3d48f7a3a17a430a87255a5d68f8711b
  python3 scripts/pull-mlflow-run.py 3d48f7a3... --out /tmp/that-run
"""
from __future__ import annotations

import argparse
import base64
import json
import os
import subprocess
import sys
import traceback
import warnings
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = REPO_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

DEFAULT_SECRET_NS = "benchflow"
DEFAULT_SECRET_NAME = "mlflow-ui-auth"


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace(
        "+00:00", "Z"
    )


def _mlflow_properties(value: Any) -> dict[str, Any] | None:
    if not type(value).__module__.startswith("mlflow."):
        return None
    data: dict[str, Any] = {}
    for name in dir(value):
        if name.startswith("_"):
            continue
        attr = getattr(type(value), name, None)
        if not isinstance(attr, property):
            continue
        try:
            data[name] = getattr(value, name)
        except Exception:
            continue
    return data or None


def _jsonable(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_jsonable(v) for v in value]
    if hasattr(value, "to_dictionary"):
        return _jsonable(value.to_dictionary())
    if hasattr(value, "to_dict"):
        return _jsonable(value.to_dict())
    props = _mlflow_properties(value)
    if props is not None:
        return _jsonable(props)
    if hasattr(value, "__dict__"):
        public = {
            k: v for k, v in vars(value).items() if not k.startswith("_")
        }
        if public:
            return _jsonable(public)
    return str(value)


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(_jsonable(payload), indent=2, sort_keys=True) + "\n")


def _log(message: str) -> None:
    print(message, file=sys.stderr)


def _load_kube_secret(namespace: str, name: str) -> dict[str, str]:
    raw = subprocess.check_output(
        ["kubectl", "-n", namespace, "get", "secret", name, "-o", "json"],
        text=True,
    )
    secret = json.loads(raw)
    decoded: dict[str, str] = {}
    for key, value in (secret.get("data") or {}).items():
        decoded[key] = base64.b64decode(value).decode()
    return decoded


def configure_auth(args: argparse.Namespace) -> None:
    uri = (os.environ.get("MLFLOW_TRACKING_URI") or "").strip()
    user = (os.environ.get("MLFLOW_TRACKING_USERNAME") or "").strip()
    password = (os.environ.get("MLFLOW_TRACKING_PASSWORD") or "").strip()
    workspace = (os.environ.get("MLFLOW_WORKSPACE") or "").strip()

    need_secret = args.kube_secret and (
        not uri or not user or not password or not workspace
    )
    if need_secret:
        secret = _load_kube_secret(args.secret_namespace, args.secret_name)
        uri = uri or (secret.get("tracking-uri") or "").strip()
        user = user or (secret.get("username") or "").strip()
        password = password or (secret.get("password") or "").strip()
        workspace = workspace or (secret.get("workspace") or "").strip()
        _log(
            f"loaded MLflow auth from secret {args.secret_namespace}/{args.secret_name}"
        )

    if not uri:
        raise SystemExit(
            "MLFLOW_TRACKING_URI is unset and Kubernetes secret lookup failed or "
            "was disabled (--no-kube-secret)"
        )

    os.environ["MLFLOW_TRACKING_URI"] = uri
    if user:
        os.environ["MLFLOW_TRACKING_USERNAME"] = user
    if password:
        os.environ["MLFLOW_TRACKING_PASSWORD"] = password
    if workspace:
        os.environ["MLFLOW_WORKSPACE"] = workspace
    if args.insecure_tls:
        os.environ["MLFLOW_TRACKING_INSECURE_TLS"] = "true"
    elif "MLFLOW_TRACKING_INSECURE_TLS" not in os.environ:
        os.environ["MLFLOW_TRACKING_INSECURE_TLS"] = "false"

    _log(f"tracking URI: {uri}")
    if workspace:
        _log(f"workspace: {workspace}")


def create_client():
    try:
        from benchflow.mlflow_compat import (
            configure_mlflow_tracking,
            create_mlflow_client,
        )

        configure_mlflow_tracking()
        return create_mlflow_client()
    except ImportError:
        import mlflow

        mlflow.set_tracking_uri(os.environ["MLFLOW_TRACKING_URI"])
        workspace = os.environ.get("MLFLOW_WORKSPACE", "").strip()
        if workspace and hasattr(mlflow, "set_workspace"):
            mlflow.set_workspace(workspace)
        return mlflow.tracking.MlflowClient()


def list_artifact_tree(client, run_id: str, prefix: str = "") -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    listed = client.list_artifacts(run_id, prefix or None)
    for item in listed:
        rec = {
            "path": item.path,
            "is_dir": bool(item.is_dir),
            "file_size": None if item.is_dir else item.file_size,
        }
        entries.append(rec)
        if item.is_dir:
            entries.extend(list_artifact_tree(client, run_id, item.path))
    return entries


def dump_metric_histories(client, run_id: str, metric_keys: list[str]) -> dict[str, Any]:
    histories: dict[str, Any] = {}
    for key in metric_keys:
        points = []
        for metric in client.get_metric_history(run_id, key):
            points.append(
                {
                    "key": metric.key,
                    "value": metric.value,
                    "timestamp": metric.timestamp,
                    "step": metric.step,
                }
            )
        histories[key] = points
    return histories


def dump_optional(label: str, fn) -> Any:
    try:
        return fn()
    except Exception as exc:
        _log(f"skipping {label}: {type(exc).__name__}: {exc}")
        return {"error": f"{type(exc).__name__}: {exc}"}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Download one MLflow run: metadata, metrics, artifacts."
    )
    parser.add_argument("run_id", help="MLflow run ID")
    parser.add_argument(
        "--out",
        type=Path,
        default=None,
        help="Output directory (default: ./mlflow-runs/<run_id>)",
    )
    parser.add_argument(
        "--no-artifacts",
        action="store_true",
        help="Skip downloading artifact files (still writes artifact_index.json)",
    )
    parser.add_argument(
        "--no-metric-history",
        action="store_true",
        help="Skip per-key metric history (still writes latest metrics)",
    )
    parser.add_argument(
        "--no-kube-secret",
        dest="kube_secret",
        action="store_false",
        help="Do not read mlflow-ui-auth from Kubernetes",
    )
    parser.add_argument(
        "--secret-namespace",
        default=os.environ.get("MLFLOW_SECRET_NAMESPACE", DEFAULT_SECRET_NS),
    )
    parser.add_argument(
        "--secret-name",
        default=os.environ.get("MLFLOW_SECRET_NAME", DEFAULT_SECRET_NAME),
    )
    parser.add_argument(
        "--insecure-tls",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Skip TLS verify (default: true, matches this cluster's MLflow)",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    run_id = args.run_id.strip()
    if not run_id:
        raise SystemExit("run id is empty")

    out = (args.out or Path("mlflow-runs") / run_id).resolve()
    out.mkdir(parents=True, exist_ok=True)

    if args.insecure_tls:
        warnings.filterwarnings("ignore", message="Unverified HTTPS request")

    configure_auth(args)
    client = create_client()

    _log(f"fetching run {run_id}")
    run = client.get_run(run_id)
    run_dict = run.to_dictionary()
    _write_json(out / "run.json", run_dict)

    experiment_id = run.info.experiment_id
    experiment = dump_optional(
        "experiment", lambda: client.get_experiment(experiment_id)
    )
    _write_json(out / "experiment.json", experiment)

    parent = dump_optional("parent run", lambda: client.get_parent_run(run_id))
    _write_json(out / "parent_run.json", parent)

    child_runs = dump_optional(
        "child runs",
        lambda: list(
            client.search_runs(
                experiment_ids=[experiment_id],
                filter_string=f"tags.mlflow.parentRunId = '{run_id}'",
            )
        ),
    )
    _write_json(out / "child_runs.json", child_runs)

    traces = dump_optional(
        "traces",
        lambda: list(client.search_traces(locations=[experiment_id], run_id=run_id)),
    )
    _write_json(out / "traces.json", traces)

    logged_models = dump_optional(
        "logged models",
        lambda: list(client.search_logged_models(experiment_ids=[experiment_id])),
    )
    _write_json(out / "logged_models.json", logged_models)

    latest_metrics = dict(run.data.metrics or {})
    _write_json(out / "metrics_latest.json", latest_metrics)
    _write_json(out / "params.json", dict(run.data.params or {}))
    _write_json(out / "tags.json", dict(run.data.tags or {}))

    if args.no_metric_history:
        _write_json(out / "metrics_history.json", {})
    else:
        _log(f"fetching metric history for {len(latest_metrics)} key(s)")
        histories = dump_metric_histories(client, run_id, sorted(latest_metrics))
        _write_json(out / "metrics_history.json", histories)

    _log("listing artifacts")
    artifact_index = list_artifact_tree(client, run_id)
    _write_json(out / "artifact_index.json", artifact_index)

    artifacts_dir = out / "artifacts"
    artifacts_dir.mkdir(parents=True, exist_ok=True)
    if args.no_artifacts:
        _log("skipping artifact download")
    elif not artifact_index:
        _log("no artifacts on this run")
    else:
        _log(f"downloading {len(artifact_index)} artifact entries to {artifacts_dir}")
        try:
            client.download_artifacts(run_id, "", str(artifacts_dir))
        except Exception:
            _log("bulk artifact download failed; retrying top-level entries")
            _log(traceback.format_exc().rstrip())
            for item in client.list_artifacts(run_id):
                try:
                    client.download_artifacts(run_id, item.path, str(artifacts_dir))
                except Exception as exc:
                    _log(f"failed to download {item.path}: {type(exc).__name__}: {exc}")

    summary = {
        "pulled_at": _utc_now(),
        "run_id": run_id,
        "experiment_id": experiment_id,
        "experiment_name": (
            experiment.get("name")
            if isinstance(experiment, dict)
            else getattr(experiment, "name", None)
        ),
        "run_name": (run.data.tags or {}).get("mlflow.runName"),
        "status": run.info.status,
        "artifact_uri": run.info.artifact_uri,
        "metric_keys": sorted(latest_metrics),
        "param_keys": sorted(run.data.params or {}),
        "tag_count": len(run.data.tags or {}),
        "artifact_entries": len(artifact_index),
        "output_dir": str(out),
        "tracking_uri": os.environ.get("MLFLOW_TRACKING_URI"),
        "workspace": os.environ.get("MLFLOW_WORKSPACE") or None,
    }
    _write_json(out / "summary.json", summary)

    _log(f"wrote {out}")
    _log(
        f"status={run.info.status} metrics={len(latest_metrics)} "
        f"artifacts={len(artifact_index)}"
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        raise SystemExit(130)
