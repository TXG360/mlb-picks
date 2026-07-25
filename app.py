from flask import Flask, jsonify
import requests
from datetime import datetime, timedelta
import pytz
import csv
import io
import logging

app = Flask(__name__)
logging.basicConfig(level=logging.INFO, format='%(levelname)s: %(message)s')
logger = logging.getLogger(__name__)

# ─── Cache & Global State ─────────────────────────────────────────────────────
_cache = {}
CACHE_TTL = 3600
_game_states = {} 

def cached(key, fn, ttl=CACHE_TTL):
    now = datetime.utcnow().timestamp()
    if key in _cache and now - _cache[key]['ts'] < ttl:
        return _cache[key]['data']
    result = fn()
    _cache[key] = {'data': result, 'ts': now}
    return result

# ─── Park Factors & Teams ─────────────────────────────────────────────────────
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

INDOOR_TEAMS = {'Tampa Bay Rays', 'Toronto Blue Jays', 'Milwaukee Brewers', 'Minnesota Twins', 'Houston Astros', 'Arizona Diamondbacks', 'Seattle Mariners', 'Miami Marlins'}
TEAM_CITIES = {
    'Colorado Rockies': 'Denver+CO', 'Cincinnati Reds': 'Cincinnati+OH', 'Boston Red Sox': 'Boston+MA', 'Texas Rangers': 'Arlington+TX',
    'Philadelphia Phillies': 'Philadelphia+PA', 'Chicago Cubs': 'Chicago+IL', 'Atlanta Braves': 'Cumberland+GA', 'Baltimore Orioles': 'Baltimore+MD',
    'Los Angeles Angels': 'Anaheim+CA', 'New York Yankees': 'Bronx+NY', 'Kansas City Royals': 'Kansas+City+MO', 'Detroit Tigers': 'Detroit+MI',
    'Cleveland Guardians': 'Cleveland+OH', 'Washington Nationals': 'Washington+DC', 'Chicago White Sox': 'Chicago+IL', 'Los Angeles Dodgers': 'Los+Angeles+CA',
    'San Francisco Giants': 'San+Francisco+CA', 'Pittsburgh Pirates': 'Pittsburgh+PA', 'St. Louis Cardinals': 'St+Louis+MO', 'New York Mets': 'Queens+NY',
    'San Diego Padres': 'San+Diego+CA', 'Oakland Athletics': 'Sacramento+CA',
}

# ─── Fuzzy Lookup & Clean ─────────────────────────────────────────────────────
def fuzzy_lookup(name, data_dict):
    if not data_dict or not name: return None
    if name in data_dict: return data_dict[name]
    parts = name.split()
    if not parts: return None
    last = parts[-1].lower()
    for key in data_dict:
        if key.split() and key.split()[-1].lower() == last:
            return data_dict[key]
    return None

def clean(val): return str(val).strip().strip('"').strip("'").strip()

# ─── Statcast Data (CSV Parsing) ──────────────────────────────────────────────
def get_statcast_batter_data():
    def fetch():
        try:
            url = "https://baseballsavant.mlb.com/leaderboard/custom?year=2026&type=batter&filter=&sort=4&sortDir=desc&min=10&selections=xba,xslg,xwoba,hard_hit_percent,barrel_batted_rate&csv=true"
            r = requests.get(url, headers={'User-Agent': 'Mozilla/5.0'}, timeout=15)
            if r.status_code != 200: return {}
            reader = csv.DictReader(io.StringIO(r.text.lstrip('\ufeff')))
            lookup = {}
            for row in reader:
                raw_name = clean(row.get('last_name, first_name', ''))
                if ',' not in raw_name: continue
                last, first = raw_name.split(',', 1)
                name = f"{first.strip()} {last.strip()}"
                lookup[name] = {
                    'xwOBA': clean(row.get('xwoba', 'N/A')), 'xBA': clean(row.get('xba', 'N/A')),
                    'HardHit%': f"{clean(row.get('hard_hit_percent', ''))}%",
                    'Barrel%': f"{clean(row.get('barrel_batted_rate', ''))}%"
                }
            return lookup
        except: return {}
    return cached('statcast_batters', fetch)

def get_statcast_pitcher_data():
    def fetch():
        try:
            url = "https://baseballsavant.mlb.com/leaderboard/custom?year=2026&type=pitcher&filter=&sort=4&sortDir=desc&min=10&selections=xera,xwoba,hard_hit_percent,barrel_batted_rate,whiff_percent&csv=true"
            r = requests.get(url, headers={'User-Agent': 'Mozilla/5.0'}, timeout=15)
            if r.status_code != 200: return {}
            reader = csv.DictReader(io.StringIO(r.text.lstrip('\ufeff')))
            lookup = {}
            for row in reader:
                raw_name = clean(row.get('last_name, first_name', ''))
                if ',' not in raw_name: continue
                last, first = raw_name.split(',', 1)
                name = f"{first.strip()} {last.strip()}"
                lookup[name] = {
                    'xERA': clean(row.get('xera', 'N/A')), 'xwOBA': clean(row.get('xwoba', 'N/A')),
                    'Whiff%': f"{clean(row.get('whiff_percent', ''))}%",
                    'HardHit%': f"{clean(row.get('hard_hit_percent', ''))}%",
                    'Barrel%': f"{clean(row.get('barrel_batted_rate', ''))}%"
                }
            return lookup
        except: return {}
    return cached('statcast_pitchers', fetch)

def get_pitcher_stats_mlb(player_id, pitcher_name, sc_data):
    if not player_id: return None
    def fetch():
        try:
            data = requests.get(f"https://statsapi.mlb.com/api/v1/people/{player_id}/stats?stats=season&group=pitching&season=2026", timeout=5).json()
            s = data.get('stats', [{}])[0].get('splits', [])[0]['stat']
            ip, gs, bf = float(s.get('inningsPitched', 0)), int(s.get('gamesStarted', 0)), int(s.get('battersFaced', 1))
            base = {
                'ERA': round(float(s.get('era', 0)), 2), 'WHIP': round(float(s.get('whip', 0)), 2),
                'K%': f"{round((int(s.get('strikeOuts', 0))/bf)*100, 1)}%", 'BB%': f"{round((int(s.get('baseOnBalls', 0))/bf)*100, 1)}%",
                'HR/9': round((int(s.get('homeRuns', 0))/ip)*9, 2) if ip else 0, 'IP': round(ip, 1), 'GS': gs,
            }
            sc = fuzzy_lookup(pitcher_name, sc_data)
            if sc: base.update(sc)
            return base
        except: return None
    return cached(f'pitcher_{player_id}', fetch)

# ─── V4.2 Fatigue Engine (3-Day Lookback) ─────────────────────────────────────
def get_league_fatigue():
    """
    Scrapes the MLB schedule for the past 3 days and hydrates the boxscores 
    to map out daily pitch counts for every reliever.
    """
    def fetch():
        try:
            pacific = pytz.timezone('America/Los_Angeles')
            today = datetime.now(pacific)
            d1 = (today - timedelta(days=1)).strftime('%Y-%m-%d')
            d2 = (today - timedelta(days=2)).strftime('%Y-%m-%d')
            d3 = (today - timedelta(days=3)).strftime('%Y-%m-%d')
            
            url = f"https://statsapi.mlb.com/api/v1/schedule?sportId=1&startDate={d3}&endDate={d1}&hydrate=boxscore"
            data = requests.get(url, timeout=10).json()
            
            fatigue = {} # { player_id: { date_str: pitches } }
            for date_entry in data.get('dates', []):
                d_str = date_entry['date']
                for game in date_entry.get('games', []):
                    box = game.get('boxscore', {})
                    if not box: continue
                    for side in ['away', 'home']:
                        for p_id, p_info in box['teams'][side]['players'].items():
                            pitches = p_info.get('stats', {}).get('pitching', {}).get('numberOfPitches', 0)
                            if pitches > 0:
                                pid = p_info['person']['id']
                                if pid not in fatigue: fatigue[pid] = {}
                                fatigue[pid][d_str] = fatigue[pid].get(d_str, 0) + pitches
            return {'d1': d1, 'd2': d2, 'd3': d3, 'data': fatigue}
        except: return {}
    return cached('league_fatigue', fetch, ttl=3600)

def get_full_roster_metrics(team_id, starter_name, lineup_names, pitcher_sc, batter_sc):
    """
    V4.2: Excludes fatigued pitchers, isolates high-leverage arms, and calculates true bench depth.
    """
    def fetch():
        try: return requests.get(f"https://statsapi.mlb.com/api/v1/teams/{team_id}/roster/Active?season=2026", timeout=5).json()
        except: return {}
        
    roster = cached(f'roster_{team_id}', fetch).get('roster', [])
    fatigue_sys = get_league_fatigue()
    fatigue_data = fatigue_sys.get('data', {})
    d1, d2, d3 = fatigue_sys.get('d1'), fatigue_sys.get('d2'), fatigue_sys.get('d3')
    
    available_bp_xeras = []
    fatigued_count = 0
    bench_xwobas = []
    
    for player in roster:
        name = player['person']['fullName']
        pos = player['position']['abbreviation']
        pid = player['person']['id']
        
        # Bullpen Logic
        if pos in ['P', 'TWP'] and name != starter_name:
            stats = fuzzy_lookup(name, pitcher_sc)
            if stats and stats.get('xERA') not in ('N/A', None, ''):
                # Check Fatigue
                f_log = fatigue_data.get(pid, {})
                p1, p2, p3 = f_log.get(d1, 0), f_log.get(d2, 0), f_log.get(d3, 0)
                
                # Rule 1: Threw > 25 pitches yesterday
                # Rule 2: Pitched back-to-back days (yesterday and day before)
                # Rule 3: Threw > 45 pitches total in last 3 days
                if p1 > 25 or (p1 > 0 and p2 > 0) or (p1 + p2 + p3) > 45:
                    fatigued_count += 1
                    continue # Exclude from available pool
                    
                try: available_bp_xeras.append(float(stats['xERA']))
                except: pass
                
        # Bench Bats Logic
        elif pos not in ['P', 'TWP'] and name not in lineup_names:
            stats = fuzzy_lookup(name, batter_sc)
            if stats and stats.get('xwOBA') not in ('N/A', None, ''):
                try: bench_xwobas.append(float(stats['xwOBA']))
                except: pass
                
    # LEVERAGE FILTER: Sort the rested bullpen arms by xERA and only average the top 4 (A-Bullpen)
    available_bp_xeras.sort()
    high_leverage_arms = available_bp_xeras[:4] if len(available_bp_xeras) >= 4 else available_bp_xeras
    
    bp_xera = round(sum(high_leverage_arms)/len(high_leverage_arms), 2) if high_leverage_arms else 4.20
    bench_xwoba = round(sum(bench_xwobas)/len(bench_xwobas), 3) if bench_xwobas else 0.300
    
    return bp_xera, bench_xwoba, len(high_leverage_arms), fatigued_count

# ─── V4.2 Core Logic Helpers ──────────────────────────────────────────────────
def get_top_4_xwoba(lineup_sc):
    if not lineup_sc or len(lineup_sc) < 4: return 0.0
    vals = [float(p['statcast']['xwOBA']) for p in lineup_sc[:4] if p.get('statcast', {}).get('xwOBA') not in ('N/A', '-', '', None)]
    return sum(vals) / len(vals) if vals else 0.0

def evaluate_buzzsaw(opp_top_4_xwoba, base_required_delta=0.75):
    if opp_top_4_xwoba >= 0.365: return 1.60
    elif opp_top_4_xwoba >= 0.350: return 1.15
    return base_required_delta

def blended_pitching_metric_v4(starter_xera, bullpen_xera):
    if starter_xera in ('N/A', None, ''): return None
    try: return ((5.0 / 9.0) * float(starter_xera)) + ((4.0 / 9.0) * bullpen_xera)
    except: return None

# ─── Game Loop ────────────────────────────────────────────────────────────────
def get_todays_games():
    pacific = pytz.timezone('America/Los_Angeles')
    today = datetime.now(pacific).strftime('%Y-%m-%d')
    url = f"https://statsapi.mlb.com/api/v1/schedule?sportId=1&date={today}&hydrate=probablePitcher,lineups,team,venue,game,linescore"
    
    data = requests.get(url).json()
    batter_sc = get_statcast_batter_data()
    pitcher_sc = get_statcast_pitcher_data()
    games = []
    
    for date_entry in data.get('dates', []):
        for game in date_entry.get('games', []):
            away_team, home_team = game['teams']['away']['team']['name'], game['teams']['home']['team']['name']
            away_id, home_id = game['teams']['away']['team']['id'], game['teams']['home']['team']['id']
            status = game.get('status', {})
            abstract_state = status.get('abstractGameState', '')
            away_p = game['teams']['away'].get('probablePitcher', {}).get('fullName', 'TBD')
            home_p = game['teams']['home'].get('probablePitcher', {}).get('fullName', 'TBD')
            
            away_p_stats = get_pitcher_stats_mlb(game['teams']['away'].get('probablePitcher', {}).get('id'), away_p, pitcher_sc)
            home_p_stats = get_pitcher_stats_mlb(game['teams']['home'].get('probablePitcher', {}).get('id'), home_p, pitcher_sc)
            
            away_lineup, home_lineup, away_lineup_sc, home_lineup_sc = [], [], [], []
            if 'lineups' in game:
                for p in game['lineups'].get('awayPlayers', []):
                    if p.get('fullName'):
                        away_lineup.append(p['fullName'])
                        away_lineup_sc.append({'name': p['fullName'], 'statcast': fuzzy_lookup(p['fullName'], batter_sc) or {}})
                for p in game['lineups'].get('homePlayers', []):
                    if p.get('fullName'):
                        home_lineup.append(p['fullName'])
                        home_lineup_sc.append({'name': p['fullName'], 'statcast': fuzzy_lookup(p['fullName'], batter_sc) or {}})

            # ─── V4.2 Fatigue Resolution ───
            a_bp_xera, a_bench, a_rested, a_tired = get_full_roster_metrics(away_id, away_p, away_lineup, pitcher_sc, batter_sc)
            h_bp_xera, h_bench, h_rested, h_tired = get_full_roster_metrics(home_id, home_p, home_lineup, pitcher_sc, batter_sc)

            game_id = game['gamePk']
            cached_state = _game_states.get(game_id)
            v3_pick, v3_color, v3_reason = "Awaiting Lineups/Pitchers", "#888", "Missing statcast data for calculation"

            if cached_state and (abstract_state in ['Live', 'Final'] or (abstract_state == 'Preview' and hash(tuple(away_lineup+home_lineup)) == cached_state['lineups_hash'])):
                v3_pick, v3_color, v3_reason = cached_state['v3_data'].values()
            else:
                a_blended = blended_pitching_metric_v4(away_p_stats.get('xERA') if away_p_stats else None, a_bp_xera)
                h_blended = blended_pitching_metric_v4(home_p_stats.get('xERA') if home_p_stats else None, h_bp_xera)

                if a_blended and h_blended and len(away_lineup) >= 4 and len(home_lineup) >= 4:
                    away_adv, home_adv = h_blended - a_blended, a_blended - h_blended
                    if a_bench < 0.280: away_adv -= 0.15 # V4.2 Bench Depth Penalty
                    if h_bench < 0.280: home_adv -= 0.15

                    req_away, req_home = evaluate_buzzsaw(get_top_4_xwoba(home_lineup_sc)), evaluate_buzzsaw(get_top_4_xwoba(away_lineup_sc))
                    
                    if away_adv >= req_away: v3_pick, v3_color, v3_reason = f"🟢 V4.2 PLAY {away_team} ML", "#00ff88", f"+{away_adv:.2f} Adjusted Edge"
                    elif home_adv >= req_home: v3_pick, v3_color, v3_reason = f"🟢 V4.2 PLAY {home_team} ML", "#00ff88", f"+{home_adv:.2f} Adjusted Edge"
                    elif away_adv >= 0.40: v3_pick, v3_color, v3_reason = f"🟡 V4.2 LEAN {away_team} +1.5", "#ffd700", f"+{away_adv:.2f} Adjusted Edge"
                    elif home_adv >= 0.40: v3_pick, v3_color, v3_reason = f"🟡 V4.2 LEAN {home_team} +1.5", "#ffd700", f"+{home_adv:.2f} Adjusted Edge"
                    else: v3_pick, v3_color, v3_reason = f"🛑 SKIP", "#ff6b6b", "Metrics dead even"

            _game_states[game_id] = {'state': abstract_state, 'lineups_hash': hash(tuple(away_lineup+home_lineup)), 'v3_data': {'pick': v3_pick, 'color': v3_color, 'reason': v3_reason}}

            games.append({
                'game_id': game_id, 'away_team': away_team, 'home_team': home_team, 'abstract_state': abstract_state,
                'away_score': game['teams']['away'].get('score'), 'home_score': game['teams']['home'].get('score'),
                'away_pitcher': away_p, 'home_pitcher': home_p, 'away_p_stats': away_p_stats, 'home_p_stats': home_p_stats,
                'a_bp_xera': a_bp_xera, 'a_bench': a_bench, 'a_rested': a_rested, 'a_tired': a_tired,
                'h_bp_xera': h_bp_xera, 'h_bench': h_bench, 'h_rested': h_rested, 'h_tired': h_tired,
                'lineup_confirmed': len(away_lineup) > 0 and len(home_lineup) > 0,
                'v3_pick': v3_pick, 'v3_color': v3_color, 'v3_reason': v3_reason,
            })
    return games

# ─── HTML Frontend ────────────────────────────────────────────────────────────
def stat_color(stat, value):
    try: v = float(str(value).replace('%', ''))
    except: return ''
    if stat == 'xERA': return 'elite' if v<=3.0 else 'good' if v<=3.75 else 'avg' if v<=4.5 else 'bad'
    if stat == 'xwOBA': return 'elite' if v>=0.350 else 'good' if v>=0.320 else 'avg' if v>=0.290 else 'bad'
    return ''

def render_roster_metrics(name, bp_xera, bench, rested, tired):
    return (f'<div style="margin-top:8px; padding:8px; background:#1e1e3a; border-radius:4px; font-size:0.85em; color:#ddd;">'
            f'<div style="margin-bottom:6px"><b>{name} Late Innings:</b></div>'
            f'<div style="display:flex; justify-content:space-between; margin-bottom:4px;">'
            f'<span>🔥 Available A-Bullpen xERA: <b class="{stat_color("xERA", bp_xera)}">{bp_xera}</b></span>'
            f'<span style="color:#ff6b6b; font-size:0.85em">({tired} Fatigued/Out)</span>'
            f'</div>'
            f'<div><span>🪵 Bench Pinch-Hit xwOBA: <b class="{stat_color("xwOBA", bench)}">{bench}</b></span></div>'
            f'</div>')

def render_pitcher_block(name, stats):
    if not stats: return f'<div class="pitcher-block"><p class="pname">⚾ {name}</p></div>'
    return f'<div class="pitcher-block"><p class="pname">⚾ {name} ({stats["GS"]} GS)</p><div class="sgrid"><div class="sc"><span class="sl">xERA</span><span class="sv {stat_color("xERA", stats.get("xERA"))}">{stats.get("xERA","N/A")}</span></div></div></div>'

def render_card(g):
    b_cls = 'final-game' if g['abstract_state'] == 'Final' else 'live-game' if g['abstract_state'] == 'Live' else 'confirmed' if g['lineup_confirmed'] else 'pending'
    return f'''
    <div class="game {b_cls}">
      <div class="gh"><h3>{g["away_team"]} @ {g["home_team"]}</h3></div>
      <div class="v3-banner" style="border-color:{g.get('v3_color', '#888')}">
        <span class="v3-pick" style="color:{g.get('v3_color', '#888')}">{g.get('v3_pick', '')}</span>
        <span class="v3-reason">{g.get('v3_reason', '')}</span>
      </div>
      <div class="pr">
        {render_pitcher_block(g["away_pitcher"], g["away_p_stats"])}
        {render_pitcher_block(g["home_pitcher"], g["home_p_stats"])}
      </div>
      <div class="pr">
        <div style="flex:1">{render_roster_metrics(g["away_team"], g["a_bp_xera"], g["a_bench"], g["a_rested"], g["a_tired"])}</div>
        <div style="flex:1">{render_roster_metrics(g["home_team"], g["h_bp_xera"], g["h_bench"], g["h_rested"], g["h_tired"])}</div>
      </div>
    </div>'''

@app.route('/')
def index():
    games = cached('games_list', get_todays_games)
    css = "*{box-sizing:border-box} body{font-family:Arial,sans-serif;background:#1a1a2e;color:#eee;padding:16px;max-width:1100px;margin:auto} h1{color:#ffd700} .game{background:#16213e;border:1px solid #0f3460;padding:14px;margin:10px 0;border-radius:10px} .confirmed{border-left:4px solid #00ff88} .pending{border-left:4px solid #ff6b6b} .live-game{border-left:4px solid #ff4444} .final-game{border-left:4px solid #444} .v3-banner{background:#0a0f1a;border:1px solid;padding:10px;margin-bottom:12px;border-radius:6px;display:flex;justify-content:space-between} .pr{display:flex;gap:12px} .pitcher-block{flex:1;background:#0f1929;padding:10px;border-radius:8px} .sgrid{display:grid;grid-template-columns:repeat(5,1fr);gap:4px} .sc{background:#1a2540;padding:4px;text-align:center} .sl{font-size:0.6em;color:#888;display:block} .sv{font-size:0.85em;font-weight:bold} .elite{color:#00ff88} .bad{color:#ff6b6b}"
    return f"<!DOCTYPE html><html><head><style>{css}</style></head><body><h1>⚾ MLB V4.2 Fatigue Engine</h1>" + ''.join(render_card(g) for g in games) + "</body></html>"

@app.route('/api/games')
def api_games(): return jsonify(cached('games_list', get_todays_games))

if __name__ == '__main__': app.run(debug=True)


