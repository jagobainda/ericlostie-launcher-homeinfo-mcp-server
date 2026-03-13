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
        "Servidor MCP para gestionar el contenido de la pantalla de inicio del launcher 'Eric Lostie Launcher'. "
        "El JSON persistido tiene dos secciones: 'news' (novedades visibles como tarjetas en el feed del launcher) "
        "y 'notifications' (banners de alerta que aparecen sobre la interfaz). "
        "Cada elemento se identifica por un GUID único en el campo 'id'. "
        "Tanto las 'news' como las 'notifications' tienen expiración obligatoria. "
        "Flujo habitual: usar add_news o add_notification para publicar contenido, "
        "remove_news / remove_notification para retirar uno concreto, "
        "y purge_expired_news / purge_expired_notifications para limpiar los caducados de forma masiva. "
        "Ante cualquier duda sobre el contenido actual, llama primero a get_home_info."
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
    Devuelve el contenido completo del JSON del launcher: tanto 'news' como 'notifications'.

    Úsala como punto de partida para conocer el estado actual antes de añadir, eliminar
    o purgar contenido. Evita llamar a get_news y get_notifications por separado si
    necesitas ambas secciones a la vez.

    Estructura del JSON devuelto:
    {
      "news": [
        {
          "id": "xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx",  // GUID único
          "title": "...",                                 // Título visible en el feed
          "description": "...",                          // Cuerpo completo de la noticia
          "tag": "...",                                  // Etiqueta (p.ej. Release, Hotfix)
          "date": "YYYY-MM-DDTHH:MM:SS",                // Fecha de publicación
          "expires_at": "YYYY-MM-DDTHH:MM:SS"           // Siempre presente en news
        }
      ],
      "notifications": [
        {
          "id": "xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx",  // GUID único
          "title": "...",                                 // Título del banner
          "message": "...",                              // Texto completo del banner
          "type": "Info|Warning|Error",                  // Nivel de urgencia
          "date": "YYYY-MM-DDTHH:MM:SS",                // Fecha de la notificación
          "expires_at": "YYYY-MM-DDTHH:MM:SS"           // Siempre presente en notifications
        }
      ]
    }

    Returns:
        El objeto JSON completo tal como está almacenado en disco.
    """
    return read_json()


@mcp.tool()
def get_news() -> list[dict[str, Any]]:
    """
    Devuelve únicamente la lista de novedades ('news') del launcher.

    Úsala cuando solo necesites consultar o razonar sobre las noticias,
    sin necesitar los datos de notificaciones.

    Returns:
        Lista (posiblemente vacía) de objetos noticia.
        Cada objeto contiene: id, title, description, tag, date, expires_at.
    """
    return read_json().get("news", [])


@mcp.tool()
def get_notifications() -> list[dict[str, Any]]:
    """
    Devuelve únicamente la lista de notificaciones del launcher.

    Úsala cuando solo necesites consultar o razonar sobre las notificaciones,
    sin necesitar los datos de noticias.

    Returns:
        Lista (posiblemente vacía) de objetos notificación.
        Cada objeto contiene: id, title, message, type, date, expires_at.
    """
    return read_json().get("notifications", [])


@mcp.tool()
def add_news(
    title: Annotated[str, Field(description="Título breve y descriptivo de la novedad (máx. una línea). Es lo primero que lee el usuario en el feed.")],
    description: Annotated[str, Field(description="Cuerpo completo de la noticia. Puede ocupar varios párrafos. Describe qué hay de nuevo, qué cambia o por qué es relevante para el usuario.")],
    tag: Annotated[str, Field(description="Etiqueta que categoriza la novedad. Ejemplos habituales: 'Release' (nueva versión), 'Update' (actualización menor), 'Hotfix' (corrección urgente), 'Maintenance' (mantenimiento), 'Event' (evento especial).")],
    date: Annotated[str, Field(description="Fecha y hora de publicación de la noticia en formato ISO 8601 sin zona horaria (ej: '2025-07-10T00:00:00'). Normalmente es la fecha actual o la fecha oficial del evento.")],
    expires_days: Annotated[
        int,
        Field(description="Número de días a partir de HOY tras los que la noticia expirará. Mínimo 1. La fecha resultante se calculará automáticamente como medianoche del día (hoy + expires_days). Es obligatorio: toda noticia debe tener fecha de caducidad.", ge=1),
    ],
) -> dict[str, str]:
    """
    Publica una nueva noticia en el feed del launcher.

    El 'id' se genera automáticamente como GUID. El campo 'expires_at' se calcula
    a partir de 'expires_days' (medianoche del día resultante). Una vez creada,
    la noticia no se puede editar; si necesitas corregirla, elimínala con remove_news
    y vuelve a crearla.

    Returns:
        {"status": "ok", "id": "<guid_asignado>"} si se publicó correctamente.
        {"status": "error", "detail": "<motivo>"} si la fecha no tiene formato válido.
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
        "expires_at": _days_from_today_to_datetime(expires_days),
    }
    data["news"].append(entry)
    write_json(data)
    return {"status": "ok", "id": entry["id"]}


@mcp.tool()
def remove_news(
    news_id: Annotated[str, Field(description="GUID de la noticia que se desea eliminar. Obtenlo previamente con get_news o get_home_info.")],
) -> dict[str, str]:
    """
    Elimina una noticia concreta del feed por su GUID.

    Úsala cuando necesites retirar una noticia específica antes de que expire,
    o para corregirla (eliminar + volver a crear con add_news).
    Para limpiar en masa las noticias caducadas, usa purge_expired_news en su lugar.

    Returns:
        {"status": "ok"} si se eliminó correctamente.
        {"status": "error", "detail": "<motivo>"} si el GUID no existe.
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
    title: Annotated[str, Field(description="Título breve del banner (máx. una línea). Resume el motivo de la notificación.")],
    message: Annotated[str, Field(description="Texto completo que se mostrará en el banner. Debe ser claro y accionable: indica qué ocurre y, si aplica, qué debe hacer el usuario.")],
    notification_type: Annotated[
        str,
        Field(description="Nivel de urgencia del banner. Valores permitidos: 'Info' (información general sin impacto en el juego), 'Warning' (advertencia que puede afectar la experiencia), 'Error' (problema crítico que impide o interrumpe el juego)."),
    ],
    date: Annotated[str, Field(description="Fecha y hora de la notificación en formato ISO 8601 sin zona horaria (ej: '2025-07-10T00:00:00'). Normalmente es el momento en que se detecta o se comunica el evento.")],
    expires_days: Annotated[
        int,
        Field(description="Número de días a partir de HOY tras los que la notificación expirará. Mínimo 1. La fecha resultante se calculará automáticamente como medianoche del día (hoy + expires_days). Es obligatorio: toda notificación debe tener fecha de caducidad.", ge=1),
    ],
) -> dict[str, str]:
    """
    Publica una nueva notificación/banner en el launcher.

    Los banners se muestran de forma prominente sobre la interfaz. Usa 'Info' para
    comunicados generales, 'Warning' para advertencias (ej: mantenimiento programado)
    y 'Error' para incidencias críticas (ej: servidores caídos).

    El 'id' se genera automáticamente. El campo 'expires_at' se calcula
    a partir de 'expires_days' (medianoche del día resultante). Una vez creada,
    la notificación no se puede editar; si necesitas corregirla, elimínala con
    remove_notification y vuelve a crearla.

    Returns:
        {"status": "ok", "id": "<guid_asignado>"} si se publicó correctamente.
        {"status": "error", "detail": "<motivo>"} si el tipo o la fecha no son válidos.
    """
    if notification_type not in ("Info", "Warning", "Error"):
        return {"status": "error", "detail": "notification_type debe ser 'Info', 'Warning' o 'Error'"}

    if not _validate_iso_datetime(date):
        return {"status": "error", "detail": f"El formato de fecha '{date}' no es válido. Usa ISO 8601 (ej: '2025-07-10T00:00:00')."}

    data = read_json()
    data.setdefault("notifications", [])

    entry = {
        "id": str(uuid.uuid4()),
        "title": title,
        "message": message,
        "type": notification_type,
        "date": date,
        "expires_at": _days_from_today_to_datetime(expires_days),
    }
    data["notifications"].append(entry)
    write_json(data)
    return {"status": "ok", "id": entry["id"]}


@mcp.tool()
def remove_notification(
    notification_id: Annotated[str, Field(description="GUID de la notificación que se desea eliminar. Obtenlo previamente con get_notifications o get_home_info.")],
) -> dict[str, str]:
    """
    Elimina una notificación concreta por su GUID.

    Úsala cuando necesites retirar un banner específico (ej: la incidencia ya se resolvió).
    Para eliminar TODAS las notificaciones de golpe usa clear_notifications.
    Para eliminar solo las caducadas usa purge_expired_notifications.

    Returns:
        {"status": "ok"} si se eliminó correctamente.
        {"status": "error", "detail": "<motivo>"} si el GUID no existe.
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
    Elimina TODAS las notificaciones activas de una sola vez, independientemente de su fecha de expiración.

    Úsala para limpiar el tablón completamente (ej: tras resolver una incidencia mayor
    que generó varios banners). Si solo quieres eliminar una notificación concreta,
    usa remove_notification. Si solo quieres eliminar las caducadas, usa purge_expired_notifications.

    Returns:
        {"status": "ok"} siempre (aunque no hubiera ninguna notificación).
    """
    data = read_json()
    data["notifications"] = []
    write_json(data)
    return {"status": "ok"}


@mcp.tool()
def purge_expired_notifications() -> dict[str, Any]:
    """
    Elimina todas las notificaciones cuya fecha 'expires_at' es anterior al momento actual.

    Operación de mantenimiento segura: solo borra las caducadas, nunca las que aún
    están vigentes. Úsala periódicamente para mantener limpia la lista de notificaciones
    sin intervención manual elemento a elemento.

    Returns:
        {"status": "ok", "removed": <n>} donde 'removed' es el número de notificaciones eliminadas (puede ser 0).
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
    Elimina todas las noticias cuya fecha 'expires_at' es anterior al momento actual.

    Operación de mantenimiento segura: solo borra las noticias caducadas, nunca las vigentes.
    Dado que toda noticia tiene 'expires_at' obligatorio, esta herramienta es la forma
    recomendada de limpiar el feed de forma masiva sin tener que borrar cada noticia a mano.

    Returns:
        {"status": "ok", "removed": <n>} donde 'removed' es el número de noticias eliminadas (puede ser 0).
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
