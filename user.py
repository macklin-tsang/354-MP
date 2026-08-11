"""Library application -- CMPT 354 mini project.

The eight operations a library user needs: find an item, borrow it, return it,
donate one, find an event, register for it, volunteer, and ask a librarian for
help.

    python user.py

opens the menu.  The database must already exist -- run `python library.py`
first; this module never creates schema.

Two of the eight operations have no table behind them -- the report's section
1.2 never describes volunteering or help requests -- so they are mapped onto the
existing schema.  The mappings are lossy, and knowingly so:

  * volunteer()     inserts an Employee row with job_title 'Volunteer - <role>'
                    and salary 0.  Consequences: staff counts, payroll sums and
                    supervision queries include volunteers unless they filter
                    job_title NOT LIKE 'Volunteer%'; and since Employee has no
                    member_id there is no reliable link back to the member who
                    volunteered -- this module matches on name plus phone, which
                    is not a key and can collide.

  * ask_librarian() does not persist anything.  It returns the librarians on
                    duty; the question is lost when the process exits.
"""

import os
import sqlite3
from datetime import date

from library import DB_NAME, connect, is_initialised


class LibraryError(Exception):
    """A request that cannot be carried out for a reason SQLite has no
    constraint for: an unknown member, no head librarian on file, and so on.
    Business rules are never raised from here -- the triggers raise those, and
    they arrive as sqlite3.Error carrying the trigger's own message.

    It lives in this module because this is the only layer that raises it.
    library.py builds the schema and runs SQL; anything that goes wrong there is
    already a sqlite3.Error worth reading as-is."""


# ---------------------------------------------------------------------------
# Item Type used to derive due date. 
# ---------------------------------------------------------------------------

_LOAN_JOIN = """
      FROM Loan l
      JOIN Item i     ON i.item_id   = l.item_id
      JOIN ItemType t ON t.type_code = i.type_code
"""

_DUE_DATE = "date(l.checkout_date, '+' || t.loan_period || ' days')"


def _today():
    return date.today().isoformat()


# ---------------------------------------------------------------------------
# 1. Allow user to find an item in the library
# ---------------------------------------------------------------------------

def find_items(conn, keyword=""):
    """Items whose title or creator matches, with how many copies are free."""
    pattern = "%" + keyword + "%"
    return conn.execute("""
        SELECT i.item_id, i.title, i.creator, i.published_year, i.language,
               t.type_name, t.loan_period, t.daily_fine_rate,
               (SELECT count(*) FROM Copy c
                 WHERE c.item_id = i.item_id) AS total_copies,
               (SELECT count(*) FROM Copy c
                 WHERE c.item_id = i.item_id
                   AND c.copy_status = 'available') AS available_copies
          FROM Item i
          JOIN ItemType t ON t.type_code = i.type_code
         WHERE i.title LIKE ? OR ifnull(i.creator, '') LIKE ?
         ORDER BY i.title
    """, (pattern, pattern)).fetchall()

def find_available_copies(conn, item_id):
    """Copies of an item that are free to borrow right now."""
    return conn.execute("""
        SELECT c.item_id, c.copy_number, c.acquisition_date, c.copy_status
          FROM Copy c
         WHERE c.item_id = ?
           AND c.copy_status = 'available'
           AND NOT EXISTS (SELECT 1 FROM Loan l
                            WHERE l.item_id     = c.item_id
                              AND l.copy_number = c.copy_number
                              AND l.return_date IS NULL)
         ORDER BY c.copy_number
    """, (item_id,)).fetchall()

# ---------------------------------------------------------------------------
# 2. Borrow an item from the library
# ---------------------------------------------------------------------------

def borrow_item(conn, member_id, item_id, copy_number, checkout_date=None):
    """Check a copy out.  Returns (loan_id, due_date).

    BR6 (copy free), BR10 (five-loan cap) and BR11 (not suspended) are enforced
    by triggers; a violation surfaces as sqlite3.IntegrityError carrying the
    trigger's own message.
    """
    checkout_date = checkout_date or _today()
    with conn:
        cur = conn.execute("""
            INSERT INTO Loan (item_id, copy_number, member_id, checkout_date)
            VALUES (?, ?, ?, ?)
        """, (item_id, copy_number, member_id, checkout_date))
        loan_id = cur.lastrowid
    return loan_id, due_date_of(conn, loan_id)


def due_date_of(conn, loan_id):
    """The derived due date of a loan: checkout date plus the type's period."""
    row = conn.execute(
        "SELECT " + _DUE_DATE + " AS due_date" + _LOAN_JOIN +
        " WHERE l.loan_id = ?", (loan_id,)).fetchone()
    if row is None:
        raise LibraryError("There is no loan with id %s." % loan_id)
    return row["due_date"]


def outstanding_loans(conn, member_id):
    """A member's un-returned loans, with derived due dates and days overdue."""
    return conn.execute(
        "SELECT l.loan_id, l.item_id, l.copy_number, i.title,"
        "       l.checkout_date, " + _DUE_DATE + " AS due_date,"
        "       CAST(julianday('now') - julianday(" + _DUE_DATE +
        "            ) AS integer) AS days_overdue" + _LOAN_JOIN +
        " WHERE l.member_id = ? AND l.return_date IS NULL"
        " ORDER BY due_date", (member_id,)).fetchall()


# ---------------------------------------------------------------------------
# 3. Return a borrowed item
# ---------------------------------------------------------------------------

def return_item(conn, loan_id, return_date=None):
    """Return a copy.  Returns (due_date, days_overdue, fine_amount_or_None).

    The BR13 trigger refuses a fine on a loan that was not late and computes the
    amount itself, so this only decides whether to create the Fine row at all
    and then reads back what the trigger worked out.
    """
    return_date = return_date or _today()
    with conn:
        cur = conn.execute(
            "UPDATE Loan SET return_date = ? WHERE loan_id = ? "
            "AND return_date IS NULL", (return_date, loan_id))
        if cur.rowcount == 0:
            raise LibraryError(
                "Loan %s does not exist or has already been returned." % loan_id)

        row = conn.execute(
            "SELECT " + _DUE_DATE + " AS due_date,"
            "       CAST(julianday(l.return_date) - julianday(" + _DUE_DATE +
            "            ) AS integer) AS days_overdue" + _LOAN_JOIN +
            " WHERE l.loan_id = ?", (loan_id,)).fetchone()

        fine = None
        if row["days_overdue"] > 0:
            conn.execute(
                "INSERT INTO Fine (loan_id, assessed_date) VALUES (?, ?)",
                (loan_id, return_date))
            fine = conn.execute(
                "SELECT amount FROM Fine WHERE loan_id = ?",
                (loan_id,)).fetchone()["amount"]

    return row["due_date"], row["days_overdue"], fine


# ---------------------------------------------------------------------------
# 4. Donate an item to the library
# ---------------------------------------------------------------------------

def donate_item(conn, member_id, title, type_code, creator=None,
                published_year=None, language=None, donated_date=None):
    """Accept a donated item.  Returns (item_id, copy_number).

    Copy.acquisition_method and Copy.donated_by record how the copy arrived and
    who gave it, so the donation needs no stand-in row elsewhere.  Both inserts
    are one transaction -- a failure leaves nothing.
    """
    donated_date = donated_date or _today()

    known = conn.execute(
        "SELECT 1 FROM ItemType WHERE type_code = ?", (type_code,)).fetchone()
    if known is None:
        raise LibraryError(
            "There is no item type '%s'.  Known types: %s" %
            (type_code, ", ".join(r["type_code"] for r in
                                  conn.execute("SELECT type_code FROM ItemType"))))

    with conn:
        cur = conn.execute("""
            INSERT INTO Item (title, creator, published_year, language, type_code)
            VALUES (?, ?, ?, ?, ?)
        """, (title, creator, published_year, language, type_code))
        item_id = cur.lastrowid

        conn.execute("""
            INSERT INTO Copy (item_id, copy_number, acquisition_date,
                              acquisition_method, donated_by, copy_status)
            VALUES (?, 1, ?, 'donation', ?, 'available')
        """, (item_id, donated_date, member_id))

    return item_id, 1


# ---------------------------------------------------------------------------
# 5. Find an event in the library
# ---------------------------------------------------------------------------

def find_events(conn, keyword=None, upcoming_only=True):
    """Events matching a keyword, with seats left."""
    pattern = "%" + (keyword or "") + "%"
    return conn.execute("""
        SELECT e.event_id, e.title, e.event_category, e.audience_category,
               e.start_datetime, e.end_datetime, e.max_attendees,
               e.room_number, r.capacity,
               (SELECT count(*) FROM Registration g
                 WHERE g.event_id = e.event_id) AS registered,
               e.max_attendees - (SELECT count(*) FROM Registration g
                                   WHERE g.event_id = e.event_id) AS seats_left
          FROM Event e
          JOIN Room r ON r.room_number = e.room_number
         WHERE (e.title LIKE ? OR e.event_category LIKE ?
                OR e.audience_category LIKE ?)
           AND (? = 0 OR e.start_datetime >= datetime('now'))
         ORDER BY e.start_datetime
    """, (pattern, pattern, pattern, 1 if upcoming_only else 0)).fetchall()


# ---------------------------------------------------------------------------
# 6. Register for an event
# ---------------------------------------------------------------------------

def register_for_event(conn, member_id, event_id, registration_date=None):
    """Register a member.  BR11 (not suspended) and BR18 (capacity) are trigger
    rules; BR17 (at most once) is the composite primary key."""
    registration_date = registration_date or _today()
    with conn:
        conn.execute("""
            INSERT INTO Registration (member_id, event_id, registration_date)
            VALUES (?, ?, ?)
        """, (member_id, event_id, registration_date))
    return member_id, event_id


# ---------------------------------------------------------------------------
# 7. Volunteer for the library
# ---------------------------------------------------------------------------

def volunteer(conn, member_id, role="General"):
    """Sign a member up as a volunteer.  Returns the new employee_id.

    See the module docstring: volunteers are stored as Employee rows, which is
    the only staff-shaped table in the schema.
    """
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

    already = conn.execute("""
        SELECT employee_id FROM Employee
         WHERE first_name = ? AND last_name = ? AND phone = ?
           AND job_title LIKE 'Volunteer%'
    """, (member["first_name"], member["last_name"], member["phone"])).fetchone()
    if already is not None:
        raise LibraryError(
            "%s %s already volunteers here (employee %s)." %
            (member["first_name"], member["last_name"], already["employee_id"]))

    with conn:
        cur = conn.execute("""
            INSERT INTO Employee (first_name, last_name, job_title, salary,
                                  phone, supervisor_id)
            VALUES (?, ?, ?, 0, ?, ?)
        """, (member["first_name"], member["last_name"],
              "Volunteer - " + role, member["phone"], head["employee_id"]))
    return cur.lastrowid


# ---------------------------------------------------------------------------
# 8. Ask for help from a librarian
# ---------------------------------------------------------------------------

def ask_librarian(conn, question=""):
    """Return the librarians who can help.

    See the module docstring: there is no relation for help requests, so the
    question itself is not stored anywhere.
    """
    return conn.execute("""
        SELECT employee_id, first_name, last_name, job_title, phone
          FROM Employee
         WHERE job_title LIKE '%Librarian%'
           AND job_title NOT LIKE 'Volunteer%'
         ORDER BY job_title, last_name
    """).fetchall()


# ---------------------------------------------------------------------------
# Command line interface
# ---------------------------------------------------------------------------

MENU = """
  1. Find an item in the library
  2. Borrow an item from the library
  3. Return a borrowed item
  4. Donate an item to the library
  5. Find an event in the library
  6. Register for an event in the library
  7. Volunteer for the library
  8. Ask for help from a librarian
  0. Quit
"""


def _show(rows, columns=None):
    """Print rows as an aligned table."""
    rows = list(rows)
    if not rows:
        print("  (nothing found)")
        return
    columns = columns or rows[0].keys()
    widths = [max(len(str(c)), max(len(str(r[c])) for r in rows)) for c in columns]
    print("  " + "  ".join(str(c).ljust(w) for c, w in zip(columns, widths)))
    print("  " + "  ".join("-" * w for w in widths))
    for r in rows:
        print("  " + "  ".join(str(r[c]).ljust(w) for c, w in zip(columns, widths)))


def _ask(prompt, cast=str, optional=False):
    raw = input(prompt).strip()
    if not raw:
        if optional:
            return None
        raise LibraryError("A value is required.")
    return cast(raw)


def _do_find_item(conn):
    _show(find_items(conn, _ask("  Title or creator: ", optional=True) or ""),
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
    due, overdue, fine = return_item(conn, loan_id)
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


ACTIONS = {
    "1": _do_find_item,
    "2": _do_borrow,
    "3": _do_return,
    "4": _do_donate,
    "5": _do_find_event,
    "6": _do_register,
    "7": _do_volunteer,
    "8": _do_ask_librarian,
}


def _not_set_up():
    print("Database is not set up yet.")
    print("Run `python library.py` to create the schema, triggers and "
          "data, then try again.")


def main():
    # Check the file exists before connecting: sqlite3.connect() would create an
    # empty database as a side effect, leaving a stray file behind on refusal.
    if not os.path.exists(DB_NAME):
        _not_set_up()
        return
    conn = connect()
    if not is_initialised(conn):
        conn.close()
        _not_set_up()
        return

    print("\nLibrary database application.")
    while True:
        print(MENU)
        try:
            choice = input("  Choose an option: ").strip()
        except EOFError:
            break
        if choice == "0":
            break
        action = ACTIONS.get(choice)
        if action is None:
            print("  '%s' is not one of the options." % choice)
            continue
        try:
            action(conn)
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
