from datetime import datetime

from sqlalchemy import DateTime, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from backend.app.database.database import Base


# An admin-chosen override for one runtime setting (see services/admin_settings.py for the whitelist). The value wins over
# the .env file while the row exists; deleting the row ("reset") puts the .env value back. Never holds a secret.

class AdminSetting(Base):

    __tablename__ = "admin_settings"

    key: Mapped[str] = mapped_column(String(80), primary_key=True)
    value: Mapped[str] = mapped_column(Text, nullable=False)
    updated_by_admin_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=datetime.utcnow)
