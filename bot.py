import logging

from telegram import Update
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler, ContextTypes,
)

from config import BOT_TOKEN
from database import init_db
from game_engine import (
    create_game, join_game, leave_game, start_game, get_status,
    handle_private_action, publish_fact, advance_if_ready, recover_games,
)

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("last_night")


async def zombie(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat
    user = update.effective_user
    if chat.type == "private":
        await update.message.reply_text("🧟 Խաղը պետք է ստեղծել Telegram խմբում։")
        return
    text, keyboard = await create_game(chat.id, user)
    await update.message.reply_text(text, reply_markup=keyboard)


async def join(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.type == "private":
        await update.message.reply_text("Միանալը կատարվում է խաղի խմբում։")
        return
    text, keyboard = await join_game(update.effective_chat.id, update.effective_user)
    await update.message.reply_text(text, reply_markup=keyboard)


async def leave(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.type == "private":
        await update.message.reply_text("Lobby-ից դուրս գալը կատարվում է խաղի խմբում։")
        return
    text, keyboard = await leave_game(update.effective_chat.id, update.effective_user.id)
    await update.message.reply_text(text, reply_markup=keyboard)


async def startgame(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.type == "private":
        await update.message.reply_text("Խաղը պետք է սկսել խմբում։")
        return
    text, keyboard = await start_game(context.bot, update.effective_chat.id, update.effective_user)
    await update.message.reply_text(text, reply_markup=keyboard)


async def status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = await get_status(update.effective_chat.id)
    await update.message.reply_text(text)


async def publish(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.type == "private":
        await update.message.reply_text("/publish-ը օգտագործվում է խաղի խմբում, քննարկման փուլում։")
        return
    text = await publish_fact(update.effective_chat.id, update.effective_user.id)
    await update.message.reply_text(text)


async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🧟 ՎԵՐՋԻՆ ԳԻՇԵՐԸ\n\n"
        "/zombie — ստեղծել lobby\n/join — միանալ\n/startgame — սկսել\n"
        "/status — վիճակ\n/role — role\n/goal — նպատակ\n/inventory — evidence\n"
        "/publish — լրագրողի հրապարակում (խմբում, քննարկման փուլում)\n"
        "/leave — լքել\n/help — կանոններ\n\n"
        "Գաղտնի գործողությունները կատարվում են bot-ի private chat-ում. "
        "նախապես գրիր bot-ին /start, որպեսզի կարողանա քեզ գաղտնի հաղորդագրություններ ուղարկել։"
    )


async def private_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.type != "private":
        return
    cmd = update.message.text.split()[0].lower()
    text = await handle_private_action(update.effective_user.id, cmd, update.message)
    await update.message.reply_text(text)


async def lobby_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    action = q.data.split(":", 1)[1]
    chat_id = q.message.chat_id
    user = q.from_user
    if action == "join":
        text, keyboard = await join_game(chat_id, user)
    elif action == "leave":
        text, keyboard = await leave_game(chat_id, user.id)
    elif action == "start":
        text, keyboard = await start_game(context.bot, chat_id, user)
    else:
        return
    try:
        await q.edit_message_text(text, reply_markup=keyboard)
    except Exception:
        await q.message.reply_text(text, reply_markup=keyboard)


async def game_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    result = await handle_private_action(q.from_user.id, q.data, q.message)
    if result:
        try:
            await q.edit_message_text(result)
        except Exception:
            await q.message.reply_text(result)


async def scheduler(context: ContextTypes.DEFAULT_TYPE):
    await advance_if_ready(context.bot)


async def post_init(application: Application) -> None:
    """Runs once, after the bot initializes its connection, before polling
    starts. Async startup work belongs here — trying to do it via
    asyncio.run() wrapped around an awaited application.run_polling()
    instead crashes with 'event loop is already running', since
    run_polling() manages its own event loop and must be called as a plain
    (non-awaited) function, not from inside asyncio.run()."""
    await init_db()
    await recover_games(application.bot)


def main():
    app = Application.builder().token(BOT_TOKEN).post_init(post_init).build()

    app.add_handler(CommandHandler("zombie", zombie))
    app.add_handler(CommandHandler("join", join))
    app.add_handler(CommandHandler("leave", leave))
    app.add_handler(CommandHandler("startgame", startgame))
    app.add_handler(CommandHandler("status", status))
    app.add_handler(CommandHandler("publish", publish))
    app.add_handler(CommandHandler("help", help_cmd))
    app.add_handler(CommandHandler("role", private_text))
    app.add_handler(CommandHandler("goal", private_text))
    app.add_handler(CommandHandler("inventory", private_text))
    app.add_handler(CallbackQueryHandler(lobby_callback, pattern=r"^lobby:"))
    app.add_handler(CallbackQueryHandler(game_callback))

    app.job_queue.run_repeating(scheduler, interval=5, first=5)
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
