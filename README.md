# Bid-Assist

Automated Freelancer project discovery and bidding bot with AI-powered analysis.

## Features

- **Automatic Project Discovery** - Monitors Freelancer.com for new projects matching your skills
- **Smart Filtering** - Filters by skills, budget range, and blacklisted keywords
- **AI Analysis** - Uses OpenAI to analyze project complexity and generate personalized bid proposals
- **One-Click Bidding** - Place bids with a single button click in Telegram
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

## How It Works

```
1. POLL        → Fetch new projects from Freelancer (every 5 min)
2. FILTER      → Apply skill, budget, and blacklist filters
3. AI ANALYZE  → Get difficulty rating, summary, and bid proposal
4. NOTIFY      → Send Telegram message with "Place Bid" button
5. YOU CLICK   → Bot places bid on Freelancer
```

## Telegram Commands

| Command | Description |
|---------|-------------|
| `/start` | Show welcome message |
| `/status` | Show current status and statistics |
| `/setbudget <min> <max>` | Change budget range |
| `/pause` | Pause project monitoring |
| `/resume` | Resume monitoring |
| `/stats` | Show bid statistics |

## Example Notification

When the bot finds a matching project, you'll receive:

```
**Python Web Scraper for E-commerce**

📝 Summary: Client needs a simple scraper to extract
product prices from 3 websites. Straightforward job.

💰 Budget: 50 - 150 USD
💵 Suggested Bid: $120
📅 Suggested Period: 5 days

🔗 Project link:
https://www.freelancer.com/projects/12345

👇 Bid Proposal:
┌─────────────────────────────────────
│ Hi, I'm YourName, expert in Python...
└─────────────────────────────────────

#EASY

[ 💰 Place Bid ($120) ]   ← Click to bid!
```

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

## Safety

- Bids are only placed when **you click the button**
- All bid attempts are logged in the database
- Budget limits prevent showing out-of-range projects

## License

MIT
