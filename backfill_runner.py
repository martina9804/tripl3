import json, os, sys, time
from datetime import date, timedelta
from pathlib import Path
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError

BASE = os.environ['SUPABASE_URL'].rstrip('/')
TOKEN = os.environ['TRIPL3_SYNC_TOKEN']
BUDGET = int(os.getenv('BACKFILL_REQUEST_BUDGET', '400'))
if not 15 <= BUDGET <= 1000:
    raise ValueError('BACKFILL_REQUEST_BUDGET must be between 15 and 1000')
END = date.today() - timedelta(days=1)
LEAGUES = [('acb', date(2026, 9, 1)), ('euroleague', date(2026, 9, 1)), ('nba', date(2026, 10, 1))]
STATE_FILE = Path(os.getenv('BACKFILL_STATE_FILE', 'backfill_progress.json'))
used = 0


def load_progress():
    if not STATE_FILE.exists():
        return {}
    data = json.loads(STATE_FILE.read_text(encoding='utf-8'))
    if not isinstance(data, dict):
        raise ValueError('Invalid progress file')
    return data


def save_progress(progress):
    tmp = STATE_FILE.with_suffix('.tmp')
    tmp.write_text(json.dumps(progress, indent=2, sort_keys=True) + '\n', encoding='utf-8')
    tmp.replace(STATE_FILE)


def call(league, start, days):
    payload = json.dumps({'league': league, 'from': start.isoformat(), 'days': days}).encode()
    req = Request(f'{BASE}/functions/v1/backfill-season', data=payload,
                  headers={'Content-Type': 'application/json', 'x-sync-token': TOKEN}, method='POST')
    with urlopen(req, timeout=90) as response:
        result = json.load(response)
    if not result.get('success'):
        raise RuntimeError(f'Unexpected response for {league} {start}: {result}')
    return result


progress = load_progress()
try:
    for league, first in LEAGUES:
        current = date.fromisoformat(progress.get(league, first.isoformat()))
        if current < first:
            current = first
        print(f'\n=== {league.upper()} from {current} ===', flush=True)
        while current <= END:
            days = min(3, (END - current).days + 1)
            previous_missing = None
            while True:
                # Worst case: one API request per day plus 12 player-stat requests.
                if used + days + 12 > BUDGET:
                    print(f'Budget guard: {used}/{BUDGET}. Saved checkpoint.', flush=True)
                    save_progress(progress)
                    sys.exit(0)
                result = call(league, current, days)
                calls = result.get('api_requests')
                if not isinstance(calls, int) or not 0 <= calls <= days + 12:
                    raise RuntimeError(f'Unexpected API request count: {calls}')
                used += calls
                missing = result['finished_games_still_missing_stats']
                print(f'{league} {current} +{days}d: games={result["games_saved"]}, '
                      f'players={result["players_saved"]}, missing={missing}, calls={used}/{BUDGET}', flush=True)
                if missing == 0:
                    current += timedelta(days=days)
                    progress[league] = current.isoformat()
                    save_progress(progress)
                    break
                if missing == previous_missing or result['stats_games_with_data'] == 0:
                    raise RuntimeError(f'{league} {current}: {missing} games without player stats; '
                                       'provider data may be unavailable. Check before advancing.')
                previous_missing = missing
                time.sleep(0.5)
            time.sleep(0.5)
    print(f'All leagues checked through {END}. API requests: {used}.', flush=True)
except (HTTPError, URLError, TimeoutError, RuntimeError, ValueError) as exc:
    print(f'ERROR: {exc}. Checkpoint retained for next run.', file=sys.stderr, flush=True)
    sys.exit(1)
