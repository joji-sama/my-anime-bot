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
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('debug.log'),
        logging.StreamHandler()
    ]
)

def sanitize_genres(genres):
    """Ensure proper genre formatting for AniList API"""
    return [str(g).lower().strip() for g in genres if g and str(g).strip()]

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
        "search": search.strip() if search else None,
        "genre_in": sanitize_genres(genres) if genres else []
    }

    try:
        response = requests.post(
            'https://graphql.anilist.co',
            json={'query': query, 'variables': variables},
            timeout=15
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

def generate_sassy_response(anime_list):
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

@app.route('/')
def health_check():
    return "Nikko is ready to roast your anime tastes! 🎌", 200

@app.route('/webhook', methods=['POST'])
def chat_handler():
    try:
        dialogflow_request = request.json
        logging.info(f"Raw Dialogflow request: {json.dumps(dialogflow_request, indent=2)}")
        
        # Extract parameters from Dialogflow
        query_result = dialogflow_request.get('queryResult', {})
        user_message = query_result.get('queryText', '')
        parameters = query_result.get('parameters', {})
        
        # Try using Dialogflow parameters first
        genres = parameters.get('AnimeGenre', [])
        search = parameters.get('search-term', '')
        
        if not genres and not search:
            # Fallback to Gemini parsing
            gemini_response = generate_nikko_response(user_message)
            if not gemini_response:
                raise ValueError("Empty response from Gemini")
                
            parsed_data = json.loads(gemini_response)
            genres = parsed_data.get('genres', [])
            search = parsed_data.get('search', '')
            logging.info(f"Gemini parsed data: {parsed_data}")

        # Sanitize inputs
        genres = sanitize_genres(genres)
        search = search.strip() if search else None

        # Query AniList
        anime_list = query_anilist(
            genres=genres if genres else None,
            search=search
        )
        
        # Generate response
        response_text = generate_sassy_response(anime_list) if anime_list else \
            "Wow, even the anime gods are ignoring you. Try again with less obscure preferences."
            
    except json.JSONDecodeError as e:
        logging.error(f"JSON parsing failed: {str(e)}")
        response_text = "Ugh, even my brain is glitching. Try again, human."
    except Exception as e:
        logging.error(f"Critical error: {str(e)}", exc_info=True)
        response_text = "AniList is being tsundere 🎌 Try again later!"

    # Dialogflow-compliant response format
    return jsonify({
        "fulfillmentText": response_text,
        "fulfillmentMessages": [{
            "text": {
                "text": [response_text]
            }
        }],
        "source": "webhook-nikko",
        "payload": {
            "google": {
                "expectUserResponse": False
            }
        }
    })

if __name__ == '__main__':
    port = int(os.environ.get("PORT", 5000))
    app.run(host='0.0.0.0', port=port)
