import os
import sqlite3
import json
import time
import random
from datetime import datetime, timedelta
from flask import Flask, request, session, redirect, url_for, render_template_string, jsonify

# Configuration
app = Flask(__name__)
app.secret_key = os.urandom(24)
DB_FILE = 'anonchat.db'

# --- Security / Rate Limiting ---
# Simple in-memory rate limiter. 
RATE_LIMITS = {}
# Active User Tracking: { 'room_id': { 'username': timestamp } }
# room_id is 'global' or the code (str)
LAST_SEEN = {}

def check_rate_limit(ident, action, limit, window):
    """
    Returns True if allowed, False if limit exceeded.
    """
    now = time.time()
    
    if ident not in RATE_LIMITS:
        RATE_LIMITS[ident] = {}
        
    if action not in RATE_LIMITS[ident]:
        RATE_LIMITS[ident][action] = []
        
    # Filter out old timestamps
    timestamps = RATE_LIMITS[ident][action]
    timestamps = [t for t in timestamps if now - t < window]
    RATE_LIMITS[ident][action] = timestamps
    
    if len(timestamps) >= limit:
        return False
        
    RATE_LIMITS[ident][action].append(now)
    return True

def update_presence(room_id, username):
    """Updates the last seen timestamp for a user in a room."""
    now = time.time()
    if room_id not in LAST_SEEN:
        LAST_SEEN[room_id] = {}
    LAST_SEEN[room_id][username] = now

def get_active_count(room_id):
    """Returns count of users seen in the last 10 seconds."""
    if room_id not in LAST_SEEN:
        return 0
    
    now = time.time()
    # Filter and count
    active_users = [u for u, t in LAST_SEEN[room_id].items() if now - t < 10]
    
    # Optional: cleanup old
    LAST_SEEN[room_id] = {u: t for u, t in LAST_SEEN[room_id].items() if now - t < 10}
    
    return len(active_users)

# --- Database Management ---
def get_db_connection():
    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    """Initialize the database with messages and rooms tables."""
    conn = get_db_connection()
    # For dev simplicity, we drop tables to handle schema changes
    conn.execute('DROP TABLE IF EXISTS messages')
    conn.execute('DROP TABLE IF EXISTS rooms')
    
    conn.execute('''
        CREATE TABLE rooms (
            code INTEGER PRIMARY KEY,
            name TEXT NOT NULL,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    
    conn.execute('''
        CREATE TABLE messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT NOT NULL,
            content TEXT NOT NULL,
            room_code INTEGER,
            timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    conn.commit()
    conn.close()

def cleanup_old_messages():
    """
    Deletes messages/rooms older than 1 hour.
    Also cleans up old rate limit data to prevent memory leak.
    """
    conn = get_db_connection()
    one_hour_ago = datetime.utcnow() - timedelta(hours=1)
    
    conn.execute("DELETE FROM messages WHERE timestamp < ?", (one_hour_ago,))
    conn.execute("DELETE FROM rooms WHERE created_at < ?", (one_hour_ago,))
    
    conn.commit()
    conn.close()
    
    # Simple rate limit cleanup
    now = time.time()
    for ident in list(RATE_LIMITS.keys()):
        empty = True
        for action in list(RATE_LIMITS[ident].keys()):
            RATE_LIMITS[ident][action] = [t for t in RATE_LIMITS[ident][action] if now - t < 300]
            if RATE_LIMITS[ident][action]:
                empty = False
            else:
                del RATE_LIMITS[ident][action]
        if empty:
            del RATE_LIMITS[ident]

# Initialize DB on start
if not os.path.exists(DB_FILE):
    init_db()

# --- Frontend Template (HTML/CSS/JS) ---
HTML_TEMPLATE = """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>AnonHere</title>
    <script src="https://cdn.tailwindcss.com"></script>
    <style>
        body { background-color: #000000; color: #ffffff; font-family: 'Courier New', monospace; }
        .scrollbar-hide::-webkit-scrollbar { display: none; }
        .scrollbar-hide { -ms-overflow-style: none; scrollbar-width: none; }
        .neon-text { text-shadow: 0 0 5px rgba(255, 255, 255, 0.7); }
        .msg-bubble { animation: fadeIn 0.3s ease-out; }
        @keyframes fadeIn { from { opacity: 0; transform: translateY(10px); } to { opacity: 1; transform: translateY(0); } }
        ::selection { background: #ffffff; color: #000000; }
        .shake { animation: shake 0.5s cubic-bezier(.36,.07,.19,.97) both; }
        @keyframes shake { 10%, 90% { transform: translate3d(-1px, 0, 0); } 20%, 80% { transform: translate3d(2px, 0, 0); } 30%, 50%, 70% { transform: translate3d(-4px, 0, 0); } 40%, 60% { transform: translate3d(4px, 0, 0); } }
    </style>
</head>
<body class="h-screen flex flex-col items-center justify-center {{ 'p-4' if not session.get('username') or not session.get('room_type') else '' }}">

    <!-- Login View (Set Identity) -->
    {% if not session.get('username') %}
    <div class="max-w-md w-full bg-black p-8 rounded-none border border-white shadow-[0_0_15px_rgba(255,255,255,0.2)]">
        <h1 class="text-3xl font-bold text-center mb-2 text-white neon-text tracking-tighter">ANON_HERE</h1>
        <p class="text-gray-400 text-center mb-6 text-xs uppercase tracking-widest">Ephemeral. Encrypted. Void.</p>
        <form action="/login" method="POST" class="space-y-4">
            <div>
                <label class="block text-[10px] uppercase tracking-widest text-gray-500 mb-1">Identity</label>
                <input type="text" name="username" placeholder="NAME" required maxlength="15"
                    class="w-full bg-black border border-gray-600 text-white p-3 rounded-none focus:outline-none focus:border-white focus:ring-0 transition font-mono uppercase">
            </div>
            <button type="submit" class="w-full bg-white hover:bg-gray-200 text-black font-bold py-3 rounded-none uppercase tracking-widest transition duration-200 border border-white">
                Enter
            </button>
        </form>
    </div>

    <!-- Lobby View (Choose Room) -->
    {% elif not session.get('room_type') %}
    <div class="max-w-md w-full bg-black p-8 rounded-none border border-white shadow-[0_0_15px_rgba(255,255,255,0.2)]">
        <h1 class="text-xl font-bold text-center mb-2 text-white tracking-widest uppercase">AnonHere</h1>
        <p class="text-gray-400 text-center mb-6 text-[10px] uppercase tracking-widest">Logged in as: <span class="text-white">{{ session['username'] }}</span></p>
        
        <!-- Flash Messages -->
        {% if get_flashed_messages() %}
        <div class="mb-4 text-center">
            <div class="text-red-500 text-xs border border-red-500 p-2 uppercase tracking-widest shake">
                {{ get_flashed_messages()[0] }}
            </div>
        </div>
        {% endif %}

        <div class="space-y-6">
            <!-- Global Option -->
            <a href="/join_global" class="block w-full text-center bg-transparent hover:bg-white hover:text-black border border-white text-white py-3 transition uppercase tracking-widest text-sm">
                Enter Global
            </a>

            <div class="border-t border-gray-800"></div>

            <!-- Create Room -->
            <form action="/create_room" method="POST" class="space-y-2">
                <label class="block text-[10px] uppercase tracking-widest text-gray-500">Create Private Room</label>
                <div class="flex space-x-2">
                    <input type="text" name="room_name" placeholder="ROOM NAME" required
                        class="flex-1 bg-black border border-gray-600 text-white p-2 text-sm focus:border-white outline-none uppercase font-mono">
                    <button type="submit" class="bg-gray-800 hover:bg-white hover:text-black border border-gray-600 hover:border-white text-white px-4 text-xs uppercase tracking-widest transition">
                        CREATE
                    </button>
                </div>
            </form>

            <div class="border-t border-gray-800"></div>

            <!-- Join Room -->
            <form action="/join_room" method="POST" class="space-y-2">
                <label class="block text-[10px] uppercase tracking-widest text-gray-500">Join Room</label>
                <div class="flex space-x-2">
                    <input type="number" name="room_code" placeholder="CODE (e.g. 123456)" required
                        class="flex-1 bg-black border border-gray-600 text-white p-2 text-sm focus:border-white outline-none font-mono">
                    <button type="submit" class="bg-gray-800 hover:bg-white hover:text-black border border-gray-600 hover:border-white text-white px-4 text-xs uppercase tracking-widest transition">
                        JOIN
                    </button>
                </div>
            </form>
            
            <a href="/logout" class="block text-center text-xs text-red-500 hover:text-red-400 uppercase tracking-widest mt-4">[ DISCONNECT ]</a>
        </div>
    </div>
    
    <!-- Chat View -->
    {% else %}
    <div class="w-full h-full flex flex-col bg-black overflow-hidden border-x border-white/10">
        
        <!-- Header -->
        <div class="bg-black p-4 border-b border-white flex justify-between items-center">
            <div class="flex flex-col">
                <div class="flex items-center space-x-2">
                    <div class="w-2 h-2 bg-white animate-pulse"></div>
                    <h1 class="font-bold text-white tracking-widest uppercase text-sm">
                        {% if session.get('room_code') %}
                            SECURE // {{ session['room_name'] }}
                        {% else %}
                            ANON_HERE // GLOBAL
                        {% endif %}
                    </h1>
                </div>
                <div class="flex space-x-4 mt-1">
                    {% if session.get('room_code') %}
                    <span class="text-[10px] text-gray-500 uppercase tracking-widest">FREQ CODE: <span class="text-white border border-gray-700 px-1">{{ session['room_code'] }}</span></span>
                    {% endif %}
                    <span class="text-[10px] text-gray-500 uppercase tracking-widest">NODES: <span id="node-count" class="text-white">1</span></span>
                </div>
            </div>
            <div class="flex items-center space-x-4">
                <span class="text-[10px] text-gray-400 uppercase tracking-wider hidden sm:inline">Node: <span class="text-white">{{ session['username'] }}</span></span>
                <a href="/leave_room" class="text-[10px] text-gray-500 hover:text-white uppercase tracking-wider border border-gray-800 px-2 py-1 hover:border-white transition">[EXIT NET]</a>
            </div>
        </div>

        <!-- Messages Area -->
        <div id="message-container" class="flex-1 overflow-y-auto p-4 space-y-4 scrollbar-hide">
            <div class="text-center py-10 text-gray-600 text-xs font-mono uppercase tracking-widest">
                Awaiting encrypted transmission...
            </div>
        </div>

        <!-- Input Area -->
        <div class="bg-black p-4 border-t border-white">
            <form id="chat-form" class="flex space-x-2">
                <input type="text" id="msg-input" placeholder="ENTER MESSAGE..." required autocomplete="off"
                    class="flex-1 bg-black border border-gray-600 text-white p-3 rounded-none focus:border-white focus:ring-0 outline-none font-mono">
                <button type="submit" class="bg-white hover:bg-gray-200 text-black px-6 py-2 rounded-none font-bold uppercase tracking-widest transition">
                    SEND
                </button>
            </form>
            <div id="status-msg" class="text-[10px] text-gray-600 mt-2 text-center uppercase tracking-widest">
                Data purge in 60m.
            </div>
        </div>
    </div>

    <script>
        const container = document.getElementById('message-container');
        const form = document.getElementById('chat-form');
        const input = document.getElementById('msg-input');
        const statusMsg = document.getElementById('status-msg');
        const nodeCount = document.getElementById('node-count');
        const currentUser = "{{ session['username'] }}";

        // Scroll to bottom helper
        function scrollToBottom() {
            container.scrollTop = container.scrollHeight;
        }

        // Fetch messages
        async function fetchMessages() {
            try {
                const response = await fetch('/api/messages');
                if (response.status === 429) return; 
                
                const data = await response.json();
                const messages = data.messages;
                
                // Update active node count
                if (data.active_count !== undefined && nodeCount) {
                    nodeCount.textContent = data.active_count;
                }
                
                const currentContent = messages.map(msg => msg.id).join(',');
                if (container.dataset.hash !== currentContent) {
                    container.dataset.hash = currentContent;
                    
                    if(messages.length === 0) {
                        container.innerHTML = '<div class="text-center py-10 text-gray-600 text-xs font-mono uppercase tracking-widest">Signal Silence.</div>';
                        return;
                    }

                    container.innerHTML = messages.map(msg => {
                        const isMe = msg.username === currentUser;
                        const time = new Date(msg.timestamp + "Z").toLocaleTimeString([], {hour: '2-digit', minute:'2-digit'});
                        
                        return `
                            <div class="flex flex-col ${isMe ? 'items-end' : 'items-start'} msg-bubble">
                                <div class="text-[10px] text-gray-500 mb-1 px-1 font-mono uppercase">
                                    ${isMe ? 'YOU' : msg.username} <span class="text-gray-700">|</span> ${time}
                                </div>
                                <div class="${isMe ? 'bg-white text-black border border-white' : 'bg-black text-white border border-white'} max-w-[80%] px-4 py-2 rounded-none shadow-none text-sm break-words font-mono">
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

        form.addEventListener('submit', async (e) => {
            e.preventDefault();
            const content = input.value;
            if (!content) return;
            
            input.value = ''; 
            statusMsg.textContent = "Data purge in 60m.";
            statusMsg.classList.remove('text-red-500');
            
            const res = await fetch('/api/messages', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ content: content })
            });
            
            if (res.status === 429) {
                statusMsg.textContent = "SLOW DOWN // TRANSMISSION RATE EXCEEDED";
                statusMsg.classList.add('text-red-500', 'shake');
                setTimeout(() => statusMsg.classList.remove('shake'), 500);
            }
            
            fetchMessages(); 
        });

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
    if 'username' in session and 'room_type' in session:
        return render_template_string(HTML_TEMPLATE)
    if 'username' in session:
        return render_template_string(HTML_TEMPLATE) # Lobby
    return render_template_string(HTML_TEMPLATE) # Login

@app.route('/login', methods=['POST'])
def login():
    username = request.form.get('username')
    if username:
        session['username'] = username
    return redirect(url_for('home'))

@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('home'))

@app.route('/join_global')
def join_global():
    session['room_type'] = 'global'
    session.pop('room_code', None)
    session.pop('room_name', None)
    return redirect(url_for('home'))

@app.route('/create_room', methods=['POST'])
def create_room():
    room_name = request.form.get('room_name')
    if not room_name:
        return redirect(url_for('home'))
        
    # Generate 6 digit code
    while True:
        code = random.randint(100000, 999999)
        conn = get_db_connection()
        exists = conn.execute('SELECT 1 FROM rooms WHERE code = ?', (code,)).fetchone()
        if not exists:
            conn.execute('INSERT INTO rooms (code, name) VALUES (?, ?)', (code, room_name))
            conn.commit()
            conn.close()
            break
        conn.close()
    
    session['room_type'] = 'private'
    session['room_code'] = code
    session['room_name'] = room_name
    return redirect(url_for('home'))

@app.route('/join_room', methods=['POST'])
def join_room():
    code = request.form.get('room_code')
    if not code:
        return redirect(url_for('home'))
        
    # Rate Limit: 5 failed attempts per minute per IP
    ip = request.remote_addr
    if not check_rate_limit(ip, 'join_fail', 5, 60):
        # Using flask flash would be ideal but sticking to simple rendered tempalte
        Flask.flash = lambda self, m: None # Mock if needed or just use flash
        # Using a simple hack to pass error via flash (which needs secret key)
        from flask import flash
        flash("SECURITY LOCKOUT // TOO MANY FAILED ATTEMPTS")
        return redirect(url_for('home'))
    
    conn = get_db_connection()
    room = conn.execute('SELECT * FROM rooms WHERE code = ?', (code,)).fetchone()
    conn.close()
    
    if room:
        session['room_type'] = 'private'
        session['room_code'] = room['code']
        session['room_name'] = room['name']
    else:
        from flask import flash
        flash("INVALID FREQUENCY CODE")
    
    return redirect(url_for('home'))

@app.route('/leave_room')
def leave_room():
    session.pop('room_type', None)
    session.pop('room_code', None)
    session.pop('room_name', None)
    return redirect(url_for('home'))

@app.route('/api/messages', methods=['GET', 'POST'])
def api_messages():
    cleanup_old_messages()
    
    if 'username' not in session:
        return jsonify({"error": "Unauthorized"}), 401
    
    room_code = session.get('room_code')
    
    # Track Presence
    room_id = str(room_code) if room_code else 'global'
    update_presence(room_id, session['username'])
    
    if request.method == 'POST':
        # Rage Limit: 5 messages per 10 seconds.
        ip = request.remote_addr
        
        if not check_rate_limit(ip, 'send_msg', 5, 10):
            return jsonify({"error": "Rate limit exceeded"}), 429

        data = request.get_json()
        content = data.get('content')
        
        if content:
            conn = get_db_connection()
            conn.execute('INSERT INTO messages (username, content, room_code, timestamp) VALUES (?, ?, ?, ?)',
                         (session['username'], content, room_code, datetime.utcnow()))
            conn.commit()
            conn.close()
            return jsonify({"status": "sent"})

    # GET request
    conn = get_db_connection()
    if room_code:
        messages = conn.execute('SELECT * FROM messages WHERE room_code = ? ORDER BY timestamp ASC', (room_code,)).fetchall()
    else:
        messages = conn.execute('SELECT * FROM messages WHERE room_code IS NULL ORDER BY timestamp ASC').fetchall()
    conn.close()
    
    messages_list = [dict(ix) for ix in messages]
    
    # Return active count
    active_count = get_active_count(room_id)
    
    return jsonify({
        "messages": messages_list,
        "active_count": active_count
    })

if __name__ == '__main__':
    # Force init_db to apply new schema for this session
    init_db()
    app.run(debug=True, port=5000)
