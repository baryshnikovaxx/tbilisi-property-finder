"""
Отправляет в Telegram (1) новые объекты, о которых ещё не сообщали, и
(2) алерты на снижение цены по уже виденным объектам — через обычный Bot
API, без сторонних библиотек, только requests. Каждое сообщение с объектом
несёт инлайн-кнопки 👍/👎 (реакция сохраняется в user_feedback и учитывается
при следующих рассылках и в интерактивном боте, см. webhook/worker.js).

Нужны переменные окружения:
    TELEGRAM_BOT_TOKEN — токен от @BotFather
    TELEGRAM_CHAT_ID   — твой chat_id (см. README, раздел "Telegram")

Использование (после score.py --db, который проставляет score в БД):
    python notify_telegram.py --min-score 20 --limit 8
"""

import argparse
import os
import sys

import requests

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "db"))
from db import fetch_new_listings, fetch_price_drops, mark_notified, fetch_disliked_ids  # noqa: E402


def send_message(token, chat_id, text, reply_markup=None):
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": False,
    }
    if reply_markup:
        payload["reply_markup"] = reply_markup
    resp = requests.post(url, json=payload, timeout=15)
    resp.raise_for_status()
    return resp.json()


def listing_keyboard(listing_id):
    return {
        "inline_keyboard": [[
            {"text": "👍 Нравится", "callback_data": f"like:{listing_id}"},
            {"text": "👎 Не интересно", "callback_data": f"dislike:{listing_id}"},
        ]]
    }


def format_listing(row):
    flags = row.get("data_quality_flags") or []
    flags_txt = "".join(f"\n⚠ {f}" for f in flags)
    dist = row.get("dist_to_round_garden_km")
    dist_txt = f"{dist:.2f} км до Круглого сада" if dist is not None else "нет координат"
    return (
        f"<b>{row.get('district_label')}</b> — ${float(row['price_usd']):,.0f} "
        f"({row.get('price_sqm_usd')}$/м², {row.get('area_m2')} м², "
        f"{row.get('floor')}/{row.get('total_floors')} эт.)\n"
        f"{row.get('address', '')}\n"
        f"{dist_txt}\n"
        f"Score: {row.get('score')}{flags_txt}\n"
        f"{row.get('url')}"
    )


def format_price_drop(row):
    old = float(row["last_notified_price"])
    new = float(row["price_usd"])
    pct = (old - new) / old * 100
    return (
        f"📉 <b>Подешевело на {pct:.0f}%</b> (${old:,.0f} → ${new:,.0f})\n"
        + format_listing(row)
    )


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--min-score", type=float, default=15)
    p.add_argument("--limit", type=int, default=8)
    p.add_argument("--min-drop-pct", type=float, default=3.0)
    args = p.parse_args()

    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        print("TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID не заданы — пропускаю отправку.")
        return

    disliked = fetch_disliked_ids()
    sent_count = 0

    # 1. Новые объекты, о которых ещё не сообщали.
    new_rows = fetch_new_listings()
    new_rows = [r for r in new_rows if r.get("score") is not None and r["score"] >= args.min_score]
    new_rows = [r for r in new_rows if r["id"] not in disliked]
    new_rows = new_rows[: args.limit]

    if new_rows:
        send_message(token, chat_id, f"🏠 Новых объектов по критериям: {len(new_rows)}")
        for row in new_rows:
            send_message(token, chat_id, format_listing(row), listing_keyboard(row["id"]))
            sent_count += 1
        mark_notified([{"id": r["id"], "price_usd": r["price_usd"]} for r in new_rows])
    else:
        print("Новых объектов выше порога нет.")

    # 2. Алерты на снижение цены по уже виденным объектам.
    drop_rows = fetch_price_drops(args.min_drop_pct)
    drop_rows = [r for r in drop_rows if r["id"] not in disliked]

    if drop_rows:
        for row in drop_rows:
            send_message(token, chat_id, format_price_drop(row), listing_keyboard(row["id"]))
            sent_count += 1
        mark_notified([{"id": r["id"], "price_usd": r["price_usd"]} for r in drop_rows])
    else:
        print("Снижений цены выше порога нет.")

    print(f"Отправлено {sent_count} сообщений в Telegram "
          f"({len(new_rows)} новых, {len(drop_rows)} по снижению цены).")


if __name__ == "__main__":
    main()
