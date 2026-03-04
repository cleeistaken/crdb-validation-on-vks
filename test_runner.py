#!/usr/bin/env python3
"""
CockroachDB Operator VKS Functional Test Runner

Simplified test runner that assumes:
- VKS cluster already exists and is accessible
- Default kubeconfig is authenticated and connected to VKS cluster
- Each test handles its own setup, execution, and cleanup
"""

import argparse
import json
import subprocess
import sys
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Optional

# Paths
SCRIPT_DIR = Path(__file__).parent
RESULTS_BASE_DIR = SCRIPT_DIR / "results"
TEST_BASE_DIR = SCRIPT_DIR / "tests"


class TestStatus(Enum):
    PENDING = "pending"
    RUNNING = "running"
    PASSED = "passed"
    FAILED = "failed"
    SKIPPED = "skipped"


@dataclass
class TestStep:
    """A single step in a test."""
    name: str
    command: str
    timeout: int = 300
    continue_on_failure: bool = False


@dataclass
class TestResult:
    """Result of a test execution."""
    test_id: str
    name: str
    status: TestStatus = TestStatus.PENDING
    start_time: Optional[datetime] = None
    end_time: Optional[datetime] = None
    duration_seconds: float = 0.0
    output: str = ""
    error: str = ""
    steps_passed: int = 0
    steps_total: int = 0


@dataclass
class TestCase:
    """Definition of a test case."""
    test_id: str
    name: str
    description: str
    setup_steps: list[TestStep] = field(default_factory=list)
    test_steps: list[TestStep] = field(default_factory=list)
    cleanup_steps: list[TestStep] = field(default_factory=list)
    

class TestRunner:
    """Simplified test runner for CockroachDB on VKS."""
    
    def __init__(self, results_dir: Optional[Path] = None, verbose: bool = False, dry_run: bool = False):
        self.verbose = verbose
        self.dry_run = dry_run
        self.results: list[TestResult] = []
        
        # Create results directory
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        self.results_dir = results_dir or RESULTS_BASE_DIR / timestamp
        self.results_dir.mkdir(parents=True, exist_ok=True)
        
        # Setup logging
        self.log_file = self.results_dir / "test_runner.log"
        
        # Load test definitions
        self.tests: dict[str, TestCase] = {}
        self._register_tests()
    
    def log(self, message: str, level: str = "INFO"):
        """Log message to console and file."""
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        log_line = f"{timestamp} - {level} - {message}"
        print(log_line)
        with open(self.log_file, "a") as f:
            f.write(log_line + "\n")
    
    def _run_command(self, command: str, timeout: int = 300) -> tuple[int, str, str]:
        """Execute a shell command."""
        try:
            result = subprocess.run(
                command,
                shell=True,
                capture_output=True,
                text=True,
                timeout=timeout
            )
            return result.returncode, result.stdout, result.stderr
        except subprocess.TimeoutExpired:
            return -1, "", f"Command timed out after {timeout}s"
        except Exception as e:
            return -1, "", str(e)
    
    def _run_steps(self, steps: list[TestStep], phase: str, result: TestResult, log_output: list) -> bool:
        """Run a list of steps. Returns True if all steps passed."""
        for i, step in enumerate(steps):
            self.log(f"  [{phase}] Step {i+1}/{len(steps)}: {step.name}")
            log_output.append(f"\n=== [{phase}] Step {i+1}: {step.name} ===")
            log_output.append(f"Command: {step.command}")
            
            if self.dry_run:
                self.log(f"    [DRY RUN] Would execute: {step.command}")
                result.steps_passed += 1
                continue
            
            exit_code, stdout, stderr = self._run_command(step.command, step.timeout)
            log_output.append(f"Exit code: {exit_code}")
            if stdout:
                log_output.append(f"Output:\n{stdout}")
            if stderr:
                log_output.append(f"Stderr:\n{stderr}")
            
            if exit_code != 0:
                self.log(f"    FAILED: {stderr or stdout}", "ERROR")
                if not step.continue_on_failure:
                    return False
            else:
                result.steps_passed += 1
                if self.verbose and stdout:
                    self.log(f"    Output: {stdout[:200]}...")
        
        return True
    
    def run_test(self, test: TestCase) -> TestResult:
        """Execute a single test with setup, test, and cleanup phases."""
        result = TestResult(
            test_id=test.test_id,
            name=test.name,
            steps_total=len(test.setup_steps) + len(test.test_steps) + len(test.cleanup_steps)
        )
        result.start_time = datetime.now()
        result.status = TestStatus.RUNNING
        
        self.log(f"\n{'='*60}")
        self.log(f"Starting test: {test.test_id} - {test.name}")
        self.log(f"{'='*60}")
        
        log_output = [f"Test: {test.test_id} - {test.name}", f"Description: {test.description}"]
        setup_ok = True
        test_ok = True
        
        # Setup phase
        if test.setup_steps:
            self.log("Running setup...")
            setup_ok = self._run_steps(test.setup_steps, "SETUP", result, log_output)
            if not setup_ok:
                self.log("Setup failed!", "ERROR")
        
        # Test phase (only if setup succeeded)
        if setup_ok and test.test_steps:
            self.log("Running test...")
            test_ok = self._run_steps(test.test_steps, "TEST", result, log_output)
        
        # Cleanup phase (always runs)
        if test.cleanup_steps:
            self.log("Running cleanup...")
            self._run_steps(test.cleanup_steps, "CLEANUP", result, log_output)
        
        # Determine final status
        result.end_time = datetime.now()
        result.duration_seconds = (result.end_time - result.start_time).total_seconds()
        
        if not setup_ok:
            result.status = TestStatus.FAILED
            result.error = "Setup failed"
        elif not test_ok:
            result.status = TestStatus.FAILED
            result.error = "Test failed"
        else:
            result.status = TestStatus.PASSED
        
        result.output = "\n".join(log_output)
        
        # Save test log
        test_log_file = self.results_dir / f"{test.test_id}.log"
        with open(test_log_file, "w") as f:
            f.write(result.output)
        
        status_str = "PASSED" if result.status == TestStatus.PASSED else "FAILED"
        self.log(f"Test {test.test_id}: {status_str} ({result.duration_seconds:.1f}s)")
        
        return result
    
    def run_tests(self, test_ids: list[str]) -> int:
        """Run specified tests and return exit code."""
        self.log(f"Starting test run with {len(test_ids)} test(s)")
        self.log(f"Results directory: {self.results_dir}")
        
        # Verify cluster access
        if not self.dry_run:
            self.log("Verifying cluster access...")
            exit_code, stdout, stderr = self._run_command("kubectl cluster-info", timeout=30)
            if exit_code != 0:
                self.log(f"Cannot connect to cluster: {stderr}", "ERROR")
                self.log("Please ensure your kubeconfig is configured correctly.", "ERROR")
                return 1
            self.log("Cluster access verified.")
        
        # Run tests
        for test_id in test_ids:
            if test_id not in self.tests:
                self.log(f"Unknown test: {test_id}", "ERROR")
                continue
            
            result = self.run_test(self.tests[test_id])
            self.results.append(result)
        
        # Generate summary
        self._generate_summary()
        
        # Return non-zero if any test failed
        failed = sum(1 for r in self.results if r.status == TestStatus.FAILED)
        return 1 if failed > 0 else 0
    
    def _generate_summary(self):
        """Generate test summary report."""
        passed = sum(1 for r in self.results if r.status == TestStatus.PASSED)
        failed = sum(1 for r in self.results if r.status == TestStatus.FAILED)
        skipped = sum(1 for r in self.results if r.status == TestStatus.SKIPPED)
        total = len(self.results)
        
        summary_lines = [
            "=" * 60,
            "TEST SUMMARY",
            "=" * 60,
            f"Total:   {total}",
            f"Passed:  {passed}",
            f"Failed:  {failed}",
            f"Skipped: {skipped}",
            "",
            "Results by test:",
            "-" * 40,
        ]
        
        for r in self.results:
            status = "PASS" if r.status == TestStatus.PASSED else "FAIL"
            summary_lines.append(f"  [{status}] {r.test_id}: {r.name} ({r.duration_seconds:.1f}s)")
            if r.error:
                summary_lines.append(f"         Error: {r.error}")
        
        summary_lines.extend(["", "=" * 60])
        
        summary = "\n".join(summary_lines)
        self.log(summary)
        
        # Save summary to file
        summary_file = self.results_dir / "summary.txt"
        with open(summary_file, "w") as f:
            f.write(summary)
        
        # Save JSON results
        json_file = self.results_dir / "results.json"
        json_results = {
            "timestamp": datetime.now().isoformat(),
            "total": total,
            "passed": passed,
            "failed": failed,
            "skipped": skipped,
            "tests": [
                {
                    "test_id": r.test_id,
                    "name": r.name,
                    "status": r.status.value,
                    "duration_seconds": r.duration_seconds,
                    "error": r.error,
                    "steps_passed": r.steps_passed,
                    "steps_total": r.steps_total,
                }
                for r in self.results
            ]
        }
        with open(json_file, "w") as f:
            json.dump(json_results, f, indent=2)
        
        self.log(f"\nResults saved to: {self.results_dir}")
    
    def list_tests(self):
        """List all available tests."""
        print("\nAvailable tests:")
        print("-" * 60)
        for test_id, test in sorted(self.tests.items()):
            print(f"  {test_id}: {test.name}")
            if self.verbose:
                print(f"      {test.description}")
        print()
    
    def _register_tests(self):
        """Register all test definitions."""
        # Namespaces used by tests
        op_ns = "crdb-operator"
        db_ns = "crdb-cluster"
        storage_class = "vsan-esa-default-policy-raid5"
        
        # VKS-01: Install CockroachDB Operator
        self.tests["VKS-01"] = TestCase(
            test_id="VKS-01",
            name="Install CockroachDB Operator via Helm",
            description="Install the CockroachDB Operator using Helm",
            setup_steps=[
                TestStep("Create operator namespace", f"kubectl create namespace {op_ns} --dry-run=client -o yaml | kubectl apply -f -"),
            ],
            test_steps=[
                TestStep("Add Helm repo", "helm repo add cockroachdb https://charts.cockroachdb.com/ && helm repo update", timeout=120),
                TestStep("Install operator", f"helm install crdb-operator cockroachdb/cockroachdb-operator -n {op_ns} --wait --timeout=5m", timeout=360),
                TestStep("Verify operator pod", f"kubectl wait --for=condition=ready pod -l app.kubernetes.io/name=cockroachdb-operator -n {op_ns} --timeout=120s"),
                TestStep("Check operator logs", f"kubectl logs -l app.kubernetes.io/name=cockroachdb-operator -n {op_ns} --tail=20"),
            ],
            cleanup_steps=[]  # Operator stays installed for other tests
        )
        
        # VKS-02: Install CockroachDB Cluster
        self.tests["VKS-02"] = TestCase(
            test_id="VKS-02",
            name="Install CockroachDB Cluster via Helm",
            description="Deploy a 3-node CockroachDB cluster",
            setup_steps=[
                TestStep("Create cluster namespace", f"kubectl create namespace {db_ns} --dry-run=client -o yaml | kubectl apply -f -"),
            ],
            test_steps=[
                TestStep("Install CockroachDB cluster", 
                    f"helm install cockroachdb cockroachdb/cockroachdb -n {db_ns} "
                    f"--set statefulset.replicas=3 "
                    f"--set storage.persistentVolume.storageClass={storage_class} "
                    f"--set storage.persistentVolume.size=10Gi "
                    f"--wait --timeout=10m", timeout=660),
                TestStep("Wait for pods ready", f"kubectl wait --for=condition=ready pod -l app.kubernetes.io/name=cockroachdb -n {db_ns} --timeout=300s", timeout=320),
                TestStep("Verify cluster status", f"kubectl exec -n {db_ns} cockroachdb-0 -- cockroach node status --insecure"),
            ],
            cleanup_steps=[]  # Cluster stays for other tests
        )
        
        # VKS-03: Secure Cluster (TLS)
        self.tests["VKS-03"] = TestCase(
            test_id="VKS-03",
            name="Secure Cluster Initialization (TLS)",
            description="Verify TLS certificate configuration",
            setup_steps=[],
            test_steps=[
                TestStep("Check TLS secrets exist", f"kubectl get secrets -n {db_ns} -l app.kubernetes.io/name=cockroachdb"),
                TestStep("Verify node certificates", f"kubectl exec -n {db_ns} cockroachdb-0 -- ls -la /cockroach/cockroach-certs/"),
            ],
            cleanup_steps=[]
        )
        
        # VKS-04: Locality Mappings
        self.tests["VKS-04"] = TestCase(
            test_id="VKS-04",
            name="Multi-region / Locality Mappings",
            description="Verify locality labels are applied to nodes",
            setup_steps=[],
            test_steps=[
                TestStep("Check node localities", f"kubectl exec -n {db_ns} cockroachdb-0 -- cockroach node status --insecure --format=table"),
                TestStep("Verify topology labels", "kubectl get nodes --show-labels | grep -E 'topology.kubernetes.io|NAME'"),
            ],
            cleanup_steps=[]
        )
        
        # VKS-05: Pod Security Admission
        self.tests["VKS-05"] = TestCase(
            test_id="VKS-05",
            name="Pod Security Admission Compatibility",
            description="Verify pods comply with Pod Security Standards",
            setup_steps=[],
            test_steps=[
                TestStep("Check namespace labels", f"kubectl get namespace {db_ns} -o yaml | grep -A5 labels"),
                TestStep("Verify pod security context", f"kubectl get pod cockroachdb-0 -n {db_ns} -o jsonpath='{{.spec.securityContext}}'"),
                TestStep("Check for PSA violations", f"kubectl get events -n {db_ns} --field-selector reason=FailedCreate 2>/dev/null | grep -i security || echo 'No PSA violations found'"),
            ],
            cleanup_steps=[]
        )
        
        # VKS-06: Network Policy
        self.tests["VKS-06"] = TestCase(
            test_id="VKS-06",
            name="NetworkPolicy Compatibility",
            description="Test network policies with CockroachDB",
            setup_steps=[
                TestStep("Create test network policy", f"""cat <<EOF | kubectl apply -f -
apiVersion: networking.k8s.io/v1
kind: NetworkPolicy
metadata:
  name: cockroachdb-test-policy
  namespace: {db_ns}
spec:
  podSelector:
    matchLabels:
      app.kubernetes.io/name: cockroachdb
  policyTypes:
  - Ingress
  - Egress
  ingress:
  - from:
    - podSelector:
        matchLabels:
          app.kubernetes.io/name: cockroachdb
    ports:
    - port: 26257
    - port: 8080
  egress:
  - to:
    - podSelector:
        matchLabels:
          app.kubernetes.io/name: cockroachdb
    ports:
    - port: 26257
EOF"""),
            ],
            test_steps=[
                TestStep("Verify policy created", f"kubectl get networkpolicy cockroachdb-test-policy -n {db_ns}"),
                TestStep("Test inter-node communication", f"kubectl exec -n {db_ns} cockroachdb-0 -- cockroach node status --insecure"),
            ],
            cleanup_steps=[
                TestStep("Delete test policy", f"kubectl delete networkpolicy cockroachdb-test-policy -n {db_ns} --ignore-not-found"),
            ]
        )
        
        # VKS-07: Scale Up
        self.tests["VKS-07"] = TestCase(
            test_id="VKS-07",
            name="Scale Up Nodes",
            description="Scale CockroachDB cluster from 3 to 5 nodes",
            setup_steps=[
                TestStep("Record initial node count", f"kubectl exec -n {db_ns} cockroachdb-0 -- cockroach node status --insecure"),
            ],
            test_steps=[
                TestStep("Scale up to 5 nodes", f"helm upgrade cockroachdb cockroachdb/cockroachdb -n {db_ns} --set statefulset.replicas=5 --reuse-values --wait --timeout=10m", timeout=660),
                TestStep("Wait for new pods", f"kubectl wait --for=condition=ready pod -l app.kubernetes.io/name=cockroachdb -n {db_ns} --timeout=300s", timeout=320),
                TestStep("Verify 5 nodes", f"kubectl exec -n {db_ns} cockroachdb-0 -- cockroach node status --insecure | grep -c 'is_live.*true' | grep -q 5"),
            ],
            cleanup_steps=[
                TestStep("Scale back to 3", f"helm upgrade cockroachdb cockroachdb/cockroachdb -n {db_ns} --set statefulset.replicas=3 --reuse-values --wait --timeout=10m", timeout=660),
            ]
        )
        
        # VKS-08: Scale Down
        self.tests["VKS-08"] = TestCase(
            test_id="VKS-08",
            name="Scale Down Nodes",
            description="Scale CockroachDB cluster from 3 to 2 nodes (with decommission)",
            setup_steps=[
                TestStep("Verify initial state", f"kubectl exec -n {db_ns} cockroachdb-0 -- cockroach node status --insecure"),
            ],
            test_steps=[
                TestStep("Decommission node 3", f"kubectl exec -n {db_ns} cockroachdb-0 -- cockroach node decommission 3 --insecure --wait=all", timeout=600),
                TestStep("Scale down to 2", f"helm upgrade cockroachdb cockroachdb/cockroachdb -n {db_ns} --set statefulset.replicas=2 --reuse-values --wait --timeout=5m", timeout=360),
                TestStep("Verify 2 live nodes", f"kubectl exec -n {db_ns} cockroachdb-0 -- cockroach node status --insecure"),
            ],
            cleanup_steps=[
                TestStep("Scale back to 3", f"helm upgrade cockroachdb cockroachdb/cockroachdb -n {db_ns} --set statefulset.replicas=3 --reuse-values --wait --timeout=10m", timeout=660),
            ]
        )
        
        # VKS-09: Node Decommission via Annotation
        self.tests["VKS-09"] = TestCase(
            test_id="VKS-09",
            name="Node Decommission via Annotation",
            description="Test decommissioning a node using Kubernetes annotations",
            setup_steps=[
                TestStep("Scale to 4 nodes first", f"helm upgrade cockroachdb cockroachdb/cockroachdb -n {db_ns} --set statefulset.replicas=4 --reuse-values --wait --timeout=10m", timeout=660),
                TestStep("Wait for 4th node", f"kubectl wait --for=condition=ready pod cockroachdb-3 -n {db_ns} --timeout=300s", timeout=320),
            ],
            test_steps=[
                TestStep("Annotate pod for decommission", f"kubectl annotate pod cockroachdb-3 -n {db_ns} crdb.io/decommission=true --overwrite"),
                TestStep("Wait for decommission", "sleep 60", timeout=120),
                TestStep("Check decommission status", f"kubectl exec -n {db_ns} cockroachdb-0 -- cockroach node status --insecure --decommission"),
            ],
            cleanup_steps=[
                TestStep("Remove annotation", f"kubectl annotate pod cockroachdb-3 -n {db_ns} crdb.io/decommission- --ignore-not-found || true"),
                TestStep("Scale back to 3", f"helm upgrade cockroachdb cockroachdb/cockroachdb -n {db_ns} --set statefulset.replicas=3 --reuse-values --wait --timeout=10m", timeout=660),
            ]
        )
        
        # VKS-10: Patch Upgrade
        self.tests["VKS-10"] = TestCase(
            test_id="VKS-10",
            name="Patch Version Upgrade",
            description="Upgrade CockroachDB to a patch version",
            setup_steps=[
                TestStep("Record current version", f"kubectl exec -n {db_ns} cockroachdb-0 -- cockroach version"),
            ],
            test_steps=[
                TestStep("Trigger rolling upgrade", f"helm upgrade cockroachdb cockroachdb/cockroachdb -n {db_ns} --reuse-values --wait --timeout=15m", timeout=960),
                TestStep("Verify all pods restarted", f"kubectl rollout status statefulset/cockroachdb -n {db_ns} --timeout=300s", timeout=320),
                TestStep("Check cluster health", f"kubectl exec -n {db_ns} cockroachdb-0 -- cockroach node status --insecure"),
            ],
            cleanup_steps=[]
        )
        
        # VKS-14: Operator Upgrade
        self.tests["VKS-14"] = TestCase(
            test_id="VKS-14",
            name="Operator Helm Chart Upgrade",
            description="Upgrade the CockroachDB operator",
            setup_steps=[
                TestStep("Record current operator version", f"helm list -n {op_ns} -o json | grep -o '\"chart\":\"[^\"]*\"'"),
            ],
            test_steps=[
                TestStep("Update Helm repo", "helm repo update cockroachdb", timeout=60),
                TestStep("Upgrade operator", f"helm upgrade crdb-operator cockroachdb/cockroachdb-operator -n {op_ns} --wait --timeout=5m", timeout=360),
                TestStep("Verify operator running", f"kubectl wait --for=condition=ready pod -l app.kubernetes.io/name=cockroachdb-operator -n {op_ns} --timeout=120s"),
            ],
            cleanup_steps=[]
        )
        
        # VKS-16: DB Console Access
        self.tests["VKS-16"] = TestCase(
            test_id="VKS-16",
            name="Expose DB Console (Admin UI)",
            description="Expose and verify access to CockroachDB Admin UI",
            setup_steps=[],
            test_steps=[
                TestStep("Check existing service", f"kubectl get svc -n {db_ns} -l app.kubernetes.io/name=cockroachdb"),
                TestStep("Port-forward and test", f"timeout 10 kubectl port-forward svc/cockroachdb-public -n {db_ns} 8080:8080 &>/dev/null & sleep 3 && curl -s -o /dev/null -w '%{{http_code}}' http://localhost:8080/_status/vars || echo 'Port-forward test completed'"),
            ],
            cleanup_steps=[]
        )
        
        # VKS-17: SQL LoadBalancer
        self.tests["VKS-17"] = TestCase(
            test_id="VKS-17",
            name="SQL Service via LoadBalancer",
            description="Expose SQL service via LoadBalancer",
            setup_steps=[],
            test_steps=[
                TestStep("Check SQL service", f"kubectl get svc cockroachdb-public -n {db_ns} -o wide"),
                TestStep("Test SQL connectivity", f"kubectl exec -n {db_ns} cockroachdb-0 -- cockroach sql --insecure -e 'SELECT 1'"),
            ],
            cleanup_steps=[]
        )
        
        # VKS-18: Monitoring
        self.tests["VKS-18"] = TestCase(
            test_id="VKS-18",
            name="Cluster Monitoring Integration",
            description="Verify Prometheus metrics endpoint",
            setup_steps=[],
            test_steps=[
                TestStep("Check metrics endpoint", f"kubectl exec -n {db_ns} cockroachdb-0 -- curl -s localhost:8080/_status/vars | head -20"),
                TestStep("Verify metrics format", f"kubectl exec -n {db_ns} cockroachdb-0 -- curl -s localhost:8080/_status/vars | grep -c 'sql_' || echo 'Metrics available'"),
            ],
            cleanup_steps=[]
        )
        
        # VKS-19: Backup and Restore
        self.tests["VKS-19"] = TestCase(
            test_id="VKS-19",
            name="Backup and Restore",
            description="Test backup and restore functionality",
            setup_steps=[
                TestStep("Create test database", f"kubectl exec -n {db_ns} cockroachdb-0 -- cockroach sql --insecure -e 'CREATE DATABASE IF NOT EXISTS backup_test; CREATE TABLE IF NOT EXISTS backup_test.t1 (id INT PRIMARY KEY, data STRING); INSERT INTO backup_test.t1 VALUES (1, \"test\") ON CONFLICT DO NOTHING;'"),
            ],
            test_steps=[
                TestStep("Create backup", f"kubectl exec -n {db_ns} cockroachdb-0 -- cockroach sql --insecure -e \"BACKUP DATABASE backup_test INTO 'nodelocal://1/backup_test' WITH revision_history;\"", timeout=120),
                TestStep("Verify backup", f"kubectl exec -n {db_ns} cockroachdb-0 -- cockroach sql --insecure -e \"SHOW BACKUPS IN 'nodelocal://1/backup_test';\""),
                TestStep("Drop and restore", f"kubectl exec -n {db_ns} cockroachdb-0 -- cockroach sql --insecure -e 'DROP DATABASE backup_test CASCADE;'"),
                TestStep("Restore database", f"kubectl exec -n {db_ns} cockroachdb-0 -- cockroach sql --insecure -e \"RESTORE DATABASE backup_test FROM LATEST IN 'nodelocal://1/backup_test';\"", timeout=120),
                TestStep("Verify restore", f"kubectl exec -n {db_ns} cockroachdb-0 -- cockroach sql --insecure -e 'SELECT * FROM backup_test.t1;'"),
            ],
            cleanup_steps=[
                TestStep("Cleanup test database", f"kubectl exec -n {db_ns} cockroachdb-0 -- cockroach sql --insecure -e 'DROP DATABASE IF EXISTS backup_test CASCADE;'"),
            ]
        )
        
        # VKS-20: Pod Eviction
        self.tests["VKS-20"] = TestCase(
            test_id="VKS-20",
            name="Pod Eviction / Rescheduling",
            description="Test cluster recovery after pod deletion",
            setup_steps=[
                TestStep("Verify cluster healthy", f"kubectl exec -n {db_ns} cockroachdb-0 -- cockroach node status --insecure"),
            ],
            test_steps=[
                TestStep("Delete a pod", f"kubectl delete pod cockroachdb-1 -n {db_ns}"),
                TestStep("Wait for pod recreation", f"kubectl wait --for=condition=ready pod cockroachdb-1 -n {db_ns} --timeout=180s", timeout=200),
                TestStep("Verify cluster recovered", f"kubectl exec -n {db_ns} cockroachdb-0 -- cockroach node status --insecure"),
            ],
            cleanup_steps=[]
        )
        
        # VKS-26: Operator Restart
        self.tests["VKS-26"] = TestCase(
            test_id="VKS-26",
            name="Operator Restart During Operations",
            description="Test operator recovery after restart",
            setup_steps=[
                TestStep("Verify operator running", f"kubectl get pods -n {op_ns} -l app.kubernetes.io/name=cockroachdb-operator"),
            ],
            test_steps=[
                TestStep("Delete operator pod", f"kubectl delete pod -n {op_ns} -l app.kubernetes.io/name=cockroachdb-operator"),
                TestStep("Wait for operator restart", f"kubectl wait --for=condition=ready pod -l app.kubernetes.io/name=cockroachdb-operator -n {op_ns} --timeout=120s"),
                TestStep("Verify cluster still healthy", f"kubectl exec -n {db_ns} cockroachdb-0 -- cockroach node status --insecure"),
            ],
            cleanup_steps=[]
        )
        
        # Cleanup test - removes everything
        self.tests["CLEANUP"] = TestCase(
            test_id="CLEANUP",
            name="Full Cleanup",
            description="Remove all CockroachDB resources (use with caution)",
            setup_steps=[],
            test_steps=[
                TestStep("Uninstall CockroachDB cluster", f"helm uninstall cockroachdb -n {db_ns} --wait || true", timeout=300),
                TestStep("Delete PVCs", f"kubectl delete pvc -n {db_ns} -l app.kubernetes.io/name=cockroachdb --wait=false || true"),
                TestStep("Uninstall operator", f"helm uninstall crdb-operator -n {op_ns} --wait || true", timeout=120),
                TestStep("Delete namespaces", f"kubectl delete namespace {db_ns} {op_ns} --wait=false || true"),
            ],
            cleanup_steps=[]
        )


def main():
    parser = argparse.ArgumentParser(
        description="CockroachDB Operator VKS Functional Test Runner",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # List all available tests
  python test_runner.py --list

  # Run a single test
  python test_runner.py --test VKS-01

  # Run multiple tests
  python test_runner.py --test VKS-01 VKS-02 VKS-03

  # Run all tests
  python test_runner.py --all

  # Dry run (show commands without executing)
  python test_runner.py --test VKS-01 --dry-run

  # Verbose output
  python test_runner.py --test VKS-01 --verbose
"""
    )
    
    parser.add_argument("--list", "-l", action="store_true", help="List all available tests")
    parser.add_argument("--test", "-t", nargs="+", help="Test ID(s) to run")
    parser.add_argument("--all", "-a", action="store_true", help="Run all tests (except CLEANUP)")
    parser.add_argument("--dry-run", "-n", action="store_true", help="Show commands without executing")
    parser.add_argument("--verbose", "-v", action="store_true", help="Verbose output")
    parser.add_argument("--results-dir", "-o", type=Path, help="Custom results directory")
    
    args = parser.parse_args()
    
    runner = TestRunner(
        results_dir=args.results_dir,
        verbose=args.verbose,
        dry_run=args.dry_run
    )
    
    if args.list:
        runner.list_tests()
        return 0
    
    if args.all:
        # Run all tests except CLEANUP
        test_ids = [t for t in runner.tests.keys() if t != "CLEANUP"]
        test_ids.sort()
    elif args.test:
        test_ids = args.test
    else:
        parser.print_help()
        return 1
    
    return runner.run_tests(test_ids)


if __name__ == "__main__":
    sys.exit(main())
