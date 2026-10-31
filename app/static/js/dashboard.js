// Monthly dashboard. The server computes everything; every edit sends one field and
// gets the whole recomputed dashboard back.
document.addEventListener("alpine:init", () => {
  const moneyFmt = new Intl.NumberFormat("en-US", { maximumFractionDigits: 2 });

  function nextPeriod(year, month) {
    return month === 12 ? [year + 1, 1] : [year, month + 1];
  }

  function emptyExpense(typeId) {
    return { name: "", type_id: typeId || "", cash_amount: "", inst_balance: "", inst_count: "" };
  }

  function emptyWizard() {
    return { open: false, year: null, month: null, plan: null, carry: [], addReturn: false, dropCompleted: false,
             balancesFor: null, balances: { emergency_current: 0, emergency_min: 0, savings_current: 0 } };
  }

  Alpine.data("dashboard", () => ({
    ...pageMixin(),
    months: [],
    budgetId: null,
    d: null,
    error: "",
    newIncome: { source: "", amount: "", is_savings_withdrawal: false },
    newExpense: emptyExpense(),
    newGoal: { name: "", target_amount: "" },
    openNotes: {},
    hoverDay: null,
    showDailyTable: false,
    wizard: emptyWizard(),

    async init() {
      await this.loadMonths();
      if (!this.months.length) {
        this.error = tr("dashboard.no_months");
        return;
      }
      await this.load(this.initialMonth());
    },

    // ---------- loading

    async loadMonths() {
      const r = await this.request("GET", "/api/budgets");
      if (r) this.months = r;
    },

    initialMonth() {
      const fromUrl = Number(new URLSearchParams(location.search).get("m"));
      if (this.months.some((m) => m.id === fromUrl)) return fromUrl;
      const now = new Date();
      const current = this.months.find((m) => m.year === now.getFullYear() && m.month === now.getMonth() + 1);
      return (current || this.months[this.months.length - 1]).id;
    },

    async load(id) {
      const r = await this.request("GET", `/api/budgets/${id}`);
      if (!r) return;
      this.budgetId = id;
      this.setDashboard(r);
      history.replaceState(null, "", `?m=${id}`);
    },

    setDashboard(d) {
      this.d = d;
      if (!this.newExpense.type_id && d.expense_types.length) {
        const essential = d.expense_types.find((t) => t.role === "essential");
        this.newExpense.type_id = (essential || d.expense_types[0]).id;
      }
    },

    // ---------- server calls

    onRequestError() {
      if (this.d) this.d = JSON.parse(JSON.stringify(this.d));   // re-render: undo what was typed
    },

    async write(method, url, body) {
      const r = await this.request(method, url, body);
      if (r && r.dashboard) this.setDashboard(r.dashboard);
      return r;
    },

    budgetUrl(path = "") {
      return `/api/budgets/${this.budgetId}${path}`;
    },

    // ---------- edits

    async setBalance(field, value) {
      const r = await this.request("PATCH", this.budgetUrl(), { [field]: this.num(value) });
      if (r) this.setDashboard(r);
    },

    updateItem(kind, item, field, value) {
      if (item[field] === value) return;
      return this.write("PATCH", this.budgetUrl(`/${kind}/${item.id}`), { [field]: value });
    },

    // Today's date (the user's own clock) is stamped when money actually leaves: marking an
    // item paid, or deferring only part of it. Deferring all of it pays nothing, so no date.
    setStatus(expense, status) {
      const body = { status };
      if (status === "paid" && !expense.paid_at) body.paid_at = this.today();
      return this.write("PATCH", this.budgetUrl(`/expenses/${expense.id}`), body);
    },

    setDeferred(expense, value) {
      const body = { deferred_amount: this.num(value) };
      if (body.deferred_amount < expense.due && !expense.paid_at) body.paid_at = this.today();
      return this.write("PATCH", this.budgetUrl(`/expenses/${expense.id}`), body);
    },

    today() {
      const now = new Date();
      const pad = (n) => String(n).padStart(2, "0");
      return `${now.getFullYear()}-${pad(now.getMonth() + 1)}-${pad(now.getDate())}`;
    },

    toggleNotes(kind, item) {
      const key = `${kind}-${item.id}`;
      this.openNotes = { ...this.openNotes, [key]: !this.openNotes[key] };
    },

    notesOpen(kind, item) {
      return !!this.openNotes[`${kind}-${item.id}`];
    },

    async removeItem(kind, item) {
      const name = item.name || item.source;
      if (!confirm(tr("common.confirm_delete", { name }))) return;
      await this.write("DELETE", this.budgetUrl(`/${kind}/${item.id}`));
    },

    async addIncome() {
      const n = this.newIncome;
      if (!n.source.trim()) return this.notify(tr("incomes.err_empty"));
      const r = await this.write("POST", this.budgetUrl("/incomes"), {
        source: n.source, amount: this.num(n.amount), is_savings_withdrawal: n.is_savings_withdrawal,
      });
      if (r) this.newIncome = { source: "", amount: "", is_savings_withdrawal: false };
    },

    async addExpense() {
      const n = this.newExpense;
      if (!n.name.trim()) return this.notify(tr("expenses.err_empty"));
      const r = await this.write("POST", this.budgetUrl("/expenses"), {
        name: n.name, type_id: Number(n.type_id), cash_amount: this.num(n.cash_amount),
        inst_balance: this.num(n.inst_balance), inst_count: this.num(n.inst_count),
      });
      if (r) this.newExpense = emptyExpense(n.type_id);
    },

    async addGoal() {
      const n = this.newGoal;
      if (!n.name.trim()) return this.notify(tr("goals.err_empty"));
      const r = await this.write("POST", this.budgetUrl("/goals"), {
        name: n.name, target_amount: this.numOrNull(n.target_amount),
      });
      if (r) this.newGoal = { name: "", target_amount: "" };
    },

    nextLabel() {
      if (!this.months.length) return "";
      const last = this.months[this.months.length - 1];
      const [y, m] = nextPeriod(last.year, last.month);
      return `${String(m).padStart(2, "0")}/${y}`;
    },

    async applyTemplates() {
      const r = await this.write("POST", this.budgetUrl("/apply-templates"));
      if (r) this.notify(r.added ? tr("expenses.templates_added", { items: tr("count.items", { n: r.added }) })
                                 : tr("expenses.templates_none_missing"));
    },

    // ---------- new month wizard: a preview of everything the new month will start with

    async openWizard() {
      const last = this.months[this.months.length - 1];
      const [year, month] = nextPeriod(last.year, last.month);
      this.wizard = { ...emptyWizard(), open: true, year, month };
      await this.previewWizard(true);
    },

    closeWizard() {
      if (this.wizard.open) this.wizard = emptyWizard();
    },

    // A month after the latest one is copied from it (mode "copy"); an earlier month is
    // created empty with no automatic carry-over (mode "blank").
    async previewWizard(resetBalances = false) {
      const w = this.wizard;
      const body = { year: w.year, month: w.month };
      if (w.carry.length) body.carry_unpaid_ids = w.carry;
      const plan = await this.request("POST", "/api/budgets/rollover-preview", body);
      if (!plan) return;
      w.plan = plan;
      if (plan.mode === "copy" && (resetBalances || w.balancesFor !== plan.target.label)) {
        w.balances = { ...plan.balances };
        w.balancesFor = plan.target.label;
        w.addReturn = false;
      }
      if (plan.mode === "blank") w.carry = [];
    },

    setWizardPeriod(year, month) {
      this.wizard.year = year;
      this.wizard.month = month;
      return this.previewWizard();
    },

    toggleCarry(id) {
      const w = this.wizard;
      w.carry = w.carry.includes(id) ? w.carry.filter((x) => x !== id) : [...w.carry, id];
      return this.previewWizard();
    },

    toggleReturn(checked) {
      const w = this.wizard;
      const delta = checked ? w.plan.return_to_savings : -w.plan.return_to_savings;
      w.balances.savings_current = Math.max(0, Math.round((Number(w.balances.savings_current) + delta) * 100) / 100);
      w.addReturn = checked;
    },

    completedGoals() {
      return this.wizard.plan.goals.filter((g) => g.completed).map((g) => g.name);
    },

    planBase(e) {
      const installment = e.inst_count > 0 ? e.inst_balance / e.inst_count : e.inst_balance;
      return e.cash_amount + installment;
    },

    async confirmWizard() {
      const w = this.wizard;
      let body = { year: w.year, month: w.month };
      if (w.plan.mode === "copy") {
        const balances = Object.fromEntries(Object.entries(w.balances).map(([k, v]) => [k, this.num(v)]));
        body = { ...body, copy_from_previous: true, carry_unpaid_ids: w.carry,
                 drop_completed_goals: w.dropCompleted, ...balances };
      }
      const r = await this.request("POST", "/api/budgets", body);
      if (!r) return;
      this.wizard = emptyWizard();
      await this.loadMonths();
      this.budgetId = r.budget.id;
      this.setDashboard(r);
      history.replaceState(null, "", `?m=${r.budget.id}`);
      this.notify(tr("dashboard.month_created", { label: `${String(r.budget.month).padStart(2, "0")}/${r.budget.year}` }));
    },

    // ---------- view helpers

    sortedWarnings() {
      return [...this.d.warnings].sort((a, b) => Number(b.active) - Number(a.active));
    },

    // ---------- charts

    typeBars() {
      const rows = this.d.charts.by_type.filter((s) => s.amount > 0);
      const max = Math.max(0, ...rows.map((s) => s.amount));
      return rows
        .sort((a, b) => b.amount - a.amount)
        .map((s) => ({ ...s, width: max ? (s.amount / max) * 100 : 0 }));
    },

    hasDailyData() {
      return this.d.charts.daily.by_day.some((x) => x > 0);
    },

    // a clean top for the y-axis: 1, 2, 2.5 or 5 × 10^k
    dailyScale() {
      const max = Math.max(...this.d.charts.daily.by_day);
      if (max <= 0) return 1;
      const p = 10 ** Math.floor(Math.log10(max));
      return [1, 2, 2.5, 5, 10].map((m) => m * p).find((v) => v >= max);
    },

    dailyTicks() {
      const top = this.dailyScale();
      return [0, top / 2, top];
    },

    peakDay() {
      const days = this.d.charts.daily.by_day;
      return days.indexOf(Math.max(...days));
    },

    dayTick(i) {
      const n = this.d.charts.daily.by_day.length;
      const lastFits = n % 5 === 0 || n % 5 >= 3;     // keep the last day's label off its neighbour
      return i === 0 || (i === n - 1 && lastFits) || (i + 1) % 5 === 0 ? String(i + 1) : "";
    },

    tipStyle() {
      const n = this.d.charts.daily.by_day.length;
      const r = (this.hoverDay + 0.5) / n;               // distance from the start side (day 1)
      if (r < 0.12) return "inset-inline-start:0";
      if (r > 0.88) return "inset-inline-end:0";
      const shift = document.dir === "rtl" ? "50%" : "-50%";
      return `inset-inline-start:${r * 100}%;transform:translateX(${shift})`;
    },

    compact(x) {
      return x >= 1000 ? `${moneyFmt.format(Math.round(x / 100) / 10)}K` : moneyFmt.format(Math.round(x));
    },

    progress(bar) {
      const total = bar.available + bar.remaining;
      return total > 0 ? bar.available / total : 0;
    },

    sum(rows, key) {
      return rows.reduce((acc, r) => acc + (r[key] || 0), 0);
    },

    money(x) {
      return x === null || x === undefined ? "" : moneyFmt.format(x);
    },

    pct(x) {
      return x === null || x === undefined ? "" : `${(x * 100).toFixed(1)}%`;
    },

    pctPlain(x) {
      return x === null || x === undefined ? "" : String(Math.round(x * 1000) / 10);
    },

    num(v) {
      return v === "" || v === null || v === undefined ? 0 : Number(v);
    },

    numOrNull(v) {
      return v === "" || v === null || v === undefined ? null : Number(v);
    },

    percentToRatio(v) {
      return v === "" ? null : Math.round(Number(v) * 10000) / 1000000;
    },
  }));
});
