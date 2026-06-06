import json

with open("decoded_blogs.json", "r", encoding="utf-8") as f:
    data = json.load(f)

def fix_text(text):
    try:
        return text.encode('latin1').decode('utf-8')
    except:
        return text  # fallback

# Fix only the `content` field of each blog
for blog in data:
    blog["content"] = fix_text(blog["content"])

# Save to a new clean file
with open("cleaned_blogs.json", "w", encoding="utf-8") as f:
    json.dump(data, f, ensure_ascii=False, indent=2)

print("✅ Blog content fixed and saved to cleaned_blogs.json")
