#!/usr/bin/env python3
"""FastAPI backend for the Integration Catalog application."""

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
import sqlite3
import os
from typing import Optional

BASE_DIR   = os.path.dirname(os.path.abspath(__file__))
DB_PATH    = os.path.join(BASE_DIR, 'catalog.db')
STATIC_DIR = os.path.join(BASE_DIR, 'static')

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


# ── static & root ─────────────────────────────────────────────────────────────

app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.get("/")
def serve_app():
    return FileResponse(os.path.join(STATIC_DIR, "index.html"))


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
