# VKS-03: Secure Cluster Initialization (Certs and TLS)

## Test Information

| Field | Value |
|-------|-------|
| **Test ID** | VKS-03 |
| **Category** | Cluster Provisioning |
| **Dependencies** | [VKS-02](VKS-02-cluster-install.md) |

## Objective

Deploy or upgrade the CockroachDB cluster with TLS enabled, verify secure SQL connections using certificates, and confirm that database operations succeed over TLS.

## Pre-requisites

- VKS-02 completed (CockroachDB cluster installed)
- `kubectl` configured with VKS cluster kubeconfig
- `helm` CLI installed (v3.x)
- Understanding of CockroachDB TLS certificate requirements

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

### Step 1: Upgrade Cluster to Enable TLS

```bash
# Upgrade the cluster with TLS enabled
helm upgrade ${CRDB_RELEASE_NAME} ${CRDB_CHART_PATH} \
  --namespace ${CRDB_CLUSTER_NS} \
  --reuse-values \
  --set tls.enabled=true \
  --wait \
  --timeout 10m
```

**Expected Output:**
```
Release "cockroachdb" has been upgraded. Happy Helming!
```

### Step 2: Monitor Rolling Update

```bash
# Watch pods restart with TLS configuration
kubectl get pods -n ${CRDB_CLUSTER_NS} -w

# Wait for all pods to be ready
kubectl wait --for=condition=Ready pods --all -n ${CRDB_CLUSTER_NS} --timeout=600s
```

**Expected Output:**
- Pods restart one by one (rolling update)
- All pods reach `Running` state with `1/1` Ready

### Step 3: Verify TLS Secrets Created

```bash
# List secrets related to TLS
kubectl get secrets -n ${CRDB_CLUSTER_NS} | grep -E "(ca|node|client|tls)"

# Check CA certificate secret
kubectl get secret -n ${CRDB_CLUSTER_NS} cockroachdb-ca-secret -o yaml 2>/dev/null || \
kubectl get secret -n ${CRDB_CLUSTER_NS} -l app.kubernetes.io/name=cockroachdb | head -20
```

**Expected Output:**
- CA secret exists
- Node certificate secrets exist
- Client certificate secrets exist (if auto-generated)

### Step 4: Verify TLS Configuration in Pods

```bash
# Check that pods are running with TLS flags
kubectl exec -n ${CRDB_CLUSTER_NS} cockroachdb-0 -- ps aux | grep cockroach

# Verify certificate files exist in pod
kubectl exec -n ${CRDB_CLUSTER_NS} cockroachdb-0 -- ls -la /cockroach/cockroach-certs/
```

**Expected Output:**
- CockroachDB process running with `--certs-dir` flag
- Certificate files present: `ca.crt`, `node.crt`, `node.key`

### Step 5: Create Client Certificate Secret

```bash
# If client certificates are not auto-generated, create them
# First, extract CA certificate and key
kubectl exec -n ${CRDB_CLUSTER_NS} cockroachdb-0 -- cat /cockroach/cockroach-certs/ca.crt > /tmp/ca.crt

# Create a client pod with certificates mounted
cat <<EOF | kubectl apply -f -
apiVersion: v1
kind: Pod
metadata:
  name: crdb-client-secure
  namespace: ${CRDB_CLUSTER_NS}
spec:
  containers:
  - name: cockroach
    image: cockroachdb/cockroach:latest
    command: ["sleep", "3600"]
    volumeMounts:
    - name: certs
      mountPath: /cockroach/cockroach-certs
      readOnly: true
  volumes:
  - name: certs
    projected:
      sources:
      - secret:
          name: cockroachdb-client-secret
          items:
          - key: ca.crt
            path: ca.crt
          - key: tls.crt
            path: client.root.crt
          - key: tls.key
            path: client.root.key
  restartPolicy: Never
EOF

# Wait for client pod to be ready
kubectl wait --for=condition=Ready pod/crdb-client-secure -n ${CRDB_CLUSTER_NS} --timeout=120s
```

### Step 6: Test Secure SQL Connection

```bash
# Connect using TLS from the client pod
kubectl exec -n ${CRDB_CLUSTER_NS} crdb-client-secure -- \
  cockroach sql \
  --certs-dir=/cockroach/cockroach-certs \
  --host=cockroachdb-public \
  --execute="SELECT 1 AS test;"
```

**Expected Output:**
```
  test
--------
     1
```

### Step 7: Verify TLS Connection Details

```bash
# Check connection is using TLS
kubectl exec -n ${CRDB_CLUSTER_NS} crdb-client-secure -- \
  cockroach sql \
  --certs-dir=/cockroach/cockroach-certs \
  --host=cockroachdb-public \
  --execute="SHOW ssl;"
```

**Expected Output:**
```
  ssl
-------
  on
```

### Step 8: Run Database Operations Over TLS

```bash
# Execute database operations
kubectl exec -n ${CRDB_CLUSTER_NS} crdb-client-secure -- \
  cockroach sql \
  --certs-dir=/cockroach/cockroach-certs \
  --host=cockroachdb-public \
  --execute="
    CREATE DATABASE IF NOT EXISTS secure_test;
    USE secure_test;
    CREATE TABLE IF NOT EXISTS tls_test (
      id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
      data STRING,
      created_at TIMESTAMP DEFAULT now()
    );
    INSERT INTO tls_test (data) VALUES ('TLS connection verified');
    SELECT * FROM tls_test;
  "
```

**Expected Output:**
```
CREATE DATABASE
SET
CREATE TABLE
INSERT 1
                   id                  |          data           |         created_at
---------------------------------------+-------------------------+-----------------------------
  xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx | TLS connection verified | 2024-XX-XX XX:XX:XX.XXXXXX
```

### Step 9: Verify Insecure Connection is Rejected

```bash
# Attempt insecure connection (should fail or be rejected)
kubectl run crdb-insecure-test --rm -it \
  --namespace ${CRDB_CLUSTER_NS} \
  --image=cockroachdb/cockroach:latest \
  --restart=Never \
  -- sql --insecure --host=cockroachdb-public \
  --execute="SELECT 1;" 2>&1 || echo "Insecure connection correctly rejected"
```

**Expected Output:**
- Connection should fail or be rejected when TLS is enforced

### Step 10: Verify Inter-Node TLS Communication

```bash
# Check node status (uses inter-node TLS)
kubectl exec -n ${CRDB_CLUSTER_NS} cockroachdb-0 -- \
  cockroach node status \
  --certs-dir=/cockroach/cockroach-certs \
  --host=localhost:26257

# Verify all nodes are communicating securely
kubectl exec -n ${CRDB_CLUSTER_NS} cockroachdb-0 -- \
  cockroach sql \
  --certs-dir=/cockroach/cockroach-certs \
  --host=localhost:26257 \
  --execute="SELECT node_id, address, is_live FROM crdb_internal.gossip_nodes;"
```

**Expected Output:**
- All nodes listed with `is_live = true`
- No TLS handshake errors in logs

### Step 11: Verify Admin UI Over HTTPS (Optional)

```bash
# Port-forward to Admin UI
kubectl port-forward svc/cockroachdb-public -n ${CRDB_CLUSTER_NS} 8080:8080 &
PF_PID=$!
sleep 3

# Test HTTPS endpoint (may need to use --insecure for self-signed certs)
curl -k https://localhost:8080/health 2>/dev/null || \
curl http://localhost:8080/health

# Stop port-forward
kill $PF_PID 2>/dev/null
```

## Validation Commands

```bash
# Complete validation script
echo "=== TLS Configuration Validation ==="

echo -e "\n1. TLS secrets exist:"
kubectl get secrets -n ${CRDB_CLUSTER_NS} | grep -E "(ca|node|client|tls)"

echo -e "\n2. Pods running with TLS:"
kubectl exec -n ${CRDB_CLUSTER_NS} cockroachdb-0 -- ls /cockroach/cockroach-certs/

echo -e "\n3. Secure SQL connection test:"
kubectl exec -n ${CRDB_CLUSTER_NS} crdb-client-secure -- \
  cockroach sql \
  --certs-dir=/cockroach/cockroach-certs \
  --host=cockroachdb-public \
  --execute="SHOW ssl;"

echo -e "\n4. Cluster health over TLS:"
kubectl exec -n ${CRDB_CLUSTER_NS} cockroachdb-0 -- \
  cockroach node status \
  --certs-dir=/cockroach/cockroach-certs \
  --host=localhost:26257

echo -e "\n=== Validation Complete ==="
```

## Expected Results

| Check | Expected State |
|-------|----------------|
| TLS secrets | CA, node, and client secrets exist |
| Certificate files | Present in `/cockroach/cockroach-certs/` |
| SSL status | `on` when queried via SQL |
| Secure SQL | Queries execute successfully with certs |
| Inter-node TLS | All nodes communicating securely |
| Insecure connection | Rejected when TLS enforced |

## Cleanup

```bash
# Remove test client pod
kubectl delete pod crdb-client-secure -n ${CRDB_CLUSTER_NS}

# Clean up test database
kubectl exec -n ${CRDB_CLUSTER_NS} cockroachdb-0 -- \
  cockroach sql \
  --certs-dir=/cockroach/cockroach-certs \
  --host=localhost:26257 \
  --execute="DROP DATABASE IF EXISTS secure_test CASCADE;"
```

## Notes

- VKS does not use HTTP Ingress for SQL traffic; TCP LoadBalancer is the recommended pattern
- TLS certificates may be auto-generated by the operator or manually provisioned
- For production, use certificates signed by a trusted CA
- Client certificates are required for secure root user access

## Troubleshooting

### TLS Handshake Errors

```bash
# Check pod logs for TLS errors
kubectl logs -n ${CRDB_CLUSTER_NS} cockroachdb-0 | grep -i tls

# Verify certificate validity
kubectl exec -n ${CRDB_CLUSTER_NS} cockroachdb-0 -- \
  openssl x509 -in /cockroach/cockroach-certs/node.crt -text -noout | head -20
```

### Certificate Not Found

```bash
# List all secrets
kubectl get secrets -n ${CRDB_CLUSTER_NS}

# Check Helm values for TLS configuration
helm get values ${CRDB_RELEASE_NAME} -n ${CRDB_CLUSTER_NS}
```

### Connection Refused

```bash
# Verify service is exposing correct port
kubectl get svc cockroachdb-public -n ${CRDB_CLUSTER_NS} -o yaml

# Check pod is listening on 26257
kubectl exec -n ${CRDB_CLUSTER_NS} cockroachdb-0 -- netstat -tlnp | grep 26257
```
