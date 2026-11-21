"""The relevance sort needs no download counts, so a search under it skips the lookups.

The counts cost one request per result, most of a warm search. Only the "most downloads"
order and the list's download column use them.
"""

import pytest

from shelfmark.core import search_deadline
from shelfmark.core.models import SearchFilters
from shelfmark.release_sources.direct_download import annas_archive as aa


@pytest.fixture(autouse=True)
def _no_ambient_deadline():
    token = search_deadline._current.set(None)
    yield
    search_deadline._current.reset(token)


_ROW = (
    "<tr><td><img src='c.jpg'></td>"
    "<td><a href='/md5/abc123'><span>Dune</span></a></td>"
    "<td><span>Frank Herbert</span></td><td><span>Ace</span></td><td><span>1965</span></td>"
    "<td><span>path</span></td><td><span>src</span></td><td><span>en</span></td>"
    "<td><span>Book (fiction)</span></td><td><span>epub</span></td><td><span>1MB</span></td>"
    "<td></td></tr>"
)
_PAGE = f"<html><body><main><table><tbody>{_ROW}</tbody></table></main></body></html>"


def _search(monkeypatch, sort: str | None) -> list[list[str]]:
    enriched: list[list[str]] = []
    monkeypatch.setattr(aa.downloader, "html_get_page", lambda _url, **_k: _PAGE)
    monkeypatch.setattr(aa.network, "get_available_aa_urls", lambda: ["https://annas-archive.gl"])
    monkeypatch.setattr(aa.network, "get_aa_base_url", lambda: "https://annas-archive.gl")
    monkeypatch.setattr(
        aa, "_enrich_search_results_with_downloads", lambda books: enriched.append(books)
    )

    books, _total = aa.search_books("dune", SearchFilters(sort=sort))

    assert [book.id for book in books] == ["abc123"]
    return enriched


def test_relevance_sort_skips_the_count_lookups(monkeypatch):
    assert _search(monkeypatch, "relevance") == []


@pytest.mark.parametrize("sort", [None, "", "downloads", "newest"])
def test_other_sorts_still_fetch_the_counts(monkeypatch, sort):
    assert len(_search(monkeypatch, sort)) == 1
