"""Strict bounded JUnit-to-TestRunReport.v1 mapping; no XML means no PASS."""
from __future__ import annotations

import hashlib
import xml.etree.ElementTree as ET
from collections import Counter

from app.services.verification_report_models import TestRunReport


class JUnitError(ValueError):
    pass


def _case(element: ET.Element) -> dict[str, str]:
    outcomes = [child.tag for child in element if child.tag in ('failure', 'error', 'skipped')]
    if len(outcomes) > 1:
        raise JUnitError('JUnit testcase contains conflicting outcomes')
    status = {'failure': 'failed', 'error': 'errored', 'skipped': 'skipped'}.get(next(iter(outcomes), ''), 'passed')
    identity = element.attrib.get('classname', '') + '::' + element.attrib.get('name', '')
    if identity == '::':
        raise JUnitError('JUnit testcase has no identity')
    if len(identity) > 255:
        identity = 'sha256:' + hashlib.sha256(identity.encode()).hexdigest()
    return {'id': identity, 'status': status}


def junit_cases(content: bytes, *, max_bytes: int) -> list[dict[str, str]]:
    if not content or len(content) > max_bytes or b'<!DOCTYPE' in content.upper() or b'<!ENTITY' in content.upper():
        raise JUnitError('JUnit XML unavailable, oversized or contains declarations')
    try:
        root = ET.fromstring(content)
    except (ET.ParseError, ValueError) as exc:
        raise JUnitError('JUnit XML is malformed') from exc
    suites = [root] if root.tag == 'testsuite' else list(root)
    if root.tag not in ('testsuite', 'testsuites') or not suites or len(suites) > 100:
        raise JUnitError('JUnit suite structure is invalid')
    cases = []
    for suite in suites:
        cases.extend(_suite_cases(suite))
        if len(cases) > 50000:
            raise JUnitError('JUnit testcase count exceeds limit')
    if len({case['id'] for case in cases}) != len(cases):
        raise JUnitError('JUnit contains duplicate case identities')
    return cases


def _suite_cases(suite: ET.Element) -> list[dict[str, str]]:
    if suite.tag != 'testsuite':
        raise JUnitError('JUnit contains unsupported nested suite data')
    current = [_case(child) for child in suite if child.tag == 'testcase']
    counts = Counter(case['status'] for case in current)
    expected = {'tests': len(current), 'failures': counts['failed'], 'errors': counts['errored'], 'skipped': counts['skipped']}
    try:
        matches = all(int(suite.attrib[key]) == value for key, value in expected.items())
    except (KeyError, ValueError) as exc:
        raise JUnitError('JUnit counters are invalid') from exc
    if not matches:
        raise JUnitError('JUnit counters disagree with testcase outcomes')
    return current


def build_test_report(*, mission_id: str, work_unit_id: str, attempt: int,
                      cases: list[dict[str, str]], exit_code: int | None,
                      interrupted: bool) -> TestRunReport:
    counts = Counter(case['status'] for case in cases)
    passed = not interrupted and exit_code == 0 and counts['passed'] > 0 and not (counts['failed'] or counts['errored'])
    conclusion = 'passed' if passed else 'failed'
    if interrupted or not cases or not (counts['passed'] or counts['failed'] or counts['errored']):
        conclusion = 'inconclusive'
    return TestRunReport.model_validate({
        'schemaVersion': 1, 'reportType': 'test-run', 'missionId': mission_id,
        'workUnitId': work_unit_id, 'attempt': attempt, 'conclusion': conclusion, 'exitCode': exit_code,
        'total': len(cases), 'passed': counts['passed'], 'failed': counts['failed'],
        'errored': counts['errored'], 'skipped': counts['skipped'], 'cases': cases,
    })
