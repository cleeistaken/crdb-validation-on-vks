# VKS-13: Rollback Patch Upgrade

## Test Information

| Field | Value |
|-------|-------|
| **Test ID** | VKS-13 |
| **Category** | Upgrades |
| **Dependencies** | [VKS-10](VKS-10-patch-upgrade.md) |

## Objective

Rollback a patch upgrade to the previous version, verify rolling restart completes successfully, confirm no data loss, and validate cluster functionality after rollback.

## Pre-requisites

- VKS-10 completed (patch upgrade performed)
- Still within same major version (no irreversible schema migrations)
- Backup available (recommended)
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
  cockroach version --build-tag 2>/dev/null || \
  kubectl get pods -n ${CRDB_CLUSTER_NS} cockroachdb-0 -o jsonpath='{.spec.containers[0].image}' | awk -F: '{print $2}')
echo "Current version: ${CURRENT_VERSION}"

# Set rollback target version (previous patch)
export ROLLBACK_VERSION="v23.2.4"  # Example - set to your previous version
echo "Rollback target: ${ROLLBACK_VERSION}"
```

## Steps

### Step 1: Verify Current State

```bash
# Check current version across all pods
for pod in cockroachdb-0 cockroachdb-1 cockroachdb-2; do
  echo "=== ${pod} ==="
  kubectl exec -n ${CRDB_CLUSTER_NS} ${pod} -- cockroach version --build-tag 2>/dev/null || \
  kubectl get pod -n ${CRDB_CLUSTER_NS} ${pod} -o jsonpath='{.spec.containers[0].image}'
  echo ""
done

# Check cluster version
kubectl exec -n ${CRDB_CLUSTER_NS} cockroachdb-0 -- \
  cockroach sql --insecure --host=localhost:26257 \
  --execute="SHOW CLUSTER SETTING version;"
```

### Step 2: Verify Rollback is Possible

```bash
# Check if any irreversible migrations have occurred
kubectl exec -n ${CRDB_CLUSTER_NS} cockroachdb-0 -- \
  cockroach sql --insecure --host=localhost:26257 \
  --execute="
    SELECT 
      'Cluster Version' as check,
      (SELECT value FROM [SHOW CLUSTER SETTING version]) as value
    UNION ALL
    SELECT 
      'Preserve Downgrade Option',
      COALESCE((SELECT value FROM [SHOW CLUSTER SETTING cluster.preserve_downgrade_option]), 'NOT SET');
  "

# For patch rollback within same major, this should be safe
echo "Patch rollback within same major version is generally safe"
```

### Step 3: Create Pre-Rollback Checkpoint

```bash
# Record current data state
kubectl exec -n ${CRDB_CLUSTER_NS} cockroachdb-0 -- \
  cockroach sql --insecure --host=localhost:26257 \
  --execute="
    -- Create rollback test data
    CREATE DATABASE IF NOT EXISTS rollback_test;
    USE rollback_test;
    CREATE TABLE IF NOT EXISTS checkpoint_data (
      id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
      value INT,
      version STRING DEFAULT (SELECT value FROM crdb_internal.node_build_info WHERE field='Tag'),
      phase STRING DEFAULT 'pre-rollback',
      created_at TIMESTAMP DEFAULT now()
    );
    INSERT INTO checkpoint_data (value) SELECT generate_series(1, 100);
    SELECT count(*) as pre_rollback_rows FROM checkpoint_data;
  "

# Record row counts from existing databases
kubectl exec -n ${CRDB_CLUSTER_NS} cockroachdb-0 -- \
  cockroach sql --insecure --host=localhost:26257 \
  --execute="
    SELECT 
      table_catalog as database,
      count(*) as table_count
    FROM information_schema.tables 
    WHERE table_catalog NOT IN ('system', 'postgres')
    GROUP BY table_catalog;
  " > /tmp/pre-rollback-state.txt

cat /tmp/pre-rollback-state.txt
```

### Step 4: Perform Rollback

```bash
# Rollback to previous patch version via Helm
helm upgrade ${CRDB_RELEASE_NAME} ${CRDB_CHART_PATH} \
  --namespace ${CRDB_CLUSTER_NS} \
  --reuse-values \
  --set image.tag=${ROLLBACK_VERSION} \
  --timeout 20m
```

### Step 5: Monitor Rolling Restart

```bash
# Watch pods restart
kubectl get pods -n ${CRDB_CLUSTER_NS} -w &
WATCH_PID=$!

# Monitor rollback progress
for i in {1..60}; do
  echo "=== Check $i ($(date)) ==="
  
  kubectl get pods -n ${CRDB_CLUSTER_NS}
  
  # Check versions
  echo "Pod versions:"
  for pod in cockroachdb-0 cockroachdb-1 cockroachdb-2; do
    VERSION=$(kubectl get pod -n ${CRDB_CLUSTER_NS} ${pod} -o jsonpath='{.spec.containers[0].image}' 2>/dev/null | awk -F: '{print $2}')
    STATUS=$(kubectl get pod -n ${CRDB_CLUSTER_NS} ${pod} -o jsonpath='{.status.phase}' 2>/dev/null)
    echo "  ${pod}: ${VERSION} (${STATUS})"
  done
  
  # Check if all pods are on rollback version
  ROLLBACK_COUNT=$(kubectl get pods -n ${CRDB_CLUSTER_NS} -o jsonpath='{.items[*].spec.containers[0].image}' | tr ' ' '\n' | grep -c "${ROLLBACK_VERSION}" || echo "0")
  TOTAL_PODS=$(kubectl get pods -n ${CRDB_CLUSTER_NS} --no-headers | wc -l)
  
  echo "Pods on rollback version: ${ROLLBACK_COUNT}/${TOTAL_PODS}"
  
  if [ "${ROLLBACK_COUNT}" -eq "${TOTAL_PODS}" ]; then
    echo "Rollback complete!"
    break
  fi
  
  sleep 15
done

kill $WATCH_PID 2>/dev/null
```

### Step 6: Wait for All Pods Ready

```bash
# Wait for all pods to be ready
kubectl wait --for=condition=Ready pods --all -n ${CRDB_CLUSTER_NS} --timeout=300s

# Verify pod status
kubectl get pods -n ${CRDB_CLUSTER_NS}
```

### Step 7: Verify Rollback Version

```bash
# Check version on all pods
for pod in cockroachdb-0 cockroachdb-1 cockroachdb-2; do
  echo "=== ${pod} ==="
  kubectl exec -n ${CRDB_CLUSTER_NS} ${pod} -- cockroach version 2>/dev/null || \
  kubectl get pod -n ${CRDB_CLUSTER_NS} ${pod} -o jsonpath='{.spec.containers[0].image}'
  echo ""
done

# Verify via SQL
kubectl exec -n ${CRDB_CLUSTER_NS} cockroachdb-0 -- \
  cockroach sql --insecure --host=localhost:26257 \
  --execute="
    SELECT 
      node_id,
      (SELECT value FROM crdb_internal.node_build_info WHERE field='Tag') as version
    FROM crdb_internal.gossip_nodes
    ORDER BY node_id;
  "
```

**Expected Output:**
- All pods show the rollback version

### Step 8: Verify Data Integrity

```bash
# Check pre-rollback data is intact
kubectl exec -n ${CRDB_CLUSTER_NS} cockroachdb-0 -- \
  cockroach sql --insecure --host=localhost:26257 \
  --execute="
    USE rollback_test;
    SELECT count(*) as rows FROM checkpoint_data WHERE phase = 'pre-rollback';
    SELECT min(value), max(value) FROM checkpoint_data;
  "

# Verify database state matches pre-rollback
kubectl exec -n ${CRDB_CLUSTER_NS} cockroachdb-0 -- \
  cockroach sql --insecure --host=localhost:26257 \
  --execute="
    SELECT 
      table_catalog as database,
      count(*) as table_count
    FROM information_schema.tables 
    WHERE table_catalog NOT IN ('system', 'postgres')
    GROUP BY table_catalog;
  "

# Compare with pre-rollback state
echo "=== Pre-rollback state ==="
cat /tmp/pre-rollback-state.txt
```

### Step 9: Verify Cluster Health

```bash
# Check node status
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
      'Unavailable Ranges', count(*)::string
    FROM crdb_internal.ranges
    WHERE array_length(replicas, 1) = 0;
  "
```

### Step 10: Test Post-Rollback Operations

```bash
# Run SQL operations to verify functionality
kubectl exec -n ${CRDB_CLUSTER_NS} cockroachdb-0 -- \
  cockroach sql --insecure --host=localhost:26257 \
  --execute="
    USE rollback_test;
    
    -- Insert post-rollback data
    INSERT INTO checkpoint_data (value, phase) 
    SELECT generate_series(101, 200), 'post-rollback';
    
    -- Verify data by phase
    SELECT phase, count(*) as rows, min(value), max(value) 
    FROM checkpoint_data 
    GROUP BY phase 
    ORDER BY phase;
    
    -- Test schema operations
    ALTER TABLE checkpoint_data ADD COLUMN IF NOT EXISTS rollback_verified BOOL DEFAULT true;
    
    -- Test transactions
    BEGIN;
    INSERT INTO checkpoint_data (value, phase) VALUES (9999, 'transaction-test');
    COMMIT;
    
    SELECT count(*) as total_rows FROM checkpoint_data;
  "
```

### Step 11: Verify Workload Functionality

```bash
# Run a quick workload test
kubectl exec -n ${CRDB_CLUSTER_NS} cockroachdb-0 -- \
  cockroach workload run kv \
  --duration=30s \
  --concurrency=2 \
  --max-rate=20 \
  'postgresql://root@localhost:26257/rollback_test?sslmode=disable'
```

## Validation Commands

```bash
# Complete validation script
echo "=== Patch Rollback Validation ==="

echo -e "\n1. All pods on rollback version (${ROLLBACK_VERSION}):"
kubectl get pods -n ${CRDB_CLUSTER_NS} -o jsonpath='{range .items[*]}{.metadata.name}: {.spec.containers[0].image}{"\n"}{end}'

echo -e "\n2. CockroachDB version:"
kubectl exec -n ${CRDB_CLUSTER_NS} cockroachdb-0 -- \
  cockroach sql --insecure --host=localhost:26257 \
  --execute="SELECT node_id, (SELECT value FROM crdb_internal.node_build_info WHERE field='Tag') as version FROM crdb_internal.gossip_nodes ORDER BY node_id;"

echo -e "\n3. Cluster health:"
kubectl exec -n ${CRDB_CLUSTER_NS} cockroachdb-0 -- \
  cockroach node status --insecure --host=localhost:26257

echo -e "\n4. Data integrity:"
kubectl exec -n ${CRDB_CLUSTER_NS} cockroachdb-0 -- \
  cockroach sql --insecure --host=localhost:26257 \
  --execute="SELECT phase, count(*) as rows FROM rollback_test.checkpoint_data GROUP BY phase ORDER BY phase;"

echo -e "\n5. No data loss check:"
kubectl exec -n ${CRDB_CLUSTER_NS} cockroachdb-0 -- \
  cockroach sql --insecure --host=localhost:26257 \
  --execute="SELECT count(*) as pre_rollback_rows FROM rollback_test.checkpoint_data WHERE phase = 'pre-rollback';"

echo -e "\n=== Validation Complete ==="
```

## Expected Results

| Check | Expected State |
|-------|----------------|
| Rolling restart | Pods restart one-by-one |
| All pods version | Rollback version |
| Pre-rollback data | 100 rows intact |
| Post-rollback ops | SQL operations succeed |
| Cluster health | All nodes live |
| Under-replicated ranges | 0 |
| Data loss | None |

## Cleanup

```bash
# Remove test database
kubectl exec -n ${CRDB_CLUSTER_NS} cockroachdb-0 -- \
  cockroach sql --insecure --host=localhost:26257 \
  --execute="DROP DATABASE IF EXISTS rollback_test CASCADE;"

# Remove temporary files
rm -f /tmp/pre-rollback-state.txt
```

## Notes

- Patch rollbacks within the same major version are generally safe
- Rollback is only possible before any irreversible schema migrations
- Major version rollbacks require the preserve_downgrade_option to be set before upgrade
- Always verify data integrity after rollback
- The cluster remains available during the rolling restart

## Troubleshooting

### Rollback Fails

```bash
# Check pod status
kubectl describe pod -n ${CRDB_CLUSTER_NS} cockroachdb-0

# Check for image pull issues
kubectl get events -n ${CRDB_CLUSTER_NS} --sort-by='.lastTimestamp' | grep -i image

# Verify rollback image exists
kubectl run test-pull --rm -it --image=cockroachdb/cockroach:${ROLLBACK_VERSION} --restart=Never -- version
```

### Incompatible Schema

```bash
# Check cluster version
kubectl exec -n ${CRDB_CLUSTER_NS} cockroachdb-0 -- \
  cockroach sql --insecure --host=localhost:26257 \
  --execute="SHOW CLUSTER SETTING version;"

# If cluster version is newer than rollback binary, rollback may not be possible
# Check for migration status
kubectl exec -n ${CRDB_CLUSTER_NS} cockroachdb-0 -- \
  cockroach sql --insecure --host=localhost:26257 \
  --execute="SELECT * FROM system.migrations ORDER BY created DESC LIMIT 10;"
```

### Data Inconsistency

```bash
# Run consistency check
kubectl exec -n ${CRDB_CLUSTER_NS} cockroachdb-0 -- \
  cockroach sql --insecure --host=localhost:26257 \
  --execute="SELECT * FROM crdb_internal.invalid_objects;"

# Check for range issues
kubectl exec -n ${CRDB_CLUSTER_NS} cockroachdb-0 -- \
  cockroach sql --insecure --host=localhost:26257 \
  --execute="SELECT range_id, replicas FROM crdb_internal.ranges WHERE array_length(replicas, 1) < 3;"
```
