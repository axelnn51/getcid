import urllib.request, json, base64
req = urllib.request.Request('https://api.github.com/repos/laomms/GetCID-Pro/readme', headers={'User-Agent': 'Mozilla/5.0'})
try:
    with urllib.request.urlopen(req) as response:
        data = json.loads(response.read().decode())
        text = base64.b64decode(data['content']).decode('utf-8')
        print(text.encode('ascii', 'ignore').decode())
except Exception as e:
    print(e)
