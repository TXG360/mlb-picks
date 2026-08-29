from flask import Flask, jsonify, send_file
import requests
from datetime import datetime, timedelta
import pytz
import csv
import io
import logging
import re
import os

app = Flask(__name__)

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(levelname)s: %(message)s')
logger = logging.getLogger(__name__)

# ─── API KEYS & CONFIG ────────────────────────────────────────────────────────
ODDS_API_KEYS = [
    "toa_live_3ofpyj5mayimyz5t", 
    "toa_live_fuft13uimb8wwxji", 
    "toa_live_o04ab9ku89lj2k0x"  
]

# ─── Cache & Global State ─────────────────────────────────────────────────────
_cache = {}
CACHE_TTL = 3600
_game_states = {} 
LOG_FILE = "v5_algorithm_ledger.csv"

CURRENT_YEAR = datetime.now().year

def cached(key, fn, ttl=CACHE_TTL):
    now = datetime.utcnow().timestamp()
    if key in _cache and now - _cache[key]['ts'] < ttl:
        return _cache[key]['data']
    result = fn()
    _cache[key] = {'data': result, 'ts': now}
    return result

# ─── CSV Auto-Logger ──────────────────────────────────────────────────────────
def init_csv_logger():
    """Creates the CSV file with headers if it doesn't exist."""
    if not os.path.exists(LOG_FILE):
        with open(LOG_FILE, mode='w', newline='', encoding='utf-8') as file:
            writer = csv.writer(file)
            writer.writerow([
                "Date", "Game ID", "Away Team", "Home Team", 
                "Away Score", "Home Score", "Result State",
                "Algorithm Pick", "Reason / Edge", "Color Code"
            ])

def log_final_games(games_list):
    """Logs completely finished games to a CSV file for historical tracking."""
    init_csv_logger()
    
    logged_ids = set()
    try:
        with open(LOG_FILE, mode='r', encoding='utf-8') as file:
            reader = csv.reader(file)
            next(reader, None) # Skip header
            for row in reader:
                if row and len(row) > 1:
                    logged_ids.add(row[1])
    except Exception as e:
        logger.error(f"Error reading CSV: {e}")

    new_logs = []
    pacific = pytz.timezone('America/Los_Angeles')
    today_str = datetime.now(pacific).strftime('%Y-%m-%d')

    for g in games_list:
        game_id = str(g.get('game_id'))
        state = g.get('abstract_state')

        if state == 'Final' and game_id not in logged_ids:
            new_logs.append([
                today_str,
                game_id,
                g.get('away_team'),
                g.get('home_team'),
                g.get('away_score', 0),
                g.get('home_score', 0),
                state,
                g.get('v3_pick', 'No Pick'),
                g.get('v3_reason', 'None'),
                g.get('v3_color', '')
            ])

    if new_logs:
        try:
            with open(LOG_FILE, mode='a', newline='', encoding='utf-8') as file:
                writer = csv.writer(file)
                writer.writerows(new_logs)
            logger.info(f"✅ Successfully logged {len(new_logs)} completed games to the database.")
        except Exception as e:
            logger.error(f"Error writing to CSV: {e}")

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

# ─── LIVE ODDS ENGINE (Auto-Rotating Keys & Smart Sleep) ──────────────────────
def normalize_team_name(name):
    if not name: return name
    for full_name in PARK_FACTORS.keys():
        if name.lower() == full_name.lower():
            return full_name
        if name.split()[-1].lower() == full_name.split()[-1].lower():
            if "Sox" in name: 
                if "White" in name and "White" in full_name: return full_name
                if "Red" in name and "Red" in full_name: return full_name
            else:
                return full_name
    return name

def get_live_odds():
    def fetch():
        pacific = pytz.timezone('America/Los_Angeles')
        now_pt = datetime.now(pacific)
        
        # 🌙 SMART SLEEP MODE: Pause requests between 1 AM and 8 AM PT.
        if 1 <= now_pt.hour < 8:
            logger.info("🌙 Smart Sleep Active: Pausing odds API requests until 8 AM PT.")
            return _cache.get('live_odds', {}).get('data', {}) if 'live_odds' in _cache else {}

        for api_key in ODDS_API_KEYS:
            if not api_key: continue
            
            try:
                # 🛑 FIX: Using exact endpoint and Header Auth requested by Neil's email
                url = "https://api.theoddsapi.com/odds/?sport_key=baseball_mlb"
                headers = {"x-api-key": api_key}
                
                r = requests.get(url, headers=headers, timeout=10)
                
                if r.status_code in [429, 401, 403]:
                    logger.warning(f"Key {api_key[:8]}... failed (limit/invalid). Rotating to next.")
                    continue 
                    
                data = r.json()
                odds_map = {}
                
                games = data.get('data', []) if isinstance(data, dict) else data
                if not isinstance(games, list):
                    continue
                
                for game in games:
                    books = game.get('books', [])
                    if not books: continue
                    
                    h2h_books = [b for b in books if b.get('market') == 'h2h']
                    if not h2h_books: continue
                    
                    preferred_book = None
                    for b in h2h_books:
                        if b.get('book') in ['fanduel', 'draftkings']:
                            preferred_book = b
                            break
                    
                    if not preferred_book:
                        preferred_book = h2h_books[0]
                        
                    outcomes = preferred_book.get('outcomes', [])
                    for outcome in outcomes:
                        raw_team_name = outcome.get('name')
                        price = outcome.get('price')
                        if raw_team_name and price is not None:
                            normalized_name = normalize_team_name(raw_team_name)
                            odds_map[normalized_name] = price
                                    
                return odds_map 
                
            except Exception as e:
                logger.error(f"Live Odds API Failed on key {api_key[:8]}: {e}")
                
        return {}
        
    return cached('live_odds', fetch, ttl=1800) 

# ─── Fuzzy Name Lookup & Normalization (V5 UPGRADE) ────────────
def normalize_player_name(name):
    if not name: return ""
    name = re.sub(r'\b(Jr\.|Sr\.|II|III|IV)\b', '', name, flags=re.IGNORECASE)
    name = re.sub(r"['\-\.]", '', name)
    return ' '.join(name.lower().split())

def fuzzy_lookup(name, data_dict):
    if not data_dict or not name: return None
    
    target_name = normalize_player_name(name)
    
    for key in data_dict:
        if normalize_player_name(key) == target_name: 
            return data_dict[key]
            
    parts = target_name.split()
    if not parts: return None
    
    first_init, last_name = parts[0][0], parts[-1]
    
    for key in data_dict:
        k_parts = normalize_player_name(key).split()
        if len(k_parts) > 1 and k_parts[0][0] == first_init and k_parts[-1] == last_name: 
            return data_dict[key]
            
    for key in data_dict:
        k_parts = normalize_player_name(key).split()
        if k_parts and k_parts[-1] == last_name: 
            return data_dict[key]
            
    return None

def clean(val): 
    return str(val).strip().strip('"').strip("'").strip()

# ─── Statcast Data (CSV Parsing) ──────────────────────────────────────────────
def get_statcast_batter_data():
    def fetch():
        try:
            url = f"https://baseballsavant.mlb.com/leaderboard/custom?year={CURRENT_YEAR}&type=batter&filter=&sort=4&sortDir=desc&min=10&selections=xba,xslg,xwoba,hard_hit_percent,barrel_batted_rate&csv=true"
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
                    'xSLG': clean(row.get('xslg', 'N/A')),
                    'HardHit%': f"{clean(row.get('hard_hit_percent', ''))}%", 
                    'Barrel%': f"{clean(row.get('barrel_batted_rate', ''))}%"
                }
            return lookup
        except: return {}
    return cached('statcast_batters', fetch)

def get_statcast_pitcher_data():
    def fetch():
        try:
            url = f"https://baseballsavant.mlb.com/leaderboard/custom?year={CURRENT_YEAR}&type=pitcher&filter=&sort=4&sortDir=desc&min=10&selections=xera,xwoba,hard_hit_percent,barrel_batted_rate,whiff_percent&csv=true"
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
            url = f"https://statsapi.mlb.com/api/v1/people/{player_id}/stats?stats=season&group=pitching&season={CURRENT_YEAR}"
            data = requests.get(url, timeout=5).json()
            s = data.get('stats', [{}])[0].get('splits', [])[0]['stat']
            ip, gs, bf = float(s.get('inningsPitched', 0)), int(s.get('gamesStarted', 0)), int(s.get('battersFaced', 1))
            k, bb, hr = int(s.get('strikeOuts', 0)), int(s.get('baseOnBalls', 0)), int(s.get('homeRuns', 0))
            base = {
                'ERA': round(float(s.get('era', 0)), 2), 'WHIP': round(float(s.get('whip', 0)), 2),
                'K%': f"{round((k/bf)*100, 1)}%" if bf else "0%", 'BB%': f"{round((bb/bf)*100, 1)}%" if bf else "0%",
                'HR/9': round((hr/ip)*9, 2) if ip else 0, 'IP': round(ip, 1), 'GS': gs,
            }
            sc = fuzzy_lookup(pitcher_name, sc_data)
            if sc: base.update(sc)
            return base
        except: return None
    return cached(f'pitcher_{player_id}', fetch)

# ─── V4.5 Fatigue Engine & Roster Metrics ─────────────────────────────────────
def get_league_fatigue():
    def fetch():
        try:
            pacific = pytz.timezone('America/Los_Angeles')
            today = datetime.now(pacific)
            d1, d2, d3 = [(today - timedelta(days=i)).strftime('%Y-%m-%d') for i in [1, 2, 3]]
            url = f"https://statsapi.mlb.com/api/v1/schedule?sportId=1&startDate={d3}&endDate={d1}&hydrate=boxscore"
            data = requests.get(url, timeout=10).json()
            
            fatigue = {}
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
    def fetch():
        try: return requests.get(f"https://statsapi.mlb.com/api/v1/teams/{team_id}/roster/Active?season={CURRENT_YEAR}", timeout=5).json()
        except: return {}
        
    roster = cached(f'roster_{team_id}', fetch).get('roster', [])
    fatigue_sys = get_league_fatigue()
    f_data, d1, d2, d3 = fatigue_sys.get('data', {}), fatigue_sys.get('d1'), fatigue_sys.get('d2'), fatigue_sys.get('d3')
    
    bp_xeras, bench_xwobas = [], []
    fatigued_count = 0
    
    for player in roster:
        name = player['person']['fullName']
        pos = player['position']['abbreviation']
        pid = player['person']['id']
        
        if pos in ['P', 'TWP'] and name != starter_name:
            stats = fuzzy_lookup(name, pitcher_sc)
            if stats and stats.get('xERA') not in ('N/A', None, ''):
                f_log = f_data.get(pid, {})
                p1, p2, p3 = f_log.get(d1, 0), f_log.get(d2, 0), f_log.get(d3, 0)
                if p1 > 25 or (p1 > 0 and p2 > 0) or (p1 + p2 + p3) > 45:
                    fatigued_count += 1
                    continue
                try: bp_xeras.append(float(stats['xERA']))
                except: pass
                
        elif pos not in ['P', 'TWP'] and name not in lineup_names:
            stats = fuzzy_lookup(name, batter_sc)
            if stats and stats.get('xwOBA') not in ('N/A', None, ''):
                try: bench_xwobas.append(float(stats['xwOBA']))
                except: pass
                
    bp_xeras.sort()
    high_leverage = bp_xeras[:4] if len(bp_xeras) >= 4 else bp_xeras
    bp_xera = round(sum(high_leverage)/len(high_leverage), 2) if high_leverage else 4.20
    bench_xwoba = round(sum(bench_xwobas)/len(bench_xwobas), 3) if bench_xwobas else 0.300
    
    return bp_xera, bench_xwoba, len(high_leverage), fatigued_count

# ─── Core Logic Helpers (V4.6.2 Sniper Mode) ─────────────────────────
def get_top_4_xwoba(lineup_sc):
    if not lineup_sc or len(lineup_sc) < 4: return 0.0
    vals = []
    for p in lineup_sc[:4]:
        val = p.get('statcast', {}).get('xwOBA')
        if val in ('N/A', '-', '', None): return 0.0 # Rule 0 Enforcement
        vals.append(float(val))
    return sum(vals) / len(vals) if vals else 0.0

def evaluate_buzzsaw(opp_top_4_xwoba, base_required_delta=0.75): 
    if opp_top_4_xwoba >= 0.365: return 1.60
    elif opp_top_4_xwoba >= 0.350: return 1.15
    return base_required_delta

def blended_pitching_metric_v4(starter_xera, bullpen_xera):
    if starter_xera in ('N/A', None, ''): return None
    try: return ((5.0 / 9.0) * float(starter_xera)) + ((4.0 / 9.0) * bullpen_xera)
    except: return None

# ─── Weather ──────────────────────────────────────────────────────────────────
def get_weather(home_team):
    if home_team in INDOOR_TEAMS: return {'label': 'Dome', 'relevant': False}
    city = TEAM_CITIES.get(home_team)
    if not city: return None
    try:
        r = requests.get(f"https://wttr.in/{city}?format=j1", timeout=5)
        c = r.json()['current_condition'][0]
        return {'label': c['weatherDesc'][0]['value'], 'temp': f"{c['temp_F']}°F", 'wind': f"{c['windspeedMiles']} mph {c['winddir16Point']}", 'relevant': True}
    except: return None

# ─── Game Loop ────────────────────────────────────────────────────────────────
def get_todays_games():
    pacific = pytz.timezone('America/Los_Angeles')
    today = datetime.now(pacific).strftime('%Y-%m-%d')
    url = f"https://statsapi.mlb.com/api/v1/schedule?sportId=1&date={today}&hydrate=probablePitcher,lineups,team,venue,game,linescore"
    
    data = requests.get(url).json()
    batter_sc = get_statcast_batter_data()
    pitcher_sc = get_statcast_pitcher_data()
    live_odds = get_live_odds() 
    games = []
    
    for date_entry in data.get('dates', []):
        for game in date_entry.get('games', []):
            game_id = game['gamePk']
            cached_state = _game_states.get(game_id, {})
            
            away_team, home_team = game['teams']['away']['team']['name'], game['teams']['home']['team']['name']
            away_id, home_id = game['teams']['away']['team']['id'], game['teams']['home']['team']['id']
            
            status = game.get('status', {})
            abstract_state = status.get('abstractGameState', '')
            detailed_state = status.get('detailedState', '')
            away_score = game['teams']['away'].get('score')
            home_score = game['teams']['home'].get('score')
            
            # 🛑 THE CLOSING ODDS MEMORY VAULT 🛑
            live_away_odds = live_odds.get(away_team)
            live_home_odds = live_odds.get(home_team)
            
            if abstract_state in ['Live', 'Final']:
                away_odds = cached_state.get('closing_odds', {}).get('away', live_away_odds)
                home_odds = cached_state.get('closing_odds', {}).get('home', live_home_odds)
            else:
                away_odds = live_away_odds if live_away_odds is not None else cached_state.get('closing_odds', {}).get('away')
                home_odds = live_home_odds if live_home_odds is not None else cached_state.get('closing_odds', {}).get('home')
            
            inning_info = ''
            if abstract_state == 'Live':
                ls = game.get('linescore', {})
                inning_info = f"{ls.get('inningHalf', '')} {ls.get('currentInningOrdinal', '')}"
            
            game_time_pt = ''
            if game.get('gameDate'):
                try: game_time_pt = datetime.strptime(game['gameDate'], '%Y-%m-%dT%H:%M:%SZ').replace(tzinfo=pytz.utc).astimezone(pacific).strftime('%-I:%M %p PT')
                except: pass

            game_probs = game.get('probablePitchers', {})
            away_pitcher_obj = game_probs.get('away') or game['teams']['away'].get('probablePitcher', {})
            home_pitcher_obj = game_probs.get('home') or game['teams']['home'].get('probablePitcher', {})
            
            away_p = away_pitcher_obj.get('fullName', 'TBD')
            home_p = home_pitcher_obj.get('fullName', 'TBD')
            away_p_id = away_pitcher_obj.get('id')
            home_p_id = home_pitcher_obj.get('id')
            
            away_p_stats = get_pitcher_stats_mlb(away_p_id, away_p, pitcher_sc)
            home_p_stats = get_pitcher_stats_mlb(home_p_id, home_p, pitcher_sc)
            
            away_lineup, home_lineup, away_lineup_sc, home_lineup_sc = [], [], [], []
            has_missing_data = False
            
            if 'lineups' in game:
                for p in game['lineups'].get('awayPlayers', []):
                    if p.get('fullName'):
                        away_lineup.append(p['fullName'])
                        sc_data = fuzzy_lookup(p['fullName'], batter_sc) or {}
                        if not sc_data: has_missing_data = True
                        away_lineup_sc.append({'name': p['fullName'], 'statcast': sc_data})
                        
                for p in game['lineups'].get('homePlayers', []):
                    if p.get('fullName'):
                        home_lineup.append(p['fullName'])
                        sc_data = fuzzy_lookup(p['fullName'], batter_sc) or {}
                        if not sc_data: has_missing_data = True
                        home_lineup_sc.append({'name': p['fullName'], 'statcast': sc_data})

            a_bp_xera, a_bench, a_rested, a_tired = get_full_roster_metrics(away_id, away_p, away_lineup, pitcher_sc, batter_sc)
            h_bp_xera, h_bench, h_rested, h_tired = get_full_roster_metrics(home_id, home_p, home_lineup, pitcher_sc, batter_sc)
            
            v3_pick, v3_color, v3_reason = "Awaiting Lineups/Pitchers", "#888888", "Missing pitcher/statcast data"
            
            a_top4_xwoba = get_top_4_xwoba(away_lineup_sc)
            h_top4_xwoba = get_top_4_xwoba(home_lineup_sc)
            a_blended_xera = None
            h_blended_xera = None

            if cached_state and (abstract_state in ['Live', 'Final'] or (abstract_state == 'Preview' and hash(tuple(away_lineup+home_lineup)) == cached_state['lineups_hash'])):
                v3_data = cached_state.get('v3_data', {})
                v3_pick = v3_data.get('pick', v3_pick)
                v3_color = v3_data.get('color', v3_color)
                v3_reason = v3_data.get('reason', v3_reason)
            else:
                if has_missing_data:
                    # ⚪️ WHITE TIER (Missing Data)
                    v3_color = "#ffffff"
                    v3_pick = "⚪️ SKIP (Missing Data)"
                    v3_reason = "Incomplete statcast profiles in confirmed lineup."
                else:
                    try: a_ip = float(away_p_stats.get('IP', 0)) if away_p_stats else 0
                    except: a_ip = 0
                    try: h_ip = float(home_p_stats.get('IP', 0)) if home_p_stats else 0
                    except: h_ip = 0
                    
                    if a_ip < 25 or h_ip < 25:
                        # ⚪️ WHITE TIER (Small Sample)
                        v3_color = "#ffffff"
                        v3_pick = "⚪️ SKIP (Small Sample)"
                        v3_reason = f"Pitcher IP under 25.0 minimum (A: {a_ip}, H: {h_ip})"
                    else:
                        a_blended_xera = blended_pitching_metric_v4(away_p_stats.get('xERA') if away_p_stats else None, a_bp_xera)
                        h_blended_xera = blended_pitching_metric_v4(home_p_stats.get('xERA') if home_p_stats else None, h_bp_xera)

                        if a_blended_xera and h_blended_xera and len(away_lineup) >= 4 and len(home_lineup) >= 4:
                            away_adv = h_blended_xera - a_blended_xera
                            home_adv = a_blended_xera - h_blended_xera
                            
                            if a_bench < 0.280: away_adv -= 0.15 
                            if h_bench < 0.280: home_adv -= 0.15

                            req_away = evaluate_buzzsaw(h_top4_xwoba)
                            req_home = evaluate_buzzsaw(a_top4_xwoba)
                            
                            max_adv = max(away_adv, home_adv)
                            raw_lean_team = away_team if away_adv > home_adv else home_team if home_adv > away_adv else "Tie"

                            if away_adv >= req_away: 
                                v3_pick, v3_color, v3_reason = f"🟢 V4.6 PLAY {away_team} ML", "#00ff88", f"+{away_adv:.2f} Edge (>{req_away:.2f} req)"
                            elif home_adv >= req_home: 
                                v3_pick, v3_color, v3_reason = f"🟢 V4.6 PLAY {home_team} ML", "#00ff88", f"+{home_adv:.2f} Edge (>{req_home:.2f} req)"
                            else: 
                                # ⚪️ WHITE TIER (Math Failed - Margin Too Thin)
                                v3_color = "#ffffff"
                                if max_adv > 0: 
                                    req_for_max = req_away if away_adv > home_adv else req_home
                                    v3_pick, v3_reason = f"⚪️ SKIP ({raw_lean_team} Lean)", f"Margin too thin (+{max_adv:.2f} edge < {req_for_max:.2f} req)"
                                else: 
                                    v3_pick, v3_reason = "⚪️ SKIP (Dead Even)", "Metrics dead even"

                            # RULE 10: DUAL-TIER PRICE FILTER 
                            if "PLAY" in v3_pick:
                                target_team = away_team if away_team in v3_pick else home_team
                                target_odds = away_odds if away_team in v3_pick else home_odds
                                
                                if target_odds in ('', 'N/A', None):
                                    pass # Remains 🟢 GREEN if API fails
                                else:
                                    try:
                                        t_odds_int = int(target_odds)
                                        if t_odds_int <= -201:
                                            original_edge = v3_reason.split('Edge')[0].strip()
                                            v3_pick = f"🛑 PRICE SKIP ({target_team})"
                                            v3_reason = f"Odds {t_odds_int} hit -201+ limit. Math: {original_edge} Edge"
                                            v3_color = "#ff6b6b"
                                        elif t_odds_int <= -151:
                                            original_edge = v3_reason.split('Edge')[0].strip()
                                            v3_pick = f"🟡 VALUE SKIP ({target_team})"
                                            v3_reason = f"Odds {t_odds_int} heavily juiced. Math: {original_edge} Edge"
                                            v3_color = "#ffd700"
                                    except (ValueError, TypeError):
                                        pass 

            # Lock the state into the vault 
            _game_states[game_id] = {
                'state': abstract_state, 
                'lineups_hash': hash(tuple(away_lineup+home_lineup)), 
                'closing_odds': {'away': away_odds, 'home': home_odds},
                'v3_data': {'pick': v3_pick, 'color': v3_color, 'reason': v3_reason}
            }

            def format_odds(odds):
                if odds in ('N/A', '', None): return ''
                return f"+{odds}" if isinstance(odds, int) and odds > 0 else str(odds)

            games.append({
                'game_id': game_id, 'away_team': away_team, 'home_team': home_team, 'game_time': game_time_pt,
                'away_odds': format_odds(away_odds), 'home_odds': format_odds(home_odds),
                'abstract_state': abstract_state, 'detailed_state': detailed_state,
                'away_score': away_score, 'home_score': home_score, 'inning_info': inning_info,
                'away_pitcher': away_p, 'home_pitcher': home_p, 'away_p_stats': away_p_stats, 'home_p_stats': home_p_stats,
                'away_lineup': away_lineup, 'home_lineup': home_lineup,
                'away_lineup_sc': away_lineup_sc, 'home_lineup_sc': home_lineup_sc,
                'a_bp_xera': a_bp_xera, 'a_bench': a_bench, 'a_tired': a_tired,
                'h_bp_xera': h_bp_xera, 'h_bench': h_bench, 'h_tired': h_tired,
                'a_top4_xwoba': a_top4_xwoba, 'h_top4_xwoba': h_top4_xwoba,
                'a_blended_xera': a_blended_xera, 'h_blended_xera': h_blended_xera,
                'lineup_confirmed': len(away_lineup) > 0 and len(home_lineup) > 0,
                'weather': get_weather(home_team), 'park_factor': PARK_FACTORS.get(home_team, 100),
                'v3_pick': v3_pick, 'v3_color': v3_color, 'v3_reason': v3_reason,
            })
            
    log_final_games(games)
    return games

# ─── HTML Frontend Helpers ────────────────────────────────────────────────────
def stat_color(stat, value):
    try: v = float(str(value).replace('%', ''))
    except: return ''
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
    if stat not in rules: return ''
    thresholds, higher_is_better = rules[stat]
    if higher_is_better:
        for t, cls in thresholds:
            if v >= t: return cls
        return 'bad'
    else:
        for t, cls in thresholds:
            if v <= t: return cls
        return 'bad'

def render_pitcher_block(name, stats):
    if not stats:
        return f'<div class="pitcher-block"><p class="pname">⚾ {name}</p><p style="color:#888;font-size:0.8em">Stats unavailable</p></div>'
    base_keys, sc_keys = ['ERA', 'WHIP', 'K%', 'BB%', 'HR/9'], ['xERA', 'Whiff%', 'HardHit%', 'Barrel%', 'xwOBA']
    def stat_cell(k, v): return f'<div class="sc"><span class="sl">{k}</span><span class="sv {stat_color(k, v)}">{v}</span></div>'
    base_grid = ''.join(stat_cell(k, stats.get(k, 'N/A')) for k in base_keys)
    sc_grid   = ''.join(stat_cell(k, stats.get(k, 'N/A')) for k in sc_keys)
    return f'<div class="pitcher-block"><p class="pname">⚾ {name} <span style="color:#888;font-size:0.75em">({stats.get("GS",0)} GS · {stats.get("IP",0)} IP)</span></p><div class="sgrid">{base_grid}</div><div class="sgrid" style="margin-top:4px">{sc_grid}</div></div>'

def render_roster_metrics(team_name, bp_xera, bench, tired):
    return (f'<div class="roster-metrics">'
            f'<div style="margin-bottom:6px; color:#aaa; font-size:0.85em;"><b>{team_name} Late Innings:</b></div>'
            f'<div style="display:flex; justify-content:space-between; margin-bottom:4px;">'
            f'<span>🔥 Available A-Bullpen xERA: <b class="{stat_color("xERA", bp_xera)}">{bp_xera}</b></span>'
            f'<span style="color:#ff6b6b; font-size:0.85em">({tired} Fatigued/Out)</span>'
            f'</div>'
            f'<div><span>🪵 Bench Pinch-Hit xwOBA: <b class="{stat_color("xwOBA", bench)}">{bench}</b></span></div>'
            f'</div>')

def render_lineup_sc(lineup_sc, team_name):
    if not lineup_sc: return ''
    rows = ''
    for p in lineup_sc:
        sc = p.get('statcast', {})
        if sc: rows += f'<tr><td>{p["name"]}</td><td class="{stat_color("xwOBA", sc.get("xwOBA","N/A"))}">{sc.get("xwOBA","—")}</td><td class="{stat_color("HardHit%", sc.get("HardHit%","N/A"))}">{sc.get("HardHit%","—")}</td><td class="{stat_color("Barrel%", sc.get("Barrel%","N/A"))}">{sc.get("Barrel%","—")}</td></tr>'
        else: rows += f'<tr><td>{p["name"]}</td><td>—</td><td>—</td><td>—</td></tr>'
    return f'<div class="sc-table-wrap"><p class="sc-title">📊 {team_name} Statcast</p><table class="sc-table"><tr><th>Batter</th><th>xwOBA</th><th>HardHit%</th><th>Barrel%</th></tr>{rows}</table></div>'

def render_score_banner(g):
    state, away, home, as_, hs = g['abstract_state'], g['away_team'], g['home_team'], g.get('away_score'), g.get('home_score')
    if state == 'Final':
        if as_ is None or hs is None: return f'<div class="score-banner final"><span class="score-teams">{away} — {home}</span><span class="score-label">FINAL</span></div>'
        winner = away if as_ > hs else home
        return f'<div class="score-banner final"><span class="score-teams">{away} <span class="score-num">{as_}</span> — <span class="score-num">{hs}</span> {home}</span><span class="score-label">FINAL · {winner} Win</span></div>'
    elif state == 'Live':
        return f'<div class="score-banner live"><span class="score-teams">{away} <span class="score-num">{as_ if as_ is not None else 0}</span> — <span class="score-num">{hs if hs is not None else 0}</span> {home}</span><span class="score-label">🔴 LIVE · {g["inning_info"]}</span></div>'
    return ''

def render_card(g):
    state, pf = g['abstract_state'], g['park_factor']
    border_cls = 'final-game' if state == 'Final' else 'live-game' if state == 'Live' else 'confirmed' if g['lineup_confirmed'] else 'pending'
    pf_label = '🔴 Hitter Friendly' if pf >= 105 else '🟢 Pitcher Friendly' if pf <= 95 else '⚪ Neutral'
    pf_cls = 'hitter' if pf >= 105 else 'pitcher-park' if pf <= 95 else 'neutral'
    
    weather_html = f'<span class="badge wx">🌤️ {g["weather"]["label"]} · {g["weather"]["temp"]} · 💨 {g["weather"]["wind"]}</span>' if g.get('weather') and g['weather']['relevant'] else '<span class="badge dome">🏟️ Dome</span>' if g.get('weather') else ''
                            
    score_html = render_score_banner(g)
    v3_banner = f'<div class="v3-banner" style="border-color:{g.get("v3_color", "#888")}"><span class="v3-pick" style="color:{g.get("v3_color", "#888")}">{g.get("v3_pick", "")}</span><span class="v3-reason">{g.get("v3_reason", "")}</span></div>'

    away_odds_disp = f'<span style="color:#88ff44; font-size:0.85em; margin-left:8px">[{g.get("away_odds", "")}]</span>' if g.get("away_odds") else ''
    home_odds_disp = f'<span style="color:#88ff44; font-size:0.85em; margin-left:8px">[{g.get("home_odds", "")}]</span>' if g.get("home_odds") else ''
    
    pitchers_html = f'<div class="pr">{render_pitcher_block(g["away_pitcher"], g["away_p_stats"])}{render_pitcher_block(g["home_pitcher"], g["home_p_stats"])}</div>'
    roster_html = f'<div class="pr"><div style="flex:1">{render_roster_metrics(g["away_team"], g["a_bp_xera"], g["a_bench"], g["a_tired"])}</div><div style="flex:1">{render_roster_metrics(g["home_team"], g["h_bp_xera"], g["h_bench"], g["h_tired"])}</div></div>'
    
    lineups_html = ''
    if g['lineup_confirmed']:
        away_sc_table = render_lineup_sc(g.get('away_lineup_sc', []), g['away_team'])
        home_sc_table = render_lineup_sc(g.get('home_lineup_sc', []), g['home_team'])
        away_li = ''.join(f'<li>{p}</li>' for p in g['away_lineup'])
        home_li = ''.join(f'<li>{p}</li>' for p in g['home_lineup'])
        lineups_html = f'<details class="lu"><summary>📋 View Lineups + Statcast</summary><div class="lu-row"><div><b>{g["away_team"]}</b><ol>{away_li}</ol></div><div><b>{g["home_team"]}</b><ol>{home_li}</ol></div></div><div class="sc-tables-row">{away_sc_table}{home_sc_table}</div></details>'
    elif state not in ('Final', 'Live'):
        lineups_html = '<p style="color:#ff6b6b;font-size:0.82em;margin-top:10px;">⏳ Lineup not yet confirmed</p>'
        
    header_right = '<span class="gt" style="color:#888">FINAL</span>' if state == 'Final' else f'<span class="gt" style="color:#ff4444">🔴 LIVE · {g["inning_info"]}</span>' if state == 'Live' else f'<span class="gt">🕐 {g["game_time"]}</span>'
        
    return f'<div class="game {border_cls}"><div class="gh"><h3>{g["away_team"]} {away_odds_disp} @ {g["home_team"]} {home_odds_disp}</h3>{header_right}</div>{score_html}{v3_banner}<div class="badges"><span class="badge {pf_cls}">🏠 PF {pf} · {pf_label}</span>{weather_html}</div>{pitchers_html}{roster_html}{lineups_html}</div>'

# ─── API Routes ───────────────────────────────────────────────────────────────
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
    .score-banner{display:flex;justify-content:space-between;align-items:center;padding:8px 12px;border-radius:6px;margin-bottom:10px;font-size:0.9em}
    .score-banner.final{background:#1a1a1a;color:#aaa}
    .score-banner.live{background:#2a0a0a;color:#ff8888}
    .score-num{font-size:1.3em;font-weight:bold;color:#ffd700}
    .score-label{font-size:0.78em;color:#888}
    .score-banner.live .score-label{color:#ff6666}
    .v3-banner{background:#0a0f1a;border:1px solid;padding:10px 14px;margin-bottom:12px;border-radius:6px;display:flex;justify-content:space-between;align-items:center}
    .v3-pick{font-weight:bold;font-size:1.05em;letter-spacing:0.5px}
    .v3-reason{color:#aaa;font-size:0.85em;text-align:right}
    .badges{display:flex;flex-wrap:wrap;gap:6px;margin-bottom:10px}
    .badge{font-size:0.73em;padding:3px 8px;border-radius:12px}
    .hitter{background:#3d1515;color:#ff6b6b}
    .pitcher-park{background:#0d2e1a;color:#00ff88}
    .neutral{background:#1e1e3a;color:#aaa}
    .wx{background:#1a2a3a;color:#7ec8e3}
    .dome{background:#2a2a2a;color:#888}
    .pr{display:flex;gap:12px;flex-wrap:wrap;margin-bottom:10px}
    .pitcher-block{flex:1;min-width:220px;background:#0f1929;border-radius:8px;padding:10px}
    .roster-metrics{margin-top:8px; padding:10px; background:#1e1e3a; border-radius:6px; font-size:0.9em; color:#ddd; border-left:3px solid #7ec8e3;}
    .pname{color:#ffd700;font-size:0.88em;margin-bottom:8px}
    .sgrid{display:grid;grid-template-columns:repeat(5,1fr);gap:4px}
    .sc{background:#1a2540;border-radius:4px;padding:4px 6px;text-align:center}
    .sl{display:block;font-size:0.62em;color:#888}
    .sv{display:block;font-size:0.88em;font-weight:bold}
    .elite{color:#00ff88}.good{color:#88ff44}.avg{color:#ffd700}.bad{color:#ff6b6b}
    .lu{margin-top:12px; background:#0f1929; padding:8px 12px; border-radius:6px;}
    .lu summary{cursor:pointer;color:#aaa;font-size:0.85em;padding:4px 0; font-weight:bold;}
    .lu summary:hover{color:#fff;}
    .lu-row{display:flex;gap:16px;flex-wrap:wrap;margin-top:10px;font-size:0.82em;color:#ccc}
    .lu-row ol{padding-left:22px;margin-top:6px}
    .lu-row li{margin:3px 0}
    .sc-tables-row{display:flex;gap:16px;flex-wrap:wrap;margin-top:16px}
    .sc-table-wrap{flex:1;min-width:240px}
    .sc-title{color:#7ec8e3;font-size:0.85em;margin-bottom:8px; font-weight:bold;}
    .sc-table{width:100%;border-collapse:collapse;font-size:0.78em}
    .sc-table th{color:#888;text-align:left;padding:4px 6px;border-bottom:1px solid #1a2540}
    .sc-table td{padding:4px 6px;border-bottom:1px solid #1a2540}
    .sc-table tr:hover td{background:#1a2540}
    """
    def section(title, color, items):
        if not items: return ''
        return f'<h2 style="color:{color}">{title} ({len(items)} games)</h2>' + ''.join(render_card(g) for g in items)
        
    html = f"""<!DOCTYPE html><html>
    <head>
      <title>MLB V5.1 Dashboard</title>
      <meta name="viewport" content="width=device-width,initial-scale=1">
      <style>{css}</style>
    </head>
    <body>
      <h1>⚾ MLB V5.1 Sniper Engine</h1>
      <p class="sub">Last updated: {now_pt.strftime('%I:%M %p PT')} &middot; {now_pt.strftime('%b %d, %Y')}</p>
      {section('🔴 Live Now', '#ff4444', live)}
      {section('✅ Lineups Confirmed', '#00ff88', confirmed)}
      {section('⏳ Lineups Pending', '#ff6b6b', pending)}
      {section('☑️ Completed', '#555', final)}
    </body></html>"""
    return html

@app.route('/api')
def api_base():
    """Safety net so visiting /api doesn't throw a 404 error."""
    return jsonify({
        "status": "Online",
        "engine": "V5.1",
        "endpoints": {
            "dashboard": "/",
            "json_feed": "/api/games",
            "csv_ledger": "/api/ledger"
        }
    })

@app.route('/api/games')
def api_games():
    return jsonify(cached('games_list', get_todays_games))

@app.route('/api/ledger')
def api_ledger():
    if os.path.exists(LOG_FILE):
        return send_file(
            LOG_FILE,
            mimetype='text/csv',
            as_attachment=True,
            download_name='v5_algorithm_ledger.csv'
        )
    else:
        return jsonify({
            "message": "The ledger file is empty. Check back after the first game goes 'Final'."
        }), 200

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=10000, debug=False)
