import { formatMoneyString } from '../money';

type PlansData = {
  all_scope_note?: string | null;
  goals: Array<{ title: string; target: string; current: string; percent: number; currency: string; status: string; deadline?: string | null }>;
  limits: Array<{ category: string; amount: string; spent: string; remaining: string; percent: number; period: string; status: string; currency: string }>;
};

function esc(value: unknown): string {
  return String(value ?? '').replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
}

export function PlansScreen(plans: PlansData | null): string {
  if (plans?.all_scope_note) {
    return `<section class="screen"><div class="panel">${esc(plans.all_scope_note)}</div></section>`;
  }
  return `
    <section class="screen">
      <div class="panel">
        <strong>Цели</strong>
        ${(plans?.goals || []).map((goal) => `
          <div class="detail-row">
            <span>${esc(goal.title)}<br><small>${esc(goal.status)}${goal.deadline ? ` · ${esc(goal.deadline)}` : ''}</small></span>
            <strong>${formatMoneyString(goal.current, goal.currency)} / ${formatMoneyString(goal.target, goal.currency)}<br><small>${goal.percent}%</small></strong>
          </div>
        `).join('') || '<p class="caption">Целей пока нет.</p>'}
      </div>
      <div class="panel">
        <strong>Лимиты</strong>
        ${(plans?.limits || []).map((limit) => `
          <div class="detail-row">
            <span>${esc(limit.category)}<br><small>${esc(limit.period)} · ${esc(limit.status)}</small></span>
            <strong>${formatMoneyString(limit.spent, limit.currency)} / ${formatMoneyString(limit.amount, limit.currency)}<br><small>Осталось ${formatMoneyString(limit.remaining, limit.currency)} · ${limit.percent}%</small></strong>
          </div>
        `).join('') || '<p class="caption">Лимитов пока нет.</p>'}
      </div>
    </section>
  `;
}
