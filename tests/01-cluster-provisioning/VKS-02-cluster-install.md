# VKS-02: Install CockroachDB Cluster via Helm

## Test Information

| Field | Value |
|-------|-------|
| **Test ID** | VKS-02 |
| **Category** | Cluster Provisioning |
| **Dependencies** | [VKS-01](VKS-01-operator-install.md) |

## Objective

Deploy a CockroachDB cluster using the Helm chart with the operator managing the cluster lifecycle. Validate that all pods become Ready and SQL/HTTP services are reachable.

## Pre-requisites

- VKS-01 completed (CockroachDB Operator installed)
- `kubectl` configured with VKS cluster kubeconfig
- `helm` CLI installed (v3.x)
- CockroachDB Helm chart accessible at `./cockroachdb-parent/charts/cockroachdb`

## Environment Setup

```bash
# Ensure KUBECONFIG points to VKS cluster
export KUBECONFIG=$(pwd)/vks-kubeconfig.yaml

# Set environment variables
export CRDB_CLUSTER_NS="crdb-cluster"
export CRDB_RELEASE_NAME="cockroachdb"
export CRDB_CHART_PATH="./cockroachdb-parent/charts/cockroachdb"
export STORAGE_CLASS="vsan-esa-default-policy-raid5"

# Verify operator is running
kubectl get pods -n crdb-operator
```

## Steps

### Step 1: Create Cluster Namespace

```bash
# Create the namespace for the CockroachDB cluster
kubectl create namespace ${CRDB_CLUSTER_NS}

# Verify namespace creation
kubectl get namespace ${CRDB_CLUSTER_NS}
```

**Expected Output:**
```
namespace/crdb-cluster created
```

### Step 2: Verify Helm Chart

```bash
# Check Helm chart exists and is valid
helm lint ${CRDB_CHART_PATH}

# Show chart information
helm show chart ${CRDB_CHART_PATH}

# Review default values
helm show values ${CRDB_CHART_PATH} | head -100
```

**Expected Output:**
- Lint passes with no errors
- Chart metadata displayed

### Step 3: Create Custom Values File (Optional)

```bash
# Create a values file for VKS-specific configuration
cat > /tmp/crdb-values.yaml << 'EOF'
# CockroachDB cluster configuration for VKS
conf:
  cluster-name: "crdb-vks-cluster"

statefulset:
  replicas: 3

storage:
  persistentVolume:
    enabled: true
    size: 50Gi
    storageClass: "vsan-esa-default-policy-raid5"

tls:
  enabled: true
EOF
```

### Step 4: Install CockroachDB Cluster

```bash
# Install the CockroachDB cluster using Helm
helm install ${CRDB_RELEASE_NAME} ${CRDB_CHART_PATH} \
  --namespace ${CRDB_CLUSTER_NS} \
  --set storage.persistentVolume.storageClass=${STORAGE_CLASS} \
  --wait \
  --timeout 10m

# Verify Helm release
helm list -n ${CRDB_CLUSTER_NS}
```

**Expected Output:**
```
NAME          NAMESPACE      REVISION  UPDATED                                 STATUS    CHART                  APP VERSION
cockroachdb   crdb-cluster   1         2024-XX-XX XX:XX:XX.XXXXXX +0000 UTC   deployed  cockroachdb-X.X.X      XX.X.X
```

### Step 5: Monitor Pod Deployment

```bash
# Watch pods come up
kubectl get pods -n ${CRDB_CLUSTER_NS} -w

# Wait for all pods to be ready (run after pods start appearing)
kubectl wait --for=condition=Ready pods --all -n ${CRDB_CLUSTER_NS} --timeout=600s
```

**Expected Output:**
```
NAME              READY   STATUS    RESTARTS   AGE
cockroachdb-0     1/1     Running   0          XXm
cockroachdb-1     1/1     Running   0          XXm
cockroachdb-2     1/1     Running   0          XXm
```

### Step 6: Verify Persistent Volume Claims

```bash
# Check PVCs
kubectl get pvc -n ${CRDB_CLUSTER_NS}

# Verify PVC binding
kubectl get pvc -n ${CRDB_CLUSTER_NS} -o jsonpath='{range .items[*]}{.metadata.name}{"\t"}{.status.phase}{"\t"}{.spec.storageClassName}{"\n"}{end}'
```

**Expected Output:**
```
datadir-cockroachdb-0   Bound   vsan-esa-default-policy-raid5
datadir-cockroachdb-1   Bound   vsan-esa-default-policy-raid5
datadir-cockroachdb-2   Bound   vsan-esa-default-policy-raid5
```

### Step 7: Verify Services

```bash
# List all services
kubectl get services -n ${CRDB_CLUSTER_NS}

# Check service endpoints
kubectl get endpoints -n ${CRDB_CLUSTER_NS}
```

**Expected Output:**
```
NAME                    TYPE        CLUSTER-IP      EXTERNAL-IP   PORT(S)              AGE
cockroachdb             ClusterIP   None            <none>        26257/TCP,8080/TCP   XXm
cockroachdb-public      ClusterIP   10.X.X.X        <none>        26257/TCP,8080/TCP   XXm
```

### Step 8: Verify Cluster Health via SQL

```bash
# Create a temporary client pod to test SQL connectivity
kubectl run crdb-client --rm -it \
  --namespace ${CRDB_CLUSTER_NS} \
  --image=cockroachdb/cockroach:latest \
  --restart=Never \
  -- sql --insecure --host=cockroachdb-public \
  --execute="SELECT node_id, address, is_live FROM crdb_internal.gossip_nodes;"
```

**Expected Output:**
```
  node_id |           address           | is_live
----------+-----------------------------+----------
        1 | cockroachdb-0.cockroachdb:26257 |  true
        2 | cockroachdb-1.cockroachdb:26257 |  true
        3 | cockroachdb-2.cockroachdb:26257 |  true
```

### Step 9: Verify HTTP Admin UI Access

```bash
# Port-forward to access Admin UI
kubectl port-forward svc/cockroachdb-public -n ${CRDB_CLUSTER_NS} 8080:8080 &
PF_PID=$!

# Wait for port-forward to establish
sleep 3

# Test HTTP endpoint
curl -s http://localhost:8080/_status/vars | head -20

# Check cluster health endpoint
curl -s http://localhost:8080/health

# Stop port-forward
kill $PF_PID 2>/dev/null
```

**Expected Output:**
- Metrics output from `/_status/vars`
- `{"status":"ok"}` or similar from `/health`

### Step 10: Verify Pod Distribution Across Nodes

```bash
# Check which nodes pods are scheduled on
kubectl get pods -n ${CRDB_CLUSTER_NS} -o wide

# Verify pods are on different worker nodes
kubectl get pods -n ${CRDB_CLUSTER_NS} -o jsonpath='{range .items[*]}{.metadata.name}{"\t"}{.spec.nodeName}{"\n"}{end}'
```

**Expected Output:**
- Each CockroachDB pod should be on a different worker node
- Pods distributed across node-pool-1, node-pool-2, node-pool-3

### Step 11: Run Basic SQL Operations

```bash
# Connect and run test operations
kubectl run crdb-test --rm -it \
  --namespace ${CRDB_CLUSTER_NS} \
  --image=cockroachdb/cockroach:latest \
  --restart=Never \
  -- sql --insecure --host=cockroachdb-public \
  --execute="
    CREATE DATABASE IF NOT EXISTS test_db;
    USE test_db;
    CREATE TABLE IF NOT EXISTS test_table (id INT PRIMARY KEY, name STRING);
    INSERT INTO test_table VALUES (1, 'test');
    SELECT * FROM test_table;
    DROP TABLE test_table;
    DROP DATABASE test_db;
  "
```

**Expected Output:**
```
CREATE DATABASE
SET
CREATE TABLE
INSERT 1
  id | name
-----+-------
   1 | test
DROP TABLE
DROP DATABASE
```

## Validation Commands

```bash
# Complete validation script
echo "=== CockroachDB Cluster Validation ==="

echo -e "\n1. Namespace exists:"
kubectl get namespace ${CRDB_CLUSTER_NS}

echo -e "\n2. Helm release deployed:"
helm status ${CRDB_RELEASE_NAME} -n ${CRDB_CLUSTER_NS} | head -10

echo -e "\n3. All pods running:"
kubectl get pods -n ${CRDB_CLUSTER_NS}

echo -e "\n4. PVCs bound:"
kubectl get pvc -n ${CRDB_CLUSTER_NS}

echo -e "\n5. Services available:"
kubectl get svc -n ${CRDB_CLUSTER_NS}

echo -e "\n6. Cluster node status:"
kubectl exec -n ${CRDB_CLUSTER_NS} cockroachdb-0 -- cockroach node status --insecure

echo -e "\n=== Validation Complete ==="
```

## Expected Results

| Check | Expected State |
|-------|----------------|
| Namespace | `crdb-cluster` exists |
| Helm release | `deployed` status |
| CockroachDB pods | 3 pods, all `Running` with `1/1` Ready |
| PVCs | 3 PVCs, all `Bound` |
| Services | `cockroachdb` and `cockroachdb-public` exist |
| SQL connectivity | Queries execute successfully |
| HTTP Admin UI | Accessible on port 8080 |
| Pod distribution | Pods spread across different worker nodes |

## Cleanup

**Note:** Only run cleanup if you need to remove the cluster. This will affect dependent tests.

```bash
# Uninstall CockroachDB cluster
helm uninstall ${CRDB_RELEASE_NAME} -n ${CRDB_CLUSTER_NS}

# Delete PVCs (data will be lost)
kubectl delete pvc --all -n ${CRDB_CLUSTER_NS}

# Delete namespace
kubectl delete namespace ${CRDB_CLUSTER_NS}
```

## Notes

- This test validates the default Helm chart + operator integration on VKS
- The cluster uses insecure mode by default; VKS-03 will enable TLS
- Keep the cluster running for subsequent tests
- Default configuration deploys 3 nodes for high availability

## Troubleshooting

### Pods stuck in Pending

```bash
# Check events
kubectl get events -n ${CRDB_CLUSTER_NS} --sort-by='.lastTimestamp'

# Check PVC status
kubectl describe pvc -n ${CRDB_CLUSTER_NS}

# Check storage class
kubectl get storageclass ${STORAGE_CLASS}
```

### Pods in CrashLoopBackOff

```bash
# Check pod logs
kubectl logs -n ${CRDB_CLUSTER_NS} cockroachdb-0 --previous

# Check pod description
kubectl describe pod -n ${CRDB_CLUSTER_NS} cockroachdb-0
```

### SQL Connection Fails

```bash
# Check service endpoints
kubectl get endpoints -n ${CRDB_CLUSTER_NS} cockroachdb-public

# Check pod networking
kubectl exec -n ${CRDB_CLUSTER_NS} cockroachdb-0 -- netstat -tlnp
```

### Cluster Not Initializing

```bash
# Check init job (if applicable)
kubectl get jobs -n ${CRDB_CLUSTER_NS}

# Check operator logs
kubectl logs -n crdb-operator -l app.kubernetes.io/name=cockroach-operator --tail=100
```
