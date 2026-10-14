"""Write data/progression_config.json (the GET /progression/config payload: every progression path with its
ranks, XP curve and rewards) from a saved copy of the official response (game data: made locally, not distributed).
python tools/make_progression_config.py <progression config JSON> [out]"""
import json
import os
import sys

src = sys.argv[1]
out = sys.argv[2] if len(sys.argv) > 2 else os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                                                          'data', 'progression_config.json')
d = json.load(open(src, encoding='utf-8-sig'))
payload = d.get('payload', d) if isinstance(d, dict) else None
if not isinstance(payload, dict) or not isinstance(payload.get('paths'), list):
    raise SystemExit('not a progression config capture: ' + src)
os.makedirs(os.path.dirname(out), exist_ok=True)
json.dump({'paths': payload['paths']}, open(out, 'w', encoding='utf-8'), indent=1)
print('progression paths', len(payload['paths']), '->', out)
