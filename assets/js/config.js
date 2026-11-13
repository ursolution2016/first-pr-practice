/* ==========================================================================
   LUMO SHELL — store configuration
   Everything you are likely to change lives in this one file.
   Anything left as '' is shown as a highlighted "to complete" field on the
   site (and on the policies page) so nothing is silently invented for you.
   ========================================================================== */
window.LUMO_CONFIG = {

  /* ---- Pricing ---------------------------------------------------------
     Price depends on HOW MANY upgrades are chosen (any combination):
       0 upgrades = $499, 1 = $749, 2 = $999, all 3 = $1,199 (MAX).         */
  currency: 'AUD',
  locale: 'en-AU',
  pricesIncludeGST: true,           // ACL: advertised price must be the total price
  base: { name: 'LUMO SHELL' },
  priceByUpgradeCount: [499, 749, 999, 1199],
  upgrades: [
    { id: 'colour',     name: 'Colour-change LEDs',
      desc: 'Switch the LED colour away from the standard fluoro yellow.' },
    { id: 'brightness', name: 'Brightness control',
      desc: 'Turn the LEDs up for dark roads or down for well-lit streets.' },
    { id: 'battery',    name: 'Extended battery',
      desc: 'A larger battery pack for longer runtime than the standard 8–10 hours.' }
  ],
  sizes: ['S', 'M', 'L', 'XL', '2XL'],   // ASSUMED size run: edit to match your product

  /* ---- Your business (used on the policies page & footer) ------------- */
  businessName: 'NI Claims',      // e.g. 'Lumo Pty Ltd'
  abn: '77 022 022 391',               // 11-digit ABN
  businessAddress: '5 Lachlan St, Bossley Park NSW 2176',   // street address shown in the terms / warranty
  contactEmail: 'ursolution2016@gmail.com',      // customer service email
  contactPhone: '0404 435 069',      // optional

  /* ---- Fulfilment text (shown in FAQ / policies) ---------------------- */
  dispatchTime: 'To be advised when we confirm your order',      // e.g. 'within 5 business days'
  shippingInfo: '$20 flat-rate shipping.',      // e.g. 'Flat-rate $15 Australia-wide via Australia Post'
  warrantyPeriod: '12 months',    // e.g. '12 months'
  returnsWindow: '30 days',     // e.g. '30 days' (change-of-mind policy, if you offer one)

  /* ---- Checkout --------------------------------------------------------
     The site ships as a static front end, so it cannot take card payments
     by itself. By default the checkout collects an ORDER REQUEST:
       1. Set orderEndpoint to a form-handling URL (Formspree, Netlify Forms,
          Basin, your own API ...). The order is POSTed there as JSON.
       2. OR leave it '' and set contactEmail: the order opens as an email.
     To take real payments, connect Shopify / Stripe — see README.md.      */
  orderEndpoint: '',
  checkoutBlurb:
    'Submit your order request and we will confirm availability and delivery, ' +
    'then send you a secure payment link. You are not charged when you submit this form.'
};
