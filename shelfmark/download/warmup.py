"""Boot-time warm-up of the direct-download source.

The first AA search after a cold start pays for the whole cold path at once: DNS
resolution, electing a live mirror, spinning up headless Chrome and solving the
DDoS-Guard challenge. That is tens of seconds with the user sat at the search box.

Running one throwaway search shortly after boot moves that cost off the user's first
search. It primes the DNS cache, elects (and quarantines) mirrors, and leaves the
clearance cookie in the bypasser's per-domain cache, so the first real search reuses
it instead of solving from scratch.

Runs on a daemon thread and swallows every failure: this is an optimisation, and a
source that is down at boot must not affect startup or health.
"""

from __future__ import annotations

import contextlib
import os
import threading
import time
from typing import TYPE_CHECKING

from shelfmark.bypass.idle_timeout import helper_idle_timeout
from shelfmark.core.config import config
from shelfmark.core.logger import setup_logger

if TYPE_CHECKING:
    from collections.abc import Iterator

logger = setup_logger(__name__)

# Delay before the warm-up fires. Long enough that it does not compete with the rest
# of startup (and with a container's own health probe) for the first request.
_DEFAULT_DELAY_SECONDS = 15.0

_DEFAULT_QUERY = "The Great Gatsby"

_warmup_thread: threading.Thread | None = None
_warmup_lock = threading.Lock()

# Set as soon as a real release search starts. The warm-up exists to pay the cold path
# *before* the user does; once they have beaten it to the box there is nothing left to
# pre-solve, and running anyway is actively harmful - the bypasser serializes on one
# browser, so the warm-up's solve goes in front of the search the user is watching. In
# the bundle on issue #1276 that cost a full minute of a 2m27s wait, on a container 16
# seconds old, for a throwaway "The Great Gatsby" query nobody asked for.
_user_search_seen = threading.Event()

# Warm-up from a page open: when a warm-up or a real search last used the bypass helper,
# how many real searches are running, and the thread of a warm-up already running for a
# page open.
_connect_thread: threading.Thread | None = None
_last_bypass_use: float | None = None
_searches_in_flight = 0


def note_user_search() -> None:
    """Record that a real search has run, so a pending warm-up stands down."""
    global _last_bypass_use
    _user_search_seen.set()
    _last_bypass_use = time.monotonic()


@contextlib.contextmanager
def user_search() -> Iterator[None]:
    """Mark a real search as running; a page open starts no warm-up until it ends."""
    global _searches_in_flight, _last_bypass_use
    note_user_search()
    with _warmup_lock:
        _searches_in_flight += 1
    try:
        yield
    finally:
        with _warmup_lock:
            _searches_in_flight -= 1
            _last_bypass_use = time.monotonic()


def _as_bool(value: object, *, default: bool) -> bool:
    """Coerce a config value that may arrive as a string, bool or None."""
    if value is None:
        return default
    if isinstance(value, str):
        from shelfmark.config.env import string_to_bool

        return string_to_bool(value)
    return bool(value)


def _setting(key: str, default: object) -> object:
    """Read a warm-up setting, preferring the deployment environment.

    These keys are not in the settings registry, and ``config.get`` only consults the
    environment for keys it knows about - so reading config alone silently ignored
    SEARCH_WARMUP_ENABLED and always returned the default. Check os.environ first so
    the documented switches actually work.
    """
    raw = os.environ.get(key)
    if raw is not None and raw.strip():
        return raw
    return config.get(key, default)


def is_enabled() -> bool:
    """Whether the boot-time warm-up search should run."""
    if not _as_bool(_setting("SEARCH_WARMUP_ENABLED", True), default=True):
        return False
    # Nothing to warm if the source is off, and no challenge to pre-solve without
    # the bypasser - a plain search is fast enough not to need this.
    if not _as_bool(_setting("DIRECT_DOWNLOAD_ENABLED", True), default=True):
        logger.debug("Search warm-up skipped: direct download disabled")
        return False
    return True


def warmup_query() -> str:
    """The query used to warm the source."""
    raw = _setting("SEARCH_WARMUP_QUERY", _DEFAULT_QUERY)
    query = str(raw).strip() if raw else ""
    return query or _DEFAULT_QUERY


def run_warmup() -> bool:
    """Run one warm-up search. Returns True if it produced results.

    Never raises: every failure mode here is one the next real search would hit
    anyway, and reporting it is the search path's job, not the warm-up's.
    """
    from shelfmark.core.mirrors import has_aa_mirror_configuration

    # Checked here rather than only at schedule time: the delay is what this races with,
    # so the user's first search usually lands *during* the wait, not before it.
    if _user_search_seen.is_set():
        logger.info("Search warm-up skipped: a real search got there first")
        return False

    if not has_aa_mirror_configuration():
        logger.debug("Search warm-up skipped: no Anna's Archive mirrors configured")
        return False

    return _search_once()


def _search_once() -> bool:
    """Run the throwaway search. Returns True if it produced results."""
    global _last_bypass_use

    query = warmup_query()
    logger.info("Warming up direct download search (%r)", query)
    try:
        from shelfmark.core.models import SearchFilters
        from shelfmark.release_sources.direct_download import search_books

        results, _ = search_books(query, SearchFilters())
    except Exception:
        # Broad by design: a warm-up must never take the app down, and the source
        # raises everything from network errors to parse failures.
        logger.warning("Search warm-up did not complete; first user search may be slow")
        logger.debug("Search warm-up failure detail", exc_info=True)
        return False
    finally:
        _last_bypass_use = time.monotonic()

    if results:
        logger.info("Search warm-up complete: %s results, source is ready", len(results))
        return True
    logger.info("Search warm-up returned no results; source reachable but empty")
    return False


def warm_on_connect() -> bool:
    """Start a warm-up because a client opened the page. Returns True if one started.

    The boot warm-up only helps the first visit after a restart. Once the bypass helper
    has idled out, the next search pays the cold path again, so a page open is the cue
    to pay it ahead of the search box. It runs on a daemon thread and never raises.

    A warm-up is skipped while another is pending or running, while a real search is
    running, and while the last warm-up or search is within the helper's idle timeout,
    so a reload or a second tab must not start another solve. Like the boot warm-up, it
    needs an Anna's Archive mirror.
    """
    global _connect_thread, _last_bypass_use

    from shelfmark.core.mirrors import has_aa_mirror_configuration

    if not is_enabled() or not has_aa_mirror_configuration():
        return False

    with _warmup_lock:
        if _searches_in_flight:
            logger.debug("Search warm-up skipped: a search is running")
            return False

        for thread in (_warmup_thread, _connect_thread):
            if thread is not None and thread.is_alive():
                logger.debug("Search warm-up already pending or running")
                return False

        last_use = _last_bypass_use
        if last_use is not None and time.monotonic() - last_use < helper_idle_timeout():
            logger.debug("Search warm-up skipped: bypass helper still warm")
            return False

        # Claim the window before the thread runs, so a burst of connects starts one.
        _last_bypass_use = time.monotonic()
        _connect_thread = threading.Thread(
            target=_search_once, name="SearchWarmupOnConnect", daemon=True
        )
        _connect_thread.start()

    return True


def start(delay_seconds: float = _DEFAULT_DELAY_SECONDS) -> bool:
    """Schedule the warm-up on a daemon thread. Safe to call multiple times."""
    global _warmup_thread

    if not is_enabled():
        return False

    with _warmup_lock:
        if _warmup_thread is not None and _warmup_thread.is_alive():
            logger.debug("Search warm-up already scheduled")
            return False

        def _run() -> None:
            run_warmup()

        _warmup_thread = threading.Timer(delay_seconds, _run)
        _warmup_thread.daemon = True
        _warmup_thread.name = "SearchWarmup"
        _warmup_thread.start()

    logger.debug("Search warm-up scheduled in %ss", delay_seconds)
    return True
