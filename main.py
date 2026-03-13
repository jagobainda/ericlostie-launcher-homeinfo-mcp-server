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


def _validate_iso_datetime(value: str) -> bool:
    """Valida que el valor sea un datetime ISO 8601 con formato YYYY-MM-DDTHH:MM:SS."""
    try:
        datetime.datetime.fromisoformat(value)
        return True
    except ValueError:
        return False


def _days_from_today_to_datetime(days: int) -> str:
    """Devuelve un datetime ISO 8601 correspondiente a la medianoche de hoy + días."""
    target = datetime.datetime.combine(
        datetime.date.today() + datetime.timedelta(days=days),
        datetime.time.min,
    )
    return target.isoformat()


# --- Herramientas MCP ---

@mcp.tool()
def get_home_info() -> dict[str, Any]:
    """
    Devuelve el contenido completo del JSON del launcher.

    El JSON tiene la siguiente estructura:
    {
      "news": [
        {
          "id": "xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx",
          "title": "...",
          "description": "...",
          "tag": "...",
          "date": "YYYY-MM-DDTHH:MM:SS",
          "expires_at": "YYYY-MM-DDTHH:MM:SS|null"
        }
      ],
      "notifications": [
        {
          "id": "xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx",
          "title": "...",
          "message": "...",
          "type": "Info|Warning|Error",
          "date": "YYYY-MM-DDTHH:MM:SS",
          "expires_at": "YYYY-MM-DDTHH:MM:SS|null"
        }
      ]
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
        Lista de objetos noticia. Cada objeto tiene: id, title, description, tag, date, expires_at.
    """
    return read_json().get("news", [])


@mcp.tool()
def get_notifications() -> list[dict[str, Any]]:
    """
    Devuelve únicamente la lista de notificaciones activas del launcher.

    Útil cuando solo necesitas consultar las notificaciones sin cargar el JSON completo.

    Returns:
        Lista de objetos notificación. Cada objeto tiene: id, title, message, type, date, expires_at.
    """
    return read_json().get("notifications", [])


@mcp.tool()
def add_news(
    title: Annotated[str, Field(description="Título breve y descriptivo de la novedad que verá el usuario en el launcher.")],
    description: Annotated[str, Field(description="Descripción completa de la novedad. Puede incluir varios párrafos.")],
    tag: Annotated[str, Field(description="Etiqueta o categoría de la novedad (ej: 'Release', 'Update', 'Hotfix').")],
    date: Annotated[str, Field(description="Fecha y hora de publicación en formato ISO 8601 (ej: '2025-07-10T00:00:00').")],
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
    if not _validate_iso_datetime(date):
        return {"status": "error", "detail": f"El formato de fecha '{date}' no es válido. Usa ISO 8601 (ej: '2025-07-10T00:00:00')."}

    data = read_json()
    data.setdefault("news", [])

    entry = {
        "id": str(uuid.uuid4()),
        "title": title,
        "description": description,
        "tag": tag,
        "date": date,
        "expires_at": _days_from_today_to_datetime(expires_days) if expires_days else None,
    }
    data["news"].append(entry)
    write_json(data)
    return {"status": "ok", "id": entry["id"]}


@mcp.tool()
def update_news(
    news_id: Annotated[str, Field(description="GUID de la novedad que se desea modificar.")],
    title: Annotated[str | None, Field(description="Nuevo título. Si se omite, no se modifica.")] = None,
    description: Annotated[str | None, Field(description="Nueva descripción. Si se omite, no se modifica.")] = None,
    tag: Annotated[str | None, Field(description="Nueva etiqueta/categoría. Si se omite, no se modifica.")] = None,
    date: Annotated[str | None, Field(description="Nueva fecha en formato ISO 8601 (ej: '2025-07-10T00:00:00'). Si se omite, no se modifica.")] = None,
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
    if date and not _validate_iso_datetime(date):
        return {"status": "error", "detail": f"El formato de fecha '{date}' no es válido. Usa ISO 8601 (ej: '2025-07-10T00:00:00')."}

    data = read_json()
    news_list = data.get("news", [])
    for item in news_list:
        if item.get("id") == news_id:
            if title is not None:
                item["title"] = title
            if description is not None:
                item["description"] = description
            if tag is not None:
                item["tag"] = tag
            if date is not None:
                item["date"] = date
            if expires_days is not None:
                if expires_days == 0:
                    item["expires_at"] = None
                else:
                    item["expires_at"] = _days_from_today_to_datetime(expires_days)
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
    title: Annotated[str, Field(description="Título breve de la notificación que se mostrará en el launcher.")],
    message: Annotated[str, Field(description="Texto completo de la notificación que se mostrará como banner en el launcher.")],
    type: Annotated[
        str,
        Field(description="Nivel de urgencia de la notificación. Valores válidos: 'Info' (informativo), 'Warning' (advertencia), 'Error' (crítico)."),
    ],
    date: Annotated[str, Field(description="Fecha y hora de la notificación en formato ISO 8601 (ej: '2025-07-10T00:00:00').")],
    expires_days: Annotated[
        int | None,
        Field(description="Número de días a partir de hoy tras los que la notificación expirará. Pasa null si no debe expirar nunca.", ge=1),
    ],
) -> dict[str, str]:
    """
    Añade una notificación/banner al launcher.

    Returns:
        {"status": "ok", "id": "<id_asignado>"} si se creó correctamente.
        {"status": "error", "detail": "<mensaje>"} si el tipo o la fecha no son válidos.
    """
    if type not in ("Info", "Warning", "Error"):
        return {"status": "error", "detail": "type debe ser 'Info', 'Warning' o 'Error'"}

    if not _validate_iso_datetime(date):
        return {"status": "error", "detail": f"El formato de fecha '{date}' no es válido. Usa ISO 8601 (ej: '2025-07-10T00:00:00')."}

    data = read_json()
    data.setdefault("notifications", [])

    entry = {
        "id": str(uuid.uuid4()),
        "title": title,
        "message": message,
        "type": type,
        "date": date,
        "expires_at": _days_from_today_to_datetime(expires_days) if expires_days else None,
    }
    data["notifications"].append(entry)
    write_json(data)
    return {"status": "ok", "id": entry["id"]}


@mcp.tool()
def update_notification(
    notification_id: Annotated[str, Field(description="GUID de la notificación que se desea modificar.")],
    title: Annotated[str | None, Field(description="Nuevo título. Si se omite, no se modifica.")] = None,
    message: Annotated[str | None, Field(description="Nuevo mensaje. Si se omite, no se modifica.")] = None,
    type: Annotated[str | None, Field(description="Nuevo tipo: 'Info', 'Warning' o 'Error'. Si se omite, no se modifica.")] = None,
    date: Annotated[str | None, Field(description="Nueva fecha en formato ISO 8601 (ej: '2025-07-10T00:00:00'). Si se omite, no se modifica.")] = None,
    expires_days: Annotated[
        int | None,
        Field(description="Nuevo número de días hasta expiración (calculado desde hoy). Pasa 0 para eliminar la expiración.", ge=0),
    ] = None,
) -> dict[str, str]:
    """
    Actualiza los campos de una notificación existente.

    Solo se modifican los campos que se proporcionen; el resto se mantiene intacto.

    Returns:
        {"status": "ok"} si se actualizó correctamente.
        {"status": "error", "detail": "<mensaje>"} si no se encontró la notificación o hay un error de validación.
    """
    if type is not None and type not in ("Info", "Warning", "Error"):
        return {"status": "error", "detail": "type debe ser 'Info', 'Warning' o 'Error'"}

    if date and not _validate_iso_datetime(date):
        return {"status": "error", "detail": f"El formato de fecha '{date}' no es válido. Usa ISO 8601 (ej: '2025-07-10T00:00:00')."}

    data = read_json()
    for item in data.get("notifications", []):
        if item.get("id") == notification_id:
            if title is not None:
                item["title"] = title
            if message is not None:
                item["message"] = message
            if type is not None:
                item["type"] = type
            if date is not None:
                item["date"] = date
            if expires_days is not None:
                if expires_days == 0:
                    item["expires_at"] = None
                else:
                    item["expires_at"] = _days_from_today_to_datetime(expires_days)
            write_json(data)
            return {"status": "ok"}
    return {"status": "error", "detail": f"No se encontró la notificación con id={notification_id}"}


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

    Compara el campo 'expires_at' de cada notificación con el datetime actual.
    Las notificaciones sin fecha de expiración (expires_at = null) nunca se eliminan.

    Returns:
        {"status": "ok", "removed": <número_de_notificaciones_eliminadas>}
    """
    now = datetime.datetime.now().replace(microsecond=0).isoformat()
    data = read_json()
    before = len(data.get("notifications", []))
    data["notifications"] = [
        n for n in data.get("notifications", [])
        if n.get("expires_at") is None or n["expires_at"] >= now
    ]
    removed = before - len(data["notifications"])
    write_json(data)
    return {"status": "ok", "removed": removed}


@mcp.tool()
def purge_expired_news() -> dict[str, Any]:
    """
    Elimina automáticamente todas las novedades cuya fecha de expiración ya ha pasado.

    Compara el campo 'expires_at' de cada novedad con el datetime actual.
    Las novedades sin fecha de expiración (expires_at = null) nunca se eliminan.
    Útil para ejecutar periódicamente y mantener el JSON limpio.

    Returns:
        {"status": "ok", "removed": <número_de_novedades_eliminadas>}
    """
    now = datetime.datetime.now().replace(microsecond=0).isoformat()
    data = read_json()
    before = len(data.get("news", []))
    data["news"] = [
        n for n in data.get("news", [])
        if n.get("expires_at") is None or n["expires_at"] >= now
    ]
    removed = before - len(data["news"])
    write_json(data)
    return {"status": "ok", "removed": removed}


# --- Entrypoint ---
if __name__ == "__main__":
    mcp.run(transport="sse")
