import json
import os
from datetime import date, timedelta, time
from zoneinfo import ZoneInfo

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import Application, CallbackQueryHandler, CommandHandler, ContextTypes

BOT_TOKEN = "8756258315:AAH0caCOy4MQkG-jUMdPMmeyoJv9k0GeQjY"
CHAT_ID = 4985901416
TZ = ZoneInfo("Asia/Singapore")

STATE_FILE = os.path.join(os.path.dirname(__file__), "state.json")

NAMES = ["Alexis", "FC"]          # index 0 = Alexis, index 1 = FC
MOPPING_BASE = date(2026, 5, 9)   # week 0: Alexis mops
TOILET_BASE  = date(2026, 5, 9)   # toilet weekend 0: FC cleans


# ── Schedule helpers (calculated from fixed base dates, no state needed) ──────

def _mopping_person(saturday: date) -> str:
    weeks = (saturday - MOPPING_BASE).days // 7
    return NAMES[weeks % 2]


def _is_toilet_weekend(saturday: date) -> bool:
    delta = (saturday - TOILET_BASE).days
    return delta >= 0 and delta % 14 == 0


def _toilet_person(saturday: date) -> str:
    delta = (saturday - TOILET_BASE).days
    nth = delta // 14
    return NAMES[(nth + 1) % 2]  # nth=0→FC, nth=1→Alexis, ...


def _this_saturday() -> date:
    today = date.today()
    w = today.weekday()
    if w == 6:
        return today - timedelta(days=1)
    return today + timedelta(days=(5 - w) % 7)


def _fmt(d: date) -> str:
    return f"{d.day} {d.strftime('%b')}"


# ── State (only tracks done flags and last reminder date) ─────────────────────

def load_state() -> dict:
    defaults = {
        "mopping_done": False,
        "toilet_done": False,
        "last_reminder_sat": None,
    }
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE) as f:
            saved = json.load(f)
        defaults.update(saved)
    return defaults


def save_state(state: dict):
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, indent=2)


def _done_keyboard(chore: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[InlineKeyboardButton("✅ Done!", callback_data=f"done_{chore}")]])


# ── Scheduled jobs ────────────────────────────────────────────────────────────

async def saturday_job(context: ContextTypes.DEFAULT_TYPE):
    today = date.today()
    state = load_state()
    state["mopping_done"] = False
    state["toilet_done"] = False
    state["last_reminder_sat"] = today.isoformat()
    save_state(state)

    mop_person = _mopping_person(today)
    await context.bot.send_message(
        chat_id=CHAT_ID,
        text=f"🧹 Weekend chore reminder!\n\n{mop_person}, it's your turn to mop the floor!",
        reply_markup=_done_keyboard("mopping"),
    )

    if _is_toilet_weekend(today):
        toilet_person = _toilet_person(today)
        await context.bot.send_message(
            chat_id=CHAT_ID,
            text=f"🚽 Toilet cleaning week too!\n\n{toilet_person}, it's your turn to scrub the toilet!",
            reply_markup=_done_keyboard("toilet"),
        )


async def sunday_job(context: ContextTypes.DEFAULT_TYPE):
    this_sat = date.today() - timedelta(days=1)
    state = load_state()

    if not state["mopping_done"]:
        mop_person = _mopping_person(this_sat)
        await context.bot.send_message(
            chat_id=CHAT_ID,
            text=f"⚠️ Mopping still not done! {mop_person}, last chance today 🧹",
            reply_markup=_done_keyboard("mopping"),
        )

    if _is_toilet_weekend(this_sat) and not state["toilet_done"]:
        toilet_person = _toilet_person(this_sat)
        await context.bot.send_message(
            chat_id=CHAT_ID,
            text=f"⚠️ Toilet still not done! {toilet_person}, last chance today 🚽",
            reply_markup=_done_keyboard("toilet"),
        )


async def monday_reset(_context: ContextTypes.DEFAULT_TYPE):
    state = load_state()
    state["mopping_done"] = False
    state["toilet_done"] = False
    save_state(state)


async def startup_check(context: ContextTypes.DEFAULT_TYPE):
    today = date.today()
    w = today.weekday()
    if w not in (5, 6):
        return

    this_sat = today if w == 5 else today - timedelta(days=1)
    if not _is_toilet_weekend(this_sat):
        return

    state = load_state()
    if state.get("last_reminder_sat") == this_sat.isoformat():
        return  # reminder already sent for this Saturday

    toilet_person = _toilet_person(this_sat)
    state["last_reminder_sat"] = this_sat.isoformat()
    save_state(state)
    await context.bot.send_message(
        chat_id=CHAT_ID,
        text=f"🚽 Toilet cleaning reminder!\n\n{toilet_person}, it's your turn to scrub the toilet!",
        reply_markup=_done_keyboard("toilet"),
    )


# ── Shared status builder ─────────────────────────────────────────────────────

def _build_status(state: dict) -> tuple:
    this_sat = _this_saturday()
    mop_person = _mopping_person(this_sat)
    toilet_this_wknd = _is_toilet_weekend(this_sat)

    mop_status = "✅ Done" if state["mopping_done"] else "❌ Not done yet"

    if toilet_this_wknd:
        toilet_status = "✅ Done" if state["toilet_done"] else "❌ Not done yet"
        toilet_person = _toilet_person(this_sat)
        toilet_date = this_sat
    else:
        toilet_status = "⏳ Not this weekend"
        # find next toilet Saturday
        next_toilet = this_sat + timedelta(weeks=1)
        while not _is_toilet_weekend(next_toilet):
            next_toilet += timedelta(weeks=1)
        toilet_person = _toilet_person(next_toilet)
        toilet_date = next_toilet

    lines = [
        "📋 Chore Status\n",
        f"🧹 Mopping: {mop_status}",
        f"   (Sat {_fmt(this_sat)}, {mop_person}'s turn)",
        "",
        f"🚽 Toilet: {toilet_status}",
        f"   (Sat {_fmt(toilet_date)}, {toilet_person}'s turn)",
        "",
        "📅 Upcoming schedule",
    ]

    for i in range(1, 6):
        sat = this_sat + timedelta(weeks=i)
        sun = sat + timedelta(days=1)
        m = _mopping_person(sat)
        if _is_toilet_weekend(sat):
            chore_str = f"🧹 Mop ({m}) + 🚽 Toilet ({_toilet_person(sat)})"
        else:
            chore_str = f"🧹 Mop ({m})"
        lines.append(f"• {_fmt(sat)}-{_fmt(sun)}: {chore_str}")

    row1, row2 = [], []
    if not state["mopping_done"]:
        row1.append(InlineKeyboardButton("✅ Mopping done!", callback_data="update_done_mopping"))
    else:
        row1.append(InlineKeyboardButton("↩️ Undo mopping", callback_data="undo_mopping"))
    if toilet_this_wknd:
        if not state["toilet_done"]:
            row2.append(InlineKeyboardButton("✅ Toilet done!", callback_data="update_done_toilet"))
        else:
            row2.append(InlineKeyboardButton("↩️ Undo toilet", callback_data="undo_toilet"))

    keyboard = [row1] + ([row2] if row2 else [])
    return "\n".join(lines), InlineKeyboardMarkup(keyboard)


# ── Callback: Done / Undo buttons ─────────────────────────────────────────────

async def done_callback(update: Update, _context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    state = load_state()
    name = update.effective_user.first_name
    data = query.data

    if data in ("done_mopping", "update_done_mopping"):
        if state["mopping_done"]:
            await query.answer("Already marked done!")
            return
        state["mopping_done"] = True
        save_state(state)
        await query.answer("Mopping marked done!")

    elif data in ("done_toilet", "update_done_toilet"):
        if state["toilet_done"]:
            await query.answer("Already marked done!")
            return
        state["toilet_done"] = True
        save_state(state)
        await query.answer("Toilet marked done!")

    elif data == "undo_mopping":
        state["mopping_done"] = False
        save_state(state)
        await query.answer("Mopping unmarked!")

    elif data == "undo_toilet":
        state["toilet_done"] = False
        save_state(state)
        await query.answer("Toilet unmarked!")

    else:
        return

    if data.startswith("update_") or data.startswith("undo_"):
        text, markup = _build_status(state)
        await query.edit_message_text(text, reply_markup=markup)
    else:
        await query.edit_message_text(f"✅ Thanks {name}! 🎉")


# ── Commands ──────────────────────────────────────────────────────────────────

async def start(update: Update, _context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        f"👋 Chore bot is running!\n"
        f"Chat ID: {update.effective_chat.id}\n\n"
        f"Use /status to see chore status and upcoming schedule.",
    )


async def status(update: Update, _context: ContextTypes.DEFAULT_TYPE):
    state = load_state()
    text, markup = _build_status(state)
    await update.message.reply_text(text, reply_markup=markup)


async def update_chores(update: Update, _context: ContextTypes.DEFAULT_TYPE):
    state = load_state()
    text, markup = _build_status(state)
    await update.message.reply_text(text, reply_markup=markup)


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("status", status))
    app.add_handler(CommandHandler("update", update_chores))
    app.add_handler(CallbackQueryHandler(done_callback))

    jq = app.job_queue
    jq.run_daily(saturday_job, time=time(9, 0, tzinfo=TZ), days=(5,))
    jq.run_daily(sunday_job,   time=time(9, 0, tzinfo=TZ), days=(6,))
    jq.run_daily(monday_reset, time=time(7, 0, tzinfo=TZ), days=(0,))
    jq.run_once(startup_check, when=5)

    print("Chore bot running. Press Ctrl+C to stop.")
    app.run_polling()


if __name__ == "__main__":
    main()
