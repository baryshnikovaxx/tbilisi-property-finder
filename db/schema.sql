-- Схема для Supabase / любого Postgres.
-- Применить один раз: в Supabase — SQL Editor -> вставить -> Run.

create table if not exists listings (
    id                bigint primary key,          -- id объявления на myhome.ge
    source            text default 'myhome.ge',
    district_label    text,                          -- наш ярлык района (Vake/Vera/Mtatsminda/Bagebi)
    urban             text,                          -- поле с сайта (urban_name)
    address           text,
    price_usd         numeric,
    price_sqm_usd     numeric,
    area_m2           numeric,
    rooms             text,
    bedrooms          text,
    floor             int,
    total_floors      int,
    lat               double precision,
    lng               double precision,
    condition_id      int,
    comment           text,
    url               text,
    is_duplicate      boolean default false,
    duplicate_of      bigint,
    data_quality_flags jsonb default '[]'::jsonb,
    score             numeric,
    dist_to_round_garden_km numeric,
    first_seen        timestamptz default now(),
    last_seen         timestamptz default now(),
    is_active         boolean default true            -- false, если объявление пропало из выдачи
);

-- Индексы под частые запросы (топ по score, свежие объекты, по району)
create index if not exists idx_listings_score on listings (score desc);
create index if not exists idx_listings_district on listings (district_label);
create index if not exists idx_listings_last_seen on listings (last_seen desc);

-- История цен — на будущее, когда захочется отслеживать снижения/повышения
-- цены и считать реальную ликвидность (дни на рынке, частота торга).
create table if not exists price_history (
    id            bigserial primary key,
    listing_id    bigint references listings(id),
    price_usd     numeric,
    observed_at   timestamptz default now()
);

create index if not exists idx_price_history_listing on price_history (listing_id, observed_at);

-- Миграция: только-новые уведомления + алерт на снижение цены.
-- Безопасно запускать повторно на уже существующей базе.
alter table listings add column if not exists notified_at timestamptz;
alter table listings add column if not exists last_notified_price numeric;

-- Реакции из интерактивного бота (кнопки 👍/👎 под объявлением в Telegram).
create table if not exists user_feedback (
    id           bigserial primary key,
    listing_id   bigint references listings(id),
    chat_id      text not null,
    action       text not null check (action in ('like', 'dislike')),
    created_at   timestamptz default now(),
    unique (listing_id, chat_id)
);

create index if not exists idx_user_feedback_chat on user_feedback (chat_id, action);
