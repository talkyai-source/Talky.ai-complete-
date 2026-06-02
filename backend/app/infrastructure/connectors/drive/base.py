"""
Drive Provider Base Class
Abstract interface for cloud storage integrations.
"""
from abc import abstractmethod
from typing import List, Dict, Any, Optional, BinaryIO
from datetime import datetime
from pydantic import BaseModel

from app.infrastructure.connectors.base import BaseConnector, ConnectorCapability


class DriveFile(BaseModel):
    """Represents a file in cloud storage."""
    id: Optional[str] = None
    name: str = ""
    mime_type: Optional[str] = None
    size: Optional[int] = None
    parent_id: Optional[str] = None
    web_link: Optional[str] = None
    download_link: Optional[str] = None
    created_at: Optional[datetime] = None
    modified_at: Optional[datetime] = None
    is_folder: bool = False
    
    class Config:
        extra = "allow"


class DriveProvider(BaseConnector):
    """
    Abstract base class for cloud storage providers.
    
    Extends BaseConnector with drive-specific methods.
    """
    
    @property
    def connector_type(self) -> str:
        return "drive"
    
    @property
    def capabilities(self) -> List[ConnectorCapability]:
        return [
            ConnectorCapability.UPLOAD_FILE,
            ConnectorCapability.DOWNLOAD_FILE,
            ConnectorCapability.LIST_FILES,
            ConnectorCapability.CREATE_FOLDER
        ]
    
    @abstractmethod
    async def upload_file(
        self,
        name: str,
        content: bytes,
        mime_type: str = "application/octet-stream",
        parent_folder_id: Optional[str] = None
    ) -> DriveFile:
        """
        Upload a file to cloud storage.
        
        Args:
            name: File name
            content: File content as bytes
            mime_type: MIME type of the file
            parent_folder_id: Optional folder to upload to
            
        Returns:
            Created DriveFile with provider's file ID
        """
        pass
    
    @abstractmethod
    async def download_file(self, file_id: str) -> bytes:
        """Download a file's content."""
        pass
    
    @abstractmethod
    async def list_files(
        self,
        folder_id: Optional[str] = None,
        query: Optional[str] = None,
        max_results: int = 100
    ) -> List[DriveFile]:
        """List files with optional filtering."""
        pass
    
    @abstractmethod
    async def create_folder(
        self,
        name: str,
        parent_folder_id: Optional[str] = None
    ) -> DriveFile:
        """Create a folder."""
        pass
    
    @abstractmethod
    async def delete_file(self, file_id: str) -> bool:
        """Delete a file or folder."""
        pass
    
    @abstractmethod
    async def get_file(self, file_id: str) -> DriveFile:
        """Get file metadata."""
        pass
