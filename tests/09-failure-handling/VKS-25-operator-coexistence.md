# VKS-25: Coexistence with Legacy Public Operator

## Test Information

| Field | Value |
|-------|-------|
| **Test ID** | VKS-25 |
| **Category** | Compatibility |
| **Dependencies** | [VKS-01](../01-cluster-provisioning/VKS-01-operator-install.md) |

## Objective

Verify that the new CockroachDB Operator can coexist with the legacy Public Operator on the same VKS cluster without CRD conflicts, with each operator managing only its own clusters.

## Pre-requisites

- VKS-01 completed (New CockroachDB Operator installed)
- Legacy Public Operator available (optional - for existing deployments)
- `kubectl` configured with VKS cluster kubeconfig

## Environment Setup

```bash
# Ensure KUBECONFIG points to VKS cluster
export KUBECONFIG=$(pwd)/vks-kubeconfig.yaml

# Set environment variables
export NEW_OPERATOR_NS="crdb-operator"
export LEGACY_OPERATOR_NS="crdb-operator-legacy"
export NEW_CLUSTER_NS="crdb-new"
export LEGACY_CLUSTER_NS="crdb-legacy"

# Verify new operator is running
kubectl get pods -n ${NEW_OPERATOR_NS}
```

## Steps

### Step 1: Check Current CRD State

```bash
# List CockroachDB-related CRDs
kubectl get crds | grep cockroach

# Check CRD details
kubectl get crd crdbclusters.crdb.cockroachlabs.com -o yaml | head -50

# Check CRD versions
kubectl get crd crdbclusters.crdb.cockroachlabs.com -o jsonpath='{.spec.versions[*].name}'
echo ""
```

### Step 2: Deploy Legacy Operator (If Testing Coexistence)

```bash
# Create namespace for legacy operator
kubectl create namespace ${LEGACY_OPERATOR_NS}

# Note: The legacy public operator installation depends on your source
# This is a placeholder for the actual installation command

# Option 1: From OperatorHub (if available)
# kubectl apply -f https://operatorhub.io/install/cockroachdb.yaml

# Option 2: From Helm (if legacy chart available)
# helm install legacy-operator ./legacy-operator-chart -n ${LEGACY_OPERATOR_NS}

# For this test, we'll simulate by checking if both can coexist
echo "Legacy operator deployment is environment-specific"
echo "Skip this step if only testing new operator"
```

### Step 3: Verify CRD Compatibility

```bash
# Check if CRDs conflict
kubectl get crd crdbclusters.crdb.cockroachlabs.com -o yaml | grep -A 20 "versions:"

# Check stored versions
kubectl get crd crdbclusters.crdb.cockroachlabs.com -o jsonpath='{.status.storedVersions}'
echo ""

# Verify CRD is established
kubectl get crd crdbclusters.crdb.cockroachlabs.com -o jsonpath='{.status.conditions[?(@.type=="Established")].status}'
echo ""
```

### Step 4: Deploy Cluster with New Operator

```bash
# Create namespace for new operator's cluster
kubectl create namespace ${NEW_CLUSTER_NS}

# Deploy cluster using new operator's Helm chart
helm install cockroachdb-new ./cockroachdb-parent/charts/cockroachdb \
  --namespace ${NEW_CLUSTER_NS} \
  --set conf.cluster-name="new-operator-cluster" \
  --set statefulset.replicas=3 \
  --wait \
  --timeout 10m

# Verify cluster is running
kubectl get pods -n ${NEW_CLUSTER_NS}
```

### Step 5: Verify New Operator Manages Its Cluster

```bash
# Check new operator logs for reconciliation
kubectl logs -n ${NEW_OPERATOR_NS} -l app.kubernetes.io/name=cockroach-operator --tail=50 | grep -i reconcil

# Verify cluster health
kubectl exec -n ${NEW_CLUSTER_NS} cockroachdb-new-0 -- \
  cockroach node status --insecure --host=localhost:26257
```

### Step 6: Deploy Cluster with Legacy Operator (If Available)

```bash
# If legacy operator is installed, create its cluster
# kubectl create namespace ${LEGACY_CLUSTER_NS}

# Create CrdbCluster CR for legacy operator
# cat <<EOF | kubectl apply -f -
# apiVersion: crdb.cockroachlabs.com/v1alpha1
# kind: CrdbCluster
# metadata:
#   name: legacy-cluster
#   namespace: ${LEGACY_CLUSTER_NS}
# spec:
#   nodes: 3
#   image:
#     name: cockroachdb/cockroach:latest
# EOF

echo "Legacy cluster deployment depends on operator availability"
```

### Step 7: Verify Each Operator Manages Only Its Clusters

```bash
# Check new operator logs - should only show its cluster
kubectl logs -n ${NEW_OPERATOR_NS} -l app.kubernetes.io/name=cockroach-operator --tail=100 | grep -E "(namespace|cluster)" | head -20

# If legacy operator is running, check its logs
# kubectl logs -n ${LEGACY_OPERATOR_NS} -l app=cockroach-operator --tail=100 | grep -E "(namespace|cluster)" | head -20

# List all CrdbCluster resources
kubectl get crdbcluster -A 2>/dev/null || echo "No CrdbCluster CRs found"
```

### Step 8: Perform No-Op Helm Upgrades

```bash
# Upgrade new operator (no-op)
helm upgrade crdb-operator ./cockroachdb-parent/charts/operator \
  --namespace ${NEW_OPERATOR_NS} \
  --reuse-values

# Verify no disruption to clusters
kubectl get pods -n ${NEW_CLUSTER_NS}

# Upgrade cluster managed by new operator (no-op)
helm upgrade cockroachdb-new ./cockroachdb-parent/charts/cockroachdb \
  --namespace ${NEW_CLUSTER_NS} \
  --reuse-values

# Verify cluster health
kubectl exec -n ${NEW_CLUSTER_NS} cockroachdb-new-0 -- \
  cockroach sql --insecure --host=localhost:26257 \
  --execute="SELECT count(*) as live_nodes FROM crdb_internal.gossip_nodes WHERE is_live;"
```

### Step 9: Verify No CRD Conflicts

```bash
# Check CRD status
kubectl get crd crdbclusters.crdb.cockroachlabs.com -o jsonpath='{.status.conditions}'
echo ""

# Check for any CRD-related errors in operator logs
kubectl logs -n ${NEW_OPERATOR_NS} -l app.kubernetes.io/name=cockroach-operator | grep -i -E "(crd|conflict|error)" | tail -10 || echo "No CRD errors"

# Verify both operators (if running) are healthy
kubectl get pods -n ${NEW_OPERATOR_NS}
# kubectl get pods -n ${LEGACY_OPERATOR_NS}
```

### Step 10: Test Cluster Independence

```bash
# Make a change to new operator's cluster
kubectl exec -n ${NEW_CLUSTER_NS} cockroachdb-new-0 -- \
  cockroach sql --insecure --host=localhost:26257 \
  --execute="
    CREATE DATABASE IF NOT EXISTS coexistence_test;
    USE coexistence_test;
    CREATE TABLE test (id INT PRIMARY KEY);
    INSERT INTO test VALUES (1);
    SELECT * FROM test;
  "

# Verify change doesn't affect other clusters
# (If legacy cluster exists, verify it's independent)
```

## Validation Commands

```bash
# Complete validation script
echo "=== Operator Coexistence Validation ==="

echo -e "\n1. CRD status:"
kubectl get crd crdbclusters.crdb.cockroachlabs.com -o jsonpath='Established: {.status.conditions[?(@.type=="Established")].status}'
echo ""

echo -e "\n2. New operator running:"
kubectl get pods -n ${NEW_OPERATOR_NS}

echo -e "\n3. New operator's cluster:"
kubectl get pods -n ${NEW_CLUSTER_NS}

echo -e "\n4. CRD versions:"
kubectl get crd crdbclusters.crdb.cockroachlabs.com -o jsonpath='{.spec.versions[*].name}'
echo ""

echo -e "\n5. Cluster health:"
kubectl exec -n ${NEW_CLUSTER_NS} cockroachdb-new-0 -- \
  cockroach node status --insecure --host=localhost:26257

echo -e "\n6. No CRD conflicts in logs:"
kubectl logs -n ${NEW_OPERATOR_NS} -l app.kubernetes.io/name=cockroach-operator --tail=50 | grep -i conflict || echo "No conflicts"

echo -e "\n=== Validation Complete ==="
```

## Expected Results

| Check | Expected State |
|-------|----------------|
| CRD | Established, no conflicts |
| New operator | Running |
| Legacy operator | Running (if deployed) |
| Each operator | Manages only its clusters |
| No-op upgrades | Successful, no disruption |
| Cluster independence | Changes isolated to each cluster |

## Cleanup

```bash
# Remove new operator's cluster
helm uninstall cockroachdb-new -n ${NEW_CLUSTER_NS}
kubectl delete pvc --all -n ${NEW_CLUSTER_NS}
kubectl delete namespace ${NEW_CLUSTER_NS}

# Remove legacy operator's cluster (if created)
# kubectl delete crdbcluster legacy-cluster -n ${LEGACY_CLUSTER_NS}
# kubectl delete pvc --all -n ${LEGACY_CLUSTER_NS}
# kubectl delete namespace ${LEGACY_CLUSTER_NS}

# Remove legacy operator (if deployed)
# kubectl delete namespace ${LEGACY_OPERATOR_NS}
```

## Notes

- This test is important for migration scenarios
- CRD version compatibility is critical
- Each operator should have namespace-scoped permissions where possible
- Monitor for any cross-operator reconciliation attempts
- Plan migration path from legacy to new operator

## Troubleshooting

### CRD Conflicts

```bash
# Check CRD ownership
kubectl get crd crdbclusters.crdb.cockroachlabs.com -o yaml | grep -A 5 "ownerReferences"

# Check for multiple CRD definitions
kubectl get crd | grep crdb

# Delete and recreate CRD if needed (DANGEROUS - affects all clusters)
# kubectl delete crd crdbclusters.crdb.cockroachlabs.com
```

### Operator Reconciling Wrong Cluster

```bash
# Check operator's watched namespaces
kubectl get deployment -n ${NEW_OPERATOR_NS} -o yaml | grep -A 10 "args:"

# Check RBAC permissions
kubectl get clusterrolebinding | grep cockroach
kubectl get rolebinding -A | grep cockroach
```

### Both Operators Trying to Manage Same Cluster

```bash
# Check cluster annotations/labels
kubectl get pods -n ${NEW_CLUSTER_NS} --show-labels

# Check operator logs for conflicts
kubectl logs -n ${NEW_OPERATOR_NS} -l app.kubernetes.io/name=cockroach-operator | grep -i -E "(conflict|already|managed)"
```
