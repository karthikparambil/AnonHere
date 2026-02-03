from flask import Flask, request, jsonify
import redis
import os
import json
import time

app = Flask(__name__)

# Initialize Redis connection
# Vercel provides KV_URL, KV_REST_API_URL etc.
# We usually use the direct connection string for redis-py
redis_url = os.environ.get("KV_URL")
# If running locally or without DB, this will fail gracefully in the try/except blocks
r = redis.from_url(redis_url) if redis_url else None

@app.route('/api/chat', methods=['GET', 'POST'])
def chat():
    if not r:
        return jsonify({"messages": [{"user": "SYS", "text": "Database not connected. Please create Vercel KV.", "timestamp": time.time()}]})

    if request.method == 'POST':
        data = request.json
        if not data or 'text' not in data:
            return jsonify({"error": "No text"}), 400
        
        message = {
            "text": data.get('text')[:280], # Limit to 280 chars
            "user": data.get('user', 'Anon')[:6], # Limit ID length
            "timestamp": time.time()
        }
        
        # Store in Redis List 'chat_messages'
        # LPUSH adds to the head (newest first)
        r.lpush('chat_messages', json.dumps(message))
        # Keep only last 50 messages to save space
        r.ltrim('chat_messages', 0, 49)
        
        return jsonify({"status": "sent"})

    elif request.method == 'GET':
        # Get messages (0 to 49)
        raw_msgs = r.lrange('chat_messages', 0, 49)
        messages = [json.loads(m) for m in raw_msgs]
        return jsonify({"messages": messages})

# For Vercel Serverless, we expose 'app'
if __name__ == '__main__':
    app.run()