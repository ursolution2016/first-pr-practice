# LUMO SHELL on Shopify

## Files
- `lumo-shell-shopify-import.csv`: one product, 40 variants (5 sizes x 8 models), imported as a **draft**.
- `product-description.html`: the same description, if you prefer to paste it in by hand.
- Product images: use the files in `../assets/img/` (upload `hero-rider.webp` first so it is the main image).

## Steps
1. Create a store at shopify.com (Australia, currency AUD).
2. Admin > **Products > Import** > choose `lumo-shell-shopify-import.csv` > Upload and continue > Import products.
3. Open the new draft product > **Media** > upload the images from `assets/img/`.
4. Check the prices and sizes, set stock quantities, then change **Status** to Active.
5. Settings > **Taxes and duties** > tick "Include sales tax in product price" so $499 is the GST-inclusive price.
6. Settings > **Payments** > turn on Shopify Payments. Settings > **Shipping and delivery** > set your rates.
7. Settings > **Policies** > paste the sections from `../policies.html` (returns, privacy, terms, shipping).

## Assumptions to confirm
- Upgrade prices: colour change +$300, brightness +$150, extended battery +$250 (MAX = $1,199).
- Sizes S to 2XL. Shipping weight 1.1 kg per jacket.
