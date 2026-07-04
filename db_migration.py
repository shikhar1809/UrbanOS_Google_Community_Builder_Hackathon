import sqlite3

def migrate():
    conn = sqlite3.connect('urbanos.db')
    cursor = conn.cursor()
    
    try:
        cursor.execute("ALTER TABLE messages ADD COLUMN reference_id VARCHAR(50)")
        print("Added reference_id column.")
    except sqlite3.OperationalError as e:
        print(f"reference_id column might already exist: {e}")

    try:
        cursor.execute("ALTER TABLE messages ADD COLUMN status VARCHAR(50)")
        print("Added status column.")
    except sqlite3.OperationalError as e:
        print(f"status column might already exist: {e}")

    # Set default status for existing records to 'Closed' so they don't break logic
    cursor.execute("UPDATE messages SET status = 'Closed' WHERE status IS NULL")
    
    conn.commit()
    conn.close()
    print("Migration complete!")

if __name__ == "__main__":
    migrate()
