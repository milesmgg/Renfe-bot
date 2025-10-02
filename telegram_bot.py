# telegram_bot.py
# ------------------------------------------------------------
# Bot mínimo: lista con tu scraper y guarda el elegido en JSON.
#   - /start  -> saludo
#   - /h      -> ayuda
#   - /m      -> conversación: origen → destino → fecha → lista → eliges número → guarda
#   - stop    -> (o /stop) cancela en cualquier momento
#   - /list   -> muestra tus viajes guardados (por chat)
#   - /delete -> muestra lista con índice y pide cuál eliminar
#
# Persistencia: ./monitored_trains.json (por chat_id)
# Token: .env (TELEGRAM_TOKEN)
# ------------------------------------------------------------

from __future__ import annotations
import asyncio
import json
import os
import re
import threading
from dataclasses import dataclass, asdict, field
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import pytz
from dotenv import load_dotenv
from telegram import ReplyKeyboardRemove, Update
from telegram.constants import ParseMode
from telegram.ext import (
    AIORateLimiter,
    Application,
    ApplicationBuilder,
    CallbackContext,
    CommandHandler,
    ConversationHandler,
    MessageHandler,
    filters,
)

# Usa tu scraper real
from renfe_scrapper import RenfeScraperPlaywright 


# ========================== Configuración general =========================== #
load_dotenv()
TZ_MADRID = pytz.timezone("Europe/Madrid")
DATA_FILE = Path("monitored_trains.json")
STORE_LOCK = threading.Lock()

MONITOR_CHAT_NOTIFICATIONS = False

# Estados conversación
ASK_ORIGIN, ASK_DEST, ASK_DATE, CHOOSE_TRAIN = range(4)
DEL_CHOOSE = 100  # estado de borrado
DATE_RE = re.compile(r"^\d{2}/\d{2}/\d{4}$")  # dd/mm/YYYY

# Filtro para cancelar en cualquier momento
STOP_FILTER = filters.Regex(r"(?i)^(/?stop)$")


# ============================== Modelos/Store =============================== #
@dataclass
class MonitoredTrain:
    id: str
    chat_id: int
    origen: str
    destino: str
    fecha: str          # "dd/mm/YYYY"
    salida: str         # "HH:MM"
    tolerancia_min: int = 5
    added_at: str = field(default_factory=lambda: datetime.now(TZ_MADRID).isoformat())


class Store:
    """Persistencia simple en JSON (lista de items)."""

    def __init__(self, path: Path):
        self.path = path

    def _load(self) -> List[Dict[str, Any]]:
        if not self.path.exists():
            return []
        try:
            return json.loads(self.path.read_text(encoding="utf-8"))
        except Exception:
            return []

    def _save(self, data: List[Dict[str, Any]]) -> None:
        self.path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    def list_for_chat(self, chat_id: int) -> List[MonitoredTrain]:
        data = self._load()
        return [MonitoredTrain(**d) for d in data if d.get("chat_id") == chat_id]

    def add(self, item: MonitoredTrain) -> None:
        with STORE_LOCK:
            data = self._load()
            # Evitar duplicado exacto por id
            data = [d for d in data if d.get("id") != item.id]
            data.append(asdict(item))
            self._save(data)

    def remove_ids(self, ids: List[str]) -> int:
        """Elimina por id; devuelve cuántos elementos fueron eliminados."""
        with STORE_LOCK:
            data = self._load()
            before = len(data)
            ids_set = set(ids)
            data = [d for d in data if d.get("id") not in ids_set]
            self._save(data)
            return before - len(data)


STORE = Store(DATA_FILE)


########################3
# --- Añadidos para comprobación periódica ---
import random
from datetime import timedelta

def _parse_salida_from_id(item_id: str) -> Optional[str]:
    """
    Intento robusto para extraer 'HH:MM' del id (caso histórico sin 'salida' en JSON).
    Ejemplo de id: "<chat>-06/10/2025-06:34-Vigo U-A coruña"
    """
    try:
        # Busca un patrón HH:MM dentro del id
        m = re.search(r"\b(\d{2}:\d{2})\b", item_id)
        return m.group(1) if m else None
    except Exception:
        return None

def _list_all_items() -> List[MonitoredTrain]:
    """
    Lee TODOS los viajes del JSON sin filtrar por chat.
    Usa el Store existente.
    """
    raw = STORE._load()  # lectura ya usada internamente por Store
    items: List[MonitoredTrain] = []
    for d in raw:
        try:
            # Relleno defensivo de 'salida' si falta
            if "salida" not in d or not d["salida"]:
                d["salida"] = _parse_salida_from_id(d.get("id", "")) or "00:00"
            items.append(MonitoredTrain(**d))
        except Exception:
            # Si por algún motivo no cuadra el esquema, ignoro esa entrada
            continue
    return items

async def _check_once_and_notify(context: CallbackContext) -> None:
    """
    Hace UNA pasada de comprobación sobre todos los viajes guardados.
    - Si un viaje pasa a 'OK', se notifica y se elimina del JSON.
    - Siempre imprime un resumen ✓/✗ en consola. Opcionalmente lo envía por chat.
    """
    items = _list_all_items()
    if not items:
        print("⏳ No hay viajes monitorizados en este momento.")
        return  # 👈 importante: salir aquí

    # ---- Log de inicio (consola) ----
    print("🔎 Inicializando monitorización para:")
    for it in items:
        print(f"   - {it.origen} → {it.destino} | {it.fecha} {it.salida} | tolerancia {it.tolerancia_min}min")

    # ---- Aviso de inicio por chat (opcional) ----
    if MONITOR_CHAT_NOTIFICATIONS:
        chats = {it.chat_id for it in items}
        for chat in chats:
            try:
                await context.bot.send_message(
                    chat_id=chat,
                    text="🔎 Iniciando monitorización de tus viajes guardados..."
                )
            except Exception as e:
                print(f"⚠️ No pude enviar aviso de inicio al chat {chat}: {e}")

    # ---- Scraping (uno por viaje, en hilo) ----
    async def _check_item(it: MonitoredTrain) -> Tuple[MonitoredTrain, Optional[Dict[str, Any]]]:
        def _task():
            with RenfeScraperPlaywright(headless=True) as s:
                return s.esta_lleno_en_hora(
                    origen=it.origen,
                    destino=it.destino,
                    fecha=it.fecha,            # dd/mm/YYYY
                    hora_objetivo=it.salida,   # HH:MM
                    tolerancia_min=it.tolerancia_min,
                    imprimir=False,
                )
        try:
            res = await asyncio.to_thread(_task)
            return it, res
        except Exception as e:
            print(f"⚠️ Error comprobando {it.origen}->{it.destino} {it.fecha} {it.salida}: {e}")
            return it, None

    results: List[Tuple[MonitoredTrain, Optional[Dict[str, Any]]]] = await asyncio.gather(
        *[_check_item(it) for it in items],
        return_exceptions=False
    )

    # ---- Notificaciones por 'OK' y borrado de ids ----
    to_remove_ids: List[str] = []
    for it, res in results:
        if not res:
            continue
        estado = (res.get("estado") or "").upper()
        if estado == "OK":
            try:
                await context.bot.send_message(
                    chat_id=it.chat_id,
                    text=(
                        f"🎉 Buenas noticias: el viaje que estaba lleno ahora tiene plazas libres.\n\n"
                        f"• *{it.origen} → {it.destino}*\n"
                        f"• *{it.fecha}* — salida *{res.get('salida') or it.salida}* "
                        f"(llegada {res.get('llegada','?')})\n\n"
                        f"¡Corre a por él! 🚄"
                    ),
                    parse_mode=ParseMode.MARKDOWN,
                )
                to_remove_ids.append(it.id)  # evita repetir aviso
            except Exception as e:
                print(f"⚠️ No pude enviar el aviso de hueco al chat {it.chat_id}: {e}")

    if to_remove_ids:
        try:
            STORE.remove_ids(to_remove_ids)
        except Exception as e:
            print(f"⚠️ No pude eliminar ids {to_remove_ids} del JSON: {e}")

    # ---- Resumen final del ciclo: consola + (opcional) chat ----
    print("🧾 Resumen monitorización:")
    resumen_por_chat: Dict[int, List[str]] = {}

    for it, res in results:
        estado = (res or {}).get("estado", "?")
        estado_up = (estado or "").upper()
        icon = "✓" if estado_up == "OK" else ("✗" if estado_up == "LLENO" else "?")
        linea = f"{icon} {it.origen} → {it.destino} | {it.fecha} {it.salida} — {estado}"
        print("   " + linea)

        if MONITOR_CHAT_NOTIFICATIONS:
            resumen_por_chat.setdefault(it.chat_id, []).append("• " + linea)

    if MONITOR_CHAT_NOTIFICATIONS:
        for chat_id, lineas in resumen_por_chat.items():
            try:
                await context.bot.send_message(
                    chat_id=chat_id,
                    text="📊 Resultados de la comprobación:\n" + "\n".join(lineas)
                )
            except Exception as e:
                print(f"⚠️ No pude enviar resumen al chat {chat_id}: {e}")


async def _periodic_scheduler(context: CallbackContext) -> None:
    """
    Job auto-reprogramable:
    - Ejecuta una pasada de comprobación
    - Agenda la siguiente en 5–15 minutos (aleatorio)
    """
    # 1) Pasada de comprobación
    await _check_once_and_notify(context)

    # 2) Reprogramación con intervalo aleatorio
    next_seconds = random.randint(3 * 60, 5*60)
    context.job_queue.run_once(
        _periodic_scheduler,
        when=timedelta(seconds=next_seconds),
        name="renfe_periodic_check",
    )

def schedule_first_check(app: Application) -> None:
    """
    Llama a esto tras construir el Application para disparar el primer check
    en 5–15 minutos. Luego se auto-reagenda.
    """
    first_in = random.randint(3*60, 5*60)
    app.job_queue.run_once(
        _periodic_scheduler,
        when=timedelta(seconds=first_in),
        name="renfe_periodic_check",
    )

#########################33


# ============================== Utilidades bot ============================== #
def greet_text() -> str:
    return (
        "Bienvenido a *Renfe Bot* 👋\n\n"
        "Este bot en monitoriza viajes llenos y te envía un mensaje en cuanto haya hueco.\n"
        "Comandos:\n"
        "• `/m` — iniciar flujo para listar y guardar\n"
        "• `/list` — ver tus viajes guardados\n"
        "• `/delete` — eliminar un viaje guardado por índice\n"
        "• `/h` — ayuda\n"
        "• Escribe `stop` para cancelar en cualquier momento\n"
    )
def help_text() -> str:
    return (
        "*Ayuda — Renfe Bot*\n\n"
        "Comandos principales:\n"
        "• `/m`  Añadir un viaje para monitorizar\n"
        "• `/list`  Ver viajes guardados\n"
        "• `/delete`  Borrar un viaje\n"
        "• `/stop`  Cancelar la conversación actual\n\n"
        "Flujo básico:\n"
        "1. Usa `/m` y dime origen, destino y fecha (dd/mm/YYYY).\n"
        "\t1.1 Importante añadir el nombre como aparece en la web de Renfe\n"
        "2. Te mostraré los trenes de ese día.\n"
        "3. Elige el número del trayecto para guardarlo.\n\n"
        "El bot comprobará periódicamente si hay plazas libres "
        "y te avisará automáticamente. 🚄"
    )

def now_madrid() -> datetime:
    return datetime.now(TZ_MADRID)

def normalize_date(s: str) -> Optional[str]:
    s = s.strip()
    if not DATE_RE.match(s):
        return None
    try:
        datetime.strptime(s, "%d/%m/%Y")
        return s
    except Exception:
        return None

def parse_sort_key(item: MonitoredTrain) -> Tuple[int, int]:
    """Devuelve (yyyymmdd, hhmm) para ordenar por fecha y salida."""
    try:
        d = datetime.strptime(item.fecha, "%d/%m/%Y")
        ymd = d.year * 10000 + d.month * 100 + d.day
    except Exception:
        ymd = 0
    try:
        hh, mm = item.salida.split(":")
        hm = int(hh) * 100 + int(mm)
    except Exception:
        hm = -1
    return (ymd, hm)

def format_saved_list(items: List[MonitoredTrain]) -> Tuple[str, List[str]]:
    """
    Devuelve el texto de la lista numerada y la lista paralela de IDs (en el mismo orden).
    """
    if not items:
        return "No tienes viajes guardados.", []

    # Orden por fecha y hora de salida
    items_sorted = sorted(items, key=parse_sort_key)
    lines = []
    ids = []
    for i, it in enumerate(items_sorted, start=1):
        ids.append(it.id)
        lines.append(
            f"{i}. {it.origen} → {it.destino} | {it.fecha} {it.salida}"
        )
    return "\n".join(lines), ids

def fmt_train_line(n: int, origen: str, destino: str, v: Dict[str, Any]) -> str:
    estado = v.get("estado", "?")
    icon = "✓" if estado == "OK" else ("✗" if estado == "LLENO" else "?")
    return f"{n}. {origen} → {destino} | {v['salida']}–{v['llegada']} | {estado} {icon}"

async def run_scraper_search(origen: str, destino: str, fecha: str) -> List[Dict[str, str]]:
    """
    Usa TU scraper Playwright:
    - buscar_billetes(...) para preparar la página.
    - _extraer_trayectos_ida() para obtener la lista de viajes.
    """
    def _task():
        with RenfeScraperPlaywright(headless=True) as s:
            s.buscar_billetes(origen=origen, destino=destino, fecha=fecha)
            viajes = s._extraer_trayectos_ida()
            return viajes

    return await asyncio.to_thread(_task)


# ============================== Handlers principales ======================== #
async def cmd_start(update: Update, context: CallbackContext) -> None:
    await update.effective_message.reply_text(greet_text(), parse_mode=ParseMode.MARKDOWN)

async def cmd_help(update: Update, context: CallbackContext) -> None:
    await update.effective_message.reply_text(help_text(), parse_mode=ParseMode.MARKDOWN)

async def cancel_any(update: Update, context: CallbackContext) -> int:
    """Cancela en cualquier momento con 'stop' o '/stop'."""
    context.user_data.clear()
    await update.effective_message.reply_text(greet_text(), parse_mode=ParseMode.MARKDOWN)
    return ConversationHandler.END


# ============================== Flujo /m (añadir) =========================== #
async def cmd_monitor(update: Update, context: CallbackContext) -> int:
    await update.message.reply_text("📍Origen:", reply_markup=ReplyKeyboardRemove())
    return ASK_ORIGIN

async def ask_dest(update: Update, context: CallbackContext) -> int:
    context.user_data["origen"] = update.message.text.strip()
    await update.message.reply_text("🎯Destino:")
    return ASK_DEST

async def ask_date(update: Update, context: CallbackContext) -> int:
    context.user_data["destino"] = update.message.text.strip()
    await update.message.reply_text("Fecha (dd/mm/YYYY):")
    return ASK_DATE

async def show_trains(update: Update, context: CallbackContext) -> int:
    fecha = update.message.text.strip()
    norm = normalize_date(fecha)
    if not norm:
        await update.message.reply_text("Formato inválido. Usa dd/mm/YYYY. (Añade 0 si solo es un dígito)")
        return ASK_DATE

    origen = context.user_data["origen"]
    destino = context.user_data["destino"]

    msg = await update.message.reply_text("Buscando trenes… esto puede tardar unos segundos…")
    try:
        viajes = await run_scraper_search(origen, destino, norm)
    except Exception as e:
        await msg.edit_text(f"Error buscando trenes: {e}")
        return ConversationHandler.END

    if not viajes:
        await msg.edit_text("No se encontraron trenes para esa fecha.")
        return ConversationHandler.END

    context.user_data["fecha"] = norm
    context.user_data["viajes"] = viajes

    lines = [fmt_train_line(i + 1, origen, destino, v) for i, v in enumerate(viajes)]
    lines.append("\nResponde con el *número* del tren a guardar en JSON.\n(Escribe `stop` para cancelar.)")
    await msg.edit_text("\n".join(lines), parse_mode=ParseMode.MARKDOWN)
    return CHOOSE_TRAIN

async def choose_train(update: Update, context: CallbackContext) -> int:
    """Lee el índice. Si está OK, avisa y NO guarda. Si está LLENO, guarda en JSON."""
    text = (update.message.text or "").strip()
    try:
        idx = int(text) - 1
    except Exception:
        await update.message.reply_text("Por favor, envía un número válido (o `stop` para cancelar).", parse_mode=ParseMode.MARKDOWN)
        return CHOOSE_TRAIN

    viajes = context.user_data.get("viajes", [])
    if not viajes:
        await update.message.reply_text("No tengo la lista de trenes en memoria. Vuelve a usar /m.")
        return ConversationHandler.END
    if not (0 <= idx < len(viajes)):
        await update.message.reply_text("Índice fuera de rango (o escribe `stop` para cancelar).", parse_mode=ParseMode.MARKDOWN)
        return CHOOSE_TRAIN

    elegido = viajes[idx]
    origen = context.user_data.get("origen", "")
    destino = context.user_data.get("destino", "")
    fecha = context.user_data.get("fecha", "")
    estado = (elegido.get("estado") or "").strip().upper()

    # Si el viaje está disponible, avisar y NO guardar
    if estado == "OK":
        await update.message.reply_text(
            f"ℹ️ Para *{fecha}* el tren **{origen} → {destino}** "
            f"({elegido['salida']}–{elegido['llegada']}) *no está lleno*.\n"
            "✅ Puedes comprarlo ya.\n\n",
            parse_mode=ParseMode.MARKDOWN,
        )
        # Limpieza del estado conversacional
        for k in ("viajes", "origen", "destino", "fecha"):
            context.user_data.pop(k, None)
        return ConversationHandler.END

    # Si está LLENO (o cualquier otro estado distinto de OK), guardar
    item = MonitoredTrain(
        id=f"{update.effective_chat.id}-{fecha}-{elegido['salida']}-{origen}-{destino}",
        chat_id=update.effective_chat.id,
        origen=origen,
        destino=destino,
        fecha=fecha,
        salida=elegido["salida"],
        tolerancia_min=5,
    )
    STORE.add(item)

    await update.message.reply_text(
        "✅ Añadido a *lista de monitorización*:\n"
        f"{origen} → {destino} | {fecha} {item.salida}\n",
        parse_mode=ParseMode.MARKDOWN,
    )

    for k in ("viajes", "origen", "destino", "fecha"):
        context.user_data.pop(k, None)
    return ConversationHandler.END



# ============================== /list (listar) ============================== #
async def cmd_list(update: Update, context: CallbackContext) -> None:
    chat_id = update.effective_chat.id
    items = STORE.list_for_chat(chat_id)
    text, _ = format_saved_list(items)
    await update.effective_message.reply_text(text)


# ============================== /delete (borrar) ============================ #
async def cmd_delete(update: Update, context: CallbackContext) -> int:
    """Muestra lista numerada y pide índice a borrar."""
    chat_id = update.effective_chat.id
    items = STORE.list_for_chat(chat_id)
    text, ids = format_saved_list(items)
    if not ids:
        await update.effective_message.reply_text(text)
        return ConversationHandler.END

    context.user_data["del_ids"] = ids
    await update.effective_message.reply_text(
        f"{text}\n\nEscribe el *número* del viaje que quieres eliminar (o `stop` para cancelar).",
        parse_mode=ParseMode.MARKDOWN,
    )
    return DEL_CHOOSE

async def choose_delete(update: Update, context: CallbackContext) -> int:
    text = (update.message.text or "").strip()
    try:
        idx = int(text) - 1
    except Exception:
        await update.message.reply_text("Por favor, envía un número válido (o `stop` para cancelar).", parse_mode=ParseMode.MARKDOWN)
        return DEL_CHOOSE

    ids = context.user_data.get("del_ids", [])
    if not ids:
        await update.message.reply_text("No tengo la lista para borrar. Vuelve a usar /delete.")
        return ConversationHandler.END

    if not (0 <= idx < len(ids)):
        await update.message.reply_text("Índice fuera de rango (o escribe `stop` para cancelar).", parse_mode=ParseMode.MARKDOWN)
        return DEL_CHOOSE

    removed = STORE.remove_ids([ids[idx]])
    if removed:
        await update.message.reply_text("🗑️ Viaje eliminado.")
    else:
        await update.message.reply_text("No se pudo eliminar (quizá ya no existía).")

    context.user_data.pop("del_ids", None)
    return ConversationHandler.END


# ================================ Bootstrap ================================= #
def build_application() -> Application:
    token = os.environ.get("TELEGRAM_TOKEN")
    if not token:
        raise RuntimeError("Falta TELEGRAM_TOKEN en el entorno (.env).")

    app = (
        ApplicationBuilder()
        .token(token)
        .rate_limiter(AIORateLimiter())
        .build()
    )

    # Handler global para 'stop' incluso fuera de la conversación
    #app.add_handler(MessageHandler(STOP_FILTER, cancel_any), group=0)

    # Conversación de monitorización (/m)
    app.add_handler(ConversationHandler(
        entry_points=[CommandHandler("m", cmd_monitor)],
        states={
            ASK_ORIGIN: [
                MessageHandler(STOP_FILTER, cancel_any),
                MessageHandler(filters.TEXT & ~filters.COMMAND, ask_dest),
            ],
            ASK_DEST: [
                MessageHandler(STOP_FILTER, cancel_any),
                MessageHandler(filters.TEXT & ~filters.COMMAND, ask_date),
            ],
            ASK_DATE: [
                MessageHandler(STOP_FILTER, cancel_any),
                MessageHandler(filters.TEXT & ~filters.COMMAND, show_trains),
            ],
            CHOOSE_TRAIN: [
                MessageHandler(STOP_FILTER, cancel_any),
                MessageHandler(filters.TEXT & ~filters.COMMAND, choose_train),
            ],
        },
        fallbacks=[
            CommandHandler("start", cmd_start),
            CommandHandler("h", cmd_help),
            CommandHandler("stop", cancel_any), 
            MessageHandler(STOP_FILTER, cancel_any),
        ],
        name="monitor_conv",
        persistent=False,
    ))

    # Conversación de borrado (/delete)
    app.add_handler(ConversationHandler(
        entry_points=[CommandHandler("delete", cmd_delete)],
        states={
            DEL_CHOOSE: [
                MessageHandler(STOP_FILTER, cancel_any),
                MessageHandler(filters.TEXT & ~filters.COMMAND, choose_delete),
            ],
        },
        fallbacks=[
            CommandHandler("start", cmd_start),
            CommandHandler("h", cmd_help),
            CommandHandler("stop", cancel_any), 
            MessageHandler(STOP_FILTER, cancel_any),
        ],
        name="delete_conv",
        persistent=False,
    ))

    # Comandos sueltos
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("h", cmd_help))
    app.add_handler(CommandHandler("list", cmd_list))
    app.add_handler(CommandHandler("stop", cancel_any))
    # stop ya está cubierto como mensaje y como fallback

    return app


def main() -> None:
    app = build_application()
    print("Bot arrancando…")
    schedule_first_check(app)
    app.run_polling(close_loop=False)


if __name__ == "__main__":
    main()
