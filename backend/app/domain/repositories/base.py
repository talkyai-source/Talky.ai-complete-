"""
Base Repository
Abstract base class for all repositories — defines the standard CRUD interface.

Pattern:
- Repositories encapsulate all data access logic
- Services depend on repository abstractions, not PostgreSQL directly
- Enables testing with mock repositories
"""
import logging
from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional, TypeVar, Generic

logger = logging.getLogger(__name__)

T = TypeVar("T")


class BaseRepository(ABC, Generic[T]):
    """
    Abstract repository with standard CRUD operations.
    
    Concrete implementations (e.g., PostgreSQLCallRepository) handle
    the actual database interaction.
    """
    
    @abstractmethod
    async def get_by_id(self, entity_id: str) -> Optional[T]:
        """Get a single entity by ID."""
        ...
    
    @abstractmethod
    async def list(
        self,
        filters: Optional[Dict[str, Any]] = None,
        limit: int = 100,
        offset: int = 0
    ) -> List[T]:
        """List entities with optional filtering."""
        ...
    
    @abstractmethod
    async def create(self, data: Dict[str, Any]) -> T:
        """Create a new entity."""
        ...
    
    @abstractmethod
    async def update(self, entity_id: str, data: Dict[str, Any]) -> Optional[T]:
        """Update an entity by ID."""
        ...
    
    @abstractmethod
    async def delete(self, entity_id: str) -> bool:
        """Delete an entity by ID. Returns True if deleted."""
        ...
