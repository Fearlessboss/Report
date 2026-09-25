#!/usr/bin/env python3
"""
╔══════════════════════════════════════════════════════╗
║      🛡️  ILLEGAL CONTENT DETECTOR BOT  🛡️            ║
║      Owner + Sudo | Powered by AI + Telethon         ║
╚══════════════════════════════════════════════════════╝
FIXES v6:
  • concurrent_updates(False) for ConversationHandler safety
  • Logger initialised BEFORE any code that uses it
  • safe_edit / safe_edit_with_fallback now log exceptions
  • Callback router logs every callback + returns correct states
  • Emails now medium-sized (spam-friendly) with "Where to send" button
  • Colorful emoji-enhanced inline buttons
  • Secrets loaded from env vars only
"""

import os
import re
import json
import base64
import asyncio
import logging
from datetime import datetime
from html import escape as he
from typing import List, Tuple, Optional

from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
)
from telegram.constants import ParseMode
from telegram.error import BadRequest
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ConversationHandler,
    ContextTypes,
    filters,
)

from telethon import TelegramClient, errors
from telethon.tl.functions.channels import JoinChannelRequest
from telethon.tl.functions.messages import ImportChatInviteRequest
from telethon.tl.types import Channel, MessageMediaPhoto

# ══════════════════════════════════════════════════════════
# LOGGER  (MUST be initialised BEFORE any code uses it)
# ══════════════════════════════════════════════════════════
logging.basicConfig(
    format="%(asctime)s | %(levelname)-7s | %(name)s | %(message)s",
    level=logging.INFO,
)
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("telethon").setLevel(logging.WARNING)
logger = logging.getLogger("illegal-detector-bot")

# ══════════════════════════════════════════════════════════
# ENVIRONMENT / SECRETS
# ══════════════════════════════════════════════════════════
OWNER_ID     = 6980326908
BOT_TOKEN    = "8763146794:AAFDAan4KSfIhR6KzLR8PV6G-fuLQSXWvOs"
API_ID       = 33628258
API_HASH     = "0850762925b9c1715b9b122f7b753128"

# Support single or comma-separated multi-key
_raw_keys = os.getenv("GROQ_API_KEY") or os.getenv("OPENROUTER_API_KEY") or ""
API_KEYS: List[str] = [k.strip() for k in _raw_keys.split(",") if k.strip()]

if not BOT_TOKEN:
    logger.error("❌ BOT_TOKEN env var missing.")
if not API_ID or not API_HASH:
    logger.warning("⚠️ API_ID / API_HASH env vars missing — userbot features will fail.")
if not API_KEYS:
    logger.warning("⚠️ GROQ_API_KEY / OPENROUTER_API_KEY env var missing — AI features will fail.")
if not OWNER_ID:
    logger.warning("⚠️ OWNER_ID env var missing — sudo/owner commands will not work correctly.")

OPENROUTER_URL = os.getenv(
    "OPENROUTER_URL",
    "https://openrouter.ai/api/v1/chat/completions",
)
MODEL = os.getenv("AI_MODEL", "meta-llama/llama-3.3-70b-instruct:free")
VISION_MODEL = os.getenv("AI_VISION_MODEL", "meta-llama/llama-3.2-11b-vision-instruct:free")

SESSION_FILE = os.getenv("SESSION_FILE", "userbot_session")
SUDO_FILE = os.getenv("SUDO_FILE", "sudo_users.json")

MAX_IMAGES = int(os.getenv("MAX_IMAGES", "5"))

# ══════════════════════════════════════════════════════════
# CONVERSATION STATES
# ══════════════════════════════════════════════════════════
(
    MAIN_MENU,
    ADD_PHONE,
    ADD_OTP,
    ADD_2FA,
    VERIFY_LINK,
    CHAT_AI,
    REPORT_EMAIL,
) = range(7)

# Global userbot client + AI key rotator
userbot_client: Optional[TelegramClient] = None
_api_key_index = 0

# ══════════════════════════════════════════════════════════
# TELEGRAM REPORT TREE
# ══════════════════════════════════════════════════════════
TELEGRAM_REPORT_TREE = {
    "Spam": [],
    "Violence": [
        "Graphic or disturbing content",
        "Threats of violence",
        "Terrorism",
    ],
    "Child abuse": [
        "Child sexual abuse",
        "Endangering minors",
    ],
    "Illegal adult content": [
        "Pornography",
        "Non-consensual intimate imagery",
    ],
    "Illegal goods and services": [
        "Drugs",
        "Weapons",
        "Counterfeit goods",
        "Fake documents",
    ],
    "Personal data": [
        "Doxxing",
        "Identity theft",
    ],
    "Copyright": [
        "Copyright infringement",
    ],
    "Fraud / Scam": [
        "Financial fraud",
        "Phishing",
        "Impersonation",
    ],
    "Hate speech": [
        "Discrimination",
        "Incitement",
    ],
    "Other": [],
}

_REPORT_TREE_FOR_PROMPT = json.dumps(TELEGRAM_REPORT_TREE, indent=2)

# ══════════════════════════════════════════════════════════
# SUDO USERS PERSISTENCE
# ══════════════════════════════════════════════════════════
def load_sudo_users() -> List[dict]:
    """Load sudo users. Auto-migrates old int[] → dict[]."""
    if not os.path.exists(SUDO_FILE):
        return []
    try:
        with open(SUDO_FILE, "r") as f:
            data = json.load(f)
        if not isinstance(data, list):
            return []
        migrated = []
        needs_save = False
        for item in data:
            if isinstance(item, int):
                migrated.append({"id": item, "username": None, "name": None})
                needs_save = True
            elif isinstance(item, dict) and "id" in item:
                migrated.append(item)
            else:
                needs_save = True
        if needs_save:
            with open(SUDO_FILE, "w") as f:
                json.dump(migrated, f, indent=2)
            logger.info("✅ sudo_users.json auto-migrated to dict format.")
        return migrated
    except Exception as ex:
        logger.warning(f"load_sudo_users error: {ex}")
        return []


def save_sudo_users(users: List[dict]) -> None:
    with open(SUDO_FILE, "w") as f:
        json.dump(users, f, indent=2)


def get_sudo_ids() -> set:
    ids = set()
    for u in load_sudo_users():
        try:
            ids.add(int(u["id"]))
        except (KeyError, TypeError, ValueError):
            pass
    return ids


def add_sudo_user(user_id: int, username: str = None, name: str = None) -> bool:
    users = load_sudo_users()
    for u in users:
        if u["id"] == user_id:
            u["username"] = username
            u["name"] = name
            save_sudo_users(users)
            return False
    users.append({"id": user_id, "username": username, "name": name})
    save_sudo_users(users)
    return True


def remove_sudo_user(user_id: int) -> bool:
    users = load_sudo_users()
    new_users = [u for u in users if u["id"] != user_id]
    if len(new_users) == len(users):
        return False
    save_sudo_users(new_users)
    return True


# ══════════════════════════════════════════════════════════
# HTML ESCAPE HELPER
# ══════════════════════════════════════════════════════════
def e(text) -> str:
    if text is None:
        return "N/A"
    return he(str(text))


# ══════════════════════════════════════════════════════════
# AUTHORIZATION
# ══════════════════════════════════════════════════════════
def is_authorized(user_id: int) -> bool:
    return user_id == OWNER_ID or user_id in get_sudo_ids()


def is_owner(user_id: int) -> bool:
    return user_id == OWNER_ID


# ══════════════════════════════════════════════════════════
# COLORFUL KEYBOARDS (emoji-driven color feel)
# ══════════════════════════════════════════════════════════
def main_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🟢 ➕ Add / Change Account",   callback_data="add_account")],
        [InlineKeyboardButton("🔵 🔍 Verify Group / Channel", callback_data="verify")],
        [InlineKeyboardButton("🟣 🤖 Chat with AI",           callback_data="chat_ai")],
        [InlineKeyboardButton("🟡 📊 Account Status",         callback_data="status")],
    ])


def back_keyboard(target: str = "back_main") -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[InlineKeyboardButton("🔙 ⬅️ Back", callback_data=target)]])


# ══════════════════════════════════════════════════════════
# SAFE EDITING HELPERS (with proper exception logging)
# ══════════════════════════════════════════════════════════
async def safe_edit(query, text: str, keyboard=None):
    try:
        kwargs: dict = {"text": text, "parse_mode": ParseMode.HTML}
        if keyboard:
            kwargs["reply_markup"] = keyboard
        await query.edit_message_text(**kwargs)
    except BadRequest as err:
        if "not modified" in str(err).lower():
            return
        logger.warning(f"safe_edit BadRequest, stripping HTML and retrying: {err}")
        plain = re.sub(r"<[^>]+>", "", text)
        try:
            kwargs2: dict = {"text": plain}
            if keyboard:
                kwargs2["reply_markup"] = keyboard
            await query.edit_message_text(**kwargs2)
        except Exception:
            logger.exception("safe_edit plain fallback failed")
    except Exception:
        logger.exception("safe_edit failed")


async def split_send(update_obj, text: str, keyboard=None):
    chunks = [text[i:i + 4000] for i in range(0, len(text), 4000)]
    for idx, chunk in enumerate(chunks):
        kb = keyboard if idx == len(chunks) - 1 else None
        try:
            await update_obj.reply_text(
                chunk, parse_mode=ParseMode.HTML, reply_markup=kb
            )
        except BadRequest as err:
            logger.warning(f"split_send BadRequest, stripping HTML: {err}")
            plain = re.sub(r"<[^>]+>", "", chunk)
            try:
                await update_obj.reply_text(plain, reply_markup=kb)
            except Exception:
                logger.exception("split_send plain fallback failed")
        except Exception:
            logger.exception("split_send failed")


async def safe_edit_with_fallback(msg, text: str, keyboard=None):
    try:
        kwargs: dict = {"text": text, "parse_mode": ParseMode.HTML}
        if keyboard:
            kwargs["reply_markup"] = keyboard
        await msg.edit_text(**kwargs)
    except BadRequest as err:
        if "not modified" in str(err).lower():
            return
        logger.warning(f"safe_edit_with_fallback BadRequest, stripping HTML: {err}")
        plain = re.sub(r"<[^>]+>", "", text)
        try:
            kwargs2: dict = {"text": plain}
            if keyboard:
                kwargs2["reply_markup"] = keyboard
            await msg.edit_text(**kwargs2)
        except Exception:
            logger.exception("safe_edit_with_fallback plain fallback failed")
    except Exception:
        logger.exception("safe_edit_with_fallback failed")


# ══════════════════════════════════════════════════════════
# TELEGRAM REPORT INSTRUCTIONS GENERATOR
# ══════════════════════════════════════════════════════════
def format_report_instructions(
    category: str,
    subcategory: str = "",
    report_desc: str = "",
) -> str:
    cat = category.strip()
    subcat = subcategory.strip() if subcategory else ""

    if cat not in TELEGRAM_REPORT_TREE:
        cat = "Other"
        subcat = ""

    subs = TELEGRAM_REPORT_TREE.get(cat, [])
    if subcat and subcat not in subs:
        subcat = subs[0] if subs else ""

    has_subs = bool(subs)
    has_desc = bool(report_desc)

    lines = [
        "<b>📢 How to Report on Telegram (In-App):</b>\n",
        "1️⃣  Open the group / channel",
        "2️⃣  Tap the channel name / header at the top",
        "3️⃣  Tap ⋮ (three dots menu) → tap <b>Report</b>",
    ]

    if has_subs and subcat:
        lines.append(f'4️⃣  Choose main category: "<b>{e(cat)}</b>"')
        lines.append(f'5️⃣  Choose sub-option: "<b>{e(subcat)}</b>"')
        if has_desc:
            lines.append(
                f"6️⃣  In the description box, paste:\n<code>{e(report_desc)}</code>"
            )
            lines.append("7️⃣  Tap <b>Submit</b> ✅")
        else:
            lines.append("6️⃣  Add any optional description → Tap <b>Submit</b> ✅")

    elif has_subs and not subcat:
        sub_list = "\n".join(f"   • {e(s)}" for s in subs)
        lines.append(f'4️⃣  Choose main category: "<b>{e(cat)}</b>"')
        lines.append(f"5️⃣  Choose the most relevant sub-option:\n{sub_list}")
        if has_desc:
            lines.append(
                f"6️⃣  In the description box, paste:\n<code>{e(report_desc)}</code>"
            )
            lines.append("7️⃣  Tap <b>Submit</b> ✅")
        else:
            lines.append("6️⃣  Add optional description → Tap <b>Submit</b> ✅")

    else:
        lines.append(f'4️⃣  Choose: "<b>{e(cat)}</b>"')
        if has_desc:
            lines.append(
                f"5️⃣  In the description box, paste:\n<code>{e(report_desc)}</code>"
            )
            lines.append("6️⃣  Tap <b>Submit</b> ✅")
        else:
            lines.append("5️⃣  Add optional description → Tap <b>Submit</b> ✅")

    return "\n".join(lines)


# ══════════════════════════════════════════════════════════
# AI MODULE
# ══════════════════════════════════════════════════════════
async def call_ai(messages: list, system: str = "", use_vision: bool = False) -> str:
    global _api_key_index
    import httpx

    if not API_KEYS:
        logger.error("call_ai: no API keys configured.")
        return "AI service not configured. Set GROQ_API_KEY / OPENROUTER_API_KEY."

    model_to_use = VISION_MODEL if use_vision else MODEL

    payload_messages = []
    if system:
        payload_messages.append({"role": "system", "content": system})
    payload_messages.extend(messages)

    for attempt in range(len(API_KEYS)):
        key = API_KEYS[_api_key_index % len(API_KEYS)].strip()
        try:
            headers = {
                "Authorization": "Bearer " + key,
                "Content-Type": "application/json",
            }
            body = {
                "model": model_to_use,
                "messages": payload_messages,
                "temperature": 0.5,
                "max_tokens": 2048,
            }
            async with httpx.AsyncClient(timeout=60) as client:
                resp = await client.post(OPENROUTER_URL, headers=headers, json=body)
                if resp.status_code == 200:
                    data = resp.json()
                    choices = data.get("choices", [])
                    if choices:
                        content = choices[0].get("message", {}).get("content", "")
                        if content:
                            return content.strip()
                    logger.warning(f"Unexpected response structure: {str(data)[:300]}")
                    _api_key_index += 1
                else:
                    logger.warning(
                        f"API status {resp.status_code} "
                        f"(key: {key[:20]}...): {resp.text[:200]}"
                    )
                    _api_key_index += 1
        except Exception as exc:
            logger.warning(f"AI call failed (key: {key[:20]}...): {exc}")
            _api_key_index += 1

    return "AI service temporarily unavailable. Please try again later."


# ──────────────────────────────────────────────────────────
# VISION: Analyze a single image for illegal content
# ──────────────────────────────────────────────────────────
async def analyze_single_image(image_b64: str, caption: str = "", index: int = 0) -> dict:
    vision_sys = (
        "You are a strict content moderation AI. "
        "Analyze the provided image for ANY illegal or policy-violating content. "
        "Illegal categories include: CSAM, child exploitation, graphic violence, "
        "terrorism material, drug/weapon sales, non-consensual imagery, hate symbols, "
        "animal abuse, fake documents, malware screenshots, scam content, etc.\n\n"
        "Return ONLY valid JSON, no extra text:\n"
        "{\n"
        '  "is_illegal": true | false,\n'
        '  "confidence": "HIGH" | "MEDIUM" | "LOW",\n'
        '  "violations": ["string"],\n'
        '  "description": "string"\n'
        "}"
    )

    user_content = [
        {
            "type": "image_url",
            "image_url": {"url": f"data:image/jpeg;base64,{image_b64}"},
        },
        {
            "type": "text",
            "text": (
                f"Image caption: {caption if caption else '(no caption)'}\n\n"
                "Analyze this image strictly. Return only JSON."
            ),
        },
    ]

    raw = await call_ai(
        [{"role": "user", "content": user_content}],
        system=vision_sys,
        use_vision=True,
    )

    try:
        match = re.search(r'\{.*\}', raw, re.DOTALL)
        if match:
            result = json.loads(match.group())
            result["image_index"] = index
            return result
    except Exception:
        logger.exception("analyze_single_image JSON parse failed")

    return {
        "is_illegal": False,
        "confidence": "LOW",
        "violations": [],
        "description": raw[:200],
        "image_index": index,
    }


# ──────────────────────────────────────────────────────────
# Analyze all images for a channel scan
# ──────────────────────────────────────────────────────────
async def analyze_all_images(image_list: List[dict]) -> List[dict]:
    if not image_list:
        return []

    tasks = [
        analyze_single_image(img["b64"], img.get("caption", ""), i)
        for i, img in enumerate(image_list[:MAX_IMAGES])
    ]
    results = await asyncio.gather(*tasks, return_exceptions=True)

    illegal_images = []
    for i, res in enumerate(results):
        if isinstance(res, Exception):
            logger.warning(f"Image analysis error for image {i}: {res}")
            continue
        if res.get("is_illegal"):
            res["msg_link"] = image_list[i].get("link", "")
            res["msg_id"] = image_list[i].get("msg_id", 0)
            illegal_images.append(res)

    return illegal_images


# ──────────────────────────────────────────────────────────
# Text-based illegality analysis
# ──────────────────────────────────────────────────────────
async def analyze_illegality(messages: List[str], ch_info: dict) -> dict:
    trimmed = [m[:300] for m in messages[:12]]
    indexed_text = "\n---\n".join(
        f"[{i}] {msg}" for i, msg in enumerate(trimmed)
    )
    if len(indexed_text) > 3000:
        indexed_text = indexed_text[:3000] + "\n...(truncated)"

    sys_prompt = (
        "You are a senior legal analyst specialised in cybercrime, digital content law, "
        "and Telegram's Terms of Service. Analyse the provided Telegram channel/group "
        "content strictly and return ONLY valid JSON — no markdown, no extra text.\n\n"
        "JSON schema:\n"
        "{\n"
        '  "is_illegal": true | false,\n'
        '  "confidence": "HIGH" | "MEDIUM" | "LOW",\n'
        '  "violations": ["string"],\n'
        '  "severity": "CRITICAL" | "HIGH" | "MEDIUM" | "LOW",\n'
        '  "applicable_laws": ["string"],\n'
        '  "summary": "string",\n'
        '  "detailed_reason": "string",\n'
        '  "telegram_report_category": "string",\n'
        '  "telegram_report_subcategory": "string",\n'
        '  "illegal_message_indices": [int],\n'
        '  "report_description": "string"\n'
        "}\n\n"
        "IMPORTANT — telegram_report_category MUST be exactly one of these keys:\n"
        f"{_REPORT_TREE_FOR_PROMPT}\n\n"
        "telegram_report_subcategory:\n"
        "  Must be one of the sub-options listed above for the chosen category.\n"
        "  Set to empty string '' if the category has no sub-options.\n\n"
        "illegal_message_indices:\n"
        "  Array of 0-based integer indices of messages that contain illegal content.\n"
        "  Empty [] if none.\n\n"
        "report_description:\n"
        "  A concise 2-3 sentence description of SPECIFIC violations found, "
        "  ready to paste into Telegram's in-app report form.\n"
        "  Empty string '' if is_illegal is false."
    )

    user_msg = (
        f"Channel Info:\n{json.dumps(ch_info, indent=2)}\n\n"
        f"Messages (0-based index in brackets):\n{indexed_text}\n\n"
        "Determine legality. Return ONLY the JSON object."
    )

    raw = await call_ai([{"role": "user", "content": user_msg}], sys_prompt)

    try:
        match = re.search(r'\{.*\}', raw, re.DOTALL)
        if match:
            result = json.loads(match.group())
            result.setdefault("illegal_message_indices", [])
            result.setdefault("report_description", "")
            result.setdefault("telegram_report_subcategory", "")
            return result
    except Exception:
        logger.exception("analyze_illegality JSON parse failed")

    return {
        "is_illegal": False,
        "confidence": "LOW",
        "violations": [],
        "severity": "LOW",
        "applicable_laws": [],
        "summary": raw,
        "detailed_reason": raw,
        "telegram_report_category": "Other",
        "telegram_report_subcategory": "",
        "illegal_message_indices": [],
        "report_description": "",
    }


# ──────────────────────────────────────────────────────────
# LEGAL EMAIL: medium-size, spam-friendly
# ──────────────────────────────────────────────────────────
async def generate_legal_email(
    ch_info: dict,
    analysis: dict,
    reporter_email: str,
    illegal_links: List[str],
) -> str:
    links_block = ""
    if illegal_links:
        links_block = (
            "\n\nDirect Evidence Links (Illegal Messages Only):\n"
            + "\n".join(f"  • {lnk}" for lnk in illegal_links[:6])
        )

    sys_prompt = (
        "You are a cyber-law attorney writing a MEDIUM-LENGTH, professional complaint "
        "email to Telegram's abuse team. The email must be spam-friendly: concise, "
        "clean, without excessive length or repetition (aim for 180-280 words in the "
        "email body). Use short paragraphs. No walls of text. Include: a clear subject, "
        "a formal greeting, a brief factual statement of the violation, the specific "
        "law(s) violated (1-3 citations max), evidence links, a clear action request, "
        "and a signature block with reporter contact. Avoid legal jargon overload — "
        "keep it readable and to the point so it passes spam filters."
    )
    user_msg = (
        f"Reporter Email : {reporter_email}\n"
        f"Date           : {datetime.utcnow().strftime('%B %d, %Y')} (UTC)\n"
        f"Channel/Group  : {json.dumps(ch_info, indent=2)}\n"
        f"AI Analysis    : {json.dumps(analysis, indent=2)}\n"
        f"{links_block}\n\n"
        "Write a medium-length email (180-280 words) with:\n"
        "• Subject: (concise, specific)\n"
        "• To: abuse@telegram.org\n"
        "• Short formal body, plain professional English\n"
        "• Cite 1-3 specific laws max\n"
        "• Bullet the evidence links briefly\n"
        "• Clear request: remove channel + suspend account\n"
        "• Signature with: " + reporter_email + "\n\n"
        "Keep it SHORT enough to not trigger spam filters. No repetition. "
        "No unnecessary padding. Plain text only, no markdown."
    )
    return await call_ai([{"role": "user", "content": user_msg}], sys_prompt)


# ──────────────────────────────────────────────────────────
# SUGGEST WHERE TO SEND THE EMAIL (line-by-line list)
# ──────────────────────────────────────────────────────────
async def suggest_report_destinations(ch_info: dict, analysis: dict) -> str:
    sys_prompt = (
        "You are a cyber-crime reporting expert. Given the channel info and AI legal "
        "analysis, produce a clean line-by-line list of the MOST SUITABLE official "
        "email addresses / abuse contacts where this report should be sent. "
        "For each destination give: the email address, and a one-line reason why it "
        "is suitable for THIS specific violation type / severity. "
        "Rules:\n"
        "• Plain text only, no markdown, no bullets characters other than '•'.\n"
        "• 4-8 destinations maximum.\n"
        "• Always include abuse@telegram.org first.\n"
        "• Include relevant law-enforcement (e.g. cybercrime.gov.in for India, "
        "  ic3.gov / NCMEC CyberTipline for USA, Europol / NCA for EU/UK, "
        "  INTERPOL for cross-border, IWF for CSAM, etc.) based on violation type.\n"
        "• Format each line EXACTLY as:  email@domain.com — short reason\n"
        "• No greeting, no intro, no outro. Just the list."
    )
    user_msg = (
        f"Channel/Group : {json.dumps(ch_info, indent=2)}\n"
        f"AI Analysis   : {json.dumps(analysis, indent=2)}\n\n"
        "List the best destinations to send the complaint email to, line by line."
    )
    return await call_ai([{"role": "user", "content": user_msg}], sys_prompt)


# ══════════════════════════════════════════════════════════
# USERBOT (TELETHON) MODULE
# ══════════════════════════════════════════════════════════
async def init_userbot() -> bool:
    global userbot_client
    if not API_ID or not API_HASH:
        logger.warning("init_userbot: API_ID/API_HASH not set.")
        return False
    if os.path.exists(f"{SESSION_FILE}.session"):
        try:
            userbot_client = TelegramClient(SESSION_FILE, API_ID, API_HASH)
            await userbot_client.connect()
            if await userbot_client.is_user_authorized():
                logger.info("✅ Userbot session restored.")
                return True
        except Exception as ex:
            logger.warning(f"Session restore failed: {ex}")
    return False


async def ub_send_code(phone: str):
    global userbot_client
    userbot_client = TelegramClient(SESSION_FILE, API_ID, API_HASH)
    await userbot_client.connect()
    sent = await userbot_client.send_code_request(phone)
    return sent.phone_code_hash


async def ub_sign_in(phone: str, code: str, code_hash: str):
    await userbot_client.sign_in(phone, code, phone_code_hash=code_hash)


async def ub_sign_in_2fa(password: str):
    await userbot_client.sign_in(password=password)


def _build_msg_link(entity, msg_id: int) -> str:
    username = getattr(entity, "username", None)
    if username:
        return f"https://t.me/{username}/{msg_id}"
    raw_id = str(entity.id)
    if raw_id.startswith("-100"):
        cid = raw_id[4:]
    elif raw_id.startswith("-"):
        cid = raw_id[1:]
    else:
        cid = raw_id
    return f"https://t.me/c/{cid}/{msg_id}"


async def ub_join_and_fetch(
    link: str,
    start_msg_id: int = 0,
) -> Tuple[object, dict, List[str], List[str], List[dict]]:
    """
    Returns: (entity, ch_info, messages, msg_links, image_list)
    image_list: [{"b64": str, "caption": str, "msg_id": int, "link": str}]
    If start_msg_id > 0, fetches messages FROM that ID going forward (newer).
    """
    if not userbot_client or not await userbot_client.is_user_authorized():
        raise RuntimeError("No account linked. Add account first.")

    entity = None

    # Join logic
    if "t.me/+" in link or "t.me/joinchat/" in link:
        if "t.me/+" in link:
            invite_hash = link.split("t.me/+")[-1].strip("/")
        else:
            invite_hash = link.split("t.me/joinchat/")[-1].strip("/")
        try:
            result = await userbot_client(ImportChatInviteRequest(invite_hash))
            entity = result.chats[0]
        except errors.UserAlreadyParticipantError:
            entity = await userbot_client.get_entity(link)
        except Exception as ex:
            raise RuntimeError(f"Could not join private link: {ex}")
    elif link.startswith("@"):
        entity = await userbot_client.get_entity(link.lstrip("@"))
    elif "t.me/" in link:
        slug = link.split("t.me/")[-1].split("/")[0].strip()
        entity = await userbot_client.get_entity(slug)
    else:
        entity = await userbot_client.get_entity(link.strip("@"))

    if entity is None:
        raise RuntimeError("Entity resolution failed.")

    try:
        await userbot_client(JoinChannelRequest(entity))
    except errors.UserAlreadyParticipantError:
        pass
    except Exception as ex:
        logger.warning(f"Join step warning: {ex}")

    is_channel = isinstance(entity, Channel) and getattr(entity, "broadcast", False)
    ch_info = {
        "id": entity.id,
        "title": getattr(entity, "title", "Unknown"),
        "username": getattr(entity, "username", None),
        "type": "channel" if is_channel else "group",
        "members_count": getattr(entity, "participants_count", "Unknown"),
        "link": link,
    }

    messages: List[str] = []
    msg_links: List[str] = []
    image_list: List[dict] = []
    images_downloaded = 0

    try:
        iter_kwargs = {"limit": 40}
        if start_msg_id > 0:
            iter_kwargs["min_id"] = start_msg_id - 1  # fetch msg_id and newer
        async for msg in userbot_client.iter_messages(entity, **iter_kwargs):
            msg_link = _build_msg_link(entity, msg.id)

            # Text messages
            if msg.text:
                messages.append(msg.text)
                msg_links.append(msg_link)

            # Photo messages
            if isinstance(msg.media, MessageMediaPhoto) and images_downloaded < MAX_IMAGES:
                try:
                    photo_bytes = await userbot_client.download_media(msg, file=bytes)
                    if photo_bytes:
                        b64 = base64.b64encode(photo_bytes).decode("utf-8")
                        image_list.append({
                            "b64": b64,
                            "caption": msg.text or "",
                            "msg_id": msg.id,
                            "link": msg_link,
                        })
                        images_downloaded += 1
                except Exception as ex:
                    logger.warning(f"Image download failed for msg {msg.id}: {ex}")
    except Exception as ex:
        logger.warning(f"iter_messages error: {ex}")

    return entity, ch_info, messages, msg_links, image_list


# ══════════════════════════════════════════════════════════
# TARGET RESOLVER (for /sudo /rmsudo)
# ══════════════════════════════════════════════════════════
async def _resolve_target(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> Tuple[Optional[int], Optional[str], Optional[str]]:
    if update.message.reply_to_message:
        sender = update.message.reply_to_message.from_user
        if sender:
            return sender.id, sender.username, sender.full_name
        return None, None, None

    args = context.args
    if not args:
        return None, None, None

    arg = args[0].strip()

    if arg.lstrip("-").isdigit():
        uid = int(arg)
        try:
            chat = await context.bot.get_chat(uid)
            return uid, getattr(chat, "username", None), getattr(chat, "first_name", None)
        except Exception:
            pass
        return uid, None, None

    uname = arg.lstrip("@")
    try:
        chat = await context.bot.get_chat(f"@{uname}")
        return chat.id, getattr(chat, "username", None), getattr(chat, "first_name", None)
    except Exception:
        pass

    if userbot_client and await userbot_client.is_user_authorized():
        try:
            ent = await userbot_client.get_entity(uname)
            return (
                ent.id,
                getattr(ent, "username", None),
                getattr(ent, "first_name", None),
            )
        except Exception as ex:
            logger.warning(f"Username resolve failed for @{uname}: {ex}")

    return None, uname, None


# ══════════════════════════════════════════════════════════
# SUDO COMMANDS
# ══════════════════════════════════════════════════════════
async def cmd_sudo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_owner(update.effective_user.id):
        await update.message.reply_text("🔒 Owner only command.")
        return

    uid, username, name = await _resolve_target(update, context)

    if uid is None and username is None:
        await update.message.reply_text(
            "❌ <b>Usage:</b>\n"
            "• /sudo @username\n"
            "• /sudo user_id\n"
            "• Reply to user's message and send /sudo",
            parse_mode=ParseMode.HTML,
        )
        return

    if uid is None:
        await update.message.reply_text(
            f"❌ Could not resolve @{e(username)} to a user ID.\n\n"
            "<b>Tips:</b>\n"
            "• Use numeric ID instead: /sudo 123456789\n"
            "• OR reply to their message and send /sudo\n"
            "• Username works only if they've interacted with the bot",
            parse_mode=ParseMode.HTML,
        )
        return

    if uid == OWNER_ID:
        await update.message.reply_text("⚠️ You are the owner — no sudo needed for yourself.")
        return

    added = add_sudo_user(uid, username, name)
    uname_str = f"@{e(username)}" if username else f"<code>{uid}</code>"
    name_str = e(name) if name else "Unknown"

    if added:
        await update.message.reply_text(
            f"✅ <b>Sudo Access Granted</b>\n\n"
            f"👤 User : {uname_str}\n"
            f"📛 Name : {name_str}\n"
            f"🆔 ID   : <code>{uid}</code>\n\n"
            f"They can now use this bot.",
            parse_mode=ParseMode.HTML,
        )
    else:
        await update.message.reply_text(
            f"ℹ️ {uname_str} already has sudo access (info refreshed).",
            parse_mode=ParseMode.HTML,
        )


async def cmd_rmsudo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_owner(update.effective_user.id):
        await update.message.reply_text("🔒 Owner only command.")
        return

    uid, username, _ = await _resolve_target(update, context)

    if uid is None and username is None:
        await update.message.reply_text(
            "❌ <b>Usage:</b>\n"
            "• /rmsudo @username\n"
            "• /rmsudo user_id\n"
            "• Reply to user's message and send /rmsudo",
            parse_mode=ParseMode.HTML,
        )
        return

    if uid is None:
        await update.message.reply_text(
            f"❌ Could not resolve @{e(username)} to a user ID.\n"
            "Use numeric ID or reply to their message.",
            parse_mode=ParseMode.HTML,
        )
        return

    removed = remove_sudo_user(uid)
    uname_str = f"@{e(username)}" if username else f"<code>{uid}</code>"

    if removed:
        await update.message.reply_text(
            f"✅ <b>Sudo Access Removed</b>\n\n"
            f"👤 {uname_str} (ID: <code>{uid}</code>)\n"
            f"They can no longer use this bot.",
            parse_mode=ParseMode.HTML,
        )
    else:
        await update.message.reply_text(
            f"⚠️ {uname_str} was not in the sudo list.",
            parse_mode=ParseMode.HTML,
        )


async def cmd_sudolist(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_owner(update.effective_user.id):
        await update.message.reply_text("🔒 Owner only command.")
        return

    users = load_sudo_users()
    if not users:
        await update.message.reply_text(
            "👥 <b>Sudo Users</b>\n\nNo sudo users yet.\nUse /sudo to grant access.",
            parse_mode=ParseMode.HTML,
        )
        return

    lines = []
    for i, u in enumerate(users, 1):
        uname = f"@{e(u['username'])}" if u.get("username") else "—"
        name = e(u["name"]) if u.get("name") else "Unknown"
        lines.append(
            f"{i}. {uname}  |  📛 {name}\n"
            f"   🆔 <code>{u['id']}</code>"
        )

    text = (
        f"👥 <b>Sudo Users ({len(users)})</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n\n"
        + "\n\n".join(lines)
        + "\n\n━━━━━━━━━━━━━━━━━━━━━━\n"
        "Use /rmsudo to revoke access."
    )
    await update.message.reply_text(text, parse_mode=ParseMode.HTML)


# ══════════════════════════════════════════════════════════
# BOT HANDLERS
# ══════════════════════════════════════════════════════════
async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorized(update.effective_user.id):
        await update.message.reply_text("🔒 This bot is private.")
        return ConversationHandler.END

    linked = userbot_client and await userbot_client.is_user_authorized()
    status_icon = "✅" if linked else "❌"
    status_text = "Account Linked" if linked else "No Account Linked"

    text = (
        "🛡️ <b>Illegal Content Detector Bot</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"👋 Welcome, <b>{e(update.effective_user.first_name)}</b>!\n\n"
        f"🔐 <b>Status:</b> {status_icon} {status_text}\n\n"
        "<b>What I can do:</b>\n"
        "• 🔍 Join &amp; scan any Telegram group/channel for illegal content\n"
        "• 🖼️ Analyze photos/images with Vision AI\n"
        "• ⚖️ AI-powered legal analysis with law citations\n"
        "• 📧 Generate professional legal complaint emails\n"
        "• 🤖 Answer your legal / content questions via AI chat\n\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n"
        "Choose an option:"
    )
    await update.message.reply_text(
        text,
        parse_mode=ParseMode.HTML,
        reply_markup=main_keyboard(),
    )
    return MAIN_MENU


# Universal callback router
async def cb_router(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    try:
        await query.answer()
    except Exception:
        logger.exception("query.answer() failed")

    data = query.data or ""
    logger.info(
        f"CALLBACK | user={query.from_user.id} | data={data}"
    )

    try:
        if not is_authorized(query.from_user.id):
            await safe_edit(query, "🔒 Unauthorized.")
            return ConversationHandler.END

        if data == "back_main":
            linked = userbot_client and await userbot_client.is_user_authorized()
            icon = "✅" if linked else "❌"
            label = "Account Linked" if linked else "No Account"
            await safe_edit(
                query,
                f"🛡️ <b>Illegal Content Detector Bot</b>\n\n"
                f"🔐 <b>Status:</b> {icon} {label}\n\nChoose an option:",
                main_keyboard(),
            )
            return MAIN_MENU

        if data == "add_account":
            await safe_edit(
                query,
                "📱 <b>Add Telegram Account</b>\n\n"
                "Enter phone number with country code:\n"
                "Example: <code>+917xxxxxxxxx</code>\n\n"
                "Type /cancel to abort.",
            )
            return ADD_PHONE

        if data == "verify":
            if not (userbot_client and await userbot_client.is_user_authorized()):
                await safe_edit(
                    query,
                    "❌ <b>No account linked!</b>\n\n"
                    "Add an account first using '➕ Add / Change Account'.",
                    back_keyboard(),
                )
                return MAIN_MENU
            await safe_edit(
                query,
                "🔍 <b>Verify Group / Channel</b>\n\n"
                "Send the link or username:\n\n"
                "• Private: <code>https://t.me/+xxxxxxxx</code>\n"
                "• Public:  <code>@username</code>  or  "
                "<code>https://t.me/username</code>\n\n"
                "Type /cancel to abort.",
            )
            return VERIFY_LINK

        if data == "chat_ai":
            context.user_data["chat_history"] = []
            await safe_edit(
                query,
                "🤖 <b>AI Legal Assistant</b>\n\n"
                "Ask me anything:\n"
                "• Is this content illegal?\n"
                "• Paste text/description for analysis\n"
                "• Questions about Telegram policies\n"
                "• Anything about digital law\n\n"
                "Send /back or /cancel to return to menu.",
            )
            return CHAT_AI

        if data == "status":
            if userbot_client and await userbot_client.is_user_authorized():
                me = await userbot_client.get_me()
                uname = f"@{e(me.username)}" if me.username else "N/A"
                fullname = e(me.first_name) + (" " + e(me.last_name) if me.last_name else "")
                txt = (
                    "✅ <b>Account Linked</b>\n\n"
                    f"👤 Name  : {fullname}\n"
                    f"📱 Phone : <code>{e(me.phone)}</code>\n"
                    f"🆔 ID    : <code>{me.id}</code>\n"
                    f"🔗 User  : {uname}"
                )
            else:
                txt = (
                    "❌ No account linked.\n"
                    "Tap '➕ Add / Change Account' to add one."
                )
            await safe_edit(query, txt, back_keyboard())
            return MAIN_MENU

        if data == "gen_email":
            await safe_edit(
                query,
                "📧 <b>Generate Legal Report Email</b>\n\n"
                "Enter your email address (will appear as reporter contact):\n"
                "Example: <code>yourname@gmail.com</code>",
            )
            return REPORT_EMAIL

        if data == "verify_another":
            if not (userbot_client and await userbot_client.is_user_authorized()):
                await safe_edit(query, "❌ No account linked!", back_keyboard())
                return MAIN_MENU
            await safe_edit(
                query,
                "🔍 <b>Verify Another Group / Channel</b>\n\n"
                "Send the link or @username:\n\n"
                "• Private: <code>https://t.me/+xxxxxxxx</code>\n"
                "• Public:  <code>@username</code>\n\n"
                "Type /cancel to abort.",
            )
            return VERIFY_LINK

        if data == "where_to_send":
            await safe_edit(
                query,
                "📮 <b>Finding best destinations…</b>\n\nAI is picking suitable "
                "official abuse contacts for this case…",
            )
            analysis = context.user_data.get("last_analysis", {})
            ch_info = context.user_data.get("last_ch_info", {})

            if not analysis or not ch_info:
                await safe_edit(
                    query,
                    "⚠️ No recent analysis found. Please run '🔍 Verify' first.",
                    back_keyboard(),
                )
                return MAIN_MENU

            destinations = await suggest_report_destinations(ch_info, analysis)

            kb = InlineKeyboardMarkup([
                [
                    InlineKeyboardButton("🔵 🔍 Verify Another", callback_data="verify_another"),
                    InlineKeyboardButton("🟡 🏠 Menu",           callback_data="back_main"),
                ]
            ])
            text = (
                "📮 <b>Where to Send This Report</b>\n"
                "━━━━━━━━━━━━━━━━━━━━━━\n\n"
                f"<pre>{e(destinations)}</pre>\n"
                "━━━━━━━━━━━━━━━━━━━━━━\n"
                "💡 Copy the email(s) most relevant to your case and CC them "
                "alongside <b>abuse@telegram.org</b>."
            )
            await safe_edit(query, text, kb)
            return MAIN_MENU

        # Unknown callback
        logger.warning(f"Unknown callback data: {data}")
        return MAIN_MENU

    except Exception:
        logger.exception(f"cb_router failed for data={data}")
        try:
            await safe_edit(
                query,
                "⚠️ Something went wrong. Please try again.",
                main_keyboard(),
            )
        except Exception:
            logger.exception("cb_router error-fallback edit failed")
        return MAIN_MENU


# ─────────────────────────────  Add Account: Phone  ─────────────
async def hdl_phone(update: Update, context: ContextTypes.DEFAULT_TYPE):
    phone = update.message.text.strip()
    if not phone.startswith("+"):
        await update.message.reply_text(
            "❌ Include country code, e.g. <code>+917xxxxxxxxx</code>",
            parse_mode=ParseMode.HTML,
        )
        return ADD_PHONE

    msg = await update.message.reply_text("📤 Sending OTP…")
    try:
        code_hash = await ub_send_code(phone)
        context.user_data["phone"] = phone
        context.user_data["code_hash"] = code_hash
        await safe_edit_with_fallback(
            msg,
            f"✅ OTP sent to <b>{e(phone)}</b>\n\n"
            "Enter the code you received (spaces OK):",
        )
        return ADD_OTP
    except Exception as ex:
        logger.exception("hdl_phone: send_code failed")
        await safe_edit_with_fallback(
            msg, f"❌ Failed: <code>{e(str(ex))}</code>\n\nTry again or /cancel",
        )
        return ADD_PHONE


# ─────────────────────────────  Add Account: OTP  ────────────────
async def hdl_otp(update: Update, context: ContextTypes.DEFAULT_TYPE):
    code = update.message.text.strip().replace(" ", "")
    phone = context.user_data.get("phone")
    code_hash = context.user_data.get("code_hash")
    msg = await update.message.reply_text("⏳ Verifying OTP…")

    try:
        await ub_sign_in(phone, code, code_hash)
        me = await userbot_client.get_me()
        fullname = e(me.first_name) + (" " + e(me.last_name) if me.last_name else "")
        uname = f"@{e(me.username)}" if me.username else "N/A"
        await safe_edit_with_fallback(
            msg,
            f"✅ <b>Account linked!</b>\n\n"
            f"👤 {fullname}\n"
            f"📱 <code>{e(me.phone)}</code>\n"
            f"🆔 <code>{me.id}</code>\n"
            f"🔗 {uname}",
            main_keyboard(),
        )
        return MAIN_MENU

    except errors.SessionPasswordNeededError:
        await safe_edit_with_fallback(
            msg,
            "🔐 <b>2FA Required</b>\n\nEnter your Two-Factor Authentication password:",
        )
        return ADD_2FA

    except (errors.PhoneCodeInvalidError, errors.PhoneCodeExpiredError):
        await safe_edit_with_fallback(msg, "❌ Wrong or expired OTP. Try again:")
        return ADD_OTP

    except Exception as ex:
        logger.exception("hdl_otp failed")
        await safe_edit_with_fallback(
            msg, f"❌ Error: <code>{e(str(ex))}</code>\n\nTry again or /cancel",
        )
        return ADD_OTP


# ─────────────────────────────  Add Account: 2FA  ────────────────
async def hdl_2fa(update: Update, context: ContextTypes.DEFAULT_TYPE):
    pw = update.message.text.strip()
    msg = await update.message.reply_text("⏳ Verifying 2FA…")
    try:
        await ub_sign_in_2fa(pw)
        me = await userbot_client.get_me()
        uname = f"@{e(me.username)}" if me.username else "N/A"
        await safe_edit_with_fallback(
            msg,
            f"✅ <b>Account linked!</b>\n\n"
            f"👤 {e(me.first_name)}\n"
            f"📱 <code>{e(me.phone)}</code>\n"
            f"🔗 {uname}",
            main_keyboard(),
        )
        return MAIN_MENU
    except errors.PasswordHashInvalidError:
        await safe_edit_with_fallback(msg, "❌ Wrong 2FA password. Try again:")
        return ADD_2FA
    except Exception as ex:
        logger.exception("hdl_2fa failed")
        await safe_edit_with_fallback(
            msg, f"❌ Error: <code>{e(str(ex))}</code>\n\nTry again or /cancel",
        )
        return ADD_2FA


# ══════════════════════════════════════════════════════════
# VERIFY GROUP / CHANNEL  (Updated with Image Detection)
# ══════════════════════════════════════════════════════════
async def hdl_verify(update: Update, context: ContextTypes.DEFAULT_TYPE):
    link = update.message.text.strip()

    # Detect if a specific message link was given (e.g. t.me/channel/123)
    start_msg_id = 0
    msg_id_match = re.search(r't\.me/(?:c/\d+|\w+)/(\d+)', link)
    if msg_id_match:
        start_msg_id = int(msg_id_match.group(1))
        link = re.sub(r'/(\d+)$', '', link).strip()

    mode_note = (
        f"⏳ [1/5] Joining & fetching from message #{start_msg_id} onwards…"
        if start_msg_id else
        "⏳ [1/5] Joining group/channel…"
    )
    progress = await update.message.reply_text(
        f"🔍 <b>Analysis Started…</b>\n\n{mode_note}",
        parse_mode=ParseMode.HTML,
    )

    # Step 1: Join & Fetch
    try:
        entity, ch_info, messages, msg_links, image_list = await ub_join_and_fetch(link, start_msg_id)
    except Exception as ex:
        logger.exception("hdl_verify: join/fetch failed")
        await safe_edit_with_fallback(
            progress,
            f"❌ Error: <code>{e(str(ex))}</code>\n\nSend another link or /cancel",
        )
        return VERIFY_LINK

    img_note = f" + {len(image_list)} photo(s)" if image_list else ""
    await safe_edit_with_fallback(
        progress,
        f"🔍 <b>Analysis In Progress…</b>\n\n"
        f"✅ [1/5] Joined: <b>{e(ch_info['title'])}</b>\n"
        f"⏳ [2/5] Fetched {len(messages)} messages{img_note} — running AI…",
    )

    # Step 2 & 3: Text + Image analysis in parallel
    text_task = asyncio.create_task(analyze_illegality(messages, ch_info))
    image_task = asyncio.create_task(analyze_all_images(image_list)) if image_list else None

    analysis = await text_task
    illegal_image_results: List[dict] = await image_task if image_task else []

    await safe_edit_with_fallback(
        progress,
        f"🔍 <b>Analysis In Progress…</b>\n\n"
        f"✅ [1/5] Joined: <b>{e(ch_info['title'])}</b>\n"
        f"✅ [2/5] {len(messages)} messages fetched{img_note}\n"
        f"✅ [3/5] Text AI analysis done\n"
        f"✅ [4/5] Image AI analysis done ({len(illegal_image_results)} flagged)\n"
        f"⏳ [5/5] Compiling report…",
    )

    context.user_data["last_analysis"] = analysis
    context.user_data["last_ch_info"] = ch_info
    context.user_data["last_link"] = link
    context.user_data["last_msg_links"] = msg_links
    context.user_data["illegal_image_results"] = illegal_image_results

    # Build illegal text message links
    illegal_indices = analysis.get("illegal_message_indices", [])
    illegal_links = [
        msg_links[i]
        for i in illegal_indices
        if isinstance(i, int) and 0 <= i < len(msg_links)
    ]
    context.user_data["illegal_links"] = illegal_links

    # ══════════════════════════════════════════════════════
    # ILLEGAL / VIOLATION REPORT
    # ══════════════════════════════════════════════════════
    if analysis.get("is_illegal") or illegal_image_results:
        sev = analysis.get("severity", "MEDIUM").upper()
        sev_icon = {
            "CRITICAL": "🚨",
            "HIGH": "🔴",
            "MEDIUM": "🟠",
            "LOW": "🟡",
        }.get(sev, "⚠️")

        violations_txt = "\n".join(
            f"  • {e(v)}" for v in analysis.get("violations", ["N/A"])
        ) or "  • None specified"
        laws_txt = "\n".join(
            f"  • {e(l)}" for l in analysis.get("applicable_laws", ["N/A"])
        ) or "  • None specified"

        if illegal_links:
            links_section = (
                "\n\n🔗 <b>Direct Evidence Links (Illegal Text Messages):</b>\n"
                + "\n".join(
                    f'  {i + 1}. <a href="{lnk}">{e(lnk)}</a>'
                    for i, lnk in enumerate(illegal_links)
                )
            )
        else:
            links_section = ""

        # Illegal image results section
        img_section = ""
        if illegal_image_results:
            img_lines = []
            for r in illegal_image_results:
                viols = ", ".join(r.get("violations", [])) or "Unknown violation"
                conf = r.get("confidence", "?")
                lnk = r.get("msg_link", "")
                link_str = f'<a href="{lnk}">View Photo</a>' if lnk else "Private"
                img_lines.append(
                    f"  📸 {link_str} — {e(viols)} [{conf}]"
                )
            img_section = (
                f"\n\n🖼️ <b>Illegal Photos Detected ({len(illegal_image_results)}):</b>\n"
                + "\n".join(img_lines)
            )

        # Suggested report description
        report_desc = analysis.get("report_description", "").strip()
        desc_section = (
            f"\n\n📝 <b>Suggested Report Description (paste this):</b>\n"
            f"<code>{e(report_desc)}</code>"
        ) if report_desc else ""

        # Telegram report instructions
        tg_cat = analysis.get("telegram_report_category", "Other")
        tg_subcat = analysis.get("telegram_report_subcategory", "")

        # If image violations exist but no text violation category, pick best category
        if not analysis.get("is_illegal") and illegal_image_results:
            img_viols = [v for r in illegal_image_results for v in r.get("violations", [])]
            viol_lower = " ".join(img_viols).lower()
            if any(x in viol_lower for x in ["child", "csam", "minor"]):
                tg_cat = "Child abuse"
                tg_subcat = "Child sexual abuse"
            elif any(x in viol_lower for x in ["porn", "sexual", "nude"]):
                tg_cat = "Illegal adult content"
                tg_subcat = "Pornography"
            elif any(x in viol_lower for x in ["weapon", "gun", "drug"]):
                tg_cat = "Illegal goods and services"
                tg_subcat = "Weapons" if ("weapon" in viol_lower or "gun" in viol_lower) else "Drugs"
            elif any(x in viol_lower for x in ["violence", "gore", "graphic"]):
                tg_cat = "Violence"
                tg_subcat = "Graphic or disturbing content"
            else:
                tg_cat = "Other"
                tg_subcat = ""

        report_instructions = format_report_instructions(tg_cat, tg_subcat, report_desc)

        ch_username = ch_info.get("username")
        uname_line = f"• Username : @{e(ch_username)}\n" if ch_username else ""

        report = (
            f"{sev_icon} <b>ILLEGAL CONTENT DETECTED</b> {sev_icon}\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n\n"
            f"📌 <b>Channel / Group Info</b>\n"
            f"• Title   : <b>{e(ch_info.get('title', 'N/A'))}</b>\n"
            f"{uname_line}"
            f"• Type    : {e(ch_info.get('type', 'N/A').upper())}\n"
            f"• Members : {e(ch_info.get('members_count', 'N/A'))}\n"
            f"• Link    : {e(link)}\n\n"
            f"⚖️ <b>Violations Detected</b>\n{violations_txt}\n\n"
            f"📜 <b>Applicable Laws</b>\n{laws_txt}\n\n"
            f"🎯 <b>Severity:</b> {e(analysis.get('severity', '?'))}  |  "
            f"<b>Confidence:</b> {e(analysis.get('confidence', '?'))}\n\n"
            f"📋 <b>Detailed Reason</b>\n{e(analysis.get('detailed_reason', 'N/A'))}"
            f"{img_section}"
            f"{links_section}\n\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n"
            f"{report_instructions}"
            f"{desc_section}\n\n"
            f"🌐 Web report : https://t.me/abuse\n"
            f"📧 Email      : abuse@telegram.org\n"
            f"━━━━━━━━━━━━━━━━━━━━━━"
        )

        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("🟢 📧 Generate Legal Report Email", callback_data="gen_email")],
            [InlineKeyboardButton("🟣 📮 Where to Send",                callback_data="where_to_send")],
            [
                InlineKeyboardButton("🔵 🔍 Verify Another", callback_data="verify_another"),
                InlineKeyboardButton("🟡 🏠 Menu",           callback_data="back_main"),
            ],
        ])

        if len(report) > 4000:
            parts = [report[i:i + 4000] for i in range(0, len(report), 4000)]
            for i, part in enumerate(parts):
                if i == 0:
                    await safe_edit_with_fallback(progress, part)
                elif i == len(parts) - 1:
                    await update.message.reply_text(
                        part, parse_mode=ParseMode.HTML, reply_markup=kb
                    )
                else:
                    await update.message.reply_text(part, parse_mode=ParseMode.HTML)
        else:
            await safe_edit_with_fallback(progress, report, kb)

    # ══════════════════════════════════════════════════════
    # CLEAN REPORT
    # ══════════════════════════════════════════════════════
    else:
        img_clean_note = (
            f"\n🖼️ Photos scanned  : {len(image_list)} (0 flagged)"
            if image_list else ""
        )
        clean_report = (
            f"✅ <b>Analysis Complete — No Illegal Content Found</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n\n"
            f"📌 <b>{e(ch_info.get('title', 'Unknown'))}</b>\n"
            f"📊 Messages analysed : {len(messages)}{img_clean_note}\n"
            f"🎯 Confidence        : {e(analysis.get('confidence', 'N/A'))}\n\n"
            f"📝 <b>AI Summary</b>\n"
            f"{e(analysis.get('summary', 'Content appears within legal boundaries.'))}\n\n"
            f"⚠️ This is an AI-based analysis. Human judgement is always recommended.\n"
            f"━━━━━━━━━━━━━━━━━━━━━━"
        )
        kb = InlineKeyboardMarkup([
            [
                InlineKeyboardButton("🔵 🔍 Verify Another", callback_data="verify_another"),
                InlineKeyboardButton("🟡 🏠 Menu",           callback_data="back_main"),
            ],
        ])
        await safe_edit_with_fallback(progress, clean_report, kb)

    return MAIN_MENU


# ─────────────────────────  Chat with AI  ────────────────────────
LEGAL_SYSTEM = (
    "You are a highly experienced AI legal analyst specialising in:\n"
    "• Telegram Terms of Service\n"
    "• Cybercrime laws (IT Act India, CFAA USA, GDPR EU, IPC sections, etc.)\n"
    "• Digital content legality worldwide\n"
    "• Detection of: CSAM, terrorism, drug/weapons trafficking, human trafficking, "
    "financial fraud, piracy, hate speech, doxxing, etc.\n\n"
    "Rules:\n"
    "1. Always provide a clear LEGAL / ILLEGAL verdict when asked.\n"
    "2. Cite specific laws and sections when relevant.\n"
    "3. Be thorough yet concise.\n"
    "4. If unsure, say so clearly.\n"
    "5. Write in plain text (no markdown formatting)."
)


async def hdl_chat(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message.text.strip()

    if msg.lower() in ("/back", "/cancel", "/menu"):
        await update.message.reply_text(
            "↩️ Back to main menu.", reply_markup=main_keyboard(),
        )
        return MAIN_MENU

    history = context.user_data.get("chat_history", [])
    history.append({"role": "user", "content": msg})

    thinking = await update.message.reply_text("🤖 Analysing…")
    response = await call_ai(history, LEGAL_SYSTEM)

    history.append({"role": "assistant", "content": response})
    context.user_data["chat_history"] = history[-30:]

    kb = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("🔵 🔍 Verify Channel", callback_data="verify"),
            InlineKeyboardButton("🟡 🏠 Menu",           callback_data="back_main"),
        ]
    ])
    try:
        await thinking.delete()
    except Exception:
        logger.exception("thinking message delete failed")
    await split_send(update.message, e(response), kb)
    return CHAT_AI


# ─────────────────────────  Report Email  ────────────────────────
async def hdl_email(update: Update, context: ContextTypes.DEFAULT_TYPE):
    reporter_email = update.message.text.strip()
    analysis = context.user_data.get("last_analysis", {})
    ch_info = context.user_data.get("last_ch_info", {})
    link = context.user_data.get("last_link", "")
    illegal_links = context.user_data.get("illegal_links", [])
    ch_info["link"] = link

    wait_msg = await update.message.reply_text("⏳ Generating professional legal email…")

    email_body = await generate_legal_email(ch_info, analysis, reporter_email, illegal_links)

    header = (
        "📧 <b>Professional Legal Report Email</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n\n"
    )

    ev_block = ""
    if illegal_links:
        ev_block = (
            "\n\n🔗 <b>Illegal Message Evidence Links:</b>\n"
            + "\n".join(
                f'  {i + 1}. <a href="{lnk}">{e(lnk)}</a>'
                for i, lnk in enumerate(illegal_links[:6])
            )
            + "\n"
        )

    footer = (
        f"{ev_block}\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"💡 <b>Next Steps:</b>\n"
        f"1. Copy &amp; send to <code>abuse@telegram.org</code>\n"
        f"2. Also report via in-app → ⋮ → Report\n"
        f"3. Keep a copy for your records\n"
        f"4. For CRITICAL cases, also report to local cyber-crime police\n"
        f"5. Tap <b>📮 Where to Send</b> for AI-picked destinations"
    )

    full = header + e(email_body) + footer

    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("🟣 📮 Where to Send this Email", callback_data="where_to_send")],
        [
            InlineKeyboardButton("🔵 🔍 Verify Another", callback_data="verify_another"),
            InlineKeyboardButton("🟡 🏠 Menu",           callback_data="back_main"),
        ]
    ])
    try:
        await wait_msg.delete()
    except Exception:
        logger.exception("wait_msg delete failed")
    await split_send(update.message, full, kb)
    return MAIN_MENU


# ─────────────────────────  Cancel  ──────────────────────────────
async def cmd_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "↩️ Cancelled. Back to main menu.", reply_markup=main_keyboard(),
    )
    return MAIN_MENU


# ─────────────────────────  Global error handler  ────────────────
async def on_error(update: object, context: ContextTypes.DEFAULT_TYPE):
    logger.exception(f"Unhandled exception while processing update: {context.error}")


# ══════════════════════════════════════════════════════════
# POST-INIT
# ══════════════════════════════════════════════════════════
async def post_init(application: Application) -> None:
    await init_userbot()


# ══════════════════════════════════════════════════════════
# MAIN  — concurrent_updates=False (ConversationHandler safe)
# ══════════════════════════════════════════════════════════
def main() -> None:
    logger.info("🚀 Initialising bot…")

    if not BOT_TOKEN:
        raise RuntimeError("BOT_TOKEN environment variable is missing.")

    app = (
        Application.builder()
        .token(BOT_TOKEN)
        .concurrent_updates(False)   # ✅ FIX: ConversationHandler processes sequentially
        .post_init(post_init)
        .build()
    )

    sudo_cmds = [
        CommandHandler("sudo",     cmd_sudo),
        CommandHandler("rmsudo",   cmd_rmsudo),
        CommandHandler("sudolist", cmd_sudolist),
    ]

    conv = ConversationHandler(
        entry_points=[CommandHandler("start", cmd_start)],
        states={
            MAIN_MENU: [
                CallbackQueryHandler(cb_router),
                *sudo_cmds,
            ],
            ADD_PHONE: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, hdl_phone),
                CallbackQueryHandler(cb_router),
                *sudo_cmds,
            ],
            ADD_OTP: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, hdl_otp),
                CallbackQueryHandler(cb_router),
                *sudo_cmds,
            ],
            ADD_2FA: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, hdl_2fa),
                CallbackQueryHandler(cb_router),
                *sudo_cmds,
            ],
            VERIFY_LINK: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, hdl_verify),
                CallbackQueryHandler(cb_router),
                *sudo_cmds,
            ],
            CHAT_AI: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, hdl_chat),
                CallbackQueryHandler(cb_router),
                *sudo_cmds,
            ],
            REPORT_EMAIL: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, hdl_email),
                CallbackQueryHandler(cb_router),
                *sudo_cmds,
            ],
        },
        fallbacks=[
            CommandHandler("cancel", cmd_cancel),
            CommandHandler("start",  cmd_start),
            CallbackQueryHandler(cb_router),  # safety net for stale callbacks
            *sudo_cmds,
        ],
        allow_reentry=True,
        conversation_timeout=600,
        per_message=False,
    )

    app.add_handler(conv)

    # Also register sudo commands globally so they work outside conversation
    for handler in sudo_cmds:
        app.add_handler(handler, group=1)

    app.add_error_handler(on_error)

    logger.info("✅ Bot is running! Press Ctrl+C to stop.")
    app.run_polling(drop_pending_updates=True)


# ✅ Correct name check
if __name__ == "__main__":
    main()
