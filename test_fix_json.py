# test_fix_json.py
import requests

url = "https://drive.google.com/uc?export=download&id=1mr0in_nJeVN3I6cFlPEFq_U4larqDq4r"
r = requests.get(url)
bad = r.content.decode('latin1')
fixed = bad.encode('utf-8').decode('utf-8')

with open("decoded_blogs.json", "w", encoding="utf-8") as f:
    f.write(fixed)

print("✅ Saved decoded_blogs.json successfully.")
