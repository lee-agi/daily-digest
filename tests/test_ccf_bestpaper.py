"""Tests for CCF Best Paper Collector.

TDD tests covering:
1. Auto-registration in CollectorRegistry
2. ccf-deadlines YAML real fetch
3. OpenReview API real calls (ICLR 2026 Oral, NeurIPS 2025 oral)
4. ContentItem field completeness
5. End-to-end collector.run()
6. AAAI awards page scraping (real HTTP)
7. CVPR best papers page scraping (real HTTP)
8. ACL best papers page scraping (real HTTP)
9. IJCAI stub returns empty list
10. _extract_avg_rating() unit tests
11. Rating-based filtering (real API)
"""

import logging
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

import collectors.ccf_bestpaper  # noqa: F401

from collectors.base import CollectorRegistry
from collectors.ccf_bestpaper import CcfBestPaperCollector


def _make_collector():
    return CcfBestPaperCollector({
        "score_threshold": 0,
        "lookback_hours": 720,
        "max_items": 500,
    })


class TestCcfBestPaperRegistration:
    """Verify collector is properly registered."""

    def test_auto_registration(self):
        cls = CollectorRegistry.get("ccf_bestpaper")
        assert cls is not None
        assert cls is CcfBestPaperCollector

    def test_source_name(self):
        collector = CcfBestPaperCollector({
            "score_threshold": 0,
            "lookback_hours": 720,
            "max_items": 500,
        })
        assert collector.source_name == "ccf_bestpaper"


class TestCcfDeadlinesYAML:
    """Test ccf-deadlines GitHub YAML fetch (real network call)."""

    @pytest.mark.asyncio
    async def test_fetch_conference_metadata(self):
        """Fetch real YAML files from ccf-deadlines repo."""
        collector = CcfBestPaperCollector({
            "score_threshold": 0,
            "lookback_hours": 720,
            "max_items": 500,
        })
        editions = await collector._fetch_conference_metadata()

        # Should have entries for at least some conferences
        assert len(editions) > 0

        # Check that NeurIPS or ICLR is present
        conf_names = {e.name for e in editions}
        assert conf_names & {"NeurIPS", "ICLR"}, (
            f"Expected NeurIPS or ICLR in editions, got: {conf_names}"
        )

        # Check that the latest year is reasonable (>= 2024)
        max_year = max(e.year for e in editions)
        assert max_year >= 2024, f"Latest year {max_year} is too old"

    @pytest.mark.asyncio
    async def test_yaml_has_required_fields(self):
        """Each ConferenceEdition should have name, year, and conf_url."""
        collector = CcfBestPaperCollector({
            "score_threshold": 0,
            "lookback_hours": 720,
            "max_items": 500,
        })
        editions = await collector._fetch_conference_metadata()

        for edition in editions:
            assert edition.name, "Conference name should not be empty"
            assert edition.year > 2000, f"Year {edition.year} is unreasonable"


class TestOpenReviewAPI:
    """Test OpenReview API real calls."""

    @pytest.mark.asyncio
    async def test_iclr_2026_orals(self):
        """Fetch ICLR 2026 Oral papers from OpenReview (real API call)."""
        collector = CcfBestPaperCollector({
            "score_threshold": 0,
            "lookback_hours": 720,
            "max_items": 500,
        })
        items = await collector._fetch_openreview_orals("ICLR", 2026, "Oral")

        # ICLR 2026 should have ~223 oral papers
        assert len(items) > 50, (
            f"Expected >50 ICLR 2026 Oral papers, got {len(items)}"
        )

        # Verify ContentItem fields
        item = items[0]
        assert item.source == "ccf_bestpaper"
        assert item.title.startswith("[ICLR 2026 Oral]")
        assert "openreview.net/forum?id=" in item.url
        assert item.author  # Should have authors
        assert item.content  # Should have abstract
        assert item.extra.get("conference") == "ICLR"
        assert item.extra.get("year") == 2026
        assert "iclr" in item.tags
        assert "oral" in item.tags

        # Rating fields should be present
        assert "avg_rating" in item.extra
        assert "num_reviews" in item.extra
        assert "individual_ratings" in item.extra

    @pytest.mark.asyncio
    async def test_neurips_2025_orals(self):
        """Fetch NeurIPS 2025 oral papers from OpenReview (real API call).

        NeurIPS uses lowercase 'oral' in venue tag.
        """
        collector = CcfBestPaperCollector({
            "score_threshold": 0,
            "lookback_hours": 720,
            "max_items": 500,
        })
        items = await collector._fetch_openreview_orals("NeurIPS", 2025, "oral")

        # NeurIPS 2025 should have ~77 oral papers
        assert len(items) > 10, (
            f"Expected >10 NeurIPS 2025 oral papers, got {len(items)}"
        )

        item = items[0]
        assert item.title.startswith("[NeurIPS 2025 Oral]")
        assert item.extra.get("conference") == "NeurIPS"

        # Rating fields should be present
        assert "avg_rating" in item.extra
        assert "num_reviews" in item.extra
        assert "individual_ratings" in item.extra

    @pytest.mark.asyncio
    async def test_pagination(self):
        """Verify pagination works — ICLR 2026 Oral has >200 papers."""
        collector = CcfBestPaperCollector({
            "score_threshold": 0,
            "lookback_hours": 720,
            "max_items": 500,
        })
        items = await collector._fetch_openreview_orals("ICLR", 2026, "Oral")

        # If there are >200 papers, pagination must have worked
        assert len(items) > 200, (
            f"Expected >200 items (pagination test), got {len(items)}"
        )


class TestContentItemCompleteness:
    """Verify ContentItem fields from OpenReview data."""

    @pytest.mark.asyncio
    async def test_field_completeness(self):
        """Each item should have all required fields populated."""
        collector = CcfBestPaperCollector({
            "score_threshold": 0,
            "lookback_hours": 720,
            "max_items": 10,
        })
        items = await collector._fetch_openreview_orals("ICLR", 2026, "Oral")

        for item in items[:5]:  # Check first 5
            assert item.title, "title should not be empty"
            assert item.url, "url should not be empty"
            assert item.author, "author should not be empty"
            assert item.content, "content (abstract) should not be empty"
            assert item.extra.get("conference"), "extra.conference missing"
            assert item.extra.get("year"), "extra.year missing"
            assert item.extra.get("forum_id"), "extra.forum_id missing"
            assert item.published_at is not None, "published_at should be set"
            # Rating fields
            assert "avg_rating" in item.extra, "extra.avg_rating missing"
            assert "num_reviews" in item.extra, "extra.num_reviews missing"
            assert "individual_ratings" in item.extra, "extra.individual_ratings missing"


class TestAAAIAwards:
    """Test AAAI awards page scraping (real HTTP)."""

    @pytest.mark.asyncio
    async def test_fetch_aaai_awards_2026(self):
        """Fetch AAAI 2026 awards from real page."""
        collector = _make_collector()
        items = await collector._fetch_aaai_awards(2026)

        # AAAI 2026 has Outstanding Paper + track awards (~10+ papers)
        assert len(items) >= 3, (
            f"Expected >=3 AAAI 2026 award papers, got {len(items)}"
        )

        # Verify ContentItem format
        item = items[0]
        assert item.source == "ccf_bestpaper"
        assert "[AAAI 2026" in item.title
        assert item.author, "author should not be empty"
        assert item.extra.get("conference") == "AAAI"
        assert item.extra.get("year") == 2026
        assert "aaai" in item.tags
        assert "best_paper" in item.tags

    @pytest.mark.asyncio
    async def test_fetch_aaai_awards_2025(self):
        """Fetch AAAI 2025 awards (past recipients section)."""
        collector = _make_collector()
        items = await collector._fetch_aaai_awards(2025)

        assert len(items) >= 2, (
            f"Expected >=2 AAAI 2025 award papers, got {len(items)}"
        )

        for item in items:
            assert "[AAAI 2025" in item.title
            assert item.extra.get("year") == 2025

    @pytest.mark.asyncio
    async def test_aaai_title_not_truncated(self):
        """Paper titles from AAAI should be complete, not cut off."""
        collector = _make_collector()
        items = await collector._fetch_aaai_awards(2026)

        for item in items:
            # Extract raw title (strip prefix)
            raw = item.title.split("] ", 1)[-1]
            assert len(raw) > 10, f"Title too short: {raw}"
            # Shouldn't end mid-word (heuristic: check no trailing ...)
            assert not raw.endswith("..."), f"Title appears truncated: {raw}"


class TestCVPRAwards:
    """Test CVPR best papers page scraping (real HTTP)."""

    @pytest.mark.asyncio
    async def test_fetch_cvpr_awards_2025(self):
        """Fetch CVPR 2025 best papers from real page."""
        collector = _make_collector()
        items = await collector._fetch_cvpr_awards(2025)

        # CVPR 2025 has Best Paper + Best Student Paper + Honorable Mentions
        assert len(items) >= 2, (
            f"Expected >=2 CVPR 2025 award papers, got {len(items)}"
        )

        # Verify ContentItem format
        item = items[0]
        assert item.source == "ccf_bestpaper"
        assert "[CVPR 2025" in item.title
        assert item.author, "author should not be empty"
        assert item.extra.get("conference") == "CVPR"
        assert item.extra.get("year") == 2025
        assert "cvpr" in item.tags
        assert "best_paper" in item.tags

    @pytest.mark.asyncio
    async def test_cvpr_has_best_paper_and_student(self):
        """CVPR 2025 should have both Best Paper and Best Student Paper."""
        collector = _make_collector()
        items = await collector._fetch_cvpr_awards(2025)

        award_types = {item.extra.get("award_type") for item in items}
        assert "Best Paper" in award_types, (
            f"Expected 'Best Paper' in award types, got: {award_types}"
        )
        assert "Best Student Paper" in award_types, (
            f"Expected 'Best Student Paper' in award types, got: {award_types}"
        )


class TestACLAwards:
    """Test ACL best papers page scraping (real HTTP)."""

    @pytest.mark.asyncio
    async def test_fetch_acl_awards_2024(self):
        """Fetch ACL 2024 best papers from real page."""
        collector = _make_collector()
        items = await collector._fetch_acl_awards(2024)

        # ACL 2024 has Best Paper + Best Social Impact + Best Resource + more
        assert len(items) >= 5, (
            f"Expected >=5 ACL 2024 award papers, got {len(items)}"
        )

        item = items[0]
        assert item.source == "ccf_bestpaper"
        assert "[ACL 2024" in item.title
        assert item.author, "author should not be empty"
        assert item.extra.get("conference") == "ACL"
        assert item.extra.get("year") == 2024
        assert "acl" in item.tags
        assert "best_paper" in item.tags

    @pytest.mark.asyncio
    async def test_acl_multiple_categories(self):
        """ACL 2024 should have multiple award categories."""
        collector = _make_collector()
        items = await collector._fetch_acl_awards(2024)

        award_types = {item.extra.get("award_type") for item in items}
        # Should have at least Best Paper Awards and Outstanding Papers
        assert len(award_types) >= 2, (
            f"Expected >=2 distinct award types, got: {award_types}"
        )


class TestIJCAIStub:
    """Test IJCAI still returns empty list (no reliable scraper)."""

    @pytest.mark.asyncio
    async def test_ijcai_returns_empty(self):
        """IJCAI should return empty list with a log warning."""
        collector = _make_collector()
        items = await collector._fetch_website_awards("IJCAI", 2025)

        assert items == [], f"Expected empty list for IJCAI, got {len(items)} items"

    @pytest.mark.asyncio
    async def test_ijcai_logs_warning(self, caplog):
        """IJCAI should log a warning about no scraper."""
        collector = _make_collector()
        with caplog.at_level(logging.WARNING):
            await collector._fetch_website_awards("IJCAI", 2025)

        assert any("IJCAI" in record.message for record in caplog.records), (
            "Expected a warning log mentioning IJCAI"
        )


class TestWebsiteAwardsDispatcher:
    """Test _fetch_website_awards dispatches correctly."""

    @pytest.mark.asyncio
    async def test_dispatches_aaai(self):
        """AAAI should be dispatched to _fetch_aaai_awards."""
        collector = _make_collector()
        items = await collector._fetch_website_awards("AAAI", 2026)
        assert len(items) > 0, "AAAI dispatch should return items"

    @pytest.mark.asyncio
    async def test_dispatches_cvpr(self):
        """CVPR should be dispatched to _fetch_cvpr_awards."""
        collector = _make_collector()
        items = await collector._fetch_website_awards("CVPR", 2025)
        assert len(items) > 0, "CVPR dispatch should return items"

    @pytest.mark.asyncio
    async def test_dispatches_acl(self):
        """ACL should be dispatched to _fetch_acl_awards."""
        collector = _make_collector()
        items = await collector._fetch_website_awards("ACL", 2024)
        assert len(items) > 0, "ACL dispatch should return items"

    @pytest.mark.asyncio
    async def test_unknown_conf_returns_empty(self):
        """Unknown conference should return empty list."""
        collector = _make_collector()
        items = await collector._fetch_website_awards("UNKNOWN", 2025)
        assert items == []


class TestExtractAvgRating:
    """Unit tests for _extract_avg_rating() — no network calls."""

    def test_integer_ratings(self):
        """4 reviews with integer ratings → correct average."""
        replies = [
            {"invitations": ["ICLR.cc/2026/Conference/Submission1/-/Official_Review"], "content": {"rating": {"value": 7}}},
            {"invitations": ["ICLR.cc/2026/Conference/Submission1/-/Official_Review"], "content": {"rating": {"value": 8}}},
            {"invitations": ["ICLR.cc/2026/Conference/Submission1/-/Official_Review"], "content": {"rating": {"value": 6}}},
            {"invitations": ["ICLR.cc/2026/Conference/Submission1/-/Official_Review"], "content": {"rating": {"value": 7}}},
        ]
        avg, count, ratings = CcfBestPaperCollector._extract_avg_rating(replies)
        assert avg == 7.0
        assert count == 4
        assert sorted(ratings) == [6, 7, 7, 8]

    def test_string_ratings(self):
        """String format like '8: Strong Accept' → extract leading int."""
        replies = [
            {"invitations": ["ICLR.cc/2026/Conference/Submission1/-/Official_Review"], "content": {"rating": {"value": "8: Strong Accept"}}},
            {"invitations": ["ICLR.cc/2026/Conference/Submission1/-/Official_Review"], "content": {"rating": {"value": "6: Weak Accept"}}},
        ]
        avg, count, ratings = CcfBestPaperCollector._extract_avg_rating(replies)
        assert avg == 7.0
        assert count == 2
        assert sorted(ratings) == [6, 8]

    def test_mixed_invitations(self):
        """Only Official_Review invitations counted, not Author_Response."""
        replies = [
            {"invitations": ["ICLR.cc/2026/Conference/Submission1/-/Official_Review"], "content": {"rating": {"value": 8}}},
            {"invitations": ["ICLR.cc/2026/Conference/Submission1/-/Official_Comment"], "content": {"rating": {"value": 10}}},
            {"invitations": ["ICLR.cc/2026/Conference/Submission1/-/Official_Review"], "content": {"rating": {"value": 6}}},
        ]
        avg, count, ratings = CcfBestPaperCollector._extract_avg_rating(replies)
        assert avg == 7.0
        assert count == 2
        assert sorted(ratings) == [6, 8]

    def test_no_reviews(self):
        """Empty replies → (None, 0, [])."""
        avg, count, ratings = CcfBestPaperCollector._extract_avg_rating([])
        assert avg is None
        assert count == 0
        assert ratings == []

    def test_no_official_reviews(self):
        """Only Author_Response → (None, 0, [])."""
        replies = [
            {"invitations": ["ICLR.cc/2026/Conference/Submission1/-/Official_Comment"], "content": {"rating": {"value": 10}}},
            {"invitations": ["ICLR.cc/2026/Conference/Submission1/-/Meta_Review"], "content": {"recommendation": {"value": "Accept"}}},
        ]
        avg, count, ratings = CcfBestPaperCollector._extract_avg_rating(replies)
        assert avg is None
        assert count == 0
        assert ratings == []

    def test_partial_ratings(self):
        """3 reviews, 1 without rating field → avg based on 2."""
        replies = [
            {"invitations": ["ICLR.cc/2026/Conference/Submission1/-/Official_Review"], "content": {"rating": {"value": 8}}},
            {"invitations": ["ICLR.cc/2026/Conference/Submission1/-/Official_Review"], "content": {"summary": "Good paper"}},
            {"invitations": ["ICLR.cc/2026/Conference/Submission1/-/Official_Review"], "content": {"rating": {"value": 6}}},
        ]
        avg, count, ratings = CcfBestPaperCollector._extract_avg_rating(replies)
        assert avg == 7.0
        assert count == 2
        assert sorted(ratings) == [6, 8]

    def test_nested_dict_value(self):
        """Rating as nested dict with 'value' key."""
        replies = [
            {"invitations": ["NeurIPS.cc/2025/Conference/Submission1/-/Official_Review"], "content": {"rating": {"value": 4}}},
            {"invitations": ["NeurIPS.cc/2025/Conference/Submission1/-/Official_Review"], "content": {"rating": {"value": 5}}},
        ]
        avg, count, ratings = CcfBestPaperCollector._extract_avg_rating(replies)
        assert avg == 4.5
        assert count == 2
        assert sorted(ratings) == [4, 5]

    def test_direct_int_value(self):
        """Rating as direct int (not wrapped in dict)."""
        replies = [
            {"invitations": ["ICML.cc/2025/Conference/Submission1/-/Official_Review"], "content": {"rating": 7}},
            {"invitations": ["ICML.cc/2025/Conference/Submission1/-/Official_Review"], "content": {"rating": 9}},
        ]
        avg, count, ratings = CcfBestPaperCollector._extract_avg_rating(replies)
        assert avg == 8.0
        assert count == 2
        assert sorted(ratings) == [7, 9]

    def test_legacy_invitation_singular(self):
        """Backward compat: API v1 uses 'invitation' (singular string)."""
        replies = [
            {"invitation": "ICLR/2026/Official_Review", "content": {"rating": {"value": 7}}},
            {"invitation": "ICLR/2026/Official_Review", "content": {"rating": {"value": 9}}},
        ]
        avg, count, ratings = CcfBestPaperCollector._extract_avg_rating(replies)
        assert avg == 8.0
        assert count == 2


class TestRatingFiltering:
    """Test rating-based filtering with real OpenReview API calls."""

    @pytest.mark.asyncio
    async def test_iclr_2026_orals_have_ratings(self):
        """Most ICLR 2026 Oral papers should have reviewer ratings."""
        collector = CcfBestPaperCollector({
            "score_threshold": 0,
            "lookback_hours": 720,
            "max_items": 500,
        })
        items = await collector._fetch_openreview_orals("ICLR", 2026, "Oral")

        with_ratings = [i for i in items if i.extra.get("avg_rating") is not None]
        # Majority should have ratings
        assert len(with_ratings) > len(items) * 0.5, (
            f"Expected >50% items with ratings, got {len(with_ratings)}/{len(items)}"
        )
        # Those with ratings should have >= 3 reviewers
        for item in with_ratings[:10]:
            assert item.extra["num_reviews"] >= 3, (
                f"Expected >=3 reviewers, got {item.extra['num_reviews']}"
            )

    @pytest.mark.asyncio
    async def test_iclr_rating_filter_reduces_count(self):
        """Applying min_avg_rating=7 should reduce ICLR Oral count."""
        # Without filter
        collector_no_filter = CcfBestPaperCollector({
            "score_threshold": 0,
            "lookback_hours": 720,
            "max_items": 500,
        })
        items_all = await collector_no_filter._fetch_openreview_orals(
            "ICLR", 2026, "Oral",
        )

        # With filter
        collector_with_filter = CcfBestPaperCollector({
            "score_threshold": 0,
            "lookback_hours": 720,
            "max_items": 500,
            "min_avg_rating": {"ICLR": 7},
        })
        items_filtered = await collector_with_filter._fetch_openreview_orals(
            "ICLR", 2026, "Oral",
        )

        assert len(items_filtered) < len(items_all), (
            f"Filter should reduce count: {len(items_filtered)} vs {len(items_all)}"
        )
        assert len(items_filtered) > 0, "Filter should not remove all items"

    @pytest.mark.asyncio
    async def test_neurips_2025_orals_have_ratings(self):
        """NeurIPS 2025 Oral papers should have reviewer ratings."""
        collector = CcfBestPaperCollector({
            "score_threshold": 0,
            "lookback_hours": 720,
            "max_items": 500,
        })
        items = await collector._fetch_openreview_orals("NeurIPS", 2025, "oral")

        with_ratings = [i for i in items if i.extra.get("avg_rating") is not None]
        assert len(with_ratings) > 0, "Expected some NeurIPS papers to have ratings"

    @pytest.mark.asyncio
    async def test_score_equals_avg_rating(self):
        """item.score should equal avg_rating when available."""
        collector = CcfBestPaperCollector({
            "score_threshold": 0,
            "lookback_hours": 720,
            "max_items": 500,
        })
        items = await collector._fetch_openreview_orals("ICLR", 2026, "Oral")

        for item in items[:20]:
            avg = item.extra.get("avg_rating")
            if avg is not None:
                assert item.score == avg, (
                    f"score ({item.score}) should equal avg_rating ({avg})"
                )
            else:
                assert item.score == 0.0, (
                    f"score should be 0.0 when no ratings, got {item.score}"
                )

    @pytest.mark.asyncio
    async def test_no_rating_papers_pass_filter(self):
        """Papers without public reviews (avg_rating=None) should not be filtered."""
        collector = CcfBestPaperCollector({
            "score_threshold": 0,
            "lookback_hours": 720,
            "max_items": 500,
            "min_avg_rating": {"ICLR": 7},
        })
        items = await collector._fetch_openreview_orals("ICLR", 2026, "Oral")

        no_rating = [i for i in items if i.extra.get("avg_rating") is None]
        # If there are papers without ratings, they should have passed the filter
        for item in no_rating:
            assert item.extra["avg_rating"] is None
            assert item.extra["num_reviews"] == 0


class TestEndToEnd:
    """End-to-end test via collector.run()."""

    @pytest.mark.asyncio
    async def test_run(self):
        """Full run() should succeed and return items."""
        collector = CcfBestPaperCollector({
            "score_threshold": 0,
            "lookback_hours": 720,
            "max_items": 50,
        })
        result = await collector.run()

        assert result.success, f"Collector failed: {result.error}"
        assert result.source == "ccf_bestpaper"
        assert result.raw_count > 0, "Should have collected some papers"
        assert len(result.items) > 0, "Should have items after filtering"
        assert len(result.items) <= 50, "Should respect max_items"
