import os
from flask import Flask
from threading import Thread
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes
from telegram.constants import ChatMemberStatus

# --- Keep-Alive Flask Server ---
app = Flask(__name__)
@app.route('/')
def home():
    return "Bot is alive!"

def run_server():
    app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 8080)))

# --- Configuration ---
TOKEN = os.environ.get('TELEGRAM_BOT_TOKEN')
ADMIN_ID = 1875307475
CHANNEL_USERNAME = '@your_channel_handle' # Ensure this matches your channel's handle
CHANNEL_LINK = 'https://t.me/+ckfO94UHyhllODg0'

users = set()

# --- Bot Logic ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_chat.id
    
    # 1. Membership Check
    try:
        member = await context.bot.get_chat_member(chat_id=CHANNEL_USERNAME, user_id=user_id)
        if member.status not in [ChatMemberStatus.MEMBER, ChatMemberStatus.ADMINISTRATOR, ChatMemberStatus.OWNER]:
            await update.message.reply_text(f"Please join our channel first to use this bot:\n{CHANNEL_LINK}")
            return
    except Exception as e:
        print(f"Error checking membership: {e}")

    # 2. Add to list
    users.add(user_id)
    
    # 3. Split Button Layout
    keyboard = [
        [
            InlineKeyboardButton("Request specific accounts", url="https://t.me/roguenomad_bot"),
            InlineKeyboardButton("Join Channel", url=CHANNEL_LINK)
        ]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    message = (
        "Welcome to Rogue Drops\n\n"
        "Everybody will be sent the accounts simultaneously.\n\n"
        "I'll drop accounts at random times, stay tuned!"
    )
    await update.message.reply_text(message, reply_markup=reply_markup)

async def broadcast(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return

    msg_to_copy = update.message.reply_to_message
    if not msg_to_copy:
        await update.message.reply_text("Reply to a message/file/link with /broadcast to send it.")
        return

    for chat_id in users:
        try:
            await context.bot.copy_message(chat_id=chat_id, from_chat_id=update.effective_chat.id, message_id=msg_to_copy.message_id)
        except Exception:
            continue

if __name__ == '__main__':
    Thread(target=run_server).start()
    application = ApplicationBuilder().token(TOKEN).build()
    
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("broadcast", broadcast))
    
    application.run_polling()
    
