# Add this import
from telegram.constants import ChatMemberStatus

# Your Channel Handle
CHANNEL_USERNAME = '@yourchannelhandle' 

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_chat.id
    
    # Check if user is in channel
    try:
        member = await context.bot.get_chat_member(chat_id=CHANNEL_USERNAME, user_id=user_id)
        if member.status in [ChatMemberStatus.MEMBER, ChatMemberStatus.ADMINISTRATOR, ChatMemberStatus.OWNER]:
            save_user(user_id)
            await update.message.reply_text("Welcome! You are subscribed.")
        else:
            await update.message.reply_text(f"Please join {CHANNEL_USERNAME} first to use this bot.")
    except Exception as e:
        await update.message.reply_text("Error checking membership. Make sure the bot is an admin in the channel.")
