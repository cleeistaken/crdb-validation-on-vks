# VKS-22: Encryption at Rest with Cloud KMS (If Applicable)

## Test Information

| Field | Value |
|-------|-------|
| **Test ID** | VKS-22 |
| **Category** | Advanced Features |
| **Dependencies** | [VKS-02](../01-cluster-provisioning/VKS-02-cluster-install.md) |

## Objective

Configure encryption at rest using a cloud KMS provider, verify the cluster starts successfully with KMS-backed encryption, and confirm that restarts and node replacements work without manual intervention.

## Pre-requisites

- VKS-02 completed (CockroachDB cluster running)
- VKS on cloud provider with KMS available (AWS KMS, GCP KMS, or Azure Key Vault)
- KMS key created and IAM/identity configured
- `kubectl` configured with VKS cluster kubeconfig

## Environment Setup

```bash
# Ensure KUBECONFIG points to VKS cluster
export KUBECONFIG=$(pwd)/vks-kubeconfig.yaml

# Set environment variables
export CRDB_CLUSTER_NS="crdb-cluster"
export CRDB_RELEASE_NAME="cockroachdb"
export CRDB_CHART_PATH="./cockroachdb-parent/charts/cockroachdb"

# KMS configuration (adjust based on your cloud provider)
# AWS KMS example:
export KMS_TYPE="aws-kms"
export KMS_KEY_ID="arn:aws:kms:us-east-1:123456789:key/your-key-id"

# GCP KMS example:
# export KMS_TYPE="gcp-kms"
# export KMS_KEY_ID="projects/your-project/locations/us-east1/keyRings/your-keyring/cryptoKeys/your-key"

# Azure Key Vault example:
# export KMS_TYPE="azure-kms"
# export KMS_KEY_ID="https://your-vault.vault.azure.net/keys/your-key/version"

# Verify cluster is running
kubectl get pods -n ${CRDB_CLUSTER_NS}
```

## Steps

### Step 1: Create KMS Credentials Secret

```bash
# For AWS KMS - create secret with credentials
cat <<EOF | kubectl apply -f -
apiVersion: v1
kind: Secret
metadata:
  name: kms-credentials
  namespace: ${CRDB_CLUSTER_NS}
type: Opaque
stringData:
  AWS_ACCESS_KEY_ID: "your-access-key"
  AWS_SECRET_ACCESS_KEY: "your-secret-key"
  AWS_REGION: "us-east-1"
EOF

# For GCP KMS - create secret with service account key
# kubectl create secret generic kms-credentials \
#   --namespace ${CRDB_CLUSTER_NS} \
#   --from-file=credentials.json=/path/to/service-account.json

# For Azure Key Vault - create secret with credentials
# kubectl create secret generic kms-credentials \
#   --namespace ${CRDB_CLUSTER_NS} \
#   --from-literal=AZURE_CLIENT_ID=xxx \
#   --from-literal=AZURE_CLIENT_SECRET=xxx \
#   --from-literal=AZURE_TENANT_ID=xxx

# Verify secret created
kubectl get secret kms-credentials -n ${CRDB_CLUSTER_NS}
```

### Step 2: Create KMS Configuration Values

```bash
# Create Helm values file for encryption configuration
cat > /tmp/encryption-values.yaml << EOF
# Encryption at rest configuration
conf:
  # Store key configuration
  store:
    encryption:
      key: "${KMS_KEY_ID}"
      old-key: "plain"

# Mount KMS credentials
statefulset:
  extraEnvFrom:
  - secretRef:
      name: kms-credentials
  
  # For GCP, mount service account
  # extraVolumes:
  # - name: kms-creds
  #   secret:
  #     secretName: kms-credentials
  # extraVolumeMounts:
  # - name: kms-creds
  #   mountPath: /var/secrets/google
  #   readOnly: true
  # extraEnv:
  # - name: GOOGLE_APPLICATION_CREDENTIALS
  #   value: /var/secrets/google/credentials.json
EOF

cat /tmp/encryption-values.yaml
```

### Step 3: Upgrade Cluster with Encryption

```bash
# Upgrade cluster with encryption enabled
helm upgrade ${CRDB_RELEASE_NAME} ${CRDB_CHART_PATH} \
  --namespace ${CRDB_CLUSTER_NS} \
  --reuse-values \
  -f /tmp/encryption-values.yaml \
  --timeout 15m

# Note: This will trigger a rolling restart
```

### Step 4: Monitor Rolling Restart

```bash
# Watch pods restart with encryption enabled
kubectl get pods -n ${CRDB_CLUSTER_NS} -w &
WATCH_PID=$!

# Wait for all pods to be ready
kubectl wait --for=condition=Ready pods --all -n ${CRDB_CLUSTER_NS} --timeout=600s

kill $WATCH_PID 2>/dev/null
```

### Step 5: Verify Encryption is Active

```bash
# Check encryption status via SQL
kubectl exec -n ${CRDB_CLUSTER_NS} cockroachdb-0 -- \
  cockroach sql --insecure --host=localhost:26257 \
  --execute="
    -- Check encryption status
    SELECT * FROM crdb_internal.encryption_status;
  "

# Check store encryption in node status
kubectl exec -n ${CRDB_CLUSTER_NS} cockroachdb-0 -- \
  cockroach debug encryption-status /cockroach/cockroach-data 2>/dev/null || \
  echo "Use Admin UI to verify encryption status"
```

### Step 6: Verify KMS Operations

```bash
# Check that KMS key is being used
kubectl exec -n ${CRDB_CLUSTER_NS} cockroachdb-0 -- \
  cockroach sql --insecure --host=localhost:26257 \
  --execute="
    -- Check cluster settings related to encryption
    SHOW CLUSTER SETTING enterprise.encryption.enabled;
  " 2>/dev/null || echo "Check Admin UI for encryption status"

# Verify pod logs for KMS activity
kubectl logs -n ${CRDB_CLUSTER_NS} cockroachdb-0 | grep -i -E "(kms|encrypt)" | tail -10
```

### Step 7: Create Test Data

```bash
# Create test data to verify encryption works
kubectl exec -n ${CRDB_CLUSTER_NS} cockroachdb-0 -- \
  cockroach sql --insecure --host=localhost:26257 \
  --execute="
    CREATE DATABASE IF NOT EXISTS encryption_test;
    USE encryption_test;
    CREATE TABLE IF NOT EXISTS sensitive_data (
      id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
      secret_value STRING,
      created_at TIMESTAMP DEFAULT now()
    );
    INSERT INTO sensitive_data (secret_value) VALUES 
      ('secret-1'),
      ('secret-2'),
      ('secret-3');
    SELECT * FROM sensitive_data;
  "
```

### Step 8: Test Node Restart with Encryption

```bash
# Delete a pod to test restart with encryption
kubectl delete pod cockroachdb-1 -n ${CRDB_CLUSTER_NS}

# Wait for pod to restart
kubectl wait --for=condition=Ready pod/cockroachdb-1 -n ${CRDB_CLUSTER_NS} --timeout=300s

# Verify data is still accessible
kubectl exec -n ${CRDB_CLUSTER_NS} cockroachdb-0 -- \
  cockroach sql --insecure --host=localhost:26257 \
  --execute="SELECT * FROM encryption_test.sensitive_data;"
```

### Step 9: Verify Cluster Health

```bash
# Check cluster status
kubectl exec -n ${CRDB_CLUSTER_NS} cockroachdb-0 -- \
  cockroach node status --insecure --host=localhost:26257

# Verify all nodes are healthy
kubectl exec -n ${CRDB_CLUSTER_NS} cockroachdb-0 -- \
  cockroach sql --insecure --host=localhost:26257 \
  --execute="
    SELECT node_id, is_live FROM crdb_internal.gossip_nodes ORDER BY node_id;
  "
```

### Step 10: Test Key Rotation (Optional)

```bash
# Key rotation requires creating a new KMS key and updating configuration
echo "Key rotation steps:"
echo "1. Create new KMS key in your cloud provider"
echo "2. Update Helm values with new key ID"
echo "3. Perform rolling restart"
echo "4. Verify encryption with new key"

# Example rotation command (do not run without new key):
# helm upgrade ${CRDB_RELEASE_NAME} ${CRDB_CHART_PATH} \
#   --namespace ${CRDB_CLUSTER_NS} \
#   --reuse-values \
#   --set conf.store.encryption.key="new-key-id" \
#   --set conf.store.encryption.old-key="${KMS_KEY_ID}"
```

## Validation Commands

```bash
# Complete validation script
echo "=== Encryption at Rest Validation ==="

echo -e "\n1. All pods running:"
kubectl get pods -n ${CRDB_CLUSTER_NS}

echo -e "\n2. KMS credentials secret exists:"
kubectl get secret kms-credentials -n ${CRDB_CLUSTER_NS}

echo -e "\n3. Encryption status (check Admin UI if SQL fails):"
kubectl exec -n ${CRDB_CLUSTER_NS} cockroachdb-0 -- \
  cockroach sql --insecure --host=localhost:26257 \
  --execute="SELECT * FROM crdb_internal.encryption_status;" 2>/dev/null || echo "Check Admin UI"

echo -e "\n4. Test data accessible:"
kubectl exec -n ${CRDB_CLUSTER_NS} cockroachdb-0 -- \
  cockroach sql --insecure --host=localhost:26257 \
  --execute="SELECT count(*) as rows FROM encryption_test.sensitive_data;"

echo -e "\n5. Cluster health:"
kubectl exec -n ${CRDB_CLUSTER_NS} cockroachdb-0 -- \
  cockroach node status --insecure --host=localhost:26257

echo -e "\n=== Validation Complete ==="
```

## Expected Results

| Check | Expected State |
|-------|----------------|
| Pods | Running with encryption enabled |
| KMS credentials | Secret mounted |
| Encryption status | Active |
| Data access | Works normally |
| Node restart | Successful without manual intervention |
| Cluster health | All nodes live |

## Cleanup

```bash
# Remove test database
kubectl exec -n ${CRDB_CLUSTER_NS} cockroachdb-0 -- \
  cockroach sql --insecure --host=localhost:26257 \
  --execute="DROP DATABASE IF EXISTS encryption_test CASCADE;"

# Remove KMS credentials secret (optional)
# kubectl delete secret kms-credentials -n ${CRDB_CLUSTER_NS}

# Remove temporary files
rm -f /tmp/encryption-values.yaml

# Note: Disabling encryption requires data migration
# Do not simply remove encryption configuration
```

## Notes

- Encryption at rest requires Enterprise license
- KMS key must be accessible from all nodes
- Key rotation should be planned and tested
- Backup encryption keys securely
- Monitor KMS API usage and costs

## Troubleshooting

### Pod Fails to Start with Encryption

```bash
# Check pod logs for KMS errors
kubectl logs -n ${CRDB_CLUSTER_NS} cockroachdb-0 | grep -i -E "(kms|encrypt|key)"

# Check if credentials are mounted
kubectl exec -n ${CRDB_CLUSTER_NS} cockroachdb-0 -- env | grep -i aws

# Verify KMS key permissions
# (Use cloud provider CLI to test key access)
```

### KMS Access Denied

```bash
# Check IAM permissions
# For AWS:
# aws kms describe-key --key-id ${KMS_KEY_ID}
# aws kms encrypt --key-id ${KMS_KEY_ID} --plaintext "test"

# Verify service account/credentials
kubectl get secret kms-credentials -n ${CRDB_CLUSTER_NS} -o yaml
```

### Encryption Not Showing Active

```bash
# Check CockroachDB logs
kubectl logs -n ${CRDB_CLUSTER_NS} cockroachdb-0 | tail -50

# Check store configuration
kubectl exec -n ${CRDB_CLUSTER_NS} cockroachdb-0 -- \
  cat /cockroach/cockroach-data/COCKROACHDB_VERSION 2>/dev/null

# Verify Helm values applied
helm get values ${CRDB_RELEASE_NAME} -n ${CRDB_CLUSTER_NS}
```
