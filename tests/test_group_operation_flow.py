from services.operations import category_options


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
