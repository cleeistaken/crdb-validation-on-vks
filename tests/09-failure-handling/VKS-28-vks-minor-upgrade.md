# VKS-28: VKS Minor Upgrade Smoke Test

## Test Information

| Field | Value |
|-------|-------|
| **Test ID** | VKS-28 |
| **Category** | Failure Handling |
| **Dependencies** | [VKS-02](../01-cluster-provisioning/VKS-02-cluster-install.md) |

## Objective

Verify that CockroachDB cluster and operator survive a VKS minor version upgrade, with pods rescheduled as needed and no lasting errors.

## Pre-requisites

- VKS-02 completed (CockroachDB cluster running)
- VKS minor upgrade path available in lab environment
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

# Record current version
kubectl version --short

# Verify cluster is running
kubectl get pods -A | grep -E "(cockroach|crdb)"
```

## Steps

### Step 1: Record Pre-Upgrade State

```bash
# Record current state
kubectl version -o yaml > /tmp/k8s-minor-before.txt
kubectl get nodes -o yaml > /tmp/nodes-minor-before.txt
kubectl get pods -n ${CRDB_CLUSTER_NS} -o yaml > /tmp/crdb-minor-before.txt

# Record cluster health
kubectl exec -n ${CRDB_CLUSTER_NS} cockroachdb-0 -- \
  cockroach node status --insecure --host=localhost:26257

# Create test data
kubectl exec -n ${CRDB_CLUSTER_NS} cockroachdb-0 -- \
  cockroach sql --insecure --host=localhost:26257 \
  --execute="
    CREATE DATABASE IF NOT EXISTS minor_upgrade_test;
    USE minor_upgrade_test;
    CREATE TABLE IF NOT EXISTS data (
      id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
      value INT,
      created_at TIMESTAMP DEFAULT now()
    );
    INSERT INTO data (value) SELECT generate_series(1, 100);
    SELECT count(*) as rows FROM data;
  "
```

### Step 2: Start Monitoring

```bash
# Start continuous monitoring
kubectl exec -n ${CRDB_CLUSTER_NS} cockroachdb-0 -- \
  /bin/bash -c "
    while true; do
      echo \"\$(date): \$(cockroach sql --insecure --host=cockroachdb-public:26257 --execute='SELECT count(*) FROM crdb_internal.gossip_nodes WHERE is_live;' 2>&1 | tail -1)\"
      sleep 10
    done
  " > /tmp/minor-upgrade-monitor.log 2>&1 &
MONITOR_PID=$!
echo "Monitoring started with PID: ${MONITOR_PID}"
```

### Step 3: Initiate Minor Upgrade

```bash
# Instructions for minor upgrade
echo "=== VKS Minor Upgrade Instructions ==="
echo ""
echo "From Supervisor cluster, update the VKS cluster to next minor version."
echo ""
echo "Example (adjust version as needed):"
cat <<EOF
kubectl patch cluster ${VKS_CLUSTER_NAME} --type=merge -p '
{
  "spec": {
    "topology": {
      "version": "v1.35.1+vmware.1-vkr.1"
    }
  }
}'
EOF
echo ""
echo "Monitor upgrade:"
echo "kubectl get cluster ${VKS_CLUSTER_NAME} -w"
echo ""
echo "Press Enter when upgrade is initiated..."
read
```

### Step 4: Monitor During Upgrade

```bash
# Monitor cluster during upgrade
for i in {1..30}; do
  echo "=== Check $i ($(date)) ==="
  
  # Node status
  kubectl get nodes 2>/dev/null || echo "API server unavailable"
  
  # Pod status
  kubectl get pods -n ${CRDB_CLUSTER_NS} 2>/dev/null || echo "Cannot list pods"
  
  # Quick health check
  kubectl exec -n ${CRDB_CLUSTER_NS} cockroachdb-0 -- \
    cockroach sql --insecure --host=localhost:26257 \
    --execute="SELECT 1;" 2>/dev/null && echo "SQL OK" || echo "SQL unavailable"
  
  sleep 20
done
```

### Step 5: Verify Upgrade Completion

```bash
# Stop monitoring
kill $MONITOR_PID 2>/dev/null

# Check new version
kubectl version --short

# Check nodes
kubectl get nodes -o wide

# Check pods
kubectl get pods -n ${CRDB_CLUSTER_NS}
kubectl get pods -n ${CRDB_OPERATOR_NS}
```

### Step 6: Verify Cluster Health

```bash
# Check CockroachDB health
kubectl exec -n ${CRDB_CLUSTER_NS} cockroachdb-0 -- \
  cockroach node status --insecure --host=localhost:26257

# Verify all nodes live
kubectl exec -n ${CRDB_CLUSTER_NS} cockroachdb-0 -- \
  cockroach sql --insecure --host=localhost:26257 \
  --execute="SELECT node_id, is_live FROM crdb_internal.gossip_nodes ORDER BY node_id;"
```

### Step 7: Verify Data Integrity

```bash
# Check test data
kubectl exec -n ${CRDB_CLUSTER_NS} cockroachdb-0 -- \
  cockroach sql --insecure --host=localhost:26257 \
  --execute="
    SELECT count(*) as rows FROM minor_upgrade_test.data;
    SELECT min(value), max(value) FROM minor_upgrade_test.data;
  "
```

### Step 8: Analyze Monitoring Log

```bash
# Check for any failures during upgrade
echo "=== Monitoring Summary ==="
grep -c "live" /tmp/minor-upgrade-monitor.log 2>/dev/null || echo "0 successful checks"
grep -i "error\|fail" /tmp/minor-upgrade-monitor.log 2>/dev/null | head -10 || echo "No errors"
```

## Validation Commands

```bash
# Complete validation script
echo "=== VKS Minor Upgrade Validation ==="

echo -e "\n1. Kubernetes version:"
kubectl version --short

echo -e "\n2. Nodes:"
kubectl get nodes

echo -e "\n3. CockroachDB pods:"
kubectl get pods -n ${CRDB_CLUSTER_NS}

echo -e "\n4. Cluster health:"
kubectl exec -n ${CRDB_CLUSTER_NS} cockroachdb-0 -- \
  cockroach node status --insecure --host=localhost:26257

echo -e "\n5. Data integrity:"
kubectl exec -n ${CRDB_CLUSTER_NS} cockroachdb-0 -- \
  cockroach sql --insecure --host=localhost:26257 \
  --execute="SELECT count(*) FROM minor_upgrade_test.data;"

echo -e "\n=== Validation Complete ==="
```

## Expected Results

| Check | Expected State |
|-------|----------------|
| Minor upgrade | Completed |
| Nodes | All Ready |
| CockroachDB pods | Running |
| Operator | Running |
| Data | Intact |

## Cleanup

```bash
# Remove test database
kubectl exec -n ${CRDB_CLUSTER_NS} cockroachdb-0 -- \
  cockroach sql --insecure --host=localhost:26257 \
  --execute="DROP DATABASE IF EXISTS minor_upgrade_test CASCADE;"

# Remove temporary files
rm -f /tmp/k8s-minor-before.txt /tmp/nodes-minor-before.txt
rm -f /tmp/crdb-minor-before.txt /tmp/minor-upgrade-monitor.log
```

## Notes

- Minor upgrades are typically less disruptive than major upgrades
- Details depend on lab capabilities and VMware guidance
- Document exact versions tested

## Troubleshooting

See VKS-27 for detailed troubleshooting steps.
