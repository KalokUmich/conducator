"""Pydantic schemas for file upload functionality."""
import uuid
import time
from enum import Enum
from typing import Optional
from pydantic import BaseModel, Field


class FileType(str, Enum):
    """Supported file type categories."""
    IMAGE = "image"
    PDF = "pdf"
    AUDIO = "audio"
    OTHER = "other"


class FileMetadata(BaseModel):
    """Metadata for an uploaded file."""
    id: str = Field(default_factory=lambda: str(uuid.uuid4()), description="Unique file ID")
    room_id: str = Field(..., description="Room ID this file belongs to")
    user_id: str = Field(..., description="User ID who uploaded the file")
    display_name: str = Field(..., description="Display name of uploader")
    original_filename: str = Field(..., description="Original filename")
    stored_filename: str = Field(..., description="Filename on disk (UUID-based)")
    file_type: FileType = Field(..., description="File type category")
    mime_type: str = Field(..., description="MIME type of the file")
    size_bytes: int = Field(..., description="File size in bytes")
    uploaded_at: float = Field(default_factory=time.time, description="Upload timestamp")


class FileUploadResponse(BaseModel):
    """Response after successful file upload."""
    id: str = Field(..., description="File ID")
    original_filename: str = Field(..., description="Original filename")
    file_type: FileType = Field(..., description="File type category")
    mime_type: str = Field(..., description="MIME type")
    size_bytes: int = Field(..., description="File size in bytes")
    download_url: str = Field(..., description="URL to download the file")


class FileMessage(BaseModel):
    """Chat message containing a file attachment."""
    id: str = Field(default_factory=lambda: str(uuid.uuid4()), description="Message ID")
    room_id: str = Field(..., description="Room ID")
    user_id: str = Field(..., description="User ID of sender")
    display_name: str = Field(..., description="Display name of sender")
    role: str = Field(..., description="Role of sender (host/engineer)")
    file_id: str = Field(..., description="ID of the uploaded file")
    original_filename: str = Field(..., description="Original filename")
    file_type: FileType = Field(..., description="File type category")
    mime_type: str = Field(..., description="MIME type")
    size_bytes: int = Field(..., description="File size in bytes")
    download_url: str = Field(..., description="URL to download the file")
    caption: Optional[str] = Field(None, description="Optional caption for the file")
    ts: float = Field(default_factory=time.time, description="Timestamp")


# File size limit: 20MB
MAX_FILE_SIZE_BYTES = 20 * 1024 * 1024

# Allowed MIME types by category
ALLOWED_MIME_TYPES = {
    FileType.IMAGE: [
        "image/jpeg",
        "image/png", 
        "image/gif",
        "image/webp",
        "image/svg+xml",
    ],
    FileType.PDF: [
        "application/pdf",
    ],
    FileType.AUDIO: [
        "audio/mpeg",
        "audio/mp3",
        "audio/wav",
        "audio/ogg",
        "audio/mp4",
        "audio/x-m4a",
        "audio/flac",
    ],
}


def get_file_type(mime_type: str) -> FileType:
    """Determine file type category from MIME type."""
    for file_type, mime_types in ALLOWED_MIME_TYPES.items():
        if mime_type in mime_types:
            return file_type
    return FileType.OTHER



