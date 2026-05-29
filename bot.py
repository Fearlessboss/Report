#!/usr/bin/env python3
"""
╔══════════════════════════════════════════════════════╗
║      🛡️  ILLEGAL CONTENT DETECTOR BOT  🛡️            ║
║      Owner + Sudo | Powered by AI + Telethon         ║
╚══════════════════════════════════════════════════════╝
FIXES v5:
  • CRITICAL: Fixed broken API response parsing (data["choices"][0]...)
  • Switched to stable free models on OpenRouter
  • Fixed incomplete f-string in image analysis error log
  • Fixed HTML tag regex (was missing < in pattern)
  • Fixed __name__ == "__main__" check
  • Added detailed API error logging to debug future issues
  • concurrent_updates=True  → bot replies to ALL users simultaneously
  • Accurate Telegram report categories + sub-categories (full tree)
  • AI picks exact category → subcategory path automatically
  • Photo / Image detection via Vision AI
  • load_sudo_users()  → auto-migrates old int[] → dict[] format
  • get_sudo_ids()     → safe, never crashes on any JSON format
  • _resolve_target()  → Bot API first, then userbot fallback
"""

import asyncio
import base64
import logging
import os
import json
import re
import aiohttp
from datetime import datetime
from html import escape as he
from typing import Optional, List, Tuple, Dict

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ConversationHandler,
    filters,
    ContextTypes,
)
from telegram.constants import ParseMode
from telegram.error import BadRequest

from telethon import TelegramClient, errors
from telethon.tl.functions.channels import JoinChannelRequest
from telethon.tl.functions.messages import ImportChatInviteRequest
from telethon.tl.types import Channel, MessageMediaPhoto, MessageMediaDocument

# ══════════════════════════════════════════════════════════
# CONFIGURATION
# ══════════════════════════════════════════════════════════
OWNER_ID     = 7661825494
BOT_TOKEN    = "8763146794:AAFDAan4KSfIhR6KzLR8PV6G-fuLQSXWvOs"
API_ID       = 33628258
API_HASH     = "0850762925b9c1715b9b122f7b753128"
SESSION_FILE = "userbot_session"
SUDO_FILE    = "sudo_users.json"

API_KEYS = [
    "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",   # 👈 groq key yahan paste karo (console.groq.com)
]
OPENROUTER_URL = "https://api.groq.com/openai/v1/chat/completions"  # Groq endpoint

# Groq free models (blazing fast, no credit card needed)
MODEL        = "llama-3.1-8b-instant"            # text analysis
VISION_MODEL = "meta-llama/llama-4-scout-17b-16e-instruct"  # vision model (current)
MAX_IMAGES   = 5   # Max photos to analyze per channel scan

# ══════════════════════════════════════════════════════════
# TELEGRAM REPORT CATEGORY TREE  (Accurate as of 2024-25)
# ══════════════════════════════════════════════════════════
TELEGRAM_REPORT_TREE: Dict[str, List[str]] = {
    "I don't like it":                    [],
    "Child abuse":                         ["Child sexual abuse", "Child physical abuse"],
    "Violence":                            [
        "Insults or false information",
        "Graphic or disturbing content",
        "Extreme violence, dismemberment",
        "Hate speech or symbols",
        "Calling for violence",
        "Organized crime",
        "Terrorism",
        "Animal abuse",
    ],
    "Illegal goods and services":          [
        "Weapons",
        "Drugs",
        "Fake documents",
        "Counterfeit money",
        "Hacking tools and malware",
        "Counterfeit merchandise",
        "Other goods and services",
    ],
    "Illegal adult content":               [
        "Child abuse",
        "Illegal sexual services",
        "Animal abuse",
        "Non-consensual sexual imagery",
        "Pornography",
        "Other illegal sexual content",
    ],
    "Personal data":                       [
        "Private images",
        "Phone number",
        "Address",
        "Stolen data or credentials",
        "Other personal information",
    ],
    "Scam or fraud":                       [
        "Impersonation",
        "Deceptive or unrealistic financial claims",
        "Malware, phishing",
        "Fraudulent seller, product or service",
    ],
    "Copyright":                           [],
    "Spam":                                [
        "Insults or false information",
        "Promoting illegal content",
        "Promoting other content",
    ],
    "Other":                               [],
    "It's not illegal, but must be taken down": [],
}

# Human-readable tree for AI prompt
_REPORT_TREE_FOR_PROMPT = json.dumps(
    {cat: subs if subs else "(no sub-option, direct report)" for cat, subs in TELEGRAM_REPORT_TREE.items()},
    indent=2,
)

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

# ══════════════════════════════════════════════════════════
# GLOBALS
# ══════════════════════════════════════════════════════════
userbot_client: Optional[TelegramClient] = None
_api_key_index: int = 0

logging.basicConfig(
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger("IllegalBot")

# ══════════════════════════════════════════════════════════
# SUDO MANAGEMENT
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
            u["name"]     = name
            save_sudo_users(users)
            return False
    users.append({"id": user_id, "username": username, "name": name})
    save_sudo_users(users)
    return True


def remove_sudo_user(user_id: int) -> bool:
    users     = load_sudo_users()
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
# HELPERS
# ══════════════════════════════════════════════════════════

def main_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("➕  Add / Change Account",   callback_data="add_account")],
        [InlineKeyboardButton("🔍  Verify Group / Channel", callback_data="verify")],
        [InlineKeyboardButton("🤖  Chat with AI",            callback_data="chat_ai")],
        [InlineKeyboardButton("📊  Account Status",          callback_data="status")],
    ])


def back_keyboard(target: str = "back_main") -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[InlineKeyboardButton("⬅️  Back", callback_data=target)]])


async def safe_edit(query, text: str, keyboard=None):
    try:
        kwargs: dict = {"text": text, "parse_mode": ParseMode.HTML}
        if keyboard:
            kwargs["reply_markup"] = keyboard
        await query.edit_message_text(**kwargs)
    except BadRequest as err:
        if "not modified" in str(err).lower():
            return
        # ✅ FIX: Correct HTML tag stripping regex (was missing < character)
        plain = re.sub(r"<[^>]+>", "", text)
        try:
            kwargs2: dict = {"text": plain}
            if keyboard:
                kwargs2["reply_markup"] = keyboard
            await query.edit_message_text(**kwargs2)
        except Exception:
            pass


async def split_send(update_obj, text: str, keyboard=None):
    chunks = [text[i:i+4000] for i in range(0, len(text), 4000)]
    for idx, chunk in enumerate(chunks):
        kb = keyboard if idx == len(chunks) - 1 else None
        try:
            await update_obj.reply_text(
                chunk, parse_mode=ParseMode.HTML, reply_markup=kb
            )
        except BadRequest:
            # ✅ FIX: Correct HTML tag stripping regex
            plain = re.sub(r"<[^>]+>", "", chunk)
            try:
                await update_obj.reply_text(plain, reply_markup=kb)
            except Exception:
                pass


async def safe_edit_with_fallback(msg, text: str, keyboard=None):
    try:
        kwargs: dict = {"text": text, "parse_mode": ParseMode.HTML}
        if keyboard:
            kwargs["reply_markup"] = keyboard
        await msg.edit_text(**kwargs)
    except BadRequest as err:
        if "not modified" in str(err).lower():
            return
        # ✅ FIX: Correct HTML tag stripping regex
        plain = re.sub(r"<[^>]+>", "", text)
        try:
            kwargs2: dict = {"text": plain}
            if keyboard:
                kwargs2["reply_markup"] = keyboard
            await msg.edit_text(**kwargs2)
        except Exception:
            pass

# ══════════════════════════════════════════════════════════
# TELEGRAM REPORT INSTRUCTIONS GENERATOR
# ══════════════════════════════════════════════════════════

def format_report_instructions(
    category: str,
    subcategory: str = "",
    report_desc: str = "",
) -> str:
    cat    = category.strip()
    subcat = subcategory.strip() if subcategory else ""

    if cat not in TELEGRAM_REPORT_TREE:
        cat    = "Other"
        subcat = ""

    subs      = TELEGRAM_REPORT_TREE.get(cat, [])
    if subcat and subcat not in subs:
        subcat = subs[0] if subs else ""

    has_subs = bool(subs)
    has_desc = bool(report_desc)

    lines = [
        "📢 How to Report on Telegram (In-App):\n",
        "1️⃣  Open the group / channel",
        "2️⃣  Tap the channel name / header at the top",
        "3️⃣  Tap ⋮ (three dots menu) → tap Report",
    ]

    if has_subs and subcat:
        lines.append(f'4️⃣  Choose main category: "{e(cat)}"')
        lines.append(f'5️⃣  Choose sub-option: "{e(subcat)}"')
        if has_desc:
            lines.append(
                f"6️⃣  In the description box, paste:\n"
                f"{e(report_desc)}"
            )
            lines.append("7️⃣  Tap Submit ✅")
        else:
            lines.append("6️⃣  Add any optional description → Tap Submit ✅")

    elif has_subs and not subcat:
        sub_list = "\n".join(f"   • {e(s)}" for s in subs)
        lines.append(f'4️⃣  Choose main category: "{e(cat)}"')
        lines.append(f"5️⃣  Choose the most relevant sub-option:\n{sub_list}")
        if has_desc:
            lines.append(
                f"6️⃣  In the description box, paste:\n"
                f"{e(report_desc)}"
            )
            lines.append("7️⃣  Tap Submit ✅")
        else:
            lines.append("6️⃣  Add optional description → Tap Submit ✅")

    else:
        lines.append(f'4️⃣  Choose: "{e(cat)}"')
        if has_desc:
            lines.append(
                f"5️⃣  In the description box, paste:\n"
                f"{e(report_desc)}"
            )
            lines.append("6️⃣  Tap Submit ✅")
        else:
            lines.append("5️⃣  Add optional description → Tap Submit ✅")

    return "\n".join(lines)

# ══════════════════════════════════════════════════════════
# AI MODULE
# ══════════════════════════════════════════════════════════

async def call_ai(messages: list, system: str = "", use_vision: bool = False) -> str:
    global _api_key_index
    import httpx
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
                "Content-Type":  "application/json",
            }
            body = {
                "model":       model_to_use,
                "messages":    payload_messages,
                "temperature": 0.5,
                "max_tokens":  2048,
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
            "image_url": {
                "url": f"data:image/jpeg;base64,{image_b64}"
            },
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
        pass

    return {
        "is_illegal":   False,
        "confidence":   "LOW",
        "violations":   [],
        "description":  raw[:200],
        "image_index":  index,
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
            # ✅ FIX: Completed the f-string (was cut off before)
            logger.warning(f"Image analysis error for image {i}: {res}")
            continue
        if res.get("is_illegal"):
            res["msg_link"] = image_list[i].get("link", "")
            res["msg_id"]   = image_list[i].get("msg_id", 0)
            illegal_images.append(res)

    return illegal_images

# ──────────────────────────────────────────────────────────
# Text-based illegality analysis
# ──────────────────────────────────────────────────────────

async def analyze_illegality(messages: List[str], ch_info: dict) -> dict:
    # Truncate each message to 300 chars to stay under Groq token limit
    trimmed = [m[:300] for m in messages[:12]]
    indexed_text = "\n---\n".join(
        f"[{i}] {msg}" for i, msg in enumerate(trimmed)
    )
    # Hard cap: keep total text under 3000 chars
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
        pass

    return {
        "is_illegal":                  False,
        "confidence":                  "LOW",
        "violations":                  [],
        "severity":                    "LOW",
        "applicable_laws":             [],
        "summary":                     raw,
        "detailed_reason":             raw,
        "telegram_report_category":    "Other",
        "telegram_report_subcategory": "",
        "illegal_message_indices":     [],
        "report_description":          "",
    }


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
            + "\n".join(f"  • {lnk}" for lnk in illegal_links[:10])
        )

    sys_prompt = (
        "You are a senior cyber-law attorney. Write a formal, professional, and legally "
        "precise complaint email to Telegram's abuse team and, if necessary, to law "
        "enforcement. The email must cite specific laws, include all channel evidence "
        "including the provided message links, and request immediate action. "
        "Use proper legal language."
    )
    user_msg = (
        f"Reporter Email : {reporter_email}\n"
        f"Date           : {datetime.utcnow().strftime('%B %d, %Y')} (UTC)\n"
        f"Channel/Group  : {json.dumps(ch_info, indent=2)}\n"
        f"AI Analysis    : {json.dumps(analysis, indent=2)}\n"
        f"{links_block}\n\n"
        "Write a complete email:\n"
        "• To: abuse@telegram.org\n"
        "• CC: relevant law-enforcement if critical violations\n"
        "• Subject line\n"
        "• Formal body citing laws\n"
        "• List the direct illegal message evidence links\n"
        "• Request for channel removal / account suspension\n"
        "• Closing with reporter contact details: " + reporter_email + "\n"
        "Also add a section 'Supplementary Report Note' – a short optional message "
        "the reporter can paste directly into Telegram's in-app report form."
    )
    return await call_ai([{"role": "user", "content": user_msg}], sys_prompt)

# ══════════════════════════════════════════════════════════
# USERBOT (TELETHON) MODULE
# ══════════════════════════════════════════════════════════

async def init_userbot() -> bool:
    global userbot_client
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
        "id":            entity.id,
        "title":         getattr(entity, "title", "Unknown"),
        "username":      getattr(entity, "username", None),
        "type":          "channel" if is_channel else "group",
        "members_count": getattr(entity, "participants_count", "Unknown"),
        "link":          link,
    }

    messages:   List[str]  = []
    msg_links:  List[str]  = []
    image_list: List[dict] = []
    images_downloaded = 0

    try:
        # If start_msg_id given, fetch 40 messages FROM that point going forward
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
                    img_bytes = await userbot_client.download_media(
                        msg.media, file=bytes
                    )
                    if img_bytes:
                        b64 = base64.b64encode(img_bytes).decode("utf-8")
                        image_list.append({
                            "b64":     b64,
                            "caption": msg.text or "",
                            "msg_id":  msg.id,
                            "link":    msg_link,
                        })
                        images_downloaded += 1
                except Exception as ex:
                    logger.warning(f"Photo download error: {ex}")

    except Exception as ex:
        logger.warning(f"Message fetch error: {ex}")

    return entity, ch_info, messages, msg_links, image_list

# ══════════════════════════════════════════════════════════
# TARGET RESOLVER
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
            "❌ Usage:\n"
            "• /sudo @username\n"
            "• /sudo user_id\n"
            "• Reply to user's message and send /sudo",
            parse_mode=ParseMode.HTML,
        )
        return

    if uid is None:
        await update.message.reply_text(
            f"❌ Could not resolve @{e(username)} to a user ID.\n\n"
            "Tips:\n"
            "• Use numeric ID instead: /sudo 123456789\n"
            "• OR reply to their message and send /sudo\n"
            "• Username works only if they've interacted with the bot",
            parse_mode=ParseMode.HTML,
        )
        return

    if uid == OWNER_ID:
        await update.message.reply_text("⚠️ You are the owner — no sudo needed for yourself.")
        return

    added     = add_sudo_user(uid, username, name)
    uname_str = f"@{e(username)}" if username else f"{uid}"
    name_str  = e(name) if name else "Unknown"

    if added:
        await update.message.reply_text(
            f"✅ Sudo Access Granted\n\n"
            f"👤 User : {uname_str}\n"
            f"📛 Name : {name_str}\n"
            f"🆔 ID   : {uid}\n\n"
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
            "❌ Usage:\n"
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

    removed   = remove_sudo_user(uid)
    uname_str = f"@{e(username)}" if username else f"{uid}"

    if removed:
        await update.message.reply_text(
            f"✅ Sudo Access Removed\n\n"
            f"👤 {uname_str} (ID: {uid})\n"
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
            "👥 Sudo Users\n\nNo sudo users yet.\nUse /sudo to grant access.",
            parse_mode=ParseMode.HTML,
        )
        return

    lines = []
    for i, u in enumerate(users, 1):
        uname = f"@{e(u['username'])}" if u.get("username") else "—"
        name  = e(u["name"]) if u.get("name") else "Unknown"
        lines.append(
            f"{i}. {uname}  |  📛 {name}\n"
            f"   🆔 {u['id']}"
        )

    text = (
        f"👥 Sudo Users ({len(users)})\n"
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

    linked      = userbot_client and await userbot_client.is_user_authorized()
    status_icon = "✅" if linked else "❌"
    status_text = "Account Linked" if linked else "No Account Linked"

    text = (
        "🛡️ Illegal Content Detector Bot\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"👋 Welcome, {e(update.effective_user.first_name)}!\n\n"
        f"🔐 Status: {status_icon} {status_text}\n\n"
        "What I can do:\n"
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
    await query.answer()
    data  = query.data

    if not is_authorized(query.from_user.id):
        await query.edit_message_text("🔒 Unauthorized.")
        return ConversationHandler.END

    if data == "back_main":
        linked = userbot_client and await userbot_client.is_user_authorized()
        icon   = "✅" if linked else "❌"
        label  = "Account Linked" if linked else "No Account"
        await safe_edit(
            query,
            f"🛡️ Illegal Content Detector Bot\n\n"
            f"🔐 Status: {icon} {label}\n\nChoose an option:",
            main_keyboard(),
        )
        return MAIN_MENU

    if data == "add_account":
        await safe_edit(
            query,
            "📱 Add Telegram Account\n\n"
            "Enter phone number with country code:\n"
            "Example: +917xxxxxxxxx\n\n"
            "Type /cancel to abort.",
        )
        return ADD_PHONE

    if data == "verify":
        if not (userbot_client and await userbot_client.is_user_authorized()):
            await safe_edit(
                query,
                "❌ No account linked!\n\n"
                "Add an account first using '➕ Add / Change Account'.",
                back_keyboard(),
            )
            return MAIN_MENU
        await safe_edit(
            query,
            "🔍 Verify Group / Channel\n\n"
            "Send the link or username:\n\n"
            "• Private: https://t.me/+xxxxxxxx\n"
            "• Public:  @username  or  "
            "https://t.me/username\n\n"
            "Type /cancel to abort.",
        )
        return VERIFY_LINK

    if data == "chat_ai":
        context.user_data["chat_history"] = []
        await safe_edit(
            query,
            "🤖 AI Legal Assistant\n\n"
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
            me       = await userbot_client.get_me()
            uname    = f"@{e(me.username)}" if me.username else "N/A"
            fullname = e(me.first_name) + (" " + e(me.last_name) if me.last_name else "")
            txt = (
                "✅ Account Linked\n\n"
                f"👤 Name  : {fullname}\n"
                f"📱 Phone : {e(me.phone)}\n"
                f"🆔 ID    : {me.id}\n"
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
            "📧 Generate Legal Report Email\n\n"
            "Enter your email address (will appear as reporter contact):\n"
            "Example: yourname@gmail.com",
        )
        return REPORT_EMAIL

    if data == "verify_another":
        if not (userbot_client and await userbot_client.is_user_authorized()):
            await safe_edit(query, "❌ No account linked!", back_keyboard())
            return MAIN_MENU
        await safe_edit(
            query,
            "🔍 Verify Another Group / Channel\n\n"
            "Send the link or @username:\n\n"
            "• Private: https://t.me/+xxxxxxxx\n"
            "• Public:  @username\n\n"
            "Type /cancel to abort.",
        )
        return VERIFY_LINK

    return MAIN_MENU

# Add Account: Phone

async def hdl_phone(update: Update, context: ContextTypes.DEFAULT_TYPE):
    phone = update.message.text.strip()
    if not phone.startswith("+"):
        await update.message.reply_text(
            "❌ Include country code, e.g. +917xxxxxxxxx",
            parse_mode=ParseMode.HTML,
        )
        return ADD_PHONE

    msg = await update.message.reply_text("📤 Sending OTP…")
    try:
        code_hash = await ub_send_code(phone)
        context.user_data["phone"]     = phone
        context.user_data["code_hash"] = code_hash
        await safe_edit_with_fallback(
            msg,
            f"✅ OTP sent to {e(phone)}\n\n"
            "Enter the code you received (spaces OK):",
        )
        return ADD_OTP
    except Exception as ex:
        await safe_edit_with_fallback(
            msg, f"❌ Failed: {e(str(ex))}\n\nTry again or /cancel",
        )
        return ADD_PHONE

# Add Account: OTP

async def hdl_otp(update: Update, context: ContextTypes.DEFAULT_TYPE):
    code      = update.message.text.strip().replace(" ", "")
    phone     = context.user_data.get("phone")
    code_hash = context.user_data.get("code_hash")
    msg       = await update.message.reply_text("⏳ Verifying OTP…")

    try:
        await ub_sign_in(phone, code, code_hash)
        me       = await userbot_client.get_me()
        fullname = e(me.first_name) + (" " + e(me.last_name) if me.last_name else "")
        uname    = f"@{e(me.username)}" if me.username else "N/A"
        await safe_edit_with_fallback(
            msg,
            f"✅ Account linked!\n\n"
            f"👤 {fullname}\n"
            f"📱 {e(me.phone)}\n"
            f"🆔 {me.id}\n"
            f"🔗 {uname}",
            main_keyboard(),
        )
        return MAIN_MENU

    except errors.SessionPasswordNeededError:
        await safe_edit_with_fallback(
            msg,
            "🔐 2FA Required\n\nEnter your Two-Factor Authentication password:",
        )
        return ADD_2FA

    except (errors.PhoneCodeInvalidError, errors.PhoneCodeExpiredError):
        await safe_edit_with_fallback(msg, "❌ Wrong or expired OTP. Try again:")
        return ADD_OTP

    except Exception as ex:
        await safe_edit_with_fallback(
            msg, f"❌ Error: {e(str(ex))}\n\nTry again or /cancel",
        )
        return ADD_OTP

# Add Account: 2FA

async def hdl_2fa(update: Update, context: ContextTypes.DEFAULT_TYPE):
    pw  = update.message.text.strip()
    msg = await update.message.reply_text("⏳ Verifying 2FA…")
    try:
        await ub_sign_in_2fa(pw)
        me    = await userbot_client.get_me()
        uname = f"@{e(me.username)}" if me.username else "N/A"
        await safe_edit_with_fallback(
            msg,
            f"✅ Account linked!\n\n"
            f"👤 {e(me.first_name)}\n"
            f"📱 {e(me.phone)}\n"
            f"🔗 {uname}",
            main_keyboard(),
        )
        return MAIN_MENU
    except errors.PasswordHashInvalidError:
        await safe_edit_with_fallback(msg, "❌ Wrong 2FA password. Try again:")
        return ADD_2FA
    except Exception as ex:
        await safe_edit_with_fallback(
            msg, f"❌ Error: {e(str(ex))}\n\nTry again or /cancel",
        )
        return ADD_2FA

# ══════════════════════════════════════════════════════════
# VERIFY GROUP / CHANNEL  (Updated with Image Detection)
# ══════════════════════════════════════════════════════════

async def hdl_verify(update: Update, context: ContextTypes.DEFAULT_TYPE):
    link     = update.message.text.strip()

    # Detect if a specific message link was given (e.g. t.me/channel/123)
    # Extract message ID → fetch from that point going forward
    start_msg_id = 0
    msg_id_match = re.search(r't\.me/(?:c/\d+|\w+)/(\d+)', link)
    if msg_id_match:
        start_msg_id = int(msg_id_match.group(1))
        # Strip message ID from link so join logic works cleanly
        link = re.sub(r'/(\d+)$', '', link).strip()

    mode_note = (
        f"⏳ [1/5] Joining & fetching from message #{start_msg_id} onwards…"
        if start_msg_id else
        "⏳ [1/5] Joining group/channel…"
    )
    progress = await update.message.reply_text(
        f"🔍 Analysis Started…\n\n{mode_note}",
        parse_mode=ParseMode.HTML,
    )

    # Step 1: Join & Fetch
    try:
        entity, ch_info, messages, msg_links, image_list = await ub_join_and_fetch(link, start_msg_id)
    except Exception as ex:
        await safe_edit_with_fallback(
            progress,
            f"❌ Error: {e(str(ex))}\n\nSend another link or /cancel",
        )
        return VERIFY_LINK

    img_note = f" + {len(image_list)} photo(s)" if image_list else ""
    await safe_edit_with_fallback(
        progress,
        f"🔍 Analysis In Progress…\n\n"
        f"✅ [1/5] Joined: {e(ch_info['title'])}\n"
        f"⏳ [2/5] Fetched {len(messages)} messages{img_note} — running AI…",
    )

    # Step 2 & 3: Text + Image analysis in parallel
    text_task  = asyncio.create_task(analyze_illegality(messages, ch_info))
    image_task = asyncio.create_task(analyze_all_images(image_list)) if image_list else None

    analysis = await text_task
    illegal_image_results: List[dict] = await image_task if image_task else []

    await safe_edit_with_fallback(
        progress,
        f"🔍 Analysis In Progress…\n\n"
        f"✅ [1/5] Joined: {e(ch_info['title'])}\n"
        f"✅ [2/5] {len(messages)} messages fetched{img_note}\n"
        f"✅ [3/5] Text AI analysis done\n"
        f"✅ [4/5] Image AI analysis done ({len(illegal_image_results)} flagged)\n"
        f"⏳ [5/5] Compiling report…",
    )

    context.user_data["last_analysis"]         = analysis
    context.user_data["last_ch_info"]           = ch_info
    context.user_data["last_link"]              = link
    context.user_data["last_msg_links"]         = msg_links
    context.user_data["illegal_image_results"]  = illegal_image_results

    # Build illegal text message links
    illegal_indices = analysis.get("illegal_message_indices", [])
    illegal_links   = [
        msg_links[i]
        for i in illegal_indices
        if isinstance(i, int) and 0 <= i < len(msg_links)
    ]
    context.user_data["illegal_links"] = illegal_links

    is_illegal   = analysis.get("is_illegal", False)
    has_img_viol = bool(illegal_image_results)

    if is_illegal or has_img_viol:
        sev = analysis.get("severity", "MEDIUM")
        sev_icon = {
            "CRITICAL": "🔴",
            "HIGH":     "🟠",
            "MEDIUM":   "🟡",
            "LOW":      "🟢",
        }.get(sev, "🟡")

        violations = analysis.get("violations", [])
        if violations:
            violations_txt = "\n".join(f"  • {e(v)}" for v in violations)
        else:
            violations_txt = "  • See image violations below"

        laws = analysis.get("applicable_laws", [])
        laws_txt = "\n".join(f"  • {e(l)}" for l in laws) if laws else "  • N/A"

        # Illegal text links section
        if illegal_links:
            links_section = (
                f"\n\n🔗 Illegal Evidence Links:\n"
                + "\n".join(
                    f'  {i+1}. <a href="{e(lnk)}">{e(lnk)}</a>'
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
                viols    = ", ".join(r.get("violations", [])) or "Unknown violation"
                conf     = r.get("confidence", "?")
                lnk      = r.get("msg_link", "")
                link_str = f'<a href="{e(lnk)}">View Photo</a>' if lnk else "Private"
                img_lines.append(
                    f"  📸 {link_str} — {e(viols)} [{conf}]"
                )
            img_section = (
                f"\n\n🖼️ Illegal Photos Detected ({len(illegal_image_results)}):\n"
                + "\n".join(img_lines)
            )

        # Suggested report description
        report_desc  = analysis.get("report_description", "").strip()
        desc_section = (
            f"\n\n📝 Suggested Report Description (paste this):\n"
            f"{e(report_desc)}"
        ) if report_desc else ""

        # Telegram report instructions
        tg_cat    = analysis.get("telegram_report_category", "Other")
        tg_subcat = analysis.get("telegram_report_subcategory", "")

        # If image violations exist but no text violation category, pick best category
        if not analysis.get("is_illegal") and illegal_image_results:
            img_viols  = [v for r in illegal_image_results for v in r.get("violations", [])]
            viol_lower = " ".join(img_viols).lower()
            if any(x in viol_lower for x in ["child", "csam", "minor"]):
                tg_cat    = "Child abuse"
                tg_subcat = "Child sexual abuse"
            elif any(x in viol_lower for x in ["porn", "sexual", "nude"]):
                tg_cat    = "Illegal adult content"
                tg_subcat = "Pornography"
            elif any(x in viol_lower for x in ["weapon", "gun", "drug"]):
                tg_cat    = "Illegal goods and services"
                tg_subcat = "Weapons" if "weapon" in viol_lower or "gun" in viol_lower else "Drugs"
            elif any(x in viol_lower for x in ["violence", "gore", "graphic"]):
                tg_cat    = "Violence"
                tg_subcat = "Graphic or disturbing content"
            else:
                tg_cat    = "Other"
                tg_subcat = ""

        report_instructions = format_report_instructions(tg_cat, tg_subcat, report_desc)

        ch_username = ch_info.get("username")
        uname_line  = f"• Username : @{e(ch_username)}\n" if ch_username else ""

        report = (
            f"{sev_icon} ILLEGAL CONTENT DETECTED {sev_icon}\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n\n"
            f"📌 Channel / Group Info\n"
            f"• Title   : {e(ch_info.get('title', 'N/A'))}\n"
            f"{uname_line}"
            f"• Type    : {e(ch_info.get('type', 'N/A').upper())}\n"
            f"• Members : {e(ch_info.get('members_count', 'N/A'))}\n"
            f"• Link    : {e(link)}\n\n"
            f"⚖️ Violations Detected\n{violations_txt}\n\n"
            f"📜 Applicable Laws\n{laws_txt}\n\n"
            f"🎯 Severity: {e(analysis.get('severity','?'))}  |  "
            f"Confidence: {e(analysis.get('confidence','?'))}\n\n"
            f"📋 Detailed Reason\n{e(analysis.get('detailed_reason','N/A'))}"
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
            [InlineKeyboardButton("📧 Generate Legal Report Email", callback_data="gen_email")],
            [
                InlineKeyboardButton("🔍 Verify Another", callback_data="verify_another"),
                InlineKeyboardButton("🏠 Menu",           callback_data="back_main"),
            ],
        ])

        if len(report) > 4000:
            parts = [report[i:i+4000] for i in range(0, len(report), 4000)]
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
            f"✅ Analysis Complete — No Illegal Content Found\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n\n"
            f"📌 {e(ch_info.get('title','Unknown'))}\n"
            f"📊 Messages analysed : {len(messages)}{img_clean_note}\n"
            f"🎯 Confidence        : {e(analysis.get('confidence','N/A'))}\n\n"
            f"📝 AI Summary\n"
            f"{e(analysis.get('summary','Content appears within legal boundaries.'))}\n\n"
            f"⚠️ This is an AI-based analysis. Human judgement is always recommended.\n"
            f"━━━━━━━━━━━━━━━━━━━━━━"
        )
        kb = InlineKeyboardMarkup([
            [
                InlineKeyboardButton("🔍 Verify Another", callback_data="verify_another"),
                InlineKeyboardButton("🏠 Menu",           callback_data="back_main"),
            ],
        ])
        await safe_edit_with_fallback(progress, clean_report, kb)

    return MAIN_MENU

# Chat with AI

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
    response  = await call_ai(history, LEGAL_SYSTEM)

    history.append({"role": "assistant", "content": response})
    context.user_data["chat_history"] = history[-30:]

    kb = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("🔍 Verify Channel", callback_data="verify"),
            InlineKeyboardButton("🏠 Menu",           callback_data="back_main"),
        ]
    ])
    await thinking.delete()
    await split_send(update.message, e(response), kb)
    return CHAT_AI

# Report Email

async def hdl_email(update: Update, context: ContextTypes.DEFAULT_TYPE):
    reporter_email = update.message.text.strip()
    analysis       = context.user_data.get("last_analysis",  {})
    ch_info        = context.user_data.get("last_ch_info",   {})
    link           = context.user_data.get("last_link",      "")
    illegal_links  = context.user_data.get("illegal_links",  [])
    ch_info["link"] = link

    wait_msg = await update.message.reply_text("⏳ Generating professional legal email…")

    email_body = await generate_legal_email(ch_info, analysis, reporter_email, illegal_links)

    header = (
        "📧 Professional Legal Report Email\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n\n"
    )

    ev_block = ""
    if illegal_links:
        ev_block = (
            "\n\n🔗 Illegal Message Evidence Links:\n"
            + "\n".join(
                f'  {i+1}. <a href="{e(lnk)}">{e(lnk)}</a>'
                for i, lnk in enumerate(illegal_links[:10])
            )
            + "\n"
        )

    footer = (
        f"{ev_block}\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"💡 Next Steps:\n"
        f"1. Copy &amp; send to abuse@telegram.org\n"
        f"2. Also report via in-app → ⋮ → Report\n"
        f"3. Keep a copy for your records\n"
        f"4. For CRITICAL cases, also report to local cyber-crime police"
    )

    full = header + e(email_body) + footer

    kb = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("🔍 Verify Another", callback_data="verify_another"),
            InlineKeyboardButton("🏠 Menu",           callback_data="back_main"),
        ]
    ])
    await wait_msg.delete()
    await split_send(update.message, full, kb)
    return MAIN_MENU

# Cancel

async def cmd_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "↩️ Cancelled. Back to main menu.", reply_markup=main_keyboard(),
    )
    return MAIN_MENU

# ══════════════════════════════════════════════════════════
# POST-INIT
# ══════════════════════════════════════════════════════════

async def post_init(application: Application) -> None:
    await init_userbot()

# ══════════════════════════════════════════════════════════
# MAIN  — concurrent_updates=True  ← KEY FIX
# ══════════════════════════════════════════════════════════

def main() -> None:
    logger.info("🚀 Initialising bot…")

    app = (
        Application.builder()
        .token(BOT_TOKEN)
        .concurrent_updates(True)       # ✅ FIX: All users handled simultaneously
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
            MAIN_MENU:    [CallbackQueryHandler(cb_router), *sudo_cmds],
            ADD_PHONE:    [MessageHandler(filters.TEXT & ~filters.COMMAND, hdl_phone),    CallbackQueryHandler(cb_router), *sudo_cmds],
            ADD_OTP:      [MessageHandler(filters.TEXT & ~filters.COMMAND, hdl_otp),      CallbackQueryHandler(cb_router), *sudo_cmds],
            ADD_2FA:      [MessageHandler(filters.TEXT & ~filters.COMMAND, hdl_2fa),      CallbackQueryHandler(cb_router), *sudo_cmds],
            VERIFY_LINK:  [MessageHandler(filters.TEXT & ~filters.COMMAND, hdl_verify),   CallbackQueryHandler(cb_router), *sudo_cmds],
            CHAT_AI:      [MessageHandler(filters.TEXT & ~filters.COMMAND, hdl_chat),     CallbackQueryHandler(cb_router), *sudo_cmds],
            REPORT_EMAIL: [MessageHandler(filters.TEXT & ~filters.COMMAND, hdl_email),    CallbackQueryHandler(cb_router), *sudo_cmds],
        },
        fallbacks=[
            CommandHandler("cancel", cmd_cancel),
            CommandHandler("start",  cmd_start),
            *sudo_cmds,
        ],
        allow_reentry=True,
        conversation_timeout=600,
        per_message=False,
    )

    app.add_handler(conv)

    for handler in sudo_cmds:
        app.add_handler(handler, group=1)

    logger.info("✅ Bot is running! Press Ctrl+C to stop.")
    app.run_polling(drop_pending_updates=True)


# ✅ FIX: Correct __name__ check (was: name == "main")
if __name__ == "__main__":
    main()
