import httpx
import os
from typing import Optional, Dict, Any

class GitHubService:
    BASE_URL = "https://api.github.com"
    AUTH_URL = "https://github.com/login/oauth/authorize"
    TOKEN_URL = "https://github.com/login/oauth/access_token"

    @staticmethod
    def _creds() -> tuple[str, str]:
        client_id = os.getenv("GITHUB_CLIENT_ID")
        client_secret = os.getenv("GITHUB_CLIENT_SECRET")
        if not client_id or not client_secret:
            raise ValueError("GitHub OAuth credentials are not configured")
        return client_id, client_secret

    @staticmethod
    def get_login_url(redirect_uri: str) -> str:
        client_id, _ = GitHubService._creds()
        return (
            f"{GitHubService.AUTH_URL}?"
            f"client_id={client_id}&"
            f"redirect_uri={redirect_uri}&"
            f"scope=repo,user:email"
        )

    @staticmethod
    async def get_access_token(code: str, redirect_uri: Optional[str] = None) -> Optional[str]:
        client_id, client_secret = GitHubService._creds()
        data = {
            "client_id": client_id,
            "client_secret": client_secret,
            "code": code,
        }
        if redirect_uri:
            data["redirect_uri"] = redirect_uri

        async with httpx.AsyncClient() as client:
            response = await client.post(
                GitHubService.TOKEN_URL,
                headers={"Accept": "application/json"},
                data=data,
            )
            if response.status_code == 200:
                data = response.json()
                return data.get("access_token")
            return None

    @staticmethod
    async def get_user(access_token: str) -> Optional[Dict[str, Any]]:
        async with httpx.AsyncClient() as client:
            response = await client.get(
                f"{GitHubService.BASE_URL}/user",
                headers={
                    "Authorization": f"Bearer {access_token}",
                    "Accept": "application/vnd.github.v3+json",
                },
            )
            if response.status_code == 200:
                return response.json()
            return None

    @staticmethod
    async def list_repos(access_token: str) -> list[Dict[str, Any]]:
        repos = []
        page = 1
        async with httpx.AsyncClient() as client:
            while True:
                print(f"Fetching repos page {page} with token: {access_token[:4]}...")
                response = await client.get(
                    f"{GitHubService.BASE_URL}/user/repos",
                    headers={
                        "Authorization": f"Bearer {access_token}",
                        "Accept": "application/vnd.github.v3+json",
                    },
                    params={"page": page, "per_page": 100, "sort": "updated"},
                )
                print(f"GitHub API Response Status: {response.status_code}")
                if response.status_code != 200:
                    print(f"Error response: {response.text}")
                    break
                
                data = response.json()
                print(f"Found {len(data)} repos on page {page}")
                if not data:
                    break
                
                repos.extend(data)
                page += 1
                # Limit to first 100 for now to avoid rate limits/long waits in demo
                break 
        return repos

    @staticmethod
    async def get_latest_commit(full_name: str, access_token: str) -> Optional[str]:
        """Return the latest commit SHA for a repository."""

        async with httpx.AsyncClient() as client:
            response = await client.get(
                f"{GitHubService.BASE_URL}/repos/{full_name}/commits",
                headers={
                    "Authorization": f"Bearer {access_token}",
                    "Accept": "application/vnd.github.v3+json",
                },
                params={"per_page": 1},
            )
            if response.status_code == 200:
                data = response.json()
                if data:
                    return data[0].get("sha")
        return None
    @staticmethod
    def _get_app_jwt() -> str:
        import jwt
        import time
        
        app_id = os.getenv("GITHUB_APP_ID")
        private_key = os.getenv("GITHUB_APP_PRIVATE_KEY")
        
        if not app_id or not private_key:
            raise ValueError("GitHub App credentials (ID/Private Key) not configured")
            
        payload = {
            "iat": int(time.time()),
            "exp": int(time.time()) + 600,
            "iss": app_id
        }
        
        return jwt.encode(payload, private_key, algorithm="RS256")

    @staticmethod
    async def get_installation_access_token(installation_id: str) -> str:
        jwt_token = GitHubService._get_app_jwt()
        
        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"{GitHubService.BASE_URL}/app/installations/{installation_id}/access_tokens",
                headers={
                    "Authorization": f"Bearer {jwt_token}",
                    "Accept": "application/vnd.github.v3+json",
                },
            )
            response.raise_for_status()
            return response.json()["token"]

    @staticmethod
    async def create_pr(
        token: str, 
        owner: str, 
        repo: str, 
        title: str, 
        body: str, 
        head: str, 
        base: str
    ) -> Dict[str, Any]:
        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"{GitHubService.BASE_URL}/repos/{owner}/{repo}/pulls",
                headers={
                    "Authorization": f"Bearer {token}",
                    "Accept": "application/vnd.github.v3+json",
                },
                json={
                    "title": title,
                    "body": body,
                    "head": head,
                    "base": base,
                },
            )
            # If PR already exists, GitHub returns 422. 
            # Ideally we should handle this gracefully or check first.
            # For now, let it raise if it's not 422 or if we want to catch it higher up.
            response.raise_for_status()
            return response.json()
