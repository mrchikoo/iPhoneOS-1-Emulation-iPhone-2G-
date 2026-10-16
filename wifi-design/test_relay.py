import asyncio
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
                inflight_max = max(inflight_max, total - (h.gw.tcp[(sp, R.GW_IP, port)].acked - 1))
                h.tx(seg(2, sseq + total, R.ACK))
            self.assertLessEqual(inflight_max, 1500 + R.MSS)
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
