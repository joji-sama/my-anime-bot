from flask import Flask, request, jsonify
from google.generativeai import GenerativeModel, configure
import requests
import os
import logging
import json
import random
from json import JSONDecodeError
from flask_caching import Cache

app = Flask(__name__)
cache = Cache(config={'CACHE_TYPE': 'SimpleCache'})
cache.init_app(app)

# Configure logging
logging.basicConfig(level=logging.INFO)
app.logger.setLevel(logging.INFO)

# Configure Gemini
configure(api_key=os.getenv('GEMINI_API_KEY'))
gemini = GenerativeModel('gemini-pro')

NIKKO_PROMPT = """
You're Nikko, the sassiest anime AI. Respond to "{query}" with:

1. Genre acknowledgment ("{genres} fan? Let's go!")
2. Top {count} picks with hype descriptions
3. Signature sass ("Need harder challenges? 💪")

Examples:
- "Basic taste I see... 😏 Top 3 action picks: 
  1. Demon Slayer (Breath-taking swordplay)
  2. Jujutsu Kaisen (Curse-breaking hype)
  Want REAL chaos? 🔥"

Recommendations: {recommendations}
"""

def build_anilist_query(params: dict) -> dict:
    """Builds proper AniList API query"""
    req_count = min(params.get('request_count', 3), 10)
    
    query = f"""
    query ($search: String, $genres: [String], $minEpisodes: Int, $sort: MediaSort) {{
      Page(perPage: {req_count}) {{
        media(
          type: ANIME
          search: $search
          genres: $genres
          episodes_greater: $minEpisodes
          sort: [$sort]
        ) {{
          title {{ english romaji }}
          genres
          averageScore
          siteUrl
        }}
      }}
    }}
    """
    
    variables = {
        "search": params.get("similar_to", ""),
        "genres": params.get("genres", []),
        "minEpisodes": 0,  # Changed from 12 to 0 to include short series
        "sort": "POPULARITY_DESC" if params.get("binge") else "SCORE_DESC"
    }
    
    app.logger.info(f"AniList Query Variables: {variables}")
    return {"query": query, "variables": variables}

def parse_query(user_query: str) -> dict:
    """Properly extracts and formats parameters"""
    try:
        prompt = f"""
        Extract from: "{user_query}"
        - genres (comma-separated lowercase list)
        - request_count (integer)
        Return JSON. Example: {{"genres": ["action"], "request_count": 3}}
        """
        response = gemini.generate_content(prompt)
        parsed = json.loads(response.text)
        
        # Ensure proper genre formatting
        genres = []
        if 'genres' in parsed:
            if isinstance(parsed['genres'], str):
                genres = [parsed['genres'].lower().strip()]
            elif isinstance(parsed['genres'], list):
                genres = [g.lower().strip() for g in parsed['genres'] if g.strip()]
        
        return {
            "genres": genres,
            "request_count": parsed.get('request_count', 3)
        }
        
    except Exception as e:
        app.logger.error(f"Parse error: {str(e)}")
        return {"genres": [], "request_count": 3}

def generate_nikko_response(query: str, recommendations: list, req_count: int, genres: list) -> str:
    """Generates personality-driven responses"""
    try:
        if not recommendations:
            return random.choice([
                "Dry spell? 💢 Nothing matches your criteria",
                "Zero results? Must be an anime void 🕳️"
            ])
            
        recs_formatted = "\n".join(
            f"{i+1}. {r['title']} ({r['score']}/100)" 
            for i, r in enumerate(recommendations[:req_count])
        )
        
        prompt = NIKKO_PROMPT.format(
            query=query,
            genres=", ".join(genres) if genres else "generic",
            count=len(recommendations),
            recommendations=recs_formatted
        )
        
        response = gemini.generate_content(prompt)
        return response.text.strip()
        
    except Exception as e:
        app.logger.error(f"Nikko error: {str(e)}")
        return "🔥 Top Picks:\n" + "\n".join(
            f"{i+1}. {r['title']}" for i, r in enumerate(recommendations)
        )

@app.route('/webhook', methods=['POST'])
@cache.cached(timeout=3600, query_string=True)
def webhook():
    try:
        data = request.get_json()
        user_query = data.get('queryResult', {}).get('queryText', '').strip()
        
        if not user_query:
            return jsonify({
                "fulfillmentText": "Try harder with that query! 💢",
                "fulfillmentMessages": [{"text": {"text": ["Invalid query"]}}]
            })

        # Parse parameters with proper formatting
        params = parse_query(user_query)
        req_count = min(params.get('request_count', 3), 10)
        genres = params.get('genres', [])
        
        app.logger.info(f"Parsed Parameters: {params}")

        # Build and send AniList request
        anilist_request = build_anilist_query(params)
        response = requests.post(
            "https://graphql.anilist.co",
            json=anilist_request,
            headers={"Accept-Encoding": "gzip"},
            timeout=10
        )

        app.logger.info(f"AniList Status Code: {response.status_code}")
        app.logger.info(f"AniList Response: {response.text}")

        if response.status_code != 200:
            return jsonify({
                "fulfillmentText": "AniList is being tsundere 🎌 Try again!",
                "fulfillmentMessages": [{"text": {"text": ["API Error"]}}]
            })

        # Process response
        media_items = response.json().get('data', {}).get('Page', {}).get('media', [])
        
        recommendations = []
        for media in media_items:
            title = media['title']['english'] or media['title']['romaji'] or "Untitled"
            recommendations.append({
                "title": title,
                "score": media.get('averageScore', 'N/A'),
                "genres": ", ".join(media.get('genres', [])),
                "url": media.get('siteUrl', 'https://anilist.co')
            })

        # Generate Nikko's response
        nikko_summary = generate_nikko_response(user_query, recommendations, req_count, genres)
        
        return jsonify({
            "fulfillmentText": nikko_summary,
            "fulfillmentMessages": [
                {
                    "text": {"text": [nikko_summary]},
                    "platform": "DIALOGFLOW_CONSOLE"
                },
                {
                    "payload": {
                        "recommendations": recommendations,
                        "requested_count": req_count
                    }
                }
            ]
        })

    except Exception as e:
        app.logger.error(f"Critical Error: {str(e)}")
        return jsonify({
            "fulfillmentText": "System crashed...sass overload 💥",
            "fulfillmentMessages": [{"text": {"text": ["Server error"]}}]
        })

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    debug_mode = os.getenv('FLASK_DEBUG', 'false').lower() == 'true'
    app.run(host='0.0.0.0', port=port, debug=debug_mode)
