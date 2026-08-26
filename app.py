import os
from datetime import datetime
from flask import Flask, render_template, request, redirect, url_for, session, jsonify
import mysql.connector
from mysql.connector import Error, pooling
import bcrypt

app = Flask(__name__)
app.secret_key = "super_secret_queue_key_change_this"  # Change this in production

# Database configuration pulling safely from Environment Variables
DB_CONFIG = {
    "host": os.environ.get("DB_HOST", "mysql-3aceb097-moosadesaidatabase.l.aivencloud.com"),
    "port": int(os.environ.get("DB_PORT", 13603)),
    "user": os.environ.get("DB_USER", "avnadmin"),
    "password": os.environ.get("DB_PASSWORD"),
    "database": os.environ.get("DB_NAME", "defaultdb"),
    "use_pure": True
}

# Create a connection pool to eliminate TCP handshake latency on every request
try:
    db_pool = pooling.MySQLConnectionPool(
        pool_name="queue_pool",
        pool_size=10,
        pool_reset_session=True,
        **DB_CONFIG
    )
except Error as e:
    print(f"Error initializing connection pool: {e}")
    db_pool = None


def get_db_connection():
    """Retrieve an active connection from the pool."""
    if not db_pool:
        return None
    try:
        return db_pool.get_connection()
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

            if stored_password and (stored_password.startswith("$2b$") or stored_password.startswith("$2a$")):
                try:
                    if bcrypt.checkpw(password.encode('utf-8'), stored_password.encode('utf-8')):
                        login_success = True
                except Exception:
                    pass
            
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
    
    if session.get("role") != "Admin":
        return redirect(url_for("dashboard"))
    
    conn = get_db_connection()
    if not conn:
        return "Database connection failed", 500
    
    cursor = conn.cursor(dictionary=True)
    today_str = datetime.now().strftime("%Y-%m-%d")
    
    cursor.execute("SELECT setting_value FROM settings WHERE setting_name = 'manual_seen_count'")
    manual_seen_row = cursor.fetchone()
    manual_seen = int(manual_seen_row["setting_value"]) if manual_seen_row else 0

    cursor.execute("SELECT COUNT(*) as count FROM tickets WHERE status = 'Seen' AND DATE(created_at) = %s", (today_str,))
    auto_seen = cursor.fetchone()["count"]

    cursor.execute("SELECT COUNT(*) as count FROM tickets WHERE status = 'Waiting' AND DATE(created_at) = %s", (today_str,))
    waiting_count = cursor.fetchone()["count"]

    cursor.execute("SELECT COUNT(*) as count FROM tickets WHERE status = 'Called' AND DATE(created_at) = %s", (today_str,))
    called_count = cursor.fetchone()["count"]

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

    cursor.execute("""
        SELECT HOUR(created_at) as ticket_hour, 
               AVG(TIME_TO_SEC(TIMEDIFF(COALESCE(called_at, seen_at), created_at))) as avg_wait_seconds 
        FROM tickets 
        WHERE DATE(created_at) = %s AND (called_at IS NOT NULL OR seen_at IS NOT NULL)
        GROUP BY HOUR(created_at)
    """, (today_str,))
    hourly_rows = cursor.fetchall()
    
    hourly_wait_dict = {row["ticket_hour"]: (row["avg_wait_seconds"] or 0) / 60 for row in hourly_rows}
    
    hourly_labels = []
    hourly_data = []
    for h in range(7, 17):
        hourly_labels.append(f"{h:02d}:00")
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


# --- API Endpoints ---

@app.route("/api/queue", methods=["GET"])
def get_queue():
    conn = get_db_connection()
    if not conn:
        return jsonify({"error": "DB connection failed"}), 500
    
    cursor = conn.cursor(dictionary=True)
    
    # Use MySQL CURDATE() to avoid timezone drift issues between Python server and DB
    cursor.execute("""
        SELECT * FROM tickets 
        WHERE status IN ('Waiting', 'Called') AND DATE(created_at) = CURDATE() 
        ORDER BY id ASC
    """)
    tickets = cursor.fetchall()

    cursor.execute("SELECT setting_value FROM settings WHERE setting_name = 'manual_seen_count'")
    manual_seen_row = cursor.fetchone()
    manual_seen = int(manual_seen_row["setting_value"]) if manual_seen_row else 0

    cursor.execute("SELECT COUNT(*) as count FROM tickets WHERE status = 'Seen' AND DATE(created_at) = CURDATE()")
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

    category = request.json.get("category") if request.is_json else None
    conn = get_db_connection()
    if not conn:
        return jsonify({"success": False, "error": "Database error"}), 500

    try:
        cursor = conn.cursor(dictionary=True)

        # Atomic row lock to prevent race conditions & execute fast
        cursor.execute("SELECT setting_value FROM settings WHERE setting_name = 'next_ticket_number' FOR UPDATE")
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
        return jsonify({"success": True, "ticket": ticket_number})
    except Error as e:
        conn.rollback()
        return jsonify({"success": False, "error": str(e)}), 500
    finally:
        cursor.close()
        conn.close()


@app.route("/api/ticket/call_next", methods=["POST"])
def api_call_next():
    if "username" not in session or session["role"] not in ("Admin", "Reception"):
        return jsonify({"success": False, "error": "Unauthorized"}), 403

    conn = get_db_connection()
    if not conn:
        return jsonify({"success": False, "error": "Database error"}), 500

    cursor = conn.cursor(dictionary=True)

    cursor.execute("""
        SELECT * FROM tickets WHERE status = 'Waiting' AND DATE(created_at) = CURDATE() ORDER BY id ASC LIMIT 1
    """)
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
    if not conn:
        return jsonify({"success": False, "error": "Database error"}), 500

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
    app.run(host="0.0.0.0", port=port, threaded=True)
