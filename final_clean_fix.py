import json

with open("decoded_blogs.json", "r", encoding="utf-8") as f:
    blogs = json.load(f)

def fix_text(text):
    try:
        return text.encode('latin1').decode('utf-8')
    except:
        return text

# Fix the content field in each blog
for blog in blogs:
    blog["content"] = fix_text(blog["content"])

# Save to final file
with open("cleaned_blogs.json", "w", encoding="utf-8") as f:
    json.dump(blogs, f, ensure_ascii=False, indent=2)

print("✅ Blog content fixed and saved to cleaned_blogs.json")
