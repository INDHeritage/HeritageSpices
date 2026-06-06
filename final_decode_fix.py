import json

def fix_encoding(text):
    try:
        return text.encode('latin1').decode('utf-8')
    except:
        return text

with open('cleaned_blogs.json', 'r', encoding='utf-8') as f:
    blogs = json.load(f)

for blog in blogs:
    blog['content'] = fix_encoding(blog['content'])
    blog['title'] = fix_encoding(blog['title'])

with open('final_blogs.json', 'w', encoding='utf-8') as f:
    json.dump(blogs, f, ensure_ascii=False, indent=2)

print("✅ All content cleaned. Saved to final_blogs.json")
