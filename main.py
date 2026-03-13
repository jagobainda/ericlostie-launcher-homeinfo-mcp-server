import datetime
import json
import os
import tempfile
import uuid
from pathlib import Path
from typing import Annotated, Any


from mcp.server.fastmcp import FastMCP
from pydantic import Field

# --- Configuración ---
JSON_PATH = Path(os.getenv("HOME_INFO_PATH", "/var/www/home-info/home-info.json"))

mcp = FastMCP(
    "home-info-server",
    instructions=(
        "Servidor MCP para gestionar las novedades y alertas que va a consumir un launcher de juegos. "
        "El JSON que persiste tiene dos secciones principales: 'news' (novedades/noticias) "
        "y 'notifications' (alertas). Cada elemento tiene un 'id' de tipo GUID. "
        "Usa las herramientas de este servidor para leer, añadir, editar o eliminar contenido."
    ),
    host="127.0.0.1",
    port=8765,
)


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
        # Preservar los permisos del archivo original (si existe) antes de reemplazarlo.
        # mkstemp crea con 0o600; os.replace heredaría esos permisos restrictivos.
        if JSON_PATH.exists():
            os.chmod(tmp_path, JSON_PATH.stat().st_mode)
        else:
            os.chmod(tmp_path, 0o644)
        os.replace(tmp_path, JSON_PATH)
    except Exception:
        os.unlink(tmp_path)
        raise


def _validate_iso_date(value: str) -> bool:
    try:
        datetime.date.fromisoformat(value)
        return True
    except ValueError:
        return False


# --- Herramientas MCP ---

@mcp.tool()
def get_home_info() -> dict[str, Any]:
    """
    Devuelve el contenido completo del JSON del launcher.

    El JSON tiene la siguiente estructura:
    {
      "news": [ { "id": "xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx", "title": "...", "body": "...", "date": "YYYY-MM-DD", "expires_at": "YYYY-MM-DD|null" } ],
      "notifications": [ { "id": "xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx", "message": "...", "level": "info|warning|error", "expires_at": "YYYY-MM-DD|null" } ]
    }

    Returns:
        El objeto JSON completo tal como está almacenado.
    """
    return read_json()


@mcp.tool()
def get_news() -> list[dict[str, Any]]:
    """
    Devuelve únicamente la lista de novedades/noticias del launcher.

    Útil cuando solo necesitas consultar las noticias sin cargar el JSON completo.

    Returns:
        Lista de objetos noticia. Cada objeto tiene: id, title, body, date, expires_at.
    """
    return read_json().get("news", [])


@mcp.tool()
def get_notifications() -> list[dict[str, Any]]:
    """
    Devuelve únicamente la lista de notificaciones activas del launcher.

    Útil cuando solo necesitas consultar las notificaciones sin cargar el JSON completo.

    Returns:
        Lista de objetos notificación. Cada objeto tiene: id, message, level, expires_at.
    """
    return read_json().get("notifications", [])


@mcp.tool()
def add_news(
    title: Annotated[str, Field(description="Título breve y descriptivo de la novedad que verá el usuario en el launcher.")],
    body: Annotated[str, Field(description="Cuerpo o descripción completa de la novedad. Puede incluir varios párrafos.")],
    date: Annotated[str, Field(description="Fecha de publicación en formato ISO 8601 (ej: '2025-03-12').")],
    expires_days: Annotated[
        int | None,
        Field(description="Número de días a partir de hoy tras los que la novedad expirará y podrá ser purgada. Pasa null si no debe expirar nunca.", ge=1),
    ],
) -> dict[str, str]:
    """
    Añade una novedad/noticia al launcher.

    Returns:
        {"status": "ok", "id": "<id_asignado>"} si se creó correctamente.
        {"status": "error", "detail": "<mensaje>"} si hay algún problema de validación.
    """
    if not _validate_iso_date(date):
        return {"status": "error", "detail": f"El formato de fecha '{date}' no es válido. Usa ISO 8601 (ej: '2025-03-12')."}

    data = read_json()
    data.setdefault("news", [])

    today = datetime.date.today()
    entry = {
        "id": str(uuid.uuid4()),
        "title": title,
        "body": body,
        "date": date,
        "expires_at": (today + datetime.timedelta(days=expires_days)).isoformat() if expires_days else None,
    }
    data["news"].append(entry)
    write_json(data)
    return {"status": "ok", "id": str(entry["id"])}


@mcp.tool()
def update_news(
    news_id: Annotated[str, Field(description="GUID de la novedad que se desea modificar.")],
    title: Annotated[str | None, Field(description="Nuevo título. Si se omite, no se modifica.")] = None,
    body: Annotated[str | None, Field(description="Nuevo cuerpo/descripción. Si se omite, no se modifica.")] = None,
    date: Annotated[str | None, Field(description="Nueva fecha en formato ISO 8601. Si se omite, no se modifica.")] = None,
    expires_days: Annotated[
        int | None,
        Field(description="Nuevo número de días hasta expiración (calculado desde hoy). Pasa 0 para eliminar la expiración.", ge=0),
    ] = None,
) -> dict[str, str]:
    """
    Actualiza los campos de una novedad existente.

    Solo se modifican los campos que se proporcionen; el resto se mantiene intacto.

    Returns:
        {"status": "ok"} si se actualizó correctamente.
        {"status": "error", "detail": "<mensaje>"} si no se encontró la novedad o hay un error de validación.
    """
    if date and not _validate_iso_date(date):
        return {"status": "error", "detail": f"El formato de fecha '{date}' no es válido. Usa ISO 8601 (ej: '2025-03-12')."}

    data = read_json()
    news_list = data.get("news", [])
    for item in news_list:
        if item.get("id") == news_id:
            if title is not None:
                item["title"] = title
            if body is not None:
                item["body"] = body
            if date is not None:
                item["date"] = date
            if expires_days is not None:
                if expires_days == 0:
                    item["expires_at"] = None
                else:
                    item["expires_at"] = (datetime.date.today() + datetime.timedelta(days=expires_days)).isoformat()
            write_json(data)
            return {"status": "ok"}
    return {"status": "error", "detail": f"No se encontró la novedad con id={news_id}"}


@mcp.tool()
def remove_news(
    news_id: Annotated[str, Field(description="GUID de la novedad que se desea eliminar.")],
) -> dict[str, str]:
    """
    Elimina una novedad por su ID.

    Returns:
        {"status": "ok"} si se eliminó correctamente.
        {"status": "error", "detail": "<mensaje>"} si no se encontró la novedad.
    """
    data = read_json()
    before = len(data.get("news", []))
    data["news"] = [n for n in data.get("news", []) if n.get("id") != news_id]
    if len(data["news"]) == before:
        return {"status": "error", "detail": f"No se encontró la novedad con id={news_id}"}
    write_json(data)
    return {"status": "ok"}


@mcp.tool()
def add_notification(
    message: Annotated[str, Field(description="Texto de la notificación que se mostrará como banner en el launcher.")],
    level: Annotated[
        str,
        Field(description="Nivel de urgencia de la notificación. Valores válidos: 'info' (azul, informativo), 'warning' (amarillo, advertencia), 'error' (rojo, crítico)."),
    ],
    expires_days: Annotated[
        int | None,
        Field(description="Número de días a partir de hoy tras los que la notificación expirará. Pasa null si no debe expirar nunca.", ge=1),
    ],
) -> dict[str, str]:
    """
    Añade una notificación/banner al launcher.

    Returns:
        {"status": "ok", "id": "<id_asignado>"} si se creó correctamente.
        {"status": "error", "detail": "<mensaje>"} si el nivel no es válido.
    """
    if level not in ("info", "warning", "error"):
        return {"status": "error", "detail": "level debe ser 'info', 'warning' o 'error'"}

    data = read_json()
    data.setdefault("notifications", [])

    today = datetime.date.today()
    entry = {
        "id": str(uuid.uuid4()),
        "message": message,
        "level": level,
        "expires_at": (today + datetime.timedelta(days=expires_days)).isoformat() if expires_days else None,
    }
    data["notifications"].append(entry)
    write_json(data)
    return {"status": "ok", "id": str(entry["id"])}


@mcp.tool()
def remove_notification(
    notification_id: Annotated[str, Field(description="GUID de la notificación que se desea eliminar.")],
) -> dict[str, str]:
    """
    Elimina una notificación concreta por su ID.

    Útil cuando quieres retirar una sola notificación sin borrar las demás.
    Para eliminar todas a la vez, usa clear_notifications.

    Returns:
        {"status": "ok"} si se eliminó correctamente.
        {"status": "error", "detail": "<mensaje>"} si no se encontró la notificación.
    """
    data = read_json()
    before = len(data.get("notifications", []))
    data["notifications"] = [n for n in data.get("notifications", []) if n.get("id") != notification_id]
    if len(data["notifications"]) == before:
        return {"status": "error", "detail": f"No se encontró la notificación con id={notification_id}"}
    write_json(data)
    return {"status": "ok"}


@mcp.tool()
def clear_notifications() -> dict[str, str]:
    """
    Elimina TODAS las notificaciones activas de una sola vez.

    Útil para limpiar el tablón de notificaciones por completo.
    Si solo quieres eliminar una notificación concreta, usa remove_notification.

    Returns:
        {"status": "ok"}
    """
    data = read_json()
    data["notifications"] = []
    write_json(data)
    return {"status": "ok"}


@mcp.tool()
def purge_expired_notifications() -> dict[str, Any]:
    """
    Elimina automáticamente todas las notificaciones cuya fecha de expiración ya ha pasado.

    Compara el campo 'expires_at' de cada notificación con la fecha de hoy.
    Las notificaciones sin fecha de expiración (expires_at = null) nunca se eliminan.

    Returns:
        {"status": "ok", "removed": <número_de_notificaciones_eliminadas>}
    """
    today = datetime.date.today().isoformat()
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
    Elimina automáticamente todas las novedades cuya fecha de expiración ya ha pasado.

    Compara el campo 'expires_at' de cada novedad con la fecha de hoy.
    Las novedades sin fecha de expiración (expires_at = null) nunca se eliminan.
    Útil para ejecutar periódicamente y mantener el JSON limpio.

    Returns:
        {"status": "ok", "removed": <número_de_novedades_eliminadas>}
    """

    today = datetime.date.today().isoformat()
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
    mcp.run(transport="sse")
