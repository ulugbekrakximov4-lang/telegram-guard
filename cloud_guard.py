#!/usr/bin/env python3
"""Telegram Guard — Railway cloud versiyasi (env vars orqali)"""

import asyncio
import json
import os
import sys

from telethon import TelegramClient, events
from telethon.sessions import StringSession
from telethon.tl.functions.contacts import BlockRequest
from telethon.tl.functions.messages import ReportSpamRequest

import anthropic as anthropic_sdk

# Environment variables dan o'qish
API_ID        = int(os.environ["API_ID"])
API_HASH      = os.environ["API_HASH"]
SESSION_STR   = os.environ["SESSION_STRING"]
ANTHROPIC_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
AUTO_REPLY_TEXT = os.environ.get(
    "AUTO_REPLY_TEXT",
    "Siz mening kontaktlarimda yo'qsiz. Xabaringizni ko'raman."
)

ai_client = anthropic_sdk.AsyncAnthropic(api_key=ANTHROPIC_KEY) if ANTHROPIC_KEY else None

warned: set[int] = set()
whitelist: set[int] = set()
pending: dict[int, object] = {}
user_started: set[int] = set()

BAD_KEYWORDS = [
    "xxx", "porn", "порно", "секс", "nude", "naked", "onlyfans", "18+",
    "интим", "эротика", "sex video", "kazino", "casino", "казино",
    "stavka", "ставка", "100% daromad", "быстрые деньги", "легкие деньги",
    "криптовалюта зарабат", "free bitcoin", "o'ldiraman", "урою", "убью",
]


async def ai_analyze(text: str) -> dict:
    if not ai_client or not text.strip():
        return {"category": "UNKNOWN"}
    try:
        resp = await ai_client.messages.create(
            model="claude-haiku-5-5",
            max_tokens=128,
            system=(
                "Telegram xabarini tahlil qil. Faqat JSON qaytар:\n"
                "{\"category\": \"SPAM\" | \"INAPPROPRIATE\" | \"NORMAL\", "
                "\"reason\": \"sabab (qisqa, o'zbek tilida)\"}\n"
                "SPAM: reklama, firibgarlik, lotereya takliflari.\n"
                "INAPPROPRIATE: 18+ kontent, tahdid, haqorat.\n"
                "NORMAL: oddiy suhbat."
            ),
            messages=[{"role": "user", "content": f"Xabar: {text[:400]}"}],
        )
        return json.loads(resp.content[0].text)
    except Exception:
        return {"category": "UNKNOWN"}


def keyword_check(text: str) -> str | None:
    lower = text.lower()
    for kw in BAD_KEYWORDS:
        if kw in lower:
            return kw
    return None


async def should_block(text: str) -> tuple[bool, str]:
    if ai_client:
        result = await ai_analyze(text)
        cat = result.get("category", "UNKNOWN")
        reason = result.get("reason", "")
        if cat in ("SPAM", "INAPPROPRIATE"):
            return True, f"AI: {cat} — {reason}"
        return False, ""
    kw = keyword_check(text)
    if kw:
        return True, f"Kalit so'z: `{kw}`"
    return False, ""


async def main():
    global whitelist, warned

    client = TelegramClient(StringSession(SESSION_STR), API_ID, API_HASH)

    print("[Guard] Ulanmoqda...")
    await client.connect()
    me = await client.get_me()
    print(f"[OK] Ulandi: {me.first_name} @{me.username or ''}")

    ai_status = "Claude AI ✅" if ai_client else "Kalit so'z rejimi ⚠️"
    await client.send_message("me",
        f"✅ **Telegram Guard (Cloud) ishga tushdi!**\n"
        f"🤖 Rejim: {ai_status}\n\n"
        "Buyruqlar:\n"
        "`/block ID`  `/report ID`  `/allow ID`  `/list`"
    )

    async def send_block(sender, name, ustr, is_contact, reason, preview=""):
        who = "Kontakt" if is_contact else "Notanish"
        try:
            await client.send_message(sender,
                "Kechirasiz, bu Telegram foydalanuvchisi pornografik "
                "va nomaqbul materiallardan yiroq. ❌"
            )
        except Exception:
            pass
        peer = await client.get_input_entity(sender)
        await client(ReportSpamRequest(peer))
        await client(BlockRequest(sender))
        await client.delete_dialog(sender)
        if is_contact:
            whitelist.discard(sender.id)
        msg = (
            f"🚨 **Avtomatik bloklandi!**\n\n"
            f"👤 {name} ({ustr}) [{who}]\n"
            f"🆔 ID: `{sender.id}`\n"
            f"⚠️ Sabab: {reason}\n"
        )
        if preview:
            msg += f"💬 {preview}\n"
        msg += "\n_Spam reportlandi, bloklandi va chat o'chirildi._"
        await client.send_message("me", msg)

    @client.on(events.NewMessage(outgoing=True, func=lambda e: e.is_private))
    async def on_outgoing(event):
        uid = getattr(event.peer_id, "user_id", None)
        if uid and uid != me.id:
            user_started.add(uid)

    @client.on(events.NewMessage(incoming=True, func=lambda e: e.is_private))
    async def on_incoming(event):
        sender = await event.get_sender()
        uid: int = sender.id
        if uid == me.id:
            return

        is_contact = uid in whitelist or getattr(sender, "contact", False)
        if is_contact and uid not in whitelist:
            whitelist.add(uid)

        first = getattr(sender, "first_name", "") or ""
        last  = getattr(sender, "last_name", "") or ""
        name  = f"{first} {last}".strip() or "Nomsiz"
        username = getattr(sender, "username", None)
        ustr  = f"@{username}" if username else f"ID:{uid}"
        msg_text = event.message.text or ""
        preview  = msg_text[:120] or "[media/fayl]"

        media = event.message.media
        mime  = getattr(getattr(media, "document", None), "mime_type", "") or ""
        is_sticker = mime == "image/webp"
        is_photo   = hasattr(media, "photo") and media.photo is not None
        is_video   = mime.startswith("video/")
        is_gif     = mime == "image/gif"

        if media and (is_sticker or is_photo or is_video or is_gif) and not msg_text:
            await send_block(sender, name, ustr, is_contact,
                             f"Media ({mime or 'fayl'}) yubordi")
            return

        if msg_text:
            block, reason = await should_block(msg_text)
            if block:
                if is_contact and uid not in warned:
                    warned.add(uid)
                    await client.send_message(sender,
                        "Siz mening tanishim bo'lganingiz uchun bu safar kechiraman. "
                        "Takrorlansa, spam deb hisoblab bloklaymen. ❌"
                    )
                    await client.send_message("me",
                        f"⚠️ **Kontaktga ogohlantirish**\n\n"
                        f"👤 {name} ({ustr})\n"
                        f"⚠️ Sabab: {reason}\n"
                        f"💬 {preview}\n\n_Takrorlasa — avtomatik bloklaydi._"
                    )
                    return
                await send_block(sender, name, ustr, is_contact, reason, preview)
                return

        if is_contact:
            return

        pending[uid] = sender
        await client.send_message("me",
            f"🔔 **Notanish xabar!**\n\n"
            f"👤 {name} ({ustr})\n🆔 ID: `{uid}`\n💬 {preview}\n\n"
            f"`/block {uid}`\n`/report {uid}`\n`/allow {uid}`"
        )
        await asyncio.sleep(1)
        await event.reply(AUTO_REPLY_TEXT)

    @client.on(events.NewMessage(outgoing=True, func=lambda e: e.is_private))
    async def on_cmd(event):
        if getattr(event.peer_id, "user_id", None) != me.id:
            return
        text = (event.message.text or "").strip()
        parts = text.split()
        if not parts or not parts[0].startswith("/"):
            return
        action = parts[0].lower()

        if action == "/list":
            msg = "📋 **Kutayotganlar:**\n" + (
                "".join(f"• {getattr(s,'first_name','') or 'Nomsiz'} — `{uid}`\n"
                        for uid, s in pending.items())
                or "Hozircha hech kim yo'q."
            )
            await client.send_message("me", msg)

        elif action in ("/block", "/allow", "/report") and len(parts) > 1:
            try:
                uid2 = int(parts[1])
            except ValueError:
                return
            target = pending.get(uid2)
            if action == "/allow":
                whitelist.add(uid2)
                pending.pop(uid2, None)
                await client.send_message("me", f"✅ {uid2} ruxsat ro'yxatiga qo'shildi.")
            elif action == "/block" and target:
                await client(BlockRequest(target))
                await client.delete_dialog(target)
                pending.pop(uid2, None)
                await client.send_message("me", f"🚫 {uid2} bloklandi.")
            elif action == "/report" and target:
                peer = await client.get_input_entity(target)
                await client(ReportSpamRequest(peer))
                await client(BlockRequest(target))
                await client.delete_dialog(target)
                pending.pop(uid2, None)
                await client.send_message("me", f"🚨 {uid2} reportlandi va bloklandi.")

    print("[Guard] Tayyor. Xabarlar kuzatilmoqda...")
    await client.run_until_disconnected()


if __name__ == "__main__":
    asyncio.run(main())
