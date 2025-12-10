# Bid-Assist

Automated Freelancer project discovery and bidding bot with AI-powered analysis.

## Features

- **Automatic Project Discovery** - Monitors Freelancer.com for new projects matching your skills
- **Smart Filtering** - Filters by skills, budget range, and blacklisted keywords
- **AI Analysis** - Uses OpenAI to analyze project complexity and generate personalized bid proposals
- **Auto-Bidding** - Optionally places bids automatically (disabled by default)
- **Telegram Notifications** - Sends project alerts with AI summaries and bid proposals
- **Telegram Commands** - Control the bot via Telegram commands

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
python -m src.app
```

## Configuration

See `.env.example` for all available options. Key settings:

| Variable | Description |
|----------|-------------|
| `FREELANCER_OAUTH_TOKEN` | Your Freelancer OAuth token |
| `TELEGRAM_BOT_TOKEN` | Telegram bot token from @BotFather |
| `TELEGRAM_CHAT_IDS` | Comma-separated chat IDs |
| `OPENAI_API_KEY` | OpenAI API key |
| `SKILL_IDS` | Freelancer skill IDs to monitor |
| `MIN_BUDGET` / `MAX_BUDGET` | Budget range filter |
| `AUTO_BID_ENABLED` | Enable auto-bidding (default: false) |

## Telegram Commands

| Command | Description |
|---------|-------------|
| `/start` | Show welcome message |
| `/status` | Show current status and statistics |
| `/autobid on\|off` | Enable/disable automatic bidding |
| `/setbudget <min> <max>` | Change budget range |
| `/pause` | Pause project monitoring |
| `/resume` | Resume monitoring |
| `/stats` | Show bid statistics |

## Project Structure

```
bid-assist/
├── src/
│   ├── app.py              # Main application entry point
│   ├── config/             # Settings and constants
│   ├── core/               # Exceptions
│   ├── filters/            # Project filtering logic
│   ├── models/             # Data models (Project, Bid, etc.)
│   └── services/
│       ├── ai/             # OpenAI integration
│       ├── freelancer/     # Freelancer API client
│       ├── storage/        # SQLite repository
│       └── telegram/       # Bot, handlers, notifications
├── tests/                  # Unit tests
├── .env.example            # Environment template
├── docker-compose.yml      # Docker configuration
├── pyproject.toml          # Python project config
└── requirements.txt        # Dependencies
```

## How It Works

1. **Poll** - Every 5 minutes (configurable), fetches new projects from Freelancer
2. **Filter** - Applies skill, budget, and blacklist filters
3. **Analyze** - AI analyzes project complexity and generates bid proposal
4. **Bid** - If auto-bid is enabled, places bid automatically
5. **Notify** - Sends Telegram notification with project details and AI analysis

## Safety

- Auto-bidding is **disabled by default**
- Use `/autobid on` to enable after reviewing the system
- All bid attempts are logged in the database
- Budget limits prevent bidding on out-of-range projects

## License

MIT
