"""
Supabase-backed auth for the Recipe Recommender.

Everyone who signs up gets full access to the app right now — the
`role` we store (student vs. staff) is determined purely from the
person's email domain and saved for later use (e.g. a future
inventory-management feature). It does not gate anything today.

  @csu.fullerton.edu  -> student
  @fullerton.edu      -> staff   (any other fullerton.edu subdomain
                                   is treated as staff too, EXCEPT
                                   csu.fullerton.edu specifically)
  anything else        -> rejected at signup
"""

import json
import os
from functools import wraps
from typing import Optional

import requests
from flask import session, redirect, url_for, flash
from supabase import create_client, Client

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")

_supabase: Client | None = None


def get_supabase() -> Client:
    global _supabase
    if _supabase is None:
        if not SUPABASE_URL or not SUPABASE_KEY:
            raise RuntimeError(
                "SUPABASE_URL and SUPABASE_KEY must be set in your .env file"
            )
        _supabase = create_client(SUPABASE_URL, SUPABASE_KEY)
    return _supabase


def get_user_client() -> Client:
    """A fresh client authenticated as the CURRENT logged-in user.

    We deliberately don't reuse the shared get_supabase() client for
    per-user data operations: that client is a single global object, and
    if we authenticated it as one user it would leak into every other
    user's request in this multi-user app. A new lightweight client per
    request, scoped with that user's access token, keeps Row Level
    Security correctly isolated per person.

    If the stored JWT has expired, refresh it once before retrying. This avoids
    crashing the inventory or history pages on an otherwise valid session.
    """
    token = session.get("access_token")
    if not token or not SUPABASE_URL or not SUPABASE_KEY:
        raise RuntimeError("No logged-in user session available.")

    client = create_client(SUPABASE_URL, SUPABASE_KEY)
    try:
        client.postgrest.auth(token)
        return client
    except Exception:
        if refresh_user_token():
            refreshed_token = session.get("access_token")
            if refreshed_token:
                retry_client = create_client(SUPABASE_URL, SUPABASE_KEY)
                retry_client.postgrest.auth(refreshed_token)
                return retry_client
        session.clear()
        raise RuntimeError("Your session expired. Please log in again.") from None


def refresh_user_token() -> bool:
    """Attempt to refresh the logged-in user's access token using the
    stored refresh token. Returns True if refreshed and session updated,
    False otherwise.
    """
    refresh_token = session.get("refresh_token")
    if not refresh_token or not SUPABASE_URL or not SUPABASE_KEY:
        return False

    url = f"{SUPABASE_URL}/auth/v1/token"
    headers = {
        "apikey": SUPABASE_KEY,
        "Authorization": f"Bearer {SUPABASE_KEY}",
        "Content-Type": "application/json",
    }
    payload = {"grant_type": "refresh_token", "refresh_token": refresh_token}

    try:
        resp = requests.post(url, headers=headers, data=json.dumps(payload), timeout=10)
        resp.raise_for_status()
        data = resp.json()
    except Exception:
        # On any error, don't leak details — return False so caller can force
        # a logout or show an error to the user.
        return False

    access_token = data.get("access_token")
    new_refresh_token = data.get("refresh_token")
    if not access_token:
        return False

    session["access_token"] = access_token
    if new_refresh_token:
        session["refresh_token"] = new_refresh_token
    return True


def _retry_on_expired_jwt(action, *, on_failure=None):
    """Run a Supabase action and retry once after refreshing the JWT.

    This handles the real-world case where a user session is valid but the
    short-lived access token expired between page loads. A single refresh keeps
    the inventory/history pages from crashing with a raw API traceback.
    """
    try:
        return action()
    except Exception as exc:
        msg = str(exc)
        if "JWT expired" not in msg and "PGRST303" not in msg:
            raise

        if not refresh_user_token():
            session.clear()
            if on_failure is not None:
                return on_failure()
            raise RuntimeError("Your session expired. Please log in again.") from exc

        try:
            return action()
        except Exception:
            session.clear()
            if on_failure is not None:
                return on_failure()
            raise RuntimeError("Your session expired. Please log in again.") from exc


def save_recipe_history(preferences: dict, recipes: list):
    """Log one recipe request/response pair for the current logged-in user."""
    user_id = session.get("user_id")
    if not user_id:
        return  # not logged in, nothing to save

    def _insert():
        return get_user_client().table("recipe_history").insert(
            {
                "user_id": user_id,
                "preferences": preferences,
                "recipes": recipes,
            }
        ).execute()

    try:
        _retry_on_expired_jwt(_insert, on_failure=lambda: None)
    except RuntimeError:
        return
    except Exception:
        raise


def get_recipe_history():
    """Return this user's past requests, most recent first."""
    user_id = session.get("user_id")
    if not user_id:
        return []

    def _read_history():
        result = (
            get_user_client()
            .table("recipe_history")
            .select("*")
            .eq("user_id", user_id)
            .order("created_at", desc=True)
            .execute()
        )
        return result.data or []

    try:
        return _retry_on_expired_jwt(_read_history, on_failure=lambda: [])
    except RuntimeError:
        return []
    except Exception:
        raise


def determine_role(email: str) -> str | None:
    """Return 'student', 'staff', or None if the email isn't a CSUF address."""
    domain = email.strip().lower().split("@")[-1]

    if domain == "csu.fullerton.edu":
        return "student"
    if domain == "fullerton.edu" or domain.endswith(".fullerton.edu"):
        # csu.fullerton.edu is caught above, so anything else under
        # fullerton.edu (including the bare domain) is staff.
        return "staff"
    return None


def signup_user(email: str, password: str):
    """Create a Supabase auth user with a role tag. Returns (ok, message)."""
    role = determine_role(email)
    if role is None:
        return False, "Please sign up with your @csu.fullerton.edu (student) or @fullerton.edu (staff) email."

    try:
        result = get_supabase().auth.sign_up(
            {
                "email": email,
                "password": password,
                "options": {"data": {"role": role}},
            }
        )
    except Exception as exc:  # Supabase raises AuthApiError on duplicate emails etc.
        return False, str(exc)

    if result.user is None:
        return False, "Could not create account. Please try again."

    return True, role


def login_user(email: str, password: str):
    """Verify credentials with Supabase and start a Flask session."""
    try:
        result = get_supabase().auth.sign_in_with_password(
            {"email": email, "password": password}
        )
    except Exception as exc:
        return False, str(exc)

    if result.user is None or result.session is None:
        return False, "Invalid email or password."

    session["user_id"] = result.user.id
    session["email"] = result.user.email
    session["role"] = (result.user.user_metadata or {}).get("role", "student")
    session["first_name"] = (result.user.user_metadata or {}).get("first_name", "")
    session["last_name"] = (result.user.user_metadata or {}).get("last_name", "")
    # Persist both access and refresh tokens so we can refresh the session
    # when the short-lived JWT expires.
    session["access_token"] = result.session.access_token
    session["refresh_token"] = getattr(result.session, "refresh_token", None)

    return True, None


def logout_user():
    session.clear()


def login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if "user_id" not in session:
            flash("Please log in to continue.")
            return redirect(url_for("login"))
        return view(*args, **kwargs)
    return wrapped
