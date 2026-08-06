ALTER TABLE public.users
  ADD COLUMN IF NOT EXISTS preferred_name TEXT;
