import type { ActivityCalendar } from '../types';
import { EmptyPanel, esc } from './ui';

function activityIntensity(count: number, max: number): number {
  if (!count || !max) return 0;
  return Math.max(1, Math.min(4, Math.ceil((count / max) * 4)));
}

export function ActivityCalendarView(calendar: ActivityCalendar | undefined, compact = false): string {
  if (!calendar?.days?.length) return EmptyPanel('Нет активности', 'За выбранный период операций не было.');
  const weekdays = ['Пн', 'Вт', 'Ср', 'Чт', 'Пт', 'Сб', 'Вс'];
  const monthFmt = new Intl.DateTimeFormat('ru-RU', { month: 'short' });
  const dateFmt = new Intl.DateTimeFormat('ru-RU', { day: 'numeric', month: 'long' });
  const first = new Date(`${calendar.days[0].date}T00:00:00`);
  const firstOffset = Number.isNaN(first.getTime()) ? 0 : (first.getDay() + 6) % 7;
  const padded: Array<{ kind: 'empty' } | { kind: 'day'; date: string; count: number }> = [
    ...Array.from({ length: firstOffset }, () => ({ kind: 'empty' as const })),
    ...calendar.days.map((day) => ({ kind: 'day' as const, date: day.date, count: day.count }))
  ];
  const weekCount = Math.max(1, Math.ceil(padded.length / 7));
  while (padded.length < weekCount * 7) padded.push({ kind: 'empty' });
  const monthLabels = Array.from({ length: weekCount }, (_value, weekIndex) => {
    const weekDays = padded.slice(weekIndex * 7, weekIndex * 7 + 7).filter((item): item is { kind: 'day'; date: string; count: number } => item.kind === 'day');
    const monthStart = weekDays.find((item) => new Date(`${item.date}T00:00:00`).getDate() === 1);
    const labelDay = monthStart || (weekIndex === 0 ? weekDays[0] : undefined);
    if (!labelDay) return '<span></span>';
    const current = new Date(`${labelDay.date}T00:00:00`);
    const previousDay = padded.slice(0, weekIndex * 7).reverse().find((item) => item.kind === 'day');
    const previous = previousDay && previousDay.kind === 'day' ? new Date(`${previousDay.date}T00:00:00`) : null;
    const changedMonth = !previous || previous.getMonth() !== current.getMonth() || previous.getFullYear() !== current.getFullYear() || Boolean(monthStart);
    return `<span>${changedMonth ? esc(monthFmt.format(current).replace('.', '')) : ''}</span>`;
  }).join('');
  const gridCells = padded.map((item) => {
    if (item.kind === 'empty') return '<span class="activity-cell empty" aria-hidden="true"></span>';
    const date = new Date(`${item.date}T00:00:00`);
    const label = `${dateFmt.format(date)} — ${item.count} операций`;
    const row = Number.isNaN(date.getTime()) ? 1 : ((date.getDay() + 6) % 7) + 1;
    return `<span class="activity-cell level-${activityIntensity(item.count, calendar.max_count)}" data-weekday-row="${row}" role="img" aria-label="${esc(label)}" title="${esc(label)}"></span>`;
  }).join('');
  return `
    <div class="activity-scroll ${compact ? 'compact' : ''}">
      <div class="activity-layout" style="--activity-weeks:${weekCount}">
        <div class="activity-months" aria-hidden="true">${monthLabels}</div>
        <div class="activity-weekdays" aria-hidden="true">${weekdays.map((day) => `<span>${esc(day)}</span>`).join('')}</div>
        <div class="activity-calendar">${gridCells}</div>
      </div>
    </div>
    ${compact ? '' : '<div class="activity-legend"><span>меньше</span><i class="level-0"></i><i class="level-1"></i><i class="level-2"></i><i class="level-3"></i><i class="level-4"></i><span>больше</span></div>'}
  `;
}
