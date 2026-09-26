# Website audit — September 2026

Scope: code, text/claims, speed, Google (Merchant Center / Search Console), NimbusPost, and the new pouch artwork.
Status key: **DONE** = fixed and live · **YOU** = needs an owner decision or an action in a dashboard · **LATER** = worth building next.

---

## 1. What the pouch tells us (source of truth for the website)

| Item | Printed on the pouch |
|---|---|
| Taglines | **"One Spice. Every Dish. Every Time."** and **"Rooted in Tradition. Rich in Flavor."** |
| Ingredients | Black/Green Cardamom, Black Stone Flower, Nutmeg, Cinnamon, Black Pepper, Fenugreek Seed, Coriander Seed, Clove, Black Cumin Seed, **Himalayan Pink Salt**, Red Chilli, Bay Leaf, Star Anise, Dry Mace |
| Claims | No Gluten · No GMO ingredients · No artificial ingredients & fillers · No added preservatives |
| Shelf life / storage | Best before 18 months · Reseal bag after opening; store cool, dry, hygienic |
| FSSAI | Lic. No. **21521275000514** |
| MRP | ₹60 (50 g), ₹110 (100 g) incl. taxes — the website sells at ₹55 / ₹105 (below MRP, fine) |
| Contact | heritage.spices.pvtltd@gmail.com · +91 89994 49765 · indianheritagespices.com · Instagram **indian_heritage_spices** |
| Not printed | "Organic", "lab tested", "since 2012", "international standards" |

## 2. Content problems found

| # | Finding | Status |
|---|---|---|
| 1 | **FSSAI number on the site (…21175…) ≠ pouch (…21275…).** A wrong licence number on a food website is a compliance risk. Now defined once (`FSSAI_LICENSE`, default = pouch number) and shown in trust strip, products page and footer. **Confirm it against your FSSAI certificate**; if the certificate differs, set `FSSAI_LICENSE` on Render. | DONE / **YOU verify** |
| 2 | Product description (stored in the database) lists *Coriander, Cumin, Black Pepper, Cardamom, Clove, Cinnamon, Bay Leaf, Nutmeg, Mace, Dry Ginger* — **does not match the pouch** (pouch has salt, fenugreek, black stone flower, star anise, red chilli, black cumin, no ginger). Label and website must agree. Replace in Admin → Products with the text in section 6. | **YOU** |
| 3 | "**100% organic**" appears in the hero, FAQ (x2), mission slide, products page, meta keywords. The pouch does **not** claim organic, and in India "organic" labelling needs certification (NPOP/PGS + FSSAI organic rules). Keep it only if you hold the certificate; otherwise use the pouch-backed claims (natural, no preservatives, no artificial fillers, gluten-free, no GMO). | **YOU decide** |
| 4 | Claims with no proof on file: "Lab Tested", "tested and certified for international standards", "since 2012", "full farm-level traceability", "certified to FSSAI standards". Keep only what you can document (test reports, incorporation date). | **YOU decide** |
| 5 | About page said the products reach "kitchens worldwide / across the globe" — you deliver within India only. Changed to India. | DONE |
| 6 | Footer said "Bringing Authentic Organic Spices Since 2012". Replaced with the pouch tagline + FSSAI number. | DONE |
| 7 | Taglines from the pouch now used: tagline banner ("One Spice. Every Dish. Every Time." / "Rooted in Tradition. Rich in Flavor."), hero slide, footer. | DONE |
| 8 | Contact page had no phone number. Added phone, WhatsApp link, response time, clickable e-mail. | DONE |
| 9 | Terms page still mentioned the hidden Science Hub. Removed. | DONE |
| 10 | Refund / cart / checkout / terms said "no refund, no return / all sales final" while Google Merchant Center says defective-only returns. All rewritten to the same policy (replace/refund for damaged, wrong, defective within 48 h; no change-of-mind returns; opened/used packs not returnable). | DONE |
| 11 | Instagram in the footer is `heritage.spices.in`; pouch says `indian_heritage_spices`. The footer "Twitter" link is only a hashtag search. Tell me which handle is correct and whether to drop the Twitter icon. | **YOU** |
| 12 | Pouch artwork prints MRP ₹60 / ₹110; store price ₹55 / ₹105. Legal as long as selling price ≤ MRP. | OK |
| 13 | Blog post "Why Kerala Spices Are World Famous" (and mentions in two others) still contradicts the "partner farms" wording. Rename/rewrite in Admin → Blog. | **YOU** |
| 14 | All 15 internal links on the main pages return 200 (checked automatically). | OK |

## 3. Speed

Measured on the live site (Render free plan, Singapore DB):

| Page | Time to first byte |
|---|---|
| /about (no database) | ~0.4 s |
| /, /products, /blog, /faq, /product/1 | 1.0–1.5 s |

**Done in this round**
- Chat widget (Tawk.to, ~300 KB JS) now loads only after the first touch/scroll/key or 8 s after page load, not during page load.
- `animate.css` no longer blocks rendering (loaded asynchronously).
- Bootstrap CSS and JS now the same version (5.3.3); `preconnect` hints for the CDNs, fonts and product-photo host.
- First hero image marked `fetchpriority="high"` with width/height (faster largest-paint, no layout jump).
- Homepage hero photos are ~160–180 KB each (WebP); animated logo 210 KB.

**Still the biggest wins (in order)**
1. **Render free plan sleeps** after ~15 min idle (first visit then takes 30–60 s). Upgrade to the Starter plan (~$7/month) or ping `https://www.indianheritagespices.com/healthz` every 10 min with a free monitor (UptimeRobot / cron-job.org). *YOU.*
2. Start command must be `gunicorn app:app --workers 1 --threads 8 --timeout 60` (the log shows plain `gunicorn app:app`). *YOU.*
3. Product photos come from `i.ibb.co` at 499 px and different formats. Re-upload 800×800 WebP/JPEG (also required by Google Merchant). *YOU + LATER (I can host them locally).*
4. Large unused/oversized files in `static/images` (`science_notes_mockup.png` 760 KB, `organic.webp` 328 KB, a 327 KB chilli JPG, `quality.webp` 200 KB). Compress or delete if unused. *LATER.*
5. Font Awesome (full 6.4 CSS, ~100 KB + fonts) could be replaced by the ~10 icons actually used. *LATER.*
6. Each DB round trip is ~0.3 s; pages already batch queries and cache settings/blogs. Moving the database to the same region as Render (both Singapore) is already the case — keep it that way.

## 4. NimbusPost review

What the integration does today: serviceability + rates at checkout, order creation + shipment booking (reusing the courier order on retry), label fetch, cancel, tracking by AWB, and a signed webhook for status updates.

**Fixed now**
- **Weights were guessed from the price in three places** (rate quote, order create, admin booking) — a ₹55 pouch counted as 110 g. One helper `item_weight_kg()` now reads the size from the product name (+5 g packing) everywhere, so quote, order and courier booking agree.
- **Courier phone** was sent with the country code (e.g. `918999449765`, 12 digits) — now always the last 10 digits.
- **Webhook** only understood one flat payload shape; it now also reads wrapped payloads (`{"event":…, "data":{…}}`, `shipStatus`, nested `shipment`).

**Consider (not changed — needs your decision)**
1. **Rate shown ≠ courier booked.** The customer is charged the cheapest quoted rate, but booking does not pass a courier id, so NimbusPost may assign another courier. Either pass the quoted `courier_id` when booking or accept a small variance.
2. **Three different fallback shipping prices:** manual mode ₹40, API-failure fallback ₹40, "no rates" fallback ₹60, while Merchant Center says ₹35 (0–0.5 kg) and the feed sends ₹35. Pick one number per situation and set it in one place. Google's rule: the price shown on Google must not be lower than the checkout price.
3. **Weight slab:** five 100 g pouches = 0.525 kg, just over the 0.5 kg slab. Set Merchant Center's second slab (0.5–1 kg) to your real courier cost (e.g. ₹60).
4. **Warehouse id** is hard-coded (`WH-001`; `WH-002` if the name contains "Hyderabad"). Make it an environment variable (`NIMBUS_WAREHOUSE_ID`) and confirm it matches the pickup address in your NimbusPost panel.
5. **Parcel size** is fixed 15×10×5 cm. Measure a packed 5-pouch order; wrong dimensions cause weight disputes.
6. **Tracking history:** the tracking-by-AWB call returns only the latest event. For a full timeline, store every webhook event in a table and show it on the tracking page. *LATER.*
7. **Estimated delivery date** is never saved at booking; the tracking call returns `edd`. Save it and show "Expected by …" in My Orders and the shipped e-mail. *LATER.*
8. **Damaged/wrong items** (new policy): NimbusPost supports reverse pickups (`order_type: reverse`). Add an admin "Arrange return pickup" button so replacements are fast. *LATER.*
9. **NDR / failed delivery** and **RTO** statuses map to "shipped"/"rto" only; add an admin alert (Telegram) when a shipment is undelivered or returning. *LATER.*
10. Register the webhook URL `https://www.indianheritagespices.com/api/nimbus/webhook` in the NimbusPost panel and set `NIMBUS_WEBHOOK_SECRET` on Render (the endpoint rejects unsigned calls). *YOU.*
11. `/admin/test-nimbus-login` uses the old v1 login API and env vars `NIMBUS_EMAIL/NIMBUS_PASSWORD`; the live integration uses `NIMBUS_API_TOKEN/SECRET`. Safe to delete. *LATER.*

## 5. Google & payments checklist (status)

- Merchant Center: website verified + claimed (indianheritagespices.com) ✔ · shipping policy ✔ · return policy submitted (review up to 10 days) ✔ · product feed added; issue "missing shipping weight" fixed by the feed ✔ · **waiting for Google's initial review.**
- Still open: second shipping slab → real cost; replace 499 px photos; add customer-service contact in Merchant Center; Search Console "Request indexing" for /contact, /product/1, /product/2, /about.
- Google review link is built and hidden until `GOOGLE_REVIEW_URL` is set on Render (`https://g.page/r/CWy2LYLed-SVEAE/review`); optional `GOOGLE_RATING`, `GOOGLE_REVIEW_COUNT`.
- Payments: Razorpay already opens the GPay app on phones; UPI is free for orders under ₹2,000. A separate GPay integration adds work and no saving. Optional: two-button checkout (UPI/GPay first, cards second).

## 6. Suggested product description (replaces the database text; matches the pouch)

> Heritage Spices Garam Masala is a stone-ground blend of 14 whole spices, made for curries, dals, biryani, pulao and vegetables. One Spice. Every Dish. Every Time.
>
> **Ingredients:** Black/Green Cardamom, Black Stone Flower, Nutmeg, Cinnamon, Black Pepper, Fenugreek Seed, Coriander Seed, Clove, Black Cumin Seed, Himalayan Pink Salt, Red Chilli, Bay Leaf, Star Anise, Dry Mace.
>
> **Free from:** gluten, GMO ingredients, artificial ingredients and fillers, added preservatives.
>
> **How to use:** add ½ to 1 tsp towards the end of cooking.
>
> **Shelf life:** best before 18 months from packing. **Storage:** reseal the bag after opening; keep in a cool, dry, hygienic place.
>
> Net weight 50 g / 100 g. FSSAI Lic. No. 21521275000514. Manufactured & marketed by Indian Heritage Spices.

(Remove "stone-ground" if the blend is not stone-ground — only claim what is true.)

## 7. Owner action list (summary)

1. Verify the FSSAI number against the certificate (set `FSSAI_LICENSE` if different).
2. Update the product description (section 6) in Admin → Products.
3. Decide on "100% organic", "lab tested", "since 2012", "international standards" (keep only what you can prove).
4. Confirm the Instagram handle; decide about the Twitter icon.
5. Render: paid plan or health-check pings; start command with `--workers 1 --threads 8`; register the Nimbus webhook and set `NIMBUS_WEBHOOK_SECRET`; set `GOOGLE_REVIEW_URL`.
6. Merchant Center: second slab ₹; better photos; contact details; wait for review.
7. Rename/rewrite the Kerala blog post.
8. Re-generate pouch mock-ups with the spelling errors fixed if you use them in ads.
