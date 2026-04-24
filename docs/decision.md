# Decisions

- Kept all findings in a single plan rather than splitting per-area — phases are already independent
- Corrected `except Exception` count from 35+ to 100 — original audit was conservative
- Phase 5 enum decisions flagged for user confirmation (remove vs wire-up)
- `settings.py` `logging.getLogger` may be intentional bootstrap fallback — evaluate during Phase 6
