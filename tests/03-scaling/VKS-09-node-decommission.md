# VKS-09: Kubernetes Node Decommission via Annotation

## Test Information

| Field | Value |
|-------|-------|
| **Test ID** | VKS-09 |
| **Category** | Scaling |
| **Dependencies** | [VKS-02](../01-cluster-provisioning/VKS-02-cluster-install.md) |

## Objective

Test the operator's Kubernetes node controller feature by annotating a VKS worker node for decommissioning, verifying that CockroachDB pods are drained cleanly, data migrates to other nodes, and replacement pods are scheduled elsewhere.

## Pre-requisites

- VKS-02 completed (CockroachDB cluster running)
- Operator deployed with `-enable-k8s-node-controller=true` flag
- At least 3 worker nodes in VKS cluster
- `kubectl` configured with VKS cluster kubeconfig

## Environment Setup

```bash
# Ensure KUBECONFIG points to VKS cluster
export KUBECONFIG=$(pwd)/vks-kubeconfig.yaml

# Set environment variables
export CRDB_OPERATOR_NS="crdb-operator"
export CRDB_CLUSTER_NS="crdb-cluster"

# Verify cluster is running
kubectl get pods -n ${CRDB_CLUSTER_NS} -o wide
kubectl get nodes
```

## Steps

### Step 1: Verify Operator Node Controller is Enabled

```bash
# Check operator deployment for node controller flag
kubectl get deployment -n ${CRDB_OPERATOR_NS} -o yaml | grep -i "node-controller"

# Check operator pod args
kubectl get pods -n ${CRDB_OPERATOR_NS} -o yaml | grep -A 20 "args:"

# If not enabled, the operator may need to be upgraded with this flag
# helm upgrade crdb-operator ./cockroachdb-parent/charts/operator \
#   --namespace ${CRDB_OPERATOR_NS} \
#   --reuse-values \
#   --set "operator.args={-enable-k8s-node-controller=true}"
```

### Step 2: Identify Target Node

```bash
# List CockroachDB pods and their nodes
kubectl get pods -n ${CRDB_CLUSTER_NS} -o wide

# Choose a node that:
# 1. Has a CockroachDB pod
# 2. Does NOT have the operator pod
# Store the node name
TARGET_NODE=$(kubectl get pods -n ${CRDB_CLUSTER_NS} cockroachdb-2 -o jsonpath='{.spec.nodeName}')
echo "Target node for decommission: ${TARGET_NODE}"

# Verify operator is NOT on this node
OPERATOR_NODE=$(kubectl get pods -n ${CRDB_OPERATOR_NS} -l app.kubernetes.io/name=cockroach-operator -o jsonpath='{.items[0].spec.nodeName}')
echo "Operator is on: ${OPERATOR_NODE}"

if [ "${TARGET_NODE}" == "${OPERATOR_NODE}" ]; then
  echo "WARNING: Choose a different target node - operator is on this node"
fi
```

### Step 3: Record Pre-Decommission State

```bash
# Record current node status
kubectl exec -n ${CRDB_CLUSTER_NS} cockroachdb-0 -- \
  cockroach node status --insecure --host=localhost:26257 > /tmp/pre-decommission-status.txt

cat /tmp/pre-decommission-status.txt

# Record which pod is on target node
TARGET_POD=$(kubectl get pods -n ${CRDB_CLUSTER_NS} -o wide | grep ${TARGET_NODE} | awk '{print $1}')
echo "Pod on target node: ${TARGET_POD}"

# Get the CockroachDB node ID for this pod
TARGET_CRDB_NODE=$(kubectl exec -n ${CRDB_CLUSTER_NS} ${TARGET_POD} -- \
  cockroach sql --insecure --host=localhost:26257 \
  --execute="SELECT node_id FROM crdb_internal.node_build_info;" -f csv | tail -1)
echo "CockroachDB node ID: ${TARGET_CRDB_NODE}"
```

### Step 4: Create Test Data

```bash
# Create test data to verify no data loss
kubectl exec -n ${CRDB_CLUSTER_NS} cockroachdb-0 -- \
  cockroach sql --insecure --host=localhost:26257 \
  --execute="
    CREATE DATABASE IF NOT EXISTS decommission_test;
    USE decommission_test;
    CREATE TABLE IF NOT EXISTS test_data (
      id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
      value INT,
      node_id INT DEFAULT crdb_internal.node_id()
    );
    INSERT INTO test_data (value) SELECT generate_series(1, 500);
    SELECT count(*) as row_count FROM test_data;
  "
```

### Step 5: Annotate Node for Decommissioning

```bash
# Apply the decommission annotation
kubectl annotate node ${TARGET_NODE} crdb.cockroachlabs.com/decommission="true"

# Verify annotation was applied
kubectl get node ${TARGET_NODE} -o jsonpath='{.metadata.annotations}' | grep crdb
```

**Expected Output:**
```
{"crdb.cockroachlabs.com/decommission":"true"...}
```

### Step 6: Monitor Operator Logs

```bash
# Watch operator logs for decommissioning activity
kubectl logs -n ${CRDB_OPERATOR_NS} -l app.kubernetes.io/name=cockroach-operator -f &
LOG_PID=$!

# Let it run for a minute to capture activity
sleep 60
kill $LOG_PID 2>/dev/null

# Check for decommission-related log entries
kubectl logs -n ${CRDB_OPERATOR_NS} -l app.kubernetes.io/name=cockroach-operator --tail=100 | grep -i decommission
```

### Step 7: Monitor Decommissioning Progress

```bash
# Watch node status changes
for i in {1..30}; do
  echo "=== Check $i ($(date)) ==="
  
  # Check CockroachDB node status
  kubectl exec -n ${CRDB_CLUSTER_NS} cockroachdb-0 -- \
    cockroach node status --insecure --host=localhost:26257 --decommission 2>/dev/null
  
  # Check pod status
  kubectl get pods -n ${CRDB_CLUSTER_NS} -o wide
  
  # Check if target pod has moved
  CURRENT_NODE=$(kubectl get pod -n ${CRDB_CLUSTER_NS} ${TARGET_POD} -o jsonpath='{.spec.nodeName}' 2>/dev/null)
  if [ "${CURRENT_NODE}" != "${TARGET_NODE}" ] || [ -z "${CURRENT_NODE}" ]; then
    echo "Pod has been rescheduled or terminated"
    break
  fi
  
  sleep 20
done
```

### Step 8: Verify Pod Rescheduling

```bash
# Check current pod distribution
kubectl get pods -n ${CRDB_CLUSTER_NS} -o wide

# Verify no pods on decommissioned node
kubectl get pods -n ${CRDB_CLUSTER_NS} -o wide | grep ${TARGET_NODE} || echo "No CRDB pods on ${TARGET_NODE}"

# Check if replacement pod started on different node
kubectl get pods -n ${CRDB_CLUSTER_NS} -o jsonpath='{range .items[*]}{.metadata.name}{"\t"}{.spec.nodeName}{"\n"}{end}'
```

**Expected Output:**
- No CockroachDB pods on the annotated node
- Replacement pod (if applicable) on a different node

### Step 9: Verify Data Migration

```bash
# Check that ranges have moved off the decommissioned node
kubectl exec -n ${CRDB_CLUSTER_NS} cockroachdb-0 -- \
  cockroach sql --insecure --host=localhost:26257 \
  --execute="
    SELECT 
      node_id,
      range_count,
      is_decommissioning
    FROM crdb_internal.kv_store_status
    ORDER BY node_id;
  "

# Verify no ranges on decommissioned node
kubectl exec -n ${CRDB_CLUSTER_NS} cockroachdb-0 -- \
  cockroach sql --insecure --host=localhost:26257 \
  --execute="
    SELECT count(*) as ranges_on_decommissioned 
    FROM crdb_internal.ranges 
    WHERE ${TARGET_CRDB_NODE} = ANY(replicas);
  "
```

**Expected Output:**
- Decommissioned node shows 0 ranges
- `is_decommissioning = true` for that node

### Step 10: Verify Cluster Health

```bash
# Check overall cluster health
kubectl exec -n ${CRDB_CLUSTER_NS} cockroachdb-0 -- \
  cockroach node status --insecure --host=localhost:26257

# Verify no stuck replicas
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

### Step 11: Verify Data Integrity

```bash
# Check test data is intact
kubectl exec -n ${CRDB_CLUSTER_NS} cockroachdb-0 -- \
  cockroach sql --insecure --host=localhost:26257 \
  --execute="
    USE decommission_test;
    SELECT count(*) as row_count FROM test_data;
    SELECT min(value), max(value) FROM test_data;
  "
```

**Expected Output:**
```
  row_count
------------
        500

  min | max
------+-----
    1 | 500
```

### Step 12: Remove Decommission Annotation (Cleanup)

```bash
# Remove the annotation to allow the node to be used again
kubectl annotate node ${TARGET_NODE} crdb.cockroachlabs.com/decommission-

# Verify annotation removed
kubectl get node ${TARGET_NODE} -o jsonpath='{.metadata.annotations}' | grep crdb || echo "Annotation removed"
```

## Validation Commands

```bash
# Complete validation script
echo "=== Node Decommission Validation ==="

echo -e "\n1. Target node annotation status:"
kubectl get node ${TARGET_NODE} -o jsonpath='{.metadata.annotations}' | grep -o "crdb[^,]*" || echo "No CRDB annotations"

echo -e "\n2. Pod distribution (no pods on ${TARGET_NODE}):"
kubectl get pods -n ${CRDB_CLUSTER_NS} -o wide

echo -e "\n3. CockroachDB node status:"
kubectl exec -n ${CRDB_CLUSTER_NS} cockroachdb-0 -- \
  cockroach node status --insecure --host=localhost:26257 --decommission

echo -e "\n4. Data integrity:"
kubectl exec -n ${CRDB_CLUSTER_NS} cockroachdb-0 -- \
  cockroach sql --insecure --host=localhost:26257 \
  --execute="SELECT count(*) as rows FROM decommission_test.test_data;"

echo -e "\n5. Under-replicated ranges:"
kubectl exec -n ${CRDB_CLUSTER_NS} cockroachdb-0 -- \
  cockroach sql --insecure --host=localhost:26257 \
  --execute="SELECT count(*) FROM crdb_internal.ranges WHERE array_length(replicas, 1) < 3;"

echo -e "\n=== Validation Complete ==="
```

## Expected Results

| Check | Expected State |
|-------|----------------|
| Node annotation | Applied, then removed |
| Pods on target node | None (moved elsewhere) |
| CockroachDB decommission | Node marked decommissioned |
| Data integrity | 500 rows intact |
| Under-replicated ranges | 0 |
| Cluster health | All remaining nodes live |

## Cleanup

```bash
# Remove decommission annotation if not already done
kubectl annotate node ${TARGET_NODE} crdb.cockroachlabs.com/decommission- 2>/dev/null

# Remove test database
kubectl exec -n ${CRDB_CLUSTER_NS} cockroachdb-0 -- \
  cockroach sql --insecure --host=localhost:26257 \
  --execute="DROP DATABASE IF EXISTS decommission_test CASCADE;"

# Remove temporary files
rm -f /tmp/pre-decommission-status.txt
```

## Notes

- The node controller feature must be explicitly enabled in the operator
- The operator pod should NOT be on the node being decommissioned
- This feature is useful for VKS node maintenance operations
- Decommissioning ensures data is safely migrated before pod termination
- The annotation triggers the operator to initiate CockroachDB decommissioning

## Troubleshooting

### Operator Not Responding to Annotation

```bash
# Verify node controller is enabled
kubectl get deployment -n ${CRDB_OPERATOR_NS} -o yaml | grep -i "enable-k8s-node-controller"

# Check operator logs for errors
kubectl logs -n ${CRDB_OPERATOR_NS} -l app.kubernetes.io/name=cockroach-operator --tail=100 | grep -i error

# Verify operator has permissions to watch nodes
kubectl auth can-i watch nodes --as=system:serviceaccount:${CRDB_OPERATOR_NS}:cockroach-operator-sa
```

### Decommissioning Stuck

```bash
# Check decommissioning status
kubectl exec -n ${CRDB_CLUSTER_NS} cockroachdb-0 -- \
  cockroach node status --insecure --host=localhost:26257 --decommission

# Check for stuck ranges
kubectl exec -n ${CRDB_CLUSTER_NS} cockroachdb-0 -- \
  cockroach sql --insecure --host=localhost:26257 \
  --execute="
    SELECT range_id, replicas 
    FROM crdb_internal.ranges 
    WHERE ${TARGET_CRDB_NODE} = ANY(replicas)
    LIMIT 10;
  "

# Check replication queue
kubectl exec -n ${CRDB_CLUSTER_NS} cockroachdb-0 -- \
  cockroach sql --insecure --host=localhost:26257 \
  --execute="SELECT * FROM crdb_internal.jobs WHERE job_type = 'REPLICATION';"
```

### Pod Not Rescheduling

```bash
# Check pod events
kubectl describe pod -n ${CRDB_CLUSTER_NS} ${TARGET_POD}

# Check if there are available nodes
kubectl get nodes -o wide

# Check for scheduling constraints
kubectl get statefulset -n ${CRDB_CLUSTER_NS} cockroachdb -o yaml | grep -A 20 "affinity"
```
