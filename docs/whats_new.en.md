# What's new in Mezaniya

[العربية](whats_new.md)

## Version 0.4.1 (beta)

- A full user guide for the website and the server manager, as a PDF inside the setup and the portable copy; the server manager's “User guide” button opens it.
- An About page on the website with the version, the license, the GitHub link and what's new in every release, and an About button in the server manager.
- Fixed texts that showed up on the website as raw keys instead of the translation: the missing exchange rate alert and its tooltips, and the name of “Month notes” when customizing.
- Documentation in the `docs` folder, with a tool that turns any of its files into Word and PDF.
- Works with 0.4.0 and 0.3.0 data as is, without any conversion.

## Version 0.4.0 (beta)

- Manage languages from the server manager: add a new language, edit it, turn it on or off, and delete it.
- Edit any text of the program from the server manager, with search and an “untranslated only” filter; changes show on the website within moments, without a restart.
- Export texts for translation as an Excel or CSV file, then import the translation, with checks for placeholders and Excel errors.
- Texts are stored in `languages.db` instead of `strings.json`, so your edits survive program updates.
- The language button becomes a 🌐 menu when more than two languages are on, on the website and in the server manager.
- Backups include languages and texts, and restoring asks whether to restore them too.
- When it finds a database from another version, the server manager offers to rename it, delete it, or upgrade it when possible.
- Better Arabic typing in the server manager's fields, and copy and paste with any keyboard layout.
- The “م” logo next to the program's name on every page.
- Works with 0.3.0 (beta) data as is, without any conversion.

## Version 0.3.0 (beta)

- Several currencies: a base currency from the international list, and a currency for each income, expense or goal.
- Emergency and savings balances in other currencies.
- Exchange rates per period, written in whichever direction you know; the reverse is worked out for you.
- Fetching exchange rates online (off by default), with a button to test the source, and rates can still be changed by hand.
- Each user's own list of currencies, and a search window with every currency in the world.
- Notes for each month, in a new dashboard widget.
- Fixes in the expenses table.
- The version number in the server manager, and refusing to restore a backup from another version.
- Not compatible with 0.2.0 and 0.1.0 data.

## Version 0.2.0 (beta)

- Flexible, extensible user settings, with defaults for new users.
- The built-in expense and goal type names show in the language of whoever uses the program.
- Ready-made colour themes (light and dark), an automatic theme that follows Windows' light or dark mode, and your own colours with a live preview.
- Customizing the dashboard: reorder, hide and repeat widgets.
- Several dashboards per user (up to 10), each with its own name and layout.
- A portable copy that runs without installing, with its data beside it.
- Not compatible with 0.1.0 data.

## Version 0.1.0 (beta)

- The program's first release, after turning the `mezaniya.xlsx` spreadsheet into a full web app.
- Runs on your computer and opens from any browser or phone on the home network.
- Several users, each with their own separate data and settings.
- Arabic and English interface, with the page direction switching automatically.
- Incomes, expenses and installments for each month, with payment status and date tracking.
- Carrying a whole or partial amount of an item over to the next month.
- Creating a new month from the previous one, carrying over balances, installments and recurring items.
- The month's summary, overall status, monthly suggestions and warnings.
- Emergency fund, savings, and automatic distribution of savings across goals.
- Charts of expenses by type and spending by day.
- Per-user settings: criteria, expense and goal types, and recurring items.
- A graphical server manager: start and stop, user management, and temporary passwords.
- Automatic and manual backups, and admin commands from the command line.
- A demo user with three months of ready data.
- A Windows installer.
