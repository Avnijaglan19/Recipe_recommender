"""
Staff-only route protection, kept separate from auth.py's general login
handling. This is the only decorator in the app that checks *role*, not
just login state -- e.g. it guards /inventory so students can't reach the
pantry-management pages even if they know the URL.
"""

from functools import wraps

from flask import session, redirect, url_for, flash


def staff_required(view):
    """Like auth.login_required, but also blocks students from staff-only
    pages (e.g. /inventory). A logged-in student hitting this just gets
    bounced back to the main app rather than seeing an error page."""
    @wraps(view)
    def wrapped(*args, **kwargs):
        if "user_id" not in session:
            return redirect(url_for("signup"))
        if session.get("role") != "staff":
            flash("That page is for pantry staff only.")
            return redirect(url_for("index"))
        return view(*args, **kwargs)
    return wrapped