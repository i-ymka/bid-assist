# DECISION_LOG — Bid-Assist

Формат записей: **Дата | Суть | Мотивация | Реальность**

---

## 2026-03-26/27 | [INFRA] Деплой на Vultr + Gemini quota/overload fixes

**Суть:**
1. **Деплой на Vultr VPS** (70.34.205.112, Ubuntu 22.04, $5/mo). Оба бота запущены как systemd сервисы (`bid-ymka.service`, `bid-yehia.service`). Мак больше не запускает бота.
2. **Исправлен баг двойного Call 1** (`1df6412`): при неудаче Call 1 shared_repo делал `release_claim` → второй бот видел пустой кэш и повторял вызов. Фикс: писать `FAILED` вердикт вместо release.
3. **ВРЕМЕННЫЙ flash fallback** (`e546f4a`): после 3 retry на overload — Call 1 переключается на `gemini-3.1-flash-preview` вместо отказа. **Убрать как только gemini-3.1-pro-preview стабилизируется.**
4. **Overload retries: 15→3** (`3069961`): 45 секунд ожидания до fallback были слишком долгими.

**Мотивация:**
- Мак работал без выключения месяцами — деградация железа.
- Google 25.03.2026: Pro модели заблокированы для free tier → free pool отключён.
- gemini-3.1-pro-preview периодически уходит в 503 overload (30–120 мин). Второй про аккаунт не поможет — это серверная проблема Google, не зависит от тарифа.

**Реальность (важно знать):**
- scp зависает на Vultr — всегда использовать rsync для копирования файлов.
- Реальная квота AI Pro для Gemini CLI: ~18–150 req/day (документировано 1500, но известный баг Google, issue открыт в gemini-cli repo).
- Квота сбрасывается в **07:00 UTC** (полночь Pacific Time), не в полночь UTC.
- Flash (`gemini-3.1-flash-preview`) для Call 1 даёт чуть хуже качество анализа, но лучше чем пропустить проект.

**TODO:**
- Убрать `GEMINI_OVERLOAD_FALLBACK_MODEL` и flash fallback из `gemini_analyzer.py` когда Google починит 503 на pro-3.1.

---

## 2026-03-23 | [FEAT] v2.9 — Multi-account Gemini pool + Spinner keyboard input

**Суть:**
1. **Multi-account Gemini pool** — `_run_gemini_cli(prompt, primary_model, pool_model)` пробует аккаунты по порядку: pro → free1 → free2 → free3 → free4. При 429 QUOTA_EXHAUSTED — cooldown 1ч на `(home_dir, model)`. Если все исчерпаны — `_all_exhausted_flag = True`, `analysis_loop` посылает Telegram-уведомление и спит 30 мин.
2. **Аккаунты изолированы через `HOME` env var** — каждый subprocess получает `env = {**os.environ, "HOME": account_home}`, где `account_home` = директория аккаунта (напр. `~/.gemini_accounts/pro`). Credentials в `{home}/.gemini/oauth_creds.json`.
3. **Модели:** Pro: `gemini-3.1-pro-preview` (Call 1), `gemini-3.1-flash-lite-preview` (Call 2). Pool: `gemini-3-pro-preview` (Call 1), `gemini-3-flash-preview` (Call 2).
4. **Конфиг:** `GEMINI_HOME_PRIMARY` (pro аккаунт), `GEMINI_HOME_POOL` (через запятую, free аккаунты), `GEMINI_POOL_MODEL`, `BID_POOL_MODEL` в .env.
5. **`send_quota_exhausted_notification()`** в `notifier.py` — шлёт сообщение во все chat_ids.
6. **Spinner ✏️** — в `/settings` числовые параметры теперь имеют кнопку ✏️. Нажатие запускает ConversationHandler (`WAITING_SPINNER = 2`): бот просит ввести число, `receive_spinner_value` валидирует, сохраняет, возвращает спиннер. `spincancel:` — отмена без сохранения.

**Мотивация:**
- `gemini-3.1-pro-preview` квота исчерпывалась после 3–4 анализов (~14ч reset). Бот буквально останавливался. 4 бесплатных аккаунта дают ~20+ дополнительных запросов в сутки.
- Spinner с шагами ±1/±10 неудобен для больших чисел (например, budget_max = 5000 — нужно 500 нажатий). Keyboard input решает проблему за 1 действие.

**Архитектурные решения:**
- `HOME` вместо `GEMINI_HOME`: `GEMINI_HOME` не подтверждён в документации Gemini CLI, `HOME` — стандартный Unix способ изолировать домашние директории, работает надёжно.
- Cooldown in-memory `dict[tuple[str, str], float]` — не в БД, сбрасывается при рестарте. Это ок: после рестарта quota могла сброситься сама.
- `consume_exhaustion_flag()` — one-shot флаг, чтобы `analysis_loop` получил сигнал ровно один раз.
- Pool-аккаунты используют модели без `.1` суффикса (gemini-3 vs gemini-3.1) — это реальные модели бесплатного тира (подтверждено через `gemini /stats` под free-аккаунтом).

---

## 2026-03-22 | [FEAT] v2.8 — Двойная фильтрация + UX-правило одного сообщения

**Суть:**
1. `_recheck_queue_filters(project_data, repo)` — новая функция в `run.py`. Повторяет все фильтры `polling_loop` на выходе из очереди прямо перед AI, используя только данные из БД (без API-вызовов). Фильтры: возраст проекта, бюджет, валюта, blacklist, verified/verification keywords, страна, preferred-only, bid_count при изменении настройки. Если проект не прошёл — удаляется из очереди, добавляется в processed, AI не вызывается.
2. **Авто-бид fresh bid_count** — last-mile проверка перед `place_bid()` теперь делает реальный API-вызов `project_service.get_project_details(project_id)` вместо stale данных из очереди. Если проект недоступен — тихий skip.
3. **Настройка «Verified account»** в /settings — переименована с «Crypto» (вводило в заблуждение). Комментарии в `repository.py` обновлены.
4. **Одно сообщение на проект** — предупреждение о слишком многих конкурентах при ручном биде теперь редактирует оригинальное сообщение через `edit_message_text` с полным текстом бида + предупреждением снизу + кнопками `✏️ Edit Amount`, `✏️ Edit Proposal`, `⚠️ Bid anyway (N competitors)`. Никакого `reply_text`.

**Мотивация:**
- Проект мог лежать в очереди час, устареть или клиент мог изменить его — AI всё равно запускался и тратил токены.
- Stale bid_count (из момента постановки в очередь) не отражает реальность к моменту бида.
- «Crypto» → «Verified account»: crypto — это ключевое слово в blacklist, а не настройка. Настройка управляет тем, может ли аккаунт бидить на проекты, требующие верификации (crypto/blockchain/nft/web3).
- Два сообщения на один проект путали UI — непонятно где актуальная инфа.

**Архитектурное решение:** `_recheck_queue_filters` не делает API-вызовов — только читает `runtime_settings` из БД и поля из `project_data`. Для fresh bid_count специально выбран момент ПОСЛЕ AI (перед самым `place_bid`) — это единственная проверка где нужна актуальность с точностью до секунды.

**Правило в docs:** `docs/ARCHITECTURE.md` — раздел `notifier.py` содержит правило «Одно сообщение на проект».

---

## 2026-03-17 | [REFACTOR] Промпты: ревизия после 50 бидов без результата

**Суть:**
- `analyze.md`: веб-поиски 2–4 → 2–8; Zapier → n8n в automation skills; убрана лишняя ремарка из Step 6
- `bid_writer.md`: убраны STEP 0 anti-bot шаблоны (→ упрощён до "check for special instructions"); удалён MANDATORY WEB SEARCH (bid_writer — быстрая модель, не аналитик); CLIENT NAME строже — только для подробных постов (5+ предложений); CTA "ready to start" → мягкие collaborative вопросы; новые hard constraints: em dash → дефис, одинарные кавычки для кода → двойные; пример починен (3 предложения + логичный CTA)
- Код: `ProjectOwner.display_name` (из `public_name` API), `owner_display_name` в `project_queue`, `write_bid()` получает `owner_name` → вставляет `CLIENT NAME:` (не username)

**Мотивация:** 50 бидов, 0 клиентов. Промпты писались до первого запуска и не корректировались. Главные проблемы: "ready to start" CTA звучит как будто работа уже получена; em dash и одинарные кавычки для кода — визуальные маркеры AI-текста; username вместо имени в персонализации.

**Реальность:** Промпты не в репозитории (`.gitignore: prompts_*/*`). Изменения применяются локально на каждой машине.

---

## 2026-03-16 | [FEAT] Shared Call 1 cache — исключить дублирование AI-анализа между аккаунтами

**Проблема:** Два аккаунта (yehia + ymka) запускались как отдельные сабпроцессы с изолированными БД. Когда оба обнаруживали один проект, каждый независимо запускал Gemini Call 1 (feasibility) — двойная трата токенов на идентичный результат. Call 1 дорогой (gemini-3.1-pro-preview с HIGH thinking).

**Решение:** Общий SQLite файл `data/shared_analysis.db` с WAL mode — доступен обоим процессам без блокировок.

**Архитектура:**
1. `src/services/storage/shared_repository.py` — новый класс `SharedAnalysisRepository`. Методы: `try_claim()` (атомарный `INSERT OR IGNORE` → `rowcount==1` = мы первые), `get_result()` (cached result если `done`/`skip` и < 24h), `store_result()` (сохраняет после Call 1), `release_claim()` (освобождает при ошибке Call 1), `cleanup_stale()`.
2. `gemini_analyzer.py` — `analyze_project()` получил параметр `feasibility: Optional[dict] = None`. Если передан — Call 1 пропускается. Полная обратная совместимость (default=None).
3. `run.py analysis_loop` — перед Call 1: `get_result()` → если есть → `mark_queue_status("analyzing")` → `analyze_project(feasibility=cached)`; нет → `try_claim()` → если не claim → defer + `continue`; если claim → `analyze_feasibility()` в executor → `store_result()` → `analyze_project(feasibility=result)`.
4. `run.py cleanup_loop` — добавлен `shared_repo.cleanup_stale(24)` (тот же паттерн что `project_queue`).
5. Путь к shared DB выводится из `DB_PATH`: `Path(settings.db_path).parent / "shared_analysis.db"` — без новых env-переменных.

**Поведение при одном аккаунте:** shared DB создаётся пустой, overhead нулевой. Флаг включения не нужен.

**Гонка:** Оба аккаунта одновременно обнаружили проект — `INSERT OR IGNORE` атомарен в SQLite. Один получает `rowcount=1` (claimed), второй — `rowcount=0` (defer). Второй ждёт 5 сек, повторяет итерацию, находит `status=done` в кэше, использует готовый результат.

---

## 2026-03-16 | [FEAT] bid_adjustment — настраиваемый % ценообразования в /settings

**Суть:** Хардкод `-10%` от рыночной цены заменён на DB-backed runtime-настройку `bid_adjustment`.

**Изменения:**
1. `repository.py` — `get_bid_adjustment() -> int` / `set_bid_adjustment(pct)`. Seed: `INSERT OR IGNORE INTO runtime_settings VALUES ('bid_adjustment', '-10')`.
2. `gemini_analyzer.py` — `_calculate_amount()` принимает `bid_adjustment: int = -10`; формула: `multiplier = 1 + bid_adjustment/100`. `analyze_project()` и `force_bid_analysis()` принимают и передают `bid_adjustment`.
3. `run.py analysis_loop` — читает `repo.get_bid_adjustment()` перед каждым вызовом `analyze_project()`.
4. `handlers.py` — кнопка "Bid adj: -10%" в /settings, пресеты `[-50, -25, -10, 0, 10, 25]`, callback `settings:bid_adj` (cyclic). `force_bid_analysis` call передаёт `repo.get_bid_adjustment()`.

**Мотивация:** Для ymka (новый аккаунт, 0 отзывов) нужно бидить агрессивнее — ниже рынка. Для yehia может потребоваться другой % по ситуации. Без UI приходилось лезть в код.

---

## 2026-03-16 | [FIX] winner_proposal в /bidstats показывался как "xxxx..."

**Баг:** В /bidstats карточки лосса показывали предложение победителя как "xxxxxxx..." (N иксов). Реальный текст был недоступен.

**Root cause:** `bid_outcomes` хранила только `winner_proposal_len INTEGER` (длину), а не текст. `_fetch_bid_stats_sync()` при cache hit восстанавливал `"x" * len` как placeholder. Текст терялся сразу при первой классификации.

**Фикс:**
1. `repository.py` — inline migration: `ALTER TABLE bid_outcomes ADD COLUMN winner_proposal TEXT`. `set_bid_outcome()` сохраняет `winner_proposal_text` (с COALESCE при upsert, не перезаписывает если уже есть текст). Защита: не сохраняет строки состоящие только из "x" (placeholder → не затирает будущий реальный текст).
2. `get_bid_outcome_full()` — возвращает `winner_proposal` в словаре.
3. `_fetch_bid_stats_sync()` — cache hit теперь: `cp = cached_row.get("winner_proposal"); "winner_proposal": cp if cp is not None else "x" * (cpl or 0)`.

**Результат:** Для новых лоссов реальный текст хранится и отображается. Старые строки (только len) по-прежнему показывают placeholder нужной длины для метрик.

---

## 2026-03-16 | [FEAT] "Show more ↓" пагинация лоссов в /bidstats

**Суть:** При `len(all_losses) > 10` в /bidstats теперь показывается кнопка "Show more ↓ (N left)" вместо безмолвного обрезания.

**Изменения:**
1. `handle_bidstats_callback()` — начальный показ: если лоссов > `_MAX_LOSS_CARDS`, добавляет InlineKeyboardMarkup с кнопкой `bidstats:more_losses:weekly:{_MAX_LOSS_CARDS}`.
2. Добавлена ветка `if action == "more_losses":` в начале `handle_bidstats_callback()`: парсит `period` и `offset` из callback_data, перестраивает `all_losses` через `_build_weekly_subset()`, показывает следующий батч, добавляет новую кнопку если ещё остались.

---

## 2026-03-16 | [REFACTOR] prompts_ymka/bid_writer.md — новая persona для ymka

**Суть:** Полная переработка промпта под реальность нового аккаунта.

**Изменения:**
- Удалено правило FiveM / pyramidsdev.net (не релевантно для ymka)
- Persona: "THE STEADY EXPERT" → "FOCUSED AND AVAILABLE" (новый аккаунт, не скромничает но и не притворяется ветераном)
- Длина бида: 3-5 → 4-7 предложений (нужно больше контекста без кредитов)
- Portfolio nudge: опциональный → **обязательный**: "You can check my work at iymka.com." (нет отзывов — нужна хоть какая-то ссылка на доказательство)
- Добавлена секция PRICING GUIDANCE: `FAIR_PRICE = market_estimate * 0.75` (-25% от рынка для первых проектов, наращивание отзывов важнее маржи)
- Пример бида обновлён: добавлен portfolio nudge в golden example

---

## 2026-03-16 | [FIX] Preferred-only проекты тратили AI токены

**Баг:** Проект с `pf_only=True` попадал в `project_queue` на предыдущем polling-цикле (когда флаг был False или фильтр ещё не работал). На следующих циклах polling его правильно скипал, но `analysis_loop` работает независимо — брал из очереди и тратил Gemini токены, затем бид падал с "You must be a Preferred Freelancer".

**Фикс:**
1. `repository.py` — миграция: колонка `is_preferred_only INTEGER DEFAULT 0` в `project_queue`; `add_to_queue()` принимает и сохраняет флаг.
2. `run.py polling_loop` — передаёт `is_preferred_only=project.is_preferred_only` при добавлении в очередь.
3. `run.py analysis_loop` — проверяет флаг **до** AI вызовов. Если `True` — удаляет из очереди, добавляет в processed, `continue`. Ноль токенов потрачено.
4. Страховка: если проект стал preferred **после** постановки в очередь (race condition) — ошибка "preferred freelancer" при `place_bid` теперь тихий `INFO` лог вместо `ERROR`, без уведомления в Telegram.

---

## 2026-03-16 | [FEAT] Мульти-аккаунт: --env флаг + PROMPTS_DIR + параллельный запуск

**Суть:**
1. `run.py` — `--env .env.yehia` / `--env .env.ymka` для запуска конкретного аккаунта. Без флага — запускает оба как сабпроцессы с `[yehia]` / `[ymka]` префиксами в stdout.
2. `settings.py` — `ENV_FILE` читается из `os.environ` до создания класса; добавлен `PROMPTS_DIR` (default: `prompts`).
3. `gemini_analyzer.py` — пути к промптам через `settings.prompts_dir` вместо хардкода `prompts/`.
4. Созданы: `.env.yehia`, `.env.ymka` (шаблоны), `prompts_yehia/`, `prompts_ymka/` (независимые persona-промпты).
5. `.gitignore` — `.env.*` и `prompts_*/` исключены.

**Мотивация:** Второй Freelancer-аккаунт с отдельными токенами, БД, Telegram-ботом и промптами (другая persona).

**Архитектурное решение:** Subprocess-подход (не asyncio в одном процессе), чтобы каждый аккаунт имел полностью изолированный Settings-singleton и независимые циклы. Альтернатива (передавать env_file как аргумент в Settings) сломала бы singleton-паттерн и потребовала переписать всю инициализацию.

---

## 2026-03-16 | [FEAT] v2.2: client username в биде + bid_count gate перед ручным бидом

**Суть:**
1. `owner_username` + `owner_display_name` пробрасываются через весь pipeline: `project_queue` (колонки) → `polling_loop` → `analysis_loop` → `analyze_project()` / `force_bid_analysis()` → `write_bid()` → `CLIENT NAME:` в промпте. Передаётся `display_name` (public_name из API), fallback на `username`.
2. `bid_writer.md` — правило CLIENT NAME: использовать только если пост подробный (5+ предложений клиента), не форсировать.
3. `handlers.py` — перед ручным `place_bid()` проверяется `bid_count` vs `get_max_bid_count()`. Если превышено — предупреждение в чат, бид не ставится.

**Мотивация:** Персонализация увеличивает шанс ответа. Ручной бид должен иметь те же защиты что и авто-бид.

---

## 2026-03-16 | ТЕСТИРОВАНИЕ: audit fixes + bid_writer improvements

Авто-тесты: 5/5 прошли
Ручные тесты: ожидают проверки (см. ниже)
Найдены баги: нет
Скрипты оставлены: `_verify/test_01..05` (все)

**Что проверено авто:**
- TEST-01: `*.log` в `.gitignore` — `bot_debug.log` и `logs/*` исключены
- TEST-02: `client.put()` принимает `params` и передаёт их в `_request()`
- TEST-03: `retract_bid()` использует `self._client.put()`, нет `requests.put()` и `import requests` внутри метода
- TEST-04: `bid_writer.md` содержит STEP 0 (anti-bot), новый hook, forbidden phrases, CTA variation
- TEST-05: `_validate_bid_text()` корректно пропускает чистые биды и режет AI-garbage

**Что ожидает ручной проверки:**
1. Запустить бота на живом проекте с "напиши X в начале" — проверить соблюдение
2. Проверить 3-5 реальных бидов: hook из описания (не из названия), разные CTA-формулировки
3. Убедиться что `retract_bid` работает на живом API (нужен реальный bid_id для отзыва)

---

## 2026-03-16 | [FIX] retract_bid() — убран raw requests, клише в bid_writer

**Суть:** Три правки без изменения функционала:
1. `retract_bid()` переписан через `self._client.put(params=...)` (был raw `requests.put`)
2. `client.put()` получил параметр `params` для query params
3. `*.log` добавлен в `.gitignore` (раньше `bot_debug.log` в корне не был исключён)

**Суть bid_writer.md:**
1. STEP 0 — anti-bot check: AI сканирует description на "начни со слова X" перед написанием
2. Hook теперь требует деталь из description, а не из title (явная инструкция)
3. Forbidden phrases расширены: `"is a clear task"`, `"I am free now"`, клише CTA из реальных бидов
4. CTA: инструкция менять формулировку каждый раз (не повторять одно и то же)

**Мотивация:** Аудит 50 реальных бидов показал повторяющиеся шаблоны и отсутствие реакции на bot-check инструкции в описаниях. 0 конверсий за последние 50 бидов.

---

## 2026-02-23 | Старт системной памяти проекта

**Суть:** Проведён полный аудит проекта. Созданы TECH_SPEC.md, _CODER_RULES.md, ARCHITECTURE.md, DECISION_LOG.md.

**Мотивация:** Проект работает в продакшене (v2.0.0), но не имел системной документации. Нужна база знаний для быстрой работы без потери контекста между сессиями.

**Реальность:**
- Проект полностью функционален: polling → filtering → AI analysis → Telegram notification → bidding
- Стек в целом актуален, но найдены 4 проблемы:
  1. **КРИТИЧНО:** Gemini модель `gemini-3-pro-preview` в процессе deprecation — может отключиться через 2 недели
  2. **СРЕДНЕ:** python-telegram-bot заблокирован на v20.x (`<21.0` cap), пропущено 2 года обновлений (v22.6 доступна)
  3. **СРЕДНЕ:** `openai` SDK в requirements.txt не используется (код использует Gemini CLI)
  4. **НИЗКО:** README.md устарел — описывает OpenAI вместо Gemini

---

## Архитектурные решения (ретроспектива)

### Gemini CLI вместо API SDK
**Суть:** AI-анализ через subprocess вызов `gemini` CLI, а не через Python SDK.
**Мотивация:** Вероятно, CLI проще в настройке и не требует отдельного SDK.
**Реальность:** Работает, но привязывает к локальной установке Gemini CLI. Не масштабируется в Docker без дополнительной настройки.

### SQLite как единое хранилище
**Суть:** Все данные (очередь, история, настройки, кэш) в одном SQLite файле.
**Мотивация:** Простота, zero-config, один процесс.
**Реальность:** Работает хорошо для single-user. Миграции через ALTER TABLE inline в коде (не через Alembic). При масштабировании на multi-user может стать узким местом.

### Inline-фильтрация в polling_loop
**Суть:** Фильтры применяются прямо в `run.py:polling_loop()`, а не через `FilterPipeline`.
**Мотивация:** Порядок фильтров важен (сначала дешёвые проверки, потом API-вызовы).
**Реальность:** `FilterPipeline` существует, но не используется в main loop. Фильтрация размазана: часть в pipeline, часть hardcoded в run.py. Потенциальный рефакторинг.

### Custom Emoji в Telegram
**Суть:** Используются premium custom emoji (tg://emoji?id=...) с unicode fallback.
**Мотивация:** Красивый UI для Telegram Premium пользователей.
**Реальность:** Работает, но усложняет форматирование сообщений.

---

## 2026-03-08 | [REFACTOR] Split monolithic prompt into 2 focused prompts + deterministic pricing

**Суть:** Заменили один большой `pal_rules.md` на два специализированных промпта (`analyze.md` + `bid_writer.md`) и перенесли расчёт цены из AI в детерминированный код.

**Мотивация:** Монолитный промпт делал слишком много в одном вызове: feasibility, risk, time estimation, pricing math и bid writing. AI периодически игнорировал правила (неправильные цены, сложный английский, неверный тон). Два коротких промпта с чёткой единственной задачей дают более предсказуемый результат.

**Архитектура:**
- Call 1 (`GEMINI_MODEL`, analysis): `analyze.md` → `VERDICT (PASS/SKIP) / DAYS / SUMMARY`
- Code: `_calculate_amount(days, avg_bid, budget_max, min_daily_rate)` → `AMOUNT`
- Call 2 (`BID_MODEL`, bid writing): `bid_writer.md` → `BID` text
- SKIP после Call 1 → Call 2 не вызывается (экономия)

**Pricing formula:**
```
floor  = days × min_daily_rate
target = avg_bid × 0.90   (если avg_bid > 0)
       = budget_max × 0.75 (если нет avg_bid)
amount = round(max(floor, target) / 10) × 10
```

**Изменения в коде:**
- `gemini_analyzer.py` — полная переработка: `analyze_feasibility()`, `_calculate_amount()`, `write_bid()`, новый оркестратор `analyze_project()`, `force_bid_analysis()` вызывает Call 1 для DAYS (игнорирует SKIP), затем Call 2
- `settings.py` — добавлен `bid_model` (env: `BID_MODEL`), дефолт `gemini_model` → `gemini-3.1-pro-preview`, `min_daily_rate` дефолт → 100
- `repository.py` — `get_min_daily_rate()` / `set_min_daily_rate()`, seed `min_daily_rate=100` в init
- `handlers.py` — кнопка "Min rate: $X/day" в /settings (пресеты: 50/75/100/125/150/200)
- `run.py` — убраны оба guardrail (теперь цена гарантированно валидна кодом)

**Deferred:** Рассматривался вариант с 3-м AI-вызовом, где bid_writer сам делает дополнительный research (риски, missing context, best/worst case). Отклонено — SUMMARY из Call 1 должно давать достаточный контекст; добавление research в Call 2 вернёт когнитивную перегрузку, которую мы убираем. Пересмотреть если качество Call 2 окажется недостаточным.

---

## 2026-02-25 | [BUGFIX] AI bid $50 for 3-day project — ignored $50/day minimum rate

**Bug:** Auto-bid placed a $50 bid on a 3-day project ($16.67/day), ignoring the prompt rule
"Minimum = Days × 50 = $150". The AI matched the client's stated budget ($50) instead of
applying the pricing floor.

**Root Cause:** The prompt in `pal_rules.md` clearly states "Apply floor: never below Our Minimum
(Days × 50)" but the Gemini model sometimes disregards this rule when the client explicitly
mentions a low budget number. We cannot guarantee 100% prompt compliance from any LLM.

**Fix:** Added a hard code guardrail in `analysis_loop()` (run.py) that runs AFTER AI parsing
and BEFORE bid placement:
- If `result.amount < result.period × MIN_DAILY_RATE` → override verdict from BID to SKIP
- `MIN_DAILY_RATE` is configurable via .env (`MIN_DAILY_RATE=50`, default $50/day)
- Guardrail handles currency conversion: checks amount in USD regardless of project currency
- Warning logged: "GUARDRAIL: AI bid $50 for 3 days is below minimum daily rate..."

This is a safety net — the prompt SHOULD enforce the floor, but the code now guarantees it.

---

## 2026-02-23 | [BUGFIX] Budget filter ignored user settings — always used hardcoded $50-$3000

**Bug:** User set budget range to $50-$1000 via Telegram /settings, but bot accepted a $1500-$3000
project because the budget filter was never reading the user-configured values.

**Root Cause:** Budget min/max lived ONLY in the in-memory `_runtime_state` dict in handlers.py.
It was never persisted to the DB. Meanwhile, `run.py` created `BudgetFilter()` with no arguments
on every poll cycle → hardcoded defaults $50-$3000 applied every time. Compare with `poll_interval`
which correctly uses `repo.get_poll_interval()` / `repo.set_poll_interval()` via the DB.

**Fix:**
1. Added `get_budget_range()` / `set_budget_range()` to `repository.py` (uses `runtime_settings` table,
   same pattern as `poll_interval`).
2. `handlers.py`: budget button callback and `/setbudget` command now call `repo.set_budget_range()`.
   Budget is also loaded from DB at module init to restore state after bot restart.
3. `run.py`: `BudgetFilter(min_budget=budget_min, max_budget=budget_max)` now reads from DB each cycle.

---

## 2026-02-23 | [BUGFIX] /status "Projects seen: 0" and "5 analyzing" stuck in queue

**Bug 1: session "Projects seen" always shows 0**
Root cause: `set_bot_start_time()` stored `datetime.now()` (LOCAL timezone, e.g. UTC+3 Moscow),
while `processed_projects.processed_at` and `bid_history.created_at` are set via SQLite's
`DEFAULT CURRENT_TIMESTAMP` which is always UTC. For UTC+3, all `processed_at` timestamps are
3h behind `bot_start`, making `WHERE processed_at >= bot_start` fail for all day-1 projects.
Fix: `set_bot_start_time()` now uses `datetime.utcnow()`. Uptime calculation in handlers.py
also updated to compare `datetime.utcnow() - start_time` for consistency.

**Bug 2: projects stuck in "analyzing" state accumulate indefinitely**
Root cause A: When an exception occurred anywhere in `analysis_loop` after
`mark_queue_status(project_id, "analyzing")`, the except block only logged and slept —
the project stayed in "analyzing" state permanently (never picked up again since
`get_next_from_queue` only returns `status='pending'` items).
Root cause B: `cleanup_old_queue_items()` only cleaned up `status='pending'` items,
so "analyzing" items were never garbage-collected.
Fix A: `analysis_loop` except block now calls `repo.remove_from_queue(project_id)` if
`project_id` is set, so the project can be re-queued on the next poll cycle.
Fix B: `cleanup_old_queue_items()` now includes `status IN ('pending', 'analyzing')` so
stuck analyzing items are cleaned up after `max_age_hours` (default: 24h).

---

## 2026-02-23 | [BUGFIX] /bidstats comparison metrics wildly inconsistent between calls

**Bug:** "You vs Winners" comparison metrics (bid % diff, proposal length diff, review diff) changed
dramatically between consecutive /bidstats "All time" calls — e.g., "72% higher" → "24% higher",
"237 chars shorter" → "455 chars longer".

**Root Cause:** `bid_outcomes` table cached only the outcome string ("LOSS") but NOT winner details
(winner_amount, winner_proposal length, winner_reviews). When the 30-min in-memory cache expired and
`_fetch_bid_stats_sync()` re-ran, DB-cached LOSS entries had `detail = None` because `_classify_project()`
was skipped. Comparison averages were therefore computed only over the tiny subset of losses freshly
re-classified in that specific run (new bids + random variation) — a non-representative sample → different
numbers every call.

**Fix:**
1. `bid_outcomes` table: added columns `winner_amount REAL`, `winner_proposal_len INTEGER`,
   `winner_reviews INTEGER` (inline migration, backward-compatible).
2. `set_bid_outcome()`: extended to accept optional `winner_detail` dict; extracts and stores the 3 values.
3. `get_bid_outcome_full()`: new method returning outcome + winner comparison data as a dict.
4. `_fetch_bid_stats_sync()` loop: for DB-cached LOSS entries, loads cached winner data and reconstructs
   a `detail`-compatible dict (winner_proposal is a placeholder of the correct length — only length
   matters for comparison metrics, not text). For pre-fix rows with NULL winner data, re-classifies once
   to backfill, then caches. New LOSS classifications store `detail` in DB immediately.

**Result:** Comparison averages are now stable across calls (computed over ALL historical losses, not a
random subset).

---

## 2026-03-10 | [FIX] Restore correct Gemini models after accidental breakage

**Bug:** Another session accidentally set broken/non-existent models:
- `settings.py`: `bid_model` → `gemini-3.1-flash-lite-preview` (doesn't exist)
- `gemini_analyzer.py`: `ANALYSIS_FALLBACK_MODELS` → `["gemini-3-pro-preview", "gemini-3.1-flash-lite-preview"]` (both don't exist)
- `gemini_analyzer.py`: `BID_FALLBACK_MODELS` → `["gemini-3-flash-preview", "gemini-flash-latest"]` (don't exist)
- `.env`: `GEMINI_MODEL` → `gemini-2.5-pro` (overriding the correct default)

**Fix:** Restored confirmed-working models (tested 2026-03-09):
- Call 1 primary: `gemini-3.1-pro-preview` (`.env` restored)
- Call 1 fallback: `["gemini-2.5-flash", "gemini-2.5-pro"]`
- Call 2 primary: `gemini-2.5-flash` (`settings.py` `bid_model` default)
- Call 2 fallback: `["gemini-2.5-pro"]`

---

## 2026-03-07 | [CRITICAL FIX] Deprecated Gemini model — 2 days before shutdown

**Bug:** `gemini-3-pro-preview` was set as the default model in `settings.py` AND in the
`FALLBACK_MODELS` chain in `gemini_analyzer.py`. Google announced shutdown on March 9, 2026.

**Fix:**
- `settings.py`: default `GEMINI_MODEL` changed from `"gemini-3-pro-preview"` → `"gemini-2.5-pro"`
- `gemini_analyzer.py`: `FALLBACK_MODELS` changed from `["gemini-3-pro-preview", "gemini-2.5-pro"]`
  → `["gemini-2.5-pro", "gemini-2.0-flash"]` (deprecated model removed completely)
- `.env.example`: `GEMINI_MODEL` updated to `gemini-2.5-pro`

**Note:** Users with `GEMINI_MODEL=gemini-3.1-pro-preview` in their `.env` are unaffected
(3.1 is separate from 3-preview, not deprecated). Only the code-level defaults/fallbacks were broken.

---

## 2026-03-07 | Prompts excluded from git — added pal_rules.md.example

**Суть:** `prompts/pal_rules.md` contains personal team information (names, portfolio URLs,
personal skill lists). Excluded from git via `.gitignore`. Added `prompts/pal_rules.md.example`
as a public template showing the structure without any personal data.

---

## 2026-02-23 | v2.1 Стабилизация — обновление зависимостей и документации

**Суть:** Выполнены все задачи milestone v2.1.

**Изменения:**
1. **Gemini модель**: `.env.example` обновлён на `gemini-3.1-pro-preview`. В `gemini_analyzer.py` старая `gemini-3-pro-preview` добавлена в fallback chain (теперь: primary → 3-pro → 2.5-pro).
2. **python-telegram-bot**: Убран cap `<21.0` из `requirements.txt`. Теперь `>=20.0` — позволяет обновиться до v22.6.
3. **openai SDK**: Удалён из `requirements.txt` (не используется — проект использует Gemini CLI).
4. **README.md**: Полностью переписан — заменены все упоминания OpenAI на Gemini, обновлены команды, структура, описание фич.

**Мотивация:** Аудит показал критическую проблему с deprecation Gemini 3 Pro (может отключиться за 2 недели), устаревшие cap-ы и мёртвую зависимость.

**Реальность:** Все изменения обратно-совместимы. `.env` пользователя НЕ затронут — нужно вручную обновить `GEMINI_MODEL` и запустить `pip install -r requirements.txt` для обновления telegram-bot.

---

---

## 2026-03-16 | [FEAT] Bid Intelligence — расширенная аналитика + AI-анализ недели

**Суть:** v2.6 milestone — 14 задач по расширению `/bidstats` с профилями, таймингом, и AI-анализом по кнопке.

**Изменения:**
1. `settings.py` — добавлены `GITHUB_TOKEN`, `GITHUB_REPO`.
2. `repository.py` — 6 новых колонок в `bid_outcomes`: `winner_hourly_rate`, `winner_reg_date`, `winner_earnings_score`, `winner_portfolio_count`, `my_time_to_bid_sec`, `winner_time_to_bid_sec`.
3. `projects.py` — новый метод `get_portfolio_count(user_id)` через `/users/0.1/portfolios/`.
4. `handlers.py/_classify_project()` — расширен запрос профиля победителя (`hourly_rate`, `registration_date`, `earnings`); добавлен расчёт timing; вызов `get_portfolio_count`.
5. `handlers.py/_fetch_bid_stats_sync()` — my_profile расширен теми же полями + `years_on_platform`, `earnings_score`, `portfolio_count`, `bid_adjustment`, `min_daily_rate`.
6. `handlers.py/_build_loss_card()` — строка `⏱ You: Xmin | Winner: Ymin`; расширенный профиль победителя.
7. `handlers.py/handle_bidstats_callback()` — блок «My profile» перед карточками; кнопка «📊 Analyse week».
8. `gemini_analyzer.py` — новая функция `analyse_weekly_bids(wins, losses, my_profile)` → Gemini CLI.
9. `src/services/github.py` — новый модуль `post_issue(token, repo, title, body, labels)` → GitHub REST API.
10. `docs/PROMPT_LOG.md` — бот автоматически добавляет ссылку на новый Issue при публикации.

**Ключевые решения:**
- Portfolio count — отдельный API-вызов, обёрнут в try/except → None при отказе API.
- Timing берётся из `bid.submitdate - project.time_submitted` (оба поля уже в ответе API).
- Gemini анализ использует тот же `_run_gemini_cli()` с основной моделью и fallback chain.
- GitHub Issue — через `requests.post`, нет новых зависимостей кроме встроенного `requests`.
