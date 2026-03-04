# VKS-05: Pod Security Admission / Pod Security Standards Compatibility

## Test Information

| Field | Value |
|-------|-------|
| **Test ID** | VKS-05 |
| **Category** | Security & Platform |
| **Dependencies** | [VKS-02](../01-cluster-provisioning/VKS-02-cluster-install.md) |

## Objective

Verify that the CockroachDB operator and cluster pods run successfully under VKS Pod Security Admission (PSA) constraints without requiring privileged access, hostPath, or other restricted capabilities.

## Pre-requisites

- VKS-02 completed (CockroachDB cluster installed)
- `kubectl` configured with VKS cluster kubeconfig
- Understanding of Kubernetes Pod Security Standards (baseline/restricted)

## Environment Setup

```bash
# Ensure KUBECONFIG points to VKS cluster
export KUBECONFIG=$(pwd)/vks-kubeconfig.yaml

# Set environment variables
export CRDB_OPERATOR_NS="crdb-operator"
export CRDB_CLUSTER_NS="crdb-cluster"

# Verify cluster is running
kubectl get pods -n ${CRDB_CLUSTER_NS}
kubectl get pods -n ${CRDB_OPERATOR_NS}
```

## Steps

### Step 1: Check Namespace PSA Labels

```bash
# Check PSA labels on operator namespace
kubectl get namespace ${CRDB_OPERATOR_NS} -o yaml | grep -A 5 "labels:"

# Check PSA labels on cluster namespace
kubectl get namespace ${CRDB_CLUSTER_NS} -o yaml | grep -A 5 "labels:"

# List all namespaces with PSA labels
kubectl get namespaces -o custom-columns=NAME:.metadata.name,ENFORCE:.metadata.labels.'pod-security\.kubernetes\.io/enforce',WARN:.metadata.labels.'pod-security\.kubernetes\.io/warn'
```

**Expected Output:**
- Namespaces may have PSA labels like `pod-security.kubernetes.io/enforce: baseline` or `restricted`

### Step 2: Verify Operator Pod Security Context

```bash
# Get operator pod security context
kubectl get pods -n ${CRDB_OPERATOR_NS} -o yaml | grep -A 30 "securityContext"

# Check specific security settings
kubectl get pods -n ${CRDB_OPERATOR_NS} -o jsonpath='{range .items[*]}
Pod: {.metadata.name}
  runAsNonRoot: {.spec.securityContext.runAsNonRoot}
  runAsUser: {.spec.securityContext.runAsUser}
  fsGroup: {.spec.securityContext.fsGroup}
  Container securityContext: {.spec.containers[*].securityContext}
{end}'
```

**Expected Output:**
- `runAsNonRoot: true` (preferred)
- No `privileged: true`
- No `hostNetwork`, `hostPID`, or `hostIPC`

### Step 3: Verify CockroachDB Pod Security Context

```bash
# Get CockroachDB pod security contexts
kubectl get pods -n ${CRDB_CLUSTER_NS} -o yaml | grep -A 30 "securityContext"

# Detailed security context check
for pod in $(kubectl get pods -n ${CRDB_CLUSTER_NS} -o name); do
  echo "=== ${pod} ==="
  kubectl get ${pod} -n ${CRDB_CLUSTER_NS} -o jsonpath='
  runAsNonRoot: {.spec.securityContext.runAsNonRoot}
  runAsUser: {.spec.securityContext.runAsUser}
  fsGroup: {.spec.securityContext.fsGroup}
  privileged: {.spec.containers[*].securityContext.privileged}
  allowPrivilegeEscalation: {.spec.containers[*].securityContext.allowPrivilegeEscalation}
  capabilities: {.spec.containers[*].securityContext.capabilities}
'
  echo ""
done
```

**Expected Output:**
- No privileged containers
- `allowPrivilegeEscalation: false` (preferred)
- No dangerous capabilities added

### Step 4: Check for Restricted Resources

```bash
# Check for hostPath volumes
kubectl get pods -n ${CRDB_CLUSTER_NS} -o jsonpath='{range .items[*].spec.volumes[*]}{.name}: {.hostPath.path}{"\n"}{end}' | grep -v "^:"

# Check for hostNetwork
kubectl get pods -n ${CRDB_CLUSTER_NS} -o jsonpath='{range .items[*]}{.metadata.name}: hostNetwork={.spec.hostNetwork}{"\n"}{end}'

# Check for hostPID
kubectl get pods -n ${CRDB_CLUSTER_NS} -o jsonpath='{range .items[*]}{.metadata.name}: hostPID={.spec.hostPID}{"\n"}{end}'

# Check for hostIPC
kubectl get pods -n ${CRDB_CLUSTER_NS} -o jsonpath='{range .items[*]}{.metadata.name}: hostIPC={.spec.hostIPC}{"\n"}{end}'
```

**Expected Output:**
- No hostPath volumes
- `hostNetwork=`, `hostPID=`, `hostIPC=` all empty or false

### Step 5: Verify No Privileged Containers

```bash
# Check all containers for privileged flag
kubectl get pods -n ${CRDB_CLUSTER_NS} -o jsonpath='{range .items[*].spec.containers[*]}{.name}: privileged={.securityContext.privileged}{"\n"}{end}'

kubectl get pods -n ${CRDB_OPERATOR_NS} -o jsonpath='{range .items[*].spec.containers[*]}{.name}: privileged={.securityContext.privileged}{"\n"}{end}'
```

**Expected Output:**
- All containers show `privileged=` (empty/not set) or `privileged=false`

### Step 6: Test Pod Restart Under PSA

```bash
# Delete a CockroachDB pod to test re-admission
kubectl delete pod cockroachdb-0 -n ${CRDB_CLUSTER_NS}

# Watch pod recreation
kubectl get pods -n ${CRDB_CLUSTER_NS} -w &
WATCH_PID=$!
sleep 60
kill $WATCH_PID 2>/dev/null

# Verify pod is running
kubectl wait --for=condition=Ready pod/cockroachdb-0 -n ${CRDB_CLUSTER_NS} --timeout=300s

# Check for PSA admission warnings/errors
kubectl get events -n ${CRDB_CLUSTER_NS} --sort-by='.lastTimestamp' | grep -i -E "(security|admission|denied|forbidden)" || echo "No PSA-related events"
```

**Expected Output:**
- Pod recreates successfully
- No admission denial events
- Pod reaches Ready state

### Step 7: Test Operator Pod Restart

```bash
# Delete operator pod
kubectl delete pod -n ${CRDB_OPERATOR_NS} -l app.kubernetes.io/name=cockroach-operator

# Wait for recreation
kubectl wait --for=condition=Ready pods -n ${CRDB_OPERATOR_NS} -l app.kubernetes.io/name=cockroach-operator --timeout=120s

# Check for admission issues
kubectl get events -n ${CRDB_OPERATOR_NS} --sort-by='.lastTimestamp' | grep -i -E "(security|admission|denied|forbidden)" || echo "No PSA-related events"
```

**Expected Output:**
- Operator pod recreates successfully
- No admission denial events

### Step 8: Apply Restricted PSA Labels (Optional Test)

```bash
# Create a test namespace with restricted PSA
kubectl create namespace psa-test

# Apply restricted PSA labels
kubectl label namespace psa-test \
  pod-security.kubernetes.io/enforce=restricted \
  pod-security.kubernetes.io/warn=restricted \
  pod-security.kubernetes.io/audit=restricted

# Try to create a CockroachDB pod in restricted namespace (dry-run)
kubectl run crdb-psa-test \
  --namespace psa-test \
  --image=cockroachdb/cockroach:latest \
  --dry-run=server \
  --restart=Never \
  -- start-single-node --insecure 2>&1 || echo "Check if PSA restrictions apply"
```

**Expected Output:**
- May show warnings about security context requirements
- Documents what adjustments are needed for restricted mode

### Step 9: Document Security Exceptions (If Any)

```bash
# Check if any security exceptions are configured
kubectl get podsecuritypolicies 2>/dev/null || echo "PSP not enabled (expected in newer K8s)"

# Check for any admission webhooks
kubectl get validatingwebhookconfigurations | grep -i security
kubectl get mutatingwebhookconfigurations | grep -i security

# Document current security posture
echo "=== Security Posture Summary ===" > /tmp/security-summary.txt
echo "" >> /tmp/security-summary.txt

echo "Operator Pod Security:" >> /tmp/security-summary.txt
kubectl get pods -n ${CRDB_OPERATOR_NS} -o jsonpath='{.items[0].spec.securityContext}' >> /tmp/security-summary.txt
echo "" >> /tmp/security-summary.txt

echo "CockroachDB Pod Security:" >> /tmp/security-summary.txt
kubectl get pods -n ${CRDB_CLUSTER_NS} -o jsonpath='{.items[0].spec.securityContext}' >> /tmp/security-summary.txt

cat /tmp/security-summary.txt
```

## Validation Commands

```bash
# Complete validation script
echo "=== Pod Security Admission Validation ==="

echo -e "\n1. Operator pods running (no PSA issues):"
kubectl get pods -n ${CRDB_OPERATOR_NS}

echo -e "\n2. CockroachDB pods running (no PSA issues):"
kubectl get pods -n ${CRDB_CLUSTER_NS}

echo -e "\n3. No privileged containers:"
echo "Operator:"
kubectl get pods -n ${CRDB_OPERATOR_NS} -o jsonpath='{range .items[*].spec.containers[*]}  {.name}: privileged={.securityContext.privileged}{"\n"}{end}'
echo "CockroachDB:"
kubectl get pods -n ${CRDB_CLUSTER_NS} -o jsonpath='{range .items[*].spec.containers[*]}  {.name}: privileged={.securityContext.privileged}{"\n"}{end}'

echo -e "\n4. No hostPath/hostNetwork/hostPID:"
kubectl get pods -n ${CRDB_CLUSTER_NS} -o jsonpath='{range .items[*]}{.metadata.name}: hostNetwork={.spec.hostNetwork}, hostPID={.spec.hostPID}{"\n"}{end}'

echo -e "\n5. No PSA denial events:"
kubectl get events -n ${CRDB_CLUSTER_NS} --field-selector reason=FailedCreate 2>/dev/null | grep -i security || echo "None found"

echo -e "\n=== Validation Complete ==="
```

## Expected Results

| Check | Expected State |
|-------|----------------|
| Operator pods | Running without PSA violations |
| CockroachDB pods | Running without PSA violations |
| Privileged containers | None |
| hostPath volumes | None |
| hostNetwork/hostPID/hostIPC | All false or not set |
| Pod restarts | Successful re-admission |
| PSA events | No denial or forbidden events |

## Cleanup

```bash
# Remove test namespace if created
kubectl delete namespace psa-test 2>/dev/null

# Remove temporary files
rm -f /tmp/security-summary.txt
```

## Notes

- VKS enforces Pod Security Admission by default
- The goal is to run with a "restricted" style posture where possible
- Document any required exceptions as platform-specific requirements
- CockroachDB typically requires `fsGroup` for volume permissions
- Some charts may need `runAsUser` and `runAsGroup` settings

## Troubleshooting

### Pod Stuck Pending Due to PSA

```bash
# Check events for admission errors
kubectl get events -n ${CRDB_CLUSTER_NS} --sort-by='.lastTimestamp' | tail -20

# Check pod description for warnings
kubectl describe pod -n ${CRDB_CLUSTER_NS} cockroachdb-0 | grep -A 5 -i warning
```

### Security Context Issues

```bash
# Check what security context is being applied
kubectl get statefulset -n ${CRDB_CLUSTER_NS} cockroachdb -o yaml | grep -A 20 "securityContext"

# Check Helm values for security settings
helm get values cockroachdb -n ${CRDB_CLUSTER_NS} | grep -A 10 -i security
```

### Modifying Security Context

```bash
# If needed, upgrade with explicit security context
helm upgrade cockroachdb ${CRDB_CHART_PATH} \
  --namespace ${CRDB_CLUSTER_NS} \
  --reuse-values \
  --set "securityContext.runAsNonRoot=true" \
  --set "securityContext.runAsUser=1000" \
  --set "securityContext.fsGroup=1000"
```
