import os
import json
import re
import sqlite3
from datetime import datetime, timedelta
from collections import defaultdict

from flask import Flask, render_template, request, redirect, url_for, session, flash
from flask_bcrypt import Bcrypt
from dotenv import load_dotenv
import openai
from langchain.vectorstores import Chroma
from langchain_openai import OpenAIEmbeddings
from langchain.text_splitter import RecursiveCharacterTextSplitter
from langchain.schema import Document

# --- Config ---
load_dotenv()
app = Flask(__name__)
app.secret_key = os.getenv("SECRET_KEY", "super-secret")
bcrypt = Bcrypt(app)
openai.api_key = os.getenv("OPENAI_API_KEY")
DB_PATH = "user_progress.db"
CHROMA_DIR = "chroma_db"

# --- Embeddings and Vectorstore Setup ---
embedding = OpenAIEmbeddings()
vectorstore = Chroma(embedding_function=embedding, persist_directory=CHROMA_DIR)

# --- DB Helper ---
def get_db():
    return sqlite3.connect(DB_PATH)

# --- Get or Generate Roadmap ---
def get_or_generate_roadmap(username, career):
    conn = get_db()
    c = conn.cursor()

    docs = vectorstore.similarity_search(career, k=1)
    if docs:
        roadmap_text = docs[0].page_content
        source = "cache"
    else:
        prompt = f"""
        Create a personalized learning roadmap for someone who wants to become a {career}.
        Break it down into Beginner, Intermediate, and Advanced.
        For each level, return:
        {{
            "level": "Beginner",
            "duration": "2 weeks",
            "topics": ["HTML", "CSS", "JavaScript"],
            "project": "Build a personal blog",
            "resources": ["https://codecademy.com", "https://w3schools.com"]
        }}
        Format the result as a JSON array. No commentary.
        """
        response = openai.chat.completions.create(
            model="gpt-4",
            messages=[
                {"role": "system", "content": "You are an AI roadmap planner."},
                {"role": "user", "content": prompt}
            ]
        )
        raw = response.choices[0].message.content.strip()

        match = re.search(r"\[\s*{.*?}\s*](?=\s*[\]\}])?", raw, re.DOTALL)
        if not match:
            raise ValueError("AI response did not contain a valid JSON array.")

        roadmap_json = json.loads(match.group(0))
        roadmap_text = json.dumps(roadmap_json, indent=2)

        c.execute("INSERT INTO chats (username, message, response) VALUES (?, ?, ?)",
                  (username, prompt, roadmap_text))
        conn.commit()

        doc = Document(page_content=roadmap_text, metadata={"career": career})
        splitter = RecursiveCharacterTextSplitter(chunk_size=500, chunk_overlap=50)
        chunks = splitter.split_documents([doc])
        vectorstore.add_documents(chunks)
        vectorstore.persist()
        source = "openai"

    conn.close()
    return roadmap_json, source

# --- Routes ---
@app.route("/", methods=["GET", "POST"])
@app.route("/login", methods=["GET", "POST"])
def login():
    if 'username' in session:
        return redirect(url_for("dashboard"))

    conn = get_db()
    c = conn.cursor()
    c.execute("""CREATE TABLE IF NOT EXISTS users (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        username TEXT UNIQUE,
        password TEXT,
        career_choice TEXT,
        current_step TEXT
    )""")

    if request.method == "POST":
        username = request.form.get("username")
        password = request.form.get("password")
        mode = request.form.get("mode")

        if username and password:
            if mode == "Login":
                c.execute("SELECT * FROM users WHERE username = ? AND password = ?", (username, password))
                user = c.fetchone()
                if user:
                    session["username"] = username
                    flash("Logged in successfully", "info")
                    return redirect(url_for("dashboard"))
                else:
                    flash("Invalid credentials", "danger")
            else:
                try:
                    c.execute("INSERT INTO users (username, password, career_choice, current_step) VALUES (?, ?, ?, ?)",
                              (username, password, '', 'Getting Started'))
                    conn.commit()
                    flash("Account created! Please log in.", "success")
                    return redirect(url_for("login"))
                except sqlite3.IntegrityError:
                    flash("Username already exists", "warning")
        else:
            flash("Please enter both fields", "warning")

    conn.close()
    return render_template("login.html")

@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))

@app.route("/dashboard", methods=['GET', 'POST'])
def dashboard():
    if 'username' not in session:
        return redirect(url_for('login'))

    username = session['username']
    message = None
    roadmap = []
    selected_chat = None
    chat_entries = []
    career = request.form.get('career_query', 'Data Analyst')

    conn = get_db()
    c = conn.cursor()

    c.execute("""CREATE TABLE IF NOT EXISTS roadmap_progress (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        username TEXT,
        career TEXT,
        level TEXT,
        status TEXT,
        start_date DATE,
        end_date DATE
    )""")

    c.execute("""CREATE TABLE IF NOT EXISTS chats (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        username TEXT,
        message TEXT,
        response TEXT,
        timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
    )""")

    if request.method == 'POST' and 'generate' in request.form:
        try:
            roadmap, source = get_or_generate_roadmap(username, career)
            for item in roadmap:
                c.execute("""
                    INSERT INTO roadmap_progress (username, career, level, status, start_date, end_date)
                    VALUES (?, ?, ?, ?, ?, ?)""",
                          (username, career, item['level'], 'not_started', None, None))
            conn.commit()
            message = f"Roadmap generated from {source}"
        except Exception as e:
            message = f"Error: {str(e)}"

    c.execute("SELECT response FROM chats WHERE username = ? AND message LIKE ? ORDER BY timestamp DESC LIMIT 1",
              (username, f"%{career}%"))
    chat_data = c.fetchone()
    if chat_data:
        try:
            roadmap = json.loads(chat_data[0])
        except json.JSONDecodeError:
            roadmap = []

    c.execute("SELECT level, status, start_date, end_date FROM roadmap_progress WHERE username = ? AND career = ?",
              (username, career))
    progress = c.fetchall()

    c.execute("SELECT id, message, timestamp FROM chats WHERE username = ? ORDER BY timestamp DESC", (username,))
    chat_entries = c.fetchall()

    selected = request.args.get('chat_id')
    if selected:
        c.execute("SELECT message, response FROM chats WHERE id = ?", (selected,))
        selected_chat = c.fetchone()

    grouped_chats = defaultdict(list)
    for chat_id, message, timestamp in chat_entries:
        date_key = datetime.strptime(timestamp, "%Y-%m-%d %H:%M:%S").strftime("%B %d, %Y")
        grouped_chats[date_key].append({
            "id": chat_id,
            "summary": message[:30] + "...",
            "timestamp": timestamp
        })

    conn.close()
    return render_template('dashboard.html', username=username, message=message,
                           roadmap=roadmap, progress=progress,
                           career=career, chat_entries=chat_entries,
                           selected_chat=selected_chat, grouped_chats=grouped_chats)

if __name__ == '__main__':
    app.run(debug=True)
