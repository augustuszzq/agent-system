import typer


app = typer.Typer(help="Auto Research control plane CLI.")
db_app = typer.Typer(help="Database commands.")
run_app = typer.Typer(help="Run registry commands.")

app.add_typer(db_app, name="db")
app.add_typer(run_app, name="run")


def main() -> None:
    app()


if __name__ == "__main__":
    main()
