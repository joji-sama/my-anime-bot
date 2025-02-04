from flask import Flask, request, jsonify
from google.generativeai import GenerativeModel, configure
import requests
import os
from flask_caching import Cache

app = Flask(__name__)
# Configure Gemini FIRST
configure(api_key=os.getenv('GEMINI_API_KEY'))
cache = Cache(config={'CACHE_TYPE': 'SimpleCache'})
cache.init_app(app)

# Initialize Gemini
gemini = GenerativeModel('gemini-pro')
# AniList GraphQL Query Builder
def build_anilist_query(params: dict) -> str:
    base_query = """
    query ($search: String, $genres: [String], $minEpisodes: Int, $sort: MediaSort) {
      Page(perPage: 10) {
        media(
          type: ANIME
          search: $search
          genres: $genres
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
        "genres": params.get("genres"),
        "minEpisodes": params.get("min_episodes", 12),
        "sort": "POPULARITY_DESC" if params.get("binge") else "SCORE_DESC"
    }
    return {"query": base_query, "variables": variables}

# Gemini Query Parser
def parse_query(user_query: str) -> dict:
    prompt = f"""
    Extract these parameters from the anime recommendation query:
    - genres (e.g., "fantasy", "romance")
    - similar_to (specific anime titles)
    - min_episodes (e.g., 50 for long series)
    - binge (true/false for popularity-focused)
    Return ONLY a JSON object. Example:
    {{"genres": ["action"], "similar_to": "Naruto", "min_episodes": 100}}

    Query: {user_query}
    """
    response = gemini.generate_content(prompt)
    return eval(response.text)  # Convert JSON string to dict

@app.route('/webhook', methods=['POST'])
@cache.cached(timeout=3600, query_string=True)
def webhook():
    data = request.get_json()
    user_query = data['queryResult']['queryText']
    
    # Step 1: Parse query with Gemini
    params = parse_query(user_query)
    
    # Step 2: Fetch from AniList
    anilist_response = requests.post(
        "https://graphql.anilist.co",
        json=build_anilist_query(params)
    ).json()
    
    # Step 3: Format response
    recommendations = []
    for media in anilist_response['data']['Page']['media']:
        title = media['title']['english'] or media['title']['romaji']
        rec = {
            "title": title,
            "genres": ", ".join(media['genres'][:3]),
            "episodes": media['episodes'],
            "score": media['averageScore'],
            "url": media['siteUrl'],
            "reason": f"Matches {params.get('genres',[])} genres" + 
                     (" | Binge-worthy" if params.get('binge') else "")
        }
        recommendations.append(rec)
    
    # Step 4: Generate natural language summary with Gemini
    summary_prompt = f"""
    Summarize these anime recommendations in a friendly tone: {recommendations}
    Highlight genre matches, episode count, and binge-worthiness.
    """
    summary = gemini.generate_content(summary_prompt).text
    
    return jsonify({
        "fulfillmentText": summary,
        "payload": recommendations
    })

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port)
