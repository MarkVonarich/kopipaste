-- Stage 1.3 / PR2: feedback events for category suggestions

BEGIN;

CREATE TABLE IF NOT EXISTS public.category_feedback (
  id            BIGSERIAL PRIMARY KEY,
  user_id       BIGINT NOT NULL,
  chat_id       BIGINT NOT NULL,
  raw_text      TEXT,
  norm_text     TEXT,
  suggested_cat TEXT,
  chosen_cat    TEXT,
  op_type       TEXT,
  event_type    TEXT NOT NULL CHECK (event_type IN ('accept', 'decline')),
  created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_category_feedback_user_created
  ON public.category_feedback (user_id, created_at DESC);

COMMIT;
