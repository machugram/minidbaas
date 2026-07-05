# Diagrams — Mini-DBaaS

All diagrams use [Mermaid](https://mermaid.js.org/) so they render on GitHub and
in most Markdown viewers. They are the visual companion to the
[Architecture](../ARCHITECTURE.md).

---

## 1. System context (C4 level 1)

```mermaid
flowchart TB
    dev["Developer / CI"]
    lead["Team Lead"]
    cli["mdbaas CLI"]

    subgraph MDBAAS["Mini-DBaaS platform"]
        api["Control Plane API<br/>(FastAPI)"]
        meta[("Metadata DB<br/>(Postgres)")]
        workers["Lifecycle workers<br/>(scheduler + reconciler)"]
        subgraph DP["Data plane (Docker host)"]
            i1[("pg-instance-1")]
            i2[("pg-instance-2")]
            i3[("pg-instance-N")]
        end
    end

    dev -->|REST| api
    lead -->|REST| api
    cli -->|REST| api
    api --> meta
    workers --> meta
    api -->|Docker SDK| DP
    workers -->|Docker SDK / exec| DP
```

---

## 2. Component view (C4 level 2)

```mermaid
flowchart LR
    subgraph CP["Control Plane"]
        direction TB
        R["API routers<br/>teams · instances · users<br/>credentials · backups"]
        AUTH["Auth + RBAC<br/>dependency"]
        QUOTA["Quota enforcer"]
        AUD["Audit logger"]
        PROV["Provisioner<br/>(Protocol)"]
        DOCK["DockerProvisioner"]
        SEC["Secrets<br/>(encrypt/rotate)"]
        SCHED["APScheduler"]
        BKP["Backups"]
        PATCH["Patching"]
        REAP["Reaper<br/>(expiry · GC · drift)"]
    end
    meta[("Metadata DB")]
    host["Docker daemon"]

    R --> AUTH --> QUOTA --> PROV
    R --> AUD
    R --> SEC
    PROV --> DOCK --> host
    SCHED --> BKP --> host
    SCHED --> REAP --> host
    PATCH --> host
    R --> meta
    SCHED --> meta
    BKP --> meta
    REAP --> meta
    AUD --> meta
```

---

## 3. Provisioning sequence (async)

```mermaid
sequenceDiagram
    autonumber
    actor Dev
    participant API
    participant Meta as Metadata DB
    participant Job as Reconciler/Worker
    participant Docker

    Dev->>API: POST /v1/instances (size, version, team)
    API->>API: authz + quota check
    API->>Meta: insert instance(desired=READY), job(PROVISION)
    API-->>Dev: 202 Accepted (instance id, status=PROVISIONING)
    Job->>Meta: claim job (lock)
    Job->>Docker: create volume + container (labels, port, limits)
    Docker-->>Job: container running
    Job->>Docker: wait for pg healthcheck
    Job->>Meta: set observed=READY, store encrypted creds
    Dev->>API: GET /v1/instances/{id}
    API-->>Dev: status=READY + connection info
```

---

## 4. Backup & restore sequence

```mermaid
sequenceDiagram
    autonumber
    participant Sched as Scheduler
    participant Meta as Metadata DB
    participant Docker
    participant Store as Backup store

    Sched->>Meta: due backups?
    Meta-->>Sched: instance list
    Sched->>Docker: exec pg_dump inside pg-instance-X
    Docker-->>Sched: dump stream
    Sched->>Store: write <id>/<ts>.sql.gz
    Sched->>Meta: insert backup(status=OK, size, location)
    Sched->>Store: prune beyond retention
    Note over Sched,Store: Restore = new/target instance +<br/>pg_restore from chosen artifact
```

---

## 5. Instance lifecycle state machine

```mermaid
stateDiagram-v2
    [*] --> REQUESTED
    REQUESTED --> PROVISIONING
    PROVISIONING --> READY
    PROVISIONING --> FAILED
    READY --> RESIZING
    READY --> PATCHING
    READY --> BACKING_UP
    RESIZING --> READY
    PATCHING --> READY
    PATCHING --> FAILED
    BACKING_UP --> READY
    READY --> EXPIRED: expires_at reached
    READY --> DELETING: delete
    EXPIRED --> DELETING: reaper (post grace + final backup)
    FAILED --> PROVISIONING: reconciler retry
    DELETING --> DELETED
    DELETED --> [*]
```

---

## 6. Metadata ER model

```mermaid
erDiagram
    teams ||--o{ team_memberships : has
    principals ||--o{ team_memberships : in
    teams ||--o{ instances : owns
    instances ||--o{ db_users : contains
    instances ||--o{ credentials : has
    instances ||--o{ backups : has
    instances ||--o{ jobs : drives
    teams ||--o{ audit_log : scoped
    principals ||--o{ audit_log : actor

    teams {
        uuid id PK
        string name
        int max_instances
        int max_total_memory_mb
        int max_storage_gb
    }
    principals {
        uuid id PK
        string name
        string api_key_hash
    }
    team_memberships {
        uuid team_id FK
        uuid principal_id FK
        string role
    }
    instances {
        uuid id PK
        uuid team_id FK
        string engine
        string image
        string size
        int host_port
        string desired_state
        string observed_state
        jsonb tags
        timestamp created_at
        timestamp expires_at
    }
    db_users {
        uuid id PK
        uuid instance_id FK
        string username
        string privileges
    }
    credentials {
        uuid id PK
        uuid instance_id FK
        bytea secret_encrypted
        timestamp rotated_at
    }
    backups {
        uuid id PK
        uuid instance_id FK
        string kind
        string location
        bigint size_bytes
        string status
        timestamp created_at
    }
    jobs {
        uuid id PK
        uuid instance_id FK
        string type
        string state
        string error
        timestamp created_at
    }
    audit_log {
        uuid id PK
        uuid actor_id FK
        uuid team_id FK
        string action
        string target
        timestamp at
    }
```

---

## 7. Provisioning workflow (end-to-end, with desired-state reconciliation)

```mermaid
sequenceDiagram
    participant Client
    participant API as API<br/>(desired-state<br/>writer)
    participant MetaDB as Metadata DB<br/>(source of truth)
    participant Worker as Worker<br/>(job poller)
    participant Provisioner as Provisioner<br/>(Docker SDK)
    participant Docker as Docker<br/>daemon

    Client->>API: POST /v1/instances<br/>(create my-db)
    activate API
    API->>API: validate<br/>authz + quota
    API->>MetaDB: BEGIN<br/>INSERT instance<br/>(desired=READY)<br/>INSERT job<br/>(PROVISION)
    API->>MetaDB: COMMIT
    API-->>Client: 202 Accepted<br/>instance id + status
    deactivate API

    Note over Worker: polling every 2s...
    Worker->>MetaDB: SELECT * FROM jobs<br/>WHERE state=QUEUED<br/>LIMIT 1<br/>FOR UPDATE SKIP LOCKED
    activate Worker
    MetaDB-->>Worker: job row
    Worker->>Worker: idempotent<br/>handle_provision()
    Worker->>Provisioner: create(spec)
    activate Provisioner
    Provisioner->>Docker: POST /containers/create<br/>with volume + labels
    Docker-->>Provisioner: container_id
    Provisioner->>Docker: POST /containers/{id}/start
    Docker-->>Provisioner: running
    Provisioner->>Docker: healthcheck loop<br/>pg_isready
    Docker-->>Provisioner: healthy
    Provisioner-->>Worker: Provisioned(id)
    deactivate Provisioner
    Worker->>MetaDB: UPDATE instance<br/>SET observed=READY<br/>UPDATE job<br/>SET state=DONE
    deactivate Worker

    Client->>API: GET /v1/instances/{id}/status
    API->>MetaDB: SELECT instance
    MetaDB-->>API: instance(observed=READY)
    API-->>Client: { status: "ready",<br/>connection: {...} }
    Client->>Docker: psql -h 127.0.0.1<br/>-p 15001
    Docker-->>Client: ready for queries
```

**Key insight:** The client gets a quick `202` response while the actual provisioning happens async. The reconciler converges `observed_state` toward `desired_state` independently, making the system resilient to crashes and enabling job retries (ADR-003).

---

## 8. Deployment (MVP, single host)

```mermaid
flowchart TB
    subgraph Host["Single Docker host"]
        subgraph compose["docker-compose (control plane)"]
            api["api : FastAPI"]
            meta[("metadata-db : Postgres")]
        end
        subgraph managed["Managed instances (labelled mdbaas.managed=true)"]
            p1[("pg-instance-1 :15001")]
            p2[("pg-instance-2 :15002")]
        end
        vol["/backups volume"]
    end
    api --> meta
    api -->|Docker socket| managed
    api --> vol
```
