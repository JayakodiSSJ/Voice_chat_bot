# Kubernetes Setup Guide - Aisle Agent Store

## Table of Contents
1. [Prerequisites](#prerequisites)
2. [Kubernetes Installation](#kubernetes-installation)
3. [Prepare Docker Images](#prepare-docker-images)
4. [Deploy to Kubernetes](#deploy-to-kubernetes)
5. [Troubleshooting](#troubleshooting)
6. [Next Steps](#next-steps)

---

## Prerequisites

- **Docker Desktop** installed on Windows
- **kubectl** command-line tool (comes with Docker Desktop)
- Basic understanding of containers

---

## Kubernetes Installation

### Option 1: Docker Desktop (RECOMMENDED - Easiest)

1. **Open Docker Desktop**
   - Click the Docker icon in your system tray

2. **Enable Kubernetes**
   - Settings → Kubernetes → Check "Enable Kubernetes"
   - Click "Apply & Restart"
   - Wait for initialization (5-10 minutes)

3. **Verify Installation**
   ```powershell
   kubectl version
   kubectl get nodes
   ```
   
   You should see:
   ```
   NAME             STATUS   ROLES           AGE
   docker-desktop   Ready    control-plane   Xm
   ```

### Option 2: Minikube (Alternative)

```powershell
# Install via Chocolatey
choco install minikube

# Start cluster
minikube start

# Verify
kubectl get nodes
```

---

## Prepare Docker Images

### Step 1: Build Images with Proper Tags

```powershell
# Build Backend
cd D:\Intern\aisle-agent-store\Backend_Agentstore
docker build -t aisle-backend:latest .

# Build Frontend
cd D:\Intern\aisle-agent-store\chatbot
docker build -t aisle-frontend:latest .

# Verify images are built
docker images
```

You should see:
```
REPOSITORY          TAG       IMAGE ID
aisle-backend       latest    xxxxx
aisle-frontend      latest    xxxxx
```

---

## Deploy to Kubernetes

### Step 1: Create Namespace

```powershell
kubectl apply -f .\kubernetes\01-namespace.yaml
```

**What it does:** Creates an isolated namespace called "aisle-agent" to organize your resources.

### Step 2: Deploy Backend

```powershell
kubectl apply -f .\kubernetes\02-backend-deployment.yaml
kubectl apply -f .\kubernetes\04-backend-service.yaml
```

**What it does:**
- Creates 2 replicas of backend (if one fails, traffic goes to the other)
- Exposes backend service internally on port 5000

### Step 3: Deploy Frontend

```powershell
kubectl apply -f .\kubernetes\03-frontend-deployment.yaml
kubectl apply -f .\kubernetes\05-frontend-service.yaml
```

**What it does:**
- Creates 2 replicas of frontend
- Keeps frontend internal with a ClusterIP service
- Exposes public traffic only through the Ingress controller

### Step 4: Deploy Ingress

```powershell
kubectl apply -f .\kubernetes\06-ingress.yaml
```

**What it does:** Routes HTTP traffic from the ingress controller to the internal frontend and backend services without opening application NodePorts.

### Step 5: Verify Deployment

```powershell
# Check all resources in namespace
kubectl get all -n aisle-agent

# Check pods status
kubectl get pods -n aisle-agent

# Check services
kubectl get services -n aisle-agent

# Get more details
kubectl describe pods -n aisle-agent
```

### Step 6: Access Your Application

**For Frontend:**
```powershell
# Use the configured Ingress host, or port-forward locally for development only.
kubectl get ingress -n aisle-agent
```

**Check service details:**
```powershell
kubectl describe svc aisle-frontend-service -n aisle-agent
```

---

## Useful Commands

### View Logs

```powershell
# See backend logs
kubectl logs -f deployment/aisle-backend -n aisle-agent

# See frontend logs
kubectl logs -f deployment/aisle-frontend -n aisle-agent

# See specific pod logs
kubectl logs pod-name -n aisle-agent
```

### Port Forwarding (Local Testing)

```powershell
# Access backend locally
kubectl port-forward svc/aisle-backend-service 5000:5000 -n aisle-agent

# Access frontend locally
kubectl port-forward svc/aisle-frontend-service 8080:80 -n aisle-agent
```

### Scale Deployments

```powershell
# Scale backend to 3 replicas
kubectl scale deployment/aisle-backend --replicas=3 -n aisle-agent

# Scale frontend to 1 replica
kubectl scale deployment/aisle-frontend --replicas=1 -n aisle-agent
```

### Update Deployments

```powershell
# After rebuilding Docker images
kubectl rollout restart deployment/aisle-backend -n aisle-agent
kubectl rollout restart deployment/aisle-frontend -n aisle-agent
```

### Delete Everything

```powershell
# Delete entire namespace (removes all resources)
kubectl delete namespace aisle-agent

# Or delete individual resources
kubectl delete -f .\kubernetes\
```

---

## Troubleshooting

### Pods Not Starting?

```powershell
# Check pod status
kubectl describe pod pod-name -n aisle-agent

# Check for image pull issues
kubectl get pods -n aisle-agent -o wide

# Check events
kubectl get events -n aisle-agent
```

### Image Not Found?

```powershell
# Ensure images are built locally
docker images

# If using private registry, ensure imagePullSecrets is set in deployment
# For local Docker Desktop, use imagePullPolicy: Never
```

### Cannot Access Service?

```powershell
# Test internal connectivity
kubectl run -it --rm debug --image=busybox --restart=Never -n aisle-agent -- sh
# Inside shell: wget -O- http://aisle-backend-service:5000
```

### Port Already in Use?

```powershell
# Use different port for port-forward
kubectl port-forward svc/aisle-frontend-service 9000:80 -n aisle-agent
# Access at http://localhost:9000
```

---

## Next Steps

### 1. Set Up Persistent Storage (if needed)
- Create PersistentVolumes for database
- Add ConfigMaps for environment variables
- Create Secrets for sensitive data

### 2. Set Up Ingress Controller
```powershell
# Install nginx-ingress if not available
# Then apply ingress.yaml for domain-based routing
kubectl apply -f .\kubernetes\06-ingress.yaml
```

### 3. Production Deployment
- Push images to Docker Hub / Private Registry
- Update image pull policies in deployments
- Add resource limits and requests
- Set up monitoring and logging
- Configure health checks

### 4. Use Helm (Optional)
- Simplifies deployments for production
- Create Helm charts for easier management

---

## Key Kubernetes Concepts (Quick Reference)

| Concept | What it does |
|---------|-------------|
| **Pod** | Smallest deployable unit (usually one container) |
| **Deployment** | Manages multiple pod replicas |
| **Service** | Exposes pods to network traffic |
| **Namespace** | Logical grouping of resources |
| **ConfigMap** | Store configuration data |
| **Secret** | Store sensitive data (passwords, tokens) |
| **Ingress** | Route external HTTP(S) traffic to services |
| **PersistentVolume** | Storage that survives pod restarts |

---

## Quick Deploy All at Once

```powershell
# Deploy everything in order
kubectl apply -f .\kubernetes\01-namespace.yaml
kubectl apply -f .\kubernetes\02-backend-deployment.yaml
kubectl apply -f .\kubernetes\03-frontend-deployment.yaml
kubectl apply -f .\kubernetes\04-backend-service.yaml
kubectl apply -f .\kubernetes\05-frontend-service.yaml

# Or apply all at once (if no dependency issues)
kubectl apply -f .\kubernetes\
```

---

## Clean Up

```powershell
# Remove everything
kubectl delete namespace aisle-agent

# Scale down to 0 (keeps resources, stops containers)
kubectl scale deployment/aisle-backend --replicas=0 -n aisle-agent
kubectl scale deployment/aisle-frontend --replicas=0 -n aisle-agent
```

---

## Questions?

- Check logs: `kubectl logs`
- Describe resources: `kubectl describe`
- Check events: `kubectl get events -n aisle-agent`
