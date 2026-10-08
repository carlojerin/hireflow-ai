import copy


# ============================================================
# JOBPILOT AI - TECHNICAL DOMAIN ASSESSMENT QUESTION BANK
#
# For a technical role whose required language JobPilot cannot
# execute in its Docker sandbox (SAP/ABAP, Salesforce/Apex,
# Solidity, RPA, embedded firmware, DevOps/infra tooling,
# networking/sysadmin) -- see role_classification.py's
# detect_technical_domain(). Domain-knowledge + scenario multiple
# choice, matching how real ATS platforms (TestGorilla, iMocha,
# Vervoe, Mettl) actually assess exactly these roles: "write and
# run Terraform" isn't a meaningfully sandboxable exercise the way
# Python is, so the industry standard is domain MCQ/scenario, not
# a live code sandbox.
#
# Same shape as SJT_QUESTION_BANK (id, category, question, options,
# correct_answer) -- "category" here IS the domain key, used to
# filter the bank down to only the candidate's own domain before
# sampling (see tda_question_service.py).
# ============================================================

TDA_QUESTION_BANK = [

    # ---------------- salesforce ----------------
    {
        "id": 1, "category": "salesforce",
        "question": "In Salesforce, which Apex construct runs automatically before or after a record is inserted, updated, or deleted?",
        "options": ["A Trigger", "A Visualforce Controller", "A Flow Screen Component", "A Permission Set"],
        "correct_answer": "A Trigger",
    },
    {
        "id": 2, "category": "salesforce",
        "question": "A Salesforce admin reports that a bulk data load of 5,000 records is hitting governor limits inside an Apex trigger. What is the most likely root cause?",
        "options": [
            "A SOQL query or DML statement sitting inside a for-loop instead of being bulkified",
            "The org's storage limit has been reached",
            "The user's profile lacks field-level security",
            "The records are missing a Record Type",
        ],
        "correct_answer": "A SOQL query or DML statement sitting inside a for-loop instead of being bulkified",
    },
    {
        "id": 3, "category": "salesforce",
        "question": "Which Salesforce feature would you use to declaratively (no code) automate a multi-step approval process for discount requests?",
        "options": ["Approval Process", "Apex Trigger", "Visualforce Page", "Data Loader"],
        "correct_answer": "Approval Process",
    },
    {
        "id": 4, "category": "salesforce",
        "question": "What does SOQL stand for, and what is it primarily used for?",
        "options": [
            "Salesforce Object Query Language, for querying Salesforce records",
            "Standard Object Quality Layer, for validating data quality",
            "Salesforce Operations Query Log, for audit logging",
            "Secure Object Query Logic, for encryption",
        ],
        "correct_answer": "Salesforce Object Query Language, for querying Salesforce records",
    },
    {
        "id": 5, "category": "salesforce",
        "question": "A sales user can see an Opportunity in a report but cannot open the record itself. What is the most likely cause?",
        "options": [
            "They lack object- or record-level access (sharing rules/permissions) even though the report exposes aggregated data",
            "The Opportunity was deleted",
            "Reports always show more data than records allow, by design, with no fix needed",
            "The user's browser cache is out of date",
        ],
        "correct_answer": "They lack object- or record-level access (sharing rules/permissions) even though the report exposes aggregated data",
    },
    {
        "id": 6, "category": "salesforce",
        "question": "Which tool would you use to migrate 50,000 Contact records from a CSV file into Salesforce?",
        "options": ["Data Loader", "Apex Trigger", "Flow Builder", "Visualforce"],
        "correct_answer": "Data Loader",
    },
    {
        "id": 7, "category": "salesforce",
        "question": "A Lightning Web Component needs to call Apex to fetch data. Which decorator exposes an Apex method for this?",
        "options": ["@AuraEnabled", "@track", "@wire only, never @AuraEnabled", "@api"],
        "correct_answer": "@AuraEnabled",
    },

    # ---------------- sap ----------------
    {
        "id": 20, "category": "sap",
        "question": "In SAP, what is a 'Transport Request' used for?",
        "options": [
            "Moving configuration/development changes from one SAP system (e.g. Dev) to another (e.g. QA, Production)",
            "Scheduling a background batch job",
            "Requesting additional user licenses",
            "Shipping physical hardware between data centers",
        ],
        "correct_answer": "Moving configuration/development changes from one SAP system (e.g. Dev) to another (e.g. QA, Production)",
    },
    {
        "id": 21, "category": "sap",
        "question": "An ABAP program is running far slower than expected against a large table. What is the most likely first thing to check?",
        "options": [
            "Whether the SELECT statement is using an appropriate index / WHERE clause instead of a full table scan",
            "Whether the user's SAP GUI theme is set correctly",
            "Whether the transport request was released",
            "Whether the program has too many comments",
        ],
        "correct_answer": "Whether the SELECT statement is using an appropriate index / WHERE clause instead of a full table scan",
    },
    {
        "id": 22, "category": "sap",
        "question": "What does the SAP module 'FICO' stand for?",
        "options": ["Financial Accounting and Controlling", "Field Integration and Customer Orders", "Factory Inventory and Carrier Operations", "Functional Interface and Configuration Objects"],
        "correct_answer": "Financial Accounting and Controlling",
    },
    {
        "id": 23, "category": "sap",
        "question": "A business user needs a brand-new custom table and a simple maintenance screen to manage reference data, without heavy custom development. Which SAP tool fits best?",
        "options": ["SE11/SE54 (table + generated maintenance view)", "SAP Fiori Launchpad configuration alone", "A Transport Request", "The SAP GUI login screen"],
        "correct_answer": "SE11/SE54 (table + generated maintenance view)",
    },
    {
        "id": 24, "category": "sap",
        "question": "What is the purpose of a BAPI in SAP?",
        "options": [
            "A standardized, stable API for external systems or custom code to interact with SAP business objects",
            "A type of SAP user role",
            "A backup and archiving utility",
            "A reporting tool exclusively for SAP HANA",
        ],
        "correct_answer": "A standardized, stable API for external systems or custom code to interact with SAP business objects",
    },
    {
        "id": 25, "category": "sap",
        "question": "A client wants real-time integration between SAP and a non-SAP e-commerce platform for order creation. Which SAP capability is most appropriate to mention first?",
        "options": ["SAP Integration Suite / middleware with IDocs or APIs", "Manual CSV export/import once a week", "Editing the production database directly", "Disabling the firewall between systems"],
        "correct_answer": "SAP Integration Suite / middleware with IDocs or APIs",
    },

    # ---------------- blockchain ----------------
    {
        "id": 40, "category": "blockchain",
        "question": "In Solidity, what keyword marks a function as unable to modify the contract's state?",
        "options": ["view (or pure)", "external", "payable", "immutable"],
        "correct_answer": "view (or pure)",
    },
    {
        "id": 41, "category": "blockchain",
        "question": "A smart contract repeatedly transfers funds to an external contract before updating its own internal balance. What vulnerability does this pattern invite?",
        "options": ["A reentrancy attack", "A 51% attack", "A phishing attack", "A DNS spoofing attack"],
        "correct_answer": "A reentrancy attack",
    },
    {
        "id": 42, "category": "blockchain",
        "question": "What is 'gas' in the context of Ethereum?",
        "options": [
            "A unit measuring the computational work required to execute a transaction or contract, paid by the sender",
            "A physical token representing company equity",
            "A type of consensus algorithm",
            "A wallet encryption standard",
        ],
        "correct_answer": "A unit measuring the computational work required to execute a transaction or contract, paid by the sender",
    },
    {
        "id": 43, "category": "blockchain",
        "question": "Why are smart contracts generally described as 'immutable' once deployed?",
        "options": [
            "The deployed bytecode on-chain cannot be changed in place, so fixes require a new deployment or a proxy pattern",
            "Because blockchain networks ban all future transactions",
            "Because smart contracts have no state at all",
            "Because only the original deployer can ever call the contract again",
        ],
        "correct_answer": "The deployed bytecode on-chain cannot be changed in place, so fixes require a new deployment or a proxy pattern",
    },
    {
        "id": 44, "category": "blockchain",
        "question": "What is the main purpose of a blockchain's consensus mechanism (e.g. Proof of Work, Proof of Stake)?",
        "options": [
            "To let distributed, untrusted nodes agree on a single valid version of the ledger",
            "To encrypt user wallets",
            "To compress transaction data for storage",
            "To generate smart contract source code automatically",
        ],
        "correct_answer": "To let distributed, untrusted nodes agree on a single valid version of the ledger",
    },
    {
        "id": 45, "category": "blockchain",
        "question": "Before deploying a smart contract handling real user funds, what is the single most important practice?",
        "options": [
            "A professional security audit and thorough testing (including edge cases and known attack patterns)",
            "Deploying as fast as possible to be first to market",
            "Skipping testnet deployment to save time",
            "Keeping the source code unpublished so no one can read it",
        ],
        "correct_answer": "A professional security audit and thorough testing (including edge cases and known attack patterns)",
    },

    # ---------------- rpa ----------------
    {
        "id": 60, "category": "rpa",
        "question": "An RPA bot you built keeps breaking because the target application's UI element IDs change slightly between releases. What is the best long-term fix?",
        "options": [
            "Switch to more resilient selectors (e.g. relative/anchor-based selectors) instead of brittle exact-ID matching",
            "Re-record the entire bot from scratch every time the UI changes",
            "Add a long fixed delay everywhere and hope the UI loads in time",
            "Disable error handling so the bot doesn't stop",
        ],
        "correct_answer": "Switch to more resilient selectors (e.g. relative/anchor-based selectors) instead of brittle exact-ID matching",
    },
    {
        "id": 61, "category": "rpa",
        "question": "What is the main business goal of implementing RPA (Robotic Process Automation)?",
        "options": [
            "Automating repetitive, rule-based, high-volume manual tasks to save time and reduce errors",
            "Replacing all software developers",
            "Building new customer-facing mobile apps",
            "Encrypting a company's databases",
        ],
        "correct_answer": "Automating repetitive, rule-based, high-volume manual tasks to save time and reduce errors",
    },
    {
        "id": 62, "category": "rpa",
        "question": "A process you're asked to automate has many exceptions and requires frequent human judgment calls. What should you do?",
        "options": [
            "Flag it as a poor fit for full automation and recommend attended automation or a hybrid/human-in-the-loop approach",
            "Automate it exactly as-is and ignore the exceptions",
            "Refuse to work on it at all",
            "Hard-code every exception you can think of and assume no new ones will appear",
        ],
        "correct_answer": "Flag it as a poor fit for full automation and recommend attended automation or a hybrid/human-in-the-loop approach",
    },
    {
        "id": 63, "category": "rpa",
        "question": "What is an 'orchestrator' in a typical enterprise RPA platform (e.g. UiPath Orchestrator)?",
        "options": [
            "The central system for deploying, scheduling, monitoring, and managing bots at scale",
            "A single robot that only does scheduling for itself",
            "A code editor for writing bot logic",
            "A database used only for storing credentials",
        ],
        "correct_answer": "The central system for deploying, scheduling, monitoring, and managing bots at scale",
    },
    {
        "id": 64, "category": "rpa",
        "question": "Why is exception handling especially important in RPA bots that run unattended overnight?",
        "options": [
            "No human is present to notice or recover from a failure, so the bot must detect, log, and gracefully handle errors itself",
            "Exception handling is only needed for attended bots",
            "It makes the bot run faster",
            "It is required only for compliance paperwork, not for actual reliability",
        ],
        "correct_answer": "No human is present to notice or recover from a failure, so the bot must detect, log, and gracefully handle errors itself",
    },

    # ---------------- embedded ----------------
    {
        "id": 80, "category": "embedded",
        "question": "In embedded C, what does declaring a hardware register pointer as 'volatile' prevent?",
        "options": [
            "The compiler optimizing away repeated reads/writes, since the value can change outside the program's own control flow",
            "The variable from ever being modified at all",
            "Integer overflow",
            "The linker from placing it in RAM",
        ],
        "correct_answer": "The compiler optimizing away repeated reads/writes, since the value can change outside the program's own control flow",
    },
    {
        "id": 81, "category": "embedded",
        "question": "What is the main purpose of an RTOS (Real-Time Operating System) in an embedded product?",
        "options": [
            "To schedule multiple tasks with predictable, bounded timing guarantees",
            "To provide a graphical desktop environment",
            "To run a web browser on the device",
            "To replace the need for any hardware drivers",
        ],
        "correct_answer": "To schedule multiple tasks with predictable, bounded timing guarantees",
    },
    {
        "id": 82, "category": "embedded",
        "question": "A sensor reading routine occasionally returns corrupted data only when a timer interrupt fires mid-read. What class of bug is this?",
        "options": ["A race condition / lack of proper synchronization between the ISR and main code", "A compiler syntax error", "A linker error", "A stack overflow in the bootloader"],
        "correct_answer": "A race condition / lack of proper synchronization between the ISR and main code",
    },
    {
        "id": 83, "category": "embedded",
        "question": "Why do embedded systems commonly use fixed-size buffers and avoid dynamic memory allocation (malloc) in critical code paths?",
        "options": [
            "To avoid heap fragmentation and unpredictable allocation timing on memory-constrained, real-time hardware",
            "Because embedded compilers don't support pointers",
            "Because malloc is always slower than any fixed buffer, with no other reason",
            "Because embedded CPUs cannot address RAM",
        ],
        "correct_answer": "To avoid heap fragmentation and unpredictable allocation timing on memory-constrained, real-time hardware",
    },
    {
        "id": 84, "category": "embedded",
        "question": "What is the role of a bootloader in an embedded device?",
        "options": [
            "Initial code that runs at power-on, sets up hardware, and loads/starts the main application firmware",
            "A tool used only for unit testing",
            "A cloud dashboard for monitoring devices",
            "The device's power supply circuit",
        ],
        "correct_answer": "Initial code that runs at power-on, sets up hardware, and loads/starts the main application firmware",
    },

    # ---------------- gis ----------------
    {
        "id": 100, "category": "gis",
        "question": "What does a 'coordinate reference system' (CRS) define in GIS?",
        "options": [
            "How 2D/3D map coordinates relate to real locations on the Earth's surface",
            "The color palette used for a map layer",
            "The database engine storing the data",
            "The file format of the exported map image",
        ],
        "correct_answer": "How 2D/3D map coordinates relate to real locations on the Earth's surface",
    },
    {
        "id": 101, "category": "gis",
        "question": "Two layers that should visually line up on a map are offset from each other. What is the most likely cause?",
        "options": ["The layers are in different coordinate reference systems and need to be reprojected to match", "The file names are too long", "One layer has more features than the other", "The map's zoom level is too low"],
        "correct_answer": "The layers are in different coordinate reference systems and need to be reprojected to match",
    },
    {
        "id": 102, "category": "gis",
        "question": "What is the difference between vector and raster data in GIS?",
        "options": [
            "Vector data represents discrete features as points/lines/polygons; raster data represents continuous data as a grid of cells (pixels)",
            "Vector data is always 3D; raster is always 2D",
            "They are two names for the exact same format",
            "Raster data cannot be displayed on a map, only analyzed",
        ],
        "correct_answer": "Vector data represents discrete features as points/lines/polygons; raster data represents continuous data as a grid of cells (pixels)",
    },
    {
        "id": 103, "category": "gis",
        "question": "Which task would typically use a 'buffer' analysis in GIS?",
        "options": [
            "Finding all properties within 500 meters of a proposed road",
            "Converting a shapefile to a PDF",
            "Changing the map's color scheme",
            "Renaming a layer",
        ],
        "correct_answer": "Finding all properties within 500 meters of a proposed road",
    },
    {
        "id": 104, "category": "gis",
        "question": "A client wants to automate a repetitive spatial analysis workflow in ArcGIS/QGIS. What is the standard approach?",
        "options": [
            "Script it with Python (ArcPy or PyQGIS) rather than repeating the manual steps each time",
            "Always do it manually to guarantee accuracy",
            "Export to Excel and do the analysis there instead",
            "This cannot be automated in GIS software",
        ],
        "correct_answer": "Script it with Python (ArcPy or PyQGIS) rather than repeating the manual steps each time",
    },

    # ---------------- workday ----------------
    {
        "id": 120, "category": "workday",
        "question": "In Workday, what is a 'Business Process' used for?",
        "options": [
            "Defining a configurable, auditable workflow (steps, approvals, notifications) for an HR action like hiring or a pay change",
            "A type of report chart",
            "A security group for IT administrators only",
            "A physical office location record",
        ],
        "correct_answer": "Defining a configurable, auditable workflow (steps, approvals, notifications) for an HR action like hiring or a pay change",
    },
    {
        "id": 121, "category": "workday",
        "question": "A client needs Workday to automatically notify a manager when a direct report's probation period is about to end. What's the right configuration approach?",
        "options": [
            "A custom Business Process condition/step or a scheduled Workday report with notification, not custom code",
            "Writing a standalone external script that queries Workday's production database directly",
            "Manually tracking it in a spreadsheet instead",
            "This cannot be done in Workday",
        ],
        "correct_answer": "A custom Business Process condition/step or a scheduled Workday report with notification, not custom code",
    },
    {
        "id": 122, "category": "workday",
        "question": "What does 'HCM' stand for in the context of Workday?",
        "options": ["Human Capital Management", "Hosted Configuration Module", "Human Compliance Monitor", "Hierarchical Change Management"],
        "correct_answer": "Human Capital Management",
    },
    {
        "id": 123, "category": "workday",
        "question": "Why would a Workday consultant care about 'security groups' when configuring a new report?",
        "options": [
            "They control exactly which users/roles can see or act on the sensitive data the report exposes",
            "They determine the report's color theme",
            "They only matter for payroll, never for any other report",
            "Security groups are deprecated and no longer used in Workday",
        ],
        "correct_answer": "They control exactly which users/roles can see or act on the sensitive data the report exposes",
    },

    # ---------------- devops_cloud ----------------
    {
        "id": 140, "category": "devops_cloud",
        "question": "A production deployment just went out and error rates are spiking. What is the standard, lowest-risk first response?",
        "options": [
            "Roll back the deployment to the last known-good version while investigating",
            "Immediately write a hotfix and deploy it straight to production without testing",
            "Wait and see if it resolves on its own",
            "Restart the entire production database",
        ],
        "correct_answer": "Roll back the deployment to the last known-good version while investigating",
    },
    {
        "id": 141, "category": "devops_cloud",
        "question": "What is the main benefit of defining infrastructure with Terraform (Infrastructure as Code) instead of manually clicking through a cloud console?",
        "options": [
            "Infrastructure changes become version-controlled, repeatable, and reviewable, like application code",
            "It is the only way to use any cloud provider",
            "It removes the need for any testing",
            "It automatically makes the application code faster",
        ],
        "correct_answer": "Infrastructure changes become version-controlled, repeatable, and reviewable, like application code",
    },
    {
        "id": 142, "category": "devops_cloud",
        "question": "In a CI/CD pipeline, what is the purpose of running automated tests in the 'CI' (Continuous Integration) stage, before deployment?",
        "options": [
            "To catch bugs early by validating every change before it merges or ships further down the pipeline",
            "To slow down releases intentionally",
            "CI stages never run tests, only CD stages do",
            "To replace the need for a staging environment",
        ],
        "correct_answer": "To catch bugs early by validating every change before it merges or ships further down the pipeline",
    },
    {
        "id": 143, "category": "devops_cloud",
        "question": "A container keeps restarting in a crash loop in Kubernetes. What is the first thing you'd check?",
        "options": [
            "The container's logs and recent events (kubectl logs / kubectl describe) to see why it's exiting",
            "The cloud provider's billing dashboard",
            "The DNS settings of an unrelated service",
            "The company's HR system",
        ],
        "correct_answer": "The container's logs and recent events (kubectl logs / kubectl describe) to see why it's exiting",
    },
    {
        "id": 144, "category": "devops_cloud",
        "question": "Why is it best practice to never store cloud API keys or secrets directly in source code / version control?",
        "options": [
            "A leaked repository (or even just its history) would expose the credentials to anyone with access, including attackers",
            "Source control systems cannot technically store text files",
            "It makes the code run slower",
            "It is only a style preference with no real security impact",
        ],
        "correct_answer": "A leaked repository (or even just its history) would expose the credentials to anyone with access, including attackers",
    },
    {
        "id": 145, "category": "devops_cloud",
        "question": "What does 'horizontal scaling' mean in cloud infrastructure?",
        "options": [
            "Adding more instances/servers to handle load, rather than making one server bigger",
            "Making a single server's CPU/RAM bigger",
            "Reducing the number of servers to save cost regardless of load",
            "Moving a service to a different programming language",
        ],
        "correct_answer": "Adding more instances/servers to handle load, rather than making one server bigger",
    },

    # ---------------- networking_systems ----------------
    {
        "id": 160, "category": "networking_systems",
        "question": "A user reports they can't reach a website, but others on the same network can. What's a reasonable first troubleshooting step?",
        "options": [
            "Check the affected user's own DNS/network configuration and try pinging the site's IP directly versus its domain name",
            "Immediately replace the office router",
            "Assume it's a company-wide outage without checking anything else",
            "Restart the entire company's network switches",
        ],
        "correct_answer": "Check the affected user's own DNS/network configuration and try pinging the site's IP directly versus its domain name",
    },
    {
        "id": 161, "category": "networking_systems",
        "question": "What is the main purpose of a firewall?",
        "options": [
            "To control and filter incoming/outgoing network traffic based on defined security rules",
            "To speed up internet connections",
            "To physically cool down server hardware",
            "To back up files automatically",
        ],
        "correct_answer": "To control and filter incoming/outgoing network traffic based on defined security rules",
    },
    {
        "id": 162, "category": "networking_systems",
        "question": "What does DHCP do on a network?",
        "options": [
            "Automatically assigns IP addresses and network settings to devices as they connect",
            "Encrypts all network traffic",
            "Manages user login passwords",
            "Monitors CPU temperature on servers",
        ],
        "correct_answer": "Automatically assigns IP addresses and network settings to devices as they connect",
    },
    {
        "id": 163, "category": "networking_systems",
        "question": "A remote employee needs secure access to internal company resources over the public internet. What technology is typically used?",
        "options": ["A VPN (Virtual Private Network)", "A public FTP server with no authentication", "Disabling the company firewall for that user", "Sharing the office Wi-Fi password publicly"],
        "correct_answer": "A VPN (Virtual Private Network)",
    },
    {
        "id": 164, "category": "networking_systems",
        "question": "What is the difference between TCP and UDP at a high level?",
        "options": [
            "TCP is connection-oriented and guarantees delivery/order; UDP is connectionless and faster but offers no delivery guarantee",
            "They are two names for the exact same protocol",
            "UDP always encrypts data; TCP never does",
            "TCP only works on local networks, never over the internet",
        ],
        "correct_answer": "TCP is connection-oriented and guarantees delivery/order; UDP is connectionless and faster but offers no delivery guarantee",
    },
    {
        "id": 165, "category": "networking_systems",
        "question": "On a Linux server, a critical service has stopped responding. Which is a reasonable first command-line step?",
        "options": [
            "Check its status and recent logs (e.g. systemctl status, journalctl) before restarting it blindly",
            "Immediately reinstall the entire operating system",
            "Delete the service's configuration files",
            "Unplug the server's power cable",
        ],
        "correct_answer": "Check its status and recent logs (e.g. systemctl status, journalctl) before restarting it blindly",
    },

    # ---------------- cybersecurity ----------------
    {
        "id": 180, "category": "cybersecurity",
        "question": "What is the primary difference between a vulnerability scan and a penetration test?",
        "options": [
            "A scan automatically identifies known weaknesses; a pen test actively attempts to exploit them to show real-world impact",
            "They are two names for the exact same activity",
            "A vulnerability scan is always illegal, a pen test never is",
            "A pen test only checks password strength",
        ],
        "correct_answer": "A scan automatically identifies known weaknesses; a pen test actively attempts to exploit them to show real-world impact",
    },
    {
        "id": 181, "category": "cybersecurity",
        "question": "A SOC analyst sees repeated failed logins from one external IP against many different accounts in a short window. What does this pattern most likely indicate?",
        "options": [
            "A password spraying / brute-force attack attempt",
            "A routine software update",
            "A DNS misconfiguration",
            "A scheduled backup job",
        ],
        "correct_answer": "A password spraying / brute-force attack attempt",
    },
    {
        "id": 182, "category": "cybersecurity",
        "question": "What does a SIEM (Security Information and Event Management) system primarily do?",
        "options": [
            "Aggregates and correlates log/event data from across systems to detect and alert on suspicious activity",
            "Encrypts all outbound company email",
            "Physically isolates infected machines from the network",
            "Manages employee payroll records",
        ],
        "correct_answer": "Aggregates and correlates log/event data from across systems to detect and alert on suspicious activity",
    },
    {
        "id": 183, "category": "cybersecurity",
        "question": "During incident response, why is it important to preserve a compromised system's memory and disk image before remediating it?",
        "options": [
            "To preserve forensic evidence of how the attack happened, which is lost once the system is cleaned or rebooted",
            "It is only required for legal paperwork, never useful technically",
            "Memory images are needed to reinstall the operating system",
            "It speeds up the remediation process",
        ],
        "correct_answer": "To preserve forensic evidence of how the attack happened, which is lost once the system is cleaned or rebooted",
    },
    {
        "id": 184, "category": "cybersecurity",
        "question": "What is the core idea behind the 'principle of least privilege'?",
        "options": [
            "Users and systems should only have the minimum access needed to do their job, nothing more",
            "Every employee should have administrator access by default",
            "Only the IT department needs any security controls",
            "Passwords should never be changed once set",
        ],
        "correct_answer": "Users and systems should only have the minimum access needed to do their job, nothing more",
    },
    {
        "id": 185, "category": "cybersecurity",
        "question": "An email claims to be from the company's bank, urgently asking an employee to click a link and 're-verify' their account password. What is this an example of?",
        "options": [
            "A phishing attack", "A zero-day exploit", "A DDoS attack", "A SQL injection attack",
        ],
        "correct_answer": "A phishing attack",
    },

    # ---------------- nosql ----------------
    {
        "id": 200, "category": "nosql",
        "question": "What is the main structural difference between a NoSQL document database (e.g. MongoDB) and a relational (SQL) database?",
        "options": [
            "Data is stored as flexible, often nested JSON-like documents instead of fixed rows in predefined tables",
            "NoSQL databases cannot store any data on disk",
            "They are identical; NoSQL is just a marketing term for SQL",
            "NoSQL databases only support a single document per database",
        ],
        "correct_answer": "Data is stored as flexible, often nested JSON-like documents instead of fixed rows in predefined tables",
    },
    {
        "id": 201, "category": "nosql",
        "question": "In MongoDB, which operation would you use to retrieve all documents in a 'products' collection where 'price' is greater than 100?",
        "options": [
            "db.products.find({ price: { $gt: 100 } })",
            "SELECT * FROM products WHERE price > 100",
            "db.products.filter(price > 100)",
            "GET products WHERE price > 100",
        ],
        "correct_answer": "db.products.find({ price: { $gt: 100 } })",
    },
    {
        "id": 202, "category": "nosql",
        "question": "A high-traffic app needs sub-millisecond lookups of session data by a single key, with data that can be lost on restart without major harm. Which type of database fits best?",
        "options": [
            "An in-memory key-value store (e.g. Redis)", "A graph database", "A relational database with heavy indexing", "A document database with deep nesting",
        ],
        "correct_answer": "An in-memory key-value store (e.g. Redis)",
    },
    {
        "id": 203, "category": "nosql",
        "question": "What does 'eventual consistency' mean in a distributed NoSQL database like Cassandra?",
        "options": [
            "Updates propagate to all nodes over time, so a read immediately after a write might not reflect it yet on every node",
            "Data is never consistent and queries return random results",
            "The database refuses all writes until every node agrees first",
            "It is a synonym for ACID transactions",
        ],
        "correct_answer": "Updates propagate to all nodes over time, so a read immediately after a write might not reflect it yet on every node",
    },
    {
        "id": 204, "category": "nosql",
        "question": "When would a graph database (e.g. Neo4j) be a better fit than a relational database?",
        "options": [
            "When the data's relationships and connections (e.g. social networks, recommendation paths) matter as much as the data itself",
            "When the data is purely tabular with no relationships at all",
            "When the application needs strict SQL-standard compliance",
            "Graph databases are strictly slower and never a better fit for anything",
        ],
        "correct_answer": "When the data's relationships and connections (e.g. social networks, recommendation paths) matter as much as the data itself",
    },
    {
        "id": 205, "category": "nosql",
        "question": "A MongoDB collection is being queried very slowly as it grows to millions of documents. What is the most likely first thing to check?",
        "options": [
            "Whether the queried fields have an appropriate index",
            "Whether the collection name is too long",
            "Whether the documents use too many comments",
            "Whether the database is using too many colors in its UI",
        ],
        "correct_answer": "Whether the queried fields have an appropriate index",
    },
]


def get_tda_question_bank() -> list[dict]:
    """Returns a deep copy — callers must never mutate the shared bank."""
    return copy.deepcopy(TDA_QUESTION_BANK)


def get_tda_domains() -> list[str]:
    """Every domain the bank actually has questions for — kept in
    sync with role_classification.py's _DOMAIN_KEYWORDS by test
    coverage, not by import (that module has no reason to import
    this content-only one)."""

    return sorted({q["category"] for q in TDA_QUESTION_BANK})
