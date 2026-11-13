# LUMO SHELL storefront

Static, dependency-free website for the LUMO SHELL smart LED visibility over-jacket
(`index.html`, `policies.html`, `assets/`). Open `index.html` or deploy the folder as-is to
Netlify, Vercel, Cloudflare Pages or GitHub Pages.

## Before you launch
1. **Edit `assets/js/config.js`.** Business name, ABN, address, email, dispatch/shipping text,
   warranty period and returns window. Anything blank shows as a highlighted `[add … in config.js]`
   field on the site and policies page so nothing is silently invented.
2. **Confirm pricing.** Base ($499) and top-of-range ($1,199) are as briefed. Any 1 upgrade is $749, any 2 are $999,
   all 3 (MAX) are $1,199; set in `config.js`. The size run (S–2XL) is an **assumption**.
3. **Connect checkout.** The site cannot take card payments by itself. Orders are *requests*:
   set `orderEndpoint` (Formspree/Netlify Forms/your API, receives JSON) or `contactEmail`
   (opens a pre-filled email). To take payments online, use Shopify (Buy Button / full store)
   or Stripe Checkout with a small server function.
4. **Have the policies reviewed** by a lawyer against your actual business before trading.
5. **Check road-rule compliance** for rider-worn lighting in each state/territory you sell into.

## Local preview
`python3 -m http.server 8000` then open http://localhost:8000
