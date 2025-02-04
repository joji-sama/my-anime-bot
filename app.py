from flask import Flask, request, jsonify
from google.generativeai import GenerativeModel
import os
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address

app = Flask(__name__)

# Initialize rate limiter
limiter = Limiter(
    app=app,
    key_func=get_remote_address,
    default_limits=["200 per day", "50 per hour"]
)

# Initialize Google Gemini
gemini_api_key = os.getenv('GEMINI_API_KEY')
if not gemini_api_key:
    raise ValueError("GEMINI_API_KEY environment variable not set.")

model = GenerativeModel('gemini-pro')

# Structured prompt template
PROMPT_TEMPLATE = """
You are an expert anime recommendation assistant. For the following query, suggest 3-5 relevant anime titles with these details for each:
- Title (English and Japanese if available)
- Genre
- Brief description (without spoilers)
- Reason for recommendation
- Appropriate age rating

Format the response in markdown without headers. Use bullet points with this structure:
- **Title**: [English] / [Japanese] (Age Rating)
  - Genre: [Genre1], [Genre2]
  - Description: [Brief description]
  - Why Watch: [Reason]

Query: {query}
"""

@app.route('/webhook', methods=['POST'])
@limiter.limit("10 per minute")  # Rate limiting
def webhook():
    try:
        data = request.get_json()
        user_query = data.get('queryResult', {}).get('queryText', '').strip()
        
        # Validate input
        if not user_query or len(user_query) > 200:
            return jsonify({
                "fulfillmentText": "Please provide a valid anime request (under 200 characters)."
            })

        # Generate recommendations
        response = model.generate_content(
            PROMPT_TEMPLATE.format(query=user_query),
            safety_settings={
                'HARM_CATEGORY_HATE_SPEECH': 'BLOCK_NONE',
                'HARM_CATEGORY_DANGEROUS_CONTENT': 'BLOCK_NONE'
            }
        )

        # Format response for Dialogflow
        return jsonify({
            "fulfillmentText": format_response(response.text),
            "payload": {
                "google": {
                    "richResponse": {
                        "items": [{
                            "simpleResponse": {
                                "textToSpeech": "Here are my recommendations:",
                                "displayText": format_response(response.text)
                            }
                        }]
                    }
                }
            }
        })

    except Exception as e:
        app.logger.error(f"Error processing request: {str(e)}")
        return jsonify({
            "fulfillmentText": "Sorry, I encountered an error processing your request. Please try again later."
        })

def format_response(text: str) -> str:
    """Clean up Gemini's response and format for chat"""
    # Remove markdown formatting
    cleaned = text.replace("**", "").replace("- ", "• ")
    # Truncate to 4096 characters (Dialogflow limit)
    return cleaned[:4095]

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=os.getenv('FLASK_DEBUG', 'false').lower() == 'true')
