# VKS-08: Scale Down Nodes via Helm/Operator

## Test Information

| Field | Value |
|-------|-------|
| **Test ID** | VKS-08 |
| **Category** | Scaling |
| **Dependencies** | [VKS-07](VKS-07-scale-up.md) |

## Objective

Scale down the CockroachDB cluster from 6 nodes to 3 nodes using Helm, verify that pods are terminated gracefully, ranges are rebalanced, and the cluster remains healthy with no data loss.

## Pre-requisites

- VKS-07 completed (CockroachDB cluster scaled to 6 nodes)
- Cluster under low write load (recommended for safe decommissioning)
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
export TARGET_REPLICAS=3

# Verify current cluster state (should be 6 nodes)
kubectl get pods -n ${CRDB_CLUSTER_NS}
```

## Steps

### Step 1: Verify Current Cluster Size

```bash
# Check current replica count
kubectl get statefulset -n ${CRDB_CLUSTER_NS} cockroachdb -o jsonpath='{.spec.replicas}'
echo ""

# List current pods
kubectl get pods -n ${CRDB_CLUSTER_NS} -o wide

# Check current node count in CockroachDB
kubectl exec -n ${CRDB_CLUSTER_NS} cockroachdb-0 -- \
  cockroach node status --insecure --host=localhost:26257
```

**Expected Output:**
- 6 replicas configured
- 6 pods running
- 6 CockroachDB nodes active

### Step 2: Create Test Data for Verification

```bash
# Create test data to verify no data loss after scale-down
kubectl exec -n ${CRDB_CLUSTER_NS} cockroachdb-0 -- \
  cockroach sql --insecure --host=localhost:26257 \
  --execute="
    CREATE DATABASE IF NOT EXISTS scaledown_test;
    USE scaledown_test;
    CREATE TABLE IF NOT EXISTS important_data (
      id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
      value INT,
      created_at TIMESTAMP DEFAULT now()
    );
    INSERT INTO important_data (value) SELECT generate_series(1, 1000);
    SELECT count(*) as row_count FROM important_data;
  "
```

**Expected Output:**
```
  row_count
------------
       1000
```

### Step 3: Record Pre-Scale Metrics

```bash
# Record current range and replica distribution
kubectl exec -n ${CRDB_CLUSTER_NS} cockroachdb-0 -- \
  cockroach sql --insecure --host=localhost:26257 \
  --execute="
    SELECT 
      node_id,
      range_count,
      used
    FROM crdb_internal.kv_store_status
    ORDER BY node_id;
  " > /tmp/pre-scaledown-ranges.txt

cat /tmp/pre-scaledown-ranges.txt

# Record total ranges
kubectl exec -n ${CRDB_CLUSTER_NS} cockroachdb-0 -- \
  cockroach sql --insecure --host=localhost:26257 \
  --execute="SELECT count(*) as total_ranges FROM crdb_internal.ranges;"
```

### Step 4: Scale Down CockroachDB Cluster

```bash
# Upgrade Helm release with decreased replica count
# The operator should handle graceful decommissioning
helm upgrade ${CRDB_RELEASE_NAME} ${CRDB_CHART_PATH} \
  --namespace ${CRDB_CLUSTER_NS} \
  --reuse-values \
  --set statefulset.replicas=${TARGET_REPLICAS} \
  --timeout 30m
```

**Note:** Scale-down may take longer than scale-up due to decommissioning process.

### Step 5: Monitor Decommissioning Process

```bash
# Watch the decommissioning progress
watch -n 5 "kubectl exec -n ${CRDB_CLUSTER_NS} cockroachdb-0 -- \
  cockroach node status --insecure --host=localhost:26257 2>/dev/null | tail -10"

# Alternative: Check node status periodically
for i in {1..60}; do
  echo "=== Check $i ($(date)) ==="
  kubectl exec -n ${CRDB_CLUSTER_NS} cockroachdb-0 -- \
    cockroach node status --insecure --host=localhost:26257 2>/dev/null
  
  # Check if scale-down is complete
  POD_COUNT=$(kubectl get pods -n ${CRDB_CLUSTER_NS} --no-headers 2>/dev/null | wc -l)
  if [ "$POD_COUNT" -eq "$TARGET_REPLICAS" ]; then
    echo "Scale-down complete!"
    break
  fi
  
  sleep 10
done
```

### Step 6: Monitor Pod Termination

```bash
# Watch pods being terminated
kubectl get pods -n ${CRDB_CLUSTER_NS} -w &
WATCH_PID=$!

# Wait for target replica count
while true; do
  POD_COUNT=$(kubectl get pods -n ${CRDB_CLUSTER_NS} --no-headers 2>/dev/null | wc -l)
  if [ "$POD_COUNT" -eq "$TARGET_REPLICAS" ]; then
    break
  fi
  sleep 5
done

kill $WATCH_PID 2>/dev/null

# Verify final pod count
kubectl get pods -n ${CRDB_CLUSTER_NS}
```

**Expected Output:**
- Pods cockroachdb-3, cockroachdb-4, cockroachdb-5 terminated
- Only cockroachdb-0, cockroachdb-1, cockroachdb-2 remain

### Step 7: Verify Graceful Decommissioning

```bash
# Check node status - decommissioned nodes should show as such
kubectl exec -n ${CRDB_CLUSTER_NS} cockroachdb-0 -- \
  cockroach node status --insecure --host=localhost:26257 --decommission

# Verify no stuck replicas on decommissioned nodes
kubectl exec -n ${CRDB_CLUSTER_NS} cockroachdb-0 -- \
  cockroach sql --insecure --host=localhost:26257 \
  --execute="
    SELECT node_id, is_live, is_decommissioning 
    FROM crdb_internal.gossip_nodes 
    ORDER BY node_id;
  "
```

**Expected Output:**
- Nodes 4, 5, 6 show as decommissioned (if still visible)
- Nodes 1, 2, 3 show `is_live = true`

### Step 8: Verify Cluster Health

```bash
# Check remaining nodes are healthy
kubectl exec -n ${CRDB_CLUSTER_NS} cockroachdb-0 -- \
  cockroach node status --insecure --host=localhost:26257

# Verify all ranges are properly replicated
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

**Expected Output:**
- Live Nodes: 3
- Under-replicated Ranges: 0
- Unavailable Ranges: 0

### Step 9: Verify Data Integrity

```bash
# Check test data is intact
kubectl exec -n ${CRDB_CLUSTER_NS} cockroachdb-0 -- \
  cockroach sql --insecure --host=localhost:26257 \
  --execute="
    USE scaledown_test;
    SELECT count(*) as row_count FROM important_data;
    SELECT min(value), max(value), count(DISTINCT value) FROM important_data;
  "
```

**Expected Output:**
```
  row_count
------------
       1000

  min | max  | count
------+------+-------
    1 | 1000 |  1000
```

### Step 10: Verify Range Rebalancing

```bash
# Check range distribution on remaining nodes
kubectl exec -n ${CRDB_CLUSTER_NS} cockroachdb-0 -- \
  cockroach sql --insecure --host=localhost:26257 \
  --execute="
    SELECT 
      node_id,
      range_count,
      used
    FROM crdb_internal.kv_store_status
    ORDER BY node_id;
  "

# Compare with pre-scale metrics
echo "=== Pre-scale-down ranges ==="
cat /tmp/pre-scaledown-ranges.txt
```

**Expected Output:**
- Only 3 nodes listed
- Ranges redistributed among remaining nodes
- Total range count should be similar to before

### Step 11: Verify PVCs

```bash
# Check PVC status
kubectl get pvc -n ${CRDB_CLUSTER_NS}

# Note: PVCs for removed pods may still exist (orphaned)
# They can be cleaned up manually if needed
```

**Expected Output:**
- PVCs for cockroachdb-0, -1, -2 still bound
- PVCs for cockroachdb-3, -4, -5 may be orphaned (depending on reclaim policy)

### Step 12: Clean Up Orphaned PVCs (Optional)

```bash
# List orphaned PVCs
kubectl get pvc -n ${CRDB_CLUSTER_NS} | grep -E "cockroachdb-[3-5]"

# Delete orphaned PVCs (only if data is no longer needed)
# kubectl delete pvc datadir-cockroachdb-3 datadir-cockroachdb-4 datadir-cockroachdb-5 -n ${CRDB_CLUSTER_NS}
```

## Validation Commands

```bash
# Complete validation script
echo "=== Scale Down Validation ==="

echo -e "\n1. StatefulSet replicas:"
kubectl get statefulset -n ${CRDB_CLUSTER_NS} cockroachdb -o jsonpath='Replicas: {.spec.replicas}'
echo ""

echo -e "\n2. Running pods (should be 3):"
kubectl get pods -n ${CRDB_CLUSTER_NS}

echo -e "\n3. CockroachDB live nodes:"
kubectl exec -n ${CRDB_CLUSTER_NS} cockroachdb-0 -- \
  cockroach sql --insecure --host=localhost:26257 \
  --execute="SELECT count(*) as live_nodes FROM crdb_internal.gossip_nodes WHERE is_live;"

echo -e "\n4. Data integrity check:"
kubectl exec -n ${CRDB_CLUSTER_NS} cockroachdb-0 -- \
  cockroach sql --insecure --host=localhost:26257 \
  --execute="SELECT count(*) as rows FROM scaledown_test.important_data;"

echo -e "\n5. Under-replicated ranges:"
kubectl exec -n ${CRDB_CLUSTER_NS} cockroachdb-0 -- \
  cockroach sql --insecure --host=localhost:26257 \
  --execute="SELECT count(*) as under_replicated FROM crdb_internal.ranges WHERE array_length(replicas, 1) < 3;"

echo -e "\n6. Range distribution:"
kubectl exec -n ${CRDB_CLUSTER_NS} cockroachdb-0 -- \
  cockroach sql --insecure --host=localhost:26257 \
  --execute="SELECT node_id, range_count FROM crdb_internal.kv_store_status ORDER BY node_id;"

echo -e "\n=== Validation Complete ==="
```

## Expected Results

| Check | Expected State |
|-------|----------------|
| StatefulSet replicas | 3 |
| Running pods | 3 pods (cockroachdb-0, -1, -2) |
| CockroachDB live nodes | 3 |
| Test data rows | 1000 (no data loss) |
| Under-replicated ranges | 0 |
| Decommissioned nodes | Gracefully removed |

## Cleanup

```bash
# Remove test database
kubectl exec -n ${CRDB_CLUSTER_NS} cockroachdb-0 -- \
  cockroach sql --insecure --host=localhost:26257 \
  --execute="DROP DATABASE IF EXISTS scaledown_test CASCADE;"

# Remove orphaned PVCs (optional)
kubectl delete pvc datadir-cockroachdb-3 datadir-cockroachdb-4 datadir-cockroachdb-5 -n ${CRDB_CLUSTER_NS} 2>/dev/null

# Remove temporary files
rm -f /tmp/pre-scaledown-ranges.txt
```

## Notes

- Scale-down triggers node decommissioning which moves data off nodes before termination
- The process ensures no data loss by waiting for ranges to be fully replicated elsewhere
- Scale-down takes longer than scale-up due to data migration
- Never force-delete pods during scale-down as it may cause data loss
- The operator handles the decommissioning process automatically

## Troubleshooting

### Scale-Down Stuck

```bash
# Check decommissioning status
kubectl exec -n ${CRDB_CLUSTER_NS} cockroachdb-0 -- \
  cockroach node status --insecure --host=localhost:26257 --decommission

# Check for stuck ranges
kubectl exec -n ${CRDB_CLUSTER_NS} cockroachdb-0 -- \
  cockroach sql --insecure --host=localhost:26257 \
  --execute="SELECT range_id, replicas FROM crdb_internal.ranges WHERE array_length(replicas, 1) < 3;"

# Check operator logs
kubectl logs -n crdb-operator -l app.kubernetes.io/name=cockroach-operator --tail=100
```

### Data Loss Detected

```bash
# Check for unavailable ranges
kubectl exec -n ${CRDB_CLUSTER_NS} cockroachdb-0 -- \
  cockroach sql --insecure --host=localhost:26257 \
  --execute="SELECT * FROM crdb_internal.ranges WHERE array_length(replicas, 1) = 0;"

# Check replication status
kubectl exec -n ${CRDB_CLUSTER_NS} cockroachdb-0 -- \
  cockroach sql --insecure --host=localhost:26257 \
  --execute="SHOW CLUSTER SETTING kv.allocator.range_rebalance_threshold;"
```

### Pods Not Terminating

```bash
# Check pod status
kubectl describe pod -n ${CRDB_CLUSTER_NS} cockroachdb-5

# Check for finalizers
kubectl get pod -n ${CRDB_CLUSTER_NS} cockroachdb-5 -o yaml | grep -A 5 finalizers

# Force delete (DANGEROUS - may cause data loss)
# Only use if decommissioning is confirmed complete
# kubectl delete pod cockroachdb-5 -n ${CRDB_CLUSTER_NS} --force --grace-period=0
```
