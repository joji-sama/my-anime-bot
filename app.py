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
1. Acknowledge request count with attitude ("{request_count}? Let's go!"/"Greedy much?")
2. Max 2 emojis (use 🔥🎌💢🤌)
3. Roast generic requests ("Basic taste I see...")
4. End with challenge ("Need harder picks?")
5. Keep it under 4 lines

**Formats**:
- Normal: "[Reaction] {request_count} coming up! [2 Best] [Hook]"
- Too Many: "{actual_count} is my limit. {Top Picks} Better than nothing 💢"

**Examples**:
User: "5 best isekai"
Nikko: "Five? Ambitious rookie! 🎌
1. Re:Zero (Pain simulator)
2. Mushoku Tensei (Redemption arc)
Want REAL underground picks?"

User: "15 romance anime"
Nikko: "Fifteen? 😒 I'm classy not desperate:
1. Toradora! (Tsundere classic)
2. Your Name (Cosmic love)
That's my premium cut 💎"

Current Query: "{query}"
Requested Count: {request_count}
Actual Delivered: {actual_count}
Recommendations: {recommendations}
"""

def build_anilist_query(params: dict) -> dict:
    """Build dynamic AniList query"""
    params['genres'] = params.get('genres', []) if isinstance(params.get('genres'), list) else []
    params['similar_to'] = str(params.get('similar_to', '')).strip() or None
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
        "genre_in": params.get("genres"),
        "minEpisodes": params.get("min_episodes", 12),
        "sort": "POPULARITY_DESC" if params.get("binge") else "SCORE_DESC"
    }
    return {"query": query, "variables": variables}

def parse_query(user_query: str) -> dict:
    """Enhanced query parser with number detection"""
    try:
        prompt = f"""
        Extract from: "{user_query}"
        - genres (list)
        - similar_to (string)
        - min_episodes (int)
        - binge (bool)
        - request_count (int, default=3)
        Return ONLY JSON. Example:
        {{"genres": ["action"], "request_count": 5, "binge": true}}

        Query: {user_query}
        """
        response = gemini.generate_content(prompt)
        return json.loads(response.text)
    except Exception as e:
        app.logger.error(f"Parse error: {str(e)}")
        return {"genres": [], "request_count": 3}

def generate_nikko_response(query: str, recommendations: list, req_count: int) -> str:
    """Generate personality-driven responses"""
    try:
        actual_count = len(recommendations)
        recs_formatted = "\n".join(
            f"{i+1}. {r['title']} ({r['score']}/100)" 
            for i, r in enumerate(recommendations[:actual_count])
        )

        prompt = NIKKO_PROMPT.format(
            query=query,
            request_count=req_count,
            actual_count=actual_count,
            recommendations=recs_formatted
        )
        
        response = gemini.generate_content(prompt)
        return response.text.strip()
        
    except Exception as e:
        app.logger.error(f"Nikko error: {str(e)}")
        return f"🔥 Top {actual_count} picks:\n{recs_formatted}"

@app.route('/webhook', methods=['POST'])
@cache.cached(timeout=3600, query_string=True)
def webhook():
    try:
        data = request.get_json()
        user_query = data.get('queryResult', {}).get('queryText', '').strip()
        
        if not user_query or len(user_query) > 500:
            return jsonify({
                "fulfillmentText": random.choice([
                    "Try harder with that query! 💢",
                    "Even I need more than that 🎌"
                ]),
                "fulfillmentMessages": [{"text": {"text": ["Invalid query format"]}}]
            })

        # Parse parameters
        params = parse_query(user_query)
        req_count = min(params.get('request_count', 3), 10)
        app.logger.info(f"Params: {params} | ReqCount: {req_count}")

        # Fetch from AniList
        anilist_request = build_anilist_query(params)
        response = requests.post(
            "https://graphql.anilist.co",
            json=anilist_request,
            headers={"Accept-Encoding": "gzip"},
            timeout=10
        )

        # Handle API errors
        if response.status_code != 200:
            return jsonify({
                "fulfillmentText": random.choice([
                    "AniList ghosted us 💢 Try again?",
                    "My sources are being tsundere 🎌"
                ]),
                "fulfillmentMessages": [{"text": {"text": ["API Error"]}}]
            })

        media_items = response.json().get('data', {}).get('Page', {}).get('media', [])
        recommendations = [
            {
                "title": (m['title']['english'] or m['title']['romaji']),
                "score": m.get('averageScore', 'N/A'),
                "genres": ", ".join(m.get('genres', [])[:3]),
                "url": m.get('siteUrl', 'https://anilist.co')
            } for m in media_items
        ]

        # Generate Nikko response
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
                        "nikko_flavor": "sassy_anime_expert_v2",
                        "requested_count": req_count,
                        "delivered_count": len(recommendations),
                        "recommendations": recommendations
                    }
                }
            ]
        })

    except Exception as e:
        app.logger.error(f"Critical error: {str(e)}")
        return jsonify({
            "fulfillmentText": random.choice([
                "Broke my anime glasses! 🕶️ Try again?",
                "System meltdown...too much sass ⚡"
            ]),
            "fulfillmentMessages": [{"text": {"text": ["Server error"]}}]
        })

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    debug_mode = os.getenv('FLASK_DEBUG', 'false').lower() == 'true'
    app.run(host='0.0.0.0', port=port, debug=debug_mode)
