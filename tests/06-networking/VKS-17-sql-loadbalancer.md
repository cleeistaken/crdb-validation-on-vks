# VKS-17: SQL Service Exposure via Service Type LoadBalancer (L4)

## Test Information

| Field | Value |
|-------|-------|
| **Test ID** | VKS-17 |
| **Category** | Networking & Access |
| **Dependencies** | [VKS-02](../01-cluster-provisioning/VKS-02-cluster-install.md) |

## Objective

Create a Service type LoadBalancer for CockroachDB SQL access (port 26257), verify client connectivity from inside and outside the cluster, test connection stability across pod restarts, and document the L4 load balancer behavior.

## Pre-requisites

- VKS-02 completed (CockroachDB cluster running)
- L4 load balancer implementation available (NSX LB or NSX ALB/Avi)
- Network/firewall rules allow client access to port 26257
- `kubectl` configured with VKS cluster kubeconfig

## Environment Setup

```bash
# Ensure KUBECONFIG points to VKS cluster
export KUBECONFIG=$(pwd)/vks-kubeconfig.yaml

# Set environment variables
export CRDB_CLUSTER_NS="crdb-cluster"

# Verify cluster is running
kubectl get pods -n ${CRDB_CLUSTER_NS}
kubectl get svc -n ${CRDB_CLUSTER_NS}
```

## Steps

### Step 1: Check Current SQL Service

```bash
# Check existing services
kubectl get svc -n ${CRDB_CLUSTER_NS}

# Check cockroachdb-public service type
kubectl get svc cockroachdb-public -n ${CRDB_CLUSTER_NS} -o yaml | grep -A 5 "spec:"
```

### Step 2: Create LoadBalancer Service for SQL

```bash
# Create a LoadBalancer service for SQL access
cat <<EOF | kubectl apply -f -
apiVersion: v1
kind: Service
metadata:
  name: cockroachdb-sql-lb
  namespace: ${CRDB_CLUSTER_NS}
  labels:
    app.kubernetes.io/name: cockroachdb
    app.kubernetes.io/component: sql-lb
  annotations:
    # NSX-specific annotations (adjust as needed)
    # nsx.vmware.com/load-balancer-class: "default"
spec:
  type: LoadBalancer
  selector:
    app.kubernetes.io/name: cockroachdb
  ports:
  - name: sql
    port: 26257
    targetPort: 26257
    protocol: TCP
  sessionAffinity: None
EOF

# Wait for LoadBalancer IP assignment
echo "Waiting for LoadBalancer IP..."
for i in {1..30}; do
  LB_IP=$(kubectl get svc cockroachdb-sql-lb -n ${CRDB_CLUSTER_NS} -o jsonpath='{.status.loadBalancer.ingress[0].ip}' 2>/dev/null)
  if [ -n "$LB_IP" ]; then
    echo "LoadBalancer IP: ${LB_IP}"
    break
  fi
  echo "Waiting... ($i/30)"
  sleep 5
done
```

### Step 3: Get LoadBalancer Details

```bash
# Get LoadBalancer IP/DNS
LB_IP=$(kubectl get svc cockroachdb-sql-lb -n ${CRDB_CLUSTER_NS} -o jsonpath='{.status.loadBalancer.ingress[0].ip}')
if [ -z "$LB_IP" ]; then
  LB_IP=$(kubectl get svc cockroachdb-sql-lb -n ${CRDB_CLUSTER_NS} -o jsonpath='{.status.loadBalancer.ingress[0].hostname}')
fi
echo "SQL LoadBalancer endpoint: ${LB_IP}:26257"

# Check service details
kubectl describe svc cockroachdb-sql-lb -n ${CRDB_CLUSTER_NS}
```

### Step 4: Test SQL Connection from Inside Cluster

```bash
# Create a client pod inside the cluster
kubectl run sql-client-internal -n ${CRDB_CLUSTER_NS} \
  --image=cockroachdb/cockroach:latest \
  --restart=Never \
  -- sleep 3600

# Wait for pod
kubectl wait --for=condition=Ready pod/sql-client-internal -n ${CRDB_CLUSTER_NS} --timeout=60s

# Test connection via LoadBalancer IP
kubectl exec -n ${CRDB_CLUSTER_NS} sql-client-internal -- \
  cockroach sql --insecure --host=${LB_IP}:26257 \
  --execute="SELECT 'Internal LB connection works' as result;"

# Test connection via LoadBalancer DNS (if available)
kubectl exec -n ${CRDB_CLUSTER_NS} sql-client-internal -- \
  cockroach sql --insecure --host=cockroachdb-sql-lb:26257 \
  --execute="SELECT 'Internal service DNS works' as result;"
```

### Step 5: Test SQL Connection from External Network (If Applicable)

```bash
# If you have access from an external network, test from your workstation
# Note: This requires network connectivity to the LoadBalancer IP

echo "To test from external network, run:"
echo "cockroach sql --insecure --host=${LB_IP}:26257 --execute=\"SELECT 1;\""

# Or using psql:
echo "psql \"postgresql://root@${LB_IP}:26257/defaultdb?sslmode=disable\" -c \"SELECT 1;\""
```

### Step 6: Run Basic SQL Operations

```bash
# Test various SQL operations via LoadBalancer
kubectl exec -n ${CRDB_CLUSTER_NS} sql-client-internal -- \
  cockroach sql --insecure --host=${LB_IP}:26257 \
  --execute="
    -- Test database operations
    CREATE DATABASE IF NOT EXISTS lb_test;
    USE lb_test;
    
    -- Create table
    CREATE TABLE IF NOT EXISTS connection_test (
      id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
      client_addr STRING,
      server_node INT DEFAULT crdb_internal.node_id(),
      created_at TIMESTAMP DEFAULT now()
    );
    
    -- Insert data
    INSERT INTO connection_test (client_addr) VALUES ('internal-client');
    
    -- Query data
    SELECT * FROM connection_test;
  "
```

### Step 7: Test Connection Stability Across Pod Restarts

```bash
# Start a continuous connection test in background
kubectl exec -n ${CRDB_CLUSTER_NS} sql-client-internal -- \
  /bin/bash -c "
    for i in \$(seq 1 60); do
      cockroach sql --insecure --host=${LB_IP}:26257 \
        --execute=\"INSERT INTO lb_test.connection_test (client_addr) VALUES ('stability-test-\$i');\" 2>&1 || echo 'Connection failed at \$i'
      sleep 2
    done
  " &
TEST_PID=$!

# Wait a bit then restart a pod
sleep 10
echo "Restarting cockroachdb-1..."
kubectl delete pod cockroachdb-1 -n ${CRDB_CLUSTER_NS}

# Wait for pod to come back
kubectl wait --for=condition=Ready pod/cockroachdb-1 -n ${CRDB_CLUSTER_NS} --timeout=120s

# Wait for test to complete
wait $TEST_PID 2>/dev/null

# Check results
kubectl exec -n ${CRDB_CLUSTER_NS} sql-client-internal -- \
  cockroach sql --insecure --host=${LB_IP}:26257 \
  --execute="
    SELECT count(*) as total_inserts FROM lb_test.connection_test WHERE client_addr LIKE 'stability-test-%';
  "
```

**Expected Output:**
- Most or all 60 inserts should succeed
- Brief connection interruptions during pod restart are expected (with reconnect)

### Step 8: Verify Load Distribution

```bash
# Check which nodes are receiving connections
kubectl exec -n ${CRDB_CLUSTER_NS} sql-client-internal -- \
  cockroach sql --insecure --host=${LB_IP}:26257 \
  --execute="
    -- Insert multiple rows to see distribution
    INSERT INTO lb_test.connection_test (client_addr) 
    SELECT 'distribution-test' FROM generate_series(1, 30);
    
    -- Check distribution across nodes
    SELECT server_node, count(*) as connections 
    FROM lb_test.connection_test 
    WHERE client_addr = 'distribution-test'
    GROUP BY server_node 
    ORDER BY server_node;
  "
```

### Step 9: Document LoadBalancer Configuration

```bash
# Get LoadBalancer details for documentation
echo "=== LoadBalancer Configuration ==="
kubectl get svc cockroachdb-sql-lb -n ${CRDB_CLUSTER_NS} -o yaml

# Check for any platform-specific annotations
echo -e "\n=== Annotations ==="
kubectl get svc cockroachdb-sql-lb -n ${CRDB_CLUSTER_NS} -o jsonpath='{.metadata.annotations}'
echo ""

# Check endpoints
echo -e "\n=== Endpoints ==="
kubectl get endpoints cockroachdb-sql-lb -n ${CRDB_CLUSTER_NS}

# Check health check configuration (if visible)
echo -e "\n=== Service Events ==="
kubectl get events -n ${CRDB_CLUSTER_NS} --field-selector involvedObject.name=cockroachdb-sql-lb --sort-by='.lastTimestamp'
```

### Step 10: Test Idle Timeout Behavior (Optional)

```bash
# Test connection behavior after idle period
kubectl exec -n ${CRDB_CLUSTER_NS} sql-client-internal -- \
  /bin/bash -c "
    echo 'Opening connection...'
    cockroach sql --insecure --host=${LB_IP}:26257 \
      --execute=\"SELECT 'Connection opened' as status;\"
    
    echo 'Sleeping for 60 seconds...'
    sleep 60
    
    echo 'Testing connection after idle...'
    cockroach sql --insecure --host=${LB_IP}:26257 \
      --execute=\"SELECT 'Connection after idle' as status;\"
  "
```

## Validation Commands

```bash
# Complete validation script
echo "=== SQL LoadBalancer Validation ==="

# Get LB IP
LB_IP=$(kubectl get svc cockroachdb-sql-lb -n ${CRDB_CLUSTER_NS} -o jsonpath='{.status.loadBalancer.ingress[0].ip}')

echo -e "\n1. LoadBalancer service:"
kubectl get svc cockroachdb-sql-lb -n ${CRDB_CLUSTER_NS}

echo -e "\n2. LoadBalancer IP: ${LB_IP}"

echo -e "\n3. Internal connection test:"
kubectl exec -n ${CRDB_CLUSTER_NS} sql-client-internal -- \
  cockroach sql --insecure --host=${LB_IP}:26257 \
  --execute="SELECT 'LB connection OK' as status;"

echo -e "\n4. Endpoints:"
kubectl get endpoints cockroachdb-sql-lb -n ${CRDB_CLUSTER_NS}

echo -e "\n5. Data integrity:"
kubectl exec -n ${CRDB_CLUSTER_NS} sql-client-internal -- \
  cockroach sql --insecure --host=${LB_IP}:26257 \
  --execute="SELECT count(*) as rows FROM lb_test.connection_test;"

echo -e "\n=== Validation Complete ==="
```

## Expected Results

| Check | Expected State |
|-------|----------------|
| LoadBalancer IP | Assigned and accessible |
| Internal connection | SQL queries succeed |
| External connection | SQL queries succeed (if network allows) |
| Pod restart | Connections recover with reconnect |
| Load distribution | Connections spread across nodes |
| Idle timeout | Connections survive reasonable idle periods |

## Cleanup

```bash
# Remove test client pod
kubectl delete pod sql-client-internal -n ${CRDB_CLUSTER_NS}

# Remove test database
kubectl exec -n ${CRDB_CLUSTER_NS} cockroachdb-0 -- \
  cockroach sql --insecure --host=localhost:26257 \
  --execute="DROP DATABASE IF EXISTS lb_test CASCADE;"

# Remove LoadBalancer service (optional - keep for production use)
# kubectl delete svc cockroachdb-sql-lb -n ${CRDB_CLUSTER_NS}
```

## Notes

- This is the key enterprise access pattern for CockroachDB on VKS
- Document the LB type (NSX vs Avi), IP management, and health check behavior
- Note any idle timeout settings that may affect long-running connections
- SQL traffic uses TCP (L4), not HTTP (L7)
- Connection pooling is recommended for production applications

## Troubleshooting

### LoadBalancer IP Not Assigned

```bash
# Check LoadBalancer controller
kubectl get pods -A | grep -E "(metallb|nsx|avi|cloud-controller)"

# Check events
kubectl get events -n ${CRDB_CLUSTER_NS} --sort-by='.lastTimestamp' | grep -i loadbalancer

# Check service status
kubectl describe svc cockroachdb-sql-lb -n ${CRDB_CLUSTER_NS}
```

### Connection Refused

```bash
# Check endpoints
kubectl get endpoints cockroachdb-sql-lb -n ${CRDB_CLUSTER_NS}

# Check if pods are ready
kubectl get pods -n ${CRDB_CLUSTER_NS}

# Test direct pod connection
kubectl exec -n ${CRDB_CLUSTER_NS} cockroachdb-0 -- \
  cockroach sql --insecure --host=localhost:26257 \
  --execute="SELECT 1;"
```

### Connection Timeouts

```bash
# Check network policies
kubectl get networkpolicies -n ${CRDB_CLUSTER_NS}

# Check firewall rules (platform-specific)
# For NSX, check DFW rules

# Test from different network locations
kubectl run nettest --rm -it --image=busybox --restart=Never -- \
  nc -zv ${LB_IP} 26257
```
