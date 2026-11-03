"""The currency list: Python, the currencies table and strings.json agree."""
from app import currency, i18n
from app.db import connect


def test_table_matches_the_python_list(tmp_path):
    from app.db.connection import migrate
    c = connect(tmp_path / "c.db")
    migrate(c)
    rows = c.execute("SELECT code, decimals FROM currencies ORDER BY sort_order").fetchall()
    assert [(r[0], r[1]) for r in rows] == [(currency.DEFAULT, currency.CURRENCIES[currency.DEFAULT])]
    assert len(currency.CURRENCIES) > 150                            # ISO 4217 in use today
    assert c.execute("SELECT default_value FROM default_settings WHERE key = 'currency'").fetchone()[0] \
        == currency.DEFAULT
    c.close()


def test_every_currency_has_a_symbol_and_name():
    for code in currency.CURRENCIES:
        assert i18n.has(f"currency.{code}.symbol") and i18n.has(f"currency.{code}.name"), code
    assert currency.symbol("SAR", "ar") == "ر.س" and currency.symbol("SAR", "en") == "SAR"
