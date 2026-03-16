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
| AI анализ (Call 1) | Gemini CLI | gemini-3.1-pro-preview | OK (fallback: 2.5-pro; timeout 1200s; HIGH thinking) |
| AI бид (Call 2) | Gemini CLI | gemini-2.5-flash | OK (fallback: 2.5-pro) |
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
- [ ] Аналитика win-rate по категориям/навыкам (future)
- [ ] Webhook-режим вместо polling для Telegram (для VPS)

### v3.0 — Масштабирование (FUTURE)
- [ ] Dashboard (web-интерфейс) для аналитики
- [ ] A/B-тестирование bid-текстов
- [ ] Интеграция с другими фриланс-платформами

---

## Текущий статус

**Версия:** 2.6.0
**Состояние:** Рабочий проект, используется в продакшене. Два аккаунта: yehia + ymka.

**Что готово:** Полный цикл — от обнаружения проекта до подачи ставки. Telegram-бот с командами, авто-бид, AI-анализ, фильтрация, история, статистика. Bid-качество улучшено (anti-bot check, personalized hook, запрет клише). Мульти-аккаунт из коробки. Настраиваемый % отклонения цены от рынка.

**Решённые проблемы (v2.1):**
1. ~~`gemini-3-pro-preview` deprecation~~ → обновлён на `gemini-3.1-pro-preview`, старая модель в fallback chain
2. ~~`python-telegram-bot` заблокирован на v20.x~~ → cap `<21.0` убран
3. ~~`openai` SDK лишняя зависимость~~ → удалён из requirements.txt
4. ~~`README.md` устарел~~ → полностью переписан

**Решённые проблемы (v2.2):**
1. ~~AI не реагировал на anti-bot проверки~~ → STEP 0 в bid_writer.md
2. ~~Hook пересказывал title вместо description~~ → явная инструкция брать из description
3. ~~Повторяющиеся клише в каждом биде~~ → расширен FORBIDDEN PHRASES список
4. ~~`retract_bid()` использовал raw requests~~ → переведён на `self._client.put(params=...)`
5. ~~`max_bid_count` только в .env~~ → DB-backed, настраивается через /settings
6. ~~`*.log` не исключались из git в корне~~ → добавлен в .gitignore

**Решённые проблемы (v2.3):**
1. ~~Один .env, один аккаунт~~ → `--env` флаг + `PROMPTS_DIR` + мульти-запуск из коробки
2. ~~AI не знал имени клиента~~ → `owner_username` пробрасывается через весь pipeline в prompt
3. ~~bid_count не проверялся перед ручным бидом~~ → last-mile gate в handlers.py

**Решённые проблемы (v2.4):**
1. ~~`-10%` от рынка захардкожено~~ → `bid_adjustment` DB-setting, кнопка в /settings, пресеты -50..+25
2. ~~winner_proposal в /bidstats показывался как "xxxx..."~~ → колонка `winner_proposal TEXT` хранит реальный текст
3. ~~В /bidstats нет пагинации лоссов~~ → "Show more ↓" кнопка + `more_losses` callback
4. ~~ymka промпт не отражал реальность нового аккаунта~~ → новая persona: ценообразование -25%, обязательный iymka.com

**Следующая задача:** v2.6 — Аналитика win-rate и улучшение промптов
