from __future__ import annotations

import unittest

from benchflow.bootstrap_grafana import select_grafana_rbac_documents


def _sample_grafana_rbac() -> list[dict]:
    return [
        {
            "apiVersion": "v1",
            "kind": "ServiceAccount",
            "metadata": {"name": "benchflow-grafana", "namespace": "benchflow-grafana"},
        },
        {
            "apiVersion": "rbac.authorization.k8s.io/v1",
            "kind": "Role",
            "metadata": {
                "name": "benchflow-grafana-route-reader",
                "namespace": "benchflow-grafana",
            },
            "rules": [
                {
                    "apiGroups": ["route.openshift.io"],
                    "resources": ["routes"],
                    "verbs": ["get", "list"],
                }
            ],
        },
        {
            "apiVersion": "rbac.authorization.k8s.io/v1",
            "kind": "RoleBinding",
            "metadata": {
                "name": "benchflow-grafana-route-reader",
                "namespace": "benchflow-grafana",
            },
        },
        {
            "apiVersion": "v1",
            "kind": "Secret",
            "metadata": {"name": "grafana-token", "namespace": "benchflow-grafana"},
        },
        {
            "apiVersion": "rbac.authorization.k8s.io/v1",
            "kind": "ClusterRoleBinding",
            "metadata": {"name": "benchflow-grafana-cluster-monitoring-view-benchflow"},
            "roleRef": {
                "apiGroup": "rbac.authorization.k8s.io",
                "kind": "ClusterRole",
                "name": "cluster-monitoring-view",
            },
        },
    ]


class GrafanaBootstrapFilterTest(unittest.TestCase):
    def test_openshift_keeps_all_documents(self) -> None:
        documents = _sample_grafana_rbac()
        selected = select_grafana_rbac_documents(
            documents,
            openshift_routes_available=True,
            openshift_cluster_monitoring_available=True,
        )
        self.assertEqual(len(selected), len(documents))

    def test_cks_drops_route_and_monitoring_bindings(self) -> None:
        selected = select_grafana_rbac_documents(
            _sample_grafana_rbac(),
            openshift_routes_available=False,
            openshift_cluster_monitoring_available=False,
        )
        kinds_and_names = [
            (doc["kind"], doc["metadata"]["name"]) for doc in selected
        ]
        self.assertEqual(
            kinds_and_names,
            [
                ("ServiceAccount", "benchflow-grafana"),
                ("Secret", "grafana-token"),
            ],
        )


if __name__ == "__main__":
    unittest.main()
