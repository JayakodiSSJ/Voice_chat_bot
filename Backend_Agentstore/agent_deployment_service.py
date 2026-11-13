from __future__ import annotations

import os
from typing import Any

import requests
from dotenv import load_dotenv

load_dotenv()


class AgentDeploymentService:
    def __init__(self):
        raw = os.getenv("INGESTION_URL", "http://10.238.139.124:5000")
        if raw and not raw.startswith(("http://", "https://")):
            raw = f"http://{raw}"
        base = raw.split("/api/")[0] if "/api/" in raw else raw.rstrip("/")
        self.agent_creation_url = f"{base}/api/custom-agents"

    def deploy_custom_agent(
        self,
        name: str,
        configuration: dict[str, Any],
        microsoft_token: str | None,
        jwt_token: str | None,
    ):
        if not jwt_token:
            return False, {"error": "missing_token"}, 401

        payload = {
            "agent_name": name,
            "tools": configuration.get("tools", []),
            "knowledge_base": configuration.get("knowledgeBase", []),
            "microsoft_token": microsoft_token,
        }

        try:
            response = requests.post(
                self.agent_creation_url,
                json=payload,
                timeout=30,
                headers={"Content-Type": "application/json", "Authorization": f"Bearer {jwt_token}"},
            )
            try:
                data = response.json()
            except Exception:
                data = {"raw": response.text[:2000]}
            return response.ok, data, response.status_code
        except Exception as e:
            return False, {"error": str(e)}, 500


agent_deployment_service = AgentDeploymentService()
