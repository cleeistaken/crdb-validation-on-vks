# CockroachDB Operator VKS Functional Tests

This directory contains functional test documentation for validating the CockroachDB Operator on VMware VKS (vSphere Kubernetes Service).

## Test Environment

| Component | Version |
|-----------|---------|
| VMware WCP | 3.6.0 |
| VKS | 1.35.0 |
| Kubernetes | v1.35.0+vmware.2-vkr.4 |
| Storage Class | vsan-esa-default-policy-raid5 |

## Test Execution Order

Tests are organized to minimize cluster re-deployment and build upon each other. Follow this recommended execution order:

### Phase 1: Foundation (Required for all subsequent tests)
1. **[00-VKS-CLUSTER](00-prerequisites/00-VKS-CLUSTER.md)** - Deploy VKS Kubernetes cluster
2. **[VKS-01](01-cluster-provisioning/VKS-01-operator-install.md)** - Install CockroachDB Operator
3. **[VKS-02](01-cluster-provisioning/VKS-02-cluster-install.md)** - Install CockroachDB Cluster

### Phase 2: Cluster Configuration & Security
4. **[VKS-03](01-cluster-provisioning/VKS-03-secure-cluster-tls.md)** - Secure cluster initialization (TLS)
5. **[VKS-04](01-cluster-provisioning/VKS-04-locality-mappings.md)** - Multi-region / locality mappings
6. **[VKS-05](02-security/VKS-05-pod-security-admission.md)** - Pod Security Admission compatibility
7. **[VKS-06](02-security/VKS-06-network-policy.md)** - NetworkPolicy compatibility

### Phase 3: Scaling Operations
8. **[VKS-07](03-scaling/VKS-07-scale-up.md)** - Scale up nodes
9. **[VKS-08](03-scaling/VKS-08-scale-down.md)** - Scale down nodes
10. **[VKS-09](03-scaling/VKS-09-node-decommission.md)** - Kubernetes node decommission via annotation

### Phase 4: Upgrades
11. **[VKS-10](04-upgrades/VKS-10-patch-upgrade.md)** - Patch upgrade
12. **[VKS-11](04-upgrades/VKS-11-major-upgrade-auto.md)** - Major upgrade with auto-finalization
13. **[VKS-12](04-upgrades/VKS-12-major-upgrade-manual.md)** - Major upgrade with manual finalization
14. **[VKS-13](04-upgrades/VKS-13-rollback-patch.md)** - Rollback patch upgrade

### Phase 5: Operator Lifecycle
15. **[VKS-14](05-operator-lifecycle/VKS-14-operator-upgrade.md)** - Operator Helm chart upgrade
16. **[VKS-15](05-operator-lifecycle/VKS-15-crd-migration.md)** - CRD version migration

### Phase 6: Networking & Access
17. **[VKS-16](06-networking/VKS-16-db-console-access.md)** - Expose DB Console (Admin UI)
18. **[VKS-17](06-networking/VKS-17-sql-loadbalancer.md)** - SQL service exposure via LoadBalancer

### Phase 7: Day-2 Operations
19. **[VKS-18](07-day2-ops/VKS-18-monitoring.md)** - Cluster monitoring integration
20. **[VKS-19](07-day2-ops/VKS-19-backup-restore.md)** - Backup and restore
21. **[VKS-20](07-day2-ops/VKS-20-pod-eviction.md)** - Pod eviction / rescheduling

### Phase 8: Advanced Features
22. **[VKS-21](08-advanced-features/VKS-21-pcr.md)** - Physical Cluster Replication (PCR)
23. **[VKS-22](08-advanced-features/VKS-22-encryption-kms.md)** - Encryption at rest with KMS
24. **[VKS-23](08-advanced-features/VKS-23-logical-replication.md)** - Logical Replication between clusters
25. **[VKS-24](08-advanced-features/VKS-24-cdc-kafka.md)** - CDC to Kafka or cloud pub/sub

### Phase 9: Compatibility & Failure Handling
26. **[VKS-25](09-failure-handling/VKS-25-operator-coexistence.md)** - Coexistence with legacy Public Operator
27. **[VKS-26](09-failure-handling/VKS-26-operator-restart.md)** - Operator restart during operations
28. **[VKS-27](09-failure-handling/VKS-27-vks-upgrade.md)** - VKS Kubernetes cluster upgrade
29. **[VKS-28](09-failure-handling/VKS-28-vks-minor-upgrade.md)** - VKS minor upgrade
30. **[VKS-29](09-failure-handling/VKS-29-wal-failover.md)** - WAL failover validation

## Dependency Graph

```
00-VKS-CLUSTER
    └── VKS-01 (Operator Install)
            └── VKS-02 (Cluster Install)
                    ├── VKS-03 (TLS) ──────────────────┐
                    ├── VKS-04 (Locality) ─────────────┤
                    ├── VKS-05 (PSA) ──────────────────┤
                    ├── VKS-06 (NetworkPolicy) ────────┤
                    ├── VKS-07 (Scale Up) ─────────────┼── VKS-08 (Scale Down)
                    │                                  │
                    ├── VKS-09 (Node Decommission) ────┤
                    ├── VKS-10 (Patch Upgrade) ────────┼── VKS-13 (Rollback)
                    ├── VKS-11 (Major Auto) ───────────┤
                    ├── VKS-12 (Major Manual) ─────────┤
                    ├── VKS-16 (DB Console) ───────────┤
                    ├── VKS-17 (SQL LB) ───────────────┤
                    ├── VKS-18 (Monitoring) ───────────┤
                    ├── VKS-19 (Backup/Restore) ───────┤
                    ├── VKS-20 (Pod Eviction) ─────────┤
                    ├── VKS-21 (PCR) ──────────────────┤
                    ├── VKS-22 (KMS Encryption) ───────┤
                    ├── VKS-23 (Logical Replication) ──┤
                    ├── VKS-24 (CDC Kafka) ────────────┤
                    ├── VKS-26 (Operator Restart) ─────┤
                    └── VKS-29 (WAL Failover) ─────────┘

VKS-01 (Operator Install)
    ├── VKS-14 (Operator Upgrade)
    │       └── VKS-15 (CRD Migration)
    └── VKS-25 (Operator Coexistence)

VKS-02 (Cluster Install)
    ├── VKS-27 (VKS Upgrade)
    └── VKS-28 (VKS Minor Upgrade)
```

## Test Document Format

Each test document includes:
- **Test ID**: Unique identifier
- **Category**: Test category
- **Dependencies**: Required prerequisite tests
- **Pre-requisites**: Environment requirements
- **Steps**: Detailed command-line instructions
- **Expected Results**: Success criteria
- **Validation Commands**: Commands to verify success
- **Cleanup**: Optional cleanup steps
- **Notes**: Additional information

## Common Variables

Set these environment variables before running tests:

```bash
export CRDB_OPERATOR_NS="crdb-operator"
export CRDB_CLUSTER_NS="crdb-cluster"
export CRDB_RELEASE_NAME="cockroachdb"
export OPERATOR_RELEASE_NAME="crdb-operator"
export STORAGE_CLASS="vsan-esa-default-policy-raid5"
export VKS_CLUSTER_NAME="cluster-vks"
```

## Quick Start

```bash
# 1. Deploy VKS cluster
kubectl apply -f vks.yaml

# 2. Wait for cluster to be ready
kubectl get cluster cluster-vks -w

# 3. Get kubeconfig for VKS cluster
kubectl get secret cluster-vks-kubeconfig -o jsonpath='{.data.value}' | base64 -d > vks-kubeconfig.yaml
export KUBECONFIG=vks-kubeconfig.yaml

# 4. Follow test documents in order starting with VKS-01
```
