"""A page open warms the search source, once per idle window.

The boot warm-up only covers the first visit after a restart. After the bypass helper
idles out, the next search pays the cold path again, so opening the page starts a warm-up.
"""

import threading

import shelfmark.download.warmup as warmup


def _patch_search(monkeypatch, calls: list[str], gate: threading.Event | None = None):
    def fake_search(query, _filters):
        calls.append(query)
        if gate is not None:
            gate.wait(timeout=5)
        return ["a result"], None

    monkeypatch.setattr("shelfmark.release_sources.direct_download.search_books", fake_search)
    monkeypatch.setattr(
        "shelfmark.core.mirrors.has_aa_mirror_configuration", lambda: True, raising=False
    )
    monkeypatch.setattr(warmup, "_setting", lambda _key, _default: True)


def _join_connect_thread():
    thread = warmup._connect_thread
    assert thread is not None
    thread.join(timeout=5)


def test_page_open_starts_a_warmup(monkeypatch):
    calls: list[str] = []
    _patch_search(monkeypatch, calls)

    assert warmup.warm_on_connect() is True
    _join_connect_thread()

    assert calls == [warmup.warmup_query()]


def test_page_open_warms_once_the_idle_timeout_has_passed(monkeypatch):
    calls: list[str] = []
    _patch_search(monkeypatch, calls)
    now = [1000.0]
    monkeypatch.setattr(warmup.time, "monotonic", lambda: now[0])
    monkeypatch.setattr(
        "shelfmark.bypass.idle_timeout.config.get",
        lambda key, default=None: 60 if key == "BYPASS_BROWSER_IDLE_TIMEOUT" else default,
    )
    warmup.note_user_search()

    now[0] += 59
    assert warmup.warm_on_connect() is False

    now[0] += 2
    assert warmup.warm_on_connect() is True
    _join_connect_thread()

    assert calls == [warmup.warmup_query()]


def test_page_open_inside_the_idle_window_does_nothing(monkeypatch):
    calls: list[str] = []
    _patch_search(monkeypatch, calls)
    warmup.note_user_search()

    assert warmup.warm_on_connect() is False
    assert calls == []


def test_a_burst_of_page_opens_starts_one_warmup(monkeypatch):
    calls: list[str] = []
    gate = threading.Event()
    _patch_search(monkeypatch, calls, gate)

    try:
        assert warmup.warm_on_connect() is True
        assert warmup.warm_on_connect() is False
    finally:
        gate.set()
    _join_connect_thread()

    assert len(calls) == 1


def test_page_open_waits_for_a_pending_boot_warmup(monkeypatch):
    calls: list[str] = []
    _patch_search(monkeypatch, calls)
    gate = threading.Event()
    boot = threading.Thread(target=gate.wait, daemon=True)
    boot.start()
    monkeypatch.setattr(warmup, "_warmup_thread", boot)

    try:
        assert warmup.warm_on_connect() is False
    finally:
        gate.set()
        boot.join(timeout=5)

    assert calls == []


def test_page_open_respects_the_warmup_switch(monkeypatch):
    calls: list[str] = []
    _patch_search(monkeypatch, calls)
    monkeypatch.setattr(warmup, "_setting", lambda _key, _default: False)

    assert warmup.warm_on_connect() is False
    assert calls == []


def test_page_open_without_a_mirror_starts_no_warmup(monkeypatch):
    calls: list[str] = []
    _patch_search(monkeypatch, calls)
    monkeypatch.setattr(
        "shelfmark.core.mirrors.has_aa_mirror_configuration", lambda: False, raising=False
    )

    assert warmup.warm_on_connect() is False
    assert calls == []


def test_page_open_during_a_long_search_starts_no_warmup(monkeypatch):
    calls: list[str] = []
    _patch_search(monkeypatch, calls)
    now = [1000.0]
    monkeypatch.setattr(warmup.time, "monotonic", lambda: now[0])

    with warmup.user_search():
        now[0] += 10_000
        assert warmup.warm_on_connect() is False

    assert calls == []


def test_the_idle_window_restarts_when_a_search_ends(monkeypatch):
    calls: list[str] = []
    _patch_search(monkeypatch, calls)
    now = [1000.0]
    monkeypatch.setattr(warmup.time, "monotonic", lambda: now[0])

    with warmup.user_search():
        now[0] += 250

    now[0] += 100
    assert warmup.warm_on_connect() is False
    assert calls == []
