#!/bin/bash
# The desk reached from anywhere: a Cloudflare quick tunnel to the local server (basic auth from auth.json in front).
# The trycloudflare address changes at every start; it is printed here and written to tunnel.url.
# --no-autoupdate: a quick tunnel that updates itself stops serving (it did, a day in), and the address dies with it.
cd "$(dirname "$0")"
pgrep -f "cloudflared tunnel --no-autoupdate --url http://127.0.0.1:8765" > /dev/null || (nohup ~/bin/cloudflared tunnel --no-autoupdate --url http://127.0.0.1:8765 > tunnel.log 2>&1 &)
for i in $(seq 1 20); do U=$(grep -o "https://[a-z0-9-]*\.trycloudflare\.com" tunnel.log | head -1); [ -n "$U" ] && break; sleep 1; done
echo "$U" | tee tunnel.url
