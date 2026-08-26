import csv
import os
from datetime import datetime
import tkinter as tk
from tkinter import ttk, messagebox, filedialog
import mysql.connector
from mysql.connector import Error

# ============================================================
# PYINSTALLER MODULE RESOLUTION SAFETY BLOCK FOR BCRYPT
# ============================================================
try:
    import bcrypt
except ImportError:
    import sys
    raise ImportError("The 'bcrypt' library is required. Please ensure it is installed.")

# ============================================================
# OPTIONAL EXCEL EXPORT
# ============================================================

try:
    from openpyxl import Workbook
    OPENPYXL_AVAILABLE = True
except ImportError:
    OPENPYXL_AVAILABLE = False


# ============================================================
# MATPLOTLIB FOR LIVE STATISTICS GRAPH
# ============================================================

try:
    from matplotlib.figure import Figure
    from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
    MATPLOTLIB_AVAILABLE = True
except ImportError:
    MATPLOTLIB_AVAILABLE = False


# ============================================================
# SETTINGS
# ============================================================

CATEGORIES = [
    "Bloods",
    "Scripts",
    "Skin",
    "Review",
    "General Consult"
]

DOCTORS = [
    "Dr Desai",
    "Dr Joosub",
    "Dr Govender",
    "Locum Dr"
]


# ============================================================
# COLOURS
# ============================================================

COLORS = {
    "background": "#F4F8FB",
    "white": "#FFFFFF",
    "primary": "#176B87",
    "primary_dark": "#0F4C5C",
    "secondary": "#2A9D8F",
    "accent": "#4CC9F0",
    "green": "#2E8B57",
    "orange": "#F4A261",
    "red": "#E76F51",
    "purple": "#6C63FF",
    "text": "#17324D",
    "muted": "#667788",
    "border": "#D7E3EA",
    "light_blue": "#EAF6FB",
    "light_green": "#EAF7F1",
    "light_orange": "#FFF4E5",
    "light_red": "#FDEDEC",
    "light_purple": "#F0EEFF"
}


# ============================================================
# MYSQL DATABASE MANAGER
# ============================================================


class Database:

    def __init__(self):
        self.host = "192.168.1.171" 
        self.port = 3306
        self.user = "root"
        self.password = "Desai@2026"
        self.database_name = "patient_queue_db"

        self.connect()
        self.create_database()

    def connect(self):
        try:
            temp_conn = mysql.connector.connect(
                host=self.host,
                port=self.port,
                user=self.user,
                password=self.password,
                use_pure=True
            )
            temp_cursor = temp_conn.cursor()
            temp_cursor.execute(f"CREATE DATABASE IF NOT EXISTS {self.database_name}")
            temp_cursor.close()
            temp_conn.close()

            self.connection = mysql.connector.connect(
                host=self.host,
                port=self.port,
                user=self.user,
                password=self.password,
                database=self.database_name,
                buffered=True,
                use_pure=True,
                autocommit=True  # ENABLE AUTOCOMMIT TO PREVENT TRANSACTION CACHING STALENESS
            )
        except Error as e:
            messagebox.showerror("Database Connection Error", f"Could not connect to MySQL Server at {self.host}:\n{e}")
            raise e

    def ensure_connection(self):
        try:
            if not self.connection.is_connected():
                self.connect()
            else:
                # Force ping and clear any transaction snapshot cache
                self.connection.ping(reconnect=True, attempts=3, delay=0)
        except Exception:
            self.connect()

    def create_database(self):
        self.ensure_connection()
        cursor = self.connection.cursor()

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS tickets (
                id INT AUTO_INCREMENT PRIMARY KEY,
                ticket_number VARCHAR(10) NOT NULL,
                category VARCHAR(50) NOT NULL,
                created_at VARCHAR(30) NOT NULL,
                called_at VARCHAR(30),
                seen_at VARCHAR(30),
                doctor VARCHAR(50),
                status VARCHAR(20) NOT NULL DEFAULT 'Waiting',
                cancellation_reason TEXT,
                created_by VARCHAR(50)
            )
        """)
        # Ensure 'created_by' column exists if table was already created previously
        try:
            cursor.execute("ALTER TABLE tickets ADD COLUMN created_by VARCHAR(50)")
        except Exception:
            pass # Column already exists
            
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS settings (
                setting_name VARCHAR(50) PRIMARY KEY,
                setting_value VARCHAR(255)
            )
        """)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS users (
                id INT AUTO_INCREMENT PRIMARY KEY,
                username VARCHAR(50) UNIQUE NOT NULL,
                password VARCHAR(255) NOT NULL,
                role VARCHAR(20) NOT NULL
            )
        """)

        cursor.execute("""
            INSERT IGNORE INTO settings
            (setting_name, setting_value)
            VALUES ('next_ticket_number', '1')
        """)

        cursor.execute("""
            INSERT IGNORE INTO settings
            (setting_name, setting_value)
            VALUES ('last_active_date', %s)
        """, (datetime.now().strftime("%Y-%m-%d"),))

        cursor.execute("""
            INSERT IGNORE INTO settings
            (setting_name, setting_value)
            VALUES ('manual_seen_count', '0')
        """)

        cursor.close()
        self.check_daily_reset()

    def check_daily_reset(self):
        current_date = datetime.now().strftime("%Y-%m-%d")
        last_date = self.get_setting("last_active_date", current_date)

        if last_date != current_date:
            self.set_setting("next_ticket_number", "1")
            self.set_setting("manual_seen_count", "0")
            self.set_setting("last_active_date", current_date)

    def get_setting(self, setting_name, default="0"):
        self.ensure_connection()
        cursor = self.connection.cursor(dictionary=True)
        cursor.execute("SELECT setting_value FROM settings WHERE setting_name = %s", (setting_name,))
        row = cursor.fetchone()
        cursor.close()
        return row["setting_value"] if row else default

    def set_setting(self, setting_name, value):
        self.ensure_connection()
        cursor = self.connection.cursor()
        cursor.execute("""
            INSERT INTO settings (setting_name, setting_value)
            VALUES (%s, %s)
            ON DUPLICATE KEY UPDATE setting_value = %s
        """, (setting_name, str(value), str(value)))
        cursor.close()

    def get_next_ticket_number(self):
        self.check_daily_reset()
        return int(self.get_setting("next_ticket_number", "1"))

    def set_next_ticket_number(self, number):
        self.set_setting("next_ticket_number", number)

    def get_manual_seen_count(self):
        self.check_daily_reset()
        try:
            return int(self.get_setting("manual_seen_count", "0"))
        except ValueError:
            return 0

    def set_manual_seen_count(self, number):
        self.set_setting("manual_seen_count", number)

    def authenticate_user(self, username, password):
        self.ensure_connection()
        cursor = self.connection.cursor(dictionary=True)
        cursor.execute("SELECT * FROM users WHERE username = %s", (username,))
        user = cursor.fetchone()
        cursor.close()

        if user:
            stored_password = user["password"]
            if stored_password.startswith("$2b$") or stored_password.startswith("$2a$"):
                if bcrypt.checkpw(password.encode('utf-8'), stored_password.encode('utf-8')):
                    return user
            else:
                if stored_password == password:
                    hashed = bcrypt.hashpw(password.encode('utf-8'), bcrypt.gensalt()).decode('utf-8')
                    up_cursor = self.connection.cursor()
                    up_cursor.execute("UPDATE users SET password = %s WHERE id = %s", (hashed, user["id"]))
                    up_cursor.close()
                    return user
        return None

    def create_ticket(self, category, username):
        self.check_daily_reset()
        number = self.get_next_ticket_number()
        ticket_number = f"{number:03d}"
        created_at = datetime.now().isoformat(timespec="seconds")

        self.ensure_connection()
        cursor = self.connection.cursor()
        cursor.execute("""
            INSERT INTO tickets
            (ticket_number, category, created_at, status, created_by)
            VALUES (%s, %s, %s, 'Waiting', %s)
        """, (ticket_number, category, created_at, username))

        self.set_next_ticket_number(number + 1)
        cursor.close()
        return ticket_number

    def get_all_tickets(self):
        self.ensure_connection()
        cursor = self.connection.cursor(dictionary=True)
        cursor.execute("SELECT * FROM tickets ORDER BY id ASC")
        rows = cursor.fetchall()
        cursor.close()
        return rows

    def get_active_tickets(self):
        self.check_daily_reset()
        self.ensure_connection()
        cursor = self.connection.cursor(dictionary=True)
        today_str = datetime.now().strftime("%Y-%m-%d")
        cursor.execute("""
            SELECT * FROM tickets
            WHERE status IN ('Waiting', 'Called')
            AND DATE(created_at) = %s
            ORDER BY id ASC
        """, (today_str,))
        rows = cursor.fetchall()
        cursor.close()
        return rows

    def call_ticket(self, ticket_id):
        called_at = datetime.now().isoformat(timespec="seconds")
        self.ensure_connection()
        cursor = self.connection.cursor()
        cursor.execute("""
            UPDATE tickets
            SET called_at = %s, status = 'Called'
            WHERE id = %s AND status = 'Waiting'
        """, (called_at, ticket_id))
        cursor.close()

    def call_next(self):
        self.check_daily_reset()
        self.ensure_connection()
        cursor = self.connection.cursor(dictionary=True)
        today_str = datetime.now().strftime("%Y-%m-%d")
        cursor.execute("""
            SELECT * FROM tickets
            WHERE status = 'Waiting'
            AND DATE(created_at) = %s
            ORDER BY id ASC
            LIMIT 1
        """, (today_str,))
        row = cursor.fetchone()
        cursor.close()
        if row:
            self.call_ticket(row["id"])
            return row
        return None

    def mark_seen(self, ticket_id, doctor):
        seen_at = datetime.now().isoformat(timespec="seconds")
        self.ensure_connection()
        cursor = self.connection.cursor()
        cursor.execute("""
            UPDATE tickets
            SET seen_at = %s, doctor = %s, status = 'Seen'
            WHERE id = %s AND status = 'Called'
        """, (seen_at, doctor, ticket_id))
        cursor.close()

    def cancel_ticket(self, ticket_id, reason):
        self.ensure_connection()
        cursor = self.connection.cursor()
        cursor.execute("""
            UPDATE tickets
            SET status = 'Cancelled', cancellation_reason = %s
            WHERE id = %s AND status IN ('Waiting', 'Called')
        """, (reason, ticket_id))
        cursor.close()

    def get_statistics(self, date_from=None, date_to=None):
        self.ensure_connection()
        query = "SELECT * FROM tickets WHERE 1=1"
        parameters = []

        if date_from:
            query += " AND DATE(created_at) >= %s"
            parameters.append(date_from)
        if date_to:
            query += " AND DATE(created_at) <= %s"
            parameters.append(date_to)

        query += " ORDER BY id ASC"
        cursor = self.connection.cursor(dictionary=True)
        cursor.execute(query, parameters)
        rows = cursor.fetchall()
        cursor.close()

        total_seen = 0
        total_waiting = 0
        total_called = 0
        total_cancelled = 0
        doctor_counts = {doctor: 0 for doctor in DOCTORS}
        waiting_times = {hour: [] for hour in range(7, 17)}

        for row in rows:
            status = row["status"]
            if status == "Seen": total_seen += 1
            elif status == "Waiting": total_waiting += 1
            elif status == "Called": total_called += 1
            elif status == "Cancelled": total_cancelled += 1

            doc_name = row["doctor"]
            if status == "Seen" and doc_name:
                if doc_name in doctor_counts:
                    doctor_counts[doc_name] += 1
                else:
                    doctor_counts[doc_name] = 1

            if row["created_at"] and row["called_at"]:
                try:
                    created = datetime.fromisoformat(row["created_at"])
                    called = datetime.fromisoformat(row["called_at"])
                    waiting_minutes = (called - created).total_seconds() / 60
                    if 7 <= called.hour <= 16:
                        waiting_times[called.hour].append(waiting_minutes)
                except Exception:
                    pass

        average_waiting = {hour: (round(sum(waiting_times[hour]) / len(waiting_times[hour]), 1) if waiting_times[hour] else 0) for hour in range(7, 17)}

        return {
            "total_seen": total_seen,
            "total_waiting": total_waiting,
            "total_called": total_called,
            "total_cancelled": total_cancelled,
            "doctor_counts": doctor_counts,
            "average_waiting": average_waiting
        }

    def close(self):
        if self.connection and self.connection.is_connected():
            self.connection.close()


# ============================================================
# LOGIN WINDOW
# ============================================================

class LoginWindow(tk.Toplevel):
    def __init__(self, parent, db):
        super().__init__(parent)
        self.db = db
        self.authenticated_user = None

        self.title("Staff Login - Patient Queue System")
        self.geometry("400x300")
        self.resizable(False, False)
        self.configure(bg=COLORS["background"])
        self.transient(parent)
        self.grab_set()

        header = tk.Frame(self, bg=COLORS["primary_dark"], height=60)
        header.pack(fill="x")
        header.pack_propagate(False)
        tk.Label(header, text="SYSTEM LOGIN", bg=COLORS["primary_dark"], fg="white", font=("Segoe UI", 14, "bold")).pack(pady=15)

        form_frame = tk.Frame(self, bg=COLORS["background"])
        form_frame.pack(padx=30, pady=20, fill="both", expand=True)

        tk.Label(form_frame, text="Username:", bg=COLORS["background"], fg=COLORS["text"], font=("Segoe UI", 10, "bold")).pack(anchor="w")
        self.username_var = tk.StringVar()
        tk.Entry(form_frame, textvariable=self.username_var, font=("Segoe UI", 11), width=28).pack(pady=(0, 10), fill="x")

        tk.Label(form_frame, text="Password:", bg=COLORS["background"], fg=COLORS["text"], font=("Segoe UI", 10, "bold")).pack(anchor="w")
        self.password_var = tk.StringVar()
        tk.Entry(form_frame, textvariable=self.password_var, show="*", font=("Segoe UI", 11), width=28).pack(pady=(0, 15), fill="x")

        tk.Button(
            form_frame, text="LOGIN", bg=COLORS["primary"], fg="white",
            activebackground=COLORS["primary_dark"], activeforeground="white",
            font=("Segoe UI", 10, "bold"), relief="flat", pady=8, command=self.attempt_login
        ).pack(fill="x")

        self.bind("<Return>", lambda event: self.attempt_login())

    def attempt_login(self):
        username = self.username_var.get().strip()
        password = self.password_var.get().strip()

        if not username or not password:
            messagebox.showerror("Error", "Please enter both username and password.", parent=self)
            return

        user = self.db.authenticate_user(username, password)
        if user:
            self.authenticated_user = user
            self.destroy()
        else:
            messagebox.showerror("Login Failed", "Invalid username or password.", parent=self)


# ============================================================
# MAIN APPLICATION
# ============================================================

class PatientQueueApp(tk.Tk):

    def __init__(self, user):
        super().__init__()
        self.current_user = user
        role_label = f"[{self.current_user['role']}]"

        self.title(f"Patient Queue System — Logged in as: {self.current_user['username']} {role_label}")
        self.geometry("1350x850")
        self.minsize(1100, 700)
        self.configure(bg=COLORS["background"])

        self.database = Database()
        self.selected_ticket_id = None

        self.protocol("WM_DELETE_WINDOW", self.close_application)

        self.create_styles()
        self.create_interface()
        self.refresh_everything()
        self.update_clock()

    def create_styles(self):
        style = ttk.Style(self)
        try:
            style.theme_use("clam")
        except Exception:
            pass

        style.configure(".", font=("Segoe UI", 10))
        style.configure("TFrame", background=COLORS["background"])
        style.configure("TLabel", background=COLORS["background"], foreground=COLORS["text"])
        style.configure("Title.TLabel", background=COLORS["background"], foreground=COLORS["primary_dark"], font=("Segoe UI", 24, "bold"))
        style.configure("Heading.TLabel", background=COLORS["background"], foreground=COLORS["primary_dark"], font=("Segoe UI", 13, "bold"))
        style.configure("Treeview", rowheight=34, font=("Segoe UI", 10), background=COLORS["white"], fieldbackground=COLORS["white"], foreground=COLORS["text"])
        style.configure("Treeview.Heading", font=("Segoe UI", 10, "bold"), background=COLORS["primary"], foreground=COLORS["white"])
        style.map("Treeview.Heading", background=[("active", COLORS["primary_dark"])])
        style.configure("TLabelframe", background=COLORS["background"])
        style.configure("TLabelframe.Label", background=COLORS["background"], foreground=COLORS["primary_dark"], font=("Segoe UI", 10, "bold"))

    def create_interface(self):
        self.notebook = ttk.Notebook(self)
        self.notebook.pack(fill="both", expand=True, padx=10, pady=10)

        self.queue_tab = ttk.Frame(self.notebook)
        self.notebook.add(self.queue_tab, text="  Patient Queue  ")

        # Admin and Reception both get the Statistics tab
        if self.current_user["role"] in ("Admin", "Reception"):
            self.statistics_tab = ttk.Frame(self.notebook)
            self.notebook.add(self.statistics_tab, text="  Statistics  ")
            self.create_statistics_tab()

        self.create_queue_tab()

        self.status_label = tk.Label(
            self, text=f"Logged in as {self.current_user['username']} ({self.current_user['role']})", 
            anchor="w", padx=12, pady=8, bg=COLORS["primary_dark"], fg="white", font=("Segoe UI", 10)
        )
        self.status_label.pack(fill="x")

    def create_queue_tab(self):
        header = tk.Frame(self.queue_tab, bg=COLORS["primary_dark"], height=80)
        header.pack(fill="x", padx=20, pady=(15, 10))
        header.pack_propagate(False)

        tk.Label(header, text="PATIENT QUEUE", bg=COLORS["primary_dark"], fg="white", font=("Segoe UI", 24, "bold")).pack(side="left", padx=20)
        
        self.clock_label = tk.Label(header, bg=COLORS["primary_dark"], fg="white", font=("Segoe UI", 11))
        self.clock_label.pack(side="right", padx=20)

        ticket_frame = ttk.LabelFrame(self.queue_tab, text="Create New Patient Ticket", padding=15)
        ticket_frame.pack(fill="x", padx=20, pady=5)

        category_colors = [COLORS["accent"], COLORS["secondary"], COLORS["purple"], COLORS["orange"], COLORS["primary"]]

        for index, category in enumerate(CATEGORIES):
            tk.Button(
                ticket_frame, text=category, bg=category_colors[index], fg="white",
                activebackground=COLORS["primary_dark"], activeforeground="white",
                font=("Segoe UI", 10, "bold"), relief="flat", bd=0, padx=10, pady=12, cursor="hand2",
                command=lambda c=category: self.create_ticket(c)
            ).pack(side="left", expand=True, fill="x", padx=5)

        counter_frame = ttk.LabelFrame(self.queue_tab, text="Ticket Counter", padding=12)
        counter_frame.pack(fill="x", padx=20, pady=10)

        ttk.Label(counter_frame, text="Next Ticket Number:").pack(side="left")
        self.next_ticket_var = tk.StringVar()
        
        # Admin and Reception can edit counter
        entry_state = "normal" if self.current_user["role"] in ("Admin", "Reception") else "disabled"
        self.counter_entry = ttk.Entry(counter_frame, textvariable=self.next_ticket_var, width=10, justify="center", state=entry_state)
        self.counter_entry.pack(side="left", padx=10)
        
        if self.current_user["role"] in ("Admin", "Reception"):
            ttk.Button(counter_frame, text="Save Counter", command=self.save_counter).pack(side="left")
            ttk.Label(counter_frame, text="You can change the next ticket number at any time.").pack(side="left", padx=20)
        else:
            ttk.Label(counter_frame, text="(Admin/Reception restricted configuration)").pack(side="left", padx=20)

        stats_frame = tk.Frame(self.queue_tab, bg=COLORS["background"])
        stats_frame.pack(fill="x", padx=20, pady=5)

        self.seen_variable = tk.StringVar(value="0")
        self.waiting_variable = tk.StringVar(value="0")
        self.called_variable = tk.StringVar(value="0")
        self.cancelled_variable = tk.StringVar(value="0")

        # Admin and Reception get editable stat boxes
        if self.current_user["role"] in ("Admin", "Reception"):
            self.create_editable_stat_box(stats_frame, "PATIENTS SEEN", self.seen_variable, COLORS["green"], self.edit_patients_seen)
        else:
            self.create_stat_box(stats_frame, "PATIENTS SEEN", self.seen_variable, COLORS["green"])

        self.create_stat_box(stats_frame, "PATIENTS WAITING", self.waiting_variable, COLORS["orange"])
        self.create_stat_box(stats_frame, "PATIENTS CALLED", self.called_variable, COLORS["accent"])
        self.create_stat_box(stats_frame, "CANCELLED", self.cancelled_variable, COLORS["red"])

        table_frame = ttk.LabelFrame(self.queue_tab, text="Current Patient Queue", padding=10)
        table_frame.pack(fill="both", expand=True, padx=20, pady=10)

        columns = ("ticket", "category", "arrival", "called", "status", "doctor", "waiting", "creator")
        self.queue_tree = ttk.Treeview(table_frame, columns=columns, show="headings", selectmode="browse")

        headings = {"ticket": "Ticket", "category": "Reason", "arrival": "Arrived", "called": "Called", "status": "Status", "doctor": "Doctor Seen By", "waiting": "Waiting Time", "creator": "Created By"}
        widths = {"ticket": 80, "category": 150, "arrival": 100, "called": 100, "status": 100, "doctor": 140, "waiting": 130, "creator": 120}

        for column in columns:
            self.queue_tree.heading(column, text=headings[column])
            self.queue_tree.column(column, width=widths[column], anchor="center")

        scrollbar = ttk.Scrollbar(table_frame, orient="vertical", command=self.queue_tree.yview)
        self.queue_tree.configure(yscrollcommand=scrollbar.set)
        self.queue_tree.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")
        self.queue_tree.bind("<<TreeviewSelect>>", self.queue_selected)

        actions = tk.Frame(self.queue_tab, bg=COLORS["background"])
        actions.pack(fill="x", padx=20, pady=10)

        user_role = self.current_user["role"]

        if user_role in ("Admin", "Reception"):
            tk.Button(actions, text="CALL NEXT PATIENT", bg=COLORS["primary"], fg="white", activebackground=COLORS["primary_dark"], activeforeground="white", font=("Segoe UI", 10, "bold"), relief="flat", padx=15, pady=10, command=self.call_next).pack(side="left", padx=5)
            tk.Button(actions, text="CALL SELECTED", bg=COLORS["accent"], fg="white", activebackground=COLORS["primary_dark"], activeforeground="white", font=("Segoe UI", 10, "bold"), relief="flat", padx=15, pady=10, command=self.call_selected).pack(side="left", padx=5)
            tk.Button(actions, text="CANCEL TICKET", bg=COLORS["red"], fg="white", activebackground=COLORS["primary_dark"], activeforeground="white", font=("Segoe UI", 10, "bold"), relief="flat", padx=15, pady=10, command=self.cancel_ticket).pack(side="left", padx=5)

        tk.Button(actions, text="PATIENT SEEN", bg=COLORS["green"], fg="white", activebackground=COLORS["primary_dark"], activeforeground="white", font=("Segoe UI", 10, "bold"), relief="flat", padx=15, pady=10, command=self.patient_seen).pack(side="left", padx=5)
        
        tk.Button(actions, text="REFRESH", bg=COLORS["primary_dark"], fg="white", activebackground=COLORS["primary"], activeforeground="white", font=("Segoe UI", 10, "bold"), relief="flat", padx=15, pady=10, command=self.refresh_everything).pack(side="right", padx=5)

    def create_stat_box(self, parent, title, variable, colour):
        frame = tk.Frame(parent, bg=COLORS["white"], highlightbackground=COLORS["border"], highlightthickness=1)
        frame.pack(side="left", expand=True, fill="x", padx=5)
        tk.Label(frame, text=title, bg=colour, fg="white", font=("Segoe UI", 10, "bold"), pady=7).pack(fill="x")
        tk.Label(frame, textvariable=variable, bg=COLORS["white"], fg=colour, font=("Segoe UI", 25, "bold"), pady=12).pack(fill="x")

    def create_editable_stat_box(self, parent, title, variable, colour, command):
        frame = tk.Frame(parent, bg=COLORS["white"], highlightbackground=colour, highlightthickness=2, cursor="hand2")
        frame.pack(side="left", expand=True, fill="x", padx=5)
        header = tk.Label(frame, text=title + "  ✎", bg=colour, fg="white", font=("Segoe UI", 10, "bold"), pady=7, cursor="hand2")
        header.pack(fill="x")
        number = tk.Label(frame, textvariable=variable, bg=COLORS["white"], fg=colour, font=("Segoe UI", 25, "bold"), pady=12, cursor="hand2")
        number.pack(fill="x")
        instruction = tk.Label(frame, text="Click to edit", bg=COLORS["white"], fg=COLORS["muted"], font=("Segoe UI", 8), cursor="hand2")
        instruction.pack(pady=(0, 7))

        for widget in (frame, header, number, instruction):
            widget.bind("<Button-1>", lambda event: command())
        return frame

    def edit_patients_seen(self):
        if self.current_user["role"] not in ("Admin", "Reception"):
            messagebox.showwarning("Restricted", "Only Administrators and Receptionists can manually override the seen count.")
            return

        current_value = self.database.get_manual_seen_count()
        window = tk.Toplevel(self)
        window.title("Edit Patients Seen")
        window.geometry("450x300")
        window.resizable(False, False)
        window.transient(self)
        window.grab_set()
        window.configure(bg=COLORS["background"])

        tk.Label(window, text="EDIT PATIENTS SEEN", bg=COLORS["primary_dark"], fg="white", font=("Segoe UI", 16, "bold"), pady=15).pack(fill="x")
        tk.Label(window, text="Enter the number of patients seen.\nThis number will be saved permanently.", bg=COLORS["background"], fg=COLORS["text"], font=("Segoe UI", 10), pady=20).pack()

        value = tk.StringVar(value=str(current_value))
        entry = tk.Entry(window, textvariable=value, justify="center", font=("Segoe UI", 20, "bold"), width=10)
        entry.pack(pady=5)
        entry.focus_set()
        entry.select_range(0, tk.END)

        button_frame = tk.Frame(window, bg=COLORS["background"])
        button_frame.pack(pady=20)

        def save():
            try:
                number = int(value.get().strip())
                if number < 0: raise ValueError
            except ValueError:
                messagebox.showerror("Invalid Number", "Please enter a whole number of 0 or greater.", parent=window)
                return

            self.database.set_manual_seen_count(number)
            self.status_label.config(text=f"Patients Seen manually changed to {number}")
            window.destroy()
            self.refresh_everything()

        tk.Button(button_frame, text="SAVE", bg=COLORS["green"], fg="white", font=("Segoe UI", 10, "bold"), relief="flat", padx=25, pady=10, command=save).pack(side="left", padx=5)
        tk.Button(button_frame, text="CANCEL", bg=COLORS["red"], fg="white", font=("Segoe UI", 10, "bold"), relief="flat", padx=20, pady=10, command=window.destroy).pack(side="left", padx=5)
        window.bind("<Return>", lambda event: save())
        self.wait_window(window)

    def create_statistics_tab(self):
        header = tk.Frame(self.statistics_tab, bg=COLORS["primary_dark"], height=80)
        header.pack(fill="x", padx=20, pady=(15, 10))
        header.pack_propagate(False)
        tk.Label(header, text="STATISTICS", bg=COLORS["primary_dark"], fg="white", font=("Segoe UI", 24, "bold")).pack(side="left", padx=20)

        filter_frame = ttk.LabelFrame(self.statistics_tab, text="Statistics Date Range", padding=12)
        filter_frame.pack(fill="x", padx=20, pady=5)

        ttk.Label(filter_frame, text="From:").pack(side="left")
        self.date_from_var = tk.StringVar()
        ttk.Entry(filter_frame, textvariable=self.date_from_var, width=14).pack(side="left", padx=5)

        ttk.Label(filter_frame, text="To:").pack(side="left")
        self.date_to_var = tk.StringVar()
        ttk.Entry(filter_frame, textvariable=self.date_to_var, width=14).pack(side="left", padx=5)

        ttk.Button(filter_frame, text="Today", command=self.statistics_today).pack(side="left", padx=5)
        ttk.Button(filter_frame, text="All Records", command=self.statistics_all).pack(side="left", padx=5)
        ttk.Button(filter_frame, text="Update", command=self.refresh_statistics).pack(side="left", padx=5)
        ttk.Button(filter_frame, text="Download Report", command=self.download_report).pack(side="right", padx=5)

        summary = tk.Frame(self.statistics_tab, bg=COLORS["background"])
        summary.pack(fill="x", padx=20, pady=10)

        self.statistics_seen = tk.StringVar(value="0")
        self.statistics_waiting = tk.StringVar(value="0")
        self.statistics_cancelled = tk.StringVar(value="0")

        self.create_editable_stat_box(summary, "PATIENTS SEEN", self.statistics_seen, COLORS["green"], self.edit_patients_seen)
        self.create_stat_box(summary, "WAITING", self.statistics_waiting, COLORS["orange"])
        self.create_stat_box(summary, "CANCELLED", self.statistics_cancelled, COLORS["red"])

        doctor_frame = ttk.LabelFrame(self.statistics_tab, text="Patients Seen By Doctor", padding=10)
        doctor_frame.pack(fill="x", padx=20, pady=5)

        self.doctor_tree = ttk.Treeview(doctor_frame, columns=("doctor", "patients"), show="headings", height=4)
        self.doctor_tree.heading("doctor", text="Doctor")
        self.doctor_tree.heading("patients", text="Patients Seen")
        self.doctor_tree.column("doctor", width=250)
        self.doctor_tree.column("patients", width=180, anchor="center")
        self.doctor_tree.pack(fill="x")

        graph_frame = ttk.LabelFrame(self.statistics_tab, text="Average Waiting Time — 07:00 to 16:00", padding=10)
        graph_frame.pack(fill="both", expand=True, padx=20, pady=10)

        if MATPLOTLIB_AVAILABLE:
            self.figure = Figure(figsize=(10, 4.5), dpi=100)
            self.axis = self.figure.add_subplot(111)
            self.graph_canvas = FigureCanvasTkAgg(self.figure, master=graph_frame)
            self.graph_canvas.get_tk_widget().pack(fill="both", expand=True)
        else:
            self.figure = None
            self.axis = None
            self.graph_canvas = None
            ttk.Label(graph_frame, text="Matplotlib is not installed.\n\nRun:\npip install matplotlib", font=("Segoe UI", 12)).pack(expand=True)
        
        self.refresh_statistics()

    def create_ticket(self, category):
        if self.current_user["role"] not in ("Admin", "Reception"):
            messagebox.showwarning("Restricted Action", "Doctors are not permitted to create tickets.")
            return

        try:
            ticket = self.database.create_ticket(category, self.current_user["username"])
            self.status_label.config(text=f"Ticket {ticket} created — {category}")
            self.refresh_everything()
        except Exception as error:
            messagebox.showerror("Error", str(error))

    def save_counter(self):
        if self.current_user["role"] not in ("Admin", "Reception"):
            return
        value = self.next_ticket_var.get().strip()
        try:
            number = int(value)
            if number < 1: raise ValueError
            self.database.set_next_ticket_number(number)
            self.status_label.config(text=f"Next ticket number set to {number:03d}")
            self.refresh_counter()
        except ValueError:
            messagebox.showerror("Invalid Number", "Please enter a whole number greater than 0.")

    def refresh_counter(self):
        number = self.database.get_next_ticket_number()
        self.next_ticket_var.set(f"{number:03d}")

    def queue_selected(self, event=None):
        selected = self.queue_tree.selection()
        if selected:
            self.selected_ticket_id = int(selected[0])

    def get_selected_ticket(self):
        if not self.selected_ticket_id:
            messagebox.showwarning("No Ticket Selected", "Please select a patient ticket first.")
            return None

        self.database.ensure_connection()
        cursor = self.database.connection.cursor(dictionary=True)
        cursor.execute("SELECT * FROM tickets WHERE id = %s", (self.selected_ticket_id,))
        row = cursor.fetchone()
        cursor.close()

        if not row:
            messagebox.showerror("Error", "Ticket could not be found.")
            return None
        return row

    def call_next(self):
        if self.current_user["role"] not in ("Admin", "Reception"):
            messagebox.showwarning("Restricted Action", "Doctors cannot call patients.")
            return

        row = self.database.call_next()
        if not row:
            messagebox.showinfo("Queue Empty", "There are currently no patients waiting.")
            return

        messagebox.showinfo("CALL PATIENT", f"PLEASE CALL PATIENT\n\nTICKET NUMBER\n\n     {row['ticket_number']}\n\nReason: {row['category']}")
        self.status_label.config(text=f"Called patient {row['ticket_number']}")
        self.refresh_queue()

        if self.queue_tree.exists(str(row["id"])):
            self.queue_tree.selection_set(str(row["id"]))
            self.queue_tree.focus(str(row["id"]))
            self.selected_ticket_id = row["id"]

    def call_selected(self):
        if self.current_user["role"] not in ("Admin", "Reception"):
            messagebox.showwarning("Restricted Action", "Doctors cannot call specific patients.")
            return

        row = self.get_selected_ticket()
        if not row: return
        if row["status"] != "Waiting":
            messagebox.showwarning("Cannot Call", "This patient has already been called.")
            return

        self.database.call_ticket(row["id"])
        messagebox.showinfo("CALL PATIENT", f"PLEASE CALL PATIENT\n\nTICKET NUMBER\n\n     {row['ticket_number']}")
        self.refresh_queue()

    def patient_seen(self):
        row = self.get_selected_ticket()
        if not row: return
        if row["status"] != "Called":
            messagebox.showwarning("Patient Not Called", "The patient must first be called before being marked as seen.")
            return

        doctor_name = self.current_user["username"]

        self.database.mark_seen(row["id"], doctor_name)
        self.status_label.config(text=f"Patient {row['ticket_number']} seen by {doctor_name}")
        self.selected_ticket_id = None
        self.refresh_everything()

    def cancel_ticket(self):
        if self.current_user["role"] not in ("Admin", "Reception"):
            messagebox.showwarning("Restricted Action", "Doctors cannot cancel tickets.")
            return

        row = self.get_selected_ticket()
        if not row: return
        if row["status"] not in ("Waiting", "Called"):
            messagebox.showwarning("Cannot Cancel", "This ticket cannot be cancelled.")
            return

        window = tk.Toplevel(self)
        window.title("Cancel Ticket")
        window.geometry("500x280")
        window.transient(self)
        window.grab_set()

        ttk.Label(window, text=f"Cancel Ticket {row['ticket_number']}?", style="Heading.TLabel").pack(pady=20)
        ttk.Label(window, text="Cancellation reason:").pack(anchor="w", padx=30)

        reason = tk.StringVar()
        ttk.Entry(window, textvariable=reason, width=55).pack(padx=30, pady=10)

        def confirm():
            self.database.cancel_ticket(row["id"], reason.get().strip())
            window.destroy()
            self.selected_ticket_id = None
            self.status_label.config(text=f"Ticket {row['ticket_number']} cancelled")
            self.refresh_everything()

        button_frame = ttk.Frame(window)
        button_frame.pack(pady=15)
        ttk.Button(button_frame, text="Cancel Ticket", command=confirm).pack(side="left", padx=5)
        ttk.Button(button_frame, text="Close", command=window.destroy).pack(side="left", padx=5)
        self.wait_window(window)

    def refresh_queue(self):
        for item in self.queue_tree.get_children():
            self.queue_tree.delete(item)

        rows = self.database.get_active_tickets()
        waiting_count = 0
        called_count = 0

        for row in rows:
            if row["status"] == "Waiting": waiting_count += 1
            elif row["status"] == "Called": called_count += 1

            created = datetime.fromisoformat(row["created_at"])
            if row["called_at"]:
                called = datetime.fromisoformat(row["called_at"])
                waiting_seconds = (called - created).total_seconds()
            else:
                waiting_seconds = (datetime.now() - created).total_seconds()

            waiting_seconds = max(0, int(waiting_seconds))
            minutes = waiting_seconds // 60
            seconds = waiting_seconds % 60

            self.queue_tree.insert(
                "", "end", iid=str(row["id"]),
                values=(
                    row["ticket_number"],
                    row["category"],
                    created.strftime("%H:%M:%S"),
                    datetime.fromisoformat(row["called_at"]).strftime("%H:%M:%S") if row["called_at"] else "",
                    row["status"],
                    row["doctor"] or "",
                    f"{minutes}m {seconds:02d}s",
                    row["created_by"] or ""
                )
            )

        self.waiting_variable.set(str(waiting_count))
        self.called_variable.set(str(called_count))
        
        today_str = datetime.now().strftime("%Y-%m-%d")
        stats_data = self.database.get_statistics(today_str, today_str)
        auto_seen = stats_data["total_seen"]
        manual_seen = self.database.get_manual_seen_count()
        total_seen_combined = auto_seen + manual_seen
        
        self.seen_variable.set(str(total_seen_combined))

        all_rows = self.database.get_all_tickets()
        cancelled = sum(1 for row in all_rows if row["status"] == "Cancelled" and row["created_at"].startswith(today_str))
        self.cancelled_variable.set(str(cancelled))

    def refresh_everything(self):
        self.refresh_counter()
        self.refresh_queue()
        if self.current_user["role"] in ("Admin", "Reception"):
            self.refresh_statistics()

    def update_clock(self):
        current_time = datetime.now().strftime("%A, %d %B %Y   %H:%M:%S")
        self.clock_label.config(text=current_time)
        self.refresh_queue()
        self.after(1000, self.update_clock)

    def statistics_today(self):
        today = datetime.now().strftime("%Y-%m-%d")
        self.date_from_var.set(today)
        self.date_to_var.set(today)
        self.refresh_statistics()

    def statistics_all(self):
        self.date_from_var.set("")
        self.date_to_var.set("")
        self.refresh_statistics()

    def get_date_filter(self):
        date_from = self.date_from_var.get().strip()
        date_to = self.date_to_var.get().strip()
        for date_value in (date_from, date_to):
            if date_value:
                try:
                    datetime.strptime(date_value, "%Y-%m-%d")
                except ValueError:
                    messagebox.showerror("Invalid Date", "Please use YYYY-MM-DD format.")
                    return None, None
        return date_from or None, date_to or None

    def refresh_statistics(self):
        if self.current_user["role"] not in ("Admin", "Reception"): return
        date_from, date_to = self.get_date_filter()
        if self.date_from_var.get() and date_from is None: return

        data = self.database.get_statistics(date_from, date_to)
        manual_seen = self.database.get_manual_seen_count()
        
        total_seen_combined = data["total_seen"] + manual_seen

        self.statistics_seen.set(str(total_seen_combined))
        self.statistics_waiting.set(str(data["total_waiting"]))
        self.statistics_cancelled.set(str(data["total_cancelled"]))

        for item in self.doctor_tree.get_children():
            self.doctor_tree.delete(item)

        all_doc_keys = set(DOCTORS).union(data["doctor_counts"].keys())
        for doctor in sorted(all_doc_keys):
            count = data["doctor_counts"].get(doctor, 0)
            self.doctor_tree.insert("", "end", values=(doctor, count))

        self.update_graph(data["average_waiting"])

    def update_graph(self, average_waiting):
        if not MATPLOTLIB_AVAILABLE or self.current_user["role"] not in ("Admin", "Reception"): return

        self.axis.clear()
        hours = list(range(7, 17))
        values = [average_waiting[hour] for hour in hours]
        labels = [f"{hour:02d}:00" for hour in hours]

        self.axis.plot(labels, values, marker="o", linewidth=2)
        self.axis.set_title("Average Patient Waiting Time", fontsize=14, fontweight="bold")
        self.axis.set_xlabel("Time Patient Was Called")
        self.axis.set_ylabel("Average Waiting Time (Minutes)")
        self.axis.grid(True, alpha=0.25)

        for index, value in enumerate(values):
            if value > 0:
                self.axis.annotate(f"{value:.1f} min", (index, value), textcoords="offset points", xytext=(0, 8), ha="center")

        self.figure.tight_layout()
        self.graph_canvas.draw()

    def download_report(self):
        if self.current_user["role"] not in ("Admin", "Reception"): return
        date_from, date_to = self.get_date_filter()
        if self.date_from_var.get() and date_from is None: return

        rows = self.database.get_all_tickets()
        filtered_rows = []

        for row in rows:
            created_date = datetime.fromisoformat(row["created_at"]).date()
            if date_from and created_date < datetime.strptime(date_from, "%Y-%m-%d").date(): continue
            if date_to and created_date > datetime.strptime(date_to, "%Y-%m-%d").date(): continue
            filtered_rows.append(row)

        data = self.database.get_statistics(date_from, date_to)
        filename = filedialog.asksaveasfilename(title="Save Statistics Report", defaultextension=".xlsx", filetypes=[("Excel Workbook", "*.xlsx"), ("CSV File", "*.csv")])

        if not filename: return

        try:
            if filename.lower().endswith(".xlsx"):
                self.export_excel(filename, filtered_rows, data)
            else:
                self.export_csv(filename, filtered_rows, data)
            messagebox.showinfo("Export Complete", f"Report successfully saved:\n\n{filename}")
        except Exception as error:
            messagebox.showerror("Export Error", str(error))

    def export_csv(self, filename, rows, data):
        manual_seen = self.database.get_manual_seen_count()
        total_seen_combined = data["total_seen"] + manual_seen
        with open(filename, "w", newline="", encoding="utf-8-sig") as file:
            writer = csv.writer(file)
            writer.writerow(["PATIENT QUEUE STATISTICS REPORT"])
            writer.writerow([])
            writer.writerow(["Total Patients Seen", total_seen_combined])
            writer.writerow(["Patients Waiting", data["total_waiting"]])
            writer.writerow(["Patients Cancelled", data["total_cancelled"]])
            writer.writerow([])
            writer.writerow(["DOCTOR", "PATIENTS SEEN"])
            for doctor in sorted(data["doctor_counts"].keys()): 
                writer.writerow([doctor, data["doctor_counts"][doctor]])
            writer.writerow([])
            writer.writerow(["TIME", "AVERAGE WAITING TIME (MINUTES)"])
            for hour in range(7, 17): writer.writerow([f"{hour:02d}:00", data["average_waiting"][hour]])
            writer.writerow([])
            writer.writerow(["TICKET", "CATEGORY", "ARRIVED", "CALLED", "SEEN", "DOCTOR SEEN BY", "STATUS", "CANCELLATION REASON", "CREATED BY"])
            for row in rows:
                writer.writerow([row["ticket_number"], row["category"], row["created_at"], row["called_at"] or "", row["seen_at"] or "", row["doctor"] or "", row["status"], row["cancellation_reason"] or "", row["created_by"] or ""])

    def export_excel(self, filename, rows, data):
        if not OPENPYXL_AVAILABLE:
            raise RuntimeError("Excel export requires openpyxl.\n\nInstall it with:\npip install openpyxl")
        
        manual_seen = self.database.get_manual_seen_count()
        total_seen_combined = data["total_seen"] + manual_seen
        workbook = Workbook()
        sheet = workbook.active
        sheet.title = "Summary"
        sheet.append(["PATIENT QUEUE STATISTICS REPORT"])
        sheet.append([])
        sheet.append(["Total Patients Seen", total_seen_combined])
        sheet.append(["Patients Waiting", data["total_waiting"]])
        sheet.append(["Patients Cancelled", data["total_cancelled"]])
        sheet.append([])
        sheet.append(["DOCTOR", "PATIENTS SEEN"])
        for doctor in sorted(data["doctor_counts"].keys()): 
            sheet.append([doctor, data["doctor_counts"][doctor]])
        sheet.append([])
        sheet.append(["TIME", "AVERAGE WAITING TIME (MINUTES)"])
        for hour in range(7, 17): sheet.append([f"{hour:02d}:00", data["average_waiting"][hour]])

        details = workbook.create_sheet("Patient Details")
        details.append(["Ticket", "Category", "Arrived", "Called", "Seen", "Doctor Seen By", "Status", "Cancellation Reason", "Created By"])
        for row in rows:
            details.append([row["ticket_number"], row["category"], row["created_at"], row["called_at"] or "", row["seen_at"] or "", row["doctor"] or "", row["status"], row["cancellation_reason"] or "", row["created_by"] or ""])

        workbook.save(filename)

    def close_application(self):
        answer = messagebox.askyesno("Close Program", "Are you sure you want to close the program?")
        if answer:
            self.database.close()
            self.destroy()


# ============================================================
# START APPLICATION WITH LOGIN GATEWAY
# ============================================================

if __name__ == "__main__":
    try:
        print("Initializing database connection...")
        db_instance = Database()
    except Exception as e:
        print(f"Database initialization failed: {e}")
        exit()

    try:
        cursor = db_instance.connection.cursor(dictionary=True)
        
        users_to_seed = [
            ("admin", "Caleb@2404", "Admin"),
            ("Dr Desai", "Desai@2026", "Doctor"),
            ("Dr Joosub", "Joosub@2026", "Doctor"),
            ("Dr Govender", "Govender@2026", "Doctor"),
            ("Locum Dr", "Locum@2026", "Doctor"),
            ("Reception", "Reception@2026", "Reception")
        ]

        for uname, raw_pass, urole in users_to_seed:
            cursor.execute("SELECT * FROM users WHERE username = %s", (uname,))
            if not cursor.fetchone():
                hashed_pass = bcrypt.hashpw(raw_pass.encode('utf-8'), bcrypt.gensalt()).decode('utf-8')
                cursor.execute(
                    "INSERT INTO users (username, password, role) VALUES (%s, %s, %s)",
                    (uname, hashed_pass, urole)
                )
            else:
                hashed_pass = bcrypt.hashpw(raw_pass.encode('utf-8'), bcrypt.gensalt()).decode('utf-8')
                cursor.execute(
                    "UPDATE users SET password = %s, role = %s WHERE username = %s",
                    (hashed_pass, urole, uname)
                )
            
        cursor.close()
    except Exception as e:
        print(f"Could not seed users: {e}")

    print("Launching login window...")
    
    root_tk = tk.Tk()
    login = LoginWindow(root_tk, db_instance)
    root_tk.mainloop()

    if hasattr(login, "authenticated_user") and login.authenticated_user:
        print(f"Logged in as {login.authenticated_user['username']}")
        app = PatientQueueApp(login.authenticated_user)
        app.mainloop()
    else:
        print("Login cancelled or failed.")
        db_instance.close()