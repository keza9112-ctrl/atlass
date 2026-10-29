# Refined Library Management System

An independently built library-management application inspired by the *general workflow* of the supplied HTML reference. It has its own interface, code, database schema, and branding; it does not reuse the reference's source code or embedded assets.

## Included modules
- Dashboard statistics: titles, total copies, available copies, active loans, overdue loans, and members.
- Book catalogue: add, search, edit, archive; checks active loans before reducing stock.
- Member register: students, teachers, and staff; automatically generated member codes when left blank.
- Checkout and returns: copy availability validation, due dates, return date, optional daily fine calculation.
- Loan history, overdue list, category summary, popular borrowers.
- CSV exports for books, members, and loans.
- Admin-managed staff accounts and roles.
- Password change, audit log entries for key operations, and SQLite database backups.

## Run on Windows (VS Code or normal terminal)
1. Install Python 3.10+ from https://www.python.org/downloads/ and enable **Add Python to PATH**.
2. Extract this ZIP to a writable folder.
3. Open that folder in VS Code.
4. Double-click `run_windows.bat`, or in the VS Code terminal run:
   ```powershell
   py -m venv .venv
   .\.venv\Scripts\Activate.ps1
   python -m pip install -r requirements.txt
   python app.py
   ```
   If PowerShell blocks activation, use `.\.venv\Scripts\python.exe -m pip install -r requirements.txt` and `.\.venv\Scripts\python.exe app.py`.
5. Visit http://127.0.0.1:5000.

## First sign-in
- Username: `admin`
- Temporary password: `ChangeMe123!`
- Change the password immediately from **Account**. The initial account is created only when the database has no users.

## Database and data-loss protection
- Records are stored in `data/library.sqlite3`, not browser local storage.
- SQLite foreign keys, transactions, write-ahead logging, busy timeout, and constraints help protect consistency.
- Checkout is saved as a transaction; available-copy validation prevents issuing more copies than the library owns.
- Archive actions preserve history instead of deleting records.
- Admin can create timestamped database backups from **Backups** and download them.
- Export CSV files regularly as an additional portable copy.

**Important:** no system can promise zero data loss. Backups must be stored on a second device or secure cloud location, and restore tests should be done periodically. The included backup page creates and downloads backups but does not automatically upload them off-device. Do not expose the development server to the public internet as-is. For production, add HTTPS, environment-managed secret keys, CSRF protection, deployment hardening, and automated off-site backups.

## Database structure
- `users`: login identities, password hashes, role and active status.
- `books`: title metadata, shelf, total copy count.
- `members`: member code, identity, member type and contact details.
- `loans`: checkout, due/return dates, quantity, status, fine and creating user.
- `audit_log`: who performed important actions and when.

Relationships are enforced by foreign keys. A loan references one book and one member; archived members/books remain available for historical joins.

## Roles
- **Admin**: all modules, user management, database backups.
- **Librarian**: catalogue, members, checkouts/returns, CSV export.
- **Viewer**: dashboard, catalogue, member list, history, reports.

## Notes
- The app seeds a few clearly named sample books and members the first time the database is created. Remove those sample records before real use.
- Fine amount is calculated using the daily fine value entered at return time; this starter does not implement payments.
- This is a functional starter suitable for local testing and iterative development, not a certified production deployment.
