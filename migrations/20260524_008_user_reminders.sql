CREATE TABLE IF NOT EXISTS public.user_reminders (
  id BIGSERIAL PRIMARY KEY,
  user_id BIGINT NOT NULL,
  title TEXT NOT NULL,
  rem_type TEXT NOT NULL,
  category TEXT NOT NULL,
  amount NUMERIC(14,2) NOT NULL,
  currency TEXT NOT NULL DEFAULT 'RUB',
  event_date DATE NOT NULL,
  repeat_rule TEXT NOT NULL DEFAULT 'none',
  repeat_interval_days INTEGER NULL,
  notify_days_before INTEGER NOT NULL DEFAULT 1,
  is_active BOOLEAN NOT NULL DEFAULT TRUE,
  last_sent_event_date DATE NULL,
  last_sent_at TIMESTAMPTZ NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  CHECK (amount > 0 AND amount < 1000000000),
  CHECK (notify_days_before >= 0 AND notify_days_before <= 30),
  CHECK (repeat_rule IN ('none','weekly','monthly','yearly','custom_days')),
  CHECK ((repeat_rule <> 'custom_days' AND repeat_interval_days IS NULL) OR (repeat_rule='custom_days' AND repeat_interval_days BETWEEN 1 AND 3650))
);

CREATE INDEX IF NOT EXISTS idx_user_reminders_user_active ON public.user_reminders(user_id, is_active);
CREATE INDEX IF NOT EXISTS idx_user_reminders_event_date ON public.user_reminders(event_date);
CREATE INDEX IF NOT EXISTS idx_user_reminders_active_event_date ON public.user_reminders(is_active, event_date);

CREATE TABLE IF NOT EXISTS public.user_reminder_events (
  id BIGSERIAL PRIMARY KEY,
  reminder_id BIGINT NOT NULL,
  user_id BIGINT NOT NULL,
  event_date DATE NOT NULL,
  notify_days_before INTEGER NOT NULL,
  event_type TEXT NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE UNIQUE INDEX IF NOT EXISTS ux_user_reminder_sent_dedup
ON public.user_reminder_events(reminder_id, event_date, notify_days_before, event_type)
WHERE event_type='sent';
