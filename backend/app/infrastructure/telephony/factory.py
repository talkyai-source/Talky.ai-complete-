"""
Media Gateway Factory

Provides MediaGateway instances for both browser-based and telephony-based
voice interactions.

Supported types:
  "browser"    — BrowserMediaGateway (WebSocket audio, used by browser clients
                 and FreeSWITCH mod_audio_fork / Vonage WebSocket paths)
  "telephony"  — TelephonyMediaGateway (HTTP callback audio, used by the
                 Asterisk + C++ Voice Gateway path)
"""
from app.domain.interfaces.media_gateway import MediaGateway


class MediaGatewayFactory:
    """
    Factory for creating Media Gateway instances.

    Supports browser (WebSocket) and telephony (HTTP callback) gateways.
    """

    @classmethod
    def create(cls, gateway_type: str, config: dict = None) -> MediaGateway:
        """
        Create media gateway instance.

        Args:
            gateway_type: Gateway type ("browser")
            config: Optional configuration dictionary

        Returns:
            Configured MediaGateway instance
        """
        config = config or {}

        if gateway_type == "browser":
            from app.infrastructure.telephony.browser_media_gateway import BrowserMediaGateway
            gateway = BrowserMediaGateway()
        elif gateway_type == "telephony":
            from app.infrastructure.telephony.telephony_media_gateway import TelephonyMediaGateway
            gateway = TelephonyMediaGateway()
        else:
            raise ValueError(
                f"Unknown media gateway type: {gateway_type}. "
                f"Available: browser, telephony"
            )

        return gateway

    @classmethod
    def list_gateways(cls) -> list[str]:
        """List available media gateway types"""
        return ["browser", "telephony"]
