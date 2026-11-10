"""Application settings loaded from environment."""

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_name: str = "FreedomBot"
    environment: str = "development"
    secret_key: str = "change-me-in-production"
    database_url: str = "postgresql+psycopg://freedombot:freedombot@localhost:5432/freedombot"
    default_page_size: int = 25
    max_page_size: int = 100

    # Editable temporary cadence defaults (no scheduler in Step 2).
    default_sender_name: str = "JeanneCAIO / FreedomBot Team"
    default_initial_delay_hours: int = 0
    default_followup_1_delay_hours: int = 48
    default_followup_2_delay_hours: int = 72
    default_followup_3_delay_hours: int = 96

    # Platform clocks — US Central / Chicago. Override with APP_DISPLAY_TIMEZONE.
    app_display_timezone: str = "America/Chicago"

    # Admin authentication (session cookie). Prefer bcrypt hash — never commit plaintext.
    # Generate: python -c "import bcrypt; print(bcrypt.hashpw(b'YOUR_PASSWORD', bcrypt.gensalt()).decode())"
    admin_username: str = ""
    admin_password_hash: str = ""
    admin_session_ttl_hours: int = 12
    admin_session_cookie_name: str = "freedombot_admin_session"

    # Instantly campaign send schedule — always America/Chicago (product TZ).
    # Window is Chicago clocks chosen so sending overlaps the operator's IST
    # workday (9 AM–9 PM IST ≈ 10:30 PM–10:30 AM Chicago). Instantly requires a
    # same-day window, so we use the overnight/early chunk: 12:00 AM–10:30 AM
    # Chicago (= 10:30 AM–9:00 PM IST).
    default_send_timezone: str = "America/Chicago"
    default_send_window_start: str = "00:00"
    default_send_window_end: str = "10:30"
    default_send_monday: bool = True
    default_send_tuesday: bool = True
    default_send_wednesday: bool = True
    default_send_thursday: bool = True
    default_send_friday: bool = True
    default_send_saturday: bool = False
    default_send_sunday: bool = False

    # Instantly infrastructure settings (not customer credentials).
    instantly_base_url: str = "https://api.instantly.ai/api/v2"
    instantly_timeout_seconds: float = 15.0

    # Deprecated for runtime: used only by optional one-time import script.
    instantly_api_key: str = ""

    # Master key for encrypting integration credentials at rest (Fernet).
    integration_encryption_key: str = ""

    # Public base URL for personalized assessment links (no trailing slash).
    public_base_url: str = "http://localhost:3001/acquire"

    # DEVELOPMENT: public HTTPS origin for Instantly webhooks (Cloudflare Tunnel).
    # Example: https://xxxx.trycloudflare.com — no trailing slash.
    # Webhook path: {PUBLIC_WEBHOOK_BASE_URL}/api/webhooks/instantly
    public_webhook_base_url: str = ""

    # Shared secret sent as Authorization Bearer on Instantly webhook deliveries.
    instantly_webhook_secret: str = ""

    # Optional AI provider for reply automation (none | openai).
    ai_reply_provider: str = "none"
    openai_api_key: str = ""
    openai_model: str = "gpt-4o-mini"

    # Polling fallback when webhooks unavailable (seconds).
    instantly_reply_poll_interval_seconds: int = 300

    # Placeholder required in active email bodies before activation.
    assessment_url_placeholder: str = "{{assessment_url}}"

    # Direct outreach (plain Gmail via the ops chatbot; replaces Instantly).
    outreach_daily_ceiling: int = 400          # max emails sent per local day
    outreach_gap_seconds: int = 25             # wait between two sends
    outreach_max_attempts: int = 3
    outreach_optout_line: str = ""
    outreach_business_address: str = ""
    outreach_worker_enabled: bool = False      # background sender; off until you turn it on
    outreach_sender_user_id: str = ""          # user whose connected Gmail sends the emails
    outreach_poll_seconds: int = 5
    outreach_allow_workspace_mailbox: bool = False  # development only; never true in production

    # Cal.com — prefer encrypted Integrations credentials; env is fallback only.
    calcom_api_key: str = ""
    calcom_api_base_url: str = "https://api.cal.com/v2"
    # Shared secret for Cal.com webhook HMAC (x-cal-signature-256).
    calcom_webhook_secret: str = ""
    # Extra emails that should also get Cal.com booking invites (comma-separated).
    # Prospect remains the primary booker; these are added as Cal guests.
    calcom_booking_cc_emails: str = ""

    # TEMPORARY development-only Stripe TEST checkout (not production payments).
    # Secrets must come from environment only — never hardcode in source.
    stripe_enabled: bool = False
    stripe_secret_key: str = ""
    stripe_currency: str = "usd"
    stripe_test_amount_cents: int = 10000  # $100.00 test amount — not FreedomBot pricing
    stripe_success_url: str = (
        "http://127.0.0.1:8020/stripe-test/success?session_id={CHECKOUT_SESSION_ID}"
    )
    stripe_cancel_url: str = "http://127.0.0.1:8020/stripe-test/cancel"
    stripe_webhook_secret: str = ""  # Intentionally blank for first local success-URL test

    # Autonomous discovery / public research.
    # DISCOVERY_PROVIDER=openai_agentic|multi (alias)|multi_legacy|mock|web_search|serper|brave|none
    # openai_agentic / multi = OpenAI Responses + agentic web_search (primary).
    # multi_legacy = older multi-source harvest path (OpenAI web slices only unless legacy sources enabled).
    discovery_enabled: bool = True
    discovery_provider: str = "openai_agentic"
    # Admin "Prospects per run" — FINAL usable researched/scored prospects per cycle.
    discovery_cycle_limit: int = 5
    discovery_cycle_hard_max: int = 200
    # Max provisional search candidates inspected while hunting for usable prospects.
    discovery_scan_hard_max: int = 400
    # Re-research confidently excluded duplicates only after this many days.
    discovery_excluded_refresh_days: int = 30
    discovery_timeout_seconds: float = 15.0
    discovery_max_retries: int = 1
    # Live search API credentials (never hardcode secrets).
    discovery_search_api_key: str = ""
    discovery_search_base_url: str = ""
    # Default enabled source keys for legacy multi path.
    discovery_sources_default: str = "openai_web"
    # Legacy Exa/NPPES: code retained; never called unless this flag is true AND
    # DISCOVERY_PROVIDER=multi_legacy. Agentic path never uses these sources.
    discovery_enable_legacy_sources: bool = False
    # Legacy (unused in agentic path): Exa API key retained so old env files load.
    exa_api_key: str = ""
    # Exa Team Management service key (not the search key): lets the Integrations page show the credit balance.
    exa_admin_api_key: str = ""
    # Research provider for new prospects: auto | openai_web | web_search | mock | none
    research_provider: str = "auto"
    # Model for OpenAI web search (legacy harvest + research). Blank = Integrations model.
    openai_web_model: str = ""
    openai_web_timeout_seconds: float = 90.0
    # Agentic discovery (Responses API + web_search). Values come from env only.
    openai_discovery_model: str = "gpt-6-luna"
    openai_discovery_reasoning_effort: str = "medium"
    openai_discovery_timeout_seconds: float = 300.0
    # Stage A top-up: max OpenAI discovery calls to fill requested_count.
    # Env: OPENAI_DISCOVERY_MAX_ATTEMPTS or DISCOVERY_MAX_ATTEMPTS (alias via discovery_max_attempts).
    openai_discovery_max_attempts: int = 3
    discovery_max_attempts: int | None = None  # if set, overrides openai_discovery_max_attempts
    # Stage B: max concurrent per-company enrichment OpenAI calls.
    enrichment_max_concurrency: int = 5
    # Bounded retries after a failed enrichment (total attempts = 1 + retries).
    enrichment_max_retries: int = 1
    # Max candidate companies a single outreach-ready slot may explore in one agentic call.
    outreach_slot_max_candidates: int = 3
    # Max replacement discovery rounds when usable_count < requested_count.
    discovery_replacement_max_rounds: int = 3
    # Token prices used only for the run-cost estimate shown in Admin.
    openai_price_input_per_m: float = 0.10
    openai_price_output_per_m: float = 0.50
    # Acquisition-specific GPT-6 Luna style rates (prompt caching aware).
    discovery_price_input_per_m: float = 0.10
    discovery_price_cached_input_per_m: float = 0.01
    discovery_price_cache_write_per_m: float = 0.125
    discovery_price_output_per_m: float = 0.50
    discovery_price_web_search_per_call: float = 0.01
    # 0 / unset = not configured. Never bill long-context as $0.
    discovery_price_long_context_input_per_m: float | None = None
    discovery_price_long_context_output_per_m: float | None = None
    discovery_price_long_context_threshold_tokens: int = 128_000
    # Conservative ceiling used only to decide whether a run might enter long-context
    # (prompt + assumed tool/content tokens). Not a billable exact figure.
    discovery_estimate_max_input_tokens_per_call: int = 50_000
    # Local estimate assumptions (tiktoken cannot predict these).
    discovery_estimate_stage_a_output_tokens: int = 2500
    discovery_estimate_slot_output_tokens: int = 3500
    discovery_estimate_stage_a_searches: int = 6
    discovery_estimate_slot_searches: int = 4
    discovery_estimate_stage_a_prompt_tokens_fallback: int = 2500
    discovery_estimate_slot_prompt_tokens_fallback: int = 2500
    # Optional cost guards (None / 0 = disabled). First paid test typically 0.50 / 0.60.
    discovery_max_estimated_run_cost_usd: float | None = None
    discovery_max_actual_run_cost_usd: float | None = None
    discovery_prompt_cache_key: str = "freedombot-acquisition-v1"
    discovery_refresh_days: int = 30
    discovery_verify_websites: bool = True
    discovery_verify_timeout_seconds: float = 8.0
    discovery_max_slices_per_source: int = 6
    # Absolute safety max for ONE global AI comparison of the full surviving pool.
    # Does NOT truncate by evidence completeness — exceeding this refuses the run.
    discovery_global_triage_max_candidates: int = 250
    # Legacy (unused for AI input sizing): previously truncated survivors to
    # requested * multiplier before triage. Kept for config/compat only.
    discovery_triage_pool_multiplier: int = 3
    discovery_triage_pool_hard_max: int = 60
    # Offline demo data for all sources (development/test only — ignored in production).
    discovery_demo_mode: bool = False
    # Bounded Admin live E2E test defaults (never mass-send).
    live_e2e_discovery_limit: int = 2
    live_e2e_activation_enabled: bool = False

    # Developer-only acquisition tracing. OFF by default.
    # When true, emits compact [ACQ][*] log lines for one-prospect debugging.
    # Never logs API keys, tokens, or full LLM prompts.
    acquisition_trace: bool = False

    # AI research validation (semantic layer on top of hard company-bound rules).
    # Uses existing OpenAI credentials; does not replace deterministic safety checks.
    research_validation_enabled: bool = True
    research_validation_model: str = "gpt-4o-mini"
    # AI outcome learning (priority ordering only). Technical config — not UI-exposed.
    outcome_learning_model: str = "gpt-4o-mini"
    outcome_learning_refresh_batch_max: int = 25
    ai_research_min_relevance_confidence: float = 0.70
    ai_research_min_fact_confidence: float = 0.70
    ai_research_min_dm_confidence: float = 0.80
    ai_research_max_sources: int = 12


    # Autonomous outreach hard caps; campaign id also lives on icp_settings.
    activation_batch_hard_max: int = 25
    activation_dry_run_default: bool = True
    autonomous_sender_identity: str = "JeanneCAIO / FreedomBot Team"

    # Continuous acquisition scheduler.
    # Default OFF — do not start mass outreach on deploy.
    autonomous_scheduler_inprocess: bool = False
    autonomous_scheduler_tick_seconds: int = 60

    # Acquisition owner — read-only prospect database access.
    # Provision via env; never hardcode client passwords in source.
    acquisition_owner_username: str = ""
    acquisition_owner_password_hash: str = ""


def is_non_production_environment(environment: str | None = None) -> bool:
    """True for development/dev/test — used for cookies, API docs exposure, etc."""
    env = (environment if environment is not None else get_settings().environment or "")
    return env.strip().lower() in {"development", "dev", "test"}


def api_docs_url_kwargs(environment: str | None = None) -> dict[str, str | None]:
    """Expose Swagger/ReDoc/OpenAPI only outside production."""
    if is_non_production_environment(environment):
        return {
            "docs_url": "/docs",
            "redoc_url": "/redoc",
            "openapi_url": "/openapi.json",
        }
    return {"docs_url": None, "redoc_url": None, "openapi_url": None}


@lru_cache
def get_settings() -> Settings:
    return Settings()
