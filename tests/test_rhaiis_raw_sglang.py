from __future__ import annotations

import unittest
from pathlib import Path

from benchflow.loaders import ProfileCatalog, load_experiment
from benchflow.matrix import resolve_experiment_matrix
from benchflow.renderers.deployment import render_rhaiis_raw_sglang_manifests


REPO_ROOT = Path(__file__).resolve().parents[1]


def _sglang_plan(experiment_name: str = "kimi-k3-tp8-pp2-sglang-1k-1k"):
    experiment = load_experiment(
        REPO_ROOT / f"experiments/rhaiis/{experiment_name}.yaml"
    )
    catalog = ProfileCatalog.load(REPO_ROOT / "profiles")
    plans = resolve_experiment_matrix(experiment, catalog)
    assert len(plans) == 1
    return plans[0]


class RhaiisRawSglangTest(unittest.TestCase):
    def test_profile_resolves_pp2_topology(self) -> None:
        plan = _sglang_plan()

        self.assertEqual(plan.deployment.platform, "rhaiis")
        self.assertEqual(plan.deployment.mode, "raw-sglang")
        self.assertEqual(plan.deployment.namespace, "benchflow")
        self.assertEqual(plan.deployment.runtime.replicas, 2)
        self.assertEqual(plan.deployment.runtime.tensor_parallelism, 8)
        self.assertEqual(plan.deployment.runtime.image, "lmsysorg/sglang:kimi-k3")
        self.assertEqual(
            plan.deployment.runtime.service_account_name,
            "benchflow-hostpath-runtime",
        )
        self.assertEqual(plan.deployment.options["model_path"], "/models/Kimi-K3")
        self.assertTrue(plan.deployment.options["distributed"]["enabled"])
        self.assertEqual(
            plan.deployment.options["distributed"]["launch_style"], "sglang-nnodes"
        )
        self.assertEqual(plan.deployment.options["distributed"]["master_port"], 20000)
        self.assertTrue(plan.deployment.options["distributed"]["host_network"])
        self.assertTrue(plan.deployment.options["distributed"]["host_ipc"])
        self.assertIn("--pp-size=2", plan.deployment.runtime.sglang_args)
        self.assertIn("--context-length=8192", plan.deployment.runtime.sglang_args)
        self.assertIn(
            "--moe-runner-backend=marlin", plan.deployment.runtime.sglang_args
        )
        self.assertEqual(
            plan.deployment.target.base_url,
            f"http://{plan.deployment.release_name}.{plan.deployment.namespace}"
            ".svc.cluster.local:30000",
        )
        self.assertFalse(plan.stages.download)

    def test_8k_benchmark_raises_context_length(self) -> None:
        plan = _sglang_plan("kimi-k3-tp8-pp2-sglang-8k-1k")
        self.assertIn("--context-length=16384", plan.deployment.runtime.sglang_args)
        self.assertNotIn("--context-length=8192", plan.deployment.runtime.sglang_args)
        self.assertNotIn("--max-model-len=16384", plan.deployment.runtime.vllm_args)

    def test_renderer_creates_ranked_statefulset_and_leader_service(self) -> None:
        plan = _sglang_plan()
        manifests = render_rhaiis_raw_sglang_manifests(plan)
        by_kind_name = {
            (manifest["kind"], manifest["metadata"]["name"]): manifest
            for manifest in manifests
        }
        workload_name = f"{plan.deployment.release_name}-sglang"
        headless_name = f"{workload_name}-headless"

        statefulset = by_kind_name[("StatefulSet", workload_name)]
        self.assertEqual(statefulset["spec"]["replicas"], 2)
        self.assertEqual(statefulset["spec"]["podManagementPolicy"], "Parallel")
        self.assertEqual(statefulset["spec"]["serviceName"], headless_name)

        pod_spec = statefulset["spec"]["template"]["spec"]
        self.assertEqual(
            pod_spec["serviceAccountName"],
            "benchflow-hostpath-runtime",
        )
        self.assertTrue(pod_spec["hostNetwork"])
        self.assertTrue(pod_spec["hostIPC"])
        self.assertEqual(pod_spec["dnsPolicy"], "ClusterFirstWithHostNet")
        anti_affinity = pod_spec["affinity"]["podAntiAffinity"]
        self.assertEqual(
            anti_affinity["requiredDuringSchedulingIgnoredDuringExecution"][0][
                "topologyKey"
            ],
            "kubernetes.io/hostname",
        )

        container = pod_spec["containers"][0]
        self.assertEqual(container["image"], "lmsysorg/sglang:kimi-k3")
        self.assertEqual(container["command"], ["/bin/sh", "-c"])
        script = container["args"][0]
        self.assertIn("${POD_NAME##*-}", script)
        self.assertIn('--node-rank="${node_rank}"', script)
        self.assertIn('--dist-init-addr="${dist_init_addr}:${DIST_PORT}"', script)
        self.assertNotIn("--headless", script)
        self.assertNotIn("master-addr", script)
        self.assertIn("rank0 waiting", script)
        self.assertIn("getent ahostsv4", script)
        self.assertIn("GLOO_SOCKET_IFNAME", script)
        self.assertIn("NCCL_SOCKET_IFNAME", script)
        self.assertIn("SGLANG_HOST_IP", script)
        self.assertNotIn("applying humming MoEActivation.SITU", script)
        self.assertNotIn("applying mamba_hybrid PR #50327", script)
        joined = " ".join(str(a) for a in container["args"])
        self.assertIn("sglang", container["args"])
        self.assertIn("serve", container["args"])
        self.assertIn("--nnodes=2", joined)
        self.assertIn("--tp-size=8", joined)
        self.assertIn("--pp-size=2", joined)
        self.assertIn("--port=30000", joined)
        self.assertIn("--model-path=/models/Kimi-K3", joined)
        env_by_name = {item["name"]: item for item in container["env"]}
        self.assertEqual(env_by_name["HOME"]["value"], "/tmp/sglang-home")
        self.assertEqual(env_by_name["DIST_PORT"]["value"], "20000")
        self.assertEqual(env_by_name["HEAD_START_DELAY_SECONDS"]["value"], "45")
        self.assertEqual(
            env_by_name["SGLANG_ENABLE_TP_MEMORY_INBALANCE_CHECK"]["value"], "0"
        )
        self.assertEqual(container["resources"]["limits"]["nvidia.com/gpu"], "8")
        readiness = container["readinessProbe"]["exec"]["command"][-1]
        self.assertIn("${POD_NAME##*-}", readiness)
        self.assertIn("127.0.0.1:30000/health", readiness)
        self.assertEqual(container["readinessProbe"]["failureThreshold"], 720)

        headless = by_kind_name[("Service", headless_name)]
        self.assertEqual(headless["spec"]["clusterIP"], "None")
        self.assertTrue(headless["spec"]["publishNotReadyAddresses"])
        headless_ports = {port["name"]: port["port"] for port in headless["spec"]["ports"]}
        self.assertEqual(headless_ports["http"], 30000)
        self.assertEqual(headless_ports["distributed"], 20000)

        api_service = by_kind_name[("Service", plan.deployment.release_name)]
        self.assertEqual(api_service["spec"]["type"], "ExternalName")
        self.assertEqual(
            api_service["spec"]["externalName"],
            f"{workload_name}-0.{headless_name}.{plan.deployment.namespace}.svc.cluster.local",
        )
        self.assertEqual(api_service["spec"]["ports"][0]["port"], 30000)

        monitor = by_kind_name[("ServiceMonitor", workload_name)]
        relabeling = monitor["spec"]["endpoints"][0]["relabelings"][0]
        self.assertEqual(relabeling["action"], "keep")
        self.assertEqual(relabeling["regex"], f"{workload_name}-0")

        labels = statefulset["metadata"]["labels"]
        self.assertEqual(labels["app.kubernetes.io/component"], "raw-sglang")


if __name__ == "__main__":
    unittest.main()
