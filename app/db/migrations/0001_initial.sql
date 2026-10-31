-- 0001: initial schema. Every table carries user_id; composite foreign keys
-- (x_id, user_id) → parent(id, user_id) make cross-user references impossible.
-- Money is REAL (converted to Decimal through str() in the repository).
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
  ('currency', '', 'text');             -- set from the user's language when the account is created

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

CREATE TABLE recurring_templates (
  id INTEGER PRIMARY KEY,
  user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  kind TEXT NOT NULL CHECK (kind IN ('income','expense')),
  name TEXT NOT NULL,
  type_id INTEGER,
  amount REAL CHECK (amount IS NULL OR amount >= 0),     -- NULL = variable price
  is_savings_withdrawal INTEGER NOT NULL DEFAULT 0,
  active INTEGER NOT NULL DEFAULT 1,
  notes TEXT,
  sort_order INTEGER NOT NULL DEFAULT 0,
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
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  UNIQUE (user_id, year, month),
  UNIQUE (id, user_id),
  FOREIGN KEY (source_budget_id, user_id) REFERENCES budgets(id, user_id)
);

CREATE TABLE incomes (
  id INTEGER PRIMARY KEY,
  user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  budget_id INTEGER NOT NULL,
  source TEXT NOT NULL,
  amount REAL NOT NULL DEFAULT 0 CHECK (amount >= 0),
  is_savings_withdrawal INTEGER NOT NULL DEFAULT 0,
  template_id INTEGER,
  needs_amount INTEGER NOT NULL DEFAULT 0,
  notes TEXT,
  sort_order INTEGER NOT NULL DEFAULT 0,
  UNIQUE (id, user_id),
  FOREIGN KEY (budget_id, user_id) REFERENCES budgets(id, user_id) ON DELETE CASCADE,
  FOREIGN KEY (template_id, user_id) REFERENCES recurring_templates(id, user_id)
);

CREATE TABLE expenses (
  id INTEGER PRIMARY KEY,
  user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  budget_id INTEGER NOT NULL,
  name TEXT NOT NULL,
  type_id INTEGER NOT NULL,
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
  name TEXT NOT NULL,
  category_id INTEGER,
  target_amount REAL CHECK (target_amount IS NULL OR target_amount >= 0),
  priority REAL CHECK (priority IS NULL OR priority >= 0),               -- NULL = auto (≠ 0)
  manual_allocation REAL CHECK (manual_allocation IS NULL OR manual_allocation >= 0),
  notes TEXT,
  sort_order INTEGER NOT NULL DEFAULT 0,
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
