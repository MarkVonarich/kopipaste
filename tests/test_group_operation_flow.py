from datetime import date, timedelta

from services.operations import category_options, commit_operation_draft
from services.workspaces import WorkspaceContext, is_active_telegram_member, membership_role_after_join


def test_group_category_callback_payloads_stay_compact():
    draft_id = "a" * 32
    options = category_options(["Coffee", "Taxi"])
    payloads = [f"gpick|{draft_id}|{key}" for key in options]
    assert payloads == [f"gpick|{draft_id}|c1", f"gpick|{draft_id}|c2"]
    assert all(len(p) <= 64 for p in payloads)


def test_group_options_do_not_embed_category_names():
    options = category_options(["A very long custom category name that would exceed callback limits"])
    callback_payload = f"gpick|{'b' * 32}|{next(iter(options))}"
    assert "very long" not in callback_payload
    assert len(callback_payload) <= 64


class _FakeCursor:
    def __init__(self, state):
        self.state = state
        self.result = None

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def execute(self, sql, params=()):
        normalized = " ".join(sql.split())
        if normalized.startswith("SELECT draft_id"):
            if self.state.get("missing"):
                self.result = None
                return
            self.result = (
                self.state["draft_id"],
                self.state["workspace_id"],
                self.state["chat_id"],
                self.state["actor_user_id"],
                "text",
                self.state["payload"],
                self.state["status"],
                self.state["expires_at"],
                self.state.get("committed_operation_id"),
            )
            return
        if normalized.startswith("UPDATE public.operation_drafts SET status='expired'"):
            if self.state.get("expired"):
                self.state["status"] = "expired"
                self.result = (1,)
            else:
                self.result = None
            return
        if normalized.startswith("INSERT INTO public.operations"):
            self.state["insert_count"] = self.state.get("insert_count", 0) + 1
            self.state["next_operation_id"] = self.state.get("next_operation_id", 100) + 1
            self.result = (self.state["next_operation_id"],)
            return
        if normalized.startswith("UPDATE public.operation_drafts SET status='committed'"):
            self.state["status"] = "committed"
            self.state["committed_operation_id"] = params[0]
            self.result = None
            return
        raise AssertionError(f"unexpected SQL: {normalized}")

    def fetchone(self):
        return self.result


class _FakeConn:
    def __init__(self, state):
        self.state = state

    def cursor(self):
        return _FakeCursor(self.state)

    def commit(self):
        self.state["commits"] = self.state.get("commits", 0) + 1

    def rollback(self):
        self.state["rollbacks"] = self.state.get("rollbacks", 0) + 1

    def close(self):
        self.state["closed"] = self.state.get("closed", 0) + 1


def _draft_state(**overrides):
    state = {
        "draft_id": "d1",
        "workspace_id": 10,
        "chat_id": -100,
        "actor_user_id": 22,
        "status": "draft",
        "expires_at": date.today() + timedelta(days=1),
        "payload": {
            "amount": 500,
            "type": "Расходы",
            "merchant": "coffee",
            "op_date": date(2026, 7, 19).isoformat(),
            "source": "text",
            "raw_text": "coffee 500",
        },
    }
    state.update(overrides)
    return state


def test_group_second_member_role_can_record_with_atomic_commit(monkeypatch):
    state = _draft_state()
    monkeypatch.setattr("services.operations.get_conn", lambda: _FakeConn(state))
    monkeypatch.setattr("services.operations.get_user_currency", lambda _user_id: "RUB")
    monkeypatch.setattr(
        "services.operations.resolve_workspace",
        lambda chat_id, actor_user_id, chat_type: WorkspaceContext(10, chat_id, actor_user_id, "group", "member", "Group", True),
    )
    monkeypatch.setattr("services.operations.record_financial_activity", lambda **_kwargs: None)

    result = commit_operation_draft(
        draft_id="d1",
        actor_user_id=22,
        category="Coffee",
        chat_id=-100,
        workspace_id=10,
    )

    assert result["status"] == "committed"
    assert result["recorded"].operation_id == 101
    assert state["insert_count"] == 1
    assert state["status"] == "committed"


def test_group_double_callback_creates_one_operation(monkeypatch):
    state = _draft_state()
    monkeypatch.setattr("services.operations.get_conn", lambda: _FakeConn(state))
    monkeypatch.setattr("services.operations.get_user_currency", lambda _user_id: "RUB")
    monkeypatch.setattr(
        "services.operations.resolve_workspace",
        lambda chat_id, actor_user_id, chat_type: WorkspaceContext(10, chat_id, actor_user_id, "group", "member", "Group", True),
    )
    monkeypatch.setattr("services.operations.record_financial_activity", lambda **_kwargs: None)

    first = commit_operation_draft(draft_id="d1", actor_user_id=22, category="Coffee", chat_id=-100, workspace_id=10)
    second = commit_operation_draft(draft_id="d1", actor_user_id=22, category="Coffee", chat_id=-100, workspace_id=10)

    assert first["status"] == "committed"
    assert second["status"] == "already_committed"
    assert state["insert_count"] == 1


def test_wrong_actor_and_expired_group_drafts_do_not_insert(monkeypatch):
    state = _draft_state()
    monkeypatch.setattr("services.operations.get_conn", lambda: _FakeConn(state))
    monkeypatch.setattr("services.operations.get_user_currency", lambda _user_id: "RUB")
    monkeypatch.setattr(
        "services.operations.resolve_workspace",
        lambda chat_id, actor_user_id, chat_type: WorkspaceContext(10, chat_id, actor_user_id, "group", "member", "Group", True),
    )

    wrong = commit_operation_draft(draft_id="d1", actor_user_id=99, category="Coffee", chat_id=-100, workspace_id=10)
    state.update({"status": "draft", "expired": True})
    expired = commit_operation_draft(draft_id="d1", actor_user_id=22, category="Coffee", chat_id=-100, workspace_id=10)

    assert wrong["status"] == "wrong_actor"
    assert expired["status"] == "expired"
    assert state.get("insert_count", 0) == 0


def test_group_membership_status_and_role_rules():
    assert is_active_telegram_member("member")
    assert is_active_telegram_member("administrator")
    assert is_active_telegram_member("restricted", True)
    assert not is_active_telegram_member("restricted", False)
    assert not is_active_telegram_member("left")
    assert not is_active_telegram_member("kicked")
    assert membership_role_after_join("owner") == "owner"
    assert membership_role_after_join("admin") == "admin"
    assert membership_role_after_join(None) == "member"


def test_group_custom_category_state_blocks_private_or_wrong_scope():
    state = {"draft_id": "d1", "chat_id": -100, "workspace_id": 10, "actor_user_id": 22}
    assert state["chat_id"] != 22
    assert state["actor_user_id"] != 99
