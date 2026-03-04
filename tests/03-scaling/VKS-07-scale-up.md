# VKS-07: Scale Up Nodes via Helm/Operator

## Test Information

| Field | Value |
|-------|-------|
| **Test ID** | VKS-07 |
| **Category** | Scaling |
| **Dependencies** | [VKS-02](../01-cluster-provisioning/VKS-02-cluster-install.md) |

## Objective

Scale up the CockroachDB cluster from 3 nodes to 6 nodes using Helm, verify that new pods reach Ready state, are distributed across worker nodes, and the cluster shows balanced replicas.

## Pre-requisites

- VKS-02 completed (CockroachDB cluster with 3 nodes)
- VKS cluster has sufficient worker node capacity (at least 6 workers or ability to scale)
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
export TARGET_REPLICAS=6

# Verify current cluster state
kubectl get pods -n ${CRDB_CLUSTER_NS}
kubectl get nodes
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
- 3 replicas configured
- 3 pods running
- 3 CockroachDB nodes active

### Step 2: Verify VKS Worker Node Capacity

```bash
# List available worker nodes
kubectl get nodes -l '!node-role.kubernetes.io/control-plane' -o wide

# Check node resources
kubectl top nodes 2>/dev/null || echo "Metrics server may not be available"

# Count available workers
WORKER_COUNT=$(kubectl get nodes -l '!node-role.kubernetes.io/control-plane' --no-headers | wc -l)
echo "Available worker nodes: ${WORKER_COUNT}"
```

**Expected Output:**
- At least 3 worker nodes available (ideally 6 for full distribution)

### Step 3: Scale VKS Worker Nodes (If Needed)

If you need more worker nodes, scale the VKS cluster first:

```bash
# This step requires access to the Supervisor cluster
# Switch to Supervisor context if needed
# unset KUBECONFIG

# Scale node pools (example - adjust based on your vks.yaml)
# kubectl patch cluster cluster-vks --type=merge -p '
# {
#   "spec": {
#     "topology": {
#       "workers": {
#         "machineDeployments": [
#           {"name": "node-pool-1", "replicas": 2},
#           {"name": "node-pool-2", "replicas": 2},
#           {"name": "node-pool-3", "replicas": 2}
#         ]
#       }
#     }
#   }
# }'

# Switch back to VKS cluster
# export KUBECONFIG=$(pwd)/vks-kubeconfig.yaml
```

### Step 4: Record Pre-Scale Metrics

```bash
# Record current range distribution
kubectl exec -n ${CRDB_CLUSTER_NS} cockroachdb-0 -- \
  cockroach sql --insecure --host=localhost:26257 \
  --execute="
    SELECT 
      node_id,
      store_id,
      range_count,
      used,
      available
    FROM crdb_internal.kv_store_status
    ORDER BY node_id;
  " > /tmp/pre-scale-ranges.txt

cat /tmp/pre-scale-ranges.txt
```

### Step 5: Scale Up CockroachDB Cluster

```bash
# Upgrade Helm release with increased replica count
helm upgrade ${CRDB_RELEASE_NAME} ${CRDB_CHART_PATH} \
  --namespace ${CRDB_CLUSTER_NS} \
  --reuse-values \
  --set statefulset.replicas=${TARGET_REPLICAS} \
  --wait \
  --timeout 15m
```

**Expected Output:**
```
Release "cockroachdb" has been upgraded.
```

### Step 6: Monitor New Pod Creation

```bash
# Watch pods come up
kubectl get pods -n ${CRDB_CLUSTER_NS} -w &
WATCH_PID=$!

# Wait for all pods to be ready
kubectl wait --for=condition=Ready pods --all -n ${CRDB_CLUSTER_NS} --timeout=600s

kill $WATCH_PID 2>/dev/null
```

**Expected Output:**
```
cockroachdb-0   1/1     Running   0          XXm
cockroachdb-1   1/1     Running   0          XXm
cockroachdb-2   1/1     Running   0          XXm
cockroachdb-3   1/1     Running   0          XXm
cockroachdb-4   1/1     Running   0          XXm
cockroachdb-5   1/1     Running   0          XXm
```

### Step 7: Verify Pod Distribution

```bash
# Check pod placement across nodes
kubectl get pods -n ${CRDB_CLUSTER_NS} -o wide

# Verify pods are on different nodes
kubectl get pods -n ${CRDB_CLUSTER_NS} -o jsonpath='{range .items[*]}{.metadata.name}{"\t"}{.spec.nodeName}{"\n"}{end}'

# Check for any scheduling issues
kubectl get events -n ${CRDB_CLUSTER_NS} --sort-by='.lastTimestamp' | grep -i schedule
```

**Expected Output:**
- 6 pods distributed across available worker nodes
- No scheduling failures

### Step 8: Verify New Nodes Joined Cluster

```bash
# Check CockroachDB node status
kubectl exec -n ${CRDB_CLUSTER_NS} cockroachdb-0 -- \
  cockroach node status --insecure --host=localhost:26257

# Verify all nodes are live
kubectl exec -n ${CRDB_CLUSTER_NS} cockroachdb-0 -- \
  cockroach sql --insecure --host=localhost:26257 \
  --execute="SELECT node_id, address, is_live FROM crdb_internal.gossip_nodes ORDER BY node_id;"
```

**Expected Output:**
- 6 nodes listed
- All nodes show `is_live = true`

### Step 9: Verify PVCs Created

```bash
# Check PVCs
kubectl get pvc -n ${CRDB_CLUSTER_NS}

# Verify all PVCs are bound
kubectl get pvc -n ${CRDB_CLUSTER_NS} -o jsonpath='{range .items[*]}{.metadata.name}{"\t"}{.status.phase}{"\n"}{end}'
```

**Expected Output:**
- 6 PVCs (datadir-cockroachdb-0 through datadir-cockroachdb-5)
- All PVCs in `Bound` state

### Step 10: Monitor Range Rebalancing

```bash
# Check range distribution (may take time to rebalance)
kubectl exec -n ${CRDB_CLUSTER_NS} cockroachdb-0 -- \
  cockroach sql --insecure --host=localhost:26257 \
  --execute="
    SELECT 
      node_id,
      store_id,
      range_count,
      used,
      available
    FROM crdb_internal.kv_store_status
    ORDER BY node_id;
  "

# Compare with pre-scale metrics
echo "=== Pre-scale ranges ==="
cat /tmp/pre-scale-ranges.txt
```

**Expected Output:**
- Ranges distributed across all 6 nodes
- Range counts should become more balanced over time

### Step 11: Verify Cluster Health

```bash
# Run a health check query
kubectl exec -n ${CRDB_CLUSTER_NS} cockroachdb-0 -- \
  cockroach sql --insecure --host=localhost:26257 \
  --execute="
    SELECT 
      'Total Nodes' as metric, count(*)::string as value 
    FROM crdb_internal.gossip_nodes WHERE is_live
    UNION ALL
    SELECT 
      'Total Ranges', count(*)::string 
    FROM crdb_internal.ranges
    UNION ALL
    SELECT 
      'Under-replicated Ranges', count(*)::string 
    FROM crdb_internal.ranges 
    WHERE array_length(replicas, 1) < 3;
  "
```

**Expected Output:**
- Total Nodes: 6
- Under-replicated Ranges: 0 (after rebalancing)

### Step 12: Test Workload on Scaled Cluster

```bash
# Run a simple workload to verify cluster functionality
kubectl exec -n ${CRDB_CLUSTER_NS} cockroachdb-0 -- \
  cockroach sql --insecure --host=localhost:26257 \
  --execute="
    CREATE DATABASE IF NOT EXISTS scale_test;
    USE scale_test;
    CREATE TABLE IF NOT EXISTS test_data (
      id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
      data STRING,
      node_id INT DEFAULT crdb_internal.node_id()
    );
    INSERT INTO test_data (data) SELECT 'test' FROM generate_series(1, 100);
    SELECT node_id, count(*) FROM test_data GROUP BY node_id ORDER BY node_id;
  "
```

**Expected Output:**
- Data inserted successfully
- Queries execute across the scaled cluster

## Validation Commands

```bash
# Complete validation script
echo "=== Scale Up Validation ==="

echo -e "\n1. StatefulSet replicas:"
kubectl get statefulset -n ${CRDB_CLUSTER_NS} cockroachdb -o jsonpath='Replicas: {.spec.replicas}'
echo ""

echo -e "\n2. All pods running:"
kubectl get pods -n ${CRDB_CLUSTER_NS}

echo -e "\n3. Pod distribution:"
kubectl get pods -n ${CRDB_CLUSTER_NS} -o jsonpath='{range .items[*]}{.metadata.name} -> {.spec.nodeName}{"\n"}{end}'

echo -e "\n4. CockroachDB node count:"
kubectl exec -n ${CRDB_CLUSTER_NS} cockroachdb-0 -- \
  cockroach sql --insecure --host=localhost:26257 \
  --execute="SELECT count(*) as node_count FROM crdb_internal.gossip_nodes WHERE is_live;"

echo -e "\n5. PVC status:"
kubectl get pvc -n ${CRDB_CLUSTER_NS} --no-headers | wc -l
echo "PVCs created"

echo -e "\n6. Range distribution:"
kubectl exec -n ${CRDB_CLUSTER_NS} cockroachdb-0 -- \
  cockroach sql --insecure --host=localhost:26257 \
  --execute="SELECT node_id, range_count FROM crdb_internal.kv_store_status ORDER BY node_id;"

echo -e "\n=== Validation Complete ==="
```

## Expected Results

| Check | Expected State |
|-------|----------------|
| StatefulSet replicas | 6 |
| Running pods | 6 pods, all `1/1 Running` |
| Pod distribution | Spread across worker nodes |
| CockroachDB nodes | 6 nodes, all `is_live = true` |
| PVCs | 6 PVCs, all `Bound` |
| Range rebalancing | Ranges distributed across all nodes |
| Cluster health | No under-replicated ranges |

## Cleanup

**Note:** Do not clean up if proceeding to VKS-08 (Scale Down test).

```bash
# Remove test database
kubectl exec -n ${CRDB_CLUSTER_NS} cockroachdb-0 -- \
  cockroach sql --insecure --host=localhost:26257 \
  --execute="DROP DATABASE IF EXISTS scale_test CASCADE;"

# Remove temporary files
rm -f /tmp/pre-scale-ranges.txt
```

## Notes

- Scaling up is a non-disruptive operation
- New nodes join the cluster automatically via the operator
- Range rebalancing happens automatically but may take time
- Ensure sufficient storage capacity for new PVCs
- The cluster remains available during scale-up

## Troubleshooting

### New Pods Stuck Pending

```bash
# Check events for scheduling issues
kubectl get events -n ${CRDB_CLUSTER_NS} --sort-by='.lastTimestamp' | grep -i pending

# Check node resources
kubectl describe nodes | grep -A 5 "Allocated resources"

# Check PVC binding
kubectl get pvc -n ${CRDB_CLUSTER_NS}
kubectl describe pvc -n ${CRDB_CLUSTER_NS} | grep -A 5 "Events"
```

### New Nodes Not Joining Cluster

```bash
# Check new pod logs
kubectl logs -n ${CRDB_CLUSTER_NS} cockroachdb-3

# Verify join addresses
kubectl exec -n ${CRDB_CLUSTER_NS} cockroachdb-3 -- ps aux | grep cockroach

# Check gossip connectivity
kubectl exec -n ${CRDB_CLUSTER_NS} cockroachdb-3 -- \
  cockroach sql --insecure --host=localhost:26257 \
  --execute="SELECT * FROM crdb_internal.gossip_network;"
```

### Uneven Range Distribution

```bash
# Check rebalancing status
kubectl exec -n ${CRDB_CLUSTER_NS} cockroachdb-0 -- \
  cockroach sql --insecure --host=localhost:26257 \
  --execute="SHOW CLUSTER SETTING kv.allocator.load_based_rebalancing;"

# Force rebalancing (if needed)
kubectl exec -n ${CRDB_CLUSTER_NS} cockroachdb-0 -- \
  cockroach sql --insecure --host=localhost:26257 \
  --execute="SET CLUSTER SETTING kv.allocator.load_based_rebalancing = 'leases and replicas';"
```
