#!/usr/bin/env python3
"""FastAPI backend for the Integration Catalog application."""

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from pydantic import BaseModel
import sqlite3
import os
from typing import Optional

import json as _json

try:
    import verisure
except ImportError:
    verisure = None

BASE_DIR    = os.path.dirname(os.path.abspath(__file__))
DB_PATH     = os.path.join(BASE_DIR, 'catalog.db')
STATIC_DIR  = os.path.join(BASE_DIR, 'static')
COOKIE_DIR  = os.path.join(BASE_DIR, '.verisure_sessions')
os.makedirs(COOKIE_DIR, exist_ok=True)

app = FastAPI(title="Integration Catalog API", docs_url="/api/docs", redoc_url=None)


# ── helpers ───────────────────────────────────────────────────────────────────

def get_db() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def valid_tables(conn: sqlite3.Connection) -> list[str]:
    return [r[0] for r in conn.execute("SELECT table_name FROM _tables").fetchall()]


def check_table(table: str, conn: sqlite3.Connection):
    if table not in valid_tables(conn):
        raise HTTPException(status_code=404, detail=f"Table '{table}' not found")


def table_columns(table: str, conn: sqlite3.Connection) -> list[str]:
    return [r["name"] for r in conn.execute(f'PRAGMA table_info("{table}")').fetchall()
            if r["name"] != "id"]


# ── tables & schema ───────────────────────────────────────────────────────────

@app.get("/api/tables")
def list_tables():
    conn = get_db()
    rows = conn.execute("SELECT * FROM _tables").fetchall()
    result = []
    for t in rows:
        try:
            count = conn.execute(f'SELECT COUNT(*) FROM "{t["table_name"]}"').fetchone()[0]
        except Exception:
            count = 0
        result.append({
            "name":         t["table_name"],
            "display_name": t["display_name"],
            "sheet_name":   t["sheet_name"],
            "count":        count,
        })
    conn.close()
    return result


@app.get("/api/{table}/schema")
def get_schema(table: str):
    conn = get_db()
    check_table(table, conn)
    pragma = conn.execute(f'PRAGMA table_info("{table}")').fetchall()
    labels = {r["col_name"]: r["label"]
              for r in conn.execute(
                  "SELECT col_name, label FROM _column_labels WHERE table_name = ?", [table]
              )}
    schema = []
    for row in pragma:
        col = row["name"]
        if col == "id":
            continue
        schema.append({
            "name":  col,
            "type":  row["type"],
            "label": labels.get(col, col.replace("_", " ").title()),
        })
    conn.close()
    return schema


# ── CRUD ──────────────────────────────────────────────────────────────────────

@app.get("/api/{table}/records")
def list_records(
    table:      str,
    page:       int  = Query(1,    ge=1),
    page_size:  int  = Query(25,   ge=1, le=500),
    search:     str  = Query(""),
    search_col: str  = Query(""),
    sort_by:    str  = Query(""),
    sort_desc:  bool = Query(False),
):
    conn = get_db()
    check_table(table, conn)
    cols = table_columns(table, conn)

    where, params = "", []
    if search:
        s = search.strip()
        if search_col and search_col in cols:
            where  = f'WHERE CAST("{search_col}" AS TEXT) LIKE ?'
            params = [f"%{s}%"]
        else:
            conds  = [f'CAST("{c}" AS TEXT) LIKE ?' for c in cols]
            where  = "WHERE " + " OR ".join(conds)
            params = [f"%{s}%"] * len(cols)

    total = conn.execute(f'SELECT COUNT(*) FROM "{table}" {where}', params).fetchone()[0]

    order = "ORDER BY id"
    if sort_by and sort_by in cols:
        order = f'ORDER BY CAST("{sort_by}" AS TEXT) {"DESC" if sort_desc else "ASC"}'

    offset = (page - 1) * page_size
    rows = conn.execute(
        f'SELECT * FROM "{table}" {where} {order} LIMIT ? OFFSET ?',
        params + [page_size, offset],
    ).fetchall()
    conn.close()

    return {
        "data":      [dict(r) for r in rows],
        "total":     total,
        "page":      page,
        "page_size": page_size,
        "pages":     max(1, (total + page_size - 1) // page_size),
        "columns":   ["id"] + cols,
    }


@app.get("/api/{table}/records/{record_id}")
def get_record(table: str, record_id: int):
    conn = get_db()
    check_table(table, conn)
    row = conn.execute(f'SELECT * FROM "{table}" WHERE id = ?', [record_id]).fetchone()
    conn.close()
    if not row:
        raise HTTPException(404, "Record not found")
    return dict(row)


@app.post("/api/{table}/records")
async def create_record(table: str, request: Request):
    conn = get_db()
    check_table(table, conn)
    data: dict = await request.json()
    data.pop("id", None)
    if not data:
        raise HTTPException(400, "No data provided")

    cols         = list(data.keys())
    quoted_cols  = ", ".join([f'"{c}"' for c in cols])
    placeholders = ", ".join(["?"] * len(cols))
    values       = [data[c] or None for c in cols]

    cursor = conn.execute(f'INSERT INTO "{table}" ({quoted_cols}) VALUES ({placeholders})', values)
    conn.commit()
    row = conn.execute(f'SELECT * FROM "{table}" WHERE id = ?', [cursor.lastrowid]).fetchone()
    conn.close()
    return {"success": True, "record": dict(row)}


@app.put("/api/{table}/records/{record_id}")
async def update_record(table: str, record_id: int, request: Request):
    conn = get_db()
    check_table(table, conn)
    data: dict = await request.json()
    data.pop("id", None)
    if not data:
        raise HTTPException(400, "No data provided")

    set_clause = ", ".join([f'"{k}" = ?' for k in data.keys()])
    values     = [v or None for v in data.values()] + [record_id]

    conn.execute(f'UPDATE "{table}" SET {set_clause} WHERE id = ?', values)
    conn.commit()
    row = conn.execute(f'SELECT * FROM "{table}" WHERE id = ?', [record_id]).fetchone()
    conn.close()
    if not row:
        raise HTTPException(404, "Record not found")
    return {"success": True, "record": dict(row)}


@app.delete("/api/{table}/records/{record_id}")
def delete_record(table: str, record_id: int):
    conn = get_db()
    check_table(table, conn)
    affected = conn.execute(f'DELETE FROM "{table}" WHERE id = ?', [record_id]).rowcount
    conn.commit()
    conn.close()
    if not affected:
        raise HTTPException(404, "Record not found")
    return {"success": True}


# ── cross-reference & search ──────────────────────────────────────────────────

@app.get("/api/cross-ref/{ridepos_id}")
def cross_reference(ridepos_id: str):
    """Return all records across all tables that share this RIDEPOS ID."""
    conn = get_db()
    tables = valid_tables(conn)
    result = {}
    for table in tables:
        try:
            cols   = table_columns(table, conn)
            id_col = next((c for c in cols if "ridepos" in c.lower()), None)
            if id_col:
                rows = conn.execute(
                    f'SELECT * FROM "{table}" WHERE CAST("{id_col}" AS TEXT) = ?',
                    [ridepos_id],
                ).fetchall()
                if rows:
                    result[table] = [dict(r) for r in rows]
        except Exception:
            pass
    conn.close()
    return result


@app.get("/api/global-search")
def global_search(q: str = Query(..., min_length=1)):
    """Full-text search across every table."""
    conn  = get_db()
    tables = valid_tables(conn)
    result = {}
    pat = f"%{q}%"
    for table in tables:
        try:
            cols   = table_columns(table, conn)
            conds  = [f'CAST("{c}" AS TEXT) LIKE ?' for c in cols]
            where  = " OR ".join(conds)
            params = [pat] * len(cols)
            rows   = conn.execute(
                f'SELECT * FROM "{table}" WHERE {where} LIMIT 20', params
            ).fetchall()
            if rows:
                result[table] = [dict(r) for r in rows]
        except Exception:
            pass
    conn.close()
    return result


# ── Verisure auth ────────────────────────────────────────────────────────────

_verisure_sessions: dict[str, object] = {}


class LoginRequest(BaseModel):
    username: str
    password: str


class MfaRequest(BaseModel):
    username: str
    code: str


def _cookie_path(username: str) -> str:
    safe = username.replace("@", "_at_").replace(".", "_")
    return os.path.join(COOKIE_DIR, f"{safe}.cookie")


@app.post("/auth/login")
def verisure_login(body: LoginRequest):
    """Login and request MFA code in a single step."""
    if verisure is None:
        raise HTTPException(501, "vsure package is not installed")
    try:
        session = verisure.Session(body.username, body.password,
                                   cookie_file_name=_cookie_path(body.username))
        session.request_mfa()
        _verisure_sessions[body.username] = session
        return {"success": True, "mfa_required": True, "username": body.username}
    except verisure.Error as e:
        if "disabled" in str(e).lower():
            try:
                session.login()
                _verisure_sessions[body.username] = session
                return {"success": True, "mfa_required": False, "username": body.username}
            except Exception as e2:
                raise HTTPException(401, str(e2))
        raise HTTPException(401, str(e))
    except Exception as e:
        raise HTTPException(401, str(e))


@app.post("/auth/mfa/validate")
def verisure_mfa_validate(body: MfaRequest):
    """Validate the MFA code to complete login."""
    session = _verisure_sessions.get(body.username)
    if not session:
        raise HTTPException(400, "No pending MFA session")
    try:
        session.validate_mfa(body.code)
        return {"success": True, "message": "MFA validated, session active"}
    except Exception as e:
        raise HTTPException(401, str(e))


@app.post("/auth/resume")
def verisure_resume(body: LoginRequest):
    """Resume a session from a saved cookie — no MFA needed."""
    if verisure is None:
        raise HTTPException(501, "vsure package is not installed")
    cookie = _cookie_path(body.username)
    if not os.path.exists(cookie):
        raise HTTPException(401, "No saved session")
    try:
        session = verisure.Session(body.username, body.password,
                                   cookie_file_name=cookie)
        session.login_cookie()
        _verisure_sessions[body.username] = session
        return {"success": True, "username": body.username}
    except Exception:
        raise HTTPException(401, "Saved session expired — please login again")


@app.get("/auth/installations")
def verisure_installations(username: str = Query(...)):
    """List installations for an authenticated user."""
    session = _verisure_sessions.get(username)
    if not session:
        raise HTTPException(401, "Not logged in — call /auth/login first")
    try:
        raw = session.get_installations()
        installations = []
        if isinstance(raw, dict):
            account = raw.get("data", raw).get("account", {})
            installations = account.get("installations", [])
        elif isinstance(raw, list):
            installations = raw
        return {"installations": installations}
    except Exception as e:
        raise HTTPException(500, str(e))


DEVICES_QUERY = {
    "operationName": "Devices",
    "variables": {},
    "query": (
        "query Devices($giid: String!) {\n"
        "  installation(giid: $giid) {\n"
        "    devices {\n"
        "      deviceLabel\n"
        "      area\n"
        "      __typename\n"
        "    }\n"
        "    __typename\n"
        "  }\n"
        "}\n"
    ),
}


@app.get("/auth/devices")
def verisure_devices(username: str = Query(...), giid: str = Query(...)):
    """List all devices with friendly names."""
    session = _verisure_sessions.get(username)
    if not session:
        raise HTTPException(401, "Not logged in")
    try:
        session.set_giid(giid)
        q = dict(DEVICES_QUERY)
        q["variables"] = {"giid": giid}
        raw = session.request(q)
        devices = []
        if isinstance(raw, dict):
            devices = (raw.get("data", raw)
                       .get("installation", {})
                       .get("devices", []))
        return {"devices": [
            {"deviceLabel": d.get("deviceLabel", ""),
             "area": d.get("area", d.get("deviceLabel", ""))}
            for d in devices
        ]}
    except Exception as e:
        raise HTTPException(500, str(e))


@app.get("/auth/overview")
def verisure_overview(username: str = Query(...), giid: str = Query(...)):
    """Get arm state, door/window status, and device names."""
    session = _verisure_sessions.get(username)
    if not session:
        raise HTTPException(401, "Not logged in")
    try:
        session.set_giid(giid)
        devices_q = dict(DEVICES_QUERY)
        devices_q["variables"] = {"giid": giid}
        raw = session.request(session.arm_state(), session.door_window(), devices_q)
        result = {"arm_state": {}, "door_windows": []}

        if isinstance(raw, list) and len(raw) >= 2:
            arm_raw, dw_raw = raw[0], raw[1]
            dev_raw = raw[2] if len(raw) > 2 else {}
        elif isinstance(raw, dict):
            arm_raw = dw_raw = dev_raw = raw
        else:
            arm_raw, dw_raw, dev_raw = {}, {}, {}

        if isinstance(arm_raw, dict):
            inst = arm_raw.get("data", arm_raw).get("installation", {})
            result["arm_state"] = inst.get("armState", {})

        name_map = {}
        if isinstance(dev_raw, dict):
            devices = (dev_raw.get("data", dev_raw)
                       .get("installation", {})
                       .get("devices", []))
            for d in devices:
                label = d.get("deviceLabel", "")
                area = d.get("area", "")
                if label and area:
                    name_map[label] = area

        if isinstance(dw_raw, dict):
            dws = (dw_raw.get("data", dw_raw)
                   .get("installation", {})
                   .get("doorWindows", []))
            for dw in dws:
                label = dw.get("device", {}).get("deviceLabel", "")
                dw["friendlyName"] = name_map.get(label, label)
            result["door_windows"] = dws

        return result
    except Exception as e:
        raise HTTPException(500, str(e))


class CommandRequest(BaseModel):
    username: str
    giid: str
    command: str
    device_label: str = ""
    code: str = ""
    value: str = ""


READ_COMMANDS = {
    "arm_state", "broadband", "cameras", "cameras_image_series",
    "cameras_last_image", "capability", "climate", "door_window",
    "event_log", "firmware", "guardian_sos", "is_guardian_activated",
    "permissions", "remaining_sms", "smart_button", "smart_lock",
    "smartplugs", "user_trackings", "fetch_all_installations",
}

WRITE_COMMANDS = {
    "arm_away", "arm_home", "disarm",
    "door_lock", "door_unlock",
    "set_smartplug", "set_autolock_enabled",
    "charge_sms",
}


@app.post("/auth/command")
def verisure_command(body: CommandRequest):
    """Execute a verisure command."""
    session = _verisure_sessions.get(body.username)
    if not session:
        raise HTTPException(401, "Not logged in")

    cmd = body.command
    all_commands = READ_COMMANDS | WRITE_COMMANDS
    if cmd not in all_commands:
        raise HTTPException(400, f"Unknown command: {cmd}")

    try:
        session.set_giid(body.giid)
        method = getattr(session, cmd)

        if cmd in ("arm_away", "arm_home", "disarm"):
            if not body.code:
                raise HTTPException(400, "Code is required")
            op = method(code=body.code)
        elif cmd in ("door_lock", "door_unlock"):
            if not body.device_label or not body.code:
                raise HTTPException(400, "device_label and code are required")
            op = method(device_label=body.device_label, code=body.code)
        elif cmd == "set_smartplug":
            if not body.device_label:
                raise HTTPException(400, "device_label is required")
            op = method(device_label=body.device_label,
                        value=body.value.lower() in ("true", "1", "on"))
        elif cmd == "set_autolock_enabled":
            if not body.device_label:
                raise HTTPException(400, "device_label is required")
            op = method(device_label=body.device_label,
                        value=body.value.lower() in ("true", "1", "on"))
        elif cmd == "smartplug" and body.device_label:
            op = method(device_label=body.device_label)
        elif cmd == "camera_get_request_id" and body.device_label:
            op = method(device_label=body.device_label)
        elif cmd == "camera_capture" and body.device_label:
            op = method(device_label=body.device_label, request_id=body.value)
        else:
            op = method()

        raw = session.request(op)
        if isinstance(raw, dict) and "errors" in raw:
            errors = raw["errors"]
            msg = errors[0].get("message", "Request failed") if errors else "Unknown error"
            err_data = errors[0].get("data", {}) if errors else {}
            detail = err_data.get("errorMessage", msg)
            raise HTTPException(400, detail)
        return {"success": True, "result": raw}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, str(e))


@app.post("/auth/raw-query")
async def verisure_raw_query(request: Request):
    """Execute a raw GraphQL query (debug)."""
    body = await request.json()
    session = _verisure_sessions.get(body["username"])
    if not session:
        raise HTTPException(401, "Not logged in")
    try:
        raw = session.request(body["query"])
        return {"result": raw}
    except Exception as e:
        raise HTTPException(500, str(e))


@app.post("/auth/logout")
def verisure_logout(username: str = Query(...)):
    """Remove the cached Verisure session."""
    removed = _verisure_sessions.pop(username, None)
    return {"success": True, "was_active": removed is not None}


# ── static & root ─────────────────────────────────────────────────────────────

@app.get("/verisure")
def serve_verisure():
    return FileResponse(os.path.join(STATIC_DIR, "verisure.html"))


@app.get("/verisure/control")
def serve_verisure_control():
    return FileResponse(os.path.join(STATIC_DIR, "verisure_control.html"))


@app.get("/")
def serve_app():
    return FileResponse(os.path.join(STATIC_DIR, "index.html"))


app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


# ── entrypoint ────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import uvicorn

    if not os.path.exists(DB_PATH):
        print("Database not found — running import first...\n")
        from import_data import import_data
        import_data()

    print("\n Integration Catalog")
    print("  URL      : http://localhost:8000")
    print("  API docs : http://localhost:8000/api/docs\n")
    uvicorn.run("app:app", host="0.0.0.0", port=8000, reload=False)
