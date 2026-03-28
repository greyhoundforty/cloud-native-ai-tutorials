# SEO Checklist — greyhoundforty-tutorials.netlify.app

Steps for the board to complete after the site is deployed to Netlify.

---

## 1. Verify the Site Builds and Deploys

- [ ] Confirm the latest commit on `main` triggered a Netlify deploy
- [ ] Visit `https://greyhoundforty-tutorials.netlify.app/` and confirm the site loads
- [ ] Check the Netlify deploy log for any build errors

---

## 2. Submit Sitemap to Google Search Console

### 2a. Add your site property

1. Go to [Google Search Console](https://search.google.com/search-console)
2. Click **Add property** → **URL prefix**
3. Enter `https://greyhoundforty-tutorials.netlify.app/`

### 2b. Verify ownership via Netlify DNS (recommended)

1. In Search Console, choose **DNS record** verification method
2. Copy the `TXT` record value provided (looks like `google-site-verification=xxxxxxxxxx`)
3. In the Netlify dashboard → **Domain settings** → **DNS records** → **Add record**:
   - Type: `TXT`
   - Name: `@` (root domain) or the subdomain if using a custom domain
   - Value: paste the verification string
4. Click **Save**, then return to Search Console and click **Verify**
5. DNS propagation may take up to 24 hours; verification will succeed once it propagates

> If you are using a custom domain (not `.netlify.app`), add the TXT record through your DNS provider (e.g. Cloudflare, Namecheap, Route 53) rather than Netlify's DNS panel.

### 2c. Submit the sitemap

1. In Search Console, select your verified property
2. Go to **Sitemaps** in the left sidebar
3. Enter `sitemap.xml` in the input field and click **Submit**
4. Google will begin crawling and indexing — initial indexing typically takes a few days to a week

---

## 3. Verify Open Graph / Twitter Card Previews

The homepage (`docs/index.md`) includes full OG and Twitter Card meta tags. To verify they render correctly:

- **Twitter/X**: Use the [Card Validator](https://cards-dev.twitter.com/validator) — enter `https://greyhoundforty-tutorials.netlify.app/`
- **LinkedIn**: Use the [Post Inspector](https://www.linkedin.com/post-inspector/) — enter the URL and click **Inspect**
- **Slack / Discord**: Paste the URL in a message; the preview should show title, description, and image

### Social preview image

The OG/Twitter tags reference `https://greyhoundforty-tutorials.netlify.app/assets/images/social-preview.png`.

- [ ] Create a 1200×630px PNG banner for the site and save it to `docs/assets/images/social-preview.png`
- [ ] Re-deploy and re-validate the preview after adding the image

Suggested banner content: site name, tagline ("Practical tutorials for cloud native developers"), and a simple graphic or code snippet.

---

## 4. Optional: Enable Google Analytics

To track search traffic and top pages:

1. Create a [Google Analytics 4](https://analytics.google.com) property
2. Copy the Measurement ID (format: `G-XXXXXXXXXX`)
3. Add to `mkdocs.yml`:
   ```yaml
   extra:
     analytics:
       provider: google
       property: G-XXXXXXXXXX
   ```
4. Deploy and confirm events appear in the GA4 realtime report

---

## 5. Ongoing

- Re-submit the sitemap after adding new tutorials
- Check Search Console **Coverage** report monthly for crawl errors
- Monitor **Performance** → **Queries** to see which search terms drive traffic
