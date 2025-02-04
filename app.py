from flask import Flask, request, jsonify
from google.generativeai import GenerativeModel
import requests  # Import requests!
import os
import json

app = Flask(__name__)

# Initialize Google Gemini (Corrected Authentication)
gemini_api_key = os.getenv('GEMINI_API_KEY')
if not gemini_api_key:
    raise ValueError("GEMINI_API_KEY environment variable not set.")

gemini = GenerativeModel(model='gemini-pro')
gemini.api_key = gemini_api_key

# AniList GraphQL Query
def fetch_anilist_data(genre: str, min_score: int = 70):
    query = """
    query ($genre: String, $minScore: Int) {
      Page(perPage: 5) {
        media(type: ANIME, genre: $genre, averageScore_greater: $minScore, sort: POPULARITY_DESC) {
          title { english }
          description
          averageScore
          siteUrl
        }
      }
    }
    """
    variables = {"genre": genre, "minScore": min_score}
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json"
    }

    try:
        print(f"AniList Query: {query}")
        response = requests.post(
            "https://graphql.anilist.co",
            headers=headers,
            json={"query": query, "variables": variables}
        )
        print(f"Status Code: {response.status_code}")
        print(f"AniList Response: {response.text}")
        response.raise_for_status()
        data = response.json()
        return data
    except requests.exceptions.RequestException as e:
        print(f"Error fetching AniList data: {e}")
        return None
    except json.JSONDecodeError as e:
        print(f"Error decoding AniList response: {e}")
        return None


# Webhook for Dialogflow/Google AI integration
@app.route('/webhook', methods=['POST'])
def webhook():
    data = request.get_json()
    user_query = data.get('queryResult', {}).get('queryText')

    if not user_query:
        return jsonify({"fulfillmentText": "Sorry, I didn't understand your request."})

    prompt = f"""
    Extract the anime genre from this user query. Return ONLY the genre name.
    Examples: "romance", "sci-fi", "action". Query: {user_query}
    """
    try:
        gemini_response = gemini.generate_content(prompt)
        genre = gemini_response.text.strip().lower()
        print(f"Extracted Genre: {genre}")
    except Exception as e:
        print(f"Error with Gemini: {e}")
        return jsonify({"fulfillmentText": "Error processing your request."})

    anilist_data = fetch_anilist_data(genre)
    if anilist_data is None:
        return jsonify({"fulfillmentText": "Error fetching anime data."})

    media_items = anilist_data.get('data', {}).get('Page', {}).get('media', [])

    if not media_items:
        return jsonify({"fulfillmentText": f"No {genre} anime found matching your criteria."})

    NUM_RECOMMENDATIONS = int(os.environ.get("NUM_RECOMMENDATIONS", 5))
    response = {
        "fulfillmentText": f"Here are some top {genre} anime recommendations:",
        "fulfillmentMessages": [
            {
                "text": {
                    "text": [
                        f"*{item['title']['english']}* (Score: {item['averageScore']}/100)\n{item.get('description', 'No description available')}\nMore info: {item.get('siteUrl', 'No URL available')}"
                        for item in media_items[:NUM_RECOMMENDATIONS]
                    ]
                }
            }
        ]
    }
    return jsonify(response)


if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=True)  # Set debug=False for production
