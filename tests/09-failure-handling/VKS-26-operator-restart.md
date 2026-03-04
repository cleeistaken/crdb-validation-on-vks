# VKS-26: Operator Restart During Cluster Operations

## Test Information

| Field | Value |
|-------|-------|
| **Test ID** | VKS-26 |
| **Category** | Failure Handling |
| **Dependencies** | [VKS-02](../01-cluster-provisioning/VKS-02-cluster-install.md) |

## Objective

Verify that the CockroachDB cluster continues to serve traffic when the operator is unavailable, and that the operator resumes reconciliation correctly after restart.

## Pre-requisites

- VKS-02 completed (CockroachDB cluster running)
- Active workload running on the cluster
- `kubectl` configured with VKS cluster kubeconfig

## Environment Setup

```bash
# Ensure KUBECONFIG points to VKS cluster
export KUBECONFIG=$(pwd)/vks-kubeconfig.yaml

# Set environment variables
export CRDB_OPERATOR_NS="crdb-operator"
export CRDB_CLUSTER_NS="crdb-cluster"
export CRDB_RELEASE_NAME="cockroachdb"
export CRDB_CHART_PATH="./cockroachdb-parent/charts/cockroachdb"

# Verify cluster and operator are running
kubectl get pods -n ${CRDB_OPERATOR_NS}
kubectl get pods -n ${CRDB_CLUSTER_NS}
```

## Steps

### Step 1: Record Initial State

```bash
# Record operator state
kubectl get pods -n ${CRDB_OPERATOR_NS} -o wide > /tmp/operator-initial.txt
kubectl get deployment -n ${CRDB_OPERATOR_NS} -o yaml > /tmp/operator-deployment.txt

# Record cluster state
kubectl get pods -n ${CRDB_CLUSTER_NS} -o wide > /tmp/cluster-initial.txt

# Record cluster health
kubectl exec -n ${CRDB_CLUSTER_NS} cockroachdb-0 -- \
  cockroach node status --insecure --host=localhost:26257 > /tmp/cluster-health-initial.txt

cat /tmp/cluster-health-initial.txt
```

### Step 2: Start Continuous Workload

```bash
# Create test database
kubectl exec -n ${CRDB_CLUSTER_NS} cockroachdb-0 -- \
  cockroach sql --insecure --host=localhost:26257 \
  --execute="
    CREATE DATABASE IF NOT EXISTS operator_test;
    USE operator_test;
    CREATE TABLE IF NOT EXISTS continuous_writes (
      id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
      value INT,
      created_at TIMESTAMP DEFAULT now()
    );
  "

# Start continuous workload
kubectl exec -n ${CRDB_CLUSTER_NS} cockroachdb-0 -- \
  /bin/bash -c "
    for i in \$(seq 1 300); do
      cockroach sql --insecure --host=cockroachdb-public:26257 \
        --execute=\"INSERT INTO operator_test.continuous_writes (value) VALUES (\$i);\" 2>&1 || echo \"Write failed at \$i\"
      sleep 1
    done
  " &
WORKLOAD_PID=$!
echo "Workload started with PID: ${WORKLOAD_PID}"
```

### Step 3: Delete Operator Pod(s)

```bash
# Wait for some writes to occur
sleep 10

# Record write count before operator deletion
WRITES_BEFORE=$(kubectl exec -n ${CRDB_CLUSTER_NS} cockroachdb-0 -- \
  cockroach sql --insecure --host=localhost:26257 \
  --execute="SELECT count(*) FROM operator_test.continuous_writes;" -f csv | tail -1)
echo "Writes before operator deletion: ${WRITES_BEFORE}"

# Delete operator pod(s)
echo "Deleting operator pods..."
kubectl delete pods -n ${CRDB_OPERATOR_NS} -l app.kubernetes.io/name=cockroach-operator

# Verify operator pods are gone
kubectl get pods -n ${CRDB_OPERATOR_NS}
```

### Step 4: Verify Cluster Continues to Serve Traffic

```bash
# Monitor writes during operator downtime
for i in {1..10}; do
  echo "=== Check $i (operator down) ==="
  
  # Check write count
  CURRENT_WRITES=$(kubectl exec -n ${CRDB_CLUSTER_NS} cockroachdb-0 -- \
    cockroach sql --insecure --host=localhost:26257 \
    --execute="SELECT count(*) FROM operator_test.continuous_writes;" -f csv 2>/dev/null | tail -1)
  echo "Current writes: ${CURRENT_WRITES}"
  
  # Check cluster health
  kubectl exec -n ${CRDB_CLUSTER_NS} cockroachdb-0 -- \
    cockroach sql --insecure --host=localhost:26257 \
    --execute="SELECT count(*) as live_nodes FROM crdb_internal.gossip_nodes WHERE is_live;" 2>/dev/null
  
  # Check operator status
  OPERATOR_PODS=$(kubectl get pods -n ${CRDB_OPERATOR_NS} --no-headers 2>/dev/null | wc -l)
  echo "Operator pods: ${OPERATOR_PODS}"
  
  sleep 5
done
```

### Step 5: Wait for Operator to Restart

```bash
# Wait for operator to be recreated by deployment controller
kubectl wait --for=condition=Available deployment -n ${CRDB_OPERATOR_NS} --timeout=120s

# Verify operator is running
kubectl get pods -n ${CRDB_OPERATOR_NS}

# Check operator logs for startup
kubectl logs -n ${CRDB_OPERATOR_NS} -l app.kubernetes.io/name=cockroach-operator --tail=20
```

### Step 6: Verify Writes Continued During Operator Downtime

```bash
# Check write count after operator restart
WRITES_AFTER=$(kubectl exec -n ${CRDB_CLUSTER_NS} cockroachdb-0 -- \
  cockroach sql --insecure --host=localhost:26257 \
  --execute="SELECT count(*) FROM operator_test.continuous_writes;" -f csv | tail -1)
echo "Writes after operator restart: ${WRITES_AFTER}"

# Calculate writes during downtime
WRITES_DURING=$((WRITES_AFTER - WRITES_BEFORE))
echo "Writes during operator downtime: ${WRITES_DURING}"

# Verify writes continued (should be > 0)
if [ "${WRITES_DURING}" -gt 0 ]; then
  echo "SUCCESS: Cluster continued serving traffic during operator downtime"
else
  echo "WARNING: No writes during operator downtime - check workload"
fi
```

### Step 7: Test Reconciliation After Restart

```bash
# Make a Helm value change to trigger reconciliation
helm upgrade ${CRDB_RELEASE_NAME} ${CRDB_CHART_PATH} \
  --namespace ${CRDB_CLUSTER_NS} \
  --reuse-values \
  --set "statefulset.annotations.reconcile-test=$(date +%s)"

# Watch for reconciliation
kubectl logs -n ${CRDB_OPERATOR_NS} -l app.kubernetes.io/name=cockroach-operator -f &
LOG_PID=$!
sleep 15
kill $LOG_PID 2>/dev/null

# Verify cluster is still healthy
kubectl get pods -n ${CRDB_CLUSTER_NS}
```

### Step 8: Verify Cluster Health After Reconciliation

```bash
# Stop workload
kill $WORKLOAD_PID 2>/dev/null
wait $WORKLOAD_PID 2>/dev/null

# Final write count
FINAL_WRITES=$(kubectl exec -n ${CRDB_CLUSTER_NS} cockroachdb-0 -- \
  cockroach sql --insecure --host=localhost:26257 \
  --execute="SELECT count(*) FROM operator_test.continuous_writes;" -f csv | tail -1)
echo "Final write count: ${FINAL_WRITES}"

# Check cluster health
kubectl exec -n ${CRDB_CLUSTER_NS} cockroachdb-0 -- \
  cockroach node status --insecure --host=localhost:26257

# Compare with initial health
echo "=== Initial health ==="
cat /tmp/cluster-health-initial.txt
```

### Step 9: Verify No Data Loss

```bash
# Verify data integrity
kubectl exec -n ${CRDB_CLUSTER_NS} cockroachdb-0 -- \
  cockroach sql --insecure --host=localhost:26257 \
  --execute="
    SELECT 
      count(*) as total_writes,
      min(value) as min_value,
      max(value) as max_value,
      min(created_at) as first_write,
      max(created_at) as last_write
    FROM operator_test.continuous_writes;
  "

# Check for gaps in sequence
kubectl exec -n ${CRDB_CLUSTER_NS} cockroachdb-0 -- \
  cockroach sql --insecure --host=localhost:26257 \
  --execute="
    WITH expected AS (
      SELECT generate_series(1, (SELECT max(value) FROM operator_test.continuous_writes)) as val
    )
    SELECT count(*) as missing_values
    FROM expected e
    LEFT JOIN operator_test.continuous_writes w ON e.val = w.value
    WHERE w.value IS NULL AND e.val <= 300;
  "
```

## Validation Commands

```bash
# Complete validation script
echo "=== Operator Restart Validation ==="

echo -e "\n1. Operator running:"
kubectl get pods -n ${CRDB_OPERATOR_NS}

echo -e "\n2. Cluster running:"
kubectl get pods -n ${CRDB_CLUSTER_NS}

echo -e "\n3. Cluster health:"
kubectl exec -n ${CRDB_CLUSTER_NS} cockroachdb-0 -- \
  cockroach node status --insecure --host=localhost:26257

echo -e "\n4. Write count:"
kubectl exec -n ${CRDB_CLUSTER_NS} cockroachdb-0 -- \
  cockroach sql --insecure --host=localhost:26257 \
  --execute="SELECT count(*) as writes FROM operator_test.continuous_writes;"

echo -e "\n5. Operator logs (recent):"
kubectl logs -n ${CRDB_OPERATOR_NS} -l app.kubernetes.io/name=cockroach-operator --tail=10

echo -e "\n=== Validation Complete ==="
```

## Expected Results

| Check | Expected State |
|-------|----------------|
| Cluster during operator downtime | Continues serving traffic |
| Writes during downtime | > 0 (continuous) |
| Operator restart | Automatic via deployment |
| Reconciliation after restart | Works correctly |
| Data integrity | No data loss |
| Cluster health | Unchanged |

## Cleanup

```bash
# Remove test database
kubectl exec -n ${CRDB_CLUSTER_NS} cockroachdb-0 -- \
  cockroach sql --insecure --host=localhost:26257 \
  --execute="DROP DATABASE IF EXISTS operator_test CASCADE;"

# Remove temporary files
rm -f /tmp/operator-initial.txt /tmp/operator-deployment.txt
rm -f /tmp/cluster-initial.txt /tmp/cluster-health-initial.txt

# Kill any remaining workload processes
pkill -f "continuous_writes" 2>/dev/null
```

## Notes

- The operator is decoupled from CockroachDB cluster availability
- Cluster continues to serve traffic without the operator
- Operator is needed for reconciliation and cluster management operations
- Kubernetes deployment controller automatically restarts the operator
- This validates the operator's crash recovery behavior

## Troubleshooting

### Operator Not Restarting

```bash
# Check deployment status
kubectl get deployment -n ${CRDB_OPERATOR_NS}
kubectl describe deployment -n ${CRDB_OPERATOR_NS}

# Check events
kubectl get events -n ${CRDB_OPERATOR_NS} --sort-by='.lastTimestamp'

# Check replica count
kubectl get deployment -n ${CRDB_OPERATOR_NS} -o jsonpath='{.spec.replicas}'
```

### Cluster Affected by Operator Restart

```bash
# Check cluster events
kubectl get events -n ${CRDB_CLUSTER_NS} --sort-by='.lastTimestamp'

# Check pod logs
kubectl logs -n ${CRDB_CLUSTER_NS} cockroachdb-0 --tail=50

# Verify no unexpected restarts
kubectl get pods -n ${CRDB_CLUSTER_NS} -o jsonpath='{range .items[*]}{.metadata.name}{"\t"}{.status.containerStatuses[0].restartCount}{"\n"}{end}'
```

### Reconciliation Fails After Restart

```bash
# Check operator logs for errors
kubectl logs -n ${CRDB_OPERATOR_NS} -l app.kubernetes.io/name=cockroach-operator | grep -i error

# Check CRD status
kubectl get crd crdbclusters.crdb.cockroachlabs.com -o jsonpath='{.status.conditions}'

# Verify RBAC permissions
kubectl auth can-i --list --as=system:serviceaccount:${CRDB_OPERATOR_NS}:cockroach-operator-sa -n ${CRDB_CLUSTER_NS}
```
