import sqlite3


DATABASE = "jobpilot.db"


connection = sqlite3.connect(DATABASE)

cursor = connection.cursor()


cursor.execute("""
CREATE TABLE IF NOT EXISTS resume_analyses (

    id INTEGER PRIMARY KEY AUTOINCREMENT,

    resume_id INTEGER NOT NULL UNIQUE,

    user_id INTEGER NOT NULL,

    summary TEXT,

    skills TEXT,

    job_roles TEXT,

    education TEXT,

    experience TEXT,

    experience_level TEXT,

    raw_text TEXT,

    analyzed_at TEXT,

    FOREIGN KEY (resume_id)
        REFERENCES resumes(id),

    FOREIGN KEY (user_id)
        REFERENCES users(id)

)
""")


connection.commit()

connection.close()


print(
    "Resume analysis table ready."
)