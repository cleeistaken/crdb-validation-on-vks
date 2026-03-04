# VKS-27: VKS Kubernetes Cluster Upgrade Smoke Test

## Test Information

| Field | Value |
|-------|-------|
| **Test ID** | VKS-27 |
| **Category** | Failure Handling |
| **Dependencies** | [VKS-02](../01-cluster-provisioning/VKS-02-cluster-install.md) |

## Objective

Verify that CockroachDB cluster and operator survive a VKS Kubernetes cluster upgrade, maintaining availability within defined SLO, with no data loss.

## Pre-requisites

- VKS-02 completed (CockroachDB cluster running)
- VKS cluster upgrade path available
- PodDisruptionBudgets configured for CockroachDB
- `kubectl` configured with VKS cluster kubeconfig
- Access to Supervisor cluster for VKS management

## Environment Setup

```bash
# Ensure KUBECONFIG points to VKS cluster
export KUBECONFIG=$(pwd)/vks-kubeconfig.yaml

# Set environment variables
export CRDB_OPERATOR_NS="crdb-operator"
export CRDB_CLUSTER_NS="crdb-cluster"
export VKS_CLUSTER_NAME="cluster-vks"

# Record current Kubernetes version
kubectl version --short

# Verify cluster is running
kubectl get pods -n ${CRDB_CLUSTER_NS}
kubectl get pods -n ${CRDB_OPERATOR_NS}
```

## Steps

### Step 1: Record Pre-Upgrade State

```bash
# Record Kubernetes version
kubectl version -o yaml > /tmp/k8s-version-before.txt

# Record node versions
kubectl get nodes -o wide > /tmp/nodes-before.txt

# Record CockroachDB cluster state
kubectl get pods -n ${CRDB_CLUSTER_NS} -o wide > /tmp/crdb-pods-before.txt

# Record cluster health
kubectl exec -n ${CRDB_CLUSTER_NS} cockroachdb-0 -- \
  cockroach node status --insecure --host=localhost:26257 > /tmp/crdb-health-before.txt

# Record data state
kubectl exec -n ${CRDB_CLUSTER_NS} cockroachdb-0 -- \
  cockroach sql --insecure --host=localhost:26257 \
  --execute="
    CREATE DATABASE IF NOT EXISTS upgrade_test;
    USE upgrade_test;
    CREATE TABLE IF NOT EXISTS checkpoint (
      id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
      k8s_version STRING,
      created_at TIMESTAMP DEFAULT now()
    );
    INSERT INTO checkpoint (k8s_version) VALUES ('$(kubectl version --short 2>/dev/null | grep Server | awk '{print $3}')');
    SELECT count(*) as checkpoints FROM checkpoint;
  "
```

### Step 2: Verify PodDisruptionBudgets

```bash
# Check if PDBs exist for CockroachDB
kubectl get pdb -n ${CRDB_CLUSTER_NS}

# If no PDB exists, create one
if ! kubectl get pdb -n ${CRDB_CLUSTER_NS} 2>/dev/null | grep -q cockroachdb; then
  cat <<EOF | kubectl apply -f -
apiVersion: policy/v1
kind: PodDisruptionBudget
metadata:
  name: cockroachdb-pdb
  namespace: ${CRDB_CLUSTER_NS}
spec:
  minAvailable: 2
  selector:
    matchLabels:
      app.kubernetes.io/name: cockroachdb
EOF
fi

# Verify PDB
kubectl get pdb -n ${CRDB_CLUSTER_NS} -o yaml
```

### Step 3: Start Continuous Health Check

```bash
# Start health check workload
kubectl exec -n ${CRDB_CLUSTER_NS} cockroachdb-0 -- \
  /bin/bash -c "
    for i in \$(seq 1 600); do
      RESULT=\$(cockroach sql --insecure --host=cockroachdb-public:26257 \
        --execute=\"SELECT 1 as health;\" 2>&1)
      if echo \"\$RESULT\" | grep -q \"health\"; then
        echo \"\$(date): Health check \$i - OK\"
      else
        echo \"\$(date): Health check \$i - FAILED: \$RESULT\"
      fi
      sleep 5
    done
  " > /tmp/health-checks.log 2>&1 &
HEALTH_PID=$!
echo "Health check started with PID: ${HEALTH_PID}"
```

### Step 4: Start Continuous Write Workload

```bash
# Start write workload
kubectl exec -n ${CRDB_CLUSTER_NS} cockroachdb-0 -- \
  /bin/bash -c "
    for i in \$(seq 1 600); do
      cockroach sql --insecure --host=cockroachdb-public:26257 \
        --execute=\"INSERT INTO upgrade_test.checkpoint (k8s_version) VALUES ('during-upgrade-\$i');\" 2>&1 || echo \"Write \$i failed\"
      sleep 5
    done
  " > /tmp/write-workload.log 2>&1 &
WRITE_PID=$!
echo "Write workload started with PID: ${WRITE_PID}"
```

### Step 5: Initiate VKS Cluster Upgrade

```bash
# Switch to Supervisor cluster context
# Note: This requires access to the Supervisor cluster
echo "=== VKS Upgrade Instructions ==="
echo "1. Access the Supervisor cluster"
echo "2. Update the VKS cluster spec with new Kubernetes version"
echo ""

# Example upgrade command (run from Supervisor context):
cat <<EOF
# From Supervisor cluster:
kubectl patch cluster ${VKS_CLUSTER_NAME} --type=merge -p '
{
  "spec": {
    "topology": {
      "version": "v1.36.0+vmware.1-vkr.1"
    }
  }
}'
EOF

echo ""
echo "Monitor the upgrade from Supervisor cluster:"
echo "kubectl get cluster ${VKS_CLUSTER_NAME} -w"
echo ""
echo "Press Enter when upgrade is initiated..."
read
```

### Step 6: Monitor Upgrade Progress

```bash
# Monitor from VKS cluster perspective
echo "Monitoring upgrade from VKS cluster..."

for i in {1..60}; do
  echo "=== Check $i ($(date)) ==="
  
  # Check node status
  echo "Nodes:"
  kubectl get nodes -o wide 2>/dev/null || echo "Unable to reach API server"
  
  # Check CockroachDB pods
  echo -e "\nCockroachDB pods:"
  kubectl get pods -n ${CRDB_CLUSTER_NS} -o wide 2>/dev/null || echo "Unable to list pods"
  
  # Check cluster health (if accessible)
  kubectl exec -n ${CRDB_CLUSTER_NS} cockroachdb-0 -- \
    cockroach sql --insecure --host=localhost:26257 \
    --execute="SELECT count(*) as live FROM crdb_internal.gossip_nodes WHERE is_live;" 2>/dev/null || echo "Unable to query cluster"
  
  sleep 30
done
```

### Step 7: Verify Upgrade Completion

```bash
# Check new Kubernetes version
kubectl version -o yaml > /tmp/k8s-version-after.txt
echo "=== Kubernetes Version After Upgrade ==="
kubectl version --short

# Check node versions
kubectl get nodes -o wide

# Compare with before
echo -e "\n=== Before Upgrade ==="
cat /tmp/k8s-version-before.txt | grep -A 2 "serverVersion"
```

### Step 8: Verify CockroachDB Cluster Health

```bash
# Stop monitoring workloads
kill $HEALTH_PID 2>/dev/null
kill $WRITE_PID 2>/dev/null

# Check cluster health
kubectl exec -n ${CRDB_CLUSTER_NS} cockroachdb-0 -- \
  cockroach node status --insecure --host=localhost:26257

# Check all pods are running
kubectl get pods -n ${CRDB_CLUSTER_NS} -o wide

# Check operator is running
kubectl get pods -n ${CRDB_OPERATOR_NS}
```

### Step 9: Verify Data Integrity

```bash
# Check data survived upgrade
kubectl exec -n ${CRDB_CLUSTER_NS} cockroachdb-0 -- \
  cockroach sql --insecure --host=localhost:26257 \
  --execute="
    SELECT count(*) as total_checkpoints FROM upgrade_test.checkpoint;
    SELECT k8s_version, count(*) as count 
    FROM upgrade_test.checkpoint 
    GROUP BY k8s_version 
    ORDER BY count DESC 
    LIMIT 10;
  "

# Insert post-upgrade checkpoint
kubectl exec -n ${CRDB_CLUSTER_NS} cockroachdb-0 -- \
  cockroach sql --insecure --host=localhost:26257 \
  --execute="
    INSERT INTO upgrade_test.checkpoint (k8s_version) 
    VALUES ('$(kubectl version --short 2>/dev/null | grep Server | awk '{print $3}')');
  "
```

### Step 10: Analyze Health Check Results

```bash
# Analyze health check log
echo "=== Health Check Summary ==="
echo "Total checks:"
grep -c "Health check" /tmp/health-checks.log 2>/dev/null || echo "0"

echo "Successful checks:"
grep -c "OK" /tmp/health-checks.log 2>/dev/null || echo "0"

echo "Failed checks:"
grep -c "FAILED" /tmp/health-checks.log 2>/dev/null || echo "0"

# Show any failures
echo -e "\nFailed health checks:"
grep "FAILED" /tmp/health-checks.log 2>/dev/null | head -10 || echo "None"
```

## Validation Commands

```bash
# Complete validation script
echo "=== VKS Upgrade Validation ==="

echo -e "\n1. Kubernetes version:"
kubectl version --short

echo -e "\n2. Node status:"
kubectl get nodes

echo -e "\n3. CockroachDB pods:"
kubectl get pods -n ${CRDB_CLUSTER_NS}

echo -e "\n4. Operator pods:"
kubectl get pods -n ${CRDB_OPERATOR_NS}

echo -e "\n5. Cluster health:"
kubectl exec -n ${CRDB_CLUSTER_NS} cockroachdb-0 -- \
  cockroach node status --insecure --host=localhost:26257

echo -e "\n6. Data integrity:"
kubectl exec -n ${CRDB_CLUSTER_NS} cockroachdb-0 -- \
  cockroach sql --insecure --host=localhost:26257 \
  --execute="SELECT count(*) as checkpoints FROM upgrade_test.checkpoint;"

echo -e "\n=== Validation Complete ==="
```

## Expected Results

| Check | Expected State |
|-------|----------------|
| VKS upgrade | Completed successfully |
| Kubernetes version | Updated to target version |
| CockroachDB pods | Running, minimal restarts |
| Operator | Running |
| Cluster health | All nodes live |
| Data integrity | No data loss |
| Availability | Within SLO (brief interruptions acceptable) |

## Cleanup

```bash
# Remove test database
kubectl exec -n ${CRDB_CLUSTER_NS} cockroachdb-0 -- \
  cockroach sql --insecure --host=localhost:26257 \
  --execute="DROP DATABASE IF EXISTS upgrade_test CASCADE;"

# Remove PDB if created for test
# kubectl delete pdb cockroachdb-pdb -n ${CRDB_CLUSTER_NS}

# Remove temporary files
rm -f /tmp/k8s-version-before.txt /tmp/k8s-version-after.txt
rm -f /tmp/nodes-before.txt /tmp/crdb-pods-before.txt /tmp/crdb-health-before.txt
rm -f /tmp/health-checks.log /tmp/write-workload.log
```

## Notes

- This is a high-level validation test
- Exact VKS upgrade steps depend on VMware/Broadcom guidance
- Capture exact VKS versions, CNI/CSI versions in test results
- PDBs help prevent draining too many CockroachDB pods simultaneously
- Some brief unavailability during node upgrades is expected

## Troubleshooting

### Upgrade Stuck

```bash
# Check cluster status from Supervisor
# kubectl get cluster ${VKS_CLUSTER_NAME} -o yaml

# Check machine status
# kubectl get machines -o wide

# Check events
kubectl get events --sort-by='.lastTimestamp' | tail -20
```

### Pods Not Rescheduling

```bash
# Check PDB status
kubectl get pdb -n ${CRDB_CLUSTER_NS} -o yaml

# Check node status
kubectl get nodes
kubectl describe node <node-name>

# Check pod events
kubectl get events -n ${CRDB_CLUSTER_NS} --sort-by='.lastTimestamp'
```

### Data Loss Detected

```bash
# Check range status
kubectl exec -n ${CRDB_CLUSTER_NS} cockroachdb-0 -- \
  cockroach sql --insecure --host=localhost:26257 \
  --execute="SELECT count(*) FROM crdb_internal.ranges WHERE array_length(replicas, 1) < 3;"

# Check for unavailable ranges
kubectl exec -n ${CRDB_CLUSTER_NS} cockroachdb-0 -- \
  cockroach sql --insecure --host=localhost:26257 \
  --execute="SELECT * FROM crdb_internal.ranges WHERE array_length(replicas, 1) = 0;"
```
