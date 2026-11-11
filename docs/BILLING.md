# Manual bank payments and two-document packs

For **Razorpay card checkout with automatic payment verification**, use
[RAZORPAY.md](RAZORPAY.md). The remainder of this guide covers the optional manual
bank-transfer provider. Both providers use the same distinct-document credit rules.

ClearPage can sell a **$99 pack for two distinct PDF files**. Each successfully
opened PDF spends one document credit, regardless of page count. Invalid files
and failed previews spend nothing. Reopening a byte-identical file, even under a
different filename, is free; cleaning and downloading it again is free. A modified
file has a different fingerprint and counts as another document. The PDF upload UI
is the supported paid workflow; image imports converted to PDFs can have different
PDF bytes and should not be offered as repeat-free imports.

Payments in this optional mode are **manual bank transfers in USD**. This feature does not call
HDFC, verify transactions, collect card details, or charge customers automatically.
The customer sends the bank transaction reference and installation code to your
configured support email. Check the credited payment in your bank before issuing
a pack. A screenshot or reference alone is not proof of receipt.

## Set up the seller

1. Ask HDFC whether your account supports overseas software-sale receipts. Obtain
   the exact beneficiary name, account number, bank address, SWIFT/BIC, any
   correspondent-bank routing, invoice/purpose requirements, and applicable fees.
   Do not guess the routing details. Decide how you handle sender fees, refunds,
   and activation turnaround; include those instructions in the customer text.
2. Install the application's requirements. Create a private seller directory
   **outside this repository and outside every customer distribution**.
3. Run the owner tool from the repository root (replace the example paths):

   ```powershell
   python scripts/license_admin.py keygen --private-key C:/SellerPrivate/clearpage.pem --public-key C:/SellerPrivate/public-key.txt
   ```

   Enter a strong password when prompted. The private key is encrypted with that
   password. Back up both securely: changing the signing key invalidates previously
   issued customer packs. Never send the private key or password to customers.
4. Copy `config/examples/billing.example.json` to `billing.json`. Fill every field. Paste the
   contents of `public-key.txt` into `public_key`. The product price is fixed at
   $99 and the pack size at two documents in `clearpage/billing/licensing.py`. `billing.json` contains
   customer-visible information only. It is ignored by Git.
5. Start `python app.py`. The header displays credits and **Buy / activate**.
   Incomplete configuration hides all payment instructions; it never asks customers
   to transfer to placeholder details. Existing signed packs can still activate
   while payment instructions are temporarily unavailable.

No `billing.json` means the existing unrestricted source/development build. A
present but incomplete or malformed configuration enables the credit restriction;
it does not fall back to free access. `CLEARPAGE_BILLING_CONFIG` can point to a
different local configuration for testing. In customer builds, include the complete
configuration using `ClearPage.spec`; the build refuses an incomplete paid config.
The private key, owner CLI and issuance ledger are not included by the spec.

## Fulfil a paid order

1. Independently verify the incoming payment, amount and customer in your bank.
2. Obtain the customer's installation code from **Buy / activate**.
3. Run:

   ```powershell
   python scripts/license_admin.py issue --private-key C:/SellerPrivate/clearpage.pem --installation CP-CUSTOMER_INSTALLATION_CODE --customer "Customer name or email" --payment-reference "BANK_TRANSACTION_REFERENCE" --confirm-received --output C:/SellerPrivate/customer-pack.license
   ```

   Use the real `CP-` code; the placeholder above is not a valid code. A pack grants
   exactly two documents. The tool prompts for the private-key password and writes
   a local issuance ledger beside the private key. Repeating the same payment
   reference for that installation returns the same pack, not another two credits.
   A reference already assigned to another installation is rejected. Use one owner
   issuance process at a time and preserve the ledger with your private backups.
4. Send the contents of the `.license` file to the customer through your usual
   communication channel. The customer pastes the key and clicks **Activate document
   pack**. No bank transaction reference is sent or stored by the customer app.
5. A second payment receives a new pack using a different verified bank reference.

The UI opens documents only after credits have been activated; payment submission
does not unlock anything. Re-entering a pack is idempotent. Keys are signed with
Ed25519, bound to the installation, product, USD amount and two-document count.
Document permissions are also checked on direct preview/export/download and batch
routes, including saved documents. Saved work from another installation does not
transfer its payment permissions; reopen the original file to allocate a credit.

## Data, recovery and limits

The local `ClearPage/billing` directory under `LOCALAPPDATA` (or the configured
`CLEARPAGE_DATA_DIR`) holds the installation ID, signed packs and SHA-256 document
fingerprints. No PDFs are added to that ledger. Back up the **entire directory**;
deleting it loses the installation identity and its credits. Browser refreshes,
app restarts and saved-work restoration do not reset a valid ledger. Corrupt
configuration or credits fail closed. Keep a single running app for a profile.

Offline credits are a convenience-based license mechanism, not tamper-proof DRM.
A customer controlling their computer can patch the public application, remove
the paid configuration, or restore an old usage backup. Installation binding is
to a local identity, not physical hardware. Previews and intermediate files are
local, and can be copied. Preventing rollback, remotely revoking/refunding packs,
or sharing a reliable balance across devices requires an online authority. This
implementation intentionally does not claim those protections.

Bank, currency-conversion and tax costs are separate from this application. No
zero-fee promise or fixed exchange rate is displayed to customers.

## Validation

```powershell
python -m unittest tests.test_billing
npm ci
npm run build
```

The tests cover signatures, altered keys, wrong installations, duplicate activation,
multiple packs, persistence, failed uploads, exact-file reuse, direct export/ZIP
checks, local request protection and owner payment-reference reuse. For existing
free-mode regression tests, set `CLEARPAGE_BILLING_CONFIG` to a nonexistent path.
