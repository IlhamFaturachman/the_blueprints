with open("web_ui/index.html", "r") as f:
    js = f.read()

js = js.replace("let cleanSlug = rawSlug.replace(/-[0-9]+(c|f)?(|orhigher|orbelow)$/i, '');", "let cleanSlug = rawSlug.replace(/-[0-9]+[cf]?(orhigher|orbelow)?$/i, '');")

with open("web_ui/index.html", "w") as f:
    f.write(js)
print("UI Names Patched Slug Regex")
