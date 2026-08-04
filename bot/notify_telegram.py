"""
Отправляет в Telegram топ новых объектов (по score) через обычный Bot API —
без сторонних библиотек, только requests. Проще и надёжнее для разовых
уведомлений из GitHub Actions, чем полноценный бот с polling.

Нужны переменные окружения:
    TELEGRAM_BOT_TOKEN — токен от @BotFather
    TELEGRAM_CHAT_ID   — твой chat_id (см. README, раздел "Telegram")

Использование (после score.py --db, который проставляет score в БД):
    python notify_telegram.py --min-score 20 --only-new
"""

import argparse
import os
import sys

import requests

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "db"))
from db import fetch_active_listings  # noqa: E402


def send_message(token, chat_id, text):
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    resp = requests.post(url, json={
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": False,
    }, timeout=15)
    resp.raise_for_status()
    return resp.json()


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


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--min-score", type=float, default=15)
    p.add_argument("--limit", type=int, default=5)
    args = p.parse_args()

    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        print("TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID не заданы — пропускаю отправку.")
        return

    rows = fetch_active_listings()
    rows = [r for r in rows if r.get("score") is not None and r["score"] >= args.min_score]
    rows.sort(key=lambda r: r["score"], reverse=True)
    rows = rows[: args.limit]

    if not rows:
        print("Нет объектов выше порога — уведомление не отправлено.")
        return

    header = f"🏠 Топ {len(rows)} объектов по критериям (score ≥ {args.min_score}):"
    send_message(token, chat_id, header)
    for row in rows:
        send_message(token, chat_id, format_listing(row))

    print(f"Отправлено {len(rows)} объектов в Telegram.")


if __name__ == "__main__":
    main()
