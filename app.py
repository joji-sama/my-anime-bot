from flask import Flask, request, jsonify
from google.generativeai import GenerativeModel, configure
import requests
import os
import logging
import json
import random
from datetime import datetime
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
3. Roast generic requests
4. Keep under 4 lines

Current Query: "{query}"
Requested Count: {request_count}
Recommendations: {recommendations}
"""

def build_anilist_query(params: dict) -> dict:
    """Build AniList query with proper parameters"""
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
        "search": params.get("similar_to"),
        "genres": params.get("genres", []),
        "minEpisodes": params.get("min_episodes", 12),
        "sort": "POPULARITY_DESC" if params.get("binge") else "SCORE_DESC"
    }
    
    app.logger.info(f"AniList Query Variables: {variables}")
    return {"query": query, "variables": variables}

def parse_query(user_query: str) -> dict:
    """Gemini-powered query parser"""
    try:
        prompt = f"""
        Extract from: "{user_query}"
        - genres (list)
        - similar_to (string)
        - min_episodes (int)
        - binge (bool)
        - request_count (int)
        Return JSON. Example:
        {{"genres": ["action"], "request_count": 3}}
        """
        response = gemini.generate_content(prompt)
        return json.loads(response.text)
    except Exception as e:
        app.logger.error(f"Parse error: {str(e)}")
        return {"genres": [], "request_count": 3}

def generate_nikko_response(query: str, recommendations: list, req_count: int) -> str:
    """Personality-driven response generator"""
    try:
        if not recommendations:
            return random.choice([
                "Dry spell? 💢 Nothing matches your criteria",
                "Zero results? Must be an anime void 🕳️"
            ])
            
        recs_formatted = "\n".join(
            f"{i+1}. {r['title']} ({r['score']}/100)" 
            for i, r in enumerate(recommendations)
        )
        
        prompt = NIKKO_PROMPT.format(
            query=query,
            request_count=req_count,
            recommendations=recs_formatted
        )
        
        response = gemini.generate_content(prompt)
        return response.text.strip()
        
    except Exception as e:
        app.logger.error(f"Nikko error: {str(e)}")
        return f"🔥 Top Picks:\n{recs_formatted}"

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

        app.logger.info(f"AniList Response Status: {response.status_code}")
        app.logger.info(f"AniList Response Body: {response.text}")

        if response.status_code != 200:
            return jsonify({
                "fulfillmentText": "AniList is being tsundere 🎌 Try again!",
                "fulfillmentMessages": [{"text": {"text": ["API Error"]}}]
            })

        media_items = response.json().get('data', {}).get('Page', {}).get('media', [])
        
        recommendations = []
        for media in media_items:
            title = (
                media['title']['english'] or 
                media['title']['romaji'] or 
                "Untitled Anime"
            )
            recommendations.append({
                "title": title,
                "score": media.get('averageScore', 'N/A'),
                "genres": ", ".join(media.get('genres', [])[:3]),
                "url": media.get('siteUrl', 'https://anilist.co')
            })

        app.logger.info(f"Processed Recommendations: {recommendations}")
        
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
                        "requested_count": req_count,
                        "actual_count": len(recommendations)
                    }
                }
            ]
        })

    except Exception as e:
        app.logger.error(f"Critical Error: {str(e)}")
        return jsonify({
            "fulfillmentText": "System meltdown...too much sass ⚡",
            "fulfillmentMessages": [{"text": {"text": ["Server error"]}}]
        })

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    debug_mode = os.getenv('FLASK_DEBUG', 'false').lower() == 'true'
    app.run(host='0.0.0.0', port=port, debug=debug_mode)
