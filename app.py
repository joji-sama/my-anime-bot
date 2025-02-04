from flask import Flask, request, jsonify
from google.generativeai import text_generation
import os

app = Flask(__name__)

# Initialize Google Gemini
gemini_api_key = os.getenv('GEMINI_API_KEY')
if not gemini_api_key:
    raise ValueError("GEMINI_API_KEY environment variable not set.")

# Webhook for Dialogflow/Google AI integration
@app.route('/webhook', methods=['POST'])
def webhook():
    data = request.get_json()
    user_query = data.get('queryResult', {}).get('queryText')

    if not user_query:
        return jsonify({"fulfillmentText": "Sorry, I didn't understand your request."})

    prompt = f"""
    Based on this user query, recommend 3 anime.  Provide the title and a short description for each.

    Query: {user_query}
    """
    try:
        gemini_response = text_generation.generate_text(
            model="gemini-pro",
            prompt=prompt,
            api_key=gemini_api_key
        )
        recommendations = gemini_response.result  # Get the raw Gemini response

        # Basic formatting (you can improve this)
        response_text = f"Here are some anime recommendations based on your query:\n\n{recommendations}"

        return jsonify({"fulfillmentText": response_text})

    except Exception as e:
        print(f"Error with Gemini: {e}")
        return jsonify({"fulfillmentText": "Error processing your request."})

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=True)  # Set debug=False for production
