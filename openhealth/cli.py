import argparse
import json
import secrets
from dataclasses import replace
from pathlib import Path
from typing import List, Optional

from . import index
from .contexts import refresh_contexts
from .ingest import ingest_path, init_workspace
from .storage import ensure_repo_structure
from .whoop import (
    CAPABILITIES,
    build_authorization_url,
    exchange_code_for_tokens,
    extract_code_from_redirect_url,
    latest_whoop_summary,
    load_credentials_from_env,
    save_tokens,
    sync_whoop,
    verify_webhook_signature,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="openhealth", description="Local-first OpenHealth workspace.")
    parser.add_argument("--repo-root", default=".", help="Path to the OpenHealth repository root.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("init", help="Create folders and initialize the SQLite index.")

    ingest = subparsers.add_parser("ingest", help="Ingest a file or directory into OpenHealth.")
    ingest.add_argument("--source", required=True, help="Source type, for example whoop or messages.")
    ingest.add_argument("--path", required=True, help="File or directory to ingest.")
    ingest.add_argument("--owner", default="user", help="Owner label stored in the manifest.")
    ingest.add_argument("--label", help="Human-readable label for this source batch.")
    ingest.add_argument("--location", help="Default location when metadata is missing.")

    subparsers.add_parser("refresh-contexts", help="Rebuild contexts and insights from indexed records.")
    subparsers.add_parser("show-summary", help="Print a lightweight JSON summary of indexed data.")

    whoop_auth = subparsers.add_parser("whoop-auth-url", help="Generate a WHOOP OAuth authorization URL.")
    whoop_auth.add_argument("--state", help="Eight-character CSRF state. Generated automatically if omitted.")
    whoop_auth.add_argument(
        "--scope",
        action="append",
        metavar="SCOPE",
        help=(
            "WHOOP OAuth scope to request (repeatable). Overrides "
            "OPENHEALTH_WHOOP_SCOPES and the built-in default set. Use this "
            "when your app is not granted every default scope."
        ),
    )

    whoop_exchange = subparsers.add_parser("whoop-exchange-code", help="Exchange a WHOOP OAuth code for tokens.")
    whoop_exchange.add_argument("--code", required=True, help="Authorization code returned by WHOOP.")

    whoop_exchange_url = subparsers.add_parser(
        "whoop-exchange-redirect-url",
        help="Parse a full WHOOP redirect URL, extract the code, and exchange it for tokens.",
    )
    whoop_exchange_url.add_argument("--url", required=True, help="Full redirect URL captured after WHOOP OAuth.")
    whoop_exchange_url.add_argument("--expected-state", help="Optional OAuth state to verify.")

    whoop_sync = subparsers.add_parser("whoop-sync", help="Sync WHOOP API data into OpenHealth.")
    whoop_sync.add_argument("--start", help="ISO-8601 UTC start timestamp, for example 2026-03-01T00:00:00Z.")
    whoop_sync.add_argument("--end", help="ISO-8601 UTC end timestamp, for example 2026-03-13T12:00:00Z.")
    whoop_sync.add_argument("--days-back", type=int, default=30, help="Fallback lookback window when no start is provided.")
    whoop_sync.add_argument("--owner", default="user", help="Owner label stored in the WHOOP source manifest.")
    whoop_sync.add_argument("--no-profile", action="store_true", help="Skip syncing WHOOP profile.")
    whoop_sync.add_argument("--no-body-measurements", action="store_true", help="Skip syncing WHOOP body measurements.")

    subparsers.add_parser("whoop-capabilities", help="Show WHOOP collections and gaps in the public API.")
    subparsers.add_parser("whoop-latest", help="Show the latest WHOOP timestamps from local OpenHealth data.")

    whoop_verify = subparsers.add_parser("whoop-verify-webhook", help="Verify a WHOOP webhook signature for a saved payload file.")
    whoop_verify.add_argument("--body-file", required=True, help="Path to the raw webhook body file.")
    whoop_verify.add_argument("--signature", required=True, help="Value of the X-WHOOP-Signature header.")
    whoop_verify.add_argument("--timestamp", required=True, help="Value of the X-WHOOP-Signature-Timestamp header.")

    withings_auth = subparsers.add_parser("withings-auth-url", help="Generate a Withings OAuth authorization URL.")
    withings_auth.add_argument("--redirect-uri", required=True, help="Callback URL registered in the Withings app, e.g. http://localhost:8765/callback.")
    withings_auth.add_argument("--state", help="Eight-character CSRF state. Generated automatically if omitted.")

    withings_exchange = subparsers.add_parser(
        "withings-exchange-code",
        help="Exchange a Withings OAuth code for tokens (saved to ~/.openhealth/withings.json).",
    )
    withings_exchange.add_argument("--code", required=True, help="Authorization code returned by Withings.")
    withings_exchange.add_argument("--redirect-uri", required=True, help="Same redirect URI as withings-auth-url.")

    withings_sync = subparsers.add_parser(
        "withings-sync", help="Fetch Withings measures and sleep summaries into the index."
    )
    withings_sync.add_argument("--start", help="Start date YYYY-MM-DD (default: 30 days back).")
    withings_sync.add_argument("--end", help="End date YYYY-MM-DD (default: today).")
    withings_sync.add_argument("--no-sleep", action="store_true", help="Skip the sleep summary fetch.")
    withings_sync.add_argument("--no-measures", action="store_true", help="Skip the body measures fetch.")
    withings_sync.add_argument("--no-save", action="store_true", help="Fetch and summarize without saving.")

    subparsers.add_parser("bot-start", help="Start the Telegram intake bot (polling mode).")

    subparsers.add_parser("modules", help="List available health domain modules.")

    mod = subparsers.add_parser("module", help="Run a domain module on a JSON payload.")
    mod.add_argument("--id", required=True, help="Module id, e.g. pulse, sleep, cycle, body.")
    mod.add_argument("--payload-json", help="Inline JSON payload for the module.")
    mod.add_argument("--payload-file", help="Path to a JSON payload file.")
    mod.add_argument("--no-save", action="store_true", help="Do not persist results into the index.")

    rec = subparsers.add_parser("recent", help="Show recent records/insights from the index.")
    rec.add_argument("--type", dest="rtype", help="Filter by record_type, e.g. InsightHypothesis.")
    rec.add_argument("--metric", help="Filter by metric_name, e.g. rmssd_ms.")
    rec.add_argument("--tag", help="Filter by a tag.")
    rec.add_argument("--limit", type=int, default=10, help="Max rows.")

    ah = subparsers.add_parser("import-apple-health", help="Import an Apple Health export (export.xml or .zip) into daily observations.")
    ah.add_argument("--path", required=True, help="Path to export.xml or the Apple Health export .zip.")
    ah.add_argument("--days-back", type=int, help="Only import the last N days.")
    ah.add_argument("--no-save", action="store_true", help="Parse and summarize without writing to the index.")

    return parser


def main(argv: Optional[List[str]] = None) -> int:
    # CA-бандл для исходящего HTTPS (WHOOP token-exchange/sync, Withings) до сети.
    try:
        from ._certs import ensure_ca_certs
        ensure_ca_certs()
    except Exception:
        pass
    parser = build_parser()
    args = parser.parse_args(argv)
    repo_root = Path(args.repo_root).resolve()
    if args.command == "init":
        result = init_workspace(repo_root)
    elif args.command == "ingest":
        result = ingest_path(
            root=repo_root,
            source_type=args.source,
            path=Path(args.path).resolve(),
            owner=args.owner,
            label=args.label,
            location=args.location,
        )
    elif args.command == "refresh-contexts":
        paths = ensure_repo_structure(repo_root)
        index.init_db(paths.db_path)
        result = refresh_contexts(paths, index)
    elif args.command == "whoop-auth-url":
        credentials = load_credentials_from_env()
        if args.scope:
            credentials = replace(credentials, scopes=tuple(args.scope))
        state = args.state or secrets.token_hex(4)
        result = {
            "authorization_url": build_authorization_url(credentials, state),
            "state": state,
            "scopes": list(credentials.scopes),
        }
    elif args.command == "whoop-exchange-code":
        paths = ensure_repo_structure(repo_root)
        credentials = load_credentials_from_env()
        tokens = exchange_code_for_tokens(credentials, args.code)
        save_tokens(paths.whoop_tokens_path, tokens)
        result = {
            "token_path": str(paths.whoop_tokens_path),
            "expires_at": tokens["expires_at"],
            "scope": tokens.get("scope"),
        }
    elif args.command == "whoop-exchange-redirect-url":
        paths = ensure_repo_structure(repo_root)
        credentials = load_credentials_from_env()
        parsed = extract_code_from_redirect_url(args.url, args.expected_state)
        tokens = exchange_code_for_tokens(credentials, parsed["code"])
        save_tokens(paths.whoop_tokens_path, tokens)
        result = {
            "token_path": str(paths.whoop_tokens_path),
            "expires_at": tokens["expires_at"],
            "scope": tokens.get("scope"),
            "state": parsed.get("state"),
        }
    elif args.command == "whoop-sync":
        result = sync_whoop(
            root=repo_root,
            start=args.start,
            end=args.end,
            days_back=args.days_back,
            owner=args.owner,
            include_profile=not args.no_profile,
            include_body_measurements=not args.no_body_measurements,
        )
    elif args.command == "whoop-capabilities":
        result = CAPABILITIES
    elif args.command == "whoop-latest":
        result = latest_whoop_summary(repo_root)
    elif args.command == "whoop-verify-webhook":
        body_file = Path(args.body_file).resolve()
        secret = load_credentials_from_env().client_secret
        result = {
            "valid": verify_webhook_signature(
                secret=secret,
                payload_bytes=body_file.read_bytes(),
                signature_header=args.signature,
                timestamp_header=args.timestamp,
            )
        }
    elif args.command == "withings-auth-url":
        from .connectors import withings
        state = args.state or secrets.token_hex(4)
        result = {"authorization_url": withings.auth_url(args.redirect_uri, state), "state": state}
    elif args.command == "withings-exchange-code":
        from .connectors import withings
        tokens = withings.exchange_code(args.code, args.redirect_uri)
        result = {
            "config_path": str(withings.withings_config_path()),
            "expires_at": tokens["expires_at"],
        }
    elif args.command == "withings-sync":
        from .connectors import withings
        records = []
        if not args.no_measures:
            records.extend(withings.fetch_measures(args.start, args.end))
        if not args.no_sleep:
            records.extend(withings.fetch_sleep_summary(args.start, args.end))
        saved = 0
        if not args.no_save and records:
            paths = ensure_repo_structure(repo_root)
            index.init_db(paths.db_path)
            for record in records:
                index.upsert_record(paths.db_path, record)
                saved += 1
        result = {"summary": withings.summarize(records), "saved_to_index": saved}
    elif args.command == "bot-start":
        from .bot import start_bot
        start_bot(repo_root)
        return 0
    elif args.command == "modules":
        from . import modules as modpkg
        modpkg.load_builtin()
        result = {
            "modules": [
                {"id": m.id, "name": m.name, "domain": m.domain, "summary": m.summary}
                for m in modpkg.all_modules()
            ]
        }
    elif args.command == "module":
        from . import modules as modpkg
        modpkg.load_builtin()
        module = modpkg.get_module(args.id)
        if args.payload_file:
            payload = json.loads(Path(args.payload_file).read_text(encoding="utf-8"))
        elif args.payload_json:
            payload = json.loads(args.payload_json)
        else:
            payload = {}
        outcome = module.compute(payload)
        saved = 0
        if not args.no_save and (outcome.metrics or outcome.insights):
            paths = ensure_repo_structure(repo_root)
            index.init_db(paths.db_path)
            for record in list(outcome.metrics) + list(outcome.insights):
                index.upsert_record(paths.db_path, record)
                saved += 1
        result = {
            "module": args.id,
            "metrics": outcome.metrics,
            "insights": outcome.insights,
            "notes": outcome.notes,
            "saved_to_index": saved,
        }
    elif args.command == "recent":
        paths = ensure_repo_structure(repo_root)
        index.init_db(paths.db_path)
        rows = index.list_records(paths.db_path)
        if args.rtype:
            rows = [r for r in rows if r.get("record_type") == args.rtype]
        if args.metric:
            rows = [r for r in rows if r.get("metric_name") == args.metric]
        if args.tag:
            rows = [r for r in rows if args.tag in (r.get("tags") or [])]
        rows.sort(key=lambda r: (r.get("date") or r.get("start_date") or ""), reverse=True)
        result = {
            "count": len(rows),
            "records": [
                {
                    "id": r.get("id"),
                    "record_type": r.get("record_type"),
                    "date": r.get("date") or r.get("start_date"),
                    "title": r.get("title"),
                    "summary": r.get("summary"),
                    "metric_name": r.get("metric_name"),
                    "value": r.get("value"),
                    "unit": r.get("unit"),
                    "confidence": r.get("confidence"),
                }
                for r in rows[: args.limit]
            ],
        }
    elif args.command == "import-apple-health":
        from .connectors import apple_health
        records = apple_health.import_apple_health(args.path, days_back=args.days_back)
        saved = 0
        if not args.no_save and records:
            paths = ensure_repo_structure(repo_root)
            index.init_db(paths.db_path)
            for record in records:
                index.upsert_record(paths.db_path, record)
                saved += 1
        result = {"summary": apple_health.summarize(records), "saved_to_index": saved}
    else:
        paths = ensure_repo_structure(repo_root)
        index.init_db(paths.db_path)
        result = {
            "sources": len(index.list_sources(paths.db_path)),
            "artifacts": len(index.list_artifacts(paths.db_path)),
            "records": len(index.list_records(paths.db_path)),
        }
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0
