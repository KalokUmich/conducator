"""File storage service for Conductor.

Handles file storage on disk and metadata tracking in DuckDB.
Files are stored in: uploads/{room_id}/{uuid}.{ext}
"""
import os
import uuid
import shutil
import logging
from pathlib import Path
from typing import Optional, List
from datetime import datetime

import duckdb

from .schemas import FileMetadata, FileType, get_file_type, MAX_FILE_SIZE_BYTES

logger = logging.getLogger(__name__)


class FileStorageService:
    """Service for managing file uploads and storage."""
    
    _instance: Optional["FileStorageService"] = None
    _upload_dir: str = "uploads"
    _db_path: str = "file_metadata.duckdb"
    
    def __init__(self, upload_dir: Optional[str] = None, db_path: Optional[str] = None):
        """Initialize the file storage service."""
        if upload_dir:
            self._upload_dir = upload_dir
        if db_path:
            self._db_path = db_path
        
        self._connection: Optional[duckdb.DuckDBPyConnection] = None
        self._ensure_upload_dir()
        self._initialize_db()
    
    @classmethod
    def get_instance(cls, upload_dir: Optional[str] = None, db_path: Optional[str] = None) -> "FileStorageService":
        """Get or create the singleton instance."""
        if cls._instance is None:
            cls._instance = cls(upload_dir, db_path)
        return cls._instance
    
    @classmethod
    def reset_instance(cls) -> None:
        """Reset the singleton instance (for testing)."""
        if cls._instance and cls._instance._connection:
            cls._instance._connection.close()
        cls._instance = None
    
    def _ensure_upload_dir(self) -> None:
        """Ensure the upload directory exists."""
        Path(self._upload_dir).mkdir(parents=True, exist_ok=True)
    
    def _get_connection(self) -> duckdb.DuckDBPyConnection:
        """Get or create database connection."""
        if self._connection is None:
            self._connection = duckdb.connect(self._db_path)
        return self._connection
    
    def _initialize_db(self) -> None:
        """Initialize the database schema."""
        conn = self._get_connection()
        conn.execute("""
            CREATE TABLE IF NOT EXISTS file_metadata (
                id VARCHAR PRIMARY KEY,
                room_id VARCHAR NOT NULL,
                user_id VARCHAR NOT NULL,
                display_name VARCHAR NOT NULL,
                original_filename VARCHAR NOT NULL,
                stored_filename VARCHAR NOT NULL,
                file_type VARCHAR NOT NULL,
                mime_type VARCHAR NOT NULL,
                size_bytes BIGINT NOT NULL,
                uploaded_at TIMESTAMP NOT NULL
            )
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_file_room_id ON file_metadata(room_id)
        """)
    
    def _get_room_dir(self, room_id: str) -> Path:
        """Get the directory path for a room's files."""
        return Path(self._upload_dir) / room_id
    
    async def save_file(
        self,
        room_id: str,
        user_id: str,
        display_name: str,
        filename: str,
        content: bytes,
        mime_type: str,
    ) -> FileMetadata:
        """Save an uploaded file to disk and record metadata.
        
        Args:
            room_id: Room ID the file belongs to
            user_id: User ID who uploaded the file
            display_name: Display name of uploader
            filename: Original filename
            content: File content as bytes
            mime_type: MIME type of the file
            
        Returns:
            FileMetadata object with file information
            
        Raises:
            ValueError: If file exceeds size limit
        """
        # Check file size
        size_bytes = len(content)
        if size_bytes > MAX_FILE_SIZE_BYTES:
            raise ValueError(
                f"File size ({size_bytes} bytes) exceeds limit "
                f"({MAX_FILE_SIZE_BYTES} bytes = 20MB)"
            )
        
        # Generate unique filename
        file_id = str(uuid.uuid4())
        ext = Path(filename).suffix.lower() or ""
        stored_filename = f"{file_id}{ext}"
        
        # Determine file type
        file_type = get_file_type(mime_type)
        
        # Ensure room directory exists
        room_dir = self._get_room_dir(room_id)
        room_dir.mkdir(parents=True, exist_ok=True)
        
        # Save file to disk
        file_path = room_dir / stored_filename
        file_path.write_bytes(content)
        
        logger.info(f"Saved file: {file_path} ({size_bytes} bytes)")
        
        # Create metadata
        metadata = FileMetadata(
            id=file_id,
            room_id=room_id,
            user_id=user_id,
            display_name=display_name,
            original_filename=filename,
            stored_filename=stored_filename,
            file_type=file_type,
            mime_type=mime_type,
            size_bytes=size_bytes,
        )
        
        # Save to database
        conn = self._get_connection()
        conn.execute(
            """
            INSERT INTO file_metadata 
            (id, room_id, user_id, display_name, original_filename, stored_filename,
             file_type, mime_type, size_bytes, uploaded_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                metadata.id,
                metadata.room_id,
                metadata.user_id,
                metadata.display_name,
                metadata.original_filename,
                metadata.stored_filename,
                metadata.file_type.value,
                metadata.mime_type,
                metadata.size_bytes,
                datetime.fromtimestamp(metadata.uploaded_at),
            ]
        )
        
        return metadata

    def get_file(self, file_id: str) -> Optional[FileMetadata]:
        """Get file metadata by ID."""
        conn = self._get_connection()
        result = conn.execute(
            """
            SELECT id, room_id, user_id, display_name, original_filename, stored_filename,
                   file_type, mime_type, size_bytes, uploaded_at
            FROM file_metadata
            WHERE id = ?
            """,
            [file_id]
        ).fetchone()

        if not result:
            return None

        return FileMetadata(
            id=result[0],
            room_id=result[1],
            user_id=result[2],
            display_name=result[3],
            original_filename=result[4],
            stored_filename=result[5],
            file_type=FileType(result[6]),
            mime_type=result[7],
            size_bytes=result[8],
            uploaded_at=result[9].timestamp() if result[9] else 0,
        )

    def get_file_path(self, file_id: str) -> Optional[Path]:
        """Get the file path on disk for a file ID."""
        metadata = self.get_file(file_id)
        if not metadata:
            return None

        file_path = self._get_room_dir(metadata.room_id) / metadata.stored_filename
        if not file_path.exists():
            return None

        return file_path

    def get_room_files(self, room_id: str) -> List[FileMetadata]:
        """Get all files for a room."""
        conn = self._get_connection()
        results = conn.execute(
            """
            SELECT id, room_id, user_id, display_name, original_filename, stored_filename,
                   file_type, mime_type, size_bytes, uploaded_at
            FROM file_metadata
            WHERE room_id = ?
            ORDER BY uploaded_at ASC
            """,
            [room_id]
        ).fetchall()

        return [
            FileMetadata(
                id=r[0],
                room_id=r[1],
                user_id=r[2],
                display_name=r[3],
                original_filename=r[4],
                stored_filename=r[5],
                file_type=FileType(r[6]),
                mime_type=r[7],
                size_bytes=r[8],
                uploaded_at=r[9].timestamp() if r[9] else 0,
            )
            for r in results
        ]

    def delete_room_files(self, room_id: str) -> int:
        """Delete all files for a room.

        This is called when a session ends.

        TODO: CLOUD_BACKUP - Before deleting files, consider backing up to cloud storage
        (e.g., S3, GCS, Azure Blob) for compliance or recovery purposes.
        Implementation would involve:
        1. List all files for the room
        2. Upload each file to cloud storage with metadata
        3. Only delete local files after successful cloud upload
        4. Log the backup operation to audit trail

        Args:
            room_id: Room ID whose files should be deleted

        Returns:
            Number of files deleted
        """
        # Get file count before deletion
        files = self.get_room_files(room_id)
        file_count = len(files)

        if file_count == 0:
            return 0

        logger.info(f"Deleting {file_count} files for room {room_id}")

        # Delete files from disk
        room_dir = self._get_room_dir(room_id)
        if room_dir.exists():
            shutil.rmtree(room_dir)
            logger.info(f"Deleted directory: {room_dir}")

        # Delete metadata from database
        conn = self._get_connection()
        conn.execute(
            "DELETE FROM file_metadata WHERE room_id = ?",
            [room_id]
        )

        logger.info(f"Deleted {file_count} file records for room {room_id}")
        return file_count

