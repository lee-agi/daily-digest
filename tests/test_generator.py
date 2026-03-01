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
    """Retry up to 3 attempts on TransportError, then fallback."""

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
    async def test_fallback_after_three_failures(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("AZURE_OPENAI_API_KEY", "test-key")
        monkeypatch.setenv("AZURE_OPENAI_BASE_URL", "https://env-base.openai.azure.com/openai")

        call_count = 0

        def _stream(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            return _FakeStreamCMError(
                httpx.RemoteProtocolError("Server disconnected")
            )

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

        assert call_count == 3
        assert "LLM summary unavailable" in result

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
        """HTTPStatusError (4xx/5xx) should NOT be retried."""
        monkeypatch.setenv("AZURE_OPENAI_API_KEY", "test-key")
        monkeypatch.setenv("AZURE_OPENAI_BASE_URL", "https://env-base.openai.azure.com/openai")

        call_count = 0

        def _stream(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            return _FakeStreamCM(_FakeStreamResponse(status_code=401))

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

        assert call_count == 1
        assert "LLM summary unavailable" in result

    @pytest.mark.asyncio
    async def test_exponential_backoff(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Sleep duration should increase: 3s, 6s."""
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

        sleep_durations: list[float] = []
        original_sleep = AsyncMock(side_effect=lambda d: sleep_durations.append(d))

        with (
            patch("report.generator._load_openclaw_azure_config", return_value={"base_url": "", "model": "m"}),
            patch("httpx.AsyncClient", return_value=mock_client),
            patch("report.generator.asyncio.sleep", original_sleep),
        ):
            from report.generator import _call_llm

            await _call_llm("test", {})

        assert sleep_durations == [3, 6]
