# TECH_SPEC — Bid-Assist

## Идея и Суть

**Bid-Assist** — автоматизированный бот для поиска и подачи ставок (бидов) на фриланс-проекты Freelancer.com.

**Зачем:** Экономит время на рутине — сканирует новые проекты, фильтрует мусор, анализирует через AI, генерирует персонализированные предложения и подаёт ставки автоматически или по кнопке в Telegram.

**Vision:** Полностью автономный бидинг-ассистент, который находит лучшие проекты, ставит конкурентные ставки и увеличивает win-rate за счёт AI-анализа и быстрой реакции.

---

## Стек технологий

| Компонент | Технология | Версия | Статус |
|-----------|-----------|--------|--------|
| Язык | Python | 3.11 | OK (EOL: Oct 2027) |
| Telegram SDK | python-telegram-bot | 20.x+ (cap убран) | OK (v22.6 доступна) |
| AI анализ (Call 1) | Gemini CLI | gemini-3.1-pro-preview (pro) / gemini-3-pro-preview (pool) | OK (timeout 1200s; HIGH thinking; multi-account pool) |
| AI бид (Call 2) | Gemini CLI | gemini-3.1-flash-lite-preview (pro) / gemini-3-flash-preview (pool) | OK (pool rotation on 429) |
| HTTP-клиент | requests | 2.28+ | OK |
| Валидация | pydantic + pydantic-settings | v2.x | OK (2.12.5 доступна) |
| БД | SQLite | встроенная | OK |
| Курсы валют | open.er-api.com | бесплатный | OK |
| API | Freelancer.com REST + AJAX | v0.1 | OK, стабильное |
| Контейнеризация | Docker + docker-compose | 3.8 | OK |
| ~~openai SDK~~ | удалён | — | УДАЛЁН (не использовался) |

---

## Milestones (Этапы)

### MVP (DONE)
- [x] Polling Freelancer API по навыкам
- [x] Фильтрация: бюджет, навыки, страна, чёрный список, валюта, язык
- [x] AI-анализ через Gemini CLI (analyze.md + bid_writer.md, двухвызовная архитектура)
- [x] Telegram-уведомления с кнопками (Place Bid, Edit Amount, Edit Proposal)
- [x] Авто-бидинг с отслеживанием ранга
- [x] SQLite хранилище (обработанные проекты, очередь, история, настройки)
- [x] Конвертация валют (60+ валют)
- [x] /status, /settings, /bidstats, /help команды
- [x] Multi-chat поддержка

### v2.1 — Стабилизация (DONE)
- [x] Обновить Gemini модель → gemini-3.1-pro-preview (+ старая модель в fallback chain)
- [x] Убрать cap `<21.0` для python-telegram-bot
- [x] Удалить openai из requirements.txt (не используется)
- [x] Обновить README.md (описывал OpenAI вместо Gemini)

### v2.2 — Качество бидов (DONE)
- [x] Anti-bot check в bid_writer.md — AI сканирует описание на "напиши X в начале" перед написанием
- [x] Hook из description, не из title — явная инструкция брать детали из текста клиента
- [x] Запрет клише-фраз ("is a clear task", "I am free now" и др.) — расширен список FORBIDDEN PHRASES
- [x] Вариативность CTA — запрет повторять одну фразу каждый раз
- [x] Portfolio nudge — опциональная строка "check my portfolio in my profile"
- [x] max_bid_count настраивается через /settings (пресеты: 25/50/75/100/150/999)
- [x] Проверка bid_count прямо перед place_bid() в analysis_loop (last-mile gate)

### v2.3 — Мульти-аккаунт (DONE)
- [x] `--env` флаг при запуске — выбор .env файла для конкретного аккаунта
- [x] `PROMPTS_DIR` в .env — отдельная папка промптов на аккаунт (своя persona)
- [x] `python run.py` без аргументов — запускает оба аккаунта как сабпроцессы с префиксами [yehia]/[ymka]
- [x] Отдельная БД на аккаунт (`DB_PATH` в .env)
- [x] `CLIENT USERNAME` передаётся в bid_writer, правило в промпте (обратиться по имени если подходит)

### v2.4 — Настройки бидинга + UX (DONE)
- [x] `bid_adjustment` — настраиваемый % отклонения от рыночной цены (пресеты: -50/-25/-10/0/+10/+25), кнопка в /settings
- [x] `winner_proposal TEXT` в `bid_outcomes` — хранит реальный текст предложения победителя (вместо "xxxx..." placeholder)
- [x] "Show more ↓" пагинация для лоссов в /bidstats (кнопка когда лоссов > 10)
- [x] `prompts_ymka/bid_writer.md` — новая persona для ymka: 0 отзывов → агрессивное ценообразование (-25% от рынка), обязательный portfolio nudge iymka.com, длиннее 4-7 предложений

### v2.5 — Shared AI cache (DONE)
- [x] `SharedAnalysisRepository` — общий SQLite кэш Call 1 (`data/shared_analysis.db`, WAL mode)
- [x] Атомарный `try_claim()` — исключает гонку между аккаунтами за один проект
- [x] `analysis_loop` проверяет кэш перед Call 1: cache hit → пропускает Gemini Call 1, cache miss → claim → run → store
- [x] `analyze_project()` принимает `feasibility: Optional[dict]` — пропускает Call 1 если передан
- [x] `cleanup_loop` чистит устаревшие записи shared_analysis каждый час (тот же паттерн что project_queue)

### v2.6 — Аналитика (DONE)
- [x] Расширенный профиль победителя в LOSS-карточках: hourly rate, лет на платформе, earnings score, portfolio count
- [x] Timing в карточках: `⏱ You: Xmin | Winner: Ymin`
- [x] My profile header в `/bidstats weekly` (один раз, до карточек)
- [x] Кнопка «📊 Analyse week» → AI-анализ через Gemini CLI
- [x] Результат анализа в Telegram + GitHub Issue (`src/services/github.py`)
- [x] `docs/PROMPT_LOG.md` — автоматически пополняется ссылкой на Issue

### v2.7 — Качество промптов (DONE)
- [x] `ProjectOwner.display_name` (из `public_name` API) — пробрасывается через весь pipeline вместо username
- [x] `owner_display_name` колонка в `project_queue`
- [x] `write_bid()` / `analyze_project()` / `force_bid_analysis()` принимают `owner_name`
- [x] В промпте `CLIENT NAME:` (не `CLIENT USERNAME:`) — правило: только для подробных постов (5+ предложений)
- [x] Промпты: 2–8 веб-поисков в analyze.md, n8n вместо Zapier, em dash → дефис, мягкие CTA-вопросы
- [x] `bid_writer.md`: убран MANDATORY WEB SEARCH, упрощён STEP 0, обновлён пример

### v2.8 — Надёжность и UX (DONE)
- [x] `_recheck_queue_filters()` в `run.py` — двойная проверка всех фильтров при выходе из очереди (возраст, бюджет, валюта, blacklist, verified, страна, preferred-only, bid_count). Ноль AI-вызовов для протухших проектов
- [x] Авто-бид: fresh bid_count из API прямо перед размещением (вместо стale данных из очереди)
- [x] `analysis_loop` принимает `project_service` для fresh API-вызовов
- [x] Настройка «Verified account» в /settings (была «Crypto» — переименована во избежание путаницы)
- [x] Предупреждение о конкурентах при ручном биде: редактирует оригинальное сообщение вместо нового (правило: одно сообщение на проект)
- [x] Кнопки «✏️ Edit Amount» и «✏️ Edit Proposal» сохраняются в сообщении с предупреждением
- [x] `escape_markdown_v2` вынесен в верхний импорт `handlers.py`

### v2.9 — Multi-account Gemini pool + Spinner UX (DONE)
- [x] `GEMINI_HOME_PRIMARY` + `GEMINI_HOME_POOL` в .env — 1 pro + N бесплатных аккаунтов
- [x] `_run_gemini_cli(prompt, primary_model, pool_model)` — пробует pro, затем pool по очереди
- [x] Cooldown per `(home_dir, model)` на 1ч при 429 QUOTA_EXHAUSTED
- [x] `consume_exhaustion_flag()` — `analysis_loop` детектирует полное исчерпание → sleep 30 мин
- [x] `send_quota_exhausted_notification()` в `notifier.py` — Telegram-уведомление при полном исчерпании
- [x] Pro-модели: `gemini-3.1-pro-preview` (Call 1), `gemini-3.1-flash-lite-preview` (Call 2)
- [x] Pool-модели: `gemini-3-pro-preview` (Call 1 fallback), `gemini-3-flash-preview` (Call 2 fallback)
- [x] Spinner ✏️ — кнопка для ввода точного числа с клавиатуры (ConversationHandler `WAITING_SPINNER`)
- [x] `escape_markdown_v2` вынесен в верхний импорт `handlers.py`

### v3.0 — Масштабирование (FUTURE)
- [ ] Dashboard (web-интерфейс) для аналитики
- [ ] A/B-тестирование bid-текстов
- [ ] Webhook-режим вместо polling для Telegram (для VPS)
- [ ] Интеграция с другими фриланс-платформами

---

## Текущий статус

**Версия:** 2.9.0
**Состояние:** Рабочий проект, используется в продакшене. Два аккаунта: yehia + ymka.

**Что готово:** Полный цикл — от обнаружения проекта до подачи ставки. Telegram-бот с командами, авто-бид, AI-анализ, двойная фильтрация (до и после очереди), история, статистика. Bid-качество улучшено (anti-bot check, personalized hook, запрет клише, мягкие CTA). Мульти-аккаунт из коробки. Настраиваемый % отклонения цены от рынка. Shared AI cache между аккаунтами. Multi-account Gemini pool с автоматической ротацией при исчерпании квоты.

**Решённые проблемы (v2.1–v2.4):**
1. ~~`gemini-3-pro-preview` deprecation~~ → обновлён на `gemini-3.1-pro-preview`, старая модель в fallback chain
2. ~~`python-telegram-bot` заблокирован на v20.x~~ → cap `<21.0` убран
3. ~~`openai` SDK лишняя зависимость~~ → удалён из requirements.txt
4. ~~`max_bid_count` только в .env~~ → DB-backed, настраивается через /settings
5. ~~`-10%` от рынка захардкожено~~ → `bid_adjustment` DB-setting, пресеты -50..+25
6. ~~winner_proposal в /bidstats показывался как "xxxx..."~~ → колонка `winner_proposal TEXT` хранит реальный текст

**Решённые проблемы (v2.5–v2.8):**
1. ~~Дублирование AI Call 1 между аккаунтами~~ → Shared SQLite кэш с атомарным `try_claim()`
2. ~~preferred-only проекты тратили AI токены~~ → проверка на входе из очереди
3. ~~Проект мог пройти AI после устаревания в очереди~~ → `_recheck_queue_filters()` на выходе из очереди
4. ~~Авто-бид проверял stale bid_count~~ → fresh API вызов прямо перед размещением
5. ~~Настройка "Crypto" сбивала с толку~~ → переименована в "Verified account"
6. ~~Предупреждение о конкурентах создавало новое сообщение~~ → редактирование оригинального

**Решённые проблемы (v2.9):**
1. ~~3 проекта в сутки из-за квоты gemini-3.1-pro-preview~~ → Multi-account pool: 1 pro + 4 free аккаунтов, ротация на 429
2. ~~Ввод точного числа в спиннере недоступен~~ → кнопка ✏️ открывает keyboard input mode

**Следующая задача:** Аналитика win-rate по категориям/навыкам
