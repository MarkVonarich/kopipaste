# Mini App Authentication

The Mini App backend verifies Telegram WebApp `initData` before serving any `/miniapp/api/*` route.

## Verification

`miniapp.auth.verify_telegram_init_data`:

- parses the query string with strict parsing;
- requires `hash`;
- builds the data-check string from sorted key/value pairs excluding `hash`;
- derives the secret key with HMAC-SHA256 using `WebAppData` and the bot token;
- compares hashes with `hmac.compare_digest`;
- verifies `auth_date` freshness;
- rejects auth dates too far in the future;
- requires a valid Telegram `user.id`.

## Trust Boundary

The frontend must never choose the application user id. Server handlers use only the authenticated Telegram user id from signed `initData`.

## Logging Rules

Do not log:

- raw `initData`;
- Telegram bot token;
- HMAC secret;
- database URL;
- user-provided raw financial text;
- operation descriptions or category free text in product analytics.

## Expiration

The default maximum age is 24 hours and can be adjusted with:

```bash
MINIAPP_INITDATA_MAX_AGE_SECONDS=86400
```

Use shorter values for high-risk staging tests if needed.
