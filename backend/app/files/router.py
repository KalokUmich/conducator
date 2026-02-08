"""FastAPI router for file upload endpoints."""
import logging
from typing import Optional

from fastapi import APIRouter, File, Form, UploadFile, HTTPException, Request
from fastapi.responses import FileResponse

from .service import FileStorageService
from .schemas import FileUploadResponse, MAX_FILE_SIZE_BYTES

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/files", tags=["files"])


def get_download_url(request: Request, file_id: str) -> str:
    """Generate download URL for a file."""
    # Use the request's base URL to construct the download URL
    base_url = str(request.base_url).rstrip("/")
    return f"{base_url}/files/download/{file_id}"


@router.post("/upload/{room_id}", response_model=FileUploadResponse)
async def upload_file(
    request: Request,
    room_id: str,
    file: UploadFile = File(...),
    user_id: str = Form(...),
    display_name: str = Form(...),
    caption: Optional[str] = Form(None),
):
    """Upload a file to a chat room.
    
    Supported file types:
    - Images: jpg, jpeg, png, gif, webp, svg
    - Documents: pdf
    - Audio: mp3, wav, ogg, m4a, flac
    - Any other file type under 20MB
    
    Args:
        room_id: Room ID to upload to
        file: The file to upload
        user_id: User ID of the uploader
        display_name: Display name of the uploader
        caption: Optional caption for the file
        
    Returns:
        FileUploadResponse with file metadata and download URL
        
    Raises:
        HTTPException 413: If file exceeds 20MB limit
        HTTPException 500: If upload fails
    """
    try:
        # Read file content
        content = await file.read()
        
        # Check size before processing
        if len(content) > MAX_FILE_SIZE_BYTES:
            raise HTTPException(
                status_code=413,
                detail=f"File size exceeds limit of 20MB"
            )
        
        # Get MIME type
        mime_type = file.content_type or "application/octet-stream"
        
        # Save file
        service = FileStorageService.get_instance()
        metadata = await service.save_file(
            room_id=room_id,
            user_id=user_id,
            display_name=display_name,
            filename=file.filename or "unnamed",
            content=content,
            mime_type=mime_type,
        )
        
        # Generate download URL
        download_url = get_download_url(request, metadata.id)
        
        logger.info(
            f"File uploaded: {metadata.original_filename} "
            f"({metadata.size_bytes} bytes) to room {room_id}"
        )
        
        return FileUploadResponse(
            id=metadata.id,
            original_filename=metadata.original_filename,
            file_type=metadata.file_type,
            mime_type=metadata.mime_type,
            size_bytes=metadata.size_bytes,
            download_url=download_url,
        )
        
    except ValueError as e:
        raise HTTPException(status_code=413, detail=str(e))
    except Exception as e:
        logger.error(f"File upload failed: {e}")
        raise HTTPException(status_code=500, detail=f"Upload failed: {str(e)}")


@router.get("/download/{file_id}")
async def download_file(file_id: str):
    """Download a file by ID.
    
    Args:
        file_id: The file ID to download
        
    Returns:
        The file content with appropriate headers
        
    Raises:
        HTTPException 404: If file not found
    """
    service = FileStorageService.get_instance()
    
    # Get metadata
    metadata = service.get_file(file_id)
    if not metadata:
        raise HTTPException(status_code=404, detail="File not found")
    
    # Get file path
    file_path = service.get_file_path(file_id)
    if not file_path:
        raise HTTPException(status_code=404, detail="File not found on disk")
    
    return FileResponse(
        path=file_path,
        filename=metadata.original_filename,
        media_type=metadata.mime_type,
    )


@router.delete("/room/{room_id}")
async def delete_room_files(room_id: str):
    """Delete all files for a room.
    
    This endpoint is called when a session ends.
    
    Args:
        room_id: Room ID whose files should be deleted
        
    Returns:
        Number of files deleted
    """
    service = FileStorageService.get_instance()
    count = service.delete_room_files(room_id)
    
    logger.info(f"Deleted {count} files for room {room_id}")
    
    return {"deleted_count": count, "room_id": room_id}

