from copy import deepcopy

import pytest

from db.queries import ensure_user
from services.category_catalog import STARTER_CATEGORIES, category_appearance
from services.categories import normalized_category_key, rename_category


class RegistrationDB:
    def __init__(self):
        self.users = set()
        self.categories = []
        self.fail_seed = False

    def connect(self):
        db = self
        class Connection:
            def __init__(self):
                self.users = set(db.users)
                self.categories = deepcopy(db.categories)
                self.rowcount = 0

            def cursor(self):
                return self

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def execute(self, sql, params):
                if 'INSERT INTO public.users ' in sql:
                    self.rowcount = int(params[0] not in self.users)
                    self.users.add(params[0])
                elif 'INSERT INTO public.custom_categories' in sql:
                    if db.fail_seed:
                        raise RuntimeError('seed failed')
                    self.categories.append(params)

            def fetchone(self):
                return (10,)

            def commit(self):
                db.users = self.users
                db.categories = self.categories

            def rollback(self):
                pass

            def close(self):
                pass
        return Connection()


def test_starter_categories_only_created_by_first_registration(monkeypatch):
    db = RegistrationDB()
    monkeypatch.setattr('db.queries.get_conn', db.connect)
    assert ensure_user(1) is True
    assert len(db.categories) == 13
    assert ensure_user(1) is False
    assert len(db.categories) == 13
    assert all(row[0:2] == (10, 1) for row in db.categories)


def test_existing_users_never_receive_starter_categories(monkeypatch):
    db = RegistrationDB()
    db.users.add(1)
    monkeypatch.setattr('db.queries.get_conn', db.connect)
    assert ensure_user(1) is False
    assert db.categories == []


def test_failed_seed_rolls_back_user_and_retries_whole_registration(monkeypatch):
    db = RegistrationDB()
    db.fail_seed = True
    monkeypatch.setattr('db.queries.get_conn', db.connect)
    with pytest.raises(RuntimeError):
        ensure_user(1)
    assert db.users == set() and db.categories == []
    db.fail_seed = False
    assert ensure_user(1) is True
    assert len(db.categories) == 13


def test_catalog_reuses_predictor_fallback_and_has_no_duplicate_keys():
    keys = [(kind, normalized_category_key(name)) for kind, name, *_ in STARTER_CATEGORIES]
    assert len(keys) == len(set(keys)) == 13
    assert ('Расходы', 'другое') in keys
    for _, _, icon, color in STARTER_CATEGORIES:
        assert category_appearance(icon, color) == (icon, color)


@pytest.mark.parametrize(('icon', 'color'), [('external-url', '#187568'), ('home', 'red'), ('home', '#000000;display:none'), ('<script>', '#187568')])
def test_unknown_appearance_rejected(icon, color):
    with pytest.raises(ValueError):
        category_appearance(icon, color)


def test_appearance_only_change_does_not_touch_operations(monkeypatch):
    calls = []
    class Connection:
        def cursor(self): return self
        def __enter__(self): return self
        def __exit__(self, *args): return False
        def execute(self, sql, params): calls.append((sql, params))
        def fetchone(self): return (7,)
        def commit(self): calls.append(('COMMIT', ()))
        def rollback(self): calls.append(('ROLLBACK', ()))
        def close(self): pass
    monkeypatch.setattr('services.categories.get_conn', Connection)
    monkeypatch.setattr('services.categories._ensure_category_exists', lambda *args, **kwargs: None)
    result = rename_category(user_id=1, workspace_id=10, op_type='Расходы', source='Продукты', destination='Продукты',
                             shared_workspace=False, appearance=('home', '#2563EB'))
    assert result.category_id == 7
    assert 'COMMIT' in [sql for sql, _ in calls]
    assert not any('operations' in sql for sql, _ in calls)
    assert calls[0][1] == ('home', '#2563EB', 10, 'Расходы', 'продукты', 1, False)
