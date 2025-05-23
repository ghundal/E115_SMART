"""
Google OAuth2 Authentication Router for SMART System

This FastAPI router implements Google OAuth 2.0 login and authorization for the SMART web application.
It performs the following key functions:

1. Initiates OAuth login via Google's authorization endpoint.
2. Handles the OAuth callback to retrieve access tokens and user profile information.
3. Verifies the user's email address against an `access` table in the PostgreSQL database.
4. Stores a valid access token into the `user_tokens` table for session tracking.
5. Sets secure, HTTP-only cookies for backend authentication and frontend access.
6. Redirects authenticated users to the main frontend application (e.g., `/about`).

Requirements:
- Google OAuth client credentials JSON file via `GOOGLE_CREDENTIALS_FILE` environment variable.
- PostgreSQL database with `access` and `user_tokens` tables configured.
- Environment variables for `FRONTEND_URL` and `BACKEND_URL`.

Security:
- Tokens are stored securely in the database.
- Cookies are marked `secure` and `httponly` to mitigate XSS/CSRF.
- Unauthorized users are blocked based on access control.

Routes:
- `/login`: Redirects the user to Google for login
- `/auth`: Handles OAuth callback, performs access check, sets cookies, and redirects to frontend
"""

import json
import os

from authlib.integrations.starlette_client import OAuth
from fastapi import APIRouter, HTTPException, Request
from sqlalchemy.orm import sessionmaker
from sqlalchemy.sql import text
from starlette.responses import RedirectResponse
from utils.database import connect_to_postgres

router = APIRouter()

# DB session
engine = connect_to_postgres()
SessionLocal = sessionmaker(bind=engine)
FRONTEND_URL = os.getenv("FRONTEND_URL", "http://localhost:3000")
BACKEND_URL = os.getenv("BACKEND_URL", "http://localhost:9000")

# Load Google credentials from the JSON file in your secrets folder.
credentials_file = os.getenv("GOOGLE_CREDENTIALS_FILE", "../../secrets/client_secrets.json")
try:
    with open(credentials_file) as f:
        google_secrets = json.load(f)
except Exception as e:
    raise RuntimeError(f"Unable to load Google credentials from {credentials_file}: {e}")

# OAuth config using the credentials from the JSON file
oauth = OAuth()
oauth.register(
    name="google",
    client_id=google_secrets.get("client_id"),
    client_secret=google_secrets.get("client_secret"),
    access_token_url="https://accounts.google.com/o/oauth2/token",
    access_token_params=None,
    authorize_url="https://accounts.google.com/o/oauth2/auth",
    authorize_params={"prompt": "select_account"},  # Force account selection
    api_base_url="https://www.googleapis.com/oauth2/v1/",
    userinfo_endpoint="https://openidconnect.googleapis.com/v1/userinfo",
    client_kwargs={"scope": "openid email profile"},
    jwks_uri="https://www.googleapis.com/oauth2/v3/certs",
)


@router.get("/login")
async def login(request: Request):
    redirect_uri = BACKEND_URL + "/auth/auth"
    return await oauth.google.authorize_redirect(request, redirect_uri)


@router.get("/auth", name="auth_callback")
async def auth_callback(request: Request):
    # Get the token
    token = await oauth.google.authorize_access_token(request)

    # Get the user info using the access token
    resp = await oauth.google.get("https://www.googleapis.com/oauth2/v3/userinfo", token=token)
    user_info = resp.json()

    # Store the token in the database for future API authentication
    access_token = token.get("access_token")
    user_email = user_info.get("email")

    db = SessionLocal()
    try:
        # Check if user exists in access table
        access_check = db.execute(
            text("SELECT 1 FROM access WHERE user_email = :email LIMIT 1"),
            {"email": user_email},
        ).fetchone()

        if not access_check:
            raise HTTPException(
                status_code=403,
                detail="Access denied. You're not authorized to use this system.",
            )

        # Store token in database
        db.execute(
            text("DELETE FROM user_tokens WHERE user_email = :email"),
            {"email": user_email},
        )

        db.execute(
            text(
                "INSERT INTO user_tokens (user_email, token, expires_at) VALUES (:email, :token, :expires_at)"
            ),
            {
                "email": user_email,
                "token": access_token,
                "expires_at": token.get("expires_at"),
            },
        )
        db.commit()

        # Create response with redirect to frontend
        frontend_url = FRONTEND_URL + "/about"
        response = RedirectResponse(url=frontend_url)

        # Set secure HTTP-only cookie with the token
        max_age = token.get("expires_in", 3600)  # Default to 1 hour if not specified
        response.set_cookie(
            key="access_token",
            value=access_token,
            httponly=True,  # Prevents JavaScript access
            secure=True,  # Only sent over HTTPS
            samesite="lax",  # Protects against CSRF
            max_age=max_age,  # Cookie expiration time
            path="/",  # Available across all paths
        )

        # Set a non-httponly cookie with basic user info (optional)
        response.set_cookie(
            key="user_email",
            value=user_email,
            httponly=False,  # Frontend can access this
            secure=True,
            samesite="lax",
            max_age=max_age,
            path="/",
        )

        return response
    finally:
        db.close()
