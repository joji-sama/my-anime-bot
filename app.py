from flask import Flask, request, jsonify
import requests
import google.generativeai as genai
import os
import logging
import random
import json

app = Flask(__name__)

# Configure Gemini
genai.configure(api_key=os.getenv("GEMINI_API_KEY"))
model = genai.GenerativeModel('gemini-pro')

# Configure logging
logging.basicConfig(level=logging.INFO)

def sanitize_genres(genres):
    """Ensure proper genre formatting for AniList API"""
    return [g.lower().strip() for g in genres if g.strip()]

def generate_nikko_response(text):
    prompt = f"""
    Parse this anime recommendation request into JSON format with: genres, themes, and search terms.
    Return ONLY valid JSON with lowercase values. Example:
    {{ "genres": ["action"], "themes": ["friendship"], "search": "ninja" }}

    Request: {text}
    """
    try:
        response = model.generate_content(prompt)
        return response.text
    except Exception as e:
        logging.error(f"Gemini error: {str(e)}")
        return None

def query_anilist(genres=None, search=None):
    query = '''
    query ($search: String, $genre_in: [String]) {
        Page(page: 1, perPage: 10) {
            media(
                type: ANIME
                search: $search
                genre_in: $genre_in
                sort: POPULARITY_DESC
            ) {
                id
                title {
                    english
                    romaji
                }
                genres
                description(asHtml: false)
                averageScore
                episodes
                siteUrl
            }
        }
    }
    '''

    variables = {
        "search": search,
        "genre_in": sanitize_genres(genres) if genres else []
    }

    try:
        response = requests.post(
            'https://graphql.anilist.co',
            json={'query': query, 'variables': variables},
            timeout=10
        )
        response.raise_for_status()
        data = response.json()
        
        if 'errors' in data:
            for error in data['errors']:
                logging.error(f"AniList API Error: {error['message']}")
            return None
            
        return data['data']['Page']['media']
    except Exception as e:
        logging.error(f"AniList request failed: {str(e)}")
        return None

def generate_sassy_response(anime_list, parsed_data):
    if not anime_list:
        return "Oh please, your taste is too unique even for me. Try asking for something that actually exists."
    
    recommendations = "\n".join(
        f"- {anime['title']['english'] or anime['title']['romaji']} ({anime['averageScore']}/100)"
        for anime in anime_list[:5]
    )
    
    sassy_comments = [
        "Took you long enough to ask. Here:",
        "Ugh, fine. These might suit your basic taste:",
        "I guess these mediocre picks might work:"
    ]
    
    return f"{random.choice(sassy_comments)}\n{recommendations}"

@app.route('/webhook', methods=['POST'])
def chat_handler():
    user_message = request.json.get('message', '')
    
    # Parse user input with Gemini
    try:
        parsed_data = json.loads(generate_nikko_response(user_message))
        logging.info(f"Parsed data: {parsed_data}")
        
        # Sanitize and validate genres
        genres = sanitize_genres(parsed_data.get('genres', []))
        search = parsed_data.get('search', '').strip()

        # Query AniList
        anime_list = query_anilist(
            genres=genres,
            search=search if search else None
        )
        
        # Generate response
        response = generate_sassy_response(anime_list, parsed_data) if anime_list else \
            "Wow, even the anime gods are ignoring you. Try again with less obscure preferences."
            
    except Exception as e:
        logging.error(f"Processing error: {str(e)}")
        response = "AniList is being tsundere 🎌 Try again later!"
    
    return jsonify({'response': response})

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)
