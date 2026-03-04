# VKS-01: Install CockroachDB Operator via Helm

## Test Information

| Field | Value |
|-------|-------|
| **Test ID** | VKS-01 |
| **Category** | Cluster Provisioning |
| **Dependencies** | [00-VKS-CLUSTER](../00-prerequisites/00-VKS-CLUSTER.md) |

## Objective

Install the CockroachDB Operator using Helm on a VKS cluster and validate compatibility with Pod Security Admission (PSA/PSS), service accounts, and RBAC roles.

## Pre-requisites

- VKS cluster deployed and accessible (00-VKS-CLUSTER completed)
- `kubectl` configured with VKS cluster kubeconfig
- `helm` CLI installed (v3.x)
- CockroachDB Operator Helm chart accessible at `./cockroachdb-parent/charts/operator`

## Environment Setup

```bash
# Ensure KUBECONFIG points to VKS cluster
export KUBECONFIG=$(pwd)/vks-kubeconfig.yaml

# Set environment variables
export CRDB_OPERATOR_NS="crdb-operator"
export OPERATOR_RELEASE_NAME="crdb-operator"
export OPERATOR_CHART_PATH="./cockroachdb-parent/charts/operator"

# Verify cluster access
kubectl cluster-info
```

## Steps

### Step 1: Create Operator Namespace

```bash
# Create the namespace for the operator
kubectl create namespace ${CRDB_OPERATOR_NS}

# Verify namespace creation
kubectl get namespace ${CRDB_OPERATOR_NS}
```

**Expected Output:**
```
namespace/crdb-operator created
```

### Step 2: Verify Helm Chart

```bash
# Check Helm chart exists and is valid
helm lint ${OPERATOR_CHART_PATH}

# Show chart information
helm show chart ${OPERATOR_CHART_PATH}
```

**Expected Output:**
- Lint passes with no errors
- Chart metadata displayed (name, version, description)

### Step 3: Review Default Values (Optional)

```bash
# View default Helm values
helm show values ${OPERATOR_CHART_PATH}
```

### Step 4: Install CockroachDB Operator

```bash
# Install the operator using Helm
helm install ${OPERATOR_RELEASE_NAME} ${OPERATOR_CHART_PATH} \
  --namespace ${CRDB_OPERATOR_NS} \
  --wait \
  --timeout 5m

# Verify Helm release
helm list -n ${CRDB_OPERATOR_NS}
```

**Expected Output:**
```
NAME            NAMESPACE       REVISION    UPDATED                                 STATUS      CHART               APP VERSION
crdb-operator   crdb-operator   1           2024-XX-XX XX:XX:XX.XXXXXX +0000 UTC   deployed    cockroach-operator-X.X.X    X.X.X
```

### Step 5: Verify Operator Pods

```bash
# Check operator pods
kubectl get pods -n ${CRDB_OPERATOR_NS}

# Wait for pods to be ready
kubectl wait --for=condition=Ready pods --all -n ${CRDB_OPERATOR_NS} --timeout=300s
```

**Expected Output:**
```
NAME                                      READY   STATUS    RESTARTS   AGE
cockroach-operator-XXXXXXXXX-XXXXX        1/1     Running   0          XXs
```

### Step 6: Verify CRDs Installed

```bash
# List CockroachDB CRDs
kubectl get crds | grep cockroach

# Describe the CrdbCluster CRD
kubectl get crd crdbclusters.crdb.cockroachlabs.com -o yaml | head -50
```

**Expected Output:**
```
crdbclusters.crdb.cockroachlabs.com    YYYY-MM-DDTHH:MM:SSZ
```

### Step 7: Verify RBAC Resources

```bash
# Check service accounts
kubectl get serviceaccounts -n ${CRDB_OPERATOR_NS}

# Check cluster roles
kubectl get clusterroles | grep cockroach

# Check cluster role bindings
kubectl get clusterrolebindings | grep cockroach

# Check roles in namespace
kubectl get roles -n ${CRDB_OPERATOR_NS}

# Check role bindings in namespace
kubectl get rolebindings -n ${CRDB_OPERATOR_NS}
```

**Expected Output:**
- Service account for operator exists
- ClusterRole and ClusterRoleBinding for operator exist
- Appropriate RBAC permissions configured

### Step 8: Verify Operator Logs

```bash
# Check operator logs for any errors
kubectl logs -n ${CRDB_OPERATOR_NS} -l app.kubernetes.io/name=cockroach-operator --tail=50

# Check for any warning or error messages
kubectl logs -n ${CRDB_OPERATOR_NS} -l app.kubernetes.io/name=cockroach-operator | grep -i -E "(error|warn|fail)" || echo "No errors found"
```

**Expected Output:**
- Operator logs show successful startup
- No error or warning messages

### Step 9: Verify Pod Security Context

```bash
# Describe operator pod to check security context
kubectl get pods -n ${CRDB_OPERATOR_NS} -o yaml | grep -A 20 "securityContext"

# Check if pod runs as non-root
kubectl get pods -n ${CRDB_OPERATOR_NS} -o jsonpath='{.items[*].spec.containers[*].securityContext}'
```

**Expected Output:**
- Pod runs with appropriate security context
- No privileged containers
- runAsNonRoot set if required by cluster policy

## Validation Commands

```bash
# Complete validation script
echo "=== Operator Installation Validation ==="

echo -e "\n1. Namespace exists:"
kubectl get namespace ${CRDB_OPERATOR_NS}

echo -e "\n2. Helm release deployed:"
helm status ${OPERATOR_RELEASE_NAME} -n ${CRDB_OPERATOR_NS}

echo -e "\n3. Operator pods running:"
kubectl get pods -n ${CRDB_OPERATOR_NS}

echo -e "\n4. CRDs installed:"
kubectl get crds | grep cockroach

echo -e "\n5. No pod restarts (CrashLoopBackOff check):"
kubectl get pods -n ${CRDB_OPERATOR_NS} -o jsonpath='{range .items[*]}{.metadata.name}{"\t"}{.status.containerStatuses[*].restartCount}{"\n"}{end}'

echo -e "\n=== Validation Complete ==="
```

## Expected Results

| Check | Expected State |
|-------|----------------|
| Namespace | `crdb-operator` exists |
| Helm release | `deployed` status |
| Operator pod(s) | `Running` with `1/1` Ready |
| Pod restarts | 0 (no CrashLoopBackOff) |
| CRDs | `crdbclusters.crdb.cockroachlabs.com` installed |
| RBAC | ServiceAccount, ClusterRole, ClusterRoleBinding exist |
| Security | Pod runs without privileged access |

## Cleanup

**Note:** Only run cleanup if you need to remove the operator. This will affect dependent tests.

```bash
# Uninstall operator
helm uninstall ${OPERATOR_RELEASE_NAME} -n ${CRDB_OPERATOR_NS}

# Delete CRDs (optional - removes all CrdbCluster resources)
kubectl delete crd crdbclusters.crdb.cockroachlabs.com

# Delete namespace
kubectl delete namespace ${CRDB_OPERATOR_NS}
```

## Notes

- This is the baseline install test for VKS compatibility
- Validates that the operator works with VKS Pod Security Admission policies
- The operator must be installed before any CockroachDB clusters can be deployed
- Keep the operator running for all subsequent tests

## Troubleshooting

### Pod stuck in Pending

```bash
# Check events
kubectl get events -n ${CRDB_OPERATOR_NS} --sort-by='.lastTimestamp'

# Check pod description
kubectl describe pod -n ${CRDB_OPERATOR_NS} -l app.kubernetes.io/name=cockroach-operator
```

### CrashLoopBackOff

```bash
# Check pod logs
kubectl logs -n ${CRDB_OPERATOR_NS} -l app.kubernetes.io/name=cockroach-operator --previous

# Check resource limits
kubectl get pods -n ${CRDB_OPERATOR_NS} -o yaml | grep -A 10 "resources:"
```

### RBAC Issues

```bash
# Check if service account has proper permissions
kubectl auth can-i --list --as=system:serviceaccount:${CRDB_OPERATOR_NS}:cockroach-operator-sa
```
