#!/usr/bin/env python3
"""../wifi-design/relay.py (unchanged, imported) + a decoded per-frame log.

  python3 relay_logged.py --listen 127.0.0.1:8008 --log relay.log [relay.py options]

relay.py itself logs nothing per frame; this wraps its Gateway so every guest->relay (TX) and
relay->guest (RX) Ethernet frame is written as one line (same format as patch/coldboot.mjs relay.log),
including DHCP message types (1 DISCOVER, 2 OFFER, 3 REQUEST, 5 ACK).
"""
import importlib.util, os, socket, struct, sys, time

HERE = os.path.dirname(os.path.abspath(__file__))
RELAY = os.path.join(os.path.dirname(HERE), "wifi-design", "relay.py")
spec = importlib.util.spec_from_file_location("relay", RELAY)
relay = importlib.util.module_from_spec(spec)
spec.loader.exec_module(relay)

LOG = None
DHCP = {1: "DISCOVER", 2: "OFFER", 3: "REQUEST", 4: "DECLINE", 5: "ACK", 6: "NAK", 7: "RELEASE", 8: "INFORM"}


def decode(f):
    ip = lambda b: socket.inet_ntoa(bytes(b))
    if len(f) < 14:
        return "len=%d (short)" % len(f)
    et = struct.unpack("!H", f[12:14])[0]
    d = "len=%d %s->%s type=0x%x" % (len(f), f[6:12].hex(), f[0:6].hex(), et)
    try:
        if et == 0x800:
            pr = f[23]
            d += " %s->%s proto=%d" % (ip(f[26:30]), ip(f[30:34]), pr)
            if pr in (6, 17):
                sp, dp = struct.unpack("!HH", f[34:38])
                d += " %d->%d" % (sp, dp)
                if pr == 17 and dp in (67, 68):
                    k = f.find(b"\x63\x82\x53\x63", 42)
                    m = f.find(b"\x35\x01", k + 4) if k > 0 else -1
                    t = f[m + 2] if m > 0 else 0
                    d += " DHCP type=%d %s yiaddr=%s" % (t, DHCP.get(t, "?"), ip(f[58:62]))
                if pr == 6:
                    d += " flags=0x%x" % f[47]
            if pr == 1:
                d += " icmp type=%d" % f[34]
        elif et == 0x806:
            d += " ARP op=%d %s asks %s" % (struct.unpack("!H", f[20:22])[0], ip(f[28:32]), ip(f[38:42]))
    except (IndexError, struct.error, OSError):
        d += " (truncated)"
    return d


def log(line):
    LOG.write("%.3f %s\n" % (time.time(), line))
    LOG.flush()


class LoggedGateway(relay.Gateway):
    def __init__(self, send, *a, **k):
        log("guest connected")

        def logged_send(frame):
            log("RX " + decode(frame))
            send(frame)
        super().__init__(logged_send, *a, **k)

    def rx_from_guest(self, frame):
        log("TX " + decode(frame))
        return super().rx_from_guest(frame)


relay.Gateway = LoggedGateway   # relay.serve() looks the name up at call time
_handle_ws = relay.handle_ws


async def logged_handle_ws(reader, writer, make_gateway):
    peer = writer.get_extra_info("peername")
    try:
        return await _handle_ws(reader, writer, make_gateway)
    finally:
        log("connection closed %s" % (peer,))


relay.handle_ws = logged_handle_ws

if __name__ == "__main__":
    argv = sys.argv[1:]
    path = os.path.join(HERE, "relay.log")
    if "--log" in argv:
        i = argv.index("--log")
        path = argv[i + 1]
        del argv[i:i + 2]
    LOG = open(path, "a", buffering=1)
    log("relay start " + " ".join(argv))
    sys.argv = [RELAY] + argv
    relay.main()
