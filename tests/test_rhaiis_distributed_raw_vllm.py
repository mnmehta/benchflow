from __future__ import annotations

import unittest
from pathlib import Path

from benchflow.loaders import ProfileCatalog, load_experiment
from benchflow.matrix import resolve_experiment_matrix
from benchflow.renderers.deployment import render_rhaiis_raw_vllm_manifests


REPO_ROOT = Path(__file__).resolve().parents[1]


def _kimi_plan():
    experiment = load_experiment(
        REPO_ROOT / "experiments/rhaiis/kimi-k3-tp8-dp4-ep32.yaml"
    )
    catalog = ProfileCatalog.load(REPO_ROOT / "profiles")
    plans = resolve_experiment_matrix(experiment, catalog)
    assert len(plans) == 1
    return plans[0]


class RhaiisDistributedRawVllmTest(unittest.TestCase):
    def test_existing_raw_vllm_profile_keeps_deployment_shape(self) -> None:
        experiment = load_experiment(
            REPO_ROOT / "experiments/rhaiis/llama-33-70b-release.yaml"
        )
        plan = resolve_experiment_matrix(
            experiment, ProfileCatalog.load(REPO_ROOT / "profiles")
        )[0]

        manifests = render_rhaiis_raw_vllm_manifests(plan)

        self.assertEqual(
            [manifest["kind"] for manifest in manifests],
            ["Deployment", "Service", "ServiceMonitor"],
        )

    def test_kimi_profile_resolves_characterized_topology(self) -> None:
        plan = _kimi_plan()

        self.assertEqual(plan.deployment.platform, "rhaiis")
        self.assertEqual(plan.deployment.mode, "raw-vllm")
        self.assertEqual(plan.deployment.namespace, "benchflow")
        self.assertEqual(plan.deployment.runtime.replicas, 4)
        self.assertEqual(plan.deployment.runtime.tensor_parallelism, 8)
        self.assertEqual(plan.deployment.runtime.image, "vllm/vllm-openai:kimi-k3")
        self.assertEqual(
            plan.deployment.runtime.service_account_name,
            "benchflow-hostpath-runtime",
        )
        self.assertEqual(plan.deployment.options["model_path"], "/models/Kimi-K3")
        self.assertTrue(plan.deployment.options["distributed"]["enabled"])
        self.assertEqual(
            plan.deployment.options["distributed"]["launch_style"], "ix-agg"
        )
        self.assertTrue(plan.deployment.options["distributed"]["host_network"])
        self.assertTrue(plan.deployment.options["distributed"]["host_ipc"])
        self.assertIn("--data-parallel-size=4", plan.deployment.runtime.vllm_args)
        self.assertIn("--enable-expert-parallel", plan.deployment.runtime.vllm_args)
        self.assertNotIn(
            "--max-model-len=1048576", plan.deployment.runtime.vllm_args
        )
        self.assertEqual(
            plan.deployment.runtime.env.get("VLLM_ENGINE_READY_TIMEOUT_S"), "7200"
        )
        self.assertFalse(plan.stages.download)

    def test_renderer_creates_ranked_statefulset_and_leader_service(self) -> None:
        plan = _kimi_plan()
        manifests = render_rhaiis_raw_vllm_manifests(plan)
        by_kind_name = {
            (manifest["kind"], manifest["metadata"]["name"]): manifest
            for manifest in manifests
        }
        workload_name = f"{plan.deployment.release_name}-vllm"
        headless_name = f"{workload_name}-headless"

        statefulset = by_kind_name[("StatefulSet", workload_name)]
        self.assertEqual(statefulset["spec"]["replicas"], 4)
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
        self.assertEqual(container["image"], "vllm/vllm-openai:kimi-k3")
        self.assertEqual(container["command"], ["/bin/sh", "-c"])
        script = container["args"][0]
        self.assertIn("${POD_NAME##*-}", script)
        self.assertIn('--node-rank="${node_rank}"', script)
        self.assertIn('--master-addr="${master_addr}"', script)
        self.assertNotIn("data-parallel-address", script)
        self.assertIn("--headless", script)
        self.assertIn("rank0 waiting", script)
        self.assertIn("getent ahostsv4", script)
        # hostNetwork: detect Ethernet iface for Gloo/NCCL (avoid 127.0.0.1)
        self.assertIn("GLOO_SOCKET_IFNAME", script)
        self.assertIn("NCCL_SOCKET_IFNAME", script)
        self.assertIn("/sys/class/net", script)
        self.assertIn("applying mamba_hybrid PR #50327", script)
        self.assertIn("_fill_num_accepted_kernel", script)
        self.assertIn("applying shm_broadcast ZMQ bind retry on EADDRINUSE", script)
        self.assertIn("Kimi-K3 harness: retry ZMQ bind", script)
        self.assertIn("shm_broadcast.py", script)
        joined = " ".join(str(a) for a in container["args"])
        self.assertIn("vllm", container["args"])
        self.assertIn("serve", container["args"])
        self.assertIn("/models/Kimi-K3", container["args"])
        self.assertIn("--nnodes=4", joined)
        self.assertNotIn("--model=/models/Kimi-K3", joined)
        self.assertNotIn("vllm.entrypoints.openai.api_server", joined)
        self.assertNotIn("--data-parallel-rank=", joined)
        self.assertNotIn("--data-parallel-rpc-port=", joined)
        env_by_name = {item["name"]: item for item in container["env"]}
        self.assertEqual(
            env_by_name["POD_NAME"]["valueFrom"]["fieldRef"]["fieldPath"],
            "metadata.name",
        )
        self.assertEqual(
            env_by_name["POD_IP"]["valueFrom"]["fieldRef"]["fieldPath"],
            "status.podIP",
        )
        self.assertIn(workload_name + "-0.", env_by_name["DP_LEADER_HOST"]["value"])
        self.assertEqual(env_by_name["HEAD_START_DELAY_SECONDS"]["value"], "45")
        self.assertEqual(env_by_name["VLLM_MASTER_PORT"]["value"], "29500")
        self.assertEqual(env_by_name["VLLM_ENGINE_READY_TIMEOUT_S"]["value"], "7200")
        self.assertNotIn(
            "model-storage", {volume["name"] for volume in pod_spec["volumes"]}
        )
        self.assertEqual(container["resources"]["limits"]["nvidia.com/gpu"], "8")
        self.assertEqual(container["resources"]["limits"]["rdma/ib"], "1")
        readiness = container["readinessProbe"]["exec"]["command"][-1]
        self.assertIn("${POD_NAME##*-}", readiness)
        self.assertNotIn("${HOSTNAME##*-}", readiness)
        self.assertNotIn("kill -0 1", readiness)
        self.assertIn("/proc/net/tcp", readiness)
        self.assertIn("vllm-tcpstore-joined", readiness)
        self.assertEqual(container["readinessProbe"]["failureThreshold"], 720)
        liveness = container["livenessProbe"]["exec"]["command"][-1]
        self.assertIn("kill -0 1", liveness)
        self.assertNotIn("vllm-tcpstore-joined", liveness)
        self.assertEqual(container["livenessProbe"]["failureThreshold"], 20)

        headless = by_kind_name[("Service", headless_name)]
        self.assertEqual(headless["spec"]["clusterIP"], "None")
        self.assertTrue(headless["spec"]["publishNotReadyAddresses"])

        api_service = by_kind_name[("Service", plan.deployment.release_name)]
        self.assertEqual(api_service["spec"]["type"], "ExternalName")
        self.assertEqual(
            api_service["spec"]["externalName"],
            f"{workload_name}-0.{headless_name}.{plan.deployment.namespace}.svc.cluster.local",
        )

        monitor = by_kind_name[("ServiceMonitor", workload_name)]
        relabeling = monitor["spec"]["endpoints"][0]["relabelings"][0]
        self.assertEqual(relabeling["action"], "keep")
        self.assertEqual(relabeling["regex"], f"{workload_name}-0")

    def test_humming_profile_injects_situ_allowlist_patch(self) -> None:
        experiment = load_experiment(
            REPO_ROOT / "experiments/rhaiis/kimi-k3-tp8-pp2-humming-1k-1k.yaml"
        )
        plan = resolve_experiment_matrix(
            experiment, ProfileCatalog.load(REPO_ROOT / "profiles")
        )[0]
        self.assertIn("--moe-backend=humming", plan.deployment.runtime.vllm_args)
        self.assertEqual(plan.deployment.runtime.replicas, 2)

        manifests = render_rhaiis_raw_vllm_manifests(plan)
        statefulset = next(m for m in manifests if m["kind"] == "StatefulSet")
        script = statefulset["spec"]["template"]["spec"]["containers"][0]["args"][0]
        self.assertIn("applying humming MoEActivation.SITU allowlist patch", script)
        self.assertIn("MoEActivation.SITU", script)
        self.assertIn("fused_humming_moe.py", script)
        self.assertIn("applying mamba_hybrid PR #50327", script)
        self.assertIn("_fill_num_accepted_kernel", script)
        self.assertIn("applying shm_broadcast ZMQ bind retry on EADDRINUSE", script)
        self.assertIn("Kimi-K3 harness: retry ZMQ bind", script)
        self.assertIn("--moe-backend=humming", " ".join(
            str(a) for a in statefulset["spec"]["template"]["spec"]["containers"][0]["args"]
        ))


if __name__ == "__main__":
    unittest.main()
