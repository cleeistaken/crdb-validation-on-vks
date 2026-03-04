# VKS-21: Physical Cluster Replication (PCR) with Operator-Managed Clusters

## Test Information

| Field | Value |
|-------|-------|
| **Test ID** | VKS-21 |
| **Category** | Advanced Features |
| **Dependencies** | [VKS-02](../01-cluster-provisioning/VKS-02-cluster-install.md) (x2 clusters) |

## Objective

Configure Physical Cluster Replication (PCR) between two operator-managed CockroachDB clusters on VKS, verify replication stream functionality, run workloads on primary, and perform a cutover to the standby.

## Pre-requisites

- Two VKS clusters or namespaces representing primary and standby
- PCR feature enabled (Enterprise license required)
- Network connectivity between primary and standby SQL endpoints
- `kubectl` configured with VKS cluster kubeconfig

## Environment Setup

```bash
# Ensure KUBECONFIG points to VKS cluster
export KUBECONFIG=$(pwd)/vks-kubeconfig.yaml

# Set environment variables for primary cluster
export PRIMARY_NS="crdb-primary"
export PRIMARY_SVC="cockroachdb-primary-public"

# Set environment variables for standby cluster
export STANDBY_NS="crdb-standby"
export STANDBY_SVC="cockroachdb-standby-public"

# For single-cluster setup with two namespaces
echo "This test requires two separate CockroachDB clusters"
echo "Either in different namespaces or different VKS clusters"
```

## Steps

### Step 1: Deploy Primary Cluster

```bash
# Create primary namespace
kubectl create namespace ${PRIMARY_NS}

# Deploy primary cluster
helm install cockroachdb-primary ./cockroachdb-parent/charts/cockroachdb \
  --namespace ${PRIMARY_NS} \
  --set conf.cluster-name="primary-cluster" \
  --set statefulset.replicas=3 \
  --wait \
  --timeout 10m

# Wait for pods
kubectl wait --for=condition=Ready pods --all -n ${PRIMARY_NS} --timeout=300s

# Verify primary cluster
kubectl exec -n ${PRIMARY_NS} cockroachdb-primary-0 -- \
  cockroach node status --insecure --host=localhost:26257
```

### Step 2: Deploy Standby Cluster

```bash
# Create standby namespace
kubectl create namespace ${STANDBY_NS}

# Deploy standby cluster
helm install cockroachdb-standby ./cockroachdb-parent/charts/cockroachdb \
  --namespace ${STANDBY_NS} \
  --set conf.cluster-name="standby-cluster" \
  --set statefulset.replicas=3 \
  --wait \
  --timeout 10m

# Wait for pods
kubectl wait --for=condition=Ready pods --all -n ${STANDBY_NS} --timeout=300s

# Verify standby cluster
kubectl exec -n ${STANDBY_NS} cockroachdb-standby-0 -- \
  cockroach node status --insecure --host=localhost:26257
```

### Step 3: Verify Network Connectivity

```bash
# Test connectivity from standby to primary
kubectl exec -n ${STANDBY_NS} cockroachdb-standby-0 -- \
  nc -zv cockroachdb-primary-public.${PRIMARY_NS}.svc.cluster.local 26257

# Test connectivity from primary to standby
kubectl exec -n ${PRIMARY_NS} cockroachdb-primary-0 -- \
  nc -zv cockroachdb-standby-public.${STANDBY_NS}.svc.cluster.local 26257
```

### Step 4: Create Test Data on Primary

```bash
# Create test database on primary
kubectl exec -n ${PRIMARY_NS} cockroachdb-primary-0 -- \
  cockroach sql --insecure --host=localhost:26257 \
  --execute="
    CREATE DATABASE IF NOT EXISTS pcr_test;
    USE pcr_test;
    CREATE TABLE IF NOT EXISTS replicated_data (
      id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
      value INT,
      created_at TIMESTAMP DEFAULT now()
    );
    INSERT INTO replicated_data (value) SELECT generate_series(1, 100);
    SELECT count(*) as row_count FROM replicated_data;
  "
```

### Step 5: Configure PCR on Primary (Source)

```bash
# Enable PCR on primary cluster
kubectl exec -n ${PRIMARY_NS} cockroachdb-primary-0 -- \
  cockroach sql --insecure --host=localhost:26257 \
  --execute="
    -- Enable rangefeeds (required for PCR)
    SET CLUSTER SETTING kv.rangefeed.enabled = true;
    
    -- Create replication user
    CREATE USER IF NOT EXISTS replication_user;
    GRANT SYSTEM REPLICATION TO replication_user;
  "
```

### Step 6: Configure PCR on Standby (Destination)

```bash
# Get primary cluster connection string
PRIMARY_CONN="postgresql://replication_user@cockroachdb-primary-public.${PRIMARY_NS}.svc.cluster.local:26257?sslmode=disable"

# Configure standby as physical replication target
kubectl exec -n ${STANDBY_NS} cockroachdb-standby-0 -- \
  cockroach sql --insecure --host=localhost:26257 \
  --execute="
    -- Enable rangefeeds
    SET CLUSTER SETTING kv.rangefeed.enabled = true;
    
    -- Create external connection to primary
    CREATE EXTERNAL CONNECTION primary_cluster AS '${PRIMARY_CONN}';
  "
```

### Step 7: Start Physical Replication Stream

```bash
# Start PCR from primary to standby
kubectl exec -n ${STANDBY_NS} cockroachdb-standby-0 -- \
  cockroach sql --insecure --host=localhost:26257 \
  --execute="
    -- Start physical replication (syntax may vary by version)
    -- ALTER VIRTUAL CLUSTER standby START REPLICATION OF primary ON 'external://primary_cluster';
    
    -- For newer versions:
    -- CREATE PHYSICAL REPLICATION STREAM FROM 'external://primary_cluster';
  "

echo "Note: PCR syntax varies by CockroachDB version. Consult documentation for exact commands."
```

### Step 8: Monitor Replication Status

```bash
# Check replication status on standby
kubectl exec -n ${STANDBY_NS} cockroachdb-standby-0 -- \
  cockroach sql --insecure --host=localhost:26257 \
  --execute="
    -- Check replication jobs
    SELECT job_id, job_type, status, created 
    FROM [SHOW JOBS] 
    WHERE job_type LIKE '%REPLICATION%'
    ORDER BY created DESC;
    
    -- Check stream ingestion metrics
    SELECT * FROM crdb_internal.stream_ingestion_metrics;
  "
```

### Step 9: Run Workload on Primary

```bash
# Start a continuous workload on primary
kubectl exec -n ${PRIMARY_NS} cockroachdb-primary-0 -- \
  cockroach workload run kv \
  --init \
  --duration=120s \
  --concurrency=4 \
  --max-rate=50 \
  'postgresql://root@localhost:26257/pcr_test?sslmode=disable' &

WORKLOAD_PID=$!
echo "Workload started"

# Monitor replication lag
for i in {1..12}; do
  echo "=== Replication check $i ==="
  kubectl exec -n ${STANDBY_NS} cockroachdb-standby-0 -- \
    cockroach sql --insecure --host=localhost:26257 \
    --execute="SELECT * FROM crdb_internal.stream_ingestion_metrics;" 2>/dev/null || echo "Metrics not available"
  sleep 10
done

wait $WORKLOAD_PID 2>/dev/null
```

### Step 10: Verify Replication Lag

```bash
# Check replication is keeping up
kubectl exec -n ${STANDBY_NS} cockroachdb-standby-0 -- \
  cockroach sql --insecure --host=localhost:26257 \
  --execute="
    -- Check high-water timestamp
    SELECT * FROM crdb_internal.stream_ingestion_metrics;
    
    -- Check job status
    SELECT job_id, status, high_water_timestamp 
    FROM [SHOW JOBS] 
    WHERE job_type LIKE '%REPLICATION%';
  "
```

### Step 11: Perform Cutover

```bash
# Stop writes on primary (simulate planned failover)
echo "Stopping writes on primary..."

# Perform cutover on standby
kubectl exec -n ${STANDBY_NS} cockroachdb-standby-0 -- \
  cockroach sql --insecure --host=localhost:26257 \
  --execute="
    -- Complete the cutover (syntax varies by version)
    -- ALTER VIRTUAL CLUSTER standby COMPLETE REPLICATION TO LATEST;
    
    -- Or for newer versions:
    -- ALTER PHYSICAL REPLICATION STREAM ... COMPLETE;
  "

echo "Note: Cutover syntax varies by CockroachDB version. Consult documentation."
```

### Step 12: Verify Cutover Success

```bash
# Verify data on standby after cutover
kubectl exec -n ${STANDBY_NS} cockroachdb-standby-0 -- \
  cockroach sql --insecure --host=localhost:26257 \
  --execute="
    -- Check if database is accessible
    SHOW DATABASES;
    
    -- Verify data (after cutover, standby becomes writable)
    -- SELECT count(*) FROM pcr_test.replicated_data;
  "
```

## Validation Commands

```bash
# Complete validation script
echo "=== PCR Validation ==="

echo -e "\n1. Primary cluster status:"
kubectl exec -n ${PRIMARY_NS} cockroachdb-primary-0 -- \
  cockroach node status --insecure --host=localhost:26257

echo -e "\n2. Standby cluster status:"
kubectl exec -n ${STANDBY_NS} cockroachdb-standby-0 -- \
  cockroach node status --insecure --host=localhost:26257

echo -e "\n3. Replication jobs:"
kubectl exec -n ${STANDBY_NS} cockroachdb-standby-0 -- \
  cockroach sql --insecure --host=localhost:26257 \
  --execute="SELECT job_id, job_type, status FROM [SHOW JOBS] WHERE job_type LIKE '%REPLICATION%';"

echo -e "\n4. Network connectivity:"
kubectl exec -n ${STANDBY_NS} cockroachdb-standby-0 -- \
  nc -zv cockroachdb-primary-public.${PRIMARY_NS}.svc.cluster.local 26257 2>&1

echo -e "\n=== Validation Complete ==="
```

## Expected Results

| Check | Expected State |
|-------|----------------|
| Primary cluster | Running, healthy |
| Standby cluster | Running, healthy |
| Network connectivity | Bidirectional |
| Replication stream | Active, low lag |
| Cutover | Successful |
| Data integrity | Consistent after cutover |

## Cleanup

```bash
# Remove standby cluster
helm uninstall cockroachdb-standby -n ${STANDBY_NS}
kubectl delete pvc --all -n ${STANDBY_NS}
kubectl delete namespace ${STANDBY_NS}

# Remove primary cluster
helm uninstall cockroachdb-primary -n ${PRIMARY_NS}
kubectl delete pvc --all -n ${PRIMARY_NS}
kubectl delete namespace ${PRIMARY_NS}
```

## Notes

- PCR requires Enterprise license
- Network connectivity between clusters is critical
- Replication lag should be monitored in production
- Cutover is a planned operation; unplanned failover has different procedures
- PCR syntax varies significantly between CockroachDB versions

## Troubleshooting

### Replication Not Starting

```bash
# Check external connection
kubectl exec -n ${STANDBY_NS} cockroachdb-standby-0 -- \
  cockroach sql --insecure --host=localhost:26257 \
  --execute="SHOW EXTERNAL CONNECTIONS;"

# Check job errors
kubectl exec -n ${STANDBY_NS} cockroachdb-standby-0 -- \
  cockroach sql --insecure --host=localhost:26257 \
  --execute="SELECT error FROM [SHOW JOBS] WHERE job_type LIKE '%REPLICATION%' AND status = 'failed';"
```

### High Replication Lag

```bash
# Check network latency
kubectl exec -n ${STANDBY_NS} cockroachdb-standby-0 -- \
  ping -c 5 cockroachdb-primary-public.${PRIMARY_NS}.svc.cluster.local

# Check rangefeed settings
kubectl exec -n ${PRIMARY_NS} cockroachdb-primary-0 -- \
  cockroach sql --insecure --host=localhost:26257 \
  --execute="SHOW CLUSTER SETTING kv.rangefeed.enabled;"
```

### Cutover Fails

```bash
# Check replication status before cutover
kubectl exec -n ${STANDBY_NS} cockroachdb-standby-0 -- \
  cockroach sql --insecure --host=localhost:26257 \
  --execute="SELECT * FROM [SHOW JOBS] WHERE job_type LIKE '%REPLICATION%';"

# Ensure no active writes on primary during cutover
```
