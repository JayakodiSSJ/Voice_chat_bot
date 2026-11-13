# Quick Start - 5 Minute Setup

## 1️⃣ Enable Kubernetes in Docker Desktop

```
Settings → Kubernetes → Enable Kubernetes → Apply & Restart
Wait 5-10 minutes...
```

## 2️⃣ Verify Installation

```powershell
kubectl version
```

## 3️⃣ Build Docker Images

```powershell
cd Backend_Agentstore
docker build -t aisle-backend:latest .

cd ..\chatbot
docker build -t aisle-frontend:latest .
```

## 4️⃣ Deploy to Kubernetes

```powershell
# Go to kubernetes folder
cd ..\kubernetes

# Apply all manifests
kubectl apply -f .
```

## 5️⃣ Check Status

```powershell
kubectl get all -n aisle-agent
kubectl get pods -n aisle-agent
```

## 6️⃣ Access Your App

```powershell
# Port forward to access locally for development only
kubectl port-forward svc/aisle-frontend-service 8080:80 -n aisle-agent
# Open browser: http://localhost:8080
```

---

## Common Commands You'll Use

```powershell
# See what's running
kubectl get pods -n aisle-agent

# See logs
kubectl logs -f deployment/aisle-backend -n aisle-agent

# Restart deployment after code changes
kubectl rollout restart deployment/aisle-backend -n aisle-agent

# Delete everything
kubectl delete namespace aisle-agent

# Scale up/down
kubectl scale deployment/aisle-backend --replicas=3 -n aisle-agent
```

That's it! 🚀
