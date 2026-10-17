"""Harness generates operation identity and makes retries an explicit policy."""
from __future__ import annotations

import unittest
from unittest.mock import patch

import httpx

from app.services.harness_service import FunctionCallingHarness, FunctionTool, HarnessRequest
from app.services.model_contract import ModelResponse, ModelStreamEvent, ToolCall


class RecordingModel:
    def __init__(self, responses):
        self.responses, self.requests = list(responses), []

    async def complete(self, request):
        self.requests.append(request)
        value = self.responses.pop(0)
        if isinstance(value, Exception):
            raise value
        return value


class HarnessModelOperationTests(unittest.IsolatedAsyncioTestCase):
    def test_retries_are_bounded_explicit_integers_and_unset_metadata_remains_compatible(self):
        for value in (True, False, 0.0, -1, 2, None):
            with self.subTest(value=value), self.assertRaisesRegex(ValueError, 'model_retries'):
                FunctionCallingHarness(RecordingModel([]), [], model_retries=value)
        model = FunctionCallingHarness(RecordingModel([]), [])
        request = model._build_model_request(HarnessRequest('task', 'text', 5), (), tools_enabled=False, stream=False)
        self.assertEqual(request.metadata, {'language': 'text'})

    async def test_each_response_and_summary_have_distinct_harness_generated_ids(self):
        async def read(_arguments):
            return 'operator-local result'

        model = RecordingModel([
            ModelResponse(tool_calls=(ToolCall('one', 'read_candidate', {}),)),
            ModelResponse(tool_calls=(ToolCall('two', 'read_candidate', {}),)),
            ModelResponse(content='final summary'),
        ])
        harness = FunctionCallingHarness(model, [FunctionTool('read_candidate', read, lambda value: value)], max_iterations=2)
        original = HarnessRequest('task', 'text', 5, model_operation_id='untrusted-input-id')
        result = await harness.execute(original)
        self.assertTrue(result.sandbox.success)
        self.assertEqual([request.metadata['modelOperationId'] for request in model.requests],
                         ['harness:model:1:response', 'harness:model:2:response', 'harness:model:3:summary'])
        self.assertEqual(original.model_operation_id, 'untrusted-input-id')
        self.assertEqual(model.requests[-1].tool_choice, 'none')

    async def test_default_transient_retry_reuses_the_identical_operation(self):
        model = RecordingModel([RuntimeError('HTTP 503 unavailable'), ModelResponse(content='done')])
        with patch('app.services.harness_service.MODEL_RETRY_BACKOFF_SECONDS', 0):
            result = await FunctionCallingHarness(model, []).execute(HarnessRequest('task', 'text', 5))
        self.assertTrue(result.sandbox.success)
        self.assertEqual(len(model.requests), 2)
        self.assertIs(model.requests[0], model.requests[1])
        self.assertEqual(model.requests[0].metadata['modelOperationId'], 'harness:model:1:response')

    async def test_strict_complete_does_not_retry_exception_or_http_error_text(self):
        for error in (RuntimeError('HTTP 503 unavailable'), httpx.ConnectError('not connected')):
            model = RecordingModel([error, ModelResponse(content='must not run')])
            with self.subTest(error=type(error).__name__):
                result = await FunctionCallingHarness(model, [], model_retries=0).execute(HarnessRequest('task', 'text', 5))
                self.assertFalse(result.sandbox.success)
                self.assertEqual(len(model.requests), 1)

    async def test_legacy_complete_honors_zero_retries(self):
        class LegacyModel:
            def __init__(self):
                self.calls = 0

            async def complete(self, request, tool_results):
                self.calls += 1
                raise RuntimeError('HTTP 503 unavailable')

        model = LegacyModel()
        result = await FunctionCallingHarness(model, [], model_retries=0).execute(HarnessRequest('task', 'text', 5))
        self.assertFalse(result.sandbox.success)
        self.assertEqual(model.calls, 1)

    async def test_zero_stream_retries_refuse_even_a_pre_stream_transient_failure(self):
        class StreamingModel:
            def __init__(self):
                self.calls = 0

            async def stream(self, request):
                self.calls += 1
                raise RuntimeError('HTTP 503 unavailable')
                yield ModelStreamEvent(kind='completed')

        model = StreamingModel()
        result = await FunctionCallingHarness(model, [], model_retries=0).execute(
            HarnessRequest('task', 'text', 5, on_text_delta=lambda _text: None))
        self.assertFalse(result.sandbox.success)
        self.assertEqual(model.calls, 1)
