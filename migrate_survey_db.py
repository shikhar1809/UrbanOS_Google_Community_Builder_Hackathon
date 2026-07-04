import sqlite3

conn = sqlite3.connect('urbanos.db')
c = conn.cursor()

c.execute("""
CREATE TABLE IF NOT EXISTS surveys (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    question TEXT NOT NULL,
    options TEXT NOT NULL,
    is_active BOOLEAN DEFAULT 0
)
""")

c.execute("""
CREATE TABLE IF NOT EXISTS survey_responses (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    survey_id INTEGER NOT NULL,
    sender VARCHAR NOT NULL,
    selected_option TEXT NOT NULL,
    FOREIGN KEY(survey_id) REFERENCES surveys(id)
)
""")

# Insert a default active survey so we have something to test with
c.execute("SELECT count(*) FROM surveys")
if c.fetchone()[0] == 0:
    c.execute("INSERT INTO surveys (question, options, is_active) VALUES (?, ?, ?)", 
              ("Which sector needs immediate funding this month?", "Roads,Water,Electricity,Parks", 1))

conn.commit()
conn.close()
print("Survey tables created.")
