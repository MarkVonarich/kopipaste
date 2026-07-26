# Localization Strategy

Finuchet keeps user-facing privacy and deletion copy in `services/i18n.py`.
Handlers must render text through `t(key, locale)` instead of embedding
privacy-sensitive strings directly.

## Locale Resolution

Use this priority:

1. Saved user profile locale from `public.users.locale`.
2. Telegram `language_code`.
3. English fallback.

Unsupported languages fall back to English. This includes `uz`, so users never
see raw translation keys or partially localized privacy flows.

## Privacy Flows

Privacy screens must support Russian and English:

- privacy menu;
- export entry point;
- clear financial history;
- account and full personal-data deletion;
- custom date prompts;
- preview, confirmation, success, failure, stale, and cancellation states.

Full personal-data deletion remains a separate service flow. The normal UI uses
two inline confirmation stages and does not require a typed phrase.

Financial-history deletion is scoped to personal workspace financial records
only. It must not remove profile, categories, settings, limits, budgets, or
shared group financial history.

## Adding Copy

When adding a new privacy/deletion UI state:

1. Add the same key to both `ru` and `en` in `services/i18n.py`.
2. Use `resolve_locale(saved_locale, telegram_language_code)` at the handler
   boundary.
3. Add a handler or service test that verifies no raw key leaks to users.
