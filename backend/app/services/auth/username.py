from sqlalchemy.orm import Session


def generate_unique_username(
    db: Session,
    model,
    field_name: str,
    email: str,
    fallback: str = "user",
) -> str:
    """Derive a unique username from an email's local-part, appending a
    numeric suffix until it doesn't collide with an existing row."""

    base = "".join(
        ch for ch in email.split("@")[0] if ch.isalnum() or ch == "_"
    ) or fallback

    column = getattr(model, field_name)
    candidate = base
    suffix = 1

    while db.query(model).filter(column == candidate).first():
        suffix += 1
        candidate = f"{base}{suffix}"

    return candidate
