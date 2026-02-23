"""Tests for aggregator modules (dedup, filter, merger)."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from aggregator.dedup import deduplicate, extract_arxiv_id
from aggregator.filter import filter_by_score, filter_by_source, sort_by_score
from aggregator.merger import categorize_item, group_by_category
from schema import ContentItem, SourceType


def _item(source="test", title="Test", url="", score=0, arxiv_id=None):
    return ContentItem(
        source=source,
        source_type=SourceType.API,
        title=title,
        url=url,
        score=score,
        arxiv_id=arxiv_id,
    )


class TestDedup:
    def test_url_dedup(self):
        items = [
            _item(source="hf", title="Paper A", url="https://example.com/1", score=10),
            _item(source="cool", title="Paper A (copy)", url="https://example.com/1", score=5),
        ]
        result = deduplicate(items)
        assert len(result) == 1
        assert result[0].score == 10  # Higher score kept

    def test_arxiv_id_dedup(self):
        items = [
            _item(source="hf", title="Paper X", url="https://hf.co/1",
                  score=30, arxiv_id="2401.12345"),
            _item(source="cool", title="Paper X on Cool", url="https://cool.com/1",
                  score=20, arxiv_id="2401.12345"),
        ]
        result = deduplicate(items)
        assert len(result) == 1

    def test_title_similarity_dedup(self):
        items = [
            _item(source="reddit", title="OpenAI releases GPT-5 model",
                  url="https://reddit.com/1", score=100),
            _item(source="x_twitter", title="OpenAI releases GPT-5 model!",
                  url="https://x.com/1", score=50),
        ]
        result = deduplicate(items, title_threshold=0.85)
        assert len(result) == 1

    def test_different_items_kept(self):
        items = [
            _item(source="github", title="Repo A", url="https://github.com/a"),
            _item(source="github", title="Repo B", url="https://github.com/b"),
            _item(source="reddit", title="Totally different", url="https://reddit.com/1"),
        ]
        result = deduplicate(items)
        assert len(result) == 3


class TestExtractArxivId:
    def test_from_url(self):
        assert extract_arxiv_id("https://arxiv.org/abs/2401.12345") == "2401.12345"
        assert extract_arxiv_id("https://arxiv.org/abs/2401.12345v2") == "2401.12345"

    def test_no_match(self):
        assert extract_arxiv_id("https://github.com/repo") is None


class TestFilter:
    def test_filter_by_score(self):
        items = [_item(score=10), _item(score=50), _item(score=100)]
        result = filter_by_score(items, threshold=50)
        assert len(result) == 2

    def test_filter_by_source(self):
        items = [
            _item(source="github"), _item(source="reddit"), _item(source="github"),
        ]
        result = filter_by_source(items, "github")
        assert len(result) == 2

    def test_sort_by_score(self):
        items = [_item(score=10), _item(score=50), _item(score=30)]
        result = sort_by_score(items)
        assert [i.score for i in result] == [50, 30, 10]


class TestMerger:
    def test_categorize_github(self):
        item = _item(source="github")
        assert categorize_item(item) == "Developer Tools"

    def test_categorize_paper_with_arxiv(self):
        item = _item(source="huggingface", arxiv_id="2401.12345")
        assert categorize_item(item) == "Research Papers"

    def test_categorize_youtube(self):
        item = _item(source="youtube")
        assert categorize_item(item) == "Videos & Podcasts"

    def test_group_by_category(self):
        items = [
            _item(source="github", title="GH 1", score=10),
            _item(source="github", title="GH 2", score=20),
            _item(source="reddit", title="Reddit 1", score=50),
            _item(source="youtube", title="YT 1", score=5),
        ]
        grouped = group_by_category(items)
        assert len(grouped["Developer Tools"]) == 2
        assert len(grouped["Social & Community"]) == 1
        assert len(grouped["Videos & Podcasts"]) == 1
        # Sorted by score desc within category
        assert grouped["Developer Tools"][0].score == 20
