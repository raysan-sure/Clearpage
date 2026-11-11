"""Start the seller service using a private settings file outside the app checkout."""
import argparse
import json
from pathlib import Path
from waitress import serve
from payment_service.server import create_app


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', type=Path, required=True)
    parser.add_argument('--port', type=int, default=5056)
    parser.add_argument('--trusted-proxy', help='Exact reverse-proxy IP; configure only behind that trusted proxy.')
    parser.add_argument('--test-only', action='store_true', help='Refuse live credentials for the local test launcher.')
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    if args.config.resolve().is_relative_to(root):
        parser.error('Keep seller credentials outside the application repository.')
    try:
        settings = json.loads(args.config.read_text(encoding='utf-8-sig'))
        if args.test_only and not settings.get('RAZORPAY_KEY_ID','').startswith('rzp_test_'):
            parser.error('This test launcher accepts only Razorpay Test Mode keys.')
        app = create_app(settings)
    except (ValueError, OSError, TypeError):
        parser.error('Complete the private payment settings, signing key and password. See docs/RAZORPAY.md.')
    options = {}
    if args.trusted_proxy:
        options = dict(trusted_proxy=args.trusted_proxy, trusted_proxy_count=1,
                       trusted_proxy_headers={'x-forwarded-for', 'x-forwarded-proto'})
    print(f'ClearPage payment service: http://127.0.0.1:{args.port} '
          f'({"TEST" if settings["RAZORPAY_KEY_ID"].startswith("rzp_test_") else "LIVE"} mode)', flush=True)
    serve(app, host='127.0.0.1', port=args.port, threads=8, **options)


if __name__ == '__main__':
    main()
