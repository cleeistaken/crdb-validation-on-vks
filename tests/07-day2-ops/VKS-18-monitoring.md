# VKS-18: Cluster Monitoring Integration

## Test Information

| Field | Value |
|-------|-------|
| **Test ID** | VKS-18 |
| **Category** | Day-2 Ops |
| **Dependencies** | [VKS-02](../01-cluster-provisioning/VKS-02-cluster-install.md) |

## Objective

Configure monitoring for the CockroachDB cluster, verify metrics endpoints are reachable from Prometheus, and validate key metrics on dashboards.

## Pre-requisites

- VKS-02 completed (CockroachDB cluster running)
- Monitoring stack available (Prometheus, or VKS Monitoring)
- `kubectl` configured with VKS cluster kubeconfig

## Environment Setup

```bash
# Ensure KUBECONFIG points to VKS cluster
export KUBECONFIG=$(pwd)/vks-kubeconfig.yaml

# Set environment variables
export CRDB_CLUSTER_NS="crdb-cluster"
export MONITORING_NS="monitoring"  # Adjust based on your setup

# Verify cluster is running
kubectl get pods -n ${CRDB_CLUSTER_NS}
```

## Steps

### Step 1: Verify CockroachDB Metrics Endpoint

```bash
# Check metrics endpoint on a pod
kubectl exec -n ${CRDB_CLUSTER_NS} cockroachdb-0 -- \
  curl -s http://localhost:8080/_status/vars | head -30

# Check Prometheus-format metrics
kubectl exec -n ${CRDB_CLUSTER_NS} cockroachdb-0 -- \
  curl -s http://localhost:8080/_status/vars | grep -E "^[a-z]" | head -20
```

### Step 2: Create ServiceMonitor (For Prometheus Operator)

```bash
# Check if Prometheus Operator is installed
kubectl get crd servicemonitors.monitoring.coreos.com 2>/dev/null && echo "Prometheus Operator available"

# Create ServiceMonitor for CockroachDB
cat <<EOF | kubectl apply -f -
apiVersion: monitoring.coreos.com/v1
kind: ServiceMonitor
metadata:
  name: cockroachdb
  namespace: ${CRDB_CLUSTER_NS}
  labels:
    app.kubernetes.io/name: cockroachdb
spec:
  selector:
    matchLabels:
      app.kubernetes.io/name: cockroachdb
  endpoints:
  - port: http
    path: /_status/vars
    interval: 30s
    scheme: http
  namespaceSelector:
    matchNames:
    - ${CRDB_CLUSTER_NS}
EOF

# Verify ServiceMonitor created
kubectl get servicemonitor -n ${CRDB_CLUSTER_NS}
```

### Step 3: Alternative - Create Prometheus Scrape Config

If not using Prometheus Operator, create a ConfigMap for Prometheus:

```bash
# Example Prometheus scrape config
cat <<EOF
# Add to prometheus.yml
scrape_configs:
  - job_name: 'cockroachdb'
    kubernetes_sd_configs:
      - role: pod
        namespaces:
          names:
            - ${CRDB_CLUSTER_NS}
    relabel_configs:
      - source_labels: [__meta_kubernetes_pod_label_app_kubernetes_io_name]
        action: keep
        regex: cockroachdb
      - source_labels: [__meta_kubernetes_pod_container_port_name]
        action: keep
        regex: http
    metrics_path: /_status/vars
    scheme: http
EOF
```

### Step 4: Verify Prometheus Can Scrape Metrics

```bash
# Check if Prometheus is running
kubectl get pods -n ${MONITORING_NS} -l app=prometheus 2>/dev/null || \
kubectl get pods -A | grep prometheus

# Port-forward to Prometheus (adjust namespace/service as needed)
kubectl port-forward -n ${MONITORING_NS} svc/prometheus 9090:9090 &
PF_PID=$!
sleep 3

# Check targets
curl -s http://localhost:9090/api/v1/targets | grep -o '"job":"cockroachdb"' && echo "CockroachDB target found"

# Query a metric
curl -s 'http://localhost:9090/api/v1/query?query=sql_query_count' | head -50

kill $PF_PID 2>/dev/null
```

### Step 5: Verify Key CockroachDB Metrics

```bash
# List available metrics
kubectl exec -n ${CRDB_CLUSTER_NS} cockroachdb-0 -- \
  curl -s http://localhost:8080/_status/vars | grep -E "^[a-z_]+" | cut -d'{' -f1 | sort -u | head -50

# Check specific important metrics
echo "=== Key Metrics ==="

# SQL metrics
kubectl exec -n ${CRDB_CLUSTER_NS} cockroachdb-0 -- \
  curl -s http://localhost:8080/_status/vars | grep -E "^sql_"

# Storage metrics
kubectl exec -n ${CRDB_CLUSTER_NS} cockroachdb-0 -- \
  curl -s http://localhost:8080/_status/vars | grep -E "^(capacity|livebytes|sysbytes)"

# Replication metrics
kubectl exec -n ${CRDB_CLUSTER_NS} cockroachdb-0 -- \
  curl -s http://localhost:8080/_status/vars | grep -E "^(replicas|ranges)"
```

### Step 6: Create Grafana Dashboard (Optional)

```bash
# CockroachDB provides official Grafana dashboards
# Download from: https://github.com/cockroachdb/cockroach/tree/master/monitoring/grafana-dashboards

# Example: Create a ConfigMap with dashboard JSON
cat <<'EOF' | kubectl apply -f -
apiVersion: v1
kind: ConfigMap
metadata:
  name: cockroachdb-dashboard
  namespace: ${MONITORING_NS}
  labels:
    grafana_dashboard: "1"
data:
  cockroachdb-overview.json: |
    {
      "title": "CockroachDB Overview",
      "panels": [
        {
          "title": "SQL Query Rate",
          "type": "graph",
          "targets": [
            {
              "expr": "rate(sql_query_count[5m])",
              "legendFormat": "{{instance}}"
            }
          ]
        },
        {
          "title": "Live Nodes",
          "type": "stat",
          "targets": [
            {
              "expr": "count(up{job=\"cockroachdb\"} == 1)"
            }
          ]
        }
      ]
    }
EOF
```

### Step 7: Set Up Alerting Rules (Optional)

```bash
# Create PrometheusRule for CockroachDB alerts
cat <<EOF | kubectl apply -f -
apiVersion: monitoring.coreos.com/v1
kind: PrometheusRule
metadata:
  name: cockroachdb-alerts
  namespace: ${CRDB_CLUSTER_NS}
  labels:
    app.kubernetes.io/name: cockroachdb
spec:
  groups:
  - name: cockroachdb
    rules:
    - alert: CockroachDBNodeDown
      expr: up{job="cockroachdb"} == 0
      for: 5m
      labels:
        severity: critical
      annotations:
        summary: "CockroachDB node {{ \$labels.instance }} is down"
        
    - alert: CockroachDBUnderReplicated
      expr: ranges_underreplicated > 0
      for: 10m
      labels:
        severity: warning
      annotations:
        summary: "CockroachDB has under-replicated ranges"
        
    - alert: CockroachDBHighLatency
      expr: histogram_quantile(0.99, rate(sql_exec_latency_bucket[5m])) > 1
      for: 5m
      labels:
        severity: warning
      annotations:
        summary: "CockroachDB SQL latency is high"
EOF

# Verify rule created
kubectl get prometheusrule -n ${CRDB_CLUSTER_NS}
```

### Step 8: Verify Operator Metrics (If Available)

```bash
# Check if operator exposes metrics
kubectl get svc -n crdb-operator | grep metrics

# If metrics service exists
kubectl exec -n crdb-operator -l app.kubernetes.io/name=cockroach-operator -- \
  curl -s http://localhost:8080/metrics 2>/dev/null | head -20 || echo "Operator metrics not exposed via curl"
```

### Step 9: Test Metrics Under Load

```bash
# Generate some load
kubectl exec -n ${CRDB_CLUSTER_NS} cockroachdb-0 -- \
  cockroach workload run kv \
  --duration=60s \
  --concurrency=4 \
  'postgresql://root@localhost:26257/defaultdb?sslmode=disable' &

# While load is running, check metrics
sleep 10
kubectl exec -n ${CRDB_CLUSTER_NS} cockroachdb-0 -- \
  curl -s http://localhost:8080/_status/vars | grep -E "^sql_query_count"

# Wait for workload to complete
wait
```

## Validation Commands

```bash
# Complete validation script
echo "=== Monitoring Integration Validation ==="

echo -e "\n1. Metrics endpoint accessible:"
kubectl exec -n ${CRDB_CLUSTER_NS} cockroachdb-0 -- \
  curl -s http://localhost:8080/_status/vars | head -5

echo -e "\n2. ServiceMonitor (if using Prometheus Operator):"
kubectl get servicemonitor -n ${CRDB_CLUSTER_NS} 2>/dev/null || echo "Not using ServiceMonitor"

echo -e "\n3. Key metrics available:"
kubectl exec -n ${CRDB_CLUSTER_NS} cockroachdb-0 -- \
  curl -s http://localhost:8080/_status/vars | grep -c "^[a-z]"
echo "metrics available"

echo -e "\n4. SQL metrics:"
kubectl exec -n ${CRDB_CLUSTER_NS} cockroachdb-0 -- \
  curl -s http://localhost:8080/_status/vars | grep "sql_query_count" | head -1

echo -e "\n5. Storage metrics:"
kubectl exec -n ${CRDB_CLUSTER_NS} cockroachdb-0 -- \
  curl -s http://localhost:8080/_status/vars | grep "capacity" | head -3

echo -e "\n=== Validation Complete ==="
```

## Expected Results

| Check | Expected State |
|-------|----------------|
| Metrics endpoint | Accessible on :8080/_status/vars |
| ServiceMonitor | Created (if using Prometheus Operator) |
| Prometheus scraping | CockroachDB target discovered |
| Key metrics | sql_*, capacity, ranges_* available |
| Alerting rules | Created (optional) |

## Cleanup

```bash
# Remove ServiceMonitor (optional)
kubectl delete servicemonitor cockroachdb -n ${CRDB_CLUSTER_NS} 2>/dev/null

# Remove PrometheusRule (optional)
kubectl delete prometheusrule cockroachdb-alerts -n ${CRDB_CLUSTER_NS} 2>/dev/null

# Remove dashboard ConfigMap (optional)
kubectl delete configmap cockroachdb-dashboard -n ${MONITORING_NS} 2>/dev/null
```

## Notes

- CockroachDB exposes Prometheus-compatible metrics on port 8080
- The metrics endpoint is `/_status/vars`
- Key metrics to monitor: sql_query_count, ranges_underreplicated, capacity, livebytes
- For production, set up alerting for node down and under-replication
- Consider using CockroachDB's official Grafana dashboards

## Troubleshooting

### Metrics Not Accessible

```bash
# Check pod is running
kubectl get pods -n ${CRDB_CLUSTER_NS}

# Check port 8080 is exposed
kubectl get svc -n ${CRDB_CLUSTER_NS} -o yaml | grep 8080

# Test direct access
kubectl port-forward -n ${CRDB_CLUSTER_NS} pod/cockroachdb-0 8080:8080 &
curl http://localhost:8080/_status/vars | head -10
```

### Prometheus Not Scraping

```bash
# Check ServiceMonitor selector matches
kubectl get svc -n ${CRDB_CLUSTER_NS} --show-labels
kubectl get servicemonitor -n ${CRDB_CLUSTER_NS} -o yaml | grep -A 5 selector

# Check Prometheus configuration
kubectl get secret -n ${MONITORING_NS} prometheus-prometheus -o jsonpath='{.data.prometheus\.yaml\.gz}' | base64 -d | gunzip | grep cockroach
```

### Missing Metrics

```bash
# List all available metrics
kubectl exec -n ${CRDB_CLUSTER_NS} cockroachdb-0 -- \
  curl -s http://localhost:8080/_status/vars | grep -E "^[a-z_]+" | wc -l

# Search for specific metric
kubectl exec -n ${CRDB_CLUSTER_NS} cockroachdb-0 -- \
  curl -s http://localhost:8080/_status/vars | grep "your_metric_name"
```
