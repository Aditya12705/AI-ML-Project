import os
import re
import json
import base64
from datetime import datetime
from flask import Flask, render_template, request, redirect, url_for, session, flash
from dotenv import load_dotenv
import google.generativeai as genai
import random

# Load environment variables
load_dotenv()
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
if not GEMINI_API_KEY:
    raise RuntimeError("GEMINI_API_KEY not found in .env file. Please ensure the .env file is correctly set up.")

os.environ["GOOGLE_API_KEY"] = GEMINI_API_KEY

genai.configure()
MODEL_NAME = "gemini-1.5-flash"
model = genai.GenerativeModel(MODEL_NAME)

app = Flask(__name__)
app.secret_key = os.getenv("FLASK_SECRET_KEY", "supersecretkey")

learning_styles = {
    "practical": "Explain concepts with real-life examples and practical scenarios that the student can relate to.",
    "theory": "Provide detailed theoretical explanations with foundational principles and academic context."
}

FALLBACK_RESPONSES = {
    "laws of motion": {
        "practical": (
            "Think about riding a bicycle. When you stop pedaling, you don’t instantly stop—that’s inertia, the first law. "
            "Pedal harder, you speed up—that’s force equals mass times acceleration, the second law. "
            "Push the pedals, the bike pushes you forward—action-reaction, the third law."
        ),
        "theory": (
            "Newton’s first law: objects stay at rest or in motion unless acted upon. Second law: force equals mass times acceleration. "
            "Third law: every action has an equal, opposite reaction."
        )
    }
}

USERS_DATA_PATH = "users_data.json"

# Protégé Mode prompts
PROTEGE_MODE_MESSAGES = [
    "I am your junior student, can you please teach me this topic?",
    "I am confused, can you explain this to me like I am five?",
    "Imagine I am a new student, how would you help me understand this?"
]


# Clean response text

def clean_response_text(response):
    response = re.sub(r'\*+', '', response)
    response = re.sub(r'_+', '', response)
    lines = response.split('\n')
    point_counter = 1
    cleaned_lines = []
    for line in lines:
        line = line.strip()
        if line.startswith('-') or line.startswith('*'):
            line = line.lstrip('-* ').strip()
            if line:
                cleaned_lines.append(f"Point {point_counter}: {line}")
                point_counter += 1
        else:
            cleaned_lines.append(line)
    return ' '.join(cleaned_lines)


# Adapt response to learning style

def adapt_response(response, learning_style):
    prefix = {
        "practical": "Let me give you a practical explanation. ",
        "theory": "Let me explain the theory behind this. "
    }.get(learning_style, "Let me give you a practical explanation. ")
    cleaned_response = clean_response_text(response)
    structured_response = f"{prefix} {cleaned_response} ... Anything else you’d like to know?"
    return structured_response


# Generate AI response

def generate_response(query, user_name, learning_style, conversation_history):
    query_lower = query.lower()
    if query_lower in FALLBACK_RESPONSES:
        response_text = FALLBACK_RESPONSES[query_lower].get(learning_style, FALLBACK_RESPONSES[query_lower]["practical"])
        conversation_history.append({"role": "Human", "text": query, "time": datetime.now().strftime("%H:%M:%S")})
        conversation_history.append({"role": "Assistant", "text": response_text, "time": datetime.now().strftime("%H:%M:%S")})
        return response_text

    style_context = learning_styles.get(learning_style, learning_styles["practical"])
    history_context = "\n".join([f"{entry['role']}: {entry['text']}" for entry in conversation_history[-5:]])

    prompt = f"""Previous conversation:
{history_context}

You are an AI teacher assistant. {style_context}
Keep responses medium-length and focused on study-related topics.

Student: {user_name}
Learning Style: {learning_style}
Question: {query}

Respond matching their learning style."""

    try:
        response = model.generate_content(prompt)
        response_text = response.text.strip()
        conversation_history.append({"role": "Human", "text": query, "time": datetime.now().strftime("%H:%M:%S")})
        conversation_history.append({"role": "Assistant", "text": response_text, "time": datetime.now().strftime("%H:%M:%S")})
        return response_text
    except Exception as e:
        return "Sorry, I couldn’t process that. Check your API key or internet connection."


# Load user data

def load_users_data():
    if not os.path.exists(USERS_DATA_PATH):
        return {}
    with open(USERS_DATA_PATH, "r", encoding="utf-8") as f:
        try:
            return json.load(f)
        except Exception:
            return {}


# Save user data

def save_users_data(users_data):
    with open(USERS_DATA_PATH, "w", encoding="utf-8") as f:
        json.dump(users_data, f, indent=2)


# Routes

@app.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        username = request.form.get("username")
        learning_style = request.form.get("learning_style", "practical")
        
        # Load existing users
        users_data = {}
        if os.path.exists(USERS_DATA_PATH):
            with open(USERS_DATA_PATH, 'r') as f:
                users_data = json.load(f)
        
        # Check if username already exists
        if username in users_data:
            flash("Username already exists. Please choose another one.", "error")
            return redirect(url_for("register"))
        
        # Add new user
        users_data[username] = {
            "learning_style": learning_style,
            "points": 0,
            "conversation_history": []
        }
        
        # Save updated users data
        with open(USERS_DATA_PATH, 'w') as f:
            json.dump(users_data, f, indent=4)
        
        # Set session
        session["user_name"] = username
        flash("Registration successful! Welcome to AI Teacher Assistant!", "success")
        return redirect(url_for("chat"))
    
    return render_template("register.html")

@app.route("/", methods=["GET", "POST"])
def login():
    users_data = load_users_data()
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        if username:
            session["user_name"] = username
            session["logged_in"] = True
            user_data = users_data.get(username, {})
            session["learning_style"] = user_data.get("learning_style")
            session["conversation_history"] = user_data.get("history", [])
            session["protege_mode"] = False
            session["points"] = user_data.get("points", 0)
            session["struggled_topics"] = user_data.get("struggled_topics", [])
            # Personalized greeting if struggled topics exist
            if session["struggled_topics"]:
                last_topic = session["struggled_topics"][-1]
                flash(f"Welcome back, you previously struggled with '{last_topic}'. Shall we review it?", "warning")
            return redirect(url_for("aptitude" if session["learning_style"] is None else "chat"))
        else:
            flash("Please enter a username.", "danger")
    return render_template("login.html")


@app.route("/aptitude", methods=["GET", "POST"])
def aptitude():
    if not session.get("logged_in"):
        return redirect(url_for("login"))
    users_data = load_users_data()
    questions = [
        ("When learning a new topic, do you prefer to:", ["See real-life examples", "Understand the underlying theory"]),
        ("In class, you enjoy:", ["Hands-on activities", "Listening to detailed explanations"]),
        ("When solving problems, you:", ["Apply concepts to practical situations", "Analyze the theory behind the problem"]),
        ("You remember best when:", ["You do it yourself", "You read or hear about it in detail"]),
        ("You prefer teachers who:", ["Give practical demonstrations", "Explain concepts thoroughly"])
    ]
    if request.method == "POST":
        answers = [request.form.get(f"q{i}") for i in range(len(questions))]
        practical_count = sum(1 for a in answers if a and ("practical" in a.lower() or "hands-on" in a.lower() or "real-life" in a.lower() or "do it" in a.lower() or "demonstrations" in a.lower()))
        style = "practical" if practical_count >= 3 else "theory"
        session["learning_style"] = style
        flash(f"You are classified as a {style} learner!", "success")
        users_data[session["user_name"]] = {
            "learning_style": style,
            "history": session.get("conversation_history", []),
            "points": session.get("points", 0),
            "struggled_topics": session.get("struggled_topics", [])
        }
        save_users_data(users_data)
        return redirect(url_for("chat"))
    return render_template("aptitude.html", questions=questions)


@app.route("/chat", methods=["GET", "POST"])
def chat():
    if not session.get("logged_in"):
        return redirect(url_for("login"))
    users_data = load_users_data()
    conversation_history = session.get("conversation_history", [])
    ai_response = None
    points = session.get("points", 0)
    struggled_topics = session.get("struggled_topics", [])

    if request.method == "POST":
        if 'toggle_mode' in request.form:
            session["protege_mode"] = not session["protege_mode"]
            mode_status = "Protégé mode activated!" if session["protege_mode"] else "Protégé mode deactivated."
            flash(mode_status, "info")
            return redirect(url_for("chat"))

        user_query = request.form.get("user_query", "").strip()
        if user_query:
            # Track repeated queries as struggled topics
            repeated = any(user_query.lower() == h.get("text", "").lower() for h in conversation_history if h["role"] == "Human")
            if repeated and user_query not in struggled_topics:
                struggled_topics.append(user_query)
                session["struggled_topics"] = struggled_topics
                flash(f"This seems to be a tricky topic for you: '{user_query}'. We'll help you master it!", "warning")
            if session.get("protege_mode"):
                protege_prompt = random.choice(PROTEGE_MODE_MESSAGES)
                ai_response = f"{protege_prompt} Please explain: {user_query}"
                # Award points for explanation (simulate as always correct for demo)
                points += 1
                session["points"] = points
                flash(f"Great explanation! You earned a point. Total: {points}", "success")
            else:
                response = generate_response(user_query, session["user_name"], session["learning_style"], conversation_history)
                adapted = adapt_response(response, session["learning_style"])
                ai_response = adapted
                # If fallback or error, add to struggled topics
                if "couldn’t process" in adapted or "Sorry" in adapted:
                    if user_query not in struggled_topics:
                        struggled_topics.append(user_query)
                        session["struggled_topics"] = struggled_topics
            session["conversation_history"] = conversation_history
            users_data[session["user_name"]] = {
                "learning_style": session["learning_style"],
                "history": conversation_history,
                "points": points,
                "struggled_topics": struggled_topics
            }
            save_users_data(users_data)

    return render_template(
        "chat.html",
        user_name=session["user_name"],
        learning_style=session["learning_style"],
        conversation_history=session.get("conversation_history", []),
        ai_response=ai_response,
        protege_mode=session.get("protege_mode", False),
        points=session.get("points", 0)
    )


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


if __name__ == "__main__":
    app.run(debug=True)
