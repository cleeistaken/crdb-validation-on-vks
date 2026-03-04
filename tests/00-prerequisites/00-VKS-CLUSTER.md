# 00-VKS-CLUSTER: Deploy VKS Kubernetes Cluster

## Test Information

| Field | Value |
|-------|-------|
| **Test ID** | 00-VKS-CLUSTER |
| **Category** | Prerequisites |
| **Dependencies** | None |

## Pre-requisites

- VMware WCP 3.6.0 environment available
- `kubectl` CLI installed and configured to access the Supervisor cluster
- For VCF 9.0+: `vcf` CLI installed (see [VCF CLI documentation](https://techdocs.broadcom.com/us/en/vmware-cis/vcf/vcf-9-0-and-later/9-0/building-your-cloud-applications/getting-started-with-the-tools-for-building-applications/installing-and-using-vcf-cli-v9.html))
- Access to a vSphere Namespace with appropriate permissions
- Storage class `vsan-esa-default-policy-raid5` available

## Authentication

Before running this test, authenticate to the Supervisor cluster:

```bash
# VCF 9.0+ (using vcf CLI):
vcf context create <SUPERVISOR_CONTEXT> \
    --endpoint <SUPERVISOR_IP> \
    --username <USERNAME>

# Legacy method (vSphere 8.x / VCF 5.x):
kubectl vsphere login --server=<SUPERVISOR_IP> \
    --vsphere-username=<USERNAME> \
    --tanzu-kubernetes-cluster-namespace=<SUPERVISOR_NAMESPACE>

# Verify authentication
kubectl config get-contexts
```

## Environment Setup

```bash
# Set environment variables
export VKS_CLUSTER_NAME="cluster-vks"
export VSPHERE_NAMESPACE="your-vsphere-namespace"  # Update with your namespace
```

## Steps

### Step 1: Verify Supervisor Cluster Access

```bash
# Verify kubectl context is set to Supervisor cluster
kubectl config current-context

# List available vSphere namespaces
kubectl get namespaces
```

**Expected Output:**
- Current context shows Supervisor cluster
- Your vSphere namespace is listed

### Step 2: Verify Available Resources

```bash
# Check available VM classes
kubectl get virtualmachineclasses

# Check available storage classes
kubectl get storageclasses

# Verify the required storage class exists
kubectl get storageclass vsan-esa-default-policy-raid5
```

**Expected Output:**
- `best-effort-medium` and `best-effort-large` VM classes available
- `vsan-esa-default-policy-raid5` storage class exists

### Step 3: Review VKS Cluster Manifest

```bash
# Review the VKS cluster configuration
cat vks.yaml
```

The manifest creates:
- 1 control plane node (best-effort-medium)
- 3 worker node pools (best-effort-large), one per rack/region
- 100Gi containerd volume per worker node
- Kubernetes v1.35.0+vmware.2-vkr.4

### Step 4: Deploy VKS Cluster

```bash
# Apply the VKS cluster manifest
kubectl apply -f vks.yaml
```

**Expected Output:**
```
cluster.cluster.x-k8s.io/cluster-vks created
```

### Step 5: Monitor Cluster Provisioning

```bash
# Watch cluster status
kubectl get cluster ${VKS_CLUSTER_NAME} -w

# Check cluster phases (run in separate terminal or after cluster shows Provisioned)
kubectl get cluster ${VKS_CLUSTER_NAME} -o jsonpath='{.status.phase}'
```

**Expected Output:**
- Cluster phase progresses: `Pending` → `Provisioning` → `Provisioned`

### Step 6: Verify Machine Deployments

```bash
# Check machine deployments
kubectl get machinedeployments

# Check machines
kubectl get machines
```

**Expected Output:**
- 3 machine deployments (node-pool-1, node-pool-2, node-pool-3)
- 4 machines total (1 control plane + 3 workers)
- All machines in `Running` phase

### Step 7: Retrieve VKS Cluster Kubeconfig

```bash
# Get the kubeconfig secret
kubectl get secret ${VKS_CLUSTER_NAME}-kubeconfig -o jsonpath='{.data.value}' | base64 -d > vks-kubeconfig.yaml

# Set KUBECONFIG environment variable
export KUBECONFIG=$(pwd)/vks-kubeconfig.yaml

# Verify access to VKS cluster
kubectl cluster-info
```

**Expected Output:**
```
Kubernetes control plane is running at https://<VKS-API-SERVER>:6443
CoreDNS is running at https://<VKS-API-SERVER>:6443/api/v1/namespaces/kube-system/services/kube-dns:dns/proxy
```

### Step 8: Verify VKS Cluster Nodes

```bash
# List all nodes
kubectl get nodes -o wide

# Verify node labels for topology
kubectl get nodes --show-labels | grep topology.kubernetes.io/region
```

**Expected Output:**
- 4 nodes total (1 control plane + 3 workers)
- All nodes in `Ready` status
- Worker nodes labeled with `topology.kubernetes.io/region=rack1`, `rack2`, `rack3`

### Step 9: Verify Storage Class in VKS Cluster

```bash
# Check storage classes available in VKS cluster
kubectl get storageclasses

# Verify default storage class
kubectl get storageclass vsan-esa-default-policy-raid5 -o yaml
```

**Expected Output:**
- `vsan-esa-default-policy-raid5` storage class available
- Marked as default storage class

## Validation Commands

```bash
# Full cluster health check
kubectl get nodes
kubectl get pods -A
kubectl top nodes  # If metrics-server is available

# Verify cluster networking
kubectl run test-pod --image=busybox --rm -it --restart=Never -- wget -qO- kubernetes.default.svc.cluster.local
```

## Expected Results

| Check | Expected State |
|-------|----------------|
| Cluster phase | `Provisioned` |
| Control plane nodes | 1 node, `Ready` |
| Worker nodes | 3 nodes, `Ready` |
| Node topology labels | rack1, rack2, rack3 |
| Storage class | `vsan-esa-default-policy-raid5` available |
| Cluster networking | Functional |

## Cleanup (Optional)

Only run cleanup if you need to tear down the entire test environment:

```bash
# Switch back to Supervisor context
unset KUBECONFIG

# Delete VKS cluster
kubectl delete -f vks.yaml

# Verify deletion
kubectl get cluster ${VKS_CLUSTER_NAME}
```

## Notes

- VKS cluster provisioning typically takes 10-15 minutes
- The cluster uses Ubuntu 24.04 as the node OS
- Each worker node has a dedicated 100Gi volume for containerd
- The topology labels (rack1, rack2, rack3) will be used for CockroachDB locality mappings
- Keep the `vks-kubeconfig.yaml` file secure as it provides full cluster access

## Troubleshooting

### Cluster stuck in Provisioning

```bash
# Check cluster conditions
kubectl get cluster ${VKS_CLUSTER_NAME} -o yaml | grep -A 20 "status:"

# Check machine status
kubectl get machines -o wide

# Check events
kubectl get events --sort-by='.lastTimestamp'
```

### Node not Ready

```bash
# Describe the problematic node
kubectl describe node <node-name>

# Check kubelet logs (from Supervisor)
kubectl logs -n <vsphere-namespace> <machine-pod-name>
```
