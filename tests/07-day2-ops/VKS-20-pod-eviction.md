# VKS-20: Pod Eviction / Rescheduling Behavior

## Test Information

| Field | Value |
|-------|-------|
| **Test ID** | VKS-20 |
| **Category** | Day-2 Ops |
| **Dependencies** | [VKS-02](../01-cluster-provisioning/VKS-02-cluster-install.md) |

## Objective

Test the cluster's resilience to VKS maintenance operations by cordoning and draining a worker node, verifying that CockroachDB pods are rescheduled correctly, and confirming no data loss occurs.

## Pre-requisites

- VKS-02 completed (CockroachDB cluster running)
- Multi-node VKS worker pool (at least 3 workers)
- `kubectl` configured with VKS cluster kubeconfig

## Environment Setup

```bash
# Ensure KUBECONFIG points to VKS cluster
export KUBECONFIG=$(pwd)/vks-kubeconfig.yaml

# Set environment variables
export CRDB_CLUSTER_NS="crdb-cluster"

# Verify cluster is running
kubectl get pods -n ${CRDB_CLUSTER_NS} -o wide
kubectl get nodes
```

## Steps

### Step 1: Identify Target Node

```bash
# List CockroachDB pods and their nodes
kubectl get pods -n ${CRDB_CLUSTER_NS} -o wide

# Choose a worker node with a CockroachDB pod
TARGET_NODE=$(kubectl get pods -n ${CRDB_CLUSTER_NS} cockroachdb-1 -o jsonpath='{.spec.nodeName}')
echo "Target node for drain: ${TARGET_NODE}"

# Identify the pod on this node
TARGET_POD=$(kubectl get pods -n ${CRDB_CLUSTER_NS} -o wide | grep ${TARGET_NODE} | awk '{print $1}')
echo "Pod on target node: ${TARGET_POD}"
```

### Step 2: Create Test Data

```bash
# Create test data to verify no data loss
kubectl exec -n ${CRDB_CLUSTER_NS} cockroachdb-0 -- \
  cockroach sql --insecure --host=localhost:26257 \
  --execute="
    CREATE DATABASE IF NOT EXISTS eviction_test;
    USE eviction_test;
    CREATE TABLE IF NOT EXISTS test_data (
      id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
      value INT,
      node_id INT DEFAULT crdb_internal.node_id(),
      created_at TIMESTAMP DEFAULT now()
    );
    INSERT INTO test_data (value) SELECT generate_series(1, 1000);
    SELECT count(*) as row_count FROM test_data;
  "
```

### Step 3: Record Pre-Eviction State

```bash
# Record cluster state
kubectl exec -n ${CRDB_CLUSTER_NS} cockroachdb-0 -- \
  cockroach node status --insecure --host=localhost:26257 > /tmp/pre-eviction-status.txt

cat /tmp/pre-eviction-status.txt

# Record pod distribution
kubectl get pods -n ${CRDB_CLUSTER_NS} -o wide > /tmp/pre-eviction-pods.txt
cat /tmp/pre-eviction-pods.txt

# Record data checksum
kubectl exec -n ${CRDB_CLUSTER_NS} cockroachdb-0 -- \
  cockroach sql --insecure --host=localhost:26257 \
  --execute="SELECT count(*), sum(value) FROM eviction_test.test_data;" > /tmp/pre-eviction-data.txt
cat /tmp/pre-eviction-data.txt
```

### Step 4: Start Continuous Workload

```bash
# Start a background workload to test availability
kubectl exec -n ${CRDB_CLUSTER_NS} cockroachdb-0 -- \
  /bin/bash -c "
    for i in \$(seq 1 120); do
      cockroach sql --insecure --host=cockroachdb-public:26257 \
        --execute=\"INSERT INTO eviction_test.test_data (value) VALUES (\$i + 1000);\" 2>&1 || echo \"Insert failed at \$i\"
      sleep 1
    done
  " &
WORKLOAD_PID=$!
echo "Workload started with PID: ${WORKLOAD_PID}"
```

### Step 5: Cordon the Target Node

```bash
# Cordon the node to prevent new pods from being scheduled
kubectl cordon ${TARGET_NODE}

# Verify node is cordoned
kubectl get node ${TARGET_NODE}
```

**Expected Output:**
- Node shows `SchedulingDisabled`

### Step 6: Drain the Target Node

```bash
# Drain the node (this will evict pods)
kubectl drain ${TARGET_NODE} \
  --ignore-daemonsets \
  --delete-emptydir-data \
  --force \
  --grace-period=60 \
  --timeout=300s

# Monitor the drain process
echo "Drain initiated, monitoring pod status..."
```

### Step 7: Monitor Pod Rescheduling

```bash
# Watch pods during rescheduling
kubectl get pods -n ${CRDB_CLUSTER_NS} -o wide -w &
WATCH_PID=$!

# Wait for pod to be rescheduled
for i in {1..30}; do
  echo "=== Check $i ==="
  kubectl get pods -n ${CRDB_CLUSTER_NS} -o wide
  
  # Check if all pods are running
  RUNNING_PODS=$(kubectl get pods -n ${CRDB_CLUSTER_NS} --field-selector=status.phase=Running --no-headers | wc -l)
  TOTAL_PODS=$(kubectl get statefulset -n ${CRDB_CLUSTER_NS} cockroachdb -o jsonpath='{.spec.replicas}')
  
  echo "Running pods: ${RUNNING_PODS}/${TOTAL_PODS}"
  
  if [ "${RUNNING_PODS}" -eq "${TOTAL_PODS}" ]; then
    echo "All pods running!"
    break
  fi
  
  sleep 10
done

kill $WATCH_PID 2>/dev/null
```

### Step 8: Verify Pod Rescheduled to Different Node

```bash
# Check new pod distribution
kubectl get pods -n ${CRDB_CLUSTER_NS} -o wide

# Verify no pods on drained node
kubectl get pods -n ${CRDB_CLUSTER_NS} -o wide | grep ${TARGET_NODE} && \
  echo "WARNING: Pod still on drained node" || \
  echo "SUCCESS: No pods on drained node"

# Compare with pre-eviction
echo "=== Pre-eviction pods ==="
cat /tmp/pre-eviction-pods.txt
echo -e "\n=== Current pods ==="
kubectl get pods -n ${CRDB_CLUSTER_NS} -o wide
```

### Step 9: Verify Cluster Health

```bash
# Wait for workload to complete
wait $WORKLOAD_PID 2>/dev/null

# Check cluster status
kubectl exec -n ${CRDB_CLUSTER_NS} cockroachdb-0 -- \
  cockroach node status --insecure --host=localhost:26257

# Check for under-replicated ranges
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

### Step 10: Verify Data Integrity

```bash
# Check data is intact
kubectl exec -n ${CRDB_CLUSTER_NS} cockroachdb-0 -- \
  cockroach sql --insecure --host=localhost:26257 \
  --execute="
    SELECT count(*) as total_rows, sum(value) as sum_value 
    FROM eviction_test.test_data;
  "

# Compare with pre-eviction (original 1000 rows)
echo "=== Pre-eviction data ==="
cat /tmp/pre-eviction-data.txt

# Check for any gaps in workload inserts
kubectl exec -n ${CRDB_CLUSTER_NS} cockroachdb-0 -- \
  cockroach sql --insecure --host=localhost:26257 \
  --execute="
    SELECT count(*) as workload_inserts 
    FROM eviction_test.test_data 
    WHERE value > 1000;
  "
```

### Step 11: Uncordon the Node

```bash
# Uncordon the node to allow scheduling again
kubectl uncordon ${TARGET_NODE}

# Verify node is schedulable
kubectl get node ${TARGET_NODE}
```

### Step 12: Verify Final State

```bash
# Final cluster status
kubectl exec -n ${CRDB_CLUSTER_NS} cockroachdb-0 -- \
  cockroach node status --insecure --host=localhost:26257

# Final pod distribution
kubectl get pods -n ${CRDB_CLUSTER_NS} -o wide

# Verify all nodes are live
kubectl exec -n ${CRDB_CLUSTER_NS} cockroachdb-0 -- \
  cockroach sql --insecure --host=localhost:26257 \
  --execute="SELECT node_id, is_live FROM crdb_internal.gossip_nodes ORDER BY node_id;"
```

## Validation Commands

```bash
# Complete validation script
echo "=== Pod Eviction Validation ==="

echo -e "\n1. All pods running:"
kubectl get pods -n ${CRDB_CLUSTER_NS}

echo -e "\n2. Node status (should be schedulable):"
kubectl get node ${TARGET_NODE} | grep -v SchedulingDisabled && echo "Node is schedulable"

echo -e "\n3. CockroachDB cluster health:"
kubectl exec -n ${CRDB_CLUSTER_NS} cockroachdb-0 -- \
  cockroach sql --insecure --host=localhost:26257 \
  --execute="SELECT count(*) as live_nodes FROM crdb_internal.gossip_nodes WHERE is_live;"

echo -e "\n4. Data integrity (should be >= 1000 rows):"
kubectl exec -n ${CRDB_CLUSTER_NS} cockroachdb-0 -- \
  cockroach sql --insecure --host=localhost:26257 \
  --execute="SELECT count(*) as rows FROM eviction_test.test_data;"

echo -e "\n5. Under-replicated ranges (should be 0):"
kubectl exec -n ${CRDB_CLUSTER_NS} cockroachdb-0 -- \
  cockroach sql --insecure --host=localhost:26257 \
  --execute="SELECT count(*) FROM crdb_internal.ranges WHERE array_length(replicas, 1) < 3;"

echo -e "\n=== Validation Complete ==="
```

## Expected Results

| Check | Expected State |
|-------|----------------|
| Drain operation | Completes successfully |
| Pod rescheduling | Pod moves to different node |
| Cluster availability | Maintained during drain |
| Data integrity | No data loss |
| Under-replicated ranges | 0 (after rebalancing) |
| Node uncordon | Successful |

## Cleanup

```bash
# Ensure node is uncordoned
kubectl uncordon ${TARGET_NODE} 2>/dev/null

# Remove test database
kubectl exec -n ${CRDB_CLUSTER_NS} cockroachdb-0 -- \
  cockroach sql --insecure --host=localhost:26257 \
  --execute="DROP DATABASE IF EXISTS eviction_test CASCADE;"

# Remove temporary files
rm -f /tmp/pre-eviction-status.txt /tmp/pre-eviction-pods.txt /tmp/pre-eviction-data.txt
```

## Notes

- This test simulates VKS maintenance operations like node upgrades
- PodDisruptionBudgets (PDBs) help control how many pods can be evicted simultaneously
- The StatefulSet controller handles pod rescheduling automatically
- Data remains safe due to CockroachDB's replication
- Some brief connection interruptions are expected during pod migration

## Troubleshooting

### Drain Stuck

```bash
# Check what's blocking the drain
kubectl get pods -n ${CRDB_CLUSTER_NS} -o wide | grep ${TARGET_NODE}

# Check PDB status
kubectl get pdb -n ${CRDB_CLUSTER_NS}

# Force drain if safe (use with caution)
kubectl drain ${TARGET_NODE} --ignore-daemonsets --delete-emptydir-data --force --grace-period=30
```

### Pod Not Rescheduling

```bash
# Check events
kubectl get events -n ${CRDB_CLUSTER_NS} --sort-by='.lastTimestamp' | tail -20

# Check if there are available nodes
kubectl get nodes

# Check pod description
kubectl describe pod -n ${CRDB_CLUSTER_NS} ${TARGET_POD}
```

### Data Loss Detected

```bash
# Check range status
kubectl exec -n ${CRDB_CLUSTER_NS} cockroachdb-0 -- \
  cockroach sql --insecure --host=localhost:26257 \
  --execute="SELECT * FROM crdb_internal.ranges WHERE array_length(replicas, 1) < 3;"

# Check for unavailable ranges
kubectl exec -n ${CRDB_CLUSTER_NS} cockroachdb-0 -- \
  cockroach sql --insecure --host=localhost:26257 \
  --execute="SELECT count(*) FROM crdb_internal.ranges WHERE array_length(replicas, 1) = 0;"
```
