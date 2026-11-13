import requests


def main():
    url = "http://127.0.0.1:5002/api/auth/agent-configurations/1"
    data = {
        "name": "Test Agent",
        "description": "Test Description",
        "configuration": {"tools": []},
    }
    try:
        response = requests.patch(url, json=data, timeout=10)
        print(f"Status: {response.status_code}")
        print(f"Body: {response.text}")
    except requests.RequestException as exc:
        print(f"Could not reach the local backend: {exc}")


if __name__ == "__main__":
    main()
