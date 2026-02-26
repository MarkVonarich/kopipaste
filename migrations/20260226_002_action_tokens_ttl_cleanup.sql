-- Finuchet: Subtask 1.1 cleanup helper
-- Safe TTL cleanup for expired drafts/cancelled tokens.

DELETE FROM action_tokens
WHERE expires_at < NOW()
  AND status IN ('draft', 'cancelled', 'expired');
