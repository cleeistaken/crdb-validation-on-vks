# VKS-29: End-to-End WAL Failover Validation

## Test Information

| Field | Value |
|-------|-------|
| **Test ID** | VKS-29 |
| **Category** | Failure Handling |
| **Dependencies** | [VKS-02](../01-cluster-provisioning/VKS-02-cluster-install.md) |

## Objective

Configure WAL (Write-Ahead Log) failover with a secondary disk, verify that when the main store is stalled, CockroachDB switches WAL writes to the failover disk, and confirm the cluster stays available.

## Pre-requisites

- VKS-02 completed (CockroachDB cluster running)
- Storage class that supports dynamic PVCs
- Operator supports `walFailoverSpec` configuration
- `kubectl` configured with VKS cluster kubeconfig

## Environment Setup

```bash
# Ensure KUBECONFIG points to VKS cluster
export KUBECONFIG=$(pwd)/vks-kubeconfig.yaml

# Set environment variables
export CRDB_CLUSTER_NS="crdb-cluster"
export CRDB_RELEASE_NAME="cockroachdb"
export CRDB_CHART_PATH="./cockroachdb-parent/charts/cockroachdb"
export STORAGE_CLASS="vsan-esa-default-policy-raid5"

# Verify cluster is running
kubectl get pods -n ${CRDB_CLUSTER_NS}
```

## Steps

### Step 1: Check Current Storage Configuration

```bash
# Check current PVCs
kubectl get pvc -n ${CRDB_CLUSTER_NS}

# Check storage class
kubectl get storageclass ${STORAGE_CLASS}

# Check current volume mounts
kubectl get pods -n ${CRDB_CLUSTER_NS} cockroachdb-0 -o jsonpath='{.spec.containers[0].volumeMounts}' | jq .
```

### Step 2: Create WAL Failover Configuration

```bash
# Create Helm values for WAL failover
cat > /tmp/wal-failover-values.yaml << EOF
# WAL Failover configuration
statefulset:
  # Add additional volume for WAL failover
  extraVolumes:
  - name: wal-failover
    persistentVolumeClaim:
      claimName: ""  # Will be templated
  
  extraVolumeMounts:
  - name: wal-failover
    mountPath: /cockroach/wal_failover

  # Volume claim templates for WAL failover
  volumeClaimTemplates:
  - metadata:
      name: wal-failover
    spec:
      accessModes: ["ReadWriteOnce"]
      storageClassName: "${STORAGE_CLASS}"
      resources:
        requests:
          storage: 10Gi

# CockroachDB configuration for WAL failover
conf:
  # Enable WAL failover (syntax depends on CockroachDB version)
  # wal-failover: "path=/cockroach/wal_failover,mode=enabled"
  attrs: "wal-failover"
EOF

cat /tmp/wal-failover-values.yaml
```

### Step 3: Upgrade Cluster with WAL Failover

```bash
# Note: WAL failover configuration depends on CockroachDB and operator version
# This is a conceptual example

helm upgrade ${CRDB_RELEASE_NAME} ${CRDB_CHART_PATH} \
  --namespace ${CRDB_CLUSTER_NS} \
  --reuse-values \
  -f /tmp/wal-failover-values.yaml \
  --timeout 15m

# Wait for pods to be ready
kubectl wait --for=condition=Ready pods --all -n ${CRDB_CLUSTER_NS} --timeout=600s
```

### Step 4: Verify WAL Failover PVCs

```bash
# Check PVCs (should see additional WAL failover PVCs)
kubectl get pvc -n ${CRDB_CLUSTER_NS}

# Verify WAL failover volume is mounted
kubectl exec -n ${CRDB_CLUSTER_NS} cockroachdb-0 -- \
  ls -la /cockroach/wal_failover 2>/dev/null || echo "WAL failover path not found - check configuration"

# Check mount points
kubectl exec -n ${CRDB_CLUSTER_NS} cockroachdb-0 -- \
  df -h | grep cockroach
```

### Step 5: Create Test Workload

```bash
# Create test database with write-heavy workload
kubectl exec -n ${CRDB_CLUSTER_NS} cockroachdb-0 -- \
  cockroach sql --insecure --host=localhost:26257 \
  --execute="
    CREATE DATABASE IF NOT EXISTS wal_test;
    USE wal_test;
    CREATE TABLE IF NOT EXISTS writes (
      id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
      data STRING,
      created_at TIMESTAMP DEFAULT now()
    );
  "

# Start write-heavy workload
kubectl exec -n ${CRDB_CLUSTER_NS} cockroachdb-0 -- \
  cockroach workload run kv \
  --init \
  --duration=300s \
  --concurrency=8 \
  --max-rate=100 \
  'postgresql://root@localhost:26257/wal_test?sslmode=disable' &
WORKLOAD_PID=$!
echo "Workload started"
```

### Step 6: Monitor WAL Metrics

```bash
# Check WAL-related metrics
kubectl exec -n ${CRDB_CLUSTER_NS} cockroachdb-0 -- \
  curl -s http://localhost:8080/_status/vars | grep -i wal | head -20

# Check for failover-specific metrics
kubectl exec -n ${CRDB_CLUSTER_NS} cockroachdb-0 -- \
  curl -s http://localhost:8080/_status/vars | grep -E "storage.wal.failover" || echo "WAL failover metrics not available"
```

### Step 7: Simulate Main Store Stall (Carefully)

```bash
# WARNING: This step can cause data issues if not done carefully
# Only perform in test environments

echo "=== Simulating Store Stall ==="
echo "This step requires careful execution to avoid data loss"
echo ""
echo "Option 1: Use debug tools (if available)"
echo "Option 2: Simulate I/O pressure"
echo ""

# Option 2: Create I/O pressure (less risky)
kubectl exec -n ${CRDB_CLUSTER_NS} cockroachdb-0 -- \
  /bin/bash -c "
    # Create temporary I/O load on main store
    dd if=/dev/zero of=/cockroach/cockroach-data/io_test bs=1M count=100 oflag=direct 2>/dev/null &
    DD_PID=\$!
    sleep 10
    kill \$DD_PID 2>/dev/null
    rm -f /cockroach/cockroach-data/io_test
  " &

# Monitor during stall simulation
sleep 5
```

### Step 8: Verify WAL Failover Activation

```bash
# Check WAL failover metrics
kubectl exec -n ${CRDB_CLUSTER_NS} cockroachdb-0 -- \
  curl -s http://localhost:8080/_status/vars | grep -E "storage.wal.failover" || echo "Check Admin UI for WAL metrics"

# Check for failover events in logs
kubectl logs -n ${CRDB_CLUSTER_NS} cockroachdb-0 --tail=100 | grep -i -E "(wal|failover)" || echo "No WAL failover events in logs"

# Check cluster health during potential failover
kubectl exec -n ${CRDB_CLUSTER_NS} cockroachdb-0 -- \
  cockroach sql --insecure --host=localhost:26257 \
  --execute="SELECT count(*) as live FROM crdb_internal.gossip_nodes WHERE is_live;"
```

### Step 9: Verify Cluster Availability

```bash
# Check workload is still running
kubectl exec -n ${CRDB_CLUSTER_NS} cockroachdb-0 -- \
  cockroach sql --insecure --host=localhost:26257 \
  --execute="SELECT count(*) as writes FROM wal_test.writes;" 2>/dev/null || echo "Query failed"

# Check all nodes are healthy
kubectl exec -n ${CRDB_CLUSTER_NS} cockroachdb-0 -- \
  cockroach node status --insecure --host=localhost:26257

# Wait for workload to complete
wait $WORKLOAD_PID 2>/dev/null
```

### Step 10: Verify Data Integrity

```bash
# Check data after test
kubectl exec -n ${CRDB_CLUSTER_NS} cockroachdb-0 -- \
  cockroach sql --insecure --host=localhost:26257 \
  --execute="
    SELECT count(*) as total_writes FROM wal_test.writes;
    SELECT 
      'Under-replicated' as check,
      count(*) as count
    FROM crdb_internal.ranges 
    WHERE array_length(replicas, 1) < 3;
  "
```

## Validation Commands

```bash
# Complete validation script
echo "=== WAL Failover Validation ==="

echo -e "\n1. PVCs (including WAL failover):"
kubectl get pvc -n ${CRDB_CLUSTER_NS}

echo -e "\n2. Volume mounts:"
kubectl exec -n ${CRDB_CLUSTER_NS} cockroachdb-0 -- df -h | grep cockroach

echo -e "\n3. Cluster health:"
kubectl exec -n ${CRDB_CLUSTER_NS} cockroachdb-0 -- \
  cockroach node status --insecure --host=localhost:26257

echo -e "\n4. WAL metrics (if available):"
kubectl exec -n ${CRDB_CLUSTER_NS} cockroachdb-0 -- \
  curl -s http://localhost:8080/_status/vars | grep -E "storage.wal" | head -5 || echo "Check Admin UI"

echo -e "\n5. Data integrity:"
kubectl exec -n ${CRDB_CLUSTER_NS} cockroachdb-0 -- \
  cockroach sql --insecure --host=localhost:26257 \
  --execute="SELECT count(*) FROM crdb_internal.ranges WHERE array_length(replicas, 1) < 3;"

echo -e "\n=== Validation Complete ==="
```

## Expected Results

| Check | Expected State |
|-------|----------------|
| WAL failover PVC | Created and bound |
| WAL failover mount | Present at /cockroach/wal_failover |
| During stall | WAL switches to failover disk |
| Cluster availability | Maintained |
| Data integrity | No data loss |
| WAL metrics | Show failover activity (if triggered) |

## Cleanup

```bash
# Remove test database
kubectl exec -n ${CRDB_CLUSTER_NS} cockroachdb-0 -- \
  cockroach sql --insecure --host=localhost:26257 \
  --execute="DROP DATABASE IF EXISTS wal_test CASCADE;"

# Remove temporary files
rm -f /tmp/wal-failover-values.yaml

# Note: Removing WAL failover configuration requires careful planning
# Do not remove in production without proper migration
```

## Notes

- WAL failover is an advanced feature for high availability
- Configuration syntax varies by CockroachDB version
- Adapted from GKE WAL-FAILOVER runbook
- Same operator walFailoverSpec semantics apply on VKS storage
- Monitor WAL failover metrics in production

## Troubleshooting

### WAL Failover PVC Not Created

```bash
# Check StatefulSet volume claim templates
kubectl get statefulset -n ${CRDB_CLUSTER_NS} cockroachdb -o yaml | grep -A 20 volumeClaimTemplates

# Check storage class
kubectl get storageclass ${STORAGE_CLASS}

# Check PVC events
kubectl get events -n ${CRDB_CLUSTER_NS} | grep -i pvc
```

### WAL Failover Not Activating

```bash
# Check CockroachDB configuration
kubectl exec -n ${CRDB_CLUSTER_NS} cockroachdb-0 -- \
  ps aux | grep cockroach

# Check for WAL failover flags
kubectl exec -n ${CRDB_CLUSTER_NS} cockroachdb-0 -- \
  cat /cockroach/cockroach-data/COCKROACHDB_VERSION 2>/dev/null

# Check logs for WAL-related messages
kubectl logs -n ${CRDB_CLUSTER_NS} cockroachdb-0 | grep -i wal
```

### Cluster Unavailable During Test

```bash
# Check pod status
kubectl get pods -n ${CRDB_CLUSTER_NS}

# Check for OOM or resource issues
kubectl describe pod -n ${CRDB_CLUSTER_NS} cockroachdb-0 | grep -A 10 "State:"

# Check node resources
kubectl top pods -n ${CRDB_CLUSTER_NS}
```
