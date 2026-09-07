# Loan Intelligence — three fixes

> For the dev/agent working in the **CRM-UI** repo.
> From a screen-by-screen audit of the live dashboard, 2026-09-07.
>
> **Every number on every tab is correct** — all six tabs were checked
> against the database and nothing was wrong. These three are display
> problems, not data problems. Full audit:
> `docs/DASHBOARD_SCREEN_AUDIT_2026_09_07.md`.

---

## 1. The exclusion caption is understating by nearly half 🟠

**Pipeline tab → revenue bridge → the "Unlockable" column.**

It currently reads:

> at least ₹23.31 L
> on ₹24.03 cr still undrawn
> **3 files excluded — no lender rate**

**Five files are excluded, not three.** And you could not have known: the
API was handing you two counts that both happened to be 3, and neither is
the answer.

```
files_missing_rate      3     Tanmay (Propelld), Muskan Sehgal (Axis),
                              Soumya Goswami (PNB)
files_missing_sanction  3     Jhanvi (PNB), Ayushmann Sharma (Kuhoo),
                              Soumya Goswami (PNB)
                              ↑ counted in BOTH
─────────────────────────────────────────────────────────────────
distinct files excluded 5     not 3, and not 6
```

Quoting one understates; adding them overstates. **A file can fail both
tests**, and one does.

### The fix — a new field, deployed

`pipeline_ahead` and `revenue_bridge` now both return:

```jsonc
{
  "files_missing_rate":     3,
  "files_missing_sanction": 3,
  "files_excluded":         5   // NEW — the one to display
}
```

**Render `files_excluded`, and name both reasons:**

> **at least ₹23.31 L**
> on ₹24.03 cr still undrawn
> *5 files excluded — no lender rate or no sanctioned amount*

Rules:

- Use **`files_excluded`** for the count. Never sum the other two.
- Keep the words **"at least"** on the amount. The forecast is a floor:
  those 5 files are excluded rather than counted as zero, so the real
  figure can only be higher.
- Hide the line entirely when `files_excluded == 0`.

The other two fields stay available if you ever want to break the reason
down, but they are not for the headline.

---

## 2. The revenue bridge bars don't encode their values 🟠

Same panel. Three bars:

| Column | Value | Width should be |
|---|---|---|
| Booked | ₹17.23 L | 42% |
| Unlockable | ₹23.31 L | 57% |
| If fully drawn | ₹40.54 L | 100% |

On screen they render at **roughly equal width**. If that is what the DOM
says, the chart is decoration — the reader takes a size comparison from
it and the sizes are meaningless.

### The fix

Width proportional to value, against the largest bar (always "if fully
drawn", since it is the sum of the other two):

```
Booked          ████████████████                    ₹17.23 L
Unlockable      ██████████████████████ (dashed)     ₹23.31 L
If fully drawn  ██████████████████████████████████  ₹40.54 L
```

Keep the dashed border on Unlockable — "not yet real" reads well. Just
make the length mean something.

**Check the other bar charts on the same page while you are in there** —
"Where the students are" and "Flow of money" should be verified the same
way. `logged_in` at ₹0.50 cr must be ~1.5% of the width of `disbursed` at
₹32.48 cr, not a visible minimum-width stub.

---

## 3. One lender row renders with the wrong separators 🟡

**Lender Performance → "Who owes what" → the PNB row.**

```
PNB    1    ₹18.00.000    ₹14.868    1.2%    0%
              ↑ dots        ↑ dots
```

Every other row is correct (`₹3,01,41,385`, `₹2,15,454`). Only PNB.

The values themselves are right — ₹18,00,000 disbursed and ₹14,868 owed
match the database exactly. **This is a formatting bug, not a data one.**

Dots as thousand separators is `de-DE` / `it-IT` behaviour, so the likely
cause is a formatter falling back to a different locale for this row —
often because the value arrived in a different shape (a number where the
others are strings, or vice versa).

### The fix

- Format every money value through **one** helper, pinned to `en-IN`.
- **All money in these responses is a JSON string** (Decimal
  serialisation). Parse with a decimal library before formatting; never
  `parseFloat`, which will silently lose paise on the larger figures.
- Add a check that the formatter never receives `undefined`/`NaN` — a
  fallback path is the usual reason one row differs from the rest.

---

## Acceptance

- [ ] Pipeline exclusion line reads **5 files** from `files_excluded`,
      names both reasons, keeps "at least", and disappears at 0
- [ ] Revenue bridge bar widths proportional to value; "Where the
      students are" and "Flow of money" verified the same way
- [ ] PNB row renders `₹18,00,000` / `₹14,868`; all money goes through
      one `en-IN` formatter
