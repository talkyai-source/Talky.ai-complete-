"""
CRM Provider Package
"""
from app.infrastructure.connectors.crm.base import CRMProvider
from app.infrastructure.connectors.crm.hubspot import HubSpotConnector

__all__ = ["CRMProvider", "HubSpotConnector"]
