#!/usr/bin/env python2.5
"""xauth-less X11 forwarding over binary bidirectional tunnels"""
import os, select, socket, struct, sys, threading, time, optparse

VERBOSE=False
CHECK_UID=True

LOG_FILE = None

def Log(msg):
    if VERBOSE:
        if LOG_FILE is not None:
            LOG_FILE.write(msg)
            LOG_FILE.flush()
        sys.stderr.write(msg)

def getUnixPeerUid(socket):
    # freebsd-specific code
    LOCAL_PEERCRED = 1
    res = socket.getsockopt(0, LOCAL_PEERCRED, 1024)
    ver, uid = struct.unpack('2i', res[:8])
    if ver != 0:
        raise OSError
    return uid


def sread(fileobj, size):
    data = ''
    while len(data) < size:
        block = fileobj.read(size - len(data))
        if len(block) == 0:
            raise Exception('read() returned zero bytes')
        data += block
    return data

def muxer(path):
    server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        os.remove(path)
    except OSError:
        pass
    server.bind(path)
    os.chmod(path, 0600)
    server.listen(5)

    last_cid = 0
    fd_to_cid = {}
    clients = {}

    poll = select.poll()
    poll.register(sys.stdin, select.POLLIN | select.POLLERR)
    poll.register(server.fileno(), select.POLLIN | select.POLLERR)

    def hangup(cid, notify_server=True):
        client = clients[cid]
        fd = client.fileno()
        del clients[cid]
        del fd_to_cid[fd]
        poll.unregister(fd)
        client.close()

        if notify_server:
            sys.stdout.write(struct.pack('ii', cid, -2))
            sys.stdout.flush()

    while True:
        Log('muxer: poll()\n')
        for fd, event in poll.poll():
            Log('muxer: polled fd=%s ev=%s\n' % (fd, event))

            if fd == server.fileno():
                # incoming connection
                assert (event & select.POLLIN) == select.POLLIN

                sock, addr = server.accept()
                if CHECK_UID:
                    if getUnixPeerUid(sock) != os.getuid():
                        Log('muxer: refused connection from other UID')
                        sock.close()
                        continue

                poll.register(sock.fileno(), select.POLLIN | select.POLLHUP | select.POLLERR)

                last_cid += 1
                cid = last_cid
                fd_to_cid[sock.fileno()] = cid

                clients[cid] = sock
                sys.stdout.write(struct.pack('ii', cid, -1))
                sys.stdout.flush()

                Log('muxer: new client: %d\n' % cid)

            elif fd == sys.stdin.fileno():
                # server to client
                assert (event & select.POLLIN) == select.POLLIN

                data = sread(sys.stdin, 8)
                cid, dlen = struct.unpack('ii', data)
                data = sread(sys.stdin, dlen)

                if cid == 0:  # keep alive
                    Log('muxer: received keep-alive\n')
                    continue

                if cid not in clients:
                    Log('muxer: received data for unknown client %d\n' % cid)
                    continue

                client = clients[cid]

                if dlen == -2:  # hangup
                    Log('muxer: server hanged up on client %d\n' % cid)
                    hangup(cid, notify_server=False)
                    continue

                try:
                    client.send(data)
                except socket.error:
                    Log('muxer: socket error when trying to send data to client %d\n' % cid)
                    hangup(cid)
                    continue

                Log('muxer: sent %d bytes to client %d\n' % (len(data), cid))

            else:
                # client to server
                if not fd in fd_to_cid:
                    continue
                cid = fd_to_cid[fd]
                client = clients[cid]

                data = client.recv(4096)
                if len(data) == 0:
                    Log('muxer: client %d hanged up\n' % cid)
                    hangup(cid)
                    continue

                Log('muxer: received %d bytes from client %d\n' % (len(data), cid))
                sys.stdout.write(struct.pack('ii', cid, len(data)) + data)
                sys.stdout.flush()


def demuxer(path):
    fd_to_cid = {}
    connections = {}

    poll = select.poll()
    poll.register(sys.stdin, select.POLLIN | select.POLLERR)

    while True:
        Log('demuxer: poll()\n')

        poll_res = poll.poll(10000)
        if len(poll_res) == 0:
            Log('demuxer: sending keep-alive\n')
            sys.stdout.write(struct.pack('ii', 0, 0))
            sys.stdout.flush()
            continue

        for fd, event in poll_res:
            Log('demuxer: polled fd=%s ev=%s\n' % (fd, event))

            if fd == sys.stdin.fileno():
                # client to server
                assert (event & select.POLLIN) == select.POLLIN

                data = sread(sys.stdin, 8)
                cid, dlen = struct.unpack('ii', data)
                data = sread(sys.stdin, dlen)

                if dlen == -1:  # new client
                    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
                    sock.connect(path)

                    fd_to_cid[sock.fileno()] = cid
                    connections[cid] = sock
                    poll.register(sock.fileno(), select.POLLIN | select.POLLHUP | select.POLLERR)

                    Log('demuxer: new client: %d\n' % cid)

                elif dlen == -2:  # hangup
                    if cid not in connections:
                        continue
                    conn = connections[cid]
                    del connections[cid]
                    del fd_to_cid[conn.fileno()]
                    poll.unregister(conn.fileno())
                    conn.close()

                    Log('demuxer: server hanged up on client %d\n' % cid)

                else:  # data
                    if cid not in connections:
                        continue
                    conn = connections[cid]
                    conn.send(data)
                    Log('demuxer: client %d sent %d bytes to server\n' % (cid, len(data)))

            else:
                # server to client
                if not fd in fd_to_cid:
                    continue

                cid = fd_to_cid[fd]
                conn = connections[cid]

                data = ''
                if event & select.POLLIN:
                    data = conn.recv(4096)

                if ((event & select.POLLIN) != select.POLLIN) or len(data) == 0:
                    Log('demuxer: server hanged up on client %d\n' % cid)
                    conn.close()
                    del connections[cid]
                    del fd_to_cid[fd]
                    poll.unregister(fd)
                    sys.stdout.write(struct.pack('ii', cid, -2))
                    sys.stdout.flush()
                    continue

                Log('demuxer: received %d bytes for client %d\n' % (len(data), cid))
                sys.stdout.write(struct.pack('ii', cid, len(data)) + data)
                sys.stdout.flush()


def main():
    global VERBOSE, CHECK_UID, LOG_FILE

    sys.stdin = os.fdopen(sys.stdin.fileno(), 'r', 0)
    sys.stdout = os.fdopen(sys.stdout.fileno(), 'w', 0)

    parser = optparse.OptionParser(usage='%prog [options] <path to unix socket, e.g. /tmp/.X11-unix/X0>')
    parser.add_option('-m', '--mux', action='store_true', dest='mux', help='mux connections to socket to stdin/stdout')
    parser.add_option('-d', '--demux', action='store_false', dest='mux', help='demux stdin/stdout to connections to socket')
    parser.add_option('-v', '--verbose', action='store_true', dest='verbose')
    parser.add_option('-n', '--no-check', action='store_true', dest='no_check', help='allow socket to be used by other users (for muxer)')
    (options, args) = parser.parse_args()

    VERBOSE = options.verbose
    CHECK_UID = (options.no_check is None) or (not options.no_check)

    if VERBOSE:
        LOG_FILE = file('/tmp/x11tunnel.log', 'w')

    if options.mux is None or len(args) != 1:
        parser.print_help()
        return

    try:
        if options.mux:
            muxer(args[0])
        else:
            demuxer(args[0])
    except:
        sys.excepthook(*sys.exc_info())
        try:
            if options.mux:
                os.unlink(args[0])
        except:
            pass
        sys.exit(1)


if __name__ == '__main__':
    main()
