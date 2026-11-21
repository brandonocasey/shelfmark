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


def test_page_open_warms_even_after_a_real_search_idled_out(monkeypatch):
    calls: list[str] = []
    _patch_search(monkeypatch, calls)
    warmup.note_user_search()
    monkeypatch.setattr(warmup, "_idle_window_seconds", lambda: 0.0)

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
