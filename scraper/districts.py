"""
Справочник районов myhome.ge: slug для URL, urban_id и district_id.

Как добавить новый район:
1. Зайти на https://www.myhome.ge/en/real-estate/
2. В поле локации ввести название района, выбрать нужный вариант с пометкой "Urban"
3. Скопировать из адресной строки: .../tbilisi/<slug>/?...&urbans=<N>&districts=<M>
4. Добавить строку в DISTRICTS ниже.

Проверено вручную на реальном сайте (август 2026):
"""

DISTRICTS = {
    "vake":       {"slug": "vake",      "urban_id": 38, "district_id": 4, "label": "Vake"},
    "vera":       {"slug": "vera",      "urban_id": 64, "district_id": 6, "label": "Vera"},
    "mtatsminda": {"slug": "mtawminda", "urban_id": 66, "district_id": 6, "label": "Mtatsminda"},
    "bagebi":     {"slug": "bagebi",    "urban_id": 28, "district_id": 4, "label": "Bagebi"},
}

# Примерные центры районов (для грубой проверки геопривязки — см. validate.py)
DISTRICT_CENTROIDS = {
    "Vake": (41.7159, 44.7623),
    "Vera": (41.7104, 44.7849),
    "Mtatsminda": (41.6975, 44.7935),
    "Bagebi": (41.7110, 44.7220),
}

ROUND_GARDEN = (41.70731, 44.77489)  # Mrgvali Baghi / Круглый сад, Ваке
