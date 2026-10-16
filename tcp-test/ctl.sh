#!/bin/sh
# usage: ctl.sh "cmd1;cmd2;..."  appends commands to ctl/cmd
echo "$1" | tr ';' '\n' >> "$(dirname "$0")/ctl/cmd"
