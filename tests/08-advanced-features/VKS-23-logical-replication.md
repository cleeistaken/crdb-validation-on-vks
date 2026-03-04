# VKS-23: Logical Replication Between Clusters

## Test Information

| Field | Value |
|-------|-------|
| **Test ID** | VKS-23 |
| **Category** | Advanced Features |
| **Dependencies** | [VKS-02](../01-cluster-provisioning/VKS-02-cluster-install.md) (x2 clusters) |

## Objective

Configure logical replication between two CockroachDB clusters on VKS using CREATE LOGICALLY REPLICATED TABLE, verify data flows from producer to consumer, and monitor replication metrics.

## Pre-requisites

- Two operator-managed CockroachDB clusters on VKS
- Network connectivity between SQL endpoints
- Rangefeeds enabled on both clusters
- `kubectl` configured with VKS cluster kubeconfig

## Environment Setup

```bash
# Ensure KUBECONFIG points to VKS cluster
export KUBECONFIG=$(pwd)/vks-kubeconfig.yaml

# Set environment variables for producer cluster
export PRODUCER_NS="crdb-producer"
export PRODUCER_SVC="cockroachdb-producer-public"

# Set environment variables for consumer cluster
export CONSUMER_NS="crdb-consumer"
export CONSUMER_SVC="cockroachdb-consumer-public"

echo "This test requires two separate CockroachDB clusters"
```

## Steps

### Step 1: Deploy Producer Cluster

```bash
# Create producer namespace
kubectl create namespace ${PRODUCER_NS}

# Deploy producer cluster
helm install cockroachdb-producer ./cockroachdb-parent/charts/cockroachdb \
  --namespace ${PRODUCER_NS} \
  --set conf.cluster-name="producer-cluster" \
  --set statefulset.replicas=3 \
  --wait \
  --timeout 10m

# Wait for pods
kubectl wait --for=condition=Ready pods --all -n ${PRODUCER_NS} --timeout=300s
```

### Step 2: Deploy Consumer Cluster

```bash
# Create consumer namespace
kubectl create namespace ${CONSUMER_NS}

# Deploy consumer cluster
helm install cockroachdb-consumer ./cockroachdb-parent/charts/cockroachdb \
  --namespace ${CONSUMER_NS} \
  --set conf.cluster-name="consumer-cluster" \
  --set statefulset.replicas=3 \
  --wait \
  --timeout 10m

# Wait for pods
kubectl wait --for=condition=Ready pods --all -n ${CONSUMER_NS} --timeout=300s
```

### Step 3: Enable Rangefeeds on Both Clusters

```bash
# Enable rangefeeds on producer
kubectl exec -n ${PRODUCER_NS} cockroachdb-producer-0 -- \
  cockroach sql --insecure --host=localhost:26257 \
  --execute="SET CLUSTER SETTING kv.rangefeed.enabled = true;"

# Enable rangefeeds on consumer
kubectl exec -n ${CONSUMER_NS} cockroachdb-consumer-0 -- \
  cockroach sql --insecure --host=localhost:26257 \
  --execute="SET CLUSTER SETTING kv.rangefeed.enabled = true;"
```

### Step 4: Initialize KV Workload on Producer

```bash
# Initialize the KV workload schema on producer
kubectl exec -n ${PRODUCER_NS} cockroachdb-producer-0 -- \
  cockroach workload init kv \
  'postgresql://root@localhost:26257?sslmode=disable'

# Verify table created
kubectl exec -n ${PRODUCER_NS} cockroachdb-producer-0 -- \
  cockroach sql --insecure --host=localhost:26257 \
  --execute="
    SHOW DATABASES;
    USE kv;
    SHOW TABLES;
    SELECT count(*) FROM kv;
  "
```

### Step 5: Create Target Database on Consumer

```bash
# Create target database on consumer
kubectl exec -n ${CONSUMER_NS} cockroachdb-consumer-0 -- \
  cockroach sql --insecure --host=localhost:26257 \
  --execute="
    CREATE DATABASE IF NOT EXISTS kv_copy;
  "
```

### Step 6: Create Replication User on Both Clusters

```bash
# Create replication user on producer
kubectl exec -n ${PRODUCER_NS} cockroachdb-producer-0 -- \
  cockroach sql --insecure --host=localhost:26257 \
  --execute="
    CREATE USER IF NOT EXISTS repl;
    GRANT SELECT ON TABLE kv.kv TO repl;
    GRANT SYSTEM REPLICATION TO repl;
  "

# Create replication user on consumer
kubectl exec -n ${CONSUMER_NS} cockroachdb-consumer-0 -- \
  cockroach sql --insecure --host=localhost:26257 \
  --execute="
    CREATE USER IF NOT EXISTS repl;
    GRANT CREATE ON DATABASE kv_copy TO repl;
    GRANT SYSTEM REPLICATION TO repl;
  "
```

### Step 7: Get Producer CA Certificate

```bash
# For insecure mode, we'll use sslmode=disable
# For secure mode, extract CA cert:
# kubectl exec -n ${PRODUCER_NS} cockroachdb-producer-0 -- \
#   cat /cockroach/cockroach-certs/ca.crt > /tmp/producer-ca.crt

# Get producer service DNS
PRODUCER_DNS="cockroachdb-producer-public.${PRODUCER_NS}.svc.cluster.local"
echo "Producer DNS: ${PRODUCER_DNS}"
```

### Step 8: Create External Connection on Consumer

```bash
# Create external connection to producer
kubectl exec -n ${CONSUMER_NS} cockroachdb-consumer-0 -- \
  cockroach sql --insecure --host=localhost:26257 \
  --execute="
    CREATE EXTERNAL CONNECTION src_conn AS 
    'postgresql://repl@cockroachdb-producer-public.${PRODUCER_NS}.svc.cluster.local:26257?sslmode=disable';
  "

# Verify external connection
kubectl exec -n ${CONSUMER_NS} cockroachdb-consumer-0 -- \
  cockroach sql --insecure --host=localhost:26257 \
  --execute="SHOW EXTERNAL CONNECTIONS;"
```

### Step 9: Create Logically Replicated Table

```bash
# Create the logically replicated table on consumer
kubectl exec -n ${CONSUMER_NS} cockroachdb-consumer-0 -- \
  cockroach sql --insecure --host=localhost:26257 \
  --execute="
    CREATE LOGICALLY REPLICATED TABLE kv_copy.kv 
    FROM TABLE kv.kv 
    ON 'external://src_conn' 
    WITH unidirectional;
  "

# Check job status
kubectl exec -n ${CONSUMER_NS} cockroachdb-consumer-0 -- \
  cockroach sql --insecure --host=localhost:26257 \
  --execute="
    SELECT job_id, job_type, status, created 
    FROM [SHOW JOBS] 
    WHERE job_type LIKE '%REPLICATION%'
    ORDER BY created DESC;
  "
```

### Step 10: Start KV Workload on Producer

```bash
# Start continuous workload on producer
kubectl exec -n ${PRODUCER_NS} cockroachdb-producer-0 -- \
  cockroach workload run kv \
  --duration=120s \
  --concurrency=4 \
  --max-rate=50 \
  'postgresql://root@localhost:26257?sslmode=disable' &

WORKLOAD_PID=$!
echo "Workload started"
```

### Step 11: Monitor Replication

```bash
# Monitor replication progress
for i in {1..12}; do
  echo "=== Replication check $i ==="
  
  # Check producer row count
  PRODUCER_COUNT=$(kubectl exec -n ${PRODUCER_NS} cockroachdb-producer-0 -- \
    cockroach sql --insecure --host=localhost:26257 \
    --execute="SELECT count(*) FROM kv.kv;" -f csv 2>/dev/null | tail -1)
  echo "Producer rows: ${PRODUCER_COUNT}"
  
  # Check consumer row count
  CONSUMER_COUNT=$(kubectl exec -n ${CONSUMER_NS} cockroachdb-consumer-0 -- \
    cockroach sql --insecure --host=localhost:26257 \
    --execute="SELECT count(*) FROM kv_copy.kv;" -f csv 2>/dev/null | tail -1)
  echo "Consumer rows: ${CONSUMER_COUNT}"
  
  # Check replication metrics
  kubectl exec -n ${CONSUMER_NS} cockroachdb-consumer-0 -- \
    cockroach sql --insecure --host=localhost:26257 \
    --execute="SELECT * FROM crdb_internal.stream_ingestion_metrics;" 2>/dev/null || echo "Metrics not available"
  
  sleep 10
done

wait $WORKLOAD_PID 2>/dev/null
```

### Step 12: Verify Data Consistency

```bash
# Get final counts
echo "=== Final Counts ==="

PRODUCER_FINAL=$(kubectl exec -n ${PRODUCER_NS} cockroachdb-producer-0 -- \
  cockroach sql --insecure --host=localhost:26257 \
  --execute="SELECT count(*) FROM kv.kv;" -f csv | tail -1)
echo "Producer final count: ${PRODUCER_FINAL}"

# Wait for replication to catch up
sleep 30

CONSUMER_FINAL=$(kubectl exec -n ${CONSUMER_NS} cockroachdb-consumer-0 -- \
  cockroach sql --insecure --host=localhost:26257 \
  --execute="SELECT count(*) FROM kv_copy.kv;" -f csv | tail -1)
echo "Consumer final count: ${CONSUMER_FINAL}"

# Check if counts match
if [ "${PRODUCER_FINAL}" == "${CONSUMER_FINAL}" ]; then
  echo "SUCCESS: Row counts match!"
else
  echo "Replication may still be catching up. Producer: ${PRODUCER_FINAL}, Consumer: ${CONSUMER_FINAL}"
fi
```

## Validation Commands

```bash
# Complete validation script
echo "=== Logical Replication Validation ==="

echo -e "\n1. Producer cluster status:"
kubectl exec -n ${PRODUCER_NS} cockroachdb-producer-0 -- \
  cockroach node status --insecure --host=localhost:26257

echo -e "\n2. Consumer cluster status:"
kubectl exec -n ${CONSUMER_NS} cockroachdb-consumer-0 -- \
  cockroach node status --insecure --host=localhost:26257

echo -e "\n3. External connection:"
kubectl exec -n ${CONSUMER_NS} cockroachdb-consumer-0 -- \
  cockroach sql --insecure --host=localhost:26257 \
  --execute="SHOW EXTERNAL CONNECTIONS;"

echo -e "\n4. Replication job status:"
kubectl exec -n ${CONSUMER_NS} cockroachdb-consumer-0 -- \
  cockroach sql --insecure --host=localhost:26257 \
  --execute="SELECT job_id, job_type, status FROM [SHOW JOBS] WHERE job_type LIKE '%REPLICATION%';"

echo -e "\n5. Row counts:"
echo "Producer:"
kubectl exec -n ${PRODUCER_NS} cockroachdb-producer-0 -- \
  cockroach sql --insecure --host=localhost:26257 \
  --execute="SELECT count(*) FROM kv.kv;"
echo "Consumer:"
kubectl exec -n ${CONSUMER_NS} cockroachdb-consumer-0 -- \
  cockroach sql --insecure --host=localhost:26257 \
  --execute="SELECT count(*) FROM kv_copy.kv;"

echo -e "\n=== Validation Complete ==="
```

## Expected Results

| Check | Expected State |
|-------|----------------|
| Producer cluster | Running, healthy |
| Consumer cluster | Running, healthy |
| External connection | Created successfully |
| Replication job | Running |
| Row counts | Match (eventually consistent) |

## Cleanup

```bash
# Stop replication job on consumer
kubectl exec -n ${CONSUMER_NS} cockroachdb-consumer-0 -- \
  cockroach sql --insecure --host=localhost:26257 \
  --execute="
    CANCEL JOB (SELECT job_id FROM [SHOW JOBS] WHERE job_type LIKE '%REPLICATION%' AND status = 'running');
  " 2>/dev/null

# Remove consumer cluster
helm uninstall cockroachdb-consumer -n ${CONSUMER_NS}
kubectl delete pvc --all -n ${CONSUMER_NS}
kubectl delete namespace ${CONSUMER_NS}

# Remove producer cluster
helm uninstall cockroachdb-producer -n ${PRODUCER_NS}
kubectl delete pvc --all -n ${PRODUCER_NS}
kubectl delete namespace ${PRODUCER_NS}
```

## Notes

- Logical replication is unidirectional in this configuration
- Rangefeeds must be enabled on both clusters
- Network connectivity between clusters is critical
- Monitor replication lag in production
- Consider conflict resolution for bidirectional setups

## Troubleshooting

### External Connection Fails

```bash
# Test network connectivity
kubectl exec -n ${CONSUMER_NS} cockroachdb-consumer-0 -- \
  nc -zv cockroachdb-producer-public.${PRODUCER_NS}.svc.cluster.local 26257

# Check DNS resolution
kubectl exec -n ${CONSUMER_NS} cockroachdb-consumer-0 -- \
  nslookup cockroachdb-producer-public.${PRODUCER_NS}.svc.cluster.local
```

### Replication Job Fails

```bash
# Check job error
kubectl exec -n ${CONSUMER_NS} cockroachdb-consumer-0 -- \
  cockroach sql --insecure --host=localhost:26257 \
  --execute="SELECT error FROM [SHOW JOBS] WHERE job_type LIKE '%REPLICATION%' AND status = 'failed';"

# Check rangefeed is enabled
kubectl exec -n ${PRODUCER_NS} cockroachdb-producer-0 -- \
  cockroach sql --insecure --host=localhost:26257 \
  --execute="SHOW CLUSTER SETTING kv.rangefeed.enabled;"
```

### High Replication Lag

```bash
# Check stream ingestion metrics
kubectl exec -n ${CONSUMER_NS} cockroachdb-consumer-0 -- \
  cockroach sql --insecure --host=localhost:26257 \
  --execute="SELECT * FROM crdb_internal.stream_ingestion_metrics;"

# Check producer load
kubectl exec -n ${PRODUCER_NS} cockroachdb-producer-0 -- \
  cockroach sql --insecure --host=localhost:26257 \
  --execute="SELECT * FROM crdb_internal.node_metrics WHERE name LIKE '%rangefeed%';"
```
