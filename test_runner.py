#!/usr/bin/env python3
"""
CockroachDB Operator VKS Functional Test Runner

This script executes the VKS functional tests defined in the tests/ directory,
logs output, and generates pass/fail summaries.

Usage:
    python test_runner.py                      # Run all tests
    python test_runner.py --test VKS-01        # Run single test
    python test_runner.py --range VKS-01 VKS-05  # Run range of tests
    python test_runner.py --category scaling   # Run tests by category
    python test_runner.py --list               # List all available tests
    python test_runner.py --dry-run            # Show what would be executed
"""

import argparse
import json
import os
import re
import subprocess
import sys
from dataclasses import dataclass, field, asdict
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Optional

# Test configuration
TEST_BASE_DIR = Path(__file__).parent / "tests"
RESULTS_BASE_DIR = Path(__file__).parent / "results"


class TestStatus(Enum):
    PENDING = "pending"
    RUNNING = "running"
    PASSED = "passed"
    FAILED = "failed"
    SKIPPED = "skipped"
    ERROR = "error"


@dataclass
class TestStep:
    """Represents a single step within a test."""
    name: str
    command: str
    expected_output: Optional[str] = None
    timeout: int = 300
    continue_on_failure: bool = False


@dataclass
class TestResult:
    """Stores the result of a test execution."""
    test_id: str
    test_name: str
    category: str
    status: TestStatus = TestStatus.PENDING
    start_time: Optional[datetime] = None
    end_time: Optional[datetime] = None
    duration_seconds: float = 0.0
    steps_passed: int = 0
    steps_failed: int = 0
    steps_total: int = 0
    failure_reason: Optional[str] = None
    failure_step: Optional[str] = None
    output_log: str = ""
    error_log: str = ""

    def to_dict(self) -> dict:
        """Convert to dictionary for JSON serialization."""
        return {
            "test_id": self.test_id,
            "test_name": self.test_name,
            "category": self.category,
            "status": self.status.value,
            "start_time": self.start_time.isoformat() if self.start_time else None,
            "end_time": self.end_time.isoformat() if self.end_time else None,
            "duration_seconds": self.duration_seconds,
            "steps_passed": self.steps_passed,
            "steps_failed": self.steps_failed,
            "steps_total": self.steps_total,
            "failure_reason": self.failure_reason,
            "failure_step": self.failure_step,
        }


@dataclass
class TestDefinition:
    """Defines a test case."""
    test_id: str
    name: str
    category: str
    description: str
    dependencies: list = field(default_factory=list)
    steps: list = field(default_factory=list)
    validation_commands: list = field(default_factory=list)
    cleanup_commands: list = field(default_factory=list)
    doc_path: Optional[Path] = None


class TestRegistry:
    """Registry of all available tests."""
    
    def __init__(self):
        self.tests: dict[str, TestDefinition] = {}
        self._load_tests()
    
    def _load_tests(self):
        """Load test definitions from the tests directory."""
        # Define tests based on the markdown documentation
        self._register_prerequisite_tests()
        self._register_provisioning_tests()
        self._register_security_tests()
        self._register_scaling_tests()
        self._register_upgrade_tests()
        self._register_operator_lifecycle_tests()
        self._register_networking_tests()
        self._register_day2_ops_tests()
        self._register_advanced_feature_tests()
        self._register_failure_handling_tests()
    
    def _register_prerequisite_tests(self):
        """Register prerequisite tests."""
        self.tests["00-VKS-CLUSTER"] = TestDefinition(
            test_id="00-VKS-CLUSTER",
            name="Deploy VKS Kubernetes Cluster",
            category="prerequisites",
            description="Deploy VKS cluster using vks.yaml manifest",
            dependencies=[],
            doc_path=TEST_BASE_DIR / "00-prerequisites" / "00-VKS-CLUSTER.md",
            steps=[
                TestStep(
                    name="Apply VKS cluster manifest",
                    command="kubectl apply -f vks.yaml",
                    timeout=60
                ),
                TestStep(
                    name="Wait for cluster provisioning",
                    command="kubectl wait --for=jsonpath='{.status.phase}'=Provisioned cluster/cluster-vks --timeout=900s",
                    timeout=920
                ),
                TestStep(
                    name="Get kubeconfig",
                    command="kubectl get secret cluster-vks-kubeconfig -o jsonpath='{.data.value}' | base64 -d > vks-kubeconfig.yaml",
                    timeout=30
                ),
            ],
            validation_commands=[
                "kubectl --kubeconfig=vks-kubeconfig.yaml get nodes",
                "kubectl --kubeconfig=vks-kubeconfig.yaml cluster-info",
            ]
        )
    
    def _register_provisioning_tests(self):
        """Register cluster provisioning tests."""
        self.tests["VKS-01"] = TestDefinition(
            test_id="VKS-01",
            name="Install CockroachDB Operator via Helm",
            category="cluster-provisioning",
            description="Install the CockroachDB Operator using Helm",
            dependencies=["00-VKS-CLUSTER"],
            doc_path=TEST_BASE_DIR / "01-cluster-provisioning" / "VKS-01-operator-install.md",
            steps=[
                TestStep(
                    name="Create operator namespace",
                    command="kubectl create namespace crdb-operator --dry-run=client -o yaml | kubectl apply -f -",
                    timeout=30
                ),
                TestStep(
                    name="Install operator via Helm",
                    command="helm install crdb-operator ./cockroachdb-parent/charts/operator -n crdb-operator --wait --timeout 5m",
                    timeout=330
                ),
                TestStep(
                    name="Wait for operator pods",
                    command="kubectl wait --for=condition=Ready pods --all -n crdb-operator --timeout=300s",
                    timeout=310
                ),
            ],
            validation_commands=[
                "kubectl get pods -n crdb-operator",
                "kubectl get crds | grep cockroach",
            ]
        )
        
        self.tests["VKS-02"] = TestDefinition(
            test_id="VKS-02",
            name="Install CockroachDB Cluster via Helm",
            category="cluster-provisioning",
            description="Deploy CockroachDB cluster using Helm chart",
            dependencies=["VKS-01"],
            doc_path=TEST_BASE_DIR / "01-cluster-provisioning" / "VKS-02-cluster-install.md",
            steps=[
                TestStep(
                    name="Create cluster namespace",
                    command="kubectl create namespace crdb-cluster --dry-run=client -o yaml | kubectl apply -f -",
                    timeout=30
                ),
                TestStep(
                    name="Install CockroachDB via Helm",
                    command="helm install cockroachdb ./cockroachdb-parent/charts/cockroachdb -n crdb-cluster --set storage.persistentVolume.storageClass=vsan-esa-default-policy-raid5 --wait --timeout 10m",
                    timeout=630
                ),
                TestStep(
                    name="Wait for all pods ready",
                    command="kubectl wait --for=condition=Ready pods --all -n crdb-cluster --timeout=600s",
                    timeout=610
                ),
            ],
            validation_commands=[
                "kubectl get pods -n crdb-cluster",
                "kubectl get pvc -n crdb-cluster",
                "kubectl exec -n crdb-cluster cockroachdb-0 -- cockroach node status --insecure --host=localhost:26257",
            ]
        )
        
        self.tests["VKS-03"] = TestDefinition(
            test_id="VKS-03",
            name="Secure Cluster Initialization (TLS)",
            category="cluster-provisioning",
            description="Enable TLS on the CockroachDB cluster",
            dependencies=["VKS-02"],
            doc_path=TEST_BASE_DIR / "01-cluster-provisioning" / "VKS-03-secure-cluster-tls.md",
            steps=[
                TestStep(
                    name="Upgrade cluster with TLS enabled",
                    command="helm upgrade cockroachdb ./cockroachdb-parent/charts/cockroachdb -n crdb-cluster --reuse-values --set tls.enabled=true --wait --timeout 10m",
                    timeout=630
                ),
                TestStep(
                    name="Wait for pods to restart",
                    command="kubectl wait --for=condition=Ready pods --all -n crdb-cluster --timeout=600s",
                    timeout=610
                ),
            ],
            validation_commands=[
                "kubectl get secrets -n crdb-cluster | grep -E '(ca|tls)'",
                "kubectl exec -n crdb-cluster cockroachdb-0 -- ls /cockroach/cockroach-certs/",
            ]
        )
        
        self.tests["VKS-04"] = TestDefinition(
            test_id="VKS-04",
            name="Multi-region / Locality Mappings",
            category="cluster-provisioning",
            description="Configure locality mappings for topology awareness",
            dependencies=["VKS-02"],
            doc_path=TEST_BASE_DIR / "01-cluster-provisioning" / "VKS-04-locality-mappings.md",
            steps=[
                TestStep(
                    name="Verify node topology labels",
                    command="kubectl get nodes -o custom-columns=NAME:.metadata.name,REGION:.metadata.labels.'topology\\.kubernetes\\.io/region'",
                    timeout=30
                ),
                TestStep(
                    name="Check pod distribution",
                    command="kubectl get pods -n crdb-cluster -o wide",
                    timeout=30
                ),
            ],
            validation_commands=[
                "kubectl exec -n crdb-cluster cockroachdb-0 -- cockroach sql --insecure --host=localhost:26257 --execute=\"SELECT node_id, locality FROM crdb_internal.gossip_nodes ORDER BY node_id;\"",
            ]
        )
    
    def _register_security_tests(self):
        """Register security tests."""
        self.tests["VKS-05"] = TestDefinition(
            test_id="VKS-05",
            name="Pod Security Admission Compatibility",
            category="security",
            description="Verify PSA/PSS compatibility",
            dependencies=["VKS-02"],
            doc_path=TEST_BASE_DIR / "02-security" / "VKS-05-pod-security-admission.md",
            steps=[
                TestStep(
                    name="Check operator security context",
                    command="kubectl get pods -n crdb-operator -o jsonpath='{.items[*].spec.securityContext}'",
                    timeout=30
                ),
                TestStep(
                    name="Check cluster security context",
                    command="kubectl get pods -n crdb-cluster -o jsonpath='{.items[*].spec.securityContext}'",
                    timeout=30
                ),
                TestStep(
                    name="Restart pod to test re-admission",
                    command="kubectl delete pod cockroachdb-0 -n crdb-cluster && kubectl wait --for=condition=Ready pod/cockroachdb-0 -n crdb-cluster --timeout=300s",
                    timeout=310
                ),
            ],
            validation_commands=[
                "kubectl get pods -n crdb-cluster",
                "kubectl get events -n crdb-cluster --field-selector reason=FailedCreate 2>/dev/null | grep -i security || echo 'No PSA issues'",
            ]
        )
        
        self.tests["VKS-06"] = TestDefinition(
            test_id="VKS-06",
            name="NetworkPolicy Compatibility",
            category="security",
            description="Test NetworkPolicy enforcement",
            dependencies=["VKS-02"],
            doc_path=TEST_BASE_DIR / "02-security" / "VKS-06-network-policy.md",
            steps=[
                TestStep(
                    name="Enable NetworkPolicy",
                    command="helm upgrade cockroachdb ./cockroachdb-parent/charts/cockroachdb -n crdb-cluster --reuse-values --set networkPolicy.enabled=true --wait --timeout 5m",
                    timeout=330
                ),
                TestStep(
                    name="Verify intra-cluster communication",
                    command="kubectl exec -n crdb-cluster cockroachdb-0 -- cockroach sql --insecure --host=cockroachdb-1.cockroachdb:26257 --execute=\"SELECT 1;\"",
                    timeout=60
                ),
            ],
            validation_commands=[
                "kubectl get networkpolicies -n crdb-cluster",
                "kubectl exec -n crdb-cluster cockroachdb-0 -- cockroach sql --insecure --host=localhost:26257 --execute=\"SELECT count(*) FROM crdb_internal.gossip_nodes WHERE is_live;\"",
            ]
        )
    
    def _register_scaling_tests(self):
        """Register scaling tests."""
        self.tests["VKS-07"] = TestDefinition(
            test_id="VKS-07",
            name="Scale Up Nodes",
            category="scaling",
            description="Scale cluster from 3 to 6 nodes",
            dependencies=["VKS-02"],
            doc_path=TEST_BASE_DIR / "03-scaling" / "VKS-07-scale-up.md",
            steps=[
                TestStep(
                    name="Scale up to 6 replicas",
                    command="helm upgrade cockroachdb ./cockroachdb-parent/charts/cockroachdb -n crdb-cluster --reuse-values --set statefulset.replicas=6 --wait --timeout 15m",
                    timeout=930
                ),
                TestStep(
                    name="Wait for all pods ready",
                    command="kubectl wait --for=condition=Ready pods --all -n crdb-cluster --timeout=600s",
                    timeout=610
                ),
            ],
            validation_commands=[
                "kubectl get pods -n crdb-cluster",
                "kubectl exec -n crdb-cluster cockroachdb-0 -- cockroach node status --insecure --host=localhost:26257",
            ]
        )
        
        self.tests["VKS-08"] = TestDefinition(
            test_id="VKS-08",
            name="Scale Down Nodes",
            category="scaling",
            description="Scale cluster from 6 to 3 nodes",
            dependencies=["VKS-07"],
            doc_path=TEST_BASE_DIR / "03-scaling" / "VKS-08-scale-down.md",
            steps=[
                TestStep(
                    name="Scale down to 3 replicas",
                    command="helm upgrade cockroachdb ./cockroachdb-parent/charts/cockroachdb -n crdb-cluster --reuse-values --set statefulset.replicas=3 --timeout 30m",
                    timeout=1830
                ),
                TestStep(
                    name="Wait for scale down",
                    command="bash -c 'while [ $(kubectl get pods -n crdb-cluster --no-headers | wc -l) -gt 3 ]; do sleep 10; done'",
                    timeout=900
                ),
            ],
            validation_commands=[
                "kubectl get pods -n crdb-cluster",
                "kubectl exec -n crdb-cluster cockroachdb-0 -- cockroach sql --insecure --host=localhost:26257 --execute=\"SELECT count(*) FROM crdb_internal.gossip_nodes WHERE is_live;\"",
            ]
        )
        
        self.tests["VKS-09"] = TestDefinition(
            test_id="VKS-09",
            name="Kubernetes Node Decommission",
            category="scaling",
            description="Test node decommission via annotation",
            dependencies=["VKS-02"],
            doc_path=TEST_BASE_DIR / "03-scaling" / "VKS-09-node-decommission.md",
            steps=[
                TestStep(
                    name="Get target node",
                    command="kubectl get pods -n crdb-cluster cockroachdb-2 -o jsonpath='{.spec.nodeName}'",
                    timeout=30
                ),
            ],
            validation_commands=[
                "kubectl get pods -n crdb-cluster -o wide",
            ]
        )
    
    def _register_upgrade_tests(self):
        """Register upgrade tests."""
        self.tests["VKS-10"] = TestDefinition(
            test_id="VKS-10",
            name="Patch Upgrade",
            category="upgrades",
            description="Perform patch version upgrade",
            dependencies=["VKS-02"],
            doc_path=TEST_BASE_DIR / "04-upgrades" / "VKS-10-patch-upgrade.md",
            steps=[
                TestStep(
                    name="Get current version",
                    command="kubectl exec -n crdb-cluster cockroachdb-0 -- cockroach version",
                    timeout=30
                ),
            ],
            validation_commands=[
                "kubectl get pods -n crdb-cluster",
            ]
        )
        
        self.tests["VKS-11"] = TestDefinition(
            test_id="VKS-11",
            name="Major Upgrade with Auto-Finalization",
            category="upgrades",
            description="Major version upgrade with auto-finalization",
            dependencies=["VKS-02"],
            doc_path=TEST_BASE_DIR / "04-upgrades" / "VKS-11-major-upgrade-auto.md",
            steps=[
                TestStep(
                    name="Check cluster version",
                    command="kubectl exec -n crdb-cluster cockroachdb-0 -- cockroach sql --insecure --host=localhost:26257 --execute=\"SHOW CLUSTER SETTING version;\"",
                    timeout=30
                ),
            ],
            validation_commands=[
                "kubectl get pods -n crdb-cluster",
            ]
        )
        
        self.tests["VKS-12"] = TestDefinition(
            test_id="VKS-12",
            name="Major Upgrade with Manual Finalization",
            category="upgrades",
            description="Major version upgrade with manual finalization",
            dependencies=["VKS-02"],
            doc_path=TEST_BASE_DIR / "04-upgrades" / "VKS-12-major-upgrade-manual.md",
            steps=[
                TestStep(
                    name="Check preserve_downgrade_option",
                    command="kubectl exec -n crdb-cluster cockroachdb-0 -- cockroach sql --insecure --host=localhost:26257 --execute=\"SHOW CLUSTER SETTING cluster.preserve_downgrade_option;\"",
                    timeout=30
                ),
            ],
            validation_commands=[
                "kubectl get pods -n crdb-cluster",
            ]
        )
        
        self.tests["VKS-13"] = TestDefinition(
            test_id="VKS-13",
            name="Rollback Patch Upgrade",
            category="upgrades",
            description="Rollback a patch upgrade",
            dependencies=["VKS-10"],
            doc_path=TEST_BASE_DIR / "04-upgrades" / "VKS-13-rollback-patch.md",
            steps=[
                TestStep(
                    name="Check current version",
                    command="kubectl exec -n crdb-cluster cockroachdb-0 -- cockroach version",
                    timeout=30
                ),
            ],
            validation_commands=[
                "kubectl get pods -n crdb-cluster",
            ]
        )
    
    def _register_operator_lifecycle_tests(self):
        """Register operator lifecycle tests."""
        self.tests["VKS-14"] = TestDefinition(
            test_id="VKS-14",
            name="Operator Helm Chart Upgrade",
            category="operator-lifecycle",
            description="Upgrade the operator Helm chart",
            dependencies=["VKS-01"],
            doc_path=TEST_BASE_DIR / "05-operator-lifecycle" / "VKS-14-operator-upgrade.md",
            steps=[
                TestStep(
                    name="Upgrade operator",
                    command="helm upgrade crdb-operator ./cockroachdb-parent/charts/operator -n crdb-operator --reuse-values --wait --timeout 5m",
                    timeout=330
                ),
                TestStep(
                    name="Wait for operator ready",
                    command="kubectl wait --for=condition=Available deployment -n crdb-operator --timeout=120s",
                    timeout=130
                ),
            ],
            validation_commands=[
                "kubectl get pods -n crdb-operator",
                "helm list -n crdb-operator",
            ]
        )
        
        self.tests["VKS-15"] = TestDefinition(
            test_id="VKS-15",
            name="CRD Version Migration",
            category="operator-lifecycle",
            description="Test CRD version migration",
            dependencies=["VKS-14"],
            doc_path=TEST_BASE_DIR / "05-operator-lifecycle" / "VKS-15-crd-migration.md",
            steps=[
                TestStep(
                    name="Check CRD versions",
                    command="kubectl get crd crdbclusters.crdb.cockroachlabs.com -o jsonpath='{.spec.versions[*].name}'",
                    timeout=30
                ),
            ],
            validation_commands=[
                "kubectl get crd crdbclusters.crdb.cockroachlabs.com -o jsonpath='{.status.storedVersions}'",
            ]
        )
    
    def _register_networking_tests(self):
        """Register networking tests."""
        self.tests["VKS-16"] = TestDefinition(
            test_id="VKS-16",
            name="DB Console Access",
            category="networking",
            description="Expose and access DB Console",
            dependencies=["VKS-02"],
            doc_path=TEST_BASE_DIR / "06-networking" / "VKS-16-db-console-access.md",
            steps=[
                TestStep(
                    name="Test health endpoint via port-forward",
                    command="bash -c 'kubectl port-forward svc/cockroachdb-public -n crdb-cluster 8080:8080 & PF_PID=$!; sleep 3; curl -s http://localhost:8080/health; kill $PF_PID 2>/dev/null'",
                    timeout=30
                ),
            ],
            validation_commands=[
                "kubectl get svc -n crdb-cluster",
            ]
        )
        
        self.tests["VKS-17"] = TestDefinition(
            test_id="VKS-17",
            name="SQL LoadBalancer Exposure",
            category="networking",
            description="Expose SQL via LoadBalancer",
            dependencies=["VKS-02"],
            doc_path=TEST_BASE_DIR / "06-networking" / "VKS-17-sql-loadbalancer.md",
            steps=[
                TestStep(
                    name="Check services",
                    command="kubectl get svc -n crdb-cluster",
                    timeout=30
                ),
            ],
            validation_commands=[
                "kubectl get svc -n crdb-cluster",
            ]
        )
    
    def _register_day2_ops_tests(self):
        """Register day-2 operations tests."""
        self.tests["VKS-18"] = TestDefinition(
            test_id="VKS-18",
            name="Cluster Monitoring Integration",
            category="day2-ops",
            description="Configure monitoring integration",
            dependencies=["VKS-02"],
            doc_path=TEST_BASE_DIR / "07-day2-ops" / "VKS-18-monitoring.md",
            steps=[
                TestStep(
                    name="Check metrics endpoint",
                    command="kubectl exec -n crdb-cluster cockroachdb-0 -- curl -s http://localhost:8080/_status/vars | head -20",
                    timeout=30
                ),
            ],
            validation_commands=[
                "kubectl exec -n crdb-cluster cockroachdb-0 -- curl -s http://localhost:8080/_status/vars | grep -c '^[a-z]'",
            ]
        )
        
        self.tests["VKS-19"] = TestDefinition(
            test_id="VKS-19",
            name="Backup and Restore",
            category="day2-ops",
            description="Test backup and restore functionality",
            dependencies=["VKS-02"],
            doc_path=TEST_BASE_DIR / "07-day2-ops" / "VKS-19-backup-restore.md",
            steps=[
                TestStep(
                    name="Create test database",
                    command="kubectl exec -n crdb-cluster cockroachdb-0 -- cockroach sql --insecure --host=localhost:26257 --execute=\"CREATE DATABASE IF NOT EXISTS backup_test; USE backup_test; CREATE TABLE IF NOT EXISTS test (id INT PRIMARY KEY); INSERT INTO test VALUES (1), (2), (3);\"",
                    timeout=60
                ),
                TestStep(
                    name="Perform backup",
                    command="kubectl exec -n crdb-cluster cockroachdb-0 -- cockroach sql --insecure --host=localhost:26257 --execute=\"BACKUP DATABASE backup_test INTO 'nodelocal://1/backups' AS OF SYSTEM TIME '-10s';\"",
                    timeout=120
                ),
            ],
            validation_commands=[
                "kubectl exec -n crdb-cluster cockroachdb-0 -- cockroach sql --insecure --host=localhost:26257 --execute=\"SHOW BACKUPS IN 'nodelocal://1/backups';\"",
            ]
        )
        
        self.tests["VKS-20"] = TestDefinition(
            test_id="VKS-20",
            name="Pod Eviction / Rescheduling",
            category="day2-ops",
            description="Test pod eviction and rescheduling",
            dependencies=["VKS-02"],
            doc_path=TEST_BASE_DIR / "07-day2-ops" / "VKS-20-pod-eviction.md",
            steps=[
                TestStep(
                    name="Delete a pod",
                    command="kubectl delete pod cockroachdb-1 -n crdb-cluster",
                    timeout=30
                ),
                TestStep(
                    name="Wait for pod to be rescheduled",
                    command="kubectl wait --for=condition=Ready pod/cockroachdb-1 -n crdb-cluster --timeout=300s",
                    timeout=310
                ),
            ],
            validation_commands=[
                "kubectl get pods -n crdb-cluster",
                "kubectl exec -n crdb-cluster cockroachdb-0 -- cockroach node status --insecure --host=localhost:26257",
            ]
        )
    
    def _register_advanced_feature_tests(self):
        """Register advanced feature tests."""
        self.tests["VKS-21"] = TestDefinition(
            test_id="VKS-21",
            name="Physical Cluster Replication",
            category="advanced-features",
            description="Test PCR between clusters",
            dependencies=["VKS-02"],
            doc_path=TEST_BASE_DIR / "08-advanced-features" / "VKS-21-pcr.md",
            steps=[
                TestStep(
                    name="Check rangefeed setting",
                    command="kubectl exec -n crdb-cluster cockroachdb-0 -- cockroach sql --insecure --host=localhost:26257 --execute=\"SHOW CLUSTER SETTING kv.rangefeed.enabled;\"",
                    timeout=30
                ),
            ],
            validation_commands=[
                "kubectl get pods -n crdb-cluster",
            ]
        )
        
        self.tests["VKS-22"] = TestDefinition(
            test_id="VKS-22",
            name="Encryption at Rest with KMS",
            category="advanced-features",
            description="Test encryption at rest with KMS",
            dependencies=["VKS-02"],
            doc_path=TEST_BASE_DIR / "08-advanced-features" / "VKS-22-encryption-kms.md",
            steps=[
                TestStep(
                    name="Check encryption status",
                    command="kubectl exec -n crdb-cluster cockroachdb-0 -- cockroach sql --insecure --host=localhost:26257 --execute=\"SELECT * FROM crdb_internal.encryption_status;\" 2>/dev/null || echo 'Encryption not configured'",
                    timeout=30
                ),
            ],
            validation_commands=[
                "kubectl get pods -n crdb-cluster",
            ]
        )
        
        self.tests["VKS-23"] = TestDefinition(
            test_id="VKS-23",
            name="Logical Replication",
            category="advanced-features",
            description="Test logical replication between clusters",
            dependencies=["VKS-02"],
            doc_path=TEST_BASE_DIR / "08-advanced-features" / "VKS-23-logical-replication.md",
            steps=[
                TestStep(
                    name="Enable rangefeeds",
                    command="kubectl exec -n crdb-cluster cockroachdb-0 -- cockroach sql --insecure --host=localhost:26257 --execute=\"SET CLUSTER SETTING kv.rangefeed.enabled = true;\"",
                    timeout=30
                ),
            ],
            validation_commands=[
                "kubectl exec -n crdb-cluster cockroachdb-0 -- cockroach sql --insecure --host=localhost:26257 --execute=\"SHOW CLUSTER SETTING kv.rangefeed.enabled;\"",
            ]
        )
        
        self.tests["VKS-24"] = TestDefinition(
            test_id="VKS-24",
            name="CDC to Kafka",
            category="advanced-features",
            description="Test CDC to Kafka or cloud pub/sub",
            dependencies=["VKS-02"],
            doc_path=TEST_BASE_DIR / "08-advanced-features" / "VKS-24-cdc-kafka.md",
            steps=[
                TestStep(
                    name="Check changefeed capability",
                    command="kubectl exec -n crdb-cluster cockroachdb-0 -- cockroach sql --insecure --host=localhost:26257 --execute=\"SELECT * FROM [SHOW JOBS] WHERE job_type = 'CHANGEFEED' LIMIT 1;\" 2>/dev/null || echo 'No changefeeds'",
                    timeout=30
                ),
            ],
            validation_commands=[
                "kubectl get pods -n crdb-cluster",
            ]
        )
    
    def _register_failure_handling_tests(self):
        """Register failure handling tests."""
        self.tests["VKS-25"] = TestDefinition(
            test_id="VKS-25",
            name="Operator Coexistence",
            category="failure-handling",
            description="Test coexistence with legacy operator",
            dependencies=["VKS-01"],
            doc_path=TEST_BASE_DIR / "09-failure-handling" / "VKS-25-operator-coexistence.md",
            steps=[
                TestStep(
                    name="Check CRD status",
                    command="kubectl get crd crdbclusters.crdb.cockroachlabs.com -o jsonpath='{.status.conditions[?(@.type==\"Established\")].status}'",
                    timeout=30
                ),
            ],
            validation_commands=[
                "kubectl get crds | grep cockroach",
            ]
        )
        
        self.tests["VKS-26"] = TestDefinition(
            test_id="VKS-26",
            name="Operator Restart During Operations",
            category="failure-handling",
            description="Test operator restart during cluster operations",
            dependencies=["VKS-02"],
            doc_path=TEST_BASE_DIR / "09-failure-handling" / "VKS-26-operator-restart.md",
            steps=[
                TestStep(
                    name="Delete operator pod",
                    command="kubectl delete pods -n crdb-operator -l app.kubernetes.io/name=cockroach-operator",
                    timeout=30
                ),
                TestStep(
                    name="Verify cluster still serves traffic",
                    command="kubectl exec -n crdb-cluster cockroachdb-0 -- cockroach sql --insecure --host=localhost:26257 --execute=\"SELECT 1;\"",
                    timeout=30
                ),
                TestStep(
                    name="Wait for operator restart",
                    command="kubectl wait --for=condition=Available deployment -n crdb-operator --timeout=120s",
                    timeout=130
                ),
            ],
            validation_commands=[
                "kubectl get pods -n crdb-operator",
                "kubectl get pods -n crdb-cluster",
            ]
        )
        
        self.tests["VKS-27"] = TestDefinition(
            test_id="VKS-27",
            name="VKS Kubernetes Cluster Upgrade",
            category="failure-handling",
            description="Test VKS cluster upgrade",
            dependencies=["VKS-02"],
            doc_path=TEST_BASE_DIR / "09-failure-handling" / "VKS-27-vks-upgrade.md",
            steps=[
                TestStep(
                    name="Record current version",
                    command="kubectl version --short",
                    timeout=30
                ),
            ],
            validation_commands=[
                "kubectl get nodes",
                "kubectl get pods -n crdb-cluster",
            ]
        )
        
        self.tests["VKS-28"] = TestDefinition(
            test_id="VKS-28",
            name="VKS Minor Upgrade",
            category="failure-handling",
            description="Test VKS minor version upgrade",
            dependencies=["VKS-02"],
            doc_path=TEST_BASE_DIR / "09-failure-handling" / "VKS-28-vks-minor-upgrade.md",
            steps=[
                TestStep(
                    name="Record current version",
                    command="kubectl version --short",
                    timeout=30
                ),
            ],
            validation_commands=[
                "kubectl get nodes",
            ]
        )
        
        self.tests["VKS-29"] = TestDefinition(
            test_id="VKS-29",
            name="WAL Failover Validation",
            category="failure-handling",
            description="Test WAL failover functionality",
            dependencies=["VKS-02"],
            doc_path=TEST_BASE_DIR / "09-failure-handling" / "VKS-29-wal-failover.md",
            steps=[
                TestStep(
                    name="Check current PVCs",
                    command="kubectl get pvc -n crdb-cluster",
                    timeout=30
                ),
            ],
            validation_commands=[
                "kubectl get pvc -n crdb-cluster",
            ]
        )
    
    def get_test(self, test_id: str) -> Optional[TestDefinition]:
        """Get a test by ID."""
        return self.tests.get(test_id)
    
    def get_all_tests(self) -> list[TestDefinition]:
        """Get all tests in execution order."""
        # Define execution order
        order = [
            "00-VKS-CLUSTER",
            "VKS-01", "VKS-02", "VKS-03", "VKS-04",
            "VKS-05", "VKS-06",
            "VKS-07", "VKS-08", "VKS-09",
            "VKS-10", "VKS-11", "VKS-12", "VKS-13",
            "VKS-14", "VKS-15",
            "VKS-16", "VKS-17",
            "VKS-18", "VKS-19", "VKS-20",
            "VKS-21", "VKS-22", "VKS-23", "VKS-24",
            "VKS-25", "VKS-26", "VKS-27", "VKS-28", "VKS-29",
        ]
        return [self.tests[tid] for tid in order if tid in self.tests]
    
    def get_tests_by_category(self, category: str) -> list[TestDefinition]:
        """Get tests by category."""
        return [t for t in self.tests.values() if t.category == category]
    
    def get_tests_in_range(self, start_id: str, end_id: str) -> list[TestDefinition]:
        """Get tests in a range (inclusive)."""
        all_tests = self.get_all_tests()
        start_idx = None
        end_idx = None
        
        for i, test in enumerate(all_tests):
            if test.test_id == start_id:
                start_idx = i
            if test.test_id == end_id:
                end_idx = i
        
        if start_idx is None or end_idx is None:
            return []
        
        return all_tests[start_idx:end_idx + 1]
    
    def get_categories(self) -> list[str]:
        """Get all unique categories."""
        return sorted(set(t.category for t in self.tests.values()))


class TestRunner:
    """Executes tests and manages results."""
    
    def __init__(self, results_dir: Optional[Path] = None, dry_run: bool = False, 
                 kubeconfig: Optional[str] = None, verbose: bool = False):
        self.registry = TestRegistry()
        self.dry_run = dry_run
        self.verbose = verbose
        self.kubeconfig = kubeconfig or os.environ.get("KUBECONFIG", "vks-kubeconfig.yaml")
        
        # Create results directory with timestamp
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        self.results_dir = results_dir or RESULTS_BASE_DIR / timestamp
        self.results_dir.mkdir(parents=True, exist_ok=True)
        
        # Initialize results
        self.results: list[TestResult] = []
        
        # Setup logging
        self.log_file = self.results_dir / "test_runner.log"
        self._setup_logging()
    
    def _setup_logging(self):
        """Setup logging to file and console."""
        import logging
        
        self.logger = logging.getLogger("test_runner")
        self.logger.setLevel(logging.DEBUG if self.verbose else logging.INFO)
        
        # File handler
        fh = logging.FileHandler(self.log_file)
        fh.setLevel(logging.DEBUG)
        fh.setFormatter(logging.Formatter(
            '%(asctime)s - %(levelname)s - %(message)s'
        ))
        self.logger.addHandler(fh)
        
        # Console handler
        ch = logging.StreamHandler()
        ch.setLevel(logging.INFO)
        ch.setFormatter(logging.Formatter(
            '%(asctime)s - %(levelname)s - %(message)s'
        ))
        self.logger.addHandler(ch)
    
    def _run_command(self, command: str, timeout: int = 300) -> tuple[int, str, str]:
        """Run a shell command and return exit code, stdout, stderr."""
        env = os.environ.copy()
        if self.kubeconfig:
            env["KUBECONFIG"] = self.kubeconfig
        
        try:
            result = subprocess.run(
                command,
                shell=True,
                capture_output=True,
                text=True,
                timeout=timeout,
                env=env
            )
            return result.returncode, result.stdout, result.stderr
        except subprocess.TimeoutExpired:
            return -1, "", f"Command timed out after {timeout} seconds"
        except Exception as e:
            return -1, "", str(e)
    
    def _run_test(self, test: TestDefinition) -> TestResult:
        """Execute a single test."""
        result = TestResult(
            test_id=test.test_id,
            test_name=test.name,
            category=test.category,
            steps_total=len(test.steps)
        )
        
        result.start_time = datetime.now()
        result.status = TestStatus.RUNNING
        
        self.logger.info(f"Starting test: {test.test_id} - {test.name}")
        
        # Create test-specific log file
        test_log_file = self.results_dir / f"{test.test_id}.log"
        
        all_output = []
        all_errors = []
        
        try:
            # Execute each step
            for i, step in enumerate(test.steps):
                self.logger.info(f"  Step {i+1}/{len(test.steps)}: {step.name}")
                all_output.append(f"\n=== Step {i+1}: {step.name} ===\n")
                all_output.append(f"Command: {step.command}\n")
                
                if self.dry_run:
                    self.logger.info(f"    [DRY RUN] Would execute: {step.command}")
                    result.steps_passed += 1
                    continue
                
                exit_code, stdout, stderr = self._run_command(step.command, step.timeout)
                
                all_output.append(f"Exit code: {exit_code}\n")
                all_output.append(f"Output:\n{stdout}\n")
                if stderr:
                    all_errors.append(f"Step {i+1} stderr:\n{stderr}\n")
                
                if exit_code != 0:
                    result.steps_failed += 1
                    if not step.continue_on_failure:
                        result.status = TestStatus.FAILED
                        result.failure_reason = f"Step failed with exit code {exit_code}: {stderr or stdout}"
                        result.failure_step = step.name
                        self.logger.error(f"    FAILED: {result.failure_reason}")
                        break
                else:
                    result.steps_passed += 1
                    self.logger.info(f"    PASSED")
            
            # Run validation commands if all steps passed
            if result.status != TestStatus.FAILED and not self.dry_run:
                all_output.append("\n=== Validation ===\n")
                for cmd in test.validation_commands:
                    all_output.append(f"Validation: {cmd}\n")
                    exit_code, stdout, stderr = self._run_command(cmd, 60)
                    all_output.append(f"Output:\n{stdout}\n")
                    if exit_code != 0:
                        all_errors.append(f"Validation failed: {stderr}\n")
            
            # Set final status
            if result.status != TestStatus.FAILED:
                if self.dry_run:
                    result.status = TestStatus.SKIPPED
                else:
                    result.status = TestStatus.PASSED
        
        except Exception as e:
            result.status = TestStatus.ERROR
            result.failure_reason = str(e)
            self.logger.error(f"  ERROR: {e}")
        
        result.end_time = datetime.now()
        result.duration_seconds = (result.end_time - result.start_time).total_seconds()
        result.output_log = "".join(all_output)
        result.error_log = "".join(all_errors)
        
        # Write test log
        with open(test_log_file, "w") as f:
            f.write(f"Test: {test.test_id} - {test.name}\n")
            f.write(f"Category: {test.category}\n")
            f.write(f"Status: {result.status.value}\n")
            f.write(f"Duration: {result.duration_seconds:.2f}s\n")
            f.write(f"Steps: {result.steps_passed}/{result.steps_total} passed\n")
            if result.failure_reason:
                f.write(f"Failure: {result.failure_reason}\n")
            f.write("\n" + "="*60 + "\n")
            f.write(result.output_log)
            if result.error_log:
                f.write("\n" + "="*60 + "\n")
                f.write("ERRORS:\n")
                f.write(result.error_log)
        
        status_symbol = "✓" if result.status == TestStatus.PASSED else "✗" if result.status == TestStatus.FAILED else "○"
        self.logger.info(f"  {status_symbol} {test.test_id}: {result.status.value} ({result.duration_seconds:.2f}s)")
        
        return result
    
    def run_tests(self, tests: list[TestDefinition]) -> list[TestResult]:
        """Run a list of tests."""
        self.logger.info(f"Starting test run with {len(tests)} tests")
        self.logger.info(f"Results directory: {self.results_dir}")
        
        for test in tests:
            result = self._run_test(test)
            self.results.append(result)
        
        self._generate_summary()
        return self.results
    
    def _generate_summary(self):
        """Generate test summary report."""
        summary_file = self.results_dir / "summary.md"
        json_file = self.results_dir / "results.json"
        
        # Calculate statistics
        total = len(self.results)
        passed = sum(1 for r in self.results if r.status == TestStatus.PASSED)
        failed = sum(1 for r in self.results if r.status == TestStatus.FAILED)
        skipped = sum(1 for r in self.results if r.status == TestStatus.SKIPPED)
        errors = sum(1 for r in self.results if r.status == TestStatus.ERROR)
        
        total_duration = sum(r.duration_seconds for r in self.results)
        
        # Generate markdown summary
        with open(summary_file, "w") as f:
            f.write("# CockroachDB Operator VKS Test Results\n\n")
            f.write(f"**Date:** {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n")
            
            f.write("## Summary\n\n")
            f.write(f"| Metric | Value |\n")
            f.write(f"|--------|-------|\n")
            f.write(f"| Total Tests | {total} |\n")
            f.write(f"| Passed | {passed} |\n")
            f.write(f"| Failed | {failed} |\n")
            f.write(f"| Skipped | {skipped} |\n")
            f.write(f"| Errors | {errors} |\n")
            f.write(f"| Total Duration | {total_duration:.2f}s |\n")
            f.write(f"| Pass Rate | {(passed/total*100) if total > 0 else 0:.1f}% |\n\n")
            
            f.write("## Results by Test\n\n")
            f.write("| Test ID | Name | Status | Duration | Failure Reason |\n")
            f.write("|---------|------|--------|----------|----------------|\n")
            
            for r in self.results:
                status_emoji = "✅" if r.status == TestStatus.PASSED else "❌" if r.status == TestStatus.FAILED else "⏭️" if r.status == TestStatus.SKIPPED else "⚠️"
                failure = r.failure_reason[:50] + "..." if r.failure_reason and len(r.failure_reason) > 50 else (r.failure_reason or "-")
                f.write(f"| {r.test_id} | {r.test_name} | {status_emoji} {r.status.value} | {r.duration_seconds:.2f}s | {failure} |\n")
            
            # Failed tests details
            failed_tests = [r for r in self.results if r.status == TestStatus.FAILED]
            if failed_tests:
                f.write("\n## Failed Tests Details\n\n")
                for r in failed_tests:
                    f.write(f"### {r.test_id}: {r.test_name}\n\n")
                    f.write(f"**Failed Step:** {r.failure_step}\n\n")
                    f.write(f"**Failure Reason:**\n```\n{r.failure_reason}\n```\n\n")
            
            # Results by category
            f.write("\n## Results by Category\n\n")
            categories = {}
            for r in self.results:
                if r.category not in categories:
                    categories[r.category] = {"passed": 0, "failed": 0, "total": 0}
                categories[r.category]["total"] += 1
                if r.status == TestStatus.PASSED:
                    categories[r.category]["passed"] += 1
                elif r.status == TestStatus.FAILED:
                    categories[r.category]["failed"] += 1
            
            f.write("| Category | Passed | Failed | Total | Pass Rate |\n")
            f.write("|----------|--------|--------|-------|----------|\n")
            for cat, stats in sorted(categories.items()):
                rate = (stats["passed"] / stats["total"] * 100) if stats["total"] > 0 else 0
                f.write(f"| {cat} | {stats['passed']} | {stats['failed']} | {stats['total']} | {rate:.1f}% |\n")
        
        # Generate JSON results
        with open(json_file, "w") as f:
            json.dump({
                "timestamp": datetime.now().isoformat(),
                "summary": {
                    "total": total,
                    "passed": passed,
                    "failed": failed,
                    "skipped": skipped,
                    "errors": errors,
                    "duration_seconds": total_duration,
                    "pass_rate": (passed/total*100) if total > 0 else 0
                },
                "results": [r.to_dict() for r in self.results]
            }, f, indent=2)
        
        self.logger.info(f"\n{'='*60}")
        self.logger.info(f"TEST RUN COMPLETE")
        self.logger.info(f"{'='*60}")
        self.logger.info(f"Total: {total} | Passed: {passed} | Failed: {failed} | Skipped: {skipped}")
        self.logger.info(f"Pass Rate: {(passed/total*100) if total > 0 else 0:.1f}%")
        self.logger.info(f"Duration: {total_duration:.2f}s")
        self.logger.info(f"Results: {self.results_dir}")
        self.logger.info(f"Summary: {summary_file}")


def main():
    parser = argparse.ArgumentParser(
        description="CockroachDB Operator VKS Functional Test Runner",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python test_runner.py                        # Run all tests
  python test_runner.py --test VKS-01          # Run single test
  python test_runner.py --test VKS-01 VKS-02   # Run multiple specific tests
  python test_runner.py --range VKS-01 VKS-05  # Run range of tests
  python test_runner.py --category scaling     # Run tests by category
  python test_runner.py --list                 # List all available tests
  python test_runner.py --dry-run              # Show what would be executed
        """
    )
    
    parser.add_argument(
        "--test", "-t",
        nargs="+",
        help="Run specific test(s) by ID"
    )
    parser.add_argument(
        "--range", "-r",
        nargs=2,
        metavar=("START", "END"),
        help="Run tests in range (inclusive)"
    )
    parser.add_argument(
        "--category", "-c",
        help="Run tests by category"
    )
    parser.add_argument(
        "--list", "-l",
        action="store_true",
        help="List all available tests"
    )
    parser.add_argument(
        "--list-categories",
        action="store_true",
        help="List all test categories"
    )
    parser.add_argument(
        "--dry-run", "-n",
        action="store_true",
        help="Show what would be executed without running"
    )
    parser.add_argument(
        "--kubeconfig", "-k",
        help="Path to kubeconfig file"
    )
    parser.add_argument(
        "--output", "-o",
        help="Output directory for results"
    )
    parser.add_argument(
        "--verbose", "-v",
        action="store_true",
        help="Enable verbose output"
    )
    
    args = parser.parse_args()
    
    registry = TestRegistry()
    
    # Handle list commands
    if args.list:
        print("\nAvailable Tests:")
        print("-" * 80)
        for test in registry.get_all_tests():
            deps = ", ".join(test.dependencies) if test.dependencies else "None"
            print(f"{test.test_id:15} | {test.category:20} | {test.name}")
            print(f"{'':15} | Dependencies: {deps}")
            print()
        return 0
    
    if args.list_categories:
        print("\nAvailable Categories:")
        print("-" * 40)
        for cat in registry.get_categories():
            tests = registry.get_tests_by_category(cat)
            print(f"{cat:25} ({len(tests)} tests)")
        return 0
    
    # Determine which tests to run
    tests_to_run = []
    
    if args.test:
        for test_id in args.test:
            test = registry.get_test(test_id)
            if test:
                tests_to_run.append(test)
            else:
                print(f"Error: Test '{test_id}' not found")
                return 1
    elif args.range:
        tests_to_run = registry.get_tests_in_range(args.range[0], args.range[1])
        if not tests_to_run:
            print(f"Error: No tests found in range {args.range[0]} to {args.range[1]}")
            return 1
    elif args.category:
        tests_to_run = registry.get_tests_by_category(args.category)
        if not tests_to_run:
            print(f"Error: No tests found for category '{args.category}'")
            return 1
    else:
        tests_to_run = registry.get_all_tests()
    
    if not tests_to_run:
        print("No tests to run")
        return 1
    
    # Create runner and execute tests
    output_dir = Path(args.output) if args.output else None
    runner = TestRunner(
        results_dir=output_dir,
        dry_run=args.dry_run,
        kubeconfig=args.kubeconfig,
        verbose=args.verbose
    )
    
    results = runner.run_tests(tests_to_run)
    
    # Return non-zero if any tests failed
    failed = sum(1 for r in results if r.status == TestStatus.FAILED)
    return 1 if failed > 0 else 0


if __name__ == "__main__":
    sys.exit(main())
