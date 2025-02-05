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
You're Nikko, the sassiest anime AI. Respond to: "{query}"

**Rules**:
1. Acknowledge request count with attitude
2. Max 2 emojis (🔥🎌💢🤌)
3. Keep under 4 lines

Current Query: "{query}"
Requested Count: {request_count}
Recommendations: {recommendations}
"""

def build_anilist_query(params: dict) -> dict:
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
          description
          episodes
          genres
          averageScore
          popularity
          siteUrl
        }}
      }}
    }}
    """
    
    variables = {
        "search": params.get("similar_to", ""),
        "genres": params.get("genres", []),
        "minEpisodes": int(params.get('min_episodes', 12)),
        "sort": "POPULARITY_DESC" if params.get("binge") else "SCORE_DESC"
    }
    
    app.logger.info(f"AniList Variables: {variables}")
    return {"query": query, "variables": variables}

def parse_query(user_query: str) -> dict:
    try:
        prompt = f"""
        Extract from: "{user_query}"
        - genres (list)
        - similar_to (string)
        - min_episodes (integer)
        - binge (boolean)
        - request_count (integer)
        Return JSON. Example:
        {{"genres": ["action"], "request_count": 3}}
        """
        response = gemini.generate_content(prompt)
        parsed = json.loads(response.text)
        
        # Ensure genres is a list
        if 'genres' in parsed:
            if isinstance(parsed['genres'], str):
                parsed['genres'] = [parsed['genres']]
            elif not isinstance(parsed['genres'], list):
                parsed['genres'] = []
        
        return parsed
    except Exception as e:
        app.logger.error(f"Parse error: {str(e)}")
        return {"genres": [], "request_count": 3}

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
        app.logger.info(f"Parsed Params: {params}")

        # Build AniList request
        anilist_request = build_anilist_query(params)
        response = requests.post(
            "https://graphql.anilist.co",
            json=anilist_request,
            headers={"Accept-Encoding": "gzip"},
            timeout=10
        )

        app.logger.info(f"AniList Status: {response.status_code}")
        app.logger.info(f"AniList Response: {response.text}")

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
                "genres": ", ".join(media.get('genres', [])[:3]),
                "url": media.get('siteUrl', 'https://anilist.co')
            })

        nikko_summary = generate_nikko_response(user_query, recommendations, req_count)
        
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
