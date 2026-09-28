"""Command line administration: `mockbank --help`."""

import json

import typer

app = typer.Typer(help="MockBank administration commands.", no_args_is_help=True)


@app.command()
def seed() -> None:
    """Create seed customers, accounts, history and the demo application if missing."""
    from mockbank.db import new_session
    from mockbank.services import seed as seed_service

    with new_session() as db:
        result = seed_service.seed(db)
    typer.echo(json.dumps(result))


@app.command("reset-activity")
def reset_activity() -> None:
    """Delete payments, consents, tokens, webhooks and the ledger; keep customers and apps."""
    from mockbank.db import new_session
    from mockbank.services import seed as seed_service

    with new_session() as db:
        result = seed_service.reset_activity(db)
    typer.echo(json.dumps(result))


@app.command()
def reset(yes: bool = typer.Option(False, "--yes", help="Skip the confirmation prompt")) -> None:
    """Delete everything (including applications) and re-seed."""
    from mockbank.db import new_session
    from mockbank.services import seed as seed_service

    if not yes and not typer.confirm("This deletes every customer, application and transaction. Continue?"):
        raise typer.Abort()
    with new_session() as db:
        result = seed_service.reset_all(db)
    typer.echo(json.dumps(result))


@app.command("check-ledger")
def check_ledger() -> None:
    """Verify cached balances match the ledger and journals balance to zero."""
    from mockbank.db import new_session
    from mockbank.services.ledger import check_integrity

    with new_session() as db:
        report = check_integrity(db)
    typer.echo(json.dumps(report.__dict__, default=str))
    if not report.ok:
        raise typer.Exit(code=1)


@app.command("create-app")
def create_app(
    name: str = typer.Option(..., help="Application name shown on consent screens"),
    redirect_uri: list[str] = typer.Option(..., help="Registered redirect URI (repeatable)"),
    webhook_url: str | None = typer.Option(None),
    owner: str = typer.Option("", help="Team label"),
    settlement_email: str | None = typer.Option(None, help="Also create a business customer and settlement account"),
    settlement_balance: str = typer.Option("0.00"),
) -> None:
    """Register a student application and print its credentials."""
    from mockbank.db import new_session
    from mockbank.money import parse_amount
    from mockbank.services import admin as admin_service

    with new_session() as db:
        result = admin_service.register_application(
            db, name=name, owner_label=owner, redirect_uris=redirect_uri, webhook_url=webhook_url,
            settlement_email=settlement_email,
            settlement_opening_balance=parse_amount(settlement_balance, allow_zero=True),
        )
        db.commit()
        out = {
            "client_id": result.application.id,
            "client_secret": result.client_secret,
            "webhook_secret": result.application.webhook_secret,
            "redirect_uris": result.application.redirect_uris,
        }
        if result.settlement_account:
            out["settlement_login"] = result.settlement_customer.email
            out["settlement_password"] = result.settlement_password
            out["settlement_account_number"] = result.settlement_account.account_number
            out["settlement_account_id"] = result.settlement_account.id
    typer.echo(json.dumps(out, indent=2))


@app.command("run-worker")
def run_worker() -> None:
    """Run the webhook and settlement worker in the foreground (for a separate process)."""
    import logging
    import time

    from mockbank import worker

    logging.basicConfig(level="INFO")
    worker.start()
    try:
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        worker.stop()


if __name__ == "__main__":
    app()
