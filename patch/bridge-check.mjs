// Protocol check against the real relay: node bridge-check.mjs [ws://localhost:8008]
// Sends an ARP who-has 10.0.2.2 as ONE binary message and expects ONE binary ARP reply,
// after the relay's initial JSON text "config" message.
const url = process.argv[2] || 'ws://localhost:8008';
const ws = new WebSocket(url); ws.binaryType = 'arraybuffer';
const mac = [0x02, 0, 0, 0, 0, 1], bc = Array(6).fill(255);
const arp = Uint8Array.from([...bc, ...mac, 8, 6, 0, 1, 8, 0, 6, 4, 0, 1, ...mac, 10, 0, 2, 15, 0, 0, 0, 0, 0, 0, 10, 0, 2, 2]);
const t = setTimeout(() => { console.error('FAIL: timeout'); process.exit(1); }, 5000);
let config = null;
ws.onerror = () => { console.error('FAIL: cannot connect to ' + url); process.exit(1); };
ws.onmessage = ({data}) => {
  if (typeof data === 'string') { config = JSON.parse(data); console.log('config', config); ws.send(arp); return; }
  const f = new Uint8Array(data);
  const ok = f[12] === 8 && f[13] === 6 && f[21] === 2 && f.slice(28, 32).join('.') === '10.0.2.2';
  console.log(ok ? 'PASS: ARP reply' : 'FAIL', Buffer.from(f).toString('hex'));
  clearTimeout(t); process.exit(ok ? 0 : 1);
};
