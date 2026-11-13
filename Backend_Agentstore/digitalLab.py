from flask import Flask, render_template, jsonify
import requests

app = Flask(__name__)

# Replace this with your actual API URL
DIRECT_LINE_TOKEN_URL = "https://default534253fcdfb6462fb5cacbe81939f5.ee.environment.api.powerplatform.com/powervirtualagents/botsbyschema/crad5_agent1/directline/token?api-version=2022-03-01-preview"

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/get_token')
def get_token():
    try:
        response = requests.get(DIRECT_LINE_TOKEN_URL)
        data = response.json()
        return jsonify({"token": data.get("token")})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5002, debug=True)
