# Current commercial architecture

Read [HOSTING.md](HOSTING.md) first. Razorpay customer builds now require online
credits and document authorization. The older offline activation workflow below
is historical developer/manual-provider documentation and does not describe the
current Razorpay customer EXE. Existing offline/manual licenses require verified
owner migration; local remaining balances cannot be trusted or imported automatically.

# Razorpay: $99 for two distinct PDFs

Customers start checkout in **Buy / activate**, open the secure payment tab, and
pay through Razorpay. ClearPage checks a pending purchase every 10 seconds while
the billing dialog is open (for up to 10 minutes), or immediately using **Check
payment**. A verified, captured payment grants two document credits once. Each
successfully opened, distinct PDF spends one credit; all its pages, repeat cleanup
and repeat downloads are included. Byte-identical files can reopen for free, even
under another filename. Modified PDF bytes count as a new document.

## Architecture

The desktop app keeps PDF processing and the existing license ledger local. A
separate seller-hosted Flask payment service creates Razorpay orders, verifies
payment signatures and capture status, and signs installation-bound activation
keys. The desktop verifies those signatures before adding credits. Card details
go through Razorpay Checkout, not ClearPage. No PDF is sent to this service.

Only `payment_service_url`, seller contact details and a public verification key
go into the customer application's `billing.json`. Razorpay secrets, the signing
private key/password, and the SQLite order database stay on the seller's server.
The payment service package is not imported or bundled by the desktop build.

## Configure test mode

1. Use Razorpay Dashboard **Test mode** API credentials. No real money is collected
   by those credentials. You still need to complete Razorpay's requirements before
   accepting international payments with live credentials.
2. In an owner-only folder **outside this repository**, create a test signing key:

   ```powershell
   python scripts/license_admin.py keygen --private-key C:/SellerPrivate/test-signing.pem --public-key C:/SellerPrivate/test-public.txt
   ```

3. Copy `payment_service/settings.example.json` to that private folder, for example
   `C:/SellerPrivate/test-settings.json`. Fill in the test key ID and secret,
   a random webhook secret, the signing-key path/password, an absolute SQLite path,
   seller name and support email. Keep this file private; never put it in `static/`,
   a customer ZIP, Git, chat, screenshots or browser JavaScript.
4. Install and start the service from the repository root:

   ```powershell
   python -m pip install -r payment_service/requirements.txt
   python -m payment_service.run --config C:/SellerPrivate/test-settings.json
   ```

   This uses Waitress on `127.0.0.1:5056`. `/health` reports readiness and test mode.
5. Copy `config/examples/billing.razorpay.example.json` to a **separate test** billing file. Set
   `payment_service_url` to `http://127.0.0.1:5056`, paste the test public key, and
   enter the seller name and email. Launch the desktop with:

   ```powershell
   $env:CLEARPAGE_BILLING_CONFIG='C:/SellerPrivate/test-billing.json'
   $env:CLEARPAGE_DATA_DIR='C:/SellerPrivate/test-customer-profile'
   $env:CLEARPAGE_PAYMENT_LOCAL_TEST='1'
   python app.py
   ```

   HTTP is permitted only for localhost with this explicit test flag. Normal
   customer configuration requires HTTPS. The test banner comes from the server's
   `rzp_test_` key ID; the customer cannot switch a production server to test mode.
6. Enable **automatic payment capture** in Razorpay settings. An authorized payment
   alone never grants credits. Complete the checkout using Razorpay's documented
   test payment details. Check the final order is $99 / 9900 cents.
   Payment is not considered complete until credits appear in ClearPage.
7. Verify cancellation, failed payment, closing the payment tab, restarting
   ClearPage before activation, repeated Check payment, three different PDFs, a fourth
   blocked PDF, and reopening/downloading the first paid PDF at zero credits.

## Go live

- Finish account KYC, bank settlement setup, and live-payment approval, plus
  international-card approval if serving overseas cardholders. Supply
  the business website/policy information requested by Razorpay. Account approval
  is external to this code. The app does not register or approve an account.
- Use a **different signing key, database and customer profile** for live mode.
  Never distribute the test public key in a live paid app; otherwise test licenses
  would be accepted by that app. The service rejects a database opened with a
  different Razorpay key ID or signing key.
- Run the service on an always-available seller server behind HTTPS. It needs
  persistent writable SQLite storage and backups; ephemeral hosting storage loses
  payment recovery. Hosting/domain costs depend on your chosen service.
- The default listener is local to the server. Put an HTTPS reverse proxy in front
  of it. If using one trusted proxy, pass its exact IP with `--trusted-proxy` and
  have it overwrite forwarded headers. Otherwise rate limits apply to the proxy's
  shared address. Apply appropriate perimeter abuse limits as traffic grows.
- Configure `payment.captured` and `order.paid` webhooks at
  `https://YOUR-PAYMENT-HOST/api/webhook` using the same webhook secret. Localhost
  cannot receive Razorpay webhooks; local test recovery uses authenticated API
  polling. Verify real test webhook delivery on the hosted HTTPS service.
- Put the HTTPS origin (no path), live public key and seller details in the
  customer's `billing.json`. Build and test the customer app. Do not set the
  localhost test flag in a production launcher.
- Complete a controlled live payment and check capture, fulfillment and bank
  settlement before offering checkout publicly. No live transaction has been
  performed by this implementation task.

## Recovery and operational boundaries

New purchases are USD 9900 cents for two documents. Existing USD and INR orders and
signed packs retain their original amount and credit count; the
database upgrade records the plan on each order. Checkout shows the saved order's
actual currency, amount and document count. A confirmed refund clears the desktop's
pending purchase so the customer can intentionally buy a new pack.

The desktop writes a random purchase capability before it contacts the service.
The service stores only its SHA-256 hash, binds the order to an installation, and
reuses it on retries. Checkout carries the capability in the URL fragment so it
does not enter server access logs. Treat that checkout link as private. Keep
request-body logging off. Repeated verification/webhooks return the same signed
pack; database uniqueness and desktop pack IDs prevent double credit grants.

A lost desktop response can be recovered with **Resume checkout** or **Check
payment**. If Razorpay order creation itself succeeds but its response is lost
before the order ID reaches the service, the receipt may need owner reconciliation
in the Razorpay Dashboard. No checkout was exposed for that unknown order; never
tell the customer to pay another order just because a network request timed out.

Payment capture must match the amount and currency of the stored order and must not be refunded. Checkout callbacks use HMAC verification against the server's
stored order ID. Webhooks verify the raw request body and then re-fetch payment
status through the authenticated Razorpay API. Provider outages fail closed.

This preserves the existing offline-license limits described in BILLING.md:
customers controlling their computer can patch source or roll back their local
usage ledger. This is not tamper-proof DRM. Refunds/chargebacks after a license has
already been activated cannot remotely revoke offline credits; manage those cases
manually. Cross-device balances and enforceable revocation need online accounts.

Fees, taxes, settlement timing and foreign exchange are controlled by Razorpay
and your agreement; the app promises no zero-fee settlement or fixed INR amount.

## Developer verification and API contract

- `payment_service/openapi.json` describes the service's public JSON API.
- `python -m unittest tests.test_razorpay tests.test_billing` exercises real local HTTP calls
  against an in-process Razorpay stand-in shaped from the official API docs.
  These are simulated provider responses, not evidence of Razorpay approval or a
  successful real payment. Postman CLI was unavailable and Orbit discovery could
  not resolve; no Postman cloud resources were created.
- The simulation covers fixed pricing, capture-only fulfillment, invalid
  signatures, wrong amount/currency/order, refunds, duplicate and concurrent
  requests, webhook replay, service restart, network outages and desktop recovery.
- Existing PDF/API regression tests require a nonexistent billing config for free
  mode. Build the frontend with `npm run build`.

Primary references:
- https://razorpay.com/docs/payments/payment-gateway/web-integration/standard/integration-steps/
- https://razorpay.com/docs/api/payments/fetch-with-id/
- https://razorpay.com/docs/webhooks/validate-test/
- https://razorpay.com/docs/payments/international-payments/
