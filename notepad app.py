from flask import Flask, jsonify
import requests
from datetime import datetime

app = Flask(__name__)

def get_todays_games():
    today = datetime.now().strftime('%Y-%m-%d')
    url = f"https://statsapi.mlb.com/api/v1/schedule?sportId=1&date={today}&hydrate=probablePitcher,lineups,team"
    response = requests.get(url)
    data = response.json()
    
    games = []
    for date in data.get('dates', []):
        for game in date.get('games', []):
            game_id = game['gamePk']
            away_team = game['teams']['away']['team']['name']
            home_team = game['teams']['home']['team']['name']
            
            away_pitcher = game['teams']['away'].get('probablePitcher', {}).get('fullName', 'TBD')
            home_pitcher = game['teams']['home'].get('probablePitcher', {}).get('fullName', 'TBD')
            
            away_lineup = []
            home_lineup = []
            
            if 'lineups' in game:
                for player in game.get('lineups', {}).get('awayPlayers', []):
                    away_lineup.append(player.get('fullName', ''))
                for player in game.get('lineups', {}).get('homePlayers', []):
                    home_lineup.append(player.get('fullName', ''))
            
            games.append({
                'game_id': game_id,
                'away_team': away_team,
                'home_team': home_team,
                'away_pitcher': away_pitcher,
                'home_pitcher': home_pitcher,
                'away_lineup': away_lineup,
                'home_lineup': home_lineup,
                'lineup_confirmed': len(away_lineup) > 0 and len(home_lineup) > 0
            })
    
    return games

@app.route('/')
def index():
    games = get_todays_games()
    confirmed = [g for g in games if g['lineup_confirmed']]
    pending = [g for g in games if not g['lineup_confirmed']]
    
    html = f"""
    <html>
    <head>
        <title>MLB Picks - {datetime.now().strftime('%B %d, %Y')}</title>
        <meta name="viewport" content="width=device-width, initial-scale=1">
        <style>
            body {{ font-family: Arial, sans-serif; background: #1a1a2e; color: #eee; padding: 20px; }}
            h1 {{ color: #ffd700; }}
            h2 {{ color: #00ff88; }}
            h3 {{ color: #ff6b6b; }}
            .game {{ background: #16213e; border: 1px solid #0f3460; padding: 15px; margin: 10px 0; border-radius: 8px; }}
            .confirmed {{ border-left: 4px solid #00ff88; }}
            .pending {{ border-left: 4px solid #ff6b6b; }}
            .pitcher {{ color: #ffd700; }}
            .lineup {{ font-size: 0.9em; color: #aaa; }}
        </style>
    </head>
    <body>
        <h1>⚾ MLB Daily Picks Dashboard</h1>
        <p>Last updated: {datetime.now().strftime('%I:%M %p')}</p>
        
        <h2>✅ Lineups Confirmed ({len(confirmed)} games)</h2>
    """
    
    for game in confirmed:
        html += f"""
        <div class="game confirmed">
            <h3>{game['away_team']} @ {game['home_team']}</h3>
            <p class="pitcher">⚾ {game['away_team']}: {game['away_pitcher']}</p>
            <p class="pitcher">⚾ {game['home_team']}: {game['home_pitcher']}</p>
            <p class="lineup"><b>Away Lineup:</b> {', '.join(game['away_lineup']) if game['away_lineup'] else 'Loading...'}</p>
            <p class="lineup"><b>Home Lineup:</b> {', '.join(game['home_lineup']) if game['home_lineup'] else 'Loading...'}</p>
        </div>
        """
    
    html += f"<h3>⏳ Lineups Pending ({len(pending)} games)</h3>"
    
    for game in pending:
        html += f"""
        <div class="game pending">
            <h3>{game['away_team']} @ {game['home_team']}</h3>
            <p class="pitcher">⚾ {game['away_team']}: {game['away_pitcher']}</p>
            <p class="pitcher">⚾ {game['home_team']}: {game['home_pitcher']}</p>
            <p style="color:#ff6b6b">Lineup not yet confirmed</p>
        </div>
        """
    
    html += "</body></html>"
    return html

@app.route('/api/games')
def api_games():
    return jsonify(get_todays_games())

if __name__ == '__main__':
    app.run(debug=True)