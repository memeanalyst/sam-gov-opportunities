"""Unit tests for the SAM.gov client: input validation, mapping, pagination.

All tests use fixture data — no network, no API key needed.
"""

from __future__ import annotations

import asyncio
import json
from datetime import date
from pathlib import Path

import httpx
import pytest

from sam_gov.client import (
    SamGovError,
    _sanitize_url,
    build_params,
    fetch_all,
    map_opportunity,
)

FIXTURE = json.loads(
    (Path(__file__).parent / 'fixtures' / 'search_response.json').read_text()
)


def test_build_params_defaults_last_30_days():
    params, max_items = build_params({})
    assert max_items == 100
    assert params['ptype'] == 'o,p,k'
    assert params['limit'] == 100
    # postedFrom/postedTo default to last 30 days -> today
    assert params['postedTo'] == date.today().strftime('%m/%d/%Y')


def test_build_params_explicit_values():
    params, max_items = build_params({
        'title': 'janitorial',
        'naicsCode': '561720',
        'setAsideCode': 'SBA',
        'state': 'tx',
        'organizationName': 'Department of Veterans Affairs',
        'postedFrom': '2026-01-01',
        'postedTo': '2026-06-01',
        'maxItems': 250,
        'noticeTypes': ['o', 'r'],
    })
    assert params['title'] == 'janitorial'
    assert params['ncode'] == '561720'
    assert params['typeOfSetAside'] == 'SBA'
    assert params['state'] == 'TX'
    assert params['organizationName'] == 'Department of Veterans Affairs'
    assert params['postedFrom'] == '01/01/2026'
    assert params['postedTo'] == '06/01/2026'
    assert params['ptype'] == 'o,r'
    assert max_items == 250
    assert params['limit'] == 100


def test_build_params_rejects_bad_date():
    with pytest.raises(SamGovError, match='Invalid date'):
        build_params({'postedFrom': 'next friday'})


def test_build_params_rejects_inverted_dates():
    with pytest.raises(SamGovError, match='must be before'):
        build_params({'postedFrom': '2026-06-01', 'postedTo': '2026-01-01'})


def test_build_params_rejects_range_over_a_year():
    with pytest.raises(SamGovError, match='maximum date range of 1 year'):
        build_params({'postedFrom': '2024-01-01', 'postedTo': '2026-01-01'})


def test_build_params_rejects_bad_naics():
    with pytest.raises(SamGovError, match='Invalid NAICS'):
        build_params({'naicsCode': 'abc'})


def test_build_params_rejects_bad_set_aside():
    with pytest.raises(SamGovError, match='Unknown set-aside'):
        build_params({'setAsideCode': 'NOPE'})


def test_build_params_rejects_bad_state():
    with pytest.raises(SamGovError, match='Invalid state'):
        build_params({'state': 'Texas'})


def test_build_params_rejects_bad_max_items():
    with pytest.raises(SamGovError, match='between 1 and 10,000'):
        build_params({'maxItems': 50000})


def test_map_opportunity_solicitation():
    item = map_opportunity(FIXTURE['opportunitiesData'][0])
    assert item['noticeId'] == '5b345bbb7127b91a3ad577b203fc6f68'
    assert item['title'] == 'IT Support Services for Rock Island Arsenal'
    assert item['solicitationNumber'] == 'W52P1J-26-R-0001'
    assert item['naicsCode'] == '541512'
    assert item['setAsideCode'] == 'SBA'
    assert item['responseDeadline'] == '2026-10-20 14:00:00'
    assert item['placeOfPerformance']['city'] == 'Rock Island'
    assert item['placeOfPerformance']['state'] == 'Illinois'
    assert item['placeOfPerformance']['zip'] == '61299'
    # No personal data may leak into output, even though the fixture carries it.
    assert 'pointOfContact' not in item
    serialized = json.dumps(item)
    assert 'jane.doe@army.mil' not in serialized
    assert 'Jane Doe' not in serialized
    assert '309-782-1234' not in serialized
    # The fixture description URL contains api_key=KEY — it must be stripped.
    assert item['descriptionUrl'] == (
        'https://api.sam.gov/opportunities/v2/search'
        '?noticeid=5b345bbb7127b91a3ad577b203fc6f68'
    )
    assert 'api_key' not in serialized
    assert item['agency'].startswith('DEPT OF DEFENSE')
    assert item['award'] is None


def test_map_opportunity_award_notice():
    item = map_opportunity(FIXTURE['opportunitiesData'][1])
    assert item['award']['amount'] == 1250000
    assert item['award']['awardeeName'] == 'Acme Cyber LLC'
    assert 'pointOfContact' not in item
    assert item['responseDeadline'] is None


def _paged_transport(pages: list[dict]) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        offset = int(request.url.params.get('offset', 0))
        page = next(p for p in pages if p['offset'] == offset)
        return httpx.Response(200, json=page)
    return httpx.MockTransport(handler)


def test_fetch_all_paginates_and_stops_at_max_items():
    page1 = {**FIXTURE, 'offset': 0, 'totalRecords': 4,
             'opportunitiesData': FIXTURE['opportunitiesData']}
    page2 = {**FIXTURE, 'offset': 2, 'totalRecords': 4,
             'opportunitiesData': FIXTURE['opportunitiesData']}
    transport = _paged_transport([page1, page2])

    async def run():
        async with httpx.AsyncClient(transport=transport) as client:
            return await fetch_all('KEY', {'limit': 2}, 3, client=client)

    items, total = asyncio.run(run())
    assert total == 4
    assert len(items) == 3  # capped at max_items


def test_fetch_all_401_gives_key_guidance():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, text='Invalid API key')
    transport = httpx.MockTransport(handler)

    async def run():
        async with httpx.AsyncClient(transport=transport) as client:
            await fetch_all('BAD', {'limit': 1}, 10, client=client)

    with pytest.raises(SamGovError, match='rejected the API key'):
        asyncio.run(run())


def test_fetch_all_empty_results():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={'totalRecords': 0, 'opportunitiesData': []})
    transport = httpx.MockTransport(handler)

    async def run():
        async with httpx.AsyncClient(transport=transport) as client:
            return await fetch_all('KEY', {'limit': 100}, 100, client=client)

    items, total = asyncio.run(run())
    assert items == [] and total == 0


def test_sanitize_url_strips_secret_params():
    url = ('https://api.sam.gov/opportunities/v2/search'
           '?noticeid=abc&api_key=SUPERSECRET&other=1')
    clean = _sanitize_url(url)
    assert 'SUPERSECRET' not in clean
    assert 'api_key' not in clean
    assert 'noticeid=abc' in clean
    assert 'other=1' in clean


def test_sanitize_url_variants_and_passthrough():
    assert _sanitize_url('https://x.test/a?API_KEY=K&b=2') == 'https://x.test/a?b=2'
    assert _sanitize_url('https://x.test/a?b=2') == 'https://x.test/a?b=2'
    assert _sanitize_url(None) is None
    assert _sanitize_url('') == ''


def test_fetch_all_429_retries_then_succeeds(monkeypatch):
    calls = {'n': 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls['n'] += 1
        if calls['n'] < 3:
            return httpx.Response(429, text='slow down')
        return httpx.Response(200, json={'totalRecords': 0, 'opportunitiesData': []})

    async def no_sleep(_):
        return None

    monkeypatch.setattr('sam_gov.client.asyncio.sleep', no_sleep)

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            return await fetch_all('KEY', {'limit': 1}, 10, client=client)

    items, total = asyncio.run(run())
    assert calls['n'] == 3
    assert items == []


def test_fetch_all_429_gives_up_after_retries(monkeypatch):
    async def no_sleep(_):
        return None

    monkeypatch.setattr('sam_gov.client.asyncio.sleep', no_sleep)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(429, text='slow down')

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            await fetch_all('KEY', {'limit': 1}, 10, client=client)

    with pytest.raises(SamGovError, match='rate limit keeps triggering'):
        asyncio.run(run())


def test_fetch_all_request_error_sanitizes_key():
    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError('should not be called')

    class FailingClient(httpx.AsyncClient):
        async def get(self, url, **kwargs):
            req = httpx.Request('GET', f'{url}?api_key=LEAKEDKEY')
            raise httpx.ConnectError('boom', request=req)

    async def run():
        async with FailingClient(transport=httpx.MockTransport(handler)) as client:
            await fetch_all('LEAKEDKEY', {'limit': 1}, 10, client=client)

    with pytest.raises(SamGovError) as excinfo:
        asyncio.run(run())
    assert 'LEAKEDKEY' not in str(excinfo.value)
