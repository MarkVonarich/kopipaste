-- Stage 2.0: ML observations / data foundation

BEGIN;

CREATE TABLE IF NOT EXISTS public.ml_observations (
  id               BIGSERIAL PRIMARY KEY,
  created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
  user_id          BIGINT NOT NULL,
  chat_id          BIGINT NOT NULL,
  raw_text         TEXT NOT NULL,
  normalized_text  TEXT NOT NULL,
  detected_type    TEXT NOT NULL,
  suggested_top2   JSONB,
  chosen_category  TEXT,
  chosen_type      TEXT,
  action           TEXT NOT NULL,
  confidence_top1  NUMERIC,
  meta             JSONB
);

CREATE INDEX IF NOT EXISTS idx_ml_observations_user_created
  ON public.ml_observations (user_id, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_ml_observations_created
  ON public.ml_observations (created_at DESC);

CREATE INDEX IF NOT EXISTS idx_ml_observations_suggested_top2_gin
  ON public.ml_observations USING GIN (suggested_top2);

COMMIT;
