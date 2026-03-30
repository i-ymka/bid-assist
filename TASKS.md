# TASKS: Единый оркестратор (v3.0)

**Спека:** `SPEC_orchestrator.md`
**Ветка:** `orchestrator`
**Статус:** В работе

---

## Фаза 1: Фундамент (новые файлы, старый код не трогаем)

- [x] T001 AccountConfig dataclass — модель одного аккаунта → `src/config/account.py`
  - Поля: name, freelancer_token, freelancer_auth_v2, telegram_token, telegram_chat_ids, prompts_dir, gemini_home_primary, gemini_home_pool
  - `load_from_env(path: str) -> AccountConfig` — парсит .env файл
  - Чистый dataclass, без зависимости от pydantic Settings

- [x] T002 [P] Единая БД — новая схема с тегами → `src/services/storage/unified_repo.py`
  - `projects` — project_id PK, title, description, budget_min/max, currency, country, bid_count, avg_bid, url, time_submitted, fetched_at, status (pending/analyzing/done/skipped), call1_verdict, call1_days, call1_summary
  - `project_accounts` — (project_id, account_name) PK + price_ok, bid_placed, bid_id
  - `bid_history` — как сейчас + `account TEXT`
  - `pending_bids` — как сейча�� + `account TEXT`
  - `runtime_settings` — ключ = `account:setting` (e.g. `ymka:budget_min`)
  - `bid_outcomes` — как сейчас + `account TEXT`
  - WAL mode, busy_timeout=5000
  - Методы: add_project, tag_project, get_pending, mark_analyzing, store_call1, get_tagged_accounts, remove_tag, get/set settings per account

- [x] T003 OrchestratorConfig — загрузка всех аккаунтов + merged фильтры → `src/config/loader.py`
  - `discover_accounts()` — находит `.env.*` (кроме .env.example)
  - `OrchestratorConfig` — list[AccountConfig] + merged_budget_min/max, merged_skill_ids, merged_countries и т.д.
  - Merged = union/расширение диапазонов всех аккаунтов
  - Зависит от T001

---

## Фаза 2: Ядро pipeline (новые модули)

- [x] T004 ProjectTagger — per-account фильтрация и тегирование → `src/filters/tagger.py`
  - `ProjectTagger(accounts: list[AccountConfig], repo: UnifiedRepo)`
  - `tag_project(project_data) -> list[str]` — прогоняет через фильтры каждого аккаунта
  - Использует существующие BudgetFilter, BlacklistFilter, CountryFilter
  - Per-account: бюджет, blacklist, страны, валюты, языки, max_bid_count, verified, preferred
  - Записывает теги в project_accounts

- [x] T005 Unified polling loop → `src/orchestrator/polling.py`
  - `async def polling_loop(config: OrchestratorConfig, repo: UnifiedRepo, tagger: ProjectTagger)`
  - Один запрос к Freelancer API с merged параметрами
  - Для каждого нового проекта: tag → если теги есть, сохранить в projects + project_accounts
  - Если 0 тегов → пропустить
  - Не блокируется ожиданием Call 1

- [x] T006 Load-based Gemini ротация → `src/services/ai/gemini_analyzer.py` (модификация)
  - `_active_counts: dict[str, int]` — счётчик активных CLI per home_dir
  - В `_run_gemini_cli()`: пропускать home_dir если active >= 5
  - Инкремент/декремент через try/finally
  - Существующая cooldown-ротация сохр��няется

- [x] T007 Parallel Call 1 dispatcher → `src/orchestrator/analyzer.py`
  - `async def analysis_dispatcher(config, repo)`
  - Цикл: берёт проекты со статусом `pending`
  - Для каждого: `asyncio.create_task(run_call1(project_id))` — fire-and-forget
  - `asyncio.Semaphore(5)` — макс 5 одновременных Call 1
  - `run_call1()`: вызывает `analyze_feasibility()`, записывает результат в projects, меняет статус

- [x] T008 Post-Call-1 + Call 2 + bidding → `src/orchestrator/bidder.py`
  - `async def bid_dispatcher(config, repo, account_services)`
  - Цикл: берёт проекты status=done, verdict=PASS
  - Для каждого тегированного аккаунта: `_calculate_amount()` с его настройками
  - Если цена не прошла → remove_tag(project_id, account)
  - Оставшиеся: `asyncio.create_task(run_call2_and_bid(project, account))`
  - `run_call2_and_bid()`: write_bid (с промптом аккаунта) → place_bid (с OAuth аккаунта) → update project_accounts

---

## Фаз�� 3: Интеграция (per-account сервисы)

- [x] T009 Per-account сервисы — инициализация → `src/orchestrator/services.py`
  - `AccountServices` dataclass: name, freelancer_client, bidding_service, notifier, telegram_app
  - `init_account_services(account: AccountConfig, repo: UnifiedRepo) -> AccountServices`
  - Каждый аккаунт: свой FreelancerClient (OAuth), BiddingService, Notifier (Telegram token)

- [x] T010a Инжекция контекста в bot_data → `src/orchestrator/telegram.py`
  - Функция `setup_bot(account, repo, services) -> Application`
  - Пишет в `app.bot_data`: account_name, repo (UnifiedRepo), bidding_service, project_service, notifier
  - Вызывает `setup_handlers(app)`
  - `start_all_bots()` / `stop_all_bots()`

- [x] T010b Helper для handlers: получение контекста + AccountRepoAdapter → `handlers.py` + `repo_adapter.py`
  - `_ctx(context)` → возвращает (account_name, repo) из `context.bot_data`
  - `_svc(context)` → возвращает (bidding_service, project_service) из `context.bot_data`
  - Убрать глобальные `_bidding_service`, `_project_service`, `_init_repo`

- [x] T010c Замена ProjectRepository() → _ctx(context) во ВСЕХ handlers → `handlers.py`
  - ~30 мест: `repo = ProjectRepository()` → `account, repo = _ctx(context)`
  - Все вызовы repo.get/set методов: добавить `account` первым аргументом
  - get_bidding_service() / get_project_service() → _svc(context)

- [x] T010d Settings/keyboard уже работают через adapter (repo.is_paused() → adapter.is_paused())
  - Принимают `(account, repo)` вместо `(repo)`
  - Все repo.is_paused() → repo.is_paused(account) и т.д.

- [x] T012 Per-account notifier (per-account через services init, bot_data injection)

---

## Ф��за 4: Сборка и де��лой

- [x] T013 Новый run.py → `run_orchestrator.py` (отдельный файл, старый run.py не тронут)
  - Загрузка OrchestratorConfig
  - Инициализация UnifiedRepo, per-account services
  - Запуск asyncio tasks: polling_loop, analysis_dispatcher, bid_dispatcher, cleanup_loop
  - Telegram bots polling в том же event loop
  - Graceful shutdown
  - CLI: `python run.py` (автодетект .env.*) или `python run.py --accounts .env.ymka`

- [x] T014 [P] Cleanup loop → `src/orchestrator/cleanup.py`
  - Удаление проектов старше 24ч из projects + project_accounts
  - Удаление старых bid_outcomes
  - Без shared_repository (больше не нужен)

- [x] T015 Логирование (account prefix уже в bidder/polling через `[{account_name}]`)
  - Каждое per-account сообщение: `[ymka]` / `[yehia]` prefix
  - Один поток вывода

- [x] T015 Логирование — цвета аккаунтов
  - `_ACCT_COLORS + _acct_color()` в `gemini_analyzer.py`: ymka=plum1, yehia=cornflower_blue
  - Все логи bidder.py: `[{ac}]{account_name}[/{ac}]`
  - YEP показывает `target $X, floor $Y`
  - SENT = отправлено в TG (manual), BID  = бид размещён (auto)

- [ ] T016 Деплой на сервер
  - git push orchestrator → git pull на сервере → тест
  - Один systemd-сервис `bid-assist.service` (Restart=always)
  - Остановить bid-ymka + bid-yehia, запустить bid-assist

- [ ] T017 Ctrl+C shutdown (НЕ РЕШЕНО)
  - signal.signal / loop.add_signal_handler / remove+signal — ничего не работает
  - python-telegram-bot или asyncio перезаписывают handler
  - Workaround: pkill -9 из другого терминала

- [x] T018 Per-account skill check в tagger
  - tagger._check_filters не проверяет скиллы аккаунта
  - Нужно до разделения скиллов между аккаунтами
  - Freelancer отклонит бид если у аккаунта нет требуемого скилла

---

## Фаза 5: Feature parity с run.py (аудит 2026-03-30)

> Найдено систематическим сравнением — вещи которые есть в run.py но не попали в orchestrator при переносе.
> Принцип: берём реализацию из run.py и адаптируем под multi-account, не пишем с нуля.

- [x] T021 Skip/NOPE нотификации — при `price < floor` (bidder.py:76-79) добавить `send_skip_notification_to_user()` с проверкой `notif_mode` (all/bids_plus/bids) как в run.py:914-919 → `src/orchestrator/bidder.py`

- [x] T022 Gemini quota exhaustion — при исчерпании всех аккаунтов в `analyzer.py` делать 30min паузу и слать Telegram нотификацию (как run.py через exhaustion flag) → `src/orchestrator/analyzer.py`

- [x] T023 Language field — сохранять `language` в таблицу `projects` при поллинге (поле есть в схеме, tagger фильтрует по нему, но оно никогда не заполняется из API) → `src/orchestrator/polling.py`

- [x] T024 Last-mile bid_count check — прямо перед `bidding_service.place_bid()` делать свежую проверку `fresh_bid_count > max_bids` (как run.py:699-709), сейчас pre-bid recheck есть но этот check отсутствует → `src/orchestrator/bidder.py`

- [x] T019 Fair price guard — `bidder.py`
  - Если `fair_price > amount * 2` → NOPE, бид не размещается
  - Работает для обоих путей (manual + auto), до развилки `is_auto_bid()`
  - Лог: `(bid $350, AI est $800 = 2.3x)`

- [x] T020 AccountRepoAdapter — полное покрытие методов handlers.py
  - Аудит: все 41 метод из handlers.py покрыты
  - Добавлены: `get_bid_stats`, `get_recent_bids_full`, `get_processed_count`, `set_max_project_age`, `update_bid_record_on_place`

---

## Зависимости

```
T001 → T003 → T004 → T005
                 ↘
T002 ──→ T004    T007 → T008
     ──→ T005
     ──→ T007
     ──→ T008

T006 (независимая модификация gemini_analyzer)

T009 → T010 ──→ T013
T011 ─────────→ T013
T012 ─────────→ T013
T014, T015 ───→ T013
```

**Параллельные группы:**
- **T001 + T002 + T006** — три независимых фундамента
- **T004 + T007** — после T001+T002
- **T009 + T011 + T012** — после T002
- **T014 + T015** — после T002

**Критический путь:** T001 → T003 → T005 → T007 → T008 → T013

---

## Порядок старт��

1. **T001** AccountConfig + **T002** Unified DB + **T006** Load rotation — параллел��но
2. **T003** OrchestratorConfig — после T001
3. **T004** Tagger + **T005** Polling — после T002+T003
4. **T007** Call 1 dispatcher — после T002
5. **T008** Call 2 + bidding — после T007
6. **T009** Per-account services — после T002
7. **T010–T012** Telegram — после T009
8. **T013** run.py — финальная сборка
9. **T014–T016** Polish + deploy
