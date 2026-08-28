import os
import io
import pandas as pd
from datetime import datetime, timedelta
from flask import Flask, render_template, request, redirect, url_for, session, jsonify, send_file
import mysql.connector
from mysql.connector import Error, pooling
import bcrypt

app = Flask(__name__)
app.secret_key = "a_very_secure_and_permanent_random_string_for_queue_app_2026"
app.config['PERMANENT_SESSION_LIFETIME'] = timedelta(hours=8)

@app.before_request
def make_session_permanent():
    session.permanent = True

DB_CONFIG = {
    "host": os.environ.get("DB_HOST", "mysql-3aceb097-moosadesaidatabase.l.aivencloud.com"),
    "port": int(os.environ.get("DB_PORT", 13603)),
    "user": os.environ.get("DB_USER", "avnadmin"),
    "password": os.environ.get("DB_PASSWORD"),
    "database": os.environ.get("DB_NAME", "defaultdb"),
    "use_pure": True,
    "connection_timeout": 5
}

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
                session.permanent = True
                session["user_id"] = user["id"]
                session["username"] = user["username"]
                session["role"] = user["role"]
                session.modified = True
                return redirect(url_for("dashboard"))

        cursor.close()
        conn.close()
        return render_template("login.html", error="Invalid username or password.")

    return render_template("login.html")

@app.route("/dashboard")
def dashboard():
    if "username" not in session:
        return redirect(url_for("login"))
    
    username = session.get("username", "").strip().lower()
    role = session.get("role", "").strip().lower()
    is_owner = (username == "caleb") or (role == "owner")
    
    return render_template("dashboard.html", user=session, is_owner=is_owner)

@app.route("/stats")
def stats():
    if "username" not in session:
        return redirect(url_for("login"))
    
    username = session.get("username", "").strip().lower()
    role = session.get("role", "").strip().lower()
    if username != "caleb" and role != "owner":
        return redirect(url_for("dashboard"))
    
    selected_date = request.args.get("date", datetime.now().strftime("%Y-%m-%d"))
    view_type = request.args.get("type", "daily")
    
    total_seen = 0
    waiting_count = 0
    called_count = 0
    doctor_stats = []
    hourly_labels = [f"{h:02d}:00" for h in range(7, 17)]
    hourly_data = [0 for _ in range(7, 17)]

    conn = get_db_connection()
    if conn:
        cursor = None
        try:
            cursor = conn.cursor(dictionary=True)
            
            if view_type == "monthly":
                date_filter = "DATE_FORMAT(created_at, '%Y-%m') = %s"
                val = selected_date[:7]
            elif view_type == "yearly":
                date_filter = "YEAR(created_at) = %s"
                val = selected_date[:4]
            else:
                date_filter = "DATE(created_at) = %s"
                val = selected_date

            try:
                cursor.execute(f"SELECT COUNT(*) as count FROM tickets WHERE status = 'Seen' AND {date_filter}", (val,))
                row = cursor.fetchone()
                if row:
                    total_seen += row.get("count", 0)
            except Exception:
                pass

            try:
                cursor.execute(f"SELECT COUNT(*) as count FROM tickets WHERE status = 'Waiting' AND {date_filter}", (val,))
                row = cursor.fetchone()
                if row:
                    waiting_count = row.get("count", 0)
            except Exception:
                pass

            try:
                cursor.execute(f"SELECT COUNT(*) as count FROM tickets WHERE status = 'Called' AND {date_filter}", (val,))
                row = cursor.fetchone()
                if row:
                    called_count = row.get("count", 0)
            except Exception:
                pass

            try:
                cursor.execute(f"""
                    SELECT doctor, 
                           COUNT(*) as patients_seen, 
                           AVG(TIME_TO_SEC(TIMEDIFF(seen_at, called_at))) as avg_duration_seconds 
                    FROM tickets 
                    WHERE status = 'Seen' AND {date_filter} AND doctor IS NOT NULL 
                    GROUP BY doctor
                """, (val,))
                for row in cursor.fetchall():
                    avg_sec = row.get("avg_duration_seconds") or 0
                    minutes = int(avg_sec // 60)
                    seconds = int(avg_sec % 60)
                    doctor_stats.append({
                        "doctor": str(row.get("doctor", "Unknown")),
                        "patients_seen": int(row.get("patients_seen", 0)),
                        "avg_duration": f"{minutes}m {seconds}s"
                    })
            except Exception as e:
                print(f"Doctor stats warning: {e}")

            try:
                cursor.execute(f"""
                    SELECT HOUR(created_at) as ticket_hour, 
                           AVG(TIME_TO_SEC(TIMEDIFF(COALESCE(called_at, seen_at), created_at))) as avg_wait_seconds 
                    FROM tickets 
                    WHERE {date_filter} AND (called_at IS NOT NULL OR seen_at IS NOT NULL)
                    GROUP BY HOUR(created_at)
                """, (val,))
                hourly_rows = cursor.fetchall()
                hourly_wait_dict = {row["ticket_hour"]: (row["avg_wait_seconds"] or 0) / 60 for row in hourly_rows}
                hourly_data = [round(float(hourly_wait_dict.get(h, 0)), 2) for h in range(7, 17)]
            except Exception as e:
                print(f"Hourly stats warning: {e}")

        except Exception as e:
            print(f"Database query error in stats: {e}")
        finally:
            if cursor:
                cursor.close()
            conn.close()

    return render_template("stats.html", 
                           total_seen=total_seen, 
                           waiting_count=waiting_count, 
                           called_count=called_count,
                           doctor_stats=doctor_stats,
                           hourly_labels=hourly_labels,
                           hourly_data=hourly_data,
                           selected_date=selected_date,
                           view_type=view_type,
                           user=session,
                           is_owner=True)

@app.route("/stats/export")
def export_stats():
    if "username" not in session:
        return redirect(url_for("dashboard"))
    username = session.get("username", "").strip().lower()
    role = session.get("role", "").strip().lower()
    if username != "caleb" and role != "owner":
        return redirect(url_for("dashboard"))
    
    selected_date = request.args.get("date", datetime.now().strftime("%Y-%m-%d"))
    view_type = request.args.get("type", "daily")

    conn = get_db_connection()
    if not conn:
        return "Database connection failed", 500

    if view_type == "monthly":
        date_filter = "DATE_FORMAT(created_at, '%Y-%m') = %s"
        val = selected_date[:7]
    elif view_type == "yearly":
        date_filter = "YEAR(created_at) = %s"
        val = selected_date[:4]
    else:
        date_filter = "DATE(created_at) = %s"
        val = selected_date

    query = f"SELECT id, ticket_number, category, status, created_by, doctor, created_at, called_at, seen_at FROM tickets WHERE {date_filter}"
    df = pd.read_sql(query, conn, params=(val,))
    conn.close()

    output = io.BytesIO()
    with pd.ExcelWriter(output, engine='openpyxl') as writer:
        df.to_excel(writer, index=False, sheet_name='Analytics Data')
    output.seek(0)

    filename = f"analytics_{view_type}_{selected_date}.xlsx"
    return send_file(output, mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", as_attachment=True, download_name=filename)

@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))

@app.route("/emergency-admin-reset")
def emergency_admin_reset():
    conn = get_db_connection()
    if not conn:
        return "Database connection failed", 500
    try:
        cursor = conn.cursor()
        hashed = bcrypt.hashpw("admin".encode('utf-8'), bcrypt.gensalt()).decode('utf-8')
        cursor.execute("SELECT * FROM users WHERE username = 'caleb'")
        if cursor.fetchone():
            cursor.execute("UPDATE users SET password = %s, role = 'Owner' WHERE username = 'caleb'", (hashed,))
        else:
            cursor.execute("INSERT INTO users (username, password, role) VALUES ('caleb', %s, 'Owner')", (hashed,))
        conn.commit()
        return "SUCCESS! Owner account reset. Username: caleb | Password: admin"
    except Exception as e:
        return f"Error: {e}"
    finally:
        cursor.close()
        conn.close()

# --- API Endpoints ---

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

    manual_seen = 0
    try:
        cursor.execute("SELECT setting_value FROM settings WHERE setting_name = 'manual_seen_count'")
        manual_seen_row = cursor.fetchone()
        if manual_seen_row:
            manual_seen = int(manual_seen_row["setting_value"])
    except Exception:
        pass

    cursor.execute("""
        SELECT COUNT(*) as count FROM tickets 
        WHERE status = 'Seen' AND (DATE(seen_at) = %s OR (seen_at IS NULL AND DATE(created_at) = %s))
    """, (today_str, today_str))
    seen_row = cursor.fetchone()
    auto_seen = seen_row["count"] if seen_row else 0

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
    if "username" not in session:
        return jsonify({"success": False, "error": "Unauthorized"}), 403
    
    username = session.get("username", "").strip().lower()
    user_role = session.get("role", "").strip().lower()
    
    if username != "caleb" and user_role not in ["admin", "owner"]:
        return jsonify({"success": False, "error": "Unauthorized"}), 403

    category = request.json.get("category") if request.is_json else "Consultation"
    conn = get_db_connection()
    if not conn:
        return jsonify({"success": False, "error": "Database error"}), 500

    cursor = None
    try:
        cursor = conn.cursor(dictionary=True)
        today_str = datetime.now().strftime("%Y-%m-%d")
        created_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        cursor.execute("""
            SELECT MAX(CAST(ticket_number AS UNSIGNED)) as max_num 
            FROM tickets 
            WHERE DATE(created_at) = %s
        """, (today_str,))
        row = cursor.fetchone()
        
        next_num = (row["max_num"] + 1) if (row and row["max_num"]) else 1
        ticket_number = f"{next_num:03d}"

        cursor.execute("""
            INSERT INTO tickets (ticket_number, category, created_at, status, created_by)
            VALUES (%s, %s, %s, 'Waiting', %s)
        """, (ticket_number, category, created_at, session["username"]))

        conn.commit()
        return jsonify({"success": True, "ticket": ticket_number})
    except Exception as e:
        conn.rollback()
        return jsonify({"success": False, "error": str(e)}), 500
    finally:
        if cursor:
            cursor.close()
        conn.close()

@app.route("/api/ticket/call_next", methods=["POST"])
def api_call_next():
    if "username" not in session:
        return jsonify({"success": False, "error": "Unauthorized"}), 403
    
    username = session.get("username", "").strip().lower()
    user_role = session.get("role", "").strip().lower()
    
    if username != "caleb" and user_role not in ["admin", "owner"]:
        return jsonify({"success": False, "error": "Unauthorized"}), 403

    conn = get_db_connection()
    if not conn:
        return jsonify({"success": False, "error": "Database error"}), 500

    cursor = None
    try:
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
            return jsonify({"success": True, "ticket": row})

        return jsonify({"success": False, "message": "No patients waiting"})
    except Exception as e:
        conn.rollback()
        return jsonify({"success": False, "error": str(e)}), 500
    finally:
        if cursor:
            cursor.close()
        conn.close()

@app.route("/api/ticket/seen/<int:ticket_id>", methods=["POST"])
def api_mark_seen(ticket_id):
    if "username" not in session:
        return jsonify({"success": False, "error": "Unauthorized"}), 403
    
    user_role = session.get("role", "").strip().lower()
    if user_role == "admin":
        return jsonify({"success": False, "error": "Unauthorized: Reception cannot mark tickets as seen"}), 403

    seen_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    doctor_name = session["username"]

    conn = get_db_connection()
    if not conn:
        return jsonify({"success": False, "error": "Database error"}), 500

    cursor = None
    try:
        cursor = conn.cursor()
        cursor.execute("""
            UPDATE tickets SET seen_at = %s, doctor = %s, status = 'Seen' WHERE id = %s
        """, (seen_at, doctor_name, ticket_id))
        conn.commit()
        return jsonify({"success": True})
    except Exception as e:
        conn.rollback()
        return jsonify({"success": False, "error": str(e)}), 500
    finally:
        if cursor:
            cursor.close()
        conn.close()

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, threaded=True)