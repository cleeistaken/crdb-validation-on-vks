# VKS-14: Operator Helm Chart Upgrade

## Test Information

| Field | Value |
|-------|-------|
| **Test ID** | VKS-14 |
| **Category** | Operator Lifecycle |
| **Dependencies** | [VKS-01](../01-cluster-provisioning/VKS-01-operator-install.md) |

## Objective

Upgrade the CockroachDB Operator Helm chart to a newer version, verify the operator pod rolls out successfully, confirm no unintended changes to managed clusters, and validate logs and metrics.

## Pre-requisites

- VKS-01 completed (CockroachDB Operator installed)
- VKS-02 completed (CockroachDB cluster running - to verify no disruption)
- New operator chart version available
- `kubectl` configured with VKS cluster kubeconfig
- `helm` CLI installed (v3.x)

## Environment Setup

```bash
# Ensure KUBECONFIG points to VKS cluster
export KUBECONFIG=$(pwd)/vks-kubeconfig.yaml

# Set environment variables
export CRDB_OPERATOR_NS="crdb-operator"
export CRDB_CLUSTER_NS="crdb-cluster"
export OPERATOR_RELEASE_NAME="crdb-operator"
export OPERATOR_CHART_PATH="./cockroachdb-parent/charts/operator"

# Verify current state
kubectl get pods -n ${CRDB_OPERATOR_NS}
kubectl get pods -n ${CRDB_CLUSTER_NS}
```

## Steps

### Step 1: Record Current Operator State

```bash
# Get current operator version
helm list -n ${CRDB_OPERATOR_NS}

# Record operator pod details
kubectl get pods -n ${CRDB_OPERATOR_NS} -o wide > /tmp/pre-upgrade-operator.txt
kubectl get deployment -n ${CRDB_OPERATOR_NS} -o yaml > /tmp/pre-upgrade-operator-deployment.txt

# Get current operator image
CURRENT_OPERATOR_IMAGE=$(kubectl get deployment -n ${CRDB_OPERATOR_NS} -o jsonpath='{.items[0].spec.template.spec.containers[0].image}')
echo "Current operator image: ${CURRENT_OPERATOR_IMAGE}"

# Record CRD versions
kubectl get crd crdbclusters.crdb.cockroachlabs.com -o yaml | grep -A 5 "versions:" > /tmp/pre-upgrade-crd.txt
cat /tmp/pre-upgrade-crd.txt
```

### Step 2: Record Managed Cluster State

```bash
# Record current CockroachDB cluster state
kubectl get crdbcluster -A 2>/dev/null || echo "No CrdbCluster CRs found (using Helm-only deployment)"

# Record pod state
kubectl get pods -n ${CRDB_CLUSTER_NS} -o wide > /tmp/pre-upgrade-cluster.txt

# Record cluster health
kubectl exec -n ${CRDB_CLUSTER_NS} cockroachdb-0 -- \
  cockroach node status --insecure --host=localhost:26257 > /tmp/pre-upgrade-health.txt

cat /tmp/pre-upgrade-cluster.txt
```

### Step 3: Check New Chart Version

```bash
# Show new chart details
helm show chart ${OPERATOR_CHART_PATH}

# Compare values (if upgrading from repo)
# helm show values ${OPERATOR_CHART_PATH} > /tmp/new-operator-values.txt
```

### Step 4: Perform Operator Upgrade

```bash
# Upgrade the operator
helm upgrade ${OPERATOR_RELEASE_NAME} ${OPERATOR_CHART_PATH} \
  --namespace ${CRDB_OPERATOR_NS} \
  --reuse-values \
  --wait \
  --timeout 5m
```

**Expected Output:**
```
Release "crdb-operator" has been upgraded. Happy Helming!
```

### Step 5: Monitor Operator Rollout

```bash
# Watch operator pod rollout
kubectl rollout status deployment -n ${CRDB_OPERATOR_NS} --timeout=120s

# Verify new pod is running
kubectl get pods -n ${CRDB_OPERATOR_NS}

# Check operator logs during startup
kubectl logs -n ${CRDB_OPERATOR_NS} -l app.kubernetes.io/name=cockroach-operator --tail=50
```

### Step 6: Verify Operator Pod Health

```bash
# Check pod status
kubectl get pods -n ${CRDB_OPERATOR_NS} -o wide

# Verify no restarts
kubectl get pods -n ${CRDB_OPERATOR_NS} -o jsonpath='{range .items[*]}{.metadata.name}{"\t"}Restarts: {.status.containerStatuses[0].restartCount}{"\n"}{end}'

# Check for errors in logs
kubectl logs -n ${CRDB_OPERATOR_NS} -l app.kubernetes.io/name=cockroach-operator | grep -i -E "(error|warn|fail)" | tail -20 || echo "No errors found"
```

### Step 7: Verify No Unintended Cluster Changes

```bash
# Compare cluster state
echo "=== Pre-upgrade cluster state ==="
cat /tmp/pre-upgrade-cluster.txt

echo -e "\n=== Current cluster state ==="
kubectl get pods -n ${CRDB_CLUSTER_NS} -o wide

# Verify pods haven't restarted
kubectl get pods -n ${CRDB_CLUSTER_NS} -o jsonpath='{range .items[*]}{.metadata.name}{"\t"}Restarts: {.status.containerStatuses[0].restartCount}{"\t"}Age: {.status.startTime}{"\n"}{end}'

# Verify cluster health unchanged
kubectl exec -n ${CRDB_CLUSTER_NS} cockroachdb-0 -- \
  cockroach node status --insecure --host=localhost:26257

# Compare with pre-upgrade
echo -e "\n=== Pre-upgrade health ==="
cat /tmp/pre-upgrade-health.txt
```

### Step 8: Verify CRD Consistency

```bash
# Check CRD versions after upgrade
kubectl get crd crdbclusters.crdb.cockroachlabs.com -o yaml | grep -A 5 "versions:"

# Compare with pre-upgrade
echo -e "\n=== Pre-upgrade CRD ==="
cat /tmp/pre-upgrade-crd.txt

# Verify CRD is still served
kubectl get crd crdbclusters.crdb.cockroachlabs.com -o jsonpath='{.status.conditions[?(@.type=="Established")].status}'
echo ""
```

### Step 9: Test Operator Reconciliation

```bash
# Trigger a no-op reconciliation by updating an annotation
kubectl annotate pods -n ${CRDB_CLUSTER_NS} cockroachdb-0 \
  test-annotation="operator-upgrade-test-$(date +%s)" --overwrite

# Watch operator logs for reconciliation
kubectl logs -n ${CRDB_OPERATOR_NS} -l app.kubernetes.io/name=cockroach-operator --tail=20 -f &
LOG_PID=$!
sleep 10
kill $LOG_PID 2>/dev/null

# Verify cluster still healthy
kubectl exec -n ${CRDB_CLUSTER_NS} cockroachdb-0 -- \
  cockroach sql --insecure --host=localhost:26257 \
  --execute="SELECT count(*) as live_nodes FROM crdb_internal.gossip_nodes WHERE is_live;"
```

### Step 10: Perform Helm Value Change Test

```bash
# Test that operator can still process Helm upgrades
# This is a no-op upgrade to verify reconciliation works
helm upgrade ${OPERATOR_RELEASE_NAME} ${OPERATOR_CHART_PATH} \
  --namespace ${CRDB_OPERATOR_NS} \
  --reuse-values \
  --set "operator.annotations.upgrade-test=true" \
  --wait \
  --timeout 2m

# Verify operator still running
kubectl get pods -n ${CRDB_OPERATOR_NS}
```

### Step 11: Verify Metrics Endpoint (If Available)

```bash
# Check if operator exposes metrics
kubectl get svc -n ${CRDB_OPERATOR_NS}

# If metrics service exists, test it
METRICS_SVC=$(kubectl get svc -n ${CRDB_OPERATOR_NS} -o name | grep metrics || echo "")
if [ -n "${METRICS_SVC}" ]; then
  kubectl port-forward -n ${CRDB_OPERATOR_NS} ${METRICS_SVC} 8080:8080 &
  PF_PID=$!
  sleep 3
  curl -s http://localhost:8080/metrics | head -20
  kill $PF_PID 2>/dev/null
else
  echo "No metrics service found"
fi
```

## Validation Commands

```bash
# Complete validation script
echo "=== Operator Upgrade Validation ==="

echo -e "\n1. Operator Helm release:"
helm list -n ${CRDB_OPERATOR_NS}

echo -e "\n2. Operator pod status:"
kubectl get pods -n ${CRDB_OPERATOR_NS}

echo -e "\n3. Operator pod restarts (should be 0):"
kubectl get pods -n ${CRDB_OPERATOR_NS} -o jsonpath='{range .items[*]}{.metadata.name}: {.status.containerStatuses[0].restartCount} restarts{"\n"}{end}'

echo -e "\n4. CockroachDB cluster unchanged:"
kubectl get pods -n ${CRDB_CLUSTER_NS}

echo -e "\n5. CRD status:"
kubectl get crd crdbclusters.crdb.cockroachlabs.com -o jsonpath='Established: {.status.conditions[?(@.type=="Established")].status}'
echo ""

echo -e "\n6. Cluster health:"
kubectl exec -n ${CRDB_CLUSTER_NS} cockroachdb-0 -- \
  cockroach sql --insecure --host=localhost:26257 \
  --execute="SELECT count(*) as live_nodes FROM crdb_internal.gossip_nodes WHERE is_live;"

echo -e "\n=== Validation Complete ==="
```

## Expected Results

| Check | Expected State |
|-------|----------------|
| Helm upgrade | Successful |
| Operator pod | Running, 0 restarts |
| CockroachDB pods | Unchanged (no restarts) |
| CRD | Established, versions consistent |
| Operator logs | No errors |
| Reconciliation | Works correctly |
| Cluster health | Unchanged |

## Cleanup

```bash
# Remove test annotations
kubectl annotate pods -n ${CRDB_CLUSTER_NS} cockroachdb-0 test-annotation- 2>/dev/null

# Remove temporary files
rm -f /tmp/pre-upgrade-operator.txt /tmp/pre-upgrade-operator-deployment.txt
rm -f /tmp/pre-upgrade-crd.txt /tmp/pre-upgrade-cluster.txt /tmp/pre-upgrade-health.txt
```

## Notes

- Operator upgrades should be non-disruptive to managed clusters
- The operator is decoupled from the CockroachDB cluster availability
- CRD changes may require migration (see VKS-15)
- Always review release notes before upgrading the operator

## Troubleshooting

### Operator Pod Not Starting

```bash
# Check events
kubectl get events -n ${CRDB_OPERATOR_NS} --sort-by='.lastTimestamp'

# Check pod description
kubectl describe pod -n ${CRDB_OPERATOR_NS} -l app.kubernetes.io/name=cockroach-operator

# Check previous logs
kubectl logs -n ${CRDB_OPERATOR_NS} -l app.kubernetes.io/name=cockroach-operator --previous
```

### CRD Conflicts

```bash
# Check CRD status
kubectl get crd crdbclusters.crdb.cockroachlabs.com -o yaml | grep -A 20 "status:"

# Check for conversion webhook issues
kubectl get validatingwebhookconfigurations | grep cockroach
kubectl get mutatingwebhookconfigurations | grep cockroach
```

### Reconciliation Issues

```bash
# Check operator logs for reconciliation errors
kubectl logs -n ${CRDB_OPERATOR_NS} -l app.kubernetes.io/name=cockroach-operator --tail=100 | grep -i reconcil

# Check CrdbCluster status (if using CR)
kubectl get crdbcluster -A -o yaml | grep -A 10 "status:"
```
