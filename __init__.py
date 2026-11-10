"""Pocketbook: your budget, spending and money alerts inside Hermes."""

from __future__ import annotations

from pathlib import Path

_DELIVER = {"name": "deliver", "type": "enum", "label": "Where should alerts go?", "default": "origin",
            "options": ["origin", "local"], "strict": False}


def register(ctx) -> None:
    from . import cli, tools

    tools._cron_check = ctx.current_cron_execution
    for name, handler, emoji, description, props in tools.TOOLS:
        ctx.register_tool(name=name, toolset="pocketbook", schema=tools.schema(name, description, props),
                          handler=handler, description=description.split(".")[0], emoji=emoji)

    ctx.register_command("money", tools.money_command,
                         description="Budget snapshot; `/money check` for new alerts", args_hint="[check]")
    ctx.register_cli_command(name="pocketbook", help="Connect banks, sync, and try sample data",
                             setup_fn=cli.setup, handler_fn=cli.run)

    skill = Path(__file__).parent / "skills" / "money" / "SKILL.md"
    if skill.exists():
        ctx.register_skill("money", skill)

    ctx.register_automation_blueprint(
        "money-alerts",
        title="Money alerts",
        description="Check your accounts every two hours and message you only about new large or unusual "
                    "charges, double charges, price increases, bills due soon, and budgets at risk.",
        category="finance",
        schedule_template="7 8-22/2 * * *",
        prompt_template=(
            "Call money_alerts with action=check. If it returns no alerts, respond with exactly [SILENT]. "
            "Otherwise write a short message: one line per alert, most important first, using the alert's own "
            "numbers. For an unusual or double charge, say what to check. Do not add advice beyond the alerts."
        ),
        slots=[_DELIVER],
        tags=["finance", "alerts"],
    )
    ctx.register_automation_blueprint(
        "money-digest",
        title="Weekly money digest",
        description="A weekly summary: spending vs budgets, what changed, bills coming up, and a chart.",
        category="finance",
        schedule_template="{minute} {hour} * * {dow}",
        prompt_template=(
            "Write the user's weekly money digest. Call money_overview, money_budgets (status) and money_alerts "
            "(list). Then call money_chart with kind=pace and include its path on its own line. Cover: spending "
            "this month vs the same point last month, any budget over or on pace to go over, the biggest "
            "changes, and bills due in the next 7 days. Under 120 words. Numbers only from the tools."
        ),
        slots=[
            {"name": "time", "type": "time", "label": "What time?", "default": "09:00"},
            {"name": "day", "type": "enum", "label": "Which day?", "default": "sunday",
             "options": ["sunday", "monday", "friday", "saturday"]},
            _DELIVER,
        ],
        tags=["finance", "weekly"],
    )
