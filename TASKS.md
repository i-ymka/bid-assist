# TASKS: Bid Intelligence — расширенная аналитика + AI-анализ недели

**Спека:** `SPEC_bid_intelligence.md`
**Статус:** В работе

---

## Фаза 1: Инфраструктура

- [ ] T001 Добавить `github_token: str` и `github_repo: str` в `Settings` (`settings.py`) + добавить плейсхолдеры в `.env.example` → `src/config/settings.py`, `.env.example`

- [ ] T002 Добавить 6 новых колонок в `bid_outcomes` через inline-миграции (`ALTER TABLE IF NOT EXISTS` паттерн): `winner_hourly_rate REAL`, `winner_reg_date INTEGER`, `winner_earnings_score REAL`, `winner_portfolio_count INTEGER`, `my_time_to_bid_sec INTEGER`, `winner_time_to_bid_sec INTEGER` → `src/services/storage/repository.py`

- [ ] T003 Обновить `set_bid_outcome()` — принять и сохранить 6 новых полей из `winner_detail` dict (timing + extended profile). Обновить `get_bid_outcome_full()` — вернуть все 6 новых полей в словаре → `src/services/storage/repository.py`

---

## Фаза 2: Получение данных

- [ ] T004 [P] Добавить `get_portfolio_count(user_id: int) -> Optional[int]` в `projects.py` — GET `/users/0.1/portfolios/?users[]={user_id}&compact=true&limit=0`, вернуть `result.total_count`. Обернуть в try/except → None если API не поддерживает → `src/services/freelancer/projects.py`

- [ ] T005 Обновить `_classify_project()` в `handlers.py`:
  - Расширить params профиля победителя: добавить `hourly_rate=true`, `registration_date=true`, `earnings=true`
  - Извлечь из ответа: `hourly_rate`, `registration_date` (unix ts), `earnings_score` из `reputation.entire_history`
  - Найти мой бид в `bids` (bidder_id == my_user_id), взять его `submitdate`
  - Вычислить `my_time_to_bid_sec = my_bid.submitdate - project.time_submitted`
  - Вычислить `winner_time_to_bid_sec = winning_bid.submitdate - project.time_submitted`
  - Вызвать `get_portfolio_count(winner_user_id)` для победителя
  - Добавить все новые поля в `winner_detail` dict → `src/services/telegram/handlers.py`

- [ ] T006 Обновить блок получения `my_profile` в `_fetch_bid_stats_sync()`:
  - Расширить params: добавить `hourly_rate=true`, `registration_date=true`, `earnings=true`
  - Извлечь те же поля что у победителя
  - Вызвать `get_portfolio_count(my_user_id)`
  - Добавить в `my_profile`: `hourly_rate`, `years_on_platform` (из registration_date), `earnings_score`, `portfolio_count`, `bid_adjustment` (из settings), `min_daily_rate` (из settings) → `src/services/telegram/handlers.py`

---

## Фаза 3: Отображение

- [ ] T007 Обновить `_build_loss_card()` в `handlers.py`:
  - Добавить строку timing: `⏱ You: Xmin | Winner: Ymin` (если < 60 мин — в минутах, иначе `Xh Ym`)
  - Добавить расширенный профиль победителя: hourly rate (`$X/hr`), лет на платформе, earnings score (`X/10`), portfolio count
  - Если поле None — пропустить (не ломать карточку) → `src/services/telegram/handlers.py`

- [ ] T008 Добавить блок "My profile" в начало `handle_bidstats_callback()` перед отправкой карточек:
  - Форматировать как отдельное сообщение: username, country, rating, reviews, hourly rate, лет на платформе, earnings score, portfolio count, bid_adjustment, min_daily_rate
  - Отправить через `query.message.reply_text(...)` до первой loss-карточки → `src/services/telegram/handlers.py`

- [ ] T009 Добавить кнопку **"📊 Analyse week"** после последней loss-карточки (или после dashboard если лоссов нет):
  - Callback data: `bidstats:analyse_week:{period}` (period = "weekly")
  - Отображается только для `period == "weekly"` → `src/services/telegram/handlers.py`

---

## Фаза 4: AI-анализ и GitHub

- [ ] T010 Добавить `analyse_weekly_bids(wins, losses, my_profile)` в `gemini_analyzer.py`:
  - Строит inline-промпт (не .md файл) с полным пакетом данных за неделю
  - Промпт требует: 3+ пронумерованных предложения, паттерны победы, приоритеты
  - Вызывает `_run_gemini_cli(prompt, settings.gemini_model, ANALYSIS_FALLBACK_MODELS)`
  - Возвращает текст анализа (str) или None → `src/services/ai/gemini_analyzer.py`

- [ ] T011 [P] Создать `src/services/github.py` — функция `post_issue(token, repo, title, body, labels=[])`:
  - POST `https://api.github.com/repos/{repo}/issues`
  - Headers: `Authorization: Bearer {token}`, `Accept: application/vnd.github+json`
  - Возвращает URL нового Issue или None при ошибке → `src/services/github.py`

- [ ] T012 Добавить обработчик `bidstats:analyse_week:*` в `handle_bidstats_callback()`:
  - Ответить "⏳ Analysing your bids..." (новое сообщение через `reply_text`)
  - Собрать wins, losses, my_profile из данных `_fetch_bid_stats_sync()`
  - Вызвать `analyse_weekly_bids(...)` из T010 (в executor — блокирующий)
  - Отправить результат в Telegram (HTML-форматирование, разбивать если > 4096 символов)
  - Вызвать `post_issue(...)` из T011 — заголовок `[AI Analysis] Week of YYYY-MM-DD — @username`
  - Добавить в конец ответного сообщения: `🔗 GitHub Issue: {url}` если Issue создан
  - Добавить ссылку на Issue в `docs/PROMPT_LOG.md` (append строку `- [Issue #{N}]({url}) — YYYY-MM-DD`) → `src/services/telegram/handlers.py`

---

## Фаза 5: Полировка

- [ ] T013 [P] Обновить `.env.example` — добавить секцию с `GITHUB_TOKEN=` и `GITHUB_REPO=` (с комментарием) → `.env.example`

- [ ] T014 [P] Обновить документацию: ARCHITECTURE.md (добавить `github.py`, расширить описание `bid_outcomes`, `handlers.py`), TECH_SPEC.md (v2.6 → DONE), DECISION_LOG.md (новая запись) → `docs/`

---

## Зависимости между задачами

```
T001 → T012 (GitHub token нужен в settings)
T002 → T003 (колонки нужны для методов)
T003 → T005 (set_bid_outcome принимает новые поля)
T003 → T006 (get_bid_outcome_full возвращает новые поля)
T004 → T005 (get_portfolio_count вызывается в _classify_project)
T004 → T006 (get_portfolio_count вызывается для my_profile)
T005 → T007 (новые поля доступны для отображения)
T006 → T008 (my_profile с расширенными данными)
T006 → T010 (my_profile нужен в analyse prompt)
T007, T008, T009 → T012 (UI готов перед логикой кнопки)
T010 → T012 (analyse function нужна в callback)
T011 → T012 (GitHub posting нужен в callback)
```

Параллельно: **T004** и **T011** (независимые новые функции).
Параллельно после T012: **T013** и **T014**.

---

## Затронутые файлы

| Файл | Тип |
|------|-----|
| `src/config/settings.py` | +2 поля |
| `src/services/storage/repository.py` | +6 колонок, обновить 2 метода |
| `src/services/freelancer/projects.py` | +1 метод |
| `src/services/ai/gemini_analyzer.py` | +1 функция |
| `src/services/github.py` | НОВЫЙ |
| `src/services/telegram/handlers.py` | 5 изменений |
| `.env.example` | +2 строки |
| `docs/` | документация |
