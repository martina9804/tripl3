import json, os, sys, time
from datetime import date, timedelta
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError

BASE = os.environ['SUPABASE_URL'].rstrip('/')
TOKEN = os.environ['TRIPL3_SYNC_TOKEN']
BUDGET = int(os.getenv('BACKFILL_REQUEST_BUDGET', '400'))
if not 1 <= BUDGET <= 1000:
    raise ValueError('BACKFILL_REQUEST_BUDGET must be between 1 and 1000')
TODAY = date.today()
END = TODAY - timedelta(days=1)
LEAGUES = [('nba', date(2026,10,1)), ('acb', date(2026,9,1)), ('euroleague', date(2026,9,1))]
used = 0

# One POST processes at most 3 days and 12 games of player stats.
def call(league, start, days):
    payload = json.dumps({'league': league, 'from': start.isoformat(), 'days': days}).encode()
    req = Request(f'{BASE}/functions/v1/backfill-season', data=payload,
        headers={'Content-Type':'application/json','x-sync-token':TOKEN}, method='POST')
    with urlopen(req, timeout=90) as response:
        data = json.load(response)
    if not data.get('success'):
        raise RuntimeError(f'Unexpected response for {league} {start}: {data}')
    return data

for league, first in LEAGUES:
    current = first
    print(f'\n=== {league.upper()} starting {first} ===', flush=True)
    while current <= END:
        days = min(3, (END-current).days + 1)
        # Reserve up to 15 requests per invocation: 3 dates + 12 stats.
        if used + days + 12 > BUDGET:
            print(f'Budget guard reached: {used}/{BUDGET}. Stop for today.', flush=True)
            sys.exit(0)
        # Re-run same date range until all game stats are present.
        # If provider has no stats for a game, stop instead of an endless loop.
        previous_missing = None
        while True:
            if used + days + 12 > BUDGET:
                print(f'Budget guard reached: {used}/{BUDGET}. Stop for today.', flush=True)
                sys.exit(0)
            try:
                result = call(league, current, days)
            except (HTTPError, URLError, TimeoutError, RuntimeError) as e:
                print(f'ERROR: {league} {current}: {e}. Stop; retry on next run.', file=sys.stderr, flush=True)
                sys.exit(1)
            used += result['api_requests']
            missing = result['finished_games_still_missing_stats']
            print(f'{league} {current} +{days}d: games={result["games_saved"]}, '
                  f'players={result["players_saved"]}, missing={missing}, calls={used}/{BUDGET}', flush=True)
            if missing == 0:
                break
            if missing == previous_missing or result['stats_games_with_data'] == 0:
                print(f'WARNING: {missing} games without stats for {league} {current}. '
                      'Stop for inspection (provider may not have stats).', file=sys.stderr, flush=True)
                sys.exit(1)
            previous_missing = missing
            time.sleep(0.5)
        current += timedelta(days=days)
        time.sleep(0.5)
print(f'All leagues checked through {END}. API requests this run: {used}.', flush=True)
