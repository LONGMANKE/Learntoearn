# Learn-to-Earn Pathway Agent with Login, Planner & Dynamic Roadmap Tracking

import os
import sqlite3
import streamlit as st
from dotenv import load_dotenv
import openai
import json
import re
from datetime import datetime, timedelta

# Load environment variables
load_dotenv()
openai.api_key = os.getenv("OPENAI_API_KEY")

# Initialize SQLite database
conn = sqlite3.connect("user_progress.db", check_same_thread=False)
c = conn.cursor()

# Tables
c.execute('''CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    username TEXT UNIQUE,
    career_choice TEXT,
    current_step TEXT
)''')
c.execute('''CREATE TABLE IF NOT EXISTS roadmap_progress (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    username TEXT,
    career TEXT,
    level TEXT,
    status TEXT,
    start_date DATE,
    end_date DATE
)''')
c.execute('''CREATE TABLE IF NOT EXISTS chats (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    username TEXT,
    message TEXT,
    response TEXT,
    timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
)''')
conn.commit()

# --- Streamlit Setup ---
st.set_page_config(page_title="Learn-to-Earn Pathway Agent")
st.title("🎓 Learn-to-Earn Pathway Agent")

# --- Login ---
if "logged_in" not in st.session_state:
    st.session_state.logged_in = False
if "username" not in st.session_state:
    st.session_state.username = ""

if not st.session_state.logged_in:
    with st.form("login_form"):
        username = st.text_input("Enter your username")
        password = st.text_input("Enter your password", type="password")
        submitted = st.form_submit_button("Login")

        if submitted and username and password:
            c.execute("SELECT * FROM users WHERE username = ? AND career_choice = ?", (username, password))
            result = c.fetchone()
            if result:
                st.session_state.username = username
                st.session_state.logged_in = True
                st.success(f"Welcome back, {username}!")
                st.rerun()
            else:
                st.error("Invalid username or password.")
        elif submitted:
            st.error("Please enter both username and password.")

    with st.expander("Don't have an account? Create one"):
        new_username = st.text_input("New Username", key="signup_user")
        new_password = st.text_input("New Password", type="password", key="signup_pass")
        if st.button("Sign Up"):
            if new_username and new_password:
                try:
                    c.execute("INSERT INTO users (username, career_choice, current_step) VALUES (?, ?, ?)",
                              (new_username, new_password, "Getting Started"))
                    conn.commit()
                    st.success("Account created! Please log in.")
                except sqlite3.IntegrityError:
                    st.warning("Username already exists.")
            else:
                st.error("Please enter both username and password.")
    st.stop()

username = st.session_state.username
st.sidebar.write(f"👋 Logged in as: {username}")
if st.sidebar.button("Logout"):
    st.session_state.logged_in = False
    st.session_state.username = ""
    st.rerun()

# --- Roadmap Generation ---
st.write("Choose a career path and let the agent guide you from learning to earning.")
career = st.selectbox("Choose a career path:", ["Data Analyst", "Frontend Developer", "Backend Developer", "AI/ML Engineer", "DevOps Engineer"])

roadmap_data = []
if st.button("Generate Roadmap"):
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

    with st.spinner("Generating roadmap from OpenAI..."):
        try:
            response = openai.chat.completions.create(
                model="gpt-4",
                messages=[
                    {"role": "system", "content": "You are an AI roadmap planner."},
                    {"role": "user", "content": prompt}
                ]
            )
            raw = response.choices[0].message.content.strip()

            json_text = re.search(r"\[.*\]", raw, re.DOTALL).group()
            roadmap_data = json.loads(json_text)

            for item in roadmap_data:
                if not all(k in item for k in ["level", "duration", "topics", "project", "resources"]):
                    raise ValueError("Missing expected keys in roadmap item.")

            for item in roadmap_data:
                c.execute("""
                INSERT INTO roadmap_progress (username, career, level, status, start_date, end_date)
                VALUES (?, ?, ?, ?, ?, ?)""",
                          (username, career, item['level'], 'not_started', None, None))
            conn.commit()

            c.execute("INSERT INTO chats (username, message, response) VALUES (?, ?, ?)",
                      (username, f"Generate roadmap for {career}", json.dumps(roadmap_data)))
            conn.commit()

            st.success("Roadmap successfully generated and saved.")
        except Exception as e:
            st.error(f"⚠️ Failed to parse roadmap: {e}")
            st.text_area("⚙️ Raw AI Response", raw, height=300)

# --- Planner View ---
st.subheader("📒 Your Learning Planner")
c.execute("SELECT level, status, start_date, end_date FROM roadmap_progress WHERE username = ? AND career = ?", (username, career))
levels = c.fetchall()

c.execute("SELECT response FROM chats WHERE username = ? AND message LIKE ? ORDER BY timestamp DESC LIMIT 1", (username, f"%{career}%"))
chat_data = c.fetchone()
try:
    roadmap_response = json.loads(chat_data[0]) if chat_data and chat_data[0] else []
except json.JSONDecodeError:
    st.warning("⚠️ Could not decode saved roadmap. Please regenerate it.")
    roadmap_response = []

for item in roadmap_response:
    level_info = next((lvl for lvl in levels if lvl[0] == item["level"]), None)
    with st.expander(f"📘 {item['level']} | Status: {level_info[1].capitalize() if level_info else 'N/A'}"):
        st.markdown(f"**⏳ Duration:** {item['duration']}")
        st.markdown("**📚 Topics to Learn:**")
        for topic in item['topics']:
            st.markdown(f"- {topic}")
        st.markdown(f"**💻 Project:** {item['project']}")
        st.markdown("**🔗 Resources:**")
        for link in item['resources']:
            st.markdown(f"- [{link}]({link})")

        if level_info and level_info[1] == "not_started":
            if st.button(f"📅 Start {item['level']} Level", key=item['level']):
                start = datetime.now().date()
                end = start + timedelta(weeks=2)
                c.execute("""
                UPDATE roadmap_progress SET status = ?, start_date = ?, end_date = ?
                WHERE username = ? AND career = ? AND level = ?""",
                          ("in_progress", start, end, username, career, item['level']))
                conn.commit()
                st.success(f"Started {item['level']} level!")
                st.rerun()
        elif level_info and level_info[1] == "in_progress":
            if st.button(f"✅ Mark {item['level']} Level as Completed", key=item['level']+"done"):
                c.execute("""
                UPDATE roadmap_progress SET status = ?, end_date = ?
                WHERE username = ? AND career = ? AND level = ?""",
                          ("completed", datetime.now().date(), username, career, item['level']))
                conn.commit()
                st.success(f"Marked {item['level']} as completed!")
                st.rerun()

# --- Chat History ---
st.sidebar.subheader("💬 Chat History")
c.execute("SELECT id, message, timestamp FROM chats WHERE username = ? ORDER BY timestamp DESC", (username,))
chat_entries = c.fetchall()
selected_chat_id = st.sidebar.selectbox("Select a past chat to view:", ["-- Select --"] + [f"{entry[2]} - {entry[1][:30]}..." for entry in chat_entries])
if selected_chat_id != "-- Select --":
    index = [f"{entry[2]} - {entry[1][:30]}..." for entry in chat_entries].index(selected_chat_id)
    chat_id = chat_entries[index][0]
    c.execute("SELECT message, response FROM chats WHERE id = ?", (chat_id,))
    chat = c.fetchone()
    if chat:
        st.subheader("💬 Selected Chat")
        st.markdown(f"**You:** {chat[0]}")
        st.markdown(f"**Agent:** {chat[1]}")

# --- Internship Suggestions Placeholder ---
st.subheader("🎯 Internship & Gig Suggestions")
st.write("We'll match you with real opportunities based on your progress soon!")