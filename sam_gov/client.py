"""SAM.gov Opportunities API client: request building, pagination, response mapping.

Uses the official GSA public API (https://api.sam.gov/opportunities/v2/search).
No web scraping. The caller's own API key is required (free from sam.gov).
"""

from __future__ import annotations

import asyncio
import logging
import re
from datetime import date, timedelta
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import httpx

log = logging.getLogger(__name__)

API_BASE = 'https://api.sam.gov/opportunities/v2/search'
PAGE_SIZE = 100
REQUEST_DELAY_S = 1.0  # conservative pacing between pages
MAX_429_RETRIES = 3
RETRY_429_DELAY_S = 60.0

# Query params that must never appear in emitted URLs (may carry the user's key).
_SECRET_QUERY_PARAMS = frozenset({
    'api_key', 'apikey', 'apiKey', 'API_KEY',
    'key', 'token', 'auth', 'authToken', 'access_token',
})

SET_ASIDE_CODES = {'SBA', 'SBP', '8A', '8AN', 'HZC', 'HZS', 'SDVOSBC', 'SDVOSBS',
                   'WOSB', 'WOSBSS', 'EDWOSB', 'EDWOSBSS', 'LAS', 'IEE', 'ISBEE',
                   'BICiv', 'VSA', 'VSS'}

NOTICE_TYPES = {'o', 'p', 'k', 'r', 's', 'a', 'u', 'g'}


class SamGovError(Exception):
    """A user-facing error talking to the SAM.gov API."""


def _to_api_date(value: str | None, default: date) -> str:
    """Accept MM/DD/YYYY or YYYY-MM-DD, return MM/DD/YYYY for the API."""
    if not value:
        return default.strftime('%m/%d/%Y')
    value = value.strip()
    for fmt in ('%m/%d/%Y', '%Y-%m-%d'):
        try:
            from datetime import datetime
            return datetime.strptime(value, fmt).strftime('%m/%d/%Y')
        except ValueError:
            continue
    raise SamGovError(
        f'Invalid date "{value}". Use MM/DD/YYYY or YYYY-MM-DD, e.g. 09/01/2026.'
    )


def _parse_api_date(value: str) -> date:
    from datetime import datetime
    return datetime.strptime(value, '%m/%d/%Y').date()


def build_params(actor_input: dict[str, Any]) -> tuple[dict[str, Any], int]:
    """Validate Actor input and build SAM.gov query params.

    Returns (params, max_items). Raises SamGovError with a user-friendly message.
    """
    today = date.today()
    posted_from = _to_api_date(actor_input.get('postedFrom'), today - timedelta(days=30))
    posted_to = _to_api_date(actor_input.get('postedTo'), today)

    if _parse_api_date(posted_from) > _parse_api_date(posted_to):
        raise SamGovError('"Posted from" date must be before "Posted to" date.')
    if (_parse_api_date(posted_to) - _parse_api_date(posted_from)).days > 365:
        raise SamGovError(
            'The SAM.gov API allows a maximum date range of 1 year. '
            'Narrow your posted-from / posted-to dates.'
        )

    notice_types = actor_input.get('noticeTypes') or ['o', 'p', 'k']
    unknown = set(notice_types) - NOTICE_TYPES
    if unknown:
        raise SamGovError(f'Unknown notice type(s): {sorted(unknown)}.')

    naics = (actor_input.get('naicsCode') or '').strip()
    if naics and not re.fullmatch(r'\d{2,6}', naics):
        raise SamGovError(
            f'Invalid NAICS code "{naics}". Use 2–6 digits, e.g. 541512.'
        )

    set_aside = (actor_input.get('setAsideCode') or '').strip()
    if set_aside and set_aside not in SET_ASIDE_CODES:
        raise SamGovError(f'Unknown set-aside code "{set_aside}".')

    state = (actor_input.get('state') or '').strip().upper()
    if state and not re.fullmatch(r'[A-Z]{2}', state):
        raise SamGovError(f'Invalid state "{state}". Use a 2-letter code, e.g. TX.')

    max_items = actor_input.get('maxItems') or 100
    try:
        max_items = int(max_items)
    except (TypeError, ValueError):
        raise SamGovError('"Max opportunities" must be a number.') from None
    if not 1 <= max_items <= 10000:
        raise SamGovError('"Max opportunities" must be between 1 and 10,000.')

    params: dict[str, Any] = {
        'postedFrom': posted_from,
        'postedTo': posted_to,
        'ptype': ','.join(notice_types),
        'limit': min(PAGE_SIZE, max_items),
    }
    if title := (actor_input.get('title') or '').strip():
        params['title'] = title
    if solnum := (actor_input.get('solicitationNumber') or '').strip():
        params['solnum'] = solnum
    if naics:
        params['ncode'] = naics
    if set_aside:
        params['typeOfSetAside'] = set_aside
    if state:
        params['state'] = state
    if org := (actor_input.get('organizationName') or '').strip():
        params['organizationName'] = org

    return params, max_items


def _sanitize_url(url: Any) -> Any:
    """Strip secret-looking query params (e.g. api_key) from a URL.

    SAM.gov echoes api_key in some resource URLs; emitting them would leak
    the user's key into the dataset. Returns non-strings unchanged.
    """
    if not isinstance(url, str) or not url:
        return url
    parts = urlsplit(url)
    if not parts.query:
        return url
    clean = [
        (k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True)
        if k not in _SECRET_QUERY_PARAMS
    ]
    return urlunsplit(parts._replace(query=urlencode(clean)))


def _get(d: dict[str, Any], *keys: str, default: Any = None) -> Any:
    for key in keys:
        if isinstance(d, dict) and key in d and d[key] not in (None, ''):
            d = d[key]
            if key == keys[-1]:
                return d
    return default


def map_opportunity(raw: dict[str, Any]) -> dict[str, Any]:
    """Map one raw SAM.gov opportunity to a clean, flat dataset item."""
    data = raw.get('data') or {}

    def loc(obj: Any) -> dict[str, str]:
        if not isinstance(obj, dict):
            return {}
        out: dict[str, str] = {}
        for field in ('streetAddress', 'city', 'state', 'zip', 'country'):
            val = obj.get(field)
            if isinstance(val, dict):
                val = val.get('name') or val.get('code')
            if val:
                out[field] = str(val)
        return out

    # NOTE: point-of-contact personal data (names, emails, phones) is
    # deliberately excluded from output — see project privacy constraint.

    award_raw = data.get('award') or {}
    award = None
    if isinstance(award_raw, dict) and award_raw:
        awardee = award_raw.get('awardee') or {}
        award = {
            'number': award_raw.get('number'),
            'amount': award_raw.get('amount'),
            'date': award_raw.get('date'),
            'awardeeName': awardee.get('name') if isinstance(awardee, dict) else None,
        }

    self_link = None
    for link in raw.get('links') or []:
        if isinstance(link, dict) and link.get('rel') == 'self':
            self_link = link.get('href')

    return {
        'noticeId': raw.get('noticeId'),
        'title': (raw.get('title') or '').strip(),
        'solicitationNumber': (raw.get('solicitationNumber') or '').strip() or None,
        'noticeType': raw.get('type'),
        'active': raw.get('active'),
        'postedDate': raw.get('postedDate'),
        'responseDeadline': raw.get('reponseDeadLine') or raw.get('responseDeadLine'),
        'archiveDate': raw.get('archiveDate'),
        'agency': raw.get('fullParentPathName'),
        'office': raw.get('office'),
        'naicsCode': raw.get('naicsCode'),
        'classificationCode': raw.get('classificationCode'),
        'setAside': raw.get('setAside'),
        'setAsideCode': raw.get('setAsideCode'),
        'placeOfPerformance': loc(data.get('placeOfPerformance')),
        'award': award,
        'descriptionUrl': _sanitize_url(raw.get('description')),
        'samGovLink': _sanitize_url(self_link),
    }


async def fetch_all(
    api_key: str,
    params: dict[str, Any],
    max_items: int,
    client: httpx.AsyncClient | None = None,
) -> tuple[list[dict[str, Any]], int]:
    """Fetch all pages of results. Returns (items, total_records)."""
    own_client = client is None
    client = client or httpx.AsyncClient(timeout=60)
    items: list[dict[str, Any]] = []
    total_records = 0
    offset = 0
    retries_429 = 0
    try:
        while len(items) < max_items:
            page_params = {**params, 'api_key': api_key, 'offset': offset}
            try:
                resp = await client.get(API_BASE, params=page_params)
            except httpx.RequestError as exc:
                # str(exc) can embed the request URL incl. api_key — sanitize it.
                safe_detail = _sanitize_url(str(exc))
                raise SamGovError(
                    f'Could not reach api.sam.gov: {safe_detail}. '
                    'Check your network / proxy settings.'
                ) from exc

            if resp.status_code in (401, 404):
                # SAM.gov answers invalid keys with 401 or 404 on this endpoint.
                raise SamGovError(
                    'SAM.gov rejected the API key '
                    f'(HTTP {resp.status_code}). Get a free key at sam.gov: '
                    'sign in, open Account Details, enter your password, and copy the '
                    'public API key into the "SAM.gov API key" input.'
                )
            if resp.status_code == 403:
                raise SamGovError(
                    'SAM.gov denied the request (403). Your API key may lack the '
                    'required role, or the key was just created and is not active yet. '
                    'Wait a few minutes and try again.'
                )
            if resp.status_code == 429:
                retries_429 += 1
                if retries_429 <= MAX_429_RETRIES:
                    log.warning(
                        'SAM.gov rate limit hit (429). Waiting %ds before retry '
                        '(%d/%d).', int(RETRY_429_DELAY_S), retries_429, MAX_429_RETRIES
                    )
                    await asyncio.sleep(RETRY_429_DELAY_S)
                    continue
                raise SamGovError(
                    'SAM.gov rate limit keeps triggering (429) after '
                    f'{MAX_429_RETRIES} retries. Wait a few minutes and run again '
                    'with a smaller date range or fewer pages.'
                )
            retries_429 = 0  # reset on any non-429 response
            if resp.status_code >= 400:
                detail = (resp.text or '').strip()[:300] or 'no detail returned'
                raise SamGovError(
                    f'SAM.gov API error {resp.status_code}: {detail}'
                )
            try:
                payload = resp.json()
            except ValueError as exc:
                raise SamGovError(
                    'SAM.gov returned a non-JSON response. The service may be down — try again later.'
                ) from exc

            total_records = int(payload.get('totalRecords') or 0)
            batch = payload.get('opportunitiesData') or []
            if not batch:
                break
            items.extend(map_opportunity(o) for o in batch)
            offset += len(batch)
            if offset >= total_records:
                break
            await asyncio.sleep(REQUEST_DELAY_S)
    finally:
        if own_client:
            await client.aclose()
    return items[:max_items], total_records
