-- Finuchet: decimal monetary amounts for operations and limit/budget storage.
--
-- Production command:
--   psql "$DATABASE_URL" -X -v ON_ERROR_STOP=1 -f migrations/20260803_015_decimal_operation_amounts.sql
--
-- Existing integer values are preserved exactly, e.g. 285 -> 285.00.

DO $$
BEGIN
    IF EXISTS (
        SELECT 1
          FROM information_schema.columns
         WHERE table_schema='public' AND table_name='operations' AND column_name='amount'
           AND (data_type <> 'numeric' OR numeric_precision IS DISTINCT FROM 18 OR numeric_scale IS DISTINCT FROM 2)
    ) THEN
        ALTER TABLE public.operations
            ALTER COLUMN amount TYPE NUMERIC(18,2)
            USING amount::numeric(18,2);
    END IF;

    IF EXISTS (
        SELECT 1
          FROM information_schema.columns
         WHERE table_schema='public' AND table_name='category_limits' AND column_name='amount'
           AND (data_type <> 'numeric' OR numeric_precision IS DISTINCT FROM 18 OR numeric_scale IS DISTINCT FROM 2)
    ) THEN
        ALTER TABLE public.category_limits
            ALTER COLUMN amount TYPE NUMERIC(18,2)
            USING amount::numeric(18,2);
    END IF;

    IF EXISTS (
        SELECT 1
          FROM information_schema.columns
         WHERE table_schema='public' AND table_name='budgets' AND column_name='week_limit'
           AND (data_type <> 'numeric' OR numeric_precision IS DISTINCT FROM 18 OR numeric_scale IS DISTINCT FROM 2)
    ) THEN
        ALTER TABLE public.budgets
            ALTER COLUMN week_limit TYPE NUMERIC(18,2)
            USING week_limit::numeric(18,2);
    END IF;

    IF EXISTS (
        SELECT 1
          FROM information_schema.columns
         WHERE table_schema='public' AND table_name='budgets' AND column_name='month_limit'
           AND (data_type <> 'numeric' OR numeric_precision IS DISTINCT FROM 18 OR numeric_scale IS DISTINCT FROM 2)
    ) THEN
        ALTER TABLE public.budgets
            ALTER COLUMN month_limit TYPE NUMERIC(18,2)
            USING month_limit::numeric(18,2);
    END IF;

    IF EXISTS (
        SELECT 1
          FROM information_schema.columns
         WHERE table_schema='public' AND table_name='general_spending_limits' AND column_name='amount'
           AND (data_type <> 'numeric' OR numeric_precision IS DISTINCT FROM 18 OR numeric_scale IS DISTINCT FROM 2)
    ) THEN
        ALTER TABLE public.general_spending_limits
            ALTER COLUMN amount TYPE NUMERIC(18,2)
            USING amount::numeric(18,2);
    END IF;

    IF EXISTS (
        SELECT 1
          FROM information_schema.columns
         WHERE table_schema='public' AND table_name='category_budget_groups' AND column_name='amount'
           AND (data_type <> 'numeric' OR numeric_precision IS DISTINCT FROM 18 OR numeric_scale IS DISTINCT FROM 2)
    ) THEN
        ALTER TABLE public.category_budget_groups
            ALTER COLUMN amount TYPE NUMERIC(18,2)
            USING amount::numeric(18,2);
    END IF;
END $$;
