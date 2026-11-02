-- 0001: initial schema. Every table carries user_id; composite foreign keys
-- (x_id, user_id) → parent(id, user_id) make cross-user references impossible.
-- Money is REAL (converted to Decimal through str() in the repository).
--
-- Schema generation, checked at startup (connection.SCHEMA_GENERATION): bump both together
-- when an edit here means older beta databases can't be opened.
PRAGMA user_version = 3;
--
-- Note: ON DELETE SET NULL can't be used on composite keys (it would null user_id too),
-- so "set null on delete" is done with BEFORE DELETE triggers instead.

CREATE TABLE users (
  id INTEGER PRIMARY KEY,
  username TEXT NOT NULL UNIQUE COLLATE NOCASE,
  password_hash TEXT NOT NULL,          -- scrypt:... or #plain# (manual reset)
  -- part of every session's fingerprint: bumping it logs that user out on every device
  session_version INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  last_login_at TEXT,
  last_seen_at TEXT                     -- refreshed at most once a minute: "who is connected now"
);

-- Settings are key/value rows, so a new setting is one INSERT into default_settings, not a
-- schema change. A user without a row for a key gets default_value. The Python registry in
-- app/db/repo/settings.py validates values (type and range) since TEXT can't CHECK them here;
-- a test keeps the registry and this table in step.
CREATE TABLE default_settings (
  key TEXT PRIMARY KEY,
  default_value TEXT NOT NULL,
  type TEXT NOT NULL CHECK (type IN ('rate','number','text','json'))
);

CREATE TABLE user_settings (
  user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  key TEXT NOT NULL REFERENCES default_settings(key),
  value TEXT NOT NULL,
  value2 TEXT,                          -- customisation details (JSON), e.g. a custom theme's colours
  PRIMARY KEY (user_id, key)
);

-- Currencies a user can pick (app/currency.py holds the same list; a test keeps them equal).
-- Symbols and names are strings.json "currency.<code>.symbol" / ".name". The user's base
-- currency is the setting 'currency'; an item's `currency` column is NULL in the base currency
-- (so most rows never name one) or the code of another currency its amounts are kept in.
CREATE TABLE currencies (
  code TEXT PRIMARY KEY CHECK (length(code) = 3 AND code = upper(code)),
  decimals INTEGER NOT NULL CHECK (decimals BETWEEN 0 AND 3),
  sort_order INTEGER NOT NULL
);

INSERT INTO currencies (code, decimals, sort_order) VALUES
  ('SAR', 2, 1),
  ('EGP', 2, 2),
  ('AED', 2, 3),
  ('KWD', 3, 4),
  ('QAR', 2, 5),
  ('BHD', 3, 6),
  ('OMR', 3, 7),
  ('JOD', 3, 8),
  ('USD', 2, 9),
  ('EUR', 2, 10),
  ('GBP', 2, 11),
  ('TRY', 2, 12),
  ('MAD', 2, 13);

INSERT INTO default_settings (key, default_value, type) VALUES
  ('excellent_rate', '0.2', 'rate'),
  ('good_rate', '0.1', 'rate'),
  ('max_debt_rate', '0.33', 'rate'),
  ('max_luxury_rate', '0.3', 'rate'),
  ('emergency_cut_rate', '0.3', 'rate'),
  ('saving_rate_em_incomplete', '0.2', 'rate'),
  ('saving_rate_em_complete', '0.5', 'rate'),
  ('emergency_ref_months', '3', 'number'),
  ('emergency_ref_factor', '1.25', 'number'),
  ('currency', 'SAR', 'text'),          -- the base currency: a code from currencies
  ('theme', 'auto', 'text'),
  -- the dashboards' layout (app/dashboard.py); a test keeps it equal to default_dashboards()
  ('dashboards', '{"v":1,"boards":[{"id":"main","name":null,"rows":[["incomes","summary"],["expenses"],["by_type","payments","suggestions"],["warnings"],["emergency","savings","progress"],["goals"],["daily","notes"],["details"]]}],"seen":["summary","suggestions","warnings","incomes","emergency","savings","expenses","goals","payments","by_type","progress","daily","details","notes"]}', 'json');

-- Built-in colour themes: five base colours each; app/themes.py derives the rest. Names are
-- strings.json "theme.<id>". A user's choice is user_settings key 'theme' ('auto' = follow
-- Windows light/dark, a theme id, or 'custom' with the colours as JSON in value2).
CREATE TABLE themes (
  id TEXT PRIMARY KEY,
  sort_order INTEGER NOT NULL,
  mode TEXT NOT NULL CHECK (mode IN ('light','dark')),
  colors TEXT NOT NULL                  -- JSON {"text","background","primary","secondary","accent"}
);

INSERT INTO themes (id, sort_order, mode, colors) VALUES
  ('ember', 1, 'dark', '{"text":"#fcf3ee","background":"#090401","primary":"#d2481e","secondary":"#3b8212","accent":"#18aa29"}'),
  ('sky', 2, 'light', '{"text":"#110803","background":"#b8e5f2","primary":"#e1572d","secondary":"#a6ed7d","accent":"#55e766"}'),
  ('lavender', 3, 'light', '{"text":"#120c16","background":"#e3d4f2","primary":"#8164a6","secondary":"#cba5ba","accent":"#b56e82"}'),
  ('midnight', 4, 'dark', '{"text":"#dfe3ee","background":"#080b12","primary":"#94aadf","secondary":"#1b3a81","accent":"#245de2"}'),
  ('mezaniya_light', 5, 'light', '{"text":"#1f2933","background":"#f4f6f8","primary":"#0f766e","secondary":"#cde2fb","accent":"#2a78d6"}'),
  ('mezaniya_dark', 6, 'dark', '{"text":"#e6edf3","background":"#0f1419","primary":"#2dd4bf","secondary":"#1b2c42","accent":"#3987e5"}'),
  ('sand', 7, 'light', '{"text":"#2b2118","background":"#f6f0e6","primary":"#9a5b13","secondary":"#d9c7a8","accent":"#2f6f62"}'),
  ('mint', 8, 'light', '{"text":"#0f241c","background":"#eef8f3","primary":"#0f766e","secondary":"#bfe3d4","accent":"#7c3aed"}'),
  ('ocean', 9, 'dark', '{"text":"#e3eef2","background":"#0b1a20","primary":"#38bdf8","secondary":"#1e3a4a","accent":"#f59e0b"}');

-- Types and categories made at registration carry name_key ('savings' → strings.json
-- "seed.type.savings") and are shown in the viewer's language; name stays NULL until the user
-- renames one, and then that name is shown in every language. Types the user adds have name only.
CREATE TABLE expense_types (
  id INTEGER PRIMARY KEY,
  user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  name_key TEXT,
  name TEXT,
  role TEXT NOT NULL DEFAULT 'other'
       CHECK (role IN ('essential','luxury','debt','savings','emergency','other')),
  is_system INTEGER NOT NULL DEFAULT 0,
  sort_order INTEGER NOT NULL DEFAULT 0,
  CHECK (name IS NOT NULL OR name_key IS NOT NULL),
  UNIQUE (user_id, name),
  UNIQUE (user_id, name_key),
  UNIQUE (id, user_id)
);

CREATE TABLE goal_categories (
  id INTEGER PRIMARY KEY,
  user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  name_key TEXT,                        -- strings.json "seed.category.<key>", as for expense_types
  name TEXT,
  sort_order INTEGER NOT NULL DEFAULT 0,
  CHECK (name IS NOT NULL OR name_key IS NOT NULL),
  UNIQUE (user_id, name),
  UNIQUE (user_id, name_key),
  UNIQUE (id, user_id)
);

-- Demo names: rows the demo creates (recurring items and month items) carry name_key
-- ('rent' → strings.json "demo.rent") with the name NULL, so they read in the viewer's language,
-- and copying them into a new month keeps them that way. Once the user renames one, the name is
-- theirs and reads the same in every language. Rows the user adds have a name only.
CREATE TABLE recurring_templates (
  id INTEGER PRIMARY KEY,
  user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  kind TEXT NOT NULL CHECK (kind IN ('income','expense')),
  name_key TEXT,                        -- see "Demo names" above
  name TEXT,
  type_id INTEGER,
  amount REAL CHECK (amount IS NULL OR amount >= 0),     -- NULL = variable price
  currency TEXT REFERENCES currencies(code),             -- NULL = the base currency (see "Currencies")
  is_savings_withdrawal INTEGER NOT NULL DEFAULT 0,
  active INTEGER NOT NULL DEFAULT 1,
  notes TEXT,
  sort_order INTEGER NOT NULL DEFAULT 0,
  CHECK (name IS NOT NULL OR name_key IS NOT NULL),
  UNIQUE (id, user_id),
  CHECK ((kind = 'expense') = (type_id IS NOT NULL)),
  FOREIGN KEY (type_id, user_id) REFERENCES expense_types(id, user_id)
);

CREATE TABLE budgets (
  id INTEGER PRIMARY KEY,
  user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  year INTEGER NOT NULL CHECK (year BETWEEN 2000 AND 2200),
  month INTEGER NOT NULL CHECK (month BETWEEN 1 AND 12),
  emergency_current REAL NOT NULL DEFAULT 0 CHECK (emergency_current >= 0),
  emergency_min REAL NOT NULL DEFAULT 0 CHECK (emergency_min >= 0),
  savings_current REAL NOT NULL DEFAULT 0 CHECK (savings_current >= 0),
  -- the month this one was copied from (rollover). Months can be created in any order;
  -- carry-overs are kept in sync only along this link.
  source_budget_id INTEGER,
  notes TEXT,                           -- the month's general notes; not copied to the next month
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  UNIQUE (user_id, year, month),
  UNIQUE (id, user_id),
  FOREIGN KEY (source_budget_id, user_id) REFERENCES budgets(id, user_id)
);

-- Savings / emergency money kept in another currency (e.g. EGP cash while the base is SAR),
-- one row per month, kind and currency; budgets.savings_current / emergency_current stay the
-- part in the base currency. A new month copies these rows as they are.
CREATE TABLE budget_balances (
  id INTEGER PRIMARY KEY,
  user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  budget_id INTEGER NOT NULL,
  kind TEXT NOT NULL CHECK (kind IN ('savings','emergency')),
  currency TEXT NOT NULL REFERENCES currencies(code),
  amount REAL NOT NULL DEFAULT 0 CHECK (amount >= 0),
  UNIQUE (budget_id, kind, currency),
  UNIQUE (id, user_id),
  FOREIGN KEY (budget_id, user_id) REFERENCES budgets(id, user_id) ON DELETE CASCADE
);

-- Exchange rates, per user: 1 `currency` = `rate` of `base` (the base currency when the row
-- was written; rows for another base are simply not used). A rate holds from its month
-- (from_ym = year * 100 + month) until the next row of the same currency, so periods can't
-- overlap or leave holes. 'manual' rows are the user's and always win: fetching online
-- (source 'online') never writes over one or into a period one covers (app/db/repo/rates.py).
CREATE TABLE exchange_rates (
  id INTEGER PRIMARY KEY,
  user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  base TEXT NOT NULL REFERENCES currencies(code),
  currency TEXT NOT NULL REFERENCES currencies(code),
  from_ym INTEGER NOT NULL CHECK (from_ym BETWEEN 200001 AND 220012 AND from_ym % 100 BETWEEN 1 AND 12),
  rate REAL NOT NULL CHECK (rate > 0),
  source TEXT NOT NULL CHECK (source IN ('manual','online')),
  updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  CHECK (currency <> base),
  UNIQUE (user_id, base, currency, from_ym),
  UNIQUE (id, user_id)
);

CREATE TABLE incomes (
  id INTEGER PRIMARY KEY,
  user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  budget_id INTEGER NOT NULL,
  name_key TEXT,
  source TEXT,
  amount REAL NOT NULL DEFAULT 0 CHECK (amount >= 0),
  currency TEXT REFERENCES currencies(code),
  is_savings_withdrawal INTEGER NOT NULL DEFAULT 0,
  template_id INTEGER,
  needs_amount INTEGER NOT NULL DEFAULT 0,
  notes TEXT,
  sort_order INTEGER NOT NULL DEFAULT 0,
  CHECK (source IS NOT NULL OR name_key IS NOT NULL),
  UNIQUE (id, user_id),
  FOREIGN KEY (budget_id, user_id) REFERENCES budgets(id, user_id) ON DELETE CASCADE,
  FOREIGN KEY (template_id, user_id) REFERENCES recurring_templates(id, user_id)
);

CREATE TABLE expenses (
  id INTEGER PRIMARY KEY,
  user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  budget_id INTEGER NOT NULL,
  name_key TEXT,
  name TEXT,
  type_id INTEGER NOT NULL,
  currency TEXT REFERENCES currencies(code),            -- every amount of the row is in it
  cash_amount REAL NOT NULL DEFAULT 0 CHECK (cash_amount >= 0),
  inst_balance REAL NOT NULL DEFAULT 0 CHECK (inst_balance >= 0),        -- remaining installment balance
  inst_count INTEGER NOT NULL DEFAULT 0 CHECK (inst_count >= 0),          -- remaining payments
  inst_original_count INTEGER CHECK (inst_original_count IS NULL OR inst_original_count >= 0),
  carried_in REAL NOT NULL DEFAULT 0 CHECK (carried_in >= 0),
  status TEXT NOT NULL DEFAULT 'unpaid' CHECK (status IN ('unpaid','paid','deferred')),
  deferred_amount REAL NOT NULL DEFAULT 0 CHECK (deferred_amount >= 0),
  paid_at TEXT CHECK (paid_at IS NULL OR date(paid_at) IS paid_at),       -- YYYY-MM-DD (IS: a bad date gives NULL)
  notes TEXT,
  template_id INTEGER,
  prev_expense_id INTEGER,
  needs_amount INTEGER NOT NULL DEFAULT 0,
  sort_order INTEGER NOT NULL DEFAULT 0,
  CHECK (name IS NOT NULL OR name_key IS NOT NULL),
  UNIQUE (id, user_id),
  FOREIGN KEY (budget_id, user_id) REFERENCES budgets(id, user_id) ON DELETE CASCADE,
  FOREIGN KEY (type_id, user_id) REFERENCES expense_types(id, user_id),
  FOREIGN KEY (template_id, user_id) REFERENCES recurring_templates(id, user_id),
  FOREIGN KEY (prev_expense_id, user_id) REFERENCES expenses(id, user_id)
);

CREATE TABLE goals (
  id INTEGER PRIMARY KEY,
  user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  budget_id INTEGER NOT NULL,
  name_key TEXT,
  name TEXT,
  category_id INTEGER,
  currency TEXT REFERENCES currencies(code),            -- target and manual amount are in it
  target_amount REAL CHECK (target_amount IS NULL OR target_amount >= 0),
  priority REAL CHECK (priority IS NULL OR priority >= 0),               -- NULL = auto (≠ 0)
  manual_allocation REAL CHECK (manual_allocation IS NULL OR manual_allocation >= 0),
  notes TEXT,
  sort_order INTEGER NOT NULL DEFAULT 0,
  CHECK (name IS NOT NULL OR name_key IS NOT NULL),
  UNIQUE (id, user_id),
  CHECK (priority IS NULL OR manual_allocation IS NULL),
  FOREIGN KEY (budget_id, user_id) REFERENCES budgets(id, user_id) ON DELETE CASCADE,
  FOREIGN KEY (category_id, user_id) REFERENCES goal_categories(id, user_id)
);

CREATE INDEX ix_incomes_budget ON incomes(user_id, budget_id);
CREATE INDEX ix_expenses_budget ON expenses(user_id, budget_id);
CREATE INDEX ix_expenses_prev ON expenses(prev_expense_id);
CREATE INDEX ix_expenses_template ON expenses(template_id);
CREATE INDEX ix_incomes_template ON incomes(template_id);
CREATE INDEX ix_goals_budget ON goals(user_id, budget_id);
CREATE INDEX ix_goals_category ON goals(category_id);
CREATE INDEX ix_templates_type ON recurring_templates(type_id);
CREATE INDEX ix_budgets_source ON budgets(user_id, source_budget_id);

-- "ON DELETE SET NULL" for the composite references
CREATE TRIGGER trg_template_delete BEFORE DELETE ON recurring_templates BEGIN
  UPDATE incomes SET template_id = NULL WHERE template_id = OLD.id;
  UPDATE expenses SET template_id = NULL WHERE template_id = OLD.id;
END;

CREATE TRIGGER trg_goal_category_delete BEFORE DELETE ON goal_categories BEGIN
  UPDATE goals SET category_id = NULL WHERE category_id = OLD.id;
END;

CREATE TRIGGER trg_expense_delete BEFORE DELETE ON expenses BEGIN
  UPDATE expenses SET prev_expense_id = NULL WHERE prev_expense_id = OLD.id;
END;

CREATE TRIGGER trg_budget_delete BEFORE DELETE ON budgets BEGIN
  UPDATE budgets SET source_budget_id = NULL WHERE source_budget_id = OLD.id;
END;
