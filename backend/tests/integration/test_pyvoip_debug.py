"""
Detailed pyVoIP test with verbose logging
"""
import time
import logging
from pyVoIP.VoIP import VoIPPhone, CallState, PhoneStatus

logging.basicConfig(level=logging.DEBUG, format='%(asctime)s - %(name)s - %(message)s')
logger = logging.getLogger()

phone = VoIPPhone(
    server='192.168.1.6',
    port=5060,
    username='1002',
    password='1002',
    myIP='192.168.3.51',
    sipPort=5062,
    rtpPortLow=10000,
    rtpPortHigh=10100
)

print("Starting phone...")
phone.start()
time.sleep(3)

status = phone.get_status()
print(f'Status: {status}')

if status == PhoneStatus.REGISTERED:
    print("Making call to 1001...")
    call = phone.call('1001')
    print(f'Call object: {call}')
    
    for i in range(60):
        state = call.state if call else "No call"
        print(f'{i}s: Call state = {state}')
        
        if call and call.state == CallState.ANSWERED:
            print('CALL ANSWERED! Keeping alive...')
            # Stay in call
            continue
            
        if call and call.state == CallState.ENDED:
            print('CALL ENDED')
            break
            
        time.sleep(1)
else:
    print(f"Registration failed: {status}")

print("Stopping phone...")
phone.stop()
print("Done")
