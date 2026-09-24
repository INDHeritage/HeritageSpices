# Heritage Spices — Full Project Review

Scope: backend (`app.py` 3,149 lines, `nimbus_api.py`), database, deployment, and front end (44 templates + 9 partials, static assets). Reviewed 24 Sep 2026.

**How to read this:** every item says *where*, *why it matters*, and *what to do*. Effort: **S** = under 2 hours, **M** = about a day, **L** = several days.
Items marked ✅ *verified* were reproduced by actually running the code or measuring the page. Items marked ⚠ *not verified* are inferences I could not confirm from here.

---

## Status update (24 Sep 2026) — what has been fixed

| Item | Status |
|---|---|
| B1 Paid notes publicly downloadable | **Fixed.** Files moved to `private_content/` (not web-served); only the gated `/api/notes` route can read them. |
| Science Hub | **Hidden.** All `/science-hub*` and `/api/notes*` URLs return 404 to the public and the nav link is gone. Admins can still open it. Set `SCIENCE_HUB_ENABLED=true` on Render to bring it back. |
| B3 Payment verification not idempotent | **Fixed.** One `finalize_paid_order()` function; safe to call repeatedly; stock decremented atomically in the database; order-ownership check added. |
| B2 No Razorpay webhook | **Code done — needs 2 steps from you** (below). Also added an admin "Check Razorpay for missed payments" button that recovers lost payments today, without the webhook. |
| B4 Nimbus status mapping | **Fixed.** Courier statuses are mapped to shipped / out for delivery / delivered / rto / cancelled; unknown statuses are ignored; finished orders never move backwards; payload no longer logged. |
| B7 Security headers, compression, caching | **Fixed.** Headers added, gzip on (homepage HTML 54 KB → 14 KB), static files cached 7 days, notes never cached. Content-Security-Policy **not** added (needs a careful allow-list; do it separately). |
| Hardcoded admin email (×20) | **Fixed.** `ADMIN_EMAILS` env var (defaults to the current address). |
| DB idle-connection errors | **Handled** with connection recycling. (`pool_pre_ping` was tried and removed: it added a database round trip to every request.) |
| B9 Tests / CI / pinned dependencies | **Done.** 57 tests; all dependencies pinned. The GitHub Action file exists locally (`.github/workflows/tests.yml`) but could not be pushed: the GitHub token lacks the *workflow* permission. |
| Slugs / sitemap | **Fixed** for new posts; sitemap URLs now valid and complete. |
| Repo hygiene | **Done.** Customer CSVs, legacy uploads, empty logs and unused 22 MB video removed from the repo. |
| Front-end speed | **Partly done.** Logo 194 KB → 39 KB; hero slides 2–6 load after the page. |
| B8 customer messages | **Built, free plan** — e-mails + WhatsApp click-to-chat (see *Switch-on checklist* below). |
| Duplicate courier order on retry | **Fixed.** |
| Google One Tap sign-in, sign-in prompt on "Buy Now" for logged-out visitors | **Built, off by default** (`GOOGLE_ONE_TAP_ENABLED`). |
| Homepage: trust strip, "What will you cook?" picker, journal strip, mobile Shop/WhatsApp bar | **Live.** |
| Product pages (`/product/<id>`) with size links, price per 10 g, honest stock notes, real-reviews-only structured data, sitemap entries | **Built.** Cards link to them. |
| Admin orders page pagination (25/page, totals cover all orders) | **Built.** |
| Contact-form spam: 50 of 61 stored messages were one link-spam bot | **Fixed.** Time-trap, 3/hour limit, link filter, duplicate filter, optional Cloudflare Turnstile (`TURNSTILE_SITE_KEY` + `TURNSTILE_SECRET`); admin gets tick-and-delete tools; genuine messages now alert you on Telegram. |
| Referral updates | **Built.** E-mail + Telegram when a friend signs up and when a reward is earned; customer points history; admin page per customer (history, who referred whom, manual points adjustment). |
| Social-share image (`logo.jpg` did not exist) | **Fixed.** |
| B5 price/weight model, B6 Alembic baseline, guest checkout, content-security-policy | **Not done** — these need database changes or larger design work. Recommended next. |

**Two things only you can do:** (1) In Razorpay Dashboard → Settings → Webhooks, add `https://www.indianheritagespices.com/api/razorpay/webhook` with events `payment.captured` and `order.paid`, choose a secret, and set the same value as `RAZORPAY_WEBHOOK_SECRET` on Render. (2) Rotate the GitHub tokens / NimbusPost credentials that were shared in chat. Old customer files are still in git *history* (only removed going forward) — make sure the GitHub repo is private.

---

## Remaining work — second review, after the fixes went live (24 Sep 2026)

Everything below was checked against the live site or by running the code, not assumed.

### Switch-on checklist for what was just built (all off/harmless until you do these)
| Feature | What to set on Render → Environment | Also needed |
|---|---|---|
| **Customer e-mails** (confirmed / shipped / delivered, referral updates) | **On Render's free plan use the free HTTPS relay** (Render blocks SMTP there): `EMAIL_WEBHOOK_URL` + `EMAIL_WEBHOOK_SECRET`. Setup steps are in `docs/email_relay.gs` (a 5-minute Google Apps Script sent from your own Gmail, ~100 mails/day). On a paid plan you can instead use `SMTP_HOST`, `SMTP_USER`, `SMTP_PASSWORD`. | Click **Send Test E-mail** on the admin dashboard. Mails go to the customer's Google-account address. |
| **WhatsApp us** button + mobile bottom bar | `WHATSAPP_NUMBER` = your number, e.g. `8459593058` (10 digits, or 91 + 10 digits) | Bar shows "Shop now" only until this is set. |
| **WhatsApp customer** (admin) | nothing — works now | Opens WhatsApp with a ready message; you press Send. |
| **Google One Tap** popup | `GOOGLE_ONE_TAP_ENABLED=true` (needs the existing `GOOGLE_CLIENT_ID`) | In Google Cloud Console → APIs & Services → Credentials → your OAuth client → **Authorized JavaScript origins**, add `https://www.indianheritagespices.com`. Then test in a browser signed in to Google. |
| **Contact-form bot check** (optional, free) | `TURNSTILE_SITE_KEY` + `TURNSTILE_SECRET` from a free Cloudflare Turnstile widget (dash.cloudflare.com → Turnstile → add site `www.indianheritagespices.com`) | Catches the spam that has no links (e.g. the "what is your price" bot). |
| Homepage trust strip, "What will you cook?" picker, journal strip | nothing — live | Edit wording in `templates/partials/`. Confirm the "Lab tested / Equinox Labs" line matches your reports. |

### Why pages are slow, and what was done ✅ measured
Live timings (first byte): pages with **no** database work ≈ **0.3 s**; `/faq` (4 queries) ≈ **1.5 s**; homepage (9 queries) ≈ **3.0 s**. So every database query costs about **0.3 s**. The database is in **Singapore** (Supabase `ap-southeast-1`); if the Render service is in the US/Europe (the default), each query crosses an ocean.
- **Done in code:** settings are cached for a minute (they caused 7 of the homepage's 9 queries); visit logging skips crawlers/health checks/HEAD and runs in the background; the unused open `/track-visit` endpoint and the `/test-csrf` debug page were removed; my earlier `pool_pre_ping` setting (one extra round trip per request) was removed. Result: homepage 9 → 2 queries (only 1 blocks the visitor), `/faq` and `/about` 4 → 0. Expect the homepage to drop from ~3 s to roughly 0.6 s. A test now fails if a page exceeds its query budget.
- **Still needed (dashboard):** put the web service and the database in the **same region** (item A1) — this makes each query ~0.02 s and is the biggest remaining speed win.

### A. Do these yourself (dashboards, no code) — in this order
| # | What | Why |
|---|---|---|
| A1 | **Render region:** check *Settings → General → Region*. If it is not **Singapore**, create a new Web Service from the same repo in Singapore, copy the environment variables, then move the custom domain to it. (Region cannot be changed on an existing service.) | Removes the ~0.3 s per query. Single biggest speed gain. |
| A2 | **Start command** (*Settings → Deploy*): `gunicorn app:app --workers 1 --threads 8 --timeout 60` (the Procfile now says the same, but Render uses its own setting). Keep **one worker** — the rate limiter and settings cache live in memory. | Today it is 1 *sync* worker with a 30 s timeout: one slow call (payment, courier, image upload) freezes the whole site, and shipping (two 15 s courier calls) can be killed mid-way. |
| A3 | **Health Check Path** (*Settings → Health Checks*): `/healthz`. | Lets Render restart a stuck instance and deploy without downtime. |
| A4 | **Plan:** the service shows **Free**. Free instances sleep after ~15 min idle, so the next customer waits 30–60 s for a page (and a paying customer's checkout can stall). Consider *Starter* (~$7/month). | Biggest conversion risk for a store. |
| A5 | **Verify the Razorpay webhook:** after the next real order, open Razorpay → Webhooks and check the delivery shows **200**. (403 = the secret on Render and Razorpay differ.) | Confirms missed-payment protection works. |
| A6 | **Credentials & repo:** rotate the GitHub tokens/NimbusPost password shared in chat; confirm the GitHub repo is private (old customer CSVs remain in its history). | Security hygiene. |
| A7 | **Supabase:** confirm backups/point-in-time recovery are on and re-run the Security Advisor. | Data safety. |

### B. Code work still open (needs your decision, an account, or database changes)
1. ~~Customer notifications~~ **Built (free plan)** — confirmed / shipped / delivered e-mails over SMTP, plus a one-tap "WhatsApp customer" button in admin (see *Switch-on checklist*). The paid WhatsApp API is deliberately **not** used: Meta charges only ~₹0.115 per utility message (+18% GST) but providers add a **₹999–₹9,999/month platform fee**, which is poor value below a few hundred orders a month. Revisit at ~200+ orders/month.
2. ~~Courier retry creates a duplicate order~~ **Fixed** — the courier order id is saved as soon as it is created, and "Ship" re-books that same order instead of creating another (falls back to a fresh order if the courier no longer knows it).
3. **Individual product pages** (SEO, sharing, reviews) and a size selector merging the 50 g / 100 g products.
4. **Guest checkout / phone-OTP login** (login is Google-only).
5. **Database model:** price stored as text; parcel weight guessed from price; migrations not tracked (Alembic baseline). Needs planned production SQL.
6. **Content-Security-Policy** header (needs a careful allow-list of Razorpay, Tawk.to, CDNs, imgbb).
7. **Order list pagination** (`/admin/orders` still loads every order).
8. Small clean-ups: unused `static/firebaseConfig.js`; root-level one-off scripts; a real logging/Sentry setup; deprecated `datetime.utcnow()`/`Query.get()` calls; `blog` deletion "password" is auto-filled so adds no protection.

### C. Compliance to have checked by someone qualified (flags, not legal advice)
- "100% organic" and "tested and certified for international standards" wording vs. your actual certificates.
- The FSSAI licence number is shown on the Products page (good) but not in the footer/receipt, where it is normally expected too; the About page says "certified to FSSAI standards" — make sure that wording is accurate. There is **no GSTIN** shown anywhere, and the receipt is a simple receipt, not a GST invoice.
- The site records visitor IP addresses and signs users in with Google: confirm the Privacy Policy describes this (India's DPDP Act 2023).

### D. Note on test data
While checking pages from my machine earlier in this project, a small number of homepage requests (roughly 10–20) were recorded as visits in your real database, out of ~5,000. Harmless, but the visit count is very slightly inflated.

---

## SEO audit (24 Sep 2026) — measured on the live site

**Verdict: the foundations were fine, but there were real defects that hurt search results. They are now fixed in code; the growth work below needs you.**

**Already good (verified):** HTTPS everywhere; `http`/non-`www` redirect to `https://www`; genuine 404s (not "soft" 404s); Brotli compression; mobile viewport; `lang="en"`; every image has alt text; canonical tag on every page; sitemap with 18 URLs that all return 200; favicon; robots meta `index, follow`; no analytics/tracker bloat.

**Defects found and fixed**
| Problem found | Why it mattered | Fix |
|---|---|---|
| Homepage had **no H1** (12 H2s) | Search engines use the H1 to understand the page | First hero heading is now the H1 (same look) |
| `/products` **structured data was invalid JSON** (a product description containing a line break broke it) | Google could not read any product data there | Rebuilt safely; full `Product` data lives on each product page |
| `/products` had two extra H1s that were rating numbers ("0.0") | Confuses page structure | Now ordinary text |
| **9 of 10 pages shared one generic meta description** (187 chars, truncated) and one generic social title | Every result looks identical; low click-through | Unique title + description per page (all ≤ 160 / ≤ 65 chars, enforced by tests) |
| Descriptions promised "Goda Masala, Turmeric" — **products you do not sell** | Misleading | Removed |
| Organization data pointed to a **logo file that doesn't exist** (`logo.jpg`), the non-www URL, a Twitter *search* link | Broken knowledge-panel data | Real logo, `www` URL, only the Instagram profile, added postal address |
| Social share image missing (same `logo.jpg`) and tiny | WhatsApp/Facebook/X previews had no picture | New 1200×630 `og-default.jpg`; each page's social tags now follow its own title/description; posts use `og:type=article` |
| Blog URLs contained `:` and curly quotes; canonical and sitemap disagreed | Ugly, fragile, mixed signals | Clean URLs (e.g. `/blog/garam-masala-the-secret-behind-the-soul-of-indian-cooking`); old links **301-redirect**; sitemap lists clean URLs |
| Blog posts had **no Article/Breadcrumb markup**, titles up to 107 chars | Missed rich results; titles cut off | Article + BreadcrumbList data; long titles no longer get a suffix |
| `/faq` had no FAQ markup | Missed FAQ rich results | FAQPage data generated from the visible questions (always in sync) |
| `/products/` and `/blog/` (trailing slash) returned 404 | Lost link value, duplicate-URL risk | 301 to the real URL |
| robots.txt only blocked `/admin/` | Crawlers wasted time on cart/checkout/login/API | Private areas disallowed |
| Sitemap had no `lastmod` | Slower re-crawl of new posts | Added for blog posts |

**Measured, not fixed by code:** in a real browser the homepage transferred ~1 MB in 28 requests and took ~3.6 s to fully load, with ~2.3 s before the first byte (includes the non-www→www redirect and the Render↔Supabase distance in section A1). Speed is a ranking factor; moving Render to Singapore (A1) is the biggest lever. (Google's free PageSpeed quota was exhausted when I tried, so run https://pagespeed.web.dev yourself for the official score.)

**Could not be verified from here — check these yourself (10 minutes each)**
1. **Google Search Console** — add `https://www.indianheritagespices.com` as a "URL prefix" property (verify via the HTML tag or DNS), submit `https://www.indianheritagespices.com/sitemap.xml`, then use *URL Inspection → Request indexing* for the home page, `/products`, both product pages and your best 3 blog posts. This also shows whether Google has indexed you at all — a brand search I ran did not surface the site, which is normal for a young store but worth confirming here.
2. **Bing Webmaster Tools** — import from Search Console (one click).
3. **Google Business Profile** — free listing for "Heritage Spices, Sindewahi" with your phone and website; helps brand searches and Maps.
4. Ask the Instagram account (`heritage.spices.in`) to link to the site in its bio.

**Growth work that actually moves rankings (content, not code)**
- **Target these searches:** "garam masala online", "buy garam masala", "Kerala spices online", "garam masala 100g", plus the brand "Heritage Spices". Use the words naturally in product descriptions and blog titles.
- **Make the two product pages different.** 50 g and 100 g share the same description — search engines treat that as duplicate content. Give each a unique paragraph and add *ingredients, how to use, storage, shelf life (18 months, printed on the pouch), FSSAI number*.
- **Blog:** 7 posts of 500–700 words. Aim for 900–1,300 words with sub-headings (two posts have none), a photo, and a "Buy our Garam Masala" link to a product page. Ideas with real search demand: *garam masala vs curry powder*; *how to use garam masala (dish by dish)*; *how to store spices*; *whole vs ground spices*; *Kerala spice regions*; *homemade vs packaged garam masala*; *garam masala recipe*; *turmeric milk benefits (avoid medical claims)*.
- **Reviews:** approved reviews add star ratings in results. Ask each buyer for one after delivery (the delivered e-mail is a good place for the link).
- **Backlinks:** list the products on Meesho/IndiaMART/JustDial with a link to the site; ask food bloggers for a review copy.
- Be careful with claims ("100% organic", "tested for international standards") — search engines and regulators both care (see section C).

## Feature plan 1 — "Continue as <your Google account>" popup (Google One Tap)

**What it is.** The popup you see on many sites is **Google One Tap**. If the visitor is already signed in to Google in that browser, Google shows a small card in the corner with *their own* account name, photo and a "Continue as …" button. One tap signs them in — no redirect, no password. It is free and uses the same Google OAuth client you already have (`GOOGLE_CLIENT_ID`).

**How it works (technical).**
1. The page loads Google's script (`accounts.google.com/gsi/client`) and calls `google.accounts.id.initialize({client_id, callback})` then `prompt()`.
2. Google shows the popup. When the visitor taps, Google hands *our JavaScript* a signed **ID token** (a JWT) — no page redirect.
3. Our JavaScript POSTs that token to a new endpoint, e.g. `POST /auth/google-one-tap` (sending our normal CSRF header).
4. The server **verifies the token with Google's public keys** (library `google-auth`: checks signature, expiry, that the audience equals our client id, and `email_verified`), then creates the same login session as today.
5. The page reloads or continues what the visitor was doing.

**What has to change in this project**
| Step | Where | Effort |
|---|---|---|
| Add `https://www.indianheritagespices.com` (and `http://localhost:5000` for testing) under **Authorized JavaScript origins** for the existing OAuth client in Google Cloud Console. No new secret needed. | Google Cloud Console | S |
| Move the post-login logic (save user, link referral code, issue the welcome coupon, coupon reminders) out of `/auth` into one shared function, so the current button and One Tap behave identically. | `app.py` (`/auth`, `save_user`) | S |
| New endpoint `/auth/google-one-tap`: verify token, call the shared function, return JSON. Add `google-auth` to `requirements.txt`. Rate-limit it. | `app.py` | M |
| Load the script and call `prompt()` **only for logged-out visitors**, after ~4 s or first scroll, and **not** on checkout, admin, legal pages or `/login`. | `templates/base.html` | S |
| Combine with the existing "Sign up & get X% off — Claim Offer" popup: show One Tap first; only if Google reports it cannot display (visitor not signed in to Google, cool-down after dismissals) fall back to the current popup. Never show both at once. | `base.html` | S |
| **Just-in-time sign-in:** when a logged-out visitor taps "Buy Now / Add to cart", trigger the prompt instead of sending them to the login page, then continue adding the item. Likely the biggest conversion gain. | `products.html`, `partials/products.html`, `cart` JS | M |
| Tests: token with wrong audience / unverified email / expired is rejected; new user gets welcome coupon; referral code links; existing user does not get a second coupon; admin email still admin. Ship behind an env flag `GOOGLE_ONE_TAP_ENABLED`. | `tests/` | M |

**Limits to know about (so nobody is surprised)**
- It only appears if the visitor is **signed in to Google in that browser**. If not, they simply see your normal "Login with Google" button — keep that button.
- After a visitor closes the popup, Google **pauses it for a while** (longer after repeated closes). This is Google's rule and cannot be overridden.
- **In-app browsers** (opening your link inside Instagram, Facebook or some WhatsApp views) often block Google sign-in altogether. A large share of Indian mobile traffic arrives this way — another reason guest checkout / phone-OTP login (item B4) is worth doing.
- Mention Google sign-in and what data you keep in the Privacy Policy.

**Effort:** about one working day including tests. **Risk:** low if behind the flag; login stays exactly as today for anyone the popup does not reach.

---

## Feature plan 2 — Make the homepage interactive, with content that earns trust

### Where the site really stands (checked against live data)
2 products · **0 customer reviews** · 7 blog posts · 5 users · 2 paid orders. This is a **new store**: visitors don't know the brand yet, so the homepage's job is to answer *"Can I trust this? What do I do with it? Is it worth ordering 2 pouches?"* — not just to look good. Interactivity should serve those three questions.

**Rule for all content:** only show claims and numbers you can prove. **Do not add invented testimonials, ratings, "1,000+ customers" or fake urgency** — besides destroying trust when spotted, fake reviews and fake urgency can breach Indian consumer-protection and advertising rules (have your advisor confirm the specifics). The six banner photos are decorative (some are AI-generated); don't caption them as "our farm" unless they are.

### Proposed section order for the homepage
| # | Section | What it does | Content source |
|---|---|---|---|
| 1 | **Hero** — cut to 3 slides, one clear button each, plus a one-line promise | Faster load, clearer message | existing photos |
| 2 | **Trust strip** (one row of 4 icons) | Answers "can I trust this?" instantly | **FSSAI Lic. No. 21521175000514** (already on Products page), "Lab tested" (only if the Equinox Labs report exists — show it), "Secure payment by Razorpay", "Freshly packed in Sindewahi" |
| 3 | **"What will you cook?" picker** (interactive) — tap a dish (Paneer, Chicken curry, Biryani, Dal, Sabzi) → shows *when to add it, how many spoons, one tip* and a "Add Garam Masala" button | Answers "what do I do with it?"; turns browsing into a cart add | short copy written from your blog posts / kitchen knowledge |
| 4 | **Featured products** — size toggle 50 g / 100 g, "₹ per 10 g" value, honest low-stock note *only when `stock` is actually low*, "why minimum 2 pouches?" tooltip, free-shipping progress bar ("Add ₹X more for free shipping") | Answers "is it worth it?"; raises order value | product + stock data |
| 5 | **Farm-to-pouch story** — 5-step scroll timeline (source → sun-dry → grind → test → pack) with a real photo per step and animated counters (e.g. "Since 2012" → years running) | Trust + brand story | your real photos; only true numbers |
| 6 | **Recipes strip** — 3 newest blog posts as cards, "Cook with Heritage Garam Masala" | Uses the 7 posts you already have; helps SEO | existing blog |
| 7 | **Proof section** — until real reviews exist: batch/lab report and FSSAI certificate viewer ("See our lab report"); then swap in real reviews. Add "Be the first to review" prompt | Honest social proof | certificates; post-delivery review request (needs customer messages, item B1) |
| 8 | Refer & Earn · FAQ · Wholesale | keep; add FAQs: shipping time, shelf life (pouch says 18 months), storage, why min 2 pouches, returns | existing |

### Interactive features, ranked by value for effort
1. **Sticky mobile bottom bar** — "Shop now" + "Order on WhatsApp". Most Indian shoppers are on phones; WhatsApp ordering also catches people who won't sign in. **S**
2. **"What will you cook?" dish picker** (row 3 above). **M**
3. **Add-to-cart micro-animation + mini-cart drawer** (item flies to cart, no page jump) and the **free-shipping progress bar**. **M**
4. **Batch checker via the QR on your pouches.** Your pouch already prints a *Batch No.* and the site already records QR scans (`ProductScan`, `/admin/qr-codes`). Make the QR open a page: enter/scan batch → packing date, lab report, recipes, "leave a review". Few competitors do this; it builds trust and becomes the review-collection channel. **M–L**
5. **Animated counters and scroll reveals** using the Motion library you already load. **S**
6. **Spice heat/flavour meter** on each product (mild ▸ hot, aroma, best-used-for) as small icons. **S**
7. **Exit-intent or timed offer** — only the real welcome coupon you already issue; show once; never fake countdowns. **S**
8. **Video:** one 10–15 s vertical clip (grinding / packing) as a muted loop in the story section, hosted on YouTube or a CDN — **not** in the repo (an unused 22 MB video was just removed from it). **M**

### Performance guardrails (the site was just made faster — keep it that way)
Lazy-load everything below the first screen; keep each image ≤ ~150 KB (WebP); no new heavy libraries (use what is loaded: Bootstrap + Motion); add nothing that runs a database query on the homepage without caching (each query costs ~0.3 s until the region move in A1 is done). Re-measure after each addition.

### Copy suggestions (edit to your voice; verify every claim)
- Hero promise: *"Fresh-ground Kerala spices, packed in Maharashtra, at your door."* (only if accurate)
- Trust strip: *"FSSAI Licensed · Lab Tested · Secure Payments · Ships in 24 h"* — keep only what is true.
- Dish picker tip: *"Add Garam Masala in the last 2 minutes of cooking — heat dulls its aroma."*
- Product note: *"Minimum 2 pouches so shipping stays affordable — ₹37 covers both."* (only if that is the reason)

**Suggested order of work:** (1) One Tap, just-in-time sign-in and the mobile bottom bar (fast conversion wins) → (2) trust strip + dish picker + product size toggle → (3) story timeline + recipes strip → (4) batch-checker QR page (needs customer messaging for reviews).

---

## 1. Summary

The site works and the core flow (browse → pay → ship → track) is real and in production. The main risks are not visual — they are **money and content leaks**:

| # | Priority | Problem | Effort |
|---|----------|---------|--------|
| 1 | **P0** | Paid Science Hub notes can be downloaded by anyone, free, by URL ✅ | S |
| 2 | **P0** | Payment is only confirmed by the customer's browser — no Razorpay webhook. A closed tab after paying = charged customer, no order ✅ | M |
| 3 | **P0** | Payment verification is not idempotent (double call double-decrements stock/points) ✅ | S |
| 4 | **P1** | Shipping status from NimbusPost webhook is stored raw and will break admin/customer screens ✅ | S |
| 5 | **P1** | No security headers, no compression, no long-term caching ✅ | S |
| 6 | **P1** | No automated tests, no CI, unpinned dependencies | M |
| 7 | **P1** | Customer never receives an email/WhatsApp (order confirmed, shipped, delivered) | M |
| 8 | **P2** | Database schema managed by `create_all()`; Alembic has 1 migration and is effectively unused | M |
| 9 | **P2** | Homepage loads ~1.7 MB across 28 requests; all 6 hero images load eagerly | S |
| 10 | **P2** | No individual product pages → weak SEO and no shareable product links | L |

**Fix items 1–4 first.** They are small and they are the difference between "site" and "safe to run a business on."

---

## 2. Backend

### 2.1 Critical (P0)

**B1. Paid Science Hub notes are publicly downloadable** ✅ verified
- Where: `app.py` ~1611 (`SCIENCE_CONTENT`), ~1982 (`get_notes_page`). Files live in `static/notes/` (298 PNGs).
- Problem: the `/api/notes/...` route correctly checks purchase (returns 401 when logged out), but Flask also serves everything under `/static/` with no check. I requested `/static/notes/page_1.png` and `/static/notes/page_231.png` with no login and got `200` with the full page image. Anyone can script-download all 298 pages. `static/preview_chapter1.pdf` (4.6 MB) is meant to be public; the numbered pages are not.
- Fix: move the paid files out of `static/` to a private folder (e.g. `private_content/notes/`), serve only through the gated route with `send_from_directory`. Keep only the intended preview public. Be aware the images are already in git history, so treat existing pages as exposed; consider re-exporting with a watermark (buyer email in a corner) if piracy matters.

**B2. No server-side payment confirmation (Razorpay webhook missing)** ✅ verified
- Where: `app.py` ~2479 `verify_checkout_payment`, ~1731 `verify-payment` (science).
- Problem: an order becomes `paid` only when the customer's browser calls `/checkout/verify-payment`. If the customer pays by UPI and the app switches, the network drops, or the tab closes before the callback, Razorpay has the money and your database shows `pending`. There is no webhook to catch that.
- Fix: add `POST /api/razorpay/webhook` (CSRF-exempt, verify `X-Razorpay-Signature` with your webhook secret), handle `payment.captured`/`order.paid`, and run the same "mark paid" logic. Put that logic in one function used by both the browser callback and the webhook. Also add an admin view listing `pending` orders older than 30 minutes so nothing is missed. Register the webhook in the Razorpay dashboard.

**B3. Payment verification is not idempotent** ✅ verified by reading the code
- Where: `app.py` ~2479–2560.
- Problem: there is no `if order.payment_status == 'paid': return` guard. A double click, a retried request, or (once B2 exists) browser callback + webhook both firing will decrement stock twice, write the points debit twice, and re-send the Telegram alert. Stock is also updated with `product.stock - qty` without a row lock, so two simultaneous orders can oversell.
- Fix: early-return if already paid; wrap in a transaction and lock the order row (`with_for_update()`); use an atomic `UPDATE product SET stock = stock - :q WHERE id = :id AND stock >= :q`.

### 2.2 Important (P1)

**B4. NimbusPost webhook writes raw status text** ✅ verified by reading
- Where: `app.py` ~2976 `nimbus_webhook`.
- Problem: `order.shipping_status = status.lower()`. Your screens only understand `processing / shipped / delivered / cancelled` (`admin_orders.html` uses `== 'shipped'`, `my_orders.html` only branches on those four). The first "in transit" or "pickup_pending" update will make the order show as **Processing** to the customer and will make the **Track / Receipt / Label buttons disappear** in admin. It also prints the full payload (customer PII) to logs. ⚠ Not verified whether the webhook is currently registered in NimbusPost; if it isn't, this is dormant, but it will trigger the day you enable it.
- Fix: map courier statuses to your own small set (`shipped`, `out_for_delivery`, `delivered`, `rto`, `cancelled`) in one function; store the raw courier text in a separate column (`courier_status`). Stop printing payloads. Also register the webhook and store the last event so the tracking page doesn't call NimbusPost live on every view.

**B5. Product price is a string; weight is guessed from price**
- Where: `Product.price = db.String(100)` (~150); `int(float(item.product.price))` repeated in cart/checkout; weight rules `price <= 45 → 0.06 kg else 0.11 kg` in `ship_order`, `calculate_shipping`, `create_checkout_order`.
- Problem: changing a price silently changes the parcel weight sent to the courier, which changes the shipping cost. Any non-numeric price string crashes the cart. Orders already use integer paise correctly, so the model is inconsistent.
- Fix: migrate `price` to `Integer` (paise), add `weight_grams` to `Product`, and compute shipping from that. One helper `cart_weight(items)` instead of three copies.

**B6. Schema changes are manual**
- Where: `app.py` ~393 and ~3145 (`db.create_all()`), `migrations/` (1 version).
- Problem: `create_all()` never alters existing tables, so every new column has needed hand-run SQL in Supabase. Flask-Migrate is installed but the history does not reflect the real schema, so `flask db migrate` would produce a large, risky diff.
- Fix: snapshot the current production schema as a baseline migration (`flask db stamp` after generating from the live DB), then use `flask db migrate/upgrade` from now on; run `upgrade` in the deploy step. Remove `create_all()` from import time.

**B7. Missing security hardening** ✅ verified (response headers checked)
- No `Content-Security-Policy`, `X-Frame-Options`, `X-Content-Type-Options`, `Strict-Transport-Security`, `Referrer-Policy`.
- Fix: add `flask-talisman` (or an `after_request` hook). CSP will need allow-listing for jsDelivr, cdnjs, Google Fonts, Razorpay, Tawk.to, imgbb — start in report-only mode.
- Admin is identified by one hardcoded email string repeated ~20 times in `app.py`, plus a second "admin password" that is written into a hidden form field in the admin's own page (`admin_blog.html:51`, `blog_detail.html:93`), so it adds no real protection. (I confirmed it does **not** leak to anonymous visitors.) Fix: `ADMIN_EMAILS` env var + `is_admin` helper; delete the fake password step or make it a real re-authentication.
- Rate limiter is an in-memory dict (`_redeem_rate_limited`): resets on every deploy and is per-process. Fine at one worker; move to `Flask-Limiter` with Redis/DB if you scale.

**B8. Customers get no messages**
- Only the admin gets a Telegram alert. There is no order confirmation, shipped, out-for-delivery, or delivered message to the customer. This drives "where is my order?" support load.
- Fix: transactional email (Resend/Brevo/SES) on paid + shipped + delivered, with the AWB and tracking link; WhatsApp (Razorpay/Interakt/Gupshup) is even better for India. Trigger from the same status function as B4.

**B9. No tests, no CI, unpinned dependencies**
- `requirements.txt` pins Flask but leaves `Flask-SQLAlchemy`, `Flask-Migrate`, `Flask-WTF`, `psycopg2-binary`, `razorpay`, `qrcode`, `bleach` unpinned, so a redeploy can silently pick up breaking versions. `bleach` is deprecated (use `nh3`).
- Fix: pin all versions (`pip freeze` from a working environment). Add `pytest` with ~15 tests for the things that cost money if they break: cart totals, coupon/points math, payment verification, shipping weight, access control. Add a GitHub Action that runs them on every push, before Render deploys.

### 2.3 Improvements (P2)

- **Structure:** `app.py` is one 3,149-line file with models, routes, helpers, and admin. Split into Flask blueprints (`shop`, `checkout`, `admin`, `science`, `blog`, `api`) and a `models.py`. Do this *after* tests exist.
- **Logging:** 13 `print()` calls and 12 `except Exception` blocks. Use Python `logging` and send errors to Sentry (free tier) so you learn about failures before customers tell you.
- **Homepage does avoidable DB work:** `index()` writes a `Visit` row synchronously on every load and runs ~4 queries (3 `get_setting` + products). Cache settings for 60 s; drop or batch visit logging; count bots out (the dashboard's "5,061 visits" almost certainly includes crawlers and spam bots — add a user-agent filter or use Plausible/GA).
- **DB connection:** ✅ verified not set anywhere in `app.py`. Add `pool_pre_ping=True` and a pool recycle time in `SQLALCHEMY_ENGINE_OPTIONS` (Supabase drops idle connections; without this you occasionally get a one-off 500 after a quiet period).
- **Deploy config is inconsistent:** `Procfile` runs `gunicorn app:app` (1 sync worker, 30 s timeout); `Dockerfile` runs 1 worker + 8 threads with `--timeout 0`. Pick one for Render and set workers/threads/timeouts deliberately (`--workers 2 --threads 4 --timeout 60`).
- **Slug generation:** blog slugs are built with `title.lower().replace(' ', '-')`, producing URLs with colons, curly apostrophes and trailing hyphens (visible in `sitemap.xml`). Use a real slugify and XML-escape sitemap URLs.

### 2.4 Repository hygiene & secrets

- **Customer data is in git:** `visits.csv` (616 rows: IP addresses, user agents, emails), `users.csv`, `customer_details.csv` are tracked. ⚠ I could not check whether the GitHub repo is private — confirm it is, and run `git rm --cached` on these files (plus `static/uploads/*`, 8 legacy images).
- **Dead weight:** `dist.zip` (44 MB, gitignored but on disk), root scripts (`fix_encoding.py`, `final_*_fix.py`, `test_fix_json.py`, `copy_notes.py`, `create_tables.py`, `db_check.py`, `migrate_csv_to_db.py`), five empty `*.txt` logs, `commit.bat`, `scratch/`, `web/`, `nimbus_v2_spec.json` (260 KB), `.git` is 209 MB (22 MB `cinematic-spices.mp4` is tracked and **not referenced by any template**). Move the useful scripts to `scripts/`, delete the rest. Large media belongs in object storage, not git.
- **Credentials shared in chat:** two GitHub personal access tokens and a NimbusPost account password/old API key appeared in this project's conversation and in a screenshot of an email. **Revoke/rotate them** (GitHub → Settings → Developer settings; NimbusPost → API keys) and prefer a credential helper or SSH key over a token embedded in the git remote URL.

---

## 3. Front end

### 3.1 Performance ✅ measured on the homepage

| Metric | Now | Target |
|---|---|---|
| Requests | 28 | < 20 |
| Transferred (decoded) | ~1.7 MB | < 800 KB |
| Images | 10 images, 1.17 MB | lazy-load below-the-fold; 6 hero slides → load slide 1 only |
| Static caching | `Cache-Control: no-cache` | `max-age=31536000, immutable` on hashed assets |
| Compression | none (HTML 54 KB uncompressed) | gzip/brotli |
| Third parties | jsDelivr, cdnjs, Google Fonts, Tawk.to (3 hosts), imgbb | fewer / deferred |

Specific actions:
1. **Hero carousel** (`partials/hero_carousel.html`): I extended it to 6 slides and every image is fetched on page load (~1.1 MB). Give slides 2–6 `loading="lazy"` (or swap `src` on first slide change), add `width`/`height` to stop layout shift, and consider cutting to 3–4 slides — six rotating banners with near-identical "Shop Now" buttons dilute the message. **S**
2. **`logo.png` is 194 KB** for a ~40 px-high header logo, loaded on every page. Export a small WebP/SVG (≈10 KB). **S**
3. **Enable `Flask-Compress` and long cache headers for `/static/`** (`SEND_FILE_MAX_AGE_DEFAULT`), and version file names (or `?v=`) so updates still propagate. **S**
4. **Tawk.to chat** loads its own scripts on every page and, on mobile, the chat bubble and its "Let's chat" pop-ups overlap the cookie banner and page CTAs (visible in screenshots). Load it after first interaction/idle, hide it on checkout/admin (already hidden on some), and make the cookie banner smaller. **S**
5. **Inline CSS/JS:** `base.html` is 28 KB with 4 `<style>` blocks and each partial carries its own `<style>`. Move to one cached `app.css`. **M**
6. **Self-host Bootstrap/Font Awesome subsets** or load only the icons used (Font Awesome full CSS is ~100 KB+). **M**
7. Measure on Render, not localhost: my local first-load was slow because the dev server was cold. ⚠ Check production TTFB — if you're on a free instance, cold starts after idle will dominate; a paid instance or a cron ping fixes that.

### 3.2 Homepage design & conversion

What's there now: hero carousel → mission carousel → featured products (2 items) → tagline banner → refer & earn → FAQ → wholesale form. Suggested changes, in order of impact:

1. **Trust bar directly under the hero** (one row of 4 icons): *Lab tested (Equinox Labs is already printed on your pouch)* · *FSSAI licensed* · *Secure payments (Razorpay)* · *Ships from Maharashtra/Hyderabad*. Shoppers of a new brand decide on trust in seconds. **S**
2. **Social proof near products:** star rating and 2–3 real customer reviews (you already have a review system — surface approved reviews on the home page). **S**
3. **Products section:** you sell 50 g and 100 g as two separate products. Merge into one product with a size selector, show "₹/10 g" value, and add a combo ("2×50 g + 1×100 g") — this lifts order value. Add a visible "free shipping over ₹X" threshold message; shipping (₹37 on a ₹110 order) is your biggest checkout friction. **M**
4. **One clear message per section:** "Kerala farms" appears in the hero, mission, tagline and footer copy. Vary the message (purity/testing, freshness, farmer story, how it's ground).
5. **Mobile first:** most Indian traffic is mobile. Add a sticky bottom bar ("Shop Now" / "WhatsApp us"), and check that the cookie banner + chat bubble don't cover the buy button.
6. **Use the good assets you already have:** a short farm-to-pouch story with the real photos, plus a recipe strip linking blog posts ("Use our Garam Masala in…").
7. **Compliance check on claims:** "100% organic" and "tested and certified for international standards" appear in the hero/mission copy. In India, "organic" claims generally need organic certification (NPOP/Jaivik Bharat) and printed licence numbers. Have someone qualified confirm the wording matches your certificates. (This is a flag, not legal advice.)
8. Keep the new tagline banner, but consider making the button primary green like the rest of the site so there are not three competing button styles (green, amber, maroon outline).

### 3.3 SEO

- No individual product pages exist (`/products` only; there is no `GET /product/<id>`). Each product needs its own URL, title, description, photos, price, reviews and `Product` JSON-LD — this is the single biggest SEO gain and also enables sharing on WhatsApp. **L**
- Sitemap lists only static pages + blogs, has no `<lastmod>`, and blog URLs contain invalid characters (see 2.3). Add product URLs after the above. **S**
- Product images come from `i.ibb.co` (third-party free host). Move to your own storage/CDN (Cloudinary, Supabase Storage, S3) so images can't disappear or be hotlink-blocked. **M**
- ✅ Good: all 26 `<img>` tags already have `alt` text; robots.txt and sitemap exist; structured data is present.

### 3.4 Accessibility & UX

- Carousel: it auto-rotates every 5 s, so add a visible pause/play button (WCAG requires a way to stop auto-moving content) and visible focus rings; slides already use `alt` correctly.
- The three overlapping floaters (chat bubble, cookie banner, back-to-top) need spacing on mobile.
- Forms: keep the honeypot; add `autocomplete` attributes on checkout address/phone fields; validate PIN (6 digits) and phone (10 digits) on the client too.
- Login is Google-only. Many customers (and older buyers) won't have or want to sign in to buy spices. Offer **guest checkout** with phone/email, or phone OTP login. **M–L**

---

## 4. Feature roadmap (what will grow sales / cut your workload)

**Customer-facing**
1. Order emails/WhatsApp at paid / shipped / delivered (B8). Highest support-saving feature.
2. Guest checkout or OTP login (3.4).
3. Product detail pages with reviews and size selector (3.2, 3.3).
4. Abandoned-cart recovery: you already have `/admin/carts`; automate a reminder message + auto-generated coupon after 24 h.
5. Cash-on-delivery, or a "prepaid discount" nudge. COD is large in India but raises returns; test it with an advance fee.
6. Customer self-service: cancel before pickup, download **GST invoice** (the current receipt is a simple receipt; confirm GST invoice requirements with your accountant), return request.
7. Search, wishlist, pincode check on product page (before adding to cart), Hindi/Marathi language toggle.

**Admin / operations**
1. Bulk actions on orders: ship many at once, print all labels together (NimbusPost's labels endpoint accepts multiple IDs), export CSV.
2. Order search/filter/pagination (`/admin/orders` still loads every order; I paginated the other five admin lists, not this one).
3. Low-stock alerts to Telegram; inventory history.
4. Simple dashboard for conversion funnel (visit → cart → checkout → paid) using real events, not raw hit counts.
5. Backups: confirm Supabase point-in-time recovery is on, and test a restore once.

---

## 5. Suggested plan

**Week 1 — stop the leaks (all small):** B1 notes paywall · B3 idempotent payment · B4 status mapping · B7 security headers · rotate exposed credentials · untrack CSVs · lazy-load hero images + shrink logo · Flask-Compress + cache headers.

**Week 2 — reliability:** B2 Razorpay webhook + pending-orders view · pin dependencies · add tests + CI · Sentry · Alembic baseline · order confirmation emails.

**Weeks 3–4 — growth:** product pages + sitemap · trust bar + reviews on home · size selector/combos · guest checkout · abandoned-cart reminders.

**Later:** blueprint refactor, own image CDN, multi-language, COD trial, GST invoicing.

---

## 6. Already good (keep)

- CSRF protection on every POST form and AJAX call; server-side price/shipping/coupon recomputation (client values are not trusted); signature verification on Razorpay callback and NimbusPost webhook; HttpOnly + SameSite session cookies and a secret-key guard; ownership checks on orders; honeypot + rate limiting on public forms; per-product `alt` text, sitemap, robots, structured data; row-level security enabled on the tables Supabase flagged (re-run the Security Advisor after any new table); WebP images used site-wide.

## 7. Could not verify from here

- Whether the GitHub repo is private; what Render start command/instance type is actually used; whether the NimbusPost webhook is registered; production TTFB/cold-start behaviour; Supabase backup settings. Each is a 5-minute check in the respective dashboard.
