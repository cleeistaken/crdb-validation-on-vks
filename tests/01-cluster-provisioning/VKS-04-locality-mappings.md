# VKS-04: Multi-Region / Locality Mappings

## Test Information

| Field | Value |
|-------|-------|
| **Test ID** | VKS-04 |
| **Category** | Cluster Provisioning |
| **Dependencies** | [VKS-02](VKS-02-cluster-install.md) |

## Objective

Configure locality mappings in CockroachDB to leverage VKS node topology labels (rack1, rack2, rack3), verify that each CockroachDB node exposes correct locality information, and confirm replicas distribute according to topology.

## Pre-requisites

- VKS-02 completed (CockroachDB cluster installed)
- VKS cluster nodes labeled with `topology.kubernetes.io/region` (rack1, rack2, rack3)
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

### Step 1: Verify Node Topology Labels

```bash
# List nodes with topology labels
kubectl get nodes -o custom-columns=NAME:.metadata.name,REGION:.metadata.labels.'topology\.kubernetes\.io/region',ZONE:.metadata.labels.'topology\.kubernetes\.io/zone'

# Show all labels on worker nodes
kubectl get nodes -l '!node-role.kubernetes.io/control-plane' --show-labels
```

**Expected Output:**
```
NAME                           REGION   ZONE
cluster-vks-node-pool-1-xxxxx  rack1    <none>
cluster-vks-node-pool-2-xxxxx  rack2    <none>
cluster-vks-node-pool-3-xxxxx  rack3    <none>
```

### Step 2: Create Locality Mapping Values File

```bash
# Create Helm values file with locality mappings
cat > /tmp/locality-values.yaml << 'EOF'
# Locality configuration for VKS topology
conf:
  locality: "region=\$(REGION)"

statefulset:
  env:
  - name: REGION
    valueFrom:
      fieldRef:
        fieldPath: spec.nodeName

# Alternative: Use topology spread constraints with locality labels
localityMappings:
  - key: topology.kubernetes.io/region
    value: region

# Pod topology spread for even distribution
topologySpreadConstraints:
  - maxSkew: 1
    topologyKey: topology.kubernetes.io/region
    whenUnsatisfiable: DoNotSchedule
    labelSelector:
      matchLabels:
        app.kubernetes.io/name: cockroachdb
EOF
```

### Step 3: Upgrade Cluster with Locality Configuration

```bash
# Upgrade the cluster with locality mappings
helm upgrade ${CRDB_RELEASE_NAME} ${CRDB_CHART_PATH} \
  --namespace ${CRDB_CLUSTER_NS} \
  --reuse-values \
  --set "conf.attrs=region" \
  --set "conf.locality=region=\$(REGION)" \
  -f /tmp/locality-values.yaml \
  --wait \
  --timeout 10m
```

**Note:** The exact Helm values depend on the chart version. Check the chart documentation for the correct locality configuration syntax.

### Step 4: Alternative - Configure Locality via Pod Annotations

If the Helm chart doesn't support `localityMappings` directly, use this approach:

```bash
# Create a values file using node labels for locality
cat > /tmp/locality-values-alt.yaml << 'EOF'
statefulset:
  topologySpreadConstraints:
    - maxSkew: 1
      topologyKey: topology.kubernetes.io/region
      whenUnsatisfiable: DoNotSchedule
      labelSelector:
        matchLabels:
          app.kubernetes.io/name: cockroachdb
  
  # Use downward API to inject node labels
  extraEnv:
    - name: COCKROACH_LOCALITY
      value: "region=$(NODE_REGION)"
    - name: NODE_REGION
      valueFrom:
        fieldRef:
          fieldPath: metadata.labels['topology.kubernetes.io/region']
EOF

# Apply the upgrade
helm upgrade ${CRDB_RELEASE_NAME} ${CRDB_CHART_PATH} \
  --namespace ${CRDB_CLUSTER_NS} \
  --reuse-values \
  -f /tmp/locality-values-alt.yaml \
  --wait \
  --timeout 10m
```

### Step 5: Monitor Rolling Update

```bash
# Watch pods restart with new configuration
kubectl get pods -n ${CRDB_CLUSTER_NS} -w

# Wait for all pods to be ready
kubectl wait --for=condition=Ready pods --all -n ${CRDB_CLUSTER_NS} --timeout=600s
```

### Step 6: Verify Pod Distribution

```bash
# Check which nodes pods are scheduled on
kubectl get pods -n ${CRDB_CLUSTER_NS} -o wide

# Verify each pod is on a different region/rack
kubectl get pods -n ${CRDB_CLUSTER_NS} -o jsonpath='{range .items[*]}{.metadata.name}{"\t"}{.spec.nodeName}{"\n"}{end}'
```

**Expected Output:**
- Each CockroachDB pod on a different node
- Pods distributed across rack1, rack2, rack3

### Step 7: Verify Locality in CockroachDB

```bash
# Check locality from each node
kubectl exec -n ${CRDB_CLUSTER_NS} cockroachdb-0 -- \
  cockroach sql --insecure --host=localhost:26257 \
  --execute="SHOW LOCALITY;"

# Check all nodes' localities
kubectl exec -n ${CRDB_CLUSTER_NS} cockroachdb-0 -- \
  cockroach sql --insecure --host=localhost:26257 \
  --execute="SELECT node_id, locality FROM crdb_internal.gossip_nodes ORDER BY node_id;"
```

**Expected Output:**
```
  node_id |    locality
----------+----------------
        1 | region=rack1
        2 | region=rack2
        3 | region=rack3
```

### Step 8: Verify Replica Distribution

```bash
# Check range distribution across localities
kubectl exec -n ${CRDB_CLUSTER_NS} cockroachdb-0 -- \
  cockroach sql --insecure --host=localhost:26257 \
  --execute="
    SELECT 
      store_id,
      node_id,
      locality,
      range_count,
      used
    FROM crdb_internal.kv_store_status;
  "
```

**Expected Output:**
- Ranges distributed across all stores
- Each store associated with different locality

### Step 9: Test Zone Configuration

```bash
# Create a database with zone configuration
kubectl exec -n ${CRDB_CLUSTER_NS} cockroachdb-0 -- \
  cockroach sql --insecure --host=localhost:26257 \
  --execute="
    CREATE DATABASE IF NOT EXISTS locality_test;
    
    -- Configure zone to require replicas in each region
    ALTER DATABASE locality_test CONFIGURE ZONE USING
      num_replicas = 3,
      constraints = '[+region=rack1, +region=rack2, +region=rack3]';
    
    -- Show zone configuration
    SHOW ZONE CONFIGURATION FOR DATABASE locality_test;
  "
```

**Expected Output:**
```
     target     |              raw_config_sql
----------------+------------------------------------------
  DATABASE locality_test | ALTER DATABASE locality_test CONFIGURE ZONE USING
                |     num_replicas = 3,
                |     constraints = '[+region=rack1, +region=rack2, +region=rack3]'
```

### Step 10: Verify Constraint Satisfaction

```bash
# Create a table and insert data
kubectl exec -n ${CRDB_CLUSTER_NS} cockroachdb-0 -- \
  cockroach sql --insecure --host=localhost:26257 \
  --execute="
    USE locality_test;
    CREATE TABLE IF NOT EXISTS distributed_data (
      id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
      region STRING,
      data STRING
    );
    INSERT INTO distributed_data (region, data) VALUES 
      ('rack1', 'data1'),
      ('rack2', 'data2'),
      ('rack3', 'data3');
  "

# Check replica placement
kubectl exec -n ${CRDB_CLUSTER_NS} cockroachdb-0 -- \
  cockroach sql --insecure --host=localhost:26257 \
  --execute="
    SELECT 
      range_id,
      replicas,
      lease_holder
    FROM [SHOW RANGES FROM TABLE locality_test.distributed_data];
  "
```

### Step 11: Verify Locality-Aware Queries

```bash
# Check that queries prefer local replicas
kubectl exec -n ${CRDB_CLUSTER_NS} cockroachdb-0 -- \
  cockroach sql --insecure --host=localhost:26257 \
  --execute="
    SET CLUSTER SETTING sql.trace.log_statement_execute = true;
    SELECT * FROM locality_test.distributed_data WHERE region = 'rack1';
  "
```

## Validation Commands

```bash
# Complete validation script
echo "=== Locality Configuration Validation ==="

echo -e "\n1. Node topology labels:"
kubectl get nodes -o custom-columns=NAME:.metadata.name,REGION:.metadata.labels.'topology\.kubernetes\.io/region'

echo -e "\n2. Pod distribution:"
kubectl get pods -n ${CRDB_CLUSTER_NS} -o wide

echo -e "\n3. CockroachDB node localities:"
kubectl exec -n ${CRDB_CLUSTER_NS} cockroachdb-0 -- \
  cockroach sql --insecure --host=localhost:26257 \
  --execute="SELECT node_id, locality FROM crdb_internal.gossip_nodes ORDER BY node_id;"

echo -e "\n4. Store distribution:"
kubectl exec -n ${CRDB_CLUSTER_NS} cockroachdb-0 -- \
  cockroach sql --insecure --host=localhost:26257 \
  --execute="SELECT store_id, node_id, locality FROM crdb_internal.kv_store_status;"

echo -e "\n=== Validation Complete ==="
```

## Expected Results

| Check | Expected State |
|-------|----------------|
| Node labels | rack1, rack2, rack3 on worker nodes |
| Pod distribution | One pod per rack/region |
| CockroachDB localities | Each node shows different region |
| Replica placement | Replicas spread across regions |
| Zone configuration | Constraints respected |

## Cleanup

```bash
# Remove test database
kubectl exec -n ${CRDB_CLUSTER_NS} cockroachdb-0 -- \
  cockroach sql --insecure --host=localhost:26257 \
  --execute="DROP DATABASE IF EXISTS locality_test CASCADE;"

# Remove temporary values file
rm -f /tmp/locality-values.yaml /tmp/locality-values-alt.yaml
```

## Notes

- Locality mappings enable CockroachDB to understand the physical topology
- This is critical for multi-region deployments and disaster recovery
- The VKS cluster was pre-configured with rack1, rack2, rack3 labels in the vks.yaml
- Proper locality configuration ensures optimal replica placement and query routing

## Troubleshooting

### Locality Not Showing

```bash
# Check pod environment variables
kubectl exec -n ${CRDB_CLUSTER_NS} cockroachdb-0 -- env | grep -i locality

# Check CockroachDB startup flags
kubectl exec -n ${CRDB_CLUSTER_NS} cockroachdb-0 -- ps aux | grep cockroach
```

### Pods Not Spreading Across Regions

```bash
# Check topology spread constraints
kubectl get statefulset -n ${CRDB_CLUSTER_NS} cockroachdb -o yaml | grep -A 10 topologySpreadConstraints

# Check scheduler events
kubectl get events -n ${CRDB_CLUSTER_NS} --sort-by='.lastTimestamp' | grep -i schedule
```

### Zone Configuration Not Applied

```bash
# Check zone configuration
kubectl exec -n ${CRDB_CLUSTER_NS} cockroachdb-0 -- \
  cockroach sql --insecure --host=localhost:26257 \
  --execute="SHOW ALL ZONE CONFIGURATIONS;"

# Check for constraint violations
kubectl exec -n ${CRDB_CLUSTER_NS} cockroachdb-0 -- \
  cockroach sql --insecure --host=localhost:26257 \
  --execute="SELECT * FROM crdb_internal.zones WHERE config_sql LIKE '%constraint%';"
```
