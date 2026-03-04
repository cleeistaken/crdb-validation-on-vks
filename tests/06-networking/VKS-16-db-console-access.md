# VKS-16: Expose DB Console (Admin UI) for Operators

## Test Information

| Field | Value |
|-------|-------|
| **Test ID** | VKS-16 |
| **Category** | Networking & Access |
| **Dependencies** | [VKS-02](../01-cluster-provisioning/VKS-02-cluster-install.md) |

## Objective

Configure access to the CockroachDB Admin UI (DB Console) on port 8080 using one of the supported methods: port-forward, Ingress, or LoadBalancer. Verify access and confirm SQL traffic remains on L4 (not routed through HTTP Ingress).

## Pre-requisites

- VKS-02 completed (CockroachDB cluster running)
- Network path available from ops workstation or bastion
- `kubectl` configured with VKS cluster kubeconfig
- For Ingress: Ingress controller available (e.g., Contour, NGINX)
- For LoadBalancer: L4 load balancer available (NSX LB or NSX ALB/Avi)

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

## Method 1: Port-Forward (Development/Testing)

### Step 1.1: Start Port-Forward

```bash
# Port-forward to the Admin UI service
kubectl port-forward svc/cockroachdb-public -n ${CRDB_CLUSTER_NS} 8080:8080 &
PF_PID=$!
echo "Port-forward PID: ${PF_PID}"

# Wait for port-forward to establish
sleep 3
```

### Step 1.2: Verify Access

```bash
# Test health endpoint
curl -s http://localhost:8080/health
echo ""

# Test cluster overview endpoint
curl -s http://localhost:8080/_status/vars | head -10

# Test nodes endpoint
curl -s http://localhost:8080/_status/nodes | head -20
```

**Expected Output:**
- Health returns OK or JSON status
- Metrics and node information accessible

### Step 1.3: Access via Browser

```bash
echo "Open browser to: http://localhost:8080"
echo "You should see the CockroachDB DB Console"
```

### Step 1.4: Stop Port-Forward

```bash
kill $PF_PID 2>/dev/null
echo "Port-forward stopped"
```

## Method 2: Service Type LoadBalancer (Recommended for Ops Access)

### Step 2.1: Create LoadBalancer Service for Admin UI

```bash
# Create a dedicated LoadBalancer service for the Admin UI
cat <<EOF | kubectl apply -f -
apiVersion: v1
kind: Service
metadata:
  name: cockroachdb-admin-ui-lb
  namespace: ${CRDB_CLUSTER_NS}
  labels:
    app.kubernetes.io/name: cockroachdb
    app.kubernetes.io/component: admin-ui
spec:
  type: LoadBalancer
  selector:
    app.kubernetes.io/name: cockroachdb
  ports:
  - name: http
    port: 8080
    targetPort: 8080
    protocol: TCP
EOF

# Wait for LoadBalancer IP
echo "Waiting for LoadBalancer IP..."
kubectl get svc cockroachdb-admin-ui-lb -n ${CRDB_CLUSTER_NS} -w &
WATCH_PID=$!
sleep 30
kill $WATCH_PID 2>/dev/null
```

### Step 2.2: Get LoadBalancer IP

```bash
# Get the external IP
LB_IP=$(kubectl get svc cockroachdb-admin-ui-lb -n ${CRDB_CLUSTER_NS} -o jsonpath='{.status.loadBalancer.ingress[0].ip}')
echo "Admin UI LoadBalancer IP: ${LB_IP}"

# If IP is empty, check for hostname
if [ -z "$LB_IP" ]; then
  LB_IP=$(kubectl get svc cockroachdb-admin-ui-lb -n ${CRDB_CLUSTER_NS} -o jsonpath='{.status.loadBalancer.ingress[0].hostname}')
  echo "Admin UI LoadBalancer Hostname: ${LB_IP}"
fi
```

### Step 2.3: Verify Access via LoadBalancer

```bash
# Test health endpoint
curl -s http://${LB_IP}:8080/health
echo ""

# Test cluster status
curl -s http://${LB_IP}:8080/_status/vars | head -10

echo "Access Admin UI at: http://${LB_IP}:8080"
```

## Method 3: Ingress (HTTP Only - for Admin UI)

### Step 3.1: Check Ingress Controller

```bash
# Verify Ingress controller is available
kubectl get ingressclass

# Check for existing Ingress controller pods
kubectl get pods -A | grep -E "(ingress|contour|nginx)"
```

### Step 3.2: Create Ingress for Admin UI

```bash
# Create Ingress resource for Admin UI
cat <<EOF | kubectl apply -f -
apiVersion: networking.k8s.io/v1
kind: Ingress
metadata:
  name: cockroachdb-admin-ui
  namespace: ${CRDB_CLUSTER_NS}
  annotations:
    # Adjust annotations based on your Ingress controller
    # For NGINX:
    # nginx.ingress.kubernetes.io/backend-protocol: "HTTP"
    # For Contour:
    # projectcontour.io/websocket-routes: "/"
spec:
  ingressClassName: contour  # Adjust to your Ingress class
  rules:
  - host: crdb-admin.example.com  # Update with your domain
    http:
      paths:
      - path: /
        pathType: Prefix
        backend:
          service:
            name: cockroachdb-public
            port:
              number: 8080
EOF

# Get Ingress status
kubectl get ingress cockroachdb-admin-ui -n ${CRDB_CLUSTER_NS}
```

### Step 3.3: Verify Ingress Access

```bash
# Get Ingress address
INGRESS_IP=$(kubectl get ingress cockroachdb-admin-ui -n ${CRDB_CLUSTER_NS} -o jsonpath='{.status.loadBalancer.ingress[0].ip}')
echo "Ingress IP: ${INGRESS_IP}"

# Test access (update hostname as needed)
curl -s -H "Host: crdb-admin.example.com" http://${INGRESS_IP}/health
```

## Verify SQL is NOT Routed Through HTTP Ingress

### Step 4.1: Confirm SQL Service Configuration

```bash
# Check SQL service (should be ClusterIP or LoadBalancer, NOT Ingress)
kubectl get svc -n ${CRDB_CLUSTER_NS}

# Verify SQL port is 26257
kubectl get svc cockroachdb-public -n ${CRDB_CLUSTER_NS} -o jsonpath='{.spec.ports[?(@.name=="grpc")].port}'
echo ""
```

### Step 4.2: Test SQL Connection (Not via Ingress)

```bash
# SQL should be accessed via direct service or L4 LoadBalancer
kubectl exec -n ${CRDB_CLUSTER_NS} cockroachdb-0 -- \
  cockroach sql --insecure --host=cockroachdb-public:26257 \
  --execute="SELECT 'SQL connection works' as result;"

# Verify SQL is on port 26257, not 80/443
echo "SQL traffic should use port 26257 via ClusterIP or L4 LoadBalancer"
echo "SQL traffic should NOT go through HTTP Ingress"
```

## Validation Commands

```bash
# Complete validation script
echo "=== DB Console Access Validation ==="

echo -e "\n1. Services available:"
kubectl get svc -n ${CRDB_CLUSTER_NS}

echo -e "\n2. Port-forward test:"
kubectl port-forward svc/cockroachdb-public -n ${CRDB_CLUSTER_NS} 8080:8080 &
PF_PID=$!
sleep 3
curl -s http://localhost:8080/health
kill $PF_PID 2>/dev/null

echo -e "\n3. LoadBalancer service (if created):"
kubectl get svc cockroachdb-admin-ui-lb -n ${CRDB_CLUSTER_NS} 2>/dev/null || echo "Not created"

echo -e "\n4. Ingress (if created):"
kubectl get ingress -n ${CRDB_CLUSTER_NS} 2>/dev/null || echo "Not created"

echo -e "\n5. SQL service (should be separate from HTTP):"
kubectl get svc cockroachdb-public -n ${CRDB_CLUSTER_NS} -o jsonpath='Ports: {.spec.ports[*].name}:{.spec.ports[*].port}'
echo ""

echo -e "\n=== Validation Complete ==="
```

## Expected Results

| Check | Expected State |
|-------|----------------|
| Port-forward | Admin UI accessible on localhost:8080 |
| LoadBalancer | External IP assigned (if created) |
| Ingress | Routes HTTP traffic to Admin UI (if created) |
| Health endpoint | Returns OK status |
| SQL traffic | NOT routed through HTTP Ingress |
| SQL port | 26257 via ClusterIP or L4 LB |

## Cleanup

```bash
# Remove LoadBalancer service (if created)
kubectl delete svc cockroachdb-admin-ui-lb -n ${CRDB_CLUSTER_NS} 2>/dev/null

# Remove Ingress (if created)
kubectl delete ingress cockroachdb-admin-ui -n ${CRDB_CLUSTER_NS} 2>/dev/null

# Kill any remaining port-forwards
pkill -f "port-forward.*cockroachdb" 2>/dev/null
```

## Notes

- This is an ops-access test; keep Admin UI exposure limited in production
- Use IP allowlists, authentication, and TLS for production Admin UI access
- SQL traffic should NEVER go through HTTP Ingress
- For TLS-enabled clusters, Admin UI may require HTTPS
- LoadBalancer is the recommended method for persistent ops access

## Security Considerations

```bash
# For production, consider:
# 1. IP allowlists on LoadBalancer
# 2. TLS termination
# 3. Authentication (CockroachDB Enterprise)
# 4. Network policies to restrict access

# Example: Add annotation for IP allowlist (NSX-specific)
# kubectl annotate svc cockroachdb-admin-ui-lb -n ${CRDB_CLUSTER_NS} \
#   "service.beta.kubernetes.io/load-balancer-source-ranges=10.0.0.0/8"
```

## Troubleshooting

### Port-Forward Not Working

```bash
# Check pod is running
kubectl get pods -n ${CRDB_CLUSTER_NS}

# Check service endpoints
kubectl get endpoints cockroachdb-public -n ${CRDB_CLUSTER_NS}

# Try direct pod port-forward
kubectl port-forward pod/cockroachdb-0 -n ${CRDB_CLUSTER_NS} 8080:8080
```

### LoadBalancer Pending

```bash
# Check events
kubectl get events -n ${CRDB_CLUSTER_NS} --sort-by='.lastTimestamp' | grep -i loadbalancer

# Check service status
kubectl describe svc cockroachdb-admin-ui-lb -n ${CRDB_CLUSTER_NS}

# Verify LoadBalancer controller is running
kubectl get pods -A | grep -E "(metallb|nsx|avi)"
```

### Ingress Not Working

```bash
# Check Ingress controller logs
kubectl logs -n projectcontour -l app=contour --tail=50 2>/dev/null || \
kubectl logs -n ingress-nginx -l app.kubernetes.io/name=ingress-nginx --tail=50

# Check Ingress status
kubectl describe ingress cockroachdb-admin-ui -n ${CRDB_CLUSTER_NS}
```
