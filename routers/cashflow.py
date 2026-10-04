import asyncio

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, WebAppInfo

from db.queries import get_user_currency
from services.cashflow import CashflowRepository, cash_amount
from services.workspaces import resolve_workspace
from services.user_time import user_local_date
from settings import MINIAPP_PUBLIC_URL
from utils.money import format_money


def _preview(user_id, args):
    workspace = resolve_workspace(user_id, user_id)
    currency = get_user_currency(user_id)
    balance = cash_amount(args[0].replace(',', '.'))
    horizon_arg = args[1] if len(args) > 1 else 'месяц'
    horizon = {'месяц': 'month_end', 'доход': 'next_income'}.get(horizon_arg, 'date')
    reserve = cash_amount(args[2].replace(',', '.')) if len(args) > 2 else cash_amount('0')
    if len(args) > 3 or reserve < 0:
        raise ValueError('invalid_forecast_arguments')
    return CashflowRepository().preview(
        user_id=user_id, workspace_id=workspace.workspace_id, workspace_kind=workspace.kind,
        currency=currency, default_currency=currency, today=user_local_date(user_id, workspace.workspace_id),
        horizon=horizon, end_date=horizon_arg if horizon == 'date' else None, balance=balance, reserve=reserve,
    )


async def cmd_forecast(update, context):
    if update.effective_chat.type != 'private':
        await update.message.reply_text('Прогноз личных денег доступен в личном чате с ботом.')
        return
    keyboard = InlineKeyboardMarkup([[InlineKeyboardButton('Открыть КопиPaste', web_app=WebAppInfo(url=MINIAPP_PUBLIC_URL))]]) if MINIAPP_PUBLIC_URL else None
    if not context.args:
        await update.message.reply_text(
            'Деньги до даты\n\nУкажите фактический остаток, горизонт и резерв.\n'
            '/forecast 10000 месяц 2000\n/forecast 10000 доход 2000\n/forecast 10000 2026-12-01 2000\n\n'
            'Валюта и личное пространство берутся из настроек. Ожидаемый доход не считается деньгами сейчас. '
            'Ежедневный сценарий доступен на главной Mini App в «Деньги до даты».',
            reply_markup=keyboard,
        )
        return
    try:
        result = await asyncio.to_thread(_preview, update.effective_user.id, context.args)
    except ValueError:
        await update.message.reply_text('Проверьте остаток, резерв и дату в пределах года. Выберите личное пространство в настройках.')
        return
    money = lambda amount: format_money(amount, result['currency'])
    lines = [
        f"Прогноз до {result['end_date'] or 'следующего дохода'}",
        f"Подтверждённый остаток сейчас: {money(result['current_balance'])}",
        f"Ожидаемые доходы: {money(result['expected_income'])}",
        f"Неоплаченные платежи: {money(result['planned_expense'])}",
        f"Защищённый резерв: {money(result['protected_reserve'])}",
        f"Будущие взносы в цели: {money(result['goal_reserve'])}",
    ]
    if result['can_spend'] is None:
        lines.append('Можно потратить: недостаточно данных.')
        if 'spending_history' in result['missing']:
            lines.append('Нужно минимум 14 отмеченных дней расходов за последние 60 дней.')
        if 'next_income_date' in result['missing']:
            lines.append('Добавьте будущий доход с датой в напоминаниях.')
    else:
        lines.extend([f"Можно потратить: примерно {money(result['can_spend'])}", f"Ориентир расходов в день: {money(result['daily_allowance'])}"])
    if result['exhaustion_without_income']:
        lines.append(f"Без нового дохода деньги могут закончиться {result['exhaustion_without_income']}.")
    lines.append('Расчёт: остаток минус платежи, резервы и обычные расходы. Будущие доходы не включены в доступную сумму. Это оценка, не гарантия.')
    await update.message.reply_text('\n'.join(lines), reply_markup=keyboard)
