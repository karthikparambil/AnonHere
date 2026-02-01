import os
import sqlite3
import json
import time
from datetime import datetime, timedelta
from flask import Flask, request, session, redirect, url_for, render_template_string, jsonify

# Try importing psycopg2 for Vercel Postgres; pass if not found (local use)
try:
    import psycopg2
    from psycopg2.extras import RealDictCursor
except ImportError:
    psycopg2 = None

# Configuration
app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', os.urandom(24)) # Use env var for key in prod
DB_FILE = 'anonchat.db'

# --- Database Management ---
# Supports both SQLite (Local) and PostgreSQL (Vercel)

def get_db_connection():
    # Check for Vercel Postgres Environment Variable
    if os.environ.get('POSTGRES_URL'):
        if not psycopg2:
            raise ImportError("psycopg2 is required for Vercel Postgres but not installed.")
        conn = psycopg2.connect(os.environ['POSTGRES_URL'])
        return conn, 'postgres'
    else:
        conn = sqlite3.connect(DB_FILE)
        conn.row_factory = sqlite3.Row
        return conn, 'sqlite'

def execute_query(query, args=(), fetch=False):
    conn, db_type = get_db_connection()
    try:
        if db_type == 'postgres':
            # Use RealDictCursor for Postgres to get dictionary-like rows
            cur = conn.cursor(cursor_factory=RealDictCursor)
            # Convert SQLite placeholder (?) to Postgres placeholder (%s)
            query = query.replace('?', '%s')
        else:
            cur = conn.cursor()

        cur.execute(query, args)
        
        if fetch:
            result = cur.fetchall()
            # Convert rows to standard dicts
            return [dict(row) for row in result]
        
        conn.commit()
        return None
    finally:
        conn.close()

def init_db():
    """Initialize the database with the messages table."""
    conn, db_type = get_db_connection()
    try:
        cur = conn.cursor()
        
        if db_type == 'postgres':
            # Postgres Syntax
            cur.execute('''
                CREATE TABLE IF NOT EXISTS messages (
                    id SERIAL PRIMARY KEY,
                    username TEXT NOT NULL,
                    content TEXT NOT NULL,
                    timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            ''')
        else:
            # SQLite Syntax
            cur.execute('''
                CREATE TABLE IF NOT EXISTS messages (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    username TEXT NOT NULL,
                    content TEXT NOT NULL,
                    timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
                )
            ''')
        conn.commit()
    finally:
        conn.close()

def cleanup_old_messages():
    """Deletes messages older than 1 hour."""
    # Calculate time 1 hour ago
    one_hour_ago = datetime.utcnow() - timedelta(hours=1)
    
    # execute_query handles the ? vs %s conversion automatically
    execute_query("DELETE FROM messages WHERE timestamp < ?", (one_hour_ago,))

# Initialize DB on start (only if local file missing or just to be safe)
# In Vercel, this runs on every boot, checking "IF NOT EXISTS"
try:
    init_db()
except Exception as e:
    print(f"DB Init Warning (might be connection issue): {e}")

# --- Frontend Template (HTML/CSS/JS) ---
# Embedded here to keep the application in a single file
HTML_TEMPLATE = """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>AnonHere | Ephemeral Chat</title>
    <script src="https://cdn.tailwindcss.com"></script>
    <style>
        body { background-color: #0f172a; color: #e2e8f0; font-family: 'Courier New', monospace; }
        .scrollbar-hide::-webkit-scrollbar { display: none; }
        .scrollbar-hide { -ms-overflow-style: none; scrollbar-width: none; }
        .neon-text { text-shadow: 0 0 10px rgba(56, 189, 248, 0.5); }
        .msg-bubble { animation: fadeIn 0.3s ease-out; }
        @keyframes fadeIn { from { opacity: 0; transform: translateY(10px); } to { opacity: 1; transform: translateY(0); } }
    </style>
</head>
<body class="h-screen flex flex-col items-center justify-center p-4">

    <!-- Login View -->
    {% if not session.get('username') %}
    <div class="max-w-md w-full bg-slate-800 p-8 rounded-lg shadow-2xl border border-slate-700">
        <h1 class="text-3xl font-bold text-center mb-2 text-sky-400 neon-text">AnonHere</h1>
        <p class="text-slate-400 text-center mb-6 text-sm">Nothing is permanent. Messages vanish in 1 hour.</p>
        <form action="/login" method="POST" class="space-y-4">
            <div>
                <label class="block text-xs uppercase tracking-wider text-slate-500 mb-1">Identity</label>
                <input type="text" name="username" placeholder="Choose a code name..." required maxlength="15"
                    class="w-full bg-slate-900 border border-slate-700 text-white p-3 rounded focus:outline-none focus:border-sky-500 focus:ring-1 focus:ring-sky-500 transition">
            </div>
            <button type="submit" class="w-full bg-sky-600 hover:bg-sky-500 text-white font-bold py-3 rounded transition duration-200">
                ENTER THE VOID
            </button>
        </form>
    </div>
    
    <!-- Chat View -->
    {% else %}
    <div class="w-full max-w-2xl h-full max-h-[90vh] flex flex-col bg-slate-800 rounded-lg shadow-2xl border border-slate-700 overflow-hidden">
        
        <!-- Header -->
        <div class="bg-slate-900 p-4 border-b border-slate-700 flex justify-between items-center">
            <div class="flex items-center space-x-2">
                <div class="w-2 h-2 rounded-full bg-green-500 animate-pulse"></div>
                <h1 class="font-bold text-sky-400">AnonHere</h1>
            </div>
            <div class="flex items-center space-x-4">
                <span class="text-xs text-slate-500">Identity: <span class="text-slate-300">{{ session['username'] }}</span></span>
                <a href="/logout" class="text-xs text-red-400 hover:text-red-300">Disconnect</a>
            </div>
        </div>

        <!-- Messages Area -->
        <div id="message-container" class="flex-1 overflow-y-auto p-4 space-y-4 scrollbar-hide">
            <div class="text-center py-10 text-slate-600 text-sm italic">
                Scanning for encrypted signals...
            </div>
        </div>

        <!-- Input Area -->
        <div class="bg-slate-900 p-4 border-t border-slate-700">
            <form id="chat-form" class="flex space-x-2">
                <input type="text" id="msg-input" placeholder="Broadcast message..." required autocomplete="off"
                    class="flex-1 bg-slate-800 border-none text-white p-3 rounded focus:ring-2 focus:ring-sky-500 outline-none">
                <button type="submit" class="bg-sky-600 hover:bg-sky-500 text-white px-6 py-2 rounded font-bold transition">
                    SEND
                </button>
            </form>
            <div class="text-[10px] text-slate-600 mt-2 text-center">
                Messages auto-delete after 60 minutes.
            </div>
        </div>
    </div>

    <script>
        const container = document.getElementById('message-container');
        const form = document.getElementById('chat-form');
        const input = document.getElementById('msg-input');
        let lastTimestamp = null;
        const currentUser = "{{ session['username'] }}";

        // Scroll to bottom helper
        function scrollToBottom() {
            container.scrollTop = container.scrollHeight;
        }

        // Fetch messages
        async function fetchMessages() {
            try {
                const response = await fetch('/api/messages');
                const messages = await response.json();
                
                // If it's the first load, clear the loading text
                if (messages.length > 0 && !lastTimestamp) {
                    container.innerHTML = ''; 
                }

                // Simply re-render or append. For simplicity in this demo, we re-render list if changes detected
                // In a prod app, you'd only append new ones. Here we just wipe and rebuild to sync deletions.
                const currentContent = messages.map(msg => msg.id).join(',');
                if (container.dataset.hash !== currentContent) {
                    container.dataset.hash = currentContent;
                    
                    if(messages.length === 0) {
                        container.innerHTML = '<div class="text-center py-10 text-slate-600 text-sm italic">No active signals. The void is quiet.</div>';
                        return;
                    }

                    container.innerHTML = messages.map(msg => {
                        const isMe = msg.username === currentUser;
                        const time = new Date(msg.timestamp).toLocaleTimeString([], {hour: '2-digit', minute:'2-digit'});
                        
                        return `
                            <div class="flex flex-col ${isMe ? 'items-end' : 'items-start'} msg-bubble">
                                <div class="text-[10px] text-slate-500 mb-1 px-1">
                                    ${isMe ? 'You' : msg.username} • ${time}
                                </div>
                                <div class="${isMe ? 'bg-sky-600 text-white' : 'bg-slate-700 text-slate-200'} max-w-[80%] px-4 py-2 rounded-2xl ${isMe ? 'rounded-tr-none' : 'rounded-tl-none'} shadow-sm text-sm break-words">
                                    ${msg.content}
                                </div>
                            </div>
                        `;
                    }).join('');
                    
                    scrollToBottom();
                }
            } catch (e) {
                console.error("Connection lost...", e);
            }
        }

        // Send Message
        form.addEventListener('submit', async (e) => {
            e.preventDefault();
            const content = input.value;
            if (!content) return;
            
            input.value = ''; // Clear immediately
            
            await fetch('/api/messages', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ content: content })
            });
            
            fetchMessages(); // Refresh immediately
        });

        // Poll every 2 seconds
        setInterval(fetchMessages, 2000);
        fetchMessages();
    </script>
    {% endif %}
</body>
</html>
"""

# --- Routes ---

@app.route('/')
def home():
    if 'username' in session:
        return render_template_string(HTML_TEMPLATE)
    return render_template_string(HTML_TEMPLATE)

@app.route('/login', methods=['POST'])
def login():
    username = request.form.get('username')
    if username:
        session['username'] = username
    return redirect(url_for('home'))

@app.route('/logout')
def logout():
    session.pop('username', None)
    return redirect(url_for('home'))

@app.route('/api/messages', methods=['GET', 'POST'])
def api_messages():
    # Enforce cleanup on every interaction
    cleanup_old_messages()
    
    if request.method == 'POST':
        if 'username' not in session:
            return jsonify({"error": "Unauthorized"}), 401
            
        data = request.get_json()
        content = data.get('content')
        
        if content:
            # Store timestamp in UTC
            execute_query(
                'INSERT INTO messages (username, content, timestamp) VALUES (?, ?, ?)',
                (session['username'], content, datetime.utcnow())
            )
            return jsonify({"status": "sent"})

    # GET request
    messages_list = execute_query(
        'SELECT * FROM messages ORDER BY timestamp ASC',
        fetch=True
    )
    
    return jsonify(messages_list)

if __name__ == '__main__':
    app.run(debug=True, port=5000)
