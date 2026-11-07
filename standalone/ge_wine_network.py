# SPDX-License-Identifier: GPL-3.0-or-later
"""Read-only, process-scoped socket sampling. No packet interception or payload capture."""
import os
from pathlib import Path
import socket
import sys

from ge_wine_sessions import identity

STATES = dict(zip(('01','02','03','04','05','06','07','08','09','0A','0B','0C'),
                  ('ESTABLISHED','SYN_SENT','SYN_RECV','FIN_WAIT1','FIN_WAIT2','TIME_WAIT',
                   'CLOSE','CLOSE_WAIT','LAST_ACK','LISTEN','CLOSING','NEW_SYN_RECV')))


def endpoint(value, ipv6):
    address, port = value.split(':')
    raw = bytes.fromhex(address)
    # /proc prints each native-endian 32-bit word, including IPv6 addresses.
    raw = b''.join(raw[offset:offset+4][::-1] for offset in range(0,len(raw),4)) if sys.byteorder=='little' else raw
    return dict(ip=socket.inet_ntop(socket.AF_INET6 if ipv6 else socket.AF_INET,raw),port=int(port,16))


def connections(processes):
    """Return owned TCP/UDP sockets plus explicit coverage errors, never host-wide traffic."""
    rows = {}; errors = set(); tables = {}
    for (pid, stamp), process in processes.items():
        if identity(pid) != stamp:
            continue
        try:
            if sys.platform=='darwin':
                import psutil
                for connection in psutil.Process(pid).net_connections(kind='inet'):
                    def address(value):
                        return dict(ip=value.ip,port=value.port) if value else None
                    row = dict(pid=pid,start_ticks=stamp,name=process.get('name'),
                               protocol='tcp' if connection.type==socket.SOCK_STREAM else 'udp',
                               local=address(connection.laddr),remote=address(connection.raddr),state=connection.status)
                    rows[(pid,stamp,connection.fd,str(row['local']),str(row['remote']))]=row
            else:
                root=Path('/proc')/str(pid)
                inodes=set()
                for fd in (root/'fd').iterdir():
                    try:
                        target=os.readlink(fd)
                        if target.startswith('socket:['):inodes.add(target[8:-1])
                    except FileNotFoundError:
                        pass
                if not inodes:continue
                namespace=os.readlink(root/'ns/net')
                if namespace not in tables:
                    table=[]
                    for kind in ('tcp','tcp6','udp','udp6'):
                        try:
                            lines=(root/'net'/kind).read_text().splitlines()[1:]
                        except FileNotFoundError:
                            continue
                        for line in lines:
                            fields=line.split()
                            if len(fields)<10:continue
                            table.append((kind,fields[9],endpoint(fields[1],kind.endswith('6')),
                                          endpoint(fields[2],kind.endswith('6')),STATES.get(fields[3],fields[3])))
                    tables[namespace]=table
                for kind,inode,local,remote,state in tables[namespace]:
                    if inode in inodes:
                        rows[(pid,stamp,kind,inode)]=dict(pid=pid,start_ticks=stamp,name=process.get('name'),
                            protocol=kind,local=local,remote=remote,state=state)
            if identity(pid)!=stamp:
                rows={key:row for key,row in rows.items() if row['pid']!=pid}
        except (OSError,ValueError) as error:
            # A process exiting between reads is normal; permission failures are coverage gaps.
            if identity(pid)==stamp:errors.add(f'{type(error).__name__}: {error}')
        except Exception as error:
            if sys.platform!='darwin':raise
            import psutil
            if not isinstance(error,psutil.Error):raise
            if isinstance(error,psutil.AccessDenied):errors.add('AccessDenied: macOS denied socket inspection')
    return rows,sorted(errors)
