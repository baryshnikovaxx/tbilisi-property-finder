"""
Слой сохранения в Postgres/Supabase. Использует переменную окружения
DATABASE_URL (в Supabase: Project Settings -> Database -> Connection string
-> URI, режим "Session pooler" для внешних подключений типа GitHub Actions).

Требует psycopg2-binary (см. requirements.txt). Если библиотека не
установлена или DATABASE_URL не задан — скрапер сам переключится на CSV,
см. myhome_scraper.py.
"""

import json
import os

import psycopg2
import psycopg2.extras


def get_conn():
    url = os.environ["DATABASE_URL"]
    return psycopg2.connect(url)


UPSERT_SQL = """
insert into listings (
    id, district_label, urban, address, price_usd, price_sqm_usd, area_m2,
    rooms, bedrooms, floor, total_floors, lat, lng, condition_id, comment,
    url, is_duplicate, duplicate_of, last_seen, is_active
) values (
    %(id)s, %(district_label)s, %(urban)s, %(address)s, %(price_usd)s,
    %(price_sqm_usd)s, %(area_m2)s, %(rooms)s, %(bedrooms)s, %(floor)s,
    %(total_floors)s, %(lat)s, %(lng)s, %(condition_id)s, %(comment)s,
    %(url)s, %(is_duplicate)s, %(duplicate_of)s, now(), true
)
on conflict (id) do update set
    price_usd = excluded.price_usd,
    price_sqm_usd = excluded.price_sqm_usd,
    lat = excluded.lat,
    lng = excluded.lng,
    comment = excluded.comment,
    is_duplicate = excluded.is_duplicate,
    duplicate_of = excluded.duplicate_of,
    last_seen = now(),
    is_active = true;
"""

PRICE_HISTORY_SQL = """
insert into price_history (listing_id, price_usd)
select %(id)s, %(price_usd)s
where not exists (
    select 1 from price_history
    where listing_id = %(id)s and price_usd = %(price_usd)s
    order by observed_at desc limit 1
);
"""

MARK_INACTIVE_SQL = """
update listings set is_active = false
where last_seen < now() - interval '3 days' and is_active = true;
"""


def upsert_listings(rows):
    if not rows:
        return
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            for r in rows:
                r = {**r, "duplicate_of": r.get("duplicate_of")}
                cur.execute(UPSERT_SQL, r)
                cur.execute(PRICE_HISTORY_SQL, r)
            # объявления, которые не встретились в этом прогоне 3+ дня подряд,
            # считаем снятыми с продажи (грубая прокси-метрика для ликвидности)
            cur.execute(MARK_INACTIVE_SQL)
        conn.commit()
    finally:
        conn.close()


def fetch_active_listings():
    conn = get_conn()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("select * from listings where is_active = true;")
            return cur.fetchall()
    finally:
        conn.close()


def fetch_new_listings():
    """Активные объекты, о которых ещё не было уведомления (уведомление
    высылается один раз, дальше объект живёт только через price-drop алерты
    или интерактивного бота)."""
    conn = get_conn()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                "select * from listings where is_active = true and notified_at is null "
                "order by score desc nulls last;"
            )
            return cur.fetchall()
    finally:
        conn.close()


def fetch_price_drops(min_drop_pct=3.0):
    """Активные объекты, о которых уже сообщали, но цена с тех пор упала
    минимум на min_drop_pct% от цены на момент последнего уведомления."""
    conn = get_conn()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                "select * from listings where is_active = true and notified_at is not null "
                "and last_notified_price is not null "
                "and price_usd < last_notified_price * (1 - %s / 100.0) "
                "order by score desc nulls last;",
                (min_drop_pct,),
            )
            return cur.fetchall()
    finally:
        conn.close()


def mark_notified(rows):
    """rows: список dict с id и price_usd — фиксирует, что уведомление
    отправлено, и запоминает цену на этот момент (база для будущих
    price-drop алертов)."""
    if not rows:
        return
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            for r in rows:
                cur.execute(
                    "update listings set notified_at = now(), last_notified_price = %s where id = %s",
                    (r["price_usd"], r["id"]),
                )
        conn.commit()
    finally:
        conn.close()


def fetch_disliked_ids():
    """Все id объектов, которые хоть один пользователь бота пометил 👎 —
    исключаем их из будущих рассылок."""
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("select distinct listing_id from user_feedback where action = 'dislike';")
            return {row[0] for row in cur.fetchall()}
    finally:
        conn.close()


def update_scores(scored_rows):
    """scored_rows: список dict с ключами id, score, dist_to_round_garden_km, data_quality_flags"""
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            for r in scored_rows:
                cur.execute(
                    "update listings set score=%s, dist_to_round_garden_km=%s, data_quality_flags=%s where id=%s",
                    (r["score"], r["dist_to_round_garden_km"], json.dumps(r["data_quality_flags"]), r["id"]),
                )
        conn.commit()
    finally:
        conn.close()
