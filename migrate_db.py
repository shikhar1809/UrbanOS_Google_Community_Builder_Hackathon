import sqlite3

conn = sqlite3.connect('urbanos.db')
c = conn.cursor()

try:
    c.execute("ALTER TABLE messages ADD COLUMN constituency_zone VARCHAR;")
    print("Added constituency_zone")
except sqlite3.OperationalError as e:
    print(e)

try:
    c.execute("ALTER TABLE messages ADD COLUMN estimated_budget INTEGER;")
    print("Added estimated_budget")
except sqlite3.OperationalError as e:
    print(e)

conn.commit()
conn.close()
