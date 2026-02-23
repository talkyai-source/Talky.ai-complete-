"""
Test SIP/RTP using pyVoIP - a proven Python VoIP library.
This handles NAT traversal, RTP timing, and codec encoding properly.
"""
import time
import logging
from pyVoIP.VoIP import VoIPPhone, InvalidStateError, CallState

logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s - %(levelname)s - %(name)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Configuration
PBX_HOST = "192.168.1.6"
PBX_PORT = 5060
USERNAME = "1002"
PASSWORD = "1002"
CALL_TO = "1001"


def call_handler(call):
    """Handle incoming calls."""
    try:
        if call.state == CallState.RINGING:
            logger.info(f"📞 Incoming call from {call.request.headers.get('From')}")
            call.answer()
            logger.info("✅ Call answered")
            
            # Keep the call alive with silence
            start_time = time.time()
            while call.state == CallState.ANSWERED:
                # Send silence (pyVoIP handles the RTP internally)
                time.sleep(0.1)
                
                elapsed = time.time() - start_time
                if int(elapsed) % 5 == 0 and int(elapsed) > 0:
                    logger.info(f"📞 Call active for {elapsed:.0f}s")
                    
    except InvalidStateError:
        logger.warning("Call state changed")
    except Exception as e:
        logger.error(f"Call handler error: {e}", exc_info=True)


def main():
    logger.info("=" * 50)
    logger.info("pyVoIP Test - Proven SIP/RTP Library")
    logger.info("=" * 50)
    
    from pyVoIP.VoIP import PhoneStatus
    
    try:
        # Create VoIP phone
        phone = VoIPPhone(
            server=PBX_HOST,
            port=PBX_PORT,
            username=USERNAME,
            password=PASSWORD,
            myIP="192.168.3.51",  # Our local IP
            sipPort=5062,
            rtpPortLow=10000,
            rtpPortHigh=10100
        )
        
        # Set call handler
        phone.callback = call_handler
        
        # Start phone (registers with PBX)
        logger.info("🚀 Starting VoIP phone...")
        phone.start()
        
        # Wait for registration
        for i in range(10):
            status = phone.get_status()
            logger.info(f"Phone status: {status}")
            if status == PhoneStatus.REGISTERED:
                break
            time.sleep(1)
        
        status = phone.get_status()
        if status == PhoneStatus.REGISTERED:
            logger.info("✅ Registered with PBX!")
            
            # Make outgoing call
            logger.info(f"📞 Calling {CALL_TO}...")
            call = phone.call(CALL_TO)
            
            if call is None:
                logger.error("❌ Failed to initiate call")
                phone.stop()
                return
            
            # Wait for call to be answered
            timeout = 30
            start = time.time()
            while time.time() - start < timeout:
                if call.state == CallState.ANSWERED:
                    logger.info("✅ Call connected!")
                    break
                elif call.state == CallState.ENDED:
                    logger.error("❌ Call ended before answer")
                    break
                time.sleep(0.5)
            
            # If call is active, keep it alive
            if call.state == CallState.ANSWERED:
                logger.info("🔊 Call active - keeping alive...")
                call_start = time.time()
                
                while call.state == CallState.ANSWERED:
                    elapsed = time.time() - call_start
                    
                    if int(elapsed) % 5 == 0 and int(elapsed) > 0:
                        logger.info(f"📞 Call duration: {elapsed:.0f}s")
                    
                    time.sleep(0.5)
                
                logger.info(f"📞 Call ended after {time.time() - call_start:.1f}s")
            
        else:
            logger.error(f"❌ Failed to register with PBX (status: {status})")
        
        # Cleanup
        phone.stop()
        
    except Exception as e:
        logger.error(f"Error: {e}", exc_info=True)


if __name__ == "__main__":
    main()
