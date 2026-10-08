from passlib.context import CryptContext

from backend.app.database.database import SessionLocal
from backend.app.models.user import User


pwd_context = CryptContext(
    schemes=["bcrypt"],
    deprecated="auto"
)


def create_admin():

    db = SessionLocal()

    try:

        # ==========================================
        # ADMIN DETAILS
        # ==========================================

        username = "admin"
        email = "admin@jobpilot.ai"
        password = "Admin@12345"


        # ==========================================
        # CHECK EXISTING USERNAME
        # ==========================================

        existing_user = db.query(User).filter(
            User.username == username
        ).first()

        if existing_user:

            existing_user.role = "admin"

            db.commit()

            print()
            print("Existing user converted to ADMIN.")
            print("Username:", existing_user.username)
            print("Email:", existing_user.email)
            print("Role:", existing_user.role)

            return


        # ==========================================
        # CHECK EXISTING EMAIL
        # ==========================================

        existing_email = db.query(User).filter(
            User.email == email
        ).first()

        if existing_email:

            existing_email.role = "admin"

            db.commit()

            print()
            print("Existing email converted to ADMIN.")
            print("Username:", existing_email.username)
            print("Email:", existing_email.email)
            print("Role:", existing_email.role)

            return


        # ==========================================
        # CREATE ADMIN
        # ==========================================

        admin = User(

            full_name="JobPilot AI Owner",

            username=username,

            email=email,

            mobile="9999999999",

            hashed_password=pwd_context.hash(
                password
            ),

            location="Tamil Nadu",

            education="Administration",

            graduation_year=2026,

            experience_level="Admin",

            preferred_role="Administrator",

            preferred_work_mode="On-site",

            role="admin"
        )


        db.add(admin)

        db.commit()

        db.refresh(admin)


        print()
        print("================================")
        print("ADMIN ACCOUNT CREATED")
        print("================================")

        print("Username:", admin.username)

        print("Email:", admin.email)

        print("Password:", password)

        print("Role:", admin.role)

        print("================================")


    except Exception as error:

        db.rollback()

        print()
        print("ERROR CREATING ADMIN:")
        print(error)


    finally:

        db.close()


if __name__ == "__main__":

    create_admin()