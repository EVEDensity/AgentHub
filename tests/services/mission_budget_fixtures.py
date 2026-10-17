"""Real Mission parents shared by SQLite and disposable PostgreSQL tests."""
from datetime import datetime, timedelta, timezone

from app.domain.mission_budget import BudgetScope, TrustedModelUsage
from app.services.model_pricing import ModelPriceTable
from tests.domain.factories import build_contract, build_mission, build_work_unit


def server_prices(version='operator-prices-v1', rate='1'):
    return ModelPriceTable(version, {('provider', 'model'): (rate, '2')})


def execution_scope(work_unit_id='wu-1', **updates):
    return BudgetScope(mission_id='mis-1', work_unit_id=work_unit_id, attempt=1,
                       lease_id='lease-' + work_unit_id, runner_id='runner-1').model_copy(update=updates)


def trusted_usage(receipt='provider-response-1', **updates):
    return TrustedModelUsage(**{'provider': 'provider', 'model': 'model', 'provider_receipt_id': receipt,
                               'input_tokens': 100_000, 'output_tokens': 0, **updates})


async def seed_mission(repository):
    contract = build_contract()
    await repository.add_contract_lineage(contract.id, 'workspace-1')
    await repository.add_contract(contract)
    await repository.add_mission(build_mission(status='RUNNING'))
    await add_running_unit(repository)


async def add_running_unit(repository, identifier='wu-1', parent=None):
    unit = build_work_unit(id=identifier, status='RUNNING', attempt=1, parent_work_unit_id=parent,
                           lease={'id': 'lease-' + identifier, 'runner_id': 'runner-1',
                                  'expires_at': datetime.now(timezone.utc) + timedelta(minutes=5)})
    await repository.add_work_unit(unit)
