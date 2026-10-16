import asyncio
import os
import struct
import unittest

import relay as R

GMAC = bytes.fromhex("020000aabbcc")
GIP = "10.0.2.15"


def run(coro):
    return asyncio.run(asyncio.wait_for(coro, 15))


class Harness:
    def __init__(self, **kw):
        self.out = asyncio.Queue()
        self.gw = R.Gateway(self.out.put_nowait, **kw)

    def tx(self, frame):
        self.gw.rx_from_guest(frame)

    async def rx(self, pred=lambda f: True, t=5):
        while True:
            f = await asyncio.wait_for(self.out.get(), t)
            if pred(f):
                return f

    def ip_frame(self, dst, proto, payload, src=GIP):
        return R.eth(R.GW_MAC, GMAC, 0x0800, R.ip_packet(src, dst, proto, payload))


def ip_of(f):
    return R.parse_ip(f[14:])


def tcp_of(f):
    ip = ip_of(f)
    s = ip["payload"]
    sp, dp, seq, ack, offf, win = struct.unpack("!HHIIHH", s[:16])
    return dict(sport=sp, dport=dp, seq=seq, ack=ack, flags=offf & 63,
                data=s[(offf >> 12) * 4:], ip=ip, raw=s)


def dhcp_pkt(mtype, xid=b"\x01\x02\x03\x04", extra=b""):
    p = (struct.pack("!BBBB", 1, 1, 6, 0) + xid + b"\0\0\x80\0" + b"\0" * 16 +
         GMAC + b"\0" * 10 + b"\0" * 192 + b"\x63\x82\x53\x63" +
         bytes([53, 1, mtype]) + extra + b"\xff")
    udp = R.udp_segment("0.0.0.0", "255.255.255.255", 68, 67, p)
    return R.eth(R.BCAST, GMAC, 0x0800, R.ip_packet("0.0.0.0", "255.255.255.255", 17, udp))


def dhcp_opts(frame):
    d = ip_of(frame)["payload"][8:]
    o, i = {}, 240
    while d[i] != 255:
        o[d[i]] = d[i + 2:i + 2 + d[i + 1]]
        i += 2 + d[i + 1]
    return d, o


class Basic(unittest.TestCase):
    def test_checksum(self):
        pkt = R.ip_packet("1.2.3.4", "5.6.7.8", 17, b"abcd")
        self.assertEqual(R.csum(pkt[:20]), 0)
        # known vector from RFC 1071 example
        self.assertEqual(R.csum(bytes.fromhex("0001f203f4f5f6f7")), 0x220d)

    def test_arp(self):
        async def t():
            h = Harness()
            req = struct.pack("!HHBBH", 1, 0x0800, 6, 4, 1) + GMAC + R.ip2b(GIP) + \
                b"\0" * 6 + R.ip2b(R.GW_IP)
            h.tx(R.eth(R.BCAST, GMAC, 0x0806, req))
            f = await h.rx()
            self.assertEqual(f[:6], GMAC)
            self.assertEqual(f[12:14], b"\x08\x06")
            self.assertEqual(f[14 + 6:14 + 8], b"\x00\x02")
            self.assertEqual(f[14 + 8:14 + 14], R.GW_MAC)
            # other ip: no reply
            req2 = req[:-4] + R.ip2b("10.0.2.99")
            h.tx(R.eth(R.BCAST, GMAC, 0x0806, req2))
            await asyncio.sleep(0.05)
            self.assertTrue(h.out.empty())
        run(t())

    def test_dhcp(self):
        async def t():
            h = Harness()
            h.tx(dhcp_pkt(1))
            f = await h.rx()
            d, o = dhcp_opts(f)
            self.assertEqual(o[53], b"\x02")
            self.assertEqual(R.b2ip(d[16:20]), "10.0.2.15")
            self.assertEqual(R.b2ip(o[3]), R.GW_IP)
            self.assertEqual(R.b2ip(o[6]), R.DNS_IP)
            self.assertEqual(R.b2ip(o[1]), R.MASK)
            self.assertEqual(d[4:8], b"\x01\x02\x03\x04")
            req = bytes([50, 4]) + R.ip2b("10.0.2.15") + bytes([54, 4]) + R.ip2b(R.GW_IP)
            h.tx(dhcp_pkt(3, extra=req))
            d, o = dhcp_opts(await h.rx())
            self.assertEqual(o[53], b"\x05")
            # wrong address -> NAK
            req = bytes([50, 4]) + R.ip2b("10.0.2.77")
            h.tx(dhcp_pkt(3, extra=req))
            d, o = dhcp_opts(await h.rx())
            self.assertEqual(o[53], b"\x06")
            # another server's REQUEST ignored
            req = bytes([54, 4]) + R.ip2b("9.9.9.9")
            h.tx(dhcp_pkt(3, extra=req))
            await asyncio.sleep(0.05)
            self.assertTrue(h.out.empty())
        run(t())

    def test_icmp(self):
        async def t():
            h = Harness()
            icmp = b"\x08\x00\x00\x00\x12\x34\x00\x01payload"
            icmp = icmp[:2] + struct.pack("!H", R.csum(icmp)) + icmp[4:]
            h.tx(h.ip_frame(R.GW_IP, 1, icmp))
            ip = ip_of(await h.rx())
            self.assertEqual(ip["payload"][0], 0)
            self.assertEqual(R.csum(ip["payload"]), 0)
            self.assertEqual(ip["payload"][8:], b"payload")
        run(t())


class Net(unittest.TestCase):
    def test_dns_hosts_and_forward(self):
        async def t():
            loop = asyncio.get_running_loop()
            seen = []

            class Up(asyncio.DatagramProtocol):
                def connection_made(s, tr):
                    s.tr = tr

                def datagram_received(s, data, addr):
                    seen.append(data)
                    s.tr.sendto(data[:2] + b"\x81\x80" + data[4:], addr)
            tr, _ = await loop.create_datagram_endpoint(Up, local_addr=("127.0.0.1", 0))
            up = tr.get_extra_info("sockname")
            h = Harness(dns_upstream=up, hosts={"Test.Local": "10.0.2.2"})

            def query(name, qtype=1, txid=b"\xab\xcd"):
                q = b"".join(bytes([len(x)]) + x.encode() for x in name.split(".")) + b"\0"
                m = txid + b"\x01\x00\x00\x01\x00\x00\x00\x00\x00\x00" + q + struct.pack("!HH", qtype, 1)
                return m, h.ip_frame(R.DNS_IP, 17, R.udp_segment(GIP, R.DNS_IP, 5353, 53, m))
            m, fr = query("test.local")
            h.tx(fr)
            ip = ip_of(await h.rx())
            r = ip["payload"][8:]
            self.assertEqual(r[:2], b"\xab\xcd")
            self.assertEqual(r[-4:], bytes([10, 0, 2, 2]))
            self.assertEqual(struct.unpack("!H", r[6:8])[0], 1)
            self.assertEqual(seen, [])
            m, fr = query("example.org", txid=b"\x11\x22")
            h.tx(fr)
            ip = ip_of(await h.rx())
            self.assertEqual(ip["payload"][8:10], b"\x11\x22")
            self.assertEqual(seen, [m])
            self.assertEqual(struct.unpack("!H", ip["payload"][2:4])[0], 5353)
            tr.close()
        run(t())

    def test_udp_nat_to_host_via_gateway(self):
        async def t():
            loop = asyncio.get_running_loop()

            class Echo(asyncio.DatagramProtocol):
                def connection_made(s, tr):
                    s.tr = tr

                def datagram_received(s, data, addr):
                    s.tr.sendto(b"echo:" + data, addr)
            tr, _ = await loop.create_datagram_endpoint(Echo, local_addr=("127.0.0.1", 0))
            port = tr.get_extra_info("sockname")[1]
            h = Harness()
            for msg in (b"one", b"two"):
                h.tx(h.ip_frame(R.GW_IP, 17, R.udp_segment(GIP, R.GW_IP, 4000, port, msg)))
                ip = ip_of(await h.rx())
                self.assertEqual(ip["src"], R.GW_IP)
                self.assertEqual(ip["payload"][8:], b"echo:" + msg)
                self.assertEqual(struct.unpack("!H", ip["payload"][2:4])[0], 4000)
            self.assertEqual(len(h.gw.udp), 1)
            for u in list(h.gw.udp.values()):
                u.expire()
            tr.close()
        run(t())

    def test_tcp_nat_roundtrip(self):
        async def t():
            got = {}

            async def srv(r, w):
                data = await r.read(100)
                got["req"] = data
                w.write(b"HTTP-ish reply " * 400)          # 6000 bytes, > MSS
                await w.drain()
                got["rest"] = await r.read()                # until guest FIN
                w.close()
            server = await asyncio.start_server(srv, "127.0.0.1", 0)
            port = server.sockets[0].getsockname()[1]
            h = Harness()
            sp, gisn = 40000, 1000

            def seg(seq, ack, fl, data=b"", win=65535):
                return h.ip_frame(R.GW_IP, 6, R.tcp_segment(GIP, R.GW_IP, sp, port, seq, ack, fl, win, data))
            h.tx(seg(gisn, 0, R.SYN))
            sa = tcp_of(await h.rx())
            self.assertEqual(sa["flags"], R.SYN | R.ACK)
            self.assertEqual(sa["ack"], gisn + 1)
            self.assertEqual(R.csum(R.pseudo(R.GW_IP, GIP, 6, len(sa["raw"])) + sa["raw"]), 0)
            sseq = sa["seq"] + 1
            h.tx(seg(gisn + 1, sseq, R.ACK))
            req = b"GET / HTTP/1.0\r\n\r\n"
            h.tx(seg(gisn + 1, sseq, R.ACK | R.PSH, req))
            nxt = gisn + 1 + len(req)
            body = b""
            while len(body) < 6000:
                s = tcp_of(await h.rx(lambda f: ip_of(f) and ip_of(f)["proto"] == 6))
                if s["data"]:
                    self.assertEqual(s["seq"], sseq + len(body))
                    self.assertLessEqual(len(s["data"]), R.MSS)
                    body += s["data"]
                    h.tx(seg(nxt, sseq + len(body), R.ACK))
            self.assertEqual(body, b"HTTP-ish reply " * 400)
            self.assertEqual(got["req"], req)
            # guest half-closes; host server then closes -> relay sends FIN
            h.tx(seg(nxt, sseq + len(body), R.FIN | R.ACK))
            fin = None
            while fin is None:
                s = tcp_of(await h.rx(lambda f: ip_of(f) and ip_of(f)["proto"] == 6))
                if s["flags"] & R.FIN:
                    fin = s
            h.tx(seg(nxt + 1, fin["seq"] + 1, R.ACK))
            for _ in range(50):
                if not h.gw.tcp:
                    break
                await asyncio.sleep(0.05)
            self.assertFalse(h.gw.tcp)
            self.assertEqual(got.get("rest"), b"")
            server.close()
        run(t())

    def test_tcp_flow_control_small_window(self):
        async def t():
            async def srv(r, w):
                w.write(b"x" * 5000)
                await w.drain()
                await r.read()
                w.close()
            server = await asyncio.start_server(srv, "127.0.0.1", 0)
            port = server.sockets[0].getsockname()[1]
            h = Harness()
            sp = 41000
            seg = lambda s, a, f, w=1500: h.ip_frame(R.GW_IP, 6, R.tcp_segment(GIP, R.GW_IP, sp, port, s, a, f, w))
            h.tx(seg(1, 0, R.SYN))
            sa = tcp_of(await h.rx())
            sseq = sa["seq"] + 1
            h.tx(seg(2, sseq, R.ACK))
            total, inflight_max = 0, 0
            while total < 5000:
                s = tcp_of(await h.rx())
                total += len(s["data"])
                inflight_max = max(inflight_max, total - (h.gw.tcp[(sp, R.GW_IP, port)].una - 1))
                h.tx(seg(2, sseq + total, R.ACK))
            self.assertLessEqual(inflight_max, 1500)   # never beyond the advertised window
            for c in list(h.gw.tcp.values()):
                c.close()
            server.close()
        run(t())

    def test_tcp_refused_gets_rst(self):
        async def t():
            import socket
            s = socket.socket()
            s.bind(("127.0.0.1", 0))
            port = s.getsockname()[1]
            s.close()
            h = Harness()
            h.tx(h.ip_frame(R.GW_IP, 6, R.tcp_segment(GIP, R.GW_IP, 42000, port, 5, 0, R.SYN, 1000)))
            r = tcp_of(await h.rx())
            self.assertTrue(r["flags"] & R.RST)
        run(t())

    def test_tcp_unknown_gets_rst(self):
        async def t():
            h = Harness()
            h.tx(h.ip_frame("1.2.3.4", 6, R.tcp_segment(GIP, "1.2.3.4", 1, 2, 5, 9, R.ACK, 10, b"zz")))
            r = tcp_of(await h.rx())
            self.assertTrue(r["flags"] & R.RST)
            self.assertEqual(r["seq"], 9)
        run(t())


class GuestTcp:
    """Minimal scripted guest TCP endpoint talking to a Harness."""

    def __init__(self, h, port, sp=45000, isn=1000, win=65535, syn_opts=b""):
        self.h, self.port, self.sp, self.win = h, port, sp, win
        self.snd = isn + 1
        self.isn, self.syn_opts = isn, syn_opts
        self.rcv = None

    def seg(self, flags, data=b"", seq=None, ack=None, win=None, opts=b""):
        seq = self.snd if seq is None else seq
        ack = (self.rcv or 0) if ack is None else ack
        win = self.win if win is None else win
        raw = R.tcp_segment(GIP, R.GW_IP, self.sp, self.port, seq, ack, flags, win, data)
        if opts:
            off = (20 + len(opts)) // 4
            raw = raw[:12] + struct.pack("!H", (off << 12) | flags) + raw[14:16] + b"\0\0" + raw[18:20] + opts + data
            raw = raw[:16] + struct.pack("!H", R.csum(R.pseudo(GIP, R.GW_IP, 6, len(raw)) + raw)) + raw[18:]
        return self.h.ip_frame(R.GW_IP, 6, raw)

    async def connect(self):
        self.h.tx(self.seg(R.SYN, seq=self.isn, ack=0, opts=self.syn_opts))
        sa = await self.recv()
        assert sa["flags"] == R.SYN | R.ACK, sa["flags"]
        self.synack = sa
        self.rcv = sa["seq"] + 1
        self.h.tx(self.seg(R.ACK))
        return sa

    async def recv(self, t=5, pred=None):
        f = await self.h.rx(lambda f: ip_of(f) and ip_of(f)["proto"] == 6 and
                            (pred is None or pred(tcp_of(f))), t)
        return tcp_of(f)

    def ack(self, win=None, ack=None):
        self.h.tx(self.seg(R.ACK, ack=ack, win=win))

    def send(self, data):
        self.h.tx(self.seg(R.ACK | R.PSH, data))
        self.snd += len(data)


async def tcp_server(handler):
    server = await asyncio.start_server(handler, "127.0.0.1", 0)
    return server, server.sockets[0].getsockname()[1]


class TcpStack(unittest.TestCase):
    """Behaviour a real (BSD) TCP peer needs from the relay."""

    def test_never_sends_beyond_window_even_tiny(self):
        async def t():
            async def srv(r, w):
                w.write(bytes(range(256)) * 20)       # 5120 bytes
                await w.drain()
                await r.read()
                w.close()
            server, port = await tcp_server(srv)
            h = Harness()
            g = GuestTcp(h, port, win=60)
            await g.connect()
            got = b""
            while len(got) < 5120:
                s = await g.recv()
                if not s["data"]:
                    continue
                self.assertLessEqual(len(s["data"]), 60)
                self.assertEqual(s["seq"], g.rcv + len(got))
                got += s["data"]
                g.ack(ack=g.rcv + len(got))
            self.assertEqual(got, bytes(range(256)) * 20)
            for c in list(h.gw.tcp.values()):
                c.close()
            server.close()
        run(t())

    def test_window_shrink_midstream_no_overrun(self):
        # the real-iPhone failure: window falls to 60, relay used to send a full MSS
        async def t():
            async def srv(r, w):
                w.write(b"A" * 20000)
                await w.drain()
                await r.read()
                w.close()
            server, port = await tcp_server(srv)
            h = Harness()
            g = GuestTcp(h, port, win=3000)
            await g.connect()
            got = 0
            while got < 8000:
                s = await g.recv(pred=lambda s: s["data"])
                self.assertEqual(s["seq"], g.rcv + got)
                got += len(s["data"])
                g.ack(ack=g.rcv + got, win=3000)
            # drain whatever was already in flight under the old window, then shrink
            while not h.out.empty():
                s = tcp_of(h.out.get_nowait())
                if s["data"] and s["seq"] == g.rcv + got:
                    got += len(s["data"])
            high = g.rcv + got                         # everything sent so far is below this
            g.ack(ack=high, win=60)
            new_bytes = 0
            for _ in range(10):
                try:
                    p = await g.recv(t=0.3, pred=lambda s: s["data"])
                except asyncio.TimeoutError:
                    break
                if p["seq"] < high:                    # old in-flight or retransmission
                    continue
                self.assertLessEqual(p["seq"] + len(p["data"]), high + 60)
                self.assertLessEqual(len(p["data"]), 60)
                if p["seq"] == g.rcv + got:
                    got += len(p["data"])
                    new_bytes += len(p["data"])
            g.ack(ack=g.rcv + got, win=3000)
            while got < 20000:
                s = await g.recv(pred=lambda s: s["data"])
                if s["seq"] == g.rcv + got:
                    got += len(s["data"])
                g.ack(ack=g.rcv + got, win=3000)
            self.assertEqual(got, 20000)
            for c in list(h.gw.tcp.values()):
                c.close()
            server.close()
        run(t())

    def test_zero_window_persist_and_resume(self):
        async def t():
            payload = bytes((i * 7) & 255 for i in range(6000))

            async def srv(r, w):
                w.write(payload)
                await w.drain()
                await r.read()
                w.close()
            server, port = await tcp_server(srv)
            h = Harness()
            g = GuestTcp(h, port, win=0)
            await g.connect()
            # window is zero: nothing but (at most) tiny probes may arrive
            seen = b""
            try:
                while True:
                    s = await g.recv(t=1.2)
                    if s["data"]:
                        self.assertLessEqual(len(s["data"]), 1)
                        seen += s["data"]
                        g.ack(win=0, ack=g.rcv)
            except asyncio.TimeoutError:
                pass
            conn = next(iter(h.gw.tcp.values()))
            self.assertGreaterEqual(conn.stats["probes"], 1)
            # reopen the window: the whole payload must follow byte-exact from seq rcv
            got = b""
            g.ack(win=4000, ack=g.rcv)
            while len(got) < len(payload):
                s = await g.recv(t=3)
                if not s["data"]:
                    continue
                if s["seq"] != g.rcv + len(got):          # probe byte / retransmission
                    g.ack(win=4000, ack=g.rcv + len(got))
                    continue
                got += s["data"]
                g.ack(win=4000, ack=g.rcv + len(got))
            self.assertEqual(got, payload)
            for c in list(h.gw.tcp.values()):
                c.close()
            server.close()
        run(t())

    def test_rto_retransmit_after_loss(self):
        async def t():
            async def srv(r, w):
                w.write(b"hello world")
                await w.drain()
                await r.read()
                w.close()
            server, port = await tcp_server(srv)
            h = Harness()
            g = GuestTcp(h, port)
            await g.connect()
            first = await g.recv(pred=lambda s: s["data"])
            t0 = asyncio.get_running_loop().time()
            again = await g.recv(t=3, pred=lambda s: s["data"])      # never ACKed -> RTO
            self.assertEqual(again["seq"], first["seq"])
            self.assertEqual(again["data"], b"hello world")
            self.assertGreater(asyncio.get_running_loop().time() - t0, 0.2)
            g.ack(ack=g.rcv + 11)
            conn = next(iter(h.gw.tcp.values()))
            self.assertGreaterEqual(conn.stats["retx"], 1)
            for c in list(h.gw.tcp.values()):
                c.close()
            server.close()
        run(t())

    def test_fast_retransmit_on_three_dupacks(self):
        async def t():
            async def srv(r, w):
                w.write(b"B" * (R.MSS * 4))
                await w.drain()
                await r.read()
                w.close()
            server, port = await tcp_server(srv)
            h = Harness()
            g = GuestTcp(h, port)
            await g.connect()
            segs = []
            while len(segs) < 4:
                s = await g.recv(pred=lambda s: s["data"])
                segs.append(s)
            # guest lost segment 0 (never delivered): dup-ACK rcv three times
            for _ in range(3):
                g.ack(ack=g.rcv)
            r = await g.recv(t=0.3, pred=lambda s: s["data"] and s["seq"] == segs[0]["seq"])
            self.assertEqual(r["data"], segs[0]["data"])
            self.assertEqual(next(iter(h.gw.tcp.values())).stats["fast"], 1)
            for c in list(h.gw.tcp.values()):
                c.close()
            server.close()
        run(t())

    def test_lossy_bulk_transfer_is_byte_exact(self):
        async def t():
            payload = os.urandom(200000)

            async def srv(r, w):
                w.write(payload)
                await w.drain()
                await r.read()
                w.close()
            server, port = await tcp_server(srv)
            h = Harness()
            g = GuestTcp(h, port, win=16384)
            await g.connect()
            got, n = b"", 0
            deadline = asyncio.get_running_loop().time() + 12
            while len(got) < len(payload) and asyncio.get_running_loop().time() < deadline:
                try:
                    s = await g.recv(t=1)
                except asyncio.TimeoutError:
                    continue
                if not s["data"]:
                    continue
                n += 1
                if n % 7 == 0:                # lose every 7th segment on the way to the guest
                    continue
                if s["seq"] == g.rcv + len(got):
                    got += s["data"]
                g.ack(ack=g.rcv + len(got), win=16384)
            self.assertEqual(len(got), len(payload))
            self.assertEqual(got, payload)
            for c in list(h.gw.tcp.values()):
                c.close()
            server.close()
        run(t())

    def test_syn_retransmit_gets_synack_again(self):
        async def t():
            async def srv(r, w):
                await r.read()
                w.close()
            server, port = await tcp_server(srv)
            h = Harness()
            g = GuestTcp(h, port)
            sa1 = await _first_synack(g)
            h.tx(g.seg(R.SYN, seq=g.isn, ack=0))        # guest never got the SYN-ACK
            sa2 = await g.recv()
            self.assertEqual(sa2["flags"], R.SYN | R.ACK)
            self.assertEqual(sa2["seq"], sa1["seq"])    # same ISN, no new connection
            self.assertEqual(len(h.gw.tcp), 1)
            for c in list(h.gw.tcp.values()):
                c.close()
            server.close()
        run(t())

    def test_mss_option_respected_and_no_window_scale_offered(self):
        async def t():
            async def srv(r, w):
                w.write(b"M" * 3000)
                await w.drain()
                await r.read()
                w.close()
            server, port = await tcp_server(srv)
            h = Harness()
            # MSS 536, NOP, WS 7, SACK-permitted, NOP NOP
            opts = bytes([2, 4, 0x02, 0x18, 1, 3, 3, 7, 4, 2, 1, 1])
            g = GuestTcp(h, port, syn_opts=opts)
            h.tx(g.seg(R.SYN, seq=g.isn, ack=0, opts=opts))
            sa = await g.recv()
            raw = sa["raw"]
            ho = raw[20:(raw[12] >> 4) * 4]
            self.assertNotIn(bytes([3, 3]), ho)          # no window scale in SYN-ACK
            self.assertNotIn(bytes([4, 2]), ho)          # no SACK-permitted either
            g.rcv = sa["seq"] + 1
            g.ack()
            got = 0
            while got < 3000:
                s = await g.recv(pred=lambda s: s["data"])
                self.assertLessEqual(len(s["data"]), 536)
                got += len(s["data"])
                g.ack(ack=g.rcv + got)
            for c in list(h.gw.tcp.values()):
                c.close()
            server.close()
        run(t())

    def test_fin_after_data_is_ordered_and_retransmitted(self):
        async def t():
            async def srv(r, w):
                w.write(b"tail-data")
                await w.drain()
                w.close()                       # host closes: FIN must follow the data
            server, port = await tcp_server(srv)
            h = Harness()
            g = GuestTcp(h, port)
            await g.connect()
            d = await g.recv(pred=lambda s: s["data"])
            f1 = await g.recv(pred=lambda s: s["flags"] & R.FIN)
            self.assertEqual(f1["seq"], d["seq"] + len(d["data"]))
            # no ACK -> FIN is retransmitted (data too, go-back-N)
            f2 = await g.recv(t=3, pred=lambda s: s["flags"] & R.FIN)
            self.assertEqual(f2["seq"], f1["seq"])
            g.ack(ack=f1["seq"] + 1)
            h.tx(g.seg(R.FIN | R.ACK, ack=f1["seq"] + 1))
            g.snd += 1
            last = await g.recv(pred=lambda s: s["ack"] == g.snd)
            for _ in range(40):
                if not h.gw.tcp:
                    break
                await asyncio.sleep(0.05)
            self.assertFalse(h.gw.tcp)
            server.close()
        run(t())

    def test_guest_retransmitted_data_delivered_once(self):
        async def t():
            got = []

            async def srv(r, w):
                got.append(await r.read())
                w.close()
            server, port = await tcp_server(srv)
            h = Harness()
            g = GuestTcp(h, port)
            await g.connect()
            h.tx(g.seg(R.ACK | R.PSH, b"abcdef"))
            h.tx(g.seg(R.ACK | R.PSH, b"abcdef"))              # exact duplicate
            h.tx(g.seg(R.ACK | R.PSH, b"defghi", seq=g.snd + 3))   # overlapping retransmit
            g.snd += 9
            h.tx(g.seg(R.FIN | R.ACK))
            for _ in range(40):
                if got:
                    break
                await asyncio.sleep(0.05)
            self.assertEqual(got, [b"abcdefghi"])
            for c in list(h.gw.tcp.values()):
                c.close()
            server.close()
        run(t())


async def _first_synack(g):
    g.h.tx(g.seg(R.SYN, seq=g.isn, ack=0))
    return await g.recv()


class WebSocket(unittest.TestCase):
    def test_ws_end_to_end(self):
        async def t():
            import json
            srv = await R.serve("127.0.0.1", 0)
            port = srv.sockets[0].getsockname()[1]
            r, w = await asyncio.open_connection("127.0.0.1", port)
            w.write(b"GET / HTTP/1.1\r\nHost: x\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n"
                    b"Sec-WebSocket-Key: dGhlIHNhbXBsZSBub25jZQ==\r\nSec-WebSocket-Version: 13\r\n\r\n")
            head = await r.readuntil(b"\r\n\r\n")
            self.assertIn(b"s3pPLMBiTxaQ9kYGzzhZRbK+xOo=", head)  # RFC 6455 example
            op, p = await R.ws_read(r)
            self.assertEqual(op, 1)
            self.assertEqual(json.loads(p)["gateway"], R.GW_IP)
            h = Harness()
            w.write(R.ws_encode(2, dhcp_pkt(1), mask=True))
            op, p = await R.ws_read(r)
            self.assertEqual(op, 2)
            d, o = dhcp_opts(p)
            self.assertEqual(o[53], b"\x02")
            # big frame (extended length, 16-bit) + ping
            w.write(R.ws_encode(9, b"hi", mask=True))
            op, p = await R.ws_read(r)
            self.assertEqual((op, p), (10, b"hi"))
            w.write(R.ws_encode(8, b"\x03\xe8", mask=True))
            op, p = await R.ws_read(r)
            self.assertEqual(op, 8)
            w.close()
            srv.close()
        run(t())

    def test_ws_encode_lengths(self):
        for n in (5, 200, 70000):
            enc = R.ws_encode(2, b"a" * n, mask=True)
            async def rd():
                rd_ = asyncio.StreamReader()
                rd_.feed_data(enc)
                return await R.ws_read(rd_)
            op, p = asyncio.run(rd())
            self.assertEqual((op, len(p)), (2, n))


if __name__ == "__main__":
    unittest.main()
