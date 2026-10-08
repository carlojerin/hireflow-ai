"""
PRACTICAL "DATA" ENVIRONMENT: a table on the candidate's screen and questions whose answers are COMPUTED BY CODE.

An AI writing both a dataset and its answers makes arithmetic mistakes, and a wrong answer key is the worst failure of a practical test.
So every dataset here is generated procedurally from a seed (a different table for every candidate) and every correct answer is computed
from the very rows the candidate sees. The AI planner only chooses WHICH template fits a job.

Each builder returns: {"title", "note", "columns", "rows", "questions"} where a question is
{"question", "answer_kind": "number" | "text", "correct_answer", "tolerance", "unit"}. correct_answer never goes to the candidate.
"""

import random
import re
from datetime import date, timedelta

TEMPLATES = {
    "sales_report": "Sales report analysis",
    "bank_reconciliation": "Bank reconciliation",
    "inventory_orders": "Stock and reorder planning",
    "hr_headcount": "Headcount and attrition report",
    "support_tickets": "Support ticket performance",
}

NUMBER = "number"
TEXT = "text"


def _q(question, kind, answer, unit="", tolerance=0.0, hint=""):
    return {"question": question, "answer_kind": kind, "correct_answer": answer, "unit": unit, "tolerance": tolerance, "hint": hint}


def _money(value) -> str:
    return f"{value:,.0f}"


# ------------------------------------------------------------------ sales report (MIS, sales operations, business development)

def _sales_report(rng: random.Random) -> dict:

    regions = ["North", "South", "East", "West"]
    people = ["Asha", "Ravi", "Meena", "Karthik", "Divya", "Imran"]
    products = [("Laptop Bag", 1200), ("Wireless Mouse", 650), ("USB Hub", 900), ("Desk Lamp", 780), ("Notebook Pack", 240)]

    for _attempt in range(200):

        rows = []
        start = date(2026, 1, 1)

        for _ in range(26):
            when = start + timedelta(days=rng.randrange(0, 90))
            name, price = rng.choice(products)
            rows.append([when.isoformat(), rng.choice(regions), rng.choice(people), name, rng.randint(1, 20), price])

        # a few exact duplicate rows: the kind of mess real reports contain
        for row in rng.sample(rows[:], 3):
            rows.append(list(row))

        rng.shuffle(rows)

        revenue_by_region = {r: 0 for r in regions}
        for _d, region, _p, _n, units, price in rows:
            revenue_by_region[region] += units * price

        ordered = sorted(revenue_by_region.values(), reverse=True)
        top = max(revenue_by_region, key=revenue_by_region.get)

        product_rows = {}
        for _d, _r, _p, name, units, _price in rows:
            product_rows.setdefault(name, []).append(units)
        eligible = [p for p, units in product_rows.items() if len(units) >= 3]

        march = [(d, r) for d, r, *_rest in rows if d.startswith("2026-03")]
        march_regions = sorted({r for _d, r in march})

        if ordered[0] != ordered[1] and eligible and march_regions:
            break

    product = rng.choice(sorted(eligible))
    region = rng.choice(march_regions)

    total_revenue = sum(units * price for *_x, units, price in rows)
    average_units = round(sum(product_rows[product]) / len(product_rows[product]), 2)
    march_count = sum(1 for d, r in march if r == region)
    duplicates = len(rows) - len({tuple(r) for r in rows})

    return {
        "title": "Quarterly sales report",
        "note": "This is the raw sales extract for January to March 2026. Revenue is not a column: it is Units x Unit Price. Use every row exactly as listed.",
        "columns": ["Date", "Region", "Salesperson", "Product", "Units", "Unit Price"],
        "rows": rows,
        "questions": [
            _q("What is the total revenue across all rows? (Units x Unit Price, summed)", NUMBER, total_revenue, unit="Rs", tolerance=0.5),
            _q("Which region has the highest total revenue?", TEXT, top, hint="Type the region name as shown in the table."),
            _q(f"What is the average number of units per order for {product}? Round to 2 decimals.", NUMBER, average_units, tolerance=0.01),
            _q(f"How many orders were placed in the {region} region in March 2026?", NUMBER, march_count, tolerance=0),
            _q("The report contains rows that are exact duplicates of another row (same date, region, salesperson, product, units and price). How many extra duplicate rows should be removed?", NUMBER, duplicates, tolerance=0),
        ],
    }


# ------------------------------------------------------------------ bank reconciliation (accounts, finance executive)

def _bank_reconciliation(rng: random.Random) -> dict:

    opening = 50000
    descriptions = ["Supplier payment", "Customer receipt", "Rent", "Office supplies", "Salary advance", "Customer receipt", "Utility bill", "Customer receipt",
                    "Courier charges", "Customer receipt", "Software subscription", "Customer receipt"]

    day = date(2026, 4, 1)
    rows = []
    ref = 2001

    def stamp(offset):
        return (day + timedelta(days=offset)).isoformat()

    matched = []
    for i, desc in enumerate(descriptions[:10]):
        amount = rng.randrange(8, 90) * 100
        amount = amount if "receipt" in desc.lower() else -amount
        matched.append([stamp(i * 2), f"REF{ref + i}", desc, amount, amount])

    # cheques written in the cash book but not yet cleared by the bank (timing differences)
    outstanding = []
    for i in range(3):
        outstanding.append([stamp(21 + i), f"REF{ref + 20 + i}", "Cheque issued", -rng.randrange(10, 60) * 100, ""])

    # items the bank recorded that the cash book does not have yet (charges / interest)
    bank_only = [
        [stamp(27), f"BNK{ref + 30}", "Bank charges", "", -rng.randrange(2, 9) * 50],
        [stamp(29), f"BNK{ref + 31}", "Interest credited", "", rng.randrange(2, 9) * 40],
    ]

    rows = matched + outstanding + bank_only
    rng.shuffle(rows)
    rows.sort(key=lambda r: r[0])

    cash_total = sum(r[3] for r in rows if r[3] != "")
    bank_total = sum(r[4] for r in rows if r[4] != "")
    cash_closing = opening + cash_total
    bank_closing = opening + bank_total
    not_on_bank = sum(1 for r in rows if r[3] != "" and r[4] == "")
    bank_only_net = sum(r[4] for r in rows if r[3] == "" and r[4] != "")

    return {
        "title": "April bank reconciliation",
        "note": "Opening balance on 1 April 2026 is Rs 50,000 in BOTH the cash book and the bank statement. A blank cell means the item is not recorded on that side. Payments are negative.",
        "columns": ["Date", "Reference", "Description", "Cash book", "Bank statement"],
        "rows": rows,
        "questions": [
            _q("What is the closing balance per the cash book?", NUMBER, cash_closing, unit="Rs", tolerance=0.5),
            _q("What is the closing balance per the bank statement?", NUMBER, bank_closing, unit="Rs", tolerance=0.5),
            _q("How many items are recorded in the cash book but not on the bank statement?", NUMBER, not_on_bank, tolerance=0),
            _q("What is the net total of the items on the bank statement that are not in the cash book? (use a minus sign for a net charge)", NUMBER, bank_only_net, unit="Rs", tolerance=0.5),
            _q("After entering those bank-only items in the cash book, what is the adjusted cash book balance?", NUMBER, cash_closing + bank_only_net, unit="Rs", tolerance=0.5),
        ],
    }


# ------------------------------------------------------------------ inventory (operations, logistics, procurement)

def _inventory_orders(rng: random.Random) -> dict:

    items = ["Packing Tape", "Bubble Wrap", "Cartons (Small)", "Cartons (Large)", "Pallet Wrap", "Barcode Labels", "Safety Gloves", "Hand Truck Tyres",
             "Printer Ink", "Thermal Paper", "Stretch Film", "Zip Ties"]
    suppliers = ["Apex Supplies", "Bright Traders", "Cedar Logistics", "Delta Wholesale", "Evergreen Co", "Fusion Mart"]

    for _attempt in range(300):

        lead_times = rng.sample(range(2, 20), len(items))
        rows = []
        for index, name in enumerate(items):
            reorder = rng.randrange(10, 60)
            stock = reorder + rng.randrange(-reorder + 3, 60)
            stock = max(0, stock)
            rows.append([f"IT{100 + index}", name, stock, reorder, rng.randrange(5, 120) * 5, suppliers[index % len(suppliers)] if index < 6 else rng.choice(suppliers), lead_times[index]])

        below = [r for r in rows if r[2] <= r[3]]
        values = {r[1]: r[2] * r[4] for r in rows}
        top_value = sorted(values.values(), reverse=True)

        if len(below) >= 3 and top_value[0] != top_value[1]:
            slowest = max(below, key=lambda r: r[6])
            if sum(1 for r in below if r[6] == slowest[6]) == 1:
                break

    stock_value = sum(r[2] * r[4] for r in rows)
    top_item = max(values, key=values.get)
    reorder_cost = sum((2 * r[3] - r[2]) * r[4] for r in below)

    return {
        "title": "Warehouse stock sheet",
        "note": "Stock is the quantity on hand today. An item needs reordering when Stock is at or below its Reorder Level.",
        "columns": ["Item Code", "Item", "Stock", "Reorder Level", "Unit Cost", "Supplier", "Lead Time (days)"],
        "rows": rows,
        "questions": [
            _q("How many items are at or below their reorder level?", NUMBER, len(below), tolerance=0),
            _q("What is the total value of the stock on hand? (Stock x Unit Cost, summed)", NUMBER, stock_value, unit="Rs", tolerance=0.5),
            _q("Which item has the highest stock value?", TEXT, top_item, hint="Type the item name as shown in the table."),
            _q("Each item at or below its reorder level is ordered up to twice its Reorder Level (order quantity = 2 x Reorder Level - Stock). What is the total cost of that order?", NUMBER, reorder_cost, unit="Rs", tolerance=0.5),
            _q("Among the items that need reordering, which supplier has the longest lead time?", TEXT, slowest[5], hint="Type the supplier name as shown in the table."),
        ],
    }


# ------------------------------------------------------------------ HR headcount and attrition (HR, people operations)

def _hr_headcount(rng: random.Random) -> dict:

    departments = ["Sales", "Finance", "Operations", "HR", "IT Support"]

    for _attempt in range(300):

        rows = []
        for index in range(24):
            joined = date(2023, 1, 1) + timedelta(days=rng.randrange(0, 1100))
            left = ""
            if rng.random() < 0.28:
                left = (joined + timedelta(days=rng.randrange(90, 600))).isoformat()
                if left > "2026-03-31":
                    left = ""
            rows.append([f"E{1001 + index}", rng.choice(departments), joined.isoformat(), left, rng.randrange(25, 95) * 1000])

        active = [r for r in rows if not r[3]]
        left_rows = [r for r in rows if r[3]]
        by_department = {d: sum(1 for r in active if r[1] == d) for d in departments}
        ranked = sorted(by_department.values(), reverse=True)

        if left_rows and ranked[0] != ranked[1]:
            break

    chosen = rng.choice([d for d in departments if by_department[d] >= 2])
    salaries = [r[4] for r in active if r[1] == chosen]

    return {
        "title": "Employee master list",
        "note": "Every employee ever listed since 2023. A blank Exit Date means the person is still employed. Salaries are monthly, in Rs.",
        "columns": ["Employee ID", "Department", "Joining Date", "Exit Date", "Monthly Salary"],
        "rows": rows,
        "questions": [
            _q("How many employees are currently active (no exit date)?", NUMBER, len(active), tolerance=0),
            _q("How many employees have left (have an exit date)?", NUMBER, len(left_rows), tolerance=0),
            _q("What is the attrition rate? (employees who left / all employees listed x 100, round to 1 decimal)", NUMBER, round(len(left_rows) / len(rows) * 100, 1), unit="%", tolerance=0.05),
            _q(f"What is the average monthly salary of the ACTIVE employees in {chosen}? Round to the nearest whole rupee.", NUMBER, round(sum(salaries) / len(salaries)), unit="Rs", tolerance=1),
            _q("Which department has the most active employees?", TEXT, max(by_department, key=by_department.get), hint="Type the department name as shown in the table."),
        ],
    }


# ------------------------------------------------------------------ support tickets (customer support, customer success)

def _support_tickets(rng: random.Random) -> dict:

    sla = {"High": 4, "Medium": 8, "Low": 24}
    categories = ["Billing", "Login", "Delivery", "Refund"]

    for _attempt in range(300):

        rows = []
        for index in range(30):
            priority = rng.choice(["High", "Medium", "Low"])
            resolved = rng.random() < 0.8
            hours = round(rng.uniform(0.5, sla[priority] * 1.6), 1) if resolved else ""
            rows.append([f"T{5001 + index}", priority, rng.choice(categories), hours, sla[priority], "Resolved" if resolved else "Open"])

        counts = {c: sum(1 for r in rows if r[2] == c) for c in categories}
        ranked = sorted(counts.values(), reverse=True)
        high_resolved = [r[3] for r in rows if r[1] == "High" and r[5] == "Resolved"]

        if ranked[0] != ranked[1] and len(high_resolved) >= 3:
            break

    breached = sum(1 for r in rows if r[5] == "Resolved" and r[3] > r[4])
    resolved_count = sum(1 for r in rows if r[5] == "Resolved")

    return {
        "title": "Support ticket log",
        "note": "Hours to Resolve is blank while a ticket is still open. A resolved ticket breaches its SLA when Hours to Resolve is greater than SLA Hours.",
        "columns": ["Ticket ID", "Priority", "Category", "Hours to Resolve", "SLA Hours", "Status"],
        "rows": rows,
        "questions": [
            _q("How many resolved tickets breached their SLA?", NUMBER, breached, tolerance=0),
            _q("What is the average Hours to Resolve for resolved High priority tickets? Round to 1 decimal.", NUMBER, round(sum(high_resolved) / len(high_resolved), 1), unit="hours", tolerance=0.05),
            _q("Which category has the most tickets (all statuses)?", TEXT, max(counts, key=counts.get), hint="Type the category name as shown in the table."),
            _q("What percentage of all tickets are resolved? Round to 1 decimal.", NUMBER, round(resolved_count / len(rows) * 100, 1), unit="%", tolerance=0.05),
            _q("How many High priority tickets are still Open?", NUMBER, sum(1 for r in rows if r[1] == "High" and r[5] == "Open"), tolerance=0),
        ],
    }


_BUILDERS = {
    "sales_report": _sales_report,
    "bank_reconciliation": _bank_reconciliation,
    "inventory_orders": _inventory_orders,
    "hr_headcount": _hr_headcount,
    "support_tickets": _support_tickets,
}


def build_data_task(template: str, seed) -> dict:
    """A fresh dataset + questions for this candidate. Unknown templates fall back to the sales report."""

    builder = _BUILDERS.get(template) or _sales_report
    rng = random.Random(f"{template}:{seed}")

    task = builder(rng)
    task["template"] = template if template in _BUILDERS else "sales_report"

    return task


# ------------------------------------------------------------------ checking an answer

_NUMBER_CLEAN = re.compile(r"[^0-9.\-]")


def parse_number(text) -> float | None:
    """'Rs 1,23,456.50', '12.5%', ' -450 ' -> a float; None when it is not a number."""

    if text is None:
        return None

    raw = str(text).strip()
    negative = raw.startswith("(") and raw.endswith(")")
    cleaned = _NUMBER_CLEAN.sub("", raw.replace(",", ""))

    if not cleaned or cleaned in ("-", ".", "-."):
        return None

    try:
        value = float(cleaned)
    except ValueError:
        return None

    return -abs(value) if negative else value


def check_answer(question: dict, given) -> bool:

    if given is None or str(given).strip() == "":
        return False

    if question.get("answer_kind") == NUMBER:
        value = parse_number(given)
        return value is not None and abs(value - float(question["correct_answer"])) <= float(question.get("tolerance") or 0) + 1e-9

    return re.sub(r"\s+", " ", str(given).strip().lower()) == re.sub(r"\s+", " ", str(question["correct_answer"]).strip().lower())
