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

  // the user's boards (app/dashboard.py): printed into the page, so the first paint is already right
  function layoutPayload() {
    try {
      return JSON.parse(document.getElementById("dashboard-layout").textContent);
    } catch (e) {
      return null;
    }
  }

  const BOARD_KEY = "mezaniya.board";
  function rememberBoard(id) {
    try { localStorage.setItem(BOARD_KEY, id); } catch (e) { /* private mode: not remembered */ }
  }
  function rememberedBoard() {
    try { return localStorage.getItem(BOARD_KEY); } catch (e) { return null; }
  }

  const clone = (x) => JSON.parse(JSON.stringify(x));

  // x-widget="w": puts a copy of <template id="widget-<w>"> here. Each widget's markup is in the
  // page once; a slot clones only the one it shows, and Alpine binds the copy to this slot's scope.
  Alpine.directive("widget", (el, { expression }, { evaluateLater, effect, cleanup }) => {
    const getId = evaluateLater(expression);
    let shown = null;
    effect(() => getId((id) => {
      if (id === shown) return;
      shown = id;
      Alpine.mutateDom(() => el.replaceChildren());
      const tpl = document.getElementById(`widget-${id}`);
      if (!tpl) return;
      const node = tpl.content.firstElementChild.cloneNode(true);
      // added with the observer paused, then initialised once here (not again by the observer)
      Alpine.mutateDom(() => el.appendChild(node));
      Alpine.initTree(node);
    }));
    cleanup(() => el.replaceChildren());
  });
  const MAX_PER_ROW = 3;

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
    layout: { boards: [{ id: "main", name: null, rows: [] }] },
    defaultLayout: null,
    widgetTitles: {},
    widgetIds: [],
    maxBoards: 10,
    boardId: "main",
    editing: false,
    draft: null,

    async init() {
      const p = layoutPayload();
      if (p) {
        this.layout = p.layout;
        this.defaultLayout = p.default;
        this.widgetTitles = p.widgets;
        this.widgetIds = Object.keys(p.widgets);
        this.maxBoards = p.max_boards;
      }
      const saved = rememberedBoard();
      this.boardId = this.layout.boards.some((b) => b.id === saved) ? saved : this.layout.boards[0].id;
      await this.loadMonths();
      if (!this.months.length) {
        this.error = tr("dashboard.no_months");
        return;
      }
      await this.load(this.initialMonth());
    },

    // ---------- boards and layout editing

    boards() {
      return (this.editing ? this.draft : this.layout).boards;
    },

    board() {
      return this.boards().find((b) => b.id === this.boardId) || this.boards()[0];
    },

    boardName(b, index) {
      if (b.name) return b.name;
      return index === 0 ? tr("dashboard.board_main") : tr("dashboard.board_n", { n: index + 1 });
    },

    widgetName(w) {
      return tr(this.widgetTitles[w]);
    },

    showBoard(id) {
      this.boardId = id;
      rememberBoard(id);
    },

    // RTL pages run right to left, so "before" points right there
    arrowBefore() {
      return document.dir === "rtl" ? "→" : "←";
    },
    arrowAfter() {
      return document.dir === "rtl" ? "←" : "→";
    },

    usedIn(w) {
      return this.boards().map((b, i) => (b.rows.some((r) => r.includes(w)) ? this.boardName(b, i) : null))
        .filter(Boolean);
    },

    startEdit() {
      this.draft = clone(this.layout);
      this.editing = true;
    },

    cancelEdit() {
      this.editing = false;
      this.draft = null;
      if (!this.layout.boards.some((b) => b.id === this.boardId)) this.showBoard(this.layout.boards[0].id);
    },

    async saveEdit() {
      const r = await this.request("PATCH", "/api/settings", { dashboards: { boards: this.draft.boards } });
      if (!r) return;
      this.layout = r.dashboards;
      this.editing = false;
      this.draft = null;
      if (!this.layout.boards.some((b) => b.id === this.boardId)) this.showBoard(this.layout.boards[0].id);
      this.notify(tr("dashboard.saved"));
    },

    // take widget wi out of row ri; returns it, and drops the row if it is now empty
    takeOut(rows, ri, wi) {
      const [w] = rows[ri].splice(wi, 1);
      if (!rows[ri].length) rows.splice(ri, 1);
      return w;
    },

    // ↑ / ↓: join the neighbouring row if it has room; otherwise a lone widget swaps rows with
    // it, and one that shares its row goes onto a row of its own on that side
    moveVertical(ri, wi, dir) {
      const rows = this.board().rows;
      const to = ri + dir;
      const hasRoom = to >= 0 && to < rows.length && rows[to].length < MAX_PER_ROW;
      if (rows[ri].length === 1) {
        if (to < 0 || to >= rows.length) return;
        if (hasRoom) {
          const [w] = rows.splice(ri, 1)[0];
          if (dir < 0) rows[to].push(w);
          else rows[ri].unshift(w);            // the row below has moved up to index ri
        } else {
          [rows[ri], rows[to]] = [rows[to], rows[ri]];
        }
        return;
      }
      const [w] = rows[ri].splice(wi, 1);
      if (hasRoom) {
        if (dir < 0) rows[to].push(w);
        else rows[to].unshift(w);
      } else {
        rows.splice(dir < 0 ? ri : ri + 1, 0, [w]);
      }
    },

    moveSideways(ri, wi, dir) {
      const row = this.board().rows[ri];
      const to = wi + dir;
      if (to < 0 || to >= row.length) return;
      [row[wi], row[to]] = [row[to], row[wi]];
    },

    ownRow(ri, wi) {
      const rows = this.board().rows;
      if (rows[ri].length < 2) return;
      const w = this.takeOut(rows, ri, wi);
      rows.splice(ri + 1, 0, [w]);
    },

    hideWidget(ri, wi) {
      this.takeOut(this.board().rows, ri, wi);
    },

    addWidget(w) {
      this.board().rows.push([w]);
    },

    copyTo(w, boardId) {
      const target = this.boards().find((b) => b.id === boardId);
      if (!target) return;
      target.rows.push([w]);
      this.notify(tr("dashboard.copied", { name: this.boardName(target, this.boards().indexOf(target)) }));
    },

    askName(current) {
      const name = prompt(tr("dashboard.board_name_prompt"), current || "");
      return name && name.trim() ? name.trim().slice(0, 40) : null;
    },

    addBoard() {
      if (this.draft.boards.length >= this.maxBoards) return;
      const name = this.askName(tr("dashboard.board_n", { n: this.draft.boards.length + 1 }));
      if (!name) return;
      const id = "b" + Date.now().toString(36);
      this.draft.boards.push({ id, name, rows: [] });
      this.showBoard(id);
    },

    renameBoard() {
      const b = this.board();
      const name = this.askName(this.boardName(b, this.boards().indexOf(b)));
      if (name) b.name = name;
    },

    deleteBoard() {
      const boards = this.draft.boards;
      if (boards.length < 2) return;
      const b = this.board();
      if (!confirm(tr("dashboard.delete_board_confirm", { name: this.boardName(b, boards.indexOf(b)) }))) return;
      boards.splice(boards.indexOf(b), 1);
      this.showBoard(boards[0].id);
    },

    resetLayout() {
      if (!confirm(tr("dashboard.reset_confirm"))) return;
      this.draft = clone(this.defaultLayout);
      this.showBoard(this.draft.boards[0].id);
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
