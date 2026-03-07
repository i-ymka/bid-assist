# Bid-Assist

Automated Freelancer project discovery and bidding bot with AI-powered analysis.

## Features

- **Automatic Project Discovery** - Monitors Freelancer.com for new projects matching your skills
- **Smart Filtering** - Filters by skills, budget, country, currency, language, blacklist, project age
- **AI Analysis** - Uses Google Gemini to analyze projects and generate personalized bid proposals
- **Auto-Bidding** - Fully automatic bid placement or one-click manual mode via Telegram
- **Telegram Bot** - Notifications with interactive buttons, live settings, bid statistics
- **Currency Conversion** - Automatic conversion to/from USD for 65+ currencies

## Quick Start

### 1. Clone the repository

```bash
git clone https://github.com/Ymka239/bid-assist.git
cd bid-assist
```

### 2. Set up environment

```bash
cp .env.example .env
# Edit .env with your API keys and preferences
```

### 3. Run with Docker (recommended)

```bash
docker-compose up -d
```

### Or run locally

```bash
pip install -r requirements.txt
python run.py
```

## Configuration

See `.env.example` for all available options. Key settings:

| Variable | Description |
|----------|-------------|
| `FREELANCER_OAUTH_TOKEN` | Your Freelancer OAuth token |
| `TELEGRAM_BOT_TOKEN` | Telegram bot token from @BotFather |
| `TELEGRAM_CHAT_IDS` | Comma-separated chat IDs |
| `GEMINI_API_KEY` | Google Gemini API key |
| `GEMINI_MODEL` | Gemini model (default: gemini-3.1-pro-preview) |
| `SKILL_IDS` | Freelancer skill IDs to monitor |
| `BLOCKED_COUNTRIES` | Countries to skip (comma-separated) |
| `BL` | Blacklist keywords (comma-separated) |

Budget range, poll interval, and auto-bid mode are configured via `/settings` in Telegram.

## How It Works

```
1. POLL        → Fetch new projects from Freelancer API
2. FILTER      → Apply skill, budget, country, blacklist, currency, language filters
3. AI ANALYZE  → Gemini analyzes project, generates summary and bid proposal
4. NOTIFY      → Send Telegram message with "Place Bid" button
5. BID         → Auto-bid or manual one-click placement
```

## Telegram Commands

| Command | Description |
|---------|-------------|
| `/status` | Bot status + control buttons (pause/resume/auto-bid) |
| `/settings` | Configure budget, poll interval, filters |
| `/bidstats` | Bid history with win/loss classification |
| `/help` | Show available commands |

## Project Structure

```
bid-assist/
├── run.py                  # Entry point (polling + analysis + Telegram bot)
├── prompts/
│   └── pal_rules.md        # AI prompt rules for Gemini
├── src/
│   ├── config/             # Settings (pydantic-settings) and API constants
│   ├── core/               # Exception hierarchy
│   ├── models/             # Data models (Project, Bid, AIAnalysis)
│   ├── filters/            # Filtering pipeline (skill, budget, country, blacklist)
│   └── services/
│       ├── ai/             # Gemini CLI integration
│       ├── freelancer/     # Freelancer API client, projects, bidding
│       ├── storage/        # SQLite repository (7 tables)
│       ├── telegram/       # Bot handlers and notification formatting
│       └── currency.py     # Currency conversion (60+ currencies)
├── docs/                   # Project documentation
├── data/                   # Runtime data (SQLite DB)
├── logs/                   # Application logs
├── tests/                  # Unit tests
├── .env.example            # Environment template
├── docker-compose.yml      # Docker configuration
├── pyproject.toml          # Python project config
└── requirements.txt        # Dependencies
```

## Safety

- **Manual mode**: Bids placed only when you click the button
- **Auto-bid mode**: Fully automatic, can be toggled via `/settings`
- All bid attempts are logged in the database
- Budget and country filters prevent unwanted projects

## License

MIT
