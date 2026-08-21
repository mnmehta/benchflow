from __future__ import annotations

import unittest

from benchflow.bootstrap_resources import select_hostpath_runtime_documents


def _sample_documents() -> list[dict]:
    return [
        {
            "apiVersion": "security.openshift.io/v1",
            "kind": "SecurityContextConstraints",
            "metadata": {"name": "benchflow-hostpath-runtime"},
        },
        {
            "apiVersion": "v1",
            "kind": "ServiceAccount",
            "metadata": {
                "name": "benchflow-hostpath-runtime",
                "namespace": "benchflow",
            },
        },
        {
            "apiVersion": "rbac.authorization.k8s.io/v1",
            "kind": "ClusterRole",
            "metadata": {"name": "benchflow-hostpath-runtime"},
        },
        {
            "apiVersion": "rbac.authorization.k8s.io/v1",
            "kind": "RoleBinding",
            "metadata": {
                "name": "benchflow-hostpath-runtime-scc",
                "namespace": "benchflow",
            },
        },
    ]


class HostpathRuntimeBootstrapTest(unittest.TestCase):
    def test_openshift_keeps_full_manifest_set(self) -> None:
        documents = _sample_documents()
        selected = select_hostpath_runtime_documents(
            documents, openshift_scc_available=True
        )
        self.assertEqual([doc["kind"] for doc in selected], [d["kind"] for d in documents])
        self.assertIsNot(selected, documents)

    def test_non_openshift_keeps_only_service_account(self) -> None:
        selected = select_hostpath_runtime_documents(
            _sample_documents(), openshift_scc_available=False
        )
        self.assertEqual([doc["kind"] for doc in selected], ["ServiceAccount"])
        self.assertEqual(
            selected[0]["metadata"]["name"], "benchflow-hostpath-runtime"
        )


if __name__ == "__main__":
    unittest.main()
