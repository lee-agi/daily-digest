"""Tests for report/generator.py — streaming LLM call with retry logic."""

from __future__ import annotations

import json
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

    def test_truncated_when_missing_platform_stats(self) -> None:
        from report.generator import is_truncated_report

        text = "# Daily Digest - 2026-03-16\n\n## 1. Top\n- **The File"
        assert is_truncated_report(text) is True

    def test_truncated_when_unbalanced_bold(self) -> None:
        from report.generator import is_truncated_report

        text = (
            "# Daily Digest - 2026-03-16\n\n"
            "## Section\n- **broken\n\n"
            "## Platform Statistics\n\n| Platform | Items |\n|----------|-------|\n"
        )
        assert is_truncated_report(text) is True

    def test_not_truncated_for_complete_report(self) -> None:
        from report.generator import is_truncated_report

        text = (
            "# Daily Digest - 2026-03-16\n\n"
            "## Section\n- **complete item**\n\n"
            "## Platform Statistics\n\n"
            "| Platform | Items |\n|----------|-------|\n| github | 1 |\n| **Total** | **1** |\n"
        )
        assert is_truncated_report(text) is False


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
    """Model is always hard-coded to llab-gpt-5.2-codex, ignoring env vars."""

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

        assert captured[0]["model"] == "llab-gpt-5.2-codex"

    @pytest.mark.asyncio
    async def test_model_always_hardcoded(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Even with no env vars or openclaw config, model is llab-gpt-5.2-codex."""
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

        assert captured[0]["model"] == "llab-gpt-5.2-codex"


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

            result = await _call_llm("test", {})

        assert call_count == 2
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
            if payload.get("model") == "llab-gpt-5.2-codex":
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

            result = await _call_llm("test", {})

        total_calls = stream_call_count + post_call_count
        # codex: 2 streaming + 1 non-streaming = 3, then mini: 1 streaming = 4 total
        assert total_calls == 4
        assert "Digest" in result
        # First 2 are codex streaming, 3rd is codex non-streaming, 4th is mini streaming
        assert captured_models[:3] == ["llab-gpt-5.2-codex"] * 3
        assert captured_models[3] == "llab-gpt-5-mini"

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

            result = await _call_llm("test", {})

        # codex: 2 streaming + 1 non-streaming = 3, mini: 1 streaming + 1 non-streaming = 2 → 5 total
        assert call_count == 5
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

            result = await _call_llm("test", {})

        assert call_count == 2
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
            if payload.get("model") == "llab-gpt-5.2-codex":
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

            result = await _call_llm("test", {})

        # codex: 1 attempt (HTTP error, no retry) → mini: 1 attempt (success)
        assert call_count == 2
        assert captured_models == ["llab-gpt-5.2-codex", "llab-gpt-5-mini"]
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
            if payload.get("model") == "llab-gpt-5.2-codex":
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

            result = await _call_llm("test", {})

        # codex: 1 attempt (refusal, no retry) → mini: 1 attempt (success)
        assert call_count == 2
        assert captured_models[0] == "llab-gpt-5.2-codex"
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

            result = await _call_llm("test", {}, categorized_for_llm=test_data)

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

    def test_empty_data(self) -> None:
        from report.generator import _generate_fallback_report

        report = _generate_fallback_report(None)
        assert "No item data available" in report

    def test_empty_categories(self) -> None:
        from report.generator import _generate_fallback_report

        report = _generate_fallback_report({"AI": []})
        assert "LLM Unavailable" in report


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

            result = await _call_llm("test", {})

        # codex: attempt 1 (stream fail) → attempt 2 (stream fail) → attempt 3 (non-stream success)
        assert stream_calls == 2
        assert post_calls == 1
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
