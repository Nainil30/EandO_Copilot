# Deploying E&O Copilot on Kubernetes

Six manifests, applied in order. They run the same image as `docker-compose.yml`, with production-style
additions: replicas, health probes, resource limits, autoscaling and persistent storage.

| File | Objects |
|---|---|
| `namespace.yaml` | Namespace `eando-copilot` |
| `secret.yaml` | Secret `eando-secrets` (API key, DB URLs, Postgres bootstrap credentials) |
| `configmap.yaml` | ConfigMap `eando-config` (non-secret settings) |
| `postgres.yaml` | PVC `postgres-pvc` + Deployment + ClusterIP Service `postgres` |
| `api.yaml` | Deployment (2 replicas) + ClusterIP Service `eando-api` + HPA (2 to 6) |
| `ui.yaml` | Deployment (1 replica) + NodePort Service `eando-ui` (30501) |

## Prerequisites

- `kubectl` installed
- A cluster: [minikube](https://minikube.sigs.k8s.io/) is enough (`minikube start --memory 4096`)
- For the HPA to work: `minikube addons enable metrics-server`
- The images built where the cluster can see them. On minikube, build straight into its image store:

```bash
minikube image build -t eando-api:latest .
minikube image build -t eando-ui:latest .
```

(Same Dockerfile both times. The UI Deployment overrides the command to run Streamlit.)

## Deploy, in order

Order matters: the namespace must exist before anything goes in it, and pods fail to start if the Secret or
ConfigMap they reference is missing.

```bash
# 1. Namespace
kubectl apply -f k8s/namespace.yaml

# 2. Secrets: first put your real GEMINI_API_KEY in secret.yaml
#    echo -n 'your-real-key' | base64
kubectl apply -f k8s/secret.yaml

# 3. Config
kubectl apply -f k8s/configmap.yaml

# 4. Database, then wait until it's ready
kubectl apply -f k8s/postgres.yaml
kubectl wait --for=condition=ready pod -l app=postgres -n eando-copilot --timeout=120s

# 5. API
kubectl apply -f k8s/api.yaml
kubectl wait --for=condition=ready pod -l app=eando-api -n eando-copilot --timeout=180s

# 6. UI
kubectl apply -f k8s/ui.yaml
```

### Load the schema, data and knowledge base (one time)

```bash
# schema + read-only role + synthetic data + knowledge base, run inside one API pod
kubectl exec -n eando-copilot deploy/eando-api -- python -m scripts.bootstrap
```

The data goes to Postgres, so it is shared and survives restarts (PVC). The knowledge base (Chroma) sits in
each API pod's `emptyDir`, so the other replica needs its own copy:

```bash
kubectl get pods -n eando-copilot -l app=eando-api
kubectl exec -n eando-copilot <other-api-pod> -- python -m scripts.bootstrap --kb-only
```

### Open the UI

```bash
minikube service eando-ui -n eando-copilot     # or http://$(minikube ip):30501
```

## Check status

```bash
kubectl get pods -n eando-copilot                       # STATUS Running, READY 1/1
kubectl get all -n eando-copilot                        # pods, services, deployments, HPA
kubectl get hpa -n eando-copilot                        # current vs target CPU, replica count
kubectl logs -n eando-copilot deploy/eando-api          # structured JSON logs from one API pod
kubectl logs -n eando-copilot -l app=eando-api -f       # follow all API pods
kubectl describe pod -n eando-copilot <pod-name>        # Events: probe failures, OOMKilled, image pull errors
kubectl port-forward -n eando-copilot svc/eando-api 8000:8000   # then curl localhost:8000/ready
```

`describe` is the first thing to run when a pod is stuck: the **Events** section at the bottom says why
(`ImagePullBackOff`, `CrashLoopBackOff`, `Readiness probe failed`, `OOMKilled`).

## Tear down

```bash
kubectl delete namespace eando-copilot
```

This deletes every object in the namespace, including the PVC. Whether the underlying disk is deleted depends
on the StorageClass reclaim policy (minikube's default deletes it).

## Design decisions

**Why namespace isolation.** Everything for this app sits in one namespace: names can't clash with other teams'
objects, `kubectl delete namespace` cleans up completely, and on a shared cluster it is where you attach
resource quotas and RBAC (who may read the Secret).

**Why a separate ConfigMap and Secret.** They have different risk. Config like `SQL_MAX_ROWS` can sit in git
and be reviewed in a PR. Credentials need tighter RBAC and should come from a secret manager in production.
Keeping them apart also means the UI gets only the ConfigMap: it never sees the database password.

**Why an HPA on the API but not the UI.** The API does the work (retrieval, LLM calls, SQL), it is stateless
per request, and LLM traffic is bursty, so it scales horizontally. The UI is a thin client and Streamlit keeps
session state in process memory, so a second UI replica would lose users' chats on reconnect.

**Why a PVC for Postgres.** A container's filesystem disappears with the pod. The PVC is storage with its own
lifecycle: the Deployment can be restarted, upgraded or rescheduled and the warehouse data is still there. The
Deployment uses `strategy: Recreate` because a ReadWriteOnce volume can't be mounted by the old and new pod at
the same time.

## Known limits (and what I'd change for production)

These are deliberate simplifications. Each is a good interview answer to "what would you change?"

- **Postgres in a Deployment.** Fine for a demo. In production: a managed database (RDS / Cloud SQL), or a
  StatefulSet run by an operator (CloudNativePG) for replication, backups and failover.
- **Knowledge base per pod (`emptyDir`).** Each new replica needs a KB build. Fix: move vectors into Postgres
  with `pgvector` (on the roadmap) so every pod reads one shared index.
- **In-process state in the API.** Conversation memory, the rate limiter and the semantic cache live in each
  pod. With 2+ replicas a follow-up question can land on a pod that has not seen the first one. Fix: Redis.
- **`/ready` always returns HTTP 200.** It reports `"status": "degraded"` in the body when the DB or KB is
  missing, but Kubernetes only looks at the status code. Returning 503 when degraded would make readiness
  stricter.
- **Schema load via `kubectl exec`.** In production this becomes a Kubernetes `Job` (or a migration step in
  the CD pipeline) so it is repeatable and logged.
- **Images tagged `latest`.** Production uses immutable tags (the git SHA) so a rollback is
  `kubectl rollout undo` to a known image.
