import requests

def verify_microsoft_token(access_token):
    """
    Verifies the Microsoft Access Token by making a request to MS Graph /me.
    Returns user profile info (dict) if valid, otherwise returns None.
    """
    if not access_token:
        return None

    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json"
    }
    
    try:
        response = requests.get("https://graph.microsoft.com/v1.0/me", headers=headers, timeout=10)
        if response.status_code == 200:
            profile = response.json()
            # extract primary email (prefer mail claim, fall back to userPrincipalName)
            email = profile.get("mail") or profile.get("userPrincipalName")
            if email:
                email = email.strip().lower()
            return {
                "email": email,
                "display_name": profile.get("displayName") or email,
                "id": profile.get("id")
            }
        else:
            print(f"DEBUG: Microsoft Graph API returned status code {response.status_code}: {response.text}")
            return None
    except Exception as e:
        print(f"ERROR: Exception verifying Microsoft token: {e}")
        return None
