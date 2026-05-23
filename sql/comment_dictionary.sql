-- Комментарии к таблицам и полям для DBeaver/документации
DO $$
DECLARE r RECORD;
BEGIN
  -- ===== Комментарии к ТАБЛИЦАМ =====
  COMMENT ON TABLE public.users                IS 'Профиль и настройки пользователя';
  COMMENT ON TABLE public.records              IS 'Финансовые записи (нормализованные; ядро аналитики)';
  COMMENT ON TABLE public.operations           IS 'Историческая v1-таблица разборов текстов (для бэкапа/миграций)';
  COMMENT ON TABLE public.global_aliases       IS 'Глобальные алиасы текста → тип/категория';
  COMMENT ON TABLE public.user_aliases         IS 'Персональные алиасы пользователя';
  COMMENT ON TABLE public.category_aliases     IS 'Персональные алиасы (тип/категория/подкатегория) по синонимам';
  COMMENT ON TABLE public.user_aliases_bak     IS 'Бэкап алиасов пользователей (безопасные миграции)';
  COMMENT ON TABLE public.category_limits      IS 'Лимиты по категориям на период (week/month)';
  COMMENT ON TABLE public.category_limit_state IS 'Состояние уведомлений по лимитам (последняя ступень, mute)';
  COMMENT ON TABLE public.user_budgets         IS 'Индивидуальные агрегатные бюджеты (неделя/месяц)';
  COMMENT ON TABLE public.budgets              IS 'Устаревший вариант бюджетов (держим консистентно, миграции later)';
  COMMENT ON TABLE public.goals                IS 'Цели-накопления и прогресс';
  COMMENT ON TABLE public.fx_cache             IS 'Кэш курсов валют к RUB (для нормализации amount_rub)';
  COMMENT ON TABLE public.reminders_log        IS 'Лог отправленных напоминаний (актуальный)';
  COMMENT ON TABLE public.remind_log           IS 'Старый лог напоминаний';
  COMMENT ON TABLE public.events               IS 'Произвольные продуктовые события/аналитика';
  COMMENT ON TABLE public.action_log           IS 'Аудит действий (кто/что/когда, JSON-метаданные)';
  COMMENT ON TABLE public.action_tokens        IS 'Временные payload’ы для пошаговых сценариев/кнопок';
  COMMENT ON TABLE public.model_meta           IS 'Метаданные ML-моделей (обучение/качество/extra)';

  -- ===== Комментарии к КОЛОНКАМ (массово) =====
  FOR r IN
    SELECT * FROM (VALUES
      -- users
      ('users','user_id','ID пользователя (Telegram)'),
      ('users','locale','Локаль интерфейса (например, ru/en)'),
      ('users','currency','Базовая валюта пользователя (ISO)'),
      ('users','tz_offset_min','Смещение часового пояса (в минутах)'),
      ('users','reminder_hour','Час ежедневного напоминания (локальное время)'),
      ('users','plan','Тарифный план пользователя'),
      ('users','ml_consent','Согласие на ML-обучение (bool)'),
      ('users','created_at','Когда профиль был создан'),
      ('users','display_name','Имя, показываемое в интерфейсе'),
      ('users','updated_at','Последнее обновление профиля'),
      ('users','onboarding_done','Пройден ли онбординг (bool)'),

      -- records (ядро)
      ('records','id','PK записи'),
      ('records','user_id','ID пользователя'),
      ('records','chat_id','ID чата (если нужно для групп/лички)'),
      ('records','record_date','Дата операции (локальная)'),
      ('records','type','Тип: expense|income|goal'),
      ('records','category','Категория'),
      ('records','subcategory','Подкатегория'),
      ('records','amount','Сумма в исходной валюте'),
      ('records','currency','ISO-код исходной валюты'),
      ('records','amount_rub','Сумма в RUB (нормализация для сравнений)'),
      ('records','comment','Комментарий пользователя'),
      ('records','created_at','Timestamp вставки записи'),

      -- operations (историческая таблица парсинга)
      ('operations','id','PK'),
      ('operations','chat_id','ID чата'),
      ('operations','op_date','Дата операции'),
      ('operations','weekday','День недели (текст)'),
      ('operations','week_range','Диапазон недели (текст)'),
      ('operations','type','Тип операции'),
      ('operations','category','Категория'),
      ('operations','amount','Сумма в целевой валюте'),
      ('operations','comment','Комментарий (по умолчанию From Telegram)'),
      ('operations','created_at','Когда сохранено'),
      ('operations','user_id','ID пользователя'),
      ('operations','raw_text','Исходный текст сообщения'),

      -- aliases
      ('global_aliases','id','PK'),
      ('global_aliases','norm_text','Нормализованный текст (токен)'),
      ('global_aliases','type','Тип операции'),
      ('global_aliases','category','Категория (глобальная маппа)'),
      ('global_aliases','popularity','Популярность/вес алиаса'),
      ('global_aliases','updated_at','Когда обновили'),

      ('user_aliases','id','PK'),
      ('user_aliases','user_id','ID пользователя'),
      ('user_aliases','norm_text','Нормализованный текст алиаса'),
      ('user_aliases','type','Тип операции'),
      ('user_aliases','category','Категория'),
      ('user_aliases','updated_at','Когда обновили'),

      ('category_aliases','user_id','ID пользователя'),
      ('category_aliases','alias','Синоним/алиас (сырое слово/фраза)'),
      ('category_aliases','rtype','Тип операции'),
      ('category_aliases','category','Категория'),
      ('category_aliases','subcategory','Подкатегория'),

      ('user_aliases_bak','id','PK (бэкап)'),
      ('user_aliases_bak','user_id','ID пользователя'),
      ('user_aliases_bak','norm_text','Нормализованный текст'),
      ('user_aliases_bak','type','Тип операции'),
      ('user_aliases_bak','category','Категория'),
      ('user_aliases_bak','updated_at','Когда обновили'),

      -- limits & budgets
      ('category_limits','user_id','ID пользователя'),
      ('category_limits','period','Период: week|month'),
      ('category_limits','category','Категория'),
      ('category_limits','amount','Лимит (в currency)'),
      ('category_limits','currency','Валюта лимита'),
      ('category_limits','updated_at','Когда обновили'),

      ('category_limit_state','user_id','ID пользователя'),
      ('category_limit_state','period','Период: week|month'),
      ('category_limit_state','category','Категория'),
      ('category_limit_state','last_band','Последняя «ступень» уведомления'),
      ('category_limit_state','muted_until','Глушить уведомления до указанной даты'),
      ('category_limit_state','updated_at','Когда обновили'),

      ('user_budgets','user_id','ID пользователя'),
      ('user_budgets','week_limit','Лимит на неделю'),
      ('user_budgets','month_limit','Лимит на месяц'),
      ('user_budgets','updated_at','Когда обновили'),

      ('budgets','week_limit','Лимит на неделю'),
      ('budgets','month_limit','Лимит на месяц'),
      ('budgets','user_id','ID пользователя'),
      ('budgets','updated_at','Когда обновили'),

      -- goals
      ('goals','id','PK'),
      ('goals','user_id','ID пользователя'),
      ('goals','name','Название цели'),
      ('goals','target_amount','Целевая сумма'),
      ('goals','saved_amount','Уже накоплено'),
      ('goals','deadline','Дедлайн/дата'),
      ('goals','created_at','Когда создано'),

      -- FX
      ('fx_cache','code','ISO-код валюты'),
      ('fx_cache','rate_to_rub','Курс к RUB'),
      ('fx_cache','updated_at','Когда обновили'),

      -- reminders
      ('reminders_log','user_id','ID пользователя'),
      ('reminders_log','sent_on','Дата отправки напоминания'),
      ('reminders_log','kind','Тип напоминания'),
      ('reminders_log','tmpl_id','ID шаблона (если есть)'),
      ('reminders_log','tag','Тег отправки'),
      ('reminders_log','sent_at','Точное время отправки'),

      ('remind_log','id','PK (legacy)'),
      ('remind_log','sent_at','Время отправки (legacy)'),
      ('remind_log','user_id','ID пользователя (legacy)'),

      -- events
      ('events','id','PK'),
      ('events','user_id','ID пользователя (опционально)'),
      ('events','event_ts','Время события'),
      ('events','name','Имя события'),
      ('events','props_json','JSON-свойства события'),

      -- action & tokens
      ('action_log','id','PK'),
      ('action_log','created_at','Когда записали действие'),
      ('action_log','user_id','ID пользователя'),
      ('action_log','chat_id','ID чата'),
      ('action_log','action','Имя действия'),
      ('action_log','metadata','JSON-метаданные'),

      ('action_tokens','id','PK'),
      ('action_tokens','user_id','ID пользователя'),
      ('action_tokens','chat_id','ID чата'),
      ('action_tokens','payload','JSON-полезная нагрузка (для сценариев)'),
      ('action_tokens','created_at','Когда создано'),

      -- ML
      ('model_meta','id','PK'),
      ('model_meta','model_name','Имя модели/пайплайна'),
      ('model_meta','trained_at','Когда обучена'),
      ('model_meta','n_samples','Размер обучающей выборки'),
      ('model_meta','acc_type','Точность по типу'),
      ('model_meta','acc_micro','Micro-accuracy'),
      ('model_meta','extra','JSON с доп. сведениями')

    ) AS t(tbl,col,descr)
  LOOP
    EXECUTE format('COMMENT ON COLUMN public.%I.%I IS %L;', r.tbl, r.col, r.descr);
  END LOOP;
END$$;
