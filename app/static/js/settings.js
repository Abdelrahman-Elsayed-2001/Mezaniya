// Settings: criteria, expense types, goal categories, recurring items, account.
document.addEventListener("alpine:init", () => {
  // rate fields shown as percentages; labels are settings.<key>, hints are the given keys
  const RATE_FIELDS = [
    { key: "excellent_rate", hint: "settings.excellent_rate_hint" },
    { key: "good_rate", hint: "settings.good_rate_hint" },
    { key: "max_debt_rate", hint: "settings.of_income" },
    { key: "max_luxury_rate", hint: "settings.of_income" },
    { key: "emergency_cut_rate", hint: "settings.emergency_cut_rate_hint" },
    { key: "saving_rate_em_incomplete", hint: "settings.of_net" },
    { key: "saving_rate_em_complete", hint: "settings.of_net" },
  ];
  const ROLES = ["essential", "luxury", "debt", "savings", "emergency", "other"];
  const TABS = ["criteria", "types", "categories", "templates", "account"];

  Alpine.data("settingsPage", () => ({
    ...pageMixin(),
    tabs: TABS,
    roles: ROLES,
    rateFields: RATE_FIELDS,
    tab: "criteria",
    settings: null,
    form: {},
    types: [],
    categories: [],
    templates: [],
    newType: { name: "", role: "other" },
    newCategory: "",
    newTemplate: { kind: "expense", name: "", type_id: "", amount: "", is_savings_withdrawal: false },

    async init() {
      const fromHash = location.hash.slice(1);
      if (TABS.includes(fromHash)) this.tab = fromHash;
      await Promise.all([this.loadSettings(), this.loadTypes(), this.loadCategories(), this.loadTemplates()]);
    },

    setTab(id) {
      this.tab = id;
      history.replaceState(null, "", `#${id}`);
    },

    // ---------- loading

    async loadSettings() {
      const s = await this.request("GET", "/api/settings");
      if (!s) return;
      this.settings = s;
      this.form = {
        ...Object.fromEntries(RATE_FIELDS.map((f) => [f.key, Math.round(s[f.key] * 1000) / 10])),
        emergency_ref_months: s.emergency_ref_months,
        emergency_ref_factor: s.emergency_ref_factor,
        currency: s.currency,
      };
    },

    async loadTypes() {
      const r = await this.request("GET", "/api/expense-types");
      if (!r) return;
      this.types = r;
      if (!this.newTemplate.type_id && r.length) {
        this.newTemplate.type_id = (r.find((x) => x.role === "essential") || r[0]).id;
      }
    },

    async loadCategories() {
      const r = await this.request("GET", "/api/goal-categories");
      if (r) this.categories = r;
    },

    async loadTemplates() {
      const r = await this.request("GET", "/api/templates");
      if (r) this.templates = r;
    },

    reloadFor(resource) {
      return {
        "expense-types": () => this.loadTypes(),
        "goal-categories": () => this.loadCategories(),
        templates: () => this.loadTemplates(),
      }[resource]();
    },

    onRequestError() {
      // put the stored values back into the inputs the user just edited
      this.types = [...this.types];
      this.categories = [...this.categories];
      this.templates = [...this.templates];
    },

    // ---------- criteria

    async saveSettings() {
      const body = {
        ...Object.fromEntries(RATE_FIELDS.map((f) => [f.key, Math.round(Number(this.form[f.key]) * 10000) / 1000000])),
        emergency_ref_months: Number(this.form.emergency_ref_months),
        emergency_ref_factor: Number(this.form.emergency_ref_factor),
        currency: this.form.currency,
      };
      const r = await this.request("PATCH", "/api/settings", body);
      if (!r) return;
      this.settings = r;
      this.notify(tr("settings.saved"));
    },

    // ---------- lists

    async updateRow(resource, row, values) {
      const changed = Object.entries(values).some(([k, v]) => row[k] !== v);
      if (!changed) return;
      const r = await this.request("PATCH", `/api/${resource}/${row.id}`, values);
      if (r) await this.reloadFor(resource);
    },

    async removeRow(resource, row, name) {
      if (!confirm(tr("common.confirm_delete", { name }))) return;
      const r = await this.request("DELETE", `/api/${resource}/${row.id}`);
      if (r) await this.reloadFor(resource);
    },

    async addType() {
      if (!this.newType.name.trim()) return this.notify(tr("settings.types_err_empty"));
      const r = await this.request("POST", "/api/expense-types", this.newType);
      if (!r) return;
      this.newType = { name: "", role: "other" };
      await this.loadTypes();
    },

    async addCategory() {
      if (!this.newCategory.trim()) return this.notify(tr("settings.categories_err_empty"));
      const r = await this.request("POST", "/api/goal-categories", { name: this.newCategory });
      if (!r) return;
      this.newCategory = "";
      await this.loadCategories();
    },

    async addTemplate() {
      const n = this.newTemplate;
      if (!n.name.trim()) return this.notify(tr("settings.templates_err_empty"));
      const body = { kind: n.kind, name: n.name, amount: this.numOrNull(n.amount) };
      if (n.kind === "expense") body.type_id = Number(n.type_id);
      else body.is_savings_withdrawal = n.is_savings_withdrawal;
      const r = await this.request("POST", "/api/templates", body);
      if (!r) return;
      this.newTemplate = { ...n, name: "", amount: "", is_savings_withdrawal: false };
      await this.loadTemplates();
    },

    // ---------- account

    async changePassword(form) {
      const body = Object.fromEntries(new FormData(form).entries());
      delete body.username;
      const r = await this.request("POST", "/auth/change-password", body);
      if (!r) return;
      form.reset();
      this.notify(tr("auth.change.done"));
    },

    numOrNull(v) {
      return v === "" || v === null || v === undefined ? null : Number(v);
    },
  }));
});
