import os
from datetime import datetime, time, timedelta
from flask import Flask, render_template, request, redirect, url_for, session, jsonify
import mysql.connector
from mysql.connector import Error
import bcrypt

app = Flask(__name__)
app.secret_key = "super_secret_queue_key_change_this"  # Needed for secure session handling

# Database configuration pulling safely from Render Environment Variables
DB_CONFIG = {
    "host": os.environ.get("DB_HOST", "mysql-3aceb097-moosadesaidatabase.l.aivencloud.com"),
    "port": int(os.environ.get("DB_PORT", 13603)),
    "user": os.environ.get("DB_USER", "avnadmin"),
    "password": os.environ.get("DB_PASSWORD"),  # Pulled securely from Render Environment Variables
    "database": os.environ.get("DB_NAME", "defaultdb"),
    "use_pure": True
}


def get_db_connection():
    try:
        conn = mysql.connector.connect(**DB_CONFIG)
        return conn
    except Error as e:
        print(f"Database connection error: {e}")
        return None


@app.route("/", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "").strip()

        conn = get_db_connection()
        if not conn:
            return render_template("login.html", error="Database connection failed.")
        
        cursor = conn.cursor(dictionary=True)
        cursor.execute("SELECT * FROM users WHERE username = %s", (username,))
        user = cursor.fetchone()

        if user:
            stored_password = user["password"]
            login_success = False

            # Check if stored password is a valid bcrypt hash
            if stored_password and (stored_password.startswith("$2b$") or stored_password.startswith("$2a$")):
                try:
                    if bcrypt.checkpw(password.encode('utf-8'), stored_password.encode('utf-8')):
                        login_success = True
                except Exception:
                    pass
            
            # Fallback: if it's plain text, check and upgrade automatically
            if not login_success and stored_password == password:
                login_success = True
                hashed = bcrypt.hashpw(password.encode('utf-8'), bcrypt.gensalt()).decode('utf-8')
                up_cursor = conn.cursor()
                up_cursor.execute("UPDATE users SET password = %s WHERE id = %s", (hashed, user["id"]))
                conn.commit()
                up_cursor.close()

            cursor.close()
            conn.close()

            if login_success:
                session["user_id"] = user["id"]
                session["username"] = user["username"]
                session["role"] = user["role"]
                return redirect(url_for("dashboard"))

        cursor.close()
        conn.close()
        return render_template("login.html", error="Invalid username or password.")

    return render_template("login.html")


@app.route("/dashboard")
def dashboard():
    if "username" not in session:
        return redirect(url_for("login"))
    return render_template("dashboard.html", user=session)


@app.route("/stats")
def stats():
    if "username" not in session:
        return redirect(url_for("login"))
    
    # Restrict stats page access strictly to Admin role
    if session.get("role") != "Admin":
        return redirect(url_for("dashboard"))
    
    conn = get_db_connection()
    if not conn:
        return "Database connection failed", 500
    
    cursor = conn.cursor(dictionary=True)
    today_str = datetime.now().strftime("%Y-%m-%d")
    
    # 1. Fetch manual seen count from settings
    cursor.execute("SELECT setting_value FROM settings WHERE setting_name = 'manual_seen_count'")
    manual_seen_row = cursor.fetchone()
    manual_seen = int(manual_seen_row["setting_value"]) if manual_seen_row else 0

    # 2. Fetch automatic seen count from tickets table
    cursor.execute("SELECT COUNT(*) as count FROM tickets WHERE status = 'Seen' AND DATE(created_at) = %s", (today_str,))
    auto_seen = cursor.fetchone()["count"]

    # 3. Fetch waiting and called counts
    cursor.execute("SELECT COUNT(*) as count FROM tickets WHERE status = 'Waiting' AND DATE(created_at) = %s", (today_str,))
    waiting_count = cursor.fetchone()["count"]

    cursor.execute("SELECT COUNT(*) as count FROM tickets WHERE status = 'Called' AND DATE(created_at) = %s", (today_str,))
    called_count = cursor.fetchone()["count"]

    # 4. Detailed Doctor Stats (Patients seen per doctor & average consultation duration)
    # Assumes 'doctor' field holds the doctor username, and 'called_at' / 'seen_at' timestamps exist
    cursor.execute("""
        SELECT doctor, 
               COUNT(*) as patients_seen, 
               AVG(TIME_TO_SEC(TIMEDIFF(seen_at, called_at))) as avg_duration_seconds 
        FROM tickets 
        WHERE status = 'Seen' AND DATE(created_at) = %s AND doctor IS NOT NULL 
        GROUP BY doctor
    """, (today_str,))
    doctor_rows = cursor.fetchall()

    doctor_stats = []
    for row in doctor_rows:
        avg_sec = row["avg_duration_seconds"] or 0
        minutes = int(avg_sec // 60)
        seconds = int(avg_sec % 60)
        doctor_stats.append({
            "doctor": row["doctor"],
            "patients_seen": row["patients_seen"],
            "avg_duration": f"{minutes}m {seconds}s"
        })

    # 5. Hourly Average Waiting Time (from 07:00 to 16:00)
    # Waiting time = difference between created_at and called_at (or seen_at if called_at is missing)
    cursor.execute("""
        SELECT HOUR(created_at) as ticket_hour, 
               AVG(TIME_TO_SEC(TIMEDIFF(COALESCE(called_at, seen_at), created_at))) as avg_wait_seconds 
        FROM tickets 
        WHERE DATE(created_at) = %s AND (called_at IS NOT NULL OR seen_at IS NOT NULL)
        GROUP BY HOUR(created_at)
    """, (today_str,))
    hourly_rows = cursor.fetchall()
    
    # Map into a dictionary for clean sorting/filling between 07:00 and 16:00
    hourly_wait_dict = {row["ticket_hour"]: (row["avg_wait_seconds"] or 0) / 60 for row in hourly_rows}
    
    hourly_labels = []
    hourly_data = []
    for h in range(7, 17): # 7 AM to 4 PM (16:00)
        hourly_labels.append(f"{h:02d}:00")
        # Get average wait time in minutes for that hour, default to 0 if no data
        wait_mins = round(hourly_wait_dict.get(h, 0), 2)
        hourly_data.append(wait_mins)

    cursor.close()
    conn.close()

    return render_template("stats.html", 
                           total_seen=auto_seen + manual_seen, 
                           waiting_count=waiting_count, 
                           called_count=called_count,
                           doctor_stats=doctor_stats,
                           hourly_labels=hourly_labels,
                           hourly_data=hourly_data,
                           user=session)


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


# --- API Endpoints for Frontend Interaction ---

@app.route("/api/queue", methods=["GET"])
def get_queue():
    conn = get_db_connection()
    if not conn:
        return jsonify({"error": "DB connection failed"}), 500
    
    cursor = conn.cursor(dictionary=True)
    today_str = datetime.now().strftime("%Y-%m-%d")
    
    cursor.execute("""
        SELECT * FROM tickets 
        WHERE status IN ('Waiting', 'Called') AND DATE(created_at) = %s 
        ORDER BY id ASC
    """, (today_str,))
    tickets = cursor.fetchall()

    # Fetch stats
    cursor.execute("SELECT setting_value FROM settings WHERE setting_name = 'manual_seen_count'")
    manual_seen_row = cursor.fetchone()
    manual_seen = int(manual_seen_row["setting_value"]) if manual_seen_row else 0

    cursor.execute("SELECT COUNT(*) as count FROM tickets WHERE status = 'Seen' AND DATE(created_at) = %s", (today_str,))
    auto_seen = cursor.fetchone()["count"]

    cursor.close()
    conn.close()

    return jsonify({
        "tickets": tickets,
        "total_seen": auto_seen + manual_seen,
        "waiting_count": sum(1 for t in tickets if t["status"] == "Waiting"),
        "called_count": sum(1 for t in tickets if t["status"] == "Called")
    })


@app.route("/api/ticket/create", methods=["POST"])
def api_create_ticket():
    if "username" not in session or session["role"] not in ("Admin", "Reception"):
        return jsonify({"success": False, "error": "Unauthorized"}), 403

    category = request.json.get("category")
    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)

    # Get next ticket number
    cursor.execute("SELECT setting_value FROM settings WHERE setting_name = 'next_ticket_number'")
    row = cursor.fetchone()
    next_num = int(row["setting_value"]) if row else 1
    ticket_number = f"{next_num:03d}"
    created_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    cursor.execute("""
        INSERT INTO tickets (ticket_number, category, created_at, status, created_by)
        VALUES (%s, %s, %s, 'Waiting', %s)
    """, (ticket_number, category, created_at, session["username"]))

    cursor.execute("UPDATE settings SET setting_value = %s WHERE setting_name = 'next_ticket_number'", (str(next_num + 1),))
    conn.commit()
    cursor.close()
    conn.close()

    return jsonify({"success": True, "ticket": ticket_number})


@app.route("/api/ticket/call_next", methods=["POST"])
def api_call_next():
    if "username" not in session or session["role"] not in ("Admin", "Reception"):
        return jsonify({"success": False, "error": "Unauthorized"}), 403

    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    today_str = datetime.now().strftime("%Y-%m-%d")

    cursor.execute("""
        SELECT * FROM tickets WHERE status = 'Waiting' AND DATE(created_at) = %s ORDER BY id ASC LIMIT 1
    """, (today_str,))
    row = cursor.fetchone()

    if row:
        called_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        cursor.execute("UPDATE tickets SET called_at = %s, status = 'Called' WHERE id = %s", (called_at, row["id"]))
        conn.commit()
        cursor.close()
        conn.close()
        return jsonify({"success": True, "ticket": row})

    cursor.close()
    conn.close()
    return jsonify({"success": False, "message": "No patients waiting"})


@app.route("/api/ticket/seen/<int:ticket_id>", methods=["POST"])
def api_mark_seen(ticket_id):
    if "username" not in session:
        return jsonify({"success": False, "error": "Unauthorized"}), 403

    seen_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    doctor_name = session["username"]

    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("""
        UPDATE tickets SET seen_at = %s, doctor = %s, status = 'Seen' WHERE id = %s
    """, (seen_at, doctor_name, ticket_id))
    conn.commit()
    cursor.close()
    conn.close()

    return jsonify({"success": True})


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
