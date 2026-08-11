import os
import sqlite3
from datetime import date

from library import DB_NAME, connect, is_initialised


class LibraryError(Exception):
    """Error Handling For Cases Beyond SQLite/DB"""


# Loans with due date derived from the item type's loan period, so the
# queries below can select due_date by name
_LOANS = """
    (SELECT l.loan_id, l.item_id, l.copy_number, l.member_id, i.title, l.checkout_date, l.return_date,
            date(l.checkout_date, '+' || t.loan_period || ' days') AS due_date
       FROM Loan l
       JOIN Item i     ON i.item_id   = l.item_id
       JOIN ItemType t ON t.type_code = i.type_code)
"""

def today():
    return date.today().isoformat()

# (1) Find Items

def find_items(conn, keyword=""):
    # A NULL creator fails LIKE on its own, so ifnull() would add nothing.
    return conn.execute("""
        SELECT i.item_id, i.title, i.creator, i.published_year, i.language,
               it.type_name, it.loan_period, it.daily_fine_rate,
               count(c.copy_number) AS total_copies,
               count(*) FILTER (WHERE c.copy_status = 'available') AS available_copies
          FROM Item i
          JOIN ItemType it ON it.type_code = i.type_code
          LEFT JOIN Copy c ON c.item_id    = i.item_id
         WHERE i.title LIKE :keyword OR i.creator LIKE :keyword
         GROUP BY i.item_id
         ORDER BY i.title
    """, {"keyword": "%" + keyword + "%"}).fetchall()


def find_available_copies(conn, item_id):
    # Retrieving item_id on copies with available status
    return conn.execute("""
        SELECT item_id, copy_number, acquisition_date, copy_status
         FROM Copy WHERE item_id = ? AND copy_status = 'available'
         ORDER BY copy_number
    """, (item_id,)).fetchall()

# (2) Borrow Items

def borrow_item(conn, member_id, item_id, copy_number, checkout_date=None):
    # Borrowing item, returns loan_id and due_date
    checkout_date = checkout_date or today()
    with conn:
        cur = conn.execute("""
            INSERT INTO Loan (item_id, copy_number, member_id, checkout_date)
            VALUES (?, ?, ?, ?)
        """, (item_id, copy_number, member_id, checkout_date))
    loan_id = cur.lastrowid
    return loan_id, due_date(conn, loan_id)


def due_date(conn, loan_id):
    # Due date: check out date + loan period for item type
    row = conn.execute(
        "SELECT due_date FROM " + _LOANS + " WHERE loan_id = ?", (loan_id,)).fetchone()
    if row is None:
        raise LibraryError(f"Loan not found with id: {loan_id}.")
    return row["due_date"]

def outstanding_loans(conn, member_id):
    # Loans past expiry date, returns member_id
    return conn.execute("""
        SELECT loan_id, item_id, copy_number, title, checkout_date, due_date,
               CAST(julianday(:today) - julianday(due_date) AS integer) AS days_overdue
         FROM """ + _LOANS + """
         WHERE member_id = :member_id AND return_date IS NULL
         ORDER BY due_date
    """, {"today": today(), "member_id": member_id}).fetchall()

# (3) Return Item

def return_item(conn, loan_id, return_date=None, member_id=None):
    # Return item .. function returns (due_date, days_overdue, fine_amount_or_None).
    return_date = return_date or today()
    with conn:
        cur = conn.execute("""
            UPDATE Loan SET return_date = ?
             WHERE loan_id = ? AND return_date IS NULL
               AND (? IS NULL OR member_id = ?)
        """, (return_date, loan_id, member_id, member_id))
        if cur.rowcount == 0:
            if member_id is None:
                raise LibraryError(f"Loan {loan_id} does not exist or has already been returned")
            raise LibraryError(
                f"""Loan {loan_id} does not exist, has already been returned, or is not 
                on loan to member {member_id}""")

        row = conn.execute("""
            SELECT due_date,
                   CAST(julianday(return_date) - julianday(due_date) AS integer) AS days_overdue
              FROM """ + _LOANS + """
             WHERE loan_id = ?
        """, (loan_id,)).fetchone()

        fine = 0
        if row["days_overdue"] > 0:
            conn.execute(
                "INSERT INTO Fine (loan_id, assessed_date) VALUES (?, ?)",
                (loan_id, return_date))
            fine = conn.execute(
                "SELECT amount FROM Fine WHERE loan_id = ?",
                (loan_id,)).fetchone()["amount"]

    return row["due_date"], row["days_overdue"], fine


# --- 4. Donate an item to the library ----------------------------------------

def donate_item(conn, member_id, title, type_code, creator=None,
                published_year=None, language=None, donated_date=None):
    """Accept a donated item.  Returns (item_id, copy_number).

    Copy.acquisition_method and Copy.donated_by record how the copy arrived and
    who gave it, so the donation needs no stand-in row elsewhere.  It is
    catalogued as a new item, so its copy is always copy 1, and both inserts are
    one transaction -- a failure leaves nothing behind."""
    donated_date = donated_date or today()

    if conn.execute("SELECT 1 FROM ItemType WHERE type_code = ?",
                    (type_code,)).fetchone() is None:
        known = ", ".join(r["type_code"] for r in
                          conn.execute("SELECT type_code FROM ItemType"))
        raise LibraryError("There is no item type '%s'.  Known types: %s"
                           % (type_code, known))

    with conn:
        cur = conn.execute("""
            INSERT INTO Item (title, creator, published_year, language, type_code)
            VALUES (?, ?, ?, ?, ?)
        """, (title, creator, published_year, language, type_code))

        conn.execute("""
            INSERT INTO Copy (item_id, copy_number, acquisition_date,
                              acquisition_method, donated_by, copy_status)
            VALUES (?, 1, ?, 'donation', ?, 'available')
        """, (cur.lastrowid, donated_date, member_id))

    return cur.lastrowid, 1


# --- 5. Find an event in the library -----------------------------------------

def find_events(conn, keyword=None, upcoming_only=True):
    """Events matching a keyword, with seats left."""
    pattern = "%" + (keyword or "") + "%"
    return conn.execute("""
        SELECT e.event_id, e.title, e.event_category, e.audience_category,
               e.start_datetime, e.end_datetime, e.max_attendees,
               e.room_number, r.capacity,
               count(g.member_id) AS registered,
               e.max_attendees - count(g.member_id) AS seats_left
          FROM Event e
          JOIN Room r              ON r.room_number = e.room_number
          LEFT JOIN Registration g ON g.event_id    = e.event_id
         WHERE (e.title LIKE ? OR e.event_category LIKE ?
                OR e.audience_category LIKE ?)
           AND (? = 0 OR e.start_datetime >= datetime('now'))
         GROUP BY e.event_id
         ORDER BY e.start_datetime
    """, (pattern, pattern, pattern, 1 if upcoming_only else 0)).fetchall()


# --- 6. Register for an event ------------------------------------------------

def register_for_event(conn, member_id, event_id, registration_date=None):
    """Register a member.  BR11 (not suspended) and BR18 (capacity) are trigger
    rules; BR17 (at most once) is the composite primary key."""
    registration_date = registration_date or today()
    with conn:
        conn.execute("""
            INSERT INTO Registration (member_id, event_id, registration_date)
            VALUES (?, ?, ?)
        """, (member_id, event_id, registration_date))
    return member_id, event_id


# --- 7. Volunteer for the library --------------------------------------------

def volunteer(conn, member_id, role="General"):
    """Sign a member up as a volunteer.  Returns the new employee_id.

    Volunteers are stored as Employee rows (see the module docstring), and the
    job title marks them as volunteers rather than paid staff."""
    member = conn.execute("""
        SELECT first_name, last_name, phone FROM Member WHERE member_id = ?
    """, (member_id,)).fetchone()
    if member is None:
        raise LibraryError("There is no member with id %s." % member_id)

    # BR19: exactly one employee may have no supervisor, so a volunteer needs
    # one.  The head librarian is the employee with no supervisor.
    head = conn.execute(
        "SELECT employee_id FROM Employee WHERE supervisor_id IS NULL").fetchone()
    if head is None:
        raise LibraryError(
            "No head librarian is on file yet, so a volunteer has nobody to "
            "report to.  Add the head librarian before signing up volunteers.")

    # Employee has no member_id, so name and phone are the only link back.
    already = conn.execute("""
        SELECT employee_id FROM Employee
         WHERE first_name = ? AND last_name = ? AND phone = ?
           AND job_title LIKE 'Volunteer%'
    """, (member["first_name"], member["last_name"], member["phone"])).fetchone()
    if already is not None:
        raise LibraryError("%s %s already volunteers here (employee %s)."
                           % (member["first_name"], member["last_name"],
                              already["employee_id"]))

    with conn:
        cur = conn.execute("""
            INSERT INTO Employee (first_name, last_name, job_title, salary,
                                  phone, supervisor_id)
            VALUES (?, ?, ?, 0, ?, ?)
        """, (member["first_name"], member["last_name"],
              "Volunteer - " + role, member["phone"], head["employee_id"]))
    return cur.lastrowid


# --- 8. Ask for help from a librarian ----------------------------------------

def ask_librarian(conn, question=""):
    """The librarians who can help.  The question is not stored: the schema has
    no relation for help requests (see the module docstring)."""
    return conn.execute("""
        SELECT employee_id, first_name, last_name, job_title, phone
          FROM Employee
         WHERE job_title LIKE '%Librarian%'
           AND job_title NOT LIKE 'Volunteer%'
         ORDER BY job_title, last_name
    """).fetchall()


# --- Command line interface --------------------------------------------------

def _show(rows, columns=None):
    """Print rows as an aligned table."""
    rows = list(rows)
    if not rows:
        print("  (nothing found)")
        return
    columns = columns or rows[0].keys()
    widths = [max(len(str(c)), max(len(str(r[c])) for r in rows))
              for c in columns]

    def line(values):
        return "  " + "  ".join(str(v).ljust(w) for v, w in zip(values, widths))

    print(line(columns))
    print(line("-" * w for w in widths))
    for r in rows:
        print(line(r[c] for c in columns))


def _ask(prompt, cast=str, optional=False):
    raw = input(prompt).strip()
    if not raw:
        if optional:
            return None
        raise LibraryError("A value is required.")
    try:
        return cast(raw)
    except ValueError:
        # int is the only cast here that can fail, and main() handles just
        # LibraryError and sqlite3.Error, so a bare ValueError would end the
        # session on a typo instead of going back to the menu.
        raise LibraryError("'%s' is not a whole number." % raw)


def _do_find_item(conn):
    keyword = _ask("  Title or creator: ", optional=True) or ""
    _show(find_items(conn, keyword),
          ["item_id", "title", "creator", "type_name",
           "total_copies", "available_copies"])


def _do_borrow(conn):
    member_id = _ask("  Member id: ", int)
    item_id = _ask("  Item id: ", int)
    free = find_available_copies(conn, item_id)
    if not free:
        raise LibraryError("No copies of item %s are available." % item_id)
    _show(free, ["item_id", "copy_number", "acquisition_date"])
    copy_number = _ask("  Copy number: ", int)
    loan_id, due = borrow_item(conn, member_id, item_id, copy_number)
    print("  Loan %s created.  Due back %s." % (loan_id, due))


def _do_return(conn):
    member_id = _ask("  Member id: ", int)
    loans = outstanding_loans(conn, member_id)
    if not loans:
        raise LibraryError("Member %s has nothing on loan." % member_id)
    _show(loans, ["loan_id", "title", "checkout_date", "due_date", "days_overdue"])
    loan_id = _ask("  Loan id to return: ", int)
    due, overdue, fine = return_item(conn, loan_id, member_id=member_id)
    if fine is None:
        print("  Returned on time (due %s).  No fine." % due)
    else:
        print("  Returned %s day(s) late (due %s).  Fine assessed: %.2f"
              % (overdue, due, fine))


def _do_donate(conn):
    member_id = _ask("  Your member id: ", int)
    title = _ask("  Title: ")
    _show(conn.execute("SELECT type_code, type_name FROM ItemType").fetchall())
    type_code = _ask("  Item type code: ")
    creator = _ask("  Creator (optional): ", optional=True)
    year = _ask("  Published year (optional): ", int, optional=True)
    language = _ask("  Language (optional): ", optional=True)
    item_id, copy_number = donate_item(
        conn, member_id, title, type_code, creator, year, language)
    print("  Thank you.  Catalogued as item %s copy %s, recorded as your donation."
          % (item_id, copy_number))


def _do_find_event(conn):
    keyword = _ask("  Title, category or audience: ", optional=True)
    _show(find_events(conn, keyword),
          ["event_id", "title", "event_category", "audience_category",
           "start_datetime", "room_number", "seats_left"])


def _do_register(conn):
    member_id = _ask("  Member id: ", int)
    event_id = _ask("  Event id: ", int)
    register_for_event(conn, member_id, event_id)
    print("  Member %s is registered for event %s." % (member_id, event_id))


def _do_volunteer(conn):
    member_id = _ask("  Member id: ", int)
    role = _ask("  Role (optional): ", optional=True) or "General"
    employee_id = volunteer(conn, member_id, role)
    print("  Signed up as volunteer (employee %s, role %s)." % (employee_id, role))


def _do_ask_librarian(conn):
    question = _ask("  What do you need help with? ", optional=True) or ""
    librarians = ask_librarian(conn, question)
    if not librarians:
        raise LibraryError("No librarians are on file.")
    print("  These librarians can help:")
    _show(librarians, ["employee_id", "first_name", "last_name",
                       "job_title", "phone"])
    print("  (Note: the question itself is not stored -- the schema has no"
          " relation for help requests.)")


# The menu is printed from this list, so the numbering has one source.
ACTIONS = [
    ("Find an item in the library", _do_find_item),
    ("Borrow an item from the library", _do_borrow),
    ("Return a borrowed item", _do_return),
    ("Donate an item to the library", _do_donate),
    ("Find an event in the library", _do_find_event),
    ("Register for an event in the library", _do_register),
    ("Volunteer for the library", _do_volunteer),
    ("Ask for help from a librarian", _do_ask_librarian),
]


def _open_database():
    """The connection, or None if the database is not set up yet."""
    # Check the file exists before connecting: sqlite3.connect() would create an
    # empty database as a side effect, leaving a stray file behind on refusal.
    conn = connect() if os.path.exists(DB_NAME) else None
    if conn is not None and is_initialised(conn):
        return conn
    if conn is not None:
        conn.close()
    print("Database is not set up yet.")
    print("Run `python library.py` to create the schema, triggers and "
          "data, then try again.")
    return None


def main():
    conn = _open_database()
    if conn is None:
        return

    print("\nLibrary database application.")
    while True:
        print()
        for number, (label, _) in enumerate(ACTIONS, 1):
            print("  %s. %s" % (number, label))
        print("  0. Quit\n")
        try:
            choice = input("  Choose an option: ").strip()
            if choice == "0":
                break
            if not choice.isdigit() or not 1 <= int(choice) <= len(ACTIONS):
                print("  '%s' is not one of the options." % choice)
                continue
            ACTIONS[int(choice) - 1][1](conn)
        except (sqlite3.Error, LibraryError) as err:
            # The triggers raise messages that already read as sentences
            # ("BR10: a member may have at most five items on loan at once"),
            # so print what the database said and go back to the menu.
            print("  %s" % err)
        except EOFError:
            break
    conn.close()
    print("  Goodbye.")


if __name__ == "__main__":
    main()
