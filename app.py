from flask import Flask, request, jsonify
import requests  # For making HTTP requests to AniList
import os

app = Flask(__name__)

# AniList API endpoint
ANILIST_API_URL = "https://graphql.anilist.co"

@app.route('/webhook', methods=['POST'])
def webhook():
    req = request.get_json(silent=True, force=True)
    user_input = req.get("queryResult").get("queryText")
    intent_name = req.get("queryResult").get("intent").get("displayName")
    genre = req.get("queryResult").get("parameters").get("AnimeGenre")

    try:
        query = construct_anilist_query(user_input, intent_name, genre)
        anilist_response = make_anilist_request(query)  # Function to make the API call

        if anilist_response and anilist_response.status_code == 200:
            anime_data = anilist_response.json()
            # Extract the anime data based on the query
            if anime_data and anime_data.get("data") and anime_data["data"].get("Page") and anime_data["data"]["Page"].get("media"):
                anime = anime_data["data"]["Page"]["media"][0] # Get the first result
                response_text = format_response(anime)
            else:
                response_text = "No anime found matching your criteria."
        else:
            response_text = "Error fetching data from AniList."

        return jsonify({"fulfillmentText": response_text})

    except Exception as e:
        print(f"Error: {e}")
        return jsonify({"fulfillmentText": f"Sorry, an error occurred: {e}"})

def construct_anilist_query(user_input, intent_name, genre):
    if intent_name == "AnimeRecommendation":
        if genre:
            return f"""
            query {{
              Page(page: 1, perPage: 1) {{
                media(search: "{user_input}", type: ANIME, genre_in: "{genre}") {{
                  id
                  title {{
                    romaji
                  }}
                  description
                  genres
                  averageScore
                  coverImage {{
                    large
                  }}
                }}
              }}
            }}
            """
        else:
            return f"""
            query {{
              Page(page: 1, perPage: 1) {{
                media(search: "{user_input}", type: ANIME) {{
                  id
                  title {{
                    romaji
                  }}
                  description
                  genres
                  averageScore
                  coverImage {{
                    large
                  }}
                }}
              }}
            }}
            """
    return "" # Or handle the case where it's not AnimeRecommendation intent


def make_anilist_request(query):
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json"
    }
    try:
        response = requests.post(ANILIST_API_URL, headers=headers, json={"query": query})
        return response
    except requests.exceptions.RequestException as e:
        print(f"Error making AniList request: {e}")
        return None

def format_response(anime):
    if anime:
        template = f"I recommend {anime['title']['romaji']}. It's a {', '.join(anime['genres'])} anime with an average score of {anime['averageScore']}. Here's a short description: {anime['description'][:200]}..."
        return template
    else:
        return "No anime found matching your criteria."