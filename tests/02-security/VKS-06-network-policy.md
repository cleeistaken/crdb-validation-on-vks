# VKS-06: NetworkPolicy Compatibility

## Test Information

| Field | Value |
|-------|-------|
| **Test ID** | VKS-06 |
| **Category** | Security & Platform |
| **Dependencies** | [VKS-02](../01-cluster-provisioning/VKS-02-cluster-install.md) |

## Objective

Enable NetworkPolicy for the CockroachDB cluster, verify that intra-cluster communication functions correctly, and confirm that unauthorized pods cannot access SQL/HTTP ports.

## Pre-requisites

- VKS-02 completed (CockroachDB cluster installed)
- VKS cluster has NetworkPolicy enforcement enabled (typically via Antrea or Calico CNI)
- `kubectl` configured with VKS cluster kubeconfig
- `helm` CLI installed (v3.x)

## Environment Setup

```bash
# Ensure KUBECONFIG points to VKS cluster
export KUBECONFIG=$(pwd)/vks-kubeconfig.yaml

# Set environment variables
export CRDB_CLUSTER_NS="crdb-cluster"
export CRDB_RELEASE_NAME="cockroachdb"
export CRDB_CHART_PATH="./cockroachdb-parent/charts/cockroachdb"

# Verify cluster is running
kubectl get pods -n ${CRDB_CLUSTER_NS}
```

## Steps

### Step 1: Verify NetworkPolicy Support

```bash
# Check CNI plugin (VKS typically uses Antrea)
kubectl get pods -n kube-system | grep -E "(antrea|calico|cilium)"

# Check if NetworkPolicy CRD exists
kubectl api-resources | grep networkpolicies

# List existing NetworkPolicies
kubectl get networkpolicies --all-namespaces
```

**Expected Output:**
- CNI plugin pods running (antrea-agent, antrea-controller, or similar)
- NetworkPolicy resource available

### Step 2: Test Baseline Connectivity (Before NetworkPolicy)

```bash
# Create a test pod in a different namespace
kubectl create namespace netpol-test

kubectl run test-client -n netpol-test \
  --image=busybox:latest \
  --restart=Never \
  -- sleep 3600

# Wait for pod to be ready
kubectl wait --for=condition=Ready pod/test-client -n netpol-test --timeout=60s

# Test SQL port connectivity (should succeed before NetworkPolicy)
kubectl exec -n netpol-test test-client -- \
  nc -zv cockroachdb-public.${CRDB_CLUSTER_NS}.svc.cluster.local 26257 2>&1 || echo "Connection test complete"

# Test HTTP port connectivity (should succeed before NetworkPolicy)
kubectl exec -n netpol-test test-client -- \
  wget -qO- --timeout=5 http://cockroachdb-public.${CRDB_CLUSTER_NS}.svc.cluster.local:8080/health 2>&1 || echo "HTTP test complete"
```

**Expected Output:**
- Both connections should succeed (no NetworkPolicy yet)

### Step 3: Enable NetworkPolicy via Helm

```bash
# Upgrade cluster with NetworkPolicy enabled
helm upgrade ${CRDB_RELEASE_NAME} ${CRDB_CHART_PATH} \
  --namespace ${CRDB_CLUSTER_NS} \
  --reuse-values \
  --set networkPolicy.enabled=true \
  --wait \
  --timeout 5m
```

**Expected Output:**
```
Release "cockroachdb" has been upgraded.
```

### Step 4: Verify NetworkPolicy Created

```bash
# List NetworkPolicies in the cluster namespace
kubectl get networkpolicies -n ${CRDB_CLUSTER_NS}

# Describe the NetworkPolicy
kubectl describe networkpolicy -n ${CRDB_CLUSTER_NS}

# View NetworkPolicy YAML
kubectl get networkpolicy -n ${CRDB_CLUSTER_NS} -o yaml
```

**Expected Output:**
- NetworkPolicy resource(s) created
- Rules for ingress/egress defined

### Step 5: Verify Intra-Cluster Communication

```bash
# Test pod-to-pod communication within the cluster
kubectl exec -n ${CRDB_CLUSTER_NS} cockroachdb-0 -- \
  cockroach sql --insecure --host=cockroachdb-1.cockroachdb:26257 \
  --execute="SELECT 1;"

kubectl exec -n ${CRDB_CLUSTER_NS} cockroachdb-1 -- \
  cockroach sql --insecure --host=cockroachdb-2.cockroachdb:26257 \
  --execute="SELECT 1;"

kubectl exec -n ${CRDB_CLUSTER_NS} cockroachdb-2 -- \
  cockroach sql --insecure --host=cockroachdb-0.cockroachdb:26257 \
  --execute="SELECT 1;"
```

**Expected Output:**
- All inter-node SQL connections succeed
- Returns `1` for each query

### Step 6: Verify Cluster Health

```bash
# Check cluster status
kubectl exec -n ${CRDB_CLUSTER_NS} cockroachdb-0 -- \
  cockroach node status --insecure --host=localhost:26257

# Verify gossip connectivity
kubectl exec -n ${CRDB_CLUSTER_NS} cockroachdb-0 -- \
  cockroach sql --insecure --host=localhost:26257 \
  --execute="SELECT node_id, is_live FROM crdb_internal.gossip_nodes;"
```

**Expected Output:**
- All nodes show `is_live = true`
- Cluster fully operational

### Step 7: Test Unauthorized Access (Should Be Blocked)

```bash
# Attempt SQL connection from unauthorized namespace
kubectl exec -n netpol-test test-client -- \
  nc -zv -w 5 cockroachdb-public.${CRDB_CLUSTER_NS}.svc.cluster.local 26257 2>&1 && \
  echo "WARNING: Connection succeeded (should be blocked)" || \
  echo "SUCCESS: Connection blocked by NetworkPolicy"

# Attempt HTTP connection from unauthorized namespace
kubectl exec -n netpol-test test-client -- \
  wget -qO- --timeout=5 http://cockroachdb-public.${CRDB_CLUSTER_NS}.svc.cluster.local:8080/health 2>&1 && \
  echo "WARNING: HTTP succeeded (should be blocked)" || \
  echo "SUCCESS: HTTP blocked by NetworkPolicy"
```

**Expected Output:**
- Connections should timeout or be refused
- NetworkPolicy blocks unauthorized access

### Step 8: Test Authorized Access (Create Allowed Pod)

```bash
# Create a pod in the same namespace (should be allowed)
kubectl run authorized-client -n ${CRDB_CLUSTER_NS} \
  --image=cockroachdb/cockroach:latest \
  --restart=Never \
  -- sleep 3600

# Wait for pod
kubectl wait --for=condition=Ready pod/authorized-client -n ${CRDB_CLUSTER_NS} --timeout=60s

# Test SQL connection (should succeed)
kubectl exec -n ${CRDB_CLUSTER_NS} authorized-client -- \
  cockroach sql --insecure --host=cockroachdb-public \
  --execute="SELECT 'authorized access works';"
```

**Expected Output:**
```
        ?column?
------------------------
  authorized access works
```

### Step 9: Create Custom NetworkPolicy for External Access (Optional)

```bash
# If you need to allow specific external access, create a custom NetworkPolicy
cat <<EOF | kubectl apply -f -
apiVersion: networking.k8s.io/v1
kind: NetworkPolicy
metadata:
  name: allow-monitoring
  namespace: ${CRDB_CLUSTER_NS}
spec:
  podSelector:
    matchLabels:
      app.kubernetes.io/name: cockroachdb
  policyTypes:
  - Ingress
  ingress:
  - from:
    - namespaceSelector:
        matchLabels:
          name: monitoring
    ports:
    - protocol: TCP
      port: 8080
EOF

# Verify the policy
kubectl get networkpolicy allow-monitoring -n ${CRDB_CLUSTER_NS}
```

### Step 10: Verify NetworkPolicy Logs (If Available)

```bash
# Check for NetworkPolicy-related events
kubectl get events -n ${CRDB_CLUSTER_NS} --sort-by='.lastTimestamp' | grep -i network

# If using Antrea, check Antrea logs
kubectl logs -n kube-system -l app=antrea --tail=50 | grep -i -E "(policy|drop|reject)" || echo "No policy events in logs"
```

## Validation Commands

```bash
# Complete validation script
echo "=== NetworkPolicy Validation ==="

echo -e "\n1. NetworkPolicy exists:"
kubectl get networkpolicies -n ${CRDB_CLUSTER_NS}

echo -e "\n2. Intra-cluster communication works:"
kubectl exec -n ${CRDB_CLUSTER_NS} cockroachdb-0 -- \
  cockroach sql --insecure --host=cockroachdb-1.cockroachdb:26257 \
  --execute="SELECT 'inter-node OK';" 2>&1 | tail -3

echo -e "\n3. Cluster health:"
kubectl exec -n ${CRDB_CLUSTER_NS} cockroachdb-0 -- \
  cockroach sql --insecure --host=localhost:26257 \
  --execute="SELECT count(*) as live_nodes FROM crdb_internal.gossip_nodes WHERE is_live;"

echo -e "\n4. Unauthorized access blocked:"
kubectl exec -n netpol-test test-client -- \
  nc -zv -w 3 cockroachdb-public.${CRDB_CLUSTER_NS}.svc.cluster.local 26257 2>&1 && \
  echo "FAIL: Should be blocked" || echo "PASS: Blocked"

echo -e "\n5. Authorized access works:"
kubectl exec -n ${CRDB_CLUSTER_NS} authorized-client -- \
  cockroach sql --insecure --host=cockroachdb-public \
  --execute="SELECT 'authorized OK';" 2>&1 | tail -3

echo -e "\n=== Validation Complete ==="
```

## Expected Results

| Check | Expected State |
|-------|----------------|
| NetworkPolicy | Created in cluster namespace |
| Intra-cluster SQL | All nodes can communicate |
| Cluster health | All nodes `is_live = true` |
| Unauthorized access | Blocked (timeout/refused) |
| Authorized access | Succeeds from same namespace |

## Cleanup

```bash
# Remove test resources
kubectl delete pod authorized-client -n ${CRDB_CLUSTER_NS}
kubectl delete namespace netpol-test

# Remove custom NetworkPolicy if created
kubectl delete networkpolicy allow-monitoring -n ${CRDB_CLUSTER_NS} 2>/dev/null

# Optionally disable NetworkPolicy
# helm upgrade ${CRDB_RELEASE_NAME} ${CRDB_CHART_PATH} \
#   --namespace ${CRDB_CLUSTER_NS} \
#   --reuse-values \
#   --set networkPolicy.enabled=false
```

## Notes

- VKS uses Antrea CNI by default, which supports NetworkPolicy
- NetworkPolicy is namespace-scoped
- The Helm chart's NetworkPolicy templates define allowed traffic patterns
- For production, carefully review and customize NetworkPolicy rules
- Consider allowing access from monitoring and backup namespaces

## Troubleshooting

### NetworkPolicy Not Taking Effect

```bash
# Verify CNI supports NetworkPolicy
kubectl get pods -n kube-system | grep antrea

# Check if NetworkPolicy controller is running
kubectl logs -n kube-system -l component=antrea-controller --tail=20
```

### Intra-Cluster Communication Broken

```bash
# Check NetworkPolicy rules
kubectl get networkpolicy -n ${CRDB_CLUSTER_NS} -o yaml

# Verify pod labels match policy selectors
kubectl get pods -n ${CRDB_CLUSTER_NS} --show-labels

# Check Antrea NetworkPolicy status
kubectl get antreanetworkpolicies -A 2>/dev/null
```

### All Traffic Blocked

```bash
# Check if there's a deny-all policy
kubectl get networkpolicy -n ${CRDB_CLUSTER_NS} -o yaml | grep -A 10 "policyTypes"

# Temporarily disable NetworkPolicy to verify
helm upgrade ${CRDB_RELEASE_NAME} ${CRDB_CHART_PATH} \
  --namespace ${CRDB_CLUSTER_NS} \
  --reuse-values \
  --set networkPolicy.enabled=false
```
