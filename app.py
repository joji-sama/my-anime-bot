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
You're Nikko, the anime expert with sass. Respond to "{query}" using these rules:

1. **Genre Callout**: Explicitly mention the requested genre(s)
2. **Recommendation Style**: 1-line descriptions with genre relevance
3. **Sass Level**: Match query specificity

Current Genres: {genres}
Recommendations: {recommendations}
"""

def build_anilist_query(params: dict) -> dict:
    req_count = min(params.get('request_count', 3), 10)
    
    query = f"""
    query ($search: String, $genre_in: [String], $minEpisodes: Int, $sort: MediaSort) {{
      Page(perPage: {req_count}) {{
        media(
          type: ANIME
          search: $search
          genre_in: $genre_in
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
        "genre_in": params.get("genres", []),
        "minEpisodes": int(params.get('min_episodes', 12)),
        "sort": "POPULARITY_DESC" if params.get("binge") else "SCORE_DESC"
    }
    return {"query": query, "variables": variables}

def parse_query(user_query: str) -> dict:
    try:
        prompt = f"""
        Extract from: "{user_query}"
        - genres (list, lowercase)
        - request_count (int)
        Return JSON. Example: {{"genres": ["action"], "request_count": 3}}
        """
        response = gemini.generate_content(prompt)
        parsed = json.loads(response.text)
        
        # Capitalize and validate genres
        genres = []
        if 'genres' in parsed:
            if isinstance(parsed['genres'], str):
                genres = [parsed['genres'].capitalize()]
            elif isinstance(parsed['genres'], list):
                genres = [g.strip().capitalize() for g in parsed['genres'] if g.strip()]
        
        return {
            "genres": genres,
            "request_count": parsed.get('request_count', 3)
        }
    except Exception as e:
        app.logger.error(f"Parse error: {str(e)}")
        return {"genres": [], "request_count": 3}

def generate_nikko_response(query: str, recommendations: list, req_count: int, genres: list) -> str:
    try:
        if not recommendations:
            return random.choice([
                "Dry spell? 💢 Nothing matches your criteria",
                "Zero results? Must be an anime void 🕳️"
            ])
            
        prompt = NIKKO_PROMPT.format(
            query=query,
            genres=", ".join(genres) if genres else "generic",
            recommendations="\n".join(
                f"- {r['title']} ({r['score']}/100): {r['genres']}"
                for r in recommendations
            )
        )
        
        response = gemini.generate_content(prompt)
        return response.text.strip()
        
    except Exception as e:
        app.logger.error(f"Nikko error: {str(e)}")
        return "🔥 Top Picks (Technical Difficulties Edition):\n" + "\n".join(
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

        # Parse parameters
        params = parse_query(user_query)
        req_count = min(params.get('request_count', 3), 10)
        genres = params.get('genres', [])
        
        app.logger.info(f"Parsed Params: {params}")

        # Build AniList request
        anilist_request = build_anilist_query(params)
        response = requests.post(
            "https://graphql.anilist.co",
            json=anilist_request,
            headers={"Accept-Encoding": "gzip"},
            timeout=10
        )

        if response.status_code != 200:
            return jsonify({
                "fulfillmentText": "AniList glitched! 🛠️ Try different terms",
                "fulfillmentMessages": [{"text": {"text": ["API Error"]}}]
            })

        media_items = response.json().get('data', {}).get('Page', {}).get('media', [])
        
        recommendations = []
        for media in media_items:
            title = media['title']['english'] or media['title']['romaji'] or "Untitled"
            recommendations.append({
                "title": title,
                "score": media.get('averageScore', 'N/A'),
                "genres": ", ".join(media.get('genres', []))
            })

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
            "fulfillmentText": "System crashed...too much sass overload 💥",
            "fulfillmentMessages": [{"text": {"text": ["Server error"]}}]
        })

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    debug_mode = os.getenv('FLASK_DEBUG', 'false').lower() == 'true'
    app.run(host='0.0.0.0', port=port, debug=debug_mode)
