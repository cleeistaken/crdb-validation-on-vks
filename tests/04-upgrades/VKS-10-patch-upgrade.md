# VKS-10: Patch Upgrade of CockroachDB Version

## Test Information

| Field | Value |
|-------|-------|
| **Test ID** | VKS-10 |
| **Category** | Upgrades |
| **Dependencies** | [VKS-02](../01-cluster-provisioning/VKS-02-cluster-install.md) |

## Objective

Perform a patch version upgrade of CockroachDB (e.g., v23.1.x to v23.1.y), verify rolling update behavior, ensure cluster remains available during upgrade, and confirm all pods end on the new version.

## Pre-requisites

- VKS-02 completed (CockroachDB cluster running)
- Newer patch version available for current major version
- Workload generator available (optional but recommended)
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

# Get current version
CURRENT_VERSION=$(kubectl exec -n ${CRDB_CLUSTER_NS} cockroachdb-0 -- \
  cockroach version --build-tag 2>/dev/null || \
  kubectl exec -n ${CRDB_CLUSTER_NS} cockroachdb-0 -- \
  cockroach sql --insecure --host=localhost:26257 \
  --execute="SELECT value FROM crdb_internal.node_build_info WHERE field='Version';" -f csv | tail -1)
echo "Current version: ${CURRENT_VERSION}"

# Set target version (update this to your target patch version)
export TARGET_VERSION="v23.2.5"  # Example - update as needed
echo "Target version: ${TARGET_VERSION}"
```

## Steps

### Step 1: Verify Current Cluster State

```bash
# Check current image version
kubectl get statefulset -n ${CRDB_CLUSTER_NS} cockroachdb -o jsonpath='{.spec.template.spec.containers[0].image}'
echo ""

# Verify all pods are running same version
kubectl exec -n ${CRDB_CLUSTER_NS} cockroachdb-0 -- cockroach version
kubectl exec -n ${CRDB_CLUSTER_NS} cockroachdb-1 -- cockroach version
kubectl exec -n ${CRDB_CLUSTER_NS} cockroachdb-2 -- cockroach version

# Check cluster health
kubectl exec -n ${CRDB_CLUSTER_NS} cockroachdb-0 -- \
  cockroach node status --insecure --host=localhost:26257
```

### Step 2: Create Test Workload

```bash
# Create a database and table for testing during upgrade
kubectl exec -n ${CRDB_CLUSTER_NS} cockroachdb-0 -- \
  cockroach sql --insecure --host=localhost:26257 \
  --execute="
    CREATE DATABASE IF NOT EXISTS upgrade_test;
    USE upgrade_test;
    CREATE TABLE IF NOT EXISTS continuous_writes (
      id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
      value INT,
      written_at TIMESTAMP DEFAULT now()
    );
  "
```

### Step 3: Start Background Workload (Optional)

```bash
# Start a continuous write workload in the background
kubectl run workload-generator -n ${CRDB_CLUSTER_NS} \
  --image=cockroachdb/cockroach:latest \
  --restart=Never \
  -- workload run kv \
  --init \
  --duration=30m \
  --concurrency=2 \
  --max-rate=10 \
  'postgresql://root@cockroachdb-public:26257/upgrade_test?sslmode=disable' &

# Alternatively, run a simple insert loop
cat <<'EOF' | kubectl apply -f -
apiVersion: v1
kind: Pod
metadata:
  name: write-workload
  namespace: crdb-cluster
spec:
  containers:
  - name: workload
    image: cockroachdb/cockroach:latest
    command:
    - /bin/bash
    - -c
    - |
      while true; do
        cockroach sql --insecure --host=cockroachdb-public \
          --execute="INSERT INTO upgrade_test.continuous_writes (value) VALUES ($(date +%s));" 2>/dev/null
        sleep 1
      done
  restartPolicy: Never
EOF

# Wait for workload pod
kubectl wait --for=condition=Ready pod/write-workload -n ${CRDB_CLUSTER_NS} --timeout=60s
```

### Step 4: Record Pre-Upgrade Metrics

```bash
# Record current state
kubectl exec -n ${CRDB_CLUSTER_NS} cockroachdb-0 -- \
  cockroach sql --insecure --host=localhost:26257 \
  --execute="
    SELECT 
      node_id,
      (SELECT value FROM crdb_internal.node_build_info WHERE field='Tag') as version,
      is_live
    FROM crdb_internal.gossip_nodes
    ORDER BY node_id;
  " > /tmp/pre-upgrade-state.txt

cat /tmp/pre-upgrade-state.txt

# Record row count
kubectl exec -n ${CRDB_CLUSTER_NS} cockroachdb-0 -- \
  cockroach sql --insecure --host=localhost:26257 \
  --execute="SELECT count(*) as pre_upgrade_rows FROM upgrade_test.continuous_writes;"
```

### Step 5: Perform Patch Upgrade

```bash
# Update the CockroachDB image version via Helm
helm upgrade ${CRDB_RELEASE_NAME} ${CRDB_CHART_PATH} \
  --namespace ${CRDB_CLUSTER_NS} \
  --reuse-values \
  --set image.tag=${TARGET_VERSION} \
  --timeout 20m
```

**Note:** Do not use `--wait` to allow monitoring during the rolling update.

### Step 6: Monitor Rolling Update

```bash
# Watch pods restart one by one
kubectl get pods -n ${CRDB_CLUSTER_NS} -w &
WATCH_PID=$!

# Monitor in a loop
for i in {1..60}; do
  echo "=== Check $i ($(date)) ==="
  
  # Check pod status
  kubectl get pods -n ${CRDB_CLUSTER_NS} -o wide
  
  # Check versions across nodes
  kubectl exec -n ${CRDB_CLUSTER_NS} cockroachdb-0 -- \
    cockroach sql --insecure --host=localhost:26257 \
    --execute="SELECT node_id, (SELECT value FROM crdb_internal.node_build_info WHERE field='Tag') as version FROM crdb_internal.gossip_nodes ORDER BY node_id;" 2>/dev/null || true
  
  # Check if all pods are on new version
  NEW_VERSION_COUNT=$(kubectl get pods -n ${CRDB_CLUSTER_NS} -o jsonpath='{.items[*].spec.containers[0].image}' | tr ' ' '\n' | grep -c "${TARGET_VERSION}" || echo "0")
  TOTAL_PODS=$(kubectl get pods -n ${CRDB_CLUSTER_NS} --no-headers | wc -l)
  
  echo "Pods on new version: ${NEW_VERSION_COUNT}/${TOTAL_PODS}"
  
  if [ "${NEW_VERSION_COUNT}" -eq "${TOTAL_PODS}" ]; then
    echo "All pods upgraded!"
    break
  fi
  
  sleep 20
done

kill $WATCH_PID 2>/dev/null
```

### Step 7: Verify Cluster Availability During Upgrade

```bash
# Check if writes continued during upgrade
kubectl exec -n ${CRDB_CLUSTER_NS} cockroachdb-0 -- \
  cockroach sql --insecure --host=localhost:26257 \
  --execute="
    SELECT 
      count(*) as total_rows,
      min(written_at) as first_write,
      max(written_at) as last_write
    FROM upgrade_test.continuous_writes;
  "

# Check for any gaps in writes (should be minimal)
kubectl exec -n ${CRDB_CLUSTER_NS} cockroachdb-0 -- \
  cockroach sql --insecure --host=localhost:26257 \
  --execute="
    SELECT count(*) as writes_during_upgrade 
    FROM upgrade_test.continuous_writes 
    WHERE written_at > (SELECT min(written_at) FROM upgrade_test.continuous_writes);
  "
```

### Step 8: Verify All Pods on New Version

```bash
# Check StatefulSet image
kubectl get statefulset -n ${CRDB_CLUSTER_NS} cockroachdb -o jsonpath='{.spec.template.spec.containers[0].image}'
echo ""

# Verify each pod's version
for pod in cockroachdb-0 cockroachdb-1 cockroachdb-2; do
  echo "=== ${pod} ==="
  kubectl exec -n ${CRDB_CLUSTER_NS} ${pod} -- cockroach version --build-tag 2>/dev/null || \
  kubectl exec -n ${CRDB_CLUSTER_NS} ${pod} -- cockroach version
done

# Verify via SQL
kubectl exec -n ${CRDB_CLUSTER_NS} cockroachdb-0 -- \
  cockroach sql --insecure --host=localhost:26257 \
  --execute="
    SELECT 
      node_id,
      (SELECT value FROM crdb_internal.node_build_info WHERE field='Tag') as version
    FROM crdb_internal.gossip_nodes
    ORDER BY node_id;
  "
```

**Expected Output:**
- All pods show the target version

### Step 9: Verify Cluster Health Post-Upgrade

```bash
# Check node status
kubectl exec -n ${CRDB_CLUSTER_NS} cockroachdb-0 -- \
  cockroach node status --insecure --host=localhost:26257

# Check for any issues
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
```

### Step 10: Run Post-Upgrade Validation

```bash
# Run some SQL operations to verify functionality
kubectl exec -n ${CRDB_CLUSTER_NS} cockroachdb-0 -- \
  cockroach sql --insecure --host=localhost:26257 \
  --execute="
    USE upgrade_test;
    
    -- Test basic operations
    INSERT INTO continuous_writes (value) VALUES (999999);
    SELECT count(*) FROM continuous_writes WHERE value = 999999;
    
    -- Test schema changes
    ALTER TABLE continuous_writes ADD COLUMN IF NOT EXISTS post_upgrade BOOL DEFAULT true;
    
    -- Verify
    SELECT * FROM continuous_writes ORDER BY written_at DESC LIMIT 5;
  "
```

## Validation Commands

```bash
# Complete validation script
echo "=== Patch Upgrade Validation ==="

echo -e "\n1. All pods running:"
kubectl get pods -n ${CRDB_CLUSTER_NS}

echo -e "\n2. All pods on new version (${TARGET_VERSION}):"
kubectl get pods -n ${CRDB_CLUSTER_NS} -o jsonpath='{range .items[*]}{.metadata.name}: {.spec.containers[0].image}{"\n"}{end}'

echo -e "\n3. CockroachDB cluster version:"
kubectl exec -n ${CRDB_CLUSTER_NS} cockroachdb-0 -- \
  cockroach sql --insecure --host=localhost:26257 \
  --execute="SELECT node_id, (SELECT value FROM crdb_internal.node_build_info WHERE field='Version') as version FROM crdb_internal.gossip_nodes ORDER BY node_id;"

echo -e "\n4. Cluster health:"
kubectl exec -n ${CRDB_CLUSTER_NS} cockroachdb-0 -- \
  cockroach node status --insecure --host=localhost:26257

echo -e "\n5. Data integrity:"
kubectl exec -n ${CRDB_CLUSTER_NS} cockroachdb-0 -- \
  cockroach sql --insecure --host=localhost:26257 \
  --execute="SELECT count(*) as rows FROM upgrade_test.continuous_writes;"

echo -e "\n=== Validation Complete ==="
```

## Expected Results

| Check | Expected State |
|-------|----------------|
| Rolling update | Pods restart one-by-one |
| Cluster availability | Maintained during upgrade |
| All pods version | Target patch version |
| Node status | All nodes live |
| Data integrity | No data loss |
| Post-upgrade ops | SQL operations succeed |

## Cleanup

```bash
# Stop workload generator
kubectl delete pod write-workload -n ${CRDB_CLUSTER_NS} 2>/dev/null
kubectl delete pod workload-generator -n ${CRDB_CLUSTER_NS} 2>/dev/null

# Remove test database (optional - keep for VKS-13 rollback test)
# kubectl exec -n ${CRDB_CLUSTER_NS} cockroachdb-0 -- \
#   cockroach sql --insecure --host=localhost:26257 \
#   --execute="DROP DATABASE IF EXISTS upgrade_test CASCADE;"

# Remove temporary files
rm -f /tmp/pre-upgrade-state.txt
```

## Notes

- Patch upgrades are typically safe and don't require schema migrations
- The rolling update ensures at least 2 nodes are always available
- Keep the upgrade_test database for VKS-13 (Rollback) testing
- Monitor the Admin UI during upgrade for real-time status

## Troubleshooting

### Rolling Update Stuck

```bash
# Check StatefulSet rollout status
kubectl rollout status statefulset/cockroachdb -n ${CRDB_CLUSTER_NS}

# Check for pod issues
kubectl describe pod -n ${CRDB_CLUSTER_NS} cockroachdb-0

# Check events
kubectl get events -n ${CRDB_CLUSTER_NS} --sort-by='.lastTimestamp' | tail -20
```

### Version Mismatch

```bash
# Force pod restart if needed
kubectl delete pod cockroachdb-0 -n ${CRDB_CLUSTER_NS}

# Check image pull issues
kubectl describe pod -n ${CRDB_CLUSTER_NS} cockroachdb-0 | grep -A 10 "Events"
```

### Cluster Unavailable

```bash
# Check if quorum is maintained
kubectl exec -n ${CRDB_CLUSTER_NS} cockroachdb-0 -- \
  cockroach sql --insecure --host=localhost:26257 \
  --execute="SELECT * FROM crdb_internal.gossip_nodes;"

# Check pod logs
kubectl logs -n ${CRDB_CLUSTER_NS} cockroachdb-0 --tail=50
```
