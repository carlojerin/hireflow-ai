import sqlite3


DATABASE_PATH = "jobpilot.db"


def column_exists(cursor, table_name, column_name):

    cursor.execute(
        f"PRAGMA table_info({table_name})"
    )

    columns = cursor.fetchall()

    return any(
        column[1] == column_name
        for column in columns
    )


def add_column(
    cursor,
    column_name,
    column_definition
):

    if not column_exists(
        cursor,
        "users",
        column_name
    ):

        cursor.execute(
            f"""
            ALTER TABLE users
            ADD COLUMN {column_name}
            {column_definition}
            """
        )

        print(
            f"Added column: {column_name}"
        )

    else:

        print(
            f"Already exists: {column_name}"
        )


def main():

    connection = sqlite3.connect(
        DATABASE_PATH
    )

    cursor = connection.cursor()


    columns = {

        "full_name":
            "VARCHAR(150)",

        "mobile":
            "VARCHAR(20)",

        "hashed_password":
            "VARCHAR(255)",

        "location":
            "VARCHAR(150)",

        "education":
            "VARCHAR(200)",

        "graduation_year":
            "INTEGER",

        "experience_level":
            "VARCHAR(50)",

        "preferred_role":
            "VARCHAR(150)",

        "preferred_work_mode":
            "VARCHAR(50)",

        # =====================================
        # ADMIN / USER ROLE
        # =====================================

        "role":
            "VARCHAR(20) DEFAULT 'user'"
    }


    for column_name, definition in columns.items():

        add_column(
            cursor,
            column_name,
            definition
        )


    connection.commit()

    connection.close()


    print()
    print(
        "User database migration completed."
    )


if __name__ == "__main__":

    main()