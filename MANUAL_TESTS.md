# Manual Tests - VKS Version Upgrades

The following tests require VKS Kubernetes cluster version upgrades and are **not automated** by the test runner. These tests must be performed manually.

## Tests Requiring Manual Execution

### VKS-11: Major Upgrade with Auto-Finalization
**Description:** Upgrade CockroachDB to a major version with automatic finalization

**Prerequisites:**
- CockroachDB cluster running on current version
- Target major version available

**Manual Steps:**
1. Check current version: `kubectl exec -n crdb-cluster cockroachdb-0 -- cockroach version`
2. Update Helm values with new version
3. Run upgrade: `helm upgrade cockroachdb cockroachdb/cockroachdb -n crdb-cluster --set image.tag=<new-version> --reuse-values --wait`
4. Monitor rolling restart: `kubectl rollout status statefulset/cockroachdb -n crdb-cluster`
5. Verify upgrade: `kubectl exec -n crdb-cluster cockroachdb-0 -- cockroach node status --insecure`

---

### VKS-12: Major Upgrade with Manual Finalization
**Description:** Upgrade CockroachDB to a major version with manual finalization

**Prerequisites:**
- CockroachDB cluster running on current version
- Target major version available

**Manual Steps:**
1. Disable auto-finalization: `kubectl exec -n crdb-cluster cockroachdb-0 -- cockroach sql --insecure -e "SET CLUSTER SETTING cluster.preserve_downgrade_option = '<current-version>';"`
2. Upgrade cluster image
3. Verify all nodes on new version
4. Run finalization: `kubectl exec -n crdb-cluster cockroachdb-0 -- cockroach sql --insecure -e "RESET CLUSTER SETTING cluster.preserve_downgrade_option;"`

---

### VKS-13: Rollback Patch Upgrade
**Description:** Rollback a CockroachDB patch upgrade

**Prerequisites:**
- CockroachDB cluster that has been upgraded

**Manual Steps:**
1. Record current version
2. Update Helm values with previous version
3. Run rollback: `helm upgrade cockroachdb cockroachdb/cockroachdb -n crdb-cluster --set image.tag=<previous-version> --reuse-values --wait`
4. Monitor rollback: `kubectl rollout status statefulset/cockroachdb -n crdb-cluster`
5. Verify rollback successful

---

### VKS-15: CRD Version Migration
**Description:** Migrate CockroachDB CRDs to a new version

**Prerequisites:**
- Operator installed with CRDs

**Manual Steps:**
1. Check current CRD versions: `kubectl get crds | grep cockroach`
2. Backup CRDs: `kubectl get crd <crd-name> -o yaml > crd-backup.yaml`
3. Apply new CRD versions
4. Verify migration: `kubectl get crds | grep cockroach`

---

### VKS-21: Physical Cluster Replication (PCR)
**Description:** Set up physical cluster replication between two CockroachDB clusters

**Prerequisites:**
- Two CockroachDB clusters
- Network connectivity between clusters

**Manual Steps:**
1. Configure primary cluster
2. Configure standby cluster
3. Establish replication
4. Verify replication status

---

### VKS-22: Encryption at Rest with KMS
**Description:** Enable encryption at rest using external KMS

**Prerequisites:**
- KMS service configured (AWS KMS, GCP KMS, or Azure Key Vault)
- Appropriate credentials

**Manual Steps:**
1. Create KMS key
2. Configure CockroachDB with KMS URI
3. Enable encryption
4. Verify encryption status

---

### VKS-23: Logical Replication
**Description:** Set up logical replication between clusters

**Prerequisites:**
- Two CockroachDB clusters
- Enterprise license

**Manual Steps:**
1. Configure changefeeds on source
2. Set up target tables
3. Verify replication

---

### VKS-24: CDC to Kafka
**Description:** Configure Change Data Capture to Kafka

**Prerequisites:**
- Kafka cluster accessible
- Enterprise license

**Manual Steps:**
1. Create Kafka topic
2. Configure changefeed: `CREATE CHANGEFEED FOR TABLE ... INTO 'kafka://...'`
3. Verify messages in Kafka

---

### VKS-25: Operator Coexistence with Legacy Operator
**Description:** Test running new operator alongside legacy public operator

**Prerequisites:**
- Legacy operator installed

**Manual Steps:**
1. Install new operator in separate namespace
2. Verify both operators running
3. Test cluster management with each operator

---

### VKS-27: VKS Kubernetes Cluster Upgrade
**Description:** Upgrade the underlying VKS Kubernetes cluster

**Prerequisites:**
- VKS cluster with CockroachDB running
- Target Kubernetes version available

**Manual Steps:**
1. Verify CockroachDB cluster healthy
2. Initiate VKS cluster upgrade through vSphere/VCF
3. Monitor node upgrades
4. Verify CockroachDB survives upgrade
5. Check cluster health post-upgrade

---

### VKS-28: VKS Minor Upgrade
**Description:** Perform a minor version upgrade of VKS

**Prerequisites:**
- VKS cluster running
- Minor version upgrade available

**Manual Steps:**
1. Record current VKS version
2. Initiate minor upgrade
3. Monitor upgrade progress
4. Verify CockroachDB cluster health

---

### VKS-29: WAL Failover Validation
**Description:** Validate Write-Ahead Log failover behavior

**Prerequisites:**
- CockroachDB cluster with WAL configured

**Manual Steps:**
1. Identify WAL storage location
2. Simulate WAL storage failure
3. Verify failover behavior
4. Restore and verify recovery

---

## Running Automated Tests

For tests that ARE automated, use the test runner:

```bash
# List available automated tests
python test_runner.py --list

# Run specific tests
python test_runner.py --test VKS-01 VKS-02

# Run all automated tests
python test_runner.py --all
```

## Test Results

All test results (automated and manual) should be documented in the `results/` directory with:
- Timestamp of execution
- Pass/Fail status
- Relevant logs and output
- Any issues encountered
