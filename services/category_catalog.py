"""Portable category presentation and the opt-in-at-registration starter set."""

CATEGORY_ICONS = ("operations", "home", "plans", "profile", "analytics", "income", "expense", "empty", "more")
CATEGORY_COLORS = ("#187568", "#2563EB", "#AD3153", "#8854B8", "#8A6116", "#426877", "#59636E")
DEFAULT_ICON = "operations"
DEFAULT_COLOR = "#187568"

STARTER_CATEGORIES = (
    ("Расходы", "Продукты", "empty", "#187568"),
    ("Расходы", "Кафе и рестораны", "operations", "#8A6116"),
    ("Расходы", "Жильё и счета", "home", "#2563EB"),
    ("Расходы", "Транспорт", "operations", "#426877"),
    ("Расходы", "Здоровье", "income", "#AD3153"),
    ("Расходы", "Покупки", "empty", "#8854B8"),
    ("Расходы", "Досуг и путешествия", "plans", "#187568"),
    ("Расходы", "Другое", "more", "#59636E"),
    ("Доходы", "Зарплата", "income", "#187568"),
    ("Доходы", "Подработка", "operations", "#2563EB"),
    ("Доходы", "Проценты и инвестиционный доход", "analytics", "#8854B8"),
    ("Доходы", "Подарки", "empty", "#AD3153"),
    ("Доходы", "Другое", "more", "#59636E"),
)


def category_appearance(icon=None, color=None) -> tuple[str, str]:
    icon = DEFAULT_ICON if icon is None else str(icon)
    color = DEFAULT_COLOR if color is None else str(color).upper()
    if icon not in CATEGORY_ICONS or color not in CATEGORY_COLORS:
        raise ValueError("invalid_category_appearance")
    return icon, color


def seed_new_user_categories_cur(cur, user_id: int) -> None:
    """Only called by the transaction that successfully INSERTs a new user."""
    from services.categories import normalized_category_key

    cur.execute(
        "INSERT INTO public.workspaces (name, kind, owner_user_id) "
        "VALUES (%s, 'personal', %s) RETURNING id",
        (f"Personal {user_id}", user_id),
    )
    workspace_id = int(cur.fetchone()[0])
    cur.execute(
        "INSERT INTO public.workspace_members (workspace_id, user_id, role, status) "
        "VALUES (%s, %s, 'owner', 'active')",
        (workspace_id, user_id),
    )
    cur.execute(
        "INSERT INTO public.user_workspace_settings (user_id, active_workspace_id) VALUES (%s, %s)",
        (user_id, workspace_id),
    )
    for op_type, name, icon, color in STARTER_CATEGORIES:
        cur.execute(
            """INSERT INTO public.custom_categories
               (workspace_id, user_id, type, name, normalized_name, icon, color)
               VALUES (%s, %s, %s, %s, %s, %s, %s)
               ON CONFLICT DO NOTHING""",
            (workspace_id, user_id, op_type, name, normalized_category_key(name), icon, color),
        )
