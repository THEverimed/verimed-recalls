"""
Verimed recalls reader — reads the recall channels with the reader Telegram account
and sends new messages to the Verimed Apps Script (Recalls.gs).
Runs on GitHub Actions every ~15 minutes. Secrets needed:
  TG_API_ID, TG_API_HASH, TG_SESSION, APPS_SCRIPT_URL, RECALLS_SECRET
Channels: one per line in channels.txt (@username, t.me link, or numeric id)
"""
import asyncio, base64, io, json, os, sys, time, datetime as dt
import requests
from telethon import TelegramClient
from telethon.sessions import StringSession
from PIL import Image

API_ID = int(os.environ["TG_API_ID"])
API_HASH = os.environ["TG_API_HASH"]
import re
SESSION = re.sub(r"[\s\"'`]", "", os.environ["TG_SESSION"])   # نشيل أي مسافة أو سطر أو علامة تنصيص دخلت وقت النسخ
print("TG_SESSION length:", len(SESSION), "(لازم يكون تقريباً 353–369)")
URL = os.environ["APPS_SCRIPT_URL"]
SECRET = os.environ["RECALLS_SECRET"]
MAX_PER_CHANNEL = int(os.environ.get("MAX_PER_CHANNEL", "300"))


def call(action, payload):
    payload = dict(payload, secret=SECRET)
    for attempt in range(5):                      # لو الموقع مشغول نعيد بعد شوي
        try:
            r = requests.post(URL, json={"action": action, "payload": payload}, timeout=180)
            r.raise_for_status()
            j = r.json()
            if j.get("ok"):
                return j.get("data") or {}
            if j.get("code") != "BUSY":
                raise RuntimeError(f"{action}: {j.get('error')}")
        except (requests.RequestException, ValueError) as e:
            print("retry", action, e)
        time.sleep(15 * (attempt + 1))
    raise RuntimeError(f"{action}: busy after retries")


def jpeg_b64(raw):
    im = Image.open(io.BytesIO(raw)).convert("RGB")
    im.thumbnail((1600, 1600))
    buf = io.BytesIO()
    im.save(buf, "JPEG", quality=80)
    return base64.b64encode(buf.getvalue()).decode()


def channels():
    out = []
    raw = os.environ.get("CHANNELS", "").replace(",", "\n").splitlines()
    if not any(x.strip() for x in raw) and os.path.exists("channels.txt"):
        raw = open("channels.txt", encoding="utf-8").read().splitlines()
    for line in raw:
        line = line.split("#")[0].strip()
        if not line:
            continue
        if "t.me/" in line and "/+" not in line and "joinchat" not in line:
            line = "@" + line.rstrip("/").split("t.me/")[-1].split("/")[0]
        out.append(int(line) if line.lstrip("-").isdigit() else line)
    return out


async def main():
    state = call("recallState", {})
    last = state.get("last", {})
    since = dt.datetime.fromisoformat(state.get("since", "2026-12-01")).replace(tzinfo=dt.timezone(dt.timedelta(hours=3)))
    async with TelegramClient(StringSession(SESSION), API_ID, API_HASH) as client:
        for chan_no, ch in enumerate(channels(), 1):
            try:
                ent = await client.get_entity(ch)
            except Exception as e:
                print("!! cannot open a channel:", type(e).__name__); continue
            chat_id = str(ent.id)
            title = getattr(ent, "title", None) or getattr(ent, "username", "") or chat_id
            min_id = int(last.get(chat_id, 0))
            groups, order = {}, []
            n = 0
            async for m in client.iter_messages(ent, reverse=True, min_id=min_id,
                                                offset_date=None if min_id else since):
                if m.date < since:
                    continue
                n += 1
                if n > MAX_PER_CHANNEL:
                    break
                key = f"g{m.grouped_id}" if m.grouped_id else f"m{m.id}"
                if key not in groups:
                    groups[key] = {"chat_id": chat_id, "channel": title, "msg_id": m.id,
                                   "date": m.date.isoformat(), "text": "", "images": [], "_ids": []}
                    order.append(key)
                g = groups[key]
                g["_ids"].append(m.id)
                g["msg_id"] = max(g["msg_id"], m.id)
                if m.message:
                    g["text"] = (g["text"] + "\n" + m.message).strip()
                if m.photo and len(g["images"]) < 6:
                    try:
                        g["images"].append(jpeg_b64(await client.download_media(m, file=bytes)))
                    except Exception as e:
                        print("img fail", e)
            batch = []
            for k in order:
                g = groups[k]; g.pop("_ids", None)
                if not g["text"] and not g["images"]:
                    continue
                batch.append(g)
                if len(batch) >= 8:
                    call("recallIngest", {"items": batch}); batch = []
            if batch:
                call("recallIngest", {"items": batch})
            print(f"✓ channel {chan_no}: {len(order)} new")

    # التصحيح: نطلبه من الموقع مباشرة لين يخلص (أو يقرب وقت GitHub)
    t0 = time.time()
    while time.time() - t0 < 8 * 60:
        left = call("recallProcess", {}).get("left", 0)
        print("تصحيح — باقي:", left)
        if left == 0:
            break
        if left < 0:          # فيه تصحيح شغال من المؤقت — نستنى شوي
            time.sleep(30)


if __name__ == "__main__":
    asyncio.run(main())
