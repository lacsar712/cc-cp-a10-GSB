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


def require_writer(request: web.Request) -> dict:
    user = require_user(request)
    if user["role"] != "writer":
        raise web.HTTPForbidden(
            text=json.dumps({"detail": "仅记录员可提交读数"}, ensure_ascii=False),
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


def _package_json(pkg, items=None) -> dict:
    out = {
        "id": pkg["id"],
        "created_by": pkg["created_by"],
        "created_at": pkg["created_at"].isoformat() if pkg["created_at"] else None,
        "item_count": pkg["item_count"],
    }
    if items is not None:
        out["items"] = items
    return out


def _item_json(row) -> dict:
    return {
        "reading_id": row["reading_id"],
        "probe_id": row["probe_id"],
        "temp_c": row["temp_c"],
        "status": row["status"],
    }


async def create_package(request: web.Request) -> web.Response:
    """一键打包：把打包瞬间仍处于 pending/processing 的读数冻结进发车核对包。"""
    user = require_user(request)
    if user["role"] != "writer":
        raise web.HTTPForbidden(
            text=json.dumps({"detail": "仅记录员可执行一键打包"}, ensure_ascii=False),
            content_type="application/json",
        )
    pool: asyncpg.Pool = request.app["pool"]
    async with pool.acquire() as conn:
        async with conn.transaction():
            rows = await conn.fetch(
                """
                SELECT id, probe_id, temp_c, status
                FROM probe_readings
                WHERE status IN ('pending', 'processing')
                ORDER BY id
                FOR UPDATE
                """
            )
            if not rows:
                raise web.HTTPBadRequest(
                    text=json.dumps(
                        {"detail": "当前没有未办结读数可打包"}, ensure_ascii=False
                    ),
                    content_type="application/json",
                )
            pkg = await conn.fetchrow(
                """
                INSERT INTO departure_packages (created_by, created_at, item_count)
                VALUES ($1, now(), $2)
                RETURNING id, created_by, created_at, item_count
                """,
                user["username"],
                len(rows),
            )
            items = []
            for r in rows:
                await conn.execute(
                    """
                    INSERT INTO departure_package_items
                        (package_id, reading_id, probe_id, temp_c, status)
                    VALUES ($1, $2, $3, $4, $5)
                    """,
                    pkg["id"],
                    r["id"],
                    r["probe_id"],
                    r["temp_c"],
                    r["status"],
                )
                items.append(
                    {
                        "reading_id": r["id"],
                        "probe_id": r["probe_id"],
                        "temp_c": r["temp_c"],
                        "status": r["status"],
                    }
                )
    return web.json_response(
        {
            **_package_json(pkg, items),
            "message": f"已打包 {len(items)} 笔未办结读数",
        },
        status=201,
    )


async def list_packages(request: web.Request) -> web.Response:
    require_user(request)
    pool: asyncpg.Pool = request.app["pool"]
    rows = await pool.fetch(
        """
        SELECT id, created_by, created_at, item_count
        FROM departure_packages
        ORDER BY id DESC
        """
    )
    return web.json_response([_package_json(r) for r in rows])


async def get_package(request: web.Request) -> web.Response:
    require_user(request)
    package_id = int(request.match_info["package_id"])
    pool: asyncpg.Pool = request.app["pool"]
    pkg = await pool.fetchrow(
        """
        SELECT id, created_by, created_at, item_count
        FROM departure_packages
        WHERE id = $1
        """,
        package_id,
    )
    if not pkg:
        raise web.HTTPNotFound(
            text=json.dumps({"detail": "发车核对包不存在"}, ensure_ascii=False),
            content_type="application/json",
        )
    rows = await pool.fetch(
        """
        SELECT reading_id, probe_id, temp_c, status
        FROM departure_package_items
        WHERE package_id = $1
        ORDER BY id
        """,
        package_id,
    )
    return web.json_response(_package_json(pkg, [_item_json(r) for r in rows]))


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
    app.router.add_post("/api/packages", create_package)
    app.router.add_get("/api/packages", list_packages)
    app.router.add_get("/api/packages/{package_id:[0-9]+}", get_package)
    app.on_startup.append(on_startup)
    app.on_cleanup.append(on_cleanup)
    return app


if __name__ == "__main__":
    web.run_app(create_app(), host="0.0.0.0", port=8000)
