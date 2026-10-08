from pydantic import BaseModel


class ResumeUploadResponse(BaseModel):
    id: int
    user_id: int
    file_name: str
    file_type: str
    message: str