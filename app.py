from flask import Flask, jsonify
import requests
from datetime import datetime
import pytz
import csv
import io

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

# ─── Park Factors ─────────────────────────────────────────────────────────────
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
}

# ─── Fuzzy Name Lookup ────────────────────────────────────────────────────────
def fuzzy_lookup(name, data_dict):
    if not data_dict or not name:
        return None
    if name in data_dict:
        return data_dict[name]
    parts = name.split()
    if not parts:
        return None
    last = parts[-1].lower()
    for key in data_dict:
        key_parts = key.split()
        if not key_parts:
            continue
        if key_parts[-1].lower() == last:
            return data_dict[key]
    return None

def clean(val):
    """Strip quotes and whitespace from CSV values."""
    return str(val).strip().strip('"').strip("'").strip()

# ─── Statcast Batter Data ─────────────────────────────────────────────────────
def get_statcast_batter_data():
    def fetch():
        try:
            url = (
                "https://baseballsavant.mlb.com/leaderboard/custom"
                "?year=2026&type=batter&filter=&sort=4&sortDir=desc"
                "&min=10&selections=xba,xslg,xwoba,xobp,"
                "hard_hit_percent,barrel_batted_rate&csv=true"
            )
            headers = {'User-Agent': 'Mozilla/5.0'}
            r = requests.get(url, headers=headers, timeout=15)
            if r.status_code != 200:
                print(f"Savant batter CSV status: {r.status_code}")
                return {}

            # Strip BOM if present
            text = r.text.lstrip('\ufeff')
            reader = csv.DictReader(io.StringIO(text))
            lookup = {}
            for row in reader:
                raw_name = clean(row.get('last_name, first_name', ''))
                if not raw_name or ',' not in raw_name:
                    continue
                last, first = raw_name.split(',', 1)
                name = f"{first.strip()} {last.strip()}"
                if not name.strip():
                    continue

                hh  = clean(row.get('hard_hit_percent', ''))
                bar = clean(row.get('barrel_batted_rate', ''))

                lookup[name] = {
                    'xwOBA':    clean(row.get('xwoba', 'N/A')),
                    'xBA':      clean(row.get('xba', 'N/A')),
                    'xSLG':     clean(row.get('xslg', 'N/A')),
                    'HardHit%': f"{hh}%" if hh else 'N/A',
                    'Barrel%':  f"{bar}%" if bar else 'N/A',
                }
            print(f"Savant batter data loaded: {len(lookup)} players")
            return lookup
        except Exception as e:
            print(f"Savant batter error: {e}")
            return {}
    return cached('statcast_batters', fetch)

# ─── Statcast Pitcher Data ────────────────────────────────────────────────────
def get_statcast_pitcher_data():
    def fetch():
        try:
            url = (
                "https://baseballsavant.mlb.com/leaderboard/custom"
                "?year=2026&type=pitcher&filter=&sort=4&sortDir=desc"
                "&min=10&selections=xera,xba,xslg,xwoba,"
                "hard_hit_percent,barrel_batted_rate,whiff_percent&csv=true"
            )
            headers = {'User-Agent': 'Mozilla/5.0'}
            r = requests.get(url, headers=headers, timeout=15)
            if r.status_code != 200:
                print(f"Savant pitcher CSV status: {r.status_code}")
                return {}

            text = r.text.lstrip('\ufeff')
            reader = csv.DictReader(io.StringIO(text))
            lookup = {}
            for row in reader:
                raw_name = clean(row.get('last_name, first_name', ''))
                if not raw_name or ',' not in raw_name:
                    continue
                last, first = raw_name.split(',', 1)
                name = f"{first.strip()} {last.strip()}"
                if not name.strip():
                    continue

                hh     = clean(row.get('hard_hit_percent', ''))
                bar    = clean(row.get('barrel_batted_rate', ''))
                whiff  = clean(row.get('whiff_percent', ''))

                lookup[name] = {
                    'xERA':     clean(row.get('xera', 'N/A')),
                    'xwOBA':    clean(row.get('xwoba', 'N/A')),
                    'Whiff%':   f"{whiff}%" if whiff else 'N/A',
                    'HardHit%': f"{hh}%" if hh else 'N/A',
                    'Barrel%':  f"{bar}%" if bar else 'N/A',
                }
            print(f"Savant pitcher data loaded: {len(lookup)} players")
            return lookup
        except Exception as e:
            print(f"Savant pitcher error: {e}")
            return {}
    return cached('statcast_pitchers', fetch)

# ─── Pitcher Stats via MLB API ────────────────────────────────────────────────
def get_pitcher_stats_mlb(player_id, pitcher_name):
    if not player_id:
        return None
    def fetch():
        try:
            url = (f"https://statsapi.mlb.com/api/v1/people/{player_id}/stats"
                   f"?stats=season&group=pitching&season=2026")
            data = requests.get(url, timeout=5).json()
            splits = data.get('stats', [{}])[0].get('splits', [])
            if not splits:
                return None
            s = splits[0]['stat']

            ip   = float(s.get('inningsPitched', 0))
            gs   = int(s.get('gamesStarted', 0))
            era  = float(s.get('era', 0))
            whip = float(s.get('whip', 0))
            k    = int(s.get('strikeOuts', 0))
            bb   = int(s.get('baseOnBalls', 0))
            bf   = int(s.get('battersFaced', 1))
            hr   = int(s.get('homeRuns', 0))

            k_pct  = round((k / bf) * 100, 1) if bf else 0
            bb_pct = round((bb / bf) * 100, 1) if bf else 0
            hr9    = round((hr / ip) * 9, 2) if ip else 0

            base = {
                'ERA':  round(era, 2),
                'WHIP': round(whip, 2),
                'K%':   f"{k_pct}%",
                'BB%':  f"{bb_pct}%",
                'HR/9': hr9,
                'IP':   round(ip, 1),
                'GS':   gs,
            }

            sc = fuzzy_lookup(pitcher_name, get_statcast_pitcher_data())
            if sc:
                base.update(sc)

            return base
        except Exception as e:
            print(f"MLB stats error for player {player_id}: {e}")
            return None
    return cached(f'pitcher_{player_id}', fetch)

# ─── Stat Color Coding ────────────────────────────────────────────────────────
def stat_color(stat, value):
    try:
        v = float(str(value).replace('%', ''))
    except:
        return ''
    rules = {
        'ERA':      ([(3.0,'elite'),(3.75,'good'),(4.5,'avg')],      False),
        'WHIP':     ([(1.1,'elite'),(1.25,'good'),(1.4,'avg')],      False),
        'K%':       ([(28,'elite'),(23,'good'),(18,'avg')],          True),
        'BB%':      ([(5,'elite'),(7,'good'),(9,'avg')],             False),
        'HR/9':     ([(0.8,'elite'),(1.1,'good'),(1.4,'avg')],       False),
        'xERA':     ([(3.0,'elite'),(3.75,'good'),(4.5,'avg')],      False),
        'Whiff%':   ([(30,'elite'),(24,'good'),(18,'avg')],          True),
        'xwOBA':    ([(0.290,'elite'),(0.320,'good'),(0.350,'avg')], False),
        'HardHit%': ([(45,'elite'),(40,'good'),(35,'avg')],          True),
        'Barrel%':  ([(10,'elite'),(7,'good'),(5,'avg')],            True),
    }
    if stat not in rules:
        return ''
    thresholds, higher_is_better = rules[stat]
    if higher_is_better:
        for t, cls in thresholds:
            if v >= t: return cls
        return 'bad'
    else:
        for t, cls in thresholds:
            if v <= t: return cls
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
           f"&hydrate=probablePitcher,lineups,team,venue,game,linescore")
    data = requests.get(url).json()

    batter_sc = get_statcast_batter_data()

    games = []
    for date_entry in data.get('dates', []):
        for game in date_entry.get('games', []):
            away_team = game['teams']['away']['team']['name']
            home_team = game['teams']['home']['team']['name']

            status         = game.get('status', {})
            abstract_state = status.get('abstractGameState', '')
            detailed_state = status.get('detailedState', '')

            away_score = game['teams']['away'].get('score', None)
            home_score = game['teams']['home'].get('score', None)

            inning_info = ''
            if abstract_state == 'Live':
                ls          = game.get('linescore', {})
                inning      = ls.get('currentInningOrdinal', '')
                half        = ls.get('inningHalf', '')
                inning_info = f"{half} {inning}"

            game_time_pt = ''
            raw = game.get('gameDate', '')
            if raw:
                try:
                    utc_dt = datetime.strptime(raw, '%Y-%m-%dT%H:%M:%SZ').replace(tzinfo=pytz.utc)
                    game_time_pt = utc_dt.astimezone(pacific).strftime('%-I:%M %p PT')
                except:
                    pass

            away_p_data = game['teams']['away'].get('probablePitcher', {})
            home_p_data = game['teams']['home'].get('probablePitcher', {})
            away_p      = away_p_data.get('fullName', 'TBD')
            home_p      = home_p_data.get('fullName', 'TBD')
            away_p_id   = away_p_data.get('id')
            home_p_id   = home_p_data.get('id')

            away_lineup, home_lineup = [], []
            away_lineup_sc, home_lineup_sc = [], []

            if 'lineups' in game:
                for p in game.get('lineups', {}).get('awayPlayers', []):
                    name = p.get('fullName', '')
                    if not name:
                        continue
                    away_lineup.append(name)
                    sc = fuzzy_lookup(name, batter_sc)
                    away_lineup_sc.append({'name': name, 'statcast': sc or {}})
                for p in game.get('lineups', {}).get('homePlayers', []):
                    name = p.get('fullName', '')
                    if not name:
                        continue
                    home_lineup.append(name)
                    sc = fuzzy_lookup(name, batter_sc)
                    home_lineup_sc.append({'name': name, 'statcast': sc or {}})

            games.append({
                'game_id':          game['gamePk'],
                'away_team':        away_team,
                'home_team':        home_team,
                'game_time':        game_time_pt,
                'abstract_state':   abstract_state,
                'detailed_state':   detailed_state,
                'away_score':       away_score,
                'home_score':       home_score,
                'inning_info':      inning_info,
                'away_pitcher':     away_p,
                'home_pitcher':     home_p,
                'away_p_stats':     get_pitcher_stats_mlb(away_p_id, away_p),
                'home_p_stats':     get_pitcher_stats_mlb(home_p_id, home_p),
                'away_lineup':      away_lineup,
                'home_lineup':      home_lineup,
                'away_lineup_sc':   away_lineup_sc,
                'home_lineup_sc':   home_lineup_sc,
                'lineup_confirmed': len(away_lineup) > 0 and len(home_lineup) > 0,
                'weather':          get_weather(home_team),
                'park_factor':      PARK_FACTORS.get(home_team, 100),
            })
    return games

# ─── HTML Helpers ─────────────────────────────────────────────────────────────
def render_pitcher_block(name, stats):
    if not stats:
        return (f'<div class="pitcher-block">'
                f'<p class="pname">⚾ {name}</p>'
                f'<p style="color:#888;font-size:0.8em">Stats unavailable</p>'
                f'</div>')

    base_keys = ['ERA', 'WHIP', 'K%', 'BB%', 'HR/9']
    sc_keys   = ['xERA', 'Whiff%', 'HardHit%', 'Barrel%', 'xwOBA']

    def stat_cell(k, v):
        return (f'<div class="sc"><span class="sl">{k}</span>'
                f'<span class="sv {stat_color(k, v)}">{v}</span></div>')

    base_grid = ''.join(stat_cell(k, stats.get(k, 'N/A')) for k in base_keys)
    sc_grid   = ''.join(stat_cell(k, stats.get(k, 'N/A')) for k in sc_keys)

    return (f'<div class="pitcher-block">'
            f'<p class="pname">⚾ {name} '
            f'<span style="color:#888;font-size:0.75em">({stats["GS"]} GS · {stats["IP"]} IP)</span></p>'
            f'<div class="sgrid">{base_grid}</div>'
            f'<div class="sgrid" style="margin-top:4px">{sc_grid}</div>'
            f'</div>')

def render_lineup_sc(lineup_sc, team_name):
    if not lineup_sc:
        return ''
    rows = ''
    for p in lineup_sc:
        sc = p.get('statcast', {})
        if sc:
            rows += (f'<tr><td>{p["name"]}</td>'
                     f'<td class="{stat_color("xwOBA", sc.get("xwOBA","N/A"))}">{sc.get("xwOBA","—")}</td>'
                     f'<td class="{stat_color("HardHit%", sc.get("HardHit%","N/A"))}">{sc.get("HardHit%","—")}</td>'
                     f'<td class="{stat_color("Barrel%", sc.get("Barrel%","N/A"))}">{sc.get("Barrel%","—")}</td>'
                     f'</tr>')
        else:
            rows += f'<tr><td>{p["name"]}</td><td>—</td><td>—</td><td>—</td></tr>'

    return (f'<div class="sc-table-wrap">'
            f'<p class="sc-title">📊 {team_name} Statcast</p>'
            f'<table class="sc-table">'
            f'<tr><th>Batter</th><th>xwOBA</th><th>HardHit%</th><th>Barrel%</th></tr>'
            f'{rows}</table></div>')

def render_score_banner(g):
    state = g['abstract_state']
    away  = g['away_team']
    home  = g['home_team']
    as_   = g['away_score']
    hs    = g['home_score']

    if state == 'Final':
        winner = away if as_ > hs else home
        return (f'<div class="score-banner final">'
                f'<span class="score-teams">{away} <span class="score-num">{as_}</span> '
                f'— <span class="score-num">{hs}</span> {home}</span>'
                f'<span class="score-label">FINAL · {winner} Win</span>'
                f'</div>')
    elif state == 'Live':
        return (f'<div class="score-banner live">'
                f'<span class="score-teams">{away} <span class="score-num">{as_}</span> '
                f'— <span class="score-num">{hs}</span> {home}</span>'
                f'<span class="score-label">🔴 LIVE · {g["inning_info"]}</span>'
                f'</div>')
    return ''

def render_card(g):
    state = g['abstract_state']
    pf    = g['park_factor']

    if state == 'Final':
        border_cls = 'final-game'
    elif state == 'Live':
        border_cls = 'live-game'
    elif g['lineup_confirmed']:
        border_cls = 'confirmed'
    else:
        border_cls = 'pending'

    pf_label = ('🔴 Hitter Friendly' if pf >= 105 else
                '🟢 Pitcher Friendly' if pf <= 95 else '⚪ Neutral')
    pf_cls   = ('hitter' if pf >= 105 else
                'pitcher-park' if pf <= 95 else 'neutral')

    weather_html = ''
    w = g.get('weather')
    if w:
        if not w['relevant']:
            weather_html = '<span class="badge dome">🏟️ Dome</span>'
        else:
            weather_html = (f'<span class="badge wx">'
                            f'🌤️ {w["label"]} · {w["temp"]} · 💨 {w["wind"]}</span>')

    score_html = render_score_banner(g)

    pitchers_html = f'''
    <div class="pr">
      {render_pitcher_block(g["away_pitcher"], g["away_p_stats"])}
      {render_pitcher_block(g["home_pitcher"], g["home_p_stats"])}
    </div>'''

    lineups_html = ''
    if g['lineup_confirmed']:
        away_sc_table = render_lineup_sc(g.get('away_lineup_sc', []), g['away_team'])
        home_sc_table = render_lineup_sc(g.get('home_lineup_sc', []), g['home_team'])
        away_li = ''.join(f'<li>{p}</li>' for p in g['away_lineup'])
        home_li = ''.join(f'<li>{p}</li>' for p in g['home_lineup'])
        lineups_html = (
            f'<details class="lu"><summary>📋 View Lineups + Statcast</summary>'
            f'<div class="lu-row">'
            f'<div><b>{g["away_team"]}</b><ol>{away_li}</ol></div>'
            f'<div><b>{g["home_team"]}</b><ol>{home_li}</ol></div>'
            f'</div>'
            f'<div class="sc-tables-row">{away_sc_table}{home_sc_table}</div>'
            f'</details>'
        )
    elif state not in ('Final', 'Live'):
        lineups_html = '<p style="color:#ff6b6b;font-size:0.82em">⏳ Lineup not yet confirmed</p>'

    if state == 'Final':
        header_right = '<span class="gt" style="color:#888">FINAL</span>'
    elif state == 'Live':
        header_right = f'<span class="gt" style="color:#ff4444">🔴 LIVE · {g["inning_info"]}</span>'
    else:
        header_right = f'<span class="gt">🕐 {g["game_time"]}</span>'

    return f'''
    <div class="game {border_cls}">
      <div class="gh">
        <h3>{g["away_team"]} @ {g["home_team"]}</h3>
        {header_right}
      </div>
      {score_html}
      <div class="badges">
        <span class="badge {pf_cls}">🏠 PF {pf} · {pf_label}</span>
        {weather_html}
      </div>
      {pitchers_html}
      {lineups_html}
    </div>'''

# ─── Routes ───────────────────────────────────────────────────────────────────
@app.route('/')
def index():
    pacific = pytz.timezone('America/Los_Angeles')
    now_pt  = datetime.now(pacific)
    games   = cached('games_list', get_todays_games)

    live      = [g for g in games if g['abstract_state'] == 'Live']
    confirmed = [g for g in games if g['abstract_state'] == 'Preview' and g['lineup_confirmed']]
    pending   = [g for g in games if g['abstract_state'] == 'Preview' and not g['lineup_confirmed']]
    final     = [g for g in games if g['abstract_state'] == 'Final']

    css = """
    *{box-sizing:border-box;margin:0;padding:0}
    body{font-family:Arial,sans-serif;background:#1a1a2e;color:#eee;padding:16px;max-width:1100px;margin:auto}
    h1{color:#ffd700;font-size:1.5em;margin-bottom:4px}
    h2{font-size:1.1em;margin:16px 0 8px}
    h3{color:#eee;font-size:1em}
    .sub{color:#888;font-size:0.82em;margin-bottom:16px}
    .game{background:#16213e;border:1px solid #0f3460;padding:14px;margin:10px 0;border-radius:10px}
    .confirmed{border-left:4px solid #00ff88}
    .pending{border-left:4px solid #ff6b6b}
    .live-game{border-left:4px solid #ff4444;background:#1e1020}
    .final-game{border-left:4px solid #444;opacity:0.75}
    .gh{display:flex;justify-content:space-between;align-items:center;margin-bottom:8px}
    .gt{color:#aaa;font-size:0.82em}
    .score-banner{display:flex;justify-content:space-between;align-items:center;
                  padding:8px 12px;border-radius:6px;margin-bottom:10px;font-size:0.9em}
    .score-banner.final{background:#1a1a1a;color:#aaa}
    .score-banner.live{background:#2a0a0a;color:#ff8888}
    .score-num{font-size:1.3em;font-weight:bold;color:#ffd700}
    .score-label{font-size:0.78em;color:#888}
    .score-banner.live .score-label{color:#ff6666}
    .badges{display:flex;flex-wrap:wrap;gap:6px;margin-bottom:10px}
    .badge{font-size:0.73em;padding:3px 8px;border-radius:12px}
    .hitter{background:#3d1515;color:#ff6b6b}
    .pitcher-park{background:#0d2e1a;color:#00ff88}
    .neutral{background:#1e1e3a;color:#aaa}
    .wx{background:#1a2a3a;color:#7ec8e3}
    .dome{background:#2a2a2a;color:#888}
    .pr{display:flex;gap:12px;flex-wrap:wrap;margin-bottom:10px}
    .pitcher-block{flex:1;min-width:220px;background:#0f1929;border-radius:8px;padding:10px}
    .pname{color:#ffd700;font-size:0.88em;margin-bottom:8px}
    .sgrid{display:grid;grid-template-columns:repeat(5,1fr);gap:4px}
    .sc{background:#1a2540;border-radius:4px;padding:4px 6px;text-align:center}
    .sl{display:block;font-size:0.62em;color:#888}
    .sv{display:block;font-size:0.88em;font-weight:bold}
    .elite{color:#00ff88}.good{color:#88ff44}.avg{color:#ffd700}.bad{color:#ff6b6b}
    .lu{margin-top:8px}
    .lu summary{cursor:pointer;color:#aaa;font-size:0.83em;padding:4px 0}
    .lu-row{display:flex;gap:16px;flex-wrap:wrap;margin-top:8px;font-size:0.78em;color:#ccc}
    .lu-row ol{padding-left:18px;margin-top:4px}
    .lu-row li{margin:2px 0}
    .sc-tables-row{display:flex;gap:16px;flex-wrap:wrap;margin-top:12px}
    .sc-table-wrap{flex:1;min-width:240px}
    .sc-title{color:#7ec8e3;font-size:0.8em;margin-bottom:6px}
    .sc-table{width:100%;border-collapse:collapse;font-size:0.75em}
    .sc-table th{color:#888;text-align:left;padding:3px 6px;border-bottom:1px solid #1a2540}
    .sc-table td{padding:3px 6px;border-bottom:1px solid #0f1929}
    .sc-table tr:hover td{background:#1a2540}
    """

    def section(title, color, items):
        if not items:
            return ''
        return (f'<h2 style="color:{color}">{title} ({len(items)} games)</h2>'
                + ''.join(render_card(g) for g in items))

    html = f"""<!DOCTYPE html><html>
    <head>
      <title>MLB V2 – {now_pt.strftime('%b %d, %Y')}</title>
      <meta name="viewport" content="width=device-width,initial-scale=1">
      <style>{css}</style>
    </head>
    <body>
      <h1>⚾ MLB V2 Picks Dashboard</h1>
      <p class="sub">Last updated: {now_pt.strftime('%I:%M %p PT')} · {now_pt.strftime('%b %d, %Y')}</p>
      {section('🔴 Live Now', '#ff4444', live)}
      {section('✅ Lineups Confirmed', '#00ff88', confirmed)}
      {section('⏳ Lineups Pending', '#ff6b6b', pending)}
      {section('☑️ Completed', '#555', final)}
    </body></html>"""
    return html

@app.route('/api/games')
def api_games():
    games = cached('games_list', get_todays_games)
    return jsonify(games)

@app.route('/robots.txt')
def robots():
    return "User-agent: *\nAllow: /", 200, {'Content-Type': 'text/plain'}

@app.route('/debug/savant')
def debug_savant():
    try:
        url = (
            "https://baseballsavant.mlb.com/leaderboard/custom"
            "?year=2026&type=batter&filter=&sort=4&sortDir=desc"
            "&min=10&selections=xba,xslg,xwoba,xobp,"
            "hard_hit_percent,barrel_batted_rate&csv=true"
        )
        headers = {'User-Agent': 'Mozilla/5.0'}
        r = requests.get(url, headers=headers, timeout=15)
        return f"Status: {r.status_code}<br><pre>{r.text[:3000]}</pre>"
    except Exception as e:
        return f"Error: {e}"

if __name__ == '__main__':
    app.run(debug=True)
