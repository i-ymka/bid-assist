"""AI-powered project analysis using OpenAI."""

import logging
import re
from pathlib import Path
from typing import Optional
import openai
from src.models import Project, AIAnalysis, Difficulty
from src.config import settings
from src.core.exceptions import AIAnalysisError

logger = logging.getLogger(__name__)

# Default prompt template (used if prompt file not found)
DEFAULT_PROMPT = """You are my expert freelance assistant. Analyze this project.

**Project:** {title}
**Description:** {description}
**Budget:** {budget_min} - {budget_max} {currency}

Decide is it good project for finish it with ai help or no, should we bit or no. Write a summary, and create a bid proposal, if you decided to bid. If no - give me explanation. Always use basix english.

VERDICT: [word]
---
SUMMARY: [summary]
---
BID: [proposal mentioning {username}]
---
AMOUNT: [number]
---
PERIOD: [days]"""


class AIAnalyzer:
    """Analyzes projects using OpenAI to provide difficulty ratings, summaries, and bid proposals."""

    def __init__(self):
        """Initialize the AI analyzer."""
        self._client: Optional[openai.OpenAI] = None
        self._prompt_template: str = DEFAULT_PROMPT
        self._initialize_client()
        self._load_prompt_template()

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

    def _load_prompt_template(self):
        """Load prompt template from file."""
        prompt_path = Path(settings.ai_prompt_file)

        if prompt_path.exists():
            try:
                self._prompt_template = prompt_path.read_text(encoding="utf-8")
                logger.info(f"Loaded prompt template from {prompt_path}")
            except Exception as e:
                logger.warning(f"Failed to load prompt file: {e}. Using default.")
                self._prompt_template = DEFAULT_PROMPT
        else:
            logger.info(f"Prompt file {prompt_path} not found. Using default prompt.")
            self._prompt_template = DEFAULT_PROMPT

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
        """Build the analysis prompt for a project using template."""
        description = (project.description or "")[:3000]

        # Fill in template placeholders
        return self._prompt_template.format(
            title=project.title,
            description=description,
            budget_min=f"{project.budget.minimum:.0f}",
            budget_max=f"{project.budget.maximum:.0f}",
            currency=project.currency.code,
            username=settings.username,
            portfolio_url=settings.portfolio_url,
        )

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

        # Extract verdication
        verdict_text = parts[0].replace("VERDICT:", "").strip().upper()
        try:
            difficulty = Difficulty(verdict_text)
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
