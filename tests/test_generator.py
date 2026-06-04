"""Tests for report/generator.py — streaming LLM call with retry logic."""

from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest


# ---------------------------------------------------------------------------
# Helpers: Fake streaming response
# ---------------------------------------------------------------------------

FAKE_SSE_LINES = [
    'data: {"type":"response.output_text.delta","delta":"# Digest"}',
    'data: {"type":"response.output_text.delta","delta":"\\n\\nSome content."}',
    "data: [DONE]",
]


class _FakeStreamResponse:
    """Mock an httpx streaming response that yields SSE lines."""

    def __init__(self, lines: list[str] | None = None, status_code: int = 200):
        self.status_code = status_code
        self._lines = lines if lines is not None else list(FAKE_SSE_LINES)

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise httpx.HTTPStatusError(
                f"HTTP {self.status_code}",
                request=MagicMock(),
                response=MagicMock(status_code=self.status_code),
            )

    async def aiter_lines(self):
        for line in self._lines:
            yield line


class _FakeStreamCM:
    """Async context manager wrapping a streaming response."""

    def __init__(self, response: _FakeStreamResponse):
        self._resp = response

    async def __aenter__(self):
        return self._resp

    async def __aexit__(self, *args):
        return False


class _FakeStreamCMError:
    """Async context manager that raises on __aenter__."""

    def __init__(self, exc: BaseException):
        self._exc = exc

    async def __aenter__(self):
        raise self._exc

    async def __aexit__(self, *args):
        return False


def _make_streaming_client(
    lines: list[str] | None = None,
    captured_payloads: list[dict] | None = None,
):
    """Build mock AsyncClient with streaming support."""
    mock = AsyncMock()
    mock.__aenter__ = AsyncMock(return_value=mock)
    mock.__aexit__ = AsyncMock(return_value=False)

    def _stream(*args, **kwargs):
        if captured_payloads is not None:
            captured_payloads.append(kwargs.get("json", {}))
        return _FakeStreamCM(_FakeStreamResponse(lines))

    mock.stream = MagicMock(side_effect=_stream)
    return mock


def _clear_deployment_envs(monkeypatch: pytest.MonkeyPatch) -> None:
    for var in (
        "AZURE_OPENAI_SUMMARY_DEPLOYMENT",
        "AZURE_OPENAI_DEPLOYMENT",
        "AZURE_OPENAI_DEPLOYMENT_NAME",
    ):
        monkeypatch.delenv(var, raising=False)


# ---------------------------------------------------------------------------
# generate_digest_report integration
# ---------------------------------------------------------------------------


class TestGenerateDigestReport:
    @pytest.mark.asyncio
    async def test_falls_back_when_llm_markdown_is_truncated(self) -> None:
        from report.generator import generate_digest_report
        from schema import ContentItem, SourceType

        item = ContentItem(
            source="kindle_books",
            source_type=SourceType.HTTP_SCRAPE,
            title="Example Book",
            url="https://example.com/book",
            author="",
            content="Example content",
            score=123.0,
            tags=["book", "books_reading"],
            extra={
                "ranking_base_signal": "ratings_count",
                "ranking_base_score": 140442.0,
                "ranking_multiplier": 1.0,
                "ranking_factors": {"rank_bonus": 990.0},
                "ratings_count": 140442,
                "star_rating": 4.4,
                "rank": 1,
            },
        )
        config = {"summary": {"categories": ["Books & Reading"], "top_headlines": 3}}

        with patch("report.generator._call_llm", AsyncMock(return_value=("## Summary\n\n- **broken", False))):
            report = await generate_digest_report([item], config, "2026-04-26")

        assert "<!-- RAW_FALLBACK_REPORT -->" not in report.full_markdown
        assert "## 平台统计" in report.full_markdown
        assert "Platform Statistics" not in report.full_markdown
        assert "自动摘要未通过质量校验" in report.full_markdown
        assert "LLM Unavailable" not in report.full_markdown
        assert "Example Book" in report.full_markdown

    def test_inserts_industry_events_as_second_body_section(self) -> None:
        from report.generator import _build_industry_events_section, _insert_industry_events_section

        config = {
            "industry_events": {
                "enabled": True,
                "horizon_days": 30,
                "recent_days": 30,
                "weekly_reminder_weekday": 6,
                "events": [
                    {
                        "title": "Google I/O 2026",
                        "organizer": "Google",
                        "start_date": "2026-05-19",
                        "end_date": "2026-05-20",
                        "url": "https://io.google/2026/",
                        "focus": ["Gemini", "agent 工具"],
                    },
                    {
                        "title": "Google Cloud Next 2026",
                        "organizer": "Google Cloud",
                        "start_date": "2026-04-22",
                        "url": "https://www.googlecloudnext.com/",
                        "note": "需要补课",
                    },
                    {
                        "title": "OpenAI DevDay 2026",
                        "organizer": "OpenAI",
                        "url": "https://openai.com/devday/",
                        "focus": ["API", "agent 平台"],
                    },
                ],
            }
        }
        section = _build_industry_events_section(config, "2026-05-03")
        body = (
            "## 1. Today's Top 10 Headlines\n\n"
            "1. [Example](<https://example.com>)\n\n"
            "## 2. 主题与分类精选\n\n"
            "### 分类补充\n"
        )

        result = _insert_industry_events_section(body, section)

        assert "## 2. 最近要发生的重要产业事件" in result
        assert "口径：未来" not in result
        assert "Google I/O 2026" in result
        assert "Google Cloud Next 2026" in result
        assert "近期已发生 / 需要补课" in result
        assert "OpenAI DevDay 2026" in result
        assert "## 3. 主题与分类精选" in result
        assert result.index("## 1. Today's Top") < result.index("## 2. 最近要发生") < result.index("## 3. 主题")

    def test_render_digest_from_event_ledger_has_no_raw_dump_marker(self) -> None:
        from report.generator import render_digest_from_event_ledger

        body = render_digest_from_event_ledger({
            "events": [
                {
                    "rank": 1,
                    "topic": "Developer Tools",
                    "selected": True,
                    "event_score": 88.0,
                    "main_item": {
                        "title": "Claude Code usage limits increase",
                        "url": "https://example.com/claude-code",
                        "source": "anthropic",
                        "source_tier": "T1",
                    },
                    "supporting_sources": [
                        {"title": "X thread", "url": "https://x.com/example/status/1", "source": "x_twitter"}
                    ],
                }
            ]
        })

        assert "## 1. Today's Top Headlines" in body
        assert "Claude Code usage limits increase" in body
        assert "旁证" in body
        assert "<!-- RAW_FALLBACK_REPORT -->" not in body

    def test_render_digest_from_event_ledger_does_not_repeat_top_headlines_in_topic_section(self) -> None:
        from report.generator import render_digest_from_event_ledger

        body = render_digest_from_event_ledger({
            "events": [
                {
                    "event_id": "top-event",
                    "rank": 1,
                    "topic": "Developer Tools",
                    "selected": True,
                    "event_score": 100.0,
                    "main_item": {
                        "title": "Top release should appear only once",
                        "url": "https://example.com/top-release",
                        "source": "github",
                        "source_tier": "T1.5",
                    },
                },
                {
                    "event_id": "supplement-event",
                    "rank": 2,
                    "topic": "Developer Tools",
                    "selected": False,
                    "event_score": 80.0,
                    "main_item": {
                        "title": "Supplementary tool signal",
                        "url": "https://example.com/supplement",
                        "source": "hackernews",
                        "source_tier": "T2",
                    },
                },
            ]
        })

        topic_section = body.split("## 2. 主题与分类精选", 1)[1]
        assert "Top release should appear only once" in body
        assert "Top release should appear only once" not in topic_section
        assert "Supplementary tool signal" in topic_section


# ---------------------------------------------------------------------------
# SSE stream parsing
# ---------------------------------------------------------------------------


class TestParseSSEStream:
    """Test _parse_sse_stream extracts text deltas correctly."""

    @pytest.mark.asyncio
    async def test_basic_delta_extraction(self) -> None:
        from report.generator import _parse_sse_stream

        resp = _FakeStreamResponse(FAKE_SSE_LINES)
        result = await _parse_sse_stream(resp)
        assert "# Digest" in result
        assert "Some content." in result

    @pytest.mark.asyncio
    async def test_ignores_non_delta_events(self) -> None:
        from report.generator import _parse_sse_stream

        lines = [
            'data: {"type":"response.created","response":{"id":"r1"}}',
            'data: {"type":"response.output_item.added","output_index":0}',
            'data: {"type":"response.output_text.delta","delta":"hello"}',
            'data: {"type":"response.completed","response":{}}',
            "data: [DONE]",
        ]
        resp = _FakeStreamResponse(lines)
        result = await _parse_sse_stream(resp)
        assert result == "hello"

    @pytest.mark.asyncio
    async def test_empty_stream(self) -> None:
        from report.generator import _parse_sse_stream

        resp = _FakeStreamResponse(["data: [DONE]"])
        result = await _parse_sse_stream(resp)
        assert result == ""

    @pytest.mark.asyncio
    async def test_malformed_json_skipped(self) -> None:
        from report.generator import _parse_sse_stream

        lines = [
            "data: {not valid json}",
            'data: {"type":"response.output_text.delta","delta":"ok"}',
            "data: [DONE]",
        ]
        resp = _FakeStreamResponse(lines)
        result = await _parse_sse_stream(resp)
        assert result == "ok"

    @pytest.mark.asyncio
    async def test_non_data_lines_ignored(self) -> None:
        from report.generator import _parse_sse_stream

        lines = [
            ": comment",
            "",
            'data: {"type":"response.output_text.delta","delta":"ok"}',
            "data: [DONE]",
        ]
        resp = _FakeStreamResponse(lines)
        result = await _parse_sse_stream(resp)
        assert result == "ok"

    @pytest.mark.asyncio
    async def test_partial_result_on_disconnect(self) -> None:
        """If stream disconnects after substantial content, return partial result."""
        from report.generator import _parse_sse_stream

        long_text = "x" * 600  # >= 500 char threshold

        class _DisconnectingResponse:
            async def aiter_lines(self):
                yield f'data: {{"type":"response.output_text.delta","delta":"{long_text}"}}'
                raise httpx.RemoteProtocolError("incomplete chunked read")

        result = await _parse_sse_stream(_DisconnectingResponse())
        assert len(result) >= 500
        assert result == long_text

    @pytest.mark.asyncio
    async def test_reraise_on_disconnect_with_little_content(self) -> None:
        """If stream disconnects with < 500 chars, re-raise for retry."""
        from report.generator import _parse_sse_stream

        class _DisconnectingResponse:
            async def aiter_lines(self):
                yield 'data: {"type":"response.output_text.delta","delta":"tiny"}'
                raise httpx.RemoteProtocolError("incomplete chunked read")

        with pytest.raises(httpx.RemoteProtocolError):
            await _parse_sse_stream(_DisconnectingResponse())


# ---------------------------------------------------------------------------
# Streaming payload: stream=True must be set
# ---------------------------------------------------------------------------


class TestTruncationGuard:
    """Detect obviously truncated reports before save/push."""

    def test_truncated_when_markdown_is_visibly_incomplete(self) -> None:
        from report.generator import is_truncated_report

        text = "# Daily Digest - 2026-03-16\n\n## 1. Top\n- **The File"
        assert is_truncated_report(text) is True

    def test_truncated_when_unbalanced_bold(self) -> None:
        from report.generator import is_truncated_report

        text = (
            "# Daily Digest - 2026-03-16\n\n"
            "## Section\n- **broken\n\n"
        )
        assert is_truncated_report(text) is True

    def test_not_truncated_for_complete_report(self) -> None:
        from report.generator import is_truncated_report

        text = (
            "# Daily Digest - 2026-03-16\n\n"
            "## Section\n- **complete item**\n"
        )
        assert is_truncated_report(text) is False

    def test_not_truncated_when_report_ends_with_closed_bold_text(self) -> None:
        from report.generator import is_truncated_report

        text = (
            "# Daily Digest - 2026-03-16\n\n"
            "## Section\n- normal line\n\n"
            "---\n\n"
            "如果你愿意，我还可以继续输出一版：\n"
            "1. **更像投资人看的 executive briefing**"
        )
        assert is_truncated_report(text) is False


class TestReportShapeAndLinks:
    def test_enforces_new_report_structure(self) -> None:
        from report.generator import _enforce_report_structure

        text = (
            "# Daily Digest\n\n"
            "## 重要性与交叉验证\nremove me\n### 重要候选信息\nremove me too\n"
            "## 2. 跨平台主题分析\nkeep theme\n"
            "## 3. 分类详情\nkeep category\n"
            "## Platform Statistics\nremove stats\n"
            "## 平台统计\nkeep Chinese stats\n"
            "## Tail\nkeep tail\n"
        )
        out = _enforce_report_structure(text)
        assert "重要性与交叉验证" not in out
        assert "重要候选信息" not in out
        assert "Platform Statistics" not in out
        assert "## 平台统计" in out
        assert "keep Chinese stats" in out
        assert "## 2. 主题与分类精选" in out
        assert "### 分类补充" in out
        assert "keep tail" in out

    def test_dedupes_chinese_platform_statistics(self) -> None:
        from report.generator import _dedupe_platform_statistics_sections

        text = (
            "# Daily Digest\n\n"
            "## 平台统计\nfront stats\n"
            "## 1. 摘要\nbody\n"
            "## 平台统计\nmodel duplicate stats\n"
            "## Tail\nkeep tail\n"
        )
        out = _dedupe_platform_statistics_sections(text)
        assert out.count("## 平台统计") == 1
        assert "front stats" in out
        assert "model duplicate stats" not in out
        assert "## Tail" in out

    @pytest.mark.asyncio
    async def test_sanitizes_links_and_removes_dead_links(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from report import generator

        async def fake_validate(urls, config):
            return {url: "dead.example" not in url for url in urls}

        monkeypatch.setattr(generator, "_validate_urls", fake_validate)
        text = "Good [ok](https://example.com/a) bad [dead](https://dead.example/404) raw https://example.com/raw"
        out = await generator._sanitize_report_links(text, {"summary": {"link_validation": {"enabled": True}}})
        assert "[ok](https://example.com/a)" in out
        assert "[link](https://example.com/raw)" in out
        assert "https://dead.example/404" not in out
        assert "dead" in out

    @pytest.mark.asyncio
    async def test_validate_urls_get_fallback_when_head_false_negative(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from report import generator

        url = "https://vllm.ai/blog/2026-05-26-eagle-3-1"
        calls: list[tuple[str, str]] = []
        generator._URL_OPEN_CACHE.clear()

        class FakeAsyncClient:
            def __init__(self, *args, **kwargs):
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                return False

            async def head(self, checked_url: str, *, follow_redirects: bool):
                calls.append(("HEAD", checked_url))
                return SimpleNamespace(status_code=404)

            async def get(self, checked_url: str, *, follow_redirects: bool, headers: dict):
                calls.append(("GET", checked_url))
                assert headers == {"Range": "bytes=0-0"}
                return SimpleNamespace(status_code=200)

        monkeypatch.setattr(httpx, "AsyncClient", FakeAsyncClient)

        result = await generator._validate_urls(
            [url],
            {"summary": {"link_validation": {"enabled": True, "timeout_seconds": 1, "concurrency": 1}}},
        )

        assert result[url] is True
        assert calls == [("HEAD", url), ("GET", url)]

    @pytest.mark.asyncio
    @pytest.mark.parametrize("status_code", [401, 403, 429])
    async def test_validate_urls_keeps_auth_and_rate_limit_head_status_without_get(
        self, monkeypatch: pytest.MonkeyPatch, status_code: int
    ) -> None:
        from report import generator

        url = f"https://x.com/example/status/{status_code}"
        calls: list[tuple[str, str]] = []
        generator._URL_OPEN_CACHE.clear()

        class FakeAsyncClient:
            def __init__(self, *args, **kwargs):
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                return False

            async def head(self, checked_url: str, *, follow_redirects: bool):
                calls.append(("HEAD", checked_url))
                return SimpleNamespace(status_code=status_code)

            async def get(self, checked_url: str, *, follow_redirects: bool, headers: dict):
                raise AssertionError("GET should not run for retained anti-bot/auth statuses")

        monkeypatch.setattr(httpx, "AsyncClient", FakeAsyncClient)

        result = await generator._validate_urls(
            [url],
            {"summary": {"link_validation": {"enabled": True, "timeout_seconds": 1, "concurrency": 1}}},
        )

        assert result[url] is True
        assert calls == [("HEAD", url)]


class TestStreamingPayload:
    """Payload must include stream=True."""

    @pytest.mark.asyncio
    async def test_stream_true_in_payload(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("AZURE_OPENAI_API_KEY", "test-key")
        monkeypatch.setenv("AZURE_OPENAI_BASE_URL", "https://fake.openai.azure.com/openai")
        _clear_deployment_envs(monkeypatch)

        captured: list[dict] = []
        mock_client = _make_streaming_client(captured_payloads=captured)

        with (
            patch("report.generator._load_openclaw_azure_config", return_value={"base_url": "", "model": "m"}),
            patch("httpx.AsyncClient", return_value=mock_client),
        ):
            from report.generator import _call_llm

            await _call_llm("prompt", {})

        assert len(captured) == 1
        assert captured[0]["stream"] is True


# ---------------------------------------------------------------------------
# Fix 1: openclaw_cfg always loaded even when base_url env var is set
# ---------------------------------------------------------------------------


class TestHardcodedModel:
    """Model is always hard-coded to llab-gpt-5.4, ignoring env vars."""

    @pytest.mark.asyncio
    async def test_model_ignores_env_vars(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Env vars like AZURE_OPENAI_SUMMARY_DEPLOYMENT must NOT override the model."""
        monkeypatch.setenv("AZURE_OPENAI_API_KEY", "test-key")
        monkeypatch.setenv("AZURE_OPENAI_BASE_URL", "https://env-base.openai.azure.com/openai")
        monkeypatch.setenv("AZURE_OPENAI_SUMMARY_DEPLOYMENT", "llab-gpt-5-pro")
        monkeypatch.setenv("AZURE_OPENAI_DEPLOYMENT", "some-other-model")

        captured: list[dict] = []
        mock_client = _make_streaming_client(captured_payloads=captured)

        with (
            patch("report.generator._load_openclaw_azure_config", return_value={"base_url": "", "model": "from-json"}),
            patch("httpx.AsyncClient", return_value=mock_client),
        ):
            from report.generator import _call_llm

            await _call_llm("test", {})

        assert captured[0]["model"] == "llab-gpt-5.4"

    @pytest.mark.asyncio
    async def test_model_always_hardcoded(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Even with no env vars or openclaw config, model is llab-gpt-5.4."""
        monkeypatch.setenv("AZURE_OPENAI_API_KEY", "test-key")
        monkeypatch.setenv("AZURE_OPENAI_BASE_URL", "https://env-base.openai.azure.com/openai")
        _clear_deployment_envs(monkeypatch)

        captured: list[dict] = []
        mock_client = _make_streaming_client(captured_payloads=captured)

        with (
            patch("report.generator._load_openclaw_azure_config", return_value={"base_url": "", "model": ""}),
            patch("httpx.AsyncClient", return_value=mock_client),
        ):
            from report.generator import _call_llm

            await _call_llm("test", {})

        assert captured[0]["model"] == "llab-gpt-5.4"


# ---------------------------------------------------------------------------
# Read timeout = 30s (TTFT protection)
# ---------------------------------------------------------------------------


class TestReadTimeout:
    @pytest.mark.asyncio
    async def test_read_timeout_is_600s(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Read timeout must be 600s for SSE streaming with long inference."""
        monkeypatch.setenv("AZURE_OPENAI_API_KEY", "test-key")
        monkeypatch.setenv("AZURE_OPENAI_BASE_URL", "https://env-base.openai.azure.com/openai")

        captured_timeouts: list = []
        original_async_client = __import__("httpx").AsyncClient

        def _capture_client(*args, **kwargs):
            captured_timeouts.append(kwargs.get("timeout"))
            return _make_streaming_client()

        with (
            patch("report.generator._load_openclaw_azure_config", return_value={"base_url": "", "model": ""}),
            patch("httpx.AsyncClient", side_effect=_capture_client),
        ):
            from report.generator import _call_llm

            await _call_llm("test", {})

        assert len(captured_timeouts) >= 1
        t = captured_timeouts[0]
        assert t.read == 600.0


# ---------------------------------------------------------------------------
# Fix 3: INFO log before request
# ---------------------------------------------------------------------------


class TestPreRequestLogging:
    @pytest.mark.asyncio
    async def test_info_log_emitted(
        self, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
    ) -> None:
        import logging

        monkeypatch.setenv("AZURE_OPENAI_API_KEY", "test-key")
        monkeypatch.setenv("AZURE_OPENAI_BASE_URL", "https://env-base.openai.azure.com/openai")

        mock_client = _make_streaming_client()

        with (
            patch("report.generator._load_openclaw_azure_config", return_value={"base_url": "", "model": "m"}),
            patch("httpx.AsyncClient", return_value=mock_client),
            caplog.at_level(logging.INFO, logger="report.generator"),
        ):
            from report.generator import _call_llm

            await _call_llm("hello world", {})

        assert any(
            "Calling LLM" in r.message and "model=" in r.message
            for r in caplog.records
        )


# ---------------------------------------------------------------------------
# Fix 4: Retry on TransportError (RemoteProtocolError, ReadTimeout, etc.)
# ---------------------------------------------------------------------------


class TestRetryOnTransportError:
    """Retry up to max_attempts on TransportError per model, then try next model."""

    @pytest.mark.asyncio
    async def test_succeeds_on_second_attempt(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("AZURE_OPENAI_API_KEY", "test-key")
        monkeypatch.setenv("AZURE_OPENAI_BASE_URL", "https://env-base.openai.azure.com/openai")

        call_count = 0

        def _stream(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return _FakeStreamCMError(
                    httpx.RemoteProtocolError("Server disconnected")
                )
            return _FakeStreamCM(_FakeStreamResponse())

        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.stream = MagicMock(side_effect=_stream)

        with (
            patch("report.generator._load_openclaw_azure_config", return_value={"base_url": "", "model": "m"}),
            patch("httpx.AsyncClient", return_value=mock_client),
            patch("report.generator.asyncio.sleep", new_callable=AsyncMock),
        ):
            from report.generator import _call_llm

            result, used_fallback = await _call_llm("test", {})

        assert call_count == 2
        assert used_fallback is False
        assert "Digest" in result

    @pytest.mark.asyncio
    async def test_fallback_to_mini_model(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Primary model (codex) fails all 3 attempts → mini model succeeds.

        With progressive degradation: codex attempts 1-2 use streaming,
        attempt 3 uses non-streaming. All fail. Then mini attempt 1 (streaming) succeeds.
        """
        monkeypatch.setenv("AZURE_OPENAI_API_KEY", "test-key")
        monkeypatch.setenv("AZURE_OPENAI_BASE_URL", "https://env-base.openai.azure.com/openai")

        stream_call_count = 0
        post_call_count = 0
        captured_models: list[str] = []

        def _stream(*args, **kwargs):
            nonlocal stream_call_count
            stream_call_count += 1
            payload = kwargs.get("json", {})
            captured_models.append(payload.get("model", ""))
            # codex streaming attempts fail; mini streaming succeeds
            if payload.get("model") == "llab-gpt-5.4":
                return _FakeStreamCMError(
                    httpx.RemoteProtocolError("Server disconnected")
                )
            return _FakeStreamCM(_FakeStreamResponse())

        async def _post(*args, **kwargs):
            nonlocal post_call_count
            post_call_count += 1
            payload = kwargs.get("json", {})
            captured_models.append(payload.get("model", ""))
            # codex non-streaming attempt also fails
            raise httpx.RemoteProtocolError("Server disconnected")

        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.stream = MagicMock(side_effect=_stream)
        mock_client.post = AsyncMock(side_effect=_post)

        with (
            patch("report.generator._load_openclaw_azure_config", return_value={"base_url": "", "model": "m"}),
            patch("httpx.AsyncClient", return_value=mock_client),
            patch("report.generator.asyncio.sleep", new_callable=AsyncMock),
        ):
            from report.generator import _call_llm

            result, used_fallback = await _call_llm("test", {})

        total_calls = stream_call_count + post_call_count
        # codex: 2 streaming + 1 non-streaming = 3, then mini: 1 streaming = 4 total
        assert total_calls == 4
        assert used_fallback is False
        assert "Digest" in result
        # First 2 are codex streaming, 3rd is codex non-streaming, 4th is mini streaming
        assert captured_models[:3] == ["llab-gpt-5.4"] * 3
        assert captured_models[3] == "llab-gpt-5-mini"

    @pytest.mark.asyncio
    async def test_quality_validator_rejects_bad_primary_and_falls_back_to_mini(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """A 200 OK response can still be a bad summary; reject it and try mini."""
        monkeypatch.setenv("AZURE_OPENAI_API_KEY", "test-key")
        monkeypatch.setenv("AZURE_OPENAI_BASE_URL", "https://env-base.openai.azure.com/openai")

        captured_models: list[str] = []

        def lines_for(text: str) -> list[str]:
            return [
                "data: " + json.dumps({"type": "response.output_text.delta", "delta": text}),
                "data: [DONE]",
            ]

        def _stream(*args, **kwargs):
            payload = kwargs.get("json", {})
            model = payload.get("model", "")
            captured_models.append(model)
            if model == "llab-gpt-5.4":
                return _FakeStreamCM(_FakeStreamResponse(lines_for("bad-but-200")))
            return _FakeStreamCM(_FakeStreamResponse(lines_for("good-mini-summary")))

        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.stream = MagicMock(side_effect=_stream)

        with (
            patch("report.generator._load_openclaw_azure_config", return_value={"base_url": "", "model": "m"}),
            patch("httpx.AsyncClient", return_value=mock_client),
        ):
            from report.generator import _call_llm

            result, used_fallback = await _call_llm(
                "test",
                {},
                validator=lambda text: (text == "good-mini-summary", "quality_failed"),
            )

        assert captured_models == ["llab-gpt-5.4", "llab-gpt-5-mini"]
        assert used_fallback is False
        assert result == "good-mini-summary"

    @pytest.mark.asyncio
    async def test_fallback_to_template_when_all_models_fail(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """All models fail → fallback report."""
        monkeypatch.setenv("AZURE_OPENAI_API_KEY", "test-key")
        monkeypatch.setenv("AZURE_OPENAI_BASE_URL", "https://env-base.openai.azure.com/openai")

        call_count = 0

        def _stream(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            return _FakeStreamCMError(
                httpx.RemoteProtocolError("Server disconnected")
            )

        async def _post(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            raise httpx.RemoteProtocolError("Server disconnected")

        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.stream = MagicMock(side_effect=_stream)
        mock_client.post = AsyncMock(side_effect=_post)

        with (
            patch("report.generator._load_openclaw_azure_config", return_value={"base_url": "", "model": "m"}),
            patch("httpx.AsyncClient", return_value=mock_client),
            patch("report.generator.asyncio.sleep", new_callable=AsyncMock),
        ):
            from report.generator import _call_llm

            result, used_fallback = await _call_llm("test", {})

        # codex: 2 streaming + 1 non-streaming = 3, mini: 1 streaming + 1 non-streaming = 2 → 5 total
        assert call_count == 5
        assert used_fallback is True
        assert "LLM" in result and "unavailable" in result.lower()

    @pytest.mark.asyncio
    async def test_retry_on_read_timeout(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """ReadTimeout (a TransportError) should also be retried."""
        monkeypatch.setenv("AZURE_OPENAI_API_KEY", "test-key")
        monkeypatch.setenv("AZURE_OPENAI_BASE_URL", "https://env-base.openai.azure.com/openai")

        call_count = 0

        def _stream(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return _FakeStreamCMError(httpx.ReadTimeout("Read timed out"))
            return _FakeStreamCM(_FakeStreamResponse())

        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.stream = MagicMock(side_effect=_stream)

        with (
            patch("report.generator._load_openclaw_azure_config", return_value={"base_url": "", "model": "m"}),
            patch("httpx.AsyncClient", return_value=mock_client),
            patch("report.generator.asyncio.sleep", new_callable=AsyncMock),
        ):
            from report.generator import _call_llm

            result, used_fallback = await _call_llm("test", {})

        assert call_count == 2
        assert used_fallback is False
        assert "Digest" in result

    @pytest.mark.asyncio
    async def test_no_retry_on_http_status_error(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """HTTPStatusError (4xx) should NOT be retried — moves to next model."""
        monkeypatch.setenv("AZURE_OPENAI_API_KEY", "test-key")
        monkeypatch.setenv("AZURE_OPENAI_BASE_URL", "https://env-base.openai.azure.com/openai")

        call_count = 0
        captured_models: list[str] = []

        def _stream(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            payload = kwargs.get("json", {})
            captured_models.append(payload.get("model", ""))
            if payload.get("model") == "llab-gpt-5.4":
                return _FakeStreamCM(_FakeStreamResponse(status_code=401))
            return _FakeStreamCM(_FakeStreamResponse())

        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.stream = MagicMock(side_effect=_stream)

        with (
            patch("report.generator._load_openclaw_azure_config", return_value={"base_url": "", "model": "m"}),
            patch("httpx.AsyncClient", return_value=mock_client),
        ):
            from report.generator import _call_llm

            result, used_fallback = await _call_llm("test", {})

        # codex: 1 attempt (HTTP error, no retry) → mini: 1 attempt (success)
        assert call_count == 2
        assert used_fallback is False
        assert captured_models == ["llab-gpt-5.4", "llab-gpt-5-mini"]
        assert "Digest" in result

    @pytest.mark.asyncio
    async def test_exponential_backoff(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Sleep duration should increase: 3s, 6s for primary model."""
        monkeypatch.setenv("AZURE_OPENAI_API_KEY", "test-key")
        monkeypatch.setenv("AZURE_OPENAI_BASE_URL", "https://env-base.openai.azure.com/openai")

        def _stream(*args, **kwargs):
            return _FakeStreamCMError(
                httpx.RemoteProtocolError("Server disconnected")
            )

        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.stream = MagicMock(side_effect=_stream)
        mock_client.post = AsyncMock(side_effect=httpx.RemoteProtocolError("disconnect"))

        sleep_durations: list[float] = []
        original_sleep = AsyncMock(side_effect=lambda d: sleep_durations.append(d))

        with (
            patch("report.generator._load_openclaw_azure_config", return_value={"base_url": "", "model": "m"}),
            patch("httpx.AsyncClient", return_value=mock_client),
            patch("report.generator.asyncio.sleep", original_sleep),
        ):
            from report.generator import _call_llm

            await _call_llm("test", {})

        # codex: 3 attempts → sleep 3s, 6s; mini: 2 attempts → sleep 3s
        assert sleep_durations == [3, 6, 3]


# ---------------------------------------------------------------------------
# LLM Refusal Detection
# ---------------------------------------------------------------------------


class TestIsRefusal:
    """Test _is_refusal() function."""

    def test_short_refusal_detected(self) -> None:
        from report.generator import _is_refusal

        assert _is_refusal("I'm sorry, I cannot assist with this request.") is True
        assert _is_refusal("I apologize, but I'm unable to generate this content.") is True
        assert _is_refusal("As an AI, I cannot help with that.") is True

    def test_long_text_not_flagged(self) -> None:
        """Real digest reports (>500 chars) should not be flagged as refusal."""
        from report.generator import _is_refusal

        long_text = "# Daily Digest\n\n" + "Some real content. " * 50
        assert len(long_text) > 500
        assert _is_refusal(long_text) is False

    def test_normal_short_text_not_flagged(self) -> None:
        from report.generator import _is_refusal

        assert _is_refusal("Here is your report summary.") is False
        assert _is_refusal("# Top Headlines\n\n1. Item one") is False

    @pytest.mark.asyncio
    async def test_refusal_triggers_model_fallback(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Codex returns refusal → triggers fallback to mini model."""
        monkeypatch.setenv("AZURE_OPENAI_API_KEY", "test-key")
        monkeypatch.setenv("AZURE_OPENAI_BASE_URL", "https://env-base.openai.azure.com/openai")

        call_count = 0
        captured_models: list[str] = []

        refusal_sse = [
            'data: {"type":"response.output_text.delta","delta":"I\'m sorry, I cannot assist."}',
            "data: [DONE]",
        ]
        success_sse = list(FAKE_SSE_LINES)

        def _stream(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            payload = kwargs.get("json", {})
            captured_models.append(payload.get("model", ""))
            if payload.get("model") == "llab-gpt-5.4":
                return _FakeStreamCM(_FakeStreamResponse(refusal_sse))
            return _FakeStreamCM(_FakeStreamResponse(success_sse))

        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.stream = MagicMock(side_effect=_stream)

        with (
            patch("report.generator._load_openclaw_azure_config", return_value={"base_url": "", "model": "m"}),
            patch("httpx.AsyncClient", return_value=mock_client),
        ):
            from report.generator import _call_llm

            result, used_fallback = await _call_llm("test", {})

        # codex: 1 attempt (refusal, no retry) → mini: 1 attempt (success)
        assert call_count == 2
        assert used_fallback is False
        assert captured_models[0] == "llab-gpt-5.4"
        assert captured_models[1] == "llab-gpt-5-mini"
        assert "Digest" in result

    @pytest.mark.asyncio
    async def test_refusal_all_models_fallback_to_raw_report(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """All models return refusal → structured raw data report."""
        monkeypatch.setenv("AZURE_OPENAI_API_KEY", "test-key")
        monkeypatch.setenv("AZURE_OPENAI_BASE_URL", "https://env-base.openai.azure.com/openai")

        refusal_sse = [
            'data: {"type":"response.output_text.delta","delta":"I\'m sorry, I cannot assist."}',
            "data: [DONE]",
        ]

        def _stream(*args, **kwargs):
            return _FakeStreamCM(_FakeStreamResponse(refusal_sse))

        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.stream = MagicMock(side_effect=_stream)

        test_data = {
            "AI Models": [
                {"title": "Test Article", "url": "https://example.com/1", "source": "openai", "author": "Author1", "content": "Some content"},
            ],
        }

        with (
            patch("report.generator._load_openclaw_azure_config", return_value={"base_url": "", "model": "m"}),
            patch("httpx.AsyncClient", return_value=mock_client),
        ):
            from report.generator import _call_llm

            result, used_fallback = await _call_llm("test", {}, categorized_for_llm=test_data)

        assert used_fallback is True
        assert "Raw Data" in result
        assert "Test Article" in result
        assert "https://example.com/1" in result


# ---------------------------------------------------------------------------
# Fallback Report Structure
# ---------------------------------------------------------------------------


class TestFallbackReport:
    """Test _generate_fallback_report with structured data."""

    def test_contains_items_and_urls(self) -> None:
        from report.generator import _generate_fallback_report

        data = {
            "AI Models": [
                {"title": "GPT-5 Release", "url": "https://openai.com/gpt5", "source": "openai", "author": "OpenAI", "content": "Major update"},
                {"title": "Claude 4", "url": "https://anthropic.com/claude4", "source": "anthropic", "author": "Anthropic", "content": "New model"},
            ],
        }
        report = _generate_fallback_report(data)
        assert "GPT-5 Release" in report
        assert "https://openai.com/gpt5" in report
        assert "Claude 4" in report
        assert "https://anthropic.com/claude4" in report

    def test_grouped_by_category(self) -> None:
        from report.generator import _generate_fallback_report

        data = {
            "AI Models": [{"title": "Model A", "url": "https://a.com", "source": "s", "author": "", "content": ""}],
            "Developer Tools": [{"title": "Tool B", "url": "https://b.com", "source": "s", "author": "", "content": ""}],
        }
        report = _generate_fallback_report(data)
        assert "### AI Models" in report
        assert "### Developer Tools" in report

    def test_renders_ranking_metadata_when_present(self) -> None:
        from report.generator import _generate_fallback_report

        data = {
            "Books & Reading": [
                {
                    "title": "明朝那些事儿（全集）",
                    "url": "https://weread.qq.com/web/reader/a57325c05c8ed3a57224187",
                    "source": "weread",
                    "author": "当年明月",
                    "content": "历史故事",
                    "ranking": {
                        "base_signal": "reading_count",
                        "base_score": 7918671.0,
                        "multiplier": 1.163529,
                        "factors": {
                            "finished": 0.05,
                            "rating_percent": 0.02769,
                        },
                        "summary": "基础热度主要来自在读人数 7,918,671；并由 完读率修正 +0.050、评分率修正 +0.028 带来约 1.164x 的轻微修正。",
                    },
                }
            ],
        }
        report = _generate_fallback_report(data)
        assert "Ranking Summary:" in report
        assert "基础热度主要来自在读人数 7,918,671" in report
        assert "Ranking:" not in report
        assert "base=reading_count:7918671.0" not in report
        assert "multiplier=1.164" not in report
        assert "finished+0.050" not in report

    def test_extract_ranking_payload_builds_natural_summary(self) -> None:
        from report.generator import _extract_ranking_payload

        item = SimpleNamespace(extra={
            "ranking_base_signal": "ratings_count",
            "ranking_base_score": 140442.0,
            "ranking_multiplier": 1.0,
            "ranking_factors": {
                "rank_bonus": 990.0,
            },
            "ratings_count": 140442,
            "star_rating": 4.4,
            "rank": 1,
        })

        ranking = _extract_ranking_payload(item)
        assert ranking["base_signal"] == "ratings_count"
        assert ranking["summary"] == "基础热度主要来自评分人数 140,442；另有榜单名次加分 +990。"
        assert ranking["signals"]["ratings_count"] == 140442

    def test_build_llm_item_payload_omits_empty_author(self) -> None:
        from report.generator import _build_llm_item_payload

        item = SimpleNamespace(
            title="Example Book",
            author="",
            source="kindle_books",
            url="https://example.com/book",
            content="Example content",
            score=123.0,
            published_at=SimpleNamespace(isoformat=lambda: "2026-04-26T00:00:00+00:00"),
            tags=["book"],
            extra={},
        )

        payload = _build_llm_item_payload(item)
        assert "author" not in payload

    def test_empty_data(self) -> None:
        from report.generator import _generate_fallback_report

        report = _generate_fallback_report(None)
        assert "No item data available" in report

    def test_empty_categories(self) -> None:
        from report.generator import _generate_fallback_report

        report = _generate_fallback_report({"AI": []})
        assert "LLM Unavailable" in report


# ---------------------------------------------------------------------------
# Long-input robust summarization: chunk + compact + coverage ledger
# ---------------------------------------------------------------------------


class TestLongInputSummaryRobustness:
    @pytest.mark.asyncio
    async def test_very_long_input_uses_chunk_pipeline_and_repairs_missing_important_items(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """~230k+ char inputs must not degrade to Raw Fallback or drop must-cover items."""
        from datetime import datetime, timezone

        from report import generator
        from schema import ContentItem, SourceType

        categories = ["AI Models & Agent", "Developer Tools", "Finance & Markets"]
        items = []
        source_for_category = {
            "AI Models & Agent": "hackernews",
            "Developer Tools": "github",
            "Finance & Markets": "finance",
        }
        for idx in range(520):
            category = categories[idx % len(categories)]
            tags = ["ai"] if category == "AI Models & Agent" else [category.lower().replace(" ", "_")]
            items.append(
                ContentItem(
                    source=source_for_category[category],
                    source_type=SourceType.RSS,
                    title=f"{idx:03d}-{idx * 7919:07x} {category} breakthrough signal",
                    url=f"https://example.com/item-{idx}",
                    content=("Long context sentence. " * 20) + f"Unique payload {idx}",
                    published_at=datetime(2026, 4, 29, tzinfo=timezone.utc),
                    score=float(idx),
                    tags=tags,
                )
            )
        # Highest-priority item; fake final LLM output intentionally omits it.
        items.append(
            ContentItem(
                source="critical-source",
                source_type=SourceType.RSS,
                title="CRITICAL LEDGER ITEM SHOULD SURVIVE",
                url="https://example.com/critical-ledger-item",
                content="This is a high-impact item that must be preserved even if chunk/final LLM omits it.",
                published_at=datetime(2026, 4, 29, tzinfo=timezone.utc),
                score=999999.0,
                tags=["ai"],
            )
        )

        calls = {"chunk": 0, "final": 0, "compact": 0}

        async def fake_call_llm(prompt, config, categorized_for_llm=None, usage_collector=None, fallback_to_raw=True, validator=None):
            if "分块摘要器" in prompt:
                calls["chunk"] += 1
                return (
                    "### 重要候选\n"
                    f"- [Chunk survivor {calls['chunk']}](<https://example.com/chunk-{calls['chunk']}>)：分块保留。\n\n"
                    "### 主题信号\n- AI 与工具链持续活跃。\n\n"
                    "### 分类补充\n- 其他值得跟踪的条目。",
                    False,
                )
            if "中间压缩器" in prompt:
                calls["compact"] += 1
                return (
                    "### 重要候选\n- [Compacted survivor](<https://example.com/compacted>)：压缩后保留。\n\n"
                    "### 主题信号\n- 压缩保留跨分块主题。\n\n"
                    "### 分类补充\n- 压缩补充。",
                    False,
                )
            if "最终编辑" in prompt:
                calls["final"] += 1
                assert "CRITICAL LEDGER ITEM SHOULD SURVIVE" in prompt
                return (
                    "## 1. Today's Top 10 Headlines\n\n"
                    "- [Compacted survivor](<https://example.com/compacted>)：最终摘要保留了部分信息。\n\n"
                    "## 2. 主题与分类精选\n\n"
                    "### AI 与工具链\n- 这里故意漏掉最高优先级条目，测试覆盖补全。\n",
                    False,
                )
            raise AssertionError("unexpected prompt")

        monkeypatch.setattr(generator, "_call_llm", fake_call_llm)
        monkeypatch.setattr(generator, "deduplicate", lambda raw_items: list(raw_items))
        config = {
            "summary": {
                "categories": categories,
                "top_headlines": 10,
                "direct_prompt_char_limit": 1_000,
                "chunk_char_limit": 18_000,
                "final_synthesis_char_limit": 1_000,
                "compact_batch_char_limit": 1_200,
                "coverage_required_items": 12,
                "coverage_ledger_max_items": 40,
                "coverage_per_category": 5,
            }
        }

        report = await generator.generate_digest_report(items, config, "2026-04-29")

        assert calls["chunk"] > 1
        assert calls["compact"] >= 1
        assert calls["final"] == 1
        assert "<!-- RAW_FALLBACK_REPORT -->" not in report.full_markdown
        assert "LLM Unavailable" not in report.full_markdown
        assert "CRITICAL LEDGER ITEM SHOULD SURVIVE" in report.full_markdown
        assert "https://example.com/critical-ledger-item" in report.full_markdown
        assert "仍值得保留的补充信号" in report.full_markdown
        assert "系统校验" not in report.full_markdown

    @pytest.mark.asyncio
    async def test_failed_chunk_uses_deterministic_fallback_instead_of_aborting(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from report import generator

        categorized = {
            "AI Models & Agent": [
                {
                    "title": "Important A",
                    "url": "https://example.com/a",
                    "source": "source-a",
                    "content": "A must survive",
                    "score": 1000,
                    "source_weight": 3,
                    "published_at": "2026-04-29T00:00:00+00:00",
                    "tags": ["ai"],
                },
                {
                    "title": "Important B",
                    "url": "https://example.com/b",
                    "source": "source-b",
                    "content": "B must survive",
                    "score": 900,
                    "source_weight": 3,
                    "published_at": "2026-04-29T00:00:00+00:00",
                    "tags": ["ai"],
                },
            ]
        }
        call_index = 0

        async def fake_call_llm(prompt, config, categorized_for_llm=None, usage_collector=None, fallback_to_raw=True, validator=None):
            nonlocal call_index
            if "分块摘要器" in prompt:
                call_index += 1
                if call_index == 1:
                    return "", True
                return "### 重要候选\n- [Important B](<https://example.com/b>)", False
            if "最终编辑" in prompt:
                assert "Important A" in prompt  # deterministic failed-chunk fallback carried it forward
                return "## 1. Today's Top 2 Headlines\n\n- [Important B](<https://example.com/b>)\n\n## 2. 主题与分类精选\n", False
            return "### 重要候选\n- compact", False

        monkeypatch.setattr(generator, "_call_llm", fake_call_llm)
        out = await generator._call_llm_chunked_digest(
            categorized,
            {
                "summary": {
                    "chunk_char_limit": 260,
                    "top_headlines": 2,
                    "coverage_required_items": 2,
                    "coverage_ledger_max_items": 2,
                }
            },
            top_n=2,
            categories=["AI Models & Agent"],
            usage_collector=[],
        )

        assert out is not None
        assert "Important A" in out
        assert "https://example.com/a" in out
        assert "<!-- RAW_FALLBACK_REPORT -->" not in out


# ---------------------------------------------------------------------------
# Non-streaming fallback on TTFT timeout
# ---------------------------------------------------------------------------


class TestNonStreamingFallback:
    """Test progressive degradation: streaming → non-streaming."""

    @pytest.mark.asyncio
    async def test_non_streaming_on_third_attempt(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """After 2 streaming failures (deltas=0), attempt 3 uses non-streaming."""
        monkeypatch.setenv("AZURE_OPENAI_API_KEY", "test-key")
        monkeypatch.setenv("AZURE_OPENAI_BASE_URL", "https://env-base.openai.azure.com/openai")

        call_count = 0
        stream_calls = 0
        post_calls = 0

        def _stream(*args, **kwargs):
            nonlocal call_count, stream_calls
            call_count += 1
            stream_calls += 1
            # Simulate TTFT timeout: preamble lines but no deltas
            return _FakeStreamCMError(
                httpx.RemoteProtocolError("Server disconnected")
            )

        async def _post(*args, **kwargs):
            nonlocal call_count, post_calls
            call_count += 1
            post_calls += 1
            # Non-streaming success response
            resp = MagicMock()
            resp.status_code = 200
            resp.raise_for_status = MagicMock()
            resp.json = MagicMock(return_value={
                "output": [{
                    "type": "message",
                    "content": [{"type": "output_text", "text": "# Digest Report\n\nSuccess via non-streaming."}],
                }],
            })
            return resp

        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.stream = MagicMock(side_effect=_stream)
        mock_client.post = AsyncMock(side_effect=_post)

        with (
            patch("report.generator._load_openclaw_azure_config", return_value={"base_url": "", "model": "m"}),
            patch("httpx.AsyncClient", return_value=mock_client),
            patch("report.generator.asyncio.sleep", new_callable=AsyncMock),
        ):
            from report.generator import _call_llm

            result, used_fallback = await _call_llm("test", {})

        # codex: attempt 1 (stream fail) → attempt 2 (stream fail) → attempt 3 (non-stream success)
        assert stream_calls == 2
        assert post_calls == 1
        assert used_fallback is False
        assert "Digest Report" in result

    @pytest.mark.asyncio
    async def test_stream_true_in_first_attempts(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """First attempt uses stream=True in payload."""
        monkeypatch.setenv("AZURE_OPENAI_API_KEY", "test-key")
        monkeypatch.setenv("AZURE_OPENAI_BASE_URL", "https://env-base.openai.azure.com/openai")

        captured: list[dict] = []
        mock_client = _make_streaming_client(captured_payloads=captured)

        with (
            patch("report.generator._load_openclaw_azure_config", return_value={"base_url": "", "model": "m"}),
            patch("httpx.AsyncClient", return_value=mock_client),
        ):
            from report.generator import _call_llm

            await _call_llm("prompt", {})

        assert len(captured) == 1
        assert captured[0]["stream"] is True


def test_coverage_supplement_uses_reader_facing_title():
    from report.generator import _ensure_ledger_coverage

    ledger = {
        "must_cover": [
            {
                "event_id": "evt-reader-facing",
                "title": "Important AI signal",
                "url": "https://example.com/important-ai-signal",
                "category": "AI Models & Agent",
                "source": "openai",
                "reason": "A concise reason this matters.",
            }
        ],
        "category_priorities": {},
    }
    out = _ensure_ledger_coverage("## 1. Today's Top Headlines\n\n- Other item", ledger, {"summary": {"coverage_required_items": 1}})
    assert "## 3. 仍值得保留的补充信号" in out
    assert "系统校验" not in out
    assert "https://example.com/important-ai-signal" in out


def test_coverage_supplement_keeps_multiline_social_snippets_inline():
    from report.generator import _ensure_ledger_coverage

    ledger = {
        "must_cover": [
            {
                "event_id": "evt-agent-os",
                "title": "通用 Agent 就是未来的操作系统了\n\nApp 会有几种结局：\n- 消亡：Agent 自己就有能力，不需要独立的 App\n- 变成 CLI 或者 MCP：搭配 Skill 去让 Agent 调用",
                "url": "https://x.com/dotey/status/2060949916256460894",
                "category": "Social & Community",
                "source": "x_twitter",
                "reason": "通用 Agent 就是未来的操作系统了\n\nApp 会有几种结局：\n- 消亡：Agent 自己就有能力，不需要独立的 App\n- 变成 CLI 或者 MCP：搭配 Skill 去让 Agent 调用",
            }
        ],
        "category_priorities": {},
    }
    out = _ensure_ledger_coverage("## 1. Today's Top Headlines\n\n- Other item", ledger, {"summary": {"coverage_required_items": 1}})
    supplement = out.split("## 3. 仍值得保留的补充信号", 1)[1]
    rendered_items = [line for line in supplement.splitlines() if line.startswith("- ")]
    assert len(rendered_items) == 1
    assert "\nApp 会有几种结局" not in supplement
    assert "\n- 消亡" not in supplement
    assert "App 会有几种结局： - 消亡" in supplement
    assert "通用 Agent 就是未来的操作系统了](https://x.com/dotey/status/2060949916256460894)" in supplement
    assert "变成 CLI 或者 MCP" not in supplement.split("](https://x.com/dotey/status/2060949916256460894)", 1)[0]


def test_importance_ledger_marks_practice_and_correction_attention_signals() -> None:
    from report.generator import _ledger_row

    row = _ledger_row(
        "AI Models & Agent",
        {
            "title": "TurboQuant follow-up community benchmark",
            "url": "https://reddit.com/r/LocalLLaMA/comments/1sm6d2k/what_is_the_current_status_with_turbo_quant/",
            "source": "reddit",
            "score_breakdown": {
                "community_practice_signal": 4.0,
                "paper_quality_components": {
                    "correction_signal": 2.0,
                    "industrial_practice": 1.0,
                },
            },
        },
    )

    assert "社区实测/实践限制信号（低置信度）" in row["report_attention_signals"]
    assert "复现纠偏/适用边界信号" in row["report_attention_signals"]
    assert "生产实践/工业部署信号" in row["report_attention_signals"]
