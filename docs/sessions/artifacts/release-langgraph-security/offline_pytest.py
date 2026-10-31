import ipaddress, socket, sys
from pathlib import Path
from unittest.mock import patch
import httpx
import pytest
sys.path.insert(0,str(Path.cwd()))
blocked=[]
original_connect=socket.socket.connect
original_connect_ex=socket.socket.connect_ex
original_dns=socket.getaddrinfo

def check(address):
    host, port = address[0], int(address[1])
    try: loopback=ipaddress.ip_address(host).is_loopback
    except ValueError: loopback=host == 'localhost'
    if not loopback or port in {1,5432,55434,6379}:
        blocked.append('network')
        raise AssertionError('Release unit checks prohibit provider/database network')

def connect(sock,address):
    check(address)
    return original_connect(sock,address)

def connect_ex(sock,address):
    check(address)
    return original_connect_ex(sock,address)

def dns(host,port,*args,**kwargs):
    check((host,port))
    return original_dns(host,port,*args,**kwargs)

async def http_block(*args,**kwargs):
    blocked.append('http')
    raise AssertionError('Release unit checks prohibit real HTTP transport')

with patch.object(socket.socket,'connect',connect), patch.object(socket.socket,'connect_ex',connect_ex), patch.object(socket,'getaddrinfo',dns), patch.object(httpx.AsyncHTTPTransport,'handle_async_request',http_block):
    status=pytest.main(sys.argv[1:])
print('blocked_network_attempts='+str(len(blocked)))
raise SystemExit(status or (1 if blocked else 0))
