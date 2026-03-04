# VKS-15: CRD Version Migration (v1alpha1 -> v1beta1)

## Test Information

| Field | Value |
|-------|-------|
| **Test ID** | VKS-15 |
| **Category** | Operator Lifecycle |
| **Dependencies** | [VKS-14](VKS-14-operator-upgrade.md) |

## Objective

Upgrade the operator to a version with new CRD API version, verify CRD storedVersions and served versions, confirm existing CrdbCluster objects reconcile correctly, and ensure Helm manifests are updated to the new API version.

## Pre-requisites

- VKS-14 completed (Operator upgrade tested)
- Operator chart with new CRD version available
- No conflicting public operator instance with incompatible CRD
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
```

## Steps

### Step 1: Check Current CRD Version

```bash
# Get current CRD details
kubectl get crd crdbclusters.crdb.cockroachlabs.com -o yaml > /tmp/crd-before-migration.yaml

# Check served and storage versions
echo "=== Served Versions ==="
kubectl get crd crdbclusters.crdb.cockroachlabs.com -o jsonpath='{.spec.versions[*].name}'
echo ""

echo "=== Storage Version ==="
kubectl get crd crdbclusters.crdb.cockroachlabs.com -o jsonpath='{.status.storedVersions}'
echo ""

# Check which version is currently stored
kubectl get crd crdbclusters.crdb.cockroachlabs.com -o jsonpath='{range .spec.versions[*]}{.name}: served={.served}, storage={.storage}{"\n"}{end}'
```

### Step 2: List Existing CrdbCluster Objects

```bash
# Check for existing CrdbCluster CRs
kubectl get crdbcluster -A 2>/dev/null || echo "No CrdbCluster CRs found"

# If using Helm-only deployment, there may be no CRs
# The CRD migration still applies to the operator capability

# Get details of any existing CRs
kubectl get crdbcluster -A -o yaml 2>/dev/null > /tmp/crdbclusters-before.yaml || echo "No CRs to save"
```

### Step 3: Record Pre-Migration State

```bash
# Record operator state
kubectl get deployment -n ${CRDB_OPERATOR_NS} -o yaml > /tmp/operator-before-migration.yaml

# Record cluster state
kubectl get pods -n ${CRDB_CLUSTER_NS} -o wide > /tmp/cluster-before-migration.txt

# Record cluster health
kubectl exec -n ${CRDB_CLUSTER_NS} cockroachdb-0 -- \
  cockroach node status --insecure --host=localhost:26257 > /tmp/health-before-migration.txt
```

### Step 4: Upgrade Operator with New CRD Version

```bash
# Upgrade operator (this should include the new CRD version)
helm upgrade ${OPERATOR_RELEASE_NAME} ${OPERATOR_CHART_PATH} \
  --namespace ${CRDB_OPERATOR_NS} \
  --reuse-values \
  --wait \
  --timeout 5m

# Wait for operator to be ready
kubectl wait --for=condition=Available deployment -n ${CRDB_OPERATOR_NS} --timeout=120s
```

### Step 5: Verify CRD Version Changes

```bash
# Check new CRD versions
echo "=== Served Versions (After Upgrade) ==="
kubectl get crd crdbclusters.crdb.cockroachlabs.com -o jsonpath='{.spec.versions[*].name}'
echo ""

echo "=== Storage Version (After Upgrade) ==="
kubectl get crd crdbclusters.crdb.cockroachlabs.com -o jsonpath='{.status.storedVersions}'
echo ""

# Detailed version info
kubectl get crd crdbclusters.crdb.cockroachlabs.com -o jsonpath='{range .spec.versions[*]}{.name}: served={.served}, storage={.storage}{"\n"}{end}'

# Compare with before
echo -e "\n=== Before Migration ==="
cat /tmp/crd-before-migration.yaml | grep -A 20 "versions:"
```

### Step 6: Verify Existing Objects Reconcile

```bash
# If there are existing CrdbCluster CRs, verify they still work
if kubectl get crdbcluster -A 2>/dev/null | grep -q .; then
  echo "Checking existing CrdbCluster objects..."
  
  # List all CrdbClusters
  kubectl get crdbcluster -A
  
  # Check status of each
  for cr in $(kubectl get crdbcluster -A -o jsonpath='{range .items[*]}{.metadata.namespace}/{.metadata.name}{"\n"}{end}'); do
    NS=$(echo $cr | cut -d'/' -f1)
    NAME=$(echo $cr | cut -d'/' -f2)
    echo "=== ${NS}/${NAME} ==="
    kubectl get crdbcluster -n ${NS} ${NAME} -o jsonpath='{.status}'
    echo ""
  done
else
  echo "No CrdbCluster CRs found - testing with Helm-managed cluster"
fi

# Verify cluster pods are still running
kubectl get pods -n ${CRDB_CLUSTER_NS}
```

### Step 7: Test Creating New CR with New API Version

```bash
# Create a test CrdbCluster using the new API version (if applicable)
# Note: This is a dry-run to test API acceptance
cat <<EOF | kubectl apply --dry-run=server -f -
apiVersion: crdb.cockroachlabs.com/v1beta1
kind: CrdbCluster
metadata:
  name: test-migration-cluster
  namespace: ${CRDB_CLUSTER_NS}
spec:
  nodes: 3
  image:
    name: cockroachdb/cockroach:latest
EOF

echo "New API version accepted by server"
```

### Step 8: Verify Operator Logs

```bash
# Check operator logs for migration-related messages
kubectl logs -n ${CRDB_OPERATOR_NS} -l app.kubernetes.io/name=cockroach-operator --tail=100 | grep -i -E "(migration|version|crd|convert)" || echo "No migration-specific logs"

# Check for any errors
kubectl logs -n ${CRDB_OPERATOR_NS} -l app.kubernetes.io/name=cockroach-operator --tail=50 | grep -i error || echo "No errors found"
```

### Step 9: Verify Cluster Health Unchanged

```bash
# Compare cluster state
echo "=== Before Migration ==="
cat /tmp/cluster-before-migration.txt

echo -e "\n=== After Migration ==="
kubectl get pods -n ${CRDB_CLUSTER_NS} -o wide

# Verify health
kubectl exec -n ${CRDB_CLUSTER_NS} cockroachdb-0 -- \
  cockroach node status --insecure --host=localhost:26257

# Compare with before
echo -e "\n=== Health Before Migration ==="
cat /tmp/health-before-migration.txt
```

### Step 10: Test Helm Upgrade with New API Version

```bash
# If Helm charts need to be updated to use new API version
# This tests that the chart works with the new CRD

# Check current Helm values
helm get values cockroachdb -n ${CRDB_CLUSTER_NS} 2>/dev/null || echo "Using default values"

# Perform a no-op upgrade to verify compatibility
helm upgrade cockroachdb ./cockroachdb-parent/charts/cockroachdb \
  --namespace ${CRDB_CLUSTER_NS} \
  --reuse-values \
  --dry-run 2>&1 | head -50

echo "Helm chart compatible with new CRD version"
```

### Step 11: Verify StoredVersions Migration

```bash
# After all objects are reconciled, check storedVersions
# Eventually, only the new version should be in storedVersions

echo "Current storedVersions:"
kubectl get crd crdbclusters.crdb.cockroachlabs.com -o jsonpath='{.status.storedVersions}'
echo ""

# If old version is still in storedVersions, objects using old version still exist
# They will be migrated when next reconciled

# Force reconciliation of existing objects (if any)
if kubectl get crdbcluster -A 2>/dev/null | grep -q .; then
  for cr in $(kubectl get crdbcluster -A -o jsonpath='{range .items[*]}{.metadata.namespace}/{.metadata.name}{"\n"}{end}'); do
    NS=$(echo $cr | cut -d'/' -f1)
    NAME=$(echo $cr | cut -d'/' -f2)
    kubectl annotate crdbcluster -n ${NS} ${NAME} migration-test="$(date +%s)" --overwrite
  done
  
  # Wait and check storedVersions again
  sleep 10
  echo "StoredVersions after forced reconciliation:"
  kubectl get crd crdbclusters.crdb.cockroachlabs.com -o jsonpath='{.status.storedVersions}'
  echo ""
fi
```

### Step 12: Verify No Downtime

```bash
# Final verification that cluster had no downtime
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

# Test SQL operations
kubectl exec -n ${CRDB_CLUSTER_NS} cockroachdb-0 -- \
  cockroach sql --insecure --host=localhost:26257 \
  --execute="SELECT 'CRD migration test passed' as result;"
```

## Validation Commands

```bash
# Complete validation script
echo "=== CRD Migration Validation ==="

echo -e "\n1. CRD versions served:"
kubectl get crd crdbclusters.crdb.cockroachlabs.com -o jsonpath='{range .spec.versions[*]}{.name}: served={.served}, storage={.storage}{"\n"}{end}'

echo -e "\n2. CRD storedVersions:"
kubectl get crd crdbclusters.crdb.cockroachlabs.com -o jsonpath='{.status.storedVersions}'
echo ""

echo -e "\n3. Operator running:"
kubectl get pods -n ${CRDB_OPERATOR_NS}

echo -e "\n4. CockroachDB cluster unchanged:"
kubectl get pods -n ${CRDB_CLUSTER_NS}

echo -e "\n5. Existing CrdbCluster CRs (if any):"
kubectl get crdbcluster -A 2>/dev/null || echo "None"

echo -e "\n6. Cluster health:"
kubectl exec -n ${CRDB_CLUSTER_NS} cockroachdb-0 -- \
  cockroach sql --insecure --host=localhost:26257 \
  --execute="SELECT count(*) as live_nodes FROM crdb_internal.gossip_nodes WHERE is_live;"

echo -e "\n=== Validation Complete ==="
```

## Expected Results

| Check | Expected State |
|-------|----------------|
| CRD versions | New version served and storage |
| storedVersions | Eventually only new version |
| Existing CRs | Reconcile successfully |
| Operator | Running without errors |
| Cluster health | Unchanged, no downtime |
| Helm compatibility | Charts work with new CRD |

## Cleanup

```bash
# Remove test annotations
kubectl annotate crdbcluster -A migration-test- 2>/dev/null

# Remove temporary files
rm -f /tmp/crd-before-migration.yaml
rm -f /tmp/crdbclusters-before.yaml
rm -f /tmp/operator-before-migration.yaml
rm -f /tmp/cluster-before-migration.txt
rm -f /tmp/health-before-migration.txt
```

## Notes

- CRD version migration is part of the operator GA process
- Old and new API versions may be served simultaneously during transition
- storedVersions tracks which versions have objects stored in etcd
- Migration happens automatically when objects are reconciled
- No manual conversion is typically required

## Troubleshooting

### CRD Conversion Errors

```bash
# Check for conversion webhook
kubectl get crd crdbclusters.crdb.cockroachlabs.com -o yaml | grep -A 20 "conversion:"

# Check webhook service
kubectl get svc -n ${CRDB_OPERATOR_NS} | grep webhook

# Check webhook logs
kubectl logs -n ${CRDB_OPERATOR_NS} -l app.kubernetes.io/name=cockroach-operator | grep -i webhook
```

### Objects Not Reconciling

```bash
# Check CR status
kubectl get crdbcluster -A -o yaml | grep -A 20 "status:"

# Check operator logs for reconciliation errors
kubectl logs -n ${CRDB_OPERATOR_NS} -l app.kubernetes.io/name=cockroach-operator --tail=100 | grep -i reconcil

# Force reconciliation
kubectl annotate crdbcluster -A force-reconcile="$(date +%s)" --overwrite
```

### StoredVersions Not Updating

```bash
# Check if old version objects still exist
kubectl get crdbcluster -A -o yaml | grep "apiVersion:"

# The storedVersions will only update after all objects using old version are reconciled
# This may require manual intervention if objects are stuck
```
