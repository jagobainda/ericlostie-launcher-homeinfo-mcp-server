import json
import os
import tempfile
from pathlib import Path
from typing import Any

from mcp.server.fastmcp import FastMCP

# --- Configuración ---
JSON_PATH = Path(os.getenv("HOME_INFO_PATH", "/var/www/home-info/home-info.json"))

mcp = FastMCP("home-info-server")


# --- Helpers ---
def read_json() -> dict:
    if not JSON_PATH.exists():
        return {}
    with open(JSON_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def write_json(data: dict) -> None:
    """Escritura atómica: escribe en un tmp y luego reemplaza."""
    JSON_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp_fd, tmp_path = tempfile.mkstemp(dir=JSON_PATH.parent, suffix=".tmp")
    try:
        with os.fdopen(tmp_fd, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(tmp_path, JSON_PATH)
    except Exception:
        os.unlink(tmp_path)
        raise


# --- Herramientas MCP ---

@mcp.tool()
def get_home_info() -> dict[str, Any]:
    """Devuelve el contenido completo del JSON del launcher."""
    return read_json()


@mcp.tool()
def add_news(title: str, body: str, date: str | None = None, expires_days: int | None = None) -> dict[str, str]:
    """
    Añade una novedad/noticia al launcher.

    Args:
        title:       Título de la novedad.
        body:        Cuerpo o descripción.
        date:        Fecha en formato ISO (ej: 2025-03-12). Si se omite, usa hoy.
        expires_days: Días hasta que expira la novedad. Si se omite, no expira nunca.
    """
    from datetime import date as dt_date, timedelta

    data = read_json()
    data.setdefault("news", [])

    today = dt_date.today()
    entry = {
        "id": _next_id(data["news"]),
        "title": title,
        "body": body,
        "date": date or today.isoformat(),
        "expires_at": (today + timedelta(days=expires_days)).isoformat() if expires_days else None,
    }
    data["news"].append(entry)
    write_json(data)
    return {"status": "ok", "id": str(entry["id"])}


@mcp.tool()
def remove_news(news_id: int) -> dict[str, str]:
    """
    Elimina una novedad por su ID.

    Args:
        news_id: ID de la novedad a eliminar.
    """
    data = read_json()
    before = len(data.get("news", []))
    data["news"] = [n for n in data.get("news", []) if n.get("id") != news_id]
    if len(data["news"]) == before:
        return {"status": "error", "detail": f"No se encontró la novedad con id={news_id}"}
    write_json(data)
    return {"status": "ok"}


@mcp.tool()
def add_notification(message: str, level: str = "info", expires_days: int | None = None) -> dict[str, str]:
    """
    Añade una notificación al launcher (banners, alertas...).

    Args:
        message:      Texto de la notificación.
        level:        Nivel de la notificación: 'info', 'warning' o 'error'.
        expires_days: Días hasta que expira la notificación. Si se omite, no expira nunca.
    """
    from datetime import date as dt_date, timedelta

    if level not in ("info", "warning", "error"):
        return {"status": "error", "detail": "level debe ser 'info', 'warning' o 'error'"}

    data = read_json()
    data.setdefault("notifications", [])

    today = dt_date.today()
    entry = {
        "id": _next_id(data["notifications"]),
        "message": message,
        "level": level,
        "expires_at": (today + timedelta(days=expires_days)).isoformat() if expires_days else None,
    }
    data["notifications"].append(entry)
    write_json(data)
    return {"status": "ok", "id": str(entry["id"])}


@mcp.tool()
def clear_notifications() -> dict[str, str]:
    """Elimina todas las notificaciones activas."""
    data = read_json()
    data["notifications"] = []
    write_json(data)
    return {"status": "ok"}


# --- Utilidades internas ---
def _next_id(collection: list) -> int:
    if not collection:
        return 1
    return max(item.get("id", 0) for item in collection) + 1


@mcp.tool()
def purge_expired_notifications() -> dict[str, Any]:
    """Elimina todas las notificaciones cuya fecha de expiración ya ha pasado."""
    from datetime import date as dt_date

    today = dt_date.today().isoformat()
    data = read_json()
    before = len(data.get("notifications", []))
    data["notifications"] = [
        n for n in data.get("notifications", [])
        if n.get("expires_at") is None or n["expires_at"] >= today
    ]
    removed = before - len(data["notifications"])
    write_json(data)
    return {"status": "ok", "removed": removed}


@mcp.tool()
def purge_expired_news() -> dict[str, Any]:
    """
    Elimina todas las novedades cuya fecha de expiración ya ha pasado.
    Útil para limpiar el JSON periódicamente.
    """
    from datetime import date as dt_date

    today = dt_date.today().isoformat()
    data = read_json()
    before = len(data.get("news", []))
    data["news"] = [
        n for n in data.get("news", [])
        if n.get("expires_at") is None or n["expires_at"] >= today
    ]
    removed = before - len(data["news"])
    write_json(data)
    return {"status": "ok", "removed": removed}


# --- Entrypoint ---
if __name__ == "__main__":
    mcp.run(transport="stdio")