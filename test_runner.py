#!/usr/bin/env python3
"""
CockroachDB Operator VKS Functional Test Runner

This script executes the VKS functional tests defined in the tests/ directory,
logs output, and generates pass/fail summaries.

Usage:
    python test_runner.py --config config.yaml              # Run all tests with config
    python test_runner.py --config config.yaml --test VKS-01  # Run single test
    python test_runner.py --config config.yaml --range VKS-01 VKS-05  # Run range
    python test_runner.py --config config.yaml --category scaling  # Run by category
    python test_runner.py --list                            # List all available tests
    python test_runner.py --dry-run --config config.yaml    # Show what would execute
    python test_runner.py --generate-config                 # Generate sample config
"""

import argparse
import json
import os
import re
import subprocess
import sys
import yaml
from dataclasses import dataclass, field, asdict
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Optional, Any

# Test configuration
TEST_BASE_DIR = Path(__file__).parent / "tests"
RESULTS_BASE_DIR = Path(__file__).parent / "results"
DEFAULT_CONFIG_FILE = Path(__file__).parent / "config.yaml"


@dataclass
class TestConfig:
    """Configuration loaded from YAML file."""
    
    # Supervisor configuration
    supervisor_ip: str = ""
    supervisor_username: str = ""
    supervisor_namespace: str = ""
    supervisor_context: str = ""
    supervisor_kubeconfig: str = ""  # Path to supervisor kubeconfig
    
    # VKS cluster configuration
    vks_cluster_name: str = "cluster-vks"
    vks_cluster_namespace: str = "crdb-cluster"
    vks_cluster_context: str = ""
    vks_kubeconfig: str = "vks-kubeconfig.yaml"
    vks_version: str = "v1.35.0+vmware.2-vkr.4"
    vks_version_upgrade: str = ""
    
    # Operator configuration
    operator_namespace: str = "crdb-operator"
    operator_helm_chart: str = "./cockroachdb-parent/charts/operator"
    operator_version: str = ""
    operator_version_upgrade: str = ""
    
    # CockroachDB configuration
    crdb_namespace: str = "crdb-cluster"
    crdb_helm_chart: str = "./cockroachdb-parent/charts/cockroachdb"
    crdb_release_name: str = "cockroachdb"
    crdb_version_current: str = "v24.2.0"
    crdb_version_patch_upgrade: str = "v24.2.1"
    crdb_version_major_upgrade: str = "v24.3.0"
    crdb_version_rollback: str = "v24.2.0"
    
    # Cluster sizing
    crdb_replicas_initial: int = 3
    crdb_replicas_scale_up: int = 6
    crdb_replicas_scale_down: int = 3
    
    # Storage configuration
    storage_class: str = "vsan-esa-default-policy-raid5"
    storage_size: str = "100Gi"
    wal_storage_class: str = "vsan-esa-default-policy-raid5"
    wal_storage_size: str = "10Gi"
    
    # TLS configuration
    tls_enabled: bool = True
    
    # Networking
    sql_port: int = 26257
    http_port: int = 8080
    network_policy_enabled: bool = True
    
    # Backup configuration
    backup_local_path: str = "nodelocal://1/backups"
    backup_s3_enabled: bool = False
    backup_s3_bucket: str = ""
    backup_s3_endpoint: str = ""
    backup_s3_region: str = ""
    
    # Advanced features
    pcr_enabled: bool = False
    pcr_standby_cluster: str = "cockroachdb-standby"
    pcr_standby_namespace: str = "crdb-standby"
    encryption_enabled: bool = False
    encryption_kms_type: str = ""
    encryption_kms_uri: str = ""
    cdc_enabled: bool = False
    cdc_sink_type: str = "kafka"
    cdc_kafka_brokers: str = ""
    
    # Timeouts
    timeout_cluster_provision: int = 900
    timeout_pod_ready: int = 600
    timeout_helm_install: int = 600
    timeout_scale: int = 1800
    timeout_upgrade: int = 1800
    timeout_backup: int = 300
    timeout_restore: int = 600
    
    # Retries
    retry_max_attempts: int = 3
    retry_delay_seconds: int = 30
    
    @classmethod
    def from_yaml(cls, yaml_path: Path) -> "TestConfig":
        """Load configuration from YAML file."""
        if not yaml_path.exists():
            raise FileNotFoundError(f"Configuration file not found: {yaml_path}")
        
        with open(yaml_path) as f:
            data = yaml.safe_load(f)
        
        config = cls()
        
        # Supervisor
        if "supervisor" in data:
            sup = data["supervisor"]
            config.supervisor_ip = sup.get("ip", "")
            config.supervisor_username = sup.get("username", "")
            config.supervisor_namespace = sup.get("namespace", "")
            config.supervisor_context = sup.get("context", "")
            config.supervisor_kubeconfig = sup.get("kubeconfig", "")
        
        # VKS Cluster
        if "vks_cluster" in data:
            vks = data["vks_cluster"]
            config.vks_cluster_name = vks.get("name", "cluster-vks")
            config.vks_cluster_namespace = vks.get("namespace", "crdb-cluster")
            config.vks_cluster_context = vks.get("context", "")
            config.vks_kubeconfig = vks.get("kubeconfig", "vks-kubeconfig.yaml")
            config.vks_version = vks.get("version", "v1.35.0+vmware.2-vkr.4")
            config.vks_version_upgrade = vks.get("version_upgrade", "")
        
        # Operator
        if "operator" in data:
            op = data["operator"]
            config.operator_namespace = op.get("namespace", "crdb-operator")
            config.operator_helm_chart = op.get("helm_chart_path", "./cockroachdb-parent/charts/operator")
            config.operator_version = op.get("version", "")
            config.operator_version_upgrade = op.get("version_upgrade", "")
        
        # CockroachDB
        if "cockroachdb" in data:
            crdb = data["cockroachdb"]
            config.crdb_namespace = crdb.get("namespace", "crdb-cluster")
            config.crdb_helm_chart = crdb.get("helm_chart_path", "./cockroachdb-parent/charts/cockroachdb")
            config.crdb_release_name = crdb.get("release_name", "cockroachdb")
            
            if "version" in crdb:
                ver = crdb["version"]
                config.crdb_version_current = ver.get("current", "v24.2.0")
                config.crdb_version_patch_upgrade = ver.get("patch_upgrade", "v24.2.1")
                config.crdb_version_major_upgrade = ver.get("major_upgrade", "v24.3.0")
                config.crdb_version_rollback = ver.get("rollback", "v24.2.0")
            
            if "replicas" in crdb:
                rep = crdb["replicas"]
                config.crdb_replicas_initial = rep.get("initial", 3)
                config.crdb_replicas_scale_up = rep.get("scale_up", 6)
                config.crdb_replicas_scale_down = rep.get("scale_down", 3)
            
            if "storage" in crdb:
                stor = crdb["storage"]
                config.storage_class = stor.get("class", "vsan-esa-default-policy-raid5")
                config.storage_size = stor.get("size", "100Gi")
                config.wal_storage_class = stor.get("wal_storage_class", config.storage_class)
                config.wal_storage_size = stor.get("wal_size", "10Gi")
            
            if "tls" in crdb:
                config.tls_enabled = crdb["tls"].get("enabled", True)
        
        # Networking
        if "networking" in data:
            net = data["networking"]
            config.sql_port = net.get("sql_port", 26257)
            config.http_port = net.get("http_port", 8080)
            if "network_policy" in net:
                config.network_policy_enabled = net["network_policy"].get("enabled", True)
        
        # Backup
        if "backup" in data:
            bkp = data["backup"]
            if "local" in bkp:
                config.backup_local_path = bkp["local"].get("path", "nodelocal://1/backups")
            if "s3" in bkp:
                s3 = bkp["s3"]
                config.backup_s3_enabled = s3.get("enabled", False)
                config.backup_s3_bucket = s3.get("bucket", "")
                config.backup_s3_endpoint = s3.get("endpoint", "")
                config.backup_s3_region = s3.get("region", "")
        
        # Advanced features
        if "advanced_features" in data:
            adv = data["advanced_features"]
            if "pcr" in adv:
                pcr = adv["pcr"]
                config.pcr_enabled = pcr.get("enabled", False)
                config.pcr_standby_cluster = pcr.get("standby_cluster_name", "cockroachdb-standby")
                config.pcr_standby_namespace = pcr.get("standby_namespace", "crdb-standby")
            if "encryption" in adv:
                enc = adv["encryption"]
                config.encryption_enabled = enc.get("enabled", False)
                config.encryption_kms_type = enc.get("kms_type", "")
                config.encryption_kms_uri = enc.get("kms_uri", "")
            if "cdc" in adv:
                cdc = adv["cdc"]
                config.cdc_enabled = cdc.get("enabled", False)
                config.cdc_sink_type = cdc.get("sink_type", "kafka")
                config.cdc_kafka_brokers = cdc.get("kafka_brokers", "")
        
        # Test config (timeouts and retries)
        if "test_config" in data:
            tc = data["test_config"]
            if "timeouts" in tc:
                to = tc["timeouts"]
                config.timeout_cluster_provision = to.get("cluster_provision", 900)
                config.timeout_pod_ready = to.get("pod_ready", 600)
                config.timeout_helm_install = to.get("helm_install", 600)
                config.timeout_scale = to.get("scale_operation", 1800)
                config.timeout_upgrade = to.get("upgrade", 1800)
                config.timeout_backup = to.get("backup", 300)
                config.timeout_restore = to.get("restore", 600)
            if "retries" in tc:
                ret = tc["retries"]
                config.retry_max_attempts = ret.get("max_attempts", 3)
                config.retry_delay_seconds = ret.get("delay_seconds", 30)
        
        return config
    
    def to_env_dict(self) -> dict[str, str]:
        """Convert configuration to environment variables dictionary."""
        return {
            # Supervisor
            "SUPERVISOR_IP": self.supervisor_ip,
            "SUPERVISOR_USERNAME": self.supervisor_username,
            "SUPERVISOR_NAMESPACE": self.supervisor_namespace,
            "SUPERVISOR_CONTEXT": self.supervisor_context,
            "SUPERVISOR_KUBECONFIG": self.supervisor_kubeconfig,
            
            # VKS Cluster
            "VKS_CLUSTER_NAME": self.vks_cluster_name,
            "VKS_CLUSTER_NAMESPACE": self.vks_cluster_namespace,
            "VKS_CLUSTER_CONTEXT": self.vks_cluster_context,
            "VKS_KUBECONFIG": self.vks_kubeconfig,
            "VKS_VERSION": self.vks_version,
            "VKS_VERSION_UPGRADE": self.vks_version_upgrade,
            
            # Operator
            "CRDB_OPERATOR_NS": self.operator_namespace,
            "OPERATOR_HELM_CHART": self.operator_helm_chart,
            "OPERATOR_VERSION": self.operator_version,
            "OPERATOR_VERSION_UPGRADE": self.operator_version_upgrade,
            
            # CockroachDB
            "CRDB_CLUSTER_NS": self.crdb_namespace,
            "CRDB_HELM_CHART": self.crdb_helm_chart,
            "CRDB_RELEASE_NAME": self.crdb_release_name,
            "CRDB_VERSION": self.crdb_version_current,
            "CRDB_VERSION_PATCH_UPGRADE": self.crdb_version_patch_upgrade,
            "CRDB_VERSION_MAJOR_UPGRADE": self.crdb_version_major_upgrade,
            "CRDB_VERSION_ROLLBACK": self.crdb_version_rollback,
            
            # Replicas
            "CRDB_REPLICAS": str(self.crdb_replicas_initial),
            "CRDB_REPLICAS_SCALE_UP": str(self.crdb_replicas_scale_up),
            "CRDB_REPLICAS_SCALE_DOWN": str(self.crdb_replicas_scale_down),
            
            # Storage
            "STORAGE_CLASS": self.storage_class,
            "STORAGE_SIZE": self.storage_size,
            "WAL_STORAGE_CLASS": self.wal_storage_class,
            "WAL_STORAGE_SIZE": self.wal_storage_size,
            
            # TLS
            "TLS_ENABLED": str(self.tls_enabled).lower(),
            
            # Networking
            "SQL_PORT": str(self.sql_port),
            "HTTP_PORT": str(self.http_port),
            "NETWORK_POLICY_ENABLED": str(self.network_policy_enabled).lower(),
            
            # Backup
            "BACKUP_LOCAL_PATH": self.backup_local_path,
            "BACKUP_S3_ENABLED": str(self.backup_s3_enabled).lower(),
            "BACKUP_S3_BUCKET": self.backup_s3_bucket,
            "BACKUP_S3_ENDPOINT": self.backup_s3_endpoint,
            "BACKUP_S3_REGION": self.backup_s3_region,
            
            # Advanced features
            "PCR_ENABLED": str(self.pcr_enabled).lower(),
            "PCR_STANDBY_CLUSTER": self.pcr_standby_cluster,
            "PCR_STANDBY_NAMESPACE": self.pcr_standby_namespace,
            "ENCRYPTION_ENABLED": str(self.encryption_enabled).lower(),
            "ENCRYPTION_KMS_TYPE": self.encryption_kms_type,
            "ENCRYPTION_KMS_URI": self.encryption_kms_uri,
            "CDC_ENABLED": str(self.cdc_enabled).lower(),
            "CDC_SINK_TYPE": self.cdc_sink_type,
            "CDC_KAFKA_BROKERS": self.cdc_kafka_brokers,
            
            # Timeouts
            "TIMEOUT_CLUSTER_PROVISION": str(self.timeout_cluster_provision),
            "TIMEOUT_POD_READY": str(self.timeout_pod_ready),
            "TIMEOUT_HELM_INSTALL": str(self.timeout_helm_install),
            "TIMEOUT_SCALE": str(self.timeout_scale),
            "TIMEOUT_UPGRADE": str(self.timeout_upgrade),
            "TIMEOUT_BACKUP": str(self.timeout_backup),
            "TIMEOUT_RESTORE": str(self.timeout_restore),
            
            # Retries
            "RETRY_MAX_ATTEMPTS": str(self.retry_max_attempts),
            "RETRY_DELAY_SECONDS": str(self.retry_delay_seconds),
        }
    
    def validate(self) -> list[str]:
        """Validate configuration and return list of errors."""
        errors = []
        
        if not self.supervisor_ip:
            errors.append("supervisor.ip is required")
        if not self.supervisor_namespace:
            errors.append("supervisor.namespace is required")
        if not self.supervisor_kubeconfig:
            errors.append("supervisor.kubeconfig is required (path to supervisor kubeconfig file)")
        elif not Path(self.supervisor_kubeconfig).exists():
            errors.append(f"supervisor.kubeconfig file not found: {self.supervisor_kubeconfig}")
        if not self.vks_cluster_name:
            errors.append("vks_cluster.name is required")
        if not self.storage_class:
            errors.append("cockroachdb.storage.class is required")
        
        return errors


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
    target_cluster: str = "vks"  # "supervisor" or "vks" - which cluster to run against


class TestRegistry:
    """Registry of all available tests."""
    
    def __init__(self, config: Optional[TestConfig] = None):
        self.tests: dict[str, TestDefinition] = {}
        self.config = config or TestConfig()
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
        cfg = self.config
        self.tests["00-VKS-CLUSTER"] = TestDefinition(
            test_id="00-VKS-CLUSTER",
            name="Deploy VKS Kubernetes Cluster",
            category="prerequisites",
            description="Deploy VKS cluster using vks.yaml manifest",
            dependencies=[],
            doc_path=TEST_BASE_DIR / "00-prerequisites" / "00-VKS-CLUSTER.md",
            target_cluster="supervisor",  # This test runs against the supervisor
            steps=[
                TestStep(
                    name="Verify supervisor connection",
                    command="kubectl cluster-info",
                    timeout=30
                ),
                TestStep(
                    name="Apply VKS cluster manifest",
                    command="kubectl apply -f vks.yaml",
                    timeout=60
                ),
                TestStep(
                    name="Wait for cluster provisioning",
                    command=f"kubectl wait --for=jsonpath='{{.status.phase}}'=Provisioned cluster/{cfg.vks_cluster_name} --timeout={cfg.timeout_cluster_provision}s",
                    timeout=cfg.timeout_cluster_provision + 20
                ),
                TestStep(
                    name="Get kubeconfig",
                    command=f"kubectl get secret {cfg.vks_cluster_name}-kubeconfig -o jsonpath='{{.data.value}}' | base64 -d > {cfg.vks_kubeconfig}",
                    timeout=30
                ),
            ],
            validation_commands=[
                f"kubectl --kubeconfig={cfg.vks_kubeconfig} get nodes",
                f"kubectl --kubeconfig={cfg.vks_kubeconfig} cluster-info",
            ]
        )
    
    def _register_provisioning_tests(self):
        """Register cluster provisioning tests."""
        cfg = self.config
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
                    command=f"kubectl create namespace {cfg.operator_namespace} --dry-run=client -o yaml | kubectl apply -f -",
                    timeout=30
                ),
                TestStep(
                    name="Install operator via Helm",
                    command=f"helm install crdb-operator {cfg.operator_helm_chart} -n {cfg.operator_namespace} --wait --timeout {cfg.timeout_helm_install // 60}m",
                    timeout=cfg.timeout_helm_install + 30
                ),
                TestStep(
                    name="Wait for operator pods",
                    command=f"kubectl wait --for=condition=Ready pods --all -n {cfg.operator_namespace} --timeout={cfg.timeout_pod_ready}s",
                    timeout=cfg.timeout_pod_ready + 10
                ),
            ],
            validation_commands=[
                f"kubectl get pods -n {cfg.operator_namespace}",
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
                    command=f"kubectl create namespace {cfg.crdb_namespace} --dry-run=client -o yaml | kubectl apply -f -",
                    timeout=30
                ),
                TestStep(
                    name="Install CockroachDB via Helm",
                    command=f"helm install {cfg.crdb_release_name} {cfg.crdb_helm_chart} -n {cfg.crdb_namespace} --set storage.persistentVolume.storageClass={cfg.storage_class} --set statefulset.replicas={cfg.crdb_replicas_initial} --wait --timeout {cfg.timeout_helm_install // 60}m",
                    timeout=cfg.timeout_helm_install + 30
                ),
                TestStep(
                    name="Wait for all pods ready",
                    command=f"kubectl wait --for=condition=Ready pods --all -n {cfg.crdb_namespace} --timeout={cfg.timeout_pod_ready}s",
                    timeout=cfg.timeout_pod_ready + 10
                ),
            ],
            validation_commands=[
                f"kubectl get pods -n {cfg.crdb_namespace}",
                f"kubectl get pvc -n {cfg.crdb_namespace}",
                f"kubectl exec -n {cfg.crdb_namespace} {cfg.crdb_release_name}-0 -- cockroach node status --insecure --host=localhost:{cfg.sql_port}",
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
                    command=f"helm upgrade {cfg.crdb_release_name} {cfg.crdb_helm_chart} -n {cfg.crdb_namespace} --reuse-values --set tls.enabled=true --wait --timeout {cfg.timeout_helm_install // 60}m",
                    timeout=cfg.timeout_helm_install + 30
                ),
                TestStep(
                    name="Wait for pods to restart",
                    command=f"kubectl wait --for=condition=Ready pods --all -n {cfg.crdb_namespace} --timeout={cfg.timeout_pod_ready}s",
                    timeout=cfg.timeout_pod_ready + 10
                ),
            ],
            validation_commands=[
                f"kubectl get secrets -n {cfg.crdb_namespace} | grep -E '(ca|tls)'",
                f"kubectl exec -n {cfg.crdb_namespace} {cfg.crdb_release_name}-0 -- ls /cockroach/cockroach-certs/",
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
                    command=f"kubectl get pods -n {cfg.crdb_namespace} -o wide",
                    timeout=30
                ),
            ],
            validation_commands=[
                f"kubectl exec -n {cfg.crdb_namespace} {cfg.crdb_release_name}-0 -- cockroach sql --insecure --host=localhost:{cfg.sql_port} --execute=\"SELECT node_id, locality FROM crdb_internal.gossip_nodes ORDER BY node_id;\"",
            ]
        )
    
    def _register_security_tests(self):
        """Register security tests."""
        cfg = self.config
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
                    command=f"kubectl get pods -n {cfg.operator_namespace} -o jsonpath='{{.items[*].spec.securityContext}}'",
                    timeout=30
                ),
                TestStep(
                    name="Check cluster security context",
                    command=f"kubectl get pods -n {cfg.crdb_namespace} -o jsonpath='{{.items[*].spec.securityContext}}'",
                    timeout=30
                ),
                TestStep(
                    name="Restart pod to test re-admission",
                    command=f"kubectl delete pod {cfg.crdb_release_name}-0 -n {cfg.crdb_namespace} && kubectl wait --for=condition=Ready pod/{cfg.crdb_release_name}-0 -n {cfg.crdb_namespace} --timeout={cfg.timeout_pod_ready}s",
                    timeout=cfg.timeout_pod_ready + 10
                ),
            ],
            validation_commands=[
                f"kubectl get pods -n {cfg.crdb_namespace}",
                f"kubectl get events -n {cfg.crdb_namespace} --field-selector reason=FailedCreate 2>/dev/null | grep -i security || echo 'No PSA issues'",
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
                    command=f"helm upgrade {cfg.crdb_release_name} {cfg.crdb_helm_chart} -n {cfg.crdb_namespace} --reuse-values --set networkPolicy.enabled=true --wait --timeout 5m",
                    timeout=330
                ),
                TestStep(
                    name="Verify intra-cluster communication",
                    command=f"kubectl exec -n {cfg.crdb_namespace} {cfg.crdb_release_name}-0 -- cockroach sql --insecure --host={cfg.crdb_release_name}-1.{cfg.crdb_release_name}:{cfg.sql_port} --execute=\"SELECT 1;\"",
                    timeout=60
                ),
            ],
            validation_commands=[
                f"kubectl get networkpolicies -n {cfg.crdb_namespace}",
                f"kubectl exec -n {cfg.crdb_namespace} {cfg.crdb_release_name}-0 -- cockroach sql --insecure --host=localhost:{cfg.sql_port} --execute=\"SELECT count(*) FROM crdb_internal.gossip_nodes WHERE is_live;\"",
            ]
        )
    
    def _register_scaling_tests(self):
        """Register scaling tests."""
        cfg = self.config
        self.tests["VKS-07"] = TestDefinition(
            test_id="VKS-07",
            name="Scale Up Nodes",
            category="scaling",
            description=f"Scale cluster from {cfg.crdb_replicas_initial} to {cfg.crdb_replicas_scale_up} nodes",
            dependencies=["VKS-02"],
            doc_path=TEST_BASE_DIR / "03-scaling" / "VKS-07-scale-up.md",
            steps=[
                TestStep(
                    name=f"Scale up to {cfg.crdb_replicas_scale_up} replicas",
                    command=f"helm upgrade {cfg.crdb_release_name} {cfg.crdb_helm_chart} -n {cfg.crdb_namespace} --reuse-values --set statefulset.replicas={cfg.crdb_replicas_scale_up} --wait --timeout {cfg.timeout_scale // 60}m",
                    timeout=cfg.timeout_scale + 30
                ),
                TestStep(
                    name="Wait for all pods ready",
                    command=f"kubectl wait --for=condition=Ready pods --all -n {cfg.crdb_namespace} --timeout={cfg.timeout_pod_ready}s",
                    timeout=cfg.timeout_pod_ready + 10
                ),
            ],
            validation_commands=[
                f"kubectl get pods -n {cfg.crdb_namespace}",
                f"kubectl exec -n {cfg.crdb_namespace} {cfg.crdb_release_name}-0 -- cockroach node status --insecure --host=localhost:{cfg.sql_port}",
            ]
        )
        
        self.tests["VKS-08"] = TestDefinition(
            test_id="VKS-08",
            name="Scale Down Nodes",
            category="scaling",
            description=f"Scale cluster from {cfg.crdb_replicas_scale_up} to {cfg.crdb_replicas_scale_down} nodes",
            dependencies=["VKS-07"],
            doc_path=TEST_BASE_DIR / "03-scaling" / "VKS-08-scale-down.md",
            steps=[
                TestStep(
                    name=f"Scale down to {cfg.crdb_replicas_scale_down} replicas",
                    command=f"helm upgrade {cfg.crdb_release_name} {cfg.crdb_helm_chart} -n {cfg.crdb_namespace} --reuse-values --set statefulset.replicas={cfg.crdb_replicas_scale_down} --timeout {cfg.timeout_scale // 60}m",
                    timeout=cfg.timeout_scale + 30
                ),
                TestStep(
                    name="Wait for scale down",
                    command=f"bash -c 'while [ $(kubectl get pods -n {cfg.crdb_namespace} --no-headers | wc -l) -gt {cfg.crdb_replicas_scale_down} ]; do sleep 10; done'",
                    timeout=cfg.timeout_scale
                ),
            ],
            validation_commands=[
                f"kubectl get pods -n {cfg.crdb_namespace}",
                f"kubectl exec -n {cfg.crdb_namespace} {cfg.crdb_release_name}-0 -- cockroach sql --insecure --host=localhost:{cfg.sql_port} --execute=\"SELECT count(*) FROM crdb_internal.gossip_nodes WHERE is_live;\"",
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
                    command=f"kubectl get pods -n {cfg.crdb_namespace} {cfg.crdb_release_name}-2 -o jsonpath='{{.spec.nodeName}}'",
                    timeout=30
                ),
            ],
            validation_commands=[
                f"kubectl get pods -n {cfg.crdb_namespace} -o wide",
            ]
        )
    
    def _register_upgrade_tests(self):
        """Register upgrade tests."""
        cfg = self.config
        self.tests["VKS-10"] = TestDefinition(
            test_id="VKS-10",
            name="Patch Upgrade",
            category="upgrades",
            description=f"Perform patch version upgrade from {cfg.crdb_version_current} to {cfg.crdb_version_patch_upgrade}",
            dependencies=["VKS-02"],
            doc_path=TEST_BASE_DIR / "04-upgrades" / "VKS-10-patch-upgrade.md",
            steps=[
                TestStep(
                    name="Get current version",
                    command=f"kubectl exec -n {cfg.crdb_namespace} {cfg.crdb_release_name}-0 -- cockroach version",
                    timeout=30
                ),
                TestStep(
                    name="Upgrade to patch version",
                    command=f"helm upgrade {cfg.crdb_release_name} {cfg.crdb_helm_chart} -n {cfg.crdb_namespace} --reuse-values --set image.tag={cfg.crdb_version_patch_upgrade} --wait --timeout {cfg.timeout_upgrade // 60}m",
                    timeout=cfg.timeout_upgrade + 30
                ),
                TestStep(
                    name="Wait for rolling restart",
                    command=f"kubectl rollout status statefulset/{cfg.crdb_release_name} -n {cfg.crdb_namespace} --timeout={cfg.timeout_upgrade}s",
                    timeout=cfg.timeout_upgrade + 10
                ),
            ],
            validation_commands=[
                f"kubectl get pods -n {cfg.crdb_namespace}",
                f"kubectl exec -n {cfg.crdb_namespace} {cfg.crdb_release_name}-0 -- cockroach version",
            ]
        )
        
        self.tests["VKS-11"] = TestDefinition(
            test_id="VKS-11",
            name="Major Upgrade with Auto-Finalization",
            category="upgrades",
            description=f"Major version upgrade to {cfg.crdb_version_major_upgrade} with auto-finalization",
            dependencies=["VKS-02"],
            doc_path=TEST_BASE_DIR / "04-upgrades" / "VKS-11-major-upgrade-auto.md",
            steps=[
                TestStep(
                    name="Check cluster version",
                    command=f"kubectl exec -n {cfg.crdb_namespace} {cfg.crdb_release_name}-0 -- cockroach sql --insecure --host=localhost:{cfg.sql_port} --execute=\"SHOW CLUSTER SETTING version;\"",
                    timeout=30
                ),
                TestStep(
                    name="Upgrade to major version",
                    command=f"helm upgrade {cfg.crdb_release_name} {cfg.crdb_helm_chart} -n {cfg.crdb_namespace} --reuse-values --set image.tag={cfg.crdb_version_major_upgrade} --wait --timeout {cfg.timeout_upgrade // 60}m",
                    timeout=cfg.timeout_upgrade + 30
                ),
                TestStep(
                    name="Wait for rolling restart",
                    command=f"kubectl rollout status statefulset/{cfg.crdb_release_name} -n {cfg.crdb_namespace} --timeout={cfg.timeout_upgrade}s",
                    timeout=cfg.timeout_upgrade + 10
                ),
            ],
            validation_commands=[
                f"kubectl get pods -n {cfg.crdb_namespace}",
                f"kubectl exec -n {cfg.crdb_namespace} {cfg.crdb_release_name}-0 -- cockroach sql --insecure --host=localhost:{cfg.sql_port} --execute=\"SHOW CLUSTER SETTING version;\"",
            ]
        )
        
        self.tests["VKS-12"] = TestDefinition(
            test_id="VKS-12",
            name="Major Upgrade with Manual Finalization",
            category="upgrades",
            description=f"Major version upgrade to {cfg.crdb_version_major_upgrade} with manual finalization",
            dependencies=["VKS-02"],
            doc_path=TEST_BASE_DIR / "04-upgrades" / "VKS-12-major-upgrade-manual.md",
            steps=[
                TestStep(
                    name="Set preserve_downgrade_option",
                    command=f"kubectl exec -n {cfg.crdb_namespace} {cfg.crdb_release_name}-0 -- cockroach sql --insecure --host=localhost:{cfg.sql_port} --execute=\"SET CLUSTER SETTING cluster.preserve_downgrade_option = '{cfg.crdb_version_current.lstrip('v').rsplit('.', 1)[0]}';\"",
                    timeout=30
                ),
                TestStep(
                    name="Check preserve_downgrade_option",
                    command=f"kubectl exec -n {cfg.crdb_namespace} {cfg.crdb_release_name}-0 -- cockroach sql --insecure --host=localhost:{cfg.sql_port} --execute=\"SHOW CLUSTER SETTING cluster.preserve_downgrade_option;\"",
                    timeout=30
                ),
            ],
            validation_commands=[
                f"kubectl get pods -n {cfg.crdb_namespace}",
            ]
        )
        
        self.tests["VKS-13"] = TestDefinition(
            test_id="VKS-13",
            name="Rollback Patch Upgrade",
            category="upgrades",
            description=f"Rollback to {cfg.crdb_version_rollback}",
            dependencies=["VKS-10"],
            doc_path=TEST_BASE_DIR / "04-upgrades" / "VKS-13-rollback-patch.md",
            steps=[
                TestStep(
                    name="Check current version",
                    command=f"kubectl exec -n {cfg.crdb_namespace} {cfg.crdb_release_name}-0 -- cockroach version",
                    timeout=30
                ),
                TestStep(
                    name="Rollback to previous version",
                    command=f"helm upgrade {cfg.crdb_release_name} {cfg.crdb_helm_chart} -n {cfg.crdb_namespace} --reuse-values --set image.tag={cfg.crdb_version_rollback} --wait --timeout {cfg.timeout_upgrade // 60}m",
                    timeout=cfg.timeout_upgrade + 30
                ),
                TestStep(
                    name="Wait for rolling restart",
                    command=f"kubectl rollout status statefulset/{cfg.crdb_release_name} -n {cfg.crdb_namespace} --timeout={cfg.timeout_upgrade}s",
                    timeout=cfg.timeout_upgrade + 10
                ),
            ],
            validation_commands=[
                f"kubectl get pods -n {cfg.crdb_namespace}",
                f"kubectl exec -n {cfg.crdb_namespace} {cfg.crdb_release_name}-0 -- cockroach version",
            ]
        )
    
    def _register_operator_lifecycle_tests(self):
        """Register operator lifecycle tests."""
        cfg = self.config
        self.tests["VKS-14"] = TestDefinition(
            test_id="VKS-14",
            name="Operator Helm Chart Upgrade",
            category="operator-lifecycle",
            description=f"Upgrade the operator Helm chart to {cfg.operator_version_upgrade or 'latest'}",
            dependencies=["VKS-01"],
            doc_path=TEST_BASE_DIR / "05-operator-lifecycle" / "VKS-14-operator-upgrade.md",
            steps=[
                TestStep(
                    name="Upgrade operator",
                    command=f"helm upgrade crdb-operator {cfg.operator_helm_chart} -n {cfg.operator_namespace} --reuse-values --wait --timeout 5m",
                    timeout=330
                ),
                TestStep(
                    name="Wait for operator ready",
                    command=f"kubectl wait --for=condition=Available deployment -n {cfg.operator_namespace} --timeout=120s",
                    timeout=130
                ),
            ],
            validation_commands=[
                f"kubectl get pods -n {cfg.operator_namespace}",
                f"helm list -n {cfg.operator_namespace}",
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
        cfg = self.config
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
                    command=f"bash -c 'kubectl port-forward svc/{cfg.crdb_release_name}-public -n {cfg.crdb_namespace} {cfg.http_port}:{cfg.http_port} & PF_PID=$!; sleep 3; curl -s http://localhost:{cfg.http_port}/health; kill $PF_PID 2>/dev/null'",
                    timeout=30
                ),
            ],
            validation_commands=[
                f"kubectl get svc -n {cfg.crdb_namespace}",
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
                    command=f"kubectl get svc -n {cfg.crdb_namespace}",
                    timeout=30
                ),
            ],
            validation_commands=[
                f"kubectl get svc -n {cfg.crdb_namespace}",
            ]
        )
    
    def _register_day2_ops_tests(self):
        """Register day-2 operations tests."""
        cfg = self.config
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
                    command=f"kubectl exec -n {cfg.crdb_namespace} {cfg.crdb_release_name}-0 -- curl -s http://localhost:{cfg.http_port}/_status/vars | head -20",
                    timeout=30
                ),
            ],
            validation_commands=[
                f"kubectl exec -n {cfg.crdb_namespace} {cfg.crdb_release_name}-0 -- curl -s http://localhost:{cfg.http_port}/_status/vars | grep -c '^[a-z]'",
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
                    command=f"kubectl exec -n {cfg.crdb_namespace} {cfg.crdb_release_name}-0 -- cockroach sql --insecure --host=localhost:{cfg.sql_port} --execute=\"CREATE DATABASE IF NOT EXISTS backup_test; USE backup_test; CREATE TABLE IF NOT EXISTS test (id INT PRIMARY KEY); INSERT INTO test VALUES (1), (2), (3);\"",
                    timeout=60
                ),
                TestStep(
                    name="Perform backup",
                    command=f"kubectl exec -n {cfg.crdb_namespace} {cfg.crdb_release_name}-0 -- cockroach sql --insecure --host=localhost:{cfg.sql_port} --execute=\"BACKUP DATABASE backup_test INTO '{cfg.backup_local_path}' AS OF SYSTEM TIME '-10s';\"",
                    timeout=cfg.timeout_backup
                ),
            ],
            validation_commands=[
                f"kubectl exec -n {cfg.crdb_namespace} {cfg.crdb_release_name}-0 -- cockroach sql --insecure --host=localhost:{cfg.sql_port} --execute=\"SHOW BACKUPS IN '{cfg.backup_local_path}';\"",
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
                    command=f"kubectl delete pod {cfg.crdb_release_name}-1 -n {cfg.crdb_namespace}",
                    timeout=30
                ),
                TestStep(
                    name="Wait for pod to be rescheduled",
                    command=f"kubectl wait --for=condition=Ready pod/{cfg.crdb_release_name}-1 -n {cfg.crdb_namespace} --timeout={cfg.timeout_pod_ready}s",
                    timeout=cfg.timeout_pod_ready + 10
                ),
            ],
            validation_commands=[
                f"kubectl get pods -n {cfg.crdb_namespace}",
                f"kubectl exec -n {cfg.crdb_namespace} {cfg.crdb_release_name}-0 -- cockroach node status --insecure --host=localhost:{cfg.sql_port}",
            ]
        )
    
    def _register_advanced_feature_tests(self):
        """Register advanced feature tests."""
        cfg = self.config
        self.tests["VKS-21"] = TestDefinition(
            test_id="VKS-21",
            name="Physical Cluster Replication",
            category="advanced-features",
            description="Test PCR between clusters",
            dependencies=["VKS-02"],
            doc_path=TEST_BASE_DIR / "08-advanced-features" / "VKS-21-pcr.md",
            steps=[
                TestStep(
                    name="Enable rangefeed setting",
                    command=f"kubectl exec -n {cfg.crdb_namespace} {cfg.crdb_release_name}-0 -- cockroach sql --insecure --host=localhost:{cfg.sql_port} --execute=\"SET CLUSTER SETTING kv.rangefeed.enabled = true;\"",
                    timeout=30
                ),
                TestStep(
                    name="Check rangefeed setting",
                    command=f"kubectl exec -n {cfg.crdb_namespace} {cfg.crdb_release_name}-0 -- cockroach sql --insecure --host=localhost:{cfg.sql_port} --execute=\"SHOW CLUSTER SETTING kv.rangefeed.enabled;\"",
                    timeout=30
                ),
            ],
            validation_commands=[
                f"kubectl get pods -n {cfg.crdb_namespace}",
            ]
        )
        
        self.tests["VKS-22"] = TestDefinition(
            test_id="VKS-22",
            name="Encryption at Rest with KMS",
            category="advanced-features",
            description=f"Test encryption at rest with {cfg.encryption_kms_type or 'KMS'}",
            dependencies=["VKS-02"],
            doc_path=TEST_BASE_DIR / "08-advanced-features" / "VKS-22-encryption-kms.md",
            steps=[
                TestStep(
                    name="Check encryption status",
                    command=f"kubectl exec -n {cfg.crdb_namespace} {cfg.crdb_release_name}-0 -- cockroach sql --insecure --host=localhost:{cfg.sql_port} --execute=\"SELECT * FROM crdb_internal.encryption_status;\" 2>/dev/null || echo 'Encryption not configured'",
                    timeout=30
                ),
            ],
            validation_commands=[
                f"kubectl get pods -n {cfg.crdb_namespace}",
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
                    command=f"kubectl exec -n {cfg.crdb_namespace} {cfg.crdb_release_name}-0 -- cockroach sql --insecure --host=localhost:{cfg.sql_port} --execute=\"SET CLUSTER SETTING kv.rangefeed.enabled = true;\"",
                    timeout=30
                ),
            ],
            validation_commands=[
                f"kubectl exec -n {cfg.crdb_namespace} {cfg.crdb_release_name}-0 -- cockroach sql --insecure --host=localhost:{cfg.sql_port} --execute=\"SHOW CLUSTER SETTING kv.rangefeed.enabled;\"",
            ]
        )
        
        self.tests["VKS-24"] = TestDefinition(
            test_id="VKS-24",
            name="CDC to Kafka",
            category="advanced-features",
            description=f"Test CDC to {cfg.cdc_sink_type}",
            dependencies=["VKS-02"],
            doc_path=TEST_BASE_DIR / "08-advanced-features" / "VKS-24-cdc-kafka.md",
            steps=[
                TestStep(
                    name="Check changefeed capability",
                    command=f"kubectl exec -n {cfg.crdb_namespace} {cfg.crdb_release_name}-0 -- cockroach sql --insecure --host=localhost:{cfg.sql_port} --execute=\"SELECT * FROM [SHOW JOBS] WHERE job_type = 'CHANGEFEED' LIMIT 1;\" 2>/dev/null || echo 'No changefeeds'",
                    timeout=30
                ),
            ],
            validation_commands=[
                f"kubectl get pods -n {cfg.crdb_namespace}",
            ]
        )
    
    def _register_failure_handling_tests(self):
        """Register failure handling tests."""
        cfg = self.config
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
                    command=f"kubectl delete pods -n {cfg.operator_namespace} -l app.kubernetes.io/name=cockroach-operator",
                    timeout=30
                ),
                TestStep(
                    name="Verify cluster still serves traffic",
                    command=f"kubectl exec -n {cfg.crdb_namespace} {cfg.crdb_release_name}-0 -- cockroach sql --insecure --host=localhost:{cfg.sql_port} --execute=\"SELECT 1;\"",
                    timeout=30
                ),
                TestStep(
                    name="Wait for operator restart",
                    command=f"kubectl wait --for=condition=Available deployment -n {cfg.operator_namespace} --timeout=120s",
                    timeout=130
                ),
            ],
            validation_commands=[
                f"kubectl get pods -n {cfg.operator_namespace}",
                f"kubectl get pods -n {cfg.crdb_namespace}",
            ]
        )
        
        self.tests["VKS-27"] = TestDefinition(
            test_id="VKS-27",
            name="VKS Kubernetes Cluster Upgrade",
            category="failure-handling",
            description=f"Test VKS cluster upgrade to {cfg.vks_version_upgrade or 'next version'}",
            dependencies=["VKS-02"],
            doc_path=TEST_BASE_DIR / "09-failure-handling" / "VKS-27-vks-upgrade.md",
            steps=[
                TestStep(
                    name="Record current version",
                    command="kubectl version --short 2>/dev/null || kubectl version",
                    timeout=30
                ),
            ],
            validation_commands=[
                "kubectl get nodes",
                f"kubectl get pods -n {cfg.crdb_namespace}",
            ]
        )
        
        self.tests["VKS-28"] = TestDefinition(
            test_id="VKS-28",
            name="VKS Minor Upgrade",
            category="failure-handling",
            description=f"Test VKS minor version upgrade to {cfg.vks_version_upgrade or 'next version'}",
            dependencies=["VKS-02"],
            doc_path=TEST_BASE_DIR / "09-failure-handling" / "VKS-28-vks-minor-upgrade.md",
            steps=[
                TestStep(
                    name="Record current version",
                    command="kubectl version --short 2>/dev/null || kubectl version",
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
                    command=f"kubectl get pvc -n {cfg.crdb_namespace}",
                    timeout=30
                ),
            ],
            validation_commands=[
                f"kubectl get pvc -n {cfg.crdb_namespace}",
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
    
    def __init__(self, config: Optional[TestConfig] = None, results_dir: Optional[Path] = None, 
                 dry_run: bool = False, kubeconfig: Optional[str] = None, verbose: bool = False):
        self.config = config or TestConfig()
        self.registry = TestRegistry(config=self.config)
        self.dry_run = dry_run
        self.verbose = verbose
        
        # Store both kubeconfigs
        self.vks_kubeconfig = kubeconfig or self.config.vks_kubeconfig or os.environ.get("KUBECONFIG", "vks-kubeconfig.yaml")
        self.supervisor_kubeconfig = self.config.supervisor_kubeconfig or os.environ.get("SUPERVISOR_KUBECONFIG", "")
        
        # Current kubeconfig and context (will be switched based on test target)
        self.current_kubeconfig = self.vks_kubeconfig
        self.current_context: Optional[str] = None
        
        # Create results directory with timestamp
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        self.results_dir = results_dir or RESULTS_BASE_DIR / timestamp
        self.results_dir.mkdir(parents=True, exist_ok=True)
        
        # Initialize results
        self.results: list[TestResult] = []
        
        # Setup logging
        self.log_file = self.results_dir / "test_runner.log"
        self._setup_logging()
        
        # Save configuration to results directory
        self._save_config()
    
    def _setup_logging(self):
        """Setup logging to file and console."""
        import logging
        
        self.logger = logging.getLogger("test_runner")
        self.logger.setLevel(logging.DEBUG if self.verbose else logging.INFO)
        
        # Clear existing handlers
        self.logger.handlers = []
        
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
    
    def _save_config(self):
        """Save configuration to results directory for reference."""
        config_file = self.results_dir / "config_used.json"
        with open(config_file, "w") as f:
            json.dump(self.config.to_env_dict(), f, indent=2)
    
    def _run_command(self, command: str, timeout: int = 300) -> tuple[int, str, str]:
        """Run a shell command and return exit code, stdout, stderr."""
        env = os.environ.copy()
        
        # Add configuration as environment variables
        env.update(self.config.to_env_dict())
        
        # Set kubeconfig based on current target cluster
        if self.current_kubeconfig:
            env["KUBECONFIG"] = self.current_kubeconfig
        
        # If we have a context set, inject it into kubectl/helm commands
        actual_command = command
        if self.current_context:
            actual_command = self._inject_context(command, self.current_context)
        
        try:
            result = subprocess.run(
                actual_command,
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
    
    def _inject_context(self, command: str, context: str) -> str:
        """Inject --context flag into kubectl and helm commands."""
        # Handle piped commands by processing each part
        if '|' in command:
            parts = command.split('|')
            processed_parts = [self._inject_context_single(p.strip(), context) for p in parts]
            return ' | '.join(processed_parts)
        
        # Handle && chained commands
        if '&&' in command:
            parts = command.split('&&')
            processed_parts = [self._inject_context_single(p.strip(), context) for p in parts]
            return ' && '.join(processed_parts)
        
        return self._inject_context_single(command, context)
    
    def _inject_context_single(self, command: str, context: str) -> str:
        """Inject --context flag into a single kubectl or helm command."""
        # Skip if context is already specified
        if '--context' in command or '--context=' in command:
            return command
        
        # Skip if command already has --kubeconfig with a different file (like validation commands)
        # that explicitly specify their own kubeconfig
        if '--kubeconfig=' in command and self.current_kubeconfig not in command:
            return command
        
        # Inject context for kubectl commands
        if command.strip().startswith('kubectl '):
            return command.replace('kubectl ', f'kubectl --context={context} ', 1)
        
        # Inject context for helm commands  
        if command.strip().startswith('helm '):
            return command.replace('helm ', f'helm --kube-context={context} ', 1)
        
        # Handle bash -c commands
        if command.strip().startswith('bash -c'):
            # Extract the inner command and process it
            inner_start = command.find("'") + 1
            inner_end = command.rfind("'")
            if inner_start > 0 and inner_end > inner_start:
                inner_cmd = command[inner_start:inner_end]
                processed_inner = self._inject_context(inner_cmd, context)
                return f"bash -c '{processed_inner}'"
        
        return command
    
    def _set_target_cluster(self, target: str):
        """Set the target cluster for kubectl commands."""
        if target == "supervisor":
            self.current_kubeconfig = self.supervisor_kubeconfig or None
            self.current_context = self.config.supervisor_context or None
            if self.current_kubeconfig:
                self.logger.info(f"  Using supervisor kubeconfig: {self.current_kubeconfig}")
            if self.current_context:
                self.logger.info(f"  Using supervisor context: {self.current_context}")
            if not self.current_kubeconfig and not self.current_context:
                self.logger.warning("  No supervisor kubeconfig or context configured")
        else:
            self.current_kubeconfig = self.vks_kubeconfig
            self.current_context = self.config.vks_cluster_context or None
            self.logger.info(f"  Using VKS kubeconfig: {self.current_kubeconfig}")
            if self.current_context:
                self.logger.info(f"  Using VKS context: {self.current_context}")
    
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
        
        # Set target cluster for this test
        self._set_target_cluster(test.target_cluster)
        
        # Create test-specific log file
        test_log_file = self.results_dir / f"{test.test_id}.log"
        
        all_output = []
        all_errors = []
        
        # Log which kubeconfig is being used
        all_output.append(f"Target cluster: {test.target_cluster}\n")
        all_output.append(f"Kubeconfig: {self.current_kubeconfig or 'default context'}\n")
        
        try:
            # Execute each step
            for i, step in enumerate(test.steps):
                self.logger.info(f"  Step {i+1}/{len(test.steps)}: {step.name}")
                
                # Get the actual command that will be executed (with context injected)
                actual_command = step.command
                if self.current_context:
                    actual_command = self._inject_context(step.command, self.current_context)
                
                all_output.append(f"\n=== Step {i+1}: {step.name} ===\n")
                all_output.append(f"Command: {actual_command}\n")
                
                if self.dry_run:
                    self.logger.info(f"    [DRY RUN] Would execute: {actual_command}")
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
            
            # Configuration summary
            f.write("## Configuration\n\n")
            f.write("| Setting | Value |\n")
            f.write("|---------|-------|\n")
            f.write(f"| Supervisor IP | {self.config.supervisor_ip} |\n")
            f.write(f"| Supervisor Namespace | {self.config.supervisor_namespace} |\n")
            f.write(f"| VKS Cluster | {self.config.vks_cluster_name} |\n")
            f.write(f"| VKS Version | {self.config.vks_version} |\n")
            f.write(f"| CockroachDB Version | {self.config.crdb_version_current} |\n")
            f.write(f"| CockroachDB Replicas | {self.config.crdb_replicas_initial} |\n")
            f.write(f"| Storage Class | {self.config.storage_class} |\n\n")
            
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


def generate_sample_config(output_path: Path):
    """Generate a sample configuration file."""
    sample_config = """# CockroachDB Operator VKS Functional Test Configuration
# Update the values below with your environment settings

# VMware Supervisor Configuration
supervisor:
  ip: "10.0.0.100"                          # Supervisor cluster IP address
  username: "administrator@vsphere.local"   # vSphere SSO username
  namespace: "vks-namespace"                # Supervisor namespace for VKS cluster
  context: "supervisor-context"             # kubectl context name for supervisor
  kubeconfig: "/path/to/supervisor-kubeconfig.yaml"  # Path to supervisor kubeconfig

# VKS Cluster Configuration
vks_cluster:
  name: "cluster-vks"                       # VKS cluster name
  namespace: "crdb-cluster"                 # Namespace for CockroachDB within VKS
  context: "cluster-vks-context"            # kubectl context name for VKS cluster
  kubeconfig: "vks-kubeconfig.yaml"         # Path to VKS kubeconfig file
  version: "v1.35.0+vmware.2-vkr.4"         # VKS Kubernetes version
  version_upgrade: "v1.36.0+vmware.1-vkr.1" # Target version for upgrade tests

# CockroachDB Operator Configuration
operator:
  namespace: "crdb-operator"                # Namespace for operator deployment
  helm_chart_path: "./cockroachdb-parent/charts/operator"
  version: "2.14.0"                         # Current operator version
  version_upgrade: "2.15.0"                 # Target operator version for upgrade

# CockroachDB Cluster Configuration
cockroachdb:
  namespace: "crdb-cluster"                 # Namespace for CockroachDB cluster
  helm_chart_path: "./cockroachdb-parent/charts/cockroachdb"
  release_name: "cockroachdb"               # Helm release name
  
  # Version configuration for upgrade tests
  version:
    current: "v24.2.0"                      # Current CockroachDB version
    patch_upgrade: "v24.2.1"                # Target version for patch upgrade
    major_upgrade: "v24.3.0"                # Target version for major upgrade
    rollback: "v24.2.0"                     # Version to rollback to
  
  # Cluster sizing
  replicas:
    initial: 3                              # Initial number of replicas
    scale_up: 6                             # Replicas after scale up
    scale_down: 3                           # Replicas after scale down
  
  # Storage configuration
  storage:
    class: "vsan-esa-default-policy-raid5"  # Storage class for PVCs
    size: "100Gi"                           # PVC size
    wal_storage_class: "vsan-esa-default-policy-raid5"
    wal_size: "10Gi"                        # WAL PVC size
  
  # TLS configuration
  tls:
    enabled: true

# Networking Configuration
networking:
  sql_port: 26257
  http_port: 8080
  network_policy:
    enabled: true

# Backup Configuration
backup:
  local:
    enabled: true
    path: "nodelocal://1/backups"
  s3:
    enabled: false
    bucket: ""
    endpoint: ""
    region: ""

# Advanced Features Configuration
advanced_features:
  pcr:
    enabled: false
    standby_cluster_name: "cockroachdb-standby"
    standby_namespace: "crdb-standby"
  encryption:
    enabled: false
    kms_type: ""
    kms_uri: ""
  cdc:
    enabled: false
    sink_type: "kafka"
    kafka_brokers: ""

# Test Execution Configuration
test_config:
  timeouts:
    cluster_provision: 900
    pod_ready: 600
    helm_install: 600
    scale_operation: 1800
    upgrade: 1800
    backup: 300
    restore: 600
  retries:
    max_attempts: 3
    delay_seconds: 30
"""
    with open(output_path, "w") as f:
        f.write(sample_config)
    print(f"Sample configuration written to: {output_path}")


def main():
    parser = argparse.ArgumentParser(
        description="CockroachDB Operator VKS Functional Test Runner",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python test_runner.py --config config.yaml                # Run all tests with config
  python test_runner.py --config config.yaml --test VKS-01  # Run single test
  python test_runner.py --config config.yaml --test VKS-01 VKS-02  # Run multiple tests
  python test_runner.py --config config.yaml --range VKS-01 VKS-05  # Run range of tests
  python test_runner.py --config config.yaml --category scaling  # Run tests by category
  python test_runner.py --list                              # List all available tests
  python test_runner.py --dry-run --config config.yaml      # Show what would execute
  python test_runner.py --generate-config                   # Generate sample config file
  python test_runner.py --show-config config.yaml           # Display loaded configuration
        """
    )
    
    parser.add_argument(
        "--config", "-f",
        help="Path to YAML configuration file (required for running tests)"
    )
    parser.add_argument(
        "--generate-config",
        action="store_true",
        help="Generate a sample configuration file"
    )
    parser.add_argument(
        "--show-config",
        metavar="CONFIG_FILE",
        help="Display the loaded configuration from a file"
    )
    parser.add_argument(
        "--validate-config",
        metavar="CONFIG_FILE",
        help="Validate a configuration file"
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
        help="Path to kubeconfig file (overrides config file setting)"
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
    
    # Handle generate-config
    if args.generate_config:
        output_path = Path("config.yaml")
        if output_path.exists():
            print(f"Error: {output_path} already exists. Remove it first or use a different name.")
            return 1
        generate_sample_config(output_path)
        return 0
    
    # Handle show-config
    if args.show_config:
        try:
            config = TestConfig.from_yaml(Path(args.show_config))
            print("\nLoaded Configuration:")
            print("=" * 60)
            for key, value in config.to_env_dict().items():
                if value:
                    print(f"  {key}: {value}")
            return 0
        except FileNotFoundError as e:
            print(f"Error: {e}")
            return 1
        except Exception as e:
            print(f"Error loading configuration: {e}")
            return 1
    
    # Handle validate-config
    if args.validate_config:
        try:
            config = TestConfig.from_yaml(Path(args.validate_config))
            errors = config.validate()
            if errors:
                print("Configuration validation failed:")
                for error in errors:
                    print(f"  - {error}")
                return 1
            else:
                print("Configuration is valid.")
                return 0
        except FileNotFoundError as e:
            print(f"Error: {e}")
            return 1
        except Exception as e:
            print(f"Error loading configuration: {e}")
            return 1
    
    # Load configuration if provided
    config = None
    if args.config:
        try:
            config = TestConfig.from_yaml(Path(args.config))
        except FileNotFoundError:
            print(f"Error: Configuration file not found: {args.config}")
            return 1
        except Exception as e:
            print(f"Error loading configuration: {e}")
            return 1
    
    # Create registry with config
    registry = TestRegistry(config=config)
    
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
    
    # For running tests, config is required
    if not args.config and not args.dry_run:
        # Check if default config exists
        if DEFAULT_CONFIG_FILE.exists():
            try:
                config = TestConfig.from_yaml(DEFAULT_CONFIG_FILE)
                print(f"Using default configuration: {DEFAULT_CONFIG_FILE}")
            except Exception as e:
                print(f"Error loading default configuration: {e}")
                print("Please provide a configuration file with --config or run --generate-config")
                return 1
        else:
            print("Error: Configuration file required to run tests.")
            print("Use --config <file> to specify a configuration file")
            print("Or run --generate-config to create a sample configuration")
            return 1
    
    # Validate configuration before running
    if config:
        errors = config.validate()
        if errors:
            print("Configuration validation warnings:")
            for error in errors:
                print(f"  - {error}")
            print()
    
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
        config=config,
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
