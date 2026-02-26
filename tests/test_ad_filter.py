"""Tests for aggregator.ad_filter module."""

from __future__ import annotations

import pytest

from aggregator.ad_filter import AdFilter, GLOBAL_AD_KEYWORDS, BLOCKED_URL_PATTERNS
from schema import ContentItem, SourceType


def _make_item(
    title: str = "Normal Title",
    content: str = "",
    url: str = "https://example.com/post",
    source: str = "reddit",
    score: float = 100.0,
    extra: dict | None = None,
) -> ContentItem:
    """Helper to create a ContentItem for testing."""
    return ContentItem(
        source=source,
        source_type=SourceType.API,
        title=title,
        url=url,
        content=content,
        score=score,
        extra=extra or {},
    )


# ---------------------------------------------------------------------------
# Default config for tests
# ---------------------------------------------------------------------------
DEFAULT_CONFIG: dict = {
    "enabled": True,
    "title_keywords": [],
    "content_keywords": [],
    "url_blocklist": [],
    "per_source": {
        "reddit": {
            "title_keywords": [],
            "min_upvote_ratio": 0.4,
        },
        "youtube": {
            "title_keywords": ["#ad", "#sponsored"],
        },
    },
}


# ===========================================================================
# TestAdFilterKeywords
# ===========================================================================
class TestAdFilterKeywords:
    """Test keyword-based ad detection."""

    def test_blocks_sponsored_title(self):
        af = AdFilter(DEFAULT_CONFIG)
        item = _make_item(title="This is a Sponsored post about AI")
        is_ad, reason = af.is_ad(item)
        assert is_ad is True
        assert "keyword_match" in reason

    def test_blocks_promoted_title(self):
        af = AdFilter(DEFAULT_CONFIG)
        item = _make_item(title="[Promoted] New AI Tool Release")
        is_ad, reason = af.is_ad(item)
        assert is_ad is True
        assert "keyword_match" in reason

    def test_blocks_advertisement_in_content(self):
        af = AdFilter(DEFAULT_CONFIG)
        item = _make_item(
            title="Check this out",
            content="This is an advertisement for a new product.",
        )
        is_ad, reason = af.is_ad(item)
        assert is_ad is True

    def test_blocks_affiliate_link_phrase(self):
        af = AdFilter(DEFAULT_CONFIG)
        item = _make_item(content="Use my code SAVE20 for 20% off!")
        is_ad, reason = af.is_ad(item)
        assert is_ad is True

    def test_blocks_buy_now(self):
        af = AdFilter(DEFAULT_CONFIG)
        item = _make_item(title="Buy Now - Limited Time Only!")
        is_ad, reason = af.is_ad(item)
        assert is_ad is True

    def test_blocks_chinese_ad_keywords(self):
        af = AdFilter(DEFAULT_CONFIG)
        cases = ["这是一条广告", "推广内容", "限时优惠来袭", "种草好物分享", "带货直播", "恰饭视频"]
        for text in cases:
            item = _make_item(title=text)
            is_ad, reason = af.is_ad(item)
            assert is_ad is True, f"Should block: {text}"
            assert "keyword_match" in reason

    def test_passes_normal_content(self):
        af = AdFilter(DEFAULT_CONFIG)
        normal_titles = [
            "New GPT-5 model released by OpenAI",
            "Understanding transformer architectures",
            "论文解读：Attention Is All You Need",
            "深度学习入门教程",
            "How to fine-tune LLaMA with LoRA",
        ]
        for title in normal_titles:
            item = _make_item(title=title)
            is_ad, reason = af.is_ad(item)
            assert is_ad is False, f"False positive on: {title}"

    def test_case_insensitive(self):
        af = AdFilter(DEFAULT_CONFIG)
        variations = ["SPONSORED", "Sponsored", "sponsored", "SpOnSoReD"]
        for word in variations:
            item = _make_item(title=f"This is {word}")
            is_ad, reason = af.is_ad(item)
            assert is_ad is True, f"Should block case variation: {word}"

    def test_hashtag_ad(self):
        af = AdFilter(DEFAULT_CONFIG)
        item = _make_item(title="Great new tool #ad")
        is_ad, reason = af.is_ad(item)
        assert is_ad is True

    def test_discount_code_in_content(self):
        af = AdFilter(DEFAULT_CONFIG)
        item = _make_item(content="Get 50% off with discount code AI50")
        is_ad, reason = af.is_ad(item)
        assert is_ad is True


# ===========================================================================
# TestAdFilterURL
# ===========================================================================
class TestAdFilterURL:
    """Test URL blocklist-based ad detection."""

    def test_blocks_affiliate_links(self):
        af = AdFilter(DEFAULT_CONFIG)
        blocked_urls = [
            "https://bit.ly/3abc123",
            "https://tinyurl.com/something",
            "https://amzn.to/product123",
        ]
        for url in blocked_urls:
            item = _make_item(url=url)
            is_ad, reason = af.is_ad(item)
            assert is_ad is True, f"Should block URL: {url}"
            assert "blocked_url" in reason

    def test_blocks_ecommerce_urls(self):
        af = AdFilter(DEFAULT_CONFIG)
        item = _make_item(url="https://shopify.com/store/product")
        is_ad, reason = af.is_ad(item)
        assert is_ad is True
        assert "blocked_url" in reason

    def test_passes_normal_urls(self):
        af = AdFilter(DEFAULT_CONFIG)
        normal_urls = [
            "https://arxiv.org/abs/2401.12345",
            "https://github.com/user/repo",
            "https://www.reddit.com/r/MachineLearning/post",
            "https://huggingface.co/papers/2401.12345",
            "https://openai.com/blog/gpt-5",
        ]
        for url in normal_urls:
            item = _make_item(url=url)
            is_ad, reason = af.is_ad(item)
            assert is_ad is False, f"False positive on URL: {url}"

    def test_custom_url_blocklist(self):
        config = {**DEFAULT_CONFIG, "url_blocklist": [r"spamsite\.com"]}
        af = AdFilter(config)
        item = _make_item(url="https://spamsite.com/landing")
        is_ad, reason = af.is_ad(item)
        assert is_ad is True


# ===========================================================================
# TestAdFilterPerSource
# ===========================================================================
class TestAdFilterPerSource:
    """Test per-source filtering rules."""

    def test_reddit_low_upvote_ratio(self):
        af = AdFilter(DEFAULT_CONFIG)
        item = _make_item(
            source="reddit",
            title="Amazing new AI framework",
            extra={"upvote_ratio": 0.3, "num_comments": 2},
        )
        is_ad, reason = af.is_ad(item)
        assert is_ad is True
        assert "reddit_low_ratio" in reason

    def test_reddit_normal_upvote_ratio(self):
        af = AdFilter(DEFAULT_CONFIG)
        item = _make_item(
            source="reddit",
            title="Amazing new AI framework",
            extra={"upvote_ratio": 0.85, "num_comments": 50},
        )
        is_ad, reason = af.is_ad(item)
        assert is_ad is False

    def test_reddit_missing_upvote_ratio_defaults_pass(self):
        """Items without upvote_ratio should default to 1.0 and pass."""
        af = AdFilter(DEFAULT_CONFIG)
        item = _make_item(source="reddit", title="Normal post", extra={})
        is_ad, reason = af.is_ad(item)
        assert is_ad is False

    def test_youtube_sponsored_tag(self):
        af = AdFilter(DEFAULT_CONFIG)
        item = _make_item(
            source="youtube",
            title="Cool AI Tool Review #ad",
        )
        is_ad, reason = af.is_ad(item)
        assert is_ad is True

    def test_youtube_sponsored_hashtag(self):
        af = AdFilter(DEFAULT_CONFIG)
        item = _make_item(
            source="youtube",
            title="Best GPU for LLM Training #sponsored",
        )
        is_ad, reason = af.is_ad(item)
        assert is_ad is True

    def test_youtube_normal_title(self):
        af = AdFilter(DEFAULT_CONFIG)
        item = _make_item(
            source="youtube",
            title="How to Train Your Own LLM",
        )
        is_ad, reason = af.is_ad(item)
        assert is_ad is False

    def test_source_without_rules_passes(self):
        """Sources not in per_source config should not trigger source rules."""
        af = AdFilter(DEFAULT_CONFIG)
        item = _make_item(source="huggingface", title="New paper on reasoning")
        is_ad, reason = af.is_ad(item)
        assert is_ad is False


# ===========================================================================
# TestAdFilterIntegration
# ===========================================================================
class TestAdFilterIntegration:
    """Integration tests for filter() method."""

    def test_filter_returns_clean_and_blocked(self):
        af = AdFilter(DEFAULT_CONFIG)
        items = [
            _make_item(title="Normal AI paper discussion"),
            _make_item(title="Sponsored: Amazing new tool"),
            _make_item(title="深度学习最新进展"),
            _make_item(title="限时优惠：AI课程打折"),
            _make_item(url="https://bit.ly/scam123"),
        ]
        clean, blocked = af.filter(items)
        assert len(clean) == 2
        assert len(blocked) == 3
        # Verify original list is unchanged
        assert len(items) == 5

    def test_disabled_filter_passes_all(self):
        config = {"enabled": False}
        af = AdFilter(config)
        items = [
            _make_item(title="Sponsored post"),
            _make_item(title="Buy now!"),
            _make_item(url="https://bit.ly/spam"),
        ]
        clean, blocked = af.filter(items)
        assert len(clean) == 3
        assert len(blocked) == 0

    def test_disabled_is_ad_returns_false(self):
        config = {"enabled": False}
        af = AdFilter(config)
        item = _make_item(title="Sponsored post")
        is_ad, reason = af.is_ad(item)
        assert is_ad is False
        assert reason == ""

    def test_empty_items_list(self):
        af = AdFilter(DEFAULT_CONFIG)
        clean, blocked = af.filter([])
        assert clean == []
        assert blocked == []

    def test_all_clean(self):
        af = AdFilter(DEFAULT_CONFIG)
        items = [
            _make_item(title="GPT-5 benchmarks analysis"),
            _make_item(title="New transformer architecture paper"),
        ]
        clean, blocked = af.filter(items)
        assert len(clean) == 2
        assert len(blocked) == 0

    def test_all_blocked(self):
        af = AdFilter(DEFAULT_CONFIG)
        items = [
            _make_item(title="Sponsored AI course"),
            _make_item(title="限时优惠"),
        ]
        clean, blocked = af.filter(items)
        assert len(clean) == 0
        assert len(blocked) == 2

    def test_custom_keywords_extend_global(self):
        """Custom keywords in config should work alongside global defaults."""
        config = {
            **DEFAULT_CONFIG,
            "title_keywords": [r"\bfree trial\b"],
        }
        af = AdFilter(config)
        # Custom keyword should trigger
        item1 = _make_item(title="Get your free trial today")
        assert af.is_ad(item1)[0] is True
        # Global keywords should still work
        item2 = _make_item(title="Sponsored content")
        assert af.is_ad(item2)[0] is True


# ===========================================================================
# TestAdFilterRealData (integration with real collector patterns)
# ===========================================================================
class TestAdFilterRealData:
    """Test with data patterns matching real collector output."""

    def test_real_reddit_patterns(self):
        """Simulate realistic Reddit items."""
        af = AdFilter(DEFAULT_CONFIG)
        items = [
            # Normal high-quality post
            _make_item(
                source="reddit",
                title="[R] Scaling Laws for Neural Language Models - new paper",
                url="https://www.reddit.com/r/MachineLearning/comments/abc123",
                score=500,
                extra={"upvote_ratio": 0.95, "num_comments": 120},
            ),
            # Potential soft ad with low ratio
            _make_item(
                source="reddit",
                title="Check out this amazing new AI platform!",
                url="https://www.reddit.com/r/artificial/comments/def456",
                score=60,
                extra={"upvote_ratio": 0.35, "num_comments": 3},
            ),
            # Normal discussion post
            _make_item(
                source="reddit",
                title="LocalLLaMA: quantization comparison GGUF vs GPTQ",
                url="https://www.reddit.com/r/LocalLLaMA/comments/ghi789",
                score=200,
                extra={"upvote_ratio": 0.88, "num_comments": 45},
            ),
        ]
        clean, blocked = af.filter(items)
        assert len(clean) == 2
        assert len(blocked) == 1
        assert blocked[0].title == "Check out this amazing new AI platform!"

    def test_real_youtube_patterns(self):
        """Simulate realistic YouTube items."""
        af = AdFilter(DEFAULT_CONFIG)
        items = [
            _make_item(
                source="youtube",
                title="Andrej Karpathy: Let's build GPT from scratch",
                url="https://youtube.com/watch?v=abc123",
                score=0,
            ),
            _make_item(
                source="youtube",
                title="This VPN is AMAZING #ad #sponsored",
                url="https://youtube.com/watch?v=def456",
                score=0,
            ),
            _make_item(
                source="youtube",
                title="LangChain Tutorial: Building RAG pipelines",
                url="https://youtube.com/watch?v=ghi789",
                score=0,
            ),
        ]
        clean, blocked = af.filter(items)
        assert len(clean) == 2
        assert len(blocked) == 1
        assert "#ad" in blocked[0].title.lower() or "#sponsored" in blocked[0].title.lower()
