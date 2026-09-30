# SAM.gov Federal Contract Opportunities

An [Apify Actor](https://apify.com/) that searches **U.S. federal contract opportunities** from [SAM.gov](https://sam.gov) — solicitations, pre-solicitations, combined synopsis/solicitations, sources sought, special notices, award notices, and justifications.

**No scraping.** The Actor calls the official GSA [Get Opportunities public API](https://open.gsa.gov/api/get-opportunities-public-api/) with your own free API key, with polite request pacing. That makes it fast, reliable, and fully within SAM.gov's terms of use.

## What you get

Each run returns clean, flat JSON records with:

- Title, solicitation number, notice type, posted date, response deadline
- Agency / contracting office path
- NAICS and classification codes
- Set-aside program (e.g. Total Small Business, 8(a), HUBZone, SDVOSB, WOSB)
- Place of performance (city, state, zip)
- Public contracting-officer contact(s) listed on the notice
- Award details (for award notices)
- Link to the full opportunity description

## Getting your free SAM.gov API key

The SAM.gov API requires a free personal API key (no paid account needed):

1. Sign in at [sam.gov](https://sam.gov) (create a free account if you don't have one).
2. Open **Account Details**.
3. Enter your account password when prompted.
4. Copy the **public API key** shown on the page.
5. Paste it into the Actor's **SAM.gov API key** input (it's a secret field — never stored in the output).

## Inputs

| Input | Description |
|---|---|
| **SAM.gov API key** *(required)* | Your free public API key from sam.gov → Account Details. |
| Keyword in title | Filter by a phrase in the opportunity title, e.g. `janitorial services`. |
| Solicitation number | Look up one specific solicitation. |
| Notice types | Which notices to include (default: solicitation, pre-solicitation, combined). |
| NAICS code | 2–6 digit industry code, e.g. `541512`. |
| Set-aside type | Restrict to SBA, 8(a), HUBZone, SDVOSB, WOSB, EDWOSB… |
| Place of performance (state) | Two-letter state code, e.g. `TX`. |
| Agency / organization name | e.g. `Department of Veterans Affairs`. |
| Posted from / to | Date window (max 1 year, per the API). Defaults to the last 30 days. |
| Max opportunities | 1–10,000 (default 100). |

## Example input

```json
{
  "samApiKey": "YOUR_SAM_GOV_API_KEY",
  "title": "cybersecurity",
  "naicsCode": "541512",
  "setAsideCode": "SBA",
  "state": "VA",
  "noticeTypes": ["o", "p", "k"],
  "maxItems": 100
}
```

## Example output item

```json
{
  "noticeId": "5b345bbb7127b91a3ad577b203fc6f68",
  "title": "IT Support Services for Rock Island Arsenal",
  "solicitationNumber": "W52P1J-26-R-0001",
  "noticeType": "Solicitation",
  "active": "Yes",
  "postedDate": "2026-09-15 08:00:00",
  "responseDeadline": "2026-10-20 14:00:00",
  "agency": "DEPT OF DEFENSE > DEPT OF THE ARMY > AMC > ACC > ACC-RI",
  "naicsCode": "541512",
  "setAside": "Total Small Business Set-Aside (FAR 19.5)",
  "setAsideCode": "SBA",
  "placeOfPerformance": {"city": "Rock Island", "state": "Illinois", "zip": "61299", "country": "UNITED STATES"},
  "pointOfContact": [{"type": "Primary", "title": "Contract Specialist", "fullName": "Jane Doe", "email": "jane.doe@army.mil", "phone": "309-782-1234"}]
}
```

## Pricing

Pay-per-event: **$0.003 per opportunity fetched** ($3 per 1,000). Runs with zero matching notices are not charged. Standard Apify platform usage applies.

## Legal & safety notes

- Data source: the official SAM.gov public API, used with the end user's own API key per GSA's terms.
- All records are public U.S. federal procurement notices. Contact details included are the public business contacts published on the notices themselves.
- The Actor makes only a few requests per minute, well under SAM.gov rate limits.
- Your API key is sent only to `api.sam.gov` and never written to the dataset.

## Local development

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
pip install pytest
python -m pytest tests/ -q
```

Tests use recorded fixture data — no API key or network needed.

## License

MIT
