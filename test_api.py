import urllib.request
import json
req = urllib.request.Request(
    'http://localhost:8000/check_pid',
    data=json.dumps({'pid': '357924661076125925421879311561102041098650444344630147635232565'}).encode('utf-8'),
    headers={'Content-Type': 'application/json'}
)
res = urllib.request.urlopen(req)
print(res.read().decode('utf-8'))
