import os
import logging
from flask import Flask
from threading import Thread
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, filters

# Setup Flask for Keep-Alive
app = Flask(__name__)
@app.route('/')
def home():
    return "Bot is alive!"

def run():
    app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 8080)))

# Bot Logic
TOKEN = os.environ.get('TELEGRAM_BOT_TOKEN') # Set this in Render Environment Variables
ADMIN_ID = 1875307475

async def start(update, context):
    await update.message.reply_text("You are subscribed!")

if __name__ == '__main__':
    # Start Keep-Alive Server
    Thread(target=run).start()
    
    # Start Bot
    application = ApplicationBuilder().token(TOKEN).build()
    application.add_handler(CommandHandler("start", start))
    application.run_polling()
    
