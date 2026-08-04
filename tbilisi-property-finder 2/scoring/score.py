"""
Скоринг объектов под инвестиционные критерии + проверка качества данных.

Работает в двух режимах:
- из CSV (--csv listings.csv) — для локального прогона / отладки
- из БД (--db, нужен DATABASE_URL) — читает активные объявления, считает
  score и флаги, записывает их обратно в таблицу listings

Логика идентична прототипу, который проверялся вручную на реальной выборке
с myhome.ge (см. историю разработки): близость к Круглому саду, разумный
диапазон цены/м², отсев объектов с битой геопривязкой и дублей.
"""

import argparse
import csv
import json
import os
import sys
from math import radians, sin, cos, sqrt, atan2

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scraper"))
from districts import ROUND_GARDEN  # noqa: E402

MIN_SANE_PRICE_SQM = 900
MAX_SANE_PRICE_SQM = 4500
TBILISI_CENTER = (41.7151, 44.8271)


def haversine_km(lat1, lon1, lat2, lon2):
    R = 6371
    dlat, dlon = radians(lat2 - lat1), radians(lon2 - lon1)
    a = sin(dlat / 2) ** 2 + cos(radians(lat1)) * cos(radians(lat2)) * sin(dlon / 2) ** 2
    return 2 * R * atan2(sqrt(a), sqrt(1 - a))


def data_quality_flags(item, dup_seen):
    flags = []
    lat, lng = item.get("lat"), item.get("lng")
    price_sqm = item.get("price_sqm_usd")

    if price_sqm is not None:
        price_sqm = float(price_sqm)
        if price_sqm < MIN_SANE_PRICE_SQM or price_sqm > MAX_SANE_PRICE_SQM:
            flags.append(f"цена/м² вне диапазона (${price_sqm:.0f}/м²)")
    else:
        flags.append("нет цены за м²")

    if lat is None or lng is None:
        flags.append("координаты отсутствуют")
    elif haversine_km(float(lat), float(lng), *TBILISI_CENTER) > 20:
        flags.append("координаты далеко за пределами Тбилиси — похоже на ошибку геопривязки")

    comment = (item.get("comment") or "").lower()
    urban = (item.get("urban") or item.get("district_label") or "").lower()
    for other_district in ("digomi", "gldani", "isani", "samgori", "saburtalo", "tabakhmela", "рустави"):
        if other_district in comment and other_district not in urban:
            flags.append(f"текст описания упоминает '{other_district}', а не заявленный район")
            break

    if item.get("is_duplicate"):
        flags.append("похоже на дубль другого объявления (совпадают координаты, цена, площадь)")

    return flags


def score_item(item, flags):
    lat, lng = item.get("lat"), item.get("lng")
    dist_to_park = haversine_km(float(lat), float(lng), *ROUND_GARDEN) if lat and lng else None

    pts = 0.0
    reasons = []

    if dist_to_park is not None:
        if dist_to_park <= 0.4:
            pts += 30
            reasons.append(f"~{dist_to_park*1000:.0f} м от Круглого сада")
        elif dist_to_park <= 0.8:
            pts += 20
            reasons.append(f"{dist_to_park:.2f} км от Круглого сада")
        elif dist_to_park <= 1.5:
            pts += 10
            reasons.append(f"{dist_to_park:.2f} км от Круглого сада")
        else:
            reasons.append(f"{dist_to_park:.2f} км от Круглого сада — далеко от желаемой локации")
    else:
        pts -= 10
        reasons.append("нет координат — расстояние до парка не посчитать")

    price_sqm = item.get("price_sqm_usd")
    if price_sqm:
        pts += max(0, 15 - (float(price_sqm) - MIN_SANE_PRICE_SQM) / 150)

    pts -= 25 * len(flags)

    floor, total_floors = item.get("floor"), item.get("total_floors")
    if floor and total_floors and 1 < int(floor) < int(total_floors):
        pts += 5

    return round(pts, 1), reasons, dist_to_park


def run(rows):
    results = []
    for it in rows:
        flags = data_quality_flags(it, None)
        pts, reasons, dist = score_item(it, flags)
        results.append({
            "id": it["id"],
            "score": pts,
            "dist_to_round_garden_km": dist,
            "data_quality_flags": flags,
            "reasons": reasons,
            "raw": it,
        })
    results.sort(key=lambda r: r["score"], reverse=True)
    return results


def print_table(results):
    print(f"{'Ранг':<5}{'ID':<11}{'Район':<13}{'Цена':<10}{'$/м²':<8}{'м²':<6}{'Этаж':<8}{'Балл':<7}\n")
    for rank, r in enumerate(results, 1):
        it = r["raw"]
        district = it.get("district_label") or it.get("urban") or "?"
        print(f"{rank:<5}{it['id']:<11}{district:<13}${float(it['price_usd']):<9,.0f}"
              f"{it.get('price_sqm_usd', '?'):<8}{it.get('area_m2', '?'):<6}"
              f"{it.get('floor')}/{it.get('total_floors'):<6}{r['score']:<7}")
        print(f"      {it.get('address', '')}")
        for reason in r["reasons"]:
            print(f"      + {reason}")
        for fl in r["data_quality_flags"]:
            print(f"      ⚠ {fl}")
        print()


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--csv", help="путь к CSV со списком объектов")
    p.add_argument("--db", action="store_true", help="читать/писать из Postgres (нужен DATABASE_URL)")
    args = p.parse_args()

    if args.db:
        sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "db"))
        from db import fetch_active_listings, update_scores  # noqa: E402
        rows = fetch_active_listings()
        results = run(rows)
        print_table(results)
        update_scores([
            {"id": r["id"], "score": r["score"], "dist_to_round_garden_km": r["dist_to_round_garden_km"],
             "data_quality_flags": r["data_quality_flags"]}
            for r in results
        ])
    elif args.csv:
        with open(args.csv, encoding="utf-8") as f:
            rows = list(csv.DictReader(f))
        for r in rows:
            for key in ("lat", "lng", "price_usd", "price_sqm_usd", "area_m2", "floor", "total_floors"):
                if r.get(key) in (None, ""):
                    r[key] = None
            r["is_duplicate"] = str(r.get("is_duplicate", "")).strip().lower() in ("true", "1", "yes")
        results = run(rows)
        print_table(results)
    else:
        print("Укажи --csv <файл> или --db")


if __name__ == "__main__":
    main()
