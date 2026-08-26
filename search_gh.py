import urllib.request, json
url = "https://api.github.com/search/repositories?q=getcid+OR+webact+OR+pidkeytool&sort=stars"
req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
try:
    with urllib.request.urlopen(req) as response:
        data = json.loads(response.read().decode())
        for i in data.get('items', [])[:10]:
            desc = (i.get('description') or '').encode('ascii', 'ignore').decode()
            print(f"- {i['full_name']} | Lang: {i['language']} | Stars: {i['stargazers_count']}")
            print(f"  {desc}\n  {i['html_url']}\n")
except Exception as e:
    print(e)
