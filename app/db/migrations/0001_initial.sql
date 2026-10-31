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

CREATE TABLE user_settings (
  user_id INTEGER PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
  excellent_rate REAL NOT NULL DEFAULT 0.20,
  good_rate REAL NOT NULL DEFAULT 0.10,
  max_debt_rate REAL NOT NULL DEFAULT 0.33,
  max_luxury_rate REAL NOT NULL DEFAULT 0.30,
  emergency_cut_rate REAL NOT NULL DEFAULT 0.30,
  saving_rate_em_incomplete REAL NOT NULL DEFAULT 0.20,
  saving_rate_em_complete REAL NOT NULL DEFAULT 0.50,
  emergency_ref_months REAL NOT NULL DEFAULT 3,
  emergency_ref_factor REAL NOT NULL DEFAULT 1.25,
  currency TEXT NOT NULL,               -- set from the user's language when the account is created
  CHECK (excellent_rate >= 0 AND good_rate >= 0 AND max_debt_rate >= 0 AND max_luxury_rate >= 0
         AND emergency_cut_rate >= 0 AND saving_rate_em_incomplete >= 0
         AND saving_rate_em_complete >= 0 AND emergency_ref_months >= 0 AND emergency_ref_factor >= 0)
);

CREATE TABLE expense_types (
  id INTEGER PRIMARY KEY,
  user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  name TEXT NOT NULL,
  role TEXT NOT NULL DEFAULT 'other'
       CHECK (role IN ('essential','luxury','debt','savings','emergency','other')),
  is_system INTEGER NOT NULL DEFAULT 0,
  sort_order INTEGER NOT NULL DEFAULT 0,
  UNIQUE (user_id, name),
  UNIQUE (id, user_id)
);

CREATE TABLE goal_categories (
  id INTEGER PRIMARY KEY,
  user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  name TEXT NOT NULL,
  sort_order INTEGER NOT NULL DEFAULT 0,
  UNIQUE (user_id, name),
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
