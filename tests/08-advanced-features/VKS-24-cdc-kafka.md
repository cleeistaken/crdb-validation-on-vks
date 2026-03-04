# VKS-24: CDC to Kafka or Cloud Pub/Sub from VKS

## Test Information

| Field | Value |
|-------|-------|
| **Test ID** | VKS-24 |
| **Category** | Advanced Features |
| **Dependencies** | [VKS-02](../01-cluster-provisioning/VKS-02-cluster-install.md) |

## Objective

Configure Change Data Capture (CDC) changefeeds from CockroachDB to an external Kafka cluster or cloud pub/sub, verify data flows reliably, test pause/resume behavior, and monitor changefeed metrics.

## Pre-requisites

- VKS-02 completed (CockroachDB cluster running)
- Kafka cluster or cloud pub/sub endpoint reachable
- Network and auth configured for sink access
- `kubectl` configured with VKS cluster kubeconfig

## Environment Setup

```bash
# Ensure KUBECONFIG points to VKS cluster
export KUBECONFIG=$(pwd)/vks-kubeconfig.yaml

# Set environment variables
export CRDB_CLUSTER_NS="crdb-cluster"

# Kafka configuration (adjust to your environment)
export KAFKA_BROKER="kafka-broker.kafka.svc.cluster.local:9092"
export KAFKA_TOPIC="cockroachdb-changes"

# GCP Pub/Sub configuration (alternative)
# export PUBSUB_PROJECT="your-project"
# export PUBSUB_TOPIC="cockroachdb-changes"

# Verify cluster is running
kubectl get pods -n ${CRDB_CLUSTER_NS}
```

## Steps

### Step 1: Deploy Kafka (If Not Available)

```bash
# If you need a test Kafka cluster, deploy one
kubectl create namespace kafka

# Deploy Kafka using Strimzi or similar (simplified example)
cat <<EOF | kubectl apply -f -
apiVersion: v1
kind: Pod
metadata:
  name: kafka-test
  namespace: kafka
  labels:
    app: kafka
spec:
  containers:
  - name: kafka
    image: bitnami/kafka:latest
    ports:
    - containerPort: 9092
    env:
    - name: KAFKA_CFG_NODE_ID
      value: "0"
    - name: KAFKA_CFG_PROCESS_ROLES
      value: "controller,broker"
    - name: KAFKA_CFG_LISTENERS
      value: "PLAINTEXT://:9092,CONTROLLER://:9093"
    - name: KAFKA_CFG_LISTENER_SECURITY_PROTOCOL_MAP
      value: "CONTROLLER:PLAINTEXT,PLAINTEXT:PLAINTEXT"
    - name: KAFKA_CFG_CONTROLLER_QUORUM_VOTERS
      value: "0@localhost:9093"
    - name: KAFKA_CFG_CONTROLLER_LISTENER_NAMES
      value: "CONTROLLER"
---
apiVersion: v1
kind: Service
metadata:
  name: kafka-broker
  namespace: kafka
spec:
  selector:
    app: kafka
  ports:
  - port: 9092
    targetPort: 9092
EOF

# Wait for Kafka
kubectl wait --for=condition=Ready pod/kafka-test -n kafka --timeout=120s

# Update broker address
export KAFKA_BROKER="kafka-broker.kafka.svc.cluster.local:9092"
```

### Step 2: Create Source Table with Data

```bash
# Create test database and table
kubectl exec -n ${CRDB_CLUSTER_NS} cockroachdb-0 -- \
  cockroach sql --insecure --host=localhost:26257 \
  --execute="
    CREATE DATABASE IF NOT EXISTS cdc_test;
    USE cdc_test;
    
    CREATE TABLE IF NOT EXISTS events (
      id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
      event_type STRING NOT NULL,
      payload JSONB,
      created_at TIMESTAMP DEFAULT now()
    );
    
    -- Insert initial data
    INSERT INTO events (event_type, payload) VALUES
      ('user_created', '{\"user_id\": 1, \"name\": \"Alice\"}'),
      ('order_placed', '{\"order_id\": 100, \"amount\": 99.99}'),
      ('user_updated', '{\"user_id\": 1, \"email\": \"alice@example.com\"}');
    
    SELECT * FROM events;
  "
```

### Step 3: Verify Network Connectivity to Kafka

```bash
# Test connectivity from CockroachDB pod to Kafka
kubectl exec -n ${CRDB_CLUSTER_NS} cockroachdb-0 -- \
  nc -zv kafka-broker.kafka.svc.cluster.local 9092 2>&1 || \
  echo "Kafka not reachable - check network configuration"
```

### Step 4: Create Changefeed to Kafka

```bash
# Create changefeed
kubectl exec -n ${CRDB_CLUSTER_NS} cockroachdb-0 -- \
  cockroach sql --insecure --host=localhost:26257 \
  --execute="
    CREATE CHANGEFEED FOR TABLE cdc_test.events
    INTO 'kafka://${KAFKA_BROKER}?topic_prefix=crdb_'
    WITH 
      format = json,
      resolved = '10s',
      min_checkpoint_frequency = '10s';
  "

# Check changefeed job
kubectl exec -n ${CRDB_CLUSTER_NS} cockroachdb-0 -- \
  cockroach sql --insecure --host=localhost:26257 \
  --execute="
    SELECT job_id, job_type, status, created, high_water_timestamp
    FROM [SHOW JOBS]
    WHERE job_type = 'CHANGEFEED'
    ORDER BY created DESC
    LIMIT 1;
  "
```

### Step 5: Alternative - Create Changefeed to GCP Pub/Sub

```bash
# For GCP Pub/Sub (if using cloud sink)
# kubectl exec -n ${CRDB_CLUSTER_NS} cockroachdb-0 -- \
#   cockroach sql --insecure --host=localhost:26257 \
#   --execute="
#     CREATE CHANGEFEED FOR TABLE cdc_test.events
#     INTO 'gcpubsub://projects/${PUBSUB_PROJECT}/topics/${PUBSUB_TOPIC}'
#     WITH 
#       format = json,
#       resolved = '10s';
#   "
```

### Step 6: Generate More Data

```bash
# Insert more data to generate change events
kubectl exec -n ${CRDB_CLUSTER_NS} cockroachdb-0 -- \
  cockroach sql --insecure --host=localhost:26257 \
  --execute="
    USE cdc_test;
    
    -- Insert new events
    INSERT INTO events (event_type, payload) VALUES
      ('user_created', '{\"user_id\": 2, \"name\": \"Bob\"}'),
      ('order_placed', '{\"order_id\": 101, \"amount\": 149.99}'),
      ('payment_received', '{\"order_id\": 100, \"status\": \"paid\"}');
    
    -- Update existing event
    UPDATE events SET payload = payload || '{\"verified\": true}' WHERE event_type = 'user_created';
    
    -- Delete an event
    DELETE FROM events WHERE event_type = 'user_updated';
  "
```

### Step 7: Monitor Changefeed Metrics

```bash
# Check changefeed metrics in DB Console
kubectl exec -n ${CRDB_CLUSTER_NS} cockroachdb-0 -- \
  cockroach sql --insecure --host=localhost:26257 \
  --execute="
    -- Check changefeed job status
    SELECT 
      job_id,
      status,
      high_water_timestamp,
      error
    FROM [SHOW JOBS]
    WHERE job_type = 'CHANGEFEED'
    ORDER BY created DESC
    LIMIT 1;
    
    -- Check changefeed metrics
    SELECT * FROM crdb_internal.feature_usage WHERE feature_name LIKE '%changefeed%';
  "

# Check metrics endpoint
kubectl exec -n ${CRDB_CLUSTER_NS} cockroachdb-0 -- \
  curl -s http://localhost:8080/_status/vars | grep -i changefeed | head -10
```

### Step 8: Consume Messages from Kafka

```bash
# Start a Kafka consumer to verify messages
kubectl exec -n kafka kafka-test -- \
  kafka-console-consumer.sh \
  --bootstrap-server localhost:9092 \
  --topic crdb_cdc_test_events \
  --from-beginning \
  --max-messages 10 2>/dev/null || \
  echo "Use kafka-console-consumer or your preferred consumer to verify messages"

# Alternative: Create a consumer pod
cat <<EOF | kubectl apply -f -
apiVersion: v1
kind: Pod
metadata:
  name: kafka-consumer
  namespace: kafka
spec:
  containers:
  - name: consumer
    image: bitnami/kafka:latest
    command: ["sleep", "3600"]
  restartPolicy: Never
EOF

kubectl wait --for=condition=Ready pod/kafka-consumer -n kafka --timeout=60s

# Consume messages
kubectl exec -n kafka kafka-consumer -- \
  kafka-console-consumer.sh \
  --bootstrap-server kafka-broker:9092 \
  --topic crdb_cdc_test_events \
  --from-beginning \
  --timeout-ms 10000 2>/dev/null || echo "Check Kafka topic manually"
```

### Step 9: Test Pause and Resume

```bash
# Get changefeed job ID
JOB_ID=$(kubectl exec -n ${CRDB_CLUSTER_NS} cockroachdb-0 -- \
  cockroach sql --insecure --host=localhost:26257 \
  --execute="SELECT job_id FROM [SHOW JOBS] WHERE job_type = 'CHANGEFEED' AND status = 'running' LIMIT 1;" -f csv | tail -1)

echo "Changefeed Job ID: ${JOB_ID}"

# Pause the changefeed
kubectl exec -n ${CRDB_CLUSTER_NS} cockroachdb-0 -- \
  cockroach sql --insecure --host=localhost:26257 \
  --execute="PAUSE JOB ${JOB_ID};"

# Verify paused
kubectl exec -n ${CRDB_CLUSTER_NS} cockroachdb-0 -- \
  cockroach sql --insecure --host=localhost:26257 \
  --execute="SELECT job_id, status FROM [SHOW JOBS] WHERE job_id = ${JOB_ID};"

# Insert data while paused
kubectl exec -n ${CRDB_CLUSTER_NS} cockroachdb-0 -- \
  cockroach sql --insecure --host=localhost:26257 \
  --execute="INSERT INTO cdc_test.events (event_type, payload) VALUES ('while_paused', '{\"test\": true}');"

# Resume the changefeed
kubectl exec -n ${CRDB_CLUSTER_NS} cockroachdb-0 -- \
  cockroach sql --insecure --host=localhost:26257 \
  --execute="RESUME JOB ${JOB_ID};"

# Verify resumed and catching up
kubectl exec -n ${CRDB_CLUSTER_NS} cockroachdb-0 -- \
  cockroach sql --insecure --host=localhost:26257 \
  --execute="SELECT job_id, status, high_water_timestamp FROM [SHOW JOBS] WHERE job_id = ${JOB_ID};"
```

### Step 10: Test Changefeed Cancellation

```bash
# Cancel the changefeed
kubectl exec -n ${CRDB_CLUSTER_NS} cockroachdb-0 -- \
  cockroach sql --insecure --host=localhost:26257 \
  --execute="CANCEL JOB ${JOB_ID};"

# Verify cancelled
kubectl exec -n ${CRDB_CLUSTER_NS} cockroachdb-0 -- \
  cockroach sql --insecure --host=localhost:26257 \
  --execute="SELECT job_id, status FROM [SHOW JOBS] WHERE job_id = ${JOB_ID};"
```

## Validation Commands

```bash
# Complete validation script
echo "=== CDC to Kafka Validation ==="

echo -e "\n1. Cluster status:"
kubectl exec -n ${CRDB_CLUSTER_NS} cockroachdb-0 -- \
  cockroach node status --insecure --host=localhost:26257

echo -e "\n2. Source table:"
kubectl exec -n ${CRDB_CLUSTER_NS} cockroachdb-0 -- \
  cockroach sql --insecure --host=localhost:26257 \
  --execute="SELECT count(*) as events FROM cdc_test.events;"

echo -e "\n3. Changefeed jobs:"
kubectl exec -n ${CRDB_CLUSTER_NS} cockroachdb-0 -- \
  cockroach sql --insecure --host=localhost:26257 \
  --execute="SELECT job_id, job_type, status, created FROM [SHOW JOBS] WHERE job_type = 'CHANGEFEED' ORDER BY created DESC LIMIT 5;"

echo -e "\n4. Kafka connectivity:"
kubectl exec -n ${CRDB_CLUSTER_NS} cockroachdb-0 -- \
  nc -zv kafka-broker.kafka.svc.cluster.local 9092 2>&1 || echo "Not connected"

echo -e "\n=== Validation Complete ==="
```

## Expected Results

| Check | Expected State |
|-------|----------------|
| Changefeed job | Running (or cancelled after test) |
| High water timestamp | Advancing |
| Kafka messages | Delivered to topic |
| Pause/Resume | Works correctly |
| Cancellation | Job cancelled successfully |

## Cleanup

```bash
# Cancel any running changefeeds
kubectl exec -n ${CRDB_CLUSTER_NS} cockroachdb-0 -- \
  cockroach sql --insecure --host=localhost:26257 \
  --execute="
    CANCEL JOBS (SELECT job_id FROM [SHOW JOBS] WHERE job_type = 'CHANGEFEED' AND status = 'running');
  " 2>/dev/null

# Remove test database
kubectl exec -n ${CRDB_CLUSTER_NS} cockroachdb-0 -- \
  cockroach sql --insecure --host=localhost:26257 \
  --execute="DROP DATABASE IF EXISTS cdc_test CASCADE;"

# Remove Kafka test deployment
kubectl delete pod kafka-consumer -n kafka 2>/dev/null
kubectl delete pod kafka-test -n kafka 2>/dev/null
kubectl delete svc kafka-broker -n kafka 2>/dev/null
kubectl delete namespace kafka 2>/dev/null
```

## Notes

- CDC requires Enterprise license for production use
- Network egress must be allowed to Kafka/Pub/Sub
- Monitor changefeed lag in production
- Consider TLS and authentication for production sinks
- Changefeeds are resumable after failures

## Troubleshooting

### Changefeed Fails to Start

```bash
# Check job error
kubectl exec -n ${CRDB_CLUSTER_NS} cockroachdb-0 -- \
  cockroach sql --insecure --host=localhost:26257 \
  --execute="SELECT error FROM [SHOW JOBS] WHERE job_type = 'CHANGEFEED' AND status = 'failed' ORDER BY created DESC LIMIT 1;"

# Check network connectivity
kubectl exec -n ${CRDB_CLUSTER_NS} cockroachdb-0 -- \
  nc -zv ${KAFKA_BROKER} 2>&1
```

### Messages Not Appearing in Kafka

```bash
# Check changefeed is running
kubectl exec -n ${CRDB_CLUSTER_NS} cockroachdb-0 -- \
  cockroach sql --insecure --host=localhost:26257 \
  --execute="SELECT status, high_water_timestamp FROM [SHOW JOBS] WHERE job_type = 'CHANGEFEED' ORDER BY created DESC LIMIT 1;"

# Check Kafka topic exists
kubectl exec -n kafka kafka-test -- \
  kafka-topics.sh --bootstrap-server localhost:9092 --list
```

### High Changefeed Lag

```bash
# Check changefeed metrics
kubectl exec -n ${CRDB_CLUSTER_NS} cockroachdb-0 -- \
  curl -s http://localhost:8080/_status/vars | grep changefeed

# Check cluster load
kubectl exec -n ${CRDB_CLUSTER_NS} cockroachdb-0 -- \
  cockroach sql --insecure --host=localhost:26257 \
  --execute="SELECT * FROM crdb_internal.node_metrics WHERE name LIKE '%changefeed%';"
```
