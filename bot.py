import json
import os
from datetime import date, timedelta, time
from zoneinfo import ZoneInfo

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import Application, CallbackQueryHandler, CommandHandler, ContextTypes

BOT_TOKEN = "8756258315:AAH0caCOy4MQkG-jUMdPMmeyoJv9k0GeQjY"
CHAT_ID = 607826841
TZ = ZoneInfo("Asia/Singapore")

STATE_FILE = os.path.join(os.path.dirname(__file__), "state.json")

# Index 0 = Alexis, index 1 = FC
NAMES = ["Alexis", "FC"]


def _next_saturday() -> date:
    today = date.today()
    days = (5 - today.weekday()) % 7 or 7
    return today + timedelta(days=days)


def _fmt(d: date) -> str:
    return f"{d.day} {d.strftime('%b')}"


def load_state() -> dict:
    defaults = {
        "next_toilet_saturday": _next_saturday().isoformat(),
        "mopping_done": False,
        "toilet_done": False,
        "toilet_this_weekend": False,
        "mopping_turn": 0,   # Alexis mops first
        "toilet_turn": 1,    # FC cleans toilet first
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
    state = load_state()
    state["mopping_done"] = False
    save_state(state)

    mop_person = NAMES[state["mopping_turn"]]
    await context.bot.send_message(
        chat_id=CHAT_ID,
        text=f"🧹 Weekend chore reminder!\n\n{mop_person}, it's your turn to mop the floor!",
        reply_markup=_done_keyboard("mopping"),
    )

    today = date.today()
    if today >= date.fromisoformat(state["next_toilet_saturday"]):
        toilet_person = NAMES[state["toilet_turn"]]
        state["toilet_done"] = False
        state["toilet_this_weekend"] = True
        save_state(state)
        await context.bot.send_message(
            chat_id=CHAT_ID,
            text=f"🚽 Toilet cleaning week too!\n\n{toilet_person}, it's your turn to scrub the toilet!",
            reply_markup=_done_keyboard("toilet"),
        )


async def sunday_job(context: ContextTypes.DEFAULT_TYPE):
    state = load_state()

    if not state["mopping_done"]:
        mop_person = NAMES[state["mopping_turn"]]
        await context.bot.send_message(
            chat_id=CHAT_ID,
            text=f"⚠️ Mopping still not done! {mop_person}, last chance today 🧹",
            reply_markup=_done_keyboard("mopping"),
        )

    if state["toilet_this_weekend"] and not state["toilet_done"]:
        toilet_person = NAMES[state["toilet_turn"]]
        await context.bot.send_message(
            chat_id=CHAT_ID,
            text=f"⚠️ Toilet still not done! {toilet_person}, last chance today 🚽",
            reply_markup=_done_keyboard("toilet"),
        )


async def monday_reset(context: ContextTypes.DEFAULT_TYPE):
    state = load_state()
    if state["toilet_this_weekend"]:
        last_sat = date.fromisoformat(state["next_toilet_saturday"])
        state["next_toilet_saturday"] = (last_sat + timedelta(weeks=2)).isoformat()
        state["toilet_turn"] = 1 - state["toilet_turn"]
    state["mopping_turn"] = 1 - state["mopping_turn"]
    state["mopping_done"] = False
    state["toilet_done"] = False
    state["toilet_this_weekend"] = False
    save_state(state)


# ── Shared status builder ─────────────────────────────────────────────────────

def _build_status(state: dict) -> tuple:
    today = date.today()
    w = today.weekday()

    if w == 6:
        this_sat = today - timedelta(days=1)
    elif w == 5:
        this_sat = today
    else:
        this_sat = today + timedelta(days=5 - w)

    next_toilet_sat = date.fromisoformat(state["next_toilet_saturday"])
    mop_person = NAMES[state["mopping_turn"]]
    toilet_person = NAMES[state["toilet_turn"]]
    is_weekend = w >= 5

    mop_status = "✅ Done" if state["mopping_done"] else "❌ Not done yet"

    toilet_this_wknd = state["toilet_this_weekend"] if is_weekend else (this_sat == next_toilet_sat)
    if toilet_this_wknd:
        toilet_status = "✅ Done" if state["toilet_done"] else "❌ Not done yet"
        toilet_date = this_sat
    else:
        toilet_status = "⏳ Not this weekend"
        toilet_date = next_toilet_sat

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

    sched_start = this_sat + timedelta(weeks=1)
    mop_idx = 1 - state["mopping_turn"]

    if toilet_this_wknd:
        t_next_sat = next_toilet_sat + timedelta(weeks=2)
        t_idx = 1 - state["toilet_turn"]
    else:
        t_next_sat = next_toilet_sat
        t_idx = state["toilet_turn"]

    for i in range(5):
        sat = sched_start + timedelta(weeks=i)
        sun = sat + timedelta(days=1)
        m = NAMES[(mop_idx + i) % 2]
        if sat >= t_next_sat and (sat - t_next_sat).days % 14 == 0:
            chore_str = f"🧹 Mop ({m}) + 🚽 Toilet ({NAMES[t_idx]})"
            t_idx = 1 - t_idx
        else:
            chore_str = f"🧹 Mop ({m})"
        lines.append(f"• {_fmt(sat)}-{_fmt(sun)}: {chore_str}")

    row1 = []
    row2 = []
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


# ── Callback: Done button ─────────────────────────────────────────────────────

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

    print("Chore bot running. Press Ctrl+C to stop.")
    app.run_polling()


if __name__ == "__main__":
    main()