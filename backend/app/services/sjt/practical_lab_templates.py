"""
PRACTICAL "LAB" ENVIRONMENT: a hands-on technical exercise for DevOps, cloud, network, systems, security and support-engineering roles.

The candidate gets an ARTIFACT to inspect (a server log, an authentication log, a Kubernetes manifest, a network plan, a cloud access policy, a piece of
code with hidden flaws) and five questions about it. Like the data environment, every artifact is generated procedurally from a seed (a different one
for every candidate) and every correct answer is computed by code from the very lines the candidate sees, so the marking is exact and never an AI's opinion.

Each builder returns {"title", "note", "artifact": {"kind", "name", "lines"}, "questions": [...]} with questions shaped like the data environment's.
"""

import ipaddress
import json
import random
import re

TEMPLATES = {
    "log_analysis": "Web server log analysis",
    "auth_log": "Authentication log investigation",
    "config_debug": "Kubernetes configuration review",
    "network_math": "Subnet and addressing calculations",
    "security_review": "Secure code review",
    "iam_policy": "Cloud access policy review",
}

NUMBER, TEXT = "number", "text"


def _q(question, kind, answer, hint="", unit="", tolerance=0.0):
    return {"question": question, "answer_kind": kind, "correct_answer": answer, "unit": unit, "tolerance": tolerance, "hint": hint}


# ------------------------------------------------------------------ web server access log (SRE, system administration, performance, L2 support)

def _log_analysis(rng: random.Random) -> dict:

    ips = ["203.0.113.14", "203.0.113.77", "198.51.100.9", "198.51.100.23", "192.0.2.41", "192.0.2.58", "203.0.113.200", "198.51.100.130"]
    endpoints = ["/api/orders", "/api/login", "/api/products", "/api/cart", "/health", "/static/app.js"]

    for _attempt in range(300):

        lines, records = [], []
        bad_endpoint = rng.choice(endpoints[:4])
        bad_hour = rng.randrange(9, 21)
        top_ip = rng.choice(ips)

        for i in range(72):
            hour = rng.randrange(8, 22)
            minute, second = rng.randrange(60), rng.randrange(60)
            endpoint = rng.choice(endpoints)
            ip = top_ip if rng.random() < 0.28 else rng.choice(ips)
            method = "POST" if endpoint in ("/api/login", "/api/cart") and rng.random() < 0.7 else "GET"
            status = rng.choices([200, 200, 200, 301, 404, 500, 502, 503], weights=[40, 20, 10, 5, 8, 6, 3, 3])[0]

            if endpoint == bad_endpoint and hour == bad_hour and rng.random() < 0.85:
                status = rng.choice([500, 502, 503])

            size = rng.randrange(200, 9000)
            records.append((ip, hour, minute, second, method, endpoint, status, size))

        records.sort(key=lambda r: (r[1], r[2], r[3]))

        for ip, hour, minute, second, method, endpoint, status, size in records:
            lines.append(f'{ip} - - [12/Mar/2026:{hour:02d}:{minute:02d}:{second:02d} +0000] "{method} {endpoint} HTTP/1.1" {status} {size}')

        errors = [r for r in records if r[6] >= 500]
        by_ip, by_endpoint, by_hour = {}, {}, {}
        for r in records:
            by_ip[r[0]] = by_ip.get(r[0], 0) + 1
        for r in errors:
            by_endpoint[r[5]] = by_endpoint.get(r[5], 0) + 1
            by_hour[r[1]] = by_hour.get(r[1], 0) + 1

        def unique_max(counts):
            ordered = sorted(counts.values(), reverse=True)
            return len(ordered) >= 1 and (len(ordered) == 1 or ordered[0] != ordered[1])

        orders_ok = sum(r[7] for r in records if r[4] == "GET" and r[5] == "/api/orders" and r[6] == 200)

        if errors and unique_max(by_ip) and unique_max(by_endpoint) and unique_max(by_hour) and orders_ok > 0:
            break

    return {
        "title": "Web server access log",
        "note": "A busy afternoon on the shop's API servers. Each line: client IP, time, request, status code and bytes sent. A status of 500 or above is a server error.",
        "artifact": {"kind": "log", "name": "access.log", "lines": lines},
        "questions": [
            _q("How many requests ended in a server error (status 500 or above)?", NUMBER, len(errors)),
            _q("Which client IP address made the most requests?", TEXT, max(by_ip, key=by_ip.get), hint="Type the IP address exactly as it appears."),
            _q("Which endpoint returned the most server errors (500 or above)?", TEXT, max(by_endpoint, key=by_endpoint.get), hint="Type the path, for example /api/orders."),
            _q("In which hour of the day (0 to 23, from the time in the log) did the most server errors happen?", NUMBER, max(by_hour, key=by_hour.get)),
            _q("What is the total number of bytes sent for GET requests to /api/orders that returned status 200?", NUMBER, orders_ok, unit="bytes"),
        ],
    }


# ------------------------------------------------------------------ authentication log (SOC analyst)

def _auth_log(rng: random.Random) -> dict:

    for _attempt in range(300):

        attacker = f"198.51.100.{rng.randrange(2, 250)}"
        staff = [f"10.0.0.{rng.randrange(5, 60)}" for _ in range(4)]
        users = ["deploy", "alice", "bob", "carol", "backup"]
        guesses = ["admin", "root", "test", "oracle", "ubuntu", "postgres", "guest", "ftp"]
        events = []

        start = rng.randrange(1, 5) * 3600 + rng.randrange(0, 3000)
        t = start
        attack_users = rng.sample(guesses, rng.randrange(4, 7))
        for i in range(rng.randrange(14, 22)):
            t += rng.randrange(2, 9)
            user = attack_users[i % len(attack_users)]
            events.append((t, "Failed password for " + ("invalid user " if user not in ("root",) else "") + f"{user} from {attacker} port {rng.randrange(40000, 60000)} ssh2", attacker, user, False))

        t += rng.randrange(3, 9)
        compromised = rng.choice(["deploy", "backup", "root"])
        events.append((t, f"Accepted password for {compromised} from {attacker} port {rng.randrange(40000, 60000)} ssh2", attacker, compromised, True))

        for i in range(rng.randrange(20, 30)):
            when = rng.randrange(3600, 20 * 3600)
            ip = rng.choice(staff)
            user = rng.choice(users)
            if rng.random() < 0.12:
                events.append((when, f"Failed password for {user} from {ip} port {rng.randrange(40000, 60000)} ssh2", ip, user, False))
            else:
                events.append((when, f"Accepted publickey for {user} from {ip} port {rng.randrange(40000, 60000)} ssh2", ip, user, True))

        events.sort(key=lambda e: e[0])

        def stamp(sec):
            return f"Mar 12 {sec // 3600:02d}:{(sec % 3600) // 60:02d}:{sec % 60:02d}"

        lines = [f"{stamp(sec)} srv-web01 sshd[{rng.randrange(1000, 9999)}]: {text}" for sec, text, *_ in events]

        failed = [e for e in events if not e[4]]
        by_ip = {}
        for e in failed:
            by_ip[e[2]] = by_ip.get(e[2], 0) + 1
        ranked = sorted(by_ip.values(), reverse=True)
        top = max(by_ip, key=by_ip.get)

        if top == attacker and (len(ranked) == 1 or ranked[0] != ranked[1]):
            break

    attacker_failures = [e for e in events if e[2] == attacker and not e[4]]
    distinct_users = len({e[3] for e in attacker_failures})
    first = min(attacker_failures, key=lambda e: e[0])

    return {
        "title": "SSH authentication log",
        "note": "An excerpt of the authentication log of a production web server. 'Failed password' lines are unsuccessful logins; 'Accepted' lines are successful ones.",
        "artifact": {"kind": "log", "name": "auth.log", "lines": lines},
        "questions": [
            _q("How many failed login attempts are in the log?", NUMBER, len(failed)),
            _q("Which IP address made the most failed login attempts?", TEXT, top, hint="Type the IP address exactly as it appears."),
            _q("How many different usernames did that IP address try (in failed attempts)?", NUMBER, distinct_users),
            _q("Which username was successfully logged into from that same IP address?", TEXT, next(e[3] for e in events if e[2] == attacker and e[4]), hint="Type the username."),
            _q("At what time (HH:MM:SS) was the first failed attempt from that IP address?", TEXT, stamp(first[0])[7:], hint="Use the form 02:14:07."),
        ],
    }


# ------------------------------------------------------------------ Kubernetes manifest (DevOps, platform)

def _config_debug(rng: random.Random) -> dict:

    app = rng.choice(["orders", "billing", "catalog", "checkout", "inventory"])
    wrong_label = app + rng.choice(["-svc", "-api", "-v2"])
    port = rng.choice([3000, 5000, 8000, 8080, 9090])
    target_wrong = port + rng.choice([1, 10, 100])
    replicas = rng.randrange(2, 7)
    tag = rng.choice(["latest", "1.4.2", "2.0.1", "3.1.0"])
    service_port = rng.choice([80, 443, 8081])

    lines = [
        "apiVersion: apps/v1",
        "kind: Deployment",
        "metadata:",
        f"  name: {app}-deployment",
        "spec:",
        f"  replicas: {replicas}",
        "  selector:",
        "    matchLabels:",
        f"      app: {app}",
        "  template:",
        "    metadata:",
        "      labels:",
        f"        app: {app}",
        "    spec:",
        "      containers:",
        f"        - name: {app}",
        f"          image: registry.example.com/{app}:{tag}",
        "          ports:",
        f"            - containerPort: {port}",
        "          resources:",
        "            limits:",
        f"              memory: \"{rng.choice([256, 512])}Mi\"",
        "---",
        "apiVersion: v1",
        "kind: Service",
        "metadata:",
        f"  name: {app}-service",
        "spec:",
        "  selector:",
        f"    app: {wrong_label}",
        "  ports:",
        "    - protocol: TCP",
        f"      port: {service_port}",
        f"      targetPort: {target_wrong}",
    ]

    selector_line = next(i + 1 for i, l in enumerate(lines) if l.strip() == f"app: {wrong_label}")
    image_line = next(i + 1 for i, l in enumerate(lines) if l.strip().startswith("image:"))
    target_line = next(i + 1 for i, l in enumerate(lines) if l.strip().startswith("targetPort:"))

    return {
        "title": "Deployment and Service manifest",
        "note": "A Kubernetes Deployment and the Service that should expose it. Traffic to the Service is not reaching the application. Line numbers count from 1 at the top.",
        "artifact": {"kind": "config", "name": f"{app}.yaml", "lines": lines},
        "questions": [
            _q("On which line is the Service's selector value that does not match the pods' label?", NUMBER, selector_line),
            _q("On which line is the Service's targetPort set?", NUMBER, target_line),
            _q("Which targetPort should the Service use so that traffic reaches the container?", NUMBER, port),
            _q("How many replicas does the Deployment declare?", NUMBER, replicas),
            _q("Which image tag does the container use?", TEXT, tag, hint="Type only the tag, the part after the colon."),
        ],
    }


# ------------------------------------------------------------------ addressing and subnets (network engineer, system administrator)

def _network_math(rng: random.Random) -> dict:

    for _attempt in range(300):

        prefix_a = rng.choice([22, 23, 24, 25, 26, 27])
        net_a = ipaddress.ip_network(f"10.{rng.randrange(0, 200)}.{rng.randrange(0, 256)}.0/{prefix_a}", strict=False)

        host_b = ipaddress.ip_interface(f"172.16.{rng.randrange(0, 256)}.{rng.randrange(1, 254)}/{rng.choice([20, 21, 22, 23])}")
        net_c = ipaddress.ip_network(f"192.168.{rng.randrange(0, 256)}.{rng.randrange(0, 256)}/{rng.choice([26, 27, 28, 29])}", strict=False)

        d_prefix = rng.choice([24, 25, 26])
        base = ipaddress.ip_network(f"10.50.{rng.randrange(0, 200)}.0/{d_prefix}", strict=False)
        host_d = base.network_address + rng.randrange(1, base.num_addresses - 1)
        same = rng.random() < 0.5
        other = (base.network_address + rng.randrange(1, base.num_addresses - 1)) if same else (base.broadcast_address + rng.randrange(2, 40))

        wanted = rng.choice([30, 60, 120, 250, 500, 1000])

        host_e = other
        in_same = host_e in base

        if host_d != host_e:
            break

    best_prefix = next(p for p in range(30, 0, -1) if (2 ** (32 - p) - 2) >= wanted)

    lines = [
        "Site plan (addresses as configured)",
        "",
        f"Branch LAN          {net_a}",
        f"Core router         {host_b}",
        f"Server VLAN         {net_c}",
        f"Printer             {host_d}/{d_prefix}",
        f"Laptop              {host_e}/{d_prefix}",
        "",
        f"New office needs    {wanted} hosts",
    ]

    return {
        "title": "Site addressing plan",
        "note": "Work out the answers from the site plan; you may use the calculator. Give addresses in dotted form, for example 10.4.8.0.",
        "artifact": {"kind": "config", "name": "site-plan.txt", "lines": lines},
        "questions": [
            _q(f"How many usable host addresses does the Branch LAN ({net_a}) have?", NUMBER, net_a.num_addresses - 2),
            _q("What is the network address of the Core router's interface?", TEXT, str(host_b.network.network_address), hint="Dotted form, for example 172.16.32.0."),
            _q(f"What is the broadcast address of the Server VLAN ({net_c})?", TEXT, str(net_c.broadcast_address), hint="Dotted form."),
            _q(f"Are the Printer and the Laptop in the same subnet (both use /{d_prefix})?", TEXT, "yes" if in_same else "no", hint="Answer yes or no."),
            _q(f"What is the longest prefix length (the biggest /N number) that still gives at least {wanted} usable hosts for the new office?", NUMBER, best_prefix),
        ],
    }


# ------------------------------------------------------------------ code with hidden flaws (security engineer, penetration tester, application security)

_FUNCTIONS = [
    {"id": "sqli", "vuln": "sql injection", "body": [
        "def find_user(db, name):",
        "    query = \"SELECT * FROM users WHERE name = '\" + name + \"'\"",
        "    return db.execute(query).fetchone()"], "flaw": 1},
    {"id": "safe_query", "vuln": None, "body": [
        "def find_order(db, order_id):",
        "    return db.execute(\"SELECT * FROM orders WHERE id = ?\", (order_id,)).fetchone()"], "flaw": None},
    {"id": "secret", "vuln": "hardcoded credentials", "body": [
        "def connect_to_payments():",
        "    api_key = \"sk_live_51Hh8s2E9fLmZ0x\"",
        "    return PaymentClient(api_key)"], "flaw": 1},
    {"id": "md5", "vuln": "weak hashing", "body": [
        "def store_password(user, password):",
        "    user.password_hash = hashlib.md5(password.encode()).hexdigest()",
        "    user.save()"], "flaw": 1},
    {"id": "safe_hash", "vuln": None, "body": [
        "def store_password_safely(user, password):",
        "    user.password_hash = bcrypt.hashpw(password.encode(), bcrypt.gensalt())",
        "    user.save()"], "flaw": None},
    {"id": "cmd", "vuln": "command injection", "body": [
        "def ping_host(host):",
        "    os.system(\"ping -c 1 \" + host)"], "flaw": 1},
    {"id": "path", "vuln": "path traversal", "body": [
        "def read_report(name):",
        "    with open(\"/var/reports/\" + name) as handle:",
        "        return handle.read()"], "flaw": 1},
    {"id": "safe_path", "vuln": None, "body": [
        "def read_report_safely(name):",
        "    path = os.path.join(\"/var/reports\", os.path.basename(name))",
        "    with open(path) as handle:",
        "        return handle.read()"], "flaw": None},
]

_OPTIONS = ["sql injection", "hardcoded credentials", "weak hashing", "command injection", "path traversal"]


def _security_review(rng: random.Random) -> dict:

    for _attempt in range(200):
        chosen = rng.sample(_FUNCTIONS, rng.randrange(6, 8))
        vulnerable = [f for f in chosen if f["vuln"]]
        if len(vulnerable) >= 4 and len({f["vuln"] for f in vulnerable}) == len(vulnerable) and any(f["vuln"] is None for f in chosen):
            break

    lines = ["import os", "import hashlib", "import bcrypt", ""]
    located = {}

    for f in chosen:
        start = len(lines) + 1
        lines.extend(f["body"])
        lines.append("")
        if f["flaw"] is not None:
            located[f["vuln"]] = start + f["flaw"]

    pick = rng.sample(sorted(located), 3)
    named = rng.choice([v for v in sorted(located) if v not in pick])
    safe_count = sum(1 for f in chosen if f["vuln"] is None)

    labels = {"sql injection": "builds a database query by joining in user input",
              "hardcoded credentials": "contains a secret key written directly in the code",
              "weak hashing": "hashes a password with a weak, fast algorithm",
              "command injection": "passes user input to a shell command",
              "path traversal": "opens a file using a name the user controls without cleaning it"}

    return {
        "title": "Code review: spot the vulnerabilities",
        "note": "A small module from a web application. Review it as a security engineer would. Line numbers count from 1 at the top, including the import lines.",
        "artifact": {"kind": "code", "name": "app_helpers.py", "lines": lines},
        "questions": [
            _q(f"On which line does the code that {labels[v]} appear?", NUMBER, located[v]) for v in pick
        ] + [
            _q(f"What kind of vulnerability is on line {located[named]}?", TEXT, named, hint="Choose one: " + ", ".join(_OPTIONS) + "."),
            _q("How many functions in this module have no vulnerability?", NUMBER, safe_count),
        ],
    }


# ------------------------------------------------------------------ cloud access policy (cloud engineer, IAM specialist)

def _iam_policy(rng: random.Random) -> dict:

    pool = [
        ("ReadReports", "Allow", ["s3:GetObject", "s3:ListBucket"], "arn:aws:s3:::company-reports/*"),
        ("UploadLogs", "Allow", ["s3:PutObject"], "arn:aws:s3:::company-logs/*"),
        ("AdminAll", "Allow", ["*"], "*"),
        ("DenyDelete", "Deny", ["s3:DeleteObject", "s3:DeleteBucket"], "*"),
        ("ManageInstances", "Allow", ["ec2:StartInstances", "ec2:StopInstances"], "arn:aws:ec2:*:*:instance/*"),
        ("QueryTables", "Allow", ["dynamodb:Query", "dynamodb:GetItem"], "arn:aws:dynamodb:*:*:table/orders"),
        ("ListEverything", "Allow", ["s3:ListAllMyBuckets", "ec2:DescribeInstances"], "*"),
    ]

    chosen = rng.sample(pool, 5)

    if not any(s[0] == "AdminAll" for s in chosen):
        chosen[rng.randrange(5)] = pool[2]
    if not any(s[0] == "DenyDelete" for s in chosen):
        chosen[(next(i for i, s in enumerate(chosen) if s[0] == "AdminAll") + 1) % 5] = pool[3]

    lines = ["{", '  "Version": "2012-10-17",', '  "Statement": [']

    for index, (sid, effect, actions, resource) in enumerate(chosen):
        lines += ["    {", f'      "Sid": "{sid}",', f'      "Effect": "{effect}",', '      "Action": [' + ", ".join(json.dumps(a) for a in actions) + "],",
                  f'      "Resource": "{resource}"', "    }" + ("," if index < len(chosen) - 1 else "")]

    lines += ["  ]", "}"]

    allow = sum(1 for s in chosen if s[1] == "Allow")
    s3_allow = sum(1 for s in chosen if s[1] == "Allow" and any(a.startswith("s3:") for a in s[2]))
    admin_sid = next(s[0] for s in chosen if s[1] == "Allow" and "*" in s[2] and s[3] == "*")
    deny_sid = next(s[0] for s in chosen if s[1] == "Deny" and any("Delete" in a for a in s[2]))
    wildcard_resource_lines = sum(1 for l in lines if l.strip() == '"Resource": "*"')

    return {
        "title": "Cloud access policy",
        "note": "An access policy attached to a service role. Read it as a cloud security reviewer would: what can the role do, and what is too broad?",
        "artifact": {"kind": "config", "name": "role-policy.json", "lines": lines},
        "questions": [
            _q("How many statements in the policy have the effect Allow?", NUMBER, allow),
            _q("Which statement (give its Sid) grants every action on every resource?", TEXT, admin_sid, hint="Type the Sid exactly."),
            _q("How many Allow statements grant at least one s3 action?", NUMBER, s3_allow),
            _q("Which statement (Sid) explicitly denies deleting objects or buckets?", TEXT, deny_sid, hint="Type the Sid exactly."),
            _q("How many lines in the policy have \"Resource\": \"*\" (any resource)?", NUMBER, wildcard_resource_lines),
        ],
    }


_BUILDERS = {
    "log_analysis": _log_analysis, "auth_log": _auth_log, "config_debug": _config_debug,
    "network_math": _network_math, "security_review": _security_review, "iam_policy": _iam_policy,
}


def build_lab_task(template: str, seed) -> dict:
    """A fresh artifact + questions for this candidate. An unknown template falls back to the log analysis."""

    builder = _BUILDERS.get(template) or _log_analysis
    task = builder(random.Random(f"lab:{template}:{seed}"))
    task["template"] = template if template in _BUILDERS else "log_analysis"
    return task
