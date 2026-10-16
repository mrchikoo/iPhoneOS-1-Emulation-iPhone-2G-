#!/bin/sh
# Start the relay (ws://127.0.0.1:8008, decoded frames -> relay.log) and the site server (http://localhost:8200).
# Usage: ./run.sh [extra relay.py args, e.g. --host mysite.test=10.0.2.2]
# Then open http://localhost:8200/?cold=1   (Ctrl-C stops both)
cd "$(dirname "$0")" || exit 1
[ -d site ] || python3 build.py || exit 1
: > relay.log
python3 relay_logged.py --listen 127.0.0.1:8008 --log relay.log --host web.test=10.0.2.2 "$@" &
RELAY_PID=$!
python3 ../browser/proxy.py 8090 2>proxy.log &
PROXY_PID=$!
python3 ../mirror/serve.py 8200 site &
SERVER_PID=$!
trap 'kill $RELAY_PID $SERVER_PID $PROXY_PID 2>/dev/null' INT TERM EXIT
echo "relay  ws://127.0.0.1:8008 (pid $RELAY_PID, log $(pwd)/relay.log)"
echo "web proxy on the phone: http://web.test:8090/  (or http://10.0.2.2:8090/)"
echo "server http://localhost:8200/?cold=1 (pid $SERVER_PID)"
wait
