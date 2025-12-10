"""AI-powered project analysis using OpenAI."""

import logging
import re
from typing import Optional
import openai
from src.models import Project, AIAnalysis, Difficulty
from src.config import settings
from src.core.exceptions import AIAnalysisError

logger = logging.getLogger(__name__)


class AIAnalyzer:
    """Analyzes projects using OpenAI to provide difficulty ratings, summaries, and bid proposals."""

    def __init__(self):
        """Initialize the AI analyzer."""
        self._client: Optional[openai.OpenAI] = None
        self._initialize_client()

    def _initialize_client(self):
        """Initialize the OpenAI client."""
        if not settings.openai_api_key:
            logger.warning("OPENAI_API_KEY not set. AI analyzer will be disabled.")
            return

        try:
            self._client = openai.OpenAI(api_key=settings.openai_api_key)
            logger.info("OpenAI client initialized successfully")
        except Exception as e:
            logger.error(f"Failed to initialize OpenAI client: {e}")
            self._client = None

    @property
    def is_available(self) -> bool:
        """Check if AI analyzer is available."""
        return self._client is not None

    def analyze_project(self, project: Project) -> AIAnalysis:
        """Analyze a project and generate AI insights.

        Args:
            project: The project to analyze.

        Returns:
            AIAnalysis with difficulty, summary, and bid proposal.
        """
        if not self.is_available:
            return AIAnalysis(
                difficulty=Difficulty.UNKNOWN,
                summary="AI analyzer is not configured.",
                suggested_bid_text="AI analyzer is not configured.",
            )

        prompt = self._build_prompt(project)

        try:
            response = self._client.chat.completions.create(
                model=settings.llm_model,
                messages=[
                    {
                        "role": "system",
                        "content": (
                            "You are a skeptical, highly experienced freelance developer "
                            "who spots low budgets and hidden complexities. "
                            "You follow output formats perfectly."
                        ),
                    },
                    {"role": "user", "content": prompt},
                ],
                max_tokens=600,
                temperature=0.5,
            )

            full_response = response.choices[0].message.content.strip()
            return self._parse_response(full_response)

        except Exception as e:
            logger.error(f"AI analysis failed for project {project.id}: {e}")
            return AIAnalysis(
                difficulty=Difficulty.UNKNOWN,
                summary=f"AI analysis error: {str(e)}",
                suggested_bid_text="Could not generate bid proposal.",
            )

    def _build_prompt(self, project: Project) -> str:
        """Build the analysis prompt for a project."""
        description = (project.description or "")[:3000]

        return f"""You are my expert freelance assistant. Your job is to analyze projects with extreme skepticism. Find hidden complexities and mismatches between budget and scope. Use simple, direct English.

**Project Details:**
- Title: {project.title}
- Description: {description}
- Budget: ${project.budget.minimum} - ${project.budget.maximum} {project.currency.code}

--- TASK 1: Deep Difficulty Analysis ---
Analyze the project's TRUE complexity. Look for red flags like multi-threading, CAPTCHA, anti-detection, proxy integration, session management, or resume logic. Also, consider if the budget is ridiculously low for the requested work.
Rate the difficulty as EASY, MEDIUM, or HARD based on this deep analysis, not just keywords.

--- TASK 2: Insightful Summary ---
Explain the project's real goal in a conversational tone. AVOID robotic phrases. Mention if it's a simple script or a complex industrial-grade bot. Example: 'Okay, so this client needs a full-scale bot for mass-registering accounts on FIFA.com, including advanced anti-detection features.'

--- TASK 3: Hyper-Specific Bid Proposal ---
Write a 2-3 sentence bid proposal. It MUST be confident and directly reference key technologies (e.g., 'Selenium', 'IMAP', 'multi-threading').
**If the budget is insultingly low for a HARD project, the bid should politely address this.**
Example for low-budget HARD project: 'Hi, I'm {settings.username}. This is a complex project involving multi-threading and advanced automation. The listed budget of ${project.budget.maximum} would cover a basic proof-of-concept, but the full implementation would require a budget closer to $XXXX. See my work at {settings.portfolio_url}.'
For normally priced projects, use this style: 'Hi, I'm {settings.username}, an expert in API integration. I can connect your custom API with DUDA. See my work: {settings.portfolio_url}.'

--- TASK 4: Bid Amount Suggestion ---
Based on the project complexity and scope, suggest a realistic bid amount in USD.
If the project budget seems fair, suggest bidding near the maximum budget.
If the budget is too low for the scope, suggest a realistic amount that reflects the actual work required.

--- YOUR RESPONSE FORMAT ---
RATING: [Your rating word]
---
SUMMARY: [Your insightful, conversational summary]
---
BID: [Your hyper-specific, budget-aware proposal]
---
AMOUNT: [Suggested bid amount as a number, e.g., 150]
---
PERIOD: [Suggested delivery period in days, e.g., 5]"""

    def _parse_response(self, response: str) -> AIAnalysis:
        """Parse the AI response into an AIAnalysis object."""
        parts = response.split("---")

        if len(parts) < 3:
            logger.warning(f"AI response had unexpected format: {response[:200]}")
            return AIAnalysis(
                difficulty=Difficulty.UNKNOWN,
                summary="Could not parse AI summary.",
                suggested_bid_text=response,
            )

        # Extract rating
        rating_text = parts[0].replace("RATING:", "").strip().upper()
        try:
            difficulty = Difficulty(rating_text)
        except ValueError:
            difficulty = Difficulty.UNKNOWN

        # Extract summary
        summary = parts[1].replace("SUMMARY:", "").strip() if len(parts) > 1 else ""

        # Extract bid text
        bid_text = parts[2].replace("BID:", "").strip() if len(parts) > 2 else ""

        # Extract suggested amount (optional)
        suggested_amount = None
        if len(parts) > 3:
            amount_text = parts[3].replace("AMOUNT:", "").strip()
            try:
                suggested_amount = float(re.sub(r"[^\d.]", "", amount_text))
            except (ValueError, TypeError):
                pass

        # Extract suggested period (optional)
        suggested_period = None
        if len(parts) > 4:
            period_text = parts[4].replace("PERIOD:", "").strip()
            try:
                suggested_period = int(re.sub(r"[^\d]", "", period_text))
            except (ValueError, TypeError):
                pass

        logger.info(f"AI analysis complete. Difficulty: {difficulty.value}")

        return AIAnalysis(
            difficulty=difficulty,
            summary=summary,
            suggested_bid_text=bid_text,
            suggested_amount=suggested_amount,
            suggested_period=suggested_period,
        )
