import json
import logging
import os
from datetime import date, timedelta, time
from zoneinfo import ZoneInfo

from telegram import BotCommand, InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import Application, CallbackQueryHandler, CommandHandler, ContextTypes

logging.basicConfig(format="%(asctime)s %(levelname)s %(message)s", level=logging.INFO)
logger = logging.getLogger(__name__)

BOT_TOKEN = "8756258315:AAH0caCOy4MQkG-jUMdPMmeyoJv9k0GeQjY"
CHAT_ID = -4985901416
TZ = ZoneInfo("Asia/Singapore")

STATE_FILE = os.path.join(os.path.dirname(__file__), "state.json")

NAMES = ["Alexis", "FC"]
MOPPING_BASE   = date(2026, 5, 9)   # week 0: Alexis mops
TOILET_BASE    = date(2026, 5, 9)   # biweek 0: FC cleans toilet
BEDSHEETS_BASE = date(2026, 5, 16)  # biweek 0: Alexis washes bedsheets

CHORE_EMOJI = {"mopping": "🧹", "toilet": "🚽", "bedsheets": "🛏"}
ALL_CHORES = ("mopping", "toilet", "bedsheets")

MOPPING_ROOMS = [
    ("master",    "Master Bedroom"),
    ("alexis_fc", "Alexis & FC Bedroom"),
    ("study",     "Study Room"),
    ("living",    "Living Room"),
    ("balcony1",  "Balcony 1"),
    ("balcony2",  "Balcony 2"),
    ("kitchen",   "Kitchen"),
]
_MOPPING_ROOM_KEYS = [k for k, _ in MOPPING_ROOMS]

TOILET_TASKS = [
    ("wash",    "Washing the toilet"),
    ("scrub",   "Scrubbing the toilet bowl"),
    ("mirrors", "Wiping the 2 mirrors"),
]
_TOILET_TASK_KEYS = [k for k, _ in TOILET_TASKS]


# ── Schedule helpers ──────────────────────────────────────────────────────────

def _mopping_person(saturday: date) -> str:
    weeks = (saturday - MOPPING_BASE).days // 7
    return NAMES[weeks % 2]


def _is_toilet_weekend(saturday: date) -> bool:
    delta = (saturday - TOILET_BASE).days
    return delta >= 0 and delta % 14 == 0


def _toilet_person(saturday: date) -> str:
    nth = (saturday - TOILET_BASE).days // 14
    return NAMES[(nth + 1) % 2]  # biweek 0 → FC, biweek 1 → Alexis


def _is_bedsheets_weekend(saturday: date) -> bool:
    delta = (saturday - BEDSHEETS_BASE).days
    return delta >= 0 and delta % 14 == 0


def _bedsheets_person(_saturday: date) -> str:
    return "FC & Alexis"  # both wash their own bedsheets


def _this_saturday() -> date:
    today = date.today()
    w = today.weekday()
    if w == 6:
        return today - timedelta(days=1)
    return today + timedelta(days=(5 - w) % 7)


def _last_saturday() -> date:
    today = date.today()
    w = today.weekday()
    return today - timedelta(days=(w - 5) % 7)


def _fmt(d: date) -> str:
    return f"{d.day} {d.strftime('%b')}"


def _is_chore_weekend(chore: str, saturday: date) -> bool:
    if chore == "mopping":
        return True
    if chore == "toilet":
        return _is_toilet_weekend(saturday)
    if chore == "bedsheets":
        return _is_bedsheets_weekend(saturday)
    return False


def _chore_person(chore: str, saturday: date) -> str:
    if chore == "mopping":
        return _mopping_person(saturday)
    if chore == "toilet":
        return _toilet_person(saturday)
    if chore == "bedsheets":
        return _bedsheets_person(saturday)
    return "?"


def _lapsed_person(chore: str) -> str:
    """Person responsible for a lapsed chore — looks back to last due Saturday."""
    sat = _last_saturday()
    if chore == "mopping":
        return _mopping_person(sat)
    # For biweekly chores, go back to the most recent due Saturday
    while not _is_chore_weekend(chore, sat):
        sat -= timedelta(weeks=1)
    return _chore_person(chore, sat)


# ── State ─────────────────────────────────────────────────────────────────────

def load_state() -> dict:
    defaults = {
        "mopping_done": False,   "toilet_done": False,   "bedsheets_done": False,
        "mopping_lapsed": False, "toilet_lapsed": False, "bedsheets_lapsed": False,
        "mopping_rooms": {key: False for key in _MOPPING_ROOM_KEYS},
        "toilet_tasks": {key: False for key in _TOILET_TASK_KEYS},
        "last_reminder_sat": None,
        "last_reminder_chat": None,
        "last_sunday_sat": None,
        "last_lapse_sat": None,
        "chat_id": None,
    }
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE) as f:
            saved = json.load(f)
        defaults.update(saved)
        for key in _MOPPING_ROOM_KEYS:
            defaults["mopping_rooms"].setdefault(key, False)
        for key in _TOILET_TASK_KEYS:
            defaults["toilet_tasks"].setdefault(key, False)
    return defaults


def save_state(state: dict):
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, indent=2)


def _chat_id(state: dict) -> int:
    """Effective chat for reminders: saved from commands, else hardcoded fallback."""
    return state.get("chat_id") or CHAT_ID


def _save_chat(state: dict, update: Update):
    """Save the chat where a command was sent so scheduled jobs know where to push."""
    cid = update.effective_chat.id
    if state.get("chat_id") != cid:
        state["chat_id"] = cid
        save_state(state)


# ── Message builders ──────────────────────────────────────────────────────────

def _done_keyboard(chore: str) -> InlineKeyboardMarkup:
    if chore in ("mopping", "toilet"):
        return InlineKeyboardMarkup([
            [InlineKeyboardButton("✅ Done!", callback_data=f"done_{chore}")],
            [InlineKeyboardButton("🔸 Not completely done", callback_data=f"partial_{chore}")],
        ])
    return InlineKeyboardMarkup([[InlineKeyboardButton("✅ Done!", callback_data=f"done_{chore}")]])


def _build_mopping_rooms_text(rooms: dict) -> str:
    lines = ["🧹 Mopping — tick off each room as you go:\n"]
    for key, label in MOPPING_ROOMS:
        mark = "✅" if rooms.get(key) else "☐"
        lines.append(f"{mark} {label}")
    done_count = sum(1 for key in _MOPPING_ROOM_KEYS if rooms.get(key))
    lines.append(f"\n{done_count}/{len(MOPPING_ROOMS)} rooms done")
    return "\n".join(lines)


def _mopping_rooms_keyboard(rooms: dict) -> InlineKeyboardMarkup:
    buttons = []
    for key, label in MOPPING_ROOMS:
        mark = "✅" if rooms.get(key) else "☐"
        buttons.append([InlineKeyboardButton(f"{mark} {label}", callback_data=f"room_mopping_{key}")])
    buttons.append([InlineKeyboardButton("⬅️ Back", callback_data="rooms_back_mopping")])
    return InlineKeyboardMarkup(buttons)


def _build_toilet_tasks_text(tasks: dict) -> str:
    lines = ["🚽 Toilet — tick off each task as you go:\n"]
    for key, label in TOILET_TASKS:
        mark = "✅" if tasks.get(key) else "☐"
        lines.append(f"{mark} {label}")
    done_count = sum(1 for key in _TOILET_TASK_KEYS if tasks.get(key))
    lines.append(f"\n{done_count}/{len(TOILET_TASKS)} tasks done")
    return "\n".join(lines)


def _toilet_tasks_keyboard(tasks: dict) -> InlineKeyboardMarkup:
    buttons = []
    for key, label in TOILET_TASKS:
        mark = "✅" if tasks.get(key) else "☐"
        buttons.append([InlineKeyboardButton(f"{mark} {label}", callback_data=f"task_toilet_{key}")])
    buttons.append([InlineKeyboardButton("⬅️ Back", callback_data="tasks_back_toilet")])
    return InlineKeyboardMarkup(buttons)


def _build_main_menu(state: dict) -> tuple:
    this_sat = _this_saturday()
    lines = ["📋 Chore Dashboard\n"]

    for chore in ALL_CHORES:
        emoji = CHORE_EMOJI[chore]
        if state.get(f"{chore}_lapsed"):
            person = _lapsed_person(chore)
            lines.append(f"{emoji} {chore.title()}: ⚠️ OVERDUE ({person})")
        elif _is_chore_weekend(chore, this_sat):
            person = _chore_person(chore, this_sat)
            done = state[f"{chore}_done"]
            status = "✅ Done" if done else "❌ Pending"
            lines.append(f"{emoji} {chore.title()}: {status} ({person}, Sat {_fmt(this_sat)})")
        else:
            next_sat = this_sat + timedelta(weeks=1)
            while not _is_chore_weekend(chore, next_sat):
                next_sat += timedelta(weeks=1)
            person = _chore_person(chore, next_sat)
            lines.append(f"{emoji} {chore.title()}: ⏳ Next Sat {_fmt(next_sat)} ({person})")

    lines.append("\nTap a chore for details:")
    text = "\n".join(lines)
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("🧹 Mopping", callback_data="menu_mopping")],
        [InlineKeyboardButton("🚽 Toilet", callback_data="menu_toilet")],
        [InlineKeyboardButton("🛏 Bedsheets", callback_data="menu_bedsheets")],
    ])
    return text, keyboard


def _build_chore_detail(chore: str, state: dict) -> tuple:
    this_sat = _this_saturday()
    emoji = CHORE_EMOJI[chore]
    freq = "every weekend" if chore == "mopping" else "every 2 weeks"
    lines = [f"{emoji} {chore.title()}", f"Schedule: {freq}", ""]

    # Upcoming 4-week schedule
    lines.append("📅 Upcoming:")
    for i in range(5):
        sat = this_sat + timedelta(weeks=i)
        if _is_chore_weekend(chore, sat):
            person = _chore_person(chore, sat)
            marker = " ← this weekend" if i == 0 else ""
            lines.append(f"  • Sat {_fmt(sat)}: {person}{marker}")

    lines.append("")

    # Current status
    done = state[f"{chore}_done"]
    lapsed = state.get(f"{chore}_lapsed")

    if lapsed:
        person = _lapsed_person(chore)
        lines.append(f"⚠️ OVERDUE from last weekend — {person}, please do it ASAP!")
        buttons = [
            [InlineKeyboardButton("✅ Done! (clear overdue)", callback_data=f"update_done_{chore}")],
        ]
        if chore in ("mopping", "toilet"):
            buttons.append([InlineKeyboardButton("🔸 Not completely done", callback_data=f"partial_{chore}")])
        buttons.append([InlineKeyboardButton("⬅️ Back", callback_data="menu_main")])
    elif _is_chore_weekend(chore, this_sat):
        person = _chore_person(chore, this_sat)
        status = "✅ Done this weekend!" if done else f"❌ Not done yet — {person}'s turn"
        lines.append(f"This weekend: {status}")
        if done:
            buttons = [
                [InlineKeyboardButton("↩️ Undo", callback_data=f"undo_{chore}")],
                [InlineKeyboardButton("⬅️ Back", callback_data="menu_main")],
            ]
        else:
            buttons = [
                [InlineKeyboardButton("✅ Mark Done", callback_data=f"update_done_{chore}")],
            ]
            if chore in ("mopping", "toilet"):
                buttons.append([InlineKeyboardButton("🔸 Not completely done", callback_data=f"partial_{chore}")])
            buttons.append([InlineKeyboardButton("⬅️ Back", callback_data="menu_main")])
    else:
        next_sat = this_sat + timedelta(weeks=1)
        while not _is_chore_weekend(chore, next_sat):
            next_sat += timedelta(weeks=1)
        person = _chore_person(chore, next_sat)
        lines.append(f"Not this weekend — next up: Sat {_fmt(next_sat)}, {person}'s turn")
        buttons = [[InlineKeyboardButton("⬅️ Back", callback_data="menu_main")]]

    return "\n".join(lines), InlineKeyboardMarkup(buttons)


# ── Weekend reminder sender ───────────────────────────────────────────────────

async def _send_weekend_reminders(context, saturday: date, chat_id: int):
    for chore in ALL_CHORES:
        if not _is_chore_weekend(chore, saturday):
            continue
        person = _chore_person(chore, saturday)
        emoji = CHORE_EMOJI[chore]
        label = chore.title()
        await context.bot.send_message(
            chat_id=chat_id,
            text=f"{emoji} {person}, rmb {label}? 😏 Do it today okay!",
            reply_markup=_done_keyboard(chore),
        )


# ── Scheduled jobs ────────────────────────────────────────────────────────────

async def saturday_job(context: ContextTypes.DEFAULT_TYPE):
    today = date.today()
    logger.info("saturday_job running for %s", today)
    state = load_state()
    for chore in ALL_CHORES:
        state[f"{chore}_done"] = False
        state[f"{chore}_lapsed"] = False
    state["mopping_rooms"] = {key: False for key in _MOPPING_ROOM_KEYS}
    state["toilet_tasks"] = {key: False for key in _TOILET_TASK_KEYS}
    chat_id = _chat_id(state)
    state["last_reminder_sat"] = today.isoformat()
    state["last_reminder_chat"] = chat_id
    save_state(state)
    await _send_weekend_reminders(context, today, chat_id)


async def sunday_job(context: ContextTypes.DEFAULT_TYPE):
    this_sat = date.today() - timedelta(days=1)
    logger.info("sunday_job running, this_sat=%s", this_sat)
    state = load_state()
    state["last_sunday_sat"] = this_sat.isoformat()
    save_state(state)
    chat_id = _chat_id(state)
    for chore in ALL_CHORES:
        if _is_chore_weekend(chore, this_sat) and not state[f"{chore}_done"]:
            person = _chore_person(chore, this_sat)
            emoji = CHORE_EMOJI[chore]
            label = chore.title()
            await context.bot.send_message(
                chat_id=chat_id,
                text=f"{emoji} {person} still haven't done {label}! Do it today! Why push it to weekday man 😤",
                reply_markup=_done_keyboard(chore),
            )


async def weekday_job(context: ContextTypes.DEFAULT_TYPE):
    """9am Mon–Fri: lapse undone weekend chores and nag daily until done."""
    today = date.today()
    w = today.weekday()
    last_sat = _last_saturday()
    logger.info("weekday_job running on %s (weekday=%d)", today, w)
    state = load_state()
    chat_id = _chat_id(state)

    if state.get("last_lapse_sat") != last_sat.isoformat():
        # Lapse check not yet done for this week — run it now
        # If saturday_job was missed, done flags may be stale from a previous week
        if state.get("last_reminder_sat") != last_sat.isoformat():
            for chore in ALL_CHORES:
                state[f"{chore}_done"] = False
                state[f"{chore}_lapsed"] = False
            state["mopping_rooms"] = {key: False for key in _MOPPING_ROOM_KEYS}
            state["toilet_tasks"] = {key: False for key in _TOILET_TASK_KEYS}
        for chore in ALL_CHORES:
            if _is_chore_weekend(chore, last_sat) and not state[f"{chore}_done"]:
                state[f"{chore}_lapsed"] = True
        for chore in ALL_CHORES:
            state[f"{chore}_done"] = False
        state["mopping_rooms"] = {key: False for key in _MOPPING_ROOM_KEYS}
        state["toilet_tasks"] = {key: False for key in _TOILET_TASK_KEYS}
        state["last_lapse_sat"] = last_sat.isoformat()
        save_state(state)

    for chore in ALL_CHORES:
        if state.get(f"{chore}_lapsed"):
            person = _lapsed_person(chore)
            emoji = CHORE_EMOJI[chore]
            label = chore.title()
            await context.bot.send_message(
                chat_id=chat_id,
                text=f"{emoji} {label} still not done?? {person} last chance bro 😩",
                reply_markup=_done_keyboard(chore),
            )


async def startup_check(context: ContextTypes.DEFAULT_TYPE):
    today = date.today()
    w = today.weekday()
    logger.info("startup_check running, today=%s weekday=%d", today, w)

    if w not in (5, 6):
        # Weekday: run weekday_job if lapse check is pending or chores are still lapsed
        state = load_state()
        last_sat = _last_saturday()
        lapse_pending = state.get("last_lapse_sat") != last_sat.isoformat()
        reminders_pending = any(state.get(f"{c}_lapsed") for c in ALL_CHORES)
        if lapse_pending or reminders_pending:
            logger.info("startup_check: running weekday_job (lapse_pending=%s, reminders_pending=%s)",
                        lapse_pending, reminders_pending)
            await weekday_job(context)
        return

    this_sat = today if w == 5 else today - timedelta(days=1)
    state = load_state()
    chat_id = _chat_id(state)

    if state.get("last_reminder_sat") != this_sat.isoformat() or state.get("last_reminder_chat") != chat_id:
        logger.info("startup_check: sending missed weekend reminders for %s to %s", this_sat, chat_id)
        for chore in ALL_CHORES:
            state[f"{chore}_done"] = False
            state[f"{chore}_lapsed"] = False
        state["mopping_rooms"] = {key: False for key in _MOPPING_ROOM_KEYS}
        state["toilet_tasks"] = {key: False for key in _TOILET_TASK_KEYS}
        state["last_reminder_sat"] = this_sat.isoformat()
        state["last_reminder_chat"] = chat_id
        save_state(state)
        await _send_weekend_reminders(context, this_sat, chat_id)

    if w == 6 and state.get("last_sunday_sat") != this_sat.isoformat():
        logger.info("startup_check: running missed Sunday nudge for %s", this_sat)
        await sunday_job(context)


# ── Callback handler ──────────────────────────────────────────────────────────

async def callback_handler(update: Update, _context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data
    state = load_state()
    name = update.effective_user.first_name
    today = date.today()

    # ── Navigation ────────────────────────────────────────────────────────────
    if data.startswith("menu_"):
        chore = data[5:]
        if chore == "main":
            text, markup = _build_main_menu(state)
        else:
            text, markup = _build_chore_detail(chore, state)
        await query.edit_message_text(text, reply_markup=markup)
        return

    # ── Mopping room breakdown ────────────────────────────────────────────────
    if data == "partial_mopping":
        rooms = state.get("mopping_rooms", {})
        await query.edit_message_text(_build_mopping_rooms_text(rooms), reply_markup=_mopping_rooms_keyboard(rooms))
        return

    if data.startswith("room_mopping_"):
        room_key = data[len("room_mopping_"):]
        rooms = state.setdefault("mopping_rooms", {key: False for key in _MOPPING_ROOM_KEYS})
        rooms[room_key] = not rooms.get(room_key, False)
        if all(rooms.get(k) for k in _MOPPING_ROOM_KEYS):
            state["mopping_done"] = True
            state["mopping_lapsed"] = False
            save_state(state)
            await query.edit_message_text(
                f"✅ Thanks {name}! 🧹 Mopping done! All rooms ticked off 🎉",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("↩️ Undo", callback_data="undo_mopping")]]),
            )
        else:
            save_state(state)
            await query.edit_message_text(_build_mopping_rooms_text(rooms), reply_markup=_mopping_rooms_keyboard(rooms))
        return

    if data == "rooms_back_mopping":
        text, markup = _build_chore_detail("mopping", state)
        await query.edit_message_text(text, reply_markup=markup)
        return

    # ── Toilet task breakdown ─────────────────────────────────────────────────
    if data == "partial_toilet":
        tasks = state.get("toilet_tasks", {})
        await query.edit_message_text(_build_toilet_tasks_text(tasks), reply_markup=_toilet_tasks_keyboard(tasks))
        return

    if data.startswith("task_toilet_"):
        task_key = data[len("task_toilet_"):]
        tasks = state.setdefault("toilet_tasks", {key: False for key in _TOILET_TASK_KEYS})
        tasks[task_key] = not tasks.get(task_key, False)
        if all(tasks.get(k) for k in _TOILET_TASK_KEYS):
            state["toilet_done"] = True
            state["toilet_lapsed"] = False
            save_state(state)
            await query.edit_message_text(
                f"✅ Thanks {name}! 🚽 Toilet done! All tasks ticked off 🎉",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("↩️ Undo", callback_data="undo_toilet")]]),
            )
        else:
            save_state(state)
            await query.edit_message_text(_build_toilet_tasks_text(tasks), reply_markup=_toilet_tasks_keyboard(tasks))
        return

    if data == "tasks_back_toilet":
        text, markup = _build_chore_detail("toilet", state)
        await query.edit_message_text(text, reply_markup=markup)
        return

    # ── Parse action + chore ──────────────────────────────────────────────────
    chore = action = None
    for prefix in ("update_done_", "done_", "undo_"):
        if data.startswith(prefix):
            chore = data[len(prefix):]
            action = prefix.rstrip("_")
            break

    if chore not in ALL_CHORES:
        return

    done_key    = f"{chore}_done"
    lapsed_key  = f"{chore}_lapsed"
    emoji       = CHORE_EMOJI[chore]

    # ── Mark done ─────────────────────────────────────────────────────────────
    if action in ("done", "update_done"):
        if state[done_key] and not state.get(lapsed_key):
            await query.answer("Already marked done!")
            return
        state[done_key]   = True
        state[lapsed_key] = False
        save_state(state)

        if action == "done":
            # From a reminder message → show thanks + undo button
            undo_markup = InlineKeyboardMarkup([[
                InlineKeyboardButton("↩️ Undo", callback_data=f"undo_{chore}")
            ]])
            await query.edit_message_text(
                f"✅ Thanks {name}! {emoji} {chore.title()} done! 🎉",
                reply_markup=undo_markup,
            )
        else:
            # From status/detail → refresh detail view
            text, markup = _build_chore_detail(chore, state)
            await query.edit_message_text(text, reply_markup=markup)

    # ── Undo ──────────────────────────────────────────────────────────────────
    elif action == "undo":
        state[done_key] = False
        w = today.weekday()

        if w == 5:  # Saturday: just warn
            save_state(state)
            person = _chore_person(chore, today)
            await query.edit_message_text(
                f"⚠️ {emoji} {chore.title()} unmarked.\n\n"
                f"{person}, please get it done before Sunday! ⏰",
                reply_markup=_done_keyboard(chore),
            )
        else:  # Sunday or weekday: lapse
            state[lapsed_key] = True
            save_state(state)
            person = _lapsed_person(chore)
            await query.edit_message_text(
                f"⚠️ {emoji} {chore.title()} lapsed!\n\n"
                f"{person}, you'll get daily reminders until it's done.",
                reply_markup=_done_keyboard(chore),
            )


# ── Commands ──────────────────────────────────────────────────────────────────

async def start(update: Update, _context: ContextTypes.DEFAULT_TYPE):
    state = load_state()
    _save_chat(state, update)
    await update.message.reply_text(
        "👋 Chore bot is running!\n\nUse /status to see all chores, "
        "or use /mopping, /toilet, /bedsheets for individual chore details."
    )


async def status(update: Update, _context: ContextTypes.DEFAULT_TYPE):
    state = load_state()
    _save_chat(state, update)
    text, markup = _build_main_menu(state)
    await update.message.reply_text(text, reply_markup=markup)


async def cmd_mopping(update: Update, _context: ContextTypes.DEFAULT_TYPE):
    state = load_state()
    _save_chat(state, update)
    text, markup = _build_chore_detail("mopping", state)
    await update.message.reply_text(text, reply_markup=markup)


async def cmd_toilet(update: Update, _context: ContextTypes.DEFAULT_TYPE):
    state = load_state()
    _save_chat(state, update)
    text, markup = _build_chore_detail("toilet", state)
    await update.message.reply_text(text, reply_markup=markup)


async def cmd_bedsheets(update: Update, _context: ContextTypes.DEFAULT_TYPE):
    state = load_state()
    _save_chat(state, update)
    text, markup = _build_chore_detail("bedsheets", state)
    await update.message.reply_text(text, reply_markup=markup)


async def chatid(update: Update, _context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(f"This chat's ID is: `{update.effective_chat.id}`", parse_mode="Markdown")


async def error_handler(_update: object, context: ContextTypes.DEFAULT_TYPE):
    logger.error("Unhandled exception", exc_info=context.error)


# ── Bot setup ─────────────────────────────────────────────────────────────────

async def post_init(application: Application):
    await application.bot.set_my_commands([
        BotCommand("status",    "View all chores dashboard"),
        BotCommand("mopping",   "Mopping schedule and status"),
        BotCommand("toilet",    "Toilet cleaning schedule and status"),
        BotCommand("bedsheets", "Bedsheets schedule and status"),
    ])
    logger.info("Bot commands menu set.")


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    app = Application.builder().token(BOT_TOKEN).post_init(post_init).build()

    app.add_handler(CommandHandler("start",     start))
    app.add_handler(CommandHandler("status",    status))
    app.add_handler(CommandHandler("mopping",   cmd_mopping))
    app.add_handler(CommandHandler("toilet",    cmd_toilet))
    app.add_handler(CommandHandler("bedsheets", cmd_bedsheets))
    app.add_handler(CommandHandler("chatid",    chatid))
    app.add_handler(CallbackQueryHandler(callback_handler))
    app.add_error_handler(error_handler)

    jq = app.job_queue
    # days: 0=Mon 1=Tue 2=Wed 3=Thu 4=Fri 5=Sat 6=Sun  (python-telegram-bot convention)
    jq.run_daily(saturday_job,         time=time(9,  0, tzinfo=TZ), days=(5,))
    jq.run_daily(sunday_job,           time=time(9,  0, tzinfo=TZ), days=(6,))
    jq.run_daily(weekday_job,          time=time(9,  0, tzinfo=TZ), days=(0, 1, 2, 3, 4))
    jq.run_once(startup_check, when=5)

    logger.info("Chore bot starting...")
    app.run_polling()


if __name__ == "__main__":
    main()
