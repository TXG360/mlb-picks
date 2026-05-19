from flask import Flask, jsonify
import requests
from datetime import datetime
import pytz
import pybaseball
import pandas as pd

app = Flask(__name__)

# ─── Cache ────────────────────────────────────────────────────────────────────
_cache = {}
CACHE_TTL = 3600

def cached(key, fn):
    now = datetime.utcnow().timestamp()
    if key in _cache and now - _cache[key]['ts'] < CACHE_TTL:
        return _cache[key]['data']
    result = fn()
    _cache[key] = {'data': result, 'ts': now}
    return result

# ─── Park Factors (run factor, 100 = neutral) ─────────────────────────────────
PARK_FACTORS = {
    'Colorado Rockies': 115, 'Cincinnati Reds': 108, 'Boston Red Sox': 106,
    'Texas Rangers': 105, 'Philadelphia Phillies': 104, 'Chicago Cubs': 103,
    'Milwaukee Brewers': 102, 'Atlanta Braves': 102, 'Arizona Diamondbacks': 101,
    'Baltimore Orioles': 101, 'Los Angeles Angels': 100, 'Toronto Blue Jays': 100,
    'New York Yankees': 100, 'Kansas City Royals': 99, 'Minnesota Twins': 99,
    'Detroit Tigers': 99, 'Cleveland Guardians': 98, 'Tampa Bay Rays': 98,
    'Houston Astros': 97, 'Washington Nationals': 97, 'Chicago White Sox': 97,
    'Los Angeles Dodgers': 96, 'San Francisco Giants': 95, 'Pittsburgh Pirates': 96,
    'St. Louis Cardinals': 95, 'New York Mets': 95, 'San Diego Padres': 94,
    'Seattle Mariners': 93, 'Oakland Athletics': 94, 'Miami Marlins': 93,
}

INDOOR_TEAMS = {
    'Tampa Bay Rays', 'Toronto Blue Jays', 'Milwaukee Brewers',
    'Minnesota Twins', 'Houston Astros', 'Arizona Diamondbacks',
    'Seattle Mariners', 'Miami Marlins'
}

TEAM_CITIES = {
    'Colorado Rockies': 'Denver+CO', 'Cincinnati Reds': 'Cincinnati+OH',
    'Boston Red Sox': 'Boston+MA', 'Texas Rangers': 'Arlington+TX',
    'Philadelphia Phillies': 'Philadelphia+PA', 'Chicago Cubs': 'Chicago+IL',
    'Atlanta Braves': 'Cumberland+GA', 'Baltimore Orioles': 'Baltimore+MD',
    'Los Angeles Angels': 'Anaheim+CA', 'New York Yankees': 'Bronx+NY',
    'Kansas City Royals': 'Kansas+City+MO', 'Detroit Tigers': 'Detroit+MI',
    'Cleveland Guardians': 'Cleveland+OH', 'Washington Nationals': 'Washington+DC',
    'Chicago White Sox': 'Chicago+IL', 'Los Angeles Dodgers': 'Los+Angeles+CA',
    'San Francisco Giants': 'San+Francisco+CA', 'Pittsburgh Pirates': 'Pittsburgh+PA',
    'St. Louis Cardinals': 'St+Louis+MO', 'New York Mets': 'Queens+NY',
    'San Diego Padres': 'San+Diego+CA', 'Oakland Athletics': 'Sacramento+CA',
    'Miami Marlins': 'Miami+FL',
}

# ─── Pitcher Stats ─────────────────────────────────────────────────────────────
def get_pitcher_df():
    def fetch():
        try:
            pybaseball.cache.enable()
            return pybaseball.pitching_stats(2026, qual=0)
        except Exception as e:
            print(f"pybaseball error: {e}")
            return pd.DataFrame()
    return cached('pitchers_2026', fetch)

def lookup_pitcher(name, df):
    if df is None or df.empty or name == 'TBD':
        return None
    match = df[df['Name'] == name]
    if match.empty:
        last = name.split()[-1]
        match = df[df['Name'].str.split().str[-1] == last]
    if match.empty:
        return None
    row = match.iloc[0]

    def safe(col, decimals=2):
        try:
            return round(float(row[col]), decimals)
        except:
            return 'N/A'

    def pct(col):
        try:
            return f"{round(float(row[col]) * 100, 1)}%"
        except:
            return 'N/A'

    return {
        'ERA':    safe('ERA'),
        'WHIP':   safe('WHIP'),
        'xFIP':   safe('xFIP'),
        'SIERA':  safe('SIERA'),
        'K%':     pct('K%'),
        'BB%':    pct('BB%'),
        'SwStr%': pct('SwStr%'),
        'HR/FB':  pct('HR/FB'),
        'IP':     safe('IP', decimals=1),
        'GS':     int(row['GS']) if 'GS' in row else 'N/A',
    }

def stat_color(stat, value):
    try:
        v = float(str(value).replace('%', ''))
    except:
        return ''
    rules = {
        'xFIP':   ([(3.5,'elite'),(4.0,'good'),(4.5,'avg')], False),
        'SIERA':  ([(3.5,'elite'),(4.0,'good'),(4.5,'avg')], False),
        'ERA':    ([(3.0,'elite'),(3.75,'good'),(4.5,'avg')], False),
        'WHIP':   ([(1.1,'elite'),(1.25,'good'),(1.4,'avg')], False),
        'K%':     ([(28,'elite'),(23,'good'),(18,'avg')],    True),
        'BB%':    ([(5,'elite'),(7,'good'),(9,'avg')],       False),
        'SwStr%': ([(14,'elite'),(11,'good'),(8,'avg')],     True),
        'HR/FB':  ([(8,'elite'),(11,'good'),(14,'avg')],     False),
    }
    if stat not in rules:
        return ''
    thresholds, higher_is_better = rules[stat]
    if higher_is_better:
        for t, cls in thresholds:
            if v >= t:
                return cls
        return 'bad'
    else:
        for t, cls in thresholds:
            if v <= t:
                return cls
        return 'bad'

# ─── Weather ──────────────────────────────────────────────────────────────────
def get_weather(home_team):
    if home_team in INDOOR_TEAMS:
        return {'label': 'Dome', 'relevant': False}
    city = TEAM_CITIES.get(home_team)
    if not city:
        return None
    try:
        r = requests.get(f"https://wttr.in/{city}?format=j1", timeout=5)
        d = r.json()
        c = d['current_condition'][0]
        return {
            'label':    c['weatherDesc'][0]['value'],
            'temp':     f"{c['temp_F']}°F",
            'wind':     f"{c['windspeedMiles']} mph {c['winddir16Point']}",
            'relevant': True
        }
    except:
        return None

# ─── Games ────────────────────────────────────────────────────────────────────
def get_todays_games():
    pacific = pytz.timezone('America/Los_Angeles')
    today = datetime.now(pacific).strftime('%Y-%m-%d')
    url = (f"https://statsapi.mlb.com/api/v1/schedule"
           f"?sportId=1&date={today}"
           f"&hydrate=probablePitcher,lineups,team,venue,game")
    data = requests.get(url).json()
    pitcher_df = get_pitcher_df()

    games = []
    for date_entry in data.get('dates', []):
        for game in date_entry.get('games', []):
            away_team = game['teams']['away']['team']['name']
            home_team = game['teams']['home']['team']['name']

            game_time_pt = ''
            raw = game.get('gameDate', '')
            if raw:
                try:
                    utc_dt = datetime.strptime(raw, '%Y-%m-%dT%H:%M:%SZ').replace(tzinfo=pytz.utc)
                    game_time_pt = utc_dt.astimezone(pacific).strftime('%-I:%M %p PT')
                except:
                    pass

            away_p = game['teams']['away'].get('probablePitcher', {}).get('fullName', 'TBD')
            home_p = game['teams']['home'].get('probablePitcher', {}).get('fullName', 'TBD')

            away_lineup, home_lineup = [], []
            if 'lineups' in game:
                for p in game.get('lineups', {}).get('awayPlayers', []):
                    away_lineup.append(p.get('fullName', ''))
                for p in game.get('lineups', {}).get('homePlayers', []):
                    home_lineup.append(p.get('fullName', ''))

            games.append({
                'game_id':          game['gamePk'],
                'away_team':        away_team,
                'home_team':        home_team,
                'game_time':        game_time_pt,
                'away_pitcher':     away_p,
                'home_pitcher':     home_p,
                'away_p_stats':     lookup_pitcher(away_p, pitcher_df),
                'home_p_stats':     lookup_pitcher(home_p, pitcher_df),
                'away_lineup':      away_lineup,
                'home_lineup':      home_lineup,
                'lineup_confirmed': len(away_lineup) > 0 and len(home_lineup) > 0,
                'weather':          get_weather(home_team),
                'park_factor':      PARK_FACTORS.get(home_team, 100),
            })
    return games

# ─── HTML Helpers ─────────────────────────────────────────────────────────────
def render_pitcher_block(name, stats):
    if not stats:
        return f'<div class="pitcher-block"><p class="pname">⚾ {name}</p><p style="color:#888;font-size:0.8em">Stats unavailable</p></div>'
    keys = ['xFIP', 'SIERA', 'K%', 'BB%', 'SwStr%', 'HR/FB', 'ERA', 'WHIP']
    grid = ''.join(
        f'<div class="sc"><span class="sl">{k}</span>'
        f'<span class="sv {stat_color(k, stats.get(k,"N/A"))}">{stats.get(k,"N/A")}</span></div>'
        for k in keys
    )
    return (f'<div class="pitcher-block">'
            f'<p class="pname">⚾ {name} <span style="color:#888;font-size:0.75em">({stats["GS"]} GS · {stats["IP"]} IP)</span></p>'
            f'<div class="sgrid">{grid}</div></div>')

def render_card(g, css):
    pf = g['park_factor']
    pf_label = ('🔴 Hitter Friendly' if pf >= 105 else '🟢 Pitcher Friendly' if pf <= 95 else '⚪ Neutral')
    pf_cls   = 'hitter' if pf >= 105 else 'pitcher-park' if pf <= 95 else 'neutral'

    weather_html = ''
    w = g.get('weather')
    if w:
        if not w['relevant']:
            weather_html = '<span class="badge dome">🏟️ Dome</span>'
        else:
            weather_html = f'<span class="badge wx">🌤️ {w["label"]} · {w["temp"]} · 💨 {w["wind"]}</span>'

    lineups_html = ''
    if g['lineup_confirmed']:
        away_li = ''.join(f'<li>{p}</li>' for p in g['away_lineup'])
        home_li = ''.join(f'<li>{p}</li>' for p in g['home_lineup'])
        lineups_html = f'''
        <details class="lu"><summary>📋 View Lineups</summary>
        <div class="lu-row">
          <div><b>{g["away_team"]}</b><ol>{away_li}</ol></div>
          <div><b>{g["home_team"]}</b><ol>{home_li}</ol></div>
        </div></details>'''
    else:
        lineups_html = '<p style="color:#ff6b6b;font-size:0.82em">⏳ Lineup not yet confirmed</p>'

    return f'''
    <div class="game {css}">
      <div class="gh"><h3>{g["away_team"]} @ {g["home_team"]}</h3>
        <span class="gt">🕐 {g["game_time"]}</span></div>
      <div class="badges">
        <span class="badge {pf_cls}">🏠 PF {pf} · {pf_label}</span>
        {weather_html}
      </div>
      <div class="pr">
        {render_pitcher_block(g["away_pitcher"], g["away_p_stats"])}
        {render_pitcher_block(g["home_pitcher"], g["home_p_stats"])}
      </div>
      {lineups_html}
    </div>'''

# ─── Routes ───────────────────────────────────────────────────────────────────
@app.route('/')
def index():
    pacific = pytz.timezone('America/Los_Angeles')
    now_pt = datetime.now(pacific)
    games = get_todays_games()
    confirmed = [g for g in games if g['lineup_confirmed']]
    pending   = [g for g in games if not g['lineup_confirmed']]

    css = """
    *{box-sizing:border-box;margin:0;padding:0}
    body{font-family:Arial,sans-serif;background:#1a1a2e;color:#eee;padding:16px;max-width:1100px;margin:auto}
    h1{color:#ffd700;font-size:1.5em;margin-bottom:4px}
    h2{font-size:1.1em;margin:16px 0 8px}
    h3{color:#ff6b6b;font-size:1em}
    .sub{color:#888;font-size:0.82em;margin-bottom:16px}
    .game{background:#16213e;border:1px solid #0f3460;padding:14px;margin:10px 0;border-radius:10px}
    .confirmed{border-left:4px solid #00ff88}
    .pending{border-left:4px solid #ff6b6b}
    .gh{display:flex;justify-content:space-between;align-items:center;margin-bottom:8px}
    .gt{color:#aaa;font-size:0.82em}
    .badges{display:flex;flex-wrap:wrap;gap:6px;margin-bottom:10px}
    .badge{font-size:0.73em;padding:3px 8px;border-radius:12px}
    .hitter{background:#3d1515;color:#ff6b6b}
    .pitcher-park{background:#0d2e1a;color:#00ff88}
    .neutral{background:#1e1e3a;color:#aaa}
    .wx{background:#1a2a3a;color:#7ec8e3}
    .dome{background:#2a2a2a;color:#888}
    .pr{display:flex;gap:12px;flex-wrap:wrap;margin-bottom:10px}
    .pitcher-block{flex:1;min-width:240px;background:#0f1929;border-radius:8px;padding:10px}
    .pname{color:#ffd700;font-size:0.88em;margin-bottom:8px}
    .sgrid{display:grid;grid-template-columns:repeat(4,1fr);gap:4px}
    .sc{background:#1a2540;border-radius:4px;padding:4px 6px;text-align:center}
    .sl{display:block;font-size:0.62em;color:#888}
    .sv{display:block;font-size:0.88em;font-weight:bold}
    .elite{color:#00ff88}.good{color:#88ff44}.avg{color:#ffd700}.bad{color:#ff6b6b}
    .lu{margin-top:8px}
    .lu summary{cursor:pointer;color:#aaa;font-size:0.83em;padding:4px 0}
    .lu-row{display:flex;gap:16px;flex-wrap:wrap;margin-top:8px;font-size:0.78em;color:#ccc}
    .lu-row ol{padding-left:18px;margin-top:4px}
    .lu-row li{margin:2px 0}
    """

    html = f"""<!DOCTYPE html><html>
    <head><title>MLB V2 – {now_pt.strftime('%b %d, %Y')}</title>
    <meta name="viewport" content="width=device-width,initial-scale=1">
    <style>{css}</style></head>
    <body>
    <h1>⚾ MLB V2 Picks Dashboard</h1>
    <p class="sub">Last updated: {now_pt.strftime('%I:%M %p PT')} · {now_pt.strftime('%b %d, %Y')}</p>
    <h2 style="color:#00ff88">✅ Lineups Confirmed ({len(confirmed)} games)</h2>
    {''.join(render_card(g,'confirmed') for g in confirmed)}
    <h2 style="color:#ff6b6b">⏳ Lineups Pending ({len(pending)} games)</h2>
    {''.join(render_card(g,'pending') for g in pending)}
    </body></html>"""
    return html

@app.route('/api/games')
def api_games():
    return jsonify(get_todays_games())

if __name__ == '__main__':
    app.run(debug=True)
