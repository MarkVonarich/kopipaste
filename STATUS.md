# Finuchet Bot — Repository Status

## Current baseline
- Repository prepared for clean Git-based workflow.
- Documentation inventory is added (`README.md`, `docs/ARCHITECTURE.md`, `docs/RUNBOOK.md`).
- Sensitive/runtime files are excluded via `.gitignore`.
- No secrets, database dumps, snapshots, or backup artifacts are tracked.

## Delivery workflow
All further changes follow:
1. Create branch (`feature/<short-name>` or `fix/<short-name>`).
2. Open PR to `main`.
3. Review and merge.
4. Pull merged changes on VPS and restart `finuchet.service`.

## Deployment target
- Server: Ubuntu VPS
- App path: `/root/bot_finuchet`
- Service: `finuchet.service`
- DB: PostgreSQL

## Notes
- Keep secret values only in server `.env` files.
- Keep `.env.example` with key names only.


## Current DB work
- Added migration baseline for subtask 1.1: action tokens/pending operation and TTL cleanup.
- Files: `migrations/20260226_001_action_tokens_pending_op.sql`, `migrations/20260226_002_action_tokens_ttl_cleanup.sql`.


## Stage 2.0 — Data foundation
- Added text normalization utility for ML features: `services/ml_prep.py::normalize_for_ml` (lowercase, trim/collapse spaces, emoji stripping, conservative punctuation cleanup, numbers -> `<num>` token, currency symbols preserved).
- Added migration `migrations/20260226_007_ml_observations.sql` with table `public.ml_observations` and indexes for user timeline + recent events + JSONB suggestions lookup.
- Added DB APIs in `db/queries.py`: `insert_ml_observation(...)` and `update_ml_observation_choice(...)`.
- Embedded observation logging into Stage 1.3 flow: `suggest_shown`, `pick_cat`, `toggle_type`, `other_category`, `fallback_direct_write`, `parse_failed`.


## Stage 2.1 — ML suggestions v1
- Added baseline top-2 suggester `services/ml_suggest.py::get_top2_suggestions` (local/global alias hit first, then user frequency prior over last operations).
- Wired free-text category prompt to baseline top-2 with UI buttons: `✅ cat1`, `✅ cat2`, `✍️ Другая категория`, `🔁 Доход/Расход`.
- Kept fallback behavior safe (default categories when baseline misses) and preserved existing operation write flow on `ml_pick`.
- Extended `ml_observations` event payloads for stage 2.1 via `meta` and `suggested_top2` (`suggest_shown` + `pick_cat` compatibility).
