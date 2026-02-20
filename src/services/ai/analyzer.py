"""AI-powered project analysis using Google Gemini."""

import logging
import re
import time
from pathlib import Path
import google.generativeai as genai
from google.api_core import exceptions as google_exceptions
from src.models import Project, AIAnalysis, Verdict
from src.config import settings

logger = logging.getLogger(__name__)

# Default prompt template (used if prompt file not found)
DEFAULT_PROMPT = """You are an expert freelance assistant. Analyze this project.

**Project:** {title}
**Description:** {description}
**Budget:** {budget_min} - {budget_max} {currency}

Decide if this is a good project to bid on. Write a summary and create a bid proposal.

VERDICT: [BID or SKIP]
---
SUMMARY: [summary]
---
BID: [proposal mentioning {username}]
---
AMOUNT: [number]
---
PERIOD: [days]"""


class AIAnalyzer:
    """Analyzes projects using Google Gemini to provide verdicts, summaries, and bid proposals."""

    def __init__(self):
        """Initialize the AI analyzer."""
        self._model = None
        self._prompt_template: str = DEFAULT_PROMPT
        self._initialize_client()
        self._load_prompt_template()

    def _initialize_client(self):
        """Initialize the Gemini client."""
        if not settings.gemini_api_key:
            logger.warning("GEMINI_API_KEY not set. AI analyzer will be disabled.")
            return

        try:
            genai.configure(api_key=settings.gemini_api_key)
            self._model = genai.GenerativeModel(settings.llm_model)
            logger.info(f"Gemini client initialized with model: {settings.llm_model}")
        except Exception as e:
            logger.error(f"Failed to initialize Gemini client: {e}")
            self._model = None

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
        return self._model is not None

    def analyze_project(self, project: Project) -> AIAnalysis:
        """Analyze a project and generate AI insights.

        Args:
            project: The project to analyze.

        Returns:
            AIAnalysis with verdict, summary, and bid proposal.
        """
        if not self.is_available:
            return AIAnalysis(
                verdict=Verdict.UNKNOWN,
                summary="AI analyzer is not configured.",
                suggested_bid_text="AI analyzer is not configured.",
            )

        prompt = self._build_prompt(project)
        
        max_retries = 3
        for attempt in range(max_retries):
            try:
                response = self._model.generate_content(
                    prompt,
                    generation_config=genai.types.GenerationConfig(
                        max_output_tokens=1500,
                        temperature=0.5,
                    ),
                )

                full_response = response.text.strip()
                return self._parse_response(full_response)

            except google_exceptions.ResourceExhausted as e:
                retry_delay = 60  # default
                try:
                    # Extract delay from error metadata if available
                    if hasattr(e, 'metadata') and e.metadata:
                        for meta in e.metadata:
                            if meta.key == 'retry_delay':
                                retry_delay = int(meta.value.seconds) + 1
                                break
                except (AttributeError, ValueError, IndexError):
                    pass
                
                logger.warning(
                    f"AI rate limit hit for project {project.id}. "
                    f"Attempt {attempt + 1}/{max_retries}. Retrying in {retry_delay}s."
                )
                
                if attempt < max_retries - 1:
                    time.sleep(retry_delay)
                else:
                    logger.error(f"AI analysis failed for project {project.id} after {max_retries} retries: {e}")
                    return AIAnalysis(
                        verdict=Verdict.UNKNOWN,
                        summary=f"AI analysis error: {str(e)}",
                        suggested_bid_text="Could not generate bid proposal due to rate limits.",
                    )
            except Exception as e:
                logger.error(f"AI analysis failed for project {project.id}: {e}")
                break  # Don't retry on other errors

        # This part is reached if a non-retryable error occurs or retries are exhausted
        return AIAnalysis(verdict=Verdict.UNKNOWN, summary="AI analysis failed.")

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
                verdict=Verdict.UNKNOWN,
                summary="Could not parse AI summary.",
                suggested_bid_text=response,
            )

        # Extract verdict
        verdict_text = parts[0].replace("VERDICT:", "").strip().upper()
        try:
            verdict = Verdict(verdict_text)
        except ValueError:
            verdict = Verdict.UNKNOWN

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

        logger.info(f"AI analysis complete. Verdict: {verdict.value}")

        return AIAnalysis(
            verdict=verdict,
            summary=summary,
            suggested_bid_text=bid_text,
            suggested_amount=suggested_amount,
            suggested_period=suggested_period,
        )
