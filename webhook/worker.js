/**
 * Интерактивный Telegram-бот поверх той же Supabase-базы, что заполняет
 * scraper/db (GitHub Actions). Работает как webhook на Cloudflare Workers
 * (бесплатный тариф с большим запасом для личного использования).
 *
 * Команды:
 *   /start, /help — подсказка
 *   /top           — топ-5 активных объектов по score, с кнопками:
 *                      👍 Нравится / 👎 Не интересно / Ещё ⬇️ (пагинация)
 *
 * Секреты (Settings -> Variables -> добавить как Encrypt/Secret):
 *   TELEGRAM_BOT_TOKEN   — токен от @BotFather (тот же, что в GitHub Secrets)
 *   SUPABASE_URL         — https://<project>.supabase.co
 *   SUPABASE_SERVICE_KEY — Project Settings -> API -> service_role key
 *
 * После деплоя один раз указать Telegram, куда слать апдейты — открыть в
 * браузере (см. README):
 *   https://api.telegram.org/bot<TOKEN>/setWebhook?url=<адрес воркера>
 */

const PAGE_SIZE = 5;

export default {
  async fetch(request, env) {
    if (request.method !== "POST") {
      return new Response("tbilisi-property-finder bot is up");
    }

    let update;
    try {
      update = await request.json();
    } catch (e) {
      return new Response("bad request", { status: 400 });
    }

    try {
      if (update.message) {
        await handleMessage(update.message, env);
      } else if (update.callback_query) {
        await handleCallback(update.callback_query, env);
      }
    } catch (err) {
      console.log("handler error", err);
    }

    // Telegram ждёт 200 OK независимо от результата, иначе будет ретраить.
    return new Response("ok");
  },
};

async function handleMessage(message, env) {
  const chatId = message.chat.id;
  const text = (message.text || "").trim();

  if (text.startsWith("/start") || text.startsWith("/help")) {
    await sendMessage(
      env,
      chatId,
      "Привет! Команды:\n" +
        "/top — топ-5 активных объектов по score прямо сейчас\n\n" +
        "Под каждым объектом — кнопки:\n" +
        "👍 Нравится — просто фиксирую интерес\n" +
        "👎 Не интересно — больше не покажу этот объект нигде, включая ежедневную рассылку\n" +
        "Ещё ⬇️ — следующая пятёрка"
    );
    return;
  }

  if (text.startsWith("/top")) {
    await sendPage(env, chatId, 0);
    return;
  }

  await sendMessage(env, chatId, "Не знаю такую команду. Напиши /help.");
}

async function handleCallback(cq, env) {
  const chatId = cq.message.chat.id;
  const messageId = cq.message.message_id;
  const data = cq.data || "";

  if (data.startsWith("more:")) {
    const offset = parseInt(data.split(":")[1], 10) || 0;
    await answerCallback(env, cq.id, "Загружаю…");
    await sendPage(env, chatId, offset);
    return;
  }

  if (data.startsWith("like:") || data.startsWith("dislike:")) {
    const [action, idStr] = data.split(":");
    const listingId = parseInt(idStr, 10);
    await saveFeedback(env, listingId, chatId, action);
    await answerCallback(
      env,
      cq.id,
      action === "like" ? "Сохранено 👍" : "Скрыто, больше не покажу 👎"
    );
    await editReplyMarkup(env, chatId, messageId, {
      inline_keyboard: [
        [
          {
            text: action === "like" ? "✅ Нравится" : "🚫 Скрыто",
            callback_data: "noop",
          },
        ],
      ],
    });
    return;
  }
}

async function sendPage(env, chatId, offset) {
  const dislikedIds = await fetchDislikedIds(env, chatId);
  const rows = await fetchListings(env, offset, PAGE_SIZE, dislikedIds);

  if (rows.length === 0) {
    await sendMessage(
      env,
      chatId,
      offset === 0 ? "Пока нет активных объектов в базе." : "Это всё, больше вариантов нет."
    );
    return;
  }

  for (const row of rows) {
    const keyboard = {
      inline_keyboard: [
        [
          { text: "👍 Нравится", callback_data: `like:${row.id}` },
          { text: "👎 Не интересно", callback_data: `dislike:${row.id}` },
        ],
      ],
    };
    await sendMessage(env, chatId, formatListing(row), keyboard);
  }

  if (rows.length === PAGE_SIZE) {
    await sendMessage(env, chatId, "⬇️ Показать ещё?", {
      inline_keyboard: [[{ text: "Ещё ⬇️", callback_data: `more:${offset + PAGE_SIZE}` }]],
    });
  }
}

function formatListing(row) {
  const dist = row.dist_to_round_garden_km;
  const distTxt = dist != null ? `${Number(dist).toFixed(2)} км до Круглого сада` : "нет координат";
  const flags = (row.data_quality_flags || []).map((f) => `\n⚠ ${f}`).join("");
  const price = Number(row.price_usd).toLocaleString("en-US");
  return (
    `<b>${row.district_label || row.urban || "?"}</b> — $${price} ` +
    `(${row.price_sqm_usd}$/м², ${row.area_m2} м², ${row.floor}/${row.total_floors} эт.)\n` +
    `${row.address || ""}\n${distTxt}\nScore: ${row.score}${flags}\n${row.url}`
  );
}

async function fetchListings(env, offset, limit, excludeIds) {
  let url =
    `${env.SUPABASE_URL}/rest/v1/listings?is_active=eq.true` +
    `&order=score.desc.nullslast&limit=${limit}&offset=${offset}`;
  if (excludeIds.length > 0) {
    url += `&id=not.in.(${excludeIds.join(",")})`;
  }
  const resp = await fetch(url, { headers: supabaseHeaders(env) });
  if (!resp.ok) {
    console.log("fetchListings failed", resp.status, await resp.text());
    return [];
  }
  return resp.json();
}

async function fetchDislikedIds(env, chatId) {
  const url =
    `${env.SUPABASE_URL}/rest/v1/user_feedback?chat_id=eq.${chatId}` +
    `&action=eq.dislike&select=listing_id`;
  const resp = await fetch(url, { headers: supabaseHeaders(env) });
  if (!resp.ok) return [];
  const rows = await resp.json();
  return rows.map((r) => r.listing_id);
}

async function saveFeedback(env, listingId, chatId, action) {
  const url = `${env.SUPABASE_URL}/rest/v1/user_feedback`;
  const resp = await fetch(url, {
    method: "POST",
    headers: {
      ...supabaseHeaders(env),
      "Content-Type": "application/json",
      Prefer: "resolution=merge-duplicates",
    },
    body: JSON.stringify({ listing_id: listingId, chat_id: String(chatId), action }),
  });
  if (!resp.ok) {
    console.log("saveFeedback failed", resp.status, await resp.text());
  }
}

function supabaseHeaders(env) {
  return {
    apikey: env.SUPABASE_SERVICE_KEY,
    Authorization: `Bearer ${env.SUPABASE_SERVICE_KEY}`,
  };
}

async function sendMessage(env, chatId, text, replyMarkup) {
  const url = `https://api.telegram.org/bot${env.TELEGRAM_BOT_TOKEN}/sendMessage`;
  const payload = {
    chat_id: chatId,
    text,
    parse_mode: "HTML",
    disable_web_page_preview: false,
  };
  if (replyMarkup) payload.reply_markup = replyMarkup;
  const resp = await fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  if (!resp.ok) {
    console.log("sendMessage failed", resp.status, await resp.text());
  }
}

async function answerCallback(env, callbackQueryId, text) {
  const url = `https://api.telegram.org/bot${env.TELEGRAM_BOT_TOKEN}/answerCallbackQuery`;
  await fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ callback_query_id: callbackQueryId, text }),
  });
}

async function editReplyMarkup(env, chatId, messageId, replyMarkup) {
  const url = `https://api.telegram.org/bot${env.TELEGRAM_BOT_TOKEN}/editMessageReplyMarkup`;
  await fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ chat_id: chatId, message_id: messageId, reply_markup: replyMarkup }),
  });
}
