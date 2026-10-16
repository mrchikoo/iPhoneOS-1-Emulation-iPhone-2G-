#!/bin/sh
# usage: run.sh outdir script [extra env...]; assumes relay on :8008 and http on :8099
cd "$(dirname "$0")"; OUT=$1; SCRIPT=$2; shift 2
mkdir -p $OUT; cp $SCRIPT ctl/cmd.$$; : > ctl/cmd; cat ctl/cmd.$$ >> ctl/cmd; rm ctl/cmd.$$
env RELAY=ws://127.0.0.1:8008 CONTACTS=1 CTL=ctl/cmd "$@" node --max-old-space-size=6000 coldboot.mjs 60000000000 $OUT
