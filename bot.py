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
CHAT_ID = 4985901416
TZ = ZoneInfo("Asia/Singapore")

STATE_FILE = os.path.join(os.path.dirname(__file__), "state.json")

NAMES = ["Alexis", "FC"]
MOPPING_BASE   = date(2026, 5, 9)   # week 0: Alexis mops
TOILET_BASE    = date(2026, 5, 9)   # biweek 0: FC cleans toilet
BEDSHEETS_BASE = date(2026, 5, 16)  # biweek 0: Alexis washes bedsheets

CHORE_EMOJI = {"mopping": "🧹", "toilet": "🚽", "bedsheets": "🛏"}
ALL_CHORES = ("mopping", "toilet", "bedsheets")


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
        "last_reminder_sat": None,
        "pinned_message_id": None,
    }
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE) as f:
            saved = json.load(f)
        defaults.update(saved)
    return defaults


def save_state(state: dict):
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, indent=2)


# ── Message builders ──────────────────────────────────────────────────────────

def _done_keyboard(chore: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[InlineKeyboardButton("✅ Done!", callback_data=f"done_{chore}")]])


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
            [InlineKeyboardButton("⬅️ Back", callback_data="menu_main")],
        ]
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
                [InlineKeyboardButton("⬅️ Back", callback_data="menu_main")],
            ]
    else:
        next_sat = this_sat + timedelta(weeks=1)
        while not _is_chore_weekend(chore, next_sat):
            next_sat += timedelta(weeks=1)
        person = _chore_person(chore, next_sat)
        lines.append(f"Not this weekend — next up: Sat {_fmt(next_sat)}, {person}'s turn")
        buttons = [[InlineKeyboardButton("⬅️ Back", callback_data="menu_main")]]

    return "\n".join(lines), InlineKeyboardMarkup(buttons)


# ── Pinned dashboard helpers ──────────────────────────────────────────────────

async def _update_pinned_dashboard(context, state: dict):
    """Silently edit the existing pinned dashboard message in place."""
    msg_id = state.get("pinned_message_id")
    if not msg_id:
        return
    try:
        text, markup = _build_main_menu(state)
        await context.bot.edit_message_text(
            chat_id=CHAT_ID, message_id=msg_id, text=text, reply_markup=markup,
        )
    except Exception as e:
        logger.warning("Could not update pinned dashboard: %s", e)


async def _send_and_pin_dashboard(context, state: dict):
    """Send a fresh dashboard message to the group and pin it."""
    old_id = state.get("pinned_message_id")
    if old_id:
        try:
            await context.bot.unpin_chat_message(chat_id=CHAT_ID, message_id=old_id)
        except Exception:
            pass

    text, markup = _build_main_menu(state)
    msg = await context.bot.send_message(chat_id=CHAT_ID, text=text, reply_markup=markup)
    try:
        await context.bot.pin_chat_message(
            chat_id=CHAT_ID, message_id=msg.message_id, disable_notification=True,
        )
    except Exception as e:
        logger.warning("Could not pin dashboard: %s", e)

    state["pinned_message_id"] = msg.message_id
    save_state(state)


# ── Weekend reminder sender ───────────────────────────────────────────────────

async def _send_weekend_reminders(context, saturday: date):
    for chore in ALL_CHORES:
        if not _is_chore_weekend(chore, saturday):
            continue
        person = _chore_person(chore, saturday)
        emoji = CHORE_EMOJI[chore]
        label = chore.title()
        await context.bot.send_message(
            chat_id=CHAT_ID,
            text=f"{emoji} Weekend chore reminder!\n\n{person}, it's your turn to do {label}!",
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
    state["last_reminder_sat"] = today.isoformat()
    save_state(state)
    await _send_and_pin_dashboard(context, state)
    await _send_weekend_reminders(context, today)


async def sunday_job(context: ContextTypes.DEFAULT_TYPE):
    this_sat = date.today() - timedelta(days=1)
    logger.info("sunday_job running, this_sat=%s", this_sat)
    state = load_state()
    for chore in ALL_CHORES:
        if _is_chore_weekend(chore, this_sat) and not state[f"{chore}_done"]:
            person = _chore_person(chore, this_sat)
            emoji = CHORE_EMOJI[chore]
            await context.bot.send_message(
                chat_id=CHAT_ID,
                text=f"⚠️ {chore.title()} still not done! {person}, last chance today {emoji}",
                reply_markup=_done_keyboard(chore),
            )


async def weekday_job(context: ContextTypes.DEFAULT_TYPE):
    """9am Mon–Fri: on Monday auto-lapse undone weekend chores, then nag daily until done."""
    today = date.today()
    w = today.weekday()
    logger.info("weekday_job running on %s (weekday=%d)", today, w)
    state = load_state()

    if w == 0:  # Monday: check last weekend and set lapse flags
        this_sat = today - timedelta(days=2)
        for chore in ALL_CHORES:
            if _is_chore_weekend(chore, this_sat) and not state[f"{chore}_done"]:
                state[f"{chore}_lapsed"] = True
        for chore in ALL_CHORES:
            state[f"{chore}_done"] = False
        save_state(state)

    for chore in ALL_CHORES:
        if state.get(f"{chore}_lapsed"):
            person = _lapsed_person(chore)
            emoji = CHORE_EMOJI[chore]
            label = chore.title()
            await context.bot.send_message(
                chat_id=CHAT_ID,
                text=f"{emoji} {label} not done ah...anyhow.. {person} do it ASAP!",
                reply_markup=_done_keyboard(chore),
            )


async def startup_check(context: ContextTypes.DEFAULT_TYPE):
    today = date.today()
    w = today.weekday()
    logger.info("startup_check running, today=%s weekday=%d", today, w)

    # On weekdays, re-send lapse reminders and refresh pinned dashboard
    if w not in (5, 6):
        state = load_state()
        if any(state.get(f"{c}_lapsed") for c in ALL_CHORES):
            logger.info("startup_check: lapsed chores found, sending reminders")
            await weekday_job(context)
        await _update_pinned_dashboard(context, state)
        return

    this_sat = today if w == 5 else today - timedelta(days=1)
    state = load_state()

    if state.get("last_reminder_sat") == this_sat.isoformat():
        logger.info("startup_check: reminders already sent for %s", this_sat)
        return

    logger.info("startup_check: sending missed weekend reminders for %s", this_sat)
    for chore in ALL_CHORES:
        state[f"{chore}_done"] = False
        state[f"{chore}_lapsed"] = False
    state["last_reminder_sat"] = this_sat.isoformat()
    save_state(state)
    await _send_and_pin_dashboard(context, state)
    await _send_weekend_reminders(context, this_sat)


# ── Callback handler ──────────────────────────────────────────────────────────

async def callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data
    state = load_state()
    name = update.effective_user.first_name
    today = date.today()
    is_pinned = query.message.message_id == state.get("pinned_message_id")

    # ── Navigation ────────────────────────────────────────────────────────────
    if data.startswith("menu_"):
        chore = data[5:]
        if chore == "main":
            text, markup = _build_main_menu(state)
        else:
            text, markup = _build_chore_detail(chore, state)
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

        if not is_pinned:
            await _update_pinned_dashboard(context, state)

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

        if not is_pinned:
            await _update_pinned_dashboard(context, state)


# ── Commands ──────────────────────────────────────────────────────────────────

async def start(update: Update, _context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "👋 Chore bot is running!\n\nUse /status to see all chores, "
        "or use /mopping, /toilet, /bedsheets for individual chore details."
    )


async def status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    state = load_state()
    old_id = state.get("pinned_message_id")
    if old_id:
        try:
            text, markup = _build_main_menu(state)
            await context.bot.edit_message_text(
                chat_id=CHAT_ID, message_id=old_id, text=text, reply_markup=markup,
            )
            await update.message.reply_text("Dashboard refreshed! Check the pinned message ☝️")
            return
        except Exception:
            pass
    await _send_and_pin_dashboard(context, state)
    await update.message.reply_text("Dashboard pinned to the group! ☝️")


async def cmd_mopping(update: Update, _context: ContextTypes.DEFAULT_TYPE):
    state = load_state()
    text, markup = _build_chore_detail("mopping", state)
    await update.message.reply_text(text, reply_markup=markup)


async def cmd_toilet(update: Update, _context: ContextTypes.DEFAULT_TYPE):
    state = load_state()
    text, markup = _build_chore_detail("toilet", state)
    await update.message.reply_text(text, reply_markup=markup)


async def cmd_bedsheets(update: Update, _context: ContextTypes.DEFAULT_TYPE):
    state = load_state()
    text, markup = _build_chore_detail("bedsheets", state)
    await update.message.reply_text(text, reply_markup=markup)


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
    app.add_handler(CallbackQueryHandler(callback_handler))

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
