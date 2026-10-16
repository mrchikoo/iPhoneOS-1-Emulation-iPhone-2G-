#!/usr/bin/env python3
"""Prototype host-side relay for the emulated iPhone 2G WiFi (stdlib only).

Guest ethernet frames arrive as binary WebSocket messages (one frame per
message, no FCS).  The relay acts as a slirp-style user-mode router:

  * ARP responder for the gateway (10.0.2.2) and DNS (10.0.2.3) addresses
  * DHCP server (DISCOVER/OFFER/REQUEST/ACK/NAK, INFORM ignored)
  * ICMP echo reply for gateway/DNS addresses only
  * DNS forwarder (UDP/53) with optional static `hosts` overrides
  * UDP NAT (one host socket per guest src port + destination)
  * TCP NAT: the guest's TCP is terminated here and re-originated from a
    normal host socket, so no raw sockets / root are needed.

Traffic to the gateway address 10.0.2.2 is redirected to 127.0.0.1 on the
host (QEMU slirp convention), so the guest can reach a local web server.

Limitations (see DESIGN.md): IPv4 only, no IP fragment reassembly, no
guest-bound TCP retransmission (the WS link is reliable), no TCP persist
timer, ICMP to the internet unsupported.
"""
import argparse
import asyncio
import base64
import hashlib
import json
import os
import socket
import struct

GW_IP = "10.0.2.2"
DNS_IP = "10.0.2.3"
MASK = "255.255.255.0"
POOL_START = 15
GW_MAC = bytes.fromhex("525400123502")
BCAST = b"\xff" * 6
M32 = 0xFFFFFFFF
MSS = 1400

FIN, SYN, RST, PSH, ACK = 1, 2, 4, 8, 16


# ---------------------------------------------------------------- helpers
def ip2b(s):
    return socket.inet_aton(s)


def b2ip(b):
    return socket.inet_ntoa(b)


def csum(data):
    if len(data) & 1:
        data += b"\0"
    s = sum(struct.unpack("!%dH" % (len(data) // 2), data))
    while s >> 16:
        s = (s & 0xFFFF) + (s >> 16)
    return (~s) & 0xFFFF


def eth(dst, src, etype, payload):
    return dst + src + struct.pack("!H", etype) + payload


_ipid = [0]


def ip_packet(src, dst, proto, payload):
    _ipid[0] = (_ipid[0] + 1) & 0xFFFF
    hdr = struct.pack("!BBHHHBBH4s4s", 0x45, 0, 20 + len(payload), _ipid[0],
                      0x4000, 64, proto, 0, ip2b(src), ip2b(dst))
    hdr = hdr[:10] + struct.pack("!H", csum(hdr)) + hdr[12:]
    return hdr + payload


def pseudo(src, dst, proto, length):
    return ip2b(src) + ip2b(dst) + struct.pack("!BBH", 0, proto, length)


def udp_segment(src, dst, sport, dport, data):
    ln = 8 + len(data)
    raw = struct.pack("!HHHH", sport, dport, ln, 0) + data
    c = csum(pseudo(src, dst, 17, ln) + raw) or 0xFFFF
    return raw[:6] + struct.pack("!H", c) + raw[8:]


def tcp_segment(src, dst, sport, dport, seq, ack, flags, win, data=b"",
                mss_opt=False):
    opts = struct.pack("!BBH", 2, 4, MSS) if mss_opt else b""
    off = (20 + len(opts)) // 4
    raw = struct.pack("!HHIIHHHH", sport, dport, seq & M32, ack & M32,
                      (off << 12) | flags, win, 0, 0) + opts + data
    c = csum(pseudo(src, dst, 6, len(raw)) + raw)
    return raw[:16] + struct.pack("!H", c) + raw[18:]


def parse_ip(pkt):
    if len(pkt) < 20 or pkt[0] >> 4 != 4:
        return None
    ihl = (pkt[0] & 15) * 4
    total = struct.unpack("!H", pkt[2:4])[0]
    if ihl < 20 or total < ihl or total > len(pkt):
        return None
    fragoff = struct.unpack("!H", pkt[6:8])[0] & 0x3FFF  # MF | offset
    if fragoff:
        return None  # fragments unsupported
    return dict(proto=pkt[9], src=b2ip(pkt[12:16]), dst=b2ip(pkt[16:20]),
                payload=pkt[ihl:total])


# ------------------------------------------------------------------- DNS
def dns_parse_question(msg):
    if len(msg) < 12:
        return None
    qd = struct.unpack("!H", msg[4:6])[0]
    if qd != 1:
        return None
    i, labels = 12, []
    while True:
        if i >= len(msg):
            return None
        n = msg[i]
        if n == 0:
            i += 1
            break
        if n & 0xC0:
            return None
        labels.append(msg[i + 1:i + 1 + n].decode("ascii", "replace"))
        i += n + 1
    if i + 4 > len(msg):
        return None
    qtype, qclass = struct.unpack("!HH", msg[i:i + 4])
    return ".".join(labels).lower(), qtype, qclass, msg[12:i + 4]


def dns_answer(msg, question_raw, qtype, addr):
    txid = msg[:2]
    flags = 0x8180  # QR RD RA
    if addr is None:
        return txid + struct.pack("!HHHHH", flags, 1, 0, 0, 0) + question_raw
    rr = b"\xc0\x0c" + struct.pack("!HHIH", 1, 1, 60, 4) + ip2b(addr)
    return txid + struct.pack("!HHHHH", flags, 1, 1, 0, 0) + question_raw + rr


# ------------------------------------------------------------------ TCP
class TcpConn:
    def __init__(self, gw, key, gmac, gip, sport, dst_ip, dport, isn, win):
        self.gw, self.key, self.gmac, self.gip = gw, key, gmac, gip
        self.sport, self.dst_ip, self.dport = sport, dst_ip, dport
        self.rcv_next = (isn + 1) & M32      # next seq expected from guest
        self.isn = int.from_bytes(os.urandom(4), "big")
        self.sent = 1                        # SYN consumes one (relative)
        self.acked = 0
        self.gwin = max(win, 1)
        self.reader = self.writer = None
        self.state = "connecting"
        self.fin_sent = self.fin_rcvd = False
        self.ack_ev = asyncio.Event()
        self.tasks = []
        self.tasks.append(asyncio.ensure_future(self._connect()))

    # -- output
    def _emit(self, seq_rel, flags, data=b"", mss=False, ack=True):
        seg = tcp_segment(self.dst_ip, self.gip, self.dport, self.sport,
                          self.isn + seq_rel, self.rcv_next,
                          flags | (ACK if ack else 0), 65535, data, mss)
        self.gw.send_ip(self.gmac, self.dst_ip, self.gip, 6, seg)

    async def _connect(self):
        host = "127.0.0.1" if self.dst_ip == GW_IP else self.dst_ip
        try:
            self.reader, self.writer = await asyncio.wait_for(
                asyncio.open_connection(host, self.dport), 10)
        except Exception:
            self.state = "closed"
            self._emit(0, RST, ack=True)
            self.gw.tcp.pop(self.key, None)
            return
        self.state = "established"
        self._emit(0, SYN, mss=True)
        self.tasks.append(asyncio.ensure_future(self._pump()))

    async def _pump(self):
        try:
            while True:
                data = await self.reader.read(MSS)
                if not data:
                    break
                while self.sent - self.acked >= self.gwin:
                    self.ack_ev.clear()
                    await self.ack_ev.wait()
                    if self.state == "closed":
                        return
                self._emit(self.sent, PSH, data)
                self.sent += len(data)
        except (ConnectionError, OSError):
            self._emit(self.sent, RST)
            self.close()
            return
        self._emit(self.sent, FIN)
        self.sent += 1
        self.fin_sent = True
        self._maybe_done()

    # -- input
    def on_segment(self, seq, ack, flags, win, data):
        if flags & RST:
            self.close()
            return
        if self.state != "established":
            return
        if flags & ACK:
            rel = (ack - self.isn) & M32
            if self.acked < rel <= self.sent:
                self.acked = rel
            self.gwin = max(win, 1)
            self.ack_ev.set()
        need_ack = False
        if data or flags & FIN:
            need_ack = True
            delta = (seq - self.rcv_next) & M32
            if delta >= 0x80000000:      # starts before rcv_next: trim
                trim = (self.rcv_next - seq) & M32
                if trim < len(data) or (trim == len(data) and flags & FIN):
                    data = data[trim:]
                    seq = self.rcv_next
                    delta = 0
            if delta == 0:
                if data:
                    self.writer.write(data)
                    self.rcv_next = (self.rcv_next + len(data)) & M32
                if flags & FIN and not self.fin_rcvd:
                    self.fin_rcvd = True
                    self.rcv_next = (self.rcv_next + 1) & M32
                    try:
                        self.writer.write_eof()
                    except Exception:
                        pass
        if need_ack:
            self._emit(self.sent, 0)
        self._maybe_done()

    def _maybe_done(self):
        if self.fin_sent and self.fin_rcvd and self.acked >= self.sent:
            self.close()

    def close(self):
        if self.state == "closed":
            return
        self.state = "closed"
        self.ack_ev.set()
        self.gw.tcp.pop(self.key, None)
        for t in self.tasks:
            if t is not asyncio.current_task():
                t.cancel()
        if self.writer:
            self.writer.close()


# --------------------------------------------------------------- Gateway
class UdpFlow(asyncio.DatagramProtocol):
    def __init__(self, gw, key, gmac, gip):
        self.gw, self.key, self.gmac, self.gip = gw, key, gmac, gip
        self.transport = None
        self.timer = None

    def connection_made(self, t):
        self.transport = t

    def datagram_received(self, data, addr):
        sport, dst_ip, dport = self.key
        seg = udp_segment(dst_ip, self.gip, dport, sport, data)
        self.gw.send_ip(self.gmac, dst_ip, self.gip, 17, seg)
        self.touch()

    def touch(self):
        if self.timer:
            self.timer.cancel()
        self.timer = asyncio.get_event_loop().call_later(60, self.expire)

    def expire(self):
        self.gw.udp.pop(self.key, None)
        if self.transport:
            self.transport.close()


class Gateway:
    """Pure frame-in / frame-out router; `send(frame_bytes)` goes to guest."""

    def __init__(self, send, dns_upstream=None, hosts=None):
        self.send = send
        self.dns_upstream = dns_upstream or self._system_dns()
        self.hosts = {k.lower(): v for k, v in (hosts or {}).items()}
        self.leases = {}
        self.tcp = {}
        self.udp = {}
        self.guest_mac = None

    @staticmethod
    def _system_dns():
        try:
            with open("/etc/resolv.conf") as f:
                for line in f:
                    p = line.split()
                    if len(p) >= 2 and p[0] == "nameserver" and "." in p[1]:
                        return (p[1], 53)
        except OSError:
            pass
        return ("8.8.8.8", 53)

    def lease_for(self, mac):
        if mac not in self.leases:
            self.leases[mac] = "10.0.2.%d" % (POOL_START + len(self.leases))
        return self.leases[mac]

    # -- ingress
    def rx_from_guest(self, frame):
        if len(frame) < 14:
            return
        dst, src = frame[:6], frame[6:12]
        etype = struct.unpack("!H", frame[12:14])[0]
        if dst != GW_MAC and dst != BCAST and not (dst[0] & 1):
            return
        self.guest_mac = src
        body = frame[14:]
        if etype == 0x0806:
            self._arp(src, body)
        elif etype == 0x0800:
            ip = parse_ip(body)
            if ip:
                self._ip(src, ip)

    def send_ip(self, gmac, src, dst, proto, payload):
        self.send(eth(gmac, GW_MAC, 0x0800, ip_packet(src, dst, proto, payload)))

    # -- ARP
    def _arp(self, src, b):
        if len(b) < 28:
            return
        op = struct.unpack("!H", b[6:8])[0]
        sha, spa, tpa = b[8:14], b[14:18], b[24:28]
        if op == 1 and b2ip(tpa) in (GW_IP, DNS_IP):
            rep = struct.pack("!HHBBH", 1, 0x0800, 6, 4, 2) + GW_MAC + tpa + sha + spa
            self.send(eth(sha, GW_MAC, 0x0806, rep))

    # -- IP
    def _ip(self, gmac, ip):
        p, pl = ip["proto"], ip["payload"]
        if p == 17 and len(pl) >= 8:
            sport, dport, ln = struct.unpack("!HHH", pl[:6])
            data = pl[8:ln]
            if dport == 67:
                self._dhcp(gmac, data)
            elif dport == 53 and ip["dst"] in (GW_IP, DNS_IP):
                self._dns(gmac, ip, sport, data)
            elif ip["src"] != "0.0.0.0":
                self._udp(gmac, ip, sport, dport, data)
        elif p == 6 and len(pl) >= 20:
            self._tcp(gmac, ip, pl)
        elif p == 1 and len(pl) >= 8 and pl[0] == 8 and ip["dst"] in (GW_IP, DNS_IP):
            rep = b"\x00\x00\x00\x00" + pl[4:]
            rep = rep[:2] + struct.pack("!H", csum(rep)) + rep[4:]
            self.send_ip(gmac, ip["dst"], ip["src"], 1, rep)

    # -- DHCP
    def _dhcp(self, gmac, d):
        if len(d) < 241 or d[0] != 1 or d[236:240] != b"\x63\x82\x53\x63":
            return
        xid, flags = d[4:8], d[10:12]
        chaddr = d[28:34]
        opts, i = {}, 240
        while i < len(d) and d[i] != 255:
            if d[i] == 0:
                i += 1
                continue
            if i + 1 >= len(d):
                break
            opts[d[i]] = d[i + 2:i + 2 + d[i + 1]]
            i += 2 + d[i + 1]
        mtype = opts.get(53, b"\0")[0]
        mine = self.lease_for(chaddr)
        if mtype == 1:
            reply = 2
        elif mtype == 3:
            sid = opts.get(54)
            if sid and b2ip(sid) != GW_IP:
                return
            want = opts.get(50) or d[12:16]
            reply = 5 if b2ip(want) == mine else 6
        else:
            return
        o = bytes([53, 1, reply]) + bytes([54, 4]) + ip2b(GW_IP)
        if reply != 6:
            o += bytes([51, 4]) + struct.pack("!I", 86400)
            o += bytes([1, 4]) + ip2b(MASK) + bytes([3, 4]) + ip2b(GW_IP)
            o += bytes([6, 4]) + ip2b(DNS_IP)
        o += b"\xff"
        yi = ip2b(mine) if reply != 6 else b"\0" * 4
        pkt = (struct.pack("!BBBB", 2, 1, 6, 0) + xid + b"\0\0" + flags +
               b"\0" * 4 + yi + ip2b(GW_IP) + b"\0" * 4 + d[28:44] + b"\0" * 192 +
               b"\x63\x82\x53\x63" + o)
        udp = udp_segment(GW_IP, "255.255.255.255", 67, 68, pkt)
        self.send(eth(BCAST, GW_MAC, 0x0800, ip_packet(GW_IP, "255.255.255.255", 17, udp)))

    # -- DNS
    def _dns(self, gmac, ip, sport, msg):
        q = dns_parse_question(msg)
        reply_to = lambda data: self.send_ip(
            gmac, ip["dst"], ip["src"], 17,
            udp_segment(ip["dst"], ip["src"], 53, sport, data))
        if q:
            name, qtype, qclass, raw = q
            if name in self.hosts and qclass == 1:
                if qtype == 1:
                    return reply_to(dns_answer(msg, raw, qtype, self.hosts[name]))
                if qtype == 28:
                    return reply_to(dns_answer(msg, raw, qtype, None))
        loop = asyncio.get_event_loop()

        class P(asyncio.DatagramProtocol):
            def connection_made(s, t):
                s.t = t
                t.sendto(msg)
                loop.call_later(5, t.close)

            def datagram_received(s, data, addr):
                reply_to(data)
                s.t.close()
        asyncio.ensure_future(loop.create_datagram_endpoint(
            P, remote_addr=self.dns_upstream))

    # -- UDP NAT
    def _udp(self, gmac, ip, sport, dport, data):
        key = (sport, ip["dst"], dport)
        fl = self.udp.get(key)
        if fl is None:
            fl = UdpFlow(self, key, gmac, ip["src"])
            self.udp[key] = fl
            host = "127.0.0.1" if ip["dst"] == GW_IP else ip["dst"]
            fut = asyncio.ensure_future(asyncio.get_event_loop().create_datagram_endpoint(
                lambda: fl, remote_addr=(host, dport)))
            fl.pending = [data]

            def done(f, fl=fl):
                try:
                    f.result()
                except Exception:
                    self.udp.pop(fl.key, None)
                    return
                for d in fl.pending:
                    fl.transport.sendto(d)
                fl.pending = None
                fl.touch()
            fut.add_done_callback(done)
        elif fl.pending is not None:
            fl.pending.append(data)
        else:
            fl.transport.sendto(data)
            fl.touch()

    # -- TCP NAT
    def _tcp(self, gmac, ip, s):
        sport, dport, seq, ack, offf, win = struct.unpack("!HHIIHH", s[:16])
        off, flags = (offf >> 12) * 4, offf & 0x3F
        data = s[off:]
        key = (sport, ip["dst"], dport)
        c = self.tcp.get(key)
        if c is None:
            if flags & SYN and not flags & (ACK | RST):
                self.tcp[key] = TcpConn(self, key, gmac, ip["src"], sport,
                                        ip["dst"], dport, seq, win)
            elif not flags & RST:
                rseq, rack, rf = (ack, 0, RST) if flags & ACK else (
                    0, (seq + len(data) + (1 if flags & (SYN | FIN) else 0)) & M32, RST | ACK)
                self.send_ip(gmac, ip["dst"], ip["src"], 6, tcp_segment(
                    ip["dst"], ip["src"], dport, sport, rseq, rack, rf, 0))
            return
        c.on_segment(seq, ack, flags, win, data)


# ------------------------------------------------------------- WebSocket
WS_GUID = b"258EAFA5-E914-47DA-95CA-C5AB0DC85B11"


def ws_encode(opcode, payload, mask=False):
    b0 = 0x80 | opcode
    n = len(payload)
    m = 0x80 if mask else 0
    if n < 126:
        h = struct.pack("!BB", b0, m | n)
    elif n < 65536:
        h = struct.pack("!BBH", b0, m | 126, n)
    else:
        h = struct.pack("!BBQ", b0, m | 127, n)
    if mask:
        key = os.urandom(4)
        payload = bytes(b ^ key[i & 3] for i, b in enumerate(payload))
        return h + key + payload
    return h + payload


async def ws_read(reader):
    """Return (opcode, payload) of one complete message; handles fragments."""
    msg, op0 = b"", None
    while True:
        b0, b1 = await reader.readexactly(2)
        fin, op = b0 & 0x80, b0 & 15
        n = b1 & 0x7F
        if n == 126:
            n = struct.unpack("!H", await reader.readexactly(2))[0]
        elif n == 127:
            n = struct.unpack("!Q", await reader.readexactly(8))[0]
        key = await reader.readexactly(4) if b1 & 0x80 else None
        p = await reader.readexactly(n)
        if key:
            p = bytes(b ^ key[i & 3] for i, b in enumerate(p))
        if op >= 8:                      # control frames are never fragmented
            return op, p
        if op != 0:
            op0 = op
        msg += p
        if fin:
            return op0, msg


async def handle_ws(reader, writer, make_gateway):
    head = await reader.readuntil(b"\r\n\r\n")
    hdrs = {}
    for line in head.decode("latin1").split("\r\n")[1:]:
        if ":" in line:
            k, v = line.split(":", 1)
            hdrs[k.strip().lower()] = v.strip()
    key = hdrs.get("sec-websocket-key")
    if not key or hdrs.get("upgrade", "").lower() != "websocket":
        writer.write(b"HTTP/1.1 400 Bad Request\r\nContent-Length: 0\r\n\r\n")
        await writer.drain()
        writer.close()
        return
    acc = base64.b64encode(hashlib.sha1(key.encode() + WS_GUID).digest()).decode()
    writer.write(("HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\n"
                  "Connection: Upgrade\r\nSec-WebSocket-Accept: %s\r\n\r\n" % acc).encode())

    def send(frame):
        writer.write(ws_encode(2, frame))
    gw = make_gateway(send)
    writer.write(ws_encode(1, json.dumps(
        {"type": "config", "gateway": GW_IP, "gwmac": GW_MAC.hex(),
         "dns": DNS_IP, "mask": MASK}).encode()))
    try:
        while True:
            op, p = await ws_read(reader)
            if op == 2:
                gw.rx_from_guest(p)
            elif op == 9:
                writer.write(ws_encode(10, p))
            elif op == 8:
                writer.write(ws_encode(8, p[:2]))
                break
    except (asyncio.IncompleteReadError, ConnectionError):
        pass
    finally:
        for c in list(gw.tcp.values()):
            c.close()
        for u in list(gw.udp.values()):
            u.expire()
        writer.close()


async def serve(host, port, **gwkw):
    return await asyncio.start_server(
        lambda r, w: handle_ws(r, w, lambda send: Gateway(send, **gwkw)),
        host, port)


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--listen", default="127.0.0.1:8765")
    ap.add_argument("--dns", help="upstream DNS ip[:port]")
    ap.add_argument("--host", action="append", default=[], metavar="NAME=IP",
                    help="static DNS A record (repeatable)")
    a = ap.parse_args()
    h, p = a.listen.rsplit(":", 1)
    up = None
    if a.dns:
        d = a.dns.split(":")
        up = (d[0], int(d[1]) if len(d) > 1 else 53)
    hosts = dict(x.split("=", 1) for x in a.host)

    async def run():
        s = await serve(h, int(p), dns_upstream=up, hosts=hosts)
        print("relay listening on ws://%s:%s" % (h, p), flush=True)
        async with s:
            await s.serve_forever()
    try:
        asyncio.run(run())
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
