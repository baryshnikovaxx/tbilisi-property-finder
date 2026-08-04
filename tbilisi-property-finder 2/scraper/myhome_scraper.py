"""
Полный скрапер myhome.ge под критерии инвестиционного поиска (Тбилиси).

Технология: сайт на Next.js с SSR — весь JSON объявления уже лежит в теге
<script id="__NEXT_DATA__"> на HTML-странице. Playwright/Selenium не нужны,
достаточно requests + разбор JSON. Разобрано и проверено вручную на
реальных данных сайта (см. историю сессии / README).

Что делает скрипт:
1. Для каждого района из districts.DISTRICTS проходит по страницам списка
   объявлений (сортировка "по дате", свежие сверху) с фильтром "продажа,
   квартира, цена в USD".
2. Локально (без доп. запросов) отфильтровывает по площади/спальням/этажу —
   URL-фильтры сайта на площадь ненадёжны (см. заметку в конце файла).
3. Для объектов, прошедших фильтр, дополнительно запрашивает страницу
   объекта — там появляются координаты (lat/lng) и полное описание,
   которых нет в списке.
4. Дедуплицирует по (координаты, цена, площадь) — часто один и тот же
   объект выложен от разных агентов.
5. Сохраняет результат в БД (см. db/db.py) или в CSV, если БД не настроена.

Использование:
    python myhome_scraper.py --price-to 200000 --min-area 55 --max-area 80 \
        --min-bedrooms 2 --not-first-floor --max-pages 5

ВАЖНО: запускать с обычным интернет-доступом (локальная машина, VPS,
GitHub Actions runner) — не в песочнице с ограниченной сетью.
"""

import argparse
import csv
import json
import os
import random
import re
import sys
import time

import requests

sys.path.insert(0, os.path.dirname(__file__))
from districts import DISTRICTS  # noqa: E402

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/120.0 Safari/537.36",
    "Accept-Language": "en",
}

NEXT_DATA_RE = re.compile(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', re.DOTALL)

BASE = "https://www.myhome.ge/en/real-estate/sale/apartment/tbilisi/{slug}/"


def fetch_next_data(url, session, retries=3, timeout=20):
    last_err = None
    for attempt in range(retries):
        try:
            r = session.get(url, headers=HEADERS, timeout=timeout)
            r.raise_for_status()
            m = NEXT_DATA_RE.search(r.text)
            if not m:
                raise ValueError("__NEXT_DATA__ не найден на странице")
            return json.loads(m.group(1))
        except Exception as e:  # noqa: BLE001
            last_err = e
            time.sleep(1.5 * (attempt + 1))
    raise RuntimeError(f"не удалось загрузить {url}: {last_err}")


def find_query(dehydrated_state, key0, key1):
    for q in dehydrated_state["queries"]:
        if q["queryKey"][0] == key0 and q["queryKey"][1] == key1:
            return q["state"]["data"]
    return None


def list_page_items(district_slug, urban_id, district_id, price_to, page, session):
    url = (
        BASE.format(slug=district_slug)
        + f"?deal_types=1&real_estate_types=1&currency_id=2&price_to={price_to}"
        + f"&CardView=1&cities=1&urbans={urban_id}&districts={district_id}&page={page}"
    )
    data = fetch_next_data(url, session)
    state = data["props"]["pageProps"]["dehydratedState"]
    listing_data = find_query(state, "statements", "list")
    if not listing_data:
        return []
    return listing_data["data"]["data"]


def passes_local_filters(item, args):
    area = item.get("area")
    bedroom = item.get("bedroom")
    floor = item.get("floor")
    if area is None or not (args.min_area <= area <= args.max_area):
        return False
    try:
        if bedroom is not None and int(bedroom) < args.min_bedrooms:
            return False
    except (TypeError, ValueError):
        pass
    if args.not_first_floor and (floor is None or floor <= 1):
        return False
    return True


def fetch_detail(item_id, slug, session):
    url = f"https://www.myhome.ge/en/real-estate/{item_id}/{slug}/"
    data = fetch_next_data(url, session)
    state = data["props"]["pageProps"]["dehydratedState"]
    detail_data = find_query(state, "statements", "details")
    if not detail_data:
        return {}
    it = detail_data["data"]["statement"]
    return {
        "lat": it.get("lat"),
        "lng": it.get("lng"),
        "condition_id": it.get("condition_id"),
        "comment": re.sub(r"<[^>]+>", " ", it.get("comment") or "").strip(),
    }


def dedupe(rows):
    seen = {}
    out = []
    for r in rows:
        r.setdefault("duplicate_of", None)
        key = (
            round(r["lat"], 5) if r.get("lat") else None,
            round(r["lng"], 5) if r.get("lng") else None,
            r["price_usd"],
            r["area_m2"],
        )
        if key in seen and key[0] is not None:
            r["duplicate_of"] = seen[key]
            r["is_duplicate"] = True
        else:
            seen[key] = r["id"]
            r["is_duplicate"] = False
        out.append(r)
    return out


def scrape(args):
    session = requests.Session()
    all_rows = []

    for key, d in DISTRICTS.items():
        print(f"== {d['label']} ==")
        page = 1
        while page <= args.max_pages:
            items = list_page_items(d["slug"], d["urban_id"], d["district_id"], args.price_to, page, session)
            if not items:
                break
            matched = [it for it in items if passes_local_filters(it, args)]
            print(f"  стр.{page}: {len(items)} объявлений, {len(matched)} подходят по фильтрам")

            for it in matched:
                price_obj = (it.get("price") or {}).get("2", {})
                row = {
                    "id": it["id"],
                    "district_label": d["label"],
                    "urban": it.get("urban_name"),
                    "address": it.get("address"),
                    "price_usd": price_obj.get("price_total"),
                    "price_sqm_usd": price_obj.get("price_square"),
                    "area_m2": it.get("area"),
                    "rooms": it.get("room"),
                    "bedrooms": it.get("bedroom"),
                    "floor": it.get("floor"),
                    "total_floors": it.get("total_floors"),
                    "slug": it.get("dynamic_slug"),
                    "url": f"https://www.myhome.ge/en/real-estate/{it['id']}/{it.get('dynamic_slug')}/",
                }
                time.sleep(random.uniform(0.8, 1.8))
                try:
                    row.update(fetch_detail(it["id"], it.get("dynamic_slug"), session))
                except Exception as e:  # noqa: BLE001
                    print(f"    предупреждение: не удалось получить детали {it['id']}: {e}")
                for k in ("lat", "lng", "condition_id", "comment"):
                    row.setdefault(k, None)
                all_rows.append(row)

            page += 1
            time.sleep(random.uniform(1.0, 2.0))

    all_rows = dedupe(all_rows)
    return all_rows


def save_csv(rows, path):
    if not rows:
        print("Нет данных для сохранения.")
        return
    fieldnames = list(rows[0].keys())
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)
    print(f"Сохранено в {path}: {len(rows)} объектов")


def save_db(rows):
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "db"))
    from db import upsert_listings  # noqa: E402 (локальный импорт, чтобы CSV-режим работал без psycopg2)
    upsert_listings(rows)
    print(f"Сохранено в БД: {len(rows)} объектов")


def parse_args():
    p = argparse.ArgumentParser()
    p.add_argument("--price-to", type=int, default=200000)
    p.add_argument("--min-area", type=float, default=55)
    p.add_argument("--max-area", type=float, default=80)
    p.add_argument("--min-bedrooms", type=int, default=2)
    p.add_argument("--not-first-floor", action="store_true", default=True)
    p.add_argument("--max-pages", type=int, default=10, help="макс. страниц на район (по 20 объявлений)")
    p.add_argument("--out", default="listings.csv", help="путь для CSV, если БД не настроена")
    p.add_argument("--db", action="store_true", help="сохранить в Postgres/Supabase вместо CSV (нужен DATABASE_URL)")
    return p.parse_args()


if __name__ == "__main__":
    args = parse_args()
    rows = scrape(args)
    if args.db and os.environ.get("DATABASE_URL"):
        save_db(rows)
    else:
        save_csv(rows, args.out)

# ЗАМЕТКА ПРО URL-ФИЛЬТРЫ САЙТА:
# При ручном тестировании выяснилось, что параметры area_from/area_to в URL
# применяются сайтом ненадёжно без сопутствующего area_types=1, а сам
# параметр иногда просто игнорируется при прямой навигации (SPA держит
# часть состояния фильтра в клиенте). Поэтому здесь площадь/спальни/этаж
# фильтруются локально после получения полного списка — это медленнее,
# но гарантированно корректно.
