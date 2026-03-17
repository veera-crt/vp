import json
import os
import uuid
from datetime import datetime, timezone

import requests
from werkzeug.security import check_password_hash, generate_password_hash
from flask import Flask, Response, abort, jsonify, redirect, request, send_from_directory


APP_ROOT = os.path.dirname(os.path.abspath(__file__))
HTML_DIR = os.path.join(APP_ROOT, "html")
STATIC_DIR = os.path.join(APP_ROOT, "static")
DATA_DIR = "/tmp/acacemia-data" if os.environ.get("VERCEL") else os.path.join(APP_ROOT, "data")
SESSION_FILE = os.path.join(DATA_DIR, "session.json")
REMINDERS_FILE = os.path.join(DATA_DIR, "reminders.json")
USERS_FILE = os.path.join(DATA_DIR, "users.json")
LOCAL_DATA_FILE = os.path.join(DATA_DIR, "local_data.json")

REMOTE_BASE = "https://trackitsrm.vercel.app"
REMOTE_API = f"{REMOTE_BASE}/api/trackit"
PROXY_ENABLED = os.environ.get("TRACKIT_PROXY", "1") == "1"
BOOTSTRAP_USERS = os.environ.get("TRACKIT_BOOTSTRAP", "1") == "1"
ADMIN_TOKEN = os.environ.get("TRACKIT_ADMIN_TOKEN", "")

HOP_BY_HOP = {
    "host",
    "content-length",
    "accept-encoding",
    "connection",
    "keep-alive",
    "proxy-authenticate",
    "proxy-authorization",
    "te",
    "trailers",
    "upgrade",
    "content-encoding",
    "transfer-encoding",
}


app = Flask(__name__)


def ensure_data_dir() -> None:
    os.makedirs(DATA_DIR, exist_ok=True)


def read_json(path: str, default):
    try:
        with open(path, "r", encoding="utf-8") as handle:
            return json.load(handle)
    except (FileNotFoundError, json.JSONDecodeError):
        return default


def write_json(path: str, data) -> None:
    try:
        ensure_data_dir()
        tmp_path = f"{path}.tmp"
        with open(tmp_path, "w", encoding="utf-8") as handle:
            json.dump(data, handle, ensure_ascii=True, indent=2)
        os.replace(tmp_path, path)
    except Exception as e:
        print(f"Storage Error for {path}: {str(e)}")


def load_session():
    return read_json(SESSION_FILE, None)


def save_session(session_data) -> None:
    write_json(SESSION_FILE, session_data)


def clear_session() -> None:
    if os.path.exists(SESSION_FILE):
        os.remove(SESSION_FILE)


def load_reminders():
    return read_json(REMINDERS_FILE, [])


def save_reminders(reminders) -> None:
    write_json(REMINDERS_FILE, reminders)


def load_users():
    return read_json(USERS_FILE, [])


def save_users(users) -> None:
    write_json(USERS_FILE, users)


def load_local_data():
    return read_json(
        LOCAL_DATA_FILE,
        {
            "userInfo": {"name": "Local User"},
            "attendance": [],
            "timetable": [],
            "marks": [],
            "courses": [],
            "calendar": [],
            "optionalClasses": [],
        },
    )


def save_local_data(data) -> None:
    write_json(LOCAL_DATA_FILE, data)


def filtered_headers():
    # Drop headers that link to localhost or interfere with auth
    excluded = HOP_BY_HOP | {"origin", "referer", "cookie", "host"}
    return {
        key: value
        for key, value in request.headers.items()
        if key.lower() not in excluded
    }


def build_response(remote_response: requests.Response) -> Response:
    excluded = {"content-encoding", "content-length", "transfer-encoding", "connection"}
    headers = [
        (key, value)
        for key, value in remote_response.headers.items()
        if key.lower() not in excluded
    ]
    return Response(remote_response.content, status=remote_response.status_code, headers=headers)


def remote_request(path: str) -> requests.Response:
    url = f"{REMOTE_API}/{path}"
    session = load_session()
    cookies = {}
    if session and session.get("cookies"):
        # Parse cookies string from session: "key1=val1; key2=val2;"
        cookie_parts = session["cookies"].split(";")
        for part in cookie_parts:
            if "=" in part:
                name, value = part.strip().split("=", 1)
                cookies[name] = value
    
    return requests.request(
        method=request.method,
        url=url,
        headers=filtered_headers(),
        data=request.get_data(),
        cookies=cookies,
        allow_redirects=False,
        timeout=30,
    )


def serve_html(filename: str):
    return send_from_directory(HTML_DIR, filename)


def local_sync_response(field: str):
    session = load_session()
    if not session or "data" not in session or field not in session["data"]:
        return jsonify({"success": False, "error": "NO_AUTH_COOKIES_FOUND"}), 401
    return jsonify({"success": True, field: session["data"][field]})


@app.route("/")
def route_root():
    return serve_html("index.html")


@app.route("/dashboard")
def route_dashboard():
    return serve_html("dashboard.html")


@app.route("/dashboard/attendance")
def route_dashboard_attendance():
    return serve_html("dashboard_attendance.html")


@app.route("/dashboard/calendar")
def route_dashboard_calendar():
    return redirect("/dashboard/planner")


@app.route("/dashboard/courses")
def route_dashboard_courses():
    return serve_html("dashboard_courses.html")


@app.route("/dashboard/planner")
def route_dashboard_planner():
    return serve_html("dashboard_planner.html")


@app.route("/dashboard/marks")
def route_dashboard_marks():
    return serve_html("dashboard_marks.html")


@app.route("/dashboard/profile")
def route_dashboard_profile():
    return serve_html("dashboard_profile.html")


@app.route("/dashboard/reminder")
def route_dashboard_reminder():
    return serve_html("dashboard_reminder.html")


@app.route("/dashboard/timetable")
def route_dashboard_timetable():
    return serve_html("dashboard_timetable.html")


@app.route("/maintenance")
def route_maintenance():
    return serve_html("maintenance.html")


@app.route("/attendance")
def route_shortcut_attendance():
    return redirect("/dashboard/attendance", code=302)


@app.route("/timetable")
def route_shortcut_timetable():
    return redirect("/dashboard/timetable", code=302)


@app.route("/_next/static/<path:filename>")
def next_static(filename: str):
    return send_from_directory(os.path.join(STATIC_DIR, "_next/static"), filename)


@app.route("/api/trackit/auth/login", methods=["POST"])
def api_login():
    if PROXY_ENABLED:
        try:
            resp = remote_request("auth/login")
            payload = None
            if "application/json" in resp.headers.get("Content-Type", ""):
                payload = resp.json()
            if payload and payload.get("isAuthenticated"):
                save_session(payload)
            return build_response(resp)
        except requests.RequestException:
            pass

    payload = request.get_json(silent=True) or {}
    username = str(payload.get("username", "")).strip().lower()
    password = str(payload.get("password", "")).strip()

    users = load_users()
    if not users and BOOTSTRAP_USERS and username and password:
        users = [{"username": username, "passwordHash": generate_password_hash(password)}]
        save_users(users)

    user = next((item for item in users if item["username"] == username), None)
    if not user or not check_password_hash(user["passwordHash"], password):
        return jsonify({"isAuthenticated": False, "error": "INVALID_CREDENTIAL"}), 401

    local_data = load_local_data()
    session = {
        "isAuthenticated": True,
        "username": username,
        "sessionId": str(uuid.uuid4()),
        "cookies": "",
        "loginDateTime": datetime.now(timezone.utc).isoformat(),
        "data": local_data,
    }
    save_session(session)
    return jsonify(session)


@app.route("/api/trackit/auth/logout", methods=["POST"])
def api_logout():
    if PROXY_ENABLED:
        try:
            resp = remote_request("auth/logout")
            clear_session()
            return build_response(resp)
        except requests.RequestException:
            pass

    clear_session()
    return jsonify({"success": True})


@app.route("/api/trackit/auth", methods=["GET"])
def api_auth_check():
    return jsonify({"isAuthenticated": bool(load_session())})


@app.route("/api/trackit/health", methods=["GET"])
def api_health():
    return jsonify({"status": "ok"})


@app.route("/api/trackit/prisma-check", methods=["GET"])
def api_prisma_check():
    return jsonify({"ok": True})


@app.route("/api/trackit/internal/post-login", methods=["POST"])
def api_post_login():
    return jsonify({"success": True})


@app.route("/api/trackit/sync/updateUserInfo", methods=["POST"])
def api_sync_user_info():
    if PROXY_ENABLED:
        try:
            resp = remote_request("sync/updateUserInfo")
            if resp.status_code == 200:
                payload = resp.json()
                if payload and payload.get("userInfo"):
                    session = load_session() or {}
                    session_data = session.get("data", {})
                    session_data["userInfo"] = payload["userInfo"]
                    session["data"] = session_data
                    save_session(session)
                    
                    # Inject username if not present in payload
                    if "username" not in payload:
                        payload["username"] = session.get("username")
                    return jsonify(payload)
                return build_response(resp)
            # On 401/error, fall through to local fallback
        except Exception:
            pass

    session = load_session()
    if not session or "data" not in session or "userInfo" not in session["data"]:
        return jsonify({"success": False, "error": "NO_AUTH_COOKIES_FOUND"}), 401
    return jsonify({
        "success": True, 
        "userInfo": session["data"]["userInfo"], 
        "username": session.get("username")
    })

def handle_sync(field: str):
    if PROXY_ENABLED:
        try:
            resp = remote_request(f"sync/update{field.capitalize()}")
            if resp.status_code == 200:
                payload = resp.json()
                if payload and payload.get("success") and field in payload:
                    session = load_session() or {}
                    session_data = session.get("data", {})
                    session_data[field] = payload[field]
                    session["data"] = session_data
                    save_session(session)
                return build_response(resp)
        except Exception:
            pass

    return local_sync_response(field)


@app.route("/api/trackit/sync/updateAttendance", methods=["POST"])
def api_sync_attendance():
    return handle_sync("attendance")


@app.route("/api/trackit/sync/updateMarks", methods=["POST"])
def api_sync_marks():
    return handle_sync("marks")


@app.route("/api/trackit/sync/updateTimetable", methods=["POST"])
def api_sync_timetable():
    return handle_sync("timetable")


@app.route("/api/trackit/sync/updateCalendar", methods=["POST"])
def api_sync_calendar():
    return handle_sync("calendar")


@app.route("/api/trackit/sync/updateCourses", methods=["POST"])
def api_sync_courses():
    return handle_sync("courses")


@app.route("/api/trackit/reminders", methods=["GET", "POST"])
def api_reminders():
    if request.method == "GET":
        return jsonify(load_reminders())

    payload = request.get_json(silent=True) or {}
    title = str(payload.get("title", "")).strip()
    remind_at = str(payload.get("remindAt", "")).strip()
    description = str(payload.get("description", "")).strip() or None

    if not title or not remind_at:
        return jsonify({"error": "MISSING_FIELDS"}), 400

    reminders = load_reminders()
    if len(reminders) >= 10:
        return jsonify({"error": "MAX_REMINDERS_REACHED"}), 400

    reminder = {
        "id": uuid.uuid4().hex,
        "title": title,
        "description": description,
        "remindAt": remind_at,
        "status": "PENDING",
        "createdAt": datetime.now(timezone.utc).isoformat(),
    }
    reminders.append(reminder)
    save_reminders(reminders)
    return jsonify(reminder)


@app.route("/api/trackit/reminders/<reminder_id>", methods=["GET", "PATCH", "DELETE"])
def api_reminder_detail(reminder_id: str):
    reminders = load_reminders()
    reminder = next((item for item in reminders if item["id"] == reminder_id), None)
    if not reminder:
        return jsonify({"error": "NOT_FOUND"}), 404

    if request.method == "GET":
        return jsonify(reminder)

    if request.method == "DELETE":
        reminders = [item for item in reminders if item["id"] != reminder_id]
        save_reminders(reminders)
        return jsonify({"success": True})

    payload = request.get_json(silent=True) or {}
    if "title" in payload:
        reminder["title"] = str(payload["title"]).strip()
    if "description" in payload:
        reminder["description"] = (
            str(payload["description"]).strip() or None
        )
    if "remindAt" in payload:
        reminder["remindAt"] = str(payload["remindAt"]).strip()
    if "status" in payload:
        reminder["status"] = str(payload["status"]).strip().upper()

    reminder["updatedAt"] = datetime.now(timezone.utc).isoformat()
    save_reminders(reminders)
    return jsonify(reminder)


@app.route("/api/trackit/local/data", methods=["GET", "POST"])
def api_local_data():
    if ADMIN_TOKEN and request.headers.get("X-Admin-Token") != ADMIN_TOKEN:
        return jsonify({"error": "UNAUTHORIZED"}), 401

    if request.method == "GET":
        return jsonify(load_local_data())

    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        return jsonify({"error": "INVALID_PAYLOAD"}), 400
    save_local_data(payload)
    session = load_session()
    if session:
        session["data"] = payload
        save_session(session)
    return jsonify({"success": True})


@app.route("/api/trackit/<path:subpath>", methods=["GET", "POST", "PATCH", "DELETE"])
def api_catchall(subpath: str):
    if PROXY_ENABLED:
        try:
            resp = remote_request(subpath)
            return build_response(resp)
        except requests.RequestException:
            return jsonify({"error": "UPSTREAM_UNAVAILABLE"}), 503
    return jsonify({"error": "NOT_IMPLEMENTED"}), 501


@app.route("/<path:filename>")
def root_static(filename: str):
    static_path = os.path.join(STATIC_DIR, filename)
    if os.path.isfile(static_path):
        return send_from_directory(STATIC_DIR, filename)
    return abort(404)


if __name__ == "__main__":
    ensure_data_dir()
    app.run(host="0.0.0.0", port=5001, debug=True)
