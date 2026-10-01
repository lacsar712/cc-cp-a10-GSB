import json
import os
from datetime import datetime, timedelta, timezone

import asyncpg
import jwt
from aiohttp import web
from passlib.context import CryptContext

from db import create_pool, ensure_schema_async, seed_if_empty
from rules import judge_temp

SECRET = os.environ.get("JWT_SECRET", "coldchain-probe-dev-secret")
pwd = CryptContext(schemes=["bcrypt"], deprecated="auto")

USERS = {
    "logger": {"role": "writer", "password_hash": pwd.hash("log123456")},
    "watcher": {"role": "reader", "password_hash": pwd.hash("watch123456")},
}


def _auth_header(request: web.Request) -> str | None:
    auth = request.headers.get("Authorization", "")
    if auth.startswith("Bearer "):
        return auth[7:].strip()
    return None


def _decode_user(token: str | None) -> dict | None:
    if not token:
        return None
    try:
        payload = jwt.decode(token, SECRET, algorithms=["HS256"])
    except jwt.InvalidTokenError:
        return None
    sub = payload.get("sub")
    if sub not in USERS:
        return None
    return {"username": sub, "role": payload.get("role")}


def require_user(request: web.Request) -> dict:
    user = _decode_user(_auth_header(request))
    if not user:
        raise web.HTTPUnauthorized(text=json.dumps({"detail": "未登录"}, ensure_ascii=False), content_type="application/json")
    return user


def require_writer(request: web.Request, detail: str = "仅记录员可提交读数") -> dict:
    user = require_user(request)
    if user["role"] != "writer":
        raise web.HTTPForbidden(
            text=json.dumps({"detail": detail}, ensure_ascii=False),
            content_type="application/json",
        )
    return user


async def health(_request: web.Request) -> web.Response:
    return web.json_response({"status": "ok", "service": "coldchain-probe-desk"})


async def login(request: web.Request) -> web.Response:
    try:
        body = await request.json()
    except json.JSONDecodeError as exc:
        raise web.HTTPBadRequest(text="invalid json") from exc
    username = str(body.get("username", "")).strip()
    password = str(body.get("password", ""))
    user = USERS.get(username)
    if not user or not pwd.verify(password, user["password_hash"]):
        raise web.HTTPUnauthorized(
            text=json.dumps({"detail": "用户名或密码错误"}, ensure_ascii=False),
            content_type="application/json",
        )
    exp = datetime.now(timezone.utc) + timedelta(hours=8)
    token = jwt.encode(
        {"sub": username, "role": user["role"], "exp": exp},
        SECRET,
        algorithm="HS256",
    )
    return web.json_response(
        {"access_token": token, "username": username, "role": user["role"]}
    )


async def list_readings(request: web.Request) -> web.Response:
    require_user(request)
    pool: asyncpg.Pool = request.app["pool"]
    rows = await pool.fetch(
        """
        SELECT id, probe_id, temp_c, verdict, reason, status, created_by, created_at, processed_at
        FROM probe_readings
        ORDER BY id DESC
        """
    )
    out = []
    for r in rows:
        out.append(
            {
                "id": r["id"],
                "probe_id": r["probe_id"],
                "temp_c": r["temp_c"],
                "verdict": r["verdict"],
                "reason": r["reason"],
                "status": r["status"],
                "created_by": r["created_by"],
                "created_at": r["created_at"].isoformat() if r["created_at"] else None,
                "processed_at": r["processed_at"].isoformat() if r["processed_at"] else None,
            }
        )
    return web.json_response(out)


async def create_reading(request: web.Request) -> web.Response:
    user = require_writer(request)
    try:
        body = await request.json()
    except json.JSONDecodeError as exc:
        raise web.HTTPBadRequest(text="invalid json") from exc
    probe_id = str(body.get("probe_id", "")).strip()
    if not probe_id:
        raise web.HTTPBadRequest(
            text=json.dumps({"detail": "探头编号不能为空"}, ensure_ascii=False),
            content_type="application/json",
        )
    try:
        temp_c = float(body.get("temp_c"))
    except (TypeError, ValueError) as exc:
        raise web.HTTPBadRequest(
            text=json.dumps({"detail": "温度必须是数字"}, ensure_ascii=False),
            content_type="application/json",
        ) from exc

    pool: asyncpg.Pool = request.app["pool"]
    row = await pool.fetchrow(
        """
        INSERT INTO probe_readings (probe_id, temp_c, status, created_by, created_at)
        VALUES ($1, $2, 'pending', $3, now())
        RETURNING id, probe_id, temp_c, verdict, reason, status, created_by, created_at, processed_at
        """,
        probe_id,
        temp_c,
        user["username"],
    )
    return web.json_response(
        {
            "id": row["id"],
            "probe_id": row["probe_id"],
            "temp_c": row["temp_c"],
            "verdict": row["verdict"],
            "reason": row["reason"],
            "status": row["status"],
            "created_by": row["created_by"],
            "created_at": row["created_at"].isoformat() if row["created_at"] else None,
            "processed_at": None,
            "message": "已入队，后台工人将认领并判定",
        },
        status=201,
    )


async def package_payload(conn: asyncpg.Connection, package_id: int) -> dict | None:
    package = await conn.fetchrow(
        """
        SELECT id, item_count, created_by, created_at
        FROM departure_packages
        WHERE id = $1
        """,
        package_id,
    )
    if not package:
        return None
    items = await conn.fetch(
        """
        SELECT reading_id, probe_id, temp_c, reading_status, created_at
        FROM departure_package_items
        WHERE package_id = $1
        ORDER BY id
        """,
        package_id,
    )
    return {
        "id": package["id"],
        "item_count": package["item_count"],
        "created_by": package["created_by"],
        "created_at": package["created_at"].isoformat() if package["created_at"] else None,
        "items": [
            {
                "reading_id": item["reading_id"],
                "probe_id": item["probe_id"],
                "temp_c": item["temp_c"],
                "reading_status": item["reading_status"],
                "created_at": item["created_at"].isoformat() if item["created_at"] else None,
            }
            for item in items
        ],
    }


async def list_packages(request: web.Request) -> web.Response:
    require_user(request)
    pool: asyncpg.Pool = request.app["pool"]
    rows = await pool.fetch(
        """
        SELECT id, item_count, created_by, created_at
        FROM departure_packages
        ORDER BY id DESC
        """
    )
    return web.json_response(
        [
            {
                "id": r["id"],
                "item_count": r["item_count"],
                "created_by": r["created_by"],
                "created_at": r["created_at"].isoformat() if r["created_at"] else None,
            }
            for r in rows
        ]
    )


async def create_package(request: web.Request) -> web.Response:
    user = require_writer(request, "仅记录员可一键打包")
    pool: asyncpg.Pool = request.app["pool"]
    async with pool.acquire() as conn:
        async with conn.transaction():
            rows = await conn.fetch(
                """
                SELECT id, probe_id, temp_c, status, created_at
                FROM probe_readings
                WHERE status IN ('pending', 'processing')
                ORDER BY id
                FOR UPDATE
                """
            )
            if not rows:
                raise web.HTTPConflict(
                    text=json.dumps({"detail": "当前没有待审或处理中的读数"}, ensure_ascii=False),
                    content_type="application/json",
                )
            package_id = await conn.fetchval(
                """
                INSERT INTO departure_packages (item_count, created_by, created_at)
                VALUES ($1, $2, now())
                RETURNING id
                """,
                len(rows),
                user["username"],
            )
            await conn.executemany(
                """
                INSERT INTO departure_package_items
                    (package_id, reading_id, probe_id, temp_c, reading_status, created_at)
                VALUES ($1, $2, $3, $4, $5, $6)
                """,
                [
                    (
                        package_id,
                        row["id"],
                        row["probe_id"],
                        row["temp_c"],
                        row["status"],
                        row["created_at"],
                    )
                    for row in rows
                ],
            )
            payload = await package_payload(conn, package_id)
    return web.json_response(payload, status=201)


async def get_package(request: web.Request) -> web.Response:
    require_user(request)
    try:
        package_id = int(request.match_info["package_id"])
    except ValueError as exc:
        raise web.HTTPNotFound(text=json.dumps({"detail": "核对包不存在"}, ensure_ascii=False),
                               content_type="application/json") from exc
    pool: asyncpg.Pool = request.app["pool"]
    payload = await package_payload(pool, package_id)
    if not payload:
        raise web.HTTPNotFound(
            text=json.dumps({"detail": "核对包不存在"}, ensure_ascii=False),
            content_type="application/json",
        )
    return web.json_response(payload)


async def on_startup(app: web.Application) -> None:
    pool = await create_pool()
    app["pool"] = pool
    await ensure_schema_async(pool)
    await seed_if_empty(pool)


async def on_cleanup(app: web.Application) -> None:
    pool: asyncpg.Pool = app.get("pool")
    if pool:
        await pool.close()


def create_app() -> web.Application:
    app = web.Application()
    app.router.add_get("/api/health", health)
    app.router.add_post("/api/auth/login", login)
    app.router.add_get("/api/readings", list_readings)
    app.router.add_post("/api/readings", create_reading)
    app.router.add_get("/api/departure-packages", list_packages)
    app.router.add_post("/api/departure-packages", create_package)
    app.router.add_get("/api/departure-packages/{package_id}", get_package)
    app.on_startup.append(on_startup)
    app.on_cleanup.append(on_cleanup)
    return app


if __name__ == "__main__":
    web.run_app(create_app(), host="0.0.0.0", port=8000)
