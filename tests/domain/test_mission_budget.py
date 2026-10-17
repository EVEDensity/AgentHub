"""Money never traverses binary float or silently loses precision."""
from decimal import Decimal
import unittest

from pydantic import ValidationError

from app.domain.mission_budget import BudgetError, MAX_INT64, TrustedModelUsage, usd_to_nano
from app.services.model_pricing import ModelPrice, ModelPriceTable


class ExactMoneyTests(unittest.TestCase):
    def test_exact_nano_conversion_and_bigint_boundary(self):
        self.assertEqual(usd_to_nano(Decimal('1.000000001')), 1_000_000_001)
        self.assertEqual(usd_to_nano('9223372036.854775807'), MAX_INT64)
        self.assertEqual(usd_to_nano(0), 0)
        for value in (0.1, True, 'NaN', 'Infinity', '-1', '0.0000000001', '9223372036.854775808',
                      Decimal('1e-999999999'), Decimal('1e999999999')):
            with self.subTest(value=value), self.assertRaises(BudgetError):
                usd_to_nano(value)

    def test_call_cost_rounds_up_once_and_rejects_counts_or_cost_overflow(self):
        self.assertEqual(ModelPrice(1, 1).cost(1, 1), 1)
        self.assertEqual(ModelPrice(1_000_000_000, 2_000_000_000).cost(1, 2), 5000)
        for counts in ((True, 1), (1.0, 0), (-1, 0), (MAX_INT64 + 1, 0)):
            with self.subTest(counts=counts), self.assertRaises(BudgetError):
                ModelPrice(1, 1).cost(*counts)
        with self.assertRaises(BudgetError):
            ModelPrice(MAX_INT64, MAX_INT64).cost(MAX_INT64, MAX_INT64)

    def test_server_price_shape_and_exact_values_fail_closed(self):
        invalid = (12, [], {'ab': ('1', '2')}, {('a',): ('1', '2')}, {('a', 'b'): '12'},
                   {('a', 'b'): ('1',)}, {('a', 'b'): (0.1, '2')}, {('a', 'b'): (True, '2')})
        for prices in invalid:
            with self.subTest(prices=prices), self.assertRaises(BudgetError):
                ModelPriceTable('v1', prices)
        source = {('provider', 'model'): ['1', '2']}
        table = ModelPriceTable('v1', source)
        source[('provider', 'model')][0] = '999'
        self.assertEqual(table.price('provider', 'model').input_nano_usd_per_million, 1_000_000_000)
        with self.assertRaises(BudgetError):
            table.price('attacker', 'model')

    def test_usage_requires_real_integer_counts_and_provider_receipt(self):
        values = dict(provider='provider', model='model', provider_receipt_id='actual-provider-id', input_tokens=0, output_tokens=0)
        self.assertEqual(TrustedModelUsage(**values).input_tokens, 0)
        for updates in ({'input_tokens': True}, {'output_tokens': 1.0}, {'provider_receipt_id': ''}, {'cost': 0}):
            with self.subTest(updates=updates), self.assertRaises(ValidationError):
                TrustedModelUsage(**{**values, **updates})
