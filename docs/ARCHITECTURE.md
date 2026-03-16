# ARCHITECTURE — Bid-Assist

## Общая схема

```
[Freelancer API] → polling_loop → [Filters] → project_queue → analysis_loop → [Gemini CLI]
                                                                     ↓
                                                              [Telegram Bot] ← user commands
                                                                     ↓
                                                              [BiddingService] → [Freelancer API]
                                                                     ↓
                                                                  [SQLite DB]
```

**3 фоновых цикла** (asyncio tasks в `run.py`):
1. `polling_loop` — опрос Freelancer API, фильтрация, добавление в очередь
2. `analysis_loop` — AI-анализ через Gemini, уведомления, авто-бидинг
3. `cleanup_loop` — очистка старых записей (каждый час)

---

## Структура файлов

```
bid-assist/
├── run.py                          # Единая точка входа: 3 async-цикла + Telegram bot
├── pyproject.toml                  # Конфигурация проекта, зависимости
├── requirements.txt                # Зависимости (pip)
├── Dockerfile                      # Docker-образ (python:3.11-slim)
├── docker-compose.yml              # Docker Compose конфиг
├── .env.example                    # Шаблон переменных окружения
├── .env.yehia                      # Конфиг аккаунта yehia (gitignored)
├── .env.ymka                       # Конфиг аккаунта ymka (gitignored)
│
├── prompts_yehia/                  # Промпты аккаунта yehia (gitignored)
│   ├── analyze.md                  # Call 1: feasibility analysis
│   └── bid_writer.md               # Call 2: bid text, persona = yehia
├── prompts_ymka/                   # Промпты аккаунта ymka (gitignored)
│   ├── analyze.md                  # Call 1: feasibility analysis
│   └── bid_writer.md               # Call 2: bid text, persona = ymka
├── docs/                           # Документация проекта
│   ├── TECH_SPEC.md, ARCHITECTURE.md, DECISION_LOG.md, _CODER_RULES.md
├── data/                           # Runtime-данные (gitignored)
│   ├── yehia.db                    # Per-account SQLite БД (аккаунт yehia)
│   ├── ymka.db                     # Per-account SQLite БД (аккаунт ymka)
│   └── shared_analysis.db          # Общий кэш Call 1 (оба аккаунта, WAL mode)
├── logs/                           # Логи (gitignored)
│   └── bot_debug.log
│
├── src/
│   ├── __init__.py                 # Пакет src
│   │
│   ├── config/
│   │   ├── __init__.py             # Экспорт settings + констант
│   │   ├── settings.py             # Pydantic Settings — загрузка .env
│   │   └── constants.py            # URL-адреса API, endpoints
│   │
│   ├── core/
│   │   ├── __init__.py
│   │   └── exceptions.py           # Иерархия исключений (APIError, BidPlacementError, etc.)
│   │
│   ├── models/
│   │   ├── __init__.py             # Экспорт моделей
│   │   ├── project.py              # Project, ProjectBudget, ProjectOwner, BidStats
│   │   └── bid.py                  # Bid, BidResult, AIAnalysis, Verdict enum
│   │
│   ├── filters/
│   │   ├── __init__.py             # Экспорт фильтров
│   │   ├── base.py                 # BaseFilter (ABC)
│   │   ├── skill_filter.py         # Фильтр по навыкам
│   │   ├── budget_filter.py        # Фильтр по бюджету (BudgetFilter)
│   │   ├── blacklist_filter.py     # Чёрный список ключевых слов
│   │   ├── country_filter.py       # Фильтр по стране клиента
│   │   └── pipeline.py             # FilterPipeline — цепочка фильтров
│   │
│   └── services/
│       ├── __init__.py
│       │
│       ├── currency.py             # Конвертация валют (open.er-api.com + fallback)
│       │
│       ├── ai/
│       │   ├── __init__.py
│       │   └── gemini_analyzer.py  # AI-анализ: Gemini CLI subprocess, parse VERDICT/SUMMARY/BID
│       │
│       ├── freelancer/
│       │   ├── __init__.py         # Экспорт FreelancerClient, ProjectService, BiddingService
│       │   ├── client.py           # HTTP-клиент с OAuth (REST + AJAX API)
│       │   ├── projects.py         # Получение проектов, деталей, страны владельца
│       │   └── bidding.py          # Подача ставок, ранг, история бидов
│       │
│       ├── storage/
│       │   ├── __init__.py         # Экспорт ProjectRepository
│       │   └── repository.py       # SQLite: 7 таблиц, runtime settings, очередь, история
│       │
│       └── telegram/
│           ├── __init__.py
│           ├── handlers.py         # Команды (/status, /settings, /bidstats), callback-кнопки
│           └── notifier.py         # Форматирование и отправка уведомлений, custom emoji
│
├── _verify/                        # Verify-скрипты (запускать вручную после изменений)
│   ├── test_01_gitignore_log.py    # *.log исключён из git
│   ├── test_02_client_put_params.py # client.put() принимает params
│   ├── test_03_retract_bid_uses_client.py # retract_bid() не использует raw requests
│   ├── test_04_bid_writer_prompt.py # bid_writer.md содержит все обязательные секции
│   └── test_05_validate_bid_text.py # _validate_bid_text() happy/edge/fail cases
│
└── tests/
    ├── __init__.py
    ├── conftest.py                 # Pytest fixtures
    └── test_filters.py             # Тесты фильтров
```

---

## Модули — подробное описание

### `run.py` — Точка входа
- **Без `--env`**: запускает оба аккаунта (`yehia` + `ymka`) как сабпроцессы, стримит их вывод с префиксами `[yehia]` / `[ymka]`. Ctrl+C завершает оба.
- **С `--env .env.yehia`**: запускает один аккаунт. `ENV_FILE` устанавливается в `os.environ` до импортов, чтобы `Settings` подхватил нужный файл.
- Инициализирует все сервисы (repo, client, services, notifier)
- Запускает Telegram bot polling + 3 фоновых asyncio-таска
- Graceful shutdown по SIGINT/SIGTERM
- Логирование в файл `logs/bot_debug.log` + stdout
- Фильтрация проектов inline (budget, currency, language, age, preferred-only, verification, country, blacklist)

### `src/config/settings.py` — Конфигурация
- `Settings(BaseSettings)` — загружает файл из `os.environ["ENV_FILE"]` (default: `.env`) через pydantic-settings
- `_env_file = os.getenv("ENV_FILE", ".env")` вычисляется на уровне модуля до создания класса
- Поля: токены (Freelancer, Telegram, Gemini), фильтры (бюджет, страны, валюты, языки, навыки, blacklist), AI-настройки
- `PROMPTS_DIR` — папка промптов для этого аккаунта (default: `prompts`)
- Свойства-парсеры для comma-separated значений (skill_ids, blocked_countries, etc.)
- Singleton: `settings = Settings()`

### `src/services/ai/gemini_analyzer.py` — AI-анализ (двухвызовная архитектура)
- **Call 1** `analyze_feasibility()` — запускает `analyze.md` через Gemini CLI, парсит: VERDICT (PASS/SKIP), DAYS, SUMMARY
- **Pricing** `_calculate_amount(days, avg_bid_usd, budget_min_usd, budget_max_usd, min_daily_rate, bid_adjustment)` — детерминированная формула: `multiplier = 1 + bid_adjustment/100`, `target = avg_bid × multiplier`, `floor = days × min_daily_rate`, `amount = round(max(floor, target) / 10) × 10`
- **Call 2** `write_bid()` — запускает `bid_writer.md`, получает BID-текст. Принимает `owner_name` (display_name или username) → вставляет `CLIENT NAME:` в промпт
- `analyze_project(... bid_adjustment=-10)` — оркестратор: Call 1 → pricing → Call 2 (SKIP обрывает после Call 1). Принимает `owner_name`, `bid_adjustment`.
- `force_bid_analysis(... bid_adjustment=-10)` — принудительный BID: Call 1 для DAYS (SKIP игнорируется) → pricing → Call 2. Принимает `owner_name`, `bid_adjustment`.
- Пути к промптам: `_ROOT / settings.prompts_dir / "analyze.md"` — настраиваются через `PROMPTS_DIR` в `.env`
- Отдельные fallback-цепочки: `ANALYSIS_FALLBACK_MODELS` / `BID_FALLBACK_MODELS`
- Cooldown 5 мин на модель при 429

### `src/services/freelancer/client.py` — HTTP-клиент
- OAuth через заголовок `Freelancer-OAuth-V1`
- GET/POST/PUT/DELETE запросы к REST API (`api.freelancer.com/api/projects/0.1/`)
- `put(endpoint, data=None, params=None)` — поддерживает query params (нужно для `?action=retract`)
- Дополнительные методы через AJAX API (`freelancer-auth-v2`)
- Таймаут: 30 секунд
- Обработка ошибок → `FreelancerAPIError`

### `src/services/freelancer/projects.py` — Сервис проектов
- `get_active_projects()` — поиск по навыкам и бюджету
- `get_project_details()` — полная информация о проекте
- `get_project_bids()` — список ставок на проект
- `get_project_owner_country()` — определение страны клиента (multi-fallback)

### `src/services/freelancer/bidding.py` — Сервис бидинга
- `place_bid()` — подача ставки через API
- `retract_bid(bid_id)` — отзыв ставки через PUT `?action=retract` (использует `self._client`, не raw requests)
- `get_bid_rank()` — позиция ставки среди конкурентов
- `get_my_bidded_project_ids()` — список проектов с моими ставками
- `get_all_my_bids()` — пагинированная загрузка всех бидов
- `get_remaining_bids()` — остаток бидов в аккаунте
- `strip_markdown()` — очистка markdown из bid-текста

### `src/services/storage/shared_repository.py` — Общий кэш Call 1

- Отдельный файл `data/shared_analysis.db` — доступен обоим процессам (yehia + ymka)
- WAL mode: безопасный concurrent доступ без блокировок
- `try_claim(project_id)` — атомарный `INSERT OR IGNORE`, возвращает `True` если мы первые
- `get_result(project_id)` — возвращает `{verdict, days, summary}` если `done`/`skip` и < 24ч
- `store_result(project_id, verdict, days, summary)` — сохраняет результат Call 1
- `release_claim(project_id)` — освобождает зависший `in_progress` слот (Call 1 упал)
- `cleanup_stale(max_age_hours)` — вызывается из `cleanup_loop`, удаляет устаревшие записи
- Путь выводится из `DB_PATH`: `Path(settings.db_path).parent / "shared_analysis.db"`

### `src/services/storage/repository.py` — SQLite хранилище
**7 таблиц:**
| Таблица | Назначение |
|---------|-----------|
| `processed_projects` | Обработанные проекты (дедупликация) |
| `bid_history` | История всех ставок (успех/ошибка, уведомления) |
| `pending_bids` | Staging-зона для Telegram кнопок |
| `project_queue` | Очередь на AI-анализ; колонка `is_preferred_only` — пропускает AI если True |
| `runtime_settings` | Shared state (paused, poll_interval, auto_bid, budget, min_daily_rate, **max_bid_count**, **bid_adjustment**, etc.) |
| `user_settings` | Multi-user настройки (chat_id, skills, keywords) |
| `bid_outcomes` | Кэш результатов (WIN/LOSS/SEALED/OPEN); колонки `winner_amount`, `winner_proposal TEXT`, `winner_proposal_len`, `winner_reviews` |

### `src/services/telegram/handlers.py` — Обработчики команд
- `/status` — статус бота + кнопки управления
- `/settings` — настройки: бюджет, интервал, авто-бид, min rate, **max bids (конкуренты)**, **bid adjustment**, фильтры
- `/bidstats` — статистика бидов с классификацией (WIN/LOSS/SEALED/OPEN); пагинация лоссов "Show more ↓"
- `/help` — справка
- Callback queries: bid, edit_amount, edit_text, ask_bid, pause, resume, auto_bid, **max_bids**, **bid_adj**, **more_losses**
- Кэш bid-статистики (30 мин)
- Пресеты max_bid_count: 25 / 50 / 75 / 100 / 150 / 999 (∞)
- Пресеты bid_adjustment: -50 / -25 / -10 / 0 / +10 / +25 (%)

### `src/services/telegram/notifier.py` — Уведомления
- Форматирование сообщений с custom emoji (Telegram Premium)
- `send_gpt_decision_notification_to_user()` — BID уведомление с кнопками
- `send_skip_notification_to_user()` — SKIP уведомление
- `send_auto_bid_notification()` — авто-бид результат с рангом
- `schedule_bid_update()` — отложенное обновление статистики бидов в сообщении

### `src/services/currency.py` — Валюты
- `to_usd()` / `from_usd()` — конвертация через open.er-api.com
- Кэш курсов 24 часа
- Fallback: хардкод 65+ валютных курсов
- `round_up_10()` — округление вверх до десятков

### `src/filters/` — Система фильтрации
- `BaseFilter` (ABC) — интерфейс: `passes()`, `get_rejection_reason()`, `name`
- `SkillFilter` — совпадение хотя бы одного навыка
- `BudgetFilter` — проверка диапазона бюджета
- `BlacklistFilter` — ключевые слова в title+description
- `CountryFilter` — whitelist/blacklist стран + block_unknown
- `FilterPipeline` — цепочка фильтров с batch-обработкой

---

## Внешние зависимости

| Сервис | Назначение | Модуль |
|--------|-----------|--------|
| Freelancer REST API | Проекты, ставки, профили | freelancer/ |
| Freelancer AJAX API | Лимиты бидов, страна владельца | freelancer/client.py |
| Gemini CLI | AI-анализ проектов | ai/gemini_analyzer.py |
| Telegram Bot API | Уведомления, UI | telegram/ |
| open.er-api.com | Курсы валют | currency.py |
| SQLite | Локальная БД | storage/repository.py |
