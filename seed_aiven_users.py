import mysql.connector
import bcrypt

# Aiven connection details
DB_CONFIG = {
    "host": os.environ.get("DB_HOST", "mysql-3aceb097-moosadesaidatabase.l.aivencloud.com"),
    "port": int(os.environ.get("DB_PORT", 13603)),
    "user": os.environ.get("DB_USER", "avnadmin"),
    "password": os.environ.get("DB_PASSWORD"),  # Pulls securely from environment
    "database": os.environ.get("DB_NAME", "defaultdb"),
    "use_pure": True
}

users_to_seed = [
    ("admin", "Caleb@2404", "Admin"),
    ("Dr Desai", "Desai@2026", "Doctor"),
    ("Dr Joosub", "Joosub@2026", "Doctor"),
    ("Dr Govender", "Govender@2026", "Doctor"),
    ("Locum Dr", "Locum@2026", "Doctor"),
    ("Reception", "Reception@2026", "Admin")
]

def seed_users():
    conn = mysql.connector.connect(**DB_CONFIG)
    cursor = conn.cursor()

    for uname, raw_pass, urole in users_to_seed:
        hashed_pass = bcrypt.hashpw(raw_pass.encode('utf-8'), bcrypt.gensalt()).decode('utf-8')
        
        cursor.execute("SELECT * FROM users WHERE username = %s", (uname,))
        if not cursor.fetchone():
            cursor.execute(
                "INSERT INTO users (username, password, role) VALUES (%s, %s, %s)",
                (uname, hashed_pass, urole)
            )
            print(f"Created user: {uname}")
        else:
            cursor.execute(
                "UPDATE users SET password = %s, role = %s WHERE username = %s",
                (hashed_pass, urole, uname)
            )
            print(f"Updated user: {uname}")

    conn.commit()
    cursor.close()
    conn.close()
    print("All users successfully seeded into Aiven cloud database!")

if __name__ == "__main__":
    seed_users()