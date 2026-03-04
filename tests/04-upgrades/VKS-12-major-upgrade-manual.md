# VKS-12: Major-Version Upgrade with Manual Finalization

## Test Information

| Field | Value |
|-------|-------|
| **Test ID** | VKS-12 |
| **Category** | Upgrades |
| **Dependencies** | [VKS-02](../01-cluster-provisioning/VKS-02-cluster-install.md) |

## Objective

Perform a major version upgrade with manual finalization control, verify the cluster operates in mixed-version gate mode, run validation workloads before finalizing, and then manually trigger finalization.

## Pre-requisites

- VKS-02 completed (CockroachDB cluster running)
- Backup completed (recommended before major upgrades)
- Target major version available and compatible
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
  --execute="SHOW CLUSTER SETTING version;" -f csv | tail -1)
echo "Current cluster version: ${CURRENT_VERSION}"

# Extract major.minor for preserve_downgrade_option
SOURCE_VERSION=$(echo ${CURRENT_VERSION} | grep -oE '^[0-9]+\.[0-9]+')
echo "Source version for downgrade option: ${SOURCE_VERSION}"

# Set target major version
export TARGET_MAJOR_VERSION="v24.1.0"  # Example - update as needed
echo "Target version: ${TARGET_MAJOR_VERSION}"
```

## Steps

### Step 1: Set Preserve Downgrade Option

```bash
# Set the preserve_downgrade_option to prevent auto-finalization
kubectl exec -n ${CRDB_CLUSTER_NS} cockroachdb-0 -- \
  cockroach sql --insecure --host=localhost:26257 \
  --execute="SET CLUSTER SETTING cluster.preserve_downgrade_option = '${SOURCE_VERSION}';"

# Verify the setting
kubectl exec -n ${CRDB_CLUSTER_NS} cockroachdb-0 -- \
  cockroach sql --insecure --host=localhost:26257 \
  --execute="SHOW CLUSTER SETTING cluster.preserve_downgrade_option;"
```

**Expected Output:**
```
  cluster.preserve_downgrade_option
------------------------------------
  23.2
```

### Step 2: Record Pre-Upgrade State

```bash
# Record current state
kubectl exec -n ${CRDB_CLUSTER_NS} cockroachdb-0 -- \
  cockroach sql --insecure --host=localhost:26257 \
  --execute="
    SELECT 
      'Cluster Version' as setting, 
      (SELECT value FROM [SHOW CLUSTER SETTING version]) as value
    UNION ALL
    SELECT 
      'Preserve Downgrade',
      (SELECT value FROM [SHOW CLUSTER SETTING cluster.preserve_downgrade_option]);
  " > /tmp/pre-manual-upgrade-state.txt

cat /tmp/pre-manual-upgrade-state.txt
```

### Step 3: Create Test Data

```bash
# Create test database
kubectl exec -n ${CRDB_CLUSTER_NS} cockroachdb-0 -- \
  cockroach sql --insecure --host=localhost:26257 \
  --execute="
    CREATE DATABASE IF NOT EXISTS manual_upgrade_test;
    USE manual_upgrade_test;
    CREATE TABLE IF NOT EXISTS validation_data (
      id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
      value INT,
      phase STRING DEFAULT 'pre-upgrade',
      created_at TIMESTAMP DEFAULT now()
    );
    INSERT INTO validation_data (value) SELECT generate_series(1, 500);
    SELECT count(*) as pre_upgrade_rows FROM validation_data;
  "
```

### Step 4: Perform Major Version Upgrade

```bash
# Update to the new major version via Helm
helm upgrade ${CRDB_RELEASE_NAME} ${CRDB_CHART_PATH} \
  --namespace ${CRDB_CLUSTER_NS} \
  --reuse-values \
  --set image.tag=${TARGET_MAJOR_VERSION} \
  --timeout 30m
```

### Step 5: Monitor Rolling Upgrade

```bash
# Monitor until all pods are on new binary
for i in {1..60}; do
  echo "=== Check $i ($(date)) ==="
  
  kubectl get pods -n ${CRDB_CLUSTER_NS}
  
  # Check binary versions
  kubectl exec -n ${CRDB_CLUSTER_NS} cockroachdb-0 -- \
    cockroach sql --insecure --host=localhost:26257 \
    --execute="SELECT node_id, (SELECT value FROM crdb_internal.node_build_info WHERE field='Tag') as binary FROM crdb_internal.gossip_nodes ORDER BY node_id;" 2>/dev/null || true
  
  NEW_VERSION_COUNT=$(kubectl get pods -n ${CRDB_CLUSTER_NS} -o jsonpath='{.items[*].spec.containers[0].image}' | tr ' ' '\n' | grep -c "${TARGET_MAJOR_VERSION}" || echo "0")
  TOTAL_PODS=$(kubectl get pods -n ${CRDB_CLUSTER_NS} --no-headers | wc -l)
  
  if [ "${NEW_VERSION_COUNT}" -eq "${TOTAL_PODS}" ]; then
    echo "All pods on new binary!"
    break
  fi
  
  sleep 20
done
```

### Step 6: Verify Mixed-Version Gate Mode

```bash
# Verify cluster is in mixed-version gate mode
kubectl exec -n ${CRDB_CLUSTER_NS} cockroachdb-0 -- \
  cockroach sql --insecure --host=localhost:26257 \
  --execute="
    SELECT 
      'Binary Version' as check_type,
      (SELECT value FROM crdb_internal.node_build_info WHERE field='Tag') as value
    UNION ALL
    SELECT 
      'Cluster Version',
      (SELECT value FROM [SHOW CLUSTER SETTING version])
    UNION ALL
    SELECT 
      'Preserve Downgrade Option',
      (SELECT value FROM [SHOW CLUSTER SETTING cluster.preserve_downgrade_option]);
  "
```

**Expected Output:**
- Binary Version: New major version (e.g., v24.1.0)
- Cluster Version: Still old version (e.g., 23.2)
- Preserve Downgrade Option: Set to source version

### Step 7: Run Validation Workloads

```bash
# Insert data during mixed-version mode
kubectl exec -n ${CRDB_CLUSTER_NS} cockroachdb-0 -- \
  cockroach sql --insecure --host=localhost:26257 \
  --execute="
    USE manual_upgrade_test;
    
    -- Insert data during mixed-version mode
    INSERT INTO validation_data (value, phase) 
    SELECT generate_series(501, 1000), 'mixed-version';
    
    -- Run various operations
    SELECT phase, count(*) as rows FROM validation_data GROUP BY phase;
    
    -- Test schema operations
    CREATE INDEX IF NOT EXISTS idx_value ON validation_data(value);
    
    -- Test transactions
    BEGIN;
    INSERT INTO validation_data (value, phase) VALUES (9999, 'transaction-test');
    COMMIT;
    
    SELECT count(*) as total_rows FROM validation_data;
  "

# Run a workload test
kubectl exec -n ${CRDB_CLUSTER_NS} cockroachdb-0 -- \
  cockroach workload run kv \
  --duration=60s \
  --concurrency=4 \
  --max-rate=50 \
  'postgresql://root@localhost:26257/manual_upgrade_test?sslmode=disable'
```

### Step 8: Verify Cluster Stability in Mixed-Version Mode

```bash
# Check cluster health
kubectl exec -n ${CRDB_CLUSTER_NS} cockroachdb-0 -- \
  cockroach node status --insecure --host=localhost:26257

# Verify no issues
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
    WHERE array_length(replicas, 1) < 3;
  "
```

### Step 9: Optional - Test Rollback Capability

```bash
# At this point, rollback is still possible
# To test rollback capability (DO NOT EXECUTE unless testing rollback):

# echo "Rollback is possible - preserve_downgrade_option is set"
# To rollback, you would:
# 1. helm upgrade with old version image
# 2. Wait for all pods to roll back
# 3. Clear preserve_downgrade_option

# For this test, we proceed to finalization
echo "Proceeding to finalization..."
```

### Step 10: Manually Finalize the Upgrade

```bash
# Clear the preserve_downgrade_option to trigger finalization
kubectl exec -n ${CRDB_CLUSTER_NS} cockroachdb-0 -- \
  cockroach sql --insecure --host=localhost:26257 \
  --execute="RESET CLUSTER SETTING cluster.preserve_downgrade_option;"

# Verify it's cleared
kubectl exec -n ${CRDB_CLUSTER_NS} cockroachdb-0 -- \
  cockroach sql --insecure --host=localhost:26257 \
  --execute="SHOW CLUSTER SETTING cluster.preserve_downgrade_option;"
```

### Step 11: Monitor Finalization

```bash
# Monitor the finalization process
for i in {1..30}; do
  echo "=== Finalization Check $i ($(date)) ==="
  
  CLUSTER_VERSION=$(kubectl exec -n ${CRDB_CLUSTER_NS} cockroachdb-0 -- \
    cockroach sql --insecure --host=localhost:26257 \
    --execute="SHOW CLUSTER SETTING version;" -f csv 2>/dev/null | tail -1)
  
  echo "Cluster version: ${CLUSTER_VERSION}"
  
  # Check for migration jobs
  kubectl exec -n ${CRDB_CLUSTER_NS} cockroachdb-0 -- \
    cockroach sql --insecure --host=localhost:26257 \
    --execute="SELECT job_id, description, status FROM crdb_internal.jobs WHERE job_type = 'MIGRATION' ORDER BY created DESC LIMIT 5;" 2>/dev/null || true
  
  # Check if finalization is complete (version matches target major)
  if [[ "${CLUSTER_VERSION}" == *"24.1"* ]]; then  # Adjust pattern
    echo "Finalization complete!"
    break
  fi
  
  sleep 30
done
```

### Step 12: Verify Finalization Complete

```bash
# Verify final state
kubectl exec -n ${CRDB_CLUSTER_NS} cockroachdb-0 -- \
  cockroach sql --insecure --host=localhost:26257 \
  --execute="
    SELECT 
      'Binary Version' as check_type,
      (SELECT value FROM crdb_internal.node_build_info WHERE field='Tag') as value
    UNION ALL
    SELECT 
      'Cluster Version',
      (SELECT value FROM [SHOW CLUSTER SETTING version])
    UNION ALL
    SELECT 
      'Preserve Downgrade Option',
      COALESCE((SELECT value FROM [SHOW CLUSTER SETTING cluster.preserve_downgrade_option]), 'NOT SET');
  "

# Verify downgrade is now blocked
kubectl exec -n ${CRDB_CLUSTER_NS} cockroachdb-0 -- \
  cockroach sql --insecure --host=localhost:26257 \
  --execute="SET CLUSTER SETTING cluster.preserve_downgrade_option = '${SOURCE_VERSION}';" 2>&1 || \
  echo "Expected: Cannot set preserve_downgrade_option after finalization"
```

### Step 13: Final Data Validation

```bash
# Verify all data is intact
kubectl exec -n ${CRDB_CLUSTER_NS} cockroachdb-0 -- \
  cockroach sql --insecure --host=localhost:26257 \
  --execute="
    USE manual_upgrade_test;
    
    -- Check data from all phases
    SELECT phase, count(*) as rows FROM validation_data GROUP BY phase ORDER BY phase;
    
    -- Verify total count
    SELECT count(*) as total_rows FROM validation_data;
    
    -- Insert post-finalization data
    INSERT INTO validation_data (value, phase) VALUES (10000, 'post-finalization');
    
    SELECT phase, count(*) as rows FROM validation_data GROUP BY phase ORDER BY phase;
  "
```

## Validation Commands

```bash
# Complete validation script
echo "=== Manual Finalization Upgrade Validation ==="

echo -e "\n1. All pods on new version:"
kubectl get pods -n ${CRDB_CLUSTER_NS} -o jsonpath='{range .items[*]}{.metadata.name}: {.spec.containers[0].image}{"\n"}{end}'

echo -e "\n2. Cluster version (finalized):"
kubectl exec -n ${CRDB_CLUSTER_NS} cockroachdb-0 -- \
  cockroach sql --insecure --host=localhost:26257 \
  --execute="SHOW CLUSTER SETTING version;"

echo -e "\n3. preserve_downgrade_option (should be empty):"
kubectl exec -n ${CRDB_CLUSTER_NS} cockroachdb-0 -- \
  cockroach sql --insecure --host=localhost:26257 \
  --execute="SHOW CLUSTER SETTING cluster.preserve_downgrade_option;"

echo -e "\n4. Data integrity by phase:"
kubectl exec -n ${CRDB_CLUSTER_NS} cockroachdb-0 -- \
  cockroach sql --insecure --host=localhost:26257 \
  --execute="SELECT phase, count(*) FROM manual_upgrade_test.validation_data GROUP BY phase ORDER BY phase;"

echo -e "\n5. Cluster health:"
kubectl exec -n ${CRDB_CLUSTER_NS} cockroachdb-0 -- \
  cockroach node status --insecure --host=localhost:26257

echo -e "\n=== Validation Complete ==="
```

## Expected Results

| Check | Expected State |
|-------|----------------|
| Pre-finalization | Cluster in mixed-version gate |
| preserve_downgrade_option | Set before finalization |
| Validation workloads | Execute successfully |
| Manual finalization | Triggered by RESET |
| Post-finalization version | Target major version |
| Downgrade blocked | Cannot set preserve_downgrade_option |
| Data integrity | All phases data intact |

## Cleanup

```bash
# Remove test database
kubectl exec -n ${CRDB_CLUSTER_NS} cockroachdb-0 -- \
  cockroach sql --insecure --host=localhost:26257 \
  --execute="DROP DATABASE IF EXISTS manual_upgrade_test CASCADE;"

# Remove temporary files
rm -f /tmp/pre-manual-upgrade-state.txt
```

## Notes

- Manual finalization provides control over when irreversible schema upgrades occur
- The mixed-version gate allows testing before committing to the new version
- Once finalized, downgrade to the previous major version is not possible
- This approach is recommended for production environments
- Always have a backup before performing major upgrades

## Troubleshooting

### Cannot Set preserve_downgrade_option

```bash
# Check if already finalized
kubectl exec -n ${CRDB_CLUSTER_NS} cockroachdb-0 -- \
  cockroach sql --insecure --host=localhost:26257 \
  --execute="SHOW CLUSTER SETTING version;"

# If version is already new major, finalization has occurred
```

### Finalization Stuck

```bash
# Check for running migrations
kubectl exec -n ${CRDB_CLUSTER_NS} cockroachdb-0 -- \
  cockroach sql --insecure --host=localhost:26257 \
  --execute="SELECT * FROM crdb_internal.jobs WHERE job_type = 'MIGRATION' AND status = 'running';"

# Check for errors
kubectl exec -n ${CRDB_CLUSTER_NS} cockroachdb-0 -- \
  cockroach sql --insecure --host=localhost:26257 \
  --execute="SELECT * FROM crdb_internal.jobs WHERE status = 'failed' ORDER BY created DESC LIMIT 5;"
```

### Mixed-Version Issues

```bash
# Verify all nodes are on same binary
kubectl exec -n ${CRDB_CLUSTER_NS} cockroachdb-0 -- \
  cockroach sql --insecure --host=localhost:26257 \
  --execute="SELECT node_id, (SELECT value FROM crdb_internal.node_build_info WHERE field='Tag') FROM crdb_internal.gossip_nodes;"

# Check for version mismatches in logs
kubectl logs -n ${CRDB_CLUSTER_NS} cockroachdb-0 | grep -i "version"
```
