"""SAM.gov Federal Contract Opportunities — Apify Actor.

Searches U.S. federal contract opportunities via the official GSA public API.
No web scraping: all data comes from https://api.sam.gov with the user's own
free API key, with polite request pacing.
"""

from __future__ import annotations

from apify import Actor

from .client import SamGovError, build_params, fetch_all


async def main() -> None:
    async with Actor:
        actor_input = await Actor.get_input() or {}

        api_key = (actor_input.get('samApiKey') or '').strip()
        if not api_key:
            raise ValueError(
                'Missing "samApiKey". Get a free SAM.gov public API key: sign in at '
                'https://sam.gov, open Account Details, enter your password, and copy '
                'the API key into the "SAM.gov API key" input.'
            )

        try:
            params, max_items = build_params(actor_input)
        except SamGovError as exc:
            raise ValueError(str(exc)) from exc

        Actor.log.info(
            'Searching SAM.gov: %s (max %d items)',
            {k: v for k, v in params.items() if k != 'api_key'},
            max_items,
        )

        try:
            items, total_records = await fetch_all(api_key, params, max_items)
        except SamGovError as exc:
            raise ValueError(str(exc)) from exc

        Actor.log.info('SAM.gov reports %d matching notices; fetched %d.',
                       total_records, len(items))

        if items:
            await Actor.push_data(items)
            # Pay-per-event: one chargeable event per delivered opportunity.
            # Defensive: if monetization isn't configured yet, log loudly but
            # don't fail an otherwise successful run.
            try:
                await Actor.charge('opportunity-fetched', count=len(items))
            except Exception as exc:  # noqa: BLE001
                Actor.log.warning(
                    'Could not charge %d opportunity-fetched event(s): %s. '
                    'Check the Actor\'s Monetization settings.',
                    len(items), exc,
                )
            else:
                Actor.log.info('Charged %d opportunity-fetched event(s).', len(items))
        else:
            Actor.log.info('No opportunities matched the search — nothing charged.')
