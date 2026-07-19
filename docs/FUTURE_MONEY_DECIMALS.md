# Future Money Decimal Support

Finuchet still stores operation amounts as integer major units in `public.operations.amount`.

Until the money schema is moved to minor units or an exact decimal representation, user-entered values with non-zero cents/kopecks must be rejected before saving. The parser accepts integer values and zero-cent values such as `12.00`, but rejects values such as `12.50` or `2.99` with `fractional_amount`.

Future support should include:

- a migration to minor units or a fixed-precision decimal amount column;
- currency-aware formatting and export behavior;
- import/OCR/category flows that preserve exact cents without rounding;
- backfill rules for existing integer amounts.
