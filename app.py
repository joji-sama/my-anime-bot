from flask import Flask, request, jsonify
from google.generativeai import GenerativeModel, configure
import requests
import os
import logging
import json
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

def build_anilist_query(params: dict) -> dict:
    """Build AniList GraphQL query with parameters"""
    params['genres'] = params.get('genres', []) if isinstance(params.get('genres'), list) else []
    params['similar_to'] = str(params.get('similar_to', '')).strip() or None

    query = """
    query ($search: String, $genre_in: [String], $minEpisodes: Int, $sort: MediaSort) {
      Page(perPage: 10) {
        media(
          type: ANIME
          search: $search
          genre_in: $genre_in
          episodes_greater: $minEpisodes
          sort: [$sort]
        ) {
          title { english romaji }
          description
          episodes
          genres
          averageScore
          popularity
          siteUrl
          recommendations(perPage: 5) {
            nodes {
              mediaRecommendation {
                title { english romaji }
              }
            }
          }
        }
      }
    }
    """
    variables = {
        "search": params.get("similar_to"),
        "genre_in": params.get("genres"),
        "minEpisodes": params.get("min_episodes", 12),
        "sort": "POPULARITY_DESC" if params.get("binge") else "SCORE_DESC"
    }
    return {"query": query, "variables": variables}

def parse_query(user_query: str) -> dict:
    """Parse user query with Gemini"""
    try:
        prompt = f"""
        Extract these parameters from the anime recommendation query:
        - genres (list)
        - similar_to (string)
        - min_episodes (integer)
        - binge (boolean)
        Return ONLY valid JSON. Example:
        {{"genres": ["action"], "similar_to": "Naruto", "min_episodes": 100, "binge": true}}

        Query: {user_query}
        """
        response = gemini.generate_content(prompt)
        return json.loads(response.text)
    except (JSONDecodeError, AttributeError) as e:
        app.logger.error(f"Gemini parsing error: {str(e)}")
        return {"genres": [], "similar_to": None, "min_episodes": 12, "binge": False}

@app.route('/webhook', methods=['POST'])
@cache.cached(timeout=3600, query_string=True)
def webhook():
    try:
        data = request.get_json()
        user_query = data.get('queryResult', {}).get('queryText', '').strip()
        
        if not user_query or len(user_query) > 500:
            return jsonify({"fulfillmentText": "Please provide a valid anime request (under 500 characters)."})

        # Parse query parameters
        params = parse_query(user_query)
        app.logger.info(f"Parsed parameters: {params}")

        # Build and send AniList request
        anilist_request = build_anilist_query(params)
        response = requests.post(
            "https://graphql.anilist.co",
            json=anilist_request,
            headers={"Accept-Encoding": "gzip"},
            timeout=10
        )

        # Validate AniList response
        if response.status_code != 200:
            app.logger.error(f"AniList API Error: {response.text}")
            return jsonify({"fulfillmentText": f"Error accessing anime database. Status: {response.status_code}"})

        anilist_response = response.json()
        if 'errors' in anilist_response:
            app.logger.error(f"AniList Error: {anilist_response['errors']}")
            return jsonify({"fulfillmentText": "AniList API error. Try again later."})

        if not anilist_response.get('data') or not anilist_response['data'].get('Page'):
            return jsonify({"fulfillmentText": "No results found."})

        media_items = anilist_response['data']['Page'].get('media', [])
        if not media_items:
            return jsonify({"fulfillmentText": "No anime match your criteria. Try different parameters!"})

        # Build recommendations
        recommendations = []
        for media in media_items:
            title = (
                media['title']['english'] or 
                media['title']['romaji'] or 
                "Title Not Available"
            )
            recommendations.append({
                "title": title,
                "genres": ", ".join(media.get('genres', [])[:3]),
                "episodes": media.get('episodes', 'N/A'),
                "score": media.get('averageScore', 'N/A'),
                "url": media.get('siteUrl', 'https://anilist.co')
            })

        # Generate summary with Gemini
        summary_prompt = f"""
        Summarize these anime recommendations naturally: {recommendations}
        Mention genres and key features. Keep under 3 sentences.
        Format: "Based on your request, here are my picks: [summary]"
        """
        try:
            summary = gemini.generate_content(summary_prompt).text
        except Exception as e:
            app.logger.error(f"Gemini summary error: {str(e)}")
            summary = "Here are some recommendations based on your request:"

        return jsonify({
            "fulfillmentText": summary,
            "payload": recommendations
        })

    except Exception as e:
        app.logger.error(f"Server error: {str(e)}")
        return jsonify({"fulfillmentText": "An error occurred. Please try again later."})

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    debug_mode = os.getenv('FLASK_DEBUG', 'false').lower() == 'true'
    app.run(host='0.0.0.0', port=port, debug=debug_mode)
