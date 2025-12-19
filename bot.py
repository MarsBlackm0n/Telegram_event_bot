import json
import re
import os
import time
from datetime import datetime, date, time as dtime
from zoneinfo import ZoneInfo

from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
)
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ContextTypes,
    filters,
)

# =========================
# CONFIG
# =========================

BOT_TOKEN = os.environ["BOT_TOKEN"]
DATA_FILE = "bot_data.json"
TZ = ZoneInfo("Europe/Paris")

# =========================
# STOCKAGE SIMPLE (JSON)
# =========================

# Structure:
# {
#   "events": [
#       {
#           "chat_id": -100123,
#           "type": "birthday" / "event",
#           "username": "pseudo" or null,
#           "title": "Anniv Nolwenn",
#           "day": 25,
#           "month": 3,
#           "year": 2026 or null
#       },
#       ...
#   ]
# }

DATA = {
    "events": []
}

def load_data():
    global DATA
    if os.path.exists(DATA_FILE):
        try:
            with open(DATA_FILE, "r", encoding="utf-8") as f:
                DATA = json.load(f)
        except Exception:
            # En cas de fichier corrompu, on repart sur du propre
            DATA = {"events": []}

def save_data():
    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(DATA, f, ensure_ascii=False, indent=2)

# =========================
# DRUNK MODE (IN-MEMORY)
# =========================

# key: (chat_id, user_id) -> expiry_ts or None (pas d'expiration)
DRUNK_USERS = {}
# key: (chat_id, user_id) -> {"text": "..."}
PENDING_MESSAGES = {}


# =========================
# COMMANDES DRUNK MODE
# =========================

async def drunk_on(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Active le drunk mode pour l'utilisateur dans ce groupe."""
    if update.effective_chat.type not in ("group", "supergroup"):
        await update.message.reply_text("Cette commande est faite pour un groupe 😉")
        return

    chat_id = update.effective_chat.id
    user_id = update.effective_user.id

    expiry_ts = None
    if context.args:
        try:
            minutes = int(context.args[0])
            expiry_ts = time.time() + minutes * 60
            msg_extra = f" pour {minutes} minutes"
        except ValueError:
            msg_extra = ""
    else:
        msg_extra = ""

    DRUNK_USERS[(chat_id, user_id)] = expiry_ts

    await update.message.reply_text(
        f"🥴 Drunk Mode activé pour {update.effective_user.first_name}{msg_extra}.\n"
        f"Tes messages devront être confirmés avant d'être visibles."
    )


async def drunk_off(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Désactive le drunk mode pour l'utilisateur dans ce groupe."""
    chat_id = update.effective_chat.id
    user_id = update.effective_user.id

    key = (chat_id, user_id)
    if key in DRUNK_USERS:
        DRUNK_USERS.pop(key, None)
        PENDING_MESSAGES.pop(key, None)
        await update.message.reply_text("✅ Drunk Mode désactivé.")
    else:
        await update.message.reply_text("Tu n'es pas en Drunk Mode dans ce groupe.")


async def drunk_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Statut du drunk mode."""
    chat_id = update.effective_chat.id
    user_id = update.effective_user.id
    key = (chat_id, user_id)

    now = time.time()
    expiry_ts = DRUNK_USERS.get(key)

    if expiry_ts is None and key in DRUNK_USERS:
        await update.message.reply_text("🥴 Tu es actuellement en Drunk Mode (sans limite de temps).")
    elif expiry_ts and expiry_ts > now:
        remaining = int((expiry_ts - now) / 60)
        await update.message.reply_text(
            f"🥴 Tu es en Drunk Mode pour encore ~{remaining} minute(s)."
        )
    else:
        await update.message.reply_text("Tu n'es pas en Drunk Mode dans ce groupe.")


# =========================
# GESTION DES MESSAGES (DRUNK)
# =========================

async def drunk_message_filter(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Intercepte les messages des utilisateurs en Drunk Mode
    dans les groupes et demande confirmation.
    """
    if not update.message:
        return

    chat = update.effective_chat
    if chat.type not in ("group", "supergroup"):
        return

    user = update.effective_user
    if user.is_bot:
        return

    text = update.message.text
    if not text:
        return

    chat_id = chat.id
    user_id = user.id
    key = (chat_id, user_id)

    # Gestion expiration
    now = time.time()
    expiry_ts = DRUNK_USERS.get(key)
    if expiry_ts is not None:
        if expiry_ts < now:
            # Expiré
            DRUNK_USERS.pop(key, None)
            PENDING_MESSAGES.pop(key, None)
            return

    if key not in DRUNK_USERS:
        return  # pas en drunk mode => on laisse passer

    # On est en drunk mode : on supprime le message et on demande confirmation
    PENDING_MESSAGES[key] = {"text": text}

    # Supprimer le message original
    try:
        await context.bot.delete_message(chat_id=chat_id, message_id=update.message.message_id)
    except Exception:
        # Si le bot n'est pas admin / pas le droit, on ne pourra pas supprimer
        # Dans ce cas, on sort.
        return

    # Clavier de confirmation
    data_confirm = f"confirm|{chat_id}|{user_id}"
    data_cancel = f"cancel|{chat_id}|{user_id}"
    keyboard = [
        [
            InlineKeyboardButton("✅ Envoyer", callback_data=data_confirm),
            InlineKeyboardButton("❌ Annuler", callback_data=data_cancel),
        ]
    ]
    markup = InlineKeyboardMarkup(keyboard)

    preview = text if len(text) <= 120 else text[:117] + "..."

    # On tente en DM en priorité
    try:
        await context.bot.send_message(
            chat_id=user_id,
            text=(
                "🥴 Tu es en Drunk Mode.\n"
                "Je viens de retenir ce message :\n\n"
                f"« {preview} »\n\n"
                "Je l'envoie dans le groupe ?"
            ),
            reply_markup=markup,
        )
    except Exception:
        # Si DM impossible, on passe par le groupe
        await context.bot.send_message(
            chat_id=chat_id,
            text=(
                f"🥴 @{user.username or user.first_name}, tu es en Drunk Mode.\n"
                "Je retiens ton message. Je l'envoie ?\n\n"
                f"« {preview} »"
            ),
            reply_markup=markup,
        )


async def drunk_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Gestion des boutons ✅/❌."""
    query = update.callback_query
    await query.answer()

    data = query.data  # format: "confirm|chat_id|user_id" ou "cancel|..."
    try:
        action, chat_id_str, user_id_str = data.split("|")
        chat_id = int(chat_id_str)
        target_user_id = int(user_id_str)
    except ValueError:
        return

    # Sécurité : seul l'utilisateur concerné peut confirmer/annuler
    if query.from_user.id != target_user_id:
        await query.edit_message_text("Tu ne peux pas valider ce message.")
        return

    key = (chat_id, target_user_id)
    stored = PENDING_MESSAGES.get(key)

    if action == "cancel":
        PENDING_MESSAGES.pop(key, None)
        await query.edit_message_text("❌ Message annulé.")
        return

    if action == "confirm":
        if not stored:
            await query.edit_message_text("Le message a expiré ou a déjà été traité.")
            return

        text = stored["text"]
        PENDING_MESSAGES.pop(key, None)

        username = query.from_user.username
        display_name = f"@{username}" if username else query.from_user.first_name

        # On envoie dans le groupe
        await context.bot.send_message(
            chat_id=chat_id,
            text=f"💬 Message validé par {display_name} :\n{text}",
        )

        await query.edit_message_text("✅ Message envoyé dans le groupe.")


# =========================
# ANNIVERSAIRES & EVENTS
# =========================

def add_event_record(chat_id, type_, username, title, day, month, year=None):
    DATA["events"].append(
        {
            "chat_id": chat_id,
            "type": type_,
            "username": username,
            "title": title,
            "day": day,
            "month": month,
            "year": year,
        }
    )
    save_data()


async def add_bday(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    /add_bday @pseudo 15-02
    """
    if len(context.args) < 2:
        await update.message.reply_text("Usage : /add_bday @pseudo 15-02")
        return

    pseudo = context.args[0]
    date_raw = context.args[1]

    # Normalisation de la date : on remplace tous les caractères non numériques par des "-",
    # puis on split proprement. Ça gère les tirets chelous, espaces, etc.
    clean = re.sub(r"[^\d]", "-", date_raw)  # "15-02" -> "15-02", "15/02" -> "15-02"
    parts = [p for p in clean.split("-") if p]

    if len(parts) != 2:
        await update.message.reply_text("Format de date invalide. Utilise JJ-MM (ex: 25-03).")
        return

    try:
        day = int(parts[0])
        month = int(parts[1])
    except ValueError:
        await update.message.reply_text("Format de date invalide. Utilise JJ-MM (ex: 25-03).")
        return

    if pseudo.startswith("@"):
        username = pseudo[1:]
    else:
        username = pseudo

    chat_id = update.effective_chat.id
    title = f"Anniv {username}"

    add_event_record(chat_id, "birthday", username, title, day, month, year=None)

    await update.message.reply_text(
        f"🎂 Anniversaire de @{username} enregistré le {day:02d}-{month:02d}."
    )


async def list_bday(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Liste les anniversaires du groupe."""
    chat_id = update.effective_chat.id
    bdays = [
        e for e in DATA["events"]
        if e["chat_id"] == chat_id and e["type"] == "birthday"
    ]

    if not bdays:
        await update.message.reply_text("Aucun anniversaire enregistré pour ce groupe.")
        return

    lines = []
    for e in sorted(bdays, key=lambda x: (x["month"], x["day"], (x["username"] or ""))):
        username = e["username"] or "?"
        lines.append(f"- {e['day']:02d}-{e['month']:02d} : @{username}")

    await update.message.reply_text("🎂 Anniversaires enregistrés :\n" + "\n".join(lines))


async def add_event(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    /add_event 14-02-2026 Soirée raclette
    """
    if len(context.args) < 2:
        await update.message.reply_text("Usage : /add_event 14-02-2026 Titre de l'événement")
        return

    date_str = context.args[0]
    title = " ".join(context.args[1:])

    try:
        d_str, m_str, y_str = date_str.split("-")
        day = int(d_str)
        month = int(m_str)
        year = int(y_str)
        # validation simple
        _ = date(year, month, day)
    except Exception:
        await update.message.reply_text("Format de date invalide. Utilise JJ-MM-AAAA (ex: 14-02-2026).")
        return

    chat_id = update.effective_chat.id
    add_event_record(chat_id, "event", None, title, day, month, year)

    await update.message.reply_text(
        f"📅 Événement enregistré le {day:02d}-{month:02d}-{year} : {title}"
    )


async def list_events(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Liste les événements du groupe."""
    chat_id = update.effective_chat.id
    today = datetime.now(TZ).date()

    evts = [
        e for e in DATA["events"]
        if e["chat_id"] == chat_id and e["type"] == "event"
    ]

    if not evts:
        await update.message.reply_text("Aucun événement enregistré pour ce groupe.")
        return

    # tri par date
    def evt_date(e):
        return date(e["year"], e["month"], e["day"])

    lines = []
    for e in sorted(evts, key=evt_date):
        d = evt_date(e)
        status = "✅ passé" if d < today else "🕒 à venir"
        lines.append(f"- {d.strftime('%d-%m-%Y')} : {e['title']} ({status})")

    await update.message.reply_text("📅 Événements du groupe :\n" + "\n".join(lines))


# =========================
# RAPPELS QUOTIDIENS (J-7 / J-1)
# =========================

async def daily_reminder(context: ContextTypes.DEFAULT_TYPE):
    """
    Job quotidien qui envoie les rappels J-7 / J-1
    pour les anniversaires et événements.
    """
    today = datetime.now(TZ).date()

    for e in DATA["events"]:
        chat_id = e["chat_id"]
        type_ = e["type"]
        day = e["day"]
        month = e["month"]
        year = e.get("year")

        if type_ == "birthday":
            # prochaine occurrence de l'anniv
            evt_date = date(today.year, month, day)
            if evt_date < today:
                evt_date = date(today.year + 1, month, day)
        else:  # event daté
            if not year:
                continue
            evt_date = date(year, month, day)

        delta = (evt_date - today).days

        if delta not in (7, 1):
            continue

        # Message
        if type_ == "birthday":
            username = e["username"] or "?"
            if delta == 7:
                text = f"🎂 J-7 avant l'anniversaire de @{username} ({evt_date.strftime('%d-%m')}) !"
            else:
                text = f"🎂 Demain, c'est l'anniversaire de @{username} ({evt_date.strftime('%d-%m')}) !"
        else:
            title = e["title"]
            if delta == 7:
                text = f"📅 J-7 avant : {title} ({evt_date.strftime('%d-%m-%Y')})"
            else:
                text = f"📅 Demain : {title} ({evt_date.strftime('%d-%m-%Y')})"

        try:
            await context.bot.send_message(chat_id=chat_id, text=text)
        except Exception:
            # Si le bot est sorti du groupe ou autre → on ignore
            continue


# =========================
# START / HELP
# =========================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Hello 👋\n\n"
        "Je gère :\n"
        "🥴 Drunk Mode\n"
        "🎉 Anniversaires & événements\n\n"
        "Commandes utiles :\n"
        "- /drunk_on [minutes]\n"
        "- /drunk_off\n"
        "- /drunk_status\n"
        "- /add_bday @pseudo 25-03\n"
        "- /list_bday\n"
        "- /add_event 14-02-2026 Soirée raclette\n"
        "- /list_events\n"
    )


# =========================
# MAIN
# =========================

def main():
    load_data()

    app = ApplicationBuilder().token(BOT_TOKEN).build()

    # Commandes générales
    app.add_handler(CommandHandler("start", start))

    # Drunk mode
    app.add_handler(CommandHandler("drunk_on", drunk_on))
    app.add_handler(CommandHandler("drunk_off", drunk_off))
    app.add_handler(CommandHandler("drunk_status", drunk_status))

    # Anniversaires & events
    app.add_handler(CommandHandler("add_bday", add_bday))
    app.add_handler(CommandHandler("list_bday", list_bday))
    app.add_handler(CommandHandler("add_event", add_event))
    app.add_handler(CommandHandler("list_events", list_events))

    # Callbacks (drunk mode)
    app.add_handler(CallbackQueryHandler(drunk_callback, pattern="^(confirm|cancel)\\|"))

    # Messages texte dans les groupes (pour drunk mode)
    app.add_handler(
        MessageHandler(
            filters.TEXT & ~filters.COMMAND & filters.ChatType.GROUPS,
            drunk_message_filter,
        )
    )

    # 🔕 On désactive les rappels quotidiens pour l'instant
    # (sinon ça demande une config JobQueue spécifique)
    # Si tu veux les remettre plus tard, on réactivera ce bloc avec une JobQueue correctement initialisée.
    # from telegram.ext import JobQueue
    # app.job_queue = JobQueue()
    # app.job_queue.set_application(app)
    # app.job_queue.run_daily(
    #     daily_reminder,
    #     time=dtime(hour=9, minute=0, tz=TZ),
    # )

    print("Bot started.")
    app.run_polling()


if __name__ == "__main__":
    main()
