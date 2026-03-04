# VKS-19: Backup and Restore Basic Validation

## Test Information

| Field | Value |
|-------|-------|
| **Test ID** | VKS-19 |
| **Category** | Day-2 Ops |
| **Dependencies** | [VKS-02](../01-cluster-provisioning/VKS-02-cluster-install.md) |

## Objective

Configure a backup destination, perform a backup of a test database, drop the database, restore it, and validate data integrity. This test ensures backups can be written from the VKS-hosted cluster and restored successfully.

## Pre-requisites

- VKS-02 completed (CockroachDB cluster running)
- Backup destination configured (object store, NFS, or local)
- `kubectl` configured with VKS cluster kubeconfig
- For cloud storage: credentials configured

## Environment Setup

```bash
# Ensure KUBECONFIG points to VKS cluster
export KUBECONFIG=$(pwd)/vks-kubeconfig.yaml

# Set environment variables
export CRDB_CLUSTER_NS="crdb-cluster"

# Backup destination options:
# Option 1: Local nodelocal (for testing only)
export BACKUP_DEST="nodelocal://1/backups"

# Option 2: S3-compatible storage
# export BACKUP_DEST="s3://your-bucket/cockroachdb-backups?AWS_ACCESS_KEY_ID=xxx&AWS_SECRET_ACCESS_KEY=xxx"

# Option 3: Google Cloud Storage
# export BACKUP_DEST="gs://your-bucket/cockroachdb-backups?AUTH=implicit"

# Option 4: Azure Blob Storage
# export BACKUP_DEST="azure://your-container/cockroachdb-backups?AZURE_ACCOUNT_NAME=xxx&AZURE_ACCOUNT_KEY=xxx"

# Verify cluster is running
kubectl get pods -n ${CRDB_CLUSTER_NS}
```

## Steps

### Step 1: Create Test Database and Data

```bash
# Create a test database with sample data
kubectl exec -n ${CRDB_CLUSTER_NS} cockroachdb-0 -- \
  cockroach sql --insecure --host=localhost:26257 \
  --execute="
    -- Create test database
    CREATE DATABASE IF NOT EXISTS backup_test;
    USE backup_test;
    
    -- Create tables with various data types
    CREATE TABLE IF NOT EXISTS customers (
      id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
      name STRING NOT NULL,
      email STRING UNIQUE,
      created_at TIMESTAMP DEFAULT now()
    );
    
    CREATE TABLE IF NOT EXISTS orders (
      id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
      customer_id UUID REFERENCES customers(id),
      amount DECIMAL(10,2),
      status STRING DEFAULT 'pending',
      created_at TIMESTAMP DEFAULT now()
    );
    
    -- Insert sample data
    INSERT INTO customers (name, email) VALUES
      ('Alice', 'alice@example.com'),
      ('Bob', 'bob@example.com'),
      ('Charlie', 'charlie@example.com');
    
    INSERT INTO orders (customer_id, amount, status)
    SELECT id, (random() * 1000)::DECIMAL(10,2), 'completed'
    FROM customers;
    
    -- Verify data
    SELECT 'customers' as table_name, count(*) as row_count FROM customers
    UNION ALL
    SELECT 'orders', count(*) FROM orders;
  "
```

### Step 2: Record Pre-Backup State

```bash
# Record checksums and counts for validation
kubectl exec -n ${CRDB_CLUSTER_NS} cockroachdb-0 -- \
  cockroach sql --insecure --host=localhost:26257 \
  --execute="
    USE backup_test;
    
    -- Record row counts
    SELECT 'customers' as tbl, count(*) as cnt FROM customers
    UNION ALL
    SELECT 'orders', count(*) FROM orders;
    
    -- Record sample data
    SELECT * FROM customers ORDER BY name;
    SELECT * FROM orders ORDER BY created_at;
  " > /tmp/pre-backup-state.txt

cat /tmp/pre-backup-state.txt
```

### Step 3: Configure Backup Destination (If Using External Storage)

```bash
# For S3-compatible storage, create external connection
# kubectl exec -n ${CRDB_CLUSTER_NS} cockroachdb-0 -- \
#   cockroach sql --insecure --host=localhost:26257 \
#   --execute="
#     CREATE EXTERNAL CONNECTION backup_storage AS 's3://bucket/path?AWS_ACCESS_KEY_ID=xxx&AWS_SECRET_ACCESS_KEY=xxx';
#   "

# For this test, we'll use nodelocal (local to each node)
echo "Using nodelocal storage for backup test"
```

### Step 4: Perform Full Database Backup

```bash
# Perform backup
kubectl exec -n ${CRDB_CLUSTER_NS} cockroachdb-0 -- \
  cockroach sql --insecure --host=localhost:26257 \
  --execute="
    BACKUP DATABASE backup_test INTO '${BACKUP_DEST}' AS OF SYSTEM TIME '-10s';
  "

# Check backup status
kubectl exec -n ${CRDB_CLUSTER_NS} cockroachdb-0 -- \
  cockroach sql --insecure --host=localhost:26257 \
  --execute="
    SELECT job_id, job_type, status, created, finished
    FROM [SHOW JOBS]
    WHERE job_type = 'BACKUP'
    ORDER BY created DESC
    LIMIT 1;
  "
```

### Step 5: Verify Backup Completed

```bash
# Show backup details
kubectl exec -n ${CRDB_CLUSTER_NS} cockroachdb-0 -- \
  cockroach sql --insecure --host=localhost:26257 \
  --execute="
    SHOW BACKUPS IN '${BACKUP_DEST}';
  "

# Show backup contents
kubectl exec -n ${CRDB_CLUSTER_NS} cockroachdb-0 -- \
  cockroach sql --insecure --host=localhost:26257 \
  --execute="
    SHOW BACKUP FROM LATEST IN '${BACKUP_DEST}';
  "
```

### Step 6: Drop the Database

```bash
# Drop the database to simulate data loss
kubectl exec -n ${CRDB_CLUSTER_NS} cockroachdb-0 -- \
  cockroach sql --insecure --host=localhost:26257 \
  --execute="
    DROP DATABASE backup_test CASCADE;
  "

# Verify database is gone
kubectl exec -n ${CRDB_CLUSTER_NS} cockroachdb-0 -- \
  cockroach sql --insecure --host=localhost:26257 \
  --execute="
    SHOW DATABASES;
  " | grep backup_test || echo "Database successfully dropped"
```

### Step 7: Restore the Database

```bash
# Restore from backup
kubectl exec -n ${CRDB_CLUSTER_NS} cockroachdb-0 -- \
  cockroach sql --insecure --host=localhost:26257 \
  --execute="
    RESTORE DATABASE backup_test FROM LATEST IN '${BACKUP_DEST}';
  "

# Check restore status
kubectl exec -n ${CRDB_CLUSTER_NS} cockroachdb-0 -- \
  cockroach sql --insecure --host=localhost:26257 \
  --execute="
    SELECT job_id, job_type, status, created, finished
    FROM [SHOW JOBS]
    WHERE job_type = 'RESTORE'
    ORDER BY created DESC
    LIMIT 1;
  "
```

### Step 8: Validate Restored Data

```bash
# Verify database exists
kubectl exec -n ${CRDB_CLUSTER_NS} cockroachdb-0 -- \
  cockroach sql --insecure --host=localhost:26257 \
  --execute="SHOW DATABASES;" | grep backup_test

# Compare with pre-backup state
kubectl exec -n ${CRDB_CLUSTER_NS} cockroachdb-0 -- \
  cockroach sql --insecure --host=localhost:26257 \
  --execute="
    USE backup_test;
    
    -- Check row counts
    SELECT 'customers' as tbl, count(*) as cnt FROM customers
    UNION ALL
    SELECT 'orders', count(*) FROM orders;
    
    -- Check sample data
    SELECT * FROM customers ORDER BY name;
    SELECT * FROM orders ORDER BY created_at;
  " > /tmp/post-restore-state.txt

# Compare states
echo "=== Pre-backup state ==="
cat /tmp/pre-backup-state.txt

echo -e "\n=== Post-restore state ==="
cat /tmp/post-restore-state.txt

# Diff comparison
diff /tmp/pre-backup-state.txt /tmp/post-restore-state.txt && echo "Data matches!" || echo "Data differs - check manually"
```

### Step 9: Test Incremental Backup (Optional)

```bash
# Add more data
kubectl exec -n ${CRDB_CLUSTER_NS} cockroachdb-0 -- \
  cockroach sql --insecure --host=localhost:26257 \
  --execute="
    USE backup_test;
    INSERT INTO customers (name, email) VALUES ('Dave', 'dave@example.com');
    INSERT INTO orders (customer_id, amount) 
    SELECT id, 500.00 FROM customers WHERE name = 'Dave';
  "

# Perform incremental backup
kubectl exec -n ${CRDB_CLUSTER_NS} cockroachdb-0 -- \
  cockroach sql --insecure --host=localhost:26257 \
  --execute="
    BACKUP DATABASE backup_test INTO LATEST IN '${BACKUP_DEST}';
  "

# Show all backups
kubectl exec -n ${CRDB_CLUSTER_NS} cockroachdb-0 -- \
  cockroach sql --insecure --host=localhost:26257 \
  --execute="
    SHOW BACKUPS IN '${BACKUP_DEST}';
  "
```

### Step 10: Test Table-Level Restore (Optional)

```bash
# Drop a single table
kubectl exec -n ${CRDB_CLUSTER_NS} cockroachdb-0 -- \
  cockroach sql --insecure --host=localhost:26257 \
  --execute="
    USE backup_test;
    DROP TABLE orders;
  "

# Restore just the orders table
kubectl exec -n ${CRDB_CLUSTER_NS} cockroachdb-0 -- \
  cockroach sql --insecure --host=localhost:26257 \
  --execute="
    RESTORE TABLE backup_test.orders FROM LATEST IN '${BACKUP_DEST}';
  "

# Verify table restored
kubectl exec -n ${CRDB_CLUSTER_NS} cockroachdb-0 -- \
  cockroach sql --insecure --host=localhost:26257 \
  --execute="
    USE backup_test;
    SELECT count(*) as orders_count FROM orders;
  "
```

## Validation Commands

```bash
# Complete validation script
echo "=== Backup and Restore Validation ==="

echo -e "\n1. Database exists:"
kubectl exec -n ${CRDB_CLUSTER_NS} cockroachdb-0 -- \
  cockroach sql --insecure --host=localhost:26257 \
  --execute="SHOW DATABASES;" | grep backup_test

echo -e "\n2. Tables exist:"
kubectl exec -n ${CRDB_CLUSTER_NS} cockroachdb-0 -- \
  cockroach sql --insecure --host=localhost:26257 \
  --execute="USE backup_test; SHOW TABLES;"

echo -e "\n3. Row counts:"
kubectl exec -n ${CRDB_CLUSTER_NS} cockroachdb-0 -- \
  cockroach sql --insecure --host=localhost:26257 \
  --execute="
    SELECT 'customers' as tbl, count(*) as cnt FROM backup_test.customers
    UNION ALL
    SELECT 'orders', count(*) FROM backup_test.orders;
  "

echo -e "\n4. Backup history:"
kubectl exec -n ${CRDB_CLUSTER_NS} cockroachdb-0 -- \
  cockroach sql --insecure --host=localhost:26257 \
  --execute="SHOW BACKUPS IN '${BACKUP_DEST}';"

echo -e "\n5. Backup/Restore jobs:"
kubectl exec -n ${CRDB_CLUSTER_NS} cockroachdb-0 -- \
  cockroach sql --insecure --host=localhost:26257 \
  --execute="
    SELECT job_type, status, created 
    FROM [SHOW JOBS] 
    WHERE job_type IN ('BACKUP', 'RESTORE') 
    ORDER BY created DESC LIMIT 5;
  "

echo -e "\n=== Validation Complete ==="
```

## Expected Results

| Check | Expected State |
|-------|----------------|
| Backup job | Completed successfully |
| Backup files | Written to destination |
| Database drop | Successful |
| Restore job | Completed successfully |
| Data integrity | Pre-backup and post-restore match |
| Table counts | Same as before backup |

## Cleanup

```bash
# Remove test database
kubectl exec -n ${CRDB_CLUSTER_NS} cockroachdb-0 -- \
  cockroach sql --insecure --host=localhost:26257 \
  --execute="DROP DATABASE IF EXISTS backup_test CASCADE;"

# Remove backup files (for nodelocal)
kubectl exec -n ${CRDB_CLUSTER_NS} cockroachdb-0 -- \
  rm -rf /cockroach/cockroach-data/extern/backups 2>/dev/null || echo "Backup cleanup skipped"

# Remove temporary files
rm -f /tmp/pre-backup-state.txt /tmp/post-restore-state.txt
```

## Notes

- nodelocal backups are stored on individual nodes and not suitable for production
- For production, use cloud storage (S3, GCS, Azure) or NFS
- Ensure backup destination has sufficient storage capacity
- Consider setting up scheduled backups for production
- Test restore procedures regularly

## Troubleshooting

### Backup Fails

```bash
# Check job status
kubectl exec -n ${CRDB_CLUSTER_NS} cockroachdb-0 -- \
  cockroach sql --insecure --host=localhost:26257 \
  --execute="SELECT * FROM [SHOW JOBS] WHERE job_type = 'BACKUP' ORDER BY created DESC LIMIT 1;"

# Check for errors
kubectl exec -n ${CRDB_CLUSTER_NS} cockroachdb-0 -- \
  cockroach sql --insecure --host=localhost:26257 \
  --execute="SELECT error FROM [SHOW JOBS] WHERE job_type = 'BACKUP' AND status = 'failed' LIMIT 1;"
```

### Storage Access Issues

```bash
# For S3, check credentials
kubectl exec -n ${CRDB_CLUSTER_NS} cockroachdb-0 -- \
  cockroach sql --insecure --host=localhost:26257 \
  --execute="SHOW EXTERNAL CONNECTIONS;"

# Test storage access
kubectl exec -n ${CRDB_CLUSTER_NS} cockroachdb-0 -- \
  cockroach sql --insecure --host=localhost:26257 \
  --execute="BACKUP INTO '${BACKUP_DEST}' AS OF SYSTEM TIME '-10s' WITH detached;"
```

### Restore Fails

```bash
# Check if database already exists
kubectl exec -n ${CRDB_CLUSTER_NS} cockroachdb-0 -- \
  cockroach sql --insecure --host=localhost:26257 \
  --execute="SHOW DATABASES;"

# For existing database, use different name or drop first
kubectl exec -n ${CRDB_CLUSTER_NS} cockroachdb-0 -- \
  cockroach sql --insecure --host=localhost:26257 \
  --execute="RESTORE DATABASE backup_test FROM LATEST IN '${BACKUP_DEST}' WITH new_db_name = 'backup_test_restored';"
```
