# VKS-11: Major-Version Upgrade with Auto-Finalization

## Test Information

| Field | Value |
|-------|-------|
| **Test ID** | VKS-11 |
| **Category** | Upgrades |
| **Dependencies** | [VKS-02](../01-cluster-provisioning/VKS-02-cluster-install.md) |

## Objective

Perform a major version upgrade of CockroachDB (e.g., v23.1.x to v23.2.x) with auto-finalization enabled, verify the rolling upgrade process, confirm automatic finalization, and validate that downgrade is blocked after finalization.

## Pre-requisites

- VKS-02 completed (CockroachDB cluster running)
- Backup completed (recommended before major upgrades)
- Target major version available and compatible
- Workload generator available (recommended)
- `kubectl` configured with VKS cluster kubeconfig
- `helm` CLI installed (v3.x)

## Environment Setup

```bash
# Ensure KUBECONFIG points to VKS cluster
export KUBECONFIG=$(pwd)/vks-kubeconfig.yaml

# Set environment variables
export CRDB_CLUSTER_NS="crdb-cluster"
export CRDB_RELEASE_NAME="cockroachdb"
export CRDB_CHART_PATH="./cockroachdb-parent/charts/cockroachdb"

# Get current version
CURRENT_VERSION=$(kubectl exec -n ${CRDB_CLUSTER_NS} cockroachdb-0 -- \
  cockroach sql --insecure --host=localhost:26257 \
  --execute="SELECT value FROM crdb_internal.node_build_info WHERE field='Version';" -f csv | tail -1)
echo "Current version: ${CURRENT_VERSION}"

# Set target major version (update as needed)
export TARGET_MAJOR_VERSION="v24.1.0"  # Example - update to actual target
echo "Target version: ${TARGET_MAJOR_VERSION}"
```

## Steps

### Step 1: Verify Upgrade Path Compatibility

```bash
# Check current cluster version
kubectl exec -n ${CRDB_CLUSTER_NS} cockroachdb-0 -- \
  cockroach sql --insecure --host=localhost:26257 \
  --execute="SHOW CLUSTER SETTING version;"

# Verify all nodes are on same version
kubectl exec -n ${CRDB_CLUSTER_NS} cockroachdb-0 -- \
  cockroach sql --insecure --host=localhost:26257 \
  --execute="
    SELECT 
      node_id,
      (SELECT value FROM crdb_internal.node_build_info WHERE field='Version') as version
    FROM crdb_internal.gossip_nodes
    ORDER BY node_id;
  "
```

### Step 2: Create Backup (Recommended)

```bash
# Create a backup before major upgrade
kubectl exec -n ${CRDB_CLUSTER_NS} cockroachdb-0 -- \
  cockroach sql --insecure --host=localhost:26257 \
  --execute="
    -- If you have a backup location configured:
    -- BACKUP INTO 'your-backup-location' AS OF SYSTEM TIME '-10s';
    
    -- For this test, we'll just note the current state
    SELECT now() as backup_timestamp;
  "

# Record current database state
kubectl exec -n ${CRDB_CLUSTER_NS} cockroachdb-0 -- \
  cockroach sql --insecure --host=localhost:26257 \
  --execute="
    SELECT 
      database_name,
      (SELECT count(*) FROM information_schema.tables WHERE table_catalog = database_name) as table_count
    FROM [SHOW DATABASES]
    WHERE database_name NOT IN ('system', 'postgres', 'defaultdb');
  " > /tmp/pre-major-upgrade-state.txt

cat /tmp/pre-major-upgrade-state.txt
```

### Step 3: Verify Auto-Finalization Setting

```bash
# Check current preserve_downgrade_option setting
kubectl exec -n ${CRDB_CLUSTER_NS} cockroachdb-0 -- \
  cockroach sql --insecure --host=localhost:26257 \
  --execute="SHOW CLUSTER SETTING cluster.preserve_downgrade_option;"

# For auto-finalization, this should be empty or not set
# If it's set, clear it:
kubectl exec -n ${CRDB_CLUSTER_NS} cockroachdb-0 -- \
  cockroach sql --insecure --host=localhost:26257 \
  --execute="RESET CLUSTER SETTING cluster.preserve_downgrade_option;"
```

**Expected Output:**
- `cluster.preserve_downgrade_option` should be empty for auto-finalization

### Step 4: Create Test Data

```bash
# Create test database for upgrade validation
kubectl exec -n ${CRDB_CLUSTER_NS} cockroachdb-0 -- \
  cockroach sql --insecure --host=localhost:26257 \
  --execute="
    CREATE DATABASE IF NOT EXISTS major_upgrade_test;
    USE major_upgrade_test;
    CREATE TABLE IF NOT EXISTS upgrade_data (
      id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
      value INT,
      version_created STRING DEFAULT (SELECT value FROM crdb_internal.node_build_info WHERE field='Version'),
      created_at TIMESTAMP DEFAULT now()
    );
    INSERT INTO upgrade_data (value) SELECT generate_series(1, 1000);
    SELECT count(*) as row_count FROM upgrade_data;
  "
```

### Step 5: Start Background Workload

```bash
# Start a continuous workload
cat <<'EOF' | kubectl apply -f -
apiVersion: v1
kind: Pod
metadata:
  name: major-upgrade-workload
  namespace: crdb-cluster
spec:
  containers:
  - name: workload
    image: cockroachdb/cockroach:latest
    command:
    - /bin/bash
    - -c
    - |
      while true; do
        cockroach sql --insecure --host=cockroachdb-public \
          --execute="INSERT INTO major_upgrade_test.upgrade_data (value) VALUES ($(date +%s));" 2>/dev/null
        sleep 2
      done
  restartPolicy: Never
EOF

kubectl wait --for=condition=Ready pod/major-upgrade-workload -n ${CRDB_CLUSTER_NS} --timeout=60s
```

### Step 6: Perform Major Version Upgrade

```bash
# Update to the new major version via Helm
helm upgrade ${CRDB_RELEASE_NAME} ${CRDB_CHART_PATH} \
  --namespace ${CRDB_CLUSTER_NS} \
  --reuse-values \
  --set image.tag=${TARGET_MAJOR_VERSION} \
  --timeout 30m
```

### Step 7: Monitor Rolling Upgrade

```bash
# Monitor the upgrade process
for i in {1..90}; do
  echo "=== Check $i ($(date)) ==="
  
  # Check pod status
  kubectl get pods -n ${CRDB_CLUSTER_NS}
  
  # Check binary versions
  echo "Binary versions:"
  kubectl exec -n ${CRDB_CLUSTER_NS} cockroachdb-0 -- \
    cockroach sql --insecure --host=localhost:26257 \
    --execute="SELECT node_id, (SELECT value FROM crdb_internal.node_build_info WHERE field='Tag') as binary_version FROM crdb_internal.gossip_nodes ORDER BY node_id;" 2>/dev/null || true
  
  # Check cluster version (logical version)
  echo "Cluster version:"
  kubectl exec -n ${CRDB_CLUSTER_NS} cockroachdb-0 -- \
    cockroach sql --insecure --host=localhost:26257 \
    --execute="SHOW CLUSTER SETTING version;" 2>/dev/null || true
  
  # Check if all pods are on new version
  NEW_VERSION_COUNT=$(kubectl get pods -n ${CRDB_CLUSTER_NS} -o jsonpath='{.items[*].spec.containers[0].image}' | tr ' ' '\n' | grep -c "${TARGET_MAJOR_VERSION}" || echo "0")
  TOTAL_PODS=$(kubectl get pods -n ${CRDB_CLUSTER_NS} --no-headers | wc -l)
  
  echo "Pods on new binary: ${NEW_VERSION_COUNT}/${TOTAL_PODS}"
  
  if [ "${NEW_VERSION_COUNT}" -eq "${TOTAL_PODS}" ]; then
    echo "All pods on new binary version!"
    break
  fi
  
  sleep 20
done
```

### Step 8: Wait for Auto-Finalization

```bash
# After all nodes are on new binary, auto-finalization should occur
# Monitor the cluster version
for i in {1..30}; do
  echo "=== Finalization Check $i ($(date)) ==="
  
  CLUSTER_VERSION=$(kubectl exec -n ${CRDB_CLUSTER_NS} cockroachdb-0 -- \
    cockroach sql --insecure --host=localhost:26257 \
    --execute="SHOW CLUSTER SETTING version;" -f csv 2>/dev/null | tail -1)
  
  echo "Current cluster version: ${CLUSTER_VERSION}"
  
  # Check if version matches target major
  if [[ "${CLUSTER_VERSION}" == *"24.1"* ]]; then  # Adjust pattern for your target
    echo "Finalization complete!"
    break
  fi
  
  sleep 30
done
```

### Step 9: Verify Finalization Completed

```bash
# Check cluster version
kubectl exec -n ${CRDB_CLUSTER_NS} cockroachdb-0 -- \
  cockroach sql --insecure --host=localhost:26257 \
  --execute="SHOW CLUSTER SETTING version;"

# Check preserve_downgrade_option is cleared
kubectl exec -n ${CRDB_CLUSTER_NS} cockroachdb-0 -- \
  cockroach sql --insecure --host=localhost:26257 \
  --execute="SHOW CLUSTER SETTING cluster.preserve_downgrade_option;"

# Verify all nodes report new version
kubectl exec -n ${CRDB_CLUSTER_NS} cockroachdb-0 -- \
  cockroach sql --insecure --host=localhost:26257 \
  --execute="
    SELECT 
      node_id,
      (SELECT value FROM crdb_internal.node_build_info WHERE field='Version') as version,
      is_live
    FROM crdb_internal.gossip_nodes
    ORDER BY node_id;
  "
```

### Step 10: Verify Downgrade is Blocked

```bash
# Attempt to set preserve_downgrade_option (should fail after finalization)
kubectl exec -n ${CRDB_CLUSTER_NS} cockroachdb-0 -- \
  cockroach sql --insecure --host=localhost:26257 \
  --execute="SET CLUSTER SETTING cluster.preserve_downgrade_option = '23.2';" 2>&1 || \
  echo "Expected: Downgrade option cannot be set after finalization"

# Verify the setting is still empty
kubectl exec -n ${CRDB_CLUSTER_NS} cockroachdb-0 -- \
  cockroach sql --insecure --host=localhost:26257 \
  --execute="SHOW CLUSTER SETTING cluster.preserve_downgrade_option;"
```

**Expected Output:**
- Setting preserve_downgrade_option should fail or be rejected
- Downgrade is not possible after finalization

### Step 11: Verify Data Integrity

```bash
# Check test data
kubectl exec -n ${CRDB_CLUSTER_NS} cockroachdb-0 -- \
  cockroach sql --insecure --host=localhost:26257 \
  --execute="
    USE major_upgrade_test;
    SELECT count(*) as total_rows FROM upgrade_data;
    SELECT count(DISTINCT version_created) as versions_seen FROM upgrade_data;
    SELECT version_created, count(*) as rows FROM upgrade_data GROUP BY version_created;
  "
```

### Step 12: Verify Cluster Health

```bash
# Full health check
kubectl exec -n ${CRDB_CLUSTER_NS} cockroachdb-0 -- \
  cockroach node status --insecure --host=localhost:26257

# Check for any issues
kubectl exec -n ${CRDB_CLUSTER_NS} cockroachdb-0 -- \
  cockroach sql --insecure --host=localhost:26257 \
  --execute="
    SELECT 
      'Live Nodes' as metric, count(*)::string as value 
    FROM crdb_internal.gossip_nodes WHERE is_live
    UNION ALL
    SELECT 
      'Under-replicated Ranges', count(*)::string 
    FROM crdb_internal.ranges 
    WHERE array_length(replicas, 1) < 3
    UNION ALL
    SELECT
      'Cluster Version', (SELECT value FROM [SHOW CLUSTER SETTING version]);
  "
```

## Validation Commands

```bash
# Complete validation script
echo "=== Major Upgrade with Auto-Finalization Validation ==="

echo -e "\n1. All pods running on new version:"
kubectl get pods -n ${CRDB_CLUSTER_NS} -o jsonpath='{range .items[*]}{.metadata.name}: {.spec.containers[0].image}{"\n"}{end}'

echo -e "\n2. Cluster version (finalized):"
kubectl exec -n ${CRDB_CLUSTER_NS} cockroachdb-0 -- \
  cockroach sql --insecure --host=localhost:26257 \
  --execute="SHOW CLUSTER SETTING version;"

echo -e "\n3. preserve_downgrade_option (should be empty):"
kubectl exec -n ${CRDB_CLUSTER_NS} cockroachdb-0 -- \
  cockroach sql --insecure --host=localhost:26257 \
  --execute="SHOW CLUSTER SETTING cluster.preserve_downgrade_option;"

echo -e "\n4. Node status:"
kubectl exec -n ${CRDB_CLUSTER_NS} cockroachdb-0 -- \
  cockroach node status --insecure --host=localhost:26257

echo -e "\n5. Data integrity:"
kubectl exec -n ${CRDB_CLUSTER_NS} cockroachdb-0 -- \
  cockroach sql --insecure --host=localhost:26257 \
  --execute="SELECT count(*) as rows FROM major_upgrade_test.upgrade_data;"

echo -e "\n=== Validation Complete ==="
```

## Expected Results

| Check | Expected State |
|-------|----------------|
| Rolling upgrade | Pods restart one-by-one |
| Binary version | All pods on target major version |
| Cluster version | Updated to target major version |
| Auto-finalization | Completed automatically |
| Downgrade option | Cannot be set (blocked) |
| Data integrity | No data loss |
| Cluster health | All nodes live, no under-replicated ranges |

## Cleanup

```bash
# Stop workload generator
kubectl delete pod major-upgrade-workload -n ${CRDB_CLUSTER_NS} 2>/dev/null

# Remove test database (optional)
kubectl exec -n ${CRDB_CLUSTER_NS} cockroachdb-0 -- \
  cockroach sql --insecure --host=localhost:26257 \
  --execute="DROP DATABASE IF EXISTS major_upgrade_test CASCADE;"

# Remove temporary files
rm -f /tmp/pre-major-upgrade-state.txt
```

## Notes

- Major upgrades may include schema migrations that run during finalization
- Auto-finalization happens automatically after all nodes are on new binary
- Once finalized, downgrade to previous major version is not possible
- Always have a backup before performing major upgrades
- The operator handles the rolling upgrade orchestration

## Troubleshooting

### Finalization Not Occurring

```bash
# Check if all nodes are on new binary
kubectl exec -n ${CRDB_CLUSTER_NS} cockroachdb-0 -- \
  cockroach sql --insecure --host=localhost:26257 \
  --execute="SELECT node_id, (SELECT value FROM crdb_internal.node_build_info WHERE field='Tag') FROM crdb_internal.gossip_nodes;"

# Check for migration jobs
kubectl exec -n ${CRDB_CLUSTER_NS} cockroachdb-0 -- \
  cockroach sql --insecure --host=localhost:26257 \
  --execute="SELECT * FROM crdb_internal.jobs WHERE job_type = 'MIGRATION' AND status != 'succeeded';"
```

### Upgrade Stuck

```bash
# Check pod status
kubectl describe pod -n ${CRDB_CLUSTER_NS} cockroachdb-0

# Check operator logs
kubectl logs -n crdb-operator -l app.kubernetes.io/name=cockroach-operator --tail=100

# Check CockroachDB logs
kubectl logs -n ${CRDB_CLUSTER_NS} cockroachdb-0 --tail=100
```

### Schema Migration Errors

```bash
# Check for failed jobs
kubectl exec -n ${CRDB_CLUSTER_NS} cockroachdb-0 -- \
  cockroach sql --insecure --host=localhost:26257 \
  --execute="SELECT * FROM crdb_internal.jobs WHERE status = 'failed';"

# Check system tables
kubectl exec -n ${CRDB_CLUSTER_NS} cockroachdb-0 -- \
  cockroach sql --insecure --host=localhost:26257 \
  --execute="SELECT * FROM system.migrations ORDER BY created DESC LIMIT 10;"
```
